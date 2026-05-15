"""
Fundamental Analyst for EGX TradingAgents pipeline.

This module provides two analyst factories:
  create_fundamentals_analyst(llm)
      — LLM-based analyst (used for non-EGX markets, passes tools to LLM)

  create_deterministic_fundamentals_analyst()
      — Sector-aware deterministic analyst for EGX (no LLM call)
      — Uses the new fundamentals/ module for all computation
      — Output schema: FundamentalAnalysisReport (Pydantic)
      — Backward-compatible: returns the same state keys as before

The LLM-based analyst is unchanged from the prior version.
The deterministic analyst is a full rebuild addressing:
  1. Sector blindness: 4-sector design (banks, real_estate, holdings, operational)
  2. Magic number thresholds: removed; replaced with safety floors only
  3. Conflated confidence: 3 separate scores (data_confidence, signal_coherence, distress_flags)
  4. Broken calculate_confidence_score: replaced entirely

Plan reference: /Users/mennaazazy/.claude/plans/floofy-humming-tide.md
"""
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import json
import re
from typing import Dict, Any, Optional, List

from tradingagents.agents.utils.agent_utils import (
    get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement
)
from tradingagents.agents.utils.fundamental_data_tools import (
    get_egx_fundamentals, get_egx_income, get_egx_balance, get_egx_ratios
)
from tradingagents.dataflows.config import get_config

from .fundamentals.schemas import FundamentalAnalysisReport
from .fundamentals.financial_calculator import FinancialCalculator
from .fundamentals.statement_standardizer import StatementStandardizer
from .fundamentals.sector_config import SectorConfig
from .fundamentals.scoring import (
    compute_data_confidence,
    estimate_periods_since_filing,
    determine_financial_health_heuristic,
)
from .fundamentals.data_loader import load_multi_period
from .fundamentals.pipeline import run_cot_pipeline
from .fundamentals.rate_lookup import get_egx_risk_free_rate_as_of
from tradingagents.dataflows.macro_provider import format_macro_context_for_prompt


# =============================================================================
# LLM-Based Analyst (unchanged — used for non-EGX markets)
# =============================================================================

def create_fundamentals_analyst(llm):
    """
    LLM-based Fundamental Analyst ("Accountant") agent.
    Used for non-EGX markets. Unchanged from prior version.
    """

    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]

        config = get_config()
        target_market = config.get("target_market", "US")

        if target_market == "EGX":
            tools = [get_egx_fundamentals, get_egx_income, get_egx_balance, get_egx_ratios]
        else:
            tools = [get_fundamentals, get_income_statement, get_balance_sheet, get_cashflow]

        system_message = f"""You are a Fundamental Analyst ("Accountant") specializing in {target_market} companies.

## Your Role
Analyze company financial statements and provide a structured fundamental assessment.

## CRITICAL RULES
1. Provide VALUATION RANGES, not point estimates
2. If data is incomplete, REDUCE your confidence score explicitly
3. Always identify key risks specific to the company
4. Once you have received the data from your tools, output your final report and the structured JSON object directly.

## Required Output Format
{{{{
    "financial_health": "string (Strong/Moderate/Weak)",
    "valuation_gap": "string (Undervalued/Fair/Overvalued)",
    "fair_value_range": "string (e.g., 45-55)",
    "key_risks": ["risk1", "risk2"],
    "confidence_score": "number (0-100)",
    "data_completeness": "number (0-100)",
    "reasoning": "string (brief summary)"
}}}}
"""
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_message),
            MessagesPlaceholder(variable_name="messages"),
        ])

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke({
            "messages": state.get("fundamentals_messages") or [("human", ticker)]
        })

        if len(result.tool_calls) > 0:
            return {"fundamentals_messages": [result]}

        report = result.content
        structured_analysis = None

        try:
            json_match = re.search(r'\{.*\}', report, re.DOTALL)
            if json_match:
                structured_analysis = json.loads(json_match.group(0))
        except Exception:
            pass

        if not structured_analysis:
            structured_analysis = {
                "financial_health": "Unknown",
                "valuation_gap": "Unknown",
                "fair_value_range": "N/A",
                "key_risks": ["Analysis failed or data missing"],
                "confidence_score": 0,
                "data_completeness": 0,
                "reasoning": "Model did not provide structured output",
            }

        return {
            "fundamentals_messages": [result],
            "fundamentals_report": report,
            "fundamental_analysis": structured_analysis,
        }

    return fundamentals_analyst_node


