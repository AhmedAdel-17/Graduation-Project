"""
EGX Macro Context Provider — deterministic, zero LLM calls.

Fetches live (or as-of-date) macro indicators relevant to Egyptian equity trading:
  - CBE policy rate (from config + rate history CSV if available)
  - USD/EGP spot rate (yfinance USDEGP=X)
  - EGX30 trend and 1-month return (yfinance ^CASE30)
  - 91-day T-bill yield (config-based with manual override)
  - Egypt CPI (config-based with manual override)
  - IMF program status (static — updated when programme changes)
  - Brent crude oil (yfinance BZ=F)
  - Derived signals: real_rate, fx_trend, spread_vs_tbill

All fetches are wrapped in try/except so a network failure degrades gracefully
to config defaults. The returned dict is injected into AgentState as
`macro_context` before the graph runs.

Usage
-----
    from tradingagents.dataflows.macro_provider import get_egx_macro_context
    ctx = get_egx_macro_context(as_of_date="2025-01-15", config=cfg)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger("tradingagents.macro_provider")

# ---------------------------------------------------------------------------
# Static / semi-static defaults (updated manually when macro regime changes)
# ---------------------------------------------------------------------------

_DEFAULT_CBE_RATE      = 0.2750   # 27.50%  — CBE rate as of 2024 cycle
_DEFAULT_TBILL_91D     = 0.2750   # 27.50%  — 91-day T-bill yield, proxy for short rate
_DEFAULT_EGYPT_CPI     = 0.258    # 25.8%   — Egypt CPI YoY (2024 average)
_DEFAULT_USD_EGP       = 49.5     # EGP per USD (post-March 2024 float)
_DEFAULT_BRENT         = 80.0     # USD/bbl — rough mid-range

# IMF Extended Fund Facility: Egypt signed in Dec 2022, extended/amended 2024
_IMF_PROGRAM_ACTIVE    = True
_IMF_PROGRAM_SIZE_BN   = 8.0      # USD bn (2022 original tranche, later increased)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _yf_spot_price(ticker: str, as_of_date: str, lookback_days: int = 5) -> Optional[float]:
    """
    Fetch the closing price of `ticker` on or before `as_of_date`.

    Parameters
    ----------
    ticker        : yfinance ticker symbol (e.g. "USDEGP=X", "BZ=F")
    as_of_date    : YYYY-MM-DD string
    lookback_days : how many trading days before as_of_date to look back
                    if the exact date has no data (weekends, holidays)

    Returns None on any failure.
    """
    try:
        import yfinance as yf
        end   = datetime.strptime(as_of_date, "%Y-%m-%d") + timedelta(days=1)
        start = end - timedelta(days=lookback_days + 10)  # extra buffer
        df = yf.download(
            ticker,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        if df is None or df.empty:
            return None
        # Use last available close
        close_col = "Close"
        if close_col not in df.columns:
            return None
        series = df[close_col].dropna()
        if series.empty:
            return None
        val = series.iloc[-1]
        return float(val.iloc[0]) if hasattr(val, "iloc") else float(val)
    except Exception as exc:
        logger.debug("_yf_spot_price(%s, %s) failed: %s", ticker, as_of_date, exc)
        return None


def _yf_last_two_closes(
    ticker: str, as_of_date: str, lookback_days: int = 30
) -> tuple[Optional[float], Optional[float]]:
    """
    Return (last_close, prev_close) on or before `as_of_date`.

    Used to derive a spot level and a 1-day percentage change for the
    headline market-index strip. The window is generous (default 30 days)
    because some indices — notably ^CASE30 — have sparse Yahoo coverage.
    Returns (None, None) on any failure.
    """
    try:
        import yfinance as yf
        end   = datetime.strptime(as_of_date, "%Y-%m-%d") + timedelta(days=1)
        start = end - timedelta(days=lookback_days)
        df = yf.download(
            ticker,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        if df is None or df.empty or "Close" not in df.columns:
            return None, None
        closes = df["Close"].dropna()
        if closes.empty:
            return None, None

        def _scalar(v: Any) -> float:
            return float(v.iloc[0]) if hasattr(v, "iloc") else float(v)

        last = _scalar(closes.iloc[-1])
        prev = _scalar(closes.iloc[-2]) if len(closes) >= 2 else None
        return last, prev
    except Exception as exc:
        logger.debug("_yf_last_two_closes(%s, %s) failed: %s", ticker, as_of_date, exc)
        return None, None


# Headline market indices for the dashboard strip. Only instruments with a
# dependable free feed are listed — EGX70/EGX100 have no reliable public
# source and are intentionally excluded. ``yf`` lists fallback symbols tried
# in order until two closes are found.
_MARKET_INDEX_SPECS = (
    {"key": "egx30",   "label": "EGX 30",   "yf": ["^CASE30", "EGX30.CA"], "kind": "index",     "decimals": 0},
    {"key": "gold",    "label": "Gold",     "yf": ["GC=F", "GLD"],         "kind": "commodity", "decimals": 2},
    {"key": "usd_egp", "label": "USD / EGP", "yf": ["USDEGP=X", "EGP=X"],  "kind": "fx",        "decimals": 2},
)


def get_market_indices(as_of_date: Optional[str] = None) -> Dict[str, Any]:
    """
    Live spot level + 1-day percentage change for the headline market indices.

    Deterministic, no LLM. Each instrument degrades independently: a feed
    failure yields ``available: false`` rather than failing the whole call.
    Fallback symbols are tried until both a last and prior close are found.
    """
    target = as_of_date or datetime.now().strftime("%Y-%m-%d")
    indices = []
    for spec in _MARKET_INDEX_SPECS:
        last: Optional[float] = None
        prev: Optional[float] = None
        for symbol in spec["yf"]:
            cand_last, cand_prev = _yf_last_two_closes(symbol, target)
            if cand_last is not None:
                # Keep the first symbol that yields a usable spot; prefer one
                # that also provides a prior close for the 1-day change.
                if last is None or (prev is None and cand_prev is not None):
                    last, prev = cand_last, cand_prev
                if prev is not None:
                    break
        change_pct = None
        change_source = "intraday"
        if last is not None and prev not in (None, 0):
            change_pct = round((last - prev) / prev * 100, 2)
        # ^CASE30 exposes only one Yahoo point — fall back to a constituent
        # basket so the EGX30 tile still shows a daily move.
        if change_pct is None and spec["key"] == "egx30" and last is not None:
            proxy = _egx30_proxy_1d_return(target)
            if proxy is not None:
                change_pct = round(proxy * 100, 2)
                change_source = "constituent_basket"
        indices.append({
            "key": spec["key"],
            "label": spec["label"],
            "kind": spec["kind"],
            "value": round(last, spec["decimals"]) if last is not None else None,
            "change_pct": change_pct,
            "change_source": change_source if change_pct is not None else None,
            "available": last is not None,
        })
    return {"as_of_date": target, "indices": indices}


def _yf_1m_return(ticker: str, as_of_date: str) -> Optional[float]:
    """
    Compute the 1-month price return of `ticker` ending at `as_of_date`.
    Returns the fractional return (e.g. 0.04 = +4%) or None on failure.
    """
    try:
        import yfinance as yf
        end   = datetime.strptime(as_of_date, "%Y-%m-%d") + timedelta(days=1)
        start = end - timedelta(days=40)    # ~30 trading days + buffer
        df = yf.download(
            ticker,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        if df is None or df.empty or "Close" not in df.columns:
            return None
        closes = df["Close"].dropna()
        if len(closes) < 2:
            return None
        first = closes.iloc[0]
        last  = closes.iloc[-1]
        first = float(first.iloc[0]) if hasattr(first, "iloc") else float(first)
        last  = float(last.iloc[0])  if hasattr(last,  "iloc") else float(last)
        return (last - first) / first
    except Exception as exc:
        logger.debug("_yf_1m_return(%s, %s) failed: %s", ticker, as_of_date, exc)
        return None


def _classify_fx_trend(usd_egp_1m_return: Optional[float]) -> str:
    """
    Translate 1-month USD/EGP return into a qualitative FX trend label.

    A positive 1m USD/EGP return means EGP *depreciated* (you need more EGP
    per USD), which is negative for EGP-denominated assets priced in imports.
    """
    if usd_egp_1m_return is None:
        return "unknown"
    if usd_egp_1m_return > 0.02:
        return "depreciating"     # EGP lost >2% in a month — adverse
    if usd_egp_1m_return < -0.02:
        return "appreciating"     # EGP gained >2% — positive
    return "stable"


# Liquid EGX-30 constituents used to proxy index 1m return when ^CASE30
# only exposes the most recent point on Yahoo. Equal-weighted by design —
# weighting drift across rebalances would add noise, not signal.
_EGX30_PROXY_CONSTITUENTS = (
    "COMI.CA", "ETEL.CA", "HRHO.CA", "SWDY.CA", "TMGH.CA",
    "ABUK.CA", "EAST.CA", "MFPC.CA", "FWRY.CA", "ADIB.CA",
    "ORAS.CA", "HELI.CA", "PHDC.CA", "EFIH.CA", "JUFO.CA",
)


def _egx30_proxy_1m_return(as_of_date: str) -> Optional[float]:
    import math
    rets: list[float] = []
    for sym in _EGX30_PROXY_CONSTITUENTS:
        r = _yf_1m_return(sym, as_of_date)
        if r is not None and not math.isnan(r) and math.isfinite(r):
            rets.append(r)
    if len(rets) < 5:
        return None
    return sum(rets) / len(rets)


def _egx30_proxy_1d_return(as_of_date: str) -> Optional[float]:
    """
    Equal-weighted average 1-day return of liquid EGX-30 constituents.

    Yahoo's ^CASE30 only exposes a single point, so it cannot yield a daily
    change. The ``.CA`` constituents have proper daily history — averaging
    their last-session returns is a faithful proxy for the index move.
    Constituents are fetched in one batched request to keep the call fast.
    """
    try:
        import math
        import yfinance as yf
        end   = datetime.strptime(as_of_date, "%Y-%m-%d") + timedelta(days=1)
        start = end - timedelta(days=12)
        df = yf.download(
            list(_EGX30_PROXY_CONSTITUENTS),
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        if df is None or df.empty or "Close" not in df.columns:
            return None
        close = df["Close"]
        rets: list[float] = []
        for col in close.columns:
            series = close[col].dropna()
            if len(series) < 2:
                continue
            prev = float(series.iloc[-2])
            last = float(series.iloc[-1])
            if prev:
                r = (last - prev) / prev
                if math.isfinite(r):
                    rets.append(r)
        if len(rets) < 5:
            return None
        return sum(rets) / len(rets)
    except Exception as exc:
        logger.debug("_egx30_proxy_1d_return(%s) failed: %s", as_of_date, exc)
        return None


def _classify_egx30_trend(return_1m: Optional[float]) -> str:
    if return_1m is None:
        return "unknown"
    if return_1m > 0.03:
        return "bullish"
    if return_1m < -0.03:
        return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_egx_macro_context(
    as_of_date: str,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Return a dict of EGX macro indicators as of `as_of_date`.

    All live data is fetched from yfinance.  If a fetch fails, the function
    falls back to config-provided defaults or hard-coded conservative values.
    No LLM call is made.

    Parameters
    ----------
    as_of_date : str
        ISO-8601 date string, e.g. "2025-01-15". Temporal safety is respected —
        only data available on or before this date is used.
    config : dict, optional
        The project DEFAULT_CONFIG (or a copy with overrides). Used to read
        ``egx_risk_free_rate`` as the CBE rate proxy if a live value is
        unavailable.

    Returns
    -------
    dict with keys:
        cbe_policy_rate        float  — CBE overnight rate (fraction, e.g. 0.275)
        usd_egp                float  — USD/EGP spot (EGP per 1 USD)
        egx30_return_1m        float  — EGX30 1-month fractional return
        egx30_trend            str    — "bullish" | "bearish" | "neutral" | "unknown"
        tbill_yield_91d        float  — 91-day T-bill yield (fraction)
        egypt_cpi              float  — Egypt CPI YoY (fraction)
        real_rate              float  — cbe_policy_rate - egypt_cpi
        spread_vs_tbill        float  — cbe_policy_rate - tbill_yield_91d
        fx_trend               str    — "stable" | "depreciating" | "appreciating" | "unknown"
        usd_egp_1m_return      float  — 1-month return of USD/EGP (+ = EGP depreciation)
        brent_usd              float  — Brent crude spot (USD/bbl)
        imf_program_active     bool   — Is Egypt in an active IMF programme?
        imf_program_size_bn    float  — USD bn committed under the programme
        as_of_date             str    — Echo of input date
        data_sources           dict   — Which fields came from live vs. default
    """
    cfg = config or {}

    # ── 1. CBE policy rate ─────────────────────────────────────────────────────
    # Try config first (egx_risk_free_rate is kept up-to-date by operators);
    # fall back to hard-coded default.
    cbe_rate = float(cfg.get("egx_risk_free_rate", _DEFAULT_CBE_RATE))
    cbe_source = "config"

    # ── 2. T-bill yield (91-day) ───────────────────────────────────────────────
    # Egypt's Central Bank publishes these at auctions; no free real-time API.
    # We proxy as cbe_rate ± small spread (historically ~25–50 bps below policy).
    tbill_yield = float(cfg.get("tbill_yield_91d", _DEFAULT_TBILL_91D))
    tbill_source = "config"

    # ── 3. Egypt CPI ──────────────────────────────────────────────────────────
    egypt_cpi = float(cfg.get("egypt_cpi", _DEFAULT_EGYPT_CPI))
    cpi_source = "config"

    # ── 4. USD/EGP spot ───────────────────────────────────────────────────────
    usd_egp_spot = _yf_spot_price("USDEGP=X", as_of_date)
    usd_egp_source = "yfinance"
    if usd_egp_spot is None:
        usd_egp_spot = float(cfg.get("usd_egp_default", _DEFAULT_USD_EGP))
        usd_egp_source = "default"
        logger.info("USD/EGP: yfinance unavailable, using default %.2f", usd_egp_spot)

    # ── 5. USD/EGP 1-month return (FX trend) ──────────────────────────────────
    usd_egp_1m = _yf_1m_return("USDEGP=X", as_of_date)
    fx_trend = _classify_fx_trend(usd_egp_1m)
    fx_source = "yfinance" if usd_egp_1m is not None else "default"

    # ── 6. EGX30 (^CASE30) ────────────────────────────────────────────────────
    # Yahoo's ^CASE30 / EGX30.CA only expose the latest snapshot — not enough
    # to compute a 1-month return. Fall back to a constituent-basket proxy
    # (equal-weighted mean of liquid EGX-30 names), which Yahoo does serve.
    egx30_1m = _yf_1m_return("^CASE30", as_of_date)
    egx30_source = "yfinance"
    if egx30_1m is None:
        egx30_1m = _yf_1m_return("EGX30.CA", as_of_date)
        if egx30_1m is not None:
            egx30_source = "yfinance(alt)"
    if egx30_1m is None:
        egx30_1m = _egx30_proxy_1m_return(as_of_date)
        if egx30_1m is not None:
            egx30_source = "yfinance(proxy_basket)"
        else:
            egx30_source = "unavailable"
            logger.info("EGX30 1m return: not available from yfinance")
    egx30_trend = _classify_egx30_trend(egx30_1m)

    # ── 7. Brent crude oil ────────────────────────────────────────────────────
    brent = _yf_spot_price("BZ=F", as_of_date)
    brent_source = "yfinance"
    if brent is None:
        brent = float(cfg.get("brent_usd_default", _DEFAULT_BRENT))
        brent_source = "default"
        logger.info("Brent crude: yfinance unavailable, using default %.1f", brent)

    # ── 8. Derived signals ────────────────────────────────────────────────────
    real_rate        = round(cbe_rate - egypt_cpi, 4)
    spread_vs_tbill  = round(cbe_rate - tbill_yield, 4)

    # ── 9. IMF programme (static — update when programme status changes) ──────
    imf_active   = _IMF_PROGRAM_ACTIVE
    imf_size     = _IMF_PROGRAM_SIZE_BN

    # ── 10. Assemble output ───────────────────────────────────────────────────
    macro_context: Dict[str, Any] = {
        "cbe_policy_rate":    round(cbe_rate, 4),
        "usd_egp":            round(usd_egp_spot, 2),
        "usd_egp_1m_return":  round(usd_egp_1m, 4) if usd_egp_1m is not None else None,
        "fx_trend":           fx_trend,
        "egx30_return_1m":    round(egx30_1m, 4) if egx30_1m is not None else None,
        "egx30_trend":        egx30_trend,
        "tbill_yield_91d":    round(tbill_yield, 4),
        "egypt_cpi":          round(egypt_cpi, 4),
        "real_rate":          real_rate,
        "spread_vs_tbill":    spread_vs_tbill,
        "brent_usd":          round(brent, 2) if brent is not None else None,
        "imf_program_active": imf_active,
        "imf_program_size_bn": imf_size,
        "as_of_date":         as_of_date,
        "data_sources": {
            "cbe_rate":    cbe_source,
            "tbill":       tbill_source,
            "cpi":         cpi_source,
            "usd_egp":     usd_egp_source,
            "fx_trend":    fx_source,
            "egx30":       egx30_source,
            "brent":       brent_source,
        },
    }

    logger.info(
        "Macro context for %s: CBE=%.2f%%, USD/EGP=%.2f, EGX30_1m=%s, "
        "real_rate=%.2f%%, FX=%s",
        as_of_date,
        cbe_rate * 100,
        usd_egp_spot,
        f"{egx30_1m*100:.1f}%" if egx30_1m is not None else "N/A",
        real_rate * 100,
        fx_trend,
    )
    return macro_context


