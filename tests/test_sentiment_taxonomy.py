"""PR 1 — central 6-sector taxonomy: coverage and legacy compatibility."""
from __future__ import annotations

import pytest

from tradingagents.default_config import EGX_TICKERS
from tradingagents.sentiment.taxonomy import (
    SectorEnum,
    all_sectors,
    members_of,
    sector_aliases_ar,
    sectors_covered_by_egx_tickers,
    ticker_to_sector,
    to_fundamentals_sector,
)


def test_every_egx_ticker_resolves_to_a_known_sector() -> None:
    unknown = [t for t in EGX_TICKERS if ticker_to_sector(t) == SectorEnum.UNKNOWN]
    assert unknown == [], (
        "EGX_TICKERS contains tickers without a sector assignment. "
        "Add them to _SECTOR_MEMBERS in tradingagents/sentiment/taxonomy.py: "
        f"{unknown}"
    )


def test_ticker_normalization_handles_suffix_and_case() -> None:
    assert ticker_to_sector("COMI") == SectorEnum.BANKS
    assert ticker_to_sector("comi") == SectorEnum.BANKS
    assert ticker_to_sector("COMI.CA") == SectorEnum.BANKS
    assert ticker_to_sector(" comi.ca ") == SectorEnum.BANKS


def test_unknown_ticker_returns_unknown_not_a_guess() -> None:
    assert ticker_to_sector("ZZZZ") == SectorEnum.UNKNOWN
    assert ticker_to_sector("ZZZZ.CA") == SectorEnum.UNKNOWN


def test_six_sectors_each_have_arabic_aliases() -> None:
    six = [s for s in all_sectors() if s != SectorEnum.UNKNOWN]
    assert len(six) == 6
    for s in six:
        aliases = sector_aliases_ar(s)
        assert len(aliases) > 0, f"sector {s.value} missing Arabic aliases"
        for a in aliases:
            assert a.strip() == a
            assert any("؀" <= ch <= "ۿ" for ch in a), (
                f"alias {a!r} for {s.value} contains no Arabic characters"
            )


def test_sectors_are_mutually_exclusive() -> None:
    seen: dict[str, SectorEnum] = {}
    for s in all_sectors():
        for t in members_of(s):
            assert t not in seen, (
                f"ticker {t} double-mapped: {seen[t].value} and {s.value}"
            )
            seen[t] = s


def test_egx_tickers_cover_all_six_sectors() -> None:
    covered = sectors_covered_by_egx_tickers()
    assert covered == {
        SectorEnum.BANKS,
        SectorEnum.REAL_ESTATE,
        SectorEnum.INDUSTRY,
        SectorEnum.TELECOM_TECH,
        SectorEnum.FINANCIAL_SERVICES,
        SectorEnum.FOOD_BEV,
    }


@pytest.mark.parametrize(
    "ticker, expected",
    [
        ("COMI", SectorEnum.BANKS),
        ("ADIB", SectorEnum.BANKS),
        ("TMGH", SectorEnum.REAL_ESTATE),
        ("ESRS", SectorEnum.INDUSTRY),
        ("FWRY", SectorEnum.TELECOM_TECH),
        ("HRHO", SectorEnum.FINANCIAL_SERVICES),
        ("JUFO", SectorEnum.FOOD_BEV),
    ],
)
def test_specific_assignments_match_claude_md_section_10(
    ticker: str, expected: SectorEnum
) -> None:
    assert ticker_to_sector(ticker) == expected


def test_legacy_fundamentals_shim_returns_legal_4_sector_values() -> None:
    legal = {"banks", "real_estate", "holdings", "operational"}
    for s in all_sectors():
        assert to_fundamentals_sector(s) in legal


def test_legacy_shim_routes_canonical_to_legacy_buckets() -> None:
    assert to_fundamentals_sector(SectorEnum.BANKS) == "banks"
    assert to_fundamentals_sector(SectorEnum.REAL_ESTATE) == "real_estate"
    assert to_fundamentals_sector(SectorEnum.FINANCIAL_SERVICES) == "holdings"
    assert to_fundamentals_sector(SectorEnum.INDUSTRY) == "operational"
    assert to_fundamentals_sector(SectorEnum.TELECOM_TECH) == "operational"
    assert to_fundamentals_sector(SectorEnum.FOOD_BEV) == "operational"
    assert to_fundamentals_sector(SectorEnum.UNKNOWN) == "operational"