# =============================================================================
# Deterministic Analyst — EGX (full rebuild)
# =============================================================================

def create_deterministic_fundamentals_analyst():
    """
    Sector-aware deterministic Fundamental Analyst for EGX.
    No LLM calls. Output is identical in state schema to create_fundamentals_analyst.

    Architecture (Phase 1A):
      1. Load multi-period data (data_loader.py)
      2. Compute ratios (financial_calculator.py)
      3. Standardize statements (statement_standardizer.py)
      4. Apply sector config and generate alerts (sector_config.py)
      5. Score data quality (scoring.py)
      6. Pack FundamentalAnalysisReport (schemas.py)
      7. Emit backward-compatible state keys
    """

    def deterministic_fundamentals_analyst_node(state):
        trade_date = state["trade_date"]
        ticker = state["company_of_interest"]

        # ── 1. Load multi-period data ──────────────────────────────────────────
        multi = load_multi_period(ticker, curr_date=trade_date, n_periods=5, freq="annual")

        income_periods = multi["income"]   # list of dicts, most-recent first
        balance_periods = multi["balance"]
        ratios_periods = multi["ratios"]

        # Current period (most recent)
        cur_income = income_periods[0] if income_periods else {}
        cur_balance = balance_periods[0] if balance_periods else {}
        cur_ratios = ratios_periods[0] if ratios_periods else {}

        # Prior period (second most recent, for YoY)
        prior_income = income_periods[1] if len(income_periods) > 1 else None
        prior_balance = balance_periods[1] if len(balance_periods) > 1 else None
        prior_ratios = ratios_periods[1] if len(ratios_periods) > 1 else None

        # ── 2. Sector classification ───────────────────────────────────────────
        sector_cfg = SectorConfig(ticker)
        sector = sector_cfg.sector
        fiscal_period = cur_income.get("_period_end_date") or cur_balance.get("_period_end_date") or trade_date

        # ── 3. Compute ratios ─────────────────────────────────────────────────
        # Extract raw values from current period
        revenue = cur_income.get("revenue")
        gross_profit = cur_income.get("gross_profit")
        operating_income = cur_income.get("operating_income")
        net_income = cur_income.get("net_income")
        total_assets = cur_balance.get("total_assets")
        total_liabilities = cur_balance.get("total_liabilities")
        total_equity = cur_balance.get("total_equity")
        current_assets = cur_balance.get("current_assets")
        current_liabilities = cur_balance.get("current_liabilities")
        shares_outstanding = cur_balance.get("shares_outstanding")

        # Piotroski prior-period comparison inputs
        roa_prior = FinancialCalculator.roa(
            prior_income.get("net_income") if prior_income else None,
            prior_balance.get("total_assets") if prior_balance else None,
        )
        gm_prior = FinancialCalculator.gross_margin(
            prior_income.get("gross_profit") if prior_income else None,
            prior_income.get("revenue") if prior_income else None,
        )
        at_prior = FinancialCalculator.asset_turnover(
            prior_income.get("revenue") if prior_income else None,
            prior_balance.get("total_assets") if prior_balance else None,
        )
        de_prior = FinancialCalculator.debt_to_equity(
            prior_balance.get("total_liabilities") if prior_balance else None,
            prior_balance.get("total_equity") if prior_balance else None,
        )
        cr_prior = cur_ratios.get("current_ratio") if prior_ratios is None else prior_ratios.get("current_ratio")
        shares_prior = prior_balance.get("shares_outstanding") if prior_balance else None

        cfg = get_config()
        # Date-aware risk-free rate: looks up the CBE policy rate effective as of
        # trade_date. Falls back to static config if no CSV data is available.
        # Temporal safety: the lookup never returns a rate whose effective_date
        # is after trade_date, preventing lookahead bias in backtests.
        risk_free_rate, rfr_source, rfr_effective_date = get_egx_risk_free_rate_as_of(
            trade_date=trade_date,
            config=cfg,
        )
        # Treat state current_price=0 as unavailable (0 is the propagation.py sentinel)
        current_price = state.get("current_price") or None

        ratios_raw = FinancialCalculator.compute_all(
            revenue=revenue,
            gross_profit=gross_profit,
            operating_income=operating_income,
            net_income=net_income,
            total_assets=total_assets,
            total_liabilities=total_liabilities,
            total_equity=total_equity,
            current_assets=current_assets,
            current_liabilities=current_liabilities,
            shares_outstanding=shares_outstanding,
            # CSV-provided fallback values
            eps_csv=cur_ratios.get("eps"),
            pe_ratio_csv=cur_ratios.get("pe_ratio"),
            pb_ratio_csv=cur_ratios.get("price_to_book"),
            current_ratio_csv=cur_ratios.get("current_ratio"),
            roe_csv=cur_ratios.get("roe"),
            roa_csv=cur_ratios.get("roa"),
            gross_margin_csv=cur_ratios.get("gross_margin"),
            operating_margin_csv=cur_ratios.get("operating_margin"),
            net_margin_csv=cur_ratios.get("net_margin"),
            book_value_per_share=cur_ratios.get("book_value_per_share"),
            dividend_yield_csv=cur_ratios.get("dividend_yield"),
            # Market data: wired from state and config
            current_price=current_price,
            risk_free_rate=risk_free_rate,
            roa_prior=roa_prior,
            gross_margin_prior=gm_prior,
            asset_turnover_prior=at_prior,
            leverage_prior=de_prior,
            current_ratio_prior=cr_prior,
            shares_prior=shares_prior,
        )

        # Extract internal metadata before stripping
        pe_ratio_source: str = ratios_raw.get("_pe_ratio_source", "unavailable")

        # Strip internal metadata keys for the public ratios dict
        public_ratios: Dict[str, Optional[float]] = {
            k: v for k, v in ratios_raw.items()
            if not k.startswith("_")
        }

        # ── 4. Standardize statements ──────────────────────────────────────────
        preprocessing = StatementStandardizer.standardize(
            current_income=cur_income,
            current_balance=cur_balance,
            current_ratios={k: v for k, v in public_ratios.items() if v is not None},
            prior_income=prior_income,
            prior_balance=prior_balance,
            prior_ratios={k: v for k, v in FinancialCalculator.compute_all(
                revenue=prior_income.get("revenue") if prior_income else None,
                gross_profit=prior_income.get("gross_profit") if prior_income else None,
                operating_income=prior_income.get("operating_income") if prior_income else None,
                net_income=prior_income.get("net_income") if prior_income else None,
                total_assets=prior_balance.get("total_assets") if prior_balance else None,
                total_liabilities=prior_balance.get("total_liabilities") if prior_balance else None,
                total_equity=prior_balance.get("total_equity") if prior_balance else None,
                roa_csv=prior_ratios.get("roa") if prior_ratios else None,
                gross_margin_csv=prior_ratios.get("gross_margin") if prior_ratios else None,
                net_margin_csv=prior_ratios.get("net_margin") if prior_ratios else None,
                operating_margin_csv=prior_ratios.get("operating_margin") if prior_ratios else None,
                current_ratio_csv=prior_ratios.get("current_ratio") if prior_ratios else None,
                roe_csv=prior_ratios.get("roe") if prior_ratios else None,
                eps_csv=prior_ratios.get("eps") if prior_ratios else None,
                pe_ratio_csv=prior_ratios.get("pe_ratio") if prior_ratios else None,
                pb_ratio_csv=prior_ratios.get("price_to_book") if prior_ratios else None,
            ).items() if not k.startswith("_")} if prior_income else None,
        )

        # ── 5. Generate distress flags ─────────────────────────────────────────
        nm_val = public_ratios.get("net_margin")
        de_val = public_ratios.get("debt_to_equity")
        cr_val = public_ratios.get("current_ratio")
        roe_val = public_ratios.get("roe")
        eps_val = public_ratios.get("eps")
        pe_val = public_ratios.get("pe_ratio")
        ey_spread = public_ratios.get("earnings_yield_spread")

        distress_flags = sector_cfg.generate_distress_flags(
            net_margin=nm_val,
            debt_to_equity=de_val,
            current_ratio=cr_val,
            total_equity=total_equity,
            revenue=revenue,
            eps=eps_val,
            pe_ratio=pe_val,
            roe=roe_val,
            earnings_yield_spread=ey_spread,
        )

        # ── 6. Compute signal_coherence ────────────────────────────────────────
        signal_coherence, coherence_reasons = FinancialCalculator.compute_signal_coherence(
            ratios=ratios_raw,
            current_ratio_csv=cur_ratios.get("current_ratio"),
            current_assets=current_assets,
            current_liabilities=current_liabilities,
        )

        # ── 7. Compute data_confidence ─────────────────────────────────────────
        n_annual = multi["n_income"]  # income depth as proxy for overall period depth
        periods_since = estimate_periods_since_filing(n_annual, 5)
        data_confidence = compute_data_confidence(
            income_row=cur_income,
            balance_row=cur_balance,
            ratios_row=cur_ratios,
            n_annual_periods=n_annual,
            periods_since_last_filing=periods_since,
        )

        # ── 8. Determine financial_health heuristic ────────────────────────────
        directions = preprocessing.get("directions", {})

        # Guard: if NEGATIVE_EQUITY_ALERT fired, ROE is sign-reversed and
        # cannot be interpreted as a normal positive/negative quality signal.
        # A sign-reversed ROE (both periods negative equity) can flip the heuristic
        # verdict from 'concerning' to 'healthy' — a false positive safety risk.
        # Nullify the ROE direction to prevent contamination.
        if "NEGATIVE_EQUITY_ALERT" in distress_flags:
            directions = {**directions, "roe": "insufficient_history"}

        financial_health = determine_financial_health_heuristic(directions)

        # ── 9. Build key_risks (deterministic: structural EGX risks only) ─────
        key_risks: List[str] = [
            "EGX market structure: limited liquidity, ±10% daily price limits, no short selling",
            "EGP currency exposure: Egyptian Pound volatility affects real returns",
        ]
        for flag in distress_flags:
            if flag in ("NEGATIVE_MARGIN_ALERT", "HIGH_LEVERAGE_ALERT", "LIQUIDITY_EMERGENCY", "NEGATIVE_EQUITY_ALERT"):
                key_risks.insert(0, f"Fundamental risk: {flag.replace('_', ' ').lower()}")

        # ── 10. Assemble FundamentalAnalysisReport ────────────────────────────
        report_obj = FundamentalAnalysisReport(
            ticker=ticker.upper().replace(".CA", ""),
            analysis_date=trade_date,
            fiscal_period=fiscal_period,
            sector=sector,
            ratios=public_ratios,
            preprocessing=preprocessing,
            distress_flags=distress_flags,
            data_confidence=data_confidence,
            signal_coherence=signal_coherence,
            financial_health=financial_health,
            valuation_assessment="",  # Not assessed in deterministic mode
            earnings_direction="",    # Not predicted in deterministic mode
            earnings_direction_confidence=0,
            raw_earnings_direction_confidence=0,
            pe_ratio_source=pe_ratio_source,
            risk_free_rate_value=risk_free_rate,
            risk_free_rate_source=rfr_source,
            risk_free_rate_effective_date=rfr_effective_date or "",
            thesis_text="",           # Empty in deterministic mode
            key_risks=key_risks,
            pipeline_mode="deterministic",
            stages_completed=[],
        )

        # ── 11. Build backward-compatible text report ─────────────────────────
        flags_str = ", ".join(distress_flags) if distress_flags else "none"
        coherence_notes = "; ".join(coherence_reasons) if coherence_reasons else "all checks passed"

        # Macro context: injected by DataPrefetcher before graph runs
        macro_context = state.get("macro_context")
        macro_text = ""
        if macro_context:
            macro_text = "\n" + format_macro_context_for_prompt(macro_context)

        text_report = (
            f"EGX Fundamental Analysis — {ticker} | {fiscal_period}\n"
            f"Sector: {sector} | Financial Health: {financial_health}\n"
            f"Data Confidence: {data_confidence}/100 | Signal Coherence: {signal_coherence}/100\n"
            f"Distress Flags: {flags_str}\n"
            f"Coherence notes: {coherence_notes}\n"
            f"Key Ratios: "
            f"ROE={_fmt(public_ratios.get('roe'))} | "
            f"Net Margin={_fmt(public_ratios.get('net_margin'))} | "
            f"D/E={_fmt(public_ratios.get('debt_to_equity'))} | "
            f"Current={_fmt(public_ratios.get('current_ratio'))} | "
            f"P/E={_fmt(public_ratios.get('pe_ratio'))} | "
            f"P/B={_fmt(public_ratios.get('pb_ratio'))}\n"
            f"Revenue Growth YoY: {_fmt(preprocessing.get('revenue_growth_yoy'), pct=True)}\n"
            f"Key Risks: {'; '.join(key_risks[:3])}"
            f"{macro_text}"
        )

        # ── 12. Build backward-compatible structured_analysis dict ────────────
        # Maps to the same keys the old code emitted so downstream agents are unchanged.
        structured_analysis = {
            # Legacy keys (kept for backward compatibility)
            "financial_health": financial_health,
            "valuation_gap": "not_assessed",
            "fair_value_range": "N/A",
            "key_risks": key_risks,
            "confidence_score": data_confidence,       # maps old "confidence_score"
            "data_completeness": data_confidence,      # maps old "data_completeness"
            "reasoning": (
                f"Sector: {sector} | Health: {financial_health} | "
                f"Flags: {flags_str} | Coherence: {signal_coherence}/100"
            ),
            # New structured fields
            "data_confidence": data_confidence,
            "signal_coherence": signal_coherence,
            "distress_flags": distress_flags,
            "ratios": public_ratios,
            "preprocessing": {
                "revenue_growth_yoy": preprocessing.get("revenue_growth_yoy"),
                "net_income_growth_yoy": preprocessing.get("net_income_growth_yoy"),
                "directions": directions,
            },
            "sector": sector,
            "fiscal_period": fiscal_period,
            "pipeline_mode": "deterministic",
            "pe_ratio_source": pe_ratio_source,
            "risk_free_rate_value": risk_free_rate,
            "risk_free_rate_source": rfr_source,
            "risk_free_rate_effective_date": rfr_effective_date or "",
            # Macro overlay — pass through so downstream agents can reference it
            "macro_context": macro_context,
            # Full Pydantic report serialized for downstream agents that expect it
            "report": report_obj.model_dump(),
        }

        return {
            "fundamentals_report": text_report,
            "fundamental_analysis": structured_analysis,
            "fundamentals_messages": [],
        }

    return deterministic_fundamentals_analyst_node


