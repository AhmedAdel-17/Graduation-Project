#!/usr/bin/env python3
"""
Fundamentals data coverage report for EGX tickers.

Scans the CSV data cache and reports:
  - gross_profit coverage by ticker and sector
  - dividend_yield coverage
  - pe_ratio coverage
  - CBE policy rate provenance (total rows, PROVISIONAL markers)
  - Stub/empty files (0 annual rows)
  - Row counts per ticker (annual + quarterly)
  - Ticker completeness matrix (high / medium / low quality)

Usage:
    python scripts/fundamentals_coverage_report.py
    python scripts/fundamentals_coverage_report.py --json   # also write JSON
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# ── Resolve project root so imports work when run from repo root ────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tradingagents.agents.analysts.fundamentals.sector_config import (
    SECTOR_MAP,
    classify_sector,
)

# ── Data paths ──────────────────────────────────────────────────────────────
DATA_CACHE = os.path.join(
    _PROJECT_ROOT, "tradingagents", "dataflows", "data_cache"
)
FUNDAMENTALS_DIR = os.path.join(DATA_CACHE, "egx_fundamentals")
INCOME_DIR = os.path.join(FUNDAMENTALS_DIR, "income_statements")
BALANCE_DIR = os.path.join(FUNDAMENTALS_DIR, "balance_sheets")
RATIOS_DIR = os.path.join(FUNDAMENTALS_DIR, "key_ratios")
CBE_RATES_PATH = os.path.join(DATA_CACHE, "egx_macro", "cbe_policy_rates.csv")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _discover_tickers() -> List[str]:
    """Find all tickers that have at least one income CSV."""
    tickers = set()
    if not os.path.isdir(INCOME_DIR):
        return []
    for fname in os.listdir(INCOME_DIR):
        if fname.endswith("_income_annual.csv"):
            tickers.add(fname.replace("_income_annual.csv", ""))
        elif fname.endswith("_income_quarterly.csv"):
            tickers.add(fname.replace("_income_quarterly.csv", ""))
    return sorted(tickers)


def _read_csv(path: str) -> List[Dict[str, str]]:
    """Read a CSV file and return rows as list of dicts. Empty list if missing."""
    if not os.path.isfile(path):
        return []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return list(reader)
    except Exception:
        return []


def _is_populated(value: Optional[str]) -> bool:
    """Check if a CSV cell has a real (non-empty, non-NaN) value."""
    if value is None:
        return False
    v = value.strip()
    if v in ("", "nan", "NaN", "None", "none", "N/A", "n/a", "--", "-"):
        return False
    try:
        f = float(v)
        return math.isfinite(f)
    except (ValueError, TypeError):
        return False


def _count_populated(rows: List[Dict[str, str]], field_name: str) -> int:
    """Count how many rows have a populated value for field_name."""
    return sum(1 for r in rows if _is_populated(r.get(field_name)))


# ── Per-ticker analysis ────────────────────────────────────────────────────


# PE ratio coverage threshold: >=80% of annual rows = "adequate"
PE_ADEQUATE_THRESHOLD = 0.80

# Fields that indicate a quarterly row has real data (not just placeholders)
_QUARTERLY_INCOME_FIELDS = ("revenue", "net_income", "gross_profit", "operating_income")
_QUARTERLY_BALANCE_FIELDS = ("total_assets", "total_liabilities", "total_equity")
_QUARTERLY_RATIOS_FIELDS = ("net_margin", "roe", "roa", "eps")

# Bank-relevant fields (used instead of gross_profit for quality scoring)
_BANK_REQUIRED_FIELDS = ("interest_income", "interest_expense", "net_income")


def classify_pe_tier(populated: int, total: int) -> str:
    """Classify P/E coverage into adequate / sparse / none.

    - adequate: populated in >=80% of annual rows
    - sparse:   populated in 1+ rows but <80%
    - none:     0 populated rows
    """
    if total == 0 or populated == 0:
        return "none"
    if populated / total >= PE_ADEQUATE_THRESHOLD:
        return "adequate"
    return "sparse"


def classify_quality(
    *,
    sector: str,
    is_stub: bool,
    annual_depth: int,
    gross_profit_populated: int,
    bank_fields_populated: int,
    bank_fields_total: int,
) -> str:
    """Classify ticker data quality as high / medium / low.

    Sector-aware: banks are not penalized for missing gross_profit but must
    have bank-relevant fields (interest_income, interest_expense, net_income).
    """
    if is_stub:
        return "low"

    if sector == "banks":
        # Banks: require >=4 annual periods + bank-relevant fields populated
        has_bank_data = bank_fields_populated > 0
        if annual_depth >= 4 and has_bank_data:
            return "high"
        elif annual_depth >= 2:
            return "medium"
        return "low"

    # Non-banks: require gross_profit + annual depth
    has_gp = gross_profit_populated > 0
    if annual_depth >= 4 and has_gp:
        return "high"
    elif annual_depth >= 2:
        return "medium"
    return "low"


def count_quarterly_populated_rows(
    income_rows: List[Dict[str, str]],
    balance_rows: List[Dict[str, str]],
    ratios_rows: List[Dict[str, str]],
) -> int:
    """Count quarterly rows that have at least one meaningful populated field.

    Checks income, balance, and ratios fields. Rows where all meaningful
    fields are empty/NaN/placeholder are not counted.
    """
    populated = 0
    n = max(len(income_rows), len(balance_rows), len(ratios_rows))
    for i in range(n):
        inc = income_rows[i] if i < len(income_rows) else {}
        bal = balance_rows[i] if i < len(balance_rows) else {}
        rat = ratios_rows[i] if i < len(ratios_rows) else {}
        has_any = False
        for f in _QUARTERLY_INCOME_FIELDS:
            if _is_populated(inc.get(f)):
                has_any = True
                break
        if not has_any:
            for f in _QUARTERLY_BALANCE_FIELDS:
                if _is_populated(bal.get(f)):
                    has_any = True
                    break
        if not has_any:
            for f in _QUARTERLY_RATIOS_FIELDS:
                if _is_populated(rat.get(f)):
                    has_any = True
                    break
        if has_any:
            populated += 1
    return populated


def count_cbe_provisional(rows: List[Dict[str, str]]) -> tuple:
    """Count rows with PROVISIONAL in verification_status.

    Returns (provisional_count, provisional_dates).
    """
    count = 0
    dates: List[str] = []
    for r in rows:
        vs = r.get("verification_status", "").strip().upper()
        if "PROVISIONAL" in vs:
            count += 1
            dates.append(r.get("effective_date", ""))
    return count, dates


@dataclass
class TickerCoverage:
    ticker: str
    sector: str
    # Row counts
    income_annual_rows: int = 0
    income_quarterly_rows: int = 0
    balance_annual_rows: int = 0
    balance_quarterly_rows: int = 0
    ratios_annual_rows: int = 0
    ratios_quarterly_rows: int = 0
    # File existence
    has_annual_income: bool = False
    has_quarterly_income: bool = False
    has_annual_balance: bool = False
    has_quarterly_balance: bool = False
    has_annual_ratios: bool = False
    has_quarterly_ratios: bool = False
    # Key field coverage (out of annual rows)
    gross_profit_populated: int = 0
    dividend_yield_populated: int = 0
    pe_ratio_populated: int = 0
    pe_ratio_tier: str = ""  # adequate / sparse / none
    # Bank-specific fields
    bank_fields_populated: int = 0  # count of bank-relevant fields with data
    bank_fields_total: int = 0      # total bank-relevant field slots checked
    # Stub detection
    is_stub: bool = False
    stub_reason: str = ""
    quarterly_populated_rows: int = 0  # rows with real data (not placeholders)
    # Quality tier
    quality: str = ""  # high / medium / low

    def gross_profit_pct(self) -> str:
        if self.income_annual_rows == 0:
            return "N/A"
        return f"{self.gross_profit_populated}/{self.income_annual_rows}"

    def dividend_yield_pct(self) -> str:
        if self.ratios_annual_rows == 0:
            return "N/A"
        return f"{self.dividend_yield_populated}/{self.ratios_annual_rows}"

    def pe_ratio_display(self) -> str:
        if self.ratios_annual_rows == 0:
            return "N/A"
        return f"{self.pe_ratio_tier} ({self.pe_ratio_populated}/{self.ratios_annual_rows})"


def analyze_ticker(ticker: str) -> TickerCoverage:
    sector = classify_sector(ticker)
    tc = TickerCoverage(ticker=ticker, sector=sector)

    # Income
    inc_a = _read_csv(os.path.join(INCOME_DIR, f"{ticker}_income_annual.csv"))
    inc_q = _read_csv(os.path.join(INCOME_DIR, f"{ticker}_income_quarterly.csv"))
    tc.income_annual_rows = len(inc_a)
    tc.income_quarterly_rows = len(inc_q)
    tc.has_annual_income = os.path.isfile(os.path.join(INCOME_DIR, f"{ticker}_income_annual.csv"))
    tc.has_quarterly_income = os.path.isfile(os.path.join(INCOME_DIR, f"{ticker}_income_quarterly.csv"))
    tc.gross_profit_populated = _count_populated(inc_a, "gross_profit")

    # Bank-relevant fields
    if sector == "banks":
        for f in _BANK_REQUIRED_FIELDS:
            tc.bank_fields_total += len(inc_a)
            tc.bank_fields_populated += _count_populated(inc_a, f)

    # Balance
    bal_a = _read_csv(os.path.join(BALANCE_DIR, f"{ticker}_balance_annual.csv"))
    bal_q = _read_csv(os.path.join(BALANCE_DIR, f"{ticker}_balance_quarterly.csv"))
    tc.balance_annual_rows = len(bal_a)
    tc.balance_quarterly_rows = len(bal_q)
    tc.has_annual_balance = os.path.isfile(os.path.join(BALANCE_DIR, f"{ticker}_balance_annual.csv"))
    tc.has_quarterly_balance = os.path.isfile(os.path.join(BALANCE_DIR, f"{ticker}_balance_quarterly.csv"))

    # Ratios
    rat_a = _read_csv(os.path.join(RATIOS_DIR, f"{ticker}_ratios.csv"))
    rat_q = _read_csv(os.path.join(RATIOS_DIR, f"{ticker}_ratios_quarterly.csv"))
    tc.ratios_annual_rows = len(rat_a)
    tc.ratios_quarterly_rows = len(rat_q)
    tc.has_annual_ratios = os.path.isfile(os.path.join(RATIOS_DIR, f"{ticker}_ratios.csv"))
    tc.has_quarterly_ratios = os.path.isfile(os.path.join(RATIOS_DIR, f"{ticker}_ratios_quarterly.csv"))
    tc.dividend_yield_populated = _count_populated(rat_a, "dividend_yield")
    tc.pe_ratio_populated = _count_populated(rat_a, "pe_ratio")

    # P/E tier
    tc.pe_ratio_tier = classify_pe_tier(tc.pe_ratio_populated, tc.ratios_annual_rows)

    # Stub detection — check quarterly populated values across all statement types
    if tc.income_annual_rows == 0 and tc.balance_annual_rows == 0:
        tc.is_stub = True
        tc.quarterly_populated_rows = count_quarterly_populated_rows(inc_q, bal_q, rat_q)
        if tc.quarterly_populated_rows > 0:
            tc.stub_reason = (
                f"quarterly-only (0 annual rows, "
                f"{tc.quarterly_populated_rows}/{max(len(inc_q), len(bal_q), len(rat_q))} "
                f"quarterly rows with data)"
            )
        elif tc.income_quarterly_rows > 0 or tc.balance_quarterly_rows > 0:
            tc.stub_reason = "empty placeholders (quarterly rows exist but all fields empty)"
        else:
            tc.stub_reason = "empty (no annual or quarterly data)"

    # Quality tier (sector-aware)
    annual_depth = min(tc.income_annual_rows, tc.balance_annual_rows)
    tc.quality = classify_quality(
        sector=sector,
        is_stub=tc.is_stub,
        annual_depth=annual_depth,
        gross_profit_populated=tc.gross_profit_populated,
        bank_fields_populated=tc.bank_fields_populated,
        bank_fields_total=tc.bank_fields_total,
    )

    return tc


# ── CBE rates analysis ─────────────────────────────────────────────────────


@dataclass
class CBERatesCoverage:
    total_rows: int = 0
    provisional_rows: int = 0
    provisional_dates: List[str] = field(default_factory=list)
    date_range: str = ""
    all_dates: List[str] = field(default_factory=list)


def analyze_cbe_rates() -> CBERatesCoverage:
    cov = CBERatesCoverage()
    rows = _read_csv(CBE_RATES_PATH)
    cov.total_rows = len(rows)
    if not rows:
        return cov

    dates = [r.get("effective_date", "") for r in rows if r.get("effective_date")]
    cov.provisional_rows, cov.provisional_dates = count_cbe_provisional(rows)
    cov.all_dates = sorted(dates)
    if dates:
        cov.date_range = f"{min(dates)} to {max(dates)}"

    return cov


# ── Report rendering ────────────────────────────────────────────────────────


def render_markdown(
    tickers: List[TickerCoverage],
    cbe: CBERatesCoverage,
) -> str:
    lines: List[str] = []

    lines.append("# EGX Fundamentals Data Coverage Report\n")

    # ── Summary ─────────────────────────────────────────────────────────
    total = len(tickers)
    gp_ok = sum(1 for t in tickers if t.gross_profit_populated > 0)
    dy_ok = sum(1 for t in tickers if t.dividend_yield_populated > 0)
    pe_ok = sum(1 for t in tickers if t.pe_ratio_populated > 0)
    stubs = [t for t in tickers if t.is_stub]
    high_q = sum(1 for t in tickers if t.quality == "high")
    med_q = sum(1 for t in tickers if t.quality == "medium")
    low_q = sum(1 for t in tickers if t.quality == "low")

    pe_adequate = sum(1 for t in tickers if t.pe_ratio_tier == "adequate")
    pe_sparse = sum(1 for t in tickers if t.pe_ratio_tier == "sparse")
    pe_none = sum(1 for t in tickers if t.pe_ratio_tier == "none")

    lines.append("## Summary\n")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total tickers | {total} |")
    lines.append(f"| gross_profit coverage | {gp_ok}/{total} ({100*gp_ok//total}%) |")
    lines.append(f"| dividend_yield coverage | {dy_ok}/{total} ({100*dy_ok//total}%) |")
    lines.append(f"| pe_ratio: adequate (>=80%) | {pe_adequate}/{total} |")
    lines.append(f"| pe_ratio: sparse (<80%) | {pe_sparse}/{total} |")
    lines.append(f"| pe_ratio: none | {pe_none}/{total} |")
    lines.append(f"| Stub/empty files | {len(stubs)} |")
    lines.append(f"| Quality: high / medium / low | {high_q} / {med_q} / {low_q} |")
    lines.append(f"| CBE rate rows | {cbe.total_rows} ({cbe.date_range}) |")
    lines.append(f"| CBE PROVISIONAL rows | {cbe.provisional_rows}/{cbe.total_rows} |")
    lines.append("")

    # ── Stub files ──────────────────────────────────────────────────────
    if stubs:
        lines.append("## Stub / Empty Files\n")
        lines.append("| Ticker | Sector | Reason | Quarterly Rows |")
        lines.append("|--------|--------|--------|----------------|")
        for t in stubs:
            lines.append(
                f"| {t.ticker} | {t.sector} | {t.stub_reason} | "
                f"inc={t.income_quarterly_rows} bal={t.balance_quarterly_rows} rat={t.ratios_quarterly_rows} |"
            )
        lines.append("")

    # ── Per-sector tables ───────────────────────────────────────────────
    sectors_order = ["banks", "real_estate", "holdings", "operational"]
    for sector in sectors_order:
        sector_tickers = [t for t in tickers if t.sector == sector]
        if not sector_tickers:
            continue

        lines.append(f"## {sector.replace('_', ' ').title()} ({len(sector_tickers)} tickers)\n")
        lines.append(
            "| Ticker | Quality | Annual (I/B/R) | Quarterly (I/B/R) | "
            "gross_profit | dividend_yield | pe_ratio |"
        )
        lines.append(
            "|--------|---------|----------------|-------------------|"
            "--------------|----------------|----------|"
        )
        for t in sector_tickers:
            ann = f"{t.income_annual_rows}/{t.balance_annual_rows}/{t.ratios_annual_rows}"
            qtr = f"{t.income_quarterly_rows}/{t.balance_quarterly_rows}/{t.ratios_quarterly_rows}"
            stub_marker = " **STUB**" if t.is_stub else ""
            lines.append(
                f"| {t.ticker}{stub_marker} | {t.quality} | {ann} | {qtr} | "
                f"{t.gross_profit_pct()} | {t.dividend_yield_pct()} | {t.pe_ratio_display()} |"
            )
        lines.append("")

    # ── gross_profit detail ─────────────────────────────────────────────
    gp_missing = [t for t in tickers if t.gross_profit_populated == 0 and not t.is_stub]
    if gp_missing:
        lines.append("## gross_profit: Missing (non-stub tickers)\n")
        lines.append("| Ticker | Sector | Note |")
        lines.append("|--------|--------|------|")
        for t in gp_missing:
            note = "Structural (banks use interest income)" if t.sector == "banks" else "Data gap"
            lines.append(f"| {t.ticker} | {t.sector} | {note} |")
        lines.append("")

    # ── CBE rates ───────────────────────────────────────────────────────
    lines.append("## CBE Policy Rates\n")
    lines.append(f"- **File:** `egx_macro/cbe_policy_rates.csv`")
    lines.append(f"- **Total rows:** {cbe.total_rows}")
    lines.append(f"- **Date range:** {cbe.date_range}")
    lines.append(f"- **PROVISIONAL rows:** {cbe.provisional_rows}/{cbe.total_rows}")
    if cbe.provisional_dates:
        lines.append(f"- **PROVISIONAL dates:** {', '.join(cbe.provisional_dates)}")
    lines.append("")

    # ── Known Limitations ──────────────────────────────────────────────
    lines.append("## Known Limitations\n")
    lines.append(
        "1. **dividend_yield:** 0% usable coverage. Non-stub annual ratios "
        "have the column but all values are empty; stub tickers have no "
        "usable annual rows."
    )
    lines.append(
        "2. **pe_ratio:** Mostly sparse — typically only the most recent annual "
        "period has a value. Multi-year P/E trend analysis (compression/expansion) "
        "is not possible with current data."
    )
    lines.append(
        f"3. **CBE policy rates:** {cbe.provisional_rows}/{cbe.total_rows} rows are "
        "PROVISIONAL (reconstructed from public CBE announcements). These need "
        "verification against official MPC press releases before use in "
        "production backtests."
    )
    stub_tickers = [t.ticker for t in tickers if t.is_stub]
    if stub_tickers:
        lines.append(
            f"4. **Annual stubs:** {', '.join(stub_tickers)} have 0 annual rows. "
            "Quarterly data is empty placeholders. These tickers are non-functional "
            "in the Phase 1A fundamentals pipeline."
        )
    bank_gp_missing = [t.ticker for t in gp_missing if t.sector == "banks"]
    lines.append(
        f"5. **Bank gross_profit:** For bank tickers where gross_profit is "
        f"blank ({', '.join(bank_gp_missing)}), this is treated as "
        "structurally N/A and quality scoring uses bank-relevant fields "
        "(interest_income, interest_expense, net_income) instead."
    )
    lines.append("")

    return "\n".join(lines)


def build_json_summary(
    tickers: List[TickerCoverage],
    cbe: CBERatesCoverage,
) -> Dict[str, Any]:
    return {
        "total_tickers": len(tickers),
        "gross_profit_coverage": sum(1 for t in tickers if t.gross_profit_populated > 0),
        "dividend_yield_coverage": sum(1 for t in tickers if t.dividend_yield_populated > 0),
        "pe_ratio_adequate": [t.ticker for t in tickers if t.pe_ratio_tier == "adequate"],
        "pe_ratio_sparse": [t.ticker for t in tickers if t.pe_ratio_tier == "sparse"],
        "pe_ratio_none": [t.ticker for t in tickers if t.pe_ratio_tier == "none"],
        "stubs": [
            {"ticker": t.ticker, "stub_reason": t.stub_reason,
             "quarterly_populated_rows": t.quarterly_populated_rows}
            for t in tickers if t.is_stub
        ],
        "quality_high": [t.ticker for t in tickers if t.quality == "high"],
        "quality_medium": [t.ticker for t in tickers if t.quality == "medium"],
        "quality_low": [t.ticker for t in tickers if t.quality == "low"],
        "gross_profit_missing_non_stub": [
            {"ticker": t.ticker, "sector": t.sector}
            for t in tickers if t.gross_profit_populated == 0 and not t.is_stub
        ],
        "cbe_rates": {
            "total_rows": cbe.total_rows,
            "provisional_rows": cbe.provisional_rows,
            "provisional_dates": cbe.provisional_dates,
            "date_range": cbe.date_range,
        },
        "per_ticker": [asdict(t) for t in tickers],
    }


# ── Main ────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="EGX fundamentals data coverage report")
    parser.add_argument("--json", action="store_true", help="Write JSON summary to reports/")
    args = parser.parse_args()

    tickers_found = _discover_tickers()
    if not tickers_found:
        print("ERROR: No tickers found. Check DATA_CACHE path:", DATA_CACHE)
        sys.exit(1)

    coverages = [analyze_ticker(t) for t in tickers_found]
    cbe = analyze_cbe_rates()

    report = render_markdown(coverages, cbe)
    print(report)

    if args.json:
        reports_dir = os.path.join(_PROJECT_ROOT, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        out_path = os.path.join(reports_dir, "fundamentals_coverage.json")
        summary = build_json_summary(coverages, cbe)
        with open(out_path, "w") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"\nJSON summary written to: {out_path}")


if __name__ == "__main__":
    main()