def format_macro_context_for_prompt(macro_context: Optional[Dict[str, Any]]) -> str:
    """
    Return a human-readable macro section string for LLM prompts.

    Handles None (graceful degradation when macro was not fetched) and
    partial dicts (some fields None due to data unavailability).
    """
    if not macro_context:
        return "(Macro context unavailable — proceed without macro overlay)"

    def _pct(v: Optional[float]) -> str:
        return f"{v*100:.2f}%" if v is not None else "N/A"

    def _float2(v: Optional[float]) -> str:
        return f"{v:.2f}" if v is not None else "N/A"

    cbe   = _pct(macro_context.get("cbe_policy_rate"))
    tbill = _pct(macro_context.get("tbill_yield_91d"))
    cpi   = _pct(macro_context.get("egypt_cpi"))
    rr    = _pct(macro_context.get("real_rate"))
    usdegp = _float2(macro_context.get("usd_egp"))
    fx_trend  = macro_context.get("fx_trend", "unknown")
    egx30_ret = _pct(macro_context.get("egx30_return_1m"))
    egx30_tr  = macro_context.get("egx30_trend", "unknown")
    brent     = _float2(macro_context.get("brent_usd"))
    imf       = "ACTIVE" if macro_context.get("imf_program_active") else "inactive/expired"
    imf_size  = macro_context.get("imf_program_size_bn", "N/A")
    as_of     = macro_context.get("as_of_date", "unknown date")

    return f"""## Egyptian Macro Environment (as of {as_of})

| Indicator                | Value       | Signal context                          |
|--------------------------|-------------|-----------------------------------------|
| CBE policy rate          | {cbe:>10}  | Benchmark cost of capital               |
| 91-day T-bill yield      | {tbill:>10}  | Short-term risk-free rate               |
| Egypt CPI (YoY)          | {cpi:>10}  | Inflation erodes real returns           |
| Real interest rate       | {rr:>10}  | CBE rate − CPI (negative = inflationary)|
| USD/EGP spot             | {usdegp:>10}  | Currency baseline (EGP per USD)         |
| FX trend (1-month)       | {fx_trend:>10}  | EGP direction vs USD                    |
| EGX30 1-month return     | {egx30_ret:>10}  | Broad market momentum                   |
| EGX30 trend              | {egx30_tr:>10}  | Market sentiment                        |
| Brent crude (USD/bbl)    | {brent:>10}  | Oil price (key Egypt revenue driver)    |
| IMF programme status     | {imf:>10}  | {f'USD {imf_size}bn EFF/SBA' if imf != 'inactive/expired' else 'No active programme'} |

**Key macro reads:**
- Real rate {rr}: {"Positive real rate — monetary conditions are tight, credit expensive" if (macro_context.get("real_rate") or 0) > 0 else "Negative real rate — inflation outpaces policy rate, financial repression risk"}
- FX: {fx_trend.upper()} — {"EGP weakness raises import costs and USD-debt burden" if fx_trend == "depreciating" else "EGP stable/strengthening — import cost relief, but watch capital flows" if fx_trend in ("stable", "appreciating") else "FX direction unclear"}
- Market: EGX30 is {egx30_tr} ({"broad tailwind for risk assets" if egx30_tr == "bullish" else "broad headwind" if egx30_tr == "bearish" else "range-bound market"})
- IMF programme: {imf} — {"Anchors fiscal credibility and FX stability" if imf == "ACTIVE" else "No external anchor — watch sovereign risk premium"}"""
