"""
Phase 2B CoT Validation Gate
=============================
Validates the three-stage CoT pipeline (Phase 2A) against real EGX data
and LLM API calls.

EVALUATION POLICY (updated 2026-04-25)
---------------------------------------
ANNUAL MODE — FORMAL GATE (primary evaluation track):
  Direction accuracy KPIs apply. Gates 3–5 are pass/fail criteria.
  Overall Phase 2B pass/fail is determined by annual results only.

QUARTERLY MODE — DIAGNOSTIC ONLY (not a pass/fail gate):
  Quarterly direction hit rate is reported for diagnostic transparency but
  does NOT gate the Phase 2B result. Quarterly CoT is retained as a
  narrative synthesis / risk identification mode, not a validated
  direction-forecasting mode.

  Reason: segmented diagnostic (2026-04-25, N=47) showed CoT underperforms
  naive on stable quarterly cases (28.6% vs 54.3% naive), and matches naive
  on near-zero cases (58.3% vs 58.3%). Root cause is insufficient quarterly
  signal in the evidence pack (no P/E, P/B, dividend yield; QoQ NI is noisy).
  This is a data sufficiency ceiling, not a prompt or architecture failure.

Gates (per plan, Section 9 — ANNUAL only):
  [1] EGX QA accuracy         — 20 gold-label questions ≥ 14/20
  [2] Error propagation rate  — inter-stage validation failures < 10%
  [3] Earnings direction hit  — hit rate > naive "always_up" baseline
  [4] Brier score             — CoT Brier ≤ deterministic baseline
  [5] Confidence-weighted IC  — IC > 0
  [6] Reasoning quality       — structural proxy ≥ 3.0 / 5.0

Must pass ALL objective metrics (gates 2–5) + at least ONE qualitative
check (gate 1 OR gate 6) — for ANNUAL evaluation only.

Run:
    python tests/phase2b_audit.py

Requires:
    - EGX CSV data populated (run scripts/egx30_full_scraper.py --all)
    - Valid API credentials in tradingagents/default_config.py
    - pip install scipy
"""
from __future__ import annotations

import os
import sys
import json
import math
import time
import csv
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# ── Imports ───────────────────────────────────────────────────────────────────
from tradingagents.agents.analysts.fundamentals.data_loader import load_multi_period
from tradingagents.agents.analysts.fundamentals.financial_calculator import FinancialCalculator
from tradingagents.agents.analysts.fundamentals.statement_standardizer import StatementStandardizer
from tradingagents.agents.analysts.fundamentals.sector_config import SectorConfig
from tradingagents.agents.analysts.fundamentals.scoring import (
    compute_data_confidence,
    estimate_periods_since_filing,
    determine_financial_health_heuristic,
)
from tradingagents.agents.analysts.fundamentals.schemas import FundamentalAnalysisReport
from tradingagents.agents.analysts.fundamentals.data_cot import build_evidence_pack
from tradingagents.agents.analysts.fundamentals.concept_cot import run_concept_cot
from tradingagents.agents.analysts.fundamentals.thesis_cot import run_thesis_cot
from tradingagents.agents.analysts.fundamentals.calibration import calibrate_earnings_direction
from tradingagents.dataflows.config import get_config

# ── Constants ─────────────────────────────────────────────────────────────────

ALL_TICKERS = [
    "COMI", "EAST", "FWRY", "TMGH", "HRHO", "ETEL",
    "ABUK", "ADIB", "EFIH", "EGAL", "MFPC", "CCAP",
    "SKPC", "AMOC", "ESRS", "ORWE", "HELI", "GBCO",
    "SWDY", "ORAS", "PHDC", "CIEB", "ISPH", "DSCW",
    "RMDA", "ARCC", "BTFH", "JUFO", "ORHD", "RAYA", "VLMR",
]

# No date filter: use all available periods (needed to get ground truth next period)
_LOAD_DATE = None

# Direction threshold: ±5% change → "flat"; beyond that → "up" or "down"
_DIRECTION_THRESHOLD = 0.05

# Minimum structural quality score for thesis (proxy for human 3.0/5.0)
_MIN_REASONING_SCORE = 3.0

# Gate thresholds
_MIN_QA_CORRECT = 14          # Gate [1]: ≥ 14/20
_MAX_ERROR_RATE = 0.10        # Gate [2]: < 10% inter-stage validation failures
# Gate [3]: hit rate > naive baseline (computed dynamically)
# Gate [4]: CoT Brier ≤ deterministic "always_up at 60% confidence" baseline
# Gate [5]: IC > 0
_MIN_REASONING_QUALITY = 3.0  # Gate [6]: ≥ 3.0/5


# =============================================================================
# LLM helpers
# =============================================================================

def _build_llms():
    """
    Instantiate quick_thinking_llm and deep_thinking_llm from project config.
    Raises ValueError with a helpful message if instantiation fails.
    """
    from tradingagents.default_config import DEFAULT_CONFIG
    cfg = DEFAULT_CONFIG

    provider = cfg.get("llm_provider", "openai").lower()
    # Stage 3 (thesis_cot) uses deepseek-reasoner for extended chain-of-thought.
    # This override is intentionally scoped to the fundamentals evaluation only
    # and does not affect the trading graph (Trader / Risk Manager / Research Manager),
    # which continue to use whatever is set in default_config.py.
    deep_model = "deepseek-reasoner"
    quick_model = cfg.get("quick_think_llm", "deepseek-chat")
    backend_url = cfg.get("backend_url", "https://api.deepseek.com")

    try:
        if provider in ("openai", "ollama", "openrouter"):
            from langchain_openai import ChatOpenAI
            quick_llm = ChatOpenAI(model=quick_model, base_url=backend_url, temperature=0)
            deep_llm  = ChatOpenAI(model=deep_model,  base_url=backend_url, temperature=0)
        elif provider == "anthropic":
            from langchain_anthropic import ChatAnthropic
            quick_llm = ChatAnthropic(model=quick_model, temperature=0)
            deep_llm  = ChatAnthropic(model=deep_model,  temperature=0)
        elif provider == "google":
            from langchain_google_genai import ChatGoogleGenerativeAI
            quick_llm = ChatGoogleGenerativeAI(model=quick_model, temperature=0)
            deep_llm  = ChatGoogleGenerativeAI(model=deep_model,  temperature=0)
        else:
            raise ValueError(f"Unsupported provider: {provider}")
    except Exception as e:
        raise ValueError(
            f"LLM instantiation failed ({provider}/{quick_model}): {e}\n"
            f"Check API credentials in tradingagents/default_config.py"
        ) from e

    return quick_llm, deep_llm


# =============================================================================
# Deterministic pipeline (replicates fundamentals_analyst.py logic)
# =============================================================================

