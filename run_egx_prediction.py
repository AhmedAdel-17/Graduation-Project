"""
EGX Stock Analysis - Direct Prediction
=======================================
Fetches EGX stock data and generates trading recommendation
"""

import sys
import os
import io
import logging

log_file = None
logger = logging.getLogger("tradingagents.prediction")

def log(msg):
    print(msg)
    if log_file:
        try:
            log_file.write(msg + "\n")
            log_file.flush()
        except ValueError:
            pass # File might be closed
    sys.stdout.flush()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env before any tradingagents import so env vars are available at module init
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.dataflows.y_finance import get_YFin_data_online
from datetime import datetime, timedelta


def _resolve_llm_api_key(backend_url: str) -> str:
    """Resolve the API key that matches the configured backend URL.

    Priority: NVIDIA Build > DeepSeek direct.
    OPENAI_API_KEY is intentionally never read (maps to Groq in this repo's .env).
    """
    if "nvidia" in backend_url:
        key = os.getenv("NVIDIA_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    else:
        key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError(
            "No LLM API key found. Set NVIDIA_API_KEY (primary) or "
            "DEEPSEEK_API_KEY (fallback) in .env."
        )
    return key


def _fetch_ohlcv_resilient(ticker, start_date, end_date, min_rows=5):
    """Fetch live EGX OHLCV with a multi-provider fallback chain.

    The dashboard's Quick Analysis + Full Pipeline both used to call
    ``get_YFin_data_online`` directly with no fallback. yfinance is rate-limited
    and intermittently empty for thin EGX names (and *permanently* empty for a
    handful such as QNBA.CA / ORAS.CA), which surfaced to the user as
    "Insufficient data for <ticker>" or runs that worked for some tickers and
    refused others.

    Chain (LIVE sources only — no stale local CSV on the live path):
        1. TradingView (tvDatafeed) — keyless, near-real-time, best free EGX
           coverage (returns history even for yfinance-dead names like ORAS)
        2. yfinance  (free, ~15 min delayed)
        3. EODHD     (live, covers QNBA which is absent from TradingView; needs EODHD_API_KEY)

    Returns the first provider response carrying >= ``min_rows`` bars. If none
    qualifies, returns the best (most rows) response so the caller can raise a
    precise, provider-attributed error instead of a generic "insufficient data".
    """
    import time
    attempts = []  # (provider_name, result_dict)

    # --- Provider 1: TradingView (primary — keyless, freshest, widest coverage) ---
    try:
        from tradingagents.dataflows.tradingview_provider import get_tradingview_ohlcv
        tv_result = get_tradingview_ohlcv(ticker, start_date, end_date)
        if tv_result.get("data") and len(tv_result["data"]) >= min_rows:
            return tv_result
        attempts.append(("tradingview", tv_result))
    except Exception as e:
        logger.warning("TradingView fetch failed for %s: %s", ticker, e)

    # --- Provider 2: yfinance (retry once on a transient empty/exception) ---
    yf_result = None
    for attempt in range(2):
        try:
            yf_result = get_YFin_data_online(ticker, start_date, end_date)
            if yf_result.get("data") and len(yf_result["data"]) >= min_rows:
                return yf_result
            # Empty/short — retry once (transient yfinance throttling is common)
            if attempt == 0:
                time.sleep(1.0)
        except Exception as e:
            logger.warning("yfinance fetch failed for %s (attempt %d): %s", ticker, attempt + 1, e)
            if attempt == 0:
                time.sleep(1.0)
    if yf_result is not None:
        attempts.append(("yfinance", yf_result))

    # --- Provider 3: EODHD (live; covers QNBA, which TradingView lacks) ---
    try:
        from tradingagents.dataflows.eodhd import get_stock_data_eodhd
        eodhd_result = get_stock_data_eodhd(ticker, start_date, end_date)
        if eodhd_result.get("data") and len(eodhd_result["data"]) >= min_rows:
            logger.info("Using EODHD live fallback for %s (TradingView/yfinance insufficient)", ticker)
            return eodhd_result
        attempts.append(("eodhd", eodhd_result))
    except Exception as e:
        logger.warning("EODHD fetch failed for %s: %s", ticker, e)

    # Nothing met the bar — return the response with the most rows for a precise
    # error message (or a synthetic empty result if every provider hard-failed).
    if attempts:
        attempts.sort(key=lambda kv: len(kv[1].get("data") or []), reverse=True)
        best_name, best = attempts[0]
        best = dict(best)
        best.setdefault("_providers_tried", [name for name, _ in attempts])
        return best
    return {"symbol": ticker, "data": [], "error": "All OHLCV providers failed", "_providers_tried": ["tradingview", "yfinance", "eodhd"]}

def run_prediction(target_tickers=None):
    global log_file
    log_file = open("final_result.log", "w", encoding="utf-8")
    
    log("=" * 70)
    log("[EGX] STOCK ANALYSIS - DIRECT PREDICTION")
    log("=" * 70)

    # Stock to analyze
    if target_tickers:
        # If passed as string, wrap in list
        if isinstance(target_tickers, str):
            EGX_TICKERS = [(target_tickers, target_tickers)]
        else:
            EGX_TICKERS = [(t, t) for t in target_tickers]
    else:
        EGX_TICKERS = [
            ("COMI.CA", "Commercial International Bank"),
            ("HRHO.CA", "Hermes Holding"),
        ]

    price_data = None
    selected_ticker = None
    selected_name = None

    log("\n[1/3] Fetching stock price data...")

    # Use current date + 1 day to ensure we get today's data (yfinance end is exclusive)
    end_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    log(f"  Date range: {start_date} to {end_date} (last 30 days)")

    for ticker, name in EGX_TICKERS:
        log(f"  Trying {ticker} ({name})...")
        try:
            data = _fetch_ohlcv_resilient(ticker, start_date, end_date, min_rows=5)
            if data.get("data") and len(data["data"]) >= 5:
                price_data = data
                selected_ticker = ticker
                selected_name = name
                _src = data.get("source", "yfinance")
                log(f"  SUCCESS! Found {len(data['data'])} trading days (source: {_src})")
                break
            else:
                log(f"    Failed to get enough data for {ticker} "
                    f"(tried: {', '.join(data.get('_providers_tried', ['yfinance']))})")
        except Exception as e:
            log(f"    Error: {e}")

    if not price_data or not price_data.get("data"):
        log("\n Using sample data for demonstration...")
        selected_ticker = "COMI.CA"
        selected_name = "Commercial International Bank"
        # Re-adding minimal sample data structure to avoid crash if fetch fails
        price_data = {
            "symbol": "COMI.CA",
            "total_records": 1, 
            "data": [{"date": "2026-01-01", "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1000}], 
            "avg_daily_volume": 1000
        }

    log(f"\nAnalyzing: {selected_ticker} ({selected_name})")
    log(f"  Data points: {len(price_data['data'])} trading days")
    log(f"  ADV: {price_data.get('avg_daily_volume', 0):,.0f} shares")

    # Get the last available date
    last_date = price_data["data"][-1]["date"]
    log(f"  Last available date: {last_date}")

    # --- Try Real-Time Data ---
    log("\n[1.5] Fetching Real-Time Price from Mubasher...")
    try:
        from tradingagents.dataflows.mubasher_scraper import MubasherScraper
        live_data = MubasherScraper.get_live_data(selected_ticker)
        
        if live_data and "price" in live_data:
            live_price = live_data["price"]
            live_change = live_data.get("change_pct", 0)
            log(f"  ✓ LIVE PRICE: {live_price:.2f} EGP")
            log(f"  ✓ Change: {live_change:+.2f}%")
            log(f"  ✓ Source: {live_data.get('source')}")
        else:
            log(f"  Warning: Could not fetch live price. Data: {live_data}")
            live_price = None

    except Exception as e:
        log(f"  Warning: Real-time fetch failed ({e}).")
        live_price = None

    log("\n  Recent Price Action (Yahoo Finance - Delayed):")
    log("  " + "-" * 55)
    for day in price_data["data"][-5:]:
        log(f"  {day['date']}: O={day['open']:.2f} H={day['high']:.2f} L={day['low']:.2f} C={day['close']:.2f} V={day['volume']:,}")

    # Step 2: Calculate indicators
    log("\n[2/3] Calculating technical indicators...")

    closes = [d["close"] for d in price_data["data"]]

    sma_5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else None
    sma_10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else None

    # Use live price if we fetched it, otherwise last close
    if 'live_price' not in locals() or live_price is None:
        current_price = closes[-1]
        log(f"  Using Last Close as Current Price: {current_price:.2f} EGP")
    else:
        current_price = live_price
        log(f"  Using LIVE Price: {current_price:.2f} EGP")

    daily_change = ((closes[-1] - closes[-2]) / closes[-2]) * 100 if len(closes) >= 2 else 0
    weekly_change = ((closes[-1] - closes[-5]) / closes[-5]) * 100 if len(closes) >= 5 else 0

    # RSI
    if len(closes) >= 15:
        gains, losses = [], []
        for i in range(1, min(15, len(closes))):
            diff = closes[-i] - closes[-(i+1)]
            gains.append(max(diff, 0))
            losses.append(abs(min(diff, 0)))
        avg_gain = sum(gains) / len(gains) if gains else 0
        avg_loss = sum(losses) / len(losses) if losses else 0.001
        rsi = 100 - (100 / (1 + avg_gain / avg_loss))
    else:
        rsi = 50

    trend = "UPTREND" if sma_5 and sma_10 and sma_5 > sma_10 else "DOWNTREND" if sma_5 and sma_10 and sma_5 < sma_10 else "NEUTRAL"

    log(f"  Last Close: {closes[-1]:.2f} EGP (as of {last_date})")
    log(f"  Daily Change (Historical): {daily_change:+.2f}%")
    log(f"  Weekly Change: {weekly_change:+.2f}%")
    log(f"  SMA(5): {sma_5:.2f}" if sma_5 else "  SMA(5): N/A")
    log(f"  SMA(10): {sma_10:.2f}" if sma_10 else "  SMA(10): N/A")
    log(f"  RSI(14): {rsi:.1f}")
    log(f"  Trend: {trend}")

    # Step 3: LLM Analysis
    log("\n[3/3] Generating trading recommendation...")
    log("-" * 70)

    try:
        from langchain_openai import ChatOpenAI
        
        llm = ChatOpenAI(
            model=DEFAULT_CONFIG["quick_think_llm"],
            base_url=DEFAULT_CONFIG["backend_url"],
            api_key=_resolve_llm_api_key(DEFAULT_CONFIG["backend_url"]),
            temperature=0,
            seed=int(DEFAULT_CONFIG.get("llm_seed", 42)),
        )

        price_table = "\n".join([
            f"  {d['date']}: O={d['open']:.2f} H={d['high']:.2f} L={d['low']:.2f} C={d['close']:.2f}"
            for d in price_data["data"][-7:]
        ])

        sma5_str = f"{sma_5:.2f}" if sma_5 else "N/A"
        sma10_str = f"{sma_10:.2f}" if sma_10 else "N/A"

        prompt = f"""You are a senior equity analyst for the Egyptian Stock Exchange (EGX).

STOCK: {selected_ticker} - {selected_name}
MARKET: Egyptian Exchange (EGX)
CURRENCY: Egyptian Pound (EGP)

PRICE DATA (Last 7 Days):
{price_table}

INDICATORS:
- Current Price: {current_price:.2f} EGP (Real-time if available)
- Daily Change: {daily_change:+.2f}%
- Weekly Change: {weekly_change:+.2f}%
- SMA(5): {sma5_str}
- SMA(10): {sma10_str}
- RSI(14): {rsi:.1f}
- Trend: {trend}
- Volume: {price_data.get('avg_daily_volume', 0):,.0f} daily average

EGX CONSTRAINTS:
- Long-only (no short selling)
- No leverage
- +/-10% daily limits

YOUR TASK: Provide a trading recommendation for next week.

Format your response EXACTLY as:

SIGNAL: [BUY or SELL or HOLD]
CONFIDENCE: [HIGH or MEDIUM or LOW]
TARGET_PRICE: [number] EGP
STOP_LOSS: [number] EGP
RISK: [LOW or MEDIUM or HIGH]

REASONING:
[2-3 sentences explaining your decision]
"""
        
        log("Asking Groq LLM for recommendation...\n")
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = llm.invoke(prompt)
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    raise e
                log(f"  LLM timeout/error (attempt {attempt+1}/{max_retries}). Retrying...")
                import time
                time.sleep(2)
        
        log("=" * 70)
        log(f"TRADING RECOMMENDATION - {selected_ticker}")
        log("=" * 70)
        log(f"\nStock: {selected_ticker} ({selected_name})")
        log(f"Current Price: {current_price:.2f} EGP")
        log("-" * 70)
        log(response.content)
        
    except Exception as e:
        log(f"LLM Error: {e}")
        import traceback
        log(traceback.format_exc())

    log("\n" + "=" * 70)
    log("DISCLAIMER: Educational simulation only. Not financial advice.")
    log("=" * 70)

def analyze_ticker_for_api(ticker):
    """
    Runs the analysis for a single ticker and returns structured data.
    Returns a dictionary with all analysis components.
    """
    import re
    from datetime import datetime, timedelta
    from langchain_openai import ChatOpenAI
    
    # Fetch data
    end_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    
    try:
        price_data = _fetch_ohlcv_resilient(ticker, start_date, end_date, min_rows=5)
        if not price_data.get("data") or len(price_data["data"]) < 5:
            n = len(price_data.get("data") or [])
            tried = price_data.get("_providers_tried") or ["yfinance", "eodhd"]
            detail = price_data.get("error") or f"only {n} trading day(s) available"
            return {
                "error": (
                    f"No usable market data for {ticker} "
                    f"(tried: {', '.join(tried)} — {detail}). "
                    f"This EGX symbol may be delisted, halted, or unavailable on the live feeds."
                )
            }
    except Exception as e:
        return {"error": f"Data fetch failed for {ticker}: {e}"}
    
    # Extract stock name (ticker without .CA)
    stock_name = ticker.replace(".CA", "")
    
    # Calculate indicators
    closes = [d["close"] for d in price_data["data"]]
    sma_5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else None
    sma_10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else None
    
    # Try to get a live price. Primary: TradingView snapshot (keyless,
    # near-real-time, far more reliable than the Mubasher CSS scrape). Fallback:
    # Mubasher. Else the last historical close already in `current_price`.
    current_price = closes[-1]
    live_source = "Historical"
    try:
        from tradingagents.dataflows.tradingview_provider import get_tradingview_price
        tv_price = get_tradingview_price(ticker)
        if tv_price and tv_price.get("price"):
            current_price = tv_price["price"]
            live_source = "Live (TradingView)"
    except Exception:
        pass
    if live_source == "Historical":
        try:
            from tradingagents.dataflows.mubasher_scraper import MubasherScraper
            live_data = MubasherScraper.get_live_data(ticker)
            if live_data and "price" in live_data:
                current_price = live_data["price"]
                live_source = "Live (Mubasher)"
        except Exception:
            pass
    
    daily_change = ((closes[-1] - closes[-2]) / closes[-2]) * 100 if len(closes) >= 2 else 0
    weekly_change = ((closes[-1] - closes[-5]) / closes[-5]) * 100 if len(closes) >= 5 else 0
    
    # RSI
    if len(closes) >= 15:
        gains, losses = [], []
        for i in range(1, min(15, len(closes))):
            diff = closes[-i] - closes[-(i+1)]
            gains.append(max(diff, 0))
            losses.append(abs(min(diff, 0)))
        avg_gain = sum(gains) / len(gains) if gains else 0
        avg_loss = sum(losses) / len(losses) if losses else 0.001
        rsi = 100 - (100 / (1 + avg_gain / avg_loss))
    else:
        rsi = 50
    
    trend = "UPTREND" if sma_5 and sma_10 and sma_5 > sma_10 else "DOWNTREND" if sma_5 and sma_10 and sma_5 < sma_10 else "NEUTRAL"
    
    # Get LLM recommendation with multi-perspective analysis.
    # Use the shared failover chain (nvidia → deepseek → google → groq) instead
    # of a raw single-endpoint ChatOpenAI, so a Quick Analysis no longer dies on
    # an NVIDIA 504/429 — it rotates to DeepSeek-direct (the reliable free key)
    # automatically, the same way the full pipeline does.
    try:
        try:
            from tradingagents.agents.utils.llm_failover import build_resilient_llm
            llm = build_resilient_llm(
                DEFAULT_CONFIG, role="quick",
                seed=int(DEFAULT_CONFIG.get("llm_seed", 42)),
            )
        except Exception:
            # Last-ditch: raw single-endpoint client (keeps Quick working even if
            # the failover module can't be imported for some reason).
            llm = ChatOpenAI(
                model=DEFAULT_CONFIG["quick_think_llm"],
                base_url=DEFAULT_CONFIG["backend_url"],
                api_key=_resolve_llm_api_key(DEFAULT_CONFIG["backend_url"]),
                temperature=0,
                seed=int(DEFAULT_CONFIG.get("llm_seed", 42)),
            )

        price_table = "\n".join([
            f"  {d['date']}: O={d['open']:.2f} H={d['high']:.2f} L={d['low']:.2f} C={d['close']:.2f}"
            for d in price_data["data"][-7:]
        ])

        sma5_str = f"{sma_5:.2f}" if sma_5 else "N/A"
        sma10_str = f"{sma_10:.2f}" if sma_10 else "N/A"
        
        prompt = f"""You are a portfolio management team analyzing a stock on the Egyptian Exchange (EGX).
You must provide THREE separate analyst perspectives and a final recommendation.

STOCK: {ticker} - {stock_name}
MARKET: Egyptian Exchange (EGX)
CURRENCY: Egyptian Pound (EGP)

PRICE DATA (Last 7 Days):
{price_table}

INDICATORS:
- Current Price: {current_price:.2f} EGP
- Daily Change: {daily_change:+.2f}%
- Weekly Change: {weekly_change:+.2f}%
- SMA(5): {sma5_str}
- SMA(10): {sma10_str}
- RSI(14): {rsi:.1f}
- Trend: {trend}
- Volume: {price_data.get('avg_daily_volume', 0):,.0f} daily average

EGX CONSTRAINTS:
- Long-only (no short selling)
- No leverage
- +/-10% daily limits

Format your response EXACTLY as shown below. Each section MUST be present:

BULL_CASE:
[3-4 bullet points from the bullish analyst perspective. Focus on positive technicals, momentum, support levels, and upside catalysts.]

BEAR_CASE:
[3-4 bullet points from the bearish analyst perspective. Focus on risks, resistance levels, overvaluation concerns, and downside risks.]

NEUTRAL_CASE:
[3-4 bullet points from the neutral/balanced analyst perspective. Acknowledge both sides, propose risk management, and suggest hedging or position sizing.]

RATIONALE:
[2-3 sentences synthesizing the bull/bear debate and explaining why the final signal was chosen.]

SIGNAL: [BUY or SELL or HOLD]
CONFIDENCE: [HIGH or MEDIUM or LOW]
TARGET_PRICE: [number] EGP
STOP_LOSS: [number] EGP
RISK: [LOW or MEDIUM or HIGH]

RECOMMENDATION:
[A refined 3-5 sentence investment plan. Include specific entry price, position sizing guidance, deployment of proceeds, and re-entry triggers.]
"""
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = llm.invoke(prompt)
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    raise e
                import time
                time.sleep(2)
        
        recommendation_text = response.content
        
        # Parse structured multi-perspective sections
        def extract_section(text, start_key, end_keys):
            """Extract text between start_key and the first of end_keys."""
            if end_keys:
                boundary = '|'.join(re.escape(k) for k in end_keys)
                pattern = re.escape(start_key) + r'\s*\n?(.*?)(?=' + boundary + r'|\Z)'
            else:
                pattern = re.escape(start_key) + r'\s*\n?(.*)'
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            return match.group(1).strip() if match else ""
        
        # Strip markdown bold/italic markers before parsing structured fields
        clean_text = recommendation_text.replace('**', '').replace('*', '')
        
        logger.debug("Raw LLM response for %s: %.1500s", ticker, recommendation_text)
        
        bull_case = extract_section(clean_text, "BULL_CASE:", ["BEAR_CASE:"])
        bear_case = extract_section(clean_text, "BEAR_CASE:", ["NEUTRAL_CASE:"])
        neutral_case = extract_section(clean_text, "NEUTRAL_CASE:", ["RATIONALE:"])
        rationale = extract_section(clean_text, "RATIONALE:", ["SIGNAL:"])
        final_recommendation = extract_section(clean_text, "RECOMMENDATION:", [])
        
        # Parse structured fields from the cleaned (no-markdown) text
        signal_match = re.search(r'SIGNAL\s*:\s*(BUY|SELL|HOLD)', clean_text, re.IGNORECASE)
        confidence_match = re.search(r'CONFIDENCE\s*:\s*(HIGH|MEDIUM|LOW)', clean_text, re.IGNORECASE)
        target_match = re.search(r'TARGET[_\s]*PRICE\s*:\s*([0-9.]+)', clean_text, re.IGNORECASE)
        stop_match = re.search(r'STOP[_\s]*LOSS\s*:\s*([0-9.]+)', clean_text, re.IGNORECASE)
        risk_match = re.search(r'RISK\s*:\s*(LOW|MEDIUM|HIGH)', clean_text, re.IGNORECASE)
        
        parsed_signal = signal_match.group(1).upper() if signal_match else None
        
        # Fallback: scan the entire text for BUY/SELL keywords if regex didn't match
        if not parsed_signal:
            text_upper = clean_text.upper()
            # Look for clear BUY/SELL indicators in the signal area
            signal_area = extract_section(clean_text, "RATIONALE:", ["RECOMMENDATION:"])
            if "BUY" in signal_area.upper() and "SELL" not in signal_area.upper():
                parsed_signal = "BUY"
            elif "SELL" in signal_area.upper() and "BUY" not in signal_area.upper():
                parsed_signal = "SELL"
            else:
                parsed_signal = "HOLD"
            logger.debug("Signal regex failed, fallback signal: %s", parsed_signal)
        else:
            logger.debug("Parsed signal: %s", parsed_signal)
        
        recommendation = {
            "signal": parsed_signal,
            "confidence": confidence_match.group(1).upper() if confidence_match else "MEDIUM",
            "target_price": float(target_match.group(1)) if target_match else current_price,
            "stop_loss": float(stop_match.group(1)) if stop_match else current_price * 0.9,
            "risk": risk_match.group(1).upper() if risk_match else "MEDIUM",
            "bull_case": bull_case or "Bullish signals detected based on current technicals.",
            "bear_case": bear_case or "Bearish risks noted based on market conditions.",
            "neutral_case": neutral_case or "Balanced view considering both upside and downside.",
            "rationale": rationale or "Analysis completed based on available data.",
            "recommendation": final_recommendation or "Monitor the stock and trade within the identified levels.",
            "full_text": recommendation_text
        }
    except Exception as e:
        llm_error = str(e)
        recommendation = {
            "signal": "HOLD",
            "confidence": "LOW",
            "target_price": current_price,
            "stop_loss": current_price * 0.9,
            "risk": "HIGH",
            "bull_case": "",
            "bear_case": "",
            "neutral_case": "",
            "rationale": f"LLM Error: {str(e)}",
            "recommendation": "",
            "full_text": f"Error: {str(e)}"
        }
    else:
        llm_error = None
    
    # Full Investing-style technical panel (best-effort; never fails the response).
    try:
        from tradingagents.dataflows.technical_panel import get_live_panel
        _panel = get_live_panel(ticker)
        technical_panel = _panel if isinstance(_panel, dict) and _panel.get("panel") else None
    except Exception:
        technical_panel = None

    # Return structured data
    return {
        "ticker": ticker,
        "name": stock_name,
        "technical_panel": technical_panel,
        "price": {
            "current": round(current_price, 2),
            "daily_change": round(daily_change, 2),
            "weekly_change": round(weekly_change, 2),
            "source": live_source
        },
        "indicators": {
            "sma_5": round(sma_5, 2) if sma_5 else None,
            "sma_10": round(sma_10, 2) if sma_10 else None,
            "rsi": round(rsi, 1),
            "trend": trend,
            "volume": price_data.get('avg_daily_volume', 0)
        },
        "price_history": [
            {
                "date": d["date"],
                "open": round(d["open"], 2),
                "high": round(d["high"], 2),
                "low": round(d["low"], 2),
                "close": round(d["close"], 2),
                "volume": d.get("volume", 0)
            }
            for d in price_data["data"][-7:]
        ],
        "recommendation": recommendation,
        "llm_error": llm_error,
    }


if __name__ == "__main__":
    # Fix Windows console encoding only when running as a script
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    if len(sys.argv) > 1:
        run_prediction(sys.argv[1:])
    else:
        run_prediction()

