"""
=============================================================================
EGX Trading System - Comprehensive Agent Test Harness
=============================================================================
Tests every agent individually (isolated unit test) then runs the full graph.

Usage:
    python test_agents.py

Output: structured JSON results for each agent.
=============================================================================
"""

import io, os, sys, json, time, textwrap, traceback

# Force UTF-8 output on Windows so box/arrow chars don't crash cp1252
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
from datetime import datetime, date
from pathlib import Path
from dotenv import load_dotenv

# ── Project root on path ──────────────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

# ── Config ────────────────────────────────────────────────────────────────────
TICKER     = "COMI.CA"      # Commercial International Bank – best EGX data coverage
TRADE_DATE = "2025-01-15"   # Historical date so data is guaranteed available
PORTFOLIO  = 1_000_000.0    # 1M EGP starting capital
PRICE      = 68.0           # Approximate COMI price on that date
ADV        = 2_500_000.0    # Approximate average daily volume

# ── Colour helpers ────────────────────────────────────────────────────────────
RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
GREEN = "\033[92m"; RED = "\033[91m"; YELLOW = "\033[93m"
CYAN  = "\033[96m"; MAGENTA = "\033[95m"; BLUE = "\033[94m"

def hdr(title: str):
    bar = "═" * 70
    print(f"\n{BOLD}{CYAN}{bar}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{bar}{RESET}")

def section(name: str):
    print(f"\n{BOLD}{BLUE}▶  {name}{RESET}")

def ok(msg: str):   print(f"  {GREEN}✓{RESET}  {msg}")
def warn(msg: str): print(f"  {YELLOW}⚠{RESET}  {msg}")
def err(msg: str):  print(f"  {RED}✗{RESET}  {msg}")
def info(msg: str): print(f"  {DIM}   {msg}{RESET}")

def show_json(label: str, data, max_chars: int = 600):
    """Pretty-print a JSON blob with a character cap."""
    if data is None:
        print(f"  {MAGENTA}{label}:{RESET} {DIM}(None){RESET}")
        return
    try:
        raw = json.dumps(data, indent=2, ensure_ascii=False, default=str)
        if len(raw) > max_chars:
            raw = raw[:max_chars] + f"\n  ... ({len(raw)-max_chars} chars truncated)"
        print(f"  {MAGENTA}{label}:{RESET}\n" +
              "\n".join("    " + l for l in raw.splitlines()))
    except Exception:
        print(f"  {MAGENTA}{label}:{RESET} {data!r}")

def show_text(label: str, text: str, max_chars: int = 400):
    if not text:
        print(f"  {MAGENTA}{label}:{RESET} {DIM}(empty){RESET}")
        return
    preview = text[:max_chars] + ("…" if len(text) > max_chars else "")
    print(f"  {MAGENTA}{label}:{RESET}\n" +
          "\n".join("    " + l for l in preview.splitlines()))


# =============================================================================
# LLM + Memory setup (shared across all agents)
# =============================================================================

def build_llm():
    from langchain_openai import ChatOpenAI
    api_key = (
        os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )
    if not api_key:
        raise RuntimeError("No API key found. Set DEEPSEEK_API_KEY or OPENAI_API_KEY in .env")
    from tradingagents.default_config import DEFAULT_CONFIG
    llm = ChatOpenAI(
        model      = DEFAULT_CONFIG["quick_think_llm"],
        base_url   = DEFAULT_CONFIG["backend_url"],
        api_key    = api_key,
        temperature= 0,
        seed       = 42,
    )
    return llm


def build_memory():
    from tradingagents.agents.utils.memory import FinancialSituationMemory
    from tradingagents.default_config import DEFAULT_CONFIG
    return FinancialSituationMemory(name="test_memory", config=DEFAULT_CONFIG)


# =============================================================================
# Minimal shared AgentState skeleton
# =============================================================================