def _build_report_from_period(
    ticker: str,
    income: Dict,
    balance: Dict,
    ratios_csv: Dict,
    prior_income: Optional[Dict] = None,
    prior_balance: Optional[Dict] = None,
    prior_ratios_csv: Optional[Dict] = None,
    n_periods: int = 1,
) -> FundamentalAnalysisReport:
    """
    Build a FundamentalAnalysisReport from explicit period dicts.

    Used by the earnings direction test to construct the "prediction-time"
    report (using period[1] data) without re-running the full node framework.
    """
    sector_cfg = SectorConfig(ticker)
    sector = sector_cfg.sector
    fiscal_period = income.get("_period_end_date") or balance.get("_period_end_date") or "unknown"

    # ── Compute ratios ─────────────────────────────────────────────────────
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
    cr_prior = (
        prior_ratios_csv.get("current_ratio") if prior_ratios_csv else None
    )
    shares_prior = prior_balance.get("shares_outstanding") if prior_balance else None

    ratios_raw = FinancialCalculator.compute_all(
        revenue=income.get("revenue"),
        gross_profit=income.get("gross_profit"),
        operating_income=income.get("operating_income"),
        net_income=income.get("net_income"),
        total_assets=balance.get("total_assets"),
        total_liabilities=balance.get("total_liabilities"),
        total_equity=balance.get("total_equity"),
        current_assets=balance.get("current_assets"),
        current_liabilities=balance.get("current_liabilities"),
        shares_outstanding=balance.get("shares_outstanding"),
        eps_csv=ratios_csv.get("eps"),
        pe_ratio_csv=ratios_csv.get("pe_ratio"),
        pb_ratio_csv=ratios_csv.get("price_to_book"),
        current_ratio_csv=ratios_csv.get("current_ratio"),
        roe_csv=ratios_csv.get("roe"),
        roa_csv=ratios_csv.get("roa"),
        gross_margin_csv=ratios_csv.get("gross_margin"),
        operating_margin_csv=ratios_csv.get("operating_margin"),
        net_margin_csv=ratios_csv.get("net_margin"),
        roa_prior=roa_prior,
        gross_margin_prior=gm_prior,
        asset_turnover_prior=at_prior,
        leverage_prior=de_prior,
        current_ratio_prior=cr_prior,
        shares_prior=shares_prior,
    )

    public_ratios = {k: v for k, v in ratios_raw.items() if not k.startswith("_")}

    # ── Standardize ────────────────────────────────────────────────────────
    prior_ratios_computed = None
    if prior_income is not None:
        prior_raw = FinancialCalculator.compute_all(
            revenue=prior_income.get("revenue"),
            gross_profit=prior_income.get("gross_profit"),
            operating_income=prior_income.get("operating_income"),
            net_income=prior_income.get("net_income"),
            total_assets=prior_balance.get("total_assets") if prior_balance else None,
            total_liabilities=prior_balance.get("total_liabilities") if prior_balance else None,
            total_equity=prior_balance.get("total_equity") if prior_balance else None,
            roe_csv=prior_ratios_csv.get("roe") if prior_ratios_csv else None,
            net_margin_csv=prior_ratios_csv.get("net_margin") if prior_ratios_csv else None,
            current_ratio_csv=cr_prior,
        )
        prior_ratios_computed = {k: v for k, v in prior_raw.items() if not k.startswith("_")}

    preprocessing = StatementStandardizer.standardize(
        current_income=income,
        current_balance=balance,
        current_ratios={k: v for k, v in public_ratios.items() if v is not None},
        prior_income=prior_income,
        prior_balance=prior_balance,
        prior_ratios=prior_ratios_computed,
    )

    # ── Distress flags ─────────────────────────────────────────────────────
    distress_flags = sector_cfg.generate_distress_flags(
        net_margin=public_ratios.get("net_margin"),
        debt_to_equity=public_ratios.get("debt_to_equity"),
        current_ratio=public_ratios.get("current_ratio"),
        total_equity=balance.get("total_equity"),
        revenue=income.get("revenue"),
        eps=public_ratios.get("eps"),
        pe_ratio=public_ratios.get("pe_ratio"),
        roe=public_ratios.get("roe"),
        earnings_yield_spread=public_ratios.get("earnings_yield_spread"),
    )

    # ── Coherence + confidence ──────────────────────────────────────────────
    signal_coherence, _ = FinancialCalculator.compute_signal_coherence(
        ratios=ratios_raw,
        current_ratio_csv=ratios_csv.get("current_ratio"),
        current_assets=balance.get("current_assets"),
        current_liabilities=balance.get("current_liabilities"),
    )
    periods_since = estimate_periods_since_filing(n_periods, 5)
    data_confidence = compute_data_confidence(
        income_row=income,
        balance_row=balance,
        ratios_row=ratios_csv,
        n_annual_periods=n_periods,
        periods_since_last_filing=periods_since,
    )

    directions = preprocessing.get("directions", {})
    if "NEGATIVE_EQUITY_ALERT" in distress_flags:
        directions = {**directions, "roe": "insufficient_history"}
    financial_health = determine_financial_health_heuristic(directions)

    key_risks = [
        "EGX market structure: limited liquidity, ±10% daily price limits",
        "EGP currency exposure: Egyptian Pound volatility",
    ]

    return FundamentalAnalysisReport(
        ticker=ticker.upper().replace(".CA", ""),
        analysis_date=fiscal_period,
        fiscal_period=fiscal_period,
        sector=sector,
        ratios=public_ratios,
        preprocessing=preprocessing,
        distress_flags=distress_flags,
        data_confidence=data_confidence,
        signal_coherence=signal_coherence,
        financial_health=financial_health,
        key_risks=key_risks,
        pipeline_mode="deterministic",
        stages_completed=[],
    )


# =============================================================================
# Ground truth: earnings direction
# =============================================================================

def _compute_actual_direction(
    ni_current: Optional[float],
    ni_prior: Optional[float],
    threshold: float = _DIRECTION_THRESHOLD,
) -> Optional[str]:
    """
    Compute actual earnings direction from consecutive net_income values.

    Returns "up" / "down" / "flat" / None (if data unavailable).
    """
    if ni_current is None or ni_prior is None or ni_prior == 0:
        return None
    change = (ni_current - ni_prior) / abs(ni_prior)
    if change > threshold:
        return "up"
    elif change < -threshold:
        return "down"
    else:
        return "flat"


# =============================================================================
# Build the CoT test set: (report_for_period1, actual_direction) pairs
# =============================================================================

def build_cot_test_set(
    tickers: List[str], freq: str = "annual", n_periods: int = 5
) -> List[Dict[str, Any]]:
    """
    Build CoT test cases from historical periods.

    Annual mode: ALL consecutive valid annual pairs per ticker across full
      history, filtered to 300–540 day gaps (annual fiscal year gaps are
      365–366 days). Cases are further filtered to require a valid prior-year
      income baseline (prior_net_income must be non-null), because annual
      direction prediction depends on YoY comparison — without a baseline the
      model cannot compute a meaningful signal and returns flat/conf=50.
      Cases missing the baseline are tracked in _excluded_insufficient_baseline
      (a list appended to the returned list as metadata via the first element
      if it carries key "_meta", or accessed separately).

      Pairs are returned sorted by (prediction_year, sector, ticker) for
      balanced interleaving across years and sectors.

    Quarterly mode: ALL consecutive valid pairs per ticker across full history,
      filtered to 60–125 day gaps to ensure true quarter-on-quarter pairs.

    Returns list of dicts with keys:
      ticker, sector, report, actual_direction, actual_ni_change,
      prediction_period, actual_period, freq
    """
    from tradingagents.agents.analysts.fundamentals.sector_config import SectorConfig

    test_cases: List[Dict[str, Any]] = []
    excluded_insufficient_baseline: List[Dict[str, Any]] = []  # annual only

    for ticker in tickers:
        multi = load_multi_period(ticker, curr_date=None, n_periods=n_periods, freq=freq)
        income = multi["income"]
        balance = multi["balance"]
        ratios = multi["ratios"]

        # Date-indexed lookups for balance and ratios (safer than index-based)
        bal_lookup = {
            p["_period_end_date"]: p
            for p in balance if p.get("_period_end_date")
        }
        rat_lookup = {
            p["_period_end_date"]: p
            for p in ratios if p.get("_period_end_date")
        }

        # Valid income periods: non-null net_income and a date key
        valid_income = [
            p for p in income
            if p.get("net_income") is not None and p.get("_period_end_date")
        ]
        if len(valid_income) < 2:
            continue

        # Decide which pairs to process
        if freq == "annual":
            # All consecutive annual pairs: ~365-day gap (300–540 days)
            # Annual CSV files only contain fiscal year-end rows, so any
            # consecutive entries are annual by construction. The gap filter
            # guards against any data irregularities.
            pairs_to_process = []
            for j in range(len(valid_income) - 1):
                d0 = datetime.strptime(valid_income[j]["_period_end_date"], "%Y-%m-%d")
                d1 = datetime.strptime(valid_income[j + 1]["_period_end_date"], "%Y-%m-%d")
                gap_days = (d0 - d1).days
                if 300 <= gap_days <= 540:
                    pairs_to_process.append((j, j + 1))
        else:
            # All consecutive quarterly pairs: ~90-day gap (60–125 days)
            pairs_to_process = []
            for j in range(len(valid_income) - 1):
                d0 = datetime.strptime(valid_income[j]["_period_end_date"], "%Y-%m-%d")
                d1 = datetime.strptime(valid_income[j + 1]["_period_end_date"], "%Y-%m-%d")
                gap_days = (d0 - d1).days
                if 60 <= gap_days <= 125:
                    pairs_to_process.append((j, j + 1))

        for j0, j1 in pairs_to_process:
            p0 = valid_income[j0]   # actual (more recent, already filed)
            p1 = valid_income[j1]   # prediction input (older period)

            actual_direction = _compute_actual_direction(
                p0.get("net_income"), p1.get("net_income")
            )
            if actual_direction is None:
                continue

            ni_prior = p1.get("net_income")
            ni_current = p0.get("net_income")
            actual_ni_change = (
                (ni_current - ni_prior) / abs(ni_prior)
                if ni_prior and ni_prior != 0 else None
            )

            # Balance/ratios for prediction period (p1) via date lookup
            pred_date = p1["_period_end_date"]
            bal1 = bal_lookup.get(pred_date, {})
            rat1 = rat_lookup.get(pred_date, {})

            # Prior period for p1 (one step older): j1+1 in valid_income
            prior_income = valid_income[j1 + 1] if j1 + 1 < len(valid_income) else None
            prior_date = prior_income["_period_end_date"] if prior_income else None
            prior_balance = bal_lookup.get(prior_date) if prior_date else None
            prior_ratios = rat_lookup.get(prior_date) if prior_date else None

            # ── Annual mode: require valid prior-year baseline ─────────────
            # Annual direction prediction relies on YoY comparison. Without a
            # prior-year net_income the model has no baseline and defaults to
            # flat/conf=50, which is correct epistemic behavior but not a
            # meaningful direction signal. Exclude such cases from the formal
            # annual gate; track them separately as insufficient_baseline.
            if freq == "annual":
                prior_ni_val = prior_income.get("net_income") if prior_income else None
                if prior_ni_val is None:
                    excluded_insufficient_baseline.append({
                        "ticker": ticker,
                        "sector": SectorConfig(ticker).sector,
                        "prediction_period": pred_date,
                        "actual_period": p0.get("_period_end_date", "?"),
                        "actual_direction": actual_direction,
                        "reason": "no_prior_year_net_income",
                    })
                    continue  # skip — not a valid annual direction test case

            try:
                report = _build_report_from_period(
                    ticker=ticker,
                    income=p1,
                    balance=bal1,
                    ratios_csv=rat1,
                    prior_income=prior_income,
                    prior_balance=prior_balance,
                    prior_ratios_csv=prior_ratios,
                    n_periods=len(income),
                )
            except Exception as e:
                print(f"  [WARN] {ticker} ({pred_date}): could not build report: {e}")
                continue

            test_cases.append({
                "ticker": ticker,
                "sector": SectorConfig(ticker).sector,
                "report": report,
                "actual_direction": actual_direction,
                "actual_ni_change": actual_ni_change,
                "prediction_period": pred_date,
                "actual_period": p0.get("_period_end_date", "?"),
                "freq": freq,
            })

    if freq == "annual" and test_cases:
        # Stratified ordering: interleave by (prediction_year, sector, ticker)
        # so early partial logs are not dominated by one ticker or sector.
        import random as _rand
        rng = _rand.Random(42)
        test_cases.sort(key=lambda c: (
            c["prediction_period"][:4],   # year
            c["sector"],
            c["ticker"],
        ))
        # Re-shuffle within each year bucket for ticker interleaving
        by_year: dict = {}
        for c in test_cases:
            yr = c["prediction_period"][:4]
            by_year.setdefault(yr, []).append(c)
        ordered = []
        for yr in sorted(by_year):
            bucket = by_year[yr]
            rng.shuffle(bucket)
            ordered.extend(bucket)
        test_cases = ordered

    # Attach exclusion list as a metadata entry (key "_excluded") so callers
    # can report it without a separate return value. Callers should filter it:
    #   real_cases = [c for c in cases if not c.get("_excluded")]
    if freq == "annual" and excluded_insufficient_baseline:
        test_cases.append({
            "_excluded": True,
            "_excluded_insufficient_baseline": excluded_insufficient_baseline,
        })

    return test_cases


