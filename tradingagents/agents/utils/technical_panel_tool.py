"""LLM tool: full Investing-style technical panel for a ticker as of a date.

Wraps ``tradingagents.dataflows.technical_panel.get_live_panel`` so the Chartist LLM
can pull the complete panel (12 indicators + verdicts + SMA/EMA grid + Buy/Sell/Neutral
summaries + 5 pivot systems) in one call, instead of querying indicators one by one.
Look-ahead-safe: the underlying engine hard-filters to ``date <= curr_date``.
"""

from __future__ import annotations

import json
from typing import Annotated

from langchain_core.tools import tool


@tool
def get_technical_panel(
    symbol: Annotated[str, "EGX ticker, e.g. COMI.CA"],
    curr_date: Annotated[str, "As-of date, YYYY-MM-DD (the trading date you are on)"],
) -> str:
    """Return the full technical panel for a ticker as of a date.

    Includes RSI/STOCH/STOCHRSI/MACD/ADX/Williams%R/CCI/ATR/Highs-Lows/UltimateOsc/ROC/
    Bull-Bear Power values with Buy/Sell/Neutral verdicts, the SMA & EMA 5..200 grid,
    the indicator/MA/overall summary tallies (Strong Buy … Strong Sell), and the 5 pivot
    systems (Classic/Fibonacci/Camarilla/Woodie/DeMark). Computed deterministically from
    OHLCV — identical to the backtest dataset, look-ahead-safe.

    Returns a compact JSON string of the as-of-day panel.
    """
    # Clamp to the configured trade_date to prevent any look-ahead leakage.
    try:
        from tradingagents.dataflows.config import get_config
        _trade_date = get_config().get("trade_date")
        if _trade_date and curr_date > _trade_date:
            curr_date = _trade_date
    except Exception:
        pass

    from tradingagents.dataflows.technical_panel import get_live_panel

    res = get_live_panel(symbol, as_of=curr_date)
    if not res or not res.get("panel"):
        return json.dumps({"ticker": symbol, "as_of": curr_date,
                           "error": (res or {}).get("error", "no panel available")})

    panel = res["panel"]
    # Round floats for a compact, readable payload.
    compact = {
        k: (round(v, 4) if isinstance(v, float) else v)
        for k, v in panel.items()
    }
    return json.dumps({"ticker": res.get("ticker", symbol), "as_of": res.get("as_of"),
                       "bars": res.get("bars"), "panel": compact}, ensure_ascii=False)
