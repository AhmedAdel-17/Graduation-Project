"""
Regime Detection Analyst for EGX Market
========================================
Fully deterministic, rule-based — no LLM calls.

Classifies market regime from EGX30 index data:
- CRASH:  >20% drawdown from 52-week high, or 1-month return < -15%
- BEAR:   >10% drawdown from 52-week high, or 3-month return < -10%
- NORMAL: No significant drawdown, moderate volatility
- BULL:   3-month return > +10% and within 10% of 52-week high

Evidence basis:
- Ezzat (2013): EGX30 volatility exhibits long memory ("Joseph Effect").
  Once a high-vol regime is entered, it persists for years. The analyst
  tracks `regime_age_days` and `vol_persistence_flag` to capture this.
- Giner & Zakamulin (2023): Regime age matters — longer bull markets have
  higher reversal probability (positive duration dependence).
- Schaller & Van Norden (1997): Transition probabilities are state-dependent.

The regime analyst outputs a `strategy_bias` field that downstream agents
(Bull/Bear researchers, Trader) use to adjust their framing:
- CRASH  → "defensive" (capital preservation, cash, reduce exposure)
- BEAR   → "cautious" (reduce new positions, tighten stops)
- NORMAL → "balanced" (standard analysis)
- BULL   → "opportunistic" (wider stops, momentum-friendly)

Non-directional: regime analysis does NOT enter the directional quorum.
It modifies confidence and position size via the sentiment blend cascade,
mapping to MarketRegime from sentiment/contracts.py:
  CRASH  → PANIC
  BEAR   → FEAR
  NORMAL → NEUTRAL
  BULL   → GREED
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from tradingagents.dataflows.config import get_config

logger = logging.getLogger("tradingagents.analysts.regime")

# ─── Regime thresholds ────────────────────────────────────────────────────────
CRASH_DRAWDOWN_PCT = -0.20       # >20% from 52-week high
CRASH_1M_RETURN_PCT = -0.15      # 1-month return < -15%
BEAR_DRAWDOWN_PCT = -0.10        # >10% from 52-week high
BEAR_3M_RETURN_PCT = -0.10       # 3-month return < -10%
BULL_3M_RETURN_PCT = 0.10        # 3-month return > +10%
BULL_NEAR_HIGH_PCT = 0.10        # Within 10% of 52-week high

# Volatility persistence (Ezzat 2013 — Joseph Effect)
VOL_PERSISTENCE_WINDOW = 90      # If vol regime persists >90 days, flag it
HIGH_VOL_THRESHOLD = 0.35        # Annualized vol > 35% = high vol regime

# Strategy bias mapping
REGIME_STRATEGY_BIAS = {
    "CRASH": "defensive",
    "BEAR": "cautious",
    "NORMAL": "balanced",
    "BULL": "opportunistic",
}

# MarketRegime enum mapping (for sentiment blend cascade)
REGIME_TO_MARKET_REGIME = {
    "CRASH": "PANIC",
    "BEAR": "FEAR",
    "NORMAL": "NEUTRAL",
    "BULL": "GREED",
}


def _compute_drawdown_from_high(closes: List[float], high_window: int = 252) -> Optional[float]:
    """Current drawdown from the 52-week (252 trading day) high.

    Returns negative float (e.g., -0.15 means 15% below high).
    """
    if len(closes) < 2:
        return None
    lookback = closes[-min(high_window, len(closes)):]
    peak = max(lookback)
    if peak <= 0:
        return None
    current = closes[-1]
    return (current / peak) - 1.0


def _compute_return(closes: List[float], days: int) -> Optional[float]:
    """Return over the last `days` trading days."""
    if len(closes) < days + 1:
        return None
    try:
        past = closes[-(days + 1)]
        current = closes[-1]
        if past <= 0:
            return None
        return (current / past) - 1.0
    except IndexError:
        return None


def _compute_annualized_vol(closes: List[float], window: int = 63) -> Optional[float]:
    """Annualized realized volatility from daily log returns."""
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
        return math.sqrt(variance) * math.sqrt(252)
    except (ValueError, ZeroDivisionError):
        return None


def _classify_regime(
    drawdown: Optional[float],
    return_1m: Optional[float],
    return_3m: Optional[float],
    near_high: bool,
) -> str:
    """Classify market regime using rule-based thresholds.

    Priority order: CRASH > BEAR > BULL > NORMAL
    """
    # CRASH conditions (either severe drawdown or sharp 1-month drop)
    if drawdown is not None and drawdown <= CRASH_DRAWDOWN_PCT:
        return "CRASH"
    if return_1m is not None and return_1m <= CRASH_1M_RETURN_PCT:
        return "CRASH"

    # BEAR conditions
    if drawdown is not None and drawdown <= BEAR_DRAWDOWN_PCT:
        return "BEAR"
    if return_3m is not None and return_3m <= BEAR_3M_RETURN_PCT:
        return "BEAR"

    # BULL conditions (positive return AND near high)
    if return_3m is not None and return_3m >= BULL_3M_RETURN_PCT and near_high:
        return "BULL"

    return "NORMAL"


def _estimate_regime_age(closes: List[float], regime: str) -> int:
    """Estimate how many days the current regime has persisted.

    Uses a simple heuristic: walk backward through closes and find when
    the regime classification would have changed.

    Returns approximate number of trading days in current regime.
    """
    if len(closes) < 30:
        return len(closes)

    # Walk backward, re-checking regime at each point
    current_regime = regime
    age = 0
    for end_idx in range(len(closes) - 1, 29, -1):
        sub_closes = closes[:end_idx + 1]
        dd = _compute_drawdown_from_high(sub_closes)
        r1m = _compute_return(sub_closes, 21)
        r3m = _compute_return(sub_closes, 63)
        near_high = dd is not None and dd > -BULL_NEAR_HIGH_PCT

        check_regime = _classify_regime(dd, r1m, r3m, near_high)
        if check_regime != current_regime:
            break
        age += 1

    return age


def create_deterministic_regime_analyst():
    """Create a deterministic Regime Detection Analyst node.

    Fully deterministic — no LLM calls, no tool nodes needed.
    Uses EGX30 index data (^EGX30 or EGX30.CA) for regime classification,
    NOT the individual stock's data.

    Returns:
        Callable node function compatible with LangGraph
    """

    def regime_analyst_node(state):
        trade_date = state["trade_date"]
        ticker = state["company_of_interest"]

        # ── 1. Fetch EGX30 index data ─────────────────────────────────────
        from tradingagents.dataflows.eodhd import get_eodhd_stock_data
        from dateutil.relativedelta import relativedelta

        # Need ~252 trading days (~370 calendar days) for 52-week high
        start_date = (
            datetime.strptime(trade_date, "%Y-%m-%d") - relativedelta(days=400)
        ).strftime("%Y-%m-%d")

        # Try EGX30 index tickers
        index_tickers = ["EGX30.CA", "^EGX30", "CASE30.CA"]
        closes: List[float] = []

        for idx_ticker in index_tickers:
            try:
                price_result = get_eodhd_stock_data(idx_ticker, start_date, trade_date)
                if price_result and price_result.get("data"):
                    closes = [bar["close"] for bar in price_result["data"]]
                    if len(closes) >= 30:
                        logger.info("Loaded %d bars for %s", len(closes), idx_ticker)
                        break
            except Exception as e:
                logger.debug("Failed to fetch %s: %s", idx_ticker, e)
                continue

        # Fallback: use the stock's own data if no index data available
        if len(closes) < 30:
            logger.warning("No EGX30 index data available, using %s as proxy", ticker)
            try:
                price_result = get_eodhd_stock_data(ticker, start_date, trade_date)
                if price_result and price_result.get("data"):
                    closes = [bar["close"] for bar in price_result["data"]]
            except Exception as e:
                logger.warning("Failed to fetch %s: %s", ticker, e)

        # ── 2. Compute regime metrics ─────────────────────────────────────
        drawdown = _compute_drawdown_from_high(closes, high_window=252)
        return_1m = _compute_return(closes, 21)
        return_3m = _compute_return(closes, 63)
        return_6m = _compute_return(closes, 126)
        vol_63d = _compute_annualized_vol(closes, window=63)
        near_high = drawdown is not None and drawdown > -BULL_NEAR_HIGH_PCT

        # Classify regime
        regime = _classify_regime(drawdown, return_1m, return_3m, near_high)

        # Regime age (approximate)
        regime_age_days = _estimate_regime_age(closes, regime) if len(closes) >= 30 else 0

        # Volatility persistence flag (Ezzat 2013 — Joseph Effect)
        vol_persistence_flag = False
        if vol_63d is not None and vol_63d > HIGH_VOL_THRESHOLD:
            # Check if vol was also high 90 days ago
            if len(closes) >= 90 + 63:
                old_vol = _compute_annualized_vol(closes[:-90], window=63)
                if old_vol is not None and old_vol > HIGH_VOL_THRESHOLD:
                    vol_persistence_flag = True

        # Duration dependence warning (Giner & Zakamulin 2023)
        duration_warning = None
        if regime == "BULL" and regime_age_days > 120:
            duration_warning = (
                f"Bull regime has persisted for ~{regime_age_days} trading days. "
                f"Duration dependence research suggests increasing reversal probability."
            )

        # ── 3. Build structured analysis ──────────────────────────────────
        structured_analysis = {
            "regime": regime,
            "regime_age_days": regime_age_days,
            "strategy_bias": REGIME_STRATEGY_BIAS[regime],
            "market_regime_enum": REGIME_TO_MARKET_REGIME[regime],
            "drawdown_from_52w_high": round(drawdown, 4) if drawdown is not None else None,
            "return_1m": round(return_1m, 4) if return_1m is not None else None,
            "return_3m": round(return_3m, 4) if return_3m is not None else None,
            "return_6m": round(return_6m, 4) if return_6m is not None else None,
            "annualized_vol_63d": round(vol_63d, 4) if vol_63d is not None else None,
            "vol_persistence_flag": vol_persistence_flag,
            "duration_warning": duration_warning,
            "data_bars_available": len(closes),
        }

        # ── 4. Build text report ──────────────────────────────────────────
        dd_str = f"{drawdown:.1%}" if drawdown is not None else "N/A"
        r1m_str = f"{return_1m:+.1%}" if return_1m is not None else "N/A"
        r3m_str = f"{return_3m:+.1%}" if return_3m is not None else "N/A"
        r6m_str = f"{return_6m:+.1%}" if return_6m is not None else "N/A"
        vol_str = f"{vol_63d:.1%}" if vol_63d is not None else "N/A"

        report = (
            f"Market Regime Analysis (EGX30 index) on {trade_date}:\n"
            f"Regime: {regime} | Strategy Bias: {REGIME_STRATEGY_BIAS[regime]}\n"
            f"Drawdown from 52W High: {dd_str}\n"
            f"Returns — 1M: {r1m_str} | 3M: {r3m_str} | 6M: {r6m_str}\n"
            f"Volatility (63d annualized): {vol_str}\n"
            f"Regime Age: ~{regime_age_days} trading days\n"
        )

        if vol_persistence_flag:
            report += "⚠ VOLATILITY PERSISTENCE: High-vol regime has persisted >90 days (Joseph Effect).\n"
        if duration_warning:
            report += f"⚠ DURATION WARNING: {duration_warning}\n"

        return {
            "regime_report": report,
            "regime_analysis": structured_analysis,
            "regime_messages": [],  # Deterministic — no tool calls
        }

    return regime_analyst_node
