"""
Macro-regime test: same ticker, two different macro regimes.

The hypothesis: when CBE rate is LOW (e.g., pre-2022 ~9%, real rate POSITIVE),
the system should be more willing to issue BUY recommendations because the
"why buy stocks when T-bills give you 27.5%" argument disappears.

When CBE rate is HIGH (current 27.5%, real rate near-zero), the system should
remain in HOLD/AVOID mode because risk-free yield dominates equity earnings yield.

This test runs the SAME ticker (COMI) with TWO macro regimes and compares
the trading decisions to prove the macro layer actually changes outcomes.

Note: this is a thought experiment, not a true backtest. We override the macro
config to simulate the older regime — the company fundamentals (CSV data) and
news/sentiment (live feeds) are still current. The point is to prove the
macro-layer overlay flows through to the LLM debate.

Usage:
  python test_macro_regime.py
"""
import sys, io, time, copy
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────
TICKER     = "COMI.CA"
TRADE_DATE = "2025-01-15"
PORTFOLIO  = 1_000_000.0
PRICE      = 70.0
ADV        = 5_000_000.0

# Two macro regimes to compare
REGIMES = {
    "HIGH_RATE_2024":  {  # Current reality
        "label":            "Current EGX (Jan 2025): CBE 27.5%, hostile to equities",
        "egx_risk_free_rate": 0.275,   # CBE policy rate
        "tbill_yield_91d":    0.275,
        "egypt_cpi":          0.258,   # Real rate = +1.7% (slightly positive)
        "usd_egp_default":    49.5,
    },
    "LOW_RATE_2021":   {  # Pre-tightening cycle
        "label":            "Pre-tightening EGX (mock 2021): CBE 9.25%, equity-friendly",
        "egx_risk_free_rate": 0.0925,  # CBE policy rate ~9.25%
        "tbill_yield_91d":    0.110,   # T-bills ~11%
        "egypt_cpi":          0.06,    # CPI ~6%, real rate +3.25%
        "usd_egp_default":    15.7,    # Pre-2022 devaluation
    },
}


def run_one_regime(regime_key: str, overrides: dict) -> dict:
    """Run the full graph with the given macro overrides and return final state."""
    from tradingagents.dataflows.config import set_config
    from tradingagents.default_config import DEFAULT_CONFIG

    # Build the regime-specific config
    regime_config = {
        **DEFAULT_CONFIG,
        "target_market":           "EGX",
        "trading_currency":        "EGP",
        "backtest_mode":           True,
        "max_debate_rounds":       1,
        "max_risk_discuss_rounds": 1,
        # ── Macro overrides ──
        "egx_risk_free_rate":      overrides["egx_risk_free_rate"],
        "tbill_yield_91d":         overrides["tbill_yield_91d"],
        "egypt_cpi":               overrides["egypt_cpi"],
        "usd_egp_default":         overrides["usd_egp_default"],
    }
    set_config(regime_config)

    # Verify the macro context picks up the overrides
    from tradingagents.dataflows.macro_provider import get_egx_macro_context
    ctx = get_egx_macro_context(TRADE_DATE, regime_config)
    print(f"\n   Macro context verification:")
    print(f"     CBE rate:   {ctx['cbe_policy_rate']*100:.2f}%")
    print(f"     T-bill:     {ctx['tbill_yield_91d']*100:.2f}%")
    print(f"     CPI:        {ctx['egypt_cpi']*100:.2f}%")
    print(f"     Real rate:  {ctx['real_rate']*100:.2f}%")
    print(f"     USD/EGP:    {ctx['usd_egp']:.2f}")
    print()

    # Run the graph
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    graph = TradingAgentsGraph(
        selected_analysts=["market", "fundamentals", "news", "social"],
        config=regime_config,
        debug=False,
    )

    init_state = {
        "company_of_interest": TICKER,
        "trade_date":          TRADE_DATE,
        "messages":            [("human", TICKER)],
        "portfolio_value":     PORTFOLIO,
        "current_price":       PRICE,
        "avg_daily_volume":    ADV,
        "current_position":    {},
    }
    args = {**graph.propagator.get_graph_args(), "stream_mode": "updates"}

    accumulated = {}
    node_order = []
    t0 = time.time()
    print(f"   Streaming {regime_key}...")

    for chunk in graph.graph.stream(init_state, **args):
        for node_name, node_updates in chunk.items():
            node_order.append(node_name)
            if isinstance(node_updates, dict):
                accumulated.update(node_updates)

    elapsed = time.time() - t0
    print(f"   Done in {elapsed:.1f}s")
    print(f"   Node order ({len(node_order)} steps): {' -> '.join(node_order)}\n")
    return accumulated


