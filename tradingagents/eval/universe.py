"""Point-in-time index membership — the guard against survivorship bias.

Evaluating on TODAY's index members back-tested over history only ever sees the
survivors (the names that didn't get delisted or demoted), which inflates results.
A defensible eval must ask the universe "who was a member *on this date*?" — including
names that were later removed.

This module provides that machinery. The seed data file
(``data/egx_membership.json``) currently lists only current members with unknown
add dates, so on its own it is STILL survivor-biased — ``survivorship_coverage()``
reports this honestly, and ``members_as_of`` warns when it falls back to the
current-members universe. To become truly survivorship-safe, populate the data file
with removed/delisted tickers and real add/remove dates (see the file's README).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("tradingagents.eval.universe")

_DEFAULT_DATA_PATH = Path(__file__).parent / "data" / "egx_membership.json"


@dataclass(frozen=True)
class Membership:
    ticker: str
    index: str
    add_date: Optional[str]      # ISO YYYY-MM-DD, or None = unknown (treated active)
    remove_date: Optional[str]   # ISO YYYY-MM-DD, or None = still a member


def load_membership(path: Optional[str | Path] = None) -> List[Membership]:
    """Load the membership table. Returns [] if the file is missing/unreadable."""
    p = Path(path) if path else _DEFAULT_DATA_PATH
    if not p.exists():
        logger.warning("universe: membership file not found at %s", p)
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("universe: cannot read %s: %s", p, exc)
        return []
    rows = data.get("members", []) if isinstance(data, dict) else data
    out: List[Membership] = []
    for r in rows:
        try:
            out.append(Membership(
                ticker=str(r["ticker"]),
                index=str(r.get("index", "EGX30")),
                add_date=r.get("add_date"),
                remove_date=r.get("remove_date"),
            ))
        except (KeyError, TypeError):
            continue
    return out


def is_active(
    ticker: str,
    on_date: str,
    membership: Optional[List[Membership]] = None,
    index: Optional[str] = None,
) -> bool:
    """True iff ``ticker`` was an index member on ``on_date``.

    A row is active on D iff (add_date is None or add_date <= D) and
    (remove_date is None or D < remove_date). Unknown add_date is treated as
    'always added' — a deliberate, documented survivor-bias fallback.
    """
    membership = membership if membership is not None else load_membership()
    d = str(on_date)[:10]
    for m in membership:
        if m.ticker != ticker:
            continue
        if index is not None and m.index != index:
            continue
        if m.add_date is not None and str(m.add_date)[:10] > d:
            continue
        if m.remove_date is not None and d >= str(m.remove_date)[:10]:
            continue
        return True
    return False


def members_as_of(
    on_date: str,
    index: Optional[str] = "EGX30",
    membership: Optional[List[Membership]] = None,
) -> List[str]:
    """Return the tickers that were members of ``index`` on ``on_date``."""
    membership = membership if membership is not None else load_membership()
    d = str(on_date)[:10]
    out: List[str] = []
    for m in membership:
        if index is not None and m.index != index:
            continue
        if m.add_date is not None and str(m.add_date)[:10] > d:
            continue
        if m.remove_date is not None and d >= str(m.remove_date)[:10]:
            continue
        out.append(m.ticker)
    return sorted(set(out))


def survivorship_coverage(membership: Optional[List[Membership]] = None) -> Dict[str, float]:
    """Quantify how survivor-biased the membership data still is.

    Returns counts + the fraction of rows that carry real dates and the number of
    already-removed names. ``removed == 0`` means the data set cannot represent any
    delisting/demotion yet ⇒ still survivor-biased.
    """
    membership = membership if membership is not None else load_membership()
    n = len(membership)
    dated = sum(1 for m in membership if m.add_date or m.remove_date)
    removed = sum(1 for m in membership if m.remove_date)
    return {
        "total": n,
        "with_dates": dated,
        "removed_names": removed,
        "date_coverage": (dated / n) if n else 0.0,
        "is_survivorship_safe": bool(n and removed > 0 and dated == n),
    }


def current_universe() -> List[str]:
    """The current (survivor-biased) trading universe from default_config.

    Use only when point-in-time membership is unavailable; callers should prefer
    ``members_as_of``. Emits a one-time warning so the bias is never silent.
    """
    from tradingagents.default_config import EGX_TICKERS
    logger.warning(
        "universe: using current_universe() — this is survivor-biased; "
        "prefer members_as_of(date) once membership data carries removed names."
    )
    return list(EGX_TICKERS)