def base_state() -> dict:
    """Minimal AgentState with all required fields pre-filled."""
    return {
        # Core identity
        "company_of_interest": TICKER,
        "trade_date": TRADE_DATE,
        "sender": "test_harness",
        # Per-analyst message channels
        "messages": [],
        "market_messages": [],
        "social_messages": [],
        "news_messages": [],
        "fundamentals_messages": [],
        # Reports (filled as agents run)
        "market_report": "",
        "sentiment_report": "",
        "social_sentiment_analysis": "",
        "news_report": "",
        "fundamentals_report": "",
        # Debate states (minimal)
        "investment_debate_state": {
            "bull_history": "", "bear_history": "", "history": "",
            "current_response": "", "judge_decision": "",
            "count": 0, "bull_thesis": None, "bear_thesis": None,
        },
        "investment_plan": "",
        "trader_investment_plan": "",
        "risk_debate_state": {
            "risky_history": "", "safe_history": "", "neutral_history": "",
            "history": "", "latest_speaker": "", "count": 0,
            "current_risky_response": "", "current_safe_response": "",
            "current_neutral_response": "", "judge_decision": "",
        },
        "final_trade_decision": "",
        # EGX fields
        "data_quality": None,
        "confidence_scores": None,
        "technical_analysis": None,
        "fundamental_analysis": None,
        "sentiment_analysis": None,
        # Portfolio context
        "portfolio_value": PORTFOLIO,
        "current_price": PRICE,
        "avg_daily_volume": ADV,
        "low_liquidity": False,
        "volume_missing": False,
        "current_position": {},
        # Execution / risk
        "execution_plan": None,
        "risk_assessment": None,
        "risk_veto": None,
        "risk_action": None,
        "risk_metrics": None,
        # Pre-fetch
        "prefetched_company_news": None,
        "prefetched_market_news": None,
        "prefetched_social_sentiment": None,
        "prefetched_social_posts": None,
        "prefetched_stock_datapoints": None,
        "sentiment_blend_result": None,
        # Market
        "target_market": "EGX",
        "trading_currency": "EGP",
        # Macro context (populated by DataPrefetcher)
        "macro_context": None,
    }


# =============================================================================
# Individual agent tests
# =============================================================================

RESULTS = {}  # Collects pass/fail summary

def run_test(name: str, fn):
    """Run a single agent test, catch & display errors."""
    section(name)
    t0 = time.time()
    try:
        result = fn()
        elapsed = time.time() - t0
        ok(f"PASSED in {elapsed:.1f}s")
        RESULTS[name] = {"status": "PASS", "elapsed": elapsed}
        return result
    except Exception as e:
        elapsed = time.time() - t0
        err(f"FAILED in {elapsed:.1f}s — {type(e).__name__}: {e}")
        info(traceback.format_exc()[-800:])
        RESULTS[name] = {"status": "FAIL", "error": str(e), "elapsed": elapsed}
        return None


# ── 1. Investor Profiling ─────────────────────────────────────────────────────

def test_investor_profiling():
    from tradingagents.agents.profiling.investor_profiling_agent import InvestorProfilingAgent
    agent = InvestorProfilingAgent()
    profile = agent.classify("I want stable swing trades on EGX with moderate risk")
    show_json("InvestorProfile", {
        "category":   profile.investor_category,
        "confidence": profile.confidence_score,
        "trigger":    profile.trigger_frequency,
        "priority":   profile.analysis_priority,
        "reasoning":  profile.reasoning[:120],
    })
    assert profile.investor_category == "SWING", "Expected SWING category"
    assert profile.confidence_score  == 1.0,     "Expected 1.0 confidence"
    return profile


# ── 2. Market Analyst (Deterministic) ────────────────────────────────────────

def test_market_analyst_deterministic():
    from tradingagents.agents.analysts.market_analyst import create_deterministic_market_analyst
    from tradingagents.dataflows.config import set_config
    from tradingagents.default_config import DEFAULT_CONFIG
    set_config({**DEFAULT_CONFIG, "target_market": "EGX"})

    node = create_deterministic_market_analyst()
    state = base_state()
    result = node(state)

    show_text("market_report",  result.get("market_report", ""))
    show_json("technical_analysis", result.get("technical_analysis"))
    info(f"low_liquidity={result.get('low_liquidity')}")

    assert "market_report"     in result, "Missing market_report"
    assert "technical_analysis" in result, "Missing technical_analysis"
    ta = result.get("technical_analysis") or {}
    assert "trend_direction" in ta, f"Missing trend_direction in technical_analysis: {list(ta.keys())}"
    return result


# ── 3. Fundamentals Analyst (Deterministic) ───────────────────────────────────

def test_fundamentals_analyst():
    from tradingagents.agents.analysts.fundamentals_analyst import create_deterministic_fundamentals_analyst
    from tradingagents.dataflows.config import set_config
    from tradingagents.default_config import DEFAULT_CONFIG
    set_config({**DEFAULT_CONFIG, "target_market": "EGX"})

    node = create_deterministic_fundamentals_analyst()
    state = base_state()
    result = node(state)

    show_text("fundamentals_report",  result.get("fundamentals_report", "")[:500])
    show_json("fundamental_analysis", result.get("fundamental_analysis"))
    return result


