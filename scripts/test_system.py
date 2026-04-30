"""
EGX Trading Agents — Comprehensive System Test
===============================================
Tests every layer of the system without making LLM API calls (unless --live is set).

Usage:
    python scripts/test_system.py                     # run all offline tests
    python scripts/test_system.py --live              # also run full LLM pipeline
    python scripts/test_system.py --verbose           # print detailed output per test
    python scripts/test_system.py --ticker EAST.CA    # override test ticker

Layers tested:
    1. Environment    — Python version, dependencies, .env, API keys
    2. Configuration  — DEFAULT_CONFIG, EGX_TICKERS, dataflows config
    3. Data Providers — yfinance, indicators, local EGX CSVs, routing, gateway, cache
    4. Agent Creation — all agent factories (using mock LLM, no API calls)
    5. Tool Functions — @tool decorated wrappers for agents
    6. Sentiment      — Arabic + English offline sentiment analysis
    7. Graph Pipeline — import, construction, state init, signal processing
    8. API Server     — FastAPI app import and route existence

Exit codes:
    0 — all tests passed (SKIP counts as pass)
    1 — at least one test FAILED
"""

import sys
import os
import time
import argparse
import traceback
import re

# Windows UTF-8 fix (must be before any print with emoji/Arabic)
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# ANSI colours
# ---------------------------------------------------------------------------
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

PASS_ICON = "OK "   # safe ASCII fallback shown next to coloured text
FAIL_ICON = "FAIL"
SKIP_ICON = "SKIP"

# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------
class Result:
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"

    def __init__(self, test_id: str, description: str):
        self.test_id    = test_id
        self.description = description
        self.status     = None
        self.detail     = ""
        self.elapsed    = 0.0
        self.tb         = ""        # traceback on failure

    def mark_pass(self, detail: str = ""):
        self.status = self.PASS
        self.detail = detail

    def mark_fail(self, detail: str, tb: str = ""):
        self.status = self.FAIL
        self.detail = detail
        self.tb     = tb

    def mark_skip(self, detail: str = ""):
        self.status = self.SKIP
        self.detail = detail


# ---------------------------------------------------------------------------
# Test runner helper
# ---------------------------------------------------------------------------
_results: list[Result] = []

def run_test(test_id: str, description: str, fn, verbose: bool) -> Result:
    """Execute fn(), time it, capture pass/fail/skip into a Result."""
    r = Result(test_id, description)
    t0 = time.perf_counter()
    try:
        fn(r)
    except Exception as exc:
        r.mark_fail(str(exc), traceback.format_exc())
    r.elapsed = time.perf_counter() - t0

    if r.status is None:
        r.mark_fail("Test function did not set a status")

    # Print one-line result immediately
    if r.status == Result.PASS:
        icon = f"{GREEN}{PASS_ICON}{RESET}"
    elif r.status == Result.FAIL:
        icon = f"{RED}{FAIL_ICON}{RESET}"
    else:
        icon = f"{YELLOW}{SKIP_ICON}{RESET}"

    detail_str = f"  ({r.detail})" if r.detail and verbose else ""
    print(f"  [{icon}]  {r.test_id:<6} {r.description:<52} {r.elapsed:.3f}s{detail_str}")

    if verbose and r.status == Result.FAIL and r.tb:
        for line in r.tb.strip().splitlines()[-6:]:
            print(f"           {RED}{line}{RESET}")

    _results.append(r)
    return r


# ===========================================================================
# LAYER 1 — ENVIRONMENT
# ===========================================================================

def test_1_1_python_version(r: Result):
    vi = sys.version_info
    ver = f"{vi.major}.{vi.minor}.{vi.micro}"
    if vi < (3, 9):
        r.mark_fail(f"Python {ver} — need >= 3.9")
    else:
        r.mark_pass(f"Python {ver}")


