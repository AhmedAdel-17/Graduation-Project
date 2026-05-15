"""
End-to-end test of the full trading graph with ALL recent fixes:
  - Live news aggregator (Mubasher, Al Borsa, Google News, etc.)
  - FinBERT + CAMeLBERT transformer sentiment
  - Macro indicators (CBE rate, USD/EGP, Brent, real rate)
  - yfinance fallback for market analyst
  - safe_invoke error handling on all LLM nodes
  - seed=42 reproducibility
  - sender + prefetch state init fixes

Edit TICKER below to test different stocks.
"""
import sys, io, time
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────
TICKER     = "COMI.CA"      # Try FWRY (fintech), COMI (bank), HRHO (broker)
TRADE_DATE = "2025-01-15"
PORTFOLIO  = 1_000_000.0    # 1M EGP
PRICE      = 70.0           # COMI price ~69.86 on 2025-01-15
ADV        = 5_000_000.0    # COMI is large-cap, much higher ADV than FWRY

from tradingagents.dataflows.config import set_config
from tradingagents.default_config import DEFAULT_CONFIG
set_config({**DEFAULT_CONFIG, "target_market": "EGX"})

from tradingagents.graph.trading_graph import TradingAgentsGraph

print("=" * 70)
print(f"FULL GRAPH TEST: {TICKER} on {TRADE_DATE}")
print(f"Portfolio: {PORTFOLIO:,.0f} EGP | Price: {PRICE} EGP | ADV: {ADV:,.0f}")
print("=" * 70)
print()

graph = TradingAgentsGraph(
    selected_analysts=["market", "fundamentals", "news", "social"],
    config={
        **DEFAULT_CONFIG,
        "target_market":           "EGX",
        "trading_currency":        "EGP",
        "backtest_mode":           True,
        "max_debate_rounds":       1,
        "max_risk_discuss_rounds": 1,
    },
    debug=False,
)

print("Streaming node updates (this takes 2-4 minutes)...\n")

# Build initial state
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

for chunk in graph.graph.stream(init_state, **args):
    for node_name, node_updates in chunk.items():
        elapsed = time.time() - t0
        node_order.append(node_name)
        print(f"  → [{elapsed:5.1f}s] {node_name}", end="")
        if isinstance(node_updates, dict):
            accumulated.update(node_updates)
            keys = [k for k in node_updates if not k.endswith("_messages") and k != "messages"]
            if keys:
                print(f"   ({', '.join(keys[:4])})", end="")
        print()

total = time.time() - t0
print(f"\nTotal elapsed: {total:.1f}s")
print()

# ── Final result ──────────────────────────────────────────────────────
print("=" * 70)
print("FINAL TRADING DECISION")
print("=" * 70)

decision = accumulated.get("final_trade_decision", "")
print(f"Decision: {decision[:300]}")
print()

# ── Macro context ─────────────────────────────────────────────────────
mc = accumulated.get("macro_context") or {}
if mc:
    print("MACRO CONTEXT USED:")
    print(f"  CBE rate:      {mc.get('cbe_policy_rate', 0)*100:.2f}%")
    print(f"  Real rate:     {mc.get('real_rate', 0)*100:.2f}%")
    print(f"  USD/EGP:       {mc.get('usd_egp', 0):.2f} (trend: {mc.get('fx_trend', '?')})")
    print(f"  EGX30 trend:   {mc.get('egx30_trend', '?')}")
    print(f"  Brent crude:   ${mc.get('brent_usd', 0):.2f}")
    print()

# ── Analyst confidences ────────────────────────────────────────────────
print("ANALYST CONFIDENCES:")
ta = accumulated.get("technical_analysis") or {}
fa = accumulated.get("fundamental_analysis") or {}
sa = accumulated.get("sentiment_analysis") or {}
print(f"  Technical:     {ta.get('confidence_score', '?')}  (trend: {ta.get('trend_direction', {}).get('direction', '?')})")
print(f"  Fundamental:   {fa.get('confidence_score', '?')}  (health: {fa.get('financial_health', '?')})")
print(f"  Sentiment:     {sa.get('confidence_score', '?')}  ({sa.get('sentiment', '?')})")

# Transformer sentiment (NEW)
ts = sa.get("transformer_sentiment") or {}
if ts:
    print(f"  Transformer:   {ts.get('confidence', '?')}  ({ts.get('label', '?')}, score={ts.get('score', '?')})")

# Combined sentiment
cs = sa.get("combined_sentiment") or {}
if cs:
    print(f"  Combined:      {cs.get('confidence', '?')}  ({cs.get('label', '?')}, score={cs.get('score', '?')})")

print()

# ── Execution plan summary ─────────────────────────────────────────────
ep_raw = accumulated.get("execution_plan") or {}
ep = ep_raw.get("execution_plan", ep_raw) if isinstance(ep_raw, dict) else {}
if ep:
    print("EXECUTION PLAN:")
    print(f"  Decision:           {ep.get('decision', '?')}")
    print(f"  Conviction:         {ep.get('conviction', '?')}")
    sizing = ep.get("position_sizing", {})
    print(f"  Target shares:      {sizing.get('target_shares', '?')}")
    print(f"  Allocation:         {sizing.get('portfolio_allocation', '?')}")
    entry = ep.get("entry_logic", {})
    print(f"  Order type:         {entry.get('order_type', '?')}")
    zone = entry.get("entry_zone", {})
    if zone:
        print(f"  Entry zone:         {zone.get('price_range_low', '?')} - {zone.get('price_range_high', '?')} EGP")
    risk = ep.get("risk_management", {})
    if risk:
        print(f"  Stop-loss:          {risk.get('stop_loss', '?')}")

# ── Risk assessment ────────────────────────────────────────────────────
ra = accumulated.get("risk_assessment") or {}
if ra:
    print()
    print("RISK ASSESSMENT:")
    if ra.get("warnings"):
        print(f"  Warnings: {len(ra['warnings'])}")
        for w in ra["warnings"][:3]:
            print(f"    - [{w.get('severity','?')}] {w.get('rule','?')}")
    if ra.get("final_gate_notes"):
        print(f"  Gate notes: {ra['final_gate_notes']}")

print()
print(f"Node order ({len(node_order)} steps):")
print("  " + " → ".join(node_order))
