"""
Macro / FX / Rates Analyst for EGX Market
==========================================
Hybrid agent: deterministic data collection + LLM interpretation.

Data sources (priority: point-in-time CSV -> FRED/yfinance where available):
- CBE policy rate: egx_macro/egypt_macro.csv
- EGP/USD official rate: yfinance EGPUSD=X (live)
- T-bill 91-day yield: egx_macro/egypt_macro.csv -> FRED API
- Egypt CPI / inflation: egx_macro/egypt_macro.csv -> FRED API
- VIX (global risk proxy): yfinance ^VIX (live)
- Parallel FX premium: egx_macro/fx_premium.csv (Harvard/Oki 2023 — clause 20)

The LLM synthesizes these into a MacroDirection signal (RISK_ON / RISK_OFF / NEUTRAL)
and a narrative for downstream Bull/Bear researchers.

Non-directional: macro analysis does NOT enter the directional quorum.
It modifies confidence and position size via the sentiment blend cascade.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from tradingagents.dataflows.config import get_config

logger = logging.getLogger("tradingagents.analysts.macro")

# ─── Legacy static macro data ────────────────────────────────────────────────
# Kept only for historical reference while tests and callers migrate. The macro
# analyst no longer uses these values as a fallback because that leaked
# late-2024 demo values into earlier backtests.
_STATIC_MACRO = {
    "cbe_policy_rate": 0.275,          # 27.5% — CBE overnight lending rate (late 2024)
    "cbe_last_change_date": "2024-03-06",
    "cbe_last_change_bps": 600,        # +600 bps in March 2024
    "egp_usd_official": 48.5,          # Approximate EGP/USD (late 2024)
    "tbill_91d_yield": 0.26,           # 26% nominal (late 2024)
    "egypt_cpi_yoy": 0.258,            # 25.8% YoY CPI (approx late 2024)
    "vix_level": None,                 # Fetched live from yfinance
}

# ─── Macro interpretation thresholds ──────────────────────────────────────────
# Egypt-specific: post-2016 inflation-hedge regime means rising CPI is NOT
# automatically RISK_OFF (World Scientific 2025, ARDL evidence 1998-2024).
REAL_YIELD_RISK_OFF_THRESHOLD = 0.05    # T-bill real yield > 5% → RISK_OFF (capital flight to fixed income)
REAL_YIELD_RISK_ON_THRESHOLD = -0.02    # Negative real yield → RISK_ON (equities as inflation hedge)
VIX_ELEVATED_THRESHOLD = 25.0
VIX_PANIC_THRESHOLD = 35.0
RATE_HIKE_LARGE_BPS = 200              # ≥200 bps single hike → notable shock
RATE_SHOCK_RECENCY_DAYS = 14           # Calendar days after CBE decision within which rate_shock fires


def _fetch_yf_generic(symbol: str, start: str, end: str) -> list:
    """Fetch OHLCV from yfinance for non-EGX symbols (VIX, FX pairs).

    Unlike get_YFin_data_online, this does NOT append .CA, does NOT use a
    period-fallback, and does NOT truncate to 20 bars.  Designed exclusively
    for macro data (^VIX, EGPUSD=X) where the EGX normalization would
    corrupt the symbol.

    Returns list of {"date": str, "close": float} dicts, or [] on failure.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        data = ticker.history(start=start, end=end, interval="1d")
        if data.empty:
            logger.info("yfinance returned no data for %s (%s to %s)", symbol, start, end)
            return []
        if data.index.tz is not None:
            data.index = data.index.tz_localize(None)
        return [
            {"date": idx.strftime("%Y-%m-%d"), "close": float(row["Close"])}
            for idx, row in data.iterrows()
        ]
    except Exception as e:
        logger.warning("yfinance fetch failed for %s (%s to %s): %s", symbol, start, end, e)
        return []


def _fetch_vix(trade_date: str) -> Optional[float]:
    """Fetch VIX level from yfinance as of trade_date."""
    start = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=10)).strftime("%Y-%m-%d")
    bars = _fetch_yf_generic("^VIX", start, trade_date)
    if bars:
        return bars[-1]["close"]
    return None