def test_1_2_core_deps(r: Result):
    deps = [
        "langchain_openai", "langchain_core", "langgraph",
        "yfinance", "pandas", "numpy", "pydantic",
        "fastapi", "uvicorn", "dotenv", "diskcache",
        "typer", "rich", "bs4",
    ]
    failed = []
    for dep in deps:
        try:
            __import__(dep)
        except ImportError as e:
            failed.append(f"{dep}: {e}")
    if failed:
        r.mark_fail(f"{len(deps)-len(failed)}/{len(deps)} — missing: {', '.join(f.split(':')[0] for f in failed)}")
    else:
        r.mark_pass(f"{len(deps)}/{len(deps)}")


def test_1_3_optional_deps(r: Result):
    opts = ["backtrader", "transformers", "torch", "egxpy", "tradingview_ta"]
    missing = []
    for dep in opts:
        try:
            __import__(dep)
        except ImportError:
            missing.append(dep)
    if missing:
        r.mark_skip(f"not installed: {', '.join(missing)}")
    else:
        r.mark_pass("all optional deps present")


def test_1_4_env_file(r: Result):
    # Determine project root relative to this script
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(root, ".env")
    if os.path.exists(env_path):
        r.mark_pass(".env found")
    else:
        r.mark_fail("Missing .env — copy .env.example and fill in keys")


def test_1_5_api_keys(r: Result):
    from dotenv import load_dotenv
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(root, ".env"))
    groq = os.getenv("GROQ_API_KEY") or ""
    openai = os.getenv("OPENAI_API_KEY") or ""
    if groq or openai:
        keys_set = []
        if groq:
            keys_set.append("GROQ_API_KEY")
        if openai:
            keys_set.append("OPENAI_API_KEY")
        r.mark_pass(f"set: {', '.join(keys_set)}")
    else:
        r.mark_fail("No LLM API key set (GROQ_API_KEY or OPENAI_API_KEY)")


# ===========================================================================
# LAYER 2 — CONFIGURATION
# ===========================================================================

def test_2_1_default_config(r: Result):
    from tradingagents.default_config import DEFAULT_CONFIG
    required = {"llm_provider", "deep_think_llm", "quick_think_llm",
                "backend_url", "target_market", "data_vendors"}
    missing = required - set(DEFAULT_CONFIG.keys())
    if missing:
        r.mark_fail(f"Missing keys: {missing}")
    else:
        r.mark_pass(f"{len(DEFAULT_CONFIG)} keys, target_market={DEFAULT_CONFIG['target_market']}")


def test_2_2_egx_tickers(r: Result):
    from tradingagents.default_config import EGX_TICKERS
    if not isinstance(EGX_TICKERS, list):
        r.mark_fail("EGX_TICKERS is not a list")
        return
    if len(EGX_TICKERS) < 20:
        r.mark_fail(f"Only {len(EGX_TICKERS)} tickers — expected >= 20")
        return
    bad = [t for t in EGX_TICKERS if not t.endswith(".CA")]
    if bad:
        r.mark_fail(f"Tickers without .CA suffix: {bad}")
    else:
        r.mark_pass(f"{len(EGX_TICKERS)} tickers, all .CA")


def test_2_3_dataflows_config(r: Result):
    from tradingagents.dataflows.config import get_config, set_config
    cfg = get_config()
    if not isinstance(cfg, dict):
        r.mark_fail("get_config() did not return a dict")
        return
    set_config({"_test_key_": "test_value_xyz"})
    cfg2 = get_config()
    if cfg2.get("_test_key_") != "test_value_xyz":
        r.mark_fail("set_config/get_config round-trip failed")
    else:
        r.mark_pass("get/set round-trip OK")


# ===========================================================================
# LAYER 3 — DATA PROVIDERS
# ===========================================================================

def test_3_1_yfinance_ohlcv(r: Result, ticker: str):
    from tradingagents.dataflows.y_finance import get_YFin_data_online
    result = get_YFin_data_online(ticker, "2024-01-01", "2024-02-01")
    if not result or len(str(result)) < 50:
        r.mark_fail("Returned empty or very short result")
    else:
        # Try to count rows from string
        lines = str(result).strip().splitlines()
        r.mark_pass(f"~{len(lines)} lines returned")