# ── 4. News Analyst (LLM) ─────────────────────────────────────────────────────

def test_news_analyst():
    from tradingagents.agents.analysts.news_analyst import create_news_analyst
    from tradingagents.dataflows.config import set_config
    from tradingagents.default_config import DEFAULT_CONFIG
    set_config({**DEFAULT_CONFIG, "target_market": "EGX"})

    llm = build_llm()
    node = create_news_analyst(llm)
    state = base_state()
    # Inject prefetched data so the agent skips the tool-calling round-trip
    # and goes straight to LLM analysis (same path used in production with prefetcher)
    state["prefetched_company_news"] = (
        f"[EGX Disclosure] {TICKER} reports strong Q3 2024 results: net profit up 22% YoY "
        f"to EGP 5.8bn. NIM expanded to 5.1%. NPL ratio stable at 2.3%. "
        f"Board declares EGP 1.50/share interim dividend.\n"
        f"[Mubasher] COMI shares rise 3.2% on positive earnings surprise.\n"
        f"[Reuters Arabic] بنك CIB يحقق أرباحا قياسية في الربع الثالث"
    )
    state["prefetched_market_news"] = (
        "EGX30 index gained 1.8% this week on improved dollar liquidity outlook. "
        "CBE held rates steady at 27.25%. Banking sector leads gains."
    )
    result = node(state)

    show_text("news_report", result.get("news_report", ""))
    show_json("sentiment_analysis", result.get("sentiment_analysis"))

    assert "news_report"       in result, "Missing news_report"
    assert "sentiment_analysis" in result, "Missing sentiment_analysis"
    sa = result.get("sentiment_analysis") or {}
    assert "sentiment" in sa, f"Missing sentiment field in sentiment_analysis: {list(sa.keys())}"
    return result


# ── 5. Social Media Analyst (LLM + Layer pipeline) ───────────────────────────

def test_social_media_analyst():
    from tradingagents.agents.analysts.social_media_analyst import create_social_media_analyst
    from tradingagents.dataflows.config import set_config
    from tradingagents.default_config import DEFAULT_CONFIG
    set_config({**DEFAULT_CONFIG, "target_market": "EGX"})

    llm = build_llm()
    node = create_social_media_analyst(llm)
    state = base_state()
    # Inject prefetched social data so agent skips tool calls
    state["prefetched_social_posts"] = (
        "Post 1 (Facebook EGX group): CIB نتائج ممتازة! الأرباح ارتفعت 22% — شراء قوي\n"
        "Post 2 (Telegram @egxinvestors): COMI breaking out, strong institutional buying seen\n"
        "Post 3 (Reddit r/EgyptStocks): Fundamentals look solid, holding my COMI position\n"
        "Post 4 (Facebook): البنك التجاري الدولي أداء رائع هذا الربع"
    )
    state["prefetched_social_sentiment"] = (
        '{"overall": "bullish", "confidence": 0.65, "posts_analyzed": 4, '
        '"bullish_pct": 0.75, "bearish_pct": 0.10, "neutral_pct": 0.15}'
    )
    result = node(state)

    show_text("sentiment_report", result.get("sentiment_report", ""))
    show_json("sentiment_blend_result", result.get("sentiment_blend_result"))

    assert "sentiment_report"    in result, "Missing sentiment_report"
    assert "sentiment_blend_result" in result, "Missing sentiment_blend_result"
    return result


# ── 6. Bull Researcher (LLM) ──────────────────────────────────────────────────

def test_bull_researcher(market_result=None, fund_result=None, news_result=None, social_result=None):
    from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
    from tradingagents.dataflows.config import set_config
    from tradingagents.default_config import DEFAULT_CONFIG
    set_config({**DEFAULT_CONFIG, "target_market": "EGX"})

    llm    = build_llm()
    memory = build_memory()
    node   = create_bull_researcher(llm, memory)

    state = base_state()
    state["market_report"]       = (market_result  or {}).get("market_report",       f"Technical: bullish trend detected for {TICKER}")
    state["fundamentals_report"] = (fund_result    or {}).get("fundamentals_report", f"Fundamentals: ROE 18%, P/E 7.5x for {TICKER}")
    state["news_report"]         = (news_result    or {}).get("news_report",         f"News: Positive Q3 results reported for {TICKER}")
    state["sentiment_report"]    = (social_result  or {}).get("sentiment_report",    "Social: mixed retail sentiment, moderate volume")
    state["sentiment_blend_result"] = (social_result or {}).get("sentiment_blend_result", None)
    state["technical_analysis"]  = (market_result  or {}).get("technical_analysis",  None)
    state["fundamental_analysis"] = (fund_result   or {}).get("fundamental_analysis", None)

    result = node(state)

    ids = result.get("investment_debate_state", {})
    show_text("bull_history", ids.get("bull_history", ""))
    show_json("bull_thesis",  ids.get("bull_thesis"))

    assert "investment_debate_state" in result, "Missing investment_debate_state"
    return result