def _fetch_egp_usd(trade_date: str) -> Optional[float]:
    """Fetch EGP/USD from yfinance."""
    start = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=10)).strftime("%Y-%m-%d")
    bars = _fetch_yf_generic("EGPUSD=X", start, trade_date)
    if bars:
        # yfinance returns EGP per 1 USD
        return bars[-1]["close"]
    return None


def _load_macro_csv(trade_date: str) -> Dict[str, Any]:
    """Load macro data from local CSV if available.

    Expected file: dataflows/data_cache/egx_macro/egypt_macro.csv
    Columns: date, available_at, cbe_rate, tbill_91d, cpi_yoy, egp_usd, ...

    Filters by ``available_at`` (publication/availability date) rather than
    ``date`` (observation date) to avoid data leakage in backtests.  Falls
    back to ``date`` if ``available_at`` is absent for backward compatibility.

    NOTE: available_at values in the CSV are currently set equal to date
    (schema support only).  This does NOT fully fix CPI publication lag.
    To actually fix the leakage, each row's available_at must be verified
    against real publication dates (TODO — P2 schema redesign).

    Returns dict of latest values on or before trade_date.
    """
    import os
    config = get_config()
    data_dir = config.get("data_cache_dir", "")
    csv_path = os.path.join(data_dir, "egx_macro", "egypt_macro.csv")

    if not os.path.exists(csv_path):
        return {}

    try:
        import csv
        result = {}
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Use available_at if present, else fall back to date
                row_date = row.get("date", "")
                effective_date = row.get("available_at") or row_date
                if effective_date <= trade_date:
                    # Keep updating — last valid row wins
                    if row.get("cbe_rate"):
                        result["cbe_policy_rate"] = float(row["cbe_rate"])
                    if row.get("tbill_91d"):
                        result["tbill_91d_yield"] = float(row["tbill_91d"])
                    if row.get("cpi_yoy"):
                        result["egypt_cpi_yoy"] = float(row["cpi_yoy"])
                    if row.get("egp_usd"):
                        result["egp_usd_official"] = float(row["egp_usd"])
                    if row.get("cbe_change_bps"):
                        result["cbe_last_change_bps"] = int(row["cbe_change_bps"])
                        result["cbe_last_change_date"] = row_date
        return result
    except Exception as e:
        logger.warning("Failed to load macro CSV: %s", e)
        return {}


def _fetch_fred_macro(trade_date: str) -> Dict[str, Any]:
    """Fetch CPI and T-bill from FRED API as fallback when CSV is missing values.

    DISABLED as of provenance audit (2026-05-14):
    - FRED CPI series (FPCPITOTLZGEGY) is annual frequency and uses World Bank
      methodology, not CAPMAS monthly urban headline CPI. Mixing the two would
      produce inconsistent real-yield signals.
    - FRED T-bill series (INTGSTEGY91N) does not exist on FRED.
    - The fred_provider.py infrastructure is kept for future use if suitable
      monthly series are identified.

    Returns empty dict until a compatible FRED series is found.
    """
    return {}


def _load_tbill_yield(trade_date: str) -> Optional[float]:
    """Load 91-day T-bill monthly average yield from tbill_yields.csv.

    Source: MoF Financial Monthly Table 29 (citing CBE), cross-verified
    across three bulletins:

      - Vol 19 No 1  (Nov 2023, created 2023-11-29): Jun 2022 – Jul 2023
      - Vol 19 No 11 (Sep 2024, created 2024-09-30): Aug 2023 – Apr 2024
      - Vol 20 No 8  (Jun 2025, created 2025-06-18): May 2024 – Feb 2025

    Each row's ``available_at`` is the PDF creation date of the earliest
    bulletin that contains it, so backtests only see yields that were
    actually published.  ``data_quality`` is ``verified_pit_monthly`` for
    rows with a period-contemporary bulletin, or ``verified_ex_post_monthly``
    for rows only available from the Jun 2025 bulletin.

    Filters by ``available_at`` to maintain point-in-time discipline.

    Returns most recent yield as decimal (e.g. 0.2560 for 25.60%), or None.
    """
    import os
    config = get_config()
    data_dir = config.get("data_cache_dir", "")
    csv_path = os.path.join(data_dir, "egx_macro", "tbill_yields.csv")

    if not os.path.exists(csv_path):
        return None

    try:
        import csv
        latest_yield = None
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                effective_date = row.get("available_at") or row.get("date", "")
                if effective_date <= trade_date and row.get("avg_yield"):
                    latest_yield = float(row["avg_yield"])
        return latest_yield
    except Exception as e:
        logger.warning("Failed to load T-bill yield CSV: %s", e)
        return None