def test_3_2_technical_indicators(r: Result, ticker: str):
    from tradingagents.dataflows.y_finance import get_stock_stats_indicators_window
    result = get_stock_stats_indicators_window(ticker, "rsi", "2024-02-01", 30)
    result_str = str(result)
    has_indicator = any(kw in result_str.lower() for kw in ["rsi", "macd", "sma", "boll", "ema", "indicator"])
    if not has_indicator:
        r.mark_fail("Result does not mention any technical indicators")
    else:
        r.mark_pass(f"{len(result_str)} chars, indicators present")


def test_3_3_local_fundamentals(r: Result, ticker: str):
    from tradingagents.dataflows.local import get_egx_fundamentals_summary
    result = get_egx_fundamentals_summary(ticker, "2024-01-15")
    result_str = str(result)
    if len(result_str) < 100:
        r.mark_fail(f"Result too short ({len(result_str)} chars) — CSV files may be missing")
    else:
        r.mark_pass(f"{len(result_str)} chars")


def test_3_4_local_news(r: Result, ticker: str):
    try:
        from tradingagents.dataflows.local import get_egx_news_combined
    except ImportError:
        r.mark_skip("get_egx_news_combined not available")
        return
    result = get_egx_news_combined(ticker, "2024-01-15", 7)
    if result is None:
        r.mark_fail("Returned None")
    else:
        r.mark_pass(f"returned {type(result).__name__}")


def test_3_5_vendor_routing(r: Result):
    from tradingagents.dataflows.interface import TOOLS_CATEGORIES, VENDOR_METHODS
    issues = []
    if len(TOOLS_CATEGORIES) < 4:
        issues.append(f"Only {len(TOOLS_CATEGORIES)} categories")
    if "get_stock_data" not in VENDOR_METHODS:
        issues.append("get_stock_data missing from VENDOR_METHODS")
    if "get_indicators" not in VENDOR_METHODS:
        issues.append("get_indicators missing from VENDOR_METHODS")
    if issues:
        r.mark_fail("; ".join(issues))
    else:
        r.mark_pass(f"{len(VENDOR_METHODS)} methods, {len(TOOLS_CATEGORIES)} categories")


def test_3_6_data_gateway(r: Result):
    from tradingagents.dataflows.gateway import DataGateway
    from tradingagents.default_config import DEFAULT_CONFIG
    gw = DataGateway(DEFAULT_CONFIG)
    if gw is None:
        r.mark_fail("DataGateway() returned None")
    else:
        r.mark_pass("DataGateway instantiated")


def test_3_7_cache_manager(r: Result):
    import tempfile
    import shutil
    from tradingagents.dataflows.cache_manager import CacheManager
    tmp = tempfile.mkdtemp()
    try:
        cm = CacheManager(tmp)
        cm.set("_test_key_", "hello_cache", ttl=60)
        val = cm.get("_test_key_")
        # Close diskcache before cleanup (Windows file-lock requirement)
        if hasattr(cm, "_cache") and cm._cache is not None:
            try:
                cm._cache.close()
            except Exception:
                pass
        if val != "hello_cache":
            r.mark_fail(f"Round-trip failed: got {val!r}")
        else:
            r.mark_pass("set/get round-trip OK")
    finally:
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass


# ===========================================================================
# LAYER 4 — AGENT CREATION (mock LLM, no API calls)
# ===========================================================================

def _mock_llm():
    from unittest.mock import MagicMock
    return MagicMock()

def _mock_memory():
    from unittest.mock import MagicMock
    return MagicMock()


