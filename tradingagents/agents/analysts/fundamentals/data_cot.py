"""
Stage 1: Evidence Pack Assembly for EGX Fundamental Analyst CoT Pipeline.

Takes the fully-computed deterministic outputs (ratios, preprocessing,
distress flags, scores) and formats them into a structured evidence pack
for LLM consumption in Stage 2 (Concept CoT).

This stage is deterministic — no LLM calls. Its output is validated before
Stage 2 is invoked. If validation fails, the pipeline falls back to the
deterministic-only output.

Evidence class: DS [P7] Kim et al. 2024 — structured evidence packs improve
LLM reasoning quality by removing ambiguity in what data to interpret.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .schemas import FundamentalAnalysisReport
from .sector_config import SectorConfig, METRIC_CONTEXT_NOTES

logger = logging.getLogger(__name__)

# Minimum data_confidence for Stage 2 to be invoked.
# Below this, there is too little data for LLM interpretation to add value.
_MIN_CONFIDENCE_FOR_COT = 20


def build_evidence_pack(
    report: FundamentalAnalysisReport,
    sector_cfg: SectorConfig,
) -> Dict[str, Any]:
    """
    Assemble a structured evidence pack from a deterministic FundamentalAnalysisReport.

    The evidence pack contains all computed data in two forms:
      - Structured dict (for downstream parsing and Stage 3 JSON building)
      - Formatted narrative string (for LLM consumption in Stage 2/3 prompts)

    Returns:
      dict with keys:
        ticker, fiscal_period, sector, sector_context,
        ratios, directions, distress_flags,
        data_confidence, signal_coherence,
        revenue_growth_yoy, net_income_growth_yoy,
        common_size_income, common_size_balance,
        piotroski_score,
        narrative (str — formatted for LLM),
        _valid (bool), _validation_errors (list[str])
    """
    ratios = report.ratios or {}
    preprocessing = report.preprocessing or {}
    directions = preprocessing.get("directions", {})
    common_size_income = preprocessing.get("common_size_income") or {}
    common_size_balance = preprocessing.get("common_size_balance") or {}
    sector_context = sector_cfg.get_sector_summary()

    pack: Dict[str, Any] = {
        "ticker": report.ticker,
        "analysis_date": report.analysis_date,
        "fiscal_period": report.fiscal_period,
        "sector": report.sector,
        "sector_context": sector_context,
        "ratios": ratios,
        "directions": directions,
        "distress_flags": report.distress_flags,
        "data_confidence": report.data_confidence,
        "signal_coherence": report.signal_coherence,
        "revenue_growth_yoy": preprocessing.get("revenue_growth_yoy"),
        "net_income_growth_yoy": preprocessing.get("net_income_growth_yoy"),
        "common_size_income": common_size_income,
        "common_size_balance": common_size_balance,
        "piotroski_score": ratios.get("piotroski_score"),
        "financial_health_heuristic": report.financial_health,
    }

    # Format the narrative for LLM consumption
    pack["narrative"] = format_evidence_narrative(pack)

    # Validate
    valid, errors = validate_evidence_pack(pack)
    pack["_valid"] = valid
    pack["_validation_errors"] = errors

    return pack


def validate_evidence_pack(pack: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate the evidence pack before invoking Stage 2.

    Validation fails if:
      1. data_confidence < _MIN_CONFIDENCE_FOR_COT (too little data for LLM)
      2. ratios dict is empty (no computed metrics at all)
      3. narrative is empty (formatting failed)

    Returns (is_valid, list_of_error_strings).
    """
    errors: List[str] = []

    dc = pack.get("data_confidence", 0)
    if dc < _MIN_CONFIDENCE_FOR_COT:
        errors.append(
            f"data_confidence={dc} < threshold={_MIN_CONFIDENCE_FOR_COT}: "
            f"insufficient data for LLM interpretation"
        )

    ratios = pack.get("ratios", {})
    non_null_ratios = {k: v for k, v in ratios.items() if v is not None}
    if not non_null_ratios:
        errors.append("ratios dict has no non-null values: nothing for LLM to interpret")

    if not pack.get("narrative", "").strip():
        errors.append("evidence narrative is empty: formatting failed")

    return (len(errors) == 0), errors