def _fetch_fx_change_30d(trade_date: str) -> Optional[float]:
    """Compute 30-day EGP/USD percentage change via yfinance.

    Returns fractional change (e.g. 0.05 for +5%).
    Used by risk_scorer.check_fx_stress() for live FX stress detection.
    """
    start = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=45)).strftime("%Y-%m-%d")
    bars = _fetch_yf_generic("EGPUSD=X", start, trade_date)
    if len(bars) >= 20:
        current = bars[-1]["close"]
        past = bars[-21]["close"]  # ~30 calendar days ago
        if past > 0:
            return (current - past) / past
    return None


def _load_fx_premium(trade_date: str) -> Optional[Dict[str, Any]]:
    """Load parallel FX premium from CSV if available.

    Expected file: dataflows/data_cache/egx_macro/fx_premium.csv
    Columns: date, available_at, official_rate, parallel_rate, premium_pct,
             source, data_quality

    Filters by ``available_at`` when present, falling back to ``date``.
    NOTE: available_at is currently set equal to date (schema support only).

    data_quality must be "real" or "verified" for ELEVATED/CRITICAL signals
    to be decision-impacting. Stub/demo/provisional data defaults to NORMAL.

    Practitioner heuristic: parallel FX premium is a leading indicator of
    EGP devaluation (citation incomplete — see macro review). Constitution
    clause 20.
    """
    import os
    config = get_config()
    data_dir = config.get("data_cache_dir", "")
    csv_path = os.path.join(data_dir, "egx_macro", "fx_premium.csv")

    if not os.path.exists(csv_path):
        return None

    try:
        import csv
        latest = None
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                effective_date = row.get("available_at") or row.get("date", "")
                if effective_date <= trade_date:
                    latest = row
        if latest and latest.get("premium_pct"):
            premium = float(latest["premium_pct"])
            quality = latest.get("data_quality", "stub").strip().lower()
            is_verified = quality in ("real", "verified")

            # Only emit decision-impacting signals for verified data.
            # Stub data always maps to NORMAL (pass-through).
            if not is_verified:
                signal = "NORMAL"
            elif premium > 25.0:
                signal = "CRITICAL"
            elif premium > 10.0:
                signal = "ELEVATED"
            else:
                signal = "NORMAL"

            return {
                "fx_premium_pct": round(premium, 2),
                "fx_premium_signal": signal,
                "fx_premium_date": latest.get("date", ""),
                "data_quality": quality,
            }
    except Exception as e:
        logger.warning("Failed to load FX premium CSV: %s", e)
    return None


