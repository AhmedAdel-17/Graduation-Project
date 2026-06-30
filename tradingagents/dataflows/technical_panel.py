"""Shared technical-panel engine — ONE source of truth for backtest AND live.

Reproduces the Investing.com 'Technical' panel (12 indicators + verdicts + SMA/EMA
grid + summary tallies + 5 pivot systems) as deterministic functions of OHLCV.

Used by:
  * scripts/build_technical_signals.py  -> historical dataset (data/egx30_signals/)
  * get_live_panel(...)                 -> on-demand panel for live runs / API / agents

Because both call the SAME functions here, a live panel and the backtest dataset for
the same ticker+date are guaranteed identical (no methodology drift).

Look-ahead safety: every indicator looks strictly backward. ``get_live_panel`` fetches
OHLCV only up to the as-of date, so the result is point-in-time correct for backtests
too. Pass ``as_of=None`` for "now" (live).

Verdict rules are documented in data/egx30_signals/README.md.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("tradingagents.dataflows.technical_panel")

MA_PERIODS = [5, 10, 20, 50, 100, 200]


# --------------------------------------------------------------------------- #
# Indicator primitives (Wilder smoothing where applicable)
# --------------------------------------------------------------------------- #
def _wilder(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    rs = _wilder(gain, period) / _wilder(loss, period).replace(0.0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def stoch(high, low, close, k=9, d=6):
    ll = low.rolling(k).min()
    hh = high.rolling(k).max()
    k_line = 100.0 * (close - ll) / (hh - ll).replace(0.0, np.nan)
    return k_line, k_line.rolling(d).mean()


def stoch_rsi(close, period=14):
    r = rsi(close, period)
    lo = r.rolling(period).min()
    hi = r.rolling(period).max()
    return 100.0 * (r - lo) / (hi - lo).replace(0.0, np.nan)


def macd(close, fast=12, slow=26, signal=9):
    line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig, line - sig


def true_range(high, low, close):
    prev = close.shift(1)
    return pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)


def atr(high, low, close, period=14):
    return _wilder(true_range(high, low, close), period)


def adx(high, low, close, period=14):
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = _wilder(true_range(high, low, close), period)
    plus_di = 100.0 * _wilder(pd.Series(plus_dm, index=high.index), period) / tr.replace(0.0, np.nan)
    minus_di = 100.0 * _wilder(pd.Series(minus_dm, index=high.index), period) / tr.replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return _wilder(dx, period), plus_di, minus_di


def williams_r(high, low, close, period=14):
    hh = high.rolling(period).max()
    ll = low.rolling(period).min()
    return -100.0 * (hh - close) / (hh - ll).replace(0.0, np.nan)


def cci(high, low, close, period=14):
    tp = (high + low + close) / 3.0
    sma = tp.rolling(period).mean()
    md = (tp - sma).abs().rolling(period).mean()
    return (tp - sma) / (0.015 * md.replace(0.0, np.nan))


def ultimate_osc(high, low, close, s=7, m=14, l=28):
    prev = close.shift(1)
    bp = close - pd.concat([low, prev], axis=1).min(axis=1)
    tr = pd.concat([high, prev], axis=1).max(axis=1) - pd.concat([low, prev], axis=1).min(axis=1)
    a1 = bp.rolling(s).sum() / tr.rolling(s).sum().replace(0.0, np.nan)
    a2 = bp.rolling(m).sum() / tr.rolling(m).sum().replace(0.0, np.nan)
    a3 = bp.rolling(l).sum() / tr.rolling(l).sum().replace(0.0, np.nan)
    return 100.0 * (4 * a1 + 2 * a2 + a3) / 7.0


def roc(close, period=12):
    return 100.0 * (close - close.shift(period)) / close.shift(period)


def bull_bear_power(high, low, close, period=13):
    ema = close.ewm(span=period, adjust=False).mean()
    return (high - ema), (low - ema)


# --------------------------------------------------------------------------- #
# Verdict helpers
# --------------------------------------------------------------------------- #
def _v(cond_buy, cond_sell):
    return np.where(cond_buy, "Buy", np.where(cond_sell, "Sell", "Neutral"))


def summary_label(score: float) -> str:
    if score is None or (isinstance(score, float) and np.isnan(score)):
        return "Neutral"
    if score >= 0.5:
        return "Strong Buy"
    if score >= 0.1:
        return "Buy"
    if score > -0.1:
        return "Neutral"
    if score > -0.5:
        return "Sell"
    return "Strong Sell"


# --------------------------------------------------------------------------- #
# Pivots (vectorised, from prior-day OHLC)
# --------------------------------------------------------------------------- #
def compute_pivots(df: pd.DataFrame) -> pd.DataFrame:
    H, L, C, O = (df["high"].shift(1), df["low"].shift(1),
                  df["close"].shift(1), df["open"].shift(1))
    cur_open = df["open"]
    rng = H - L
    out = pd.DataFrame(index=df.index)
    out["date"] = df["date"]

    p = (H + L + C) / 3.0
    out["classic_P"] = p
    out["classic_R1"] = 2 * p - L; out["classic_S1"] = 2 * p - H
    out["classic_R2"] = p + rng;   out["classic_S2"] = p - rng
    out["classic_R3"] = H + 2 * (p - L); out["classic_S3"] = L - 2 * (H - p)

    out["fib_P"] = p
    for f, lbl in [(0.382, "1"), (0.618, "2"), (1.0, "3")]:
        out[f"fib_R{lbl}"] = p + f * rng
        out[f"fib_S{lbl}"] = p - f * rng

    out["cam_P"] = p
    for k, lbl in [(1.1 / 12, "1"), (1.1 / 6, "2"), (1.1 / 4, "3"), (1.1 / 2, "4")]:
        out[f"cam_R{lbl}"] = C + rng * k
        out[f"cam_S{lbl}"] = C - rng * k

    wp = (H + L + 2 * cur_open) / 4.0
    out["woodie_P"] = wp
    out["woodie_R1"] = 2 * wp - L; out["woodie_S1"] = 2 * wp - H
    out["woodie_R2"] = wp + rng;   out["woodie_S2"] = wp - rng

    x = np.where(C < O, H + 2 * L + C,
         np.where(C > O, 2 * H + L + C, H + L + 2 * C))
    x = pd.Series(x, index=df.index)
    out["demark_P"] = x / 4.0
    out["demark_R1"] = x / 2.0 - L
    out["demark_S1"] = x / 2.0 - H
    return out


# --------------------------------------------------------------------------- #
# Full signal panel
# --------------------------------------------------------------------------- #
def compute_signals(df: pd.DataFrame) -> pd.DataFrame:
    high, low, close = df["high"], df["low"], df["close"]
    out = pd.DataFrame({"date": df["date"]})

    out["rsi_14"] = rsi(close)
    k_line, d_line = stoch(high, low, close, 9, 6)
    out["stoch_k_9_6"], out["stoch_d_9_6"] = k_line, d_line
    out["stochrsi_14"] = stoch_rsi(close)
    m_line, m_sig, m_hist = macd(close)
    out["macd"], out["macd_signal"], out["macd_hist"] = m_line, m_sig, m_hist
    adx_v, plus_di, minus_di = adx(high, low, close)
    out["adx_14"], out["plus_di_14"], out["minus_di_14"] = adx_v, plus_di, minus_di
    out["williams_r_14"] = williams_r(high, low, close)
    out["cci_14"] = cci(high, low, close)
    out["atr_14"] = atr(high, low, close)
    hh14, ll14 = high.rolling(14).max(), low.rolling(14).min()
    out["high_14"], out["low_14"] = hh14, ll14
    out["ultimate_osc"] = ultimate_osc(high, low, close)
    out["roc_12"] = roc(close)
    bull, bear = bull_bear_power(high, low, close)
    out["bull_power_13"], out["bear_power_13"] = bull, bear
    out["bull_bear_power_13"] = bull + bear

    out["rsi_signal"] = _v(out["rsi_14"] < 30, out["rsi_14"] > 70)
    out["stoch_signal"] = _v(out["stoch_k_9_6"] < 20, out["stoch_k_9_6"] > 80)
    out["stochrsi_signal"] = _v(out["stochrsi_14"] < 20, out["stochrsi_14"] > 80)
    out["macd_signal_verdict"] = _v(m_line > m_sig, m_line < m_sig)
    out["adx_signal"] = _v((plus_di > minus_di) & (adx_v > 20),
                           (minus_di > plus_di) & (adx_v > 20))
    out["williams_signal"] = _v(out["williams_r_14"] < -80, out["williams_r_14"] > -20)
    out["cci_signal"] = _v(out["cci_14"] > 100, out["cci_14"] < -100)
    out["ultimate_signal"] = _v(out["ultimate_osc"] < 30, out["ultimate_osc"] > 70)
    out["roc_signal"] = _v(out["roc_12"] > 0, out["roc_12"] < 0)
    out["highslows_signal"] = _v(close >= hh14, close <= ll14)
    out["bullbear_signal"] = _v(out["bull_bear_power_13"] > 0, out["bull_bear_power_13"] < 0)
    out["atr_signal"] = np.where(out["atr_14"] > out["atr_14"].rolling(14).mean(),
                                 "High Volatility", "Less Volatility")

    ind_cols = ["rsi_signal", "stoch_signal", "stochrsi_signal", "macd_signal_verdict",
                "adx_signal", "williams_signal", "cci_signal", "ultimate_signal",
                "roc_signal", "highslows_signal", "bullbear_signal"]

    ma_cols = []
    for n in MA_PERIODS:
        sma = close.rolling(n).mean()
        ema = close.ewm(span=n, adjust=False).mean()
        out[f"sma_{n}"], out[f"ema_{n}"] = sma, ema
        out[f"sma_{n}_signal"] = np.where(close > sma, "Buy", "Sell")
        out[f"ema_{n}_signal"] = np.where(close > ema, "Buy", "Sell")
        ma_cols += [f"sma_{n}_signal", f"ema_{n}_signal"]

    def counts(cols):
        buy = sum((out[c] == "Buy").astype(int) for c in cols)
        sell = sum((out[c] == "Sell").astype(int) for c in cols)
        neu = sum((out[c] == "Neutral").astype(int) for c in cols)
        return buy, sell, neu

    ib, isl, ino = counts(ind_cols)
    out["ind_buy"], out["ind_sell"], out["ind_neutral"] = ib, isl, ino
    out["ind_score"] = (ib - isl) / (ib + isl + ino).replace(0, np.nan)
    out["ind_summary"] = out["ind_score"].map(summary_label)

    mb, ms, _ = counts(ma_cols)
    out["ma_buy"], out["ma_sell"] = mb, ms
    out["ma_score"] = (mb - ms) / (mb + ms).replace(0, np.nan)
    out["ma_summary"] = out["ma_score"].map(summary_label)

    tot = (ib + isl + ino) + (mb + ms)
    out["overall_score"] = ((ib - isl) + (mb - ms)) / tot.replace(0, np.nan)
    out["overall_summary"] = out["overall_score"].map(summary_label)
    return out


# --------------------------------------------------------------------------- #
# Live / as-of entry points
# --------------------------------------------------------------------------- #
def latest_panel(df: pd.DataFrame) -> Dict[str, Any]:
    """Compute the panel and return ONLY the last row (values + verdicts + summaries
    + pivots) as a flat dict — convenient for live runs, the API, or an agent tool."""
    sig = compute_signals(df).iloc[-1].to_dict()
    piv = compute_pivots(df).iloc[-1].to_dict()
    piv.pop("date", None)
    # Include OHLCV from the last bar so downstream consumers (Risk Scorer,
    # Trader) can access the current price without a separate fetch.
    last_bar = df.iloc[-1]
    ohlcv = {
        "close": float(last_bar["close"]),
        "open": float(last_bar["open"]),
        "high": float(last_bar["high"]),
        "low": float(last_bar["low"]),
        "volume": float(last_bar["volume"]),
    }
    return {**ohlcv, **sig, **{f"pivot_{k}": v for k, v in piv.items()}}


def _fmt(v, nd=2):
    """Format a number for the panel text, tolerating None/NaN."""
    try:
        if v is None:
            return "n/a"
        f = float(v)
        if f != f:  # NaN
            return "n/a"
        return f"{f:.{nd}f}"
    except (TypeError, ValueError):
        return "n/a"


def format_panel_text(panel: Dict[str, Any], ticker: str = "", as_of: str = "", bars=None) -> str:
    """Render a flat panel dict (from ``latest_panel``) as a compact, LLM-readable
    block: overall/indicator/MA verdict tallies, the key oscillators with their
    Buy/Sell/Neutral verdicts, the full SMA/EMA 5..200 grid, and the pivot levels.

    Shared by the market-analyst nodes so the SAME technicals/MAs/pivots that go to
    the dashboard are also injected into the agents' reasoning context. Tolerant of
    missing keys (renders 'n/a').
    """
    if not isinstance(panel, dict) or not panel:
        return "Technical panel: unavailable."

    g = panel.get
    head = "TECHNICAL PANEL"
    if ticker:
        head += f" — {ticker}"
    if as_of:
        head += f" (as of {as_of}"
        head += f", {bars} bars)" if bars else ")"

    lines = [head + " — computed deterministically from OHLCV (look-ahead-safe)"]
    lines.append(
        f"Overall: {g('overall_summary','n/a')} | "
        f"Indicators: {g('ind_summary','n/a')} "
        f"(Buy {g('ind_buy',0)}/Sell {g('ind_sell',0)}/Neutral {g('ind_neutral',0)}) | "
        f"Moving Avgs: {g('ma_summary','n/a')} "
        f"(Buy {g('ma_buy',0)}/Sell {g('ma_sell',0)})"
    )
    lines.append(
        "Oscillators: "
        f"RSI(14)={_fmt(g('rsi_14'))} [{g('rsi_signal','n/a')}] | "
        f"StochRSI={_fmt(g('stochrsi_14'))} [{g('stochrsi_signal','n/a')}] | "
        f"MACD={_fmt(g('macd'),4)} vs sig {_fmt(g('macd_signal'),4)} [{g('macd_signal_verdict','n/a')}] | "
        f"ADX(14)={_fmt(g('adx_14'))} [{g('adx_signal','n/a')}] | "
        f"CCI(14)={_fmt(g('cci_14'))} [{g('cci_signal','n/a')}] | "
        f"Williams%R={_fmt(g('williams_r_14'))} [{g('williams_signal','n/a')}] | "
        f"ROC(12)={_fmt(g('roc_12'))} [{g('roc_signal','n/a')}] | "
        f"ATR(14)={_fmt(g('atr_14'))} [{g('atr_signal','n/a')}]"
    )
    sma = " ".join(f"{n}={_fmt(g(f'sma_{n}'))}" for n in MA_PERIODS)
    ema = " ".join(f"{n}={_fmt(g(f'ema_{n}'))}" for n in MA_PERIODS)
    lines.append(f"Moving averages — SMA: {sma}")
    lines.append(f"Moving averages — EMA: {ema}")
    # Pivots (prefixed pivot_<system>_<level> in latest_panel)
    lines.append(
        "Pivots (next session) — Classic: "
        f"S2={_fmt(g('pivot_classic_S2'))} S1={_fmt(g('pivot_classic_S1'))} "
        f"P={_fmt(g('pivot_classic_P'))} R1={_fmt(g('pivot_classic_R1'))} R2={_fmt(g('pivot_classic_R2'))}"
    )
    lines.append(
        "Pivots — Fibonacci: "
        f"S1={_fmt(g('pivot_fib_S1'))} P={_fmt(g('pivot_fib_P'))} R1={_fmt(g('pivot_fib_R1'))} | "
        f"Camarilla: S1={_fmt(g('pivot_cam_S1'))} R1={_fmt(g('pivot_cam_R1'))} | "
        f"Woodie: S1={_fmt(g('pivot_woodie_S1'))} P={_fmt(g('pivot_woodie_P'))} R1={_fmt(g('pivot_woodie_R1'))} | "
        f"DeMark: S1={_fmt(g('pivot_demark_S1'))} P={_fmt(g('pivot_demark_P'))} R1={_fmt(g('pivot_demark_R1'))}"
    )
    return "\n".join(lines)


def _fetch_panel_ohlcv(ticker: str, start: str, fetch_end: str, as_of: str):
    """Resilient FULL-HISTORY OHLCV fetch for the technical panel.

    The panel needs deep history to warm up EMA/Wilder indicators, so it cannot
    reuse the Quick path's ~30-day window — it does its own long fetch. Previously
    that fetch was yfinance-only, so the panel was EMPTY for yfinance-dead names
    (QNBA) and silently STALE for half-listed ones (ORAS returned 2021 bars).

    Tries multiple LIVE sources with ``max_records=None`` (full history) and picks
    the FRESHEST sufficient dataset (≥30 bars after the ``<= as_of`` look-ahead
    filter); ties within 3 days are broken toward the deepest history (best
    indicator warm-up). Live-only chain (yfinance → TradingView → EODHD) — no
    stale local CSV on this live path, matching the rest of the live design.

    Returns ``(source, rows_sorted_asc)`` or ``(None, [])``.
    """
    candidates = []  # (source, rows)

    try:
        from .y_finance import get_YFin_data_online
        r = get_YFin_data_online(ticker, start, fetch_end, max_records=None)
        candidates.append(("yfinance", r.get("data") or []))
    except Exception as e:
        logger.debug("panel yfinance fetch failed for %s: %s", ticker, e)
    try:
        from .tradingview_provider import get_tradingview_ohlcv
        r = get_tradingview_ohlcv(ticker, start, fetch_end, max_records=None)
        candidates.append(("tradingview", r.get("data") or []))
    except Exception as e:
        logger.debug("panel tradingview fetch failed for %s: %s", ticker, e)
    try:
        from .eodhd import get_stock_data_eodhd
        r = get_stock_data_eodhd(ticker, start, fetch_end)
        candidates.append(("eodhd", r.get("data") or []))
    except Exception as e:
        logger.debug("panel eodhd fetch failed for %s: %s", ticker, e)

    # Filter each candidate to <= as_of (look-ahead guard) and keep sufficient ones.
    usable = []  # (source, rows_sorted, last_date)
    for source, rows in candidates:
        rows = [x for x in rows if (x.get("date") or "") <= as_of]
        if len(rows) < 30:
            continue
        rows = sorted(rows, key=lambda x: x["date"])
        usable.append((source, rows, rows[-1]["date"]))

    if not usable:
        return None, []

    # Freshest last bar wins; among those within 3 days of it, prefer deepest history.
    freshest = max(u[2] for u in usable)
    fresh_dt = datetime.strptime(freshest, "%Y-%m-%d")
    near = [
        u for u in usable
        if (fresh_dt - datetime.strptime(u[2], "%Y-%m-%d")).days <= 3
    ]
    best = max(near, key=lambda u: len(u[1]))
    return best[0], best[1]


def get_live_panel(
    ticker: str,
    as_of: Optional[str] = None,
    lookback_days: int = 2000,
) -> Dict[str, Any]:
    """On-demand technical panel for a single ticker, look-ahead-safe.

    Fetches daily OHLCV from the project's yfinance provider for the window
    [as_of - lookback_days, as_of] and returns ``latest_panel`` for the as-of day,
    using the SAME code as the backtest dataset.

    Parity: non-recursive indicators (SMA, RSI numerator, CCI, STOCH, Williams, ROC,
    pivots) match the full-history dataset exactly; recursive ones (EMA, MACD, Wilder
    RSI/ADX/ATR) match to ~1e-5 once warmed up — hence the generous ``lookback_days``
    default (~5.5 yr) so the EMA/Wilder seed influence is negligible.

    Look-ahead: the fetch window ends at as_of (the underlying ``end`` is bumped by one
    day to defeat yfinance's exclusive end-date), then rows are hard-filtered to
    ``date <= as_of`` so no future bar can leak in.

    Args:
        ticker: EGX ticker, e.g. "COMI.CA".
        as_of:  "YYYY-MM-DD". Defaults to today (live).
        lookback_days: calendar days of history to fetch.

    Returns:
        {"ticker", "as_of", "bars", "panel": {...}}  or  {"error": ...}
    """
    as_of = as_of or date.today().isoformat()
    start = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    # Providers treat `end` as exclusive -> bump by 1 day so the as_of bar is included.
    fetch_end = (datetime.strptime(as_of, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

    # Resilient multi-source fetch (yfinance -> TradingView -> EODHD), freshest
    # sufficient dataset wins. Replaces the old yfinance-only fetch that left the
    # panel empty for QNBA and stale (2021) for ORAS.
    source, rows = _fetch_panel_ohlcv(ticker, start, fetch_end, as_of)
    if not rows:
        return {"ticker": ticker, "as_of": as_of,
                "error": f"no usable OHLCV for '{ticker}' (tried yfinance, tradingview, eodhd)",
                "panel": None}

    df = pd.DataFrame(rows)[["date", "open", "high", "low", "close", "volume"]]
    df = df[df["date"] <= as_of].reset_index(drop=True)  # hard look-ahead guard (belt-and-braces)
    if len(df) < 30:
        return {"ticker": ticker, "as_of": as_of, "error": f"insufficient history ({len(df)} bars)", "panel": None}

    return {"ticker": ticker, "as_of": df["date"].iloc[-1], "bars": len(df),
            "panel": latest_panel(df)}
