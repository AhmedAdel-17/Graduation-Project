"""Centralized 6-sector EGX taxonomy.

Single source of truth for sentiment, fundamentals, scoring, and dashboard.
The 6-sector view is canonical going forward; the legacy 4-sector fundamentals
classifier (`agents/analysts/fundamentals/sector_config.py`) is kept working
via `to_fundamentals_sector()` so its 79 unit tests stay green.

Sector membership matches CLAUDE.md §10 (EGX_TICKERS sector grouping).
Migrating fundamentals to consume this module directly is a follow-up PR.
"""
from __future__ import annotations

import os
from enum import Enum
from typing import Iterable

from tradingagents.default_config import EGX_TICKERS


class SectorEnum(str, Enum):
    BANKS = "banks"
    REAL_ESTATE = "real_estate"
    INDUSTRY = "industry"
    TELECOM_TECH = "telecom_tech"
    FINANCIAL_SERVICES = "financial_services"
    FOOD_BEV = "food_bev"
    UNKNOWN = "unknown"


_SECTOR_MEMBERS: dict[SectorEnum, frozenset[str]] = {
    SectorEnum.BANKS: frozenset({"COMI", "ADIB", "CIEB", "EXPA", "HDBK", "QNBA", "SAUD"}),
    SectorEnum.REAL_ESTATE: frozenset({"TMGH", "HELI", "PHDC", "OCDI", "ORAS", "EMFD"}),
    SectorEnum.INDUSTRY: frozenset(
        {"EAST", "ESRS", "SWDY", "ABUK", "MFPC", "EGAL", "EGCH", "EFIC"}
    ),
    SectorEnum.TELECOM_TECH: frozenset({"ETEL", "FWRY", "EFIH", "RAYA"}),
    SectorEnum.FINANCIAL_SERVICES: frozenset({"HRHO", "BTFH", "CICH"}),
    SectorEnum.FOOD_BEV: frozenset({"JUFO", "EFID", "DOMT"}),
}

_SECTOR_ALIASES_AR: dict[SectorEnum, tuple[str, ...]] = {
    SectorEnum.BANKS: ("البنوك", "البنك", "القطاع المصرفي", "بنوك"),
    SectorEnum.REAL_ESTATE: (
        "العقارات",
        "العقاري",
        "القطاع العقاري",
        "التطوير العقاري",
    ),
    SectorEnum.INDUSTRY: (
        "الصناعة",
        "الصناعات",
        "الأسمدة",
        "الاسمدة",
        "الحديد",
        "الكيماويات",
        "الإسمنت",
        "الاسمنت",
    ),
    SectorEnum.TELECOM_TECH: (
        "الاتصالات",
        "الإتصالات",
        "التكنولوجيا",
        "المدفوعات",
        "التحول الرقمي",
    ),
    SectorEnum.FINANCIAL_SERVICES: (
        "الخدمات المالية",
        "السمسرة",
        "التمويل",
        "التأمين",
        "الإستثمار",
        "الاستثمار",
    ),
    SectorEnum.FOOD_BEV: (
        "الأغذية",
        "الاغذية",
        "المشروبات",
        "السلع الغذائية",
        "الأغذية والمشروبات",
    ),
}


_TICKER_TO_SECTOR: dict[str, SectorEnum] = {
    ticker: sector
    for sector, tickers in _SECTOR_MEMBERS.items()
    for ticker in tickers
}


def _normalize_ticker(ticker: str) -> str:
    t = ticker.strip().upper()
    if t.endswith(".CA"):
        t = t[:-3]
    return t


def ticker_to_sector(ticker: str) -> SectorEnum:
    """Return the 6-sector classification for a ticker.

    Accepts ticker with or without `.CA` suffix, any case. Unknown tickers
    return `SectorEnum.UNKNOWN` — sentiment treats them as opt-out (no sector
    layer contribution) rather than guessing.
    """
    return _TICKER_TO_SECTOR.get(_normalize_ticker(ticker), SectorEnum.UNKNOWN)


def sector_aliases_ar(sector: SectorEnum) -> tuple[str, ...]:
    return _SECTOR_ALIASES_AR.get(sector, ())


def all_sectors() -> Iterable[SectorEnum]:
    return _SECTOR_MEMBERS.keys()


def members_of(sector: SectorEnum) -> frozenset[str]:
    return _SECTOR_MEMBERS.get(sector, frozenset())


_TO_FUNDAMENTALS: dict[SectorEnum, str] = {
    SectorEnum.BANKS: "banks",
    SectorEnum.REAL_ESTATE: "real_estate",
    SectorEnum.FINANCIAL_SERVICES: "holdings",
    SectorEnum.INDUSTRY: "operational",
    SectorEnum.TELECOM_TECH: "operational",
    SectorEnum.FOOD_BEV: "operational",
    SectorEnum.UNKNOWN: "operational",
}


def to_fundamentals_sector(sector: SectorEnum) -> str:
    """Map the 6-sector taxonomy to the legacy 4-sector fundamentals view.

    Until fundamentals migrates to consume `SectorEnum` directly, this shim
    preserves its behavior. Note: the legacy classifier additionally maps a
    few tickers (SWDY, EGAL, BTFH) into different buckets than the 6-sector
    canonical — those overrides remain inside the legacy module and are not
    re-applied here. New consumers must use `ticker_to_sector` directly.
    """
    return _TO_FUNDAMENTALS[sector]