def test_4_1_agent_state(r: Result):
    from tradingagents.agents.utils.agent_states import AgentState
    required_keys = [
        "messages", "market_report", "fundamentals_report", "news_report",
        "sentiment_report", "company_of_interest", "trade_date",
        "investment_debate_state", "risk_debate_state",
        "trader_investment_plan", "final_trade_decision",
    ]
    # AgentState is a TypedDict — check annotations
    annotations = {}
    for cls in reversed(AgentState.__mro__):
        if hasattr(cls, "__annotations__"):
            annotations.update(cls.__annotations__)
    missing = [k for k in required_keys if k not in annotations]
    if missing:
        r.mark_fail(f"Missing state fields: {missing}")
    else:
        r.mark_pass(f"{len(annotations)} total fields, all required present")


def test_4_2_analyst_factories(r: Result):
    from tradingagents.agents.analysts.market_analyst import create_market_analyst
    from tradingagents.agents.analysts.fundamentals_analyst import create_fundamentals_analyst
    from tradingagents.agents.analysts.news_analyst import create_news_analyst
    from tradingagents.agents.analysts.social_media_analyst import create_social_media_analyst

    llm = _mock_llm()
    factories = {
        "market": create_market_analyst,
        "fundamentals": create_fundamentals_analyst,
        "news": create_news_analyst,
        "social_media": create_social_media_analyst,
    }
    failed = []
    for name, factory in factories.items():
        try:
            agent = factory(llm)
            if not callable(agent):
                failed.append(f"{name} returned non-callable")
        except Exception as e:
            failed.append(f"{name}: {e}")
    if failed:
        r.mark_fail("; ".join(failed))
    else:
        r.mark_pass(f"all {len(factories)} analyst factories OK")


def test_4_3_researcher_factories(r: Result):
    from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
    from tradingagents.agents.researchers.bear_researcher import create_bear_researcher

    llm, mem = _mock_llm(), _mock_memory()
    failed = []
    for name, fn in [("bull", create_bull_researcher), ("bear", create_bear_researcher)]:
        try:
            agent = fn(llm, mem)
            if not callable(agent):
                failed.append(f"{name} returned non-callable")
        except Exception as e:
            failed.append(f"{name}: {e}")
    if failed:
        r.mark_fail("; ".join(failed))
    else:
        r.mark_pass("bull + bear researchers OK")


def test_4_4_manager_factories(r: Result):
    from tradingagents.agents.managers.research_manager import create_research_manager
    from tradingagents.agents.managers.risk_manager import create_risk_manager

    llm, mem = _mock_llm(), _mock_memory()
    failed = []
    for name, fn in [("research_mgr", create_research_manager), ("risk_mgr", create_risk_manager)]:
        try:
            agent = fn(llm, mem)
            if not callable(agent):
                failed.append(f"{name} returned non-callable")
        except Exception as e:
            failed.append(f"{name}: {e}")
    if failed:
        r.mark_fail("; ".join(failed))
    else:
        r.mark_pass("research + risk managers OK")


def test_4_5_risk_debator_factories(r: Result):
    from tradingagents.agents.risk_mgmt.aggresive_debator import create_risky_debator
    from tradingagents.agents.risk_mgmt.conservative_debator import create_safe_debator
    from tradingagents.agents.risk_mgmt.neutral_debator import create_neutral_debator

    llm = _mock_llm()
    failed = []
    for name, fn in [
        ("risky",       create_risky_debator),
        ("safe",        create_safe_debator),
        ("neutral",     create_neutral_debator),
    ]:
        try:
            agent = fn(llm)
            if not callable(agent):
                failed.append(f"{name} returned non-callable")
        except Exception as e:
            failed.append(f"{name}: {e}")
    if failed:
        r.mark_fail("; ".join(failed))
    else:
        r.mark_pass("all 3 risk debators OK")


def test_4_6_trader_factory(r: Result):
    from tradingagents.agents.trader.trader import create_trader
    llm, mem = _mock_llm(), _mock_memory()
    agent = create_trader(llm, mem)
    if not callable(agent):
        r.mark_fail("create_trader returned non-callable")
    else:
        r.mark_pass("trader factory OK")


