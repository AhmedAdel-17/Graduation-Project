"""One-off live test of the Portfolio Assistant on the user's real AR prompt."""
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

PROMPT = ("انا محفظتي فيها ٢٠٪ اسهم المصرية للاتصالات و ٤٠٪ اوراسكوم كونستراكشن و ٣٠٪ اوراسكوم "
          "ديفيلوبمنت و ١٠٪ ابو قير للاسمدة و قيمة المحفظة ١٠٠ الف و معايا غيرهم ٥٠ الف كاش "
          "قولي ممكن اعمل ايه")

OUT = Path("results/user_live_test_output.txt")
_lines: list[str] = []
def log(m: str = "") -> None: _lines.append(m)

def main() -> int:
    import tempfile
    store = JsonFileWorkspaceStore(base_dir=tempfile.mkdtemp(prefix="pa_live_"))
    svc = PortfolioCopilotService(store, price_provider=lambda ts: get_live_prices(ts), enable_redis=False)
    cid = store.create_conversation(language="ar")

    log("="*78); log("LIVE TEST — user's AR prompt"); log("="*78); log("PROMPT:\n  " + PROMPT); log("")

    log("-"*78); log("TURN 1 — describe (real LLM extraction)"); log("-"*78)
    ctx1 = svc.handle_turn(cid, PROMPT, language="ar")
    log(f"routed intents: {[i.value for i in ctx1.intents]}")
    extracted = None
    for e in ctx1.events:
        d = e.model_dump(mode="json")
        if d.get("type") == "extraction":
            for b in d.get("blocks", []):
                data = b.get("data", {})
                log(f"  cash_egp={data.get('cash_egp')} total_value_egp={data.get('total_value_egp')} "
                    f"unresolved={data.get('unresolved_names')}")
                for h in data.get("holdings", []):
                    log(f"    - {h.get('ticker')}  weight={h.get('weight_pct')}  shares={h.get('shares')} "
                        f"avg_cost={h.get('avg_cost')}  raw={h.get('name_raw')}")
            extracted = e.blocks[0].data
        elif d.get("type") == "assistant_message":
            log(f"  [assistant] {d.get('text')}")
        elif d.get("type") == "clarification":
            log(f"  [clarification] {d.get('question')} missing={d.get('missing')}")

    if not extracted or not extracted.holdings:
        log("\n>> no holdings extracted; abort"); _flush(); return 1

    confirmed = s.PortfolioSnapshot(
        conversation_id=cid, cash_egp=float(extracted.cash_egp or 0.0),
        total_value_egp=extracted.total_value_egp,
        holdings=[h.model_copy() for h in extracted.holdings], confirmed_by_user=True)
    svc.confirm_snapshot(cid, confirmed, language="ar")
    base = store.get_latest_snapshot(cid)
    log("\nconfirmed baseline (service-reconciled to shares):")
    for h in base.holdings:
        log(f"  {h.ticker}: {h.shares} shares (weight {h.weight_pct})")

    log(""); log("-"*78); log("TURN 2 — optimize incl. new opportunities"); log("-"*78)
    ctx2 = svc.handle_turn(cid, "وزّع المحفظة وقولي اشتري بالكاش ايه ممكن اضيف اسهم جديدة", language="ar")
    log(f"routed intents: {[i.value for i in ctx2.intents]}")
    for e in ctx2.events:
        d = e.model_dump(mode="json")
        if d.get("type") == "assistant_message":
            log(f"  [assistant] {d.get('text')}")

    pid = store.load_workspace(cid).last_proposal_id
    if pid is not None:
        prop = store.get_proposal(pid)
        log(""); log("-"*78); log(f"PROPOSAL #{pid} (solver={prop.solver_status.value})"); log("-"*78)
        a = prop.inputs_audit
        log(f"  covariance_source={a.get('covariance_source')} prior_source={a.get('prior_source')} "
            f"views_source={a.get('views_source')}")
        log(f"  vol {prop.expected_vol_before:.3f} -> {prop.expected_vol_after:.3f} | "
            f"HHI {prop.hhi_before:.3f} -> {prop.hhi_after:.3f} | "
            f"view-return {prop.expected_return_view_annual:+.2%}/yr | "
            f"cost ~{prop.est_total_cost_egp:.0f} EGP turnover {prop.est_turnover_pct:.1f}%")
        for act in prop.actions:
            log(f"  {act.side.value:<4} {act.shares:>6} {act.ticker:<9} @ {act.price_used:.2f}  "
                f"({act.current_weight_pct:.1f}% -> {act.target_weight_pct:.1f}%)\n        | {act.rationale}")
        log("  views (top by |score|):")
        views = a.get("views") or {}
        for tk, vd in sorted(views.items(), key=lambda kv: -abs(float(kv[1].get('score',0))))[:8]:
            log(f"    {tk}: score={vd.get('score'):+.2f} conf={vd.get('confidence'):.2f}")
        for f in prop.policy_flags:
            log(f"  [flag:{f.severity.value}] {f.code}: {f.detail}")
    _flush(); return 0

def _flush() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(_lines), encoding="utf-8")
    print(f"[written] {OUT} ({len(_lines)} lines)")

if __name__ == "__main__":
    raise SystemExit(main())