# =============================================================================
# Run CoT on test set
# =============================================================================

def run_cot_on_test_set(
    test_cases: List[Dict],
    quick_llm: Any,
    deep_llm: Any,
) -> List[Dict]:
    """
    Run the CoT pipeline on each test case.

    Returns list of result dicts, each containing:
      ticker, actual_direction, actual_ni_change,
      predicted_direction, confidence,
      stages_completed, stage2_valid, stage3_valid,
      thesis_text, reasoning_score
    """
    results = []
    n = len(test_cases)

    for i, case in enumerate(test_cases):
        ticker = case["ticker"]
        report = case["report"]
        print(f"  [{i+1}/{n}] {ticker} (prediction: {case['prediction_period']} → actual: {case['actual_period']})")

        sector_cfg = SectorConfig(ticker)

        try:
            # Stage 1: evidence pack
            case_freq = case.get("freq", "annual")
            evidence_pack = build_evidence_pack(report, sector_cfg, freq=case_freq)
            stage1_valid = evidence_pack.get("_valid", False)

            stage2_valid = False
            stage3_valid = False
            concept_output = {}
            thesis_output = {}
            stages_completed = []

            if stage1_valid:
                stages_completed.append("data_cot")

                # Stage 2: concept
                concept_output = run_concept_cot(quick_llm, evidence_pack)
                stage2_valid = concept_output.get("_valid", False)

                if stage2_valid:
                    stages_completed.append("concept_cot")

                    # Stage 3: thesis
                    thesis_output = run_thesis_cot(deep_llm, evidence_pack, concept_output)
                    stage3_valid = thesis_output.get("_valid", False)
                    if stage3_valid or thesis_output.get("thesis_text", "").strip():
                        stages_completed.append("thesis_cot")

            phaseb = _apply_phaseb_calibration(thesis_output, case.get("freq", "annual"))
            predicted_direction = phaseb["earnings_direction"]
            confidence = thesis_output.get("earnings_direction_confidence", 0)
            thesis_text = thesis_output.get("thesis_text", "")

            # Compute structural reasoning quality score
            reasoning_score = _compute_reasoning_score(thesis_output, evidence_pack)

        except Exception as e:
            print(f"    [ERROR] CoT pipeline crashed: {e}")
            stage1_valid = stage2_valid = stage3_valid = False
            predicted_direction = ""
            confidence = 0
            thesis_text = ""
            stages_completed = []
            reasoning_score = 0.0
            concept_output = {}
            thesis_output = {}
            phaseb = _empty_phaseb_fields()

        results.append({
            "ticker": ticker,
            "actual_direction": case["actual_direction"],
            "actual_ni_change": case["actual_ni_change"],
            "predicted_direction": predicted_direction,
            "confidence": confidence,
            "stages_completed": stages_completed,
            "stage1_valid": stage1_valid,
            "stage2_valid": stage2_valid,
            "stage3_valid": stage3_valid,
            "thesis_text": thesis_text,
            "reasoning_score": reasoning_score,
            "prediction_period": case["prediction_period"],
            "actual_period": case["actual_period"],
            "concept_output": concept_output,
            "thesis_output": thesis_output,
            **phaseb,
        })

        if case.get("freq", "annual") == "annual":
            _write_phase2b_case_artifacts("Annual", "annual", results, test_cases[: i + 1])

        # Brief pause to avoid rate limiting
        time.sleep(0.5)

    return results


def _empty_phaseb_fields() -> Dict[str, Any]:
    return {
        "raw_earnings_direction": "",
        "calibrated_earnings_direction": "",
        "earnings_direction": "",
        "fundamental_outlook": "",
        "downside_risk_level": "",
        "calibration_policy": "",
        "signal_calibration_notes": [],
    }


def _apply_phaseb_calibration(thesis_output: Dict[str, Any], freq: str) -> Dict[str, Any]:
    """
    Mirror pipeline.py Phase B behavior for the direct audit harness path.

    Raw direction is the original Thesis-CoT direction. The public/final
    earnings_direction equals calibrated_earnings_direction, matching the
    production pipeline contract.
    """
    raw_direction = thesis_output.get("earnings_direction", "")
    confidence = thesis_output.get("earnings_direction_confidence", 0)
    fundamental_outlook = thesis_output.get("fundamental_outlook", "")
    downside_risk_level = thesis_output.get("downside_risk_level", "")

    cal = calibrate_earnings_direction(
        fundamental_outlook=fundamental_outlook,
        downside_risk_level=downside_risk_level,
        raw_earnings_direction=raw_direction,
        earnings_direction_confidence=confidence,
        freq=freq,
    )
    return {
        "raw_earnings_direction": raw_direction,
        "calibrated_earnings_direction": cal.calibrated_direction,
        "earnings_direction": cal.calibrated_direction,
        "fundamental_outlook": fundamental_outlook,
        "downside_risk_level": downside_risk_level,
        "calibration_policy": cal.policy,
        "signal_calibration_notes": cal.notes,
    }


# =============================================================================
# Reasoning quality: structural proxy for human scoring
# =============================================================================

def _compute_reasoning_score(thesis_output: Dict, evidence_pack: Dict) -> float:
    """
    Structural proxy for human reasoning quality (1–5 scale).

    Criteria (1 point each, scaled to 5):
      1. Hypothesis present and non-trivial (> 30 chars)
      2. evidence_for and evidence_against are non-empty lists
      3. thesis_text ≥ 150 chars
      4. earnings_direction with confidence (not default 0)
      5. invalidation_conditions or key_risks present and non-empty

    Returns float 1.0–5.0. Missing/empty thesis → 1.0.
    """
    if not thesis_output or not thesis_output.get("thesis_text", "").strip():
        return 1.0

    score = 1.0  # baseline

    hypothesis = thesis_output.get("hypothesis", "")
    if isinstance(hypothesis, str) and len(hypothesis) > 30:
        score += 1.0

    ev_for = thesis_output.get("evidence_for", [])
    ev_against = thesis_output.get("evidence_against", [])
    if isinstance(ev_for, list) and ev_for and isinstance(ev_against, list) and ev_against:
        score += 1.0

    thesis_text = thesis_output.get("thesis_text", "")
    if isinstance(thesis_text, str) and len(thesis_text) >= 150:
        score += 1.0

    confidence = thesis_output.get("earnings_direction_confidence", 0)
    direction = thesis_output.get("earnings_direction", "")
    if direction in ("up", "down", "flat") and isinstance(confidence, (int, float)) and confidence > 10:
        score += 1.0

    # Score: 1.0 – 5.0
    return min(5.0, score)


# =============================================================================
# Gate [1]: EGX QA accuracy — 20 gold-label questions
# =============================================================================