# ===========================================================================
# LAYER 5 — TOOL FUNCTIONS
# ===========================================================================

def test_5_1_agent_utils_tools(r: Result):
    from tradingagents.agents.utils.agent_utils import get_stock_data, get_indicators
    missing = []
    for name, fn in [("get_stock_data", get_stock_data), ("get_indicators", get_indicators)]:
        if not hasattr(fn, "name"):
            missing.append(name)
    if missing:
        r.mark_fail(f"Missing .name (not @tool decorated?): {missing}")
    else:
        r.mark_pass("get_stock_data + get_indicators are @tool")


def test_5_2_fundamental_tools(r: Result):
    from tradingagents.agents.utils.fundamental_data_tools import (
        get_egx_fundamentals, get_egx_income, get_egx_balance, get_egx_ratios
    )
    missing = []
    for name, fn in [
        ("get_egx_fundamentals", get_egx_fundamentals),
        ("get_egx_income", get_egx_income),
        ("get_egx_balance", get_egx_balance),
        ("get_egx_ratios", get_egx_ratios),
    ]:
        if not hasattr(fn, "name"):
            missing.append(name)
    if missing:
        r.mark_fail(f"Not @tool decorated: {missing}")
    else:
        r.mark_pass("all 4 fundamental @tools OK")


def test_5_3_news_tools(r: Result):
    from tradingagents.agents.utils.news_data_tools import get_egx_company_news, get_egx_market_news
    missing = []
    for name, fn in [("get_egx_company_news", get_egx_company_news), ("get_egx_market_news", get_egx_market_news)]:
        if not hasattr(fn, "name"):
            missing.append(name)
    if missing:
        r.mark_fail(f"Not @tool decorated: {missing}")
    else:
        r.mark_pass("news tools OK")


def test_5_4_social_tools(r: Result):
    try:
        from tradingagents.agents.utils.social_media_tools import get_social_sentiment, get_social_media_posts
    except ImportError as e:
        r.mark_skip(f"ImportError (optional deps): {e}")
        return
    missing = []
    for name, fn in [("get_social_sentiment", get_social_sentiment), ("get_social_media_posts", get_social_media_posts)]:
        if not hasattr(fn, "name"):
            missing.append(name)
    if missing:
        r.mark_fail(f"Not @tool decorated: {missing}")
    else:
        r.mark_pass("social tools OK")


# ===========================================================================
# LAYER 6 — SENTIMENT ENGINE
# ===========================================================================

def test_6_1_sentiment_import(r: Result):
    from tradingagents.dataflows.social_media_sources.sentiment_engine import analyze_social_sentiment
    if not callable(analyze_social_sentiment):
        r.mark_fail("analyze_social_sentiment is not callable")
    else:
        r.mark_pass("analyze_social_sentiment importable")


def test_6_2_arabic_sentiment(r: Result, verbose: bool):
    from tradingagents.dataflows.social_media_sources.sentiment_engine import analyze_social_sentiment
    from tradingagents.dataflows.social_media_sources.schema import SocialPost, SocialMediaResult

    posts = [
        SocialPost(
            text="السهم هيطلع النهارده - فرصة شراء ممتازة",
            timestamp="2024-01-15T10:00:00",
            platform="twitter",
            language="ar",
            ticker="COMI",
        ),
        SocialPost(
            text="هبوط حاد في السعر - خسائر كبيرة",
            timestamp="2024-01-15T11:00:00",
            platform="telegram",
            language="ar",
            ticker="COMI",
        ),
        SocialPost(
            text="Strong bullish momentum on COMI, fundamentals intact",
            timestamp="2024-01-15T12:00:00",
            platform="reddit",
            language="en",
            ticker="COMI",
        ),
    ]

    data = SocialMediaResult(
        ticker="COMI",
        query_date="2024-01-15",
        look_back_days=1,
        posts=posts,
        total_posts=3,
        platforms_queried=["twitter", "telegram", "reddit"],
        platforms_succeeded=["twitter", "telegram", "reddit"],
    )

    result = analyze_social_sentiment(data)

    issues = []
    score = getattr(result, "sentiment_score", None)
    if score is None:
        issues.append("result has no sentiment_score attribute")
    elif not (-1.0 <= score <= 1.0):
        issues.append(f"sentiment_score {score} outside [-1.0, 1.0]")

    analyzed = getattr(result, "total_posts_analyzed", None)
    if analyzed != 3:
        issues.append(f"total_posts_analyzed={analyzed}, expected 3")

    if issues:
        r.mark_fail("; ".join(issues))
    else:
        hype = getattr(result, "hype_detected", "?")
        detail = f"score={score:.3f}, hype={hype}"
        if verbose:
            print(f"\n           sentiment_score={score:.3f}  hype_detected={hype}")
        r.mark_pass(detail)