# =============================================================================
# Hybrid Analyst — EGX (Phase 2A: deterministic foundation + CoT pipeline)
# =============================================================================

def create_hybrid_fundamentals_analyst(quick_thinking_llm, deep_thinking_llm):
    """
    Hybrid Fundamental Analyst for EGX: deterministic foundation + three-stage CoT.

    Architecture:
      1. Runs the full deterministic pipeline (same as create_deterministic_fundamentals_analyst)
      2. Passes the FundamentalAnalysisReport to the CoT pipeline (pipeline.py)
      3. The CoT pipeline enriches: financial_health, thesis_text, earnings_direction,
         valuation_assessment, key_risks using quick_thinking_llm + deep_thinking_llm
      4. Falls back to deterministic output on any CoT failure

    Output state schema is identical to create_deterministic_fundamentals_analyst.
    """

    # Reuse the deterministic node to compute the Phase 1A report
    _deterministic_node = create_deterministic_fundamentals_analyst()

    def hybrid_fundamentals_analyst_node(state):
        # Step 1: Run deterministic pipeline
        det_result = _deterministic_node(state)

        # Extract the structured FundamentalAnalysisReport from deterministic output
        structured = det_result.get("fundamental_analysis", {})
        report_dict = structured.get("report")

        if report_dict is None:
            # Deterministic pipeline produced no report — return as-is
            return det_result

        try:
            report_obj = FundamentalAnalysisReport(**report_dict)
        except Exception:
            # Schema deserialization failed — return deterministic result
            return det_result

        # Step 2: Run CoT pipeline
        ticker = state["company_of_interest"]
        sector_cfg = SectorConfig(ticker)
        config = get_config()

        try:
            enriched_report = run_cot_pipeline(
                quick_llm=quick_thinking_llm,
                deep_llm=deep_thinking_llm,
                report=report_obj,
                sector_cfg=sector_cfg,
                freq="annual",
                use_memory=config.get("use_fundamental_memory", False),
                source_run_id=f"{ticker}-{state['trade_date']}-fundamentals",
            )
        except Exception:
            # CoT pipeline crashed entirely — return deterministic result
            return det_result

        # Step 3: Rebuild state outputs from enriched report
        trade_date = state["trade_date"]
        fiscal_period = enriched_report.fiscal_period
        sector = enriched_report.sector
        public_ratios = enriched_report.ratios
        preprocessing = enriched_report.preprocessing
        distress_flags = enriched_report.distress_flags
        data_confidence = enriched_report.data_confidence
        signal_coherence = enriched_report.signal_coherence
        financial_health = enriched_report.financial_health
        thesis_text = enriched_report.thesis_text
        earnings_direction = enriched_report.earnings_direction
        earnings_direction_confidence = enriched_report.earnings_direction_confidence
        raw_earnings_direction = enriched_report.raw_earnings_direction
        calibrated_earnings_direction = enriched_report.calibrated_earnings_direction
        fundamental_outlook = enriched_report.fundamental_outlook
        downside_risk_level = enriched_report.downside_risk_level
        calibration_policy = enriched_report.calibration_policy
        signal_calibration_notes = enriched_report.signal_calibration_notes
        valuation_assessment = enriched_report.valuation_assessment
        key_risks = enriched_report.key_risks
        pipeline_mode = enriched_report.pipeline_mode
        stages_completed = enriched_report.stages_completed

        flags_str = ", ".join(distress_flags) if distress_flags else "none"
        directions = preprocessing.get("directions", {})

        # Build enriched text report
        thesis_section = (
            f"\nInvestment Thesis: {thesis_text}"
            if thesis_text else ""
        )
        earnings_section = (
            f"\nEarnings Direction: {earnings_direction} "
            f"(confidence: {earnings_direction_confidence}/100)"
            if earnings_direction else ""
        )
        valuation_section = (
            f"\nValuation: {valuation_assessment}"
            if valuation_assessment else ""
        )

        text_report = (
            f"EGX Fundamental Analysis — {ticker} | {fiscal_period}\n"
            f"Sector: {sector} | Financial Health: {financial_health}\n"
            f"Data Confidence: {data_confidence}/100 | Signal Coherence: {signal_coherence}/100\n"
            f"Distress Flags: {flags_str}\n"
            f"Pipeline Mode: {pipeline_mode} | Stages: {', '.join(stages_completed) or 'none'}\n"
            f"Key Ratios: "
            f"ROE={_fmt(public_ratios.get('roe'))} | "
            f"Net Margin={_fmt(public_ratios.get('net_margin'))} | "
            f"D/E={_fmt(public_ratios.get('debt_to_equity'))} | "
            f"Current={_fmt(public_ratios.get('current_ratio'))} | "
            f"P/E={_fmt(public_ratios.get('pe_ratio'))} | "
            f"P/B={_fmt(public_ratios.get('pb_ratio'))}\n"
            f"Revenue Growth YoY: {_fmt(preprocessing.get('revenue_growth_yoy'), pct=True)}"
            f"{valuation_section}"
            f"{earnings_section}"
            f"{thesis_section}\n"
            f"Key Risks: {'; '.join(key_risks[:3])}"
        )

        structured_analysis = {
            # Legacy keys (backward compatibility)
            "financial_health": financial_health,
            "valuation_gap": valuation_assessment or "not_assessed",
            "fair_value_range": "N/A",
            "key_risks": key_risks,
            "confidence_score": data_confidence,
            "data_completeness": data_confidence,
            "reasoning": (
                f"Sector: {sector} | Health: {financial_health} | "
                f"Flags: {flags_str} | Coherence: {signal_coherence}/100 | "
                f"Pipeline: {pipeline_mode}"
            ),
            # New structured fields
            "data_confidence": data_confidence,
            "signal_coherence": signal_coherence,
            "distress_flags": distress_flags,
            "ratios": public_ratios,
            "preprocessing": {
                "revenue_growth_yoy": preprocessing.get("revenue_growth_yoy"),
                "net_income_growth_yoy": preprocessing.get("net_income_growth_yoy"),
                "directions": directions,
            },
            "sector": sector,
            "fiscal_period": fiscal_period,
            "pipeline_mode": pipeline_mode,
            "stages_completed": stages_completed,
            "thesis_text": thesis_text,
            "earnings_direction": earnings_direction,
            "earnings_direction_confidence": earnings_direction_confidence,
            "raw_earnings_direction_confidence": enriched_report.raw_earnings_direction_confidence,
            "raw_earnings_direction": raw_earnings_direction,
            "calibrated_earnings_direction": calibrated_earnings_direction,
            "fundamental_outlook": fundamental_outlook,
            "downside_risk_level": downside_risk_level,
            "calibration_policy": calibration_policy,
            "signal_calibration_notes": signal_calibration_notes,
            "valuation_assessment": valuation_assessment,
            "pe_ratio_source": getattr(enriched_report, "pe_ratio_source", ""),
            "risk_free_rate_value": getattr(enriched_report, "risk_free_rate_value", None),
            "risk_free_rate_source": getattr(enriched_report, "risk_free_rate_source", ""),
            "risk_free_rate_effective_date": getattr(enriched_report, "risk_free_rate_effective_date", ""),
            "report": enriched_report.model_dump(),
        }

        return {
            "fundamentals_report": text_report,
            "fundamental_analysis": structured_analysis,
            "fundamentals_messages": [],
        }

    return hybrid_fundamentals_analyst_node


def _fmt(val: Optional[float], pct: bool = False) -> str:
    """Format a float or None for display."""
    if val is None:
        return "N/A"
    if pct:
        return f"{val:+.1%}"
    if abs(val) >= 1000:
        return f"{val:,.0f}"
    return f"{val:.3f}"