# ── 7. Bear Researcher (LLM) ──────────────────────────────────────────────────

def test_bear_researcher(market_result=None, fund_result=None, news_result=None, social_result=None):
    from tradingagents.agents.researchers.bear_researcher import create_bear_researcher
    from tradingagents.dataflows.config import set_config
    from tradingagents.default_config import DEFAULT_CONFIG
    set_config({**DEFAULT_CONFIG, "target_market": "EGX"})

    llm    = build_llm()
    memory = build_memory()
    node   = create_bear_researcher(llm, memory)

    state = base_state()
    state["market_report"]       = (market_result  or {}).get("market_report",       f"Technical: RSI at 72, approaching overbought for {TICKER}")
    state["fundamentals_report"] = (fund_result    or {}).get("fundamentals_report", f"Fundamentals: Debt/equity 1.8x, margin compression for {TICKER}")
    state["news_report"]         = (news_result    or {}).get("news_report",         f"News: Regulatory scrutiny reported for banking sector")
    state["sentiment_report"]    = (social_result  or {}).get("sentiment_report",    "Social: bearish retail chatter on Telegram groups")
    state["sentiment_blend_result"] = (social_result or {}).get("sentiment_blend_result", None)
    state["technical_analysis"]  = (market_result  or {}).get("technical_analysis",  None)
    state["fundamental_analysis"] = (fund_result   or {}).get("fundamental_analysis", None)
    # Give bear researcher bull debate history to respond to
    state["investment_debate_state"]["bull_history"] = f"Bull: {TICKER} shows strong momentum with ROE above 18% and P/E re-rating potential."

    result = node(state)

    ids = result.get("investment_debate_state", {})
    show_text("bear_history", ids.get("bear_history", ""))
    show_json("bear_thesis",  ids.get("bear_thesis"))

    assert "investment_debate_state" in result, "Missing investment_debate_state"
    return result


# ── 8. Research Manager (LLM) ─────────────────────────────────────────────────

def test_research_manager(bull_r=None, bear_r=None, market_result=None, fund_result=None, news_result=None, social_result=None):
    from tradingagents.agents.managers.research_manager import create_research_manager
    from tradingagents.dataflows.config import set_config
    from tradingagents.default_config import DEFAULT_CONFIG
    set_config({**DEFAULT_CONFIG, "target_market": "EGX"})

    llm    = build_llm()
    memory = build_memory()
    node   = create_research_manager(llm, memory)

    state = base_state()
    state["market_report"]       = (market_result or {}).get("market_report", f"Technical: moderate bullish bias for {TICKER}")
    state["fundamentals_report"] = (fund_result   or {}).get("fundamentals_report", f"Fundamentals: solid bank with ROE 18%")
    state["news_report"]         = (news_result   or {}).get("news_report", f"News: positive Q3 results, stable macro")
    state["sentiment_report"]    = (social_result or {}).get("sentiment_report", "Social: neutral-to-positive")

    # Merge debate state from bull+bear if available
    bull_ids = (bull_r or {}).get("investment_debate_state", {})
    bear_ids = (bear_r or {}).get("investment_debate_state", {})
    state["investment_debate_state"] = {
        "bull_history":   bull_ids.get("bull_history", f"Bull: {TICKER} momentum strong, P/E discount to peers"),
        "bear_history":   bear_ids.get("bear_history", f"Bear: EGX macro risk + currency pressure may cap upside"),
        "history":        bull_ids.get("bull_history", "") + "\n" + bear_ids.get("bear_history", ""),
        "current_response": "",
        "judge_decision": "",
        "count": 1,
        "bull_thesis":    bull_ids.get("bull_thesis"),
        "bear_thesis":    bear_ids.get("bear_thesis"),
    }

    result = node(state)
    show_text("investment_plan", result.get("investment_plan", ""))
    assert "investment_plan" in result, "Missing investment_plan"
    return result


