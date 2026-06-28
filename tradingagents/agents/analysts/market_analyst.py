from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import time
import json
from typing import Dict, Any, Optional
from tradingagents.agents.utils.agent_utils import get_stock_data, get_indicators
from tradingagents.agents.utils.technical_panel_tool import get_technical_panel
from tradingagents.dataflows.config import get_config
from tradingagents.agents.utils.temporal import point_in_time_notice

# =============================================================================
# Technical Analyst ("Chartist") for EGX Market
# =============================================================================
# This agent analyzes EGX OHLCV data using daily indicators only.
# No scalping logic, no tight-spread assumptions.
# All outputs are structured JSON for downstream consumption.
# =============================================================================

# Confidence adjustment factors for EGX market conditions
LIQUIDITY_CONFIDENCE_PENALTY = 0.20  # Reduce confidence by 20% for low-liquidity stocks
MISSING_DATA_CONFIDENCE_PENALTY = 0.10  # Reduce by 10% per significant data gap
MAX_CONFIDENCE = 1.0
MIN_CONFIDENCE = 0.10

# Core indicators for EGX daily analysis (no intraday assumptions)
EGX_DAILY_INDICATORS = [
    "rsi",          # Momentum - overbought/oversold
    "macd",         # Trend momentum
    "macds",        # MACD signal line
    "macdh",        # MACD histogram for divergence
    "boll",         # Bollinger middle band
    "boll_ub",      # Bollinger upper band
    "boll_lb",      # Bollinger lower band
    "close_50_sma", # Medium-term trend
]


def _compute_full_panel(ticker: str, as_of: str) -> Optional[Dict[str, Any]]:
    """Compute the full Investing-style technical panel for the as-of date.

    Reuses the shared engine (tradingagents.dataflows.technical_panel) so the live
    panel is identical to the backtest dataset. Look-ahead-safe (the engine hard-filters
    to ``date <= as_of``). Best-effort: returns None on any failure so a panel hiccup
    never breaks the analyst node or the graph.
    """
    try:
        from tradingagents.dataflows.technical_panel import get_live_panel
        res = get_live_panel(ticker, as_of=as_of)
        return res if isinstance(res, dict) and res.get("panel") else None
    except Exception:
        return None