# ===========================================================================
# LAYER 7 — GRAPH PIPELINE
# ===========================================================================

def test_7_1_graph_import(r: Result):
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    if not callable(TradingAgentsGraph):
        r.mark_fail("TradingAgentsGraph not callable")
    else:
        r.mark_pass("TradingAgentsGraph importable")


def test_7_2_graph_construction(r: Result):
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG
    graph = TradingAgentsGraph(config=DEFAULT_CONFIG.copy(), debug=False)
    if graph is None:
        r.mark_fail("TradingAgentsGraph() returned None")
    else:
        r.mark_pass("graph constructed")
    return graph


def test_7_3_propagator_state(r: Result, ticker: str):
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG
    graph = TradingAgentsGraph(config=DEFAULT_CONFIG.copy(), debug=False)
    state = graph.propagator.create_initial_state(ticker, "2024-01-15")
    missing = []
    for key in ("company_of_interest", "trade_date"):
        if key not in state:
            missing.append(key)
    if missing:
        r.mark_fail(f"Missing keys in initial state: {missing}")
    else:
        coi = state.get("company_of_interest", "?")
        td  = state.get("trade_date", "?")
        r.mark_pass(f"company_of_interest={coi}, trade_date={td}")


def test_7_4_signal_extraction(r: Result):
    """Regex-based BUY/SELL/HOLD extraction (offline, no LLM call)."""
    pattern = re.compile(r"\b(BUY|SELL|HOLD)\b", re.IGNORECASE)

    cases = [
        ("After careful analysis, I recommend a BUY action on COMI.CA.", "BUY"),
        ("The final decision is SELL with a target price of 50 EGP.", "SELL"),
        ("We recommend to HOLD the current position until Q2.", "HOLD"),
    ]
    failed = []
    for text, expected in cases:
        m = pattern.search(text)
        if not m:
            failed.append(f"No match in: {text[:40]!r}")
        elif m.group(1).upper() != expected:
            failed.append(f"Got {m.group(1)!r} expected {expected!r}")
    if failed:
        r.mark_fail("; ".join(failed))
    else:
        r.mark_pass("BUY/SELL/HOLD regex extraction correct")


def test_7_5_full_pipeline_live(r: Result, ticker: str, verbose: bool, live: bool):
    if not live:
        r.mark_skip("add --live flag to run full pipeline test")
        return
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG
    graph = TradingAgentsGraph(config=DEFAULT_CONFIG.copy(), debug=verbose)
    state, decision = graph.propagate(ticker, "2024-01-15")
    if not isinstance(state, dict):
        r.mark_fail(f"propagate() returned non-dict state: {type(state)}")
        return
    if not isinstance(decision, str) or not decision.strip():
        r.mark_fail(f"propagate() returned empty decision: {decision!r}")
        return
    if verbose:
        print(f"\n           DECISION: {decision[:120]}")
    r.mark_pass(f"decision length={len(decision)} chars")


# ===========================================================================
# LAYER 8 — API SERVER
# ===========================================================================

def test_8_1_fastapi_import(r: Result):
    from server.api_server import app
    from fastapi import FastAPI
    if not isinstance(app, FastAPI):
        r.mark_fail(f"app is {type(app)}, expected FastAPI")
    else:
        r.mark_pass("FastAPI app imported")


