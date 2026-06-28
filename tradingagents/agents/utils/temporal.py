"""Point-in-time (as-of-date) information boundary for LLM agents.

The single biggest threat to backtest credibility in this system is NOT code-level
look-ahead (that is handled in ``scripts/backtester.py``) — it is the LLM's own
training-data knowledge of what happened *after* the simulated trade date. An LLM
asked to predict COMI.CA's direction on 2023-06-01 may simply recall how the stock
actually moved through 2023–2024.

There is no way to truly erase a model's parametric memory, but a strong, explicit
boundary instruction measurably reduces the leak and — just as importantly — makes
the constraint auditable: every forward-reasoning prompt now declares the
information cutoff it is supposed to respect.

Inject ``point_in_time_notice(trade_date)`` at the top of any prompt where the agent
forms a forward-looking view (analysts, researchers, CIO, trader, risk manager).
"""
from __future__ import annotations

from typing import Optional


def point_in_time_notice(as_of_date: Optional[str]) -> str:
    """Return a standardized as-of-date boundary block for an LLM prompt.

    Args:
        as_of_date: The simulated "today" (ISO ``YYYY-MM-DD``). When falsy, a
            generic boundary is returned so the instruction is never silently
            dropped.

    The text is identical across agents on purpose: a consistent, recognizable
    instruction is easier for the model to honor and for an auditor to grep.
    """
    when = (as_of_date or "the stated analysis date").strip() or "the stated analysis date"
    return (
        f"## ⏱ POINT-IN-TIME INFORMATION BOUNDARY (as of {when})\n"
        f"Reason AS IF today is {when}. You may use ONLY information that was "
        f"available on or before {when}.\n"
        f"- Do NOT use any knowledge of prices, earnings, news, events, or outcomes "
        f"that occurred after {when}.\n"
        f"- Do NOT draw on training-data memory of what later happened to this "
        f"company, sector, or market after {when}.\n"
        f"- If you are unsure whether a fact was known by {when}, treat it as UNKNOWN.\n"
        f"- Base your view only on the point-in-time reports and data provided below.\n"
        f"Using post-cutoff information is look-ahead bias and invalidates the analysis.\n"
    )
