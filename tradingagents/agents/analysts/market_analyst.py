from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import math
import time
import json
from typing import Dict, Any, List, Optional
from tradingagents.agents.utils.agent_utils import get_stock_data, get_indicators
from tradingagents.dataflows.config import get_config

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

        tools = [
            get_stock_data,
            get_indicators,
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

First call get_stock_data to retrieve OHLCV data, then use get_indicators for each indicator. Provide detailed analysis before the JSON summary."""

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

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(ticker=ticker)
        prompt = prompt.partial(low_liquidity=str(low_liquidity))
        prompt = prompt.partial(volume_missing=str(volume_missing))

        chain = prompt | llm.bind_tools(tools).bind(temperature=0, seed=42)

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


# =============================================================================
# Six-month horizon factor helpers (Phase 1.2)
# =============================================================================
# All functions are pure computations on price/volume arrays.
# They return None when insufficient data is available.
# =============================================================================

# Confidence penalty when zero-return frequency exceeds threshold
ZERO_RETURN_CONFIDENCE_PENALTY = 0.15
ZERO_RETURN_WARNING_THRESHOLD = 0.30


def _compute_momentum_6m(closes: List[float]) -> Optional[float]:
    """Six-month momentum with 21-day skip (Jegadeesh-Titman 1993).

    Returns (close_t-21 / close_t-126) - 1, skipping most recent 21 days
    to avoid short-term reversal contamination.
    Requires at least 126 data points.
    """
    if len(closes) < 126:
        return None
    try:
        recent = closes[-22]   # ~1 month ago (skip most recent 21 days)
        past = closes[-126]    # ~6 months ago
        if past <= 0:
            return None
        return (recent / past) - 1.0
    except (IndexError, ZeroDivisionError):
        return None


def _compute_realized_vol(closes: List[float], window: int = 63) -> Optional[float]:
    """Annualized realized volatility from daily log returns.

    Uses a 63-day window (~3 months of trading days).
    Returns annualized stdev (multiplied by sqrt(252)).
    """
    if len(closes) < window + 1:
        return None
    try:
        log_returns = []
        for i in range(-window, 0):
            if closes[i - 1] > 0 and closes[i] > 0:
                log_returns.append(math.log(closes[i] / closes[i - 1]))
        if len(log_returns) < window // 2:
            return None
        mean_r = sum(log_returns) / len(log_returns)
        variance = sum((r - mean_r) ** 2 for r in log_returns) / (len(log_returns) - 1)
        daily_vol = math.sqrt(variance)
        return daily_vol * math.sqrt(252)
    except (ValueError, ZeroDivisionError):
        return None


def _compute_amihud_illiq(
    closes: List[float], volumes: List[float], window: int = 21
) -> Optional[float]:
    """Amihud (2002) illiquidity ratio: mean(|r_t| / volume_t).

    Higher values indicate less liquid stocks. Uses absolute daily returns
    divided by daily volume. Window defaults to 21 trading days (~1 month).
    """
    n = min(len(closes), len(volumes))
    if n < window + 1:
        return None
    try:
        ratios = []
        for i in range(-window, 0):
            prev_close = closes[i - 1]
            cur_close = closes[i]
            vol = volumes[i]
            if prev_close > 0 and vol > 0:
                abs_return = abs(cur_close / prev_close - 1.0)
                ratios.append(abs_return / vol)
        if len(ratios) < window // 2:
            return None
        return sum(ratios) / len(ratios)
    except (IndexError, ZeroDivisionError):
        return None


def _compute_zero_return_freq(closes: List[float], window: int = 21) -> Optional[float]:
    """Fraction of days with zero returns (BHL 2007; Sussex/African Markets 2023).

    Sussex (2023) shows this outperforms Amihud on EGX specifically.
    A high zero-return frequency indicates poor price discovery / illiquidity.
    """
    if len(closes) < window + 1:
        return None
    try:
        zero_count = 0
        total = 0
        for i in range(-window, 0):
            prev_close = closes[i - 1]
            cur_close = closes[i]
            if prev_close > 0:
                total += 1
                if abs(cur_close / prev_close - 1.0) < 1e-8:
                    zero_count += 1
        if total == 0:
            return None
        return zero_count / total
    except (IndexError, ZeroDivisionError):
        return None


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

    def deterministic_market_analyst_node(state):
        trade_date = state["trade_date"]
        ticker = state["company_of_interest"]
        low_liquidity = state.get("low_liquidity", False)
        volume_missing = state.get("volume_missing", False)

        # ── 1. Fetch price data (~250 calendar days for 6-month momentum) ─────
        start_date = (
            datetime.strptime(trade_date, "%Y-%m-%d") - relativedelta(days=250)
        ).strftime("%Y-%m-%d")

        price_result = get_eodhd_stock_data(ticker, start_date, trade_date)

        closes = []
        volumes = []
        if price_result and price_result.get("data"):
            closes = [bar["close"] for bar in price_result["data"]]
            volumes = [bar.get("volume", 0) for bar in price_result["data"]]
            # Propagate liquidity flags from fresh data if not already set
            if not low_liquidity:
                low_liquidity = price_result.get("low_liquidity", False)
            if not volume_missing:
                volume_missing = price_result.get("volume_missing", False)

        # ── 2. Fetch RSI ───────────────────────────────────────────────────────
        rsi_result = get_eodhd_indicators(ticker, "RSI", trade_date, look_back_days=90)
        rsi_values = rsi_result.get("values", []) if not rsi_result.get("error") else []

        # ── 3. Fetch MACD ──────────────────────────────────────────────────────
        macd_result = get_eodhd_indicators(ticker, "MACD", trade_date, look_back_days=90)
        macd_values = {}
        if not macd_result.get("error"):
            mv = macd_result.get("values", {})
            if isinstance(mv, dict):
                macd_values = mv

        # ── 4. Build indicator_results dict (scalar values) ────────────────────
        indicator_results = {
            "rsi":   _extract_latest(rsi_values),
            "macd":  _extract_latest(macd_values.get("macd_line", [])),
            "macds": _extract_latest(macd_values.get("signal_line", [])),
            "macdh": _extract_latest(macd_values.get("histogram", [])),
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

        # ── 5b. Compute 6-month horizon factors (Phase 1.2) ───────────────────
        momentum_6m = _compute_momentum_6m(closes) if closes else None
        realized_vol_63d = _compute_realized_vol(closes, window=63) if closes else None
        amihud_21d = _compute_amihud_illiq(closes, volumes, window=21) if closes and volumes else None
        zero_return_21d = _compute_zero_return_freq(closes, window=21) if closes else None

        # Apply zero-return confidence penalty
        confidence_adjustments = (
            [f"Low liquidity penalty: -{LIQUIDITY_CONFIDENCE_PENALTY:.0%}"]
            if low_liquidity else []
        )
        liquidity_warning = None
        if zero_return_21d is not None and zero_return_21d > ZERO_RETURN_WARNING_THRESHOLD:
            confidence = max(MIN_CONFIDENCE, confidence - ZERO_RETURN_CONFIDENCE_PENALTY)
            confidence_adjustments.append(
                f"High zero-return frequency ({zero_return_21d:.1%}): -{ZERO_RETURN_CONFIDENCE_PENALTY:.0%}"
            )
            liquidity_warning = "HIGH_ZERO_RETURN"

        # ── 6. Build the structured analysis (same schema as LLM output) ───────
        structured_analysis = {
            "trend_direction": trend,
            "indicator_signals": signals,
            "confidence_score": confidence,
            "confidence_adjustments": confidence_adjustments,
            "invalidation_conditions": invalidations,
            "data_quality": {
                "missing_candles": data_quality.get("missing_indicators", 0),
                "volume_data_available": not volume_missing,
                "sufficient_history": len(closes) >= 50,
                "total_bars": len(closes),
            },
            # Phase 1.2: 6-month horizon factors
            "six_month_momentum": round(momentum_6m, 4) if momentum_6m is not None else None,
            "realized_volatility_63d": round(realized_vol_63d, 4) if realized_vol_63d is not None else None,
            "amihud_illiq_21d": amihud_21d,  # Already small float, no rounding needed
            "zero_return_frequency_21d": round(zero_return_21d, 4) if zero_return_21d is not None else None,
            "liquidity_warning": liquidity_warning,
        }

        # Build a compact text report for backward-compatible `market_report`
        mom_str = f"{momentum_6m:+.1%}" if momentum_6m is not None else "N/A"
        vol_str = f"{realized_vol_63d:.1%}" if realized_vol_63d is not None else "N/A"
        zr_str = f"{zero_return_21d:.1%}" if zero_return_21d is not None else "N/A"
        report = (
            f"Deterministic Technical Analysis for {ticker} on {trade_date}:\n"
            f"Trend: {trend['direction']} ({trend['strength']})\n"
            f"RSI: {indicator_results['rsi']} | MACD: {indicator_results['macd']}\n"
            f"6M Momentum: {mom_str} | Realized Vol (63d): {vol_str} | Zero-Return Freq (21d): {zr_str}\n"
            f"Confidence: {confidence:.2f}"
        )

        return {
            "market_report": report,
            "technical_analysis": structured_analysis,
            "low_liquidity": low_liquidity,
            # Clear per-analyst message channel (no messages were added)
            "market_messages": [],
        }

    return deterministic_market_analyst_node
