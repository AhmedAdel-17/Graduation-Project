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
    freq: str = "annual",
    prior_memory_context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Assemble a structured evidence pack from a deterministic FundamentalAnalysisReport.

    Args:
      freq: "annual" or "quarterly". Controls how growth signals are labeled
            and which comparison is treated as primary in the narrative.
            Annual → YoY is primary. Quarterly → sequential QoQ is primary.
      prior_memory_context: Optional Phase 3 context. Memory is prompt context
            only and must not overwrite deterministic evidence fields.

    The evidence pack contains all computed data in two forms:
      - Structured dict (for downstream parsing and Stage 3 JSON building)
      - Formatted narrative string (for LLM consumption in Stage 2/3 prompts)

    Returns:
      dict with keys:
        ticker, fiscal_period, sector, sector_context,
        ratios, directions, distress_flags,
        data_confidence, signal_coherence,
        freq,
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
        "freq": freq,
        "revenue_growth_yoy": preprocessing.get("revenue_growth_yoy"),
        "net_income_growth_yoy": preprocessing.get("net_income_growth_yoy"),
        "common_size_income": common_size_income,
        "common_size_balance": common_size_balance,
        "piotroski_score": ratios.get("piotroski_score"),
        "financial_health_heuristic": report.financial_health,
        "pe_ratio_source": getattr(report, "pe_ratio_source", ""),
        "risk_free_rate_source": getattr(report, "risk_free_rate_source", ""),
        "risk_free_rate_effective_date": getattr(report, "risk_free_rate_effective_date", ""),
        "supplemental_context": getattr(report, "supplemental_context", {}) or {},
    }
    if prior_memory_context:
        pack["prior_memory_context"] = prior_memory_context

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
    freq = pack.get("freq", "annual")
    is_quarterly = (freq == "quarterly")
    rev_growth = pack.get("revenue_growth_yoy")
    ni_growth = pack.get("net_income_growth_yoy")
    cs_income = pack.get("common_size_income", {})
    cs_balance = pack.get("common_size_balance", {})
    piotroski = pack.get("piotroski_score")
    health_heuristic = pack.get("financial_health_heuristic", "")
    prior_memory_context = pack.get("prior_memory_context")
    supplemental_context = pack.get("supplemental_context") or {}

    lines: List[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines.append(f"EVIDENCE PACK — {ticker} | {fiscal}")
    lines.append(f"Sector: {sector.upper()} | Analysis Date: {pack.get('analysis_date', '')}")
    lines.append(f"Data Confidence: {dc}/100 | Signal Coherence: {sc}/100")
    lines.append(f"Deterministic Health Heuristic: {health_heuristic}")
    if is_quarterly:
        lines.append(
            "ANALYSIS MODE: QUARTERLY — prediction horizon is next quarter vs current quarter (QoQ). "
            "Growth signals below show sequential quarter-over-quarter changes, NOT year-over-year. "
            "Do NOT use these as YoY signals."
        )
    else:
        lines.append("ANALYSIS MODE: ANNUAL — prediction horizon is next year vs current year (YoY).")
    lines.append("")

    # ── Phase 3 Memory Context ───────────────────────────────────────────────
    if prior_memory_context:
        lines.append(str(prior_memory_context).strip())
        lines.append("")

    # ── Distress Flags ─────────────────────────────────────────────────────
    lines.append("DISTRESS FLAGS")
    if flags:
        for f in flags:
            lines.append(f"  [!] {f}")
    else:
        lines.append("  none")
    lines.append("")

    # ── Supplemental Context ───────────────────────────────────────────────
    if supplemental_context:
        lines.append("SUPPLEMENTAL FORWARD-LOOKING CONTEXT")
        missing = supplemental_context.get("missing_categories") or []
        if missing:
            lines.append(
                "  Missing supplemental categories: "
                + ", ".join(str(item) for item in missing)
            )
        valuation = supplemental_context.get("valuation_context") or {}
        if valuation:
            lines.append(
                "  Valuation context: "
                f"price={_fmt(valuation.get('close_price'))}, "
                f"P/E={_fmt(valuation.get('pe_ratio'))}, "
                f"P/B={_fmt(valuation.get('pb_ratio'))}, "
                f"dividend_yield={_fmt(valuation.get('dividend_yield'), pct=True)}"
            )
        quality = supplemental_context.get("quality_of_earnings") or {}
        if quality:
            lines.append(
                "  Quality of earnings: "
                f"OCF={_fmt(quality.get('operating_cash_flow'))}, "
                f"FCF={_fmt(quality.get('free_cash_flow'))}, "
                f"interest_expense={_fmt(quality.get('interest_expense'))}, "
                f"one_off_gains_losses={_fmt(quality.get('one_off_gains_losses'))}"
            )
            if quality.get("one_off_description"):
                lines.append(f"  One-off description: {quality.get('one_off_description')}")
        narrative = supplemental_context.get("narrative_events") or {}
        if narrative:
            if narrative.get("management_guidance"):
                lines.append(f"  Management guidance: {narrative.get('management_guidance')}")
            if narrative.get("event_flags"):
                lines.append(f"  Event flags: {narrative.get('event_flags')}")
            if narrative.get("one_off_event_notes"):
                lines.append(f"  Event notes: {narrative.get('one_off_event_notes')}")
        macro = supplemental_context.get("macro_sector_context") or {}
        if macro:
            lines.append(
                "  Macro/sector context: "
                f"inflation={_fmt(macro.get('inflation_yoy'), pct=True)}, "
                f"policy_rate={_fmt(macro.get('policy_rate'), pct=True)}, "
                f"EGP/USD change={_fmt(macro.get('egp_usd_change_yoy'), pct=True)}"
            )
            if macro.get("commodity_context"):
                lines.append(f"  Commodity context: {macro.get('commodity_context')}")
            if macro.get("sector_cycle_notes"):
                lines.append(f"  Sector cycle notes: {macro.get('sector_cycle_notes')}")
        lines.append(
            "  NOTE: Supplemental context is externally supplied and must be source-audited; "
            "it can inform interpretation but must not overwrite deterministic statement values."
        )
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
    gpa = ratios.get("gross_profit_to_assets")
    lines.append(
        f"  Gross Margin: {_fmt(gm, pct=True)} {_direction_arrow(directions.get('gross_margin', '?'))} | "
        f"Operating Margin: {_fmt(om, pct=True)} {_direction_arrow(directions.get('operating_margin', '?'))} | "
        f"Net Margin: {_fmt(nm, pct=True)} {_direction_arrow(directions.get('net_margin', '?'))}"
    )
    lines.append(
        f"  GP/A (Gross Profit to Assets): {_fmt(gpa, pct=True)}"
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
    pe_source = pack.get("pe_ratio_source", "")
    # Annotate P/E with its source so analysts know if the value is stale
    pe_annotation = (
        "[live price]" if pe_source == "trade_date_price"
        else "[stale CSV]" if pe_source == "csv_fallback"
        else "[N/A]"
    )
    lines.append(
        f"  P/E: {_fmt(pe)} {pe_annotation} | P/B: {_fmt(pb)} | EPS: {_fmt(eps)}"
    )
    rfr_source = pack.get("risk_free_rate_source", "")
    rfr_eff_date = pack.get("risk_free_rate_effective_date", "")
    if ey_spread is not None:
        if rfr_source == "date_aware_cbe_policy_rate":
            rfr_annotation = f"[date-aware CBE: effective {rfr_eff_date}]"
        elif rfr_source == "static_config_fallback":
            rfr_annotation = "[static config — may be anachronistic for this backtest date]"
        else:
            rfr_annotation = ""
        ey_spread_str = f"{_fmt(ey_spread, pct=True)} {rfr_annotation}".strip()
    else:
        ey_spread_str = "N/A (risk_free_rate not configured)"
    lines.append(
        f"  Earnings Yield: {_fmt(ey, pct=True)} | EY Spread vs Risk-Free: {ey_spread_str}"
    )
    lines.append(f"  Dividend Yield: {_fmt(div_yield, pct=True)}")
    lines.append("")

    # ── Growth ────────────────────────────────────────────────────────────
    if is_quarterly:
        lines.append("GROWTH SIGNALS (QUARTERLY MODE — all rates are QoQ: current quarter vs prior quarter)")
        rev_label = "Revenue QoQ"
        ni_label  = "Net Income QoQ"
        lines.append(
            f"  {rev_label}: {_fmt(rev_growth, pct=True)} {_direction_arrow(directions.get('revenue', '?'))} | "
            f"{ni_label}: {_fmt(ni_growth, pct=True)} {_direction_arrow(directions.get('net_income', '?'))}"
        )
        # Add interpretation note when QoQ is extreme (sign of near-zero crossing)
        if ni_growth is not None and abs(ni_growth) > 2.0:
            lines.append(
                f"  [NOTE] Net Income QoQ magnitude > 200% — likely reflects a near-zero crossing "
                f"(e.g. trough-to-recovery or peak-to-loss). Extrapolating this rate forward is unreliable; "
                f"use trend direction and margin signals as primary context."
            )
        elif ni_growth is None:
            lines.append(
                "  [NOTE] Net Income QoQ: unavailable — no prior quarter data in scope. "
                "Use trend directions and margin levels as primary signals. Reduce confidence."
            )
    else:
        lines.append("GROWTH SIGNALS (ANNUAL MODE — all rates are YoY: current year vs prior year)")
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