def parse_technical_signals(indicator_results: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse raw indicator data into structured signals.
    Handles missing candles gracefully by flagging gaps.
    
    Returns:
        Dict with parsed signals and data quality metrics
    """
    signals = {
        "rsi": {"value": None, "signal": "neutral", "description": ""},
        "macd": {"value": None, "signal": "neutral", "description": ""},
        "bollinger": {"position": "middle", "signal": "neutral", "description": ""},
        "trend_sma": {"value": None, "signal": "neutral", "description": ""},
    }
    
    missing_count = 0
    total_expected = len(EGX_DAILY_INDICATORS)
    
    for indicator, data in indicator_results.items():
        if data is None or data == "N/A" or "error" in str(data).lower():
            missing_count += 1
            continue
            
        # Parse RSI
        if indicator == "rsi":
            try:
                rsi_val = float(data) if isinstance(data, (int, float, str)) else None
                if rsi_val is not None:
                    signals["rsi"]["value"] = round(rsi_val, 2)
                    if rsi_val >= 70:
                        signals["rsi"]["signal"] = "overbought"
                        signals["rsi"]["description"] = f"RSI at {rsi_val:.1f} indicates overbought conditions"
                    elif rsi_val <= 30:
                        signals["rsi"]["signal"] = "oversold"
                        signals["rsi"]["description"] = f"RSI at {rsi_val:.1f} indicates oversold conditions"
                    else:
                        signals["rsi"]["signal"] = "neutral"
                        signals["rsi"]["description"] = f"RSI at {rsi_val:.1f} is in neutral zone"
            except (ValueError, TypeError):
                missing_count += 1
                
        # Parse MACD
        elif indicator in ["macd", "macds", "macdh"]:
            try:
                val = float(data) if isinstance(data, (int, float, str)) else None
                if val is not None and indicator == "macd":
                    signals["macd"]["value"] = round(val, 4)
                    if val > 0:
                        signals["macd"]["signal"] = "bullish"
                        signals["macd"]["description"] = "MACD above zero indicates bullish momentum"
                    else:
                        signals["macd"]["signal"] = "bearish"
                        signals["macd"]["description"] = "MACD below zero indicates bearish momentum"
            except (ValueError, TypeError):
                pass
    
    data_quality = {
        "missing_indicators": missing_count,
        "total_indicators": total_expected,
        "data_completeness": (total_expected - missing_count) / total_expected if total_expected > 0 else 0
    }
    
    return {"signals": signals, "data_quality": data_quality}


def calculate_confidence_score(
    signals: Dict[str, Any],
    low_liquidity: bool = False,
    volume_missing: bool = False,
    data_quality: Optional[Dict[str, Any]] = None
) -> float:
    """
    Calculate confidence score adjusted for EGX market conditions.
    
    Factors:
    - Base confidence from signal agreement
    - Penalty for low liquidity stocks
    - Penalty for missing volume data
    - Penalty for incomplete indicator data
    """
    # Start with base confidence from signal agreement
    base_confidence = 0.70
    
    bullish_signals = 0
    bearish_signals = 0
    total_signals = 0
    
    for indicator, data in signals.items():
        if isinstance(data, dict) and "signal" in data:
            signal = data["signal"]
            if signal in ["bullish", "overbought"]:
                bullish_signals += 1
                total_signals += 1
            elif signal in ["bearish", "oversold"]:
                bearish_signals += 1
                total_signals += 1
            elif signal == "neutral":
                total_signals += 1
    
    # Agreement factor: higher confidence when signals agree
    if total_signals > 0:
        max_direction = max(bullish_signals, bearish_signals)
        agreement_ratio = max_direction / total_signals
        base_confidence = 0.50 + (agreement_ratio * 0.40)  # 0.50 to 0.90
    
    # Apply penalties
    confidence = base_confidence
    
    # Low liquidity penalty (EGX-specific)
    if low_liquidity:
        confidence -= LIQUIDITY_CONFIDENCE_PENALTY
    
    # Missing volume penalty
    if volume_missing:
        confidence -= MISSING_DATA_CONFIDENCE_PENALTY
    
    # Data completeness penalty
    if data_quality:
        completeness = data_quality.get("data_completeness", 1.0)
        if completeness < 0.80:
            confidence -= (1.0 - completeness) * 0.20
    
    # Clamp to valid range
    return max(MIN_CONFIDENCE, min(MAX_CONFIDENCE, round(confidence, 2)))


def determine_trend_direction(signals: Dict[str, Any]) -> Dict[str, Any]:
    """
    Determine overall trend direction from aggregated signals.
    
    Returns:
        Dict with trend direction, strength, and rationale
    """
    bullish = 0
    bearish = 0
    
    for indicator, data in signals.items():
        if isinstance(data, dict) and "signal" in data:
            signal = data["signal"]
            if signal in ["bullish", "oversold"]:  # Oversold can indicate bullish reversal
                bullish += 1
            elif signal in ["bearish", "overbought"]:  # Overbought can indicate bearish reversal
                bearish += 1
    
    if bullish > bearish + 1:
        direction = "bullish"
        strength = "strong" if bullish >= 3 else "moderate"
    elif bearish > bullish + 1:
        direction = "bearish"
        strength = "strong" if bearish >= 3 else "moderate"
    else:
        direction = "neutral"
        strength = "weak"
    
    return {
        "direction": direction,
        "strength": strength,
        "bullish_signals": bullish,
        "bearish_signals": bearish,
        "rationale": f"{bullish} bullish vs {bearish} bearish signals detected"
    }


def generate_invalidation_conditions(trend: Dict[str, Any], signals: Dict[str, Any]) -> list:
    """
    Generate conditions that would invalidate the current technical thesis.
    Critical for risk management in EGX's volatile environment.
    """
    conditions = []
    
    direction = trend.get("direction", "neutral")
    
    if direction == "bullish":
        conditions.append("RSI drops below 40 (momentum loss)")
        conditions.append("MACD crosses below signal line")
        conditions.append("Price closes below lower Bollinger Band")
        conditions.append("Volume dries up significantly (watch for < 50% of ADV)")
    elif direction == "bearish":
        conditions.append("RSI rises above 60 (momentum shift)")
        conditions.append("MACD crosses above signal line")
        conditions.append("Price closes above upper Bollinger Band")
        conditions.append("Strong volume on up days")
    else:
        conditions.append("Breakout above upper Bollinger Band with volume")
        conditions.append("Breakdown below lower Bollinger Band with volume")
        conditions.append("RSI breaks above 70 or below 30")
    
    # EGX-specific conditions
    conditions.append("Daily price limit hit (±10%) - trading halt risk")
    
    return conditions


def create_market_analyst(llm):
    """
    Create the Technical Analyst ("Chartist") agent for EGX market analysis.
    
    This agent:
    - Analyzes DAILY OHLCV data only (no intraday/scalping)
    - Uses RSI, MACD, Bollinger Bands for EGX stocks
    - Outputs structured JSON with trend, signals, confidence, invalidations
    - Explicitly reduces confidence for low-liquidity stocks
    - Handles missing candles gracefully
    """

    def market_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        company_name = state["company_of_interest"]
        
        # Check for liquidity info from upstream data
        low_liquidity = state.get("low_liquidity", False)
        volume_missing = state.get("volume_missing", False)

        # Compute the full technical panel ONCE and inject it into the prompt so the
        # Chartist reasons over the 12 indicators + verdicts + SMA/EMA grid + pivots
        # proactively (not only if it chooses to call the get_technical_panel tool).
        # Reused in the return so we never compute it twice.
        panel_res = _compute_full_panel(ticker, current_date)
        panel_context = ""
        if panel_res and panel_res.get("panel"):
            from tradingagents.dataflows.technical_panel import format_panel_text
            panel_context = format_panel_text(
                panel_res["panel"], ticker=ticker,
                as_of=panel_res.get("as_of", current_date), bars=panel_res.get("bars"),
            )

        tools = [
            get_stock_data,
            get_indicators,
            get_technical_panel,
        ]

        # EGX-specific system message for Technical Analyst
        system_message = """You are a Technical Analyst ("Chartist") specializing in the Egyptian Exchange (EGX).

## Your Role
Analyze EGX stock price data using DAILY technical indicators. You must provide structured analysis suitable for institutional trading decisions.

## Available Indicators (DAILY DATA ONLY - No Intraday)
Select from these indicators for EGX analysis:

**Momentum:**
- rsi: RSI (14-period) - Overbought >70, Oversold <30

**MACD Related:**
- macd: MACD line - Momentum direction
- macds: MACD Signal line - Crossover signals
- macdh: MACD Histogram - Divergence detection

**Volatility (Bollinger Bands):**
- boll: Bollinger Middle Band (20 SMA)
- boll_ub: Bollinger Upper Band (+2 std dev)
- boll_lb: Bollinger Lower Band (-2 std dev)

**Trend:**
- close_50_sma: 50-day SMA for medium-term trend

## EGX Market Considerations
- Daily price limits: ±10% (circuit breakers)
- Lower liquidity than US markets
- No short selling available
- Trading hours: 10:00-14:30 EST
- Currency: EGP (Egyptian Pound)

## CRITICAL RULES
1. NO scalping or intraday logic
2. NO tight-spread assumptions (EGX spreads can be wide)
3. Handle missing data gracefully - flag gaps, don't ignore them
4. ALWAYS reduce confidence for low-liquidity stocks
5. Consider EGX's ±10% daily price limits in your analysis

## Required Output Format
After your analysis, you MUST end with a JSON block in this exact format:

```json
{
    "trend_direction": {
        "direction": "bullish|bearish|neutral",
        "strength": "strong|moderate|weak",
        "rationale": "explanation of trend determination"
    },
    "indicator_signals": {
        "rsi": {"value": number, "signal": "overbought|oversold|neutral", "description": "..."},
        "macd": {"value": number, "signal": "bullish|bearish|neutral", "description": "..."},
        "bollinger": {"position": "upper|middle|lower", "signal": "...", "description": "..."}
    },
    "confidence_score": 0.0 to 1.0,
    "confidence_adjustments": ["list of factors that reduced confidence"],
    "invalidation_conditions": ["condition 1", "condition 2", "..."],
    "data_quality": {
        "missing_candles": number,
        "volume_data_available": true|false,
        "sufficient_history": true|false
    }
}
```

First call get_stock_data to retrieve OHLCV data. You may call get_technical_panel(symbol, curr_date) ONCE to retrieve the full panel (all 12 indicators with Buy/Sell/Neutral verdicts, the SMA/EMA grid, summary tallies, and pivot levels) in a single call, or use get_indicators for individual indicators. Provide detailed analysis before the JSON summary."""

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a Technical Analyst (Chartist) for EGX stocks, collaborating with other analysts."
                    " Use the provided tools to gather technical data and provide structured analysis."
                    " If you cannot fully answer, another assistant will help."
                    " Your analysis must end with a structured JSON block as specified."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "\n\nFor your reference:"
                    "\n- Current date: {current_date}"
                    "\n- Stock: {ticker}"
                    "\n- Market: EGX (Egyptian Exchange)"
                    "\n- Low Liquidity Flag: {low_liquidity}"
                    "\n- Volume Data Missing: {volume_missing}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        system_message = point_in_time_notice(current_date) + "\n" + system_message
        if panel_context:
            system_message += (
                "\n\n## Pre-computed Technical Panel (use this as your primary evidence)\n"
                "The following panel was computed deterministically from the OHLCV for this\n"
                "ticker/date (look-ahead-safe). Treat it as ground truth and reason over it;\n"
                "you do not need to re-fetch it via the tool unless you want a different date.\n\n"
                + panel_context
            )
        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(ticker=ticker)
        prompt = prompt.partial(low_liquidity=str(low_liquidity))
        prompt = prompt.partial(volume_missing=str(volume_missing))

        chain = prompt | llm.bind_tools(tools)

        result = chain.invoke(
            state.get("market_messages") or [("human", ticker)]
        )

        report = ""
        structured_analysis = None

        if len(result.tool_calls) == 0:
            report = result.content
            
            # Try to extract JSON from the report
            try:
                # Find JSON block in response
                import re
                json_match = re.search(r'```json\s*(.*?)\s*```', report, re.DOTALL)
                if json_match:
                    structured_analysis = json.loads(json_match.group(1))
                    
                    # Apply liquidity confidence adjustment if not already done
                    if low_liquidity and structured_analysis.get("confidence_score", 1.0) > 0.5:
                        original_conf = structured_analysis.get("confidence_score", 0.7)
                        adjusted_conf = max(MIN_CONFIDENCE, original_conf - LIQUIDITY_CONFIDENCE_PENALTY)
                        structured_analysis["confidence_score"] = adjusted_conf
                        
                        if "confidence_adjustments" not in structured_analysis:
                            structured_analysis["confidence_adjustments"] = []
                        structured_analysis["confidence_adjustments"].append(
                            f"Low liquidity penalty applied: -{LIQUIDITY_CONFIDENCE_PENALTY:.0%}"
                        )
            except (json.JSONDecodeError, AttributeError):
                # If JSON parsing fails, create basic structured output
                structured_analysis = {
                    "trend_direction": {"direction": "neutral", "strength": "weak", "rationale": "Unable to parse indicators"},
                    "indicator_signals": {},
                    "confidence_score": 0.30 if low_liquidity else 0.50,
                    "confidence_adjustments": ["Parsing error - reduced confidence"],
                    "invalidation_conditions": ["Requires manual review"],
                    "data_quality": {"parsing_error": True}
                }
       
        return {
            "market_messages": [result],
            "market_report": report,
            "technical_analysis": structured_analysis,
            "technical_panel": panel_res,
            "low_liquidity": low_liquidity,
        }

    return market_analyst_node


# =============================================================================
# Deterministic Market Analyst (replaces LLM calls with existing functions)
# =============================================================================

def _extract_latest(values) -> Optional[float]:
    """Safely extract the last float value from an indicator result."""
    if not values:
        return None
    try:
        return float(values[-1])
    except (TypeError, ValueError, IndexError):
        return None


def _compute_bollinger_signal(closes: list, period: int = 20) -> Dict[str, Any]:
    """
    Compute Bollinger Bands signal from a list of close prices.
    Returns a signal dict compatible with determine_trend_direction.
    """
    if len(closes) < period:
        return {"signal": "neutral", "description": "Insufficient data for Bollinger Bands"}

    window = closes[-period:]
    mean = sum(window) / period
    std = (sum((p - mean) ** 2 for p in window) / period) ** 0.5

    upper = mean + 2 * std
    lower = mean - 2 * std
    current = closes[-1]

    if current >= upper:
        return {
            "signal": "overbought",
            "description": f"Price {current:.2f} at/above upper Bollinger Band {upper:.2f}",
        }
    elif current <= lower:
        return {
            "signal": "oversold",
            "description": f"Price {current:.2f} at/below lower Bollinger Band {lower:.2f}",
        }
    else:
        position = "upper half" if current > mean else "lower half"
        return {
            "signal": "neutral",
            "description": f"Price {current:.2f} within Bollinger Bands ({position})",
        }


def _compute_sma_signal(closes: list, period: int = 50) -> Dict[str, Any]:
    """
    Compare latest close against its SMA to produce a trend_sma signal.
    """
    if len(closes) < period:
        return {"signal": "neutral", "description": f"Insufficient data for {period}-day SMA"}

    sma = sum(closes[-period:]) / period
    current = closes[-1]

    if current > sma * 1.02:
        return {
            "signal": "bullish",
            "description": f"Price {current:.2f} above {period}-day SMA {sma:.2f} (+{(current/sma - 1):.1%})",
        }
    elif current < sma * 0.98:
        return {
            "signal": "bearish",
            "description": f"Price {current:.2f} below {period}-day SMA {sma:.2f} ({(current/sma - 1):.1%})",
        }
    else:
        return {
            "signal": "neutral",
            "description": f"Price {current:.2f} near {period}-day SMA {sma:.2f}",
        }


def create_deterministic_market_analyst():
    """
    Create a deterministic Technical Analyst node that calls data tools directly
    and applies existing signal-parsing functions — no LLM call required.

    Saves ~2 LLM calls and ~4,000-6,000 tokens per run compared to the
    LLM-based `create_market_analyst`.

    The output state schema is identical to `create_market_analyst` so
    downstream agents (Bull/Bear Researchers) require no changes.
    """
    from tradingagents.dataflows.eodhd import get_eodhd_stock_data, get_eodhd_indicators
    from datetime import datetime
    from dateutil.relativedelta import relativedelta
    import logging as _ma_logging
    _ma_logger = _ma_logging.getLogger("tradingagents.market_analyst")

    # ── yfinance fallback helpers (used when EODHD is unavailable for .CA tickers) ──
    def _yf_fetch_closes_volumes(ticker: str, start: str, end: str):
        """Return (closes_list, avg_volume, low_liq_flag) via yfinance, or ([], None, False)."""
        try:
            import yfinance as yf
            df = yf.download(
                ticker, start=start, end=end,
                progress=False, auto_adjust=True,
            )
            if df is None or df.empty or "Close" not in df.columns:
                return [], None, False
            closes_series = df["Close"].dropna()
            vols_series = df["Volume"].dropna() if "Volume" in df.columns else None
            # Handle yfinance returning single-col DataFrames sometimes
            closes = [float(v.iloc[0]) if hasattr(v, "iloc") else float(v) for v in closes_series.values]
            avg_vol = float(vols_series.mean().iloc[0]) if vols_series is not None and len(vols_series) and hasattr(vols_series.mean(), "iloc") else (float(vols_series.mean()) if vols_series is not None and len(vols_series) else None)
            # EGX low-liquidity threshold (default 50k shares/day)
            low_liq = bool(avg_vol is not None and avg_vol < 50_000)
            return closes, avg_vol, low_liq
        except Exception as e:
            _ma_logger.warning("yfinance fallback failed for %s: %s", ticker, e)
            return [], None, False

    def _local_rsi(closes, period: int = 14):
        """Compute RSI(14) from a list of closes. Returns latest value or None."""
        if len(closes) < period + 1:
            return None
        try:
            import pandas as pd
            s = pd.Series(closes)
            delta = s.diff()
            gain = delta.clip(lower=0).rolling(window=period).mean()
            loss = (-delta.clip(upper=0)).rolling(window=period).mean()
            rs = gain / loss.replace(0, 1e-10)
            rsi = 100 - (100 / (1 + rs))
            v = rsi.iloc[-1]
            return float(v) if pd.notna(v) else None
        except Exception:
            return None

    def _local_macd(closes, fast: int = 12, slow: int = 26, signal: int = 9):
        """Compute MACD/signal/histogram from closes. Returns dict of latest values."""
        if len(closes) < slow + signal:
            return {"macd": None, "macds": None, "macdh": None}
        try:
            import pandas as pd
            s = pd.Series(closes)
            ema_fast = s.ewm(span=fast, adjust=False).mean()
            ema_slow = s.ewm(span=slow, adjust=False).mean()
            macd_line = ema_fast - ema_slow
            signal_line = macd_line.ewm(span=signal, adjust=False).mean()
            hist = macd_line - signal_line
            return {
                "macd":  float(macd_line.iloc[-1]) if pd.notna(macd_line.iloc[-1]) else None,
                "macds": float(signal_line.iloc[-1]) if pd.notna(signal_line.iloc[-1]) else None,
                "macdh": float(hist.iloc[-1]) if pd.notna(hist.iloc[-1]) else None,
            }
        except Exception:
            return {"macd": None, "macds": None, "macdh": None}

    def deterministic_market_analyst_node(state):
        trade_date = state["trade_date"]
        ticker = state["company_of_interest"]
        low_liquidity = state.get("low_liquidity", False)
        volume_missing = state.get("volume_missing", False)

        # ── 1. Fetch price data (120 days lookback for indicators) ─────────────
        start_date = (
            datetime.strptime(trade_date, "%Y-%m-%d") - relativedelta(days=120)
        ).strftime("%Y-%m-%d")

        # LOCAL-ONLY backtest mode: prices come ONLY from the saved
        # data/egx30_ohlcv CSVs — no EODHD/yfinance API call.
        from tradingagents.dataflows.config import get_config as _get_cfg
        _local_only = bool(_get_cfg().get("ohlcv_local_only"))

        closes = []
        data_source = "none"
        if _local_only:
            from tradingagents.dataflows.local_ohlcv import get_local_ohlcv_data
            # max_records=None -> full 120-day window (SMA50/Bollinger need the history,
            # not just the last 20 bars the default cap would return).
            price_result = get_local_ohlcv_data(ticker, start_date, trade_date, max_records=None)
            if price_result and price_result.get("data"):
                closes = [bar["close"] for bar in price_result["data"]]
                data_source = "local_csv"
                if not low_liquidity:
                    low_liquidity = price_result.get("low_liquidity", False)
                if not volume_missing:
                    volume_missing = price_result.get("volume_missing", False)
        else:
            # Try EODHD first (paid tier supports .CA), fall back to yfinance.
            price_result = get_eodhd_stock_data(ticker, start_date, trade_date)
            if price_result and price_result.get("data"):
                closes = [bar["close"] for bar in price_result["data"]]
                data_source = "eodhd"
                if not low_liquidity:
                    low_liquidity = price_result.get("low_liquidity", False)
                if not volume_missing:
                    volume_missing = price_result.get("volume_missing", False)

        # Fallback to yfinance when EODHD returned nothing (e.g. free tier on .CA).
        # Skipped in local-only mode (no network).
        if not closes and not _local_only:
            yf_closes, yf_avg_vol, yf_low_liq = _yf_fetch_closes_volumes(
                ticker, start_date, trade_date
            )
            if yf_closes:
                closes = yf_closes
                data_source = "yfinance(fallback)"
                if not low_liquidity:
                    low_liquidity = yf_low_liq
                _ma_logger.info(
                    "Market analyst %s: EODHD empty, used yfinance fallback (%d bars, avg_vol=%s)",
                    ticker, len(closes), f"{yf_avg_vol:.0f}" if yf_avg_vol else "N/A",
                )

        # ── 2. Fetch RSI (EODHD), then fall back to local pandas calc ──────────
        # In local-only mode skip the EODHD API entirely — RSI is computed locally
        # from `closes` (the saved-CSV prices) below.
        rsi_result = {} if _local_only else get_eodhd_indicators(ticker, "RSI", trade_date, look_back_days=90)
        rsi_values = rsi_result.get("values", []) if not rsi_result.get("error") else []
        rsi_latest = _extract_latest(rsi_values)
        if rsi_latest is None and closes:
            rsi_latest = _local_rsi(closes)

        # ── 3. Fetch MACD (EODHD), then fall back to local pandas calc ─────────
        # Local-only mode: skip the EODHD API; MACD is computed locally from `closes`.
        macd_result = {} if _local_only else get_eodhd_indicators(ticker, "MACD", trade_date, look_back_days=90)
        macd_values = {}
        if not macd_result.get("error"):
            mv = macd_result.get("values", {})
            if isinstance(mv, dict):
                macd_values = mv

        macd_latest  = _extract_latest(macd_values.get("macd_line", []))
        macds_latest = _extract_latest(macd_values.get("signal_line", []))
        macdh_latest = _extract_latest(macd_values.get("histogram", []))

        if macd_latest is None and closes:
            local = _local_macd(closes)
            macd_latest  = local["macd"]
            macds_latest = local["macds"]
            macdh_latest = local["macdh"]

        # ── 4. Build indicator_results dict (scalar values) ────────────────────
        indicator_results = {
            "rsi":   rsi_latest,
            "macd":  macd_latest,
            "macds": macds_latest,
            "macdh": macdh_latest,
            "_source": data_source,
        }

        # ── 5. Run existing deterministic pipeline ─────────────────────────────
        parsed = parse_technical_signals(indicator_results)
        signals = parsed["signals"]
        data_quality = parsed["data_quality"]

        # Enrich signals with Bollinger Bands and SMA from price data
        if closes:
            signals["bollinger"] = _compute_bollinger_signal(closes)
            signals["trend_sma"] = _compute_sma_signal(closes)

        confidence = calculate_confidence_score(
            signals,
            low_liquidity=low_liquidity,
            volume_missing=volume_missing,
            data_quality=data_quality,
        )
        trend = determine_trend_direction(signals)
        invalidations = generate_invalidation_conditions(trend, signals)

        # ── 6. Build the structured analysis (same schema as LLM output) ───────
        structured_analysis = {
            "trend_direction": trend,
            "indicator_signals": signals,
            "confidence_score": confidence,
            "confidence_adjustments": (
                [f"Low liquidity penalty: -{LIQUIDITY_CONFIDENCE_PENALTY:.0%}"]
                if low_liquidity else []
            ),
            "invalidation_conditions": invalidations,
            "data_quality": {
                "missing_candles": data_quality.get("missing_indicators", 0),
                "volume_data_available": not volume_missing,
                "sufficient_history": len(closes) >= 50,
                "total_bars": len(closes),
            },
        }

        # Build a compact text report for backward-compatible `market_report`
        report = (
            f"Deterministic Technical Analysis for {ticker} on {trade_date}:\n"
            f"Trend: {trend['direction']} ({trend['strength']})\n"
            f"RSI: {indicator_results['rsi']} | MACD: {indicator_results['macd']}\n"
            f"Confidence: {confidence:.2f}"
        )

        # ── Full technical panel: compute ONCE, then use TWICE ─────────────────
        # (1) inject the formatted panel (12 indicators + verdicts + SMA/EMA grid +
        #     pivots) into market_report so the downstream reasoning agents
        #     (Bull/Bear/Research-Manager/Trader) get the richer technical picture;
        # (2) return the structured panel in state for the dashboard.
        panel_res = _compute_full_panel(ticker, trade_date)
        if panel_res and panel_res.get("panel"):
            from tradingagents.dataflows.technical_panel import format_panel_text
            report += "\n\n" + format_panel_text(
                panel_res["panel"], ticker=ticker,
                as_of=panel_res.get("as_of", trade_date), bars=panel_res.get("bars"),
            )

        return {
            "market_report": report,
            "technical_analysis": structured_analysis,
            "technical_panel": panel_res,
            "low_liquidity": low_liquidity,
            # Clear per-analyst message channel (no messages were added)
            "market_messages": [],
        }

    return deterministic_market_analyst_node
