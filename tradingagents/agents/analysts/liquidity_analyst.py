"""
Liquidity / Flow Analyst for EGX Market
========================================
Fully deterministic — no LLM calls.

Computes microstructure and liquidity metrics from OHLCV data:
- Amihud ILLIQ ratio (Amihud 2002)
- Zero-return frequency (BHL 2007; Sussex/African Markets 2023)
- Average Daily Volume (ADV) and ADV trend
- Volume concentration (are a few days driving all volume?)
- Bid-ask spread proxy from high-low range (Corwin & Schultz 2012)

Sussex (2023) shows zero-return frequency outperforms Amihud on EGX specifically.

Non-directional: liquidity analysis does NOT enter the directional quorum.
It modifies confidence and position size via risk checks and the sentiment blend cascade.

Phase 2B: Foreign flow data (FPI) deferred to Phase 5 — requires manual CSV or CBE API.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from tradingagents.dataflows.config import get_config

logger = logging.getLogger("tradingagents.analysts.liquidity")

# ─── Liquidity thresholds (EGX-specific) ──────────────────────────────────────
ZERO_RETURN_HIGH_THRESHOLD = 0.30     # >30% zero-return days = severe illiquidity
ZERO_RETURN_MODERATE_THRESHOLD = 0.15  # >15% = moderate illiquidity concern
ADV_LOW_THRESHOLD = 50_000            # <50K shares/day = low liquidity (EGX context)
ADV_VERY_LOW_THRESHOLD = 10_000       # <10K shares/day = very low liquidity
VOLUME_CONCENTRATION_HIGH = 0.60       # Top 20% of days account for >60% of volume
ILLIQ_HIGH_THRESHOLD = 1e-5           # Amihud ILLIQ above this = illiquid


def _compute_amihud_illiq(
    closes: List[float], volumes: List[float], window: int = 21
) -> Optional[float]:
    """Amihud (2002) illiquidity ratio: mean(|r_t| / volume_t)."""
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
    """Fraction of days with zero returns (Sussex/African Markets 2023)."""
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


def _compute_adv(volumes: List[float], window: int = 21) -> Optional[float]:
    """Average daily volume over the last `window` trading days."""
    if len(volumes) < window:
        return None
    recent = volumes[-window:]
    return sum(recent) / len(recent)


def _compute_adv_trend(volumes: List[float], short_window: int = 5, long_window: int = 21) -> Optional[float]:
    """ADV trend: ratio of short-term ADV to long-term ADV.

    >1.0 means volume is increasing; <1.0 means declining.
    """
    if len(volumes) < long_window:
        return None
    short_adv = sum(volumes[-short_window:]) / short_window
    long_adv = sum(volumes[-long_window:]) / long_window
    if long_adv <= 0:
        return None
    return short_adv / long_adv


def _compute_volume_concentration(volumes: List[float], window: int = 21) -> Optional[float]:
    """Fraction of total volume from top 20% of trading days.

    High concentration means liquidity is episodic, not steady.
    """
    if len(volumes) < window:
        return None
    recent = sorted(volumes[-window:], reverse=True)
    top_n = max(1, window // 5)  # Top 20%
    total = sum(recent)
    if total <= 0:
        return None
    return sum(recent[:top_n]) / total


def _compute_hl_spread_proxy(
    highs: List[float], lows: List[float], window: int = 21
) -> Optional[float]:
    """Corwin & Schultz (2012) high-low spread proxy.

    Simplified version: average (high - low) / midpoint over window.
    """
    n = min(len(highs), len(lows))
    if n < window:
        return None
    try:
        spreads = []
        for i in range(-window, 0):
            h = highs[i]
            l = lows[i]
            mid = (h + l) / 2.0
            if mid > 0 and h >= l:
                spreads.append((h - l) / mid)
        if not spreads:
            return None
        return sum(spreads) / len(spreads)
    except (IndexError, ZeroDivisionError):
        return None


def _classify_liquidity(
    zero_return: Optional[float],
    adv: Optional[float],
    amihud: Optional[float],
    vol_concentration: Optional[float],
) -> str:
    """Classify overall liquidity: ADEQUATE / MODERATE_CONCERN / ILLIQUID / SEVERELY_ILLIQUID."""
    concern_count = 0

    if zero_return is not None:
        if zero_return >= ZERO_RETURN_HIGH_THRESHOLD:
            return "SEVERELY_ILLIQUID"
        elif zero_return >= ZERO_RETURN_MODERATE_THRESHOLD:
            concern_count += 1

    if adv is not None:
        if adv < ADV_VERY_LOW_THRESHOLD:
            return "SEVERELY_ILLIQUID"
        elif adv < ADV_LOW_THRESHOLD:
            concern_count += 1

    if amihud is not None and amihud > ILLIQ_HIGH_THRESHOLD:
        concern_count += 1

    if vol_concentration is not None and vol_concentration > VOLUME_CONCENTRATION_HIGH:
        concern_count += 1

    if concern_count >= 2:
        return "ILLIQUID"
    elif concern_count >= 1:
        return "MODERATE_CONCERN"
    return "ADEQUATE"


def _load_fpi_flow(ticker: str, trade_date: str) -> Optional[Dict[str, Any]]:
    """Load foreign portfolio investor (FPI) net flow from CSV.

    Expected file: dataflows/data_cache/egx_macro/foreign_flow.csv
    Columns: date, available_at, ticker, fpi_net_buy_egp, fpi_net_sell_egp,
             fpi_net_flow_egp, source, data_quality

    Filters by ``available_at`` when present, falling back to ``date``.
    NOTE: available_at is currently set to date + 1 business day (schema
    support only — not yet verified against real EGX publication lag).

    data_quality must be "real" or "verified" for the signal to be decision-impacting.
    Rows marked "stub", "demo", or "provisional" are reported for visibility
    but will not escalate liquidity classification.

    Returns dict with fpi_net_flow_egp, fpi_signal, and data_quality,
    or None if no data available.
    """
    import os
    config = get_config()
    data_dir = config.get("data_cache_dir", "")
    csv_path = os.path.join(data_dir, "egx_macro", "foreign_flow.csv")

    if not os.path.exists(csv_path):
        return None

    try:
        import csv
        latest_flow = None
        latest_quality = "stub"
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                effective_date = row.get("available_at") or row.get("date", "")
                row_ticker = row.get("ticker", "")
                if row_ticker == ticker and effective_date <= trade_date:
                    if row.get("fpi_net_flow_egp"):
                        latest_flow = float(row["fpi_net_flow_egp"])
                        latest_quality = row.get("data_quality", "stub").strip().lower()

        if latest_flow is None:
            return None

        # Classify FPI signal based on net flow magnitude (EGP)
        if latest_flow > 50_000_000:
            signal = "STRONG_INFLOW"
        elif latest_flow > 10_000_000:
            signal = "INFLOW"
        elif latest_flow >= -10_000_000:
            signal = "NEUTRAL"
        elif latest_flow >= -50_000_000:
            signal = "OUTFLOW"
        else:
            signal = "STRONG_OUTFLOW"

        return {
            "fpi_net_flow_egp": latest_flow,
            "fpi_signal": signal,
            "data_quality": latest_quality,
        }
    except Exception as e:
        logger.warning("Failed to load FPI flow CSV for %s: %s", ticker, e)
        return None


def create_deterministic_liquidity_analyst():
    """Create a deterministic Liquidity/Flow Analyst node.

    Fully deterministic — no LLM calls, no tool nodes needed.
    Reads OHLCV data from the same source as Market Analyst (EODHD/yfinance).

    Returns:
        Callable node function compatible with LangGraph
    """

    def liquidity_analyst_node(state):
        trade_date = state["trade_date"]
        ticker = state["company_of_interest"]
        config = get_config()

        # ── 1. Fetch price data ───────────────────────────────────────────
        from tradingagents.dataflows.eodhd import get_eodhd_stock_data
        from dateutil.relativedelta import relativedelta

        start_date = (
            datetime.strptime(trade_date, "%Y-%m-%d") - relativedelta(days=120)
        ).strftime("%Y-%m-%d")

        price_result = get_eodhd_stock_data(ticker, start_date, trade_date)

        closes: List[float] = []
        volumes: List[float] = []
        highs: List[float] = []
        lows: List[float] = []

        if price_result and price_result.get("data"):
            for bar in price_result["data"]:
                closes.append(bar["close"])
                volumes.append(bar.get("volume", 0))
                highs.append(bar.get("high", bar["close"]))
                lows.append(bar.get("low", bar["close"]))

        # ── 2. Compute liquidity metrics ──────────────────────────────────
        amihud_21d = _compute_amihud_illiq(closes, volumes, window=21)
        zero_return_21d = _compute_zero_return_freq(closes, window=21)
        adv_21d = _compute_adv(volumes, window=21)
        adv_trend = _compute_adv_trend(volumes, short_window=5, long_window=21)
        vol_concentration = _compute_volume_concentration(volumes, window=21)
        hl_spread = _compute_hl_spread_proxy(highs, lows, window=21)

        # Overall classification
        liquidity_class = _classify_liquidity(
            zero_return_21d, adv_21d, amihud_21d, vol_concentration
        )

        # FPI foreign flow integration (Phase 5)
        # Escalation only applies when data_quality is "real" or "verified".
        # Stub/demo/provisional data is reported for visibility but does not
        # affect liquidity_classification.
        fpi_data = _load_fpi_flow(ticker, trade_date)
        fpi_is_verified = (
            fpi_data is not None
            and fpi_data.get("data_quality", "stub") in ("real", "verified")
        )
        if fpi_is_verified and fpi_data["fpi_signal"] == "STRONG_OUTFLOW":
            escalation = {
                "ADEQUATE": "MODERATE_CONCERN",
                "MODERATE_CONCERN": "ILLIQUID",
                "ILLIQUID": "SEVERELY_ILLIQUID",
            }
            if liquidity_class in escalation:
                logger.info(
                    "FPI STRONG_OUTFLOW escalated %s → %s for %s",
                    liquidity_class, escalation[liquidity_class], ticker,
                )
                liquidity_class = escalation[liquidity_class]

        # Position sizing constraint: max shares = max_position_pct_adv * ADV
        max_pct_adv = config.get("max_position_pct_adv", 0.10)
        max_shares_adv = int(adv_21d * max_pct_adv) if adv_21d else None

        # Days to exit estimate (assuming 10% ADV participation)
        # For a hypothetical 1M EGP position at current price
        current_price = state.get("current_price", 0)
        days_to_exit = None
        if adv_21d and current_price and adv_21d > 0 and current_price > 0:
            hypothetical_shares = 1_000_000 / current_price  # 1M EGP position
            daily_capacity = adv_21d * max_pct_adv
            if daily_capacity > 0:
                days_to_exit = math.ceil(hypothetical_shares / daily_capacity)

        # ── 3. Build structured analysis ──────────────────────────────────
        structured_analysis = {
            "liquidity_classification": liquidity_class,
            "amihud_illiq_21d": round(amihud_21d, 10) if amihud_21d is not None else None,
            "zero_return_frequency_21d": round(zero_return_21d, 4) if zero_return_21d is not None else None,
            "avg_daily_volume_21d": round(adv_21d, 0) if adv_21d is not None else None,
            "adv_trend_5d_vs_21d": round(adv_trend, 3) if adv_trend is not None else None,
            "volume_concentration_top20pct": round(vol_concentration, 3) if vol_concentration is not None else None,
            "hl_spread_proxy_21d": round(hl_spread, 5) if hl_spread is not None else None,
            "max_shares_at_10pct_adv": max_shares_adv,
            "days_to_exit_1m_egp": days_to_exit,
            "fpi_net_flow_egp": fpi_data["fpi_net_flow_egp"] if fpi_data else None,
            "fpi_signal": fpi_data["fpi_signal"] if fpi_data else None,
            "fpi_data_quality": fpi_data.get("data_quality", "stub") if fpi_data else None,
            "data_bars_available": len(closes),
        }

        # ── 4. Build text report ──────────────────────────────────────────
        adv_str = f"{adv_21d:,.0f}" if adv_21d else "N/A"
        zr_str = f"{zero_return_21d:.1%}" if zero_return_21d is not None else "N/A"
        trend_str = f"{adv_trend:.2f}x" if adv_trend is not None else "N/A"
        dte_str = f"{days_to_exit}d" if days_to_exit is not None else "N/A"

        report = (
            f"Liquidity Analysis for {ticker} on {trade_date}:\n"
            f"Classification: {liquidity_class}\n"
            f"ADV (21d): {adv_str} shares | ADV Trend (5d/21d): {trend_str}\n"
            f"Zero-Return Freq (21d): {zr_str}\n"
            f"Days to Exit 1M EGP: {dte_str}\n"
        )

        if fpi_data:
            fpi_m = fpi_data["fpi_net_flow_egp"] / 1_000_000
            report += f"FPI Net Flow: {fpi_m:+,.1f}M EGP ({fpi_data['fpi_signal']})\n"

        if liquidity_class in ("ILLIQUID", "SEVERELY_ILLIQUID"):
            report += "⚠ LIQUIDITY WARNING: Position sizing must be adjusted.\n"

        return {
            "liquidity_report": report,
            "liquidity_analysis": structured_analysis,
            "liquidity_messages": [],  # Deterministic — no tool calls
        }

    return liquidity_analyst_node