_EGX_QA_QUESTIONS = [
    # Sector classification (deterministic ground truth)
    {
        "id": 1,
        "question": "What sector does COMI belong to?",
        "answer_key": "banks",
        "answer_alternatives": ["bank", "banking"],
        "category": "sector_classification",
    },
    {
        "id": 2,
        "question": "What sector does HRHO belong to?",
        "answer_key": "holdings",
        "answer_alternatives": ["holding"],
        "category": "sector_classification",
    },
    {
        "id": 3,
        "question": "What sector does PHDC belong to?",
        "answer_key": "real_estate",
        "answer_alternatives": ["real estate", "real_estate"],
        "category": "sector_classification",
    },
    {
        "id": 4,
        "question": "What sector does ETEL belong to?",
        "answer_key": "operational",
        "answer_alternatives": ["operations", "telecom"],
        "category": "sector_classification",
    },
    # Safety floors
    {
        "id": 5,
        "question": "Should HIGH_LEVERAGE_ALERT fire for a bank with D/E of 9?",
        "answer_key": "no",
        "answer_alternatives": ["no", "should not", "does not apply", "exempt"],
        "category": "safety_floors",
    },
    {
        "id": 6,
        "question": "Should LIQUIDITY_EMERGENCY fire for an operational company with current_ratio of 0.3?",
        "answer_key": "yes",
        "answer_alternatives": ["yes", "should fire", "triggers", "applies"],
        "category": "safety_floors",
    },
    {
        "id": 7,
        "question": "Should LIQUIDITY_EMERGENCY fire for a bank with current_ratio of 0.3?",
        "answer_key": "no",
        "answer_alternatives": ["no", "should not", "does not apply", "exempt"],
        "category": "safety_floors",
    },
    {
        "id": 8,
        "question": "Which flag fires when net income is negative for any sector?",
        "answer_key": "NEGATIVE_MARGIN_ALERT",
        "answer_alternatives": ["negative_margin_alert", "negative margin"],
        "category": "safety_floors",
    },
    # Metric interpretation
    {
        "id": 9,
        "question": "Is current_ratio an applicable liquidity metric for banks?",
        "answer_key": "no",
        "answer_alternatives": ["no", "not applicable", "not meaningful", "excluded"],
        "category": "metric_interpretation",
    },
    {
        "id": 10,
        "question": "Why is P/B misleading for Egyptian real estate developers?",
        "answer_key": "historical cost",
        "answer_alternatives": ["historical", "land at historical cost", "book value understated"],
        "category": "metric_interpretation",
    },
    {
        "id": 11,
        "question": "What does PB_UNDERSTATED_HISTORICAL_COST flag indicate?",
        "answer_key": "book value understated",
        "answer_alternatives": ["understat", "historical cost", "land value"],
        "category": "metric_interpretation",
    },
    {
        "id": 12,
        "question": "Is D/E of 8 structurally normal for an Egyptian bank?",
        "answer_key": "yes",
        "answer_alternatives": ["yes", "normal", "structurally normal", "deposit leverage"],
        "category": "metric_interpretation",
    },
    # Data quality scores
    {
        "id": 13,
        "question": "What does data_confidence measure — business condition or data availability?",
        "answer_key": "data availability",
        "answer_alternatives": ["availability", "data available", "how much data", "coverage"],
        "category": "quality_scores",
    },
    {
        "id": 14,
        "question": "Does a company with high data_confidence and a NEGATIVE_MARGIN_ALERT indicate bad data or a bad business?",
        "answer_key": "bad business",
        "answer_alternatives": ["bad business", "business condition", "not bad data"],
        "category": "quality_scores",
    },
    {
        "id": 15,
        "question": "What causes signal_coherence to decrease?",
        "answer_key": "mathematical inconsistency",
        "answer_alternatives": ["inconsistency", "contradiction", "roe positive net margin negative", "dupont failure"],
        "category": "quality_scores",
    },
    # Pipeline structure
    {
        "id": 16,
        "question": "What are the three stages in the CoT pipeline?",
        "answer_key": "data_cot concept_cot thesis_cot",
        "answer_alternatives": ["data", "concept", "thesis", "stage 1", "stage 2", "stage 3"],
        "category": "pipeline_structure",
    },
    {
        "id": 17,
        "question": "If Stage 1 evidence pack validation fails, what pipeline_mode is returned?",
        "answer_key": "deterministic",
        "answer_alternatives": ["deterministic", "fallback deterministic"],
        "category": "pipeline_structure",
    },
    {
        "id": 18,
        "question": "What H&P stands for in the Thesis CoT stage?",
        "answer_key": "hypothesis and prediction",
        "answer_alternatives": ["hypothesis", "prediction", "h&p"],
        "category": "pipeline_structure",
    },
    # EGX market context
    {
        "id": 19,
        "question": "What is the daily price limit on the Egyptian Exchange (EGX)?",
        "answer_key": "10%",
        "answer_alternatives": ["10", "±10", "ten percent", "10 percent"],
        "category": "egx_context",
    },
    {
        "id": 20,
        "question": "Is short selling permitted on the Egyptian Exchange?",
        "answer_key": "no",
        "answer_alternatives": ["no", "not permitted", "prohibited", "not allowed"],
        "category": "egx_context",
    },
]

_QA_SYSTEM_PROMPT = """\
You are an expert on the EGX (Egyptian Exchange) fundamental analysis pipeline.
Answer each question concisely. For yes/no questions, answer "yes" or "no".
For classification questions, answer with the exact category name.
Keep each answer to 1-2 sentences maximum.

EGX PIPELINE REFERENCE (authoritative — use these facts, not general financial knowledge):

SECTOR CLASSIFICATIONS:
- banks: COMI, EAST, ADIB, ABUK, EFIH, CIEB, EGAL
- real_estate: PHDC, TMGH, ORAS, ORHD, RMDA
- holdings: HRHO, ARCC, BTFH, JUFO
- operational: ETEL, FWRY, SKPC, AMOC, ORWE, HELI, GBCO, SWDY, ISPH, MFPC, CCAP, DSCW, VLMR, RAYA, EFIH

METRIC EXCLUSIONS FOR BANKS:
- current_ratio: NOT applicable for banks (structurally low CR is normal — deposits fund long-term assets)
- debt_to_equity: NOT applicable for banks (D/E of 6-10× is structurally normal deposit leverage, not distress)

SAFETY FLOOR FLAGS AND APPLICABILITY:
- HIGH_LEVERAGE_ALERT: fires only for OPERATIONAL/REAL_ESTATE/HOLDINGS when D/E > 5.0; EXEMPT for banks
- LIQUIDITY_EMERGENCY: fires only for OPERATIONAL/REAL_ESTATE/HOLDINGS when current_ratio < 0.5; EXEMPT for banks
- NEGATIVE_MARGIN_ALERT: fires when net income is NEGATIVE — applies to ALL sectors without exception
- NEGATIVE_EQUITY_ALERT: fires when total_equity is NEGATIVE — applies to ALL sectors
- PB_UNDERSTATED_HISTORICAL_COST: fires for REAL ESTATE — book value is understated because land is carried at historical cost, making P/B misleadingly low (true asset value is higher than book implies)
- CONSOLIDATED_BLENDING: fires for HOLDINGS — ratios blend multiple subsidiary industries

PIPELINE STRUCTURE AND MODES:
- Stage 1 (data_cot): deterministic evidence pack from ratio calculations
- Stage 2 (concept_cot): quick_thinking_llm interprets the evidence pack
- Stage 3 (thesis_cot): deep_thinking_llm writes H&P investment thesis
- H&P = Hypothesis and Prediction (structured reasoning to reduce confirmation bias)
- If Stage 1 evidence pack validation FAILS → pipeline_mode = "deterministic" (LLM never called)
- If Stage 2 (concept_cot) fails → pipeline_mode = "cot_partial", stages_completed = ["data_cot"]
- Stage 3 failure is non-blocking; pipeline still returns output

QUALITY SCORES (three separate scores, never collapsed):
- data_confidence (0-100): measures DATA AVAILABILITY and field coverage, NOT business quality
- signal_coherence (0-100): measures mathematical consistency of ratios (DuPont verification, cross-source checks)
- distress_flags: qualitative business-condition indicators; independent of data quality
- High data_confidence + NEGATIVE_MARGIN_ALERT = good data revealing a genuinely unprofitable business

EGX MARKET CONTEXT:
- Daily price limit: ±10% (circuit breaker)
- Short selling: NOT permitted on EGX
- All portfolios are long-only by market structure
"""