def _compute_macro_signals(macro_data: Dict[str, Any], trade_date: str = "") -> Dict[str, Any]:
    """Compute deterministic macro signals from raw data.

    Returns dict with:
      - real_yield_91d: T-bill yield minus CPI YoY
      - real_yield_signal: RISK_OFF / RISK_ON / NEUTRAL
      - rate_shock: bool (recent large rate change within recency window)
      - cbe_last_change_bps: magnitude of last CBE rate change
      - cbe_last_change_date: date of last CBE rate change
      - vix_regime: CALM / ELEVATED / PANIC / None
      - fx_signal: description of EGP/USD state
    """
    signals = {}

    # ── Real yield (T-bill minus inflation) ───────────────────────────────
    tbill = macro_data.get("tbill_91d_yield")
    cpi = macro_data.get("egypt_cpi_yoy")
    if tbill is not None and cpi is not None:
        real_yield = tbill - cpi
        signals["real_yield_91d"] = round(real_yield, 4)
        if real_yield > REAL_YIELD_RISK_OFF_THRESHOLD:
            signals["real_yield_signal"] = "RISK_OFF"
        elif real_yield < REAL_YIELD_RISK_ON_THRESHOLD:
            signals["real_yield_signal"] = "RISK_ON"
        else:
            signals["real_yield_signal"] = "NEUTRAL"
    else:
        signals["real_yield_91d"] = None
        signals["real_yield_signal"] = None

    # ── Rate shock (time-aware) ───────────────────────────────────────────
    # rate_shock fires only when the CBE change was large AND recent.
    # Historical backtests remain point-in-time: uses latest row on or before
    # trade_date, never future rows.
    last_change_bps = macro_data.get("cbe_last_change_bps", 0)
    last_change_date = macro_data.get("cbe_last_change_date", "")
    signals["cbe_last_change_bps"] = last_change_bps
    signals["cbe_last_change_date"] = last_change_date

    magnitude_ok = abs(last_change_bps) >= RATE_HIKE_LARGE_BPS
    recency_ok = False
    if magnitude_ok and last_change_date and trade_date:
        try:
            td = datetime.strptime(trade_date, "%Y-%m-%d")
            cd = datetime.strptime(last_change_date, "%Y-%m-%d")
            recency_ok = 0 <= (td - cd).days <= RATE_SHOCK_RECENCY_DAYS
        except ValueError:
            pass
    signals["rate_shock"] = magnitude_ok and recency_ok
    signals["cbe_policy_rate"] = macro_data.get("cbe_policy_rate")

    # ── VIX regime ────────────────────────────────────────────────────────
    vix = macro_data.get("vix_level")
    if vix is not None:
        if vix >= VIX_PANIC_THRESHOLD:
            signals["vix_regime"] = "PANIC"
        elif vix >= VIX_ELEVATED_THRESHOLD:
            signals["vix_regime"] = "ELEVATED"
        else:
            signals["vix_regime"] = "CALM"
        signals["vix_level"] = round(vix, 2)
    else:
        signals["vix_regime"] = None
        signals["vix_level"] = None

    # ── FX state ──────────────────────────────────────────────────────────
    egp_usd = macro_data.get("egp_usd_official")
    signals["egp_usd"] = round(egp_usd, 2) if egp_usd else None
    signals["egypt_cpi_yoy"] = round(cpi, 4) if cpi else None

    # ── Composite direction ───────────────────────────────────────────────
    # Simple rule: count RISK_OFF vs RISK_ON signals
    risk_off_count = 0
    risk_on_count = 0

    if signals.get("real_yield_signal") == "RISK_OFF":
        risk_off_count += 1
    elif signals.get("real_yield_signal") == "RISK_ON":
        risk_on_count += 1

    if signals.get("rate_shock"):
        risk_off_count += 1  # Rate shocks are generally risk-off

    if signals.get("vix_regime") == "PANIC":
        risk_off_count += 2  # Double weight for VIX panic
    elif signals.get("vix_regime") == "ELEVATED":
        risk_off_count += 1
    elif signals.get("vix_regime") == "CALM":
        risk_on_count += 1

    # Check whether ANY signal actually contributed to the tally.
    # If every input was None, this is insufficient data, not a balanced read.
    has_any_signal = (
        signals.get("real_yield_signal") is not None
        or signals.get("rate_shock") is True
        or signals.get("vix_regime") is not None
    )

    if not has_any_signal:
        signals["composite_direction"] = "NO_SIGNAL"
    elif risk_off_count >= 2:
        signals["composite_direction"] = "RISK_OFF"
    elif risk_on_count >= 2:
        # Rate-shock guard: a recent ≥200bps CBE move is the most disruptive
        # local EGX macro event.  Even if negative real yield + calm VIX
        # produce 2 RISK_ON votes, labelling the window "RISK_ON" is
        # misleading.  Clamp to NEUTRAL; RISK_OFF is unaffected.
        # Ablation Case 2 (Mar 2024, +600bps) motivated this rule.
        if signals.get("rate_shock"):
            signals["composite_direction"] = "NEUTRAL"
        else:
            signals["composite_direction"] = "RISK_ON"
    else:
        signals["composite_direction"] = "NEUTRAL"

    return signals