# ── 9. Trader (LLM) ───────────────────────────────────────────────────────────

def test_trader(research_result=None):
    from tradingagents.agents.trader.trader import create_trader
    from tradingagents.dataflows.config import set_config
    from tradingagents.default_config import DEFAULT_CONFIG
    set_config({**DEFAULT_CONFIG, "target_market": "EGX", "backtest_mode": True})

    llm    = build_llm()
    memory = build_memory()
    node   = create_trader(llm, memory)

    state = base_state()
    state["investment_plan"] = (research_result or {}).get(
        "investment_plan",
        f"Research consensus: BUY {TICKER}. Momentum is positive, fundamentals solid."
        f" Target: 78 EGP. Stop-loss: 63 EGP. Conviction: MEDIUM-HIGH.",
    )

    result = node(state)
    show_text("trader_investment_plan", result.get("trader_investment_plan", ""))
    show_json("execution_plan", result.get("execution_plan"))

    assert "trader_investment_plan" in result, "Missing trader_investment_plan"
    assert "execution_plan"         in result, "Missing execution_plan"
    return result


# ── 10. Risk Scorer (Deterministic) ──────────────────────────────────────────

def test_risk_scorer(trader_result=None):
    from tradingagents.agents.risk_mgmt.risk_scorer import create_risk_scorer_node
    from tradingagents.dataflows.config import set_config
    from tradingagents.default_config import DEFAULT_CONFIG
    set_config({**DEFAULT_CONFIG, "target_market": "EGX", "backtest_mode": True})

    node  = create_risk_scorer_node()
    state = base_state()

    execution_plan = (trader_result or {}).get("execution_plan") or {
        "action":    "BUY",
        "shares":    5000,
        "order_type": "limit",
        "limit_price": 68.5,
        "rationale": "Strong momentum + fundamental support",
    }
    state["execution_plan"]         = execution_plan
    state["trader_investment_plan"] = (trader_result or {}).get("trader_investment_plan", "BUY recommendation")

    result = node(state)
    show_json("risk_action",  {"action": result.get("risk_action")})
    show_json("risk_metrics", result.get("risk_metrics"))
    show_json("risk_assessment (partial)", {k: v for k, v in (result.get("risk_assessment") or {}).items() if k in ("action", "warnings", "veto_reason", "stop_loss_egp")})

    assert "risk_action" in result, "Missing risk_action"
    assert result.get("risk_action") in ("ALLOW", "WARN", "THROTTLE", "VETO"), \
        f"Unexpected risk_action: {result.get('risk_action')}"
    return result


# ── 11. Risk Debators (Aggressive / Conservative / Neutral) ──────────────────

def test_risk_debators(trader_result=None, scorer_result=None):
    from tradingagents.agents.risk_mgmt.aggressive_debator import create_risky_debator
    from tradingagents.agents.risk_mgmt.conservative_debator import create_safe_debator
    from tradingagents.agents.risk_mgmt.neutral_debator import create_neutral_debator

    llm = build_llm()

    state = base_state()
    state["trader_investment_plan"] = (trader_result or {}).get(
        "trader_investment_plan",
        f"BUY {TICKER}. Limit order at 68.50 EGP, 5,000 shares. Target 78 EGP, stop 63 EGP."
    )
    state["market_report"]       = f"Technical: bullish, RSI 58"
    state["sentiment_report"]    = "Social: neutral"
    state["news_report"]         = f"News: positive for banking sector"
    state["fundamentals_report"] = f"Fundamentals: ROE 18%, P/E 7.5x"

    # Run risky
    section("  10a. Risky Debator")
    risky_node = create_risky_debator(llm)
    r1 = risky_node(state)
    risky_resp = r1.get("risk_debate_state", {}).get("current_risky_response", "")
    show_text("risky_response", risky_resp)

    # Run conservative
    section("  10b. Conservative Debator")
    state["risk_debate_state"]["current_risky_response"] = risky_resp
    safe_node = create_safe_debator(llm)
    r2 = safe_node(state)
    safe_resp = r2.get("risk_debate_state", {}).get("current_safe_response", "")
    show_text("safe_response", safe_resp)

    # Run neutral
    section("  10c. Neutral Debator")
    state["risk_debate_state"]["current_safe_response"]  = safe_resp
    neutral_node = create_neutral_debator(llm)
    r3 = neutral_node(state)
    neutral_resp = r3.get("risk_debate_state", {}).get("current_neutral_response", "")
    show_text("neutral_response", neutral_resp)

    assert risky_resp,   "Risky debator returned empty response"
    assert safe_resp,    "Conservative debator returned empty response"
    assert neutral_resp, "Neutral debator returned empty response"
    return {"risky": risky_resp, "safe": safe_resp, "neutral": neutral_resp, "state": state}