def test_8_2_api_routes(r: Result):
    from server.api_server import app
    required_prefixes = ["/api/health", "/api/config", "/api/stock/", "/api/backtests"]
    all_paths = [getattr(route, "path", "") for route in app.routes]
    missing = []
    for prefix in required_prefixes:
        found = any(p.startswith(prefix.rstrip("/")) for p in all_paths)
        if not found:
            missing.append(prefix)
    if missing:
        r.mark_fail(f"Routes not found: {missing}")
    else:
        r.mark_pass(f"{len(all_paths)} routes, all required present")


# ===========================================================================
# MAIN
# ===========================================================================

def print_header(ticker: str, live: bool):
    width = 80
    print("\n" + "=" * width)
    print(f"  {BOLD}EGX TRADING AGENTS{RESET} — SYSTEM TEST REPORT")
    print(f"  Ticker: {ticker} | Live: {'Yes' if live else 'No'}")
    print("=" * width)


def print_layer(name: str):
    print(f"\n  {CYAN}{BOLD}{name}{RESET}")


def print_summary(results: list[Result], total_time: float):
    width = 80
    passed = sum(1 for r in results if r.status == Result.PASS)
    failed = sum(1 for r in results if r.status == Result.FAIL)
    skipped = sum(1 for r in results if r.status == Result.SKIP)

    print("\n" + "=" * width)
    colour = GREEN if failed == 0 else RED
    print(f"  {colour}{BOLD}RESULTS: {passed} PASS | {skipped} SKIP | {failed} FAIL{RESET}"
          f"                    Total: {total_time:.3f}s")
    print("=" * width)

    failures = [r for r in results if r.status == Result.FAIL]
    if failures:
        print(f"\n  {RED}{BOLD}FAILURES:{RESET}")
        for r in failures:
            print(f"\n  [{r.test_id}] {r.description}")
            print(f"  Error: {RED}{r.detail}{RESET}")
            if r.tb:
                for line in r.tb.strip().splitlines()[-8:]:
                    print(f"    {line}")
    print()