def sectors_covered_by_egx_tickers() -> set[SectorEnum]:
    """Set of sectors that have at least one member in EGX_TICKERS. Used by
    tests to detect drift between this taxonomy and the deployment universe.
    """
    return {
        ticker_to_sector(t)
        for t in EGX_TICKERS
        if ticker_to_sector(t) != SectorEnum.UNKNOWN
    }


# ─────────────────────────────────────────────────────────────────────────────
# EGX index membership (EGX30 / EGX70 / EGX100)
# ─────────────────────────────────────────────────────────────────────────────
#
# The sentiment engine produces sentiment per *index* (the market-level view the
# user asked for) by rolling ticker mentions up into the indices they belong to.
# A blue-chip mention (e.g. COMI) feeds EGX30 *and* EGX100; a mid-cap feeds EGX70
# *and* EGX100 (EGX100 = EGX30 ∪ EGX70 by construction of the EGX index family).
#
# These constituent lists are a curated, recent approximation — the real EGX30 /
# EGX70 baskets are rebalanced semi-annually by the exchange. They are overridable
# at runtime via the EGX30_CONSTITUENTS / EGX70_CONSTITUENTS env vars
# (comma-separated bare symbols, no `.CA`) so an operator can pin the exact
# review-period membership without a code change.


class IndexEnum(str, Enum):
    EGX30 = "EGX30"
    EGX70 = "EGX70"
    EGX100 = "EGX100"


# Curated recent EGX30 large-cap membership (bare symbols) that exist in this
# fork's issuer registry. ~30 names; revise on each exchange rebalance.
_DEFAULT_EGX30: frozenset[str] = frozenset({
    "COMI", "HRHO", "FWRY", "TMGH", "EAST", "ABUK", "MFPC", "ESRS", "SWDY",
    "ETEL", "EFIH", "CIEB", "ADIB", "ORWE", "JUFO", "EFID", "BTFH", "MASR",
    "SKPC", "AMOC", "HELI", "PHDC", "ORAS", "ISPH", "CLHO", "SUGR", "MTIE",
    "QNBA", "CIRA", "TALM",
})

# EGX70 EWI approximation: the next tier of mid/small caps known to this fork
# that are NOT in EGX30. The exchange's real EGX70 has exactly 70 names; this is
# the subset we can map to issuers we track.
_DEFAULT_EGX70: frozenset[str] = frozenset({
    "OCDI", "EMFD", "ORHD", "EGTS", "GPPL", "SPHT", "GIHD", "MHOT", "MASR",
    "EGAL", "EGCH", "EFIC", "ARCC", "MCQE", "SCEM", "MBSC", "IRON", "ATQA",
    "ISMQ", "ELEC", "FERC", "RAYA", "EGSA", "SCTS", "VALU", "GBCO", "CCAP",
    "BINV", "VLMR", "MOIN", "DOMT", "OLFI", "POUL", "IFAP", "PHAR", "MIPH",
    "RMDA", "MPCI", "AMES", "NIPH", "ALCN", "CSAG", "MOIL", "TAQA", "OIH",
    "BONY", "EXPA", "HDBK", "SAUD", "FAIT", "CANA", "UBEE", "EGBE", "QNBE",
})


def _parse_env_constituents(env_var: str, default: frozenset[str]) -> frozenset[str]:
    raw = os.getenv(env_var)
    if not raw:
        return default
    members = {
        _normalize_ticker(tok) for tok in raw.split(",") if tok.strip()
    }
    return frozenset(members) or default


_EGX30_MEMBERS: frozenset[str] = _parse_env_constituents("EGX30_CONSTITUENTS", _DEFAULT_EGX30)
# A symbol can be in EGX30 OR EGX70, never both; drop any EGX30 overlap from EGX70.
_EGX70_MEMBERS: frozenset[str] = (
    _parse_env_constituents("EGX70_CONSTITUENTS", _DEFAULT_EGX70) - _EGX30_MEMBERS
)
_EGX100_MEMBERS: frozenset[str] = _EGX30_MEMBERS | _EGX70_MEMBERS

_INDEX_MEMBERS: dict[IndexEnum, frozenset[str]] = {
    IndexEnum.EGX30: _EGX30_MEMBERS,
    IndexEnum.EGX70: _EGX70_MEMBERS,
    IndexEnum.EGX100: _EGX100_MEMBERS,
}


def ticker_to_indices(ticker: str) -> frozenset[IndexEnum]:
    """Return the set of EGX indices a ticker belongs to.

    A blue chip resolves to ``{EGX30, EGX100}``; a mid-cap to ``{EGX70, EGX100}``;
    an unknown/untracked ticker to ``frozenset()`` (no index contribution — the
    engine abstains rather than guessing index membership).
    """
    sym = _normalize_ticker(ticker)
    out: set[IndexEnum] = set()
    if sym in _EGX30_MEMBERS:
        out.add(IndexEnum.EGX30)
    if sym in _EGX70_MEMBERS:
        out.add(IndexEnum.EGX70)
    if sym in _EGX100_MEMBERS:
        out.add(IndexEnum.EGX100)
    return frozenset(out)


def primary_index(ticker: str) -> IndexEnum | None:
    """The most specific index for a ticker: EGX30 if blue-chip, else EGX70,
    else None. EGX100 is never the *primary* index because it is the union.
    """
    sym = _normalize_ticker(ticker)
    if sym in _EGX30_MEMBERS:
        return IndexEnum.EGX30
    if sym in _EGX70_MEMBERS:
        return IndexEnum.EGX70
    return None


def members_of_index(index: IndexEnum) -> frozenset[str]:
    return _INDEX_MEMBERS.get(index, frozenset())


def all_indices() -> Iterable[IndexEnum]:
    return _INDEX_MEMBERS.keys()