# ── 12. Risk Manager (LLM Constitutional) ─────────────────────────────────────

def test_risk_manager(trader_result=None, debate_result=None, scorer_result=None):
    from tradingagents.agents.managers.risk_manager import create_risk_manager
    from tradingagents.dataflows.config import set_config
    from tradingagents.default_config import DEFAULT_CONFIG
    set_config({**DEFAULT_CONFIG, "target_market": "EGX", "backtest_mode": True})

    llm    = build_llm()
    memory = build_memory()
    node   = create_risk_manager(llm, memory)

    state = base_state()
    state["trader_investment_plan"] = (trader_result or {}).get(
        "trader_investment_plan",
        f"BUY {TICKER}. Limit at 68.50 EGP, 5,000 shares. Target 78 EGP. Stop-loss 63 EGP."
    )
    state["execution_plan"] = (trader_result or {}).get("execution_plan") or {
        "action": "BUY", "shares": 5000, "limit_price": 68.5
    }
    state["market_report"]       = f"Technical: bullish, RSI 58, MACD positive cross"
    state["sentiment_report"]    = "Social: neutral sentiment"
    state["news_report"]         = f"News: positive earnings for {TICKER}"
    state["fundamentals_report"] = f"Fundamentals: strong balance sheet, ROE 18%"
    state["risk_metrics"]        = (scorer_result or {}).get("risk_metrics") or {
        "position_pct":           0.032,
        "adv_participation_pct":  0.0022,
        "days_to_exit":           0.5,
        "per_trade_loss_pct":     0.072,
        "stop_distance_pct":      0.072,
        "atr_14":                 None,
    }

    # Inject debate history
    if debate_result:
        state["risk_debate_state"] = {
            **state["risk_debate_state"],
            "current_risky_response":   debate_result.get("risky", ""),
            "current_safe_response":    debate_result.get("safe", ""),
            "current_neutral_response": debate_result.get("neutral", ""),
            "history": (
                f"Risky: {debate_result.get('risky','')[:200]}\n"
                f"Safe: {debate_result.get('safe','')[:200]}\n"
                f"Neutral: {debate_result.get('neutral','')[:200]}"
            ),
        }

    result = node(state)
    show_text("final_trade_decision", result.get("final_trade_decision", ""))
    show_json("risk_assessment", result.get("risk_assessment"))

    decision = result.get("final_trade_decision", "")
    assert decision, "final_trade_decision is empty"
    assert any(s in decision.upper() for s in ["BUY", "SELL", "HOLD"]), \
        f"final_trade_decision does not contain BUY/SELL/HOLD: {decision[:100]}"
    return result


# ── 13. Full Graph (end-to-end) ───────────────────────────────────────────────

def test_full_graph():
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG

    config = {
        **DEFAULT_CONFIG,
        "target_market":    "EGX",
        "trading_currency": "EGP",
        "backtest_mode":    True,
        "max_debate_rounds":      1,
        "max_risk_discuss_rounds": 1,
    }

    graph = TradingAgentsGraph(
        selected_analysts=["market", "fundamentals", "news", "social"],
        config=config,
        debug=False,
    )

    print()
    info(f"Running full graph for {TICKER} on {TRADE_DATE}…")
    info("This takes 2–8 minutes depending on API speed. Streaming node updates below:\n")

    args = {**graph.propagator.get_graph_args(), "stream_mode": "updates"}

    # Prepare initial state
    init_state = {
        "company_of_interest": TICKER,
        "trade_date": TRADE_DATE,
        "messages": [("human", TICKER)],
        "portfolio_value": PORTFOLIO,
        "current_price": PRICE,
        "avg_daily_volume": ADV,
        "current_position": {},
    }

    accumulated = {}
    node_order = []
    t_start = time.time()

    for chunk in graph.graph.stream(init_state, **args):
        for node_name, node_updates in chunk.items():
            elapsed = time.time() - t_start
            node_order.append(node_name)
            print(f"  {GREEN}→{RESET} [{elapsed:5.1f}s] {BOLD}{node_name}{RESET}", end="")

            if isinstance(node_updates, dict):
                accumulated.update(node_updates)
                # Show key fields inline
                keys = [k for k in node_updates if not k.endswith("_messages") and not k.startswith("messages")]
                if keys:
                    print(f"  {DIM}({', '.join(keys[:4])}){RESET}", end="")
            print()

    total = time.time() - t_start
    print(f"\n  {BOLD}Total elapsed: {total:.1f}s{RESET}")
    print(f"  Node order: {' → '.join(node_order)}")

    # ── Final output summary ──
    print()
    decision = accumulated.get("final_trade_decision", "")
    ra = accumulated.get("risk_assessment") or {}

    show_text("final_trade_decision", decision[:500])
    show_json("risk_assessment (summary)", {
        k: ra.get(k)
        for k in ["final_decision", "action", "constitution_check", "stop_loss_egp", "warnings"]
        if k in ra
    })
    show_json("execution_plan", accumulated.get("execution_plan"))
    show_json("technical_analysis (confidence)", {
        "trend":      (accumulated.get("technical_analysis") or {}).get("trend_direction", {}).get("direction"),
        "confidence": (accumulated.get("technical_analysis") or {}).get("confidence_score"),
    })
    show_json("sentiment_blend", accumulated.get("sentiment_blend_result"))

    assert decision, "No final_trade_decision produced"
    assert any(s in decision.upper() for s in ["BUY", "SELL", "HOLD"]), \
        f"final_trade_decision lacks BUY/SELL/HOLD: {decision[:200]}"
    return accumulated