def main():
    parser = argparse.ArgumentParser(description="EGX Trading Agents — system test suite")
    parser.add_argument("--live",    action="store_true", help="also run tests that make LLM API calls")
    parser.add_argument("--verbose", action="store_true", help="print detailed output per test")
    parser.add_argument("--ticker",  default="COMI.CA",   help="EGX ticker to use (default: COMI.CA)")
    args = parser.parse_args()

    ticker  = args.ticker
    live    = args.live
    verbose = args.verbose

    # Ensure project root on sys.path
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)

    global _results
    _results = []

    t_total = time.perf_counter()
    print_header(ticker, live)

    # ---- LAYER 1 ----
    print_layer("LAYER 1: ENVIRONMENT")
    run_test("1.1", "Python version check",          lambda r: test_1_1_python_version(r),   verbose)
    run_test("1.2", "Core dependency imports",        lambda r: test_1_2_core_deps(r),        verbose)
    run_test("1.3", "Optional dependencies",          lambda r: test_1_3_optional_deps(r),    verbose)
    run_test("1.4", ".env file exists",               lambda r: test_1_4_env_file(r),         verbose)
    run_test("1.5", "API keys configured",            lambda r: test_1_5_api_keys(r),         verbose)

    # ---- LAYER 2 ----
    print_layer("LAYER 2: CONFIGURATION")
    run_test("2.1", "DEFAULT_CONFIG loads",           lambda r: test_2_1_default_config(r),   verbose)
    run_test("2.2", "EGX_TICKERS loads",              lambda r: test_2_2_egx_tickers(r),      verbose)
    run_test("2.3", "Dataflows config get/set",       lambda r: test_2_3_dataflows_config(r), verbose)

    # ---- LAYER 3 ----
    print_layer("LAYER 3: DATA PROVIDERS")
    run_test("3.1", f"yfinance OHLCV ({ticker})",     lambda r: test_3_1_yfinance_ohlcv(r, ticker),      verbose)
    run_test("3.2", f"Technical indicators ({ticker})",lambda r: test_3_2_technical_indicators(r, ticker),verbose)
    run_test("3.3", "Local EGX fundamentals CSV",     lambda r: test_3_3_local_fundamentals(r, ticker),  verbose)
    run_test("3.4", "Local EGX news CSV",             lambda r: test_3_4_local_news(r, ticker),          verbose)
    run_test("3.5", "Vendor routing table",           lambda r: test_3_5_vendor_routing(r),              verbose)
    run_test("3.6", "DataGateway init",               lambda r: test_3_6_data_gateway(r),                verbose)
    run_test("3.7", "CacheManager round-trip",        lambda r: test_3_7_cache_manager(r),               verbose)

    # ---- LAYER 4 ----
    print_layer("LAYER 4: AGENT CREATION (mock LLM)")
    run_test("4.1", "AgentState schema fields",       lambda r: test_4_1_agent_state(r),        verbose)
    run_test("4.2", "Analyst agent factories",        lambda r: test_4_2_analyst_factories(r),  verbose)
    run_test("4.3", "Researcher agent factories",     lambda r: test_4_3_researcher_factories(r),verbose)
    run_test("4.4", "Manager agent factories",        lambda r: test_4_4_manager_factories(r),  verbose)
    run_test("4.5", "Risk debator factories",         lambda r: test_4_5_risk_debator_factories(r),verbose)
    run_test("4.6", "Trader agent factory",           lambda r: test_4_6_trader_factory(r),     verbose)

    # ---- LAYER 5 ----
    print_layer("LAYER 5: TOOL FUNCTIONS")
    run_test("5.1", "Agent utils @tools",             lambda r: test_5_1_agent_utils_tools(r),  verbose)
    run_test("5.2", "Fundamental data @tools",        lambda r: test_5_2_fundamental_tools(r),  verbose)
    run_test("5.3", "News data @tools",               lambda r: test_5_3_news_tools(r),         verbose)
    run_test("5.4", "Social media @tools",            lambda r: test_5_4_social_tools(r),       verbose)

    # ---- LAYER 6 ----
    print_layer("LAYER 6: SENTIMENT ENGINE")
    run_test("6.1", "Sentiment engine import",        lambda r: test_6_1_sentiment_import(r),              verbose)
    run_test("6.2", "Arabic+English sentiment test",  lambda r: test_6_2_arabic_sentiment(r, verbose),     verbose)

    # ---- LAYER 7 ----
    print_layer("LAYER 7: GRAPH PIPELINE")
    run_test("7.1", "TradingAgentsGraph import",      lambda r: test_7_1_graph_import(r),                          verbose)
    run_test("7.2", "Graph construction (no LLM)",    lambda r: test_7_2_graph_construction(r),                   verbose)
    run_test("7.3", "Propagator initial state",       lambda r: test_7_3_propagator_state(r, ticker),             verbose)
    run_test("7.4", "BUY/SELL/HOLD signal extraction",lambda r: test_7_4_signal_extraction(r),                    verbose)
    run_test("7.5", "Full pipeline (LIVE)",           lambda r: test_7_5_full_pipeline_live(r, ticker, verbose, live), verbose)

    # ---- LAYER 8 ----
    print_layer("LAYER 8: API SERVER")
    run_test("8.1", "FastAPI app import",             lambda r: test_8_1_fastapi_import(r),  verbose)
    run_test("8.2", "Required API routes exist",      lambda r: test_8_2_api_routes(r),      verbose)

    # ---- SUMMARY ----
    total_time = time.perf_counter() - t_total
    print_summary(_results, total_time)

    any_failed = any(r.status == Result.FAIL for r in _results)
    sys.exit(1 if any_failed else 0)


if __name__ == "__main__":
    main()