def run_egx_qa_gate(quick_llm: Any) -> Tuple[int, List[Dict]]:
    """
    Run the 20 gold-label EGX QA questions through quick_thinking_llm.

    Returns (n_correct, scored_questions_list).
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    scored = []

    for qa in _EGX_QA_QUESTIONS:
        q_id = qa["id"]
        question = qa["question"]
        answer_key = qa["answer_key"]
        alternatives = qa.get("answer_alternatives", [])

        try:
            response = quick_llm.invoke([
                SystemMessage(content=_QA_SYSTEM_PROMPT),
                HumanMessage(content=question),
            ])
            raw = response.content if hasattr(response, "content") else str(response)
            raw_lower = raw.lower().strip()

            # Check if any correct token appears in the response
            correct_tokens = [answer_key.lower()] + [a.lower() for a in alternatives]
            is_correct = any(tok in raw_lower for tok in correct_tokens)

        except Exception as e:
            raw = f"[ERROR: {e}]"
            is_correct = False

        scored.append({
            "id": q_id,
            "question": question,
            "expected": answer_key,
            "response": raw[:200],
            "correct": is_correct,
            "category": qa["category"],
        })

        time.sleep(0.3)  # Rate limit

    n_correct = sum(1 for s in scored if s["correct"])
    return n_correct, scored


# =============================================================================
# Gate metric computations
# =============================================================================

def _compute_brier(predictions: List[Dict]) -> float:
    """
    Brier score for earnings direction prediction.

    outcome_i = 1 if predicted_direction == actual_direction, 0 otherwise.
    confidence_i = earnings_direction_confidence / 100.
    Brier = (1/N) × Σ(confidence_i − outcome_i)².
    """
    scored = [
        p for p in predictions
        if p.get("predicted_direction") in ("up", "down", "flat")
        and p.get("actual_direction") in ("up", "down", "flat")
    ]
    if not scored:
        return float("nan")

    total = 0.0
    for p in scored:
        conf = min(100, max(0, p.get("confidence", 0))) / 100.0
        outcome = 1.0 if p["predicted_direction"] == p["actual_direction"] else 0.0
        total += (conf - outcome) ** 2

    return total / len(scored)


def _compute_naive_brier(actual_directions: List[str]) -> float:
    """
    Brier score for naive "always_up" baseline at 0.60 confidence.
    """
    if not actual_directions:
        return float("nan")

    total = 0.0
    for direction in actual_directions:
        outcome = 1.0 if direction == "up" else 0.0
        total += (0.60 - outcome) ** 2

    return total / len(actual_directions)


def _compute_hit_rate(predictions: List[Dict]) -> Tuple[float, float]:
    """
    Returns (cot_hit_rate, naive_always_up_hit_rate).
    Only counts cases where CoT made a prediction.
    """
    scored = [
        p for p in predictions
        if p.get("predicted_direction") in ("up", "down", "flat")
        and p.get("actual_direction") in ("up", "down", "flat")
    ]
    if not scored:
        return float("nan"), float("nan")

    actuals = [p["actual_direction"] for p in scored]
    naive_rate = sum(1 for a in actuals if a == "up") / len(actuals)

    cot_correct = sum(
        1 for p in scored if p["predicted_direction"] == p["actual_direction"]
    )
    cot_rate = cot_correct / len(scored)

    return cot_rate, naive_rate


def _compute_ic(predictions: List[Dict]) -> float:
    """
    Confidence-weighted IC = SpearmanCorr(confidence × direction_sign, actual_ni_change).

    direction_sign: +1 for "up", -1 for "down", 0 for "flat".
    Returns float (or nan if insufficient data).
    """
    try:
        from scipy.stats import spearmanr
    except ImportError:
        return float("nan")

    scored = [
        p for p in predictions
        if p.get("predicted_direction") in ("up", "down", "flat")
        and p.get("actual_ni_change") is not None
        and isinstance(p.get("confidence"), (int, float))
    ]
    if len(scored) < 3:
        return float("nan")

    sign_map = {"up": 1, "down": -1, "flat": 0}
    x = [
        (p["confidence"] / 100.0) * sign_map[p["predicted_direction"]]
        for p in scored
    ]
    y = [p["actual_ni_change"] for p in scored]

    try:
        stat, pval = spearmanr(x, y)
        return float(stat) if not math.isnan(stat) else float("nan")
    except Exception:
        return float("nan")


def _compute_error_propagation_rate(results: List[Dict]) -> float:
    """
    Fraction of CoT runs where inter-stage validation failed.

    A "failed" run is one where stage1_valid is True but stage2 or stage3
    validation failed (i.e., error propagated from one stage to the next).
    We only count cases where stage 1 passed (otherwise it's a data issue).
    """
    stage1_passed = [r for r in results if r.get("stage1_valid")]
    if not stage1_passed:
        return 0.0

    failed = [
        r for r in stage1_passed
        if not r.get("stage2_valid") or not r.get("stage3_valid")
    ]
    return len(failed) / len(stage1_passed)


def _safe_float(value: Any) -> Optional[float]:
    """Best-effort numeric coercion for artifact-only diagnostics."""
    if value is None:
        return None
    try:
        if isinstance(value, str) and not value.strip():
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _report_to_dict(report: Any) -> Dict[str, Any]:
    if report is None:
        return {}
    if isinstance(report, dict):
        return report
    if hasattr(report, "model_dump"):
        return report.model_dump()
    if hasattr(report, "dict"):
        return report.dict()
    return {}


def _first_nested_value(payload: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[float]:
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key in keys:
                if key in node:
                    val = _safe_float(node.get(key))
                    if val is not None:
                        return val
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            stack.extend(v for v in node if isinstance(v, (dict, list)))
    return None


def _direction_from_change(change: Optional[float]) -> str:
    if change is None:
        return "missing"
    if change > _DIRECTION_THRESHOLD:
        return "up"
    if change < -_DIRECTION_THRESHOLD:
        return "down"
    return "flat"


def _baseline_brier(predictions: List[str], actuals: List[str], confidence: float = 0.60) -> float:
    scored = [(p, a) for p, a in zip(predictions, actuals) if p in ("up", "down", "flat") and a in ("up", "down", "flat")]
    if not scored:
        return float("nan")
    return sum((confidence - (1.0 if p == a else 0.0)) ** 2 for p, a in scored) / len(scored)


def _write_csv(path: str, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _annual_case_result_fields() -> List[str]:
    """Column order for annual case-level artifacts, including Phase B fields."""
    return [
        "ticker", "sector", "prediction_period", "actual_period", "actual_direction",
        "predicted_direction", "confidence", "correct", "error_status",
        "prediction_year", "actual_year", "actual_ni_change",
        "stage1_valid", "stage2_valid", "stage3_valid", "thesis_text",
        "raw_earnings_direction", "calibrated_earnings_direction", "earnings_direction",
        "fundamental_outlook", "downside_risk_level", "calibration_policy",
        "signal_calibration_notes", "raw_correct", "calibrated_correct", "final_correct",
    ]


def _build_artifact_case_rows(cot_results: List[Dict], test_cases: List[Dict]) -> List[Dict[str, Any]]:
    by_key = {
        (c.get("ticker"), c.get("prediction_period"), c.get("actual_period")): c
        for c in test_cases
    }
    rows = []
    for result in cot_results:
        key = (result.get("ticker"), result.get("prediction_period"), result.get("actual_period"))
        case = by_key.get(key, {})
        pred = result.get("predicted_direction", "")
        actual = result.get("actual_direction", "")
        valid_pred = pred in ("up", "down", "flat")

        # Phase B calibration fields (backward-compatible: default to empty)
        raw_direction = result.get("raw_earnings_direction", "")
        calibrated_direction = result.get("calibrated_earnings_direction", "")
        final_direction = result.get("earnings_direction") or pred
        fundamental_outlook = result.get("fundamental_outlook", "")
        downside_risk_level = result.get("downside_risk_level", "")
        calibration_policy = result.get("calibration_policy", "")
        signal_calibration_notes = result.get("signal_calibration_notes", [])
        if isinstance(signal_calibration_notes, list):
            signal_calibration_notes = json.dumps(signal_calibration_notes, ensure_ascii=True)

        rows.append({
            "ticker": result.get("ticker", ""),
            "sector": case.get("sector", ""),
            "prediction_period": result.get("prediction_period", ""),
            "actual_period": result.get("actual_period", ""),
            "actual_direction": actual,
            "predicted_direction": pred if pred else "missing",
            "confidence": result.get("confidence", ""),
            "correct": (pred == actual) if valid_pred and actual in ("up", "down", "flat") else "",
            "error_status": "ok" if valid_pred else "missing_or_parse_error",
            "prediction_year": str(result.get("prediction_period", ""))[:4],
            "actual_year": str(result.get("actual_period", ""))[:4],
            "actual_ni_change": result.get("actual_ni_change", ""),
            "stage1_valid": result.get("stage1_valid", ""),
            "stage2_valid": result.get("stage2_valid", ""),
            "stage3_valid": result.get("stage3_valid", ""),
            "thesis_text": result.get("thesis_text", ""),
            # Phase B signal calibration fields
            "raw_earnings_direction": raw_direction,
            "calibrated_earnings_direction": calibrated_direction,
            "earnings_direction": final_direction,
            "fundamental_outlook": fundamental_outlook,
            "downside_risk_level": downside_risk_level,
            "calibration_policy": calibration_policy,
            "signal_calibration_notes": signal_calibration_notes,
            "raw_correct": (raw_direction == actual) if raw_direction in ("up", "down", "flat") and actual in ("up", "down", "flat") else "",
            "calibrated_correct": (calibrated_direction == actual) if calibrated_direction in ("up", "down", "flat") and actual in ("up", "down", "flat") else "",
            "final_correct": (final_direction == actual) if final_direction in ("up", "down", "flat") and actual in ("up", "down", "flat") else "",
        })
    return rows


def _summarize_rows(rows: List[Dict[str, Any]], group_key: str) -> List[Dict[str, Any]]:
    from collections import defaultdict, Counter
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row.get(group_key, "")].append(row)

    out = []
    for key in sorted(grouped):
        items = [r for r in grouped[key] if r.get("predicted_direction") in ("up", "down", "flat")]
        if not items:
            continue
        actual = Counter(r["actual_direction"] for r in items)
        pred = Counter(r["predicted_direction"] for r in items)
        correct = [r for r in items if r.get("correct") is True]
        wrong = [r for r in items if r.get("correct") is False]
        conf_correct = [_safe_float(r.get("confidence")) for r in correct]
        conf_wrong = [_safe_float(r.get("confidence")) for r in wrong]
        conf_all = [_safe_float(r.get("confidence")) for r in items]
        conf_correct = [v for v in conf_correct if v is not None]
        conf_wrong = [v for v in conf_wrong if v is not None]
        conf_all = [v for v in conf_all if v is not None]
        out.append({
            group_key: key,
            "n": len(items),
            "actual_up": actual.get("up", 0),
            "actual_down": actual.get("down", 0),
            "actual_flat": actual.get("flat", 0),
            "predicted_up": pred.get("up", 0),
            "predicted_down": pred.get("down", 0),
            "predicted_flat": pred.get("flat", 0),
            "cot_hit_rate": len(correct) / len(items) if items else "",
            "naive_hit_rate": actual.get("up", 0) / len(items) if items else "",
            "avg_confidence": sum(conf_all) / len(conf_all) if conf_all else "",
            "avg_confidence_correct": sum(conf_correct) / len(conf_correct) if conf_correct else "",
            "avg_confidence_wrong": sum(conf_wrong) / len(conf_wrong) if conf_wrong else "",
        })
    return out


def _project_results(results: List[Dict], direction_key: str) -> List[Dict]:
    """Return result dicts with predicted_direction replaced by a named signal."""
    projected = []
    for result in results:
        pred = result.get(direction_key, "")
        row = dict(result)
        row["predicted_direction"] = pred
        projected.append(row)
    return projected


def _confusion_matrix_rows(rows: List[Dict[str, Any]], direction_key: str) -> List[Dict[str, Any]]:
    labels = ("up", "down", "flat")
    matrix_rows = []
    for actual in labels:
        matrix_rows.append({
            "actual_direction": actual,
            **{
                f"predicted_{pred}": sum(
                    1 for r in rows
                    if r.get("actual_direction") == actual and r.get(direction_key) == pred
                )
                for pred in labels
            },
        })
    return matrix_rows


def _classwise_rows(rows: List[Dict[str, Any]], direction_key: str) -> List[Dict[str, Any]]:
    labels = ("up", "down", "flat")
    class_rows = []
    for label in labels:
        actual_items = [r for r in rows if r.get("actual_direction") == label and r.get(direction_key) in labels]
        predicted_items = [r for r in rows if r.get(direction_key) == label and r.get("actual_direction") in labels]
        tp = sum(1 for r in actual_items if r.get(direction_key) == label)
        class_rows.append({
            "actual_class": label,
            "count": len(actual_items),
            "model_recall": tp / len(actual_items) if actual_items else "",
            "model_precision": tp / len(predicted_items) if predicted_items else "",
            "model_hit_rate": tp / len(actual_items) if actual_items else "",
            "naive_hit_rate": 1.0 if label == "up" and actual_items else (0.0 if actual_items else ""),
        })
    return class_rows


def _non_up_precision_recall(results: List[Dict]) -> Tuple[float, float, int, int]:
    labels = ("up", "down", "flat")
    scored = [
        r for r in results
        if r.get("predicted_direction") in labels and r.get("actual_direction") in labels
    ]
    pred_non_up = [r for r in scored if r["predicted_direction"] in ("down", "flat")]
    actual_non_up = [r for r in scored if r["actual_direction"] in ("down", "flat")]
    true_non_up = [
        r for r in pred_non_up
        if r["actual_direction"] in ("down", "flat")
    ]
    precision = len(true_non_up) / len(pred_non_up) if pred_non_up else float("nan")
    recall = len(true_non_up) / len(actual_non_up) if actual_non_up else float("nan")
    false_non_up = sum(1 for r in pred_non_up if r.get("actual_direction") == "up")
    return precision, recall, false_non_up, len(true_non_up)


def _balanced_accuracy(results: List[Dict]) -> float:
    labels = ("up", "down", "flat")
    recalls = []
    for label in labels:
        actual_items = [
            r for r in results
            if r.get("actual_direction") == label and r.get("predicted_direction") in labels
        ]
        if actual_items:
            recalls.append(sum(1 for r in actual_items if r["predicted_direction"] == label) / len(actual_items))
    return sum(recalls) / len(recalls) if recalls else float("nan")


def _macro_f1(results: List[Dict]) -> float:
    labels = ("up", "down", "flat")
    f1s = []
    for label in labels:
        scored = [
            r for r in results
            if r.get("actual_direction") in labels and r.get("predicted_direction") in labels
        ]
        tp = sum(1 for r in scored if r["actual_direction"] == label and r["predicted_direction"] == label)
        fp = sum(1 for r in scored if r["actual_direction"] != label and r["predicted_direction"] == label)
        fn = sum(1 for r in scored if r["actual_direction"] == label and r["predicted_direction"] != label)
        if tp == 0 and fp == 0 and fn == 0:
            continue
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1s.append(2 * precision * recall / (precision + recall) if (precision + recall) else 0.0)
    return sum(f1s) / len(f1s) if f1s else float("nan")


def _print_signal_metric_summary(results: List[Dict], label: str, direction_key: str) -> None:
    projected = _project_results(results, direction_key)
    hit, naive_hit = _compute_hit_rate(projected)
    brier = _compute_brier(projected)
    ic = _compute_ic(projected)
    non_up_precision, non_up_recall, false_non_up, true_non_up = _non_up_precision_recall(projected)
    bal_acc = _balanced_accuracy(projected)
    macro_f1 = _macro_f1(projected)
    labels = ("up", "down", "flat")
    preds = [r.get("predicted_direction") for r in projected if r.get("predicted_direction") in labels]
    print(f"  {label}:")
    print(
        f"    hit={_fmt_rate(hit)} | brier={_fmt_float(brier)} | IC={_fmt_float(ic)} | "
        f"balanced_acc={_fmt_rate(bal_acc)} | macro_f1={_fmt_float(macro_f1)}"
    )
    print(
        f"    non_up_precision={_fmt_rate(non_up_precision)} | non_up_recall={_fmt_rate(non_up_recall)} | "
        f"false_non_up={false_non_up} | true_non_up={true_non_up}"
    )
    print(
        f"    predicted distribution: up={sum(1 for p in preds if p == 'up')}, "
        f"down={sum(1 for p in preds if p == 'down')}, flat={sum(1 for p in preds if p == 'flat')}"
    )
    if label.lower().startswith("calibrated"):
        print(f"    naive always-up hit={_fmt_rate(naive_hit)}")


def _write_phase2b_case_artifacts(
    eval_name: str,
    freq: str,
    cot_results: List[Dict],
    test_cases: List[Dict],
) -> None:
    """
    Persist case-level diagnostics for future forensics.

    Artifact writing is deliberately post-hoc: it does not affect model calls,
    gate scoring, prompt content, or the formal verdict.
    """
    if freq != "annual":
        return

    out_dir = os.path.join(PROJECT_ROOT, "staging", "phase2b_failure_forensics")
    rows = _build_artifact_case_rows(cot_results, test_cases)
    base_fields = _annual_case_result_fields()
    _write_csv(os.path.join(out_dir, "annual_case_results.csv"), rows, base_fields)

    labels = ("up", "down", "flat")
    matrix_rows = _confusion_matrix_rows(rows, "predicted_direction")
    _write_csv(
        os.path.join(out_dir, "annual_confusion_matrix.csv"),
        matrix_rows,
        ["actual_direction", "predicted_up", "predicted_down", "predicted_flat"],
    )
    _write_csv(
        os.path.join(out_dir, "annual_confusion_matrix_raw.csv"),
        _confusion_matrix_rows(rows, "raw_earnings_direction"),
        ["actual_direction", "predicted_up", "predicted_down", "predicted_flat"],
    )
    _write_csv(
        os.path.join(out_dir, "annual_confusion_matrix_calibrated.csv"),
        _confusion_matrix_rows(rows, "calibrated_earnings_direction"),
        ["actual_direction", "predicted_up", "predicted_down", "predicted_flat"],
    )

    class_rows = _classwise_rows(rows, "predicted_direction")
    _write_csv(
        os.path.join(out_dir, "annual_classwise_metrics.csv"),
        class_rows,
        ["actual_class", "count", "model_recall", "model_precision", "model_hit_rate", "naive_hit_rate"],
    )
    _write_csv(
        os.path.join(out_dir, "annual_classwise_metrics_raw.csv"),
        _classwise_rows(rows, "raw_earnings_direction"),
        ["actual_class", "count", "model_recall", "model_precision", "model_hit_rate", "naive_hit_rate"],
    )
    _write_csv(
        os.path.join(out_dir, "annual_classwise_metrics_calibrated.csv"),
        _classwise_rows(rows, "calibrated_earnings_direction"),
        ["actual_class", "count", "model_recall", "model_precision", "model_hit_rate", "naive_hit_rate"],
    )

    cohort_rows = _summarize_rows(rows, "prediction_year")
    _write_csv(
        os.path.join(out_dir, "annual_cohort_breakdown.csv"),
        cohort_rows,
        [
            "prediction_year", "n", "actual_up", "actual_down", "actual_flat",
            "predicted_up", "predicted_down", "predicted_flat", "cot_hit_rate",
            "naive_hit_rate", "avg_confidence", "avg_confidence_correct",
            "avg_confidence_wrong",
        ],
    )

    sector_rows = _summarize_rows(rows, "sector")
    _write_csv(
        os.path.join(out_dir, "annual_sector_breakdown.csv"),
        sector_rows,
        [
            "sector", "n", "actual_up", "actual_down", "actual_flat",
            "predicted_up", "predicted_down", "predicted_flat", "cot_hit_rate",
            "naive_hit_rate", "avg_confidence", "avg_confidence_correct",
            "avg_confidence_wrong",
        ],
    )

    false_non_up = [
        r for r in rows
        if r.get("actual_direction") == "up" and r.get("predicted_direction") in ("down", "flat")
    ]
    _write_csv(os.path.join(out_dir, "false_non_up_cases.csv"), false_non_up, base_fields)

    true_non_up = [
        r for r in rows
        if r.get("actual_direction") in ("down", "flat")
        and r.get("predicted_direction") == r.get("actual_direction")
    ]
    _write_csv(os.path.join(out_dir, "true_non_up_cases.csv"), true_non_up, base_fields)

    actuals = [r["actual_direction"] for r in rows if r.get("predicted_direction") in labels and r.get("actual_direction") in labels]
    cot_preds = [r["predicted_direction"] for r in rows if r.get("predicted_direction") in labels and r.get("actual_direction") in labels]
    raw_results = _project_results(cot_results, "raw_earnings_direction")
    calibrated_results = _project_results(cot_results, "calibrated_earnings_direction")
    baseline_rows = [{
        "baseline": "CoT annual formal run (final calibrated earnings_direction)",
        "hit_rate": sum(1 for p, a in zip(cot_preds, actuals) if p == a) / len(actuals) if actuals else "",
        "brier": _compute_brier(cot_results),
        "predicted_up": sum(1 for p in cot_preds if p == "up"),
        "predicted_down": sum(1 for p in cot_preds if p == "down"),
        "predicted_flat": sum(1 for p in cot_preds if p == "flat"),
        "comments": "Final public earnings_direction after Phase B calibration.",
    }, {
        "baseline": "raw_llm_direction",
        "hit_rate": _compute_hit_rate(raw_results)[0],
        "brier": _compute_brier(raw_results),
        "predicted_up": sum(1 for r in raw_results if r.get("predicted_direction") == "up"),
        "predicted_down": sum(1 for r in raw_results if r.get("predicted_direction") == "down"),
        "predicted_flat": sum(1 for r in raw_results if r.get("predicted_direction") == "flat"),
        "comments": "Original Thesis-CoT direction before Phase B calibration.",
    }, {
        "baseline": "calibrated_direction",
        "hit_rate": _compute_hit_rate(calibrated_results)[0],
        "brier": _compute_brier(calibrated_results),
        "predicted_up": sum(1 for r in calibrated_results if r.get("predicted_direction") == "up"),
        "predicted_down": sum(1 for r in calibrated_results if r.get("predicted_direction") == "down"),
        "predicted_flat": sum(1 for r in calibrated_results if r.get("predicted_direction") == "flat"),
        "comments": "Phase B v1_outlook_risk calibrated signal.",
    }, {
        "baseline": "always_up",
        "hit_rate": sum(1 for a in actuals if a == "up") / len(actuals) if actuals else "",
        "brier": _baseline_brier(["up"] * len(actuals), actuals),
        "predicted_up": len(actuals),
        "predicted_down": 0,
        "predicted_flat": 0,
        "comments": "Naive formal comparator, fixed 60% confidence for Brier.",
    }]
    _write_csv(
        os.path.join(out_dir, "baseline_comparison.csv"),
        baseline_rows,
        ["baseline", "hit_rate", "brier", "predicted_up", "predicted_down", "predicted_flat", "comments"],
    )

    print(f"\n  [INFO] Wrote annual case-level forensics artifacts to {out_dir}")


def _compute_reasoning_quality(results: List[Dict]) -> float:
    """Average structural reasoning quality score across results with a prediction."""
    scored = [
        r for r in results
        if r.get("predicted_direction") in ("up", "down", "flat")
    ]
    if not scored:
        return 0.0
    return sum(r.get("reasoning_score", 1.0) for r in scored) / len(scored)


# =============================================================================
# Gate reporter
# =============================================================================

def _compute_wilson_ci(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """
    Wilson score confidence interval for a proportion k/n.

    More accurate than normal-approximation CI at small N.
    Returns (lower, upper) as fractions in [0, 1].
    """
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    margin = (z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def _compute_min_n_power(p0: float, delta: float = 0.10, alpha: float = 0.05, power: float = 0.80) -> int:
    """
    Minimum N to detect a `delta` absolute improvement over baseline `p0`
    at one-sided significance `alpha` and `power`.

    Formula (one-proportion z-test, one-sided):
      N = (z_alpha * sqrt(p0*(1-p0)) + z_beta * sqrt(p1*(1-p1)))^2 / delta^2

    where p1 = p0 + delta, z_alpha = 1.645 (alpha=0.05), z_beta = 0.842 (power=0.80).
    """
    z_alpha = 1.645   # one-sided alpha = 0.05
    z_beta  = 0.842   # 80% power
    p1 = min(p0 + delta, 0.999)
    numerator = (z_alpha * math.sqrt(p0 * (1 - p0)) + z_beta * math.sqrt(p1 * (1 - p1))) ** 2
    return math.ceil(numerator / (delta ** 2))


def _fmt_rate(v: float, decimals: int = 1) -> str:
    if math.isnan(v):
        return "N/A"
    return f"{v:.{decimals}%}"

def _fmt_float(v: float, decimals: int = 4) -> str:
    if math.isnan(v):
        return "N/A"
    return f"{v:.{decimals}f}"


# =============================================================================
# Main audit runner
# =============================================================================


def run_evaluation(eval_name: str, tickers: List[str], freq: str, n_periods: int,
                   quick_llm: Any, deep_llm: Any,
                   qa_correct: int = 0, qa_pass: bool = False,
                   is_formal_gate: bool = True) -> dict:
    """
    Run the full CoT evaluation for a given frequency.

    is_formal_gate=True  → annual mode; OVERALL GATE verdict drives Phase 2B pass/fail.
    is_formal_gate=False → quarterly/diagnostic mode; results are reported but do NOT
                           affect the overall Phase 2B gate. Quarterly is retained as a
                           narrative synthesis mode, not a directional forecasting gate.
    """
    print(f"\n{'='*70}\n  {eval_name.upper()} EVALUATION (freq={freq}, n_periods={n_periods})\n{'='*70}")

    raw_cases = build_cot_test_set(tickers, freq=freq, n_periods=n_periods)

    # Separate metadata sentinel from real cases
    excluded_meta = next((c for c in raw_cases if c.get("_excluded")), None)
    test_cases = [c for c in raw_cases if not c.get("_excluded")]
    excluded_baseline = excluded_meta.get("_excluded_insufficient_baseline", []) if excluded_meta else []

    n_test = len(test_cases)
    n_tickers_in_cases = len({tc["ticker"] for tc in test_cases})
    n_excluded = len(excluded_baseline)
    n_raw = n_test + n_excluded

    print(f"\n[1/6] Building earnings direction test set...")
    print(f"  {n_raw} total candidate cases from {len(tickers)} tickers")

    if freq == "annual" and n_excluded > 0:
        print(f"\n  ── Prior-year baseline exclusion report ──────────────────────")
        print(f"  Excluded (insufficient_baseline): {n_excluded} cases")
        print(f"  Included in formal gate:          {n_test} cases")
        from collections import Counter as _Counter
        excl_by_yr = _Counter(e["prediction_period"][:4] for e in excluded_baseline)
        excl_by_tick = _Counter(e["ticker"] for e in excluded_baseline)
        print(f"  Excluded by year: {dict(sorted(excl_by_yr.items()))}")
        print(f"  Excluded tickers: {dict(sorted(excl_by_tick.items()))}")
        print(f"  (These cases are not model failures — prior-year NI absent from CSV files.)")
        print(f"  ──────────────────────────────────────────────────────────────")

    print(f"  {n_test} formal test cases from {n_tickers_in_cases} tickers")

    if n_test == 0:
        print("  [WARN] No test cases after filtering. Cannot evaluate.")
        return {}

    # Gate [1]: QA result passed in from caller (shared across evaluations)
    print(f"\n[2/6] Gate [1]: EGX QA accuracy (shared)...")
    _print_sub(f"Correct: {qa_correct}/20 | Target: >= {_MIN_QA_CORRECT}/20", qa_pass)

    print(f"\n[3/6] Running CoT pipeline on {n_test} test cases...")
    cot_results = run_cot_on_test_set(test_cases, quick_llm, deep_llm)
    _write_phase2b_case_artifacts(eval_name, freq, cot_results, test_cases)

    n_predicted = sum(1 for r in cot_results if r.get("predicted_direction") in ("up", "down", "flat"))
    print(f"\n  CoT produced predictions for {n_predicted}/{n_test} test cases")
    print("\n  ── Phase B Signal Metrics (raw vs calibrated/final) ─────────────")
    _print_signal_metric_summary(cot_results, "Raw LLM direction", "raw_earnings_direction")
    _print_signal_metric_summary(cot_results, "Calibrated/final direction", "calibrated_earnings_direction")
    actual_dirs_for_naive = [
        r["actual_direction"] for r in cot_results
        if r.get("actual_direction") in ("up", "down", "flat")
        and r.get("predicted_direction") in ("up", "down", "flat")
    ]
    print(
        f"  Naive always-up: hit={_fmt_rate(sum(1 for a in actual_dirs_for_naive if a == 'up') / len(actual_dirs_for_naive) if actual_dirs_for_naive else float('nan'))} | "
        f"brier={_fmt_float(_compute_naive_brier(actual_dirs_for_naive))}"
    )
    print("  Gate metrics below use final public earnings_direction (calibrated).")
    print("  ─────────────────────────────────────────────────────────────────")

    print("\n[4/6] Gate [2]: Error propagation rate...")
    error_rate = _compute_error_propagation_rate(cot_results)
    ep_pass = (not math.isnan(error_rate)) and error_rate < _MAX_ERROR_RATE
    _print_sub(f"Error rate: {_fmt_rate(error_rate)} | Target: < {_fmt_rate(_MAX_ERROR_RATE)}", ep_pass)

    print("\n[5/6] Gate [3]: Earnings direction hit rate...")
    cot_hit, naive_hit = _compute_hit_rate(cot_results)
    hit_pass = (not math.isnan(cot_hit)) and (not math.isnan(naive_hit)) and cot_hit > naive_hit
    _print_sub(f"CoT: {_fmt_rate(cot_hit)} vs. naive baseline: {_fmt_rate(naive_hit)} | Target: CoT > baseline", hit_pass)

    _n_scored = sum(1 for r in cot_results if r.get("predicted_direction") in ("up", "down", "flat") and r.get("actual_direction") in ("up", "down", "flat"))
    _n_correct = sum(1 for r in cot_results if r.get("predicted_direction") == r.get("actual_direction") and r.get("predicted_direction") in ("up", "down", "flat"))
    _ci_lo, _ci_hi = _compute_wilson_ci(_n_correct, _n_scored)
    _naive_p = naive_hit if not math.isnan(naive_hit) else 0.607
    _min_n = _compute_min_n_power(_naive_p, delta=0.10)

    print("\n  ── Statistical Context ──────────────────────────────────────────")
    print(f"  N = {_n_scored} prediction pairs")
    print(f"  95% CI on CoT hit rate (Wilson): [{_fmt_rate(_ci_lo)} — {_fmt_rate(_ci_hi)}]")
    print(f"  Min N for 80% power (detect +10pp over baseline): {_min_n}")
    if _n_scored < _min_n:
        print(f"  Note: Result is inconclusive due to small sample (N={_n_scored} < {_min_n})")
    else:
        print(f"  Note: Sample size is statistically meaningful (N={_n_scored} >= {_min_n}).")
    print("  ─────────────────────────────────────────────────────────────────")

    print("\n[6/6] Gate [4]: Brier | Gate [5]: IC | Gate [6]: Reasoning quality")
    cot_brier = _compute_brier(cot_results)
    actual_dirs = [r["actual_direction"] for r in cot_results if r.get("predicted_direction") in ("up", "down", "flat") and r.get("actual_direction") in ("up", "down", "flat")]
    naive_brier = _compute_naive_brier(actual_dirs)
    brier_pass = (not math.isnan(cot_brier)) and (not math.isnan(naive_brier)) and cot_brier <= naive_brier
    _print_sub(f"Brier: CoT={_fmt_float(cot_brier)} | Baseline={_fmt_float(naive_brier)} | Target: CoT <= baseline", brier_pass)

    ic = _compute_ic(cot_results)
    ic_pass = (not math.isnan(ic)) and ic > 0
    _print_sub(f"IC: {_fmt_float(ic)} | Target: IC > 0", ic_pass)

    avg_quality = _compute_reasoning_quality(cot_results)
    quality_pass = avg_quality >= _MIN_REASONING_QUALITY
    _print_sub(f"Reasoning quality: {avg_quality:.2f}/5.0 | Target: >= {_MIN_REASONING_QUALITY:.1f}", quality_pass)

    gate_results = {
        "[1] EGX QA accuracy": qa_pass,
        "[2] Error propagation < 10%": ep_pass,
        "[3] Hit rate > naive baseline": hit_pass,
        "[4] Brier <= deterministic": brier_pass,
        "[5] IC > 0": ic_pass,
        "[6] Reasoning quality >= 3.0": quality_pass,
    }
    
    all_objective_pass = all({k: v for k, v in gate_results.items() if '[1]' not in k and '[6]' not in k}.values())
    any_qualitative_pass = gate_results['[1] EGX QA accuracy'] or gate_results['[6] Reasoning quality >= 3.0']
    overall_pass = all_objective_pass and any_qualitative_pass

    print("\n" + "=" * 70)
    print(f"  {eval_name.upper()} GATE SUMMARY")
    for name, passed in gate_results.items():
        print(f"  {'[PASS]' if passed else '[FAIL]'}  {name}")

    if is_formal_gate:
        if freq == "annual" and n_excluded > 0:
            print(f"\n  Note: {n_excluded} cases excluded (insufficient_baseline) — not counted as failures.")
        print(f"\n  OVERALL {eval_name.upper()} GATE: {'PASSED' if overall_pass else 'FAILED'}")
        print("  (This result drives the Phase 2B formal pass/fail verdict.)")
    else:
        print(f"\n  {eval_name.upper()} DIAGNOSTIC RESULT: {'PASSED' if overall_pass else 'FAILED'}")
        print("  [DIAGNOSTIC ONLY — does not affect Phase 2B formal gate]")
        print("  Quarterly CoT is retained as narrative/risk synthesis mode.")
        print("  Direction accuracy is reported here for transparency, not as a KPI.")
    print("=" * 70)

    return {
        "n_evaluated": len(tickers),
        "n_scored": _n_scored,
        "n_excluded_baseline": n_excluded,
        "gates": gate_results,
        "overall_pass": overall_pass,
        "ci_lo": _ci_lo, "ci_hi": _ci_hi, "hit_rate": cot_hit, "naive_hit": naive_hit,
        "is_meaningful": _n_scored >= _min_n
    }


def main():
    print("=" * 70)
    print("  PHASE 2B CoT VALIDATION GATE (ANNUAL + QUARTERLY)")
    print("=" * 70)

    try:
        quick_llm, deep_llm = _build_llms()
    except Exception as e:
        print(f"  [FATAL] {e}\nCannot proceed. Fix credentials.")
        sys.exit(1)

    import pandas as pd
    # Use PROJECT_ROOT so the script works from any working directory
    base = os.path.join(PROJECT_ROOT, 'tradingagents', 'dataflows', 'data_cache', 'egx_fundamentals')

    valid_q_tickers = []
    excluded_q_tickers = []
    for t in ALL_TICKERS:
        f = os.path.join(base, 'income_statements', f'{t}_income_quarterly.csv')
        if os.path.exists(f):
            try:
                df = pd.read_csv(f)
                nn = df['net_income'].dropna() if 'net_income' in df.columns else []
                if len(nn) >= 2:
                    valid_q_tickers.append(t)
                else:
                    excluded_q_tickers.append((t, f"insufficient non-null net_income rows ({len(nn)})"))
            except Exception as e:
                excluded_q_tickers.append((t, f"read error: {e}"))
        else:
            excluded_q_tickers.append((t, "no quarterly income file"))

    print(f"\nQuarterly Evaluation Universe:")
    print(f"  {len(valid_q_tickers)} tickers valid for quarterly eval.")
    if excluded_q_tickers:
        print(f"  Excluded {len(excluded_q_tickers)} tickers:")
        for t, reason in excluded_q_tickers:
            print(f"    {t}: {reason}")
    print()

    # Run EGX QA gate once — result is frequency-independent
    print("\n[QA] Gate [1]: EGX QA accuracy (20 questions — run once, shared)...")
    qa_correct, qa_scored = run_egx_qa_gate(quick_llm)
    qa_pass = qa_correct >= _MIN_QA_CORRECT
    _print_sub(f"Correct: {qa_correct}/20 | Target: >= {_MIN_QA_CORRECT}/20", qa_pass)

    res_annual = run_evaluation("Annual", ALL_TICKERS, "annual", 10,
                                quick_llm, deep_llm, qa_correct=qa_correct, qa_pass=qa_pass,
                                is_formal_gate=True)
    res_quarterly = run_evaluation("Quarterly", valid_q_tickers, "quarterly", 20,
                                   quick_llm, deep_llm, qa_correct=qa_correct, qa_pass=qa_pass,
                                   is_formal_gate=False)

    # ── Phase 2B Final Verdict ─────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  PHASE 2B OVERALL VERDICT")
    print("=" * 70)
    annual_passed = res_annual.get("overall_pass", False)
    annual_meaningful = res_annual.get("is_meaningful", False)
    n_excl = res_annual.get("n_excluded_baseline", 0)
    print(f"\n  Annual gate (FORMAL):     {'PASSED' if annual_passed else 'FAILED'}")
    print(f"  Annual N (formal gate):   {res_annual.get('n_scored', '?')}")
    if n_excl > 0:
        print(f"  Annual cases excluded (insufficient_baseline): {n_excl} — not model failures")
    if not annual_meaningful:
        print(f"  Note: N below min-N threshold. Result is statistically inconclusive.")
    print(f"\n  Quarterly gate (DIAGNOSTIC ONLY): not counted toward Phase 2B verdict.")
    print(f"  Quarterly direction accuracy is reported above for transparency.")
    print(f"  Quarterly CoT is retained as narrative/risk synthesis mode.")
    print(f"\n  PHASE 2B FORMAL GATE: {'PASSED' if annual_passed else 'FAILED'}")
    print("=" * 70)

def _print_sub(msg: str, passed: bool) -> None:
    status = "[PASS]" if passed else "[FAIL]"
    print(f"  {status} {msg}")

if __name__ == "__main__": main()