# =============================================================================
# =============================================================================
# Test 14 — Macro Provider
# =============================================================================

def test_macro_provider():
    """
    Test the deterministic EGX macro context provider in isolation.
    Verifies:
      - Returns all required keys
      - Numeric values are in plausible ranges
      - String values are valid labels
      - format_macro_context_for_prompt() produces a non-empty string
    """
    section("Macro Provider Standalone Test")
    from tradingagents.dataflows.macro_provider import (
        get_egx_macro_context,
        format_macro_context_for_prompt,
    )
    from tradingagents.default_config import DEFAULT_CONFIG

    t0 = time.time()
    ctx = get_egx_macro_context(as_of_date=TRADE_DATE, config=DEFAULT_CONFIG)
    elapsed = time.time() - t0

    ok(f"get_egx_macro_context() returned in {elapsed:.2f}s")
    show_json("macro_context", ctx, max_chars=800)

    # ── Structural checks ──
    required_keys = [
        "cbe_policy_rate", "usd_egp", "fx_trend", "egx30_trend",
        "tbill_yield_91d", "egypt_cpi", "real_rate", "spread_vs_tbill",
        "brent_usd", "imf_program_active", "as_of_date", "data_sources",
    ]
    for k in required_keys:
        assert k in ctx, f"Missing key: {k}"
    ok("All required keys present")

    # ── Value range checks ──
    assert 0.0 < ctx["cbe_policy_rate"] < 1.0, \
        f"cbe_policy_rate out of range: {ctx['cbe_policy_rate']}"
    assert 10.0 < ctx["usd_egp"] < 200.0, \
        f"usd_egp implausible: {ctx['usd_egp']}"
    assert ctx["fx_trend"] in ("stable", "depreciating", "appreciating", "unknown"), \
        f"fx_trend invalid: {ctx['fx_trend']}"
    assert ctx["egx30_trend"] in ("bullish", "bearish", "neutral", "unknown"), \
        f"egx30_trend invalid: {ctx['egx30_trend']}"
    assert isinstance(ctx["imf_program_active"], bool), "imf_program_active not bool"
    assert ctx["as_of_date"] == TRADE_DATE, "as_of_date mismatch"
    ok("All value range checks passed")

    # ── Derived signal check ──
    expected_real_rate = round(ctx["cbe_policy_rate"] - ctx["egypt_cpi"], 4)
    assert abs(ctx["real_rate"] - expected_real_rate) < 1e-6, \
        f"real_rate mismatch: got {ctx['real_rate']}, expected {expected_real_rate}"
    ok(f"real_rate = {ctx['real_rate']*100:.2f}% (cbe_rate - cpi, correct)")

    # ── Prompt formatter check ──
    prompt_text = format_macro_context_for_prompt(ctx)
    assert len(prompt_text) > 100, "Prompt text too short"
    assert "CBE" in prompt_text or "policy rate" in prompt_text.lower(), \
        "Prompt text missing CBE rate"
    ok(f"format_macro_context_for_prompt() produced {len(prompt_text)}-char string")
    section("Prompt preview (first 400 chars)")
    info(prompt_text[:400])

    # ── Graceful degradation: None input ──
    fallback = format_macro_context_for_prompt(None)
    assert "unavailable" in fallback.lower(), "None fallback should mention 'unavailable'"
    ok("format_macro_context_for_prompt(None) degrades gracefully")

    # ── DataPrefetcher integration ──
    section("DataPrefetcher macro integration")
    from tradingagents.graph.prefetch import DataPrefetcher
    prefetcher = DataPrefetcher(config={**DEFAULT_CONFIG, "target_market": "EGX"})
    prefetched = prefetcher.fetch_all(TICKER, TRADE_DATE)

    assert "macro_context" in prefetched, "macro_context missing from prefetched dict"
    mc = prefetched["macro_context"]
    assert mc is not None, "macro_context is None from prefetcher"
    assert mc.get("cbe_policy_rate") is not None, "cbe_policy_rate missing in prefetched macro"
    ok(f"DataPrefetcher.fetch_all() includes macro_context (keys: {list(mc.keys())[:6]}...)")

    show_json("prefetched macro_context", mc, max_chars=400)

    return ctx


