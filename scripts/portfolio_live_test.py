"""Live end-to-end test of the Portfolio Assistant on REAL data (no mocks).

Drives one real Egyptian-Arabic message through the full copilot:
  real conversational LLM (NVIDIA-hosted Qwen) for extraction/strategy/narration,
  real yfinance EGX prices, the real optimizer + EGX validation, real signal
  resolver (Postgres analysis_sessions if populated, else quant-prior).

Writes a UTF-8 report to results/live_test_output.txt (console stays ASCII so it
works on a cp1252 Windows terminal).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

from tradingagents.portfolio import schemas as s
from tradingagents.portfolio.copilot_service import PortfolioCopilotService
from tradingagents.portfolio.market_data import get_live_prices
from tradingagents.portfolio.workspace_store import JsonFileWorkspaceStore

PROMPT = ("تقسيمة المحفظة بتاعتي دلوقتي هي 60% اسهم المصرية للاتصالات و 20% اسهم طلعت مصطفي "
          "و 20% اسهم اوراسكوم ديفيلوبمنت و قيمة المحفظة 50 الف و معايا 20 الف كاش غيرهم "
          "قولي ممكن اشتري بيهم ايه")

OUT = Path("results/live_test_output.txt")
_lines: list[str] = []


def log(msg: str = "") -> None:
    _lines.append(msg)


def _ev(e) -> str:
    d = e.model_dump(mode="json")
    t = d.get("type")
    if t == "extraction":
        rows = []
        for b in d.get("blocks", []):
            data = b.get("data", {})
            for h in data.get("holdings", []):
                rows.append(f"      - {h.get('ticker')}  shares={h.get('shares')}  "
                            f"avg_cost={h.get('avg_cost')}  weight_pct={h.get('weight_pct')}  "
                            f"raw={h.get('name_raw')}")
            extra = (f"      cash_egp={data.get('cash_egp')}  unresolved={data.get('unresolved_names')}  "
                     f"warnings={data.get('warnings')}")
        return "  [extraction]\n" + "\n".join(rows) + "\n" + extra
    if t == "clarification":
        return f"  [clarification] {d.get('question')}  missing={d.get('missing')}"
    if t == "policy_update":
        p = d.get("policy", {})
        return (f"  [policy_update] objective={p.get('objective')} risk={p.get('risk_tolerance')} "
                f"horizon={p.get('horizon')} inferred={d.get('inferred_fields')}")
    if t == "assistant_message":
        blocks = ", ".join(b.get("type") for b in d.get("blocks", []))
        return f"  [assistant_message] text={d.get('text')!r}\n      blocks=[{blocks}]"
    if t == "status":
        return f"  [status] {d.get('stage')}: {d.get('detail')}"
    return f"  [{t}] {d}"


def main() -> int:
    store = JsonFileWorkspaceStore(base_dir="results/portfolio_chats")
    svc = PortfolioCopilotService(store, price_provider=lambda ts: get_live_prices(ts), enable_redis=False)

    log("=" * 78)
    log("PORTFOLIO ASSISTANT — LIVE REAL-DATA TEST")
    log("=" * 78)
    log("PROMPT:")
    log("  " + PROMPT)
    log("")

    cid = store.create_conversation(language="ar")

    # --- Turn 1: the user's real message → real LLM extraction ---------------
    log("-" * 78)
    log("TURN 1 — describe (real Qwen extraction)")
    log("-" * 78)
    ctx1 = svc.handle_turn(cid, PROMPT, language="ar")
    log(f"  routed intents: {[i.value for i in ctx1.intents]}")
    for e in ctx1.events:
        log(_ev(e))

    snap = store.get_latest_snapshot(cid)
    extracted = None
    for e in ctx1.events:
        if isinstance(e, s.ExtractionEvent) and e.blocks:
            extracted = e.blocks[0].data
            break

    if extracted is None or not extracted.holdings:
        log("\n  >> Extraction produced no holdings; aborting optimize.")
        _flush()
        return 1

    # --- Confirm the EXTRACTED table as-is (weight-only + the LLM-captured total
    # value). The SERVICE reconciles weights → shares from live prices — no manual
    # share math here, so this exercises the real product path. ---
    prices = get_live_prices([h.ticker for h in extracted.holdings])
    log("")
    log("-" * 78)
    log("LIVE PRICES (yfinance):")
    for tk, px in prices.items():
        log(f"  {tk}: {px:.2f} EGP")
    log(f"  extracted total_value_egp = {extracted.total_value_egp}  cash = {extracted.cash_egp}")

    confirmed = s.PortfolioSnapshot(
        conversation_id=cid, cash_egp=float(extracted.cash_egp or 0.0),
        total_value_egp=extracted.total_value_egp,
        holdings=[h.model_copy() for h in extracted.holdings],
        confirmed_by_user=True)
    svc.confirm_snapshot(cid, confirmed, language="ar")
    base = store.get_latest_snapshot(cid)
    log("\n  confirmed baseline (service-reconciled to shares):")
    for h in base.holdings:
        log(f"    {h.ticker}: {h.shares} shares  (weight {h.weight_pct})")

    # --- Turn 2: optimize → real prices + real signals + real optimizer ------
    log("")
    log("-" * 78)
    log("TURN 2 — optimize / 'what can I buy' (real optimizer + narrator)")
    log("-" * 78)
    ctx2 = svc.handle_turn(cid, "وزّع المحفظة و قولي اشتري بالكاش ايه", language="ar")
    log(f"  routed intents: {[i.value for i in ctx2.intents]}")
    for e in ctx2.events:
        log(_ev(e))

    pid = store.load_workspace(cid).last_proposal_id
    if pid is not None:
        prop = store.get_proposal(pid)
        log("")
        log("-" * 78)
        log(f"PROPOSAL #{pid} (solver={prop.solver_status.value})")
        log("-" * 78)
        log(f"  vol {prop.expected_vol_before} -> {prop.expected_vol_after} | "
            f"HHI {prop.hhi_before:.3f} -> {prop.hhi_after:.3f} | "
            f"view-return {prop.expected_return_view_annual:+.2%}/yr")
        log(f"  est cost ~{prop.est_total_cost_egp:.0f} EGP | turnover {prop.est_turnover_pct:.1f}%")
        if not prop.actions:
            log("  (no trades proposed)")
        for a in prop.actions:
            log(f"  {a.side.value:<4} {a.shares:>6} {a.ticker:<9} @ {a.price_used:.2f}  "
                f"~{a.est_value_egp:.0f} EGP  ({a.current_weight_pct:.1f}% -> {a.target_weight_pct:.1f}%)"
                f"  | {a.rationale}")
        log("  signals:")
        for tk, sig in (prop.inputs_audit.get("signals") or {}).items():
            log(f"    {tk}: {sig.get('label')} conf={sig.get('confidence')} "
                f"src={sig.get('source')} stale={sig.get('is_stale')}")
        for f in prop.policy_flags:
            log(f"  [flag:{f.severity.value}] {f.code}: {f.detail}")

    _flush()
    return 0


def _flush() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(_lines), encoding="utf-8")
    print(f"[written] {OUT}  ({len(_lines)} lines)")


if __name__ == "__main__":
    raise SystemExit(main())