def create_macro_analyst(quick_llm=None, deep_llm=None):
    """Create a Macro/FX/Rates Analyst node.

    When both LLMs are None, runs in fully deterministic mode (no LLM call).
    When an LLM is provided, uses it to generate a narrative interpretation
    of the macro signals for the Bull/Bear researchers.

    Args:
        quick_llm: Quick-thinking LLM for narrative generation (optional)
        deep_llm: Deep-thinking LLM (unused, reserved for future macro reasoning)

    Returns:
        Callable node function compatible with LangGraph
    """

    def macro_analyst_node(state):
        trade_date = state["trade_date"]
        ticker = state["company_of_interest"]

        # ── 1. Collect macro data (point-in-time CSV -> FRED/yfinance) ────
        # First CSV row is 2023-01-05. For earlier dates, static defaults
        # would leak late-2024 values (CBE 27.5%, CPI 25.8%) into the
        # backtest — a data leakage bug. Guard against this.
        _FIRST_CSV_DATE = "2023-02-02"

        # Layer 1: CSV overrides (fast, no network)
        csv_data = _load_macro_csv(trade_date)

        if csv_data:
            # CSV matched at least one row. Use only fields actually present in
            # the point-in-time CSV (plus later FRED/yfinance fills). Do not
            # silently backfill missing CPI/T-bill/FX fields with static demo
            # values, because the macro CSV now intentionally leaves
            # unsupported fields blank.
            macro_data = dict(csv_data)
        elif trade_date < _FIRST_CSV_DATE:
            # No CSV data and trade_date predates CSV coverage.
            # Static defaults are from late 2024 — using them here would
            # leak future information. Start with an empty dict so
            # downstream signals come back as None / NO_SIGNAL.
            logger.warning(
                "No macro CSV data for %s (before coverage start %s). "
                "Static defaults suppressed to prevent data leakage.",
                trade_date, _FIRST_CSV_DATE,
            )
            macro_data = {}
        else:
            # trade_date is within CSV range but no rows matched (gap).
            # Do not use static demo values for historical macro gaps.
            logger.warning(
                "No macro CSV rows matched for %s. Returning unavailable "
                "macro fields instead of static demo values.", trade_date,
            )
            macro_data = {}

        # Layer 1b: T-bill yield from dedicated CSV (MoF Table 29)
        # Separate file because T-bill data is monthly average frequency,
        # vs MPC-date frequency for the main macro CSV.
        tbill_csv_yield = None
        if "tbill_91d_yield" not in macro_data:
            tbill_csv_yield = _load_tbill_yield(trade_date)
            if tbill_csv_yield is not None:
                macro_data["tbill_91d_yield"] = tbill_csv_yield

        # Layer 2: FRED API (currently disabled — no suitable Egypt series)
        fred_data = _fetch_fred_macro(trade_date)

        # Layer 3: Live market data from yfinance
        vix = _fetch_vix(trade_date)
        if vix is not None:
            macro_data["vix_level"] = vix

        egp_usd = _fetch_egp_usd(trade_date)
        if egp_usd is not None:
            macro_data["egp_usd_official"] = egp_usd

        # FX change over 30 days (for risk_scorer.check_fx_stress)
        fx_change_30d = _fetch_fx_change_30d(trade_date)

        # Parallel FX premium (Harvard/Oki 2023 — constitution clause 20)
        fx_premium = _load_fx_premium(trade_date)

        # ── 2. Compute deterministic signals ──────────────────────────────
        signals = _compute_macro_signals(macro_data, trade_date)

        # ── 3. Build structured analysis ──────────────────────────────────
        # Did we suppress static defaults due to pre-CSV-coverage date?
        _static_suppressed = (not csv_data and trade_date < _FIRST_CSV_DATE)

        def _source(field, csv_d, fred_d):
            if field in csv_d:
                return "csv"
            if field in fred_d:
                return "fred"
            if _static_suppressed:
                return "suppressed_static"
            return "unavailable"

        structured_analysis = {
            "composite_direction": signals["composite_direction"],
            "real_yield_91d": signals.get("real_yield_91d"),
            "real_yield_signal": signals.get("real_yield_signal"),
            "cbe_policy_rate": signals.get("cbe_policy_rate"),
            "rate_shock": signals.get("rate_shock", False),
            "cbe_last_change_bps": signals.get("cbe_last_change_bps", 0),
            "cbe_last_change_date": signals.get("cbe_last_change_date", ""),
            "vix_level": signals.get("vix_level"),
            "vix_regime": signals.get("vix_regime"),
            "egp_usd": signals.get("egp_usd"),
            "egypt_cpi_yoy": signals.get("egypt_cpi_yoy"),
            "fx_change_30d": round(fx_change_30d, 4) if fx_change_30d is not None else None,
            "fx_premium_pct": fx_premium["fx_premium_pct"] if fx_premium else None,
            "fx_premium_signal": fx_premium["fx_premium_signal"] if fx_premium else None,
            "data_sources": {
                "vix": "yfinance" if vix is not None else "unavailable",
                "egp_usd": "yfinance" if egp_usd is not None else _source("egp_usd_official", csv_data, {}),
                "cbe_rate": _source("cbe_policy_rate", csv_data, {}),
                "tbill": "csv" if csv_data.get("tbill_91d_yield") else ("tbill_csv" if tbill_csv_yield is not None else _source("tbill_91d_yield", {}, fred_data)),
                "cpi": _source("egypt_cpi_yoy", csv_data, fred_data),
                "fx_change_30d": "yfinance" if fx_change_30d is not None else "unavailable",
                "fx_premium": "csv" if fx_premium else "unavailable",
            },
        }

        # ── 4. Build text report ──────────────────────────────────────────
        direction = signals["composite_direction"]
        ry_str = f"{signals['real_yield_91d']:.1%}" if signals.get("real_yield_91d") is not None else "N/A"
        cbe_str = f"{signals['cbe_policy_rate']:.1%}" if signals.get("cbe_policy_rate") is not None else "N/A"
        vix_str = f"{signals['vix_level']:.1f}" if signals.get("vix_level") is not None else "N/A"
        egp_str = f"{signals['egp_usd']:.2f}" if signals.get("egp_usd") is not None else "N/A"
        cpi_str = f"{signals['egypt_cpi_yoy']:.1%}" if signals.get("egypt_cpi_yoy") is not None else "N/A"

        fx30_str = f"{fx_change_30d:+.1%}" if fx_change_30d is not None else "N/A"
        fxp_str = f"{fx_premium['fx_premium_pct']:.1f}% ({fx_premium['fx_premium_signal']})" if fx_premium else "N/A"

        report = (
            f"Macro/FX/Rates Analysis for {ticker} on {trade_date}:\n"
            f"Composite Direction: {direction}\n"
            f"CBE Policy Rate: {cbe_str} | T-bill Real Yield: {ry_str}\n"
            f"EGP/USD: {egp_str} (30d change: {fx30_str}) | CPI YoY: {cpi_str}\n"
            f"VIX: {vix_str} ({signals.get('vix_regime', 'N/A')})\n"
            f"Rate Shock: {'YES' if signals.get('rate_shock') else 'No'}\n"
            f"Parallel FX Premium: {fxp_str}\n"
        )

        # ── 5. Optional LLM narrative (if LLM provided) ──────────────────
        if quick_llm is not None:
            try:
                from langchain_core.messages import HumanMessage
                prompt = (
                    f"You are a macro analyst covering the Egyptian market (EGX). "
                    f"Interpret these macro indicators for {ticker} as of {trade_date}:\n\n"
                    f"{json.dumps(structured_analysis, indent=2)}\n\n"
                    f"IMPORTANT: Egypt post-2016 operates in an 'inflation-hedge' regime where "
                    f"rising CPI is positively correlated with equity returns (World Scientific 2025). "
                    f"Do NOT automatically interpret high inflation as bearish for equities.\n\n"
                    f"Provide a 3-4 sentence interpretation of the macro environment and its "
                    f"implications for EGX equities. Focus on: rate trajectory, real yields, "
                    f"FX stability, and global risk appetite (VIX)."
                )
                response = quick_llm.invoke(
                    [HumanMessage(content=prompt)],
                    temperature=0,
                    seed=42,
                )
                llm_narrative = response.content if hasattr(response, "content") else str(response)
                report += f"\nMacro Interpretation:\n{llm_narrative}\n"
            except Exception as e:
                logger.warning("LLM narrative failed, using deterministic report only: %s", e)

        return {
            "macro_report": report,
            "macro_analysis": structured_analysis,
            "macro_messages": [],  # Deterministic — no tool calls
        }

    return macro_analyst_node