# =============================================================================
# Master runner
# =============================================================================

if __name__ == "__main__":
    # Apply EGX config globally
    from tradingagents.dataflows.config import set_config
    from tradingagents.default_config import DEFAULT_CONFIG
    set_config({**DEFAULT_CONFIG, "target_market": "EGX"})

    hdr(f"EGX Agent Test Suite  —  {TICKER}  @  {TRADE_DATE}")
    print(f"  Portfolio: {PORTFOLIO:,.0f} EGP | Price: {PRICE} EGP | ADV: {ADV:,.0f} shares")

    # ---------- Unit tests (run sequentially so results feed forward) ----------

    hdr("LAYER 0 — Profiling")
    run_test("01 · Investor Profiling", test_investor_profiling)

    hdr("LAYER 1 — Data Analysts")
    mkt  = run_test("02 · Market Analyst (deterministic)",       test_market_analyst_deterministic)
    fund = run_test("03 · Fundamentals Analyst (deterministic)", test_fundamentals_analyst)
    news = run_test("04 · News Analyst (LLM)",                   test_news_analyst)
    soc  = run_test("05 · Social Media Analyst (LLM)",           test_social_media_analyst)

    hdr("LAYER 2 — Research Debate")
    bull = run_test("06 · Bull Researcher (LLM)",   lambda: test_bull_researcher(mkt, fund, news, soc))
    bear = run_test("07 · Bear Researcher (LLM)",   lambda: test_bear_researcher(mkt, fund, news, soc))
    res  = run_test("08 · Research Manager (LLM)",  lambda: test_research_manager(bull, bear, mkt, fund, news, soc))

    hdr("LAYER 3 — Execution")
    trd  = run_test("09 · Trader (LLM)",            lambda: test_trader(res))

    hdr("LAYER 4 — Risk Pipeline")
    scr  = run_test("10 · Risk Scorer (deterministic)",  lambda: test_risk_scorer(trd))
    deb  = run_test("11 · Risk Debators (3 x LLM)",      lambda: test_risk_debators(trd, scr))
    rmgr = run_test("12 · Risk Manager (LLM Constitutional)", lambda: test_risk_manager(trd, deb, scr))

    hdr("LAYER 5 — Full System Integration")
    full = run_test("13 · Full Graph (end-to-end)",       test_full_graph)

    hdr("LAYER 6 — Macro Indicators")
    run_test("14 · Macro Provider (deterministic)",       test_macro_provider)

    # ---------- Summary --------------------------------------------------------

    hdr("TEST SUMMARY")
    passes = sum(1 for v in RESULTS.values() if v["status"] == "PASS")
    fails  = sum(1 for v in RESULTS.values() if v["status"] == "FAIL")

    for name, r in RESULTS.items():
        icon = f"{GREEN}PASS{RESET}" if r["status"] == "PASS" else f"{RED}FAIL{RESET}"
        elapsed_str = f"{r['elapsed']:.1f}s"
        err_str = f"  — {r['error'][:80]}" if r.get("error") else ""
        print(f"  {icon}  [{elapsed_str:>6}]  {name}{err_str}")

    print()
    if fails == 0:
        print(f"  {GREEN}{BOLD}ALL {passes} TESTS PASSED ✓{RESET}")
    else:
        print(f"  {YELLOW}{BOLD}{passes} passed, {RED}{fails} FAILED{RESET}")
    print()