def _fmt(val: Optional[float], pct: bool = False, decimals: int = 3) -> str:
    """Format a float for display in the evidence narrative."""
    if val is None:
        return "N/A"
    if pct:
        return f"{val:+.1%}"
    if abs(val) >= 1_000_000:
        return f"{val/1_000_000:,.1f}M"
    if abs(val) >= 1_000:
        return f"{val:,.0f}"
    return f"{val:.{decimals}f}"


def _direction_arrow(d: str) -> str:
    """Convert direction string to a short label for narrative."""
    return {"improving": "↑", "deteriorating": "↓", "stable": "→", "insufficient_history": "?"}.get(d, "?")


def format_evidence_narrative(pack: Dict[str, Any]) -> str:
    """
    Format the evidence pack as a structured text narrative for LLM consumption.

    Uses clear section headers and a tabular layout so the LLM can parse
    the evidence systematically without needing to infer structure.
    """
    ticker = pack.get("ticker", "")
    fiscal = pack.get("fiscal_period", "")
    sector = pack.get("sector", "")
    dc = pack.get("data_confidence", 0)
    sc = pack.get("signal_coherence", 100)
    ratios = pack.get("ratios", {})
    directions = pack.get("directions", {})
    flags = pack.get("distress_flags", [])
    sector_context = pack.get("sector_context", {})
    rev_growth = pack.get("revenue_growth_yoy")
    ni_growth = pack.get("net_income_growth_yoy")
    cs_income = pack.get("common_size_income", {})
    cs_balance = pack.get("common_size_balance", {})
    piotroski = pack.get("piotroski_score")
    health_heuristic = pack.get("financial_health_heuristic", "")

    lines: List[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines.append(f"EVIDENCE PACK — {ticker} | {fiscal}")
    lines.append(f"Sector: {sector.upper()} | Analysis Date: {pack.get('analysis_date', '')}")
    lines.append(f"Data Confidence: {dc}/100 | Signal Coherence: {sc}/100")
    lines.append(f"Deterministic Health Heuristic: {health_heuristic}")
    lines.append("")

    # ── Distress Flags ─────────────────────────────────────────────────────
    lines.append("DISTRESS FLAGS")
    if flags:
        for f in flags:
            lines.append(f"  [!] {f}")
    else:
        lines.append("  none")
    lines.append("")

    # ── Sector Context ─────────────────────────────────────────────────────
    if sector_context:
        lines.append("SECTOR-SPECIFIC INTERPRETATION NOTES")
        for metric, note in sector_context.items():
            lines.append(f"  {metric}: {note}")
        lines.append("")

    # ── Profitability ─────────────────────────────────────────────────────
    lines.append("PROFITABILITY RATIOS")
    roe = ratios.get("roe")
    roa = ratios.get("roa")
    gm = ratios.get("gross_margin")
    om = ratios.get("operating_margin")
    nm = ratios.get("net_margin")
    lines.append(
        f"  ROE: {_fmt(roe, pct=True)} {_direction_arrow(directions.get('roe', '?'))} | "
        f"ROA: {_fmt(roa, pct=True)} {_direction_arrow(directions.get('roa', '?'))}"
    )
    lines.append(
        f"  Gross Margin: {_fmt(gm, pct=True)} {_direction_arrow(directions.get('gross_margin', '?'))} | "
        f"Operating Margin: {_fmt(om, pct=True)} {_direction_arrow(directions.get('operating_margin', '?'))} | "
        f"Net Margin: {_fmt(nm, pct=True)} {_direction_arrow(directions.get('net_margin', '?'))}"
    )
    lines.append("")

    # ── Leverage & Liquidity ──────────────────────────────────────────────
    lines.append("LEVERAGE & LIQUIDITY")
    de = ratios.get("debt_to_equity")
    cr = ratios.get("current_ratio")
    lines.append(
        f"  D/E: {_fmt(de)} {_direction_arrow(directions.get('debt_to_equity', '?'))} | "
        f"Current Ratio: {_fmt(cr)} {_direction_arrow(directions.get('current_ratio', '?'))}"
    )
    lines.append("")

    # ── Valuation ─────────────────────────────────────────────────────────
    lines.append("VALUATION")
    pe = ratios.get("pe_ratio")
    pb = ratios.get("pb_ratio")
    eps = ratios.get("eps")
    ey = ratios.get("earnings_yield")
    ey_spread = ratios.get("earnings_yield_spread")
    div_yield = ratios.get("dividend_yield")
    lines.append(
        f"  P/E: {_fmt(pe)} | P/B: {_fmt(pb)} | EPS: {_fmt(eps)}"
    )
    lines.append(
        f"  Earnings Yield: {_fmt(ey, pct=True)} | EY Spread vs Risk-Free: {_fmt(ey_spread, pct=True)}"
    )
    lines.append(f"  Dividend Yield: {_fmt(div_yield, pct=True)}")
    lines.append("")

    # ── Growth ────────────────────────────────────────────────────────────
    lines.append("GROWTH SIGNALS")
    lines.append(
        f"  Revenue YoY: {_fmt(rev_growth, pct=True)} {_direction_arrow(directions.get('revenue', '?'))} | "
        f"Net Income YoY: {_fmt(ni_growth, pct=True)} {_direction_arrow(directions.get('net_income', '?'))}"
    )
    lines.append(
        f"  Gross Profit trend: {_direction_arrow(directions.get('gross_profit', '?'))} | "
        f"Operating Income trend: {_direction_arrow(directions.get('operating_income', '?'))}"
    )
    lines.append(
        f"  Total Assets trend: {_direction_arrow(directions.get('total_assets', '?'))} | "
        f"Total Equity trend: {_direction_arrow(directions.get('total_equity', '?'))}"
    )
    lines.append("")

    # ── Piotroski ─────────────────────────────────────────────────────────
    lines.append("PIOTROSKI QUALITY SCORE")
    if piotroski is not None:
        lines.append(f"  {piotroski:.0f}/7 (EGX variant: OCF and accrual signals excluded from EGX CSV schema)")
        if piotroski >= 5:
            lines.append("  Signal: Strong financial quality")
        elif piotroski >= 3:
            lines.append("  Signal: Moderate financial quality")
        else:
            lines.append("  Signal: Weak financial quality")
    else:
        lines.append("  N/A (insufficient data for Piotroski computation)")
    lines.append("")

    # ── Common-Size Income ────────────────────────────────────────────────
    cs_fields_income = {
        "gross_profit_pct": "Gross Profit",
        "operating_income_pct": "Operating Income",
        "net_income_pct": "Net Income",
        "interest_expense_pct": "Interest Expense",
        "tax_expense_pct": "Tax Expense",
        "ebitda_pct": "EBITDA",
    }
    cs_income_lines = []
    for key, label in cs_fields_income.items():
        val = cs_income.get(key)
        if val is not None:
            cs_income_lines.append(f"  {label}: {_fmt(val, pct=True)}")
    if cs_income_lines:
        lines.append("INCOME STRUCTURE (% of Revenue)")
        lines.extend(cs_income_lines)
        lines.append("")

    # ── Common-Size Balance ───────────────────────────────────────────────
    cs_fields_balance = {
        "total_liabilities_pct": "Total Liabilities",
        "total_equity_pct": "Total Equity",
        "cash_and_equivalents_pct": "Cash",
        "current_assets_pct": "Current Assets",
        "current_liabilities_pct": "Current Liabilities",
        "long_term_debt_pct": "Long-term Debt",
    }
    cs_balance_lines = []
    for key, label in cs_fields_balance.items():
        val = cs_balance.get(key)
        if val is not None:
            cs_balance_lines.append(f"  {label}: {_fmt(val, pct=True)}")
    if cs_balance_lines:
        lines.append("BALANCE SHEET STRUCTURE (% of Total Assets)")
        lines.extend(cs_balance_lines)
        lines.append("")

    # ── Legend ────────────────────────────────────────────────────────────
    lines.append("LEGEND: ↑ improving | → stable | ↓ deteriorating | ? insufficient history")

    return "\n".join(lines)