def summarize(regime_key: str, regime_label: str, state: dict) -> None:
    print(f"╔══════════════════════════════════════════════════════════════════════╗")
    print(f"║  {regime_key}")
    print(f"║  {regime_label}")
    print(f"╚══════════════════════════════════════════════════════════════════════╝")

    decision = state.get("final_trade_decision", "")
    print(f"  FINAL DECISION:   {decision[:100]}")

    ep_raw = state.get("execution_plan") or {}
    ep = ep_raw.get("execution_plan", ep_raw) if isinstance(ep_raw, dict) else {}
    print(f"  Trader decision:  {ep.get('decision', '?')}  (conviction: {ep.get('conviction', '?')})")

    sizing = ep.get("position_sizing", {}) if isinstance(ep, dict) else {}
    print(f"  Allocation:       {sizing.get('portfolio_allocation', '?')}")
    print(f"  Target shares:    {sizing.get('target_shares', '?')}")

    entry = ep.get("entry_logic", {}) if isinstance(ep, dict) else {}
    zone = entry.get("entry_zone", {}) if isinstance(entry, dict) else {}
    print(f"  Entry zone:       {zone.get('price_range_low', '?')} - {zone.get('price_range_high', '?')} EGP")

    sa = state.get("sentiment_analysis") or {}
    cs = sa.get("combined_sentiment") or {}
    print(f"  Sentiment:        {sa.get('sentiment', '?')} ({sa.get('confidence_score', '?')}/100)")
    print(f"  Combined senti:   {cs.get('label', '?')} (score={cs.get('score', '?')}, conf={cs.get('confidence', '?')})")

    ra = state.get("risk_assessment") or {}
    print(f"  risk_action:      {state.get('risk_action', '?')}")
    print(f"  risk_veto:        {state.get('risk_veto', '?')}")
    print(f"  risk_assessment keys ({len(ra)}): {list(ra.keys())}")
    print(f"  LLM action:       {ra.get('llm_action', '?')}  (conf={ra.get('llm_confidence', '?')})")
    if ra.get('llm_constitution_check'):
        print(f"  Constitution:     {ra['llm_constitution_check'][:200]}...")
    if ra.get('llm_qualitative_risks'):
        print(f"  Qualitative risk: {ra['llm_qualitative_risks'][:200]}...")
    print(f"  Clauses cited:    {ra.get('clauses_referenced', [])}")
    if ra.get('warnings'):
        print(f"  Warnings:         {len(ra['warnings'])} item(s)")
        for w in ra['warnings'][:2]:
            print(f"     - [{w.get('severity','?')}] {w.get('rule','?')}: {w.get('explanation', '')[:100]}")
    if ra.get('veto_explanation'):
        print(f"  Veto reason:      {ra['veto_explanation'][:200]}")
    print()

    # Dump the Risk Manager's last response (raw text) to trace parser
    rds = state.get("risk_debate_state") or {}
    judge_text = rds.get("judge_decision", "")
    if judge_text:
        print(f"  Risk Judge raw response (last 600 chars):")
        for line in judge_text[-600:].split("\n"):
            print(f"    | {line}")
        print()


# ─── MAIN ──────────────────────────────────────────────────────────────
print("=" * 70)
print(f"MACRO REGIME COMPARISON  —  {TICKER} on {TRADE_DATE}")
print(f"Same company, same news, same price — only macro differs")
print("=" * 70)

results = {}
for regime_key, overrides in REGIMES.items():
    print(f"\n>>> Running regime: {regime_key}")
    print(f"    {overrides['label']}")
    state = run_one_regime(regime_key, overrides)
    results[regime_key] = (overrides["label"], state)

print()
print("=" * 70)
print("RESULTS COMPARISON")
print("=" * 70)
for regime_key, (label, state) in results.items():
    summarize(regime_key, label, state)

# ── Verdict ────────────────────────────────────────────────────────────
print("=" * 70)
print("VERDICT")
print("=" * 70)
high_state = results["HIGH_RATE_2024"][1]
low_state = results["LOW_RATE_2021"][1]

high_decision = (high_state.get("final_trade_decision", "") or "")[:50]
low_decision = (low_state.get("final_trade_decision", "") or "")[:50]

if high_decision != low_decision:
    print(f"✓ MACRO LAYER WORKS — Different decisions for different regimes")
    print(f"   High-rate (27.5% CBE): {high_decision}")
    print(f"   Low-rate (9.25% CBE):  {low_decision}")
else:
    print(f"⚠ Same decision in both regimes ({high_decision})")
    print(f"   Macro layer might not be influencing the decision strongly enough.")
    print(f"   Check LLM responses to see if rate context was actually used.")
