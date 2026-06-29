"""Centralized 6-sector EGX taxonomy.

Single source of truth for sentiment, fundamentals, scoring, and dashboard.
The 6-sector view is canonical going forward; the legacy 4-sector fundamentals
classifier (`agents/analysts/fundamentals/sector_config.py`) is kept working
via `to_fundamentals_sector()` so its 79 unit tests stay green.

Sector membership matches CLAUDE.md §10 (EGX_TICKERS sector grouping).
Migrating fundamentals to consume this module directly is a follow-up PR.
"""
from __future__ import annotations

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
        {"EAST", "SWDY", "ABUK", "MFPC", "EGAL", "EGCH", "EFIC"}
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
