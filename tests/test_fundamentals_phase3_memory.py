"""
Phase 3 memory tests for the EGX Fundamental Analyst.

These tests are no-LLM diagnostics. Fake LLMs return fixed JSON so the tests
exercise memory plumbing without running an evaluation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from tradingagents.agents.analysts.fundamentals.data_cot import build_evidence_pack
from tradingagents.agents.analysts.fundamentals.memory_manager import (
    FundamentalMemoryManager,
    compute_importance_score,
)
from tradingagents.agents.analysts.fundamentals.memory_schemas import (
    OperationalMemoryItem,
    StrategicMemoryItem,
)
from tradingagents.agents.analysts.fundamentals.pipeline import run_cot_pipeline
from tradingagents.agents.analysts.fundamentals.schemas import FundamentalAnalysisReport
from tradingagents.agents.analysts.fundamentals.sector_config import SectorConfig


def _report() -> FundamentalAnalysisReport:
    return FundamentalAnalysisReport(
        ticker="COMI",
        analysis_date="2024-04-01",
        fiscal_period="2023-12-31",
        sector="banks",
        ratios={
            "roe": 0.18,
            "roa": 0.025,
            "gross_margin": 0.45,
            "operating_margin": 0.28,
            "net_margin": 0.20,
            "debt_to_equity": 7.0,
            "current_ratio": 1.2,
            "eps": 3.5,
            "pe_ratio": 8.0,
            "pb_ratio": 1.4,
            "piotroski_score": 5,
        },
        preprocessing={
            "revenue_growth_yoy": 0.12,
            "net_income_growth_yoy": 0.18,
            "directions": {
                "revenue": "improving",
                "net_income": "improving",
                "roe": "improving",
                "net_margin": "improving",
            },
        },
        distress_flags=[],
        data_confidence=85,
        signal_coherence=100,
        financial_health="healthy",
        valuation_assessment="",
        earnings_direction="",
        earnings_direction_confidence=0,
        thesis_text="",
        key_risks=["EGX market structure risk"],
        pipeline_mode="deterministic",
        stages_completed=[],
    )


def _op_item(
    ticker: str = "COMI",
    period: str = "2023-12-31",
    frequency: str = "annual",
    thesis: str = "Margins improved while loan growth stayed disciplined.",
    prediction: str = "up",
    source_run_id: str = "run-1",
) -> dict:
    return {
        "ticker": ticker,
        "period_end_date": period,
        "frequency": frequency,
        "thesis_text": thesis,
        "earnings_direction_prediction": prediction,
        "earnings_direction_confidence": 72,
        "actual_earnings_direction": None,
        "thesis_vs_actual_delta": None,
        "ratio_snapshot": {"roe": 0.18, "net_margin": 0.20},
        "key_risks": ["margin compression risk"],
        "distress_flags": [],
        "data_confidence": 85,
        "signal_coherence": 100,
        "source_run_id": source_run_id,
    }


@dataclass
class _Response:
    content: str


class _FakeLLM:
    def __init__(self, payload: dict):
        self.payload = payload
        self.prompts = []

    def invoke(self, messages):
        self.prompts.append(messages)
        return _Response(json.dumps(self.payload))


def _fake_concept_llm() -> _FakeLLM:
    return _FakeLLM({
        "financial_health": "healthy",
        "financial_health_rationale": "Profitability and growth are supportive.",
        "key_metrics_discussion": "ROE, net margin, and income growth are the key signals.",
        "standout_signals": ["net income growth"],
        "risk_factors": ["margin compression risk"],
        "growth_signal": "positive",
        "growth_signal_rationale": "Net income and revenue are improving.",
        "valuation_read": "fair",
        "valuation_rationale": "Valuation is not extreme.",
        "coherence_notes": "none",
        "analyst_note": "none",
    })


def _fake_thesis_llm() -> _FakeLLM:
    return _FakeLLM({
        "hypothesis": "COMI earnings should improve if profitability momentum persists.",
        "evidence_for": ["Net income growth is positive.", "ROE is healthy."],
        "evidence_against": ["Margin compression risk remains."],
        "synthesis": "The positive growth signal outweighs the listed risk.",
        "thesis_text": "COMI has supportive profitability and growth evidence, while margin risk remains the main caveat.",
        "earnings_direction": "up",
        "earnings_direction_confidence": 72,
        "earnings_direction_rationale": "Net income growth is positive.",
        "valuation_assessment": "fair_value",
        "valuation_rationale": "Valuation is not extreme.",
        "financial_health": "healthy",
        "key_risks": ["margin compression risk"],
        "egx_specific_risks": ["EGX liquidity risk"],
        "invalidation_conditions": ["Net income turns negative."],
    })


def test_memory_disabled_preserves_existing_behavior(tmp_path):
    mgr = FundamentalMemoryManager(storage_dir=tmp_path)
    mgr.write_operational_memory(_op_item())

    concept = _fake_concept_llm()
    thesis = _fake_thesis_llm()
    result = run_cot_pipeline(
        quick_llm=concept,
        deep_llm=thesis,
        report=_report(),
        sector_cfg=SectorConfig("COMI"),
        use_memory=False,
        memory_manager=mgr,
    )

    prompt_text = str(concept.prompts[0][-1].content)
    assert "PRIOR FUNDAMENTAL MEMORY CONTEXT" not in prompt_text
    assert len(mgr.retrieve_operational_memory("COMI")) == 1
    assert result.earnings_direction == "up"


def test_operational_memory_write_read(tmp_path):
    mgr = FundamentalMemoryManager(storage_dir=tmp_path)
    written = mgr.write_operational_memory(_op_item())

    assert isinstance(written, OperationalMemoryItem)
    items = mgr.retrieve_operational_memory("COMI", frequency="annual")
    assert len(items) == 1
    assert items[0]["ticker"] == "COMI"
    assert items[0]["memory_tier"] == "operational"
    assert items[0]["source_run_id"] == "run-1"


def test_strategic_memory_write_read(tmp_path):
    mgr = FundamentalMemoryManager(storage_dir=tmp_path)
    written = mgr.write_strategic_memory(
        ticker="COMI",
        structural_observations=["Single reflected outcome available; not yet a structural pattern."],
        recurring_risk_themes=[],
        recurring_data_issues=[],
        cumulative_accuracy={"total_predictions": 1, "correct": 1, "hit_rate": 1.0},
        persistent_narrative="Single reflected outcome available; not yet a structural pattern.",
        source_run_ids=["run-1"],
    )

    assert isinstance(written, StrategicMemoryItem)
    items = mgr.retrieve_strategic_memory("COMI")
    assert len(items) == 1
    assert items[0]["memory_tier"] == "strategic"
    assert items[0]["importance"] == 5


def test_ttl_pruning_and_last_four_retention(tmp_path):
    mgr = FundamentalMemoryManager(storage_dir=tmp_path)
    for idx, period in enumerate(["2020-12-31", "2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31"]):
        mgr.write_operational_memory(_op_item(period=period, source_run_id=f"annual-{idx}"))

    annual_items = mgr.retrieve_operational_memory("COMI", frequency="annual", top_n=10)
    assert len(annual_items) == 4
    assert "2020-12-31" not in {item["period_end_date"] for item in annual_items}

    mgr.write_operational_memory(_op_item(period="2023-01-01", frequency="quarterly", source_run_id="old-q"))
    mgr.write_operational_memory(_op_item(period="2024-12-31", frequency="quarterly", source_run_id="new-q"))
    removed = mgr.prune_expired_operational_memory(
        ticker="COMI",
        frequency="quarterly",
        as_of_date="2025-04-01T00:00:00+00:00",
    )
    quarterly_items = mgr.retrieve_operational_memory(
        "COMI",
        frequency="quarterly",
        as_of_date="2025-04-01T00:00:00+00:00",
    )
    assert removed == 1
    assert [item["source_run_id"] for item in quarterly_items] == ["new-q"]


def test_keyword_bm25_fallback_without_embeddings(tmp_path):
    mgr = FundamentalMemoryManager(storage_dir=tmp_path, use_embeddings=False)
    mgr.write_operational_memory(_op_item(thesis="Margin compression and liquidity pressure.", source_run_id="risk"))
    mgr.write_operational_memory(_op_item(period="2022-12-31", thesis="Strong loan growth and fee income.", source_run_id="growth"))

    items = mgr.retrieve_operational_memory("COMI", query="liquidity margin pressure", top_n=1)
    assert items[0]["source_run_id"] == "risk"


def test_prior_context_injection_when_available(tmp_path):
    mgr = FundamentalMemoryManager(storage_dir=tmp_path)
    mgr.write_operational_memory(_op_item())
    context = mgr.build_prior_context("COMI", frequency="annual", query="margin risk")

    pack = build_evidence_pack(
        _report(),
        SectorConfig("COMI"),
        prior_memory_context=context,
    )
    assert "PRIOR FUNDAMENTAL MEMORY CONTEXT" in pack["narrative"]
    assert "margin compression risk" in pack["narrative"]


def test_no_memory_context_is_graceful(tmp_path):
    mgr = FundamentalMemoryManager(storage_dir=tmp_path)
    context = mgr.build_prior_context("COMI", frequency="annual")
    assert context == (
        "PRIOR FUNDAMENTAL MEMORY CONTEXT\n"
        "No prior memory available for this ticker/frequency."
    )


def test_memory_does_not_overwrite_deterministic_fields(tmp_path):
    mgr = FundamentalMemoryManager(storage_dir=tmp_path)
    mgr.write_operational_memory(_op_item(thesis="Prior period had a different ROE.", prediction="down"))
    report = _report()

    result = run_cot_pipeline(
        quick_llm=_fake_concept_llm(),
        deep_llm=_fake_thesis_llm(),
        report=report,
        sector_cfg=SectorConfig("COMI"),
        use_memory=True,
        memory_manager=mgr,
    )

    assert result.ratios == report.ratios
    assert result.preprocessing == report.preprocessing
    assert result.distress_flags == report.distress_flags
    assert result.data_confidence == report.data_confidence
    assert result.signal_coherence == report.signal_coherence


def test_reflection_without_actual_outcome(tmp_path):
    mgr = FundamentalMemoryManager(storage_dir=tmp_path)
    report = _report().model_copy(update={
        "earnings_direction": "up",
        "earnings_direction_confidence": 72,
        "thesis_text": "Profitability momentum supports earnings growth.",
    })
    item = mgr.record_reflection(report, source_run_id="reflection-1")

    assert item.actual_earnings_direction is None
    assert item.thesis_vs_actual_delta is None
    assert item.importance == 5
    assert mgr.retrieve_strategic_memory("COMI") == []


def test_reflection_update_with_actual_outcome(tmp_path):
    mgr = FundamentalMemoryManager(storage_dir=tmp_path)
    report = _report().model_copy(update={
        "earnings_direction": "up",
        "earnings_direction_confidence": 72,
        "thesis_text": "Profitability momentum supports earnings growth.",
    })
    mgr.record_reflection(report, source_run_id="reflection-1")
    updated = mgr.record_reflection(
        report,
        actual_earnings_direction="down",
        source_run_id="reflection-1",
    )

    assert updated.actual_earnings_direction == "down"
    assert updated.thesis_vs_actual_delta == "Prediction was up, actual was down."
    strategic = mgr.retrieve_strategic_memory("COMI")
    assert strategic[0]["cumulative_accuracy"]["total_predictions"] == 1
    assert strategic[0]["cumulative_accuracy"]["correct"] == 0


def test_output_contract_preserved_with_memory_disabled():
    result = run_cot_pipeline(
        quick_llm=_fake_concept_llm(),
        deep_llm=_fake_thesis_llm(),
        report=_report(),
        sector_cfg=SectorConfig("COMI"),
        use_memory=False,
    )
    dumped = result.model_dump()

    for field in ("ticker", "ratios", "preprocessing", "data_confidence", "signal_coherence", "earnings_direction"):
        assert field in dumped
    assert isinstance(dumped["ratios"], dict)
    assert result.pipeline_mode == "cot_full"


def test_importance_score_rules():
    assert compute_importance_score("up", 70, "up", {"roe": 0.1}) == 5
    assert compute_importance_score("up", 70, None, {"roe": 0.1}) == 5
    assert compute_importance_score(None, 0, None, {"roe": 0.1}) == 3


# ---------------------------------------------------------------------------
# Temporal isolation tests — verify as_of_date prevents lookahead leakage
# ---------------------------------------------------------------------------

def test_build_prior_context_excludes_future_writes(tmp_path):
    """Memory written AFTER as_of_date must not appear in build_prior_context output.

    Scenario: backtest analysis for 2020-01-01 must not see a memory record
    written during a 2023 run, even though the record has a prior fiscal period.
    """
    mgr = FundamentalMemoryManager(storage_dir=tmp_path)
    future_item = {
        **_op_item(period="2022-12-31", source_run_id="future-run"),
        "write_date": "2023-05-01T10:00:00+00:00",   # written AFTER as_of_date
        "thesis_text": "THIS_THESIS_MUST_NOT_APPEAR",
    }
    mgr.write_operational_memory(future_item)

    context = mgr.build_prior_context("COMI", frequency="annual", as_of_date="2020-01-01")

    assert "THIS_THESIS_MUST_NOT_APPEAR" not in context
    # With no qualifying records the context should indicate nothing available
    assert "No prior memory available" in context


def test_build_prior_context_includes_past_writes(tmp_path):
    """Memory written BEFORE as_of_date must appear in build_prior_context output."""
    mgr = FundamentalMemoryManager(storage_dir=tmp_path)
    past_item = {
        **_op_item(period="2018-12-31", source_run_id="past-run"),
        "write_date": "2019-03-15T08:00:00+00:00",   # written BEFORE as_of_date
        "thesis_text": "THIS_THESIS_MUST_APPEAR",
    }
    mgr.write_operational_memory(past_item)

    context = mgr.build_prior_context("COMI", frequency="annual", as_of_date="2020-01-01")

    assert "THIS_THESIS_MUST_APPEAR" in context
    assert "No prior memory available" not in context


def test_filter_annual_respects_as_of_date(tmp_path):
    """_filter_operational_ttl() must exclude annual items whose write_date is
    after as_of_date and include items whose write_date is on or before it."""
    mgr = FundamentalMemoryManager(storage_dir=tmp_path)

    item_future = {
        **_op_item(period="2022-12-31", source_run_id="written-2023"),
        "write_date": "2023-06-01T00:00:00+00:00",
    }
    item_past = {
        **_op_item(period="2018-12-31", source_run_id="written-2019"),
        "write_date": "2019-03-01T00:00:00+00:00",
    }

    # Future write — must be excluded when as_of_date is 2020-01-01
    result_future = mgr._filter_operational_ttl([item_future], as_of_date="2020-01-01")
    assert result_future == [], "annual item with write_date > as_of_date must be excluded"

    # Past write — must be included when as_of_date is 2020-01-01
    result_past = mgr._filter_operational_ttl([item_past], as_of_date="2020-01-01")
    assert len(result_past) == 1, "annual item with write_date <= as_of_date must be included"
    assert result_past[0]["source_run_id"] == "written-2019"

    # No as_of_date — both must pass through (backward-compatible behaviour)
    result_no_guard = mgr._filter_operational_ttl([item_future, item_past], as_of_date=None)
    assert len(result_no_guard) == 2, "without as_of_date all annual items must pass through"


def test_filter_quarterly_excludes_future_dated_records(tmp_path):
    """_filter_operational_ttl() must exclude quarterly records whose period_end_date
    is after as_of_date (negative age_days).

    Regression test for the `0 <= age_days` guard added alongside the annual
    write_date fix. Before the fix, the check was only `age_days <= 365`, which
    also passed for negative values — letting future-dated quarterly records
    through unchanged.

    as_of_date = 2022-01-01

    Future quarterly item:  period_end_date = 2023-06-30
        age_days = (2022-01-01 - 2023-06-30).days = -545  →  excluded (< 0)

    Past quarterly item:    period_end_date = 2021-09-30
        age_days = (2022-01-01 - 2021-09-30).days = 93    →  included (0 <= 93 <= 365)
    """
    mgr = FundamentalMemoryManager(storage_dir=tmp_path)

    AS_OF = "2022-01-01"

    # Future-dated quarterly item — period_end_date is AFTER as_of_date
    item_future_q = {
        **_op_item(period="2023-06-30", frequency="quarterly", source_run_id="future-q"),
        "write_date": "2023-09-01T00:00:00+00:00",
    }

    # Past quarterly item within TTL — period_end_date is 93 days before as_of_date
    item_past_q = {
        **_op_item(period="2021-09-30", frequency="quarterly", source_run_id="past-q"),
        "write_date": "2021-11-15T00:00:00+00:00",
    }

    # Future-dated quarterly must be excluded
    result_future = mgr._filter_operational_ttl([item_future_q], as_of_date=AS_OF)
    assert result_future == [], (
        "quarterly item with period_end_date > as_of_date (negative age_days) must be excluded"
    )

    # Past quarterly within TTL must be included
    result_past = mgr._filter_operational_ttl([item_past_q], as_of_date=AS_OF)
    assert len(result_past) == 1, (
        "quarterly item with 0 <= age_days <= TTL must be included"
    )
    assert result_past[0]["source_run_id"] == "past-q"

    # Without as_of_date, the TTL is measured from datetime.now(). Use a recent
    # item (within the last 90 days relative to a hardcoded recent date) to verify
    # the backward-compat path doesn't accidentally gate on as_of_date.
    from datetime import datetime, timezone, timedelta
    recent_period = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
    item_recent_q = {
        **_op_item(period=recent_period, frequency="quarterly", source_run_id="recent-q"),
        "write_date": (datetime.now(timezone.utc) - timedelta(days=85)).isoformat(),
    }
    result_no_guard = mgr._filter_operational_ttl([item_recent_q], as_of_date=None)
    assert len(result_no_guard) == 1, (
        "recent quarterly item (within TTL) must pass when as_of_date=None"
    )


# ---------------------------------------------------------------------------
# End-to-end prompt injection test — proves filtered memory reaches the LLM
# ---------------------------------------------------------------------------

def test_cot_prompt_receives_only_as_of_safe_memory(tmp_path):
    """Integration test: the concept_cot and thesis_cot LLM prompts must contain
    only memory that was written on or before report.analysis_date.

    Data-flow under test:
      report.analysis_date
        → run_cot_pipeline() calls build_prior_context(as_of_date=report.analysis_date)
        → _filter_operational_ttl() excludes future write_date records
        → filtered context string returned
        → build_evidence_pack() embeds context in evidence_pack["narrative"]
        → concept_cot: narrative is the HumanMessage content
        → thesis_cot: narrative is also embedded in the HumanMessage content
        → _FakeLLM.prompts captures every invoke() call for assertion

    Past memory (write_date before analysis_date) must appear in both prompts.
    Future memory (write_date after analysis_date) must not appear in either.
    """
    mgr = FundamentalMemoryManager(storage_dir=tmp_path)

    # Past memory — written 2019-06-01, analysis_date is 2020-01-01 → safe to use
    past_item = {
        **_op_item(period="2018-12-31", source_run_id="past-run"),
        "write_date": "2019-06-01T00:00:00+00:00",
        "thesis_text": "PAST_MEMORY_SAFE_MARKER",
    }
    # Future memory — written 2022-01-15, analysis_date is 2020-01-01 → must be blocked
    future_item = {
        **_op_item(period="2021-12-31", source_run_id="future-run"),
        "write_date": "2022-01-15T00:00:00+00:00",
        "thesis_text": "FUTURE_MEMORY_LEAKED_MARKER",
    }
    mgr.write_operational_memory(past_item)
    mgr.write_operational_memory(future_item)

    # report.analysis_date = "2020-01-01" is the temporal gate
    report = _report().model_copy(update={"analysis_date": "2020-01-01"})
    concept_llm = _fake_concept_llm()
    thesis_llm = _fake_thesis_llm()

    run_cot_pipeline(
        quick_llm=concept_llm,
        deep_llm=thesis_llm,
        report=report,
        sector_cfg=SectorConfig("COMI"),
        use_memory=True,
        memory_manager=mgr,
    )

    # Both LLMs must have been called (pipeline ran to completion)
    assert concept_llm.prompts, "concept_cot LLM was never invoked"
    assert thesis_llm.prompts, "thesis_cot LLM was never invoked"

    # Concatenate all message content sent to each LLM
    concept_prompt_text = " ".join(
        m.content for m in concept_llm.prompts[0] if hasattr(m, "content")
    )
    thesis_prompt_text = " ".join(
        m.content for m in thesis_llm.prompts[0] if hasattr(m, "content")
    )

    # Past memory must appear in both concept_cot and thesis_cot prompts
    assert "PAST_MEMORY_SAFE_MARKER" in concept_prompt_text, (
        "past memory (write_date < analysis_date) was not injected into concept_cot prompt"
    )
    assert "PAST_MEMORY_SAFE_MARKER" in thesis_prompt_text, (
        "past memory (write_date < analysis_date) was not injected into thesis_cot prompt"
    )

    # Future memory must NOT appear in either LLM's prompt
    assert "FUTURE_MEMORY_LEAKED_MARKER" not in concept_prompt_text, (
        "temporal leakage: future memory (write_date > analysis_date) reached concept_cot prompt"
    )
    assert "FUTURE_MEMORY_LEAKED_MARKER" not in thesis_prompt_text, (
        "temporal leakage: future memory (write_date > analysis_date) reached thesis_cot prompt"
    )


# ---------------------------------------------------------------------------
# Strategic memory temporal isolation tests
# ---------------------------------------------------------------------------

def _strategic_item(
    ticker: str = "COMI",
    last_updated: str = "2019-03-01T00:00:00+00:00",
    observation: str = "Default structural observation.",
) -> dict:
    """Minimal strategic memory payload for tests."""
    return {
        "ticker": ticker,
        "structural_observations": [observation],
        "recurring_risk_themes": [],
        "recurring_data_issues": [],
        "cumulative_accuracy": {"total_predictions": 1, "correct": 1, "hit_rate": 1.0},
        "persistent_narrative": observation,
        "last_updated": last_updated,
        "source_run_ids": ["test-run"],
    }


def test_retrieve_strategic_memory_excludes_future_last_updated(tmp_path):
    """Strategic memory whose last_updated is AFTER as_of_date must be excluded."""
    mgr = FundamentalMemoryManager(storage_dir=tmp_path)
    mgr.write_strategic_memory(
        **_strategic_item(
            last_updated="2023-06-01T00:00:00+00:00",
            observation="FUTURE_STRATEGIC_MUST_NOT_APPEAR",
        )
    )

    items = mgr.retrieve_strategic_memory("COMI", as_of_date="2020-01-01")
    assert items == [], (
        "strategic item with last_updated > as_of_date must be excluded"
    )


def test_retrieve_strategic_memory_includes_past_last_updated(tmp_path):
    """Strategic memory whose last_updated is BEFORE as_of_date must be included."""
    mgr = FundamentalMemoryManager(storage_dir=tmp_path)
    mgr.write_strategic_memory(
        **_strategic_item(
            last_updated="2019-03-01T00:00:00+00:00",
            observation="PAST_STRATEGIC_MUST_APPEAR",
        )
    )

    items = mgr.retrieve_strategic_memory("COMI", as_of_date="2020-01-01")
    assert len(items) == 1, (
        "strategic item with last_updated <= as_of_date must be included"
    )
    assert "PAST_STRATEGIC_MUST_APPEAR" in items[0]["structural_observations"][0]


def test_retrieve_strategic_memory_live_mode_preserves_behavior(tmp_path):
    """Without as_of_date, retrieve_strategic_memory must return all records
    for the ticker (live-mode backward-compatible behavior)."""
    mgr = FundamentalMemoryManager(storage_dir=tmp_path)
    # Write a record with a future last_updated — must pass without guard
    mgr.write_strategic_memory(
        **_strategic_item(
            last_updated="2030-01-01T00:00:00+00:00",
            observation="LIVE_MODE_RECORD",
        )
    )

    items = mgr.retrieve_strategic_memory("COMI", as_of_date=None)
    assert len(items) == 1, (
        "without as_of_date all strategic records must be returned"
    )
    assert "LIVE_MODE_RECORD" in items[0]["structural_observations"][0]


def test_cot_prompt_receives_only_as_of_safe_strategic_memory(tmp_path):
    """End-to-end prompt test: the LLM must see past strategic memory and
    must NOT see future strategic memory.

    Data-flow under test:
      report.analysis_date
        → run_cot_pipeline() → build_prior_context(as_of_date=report.analysis_date)
        → retrieve_strategic_memory(as_of_date=as_of_date)   ← new guard
        → last_updated <= as_of filter
        → filtered strategic context included in build_prior_context() output
        → build_evidence_pack() embeds output in evidence_pack["narrative"]
        → concept_cot and thesis_cot receive narrative in HumanMessage
    """
    mgr = FundamentalMemoryManager(storage_dir=tmp_path)

    # Past strategic item — last_updated 2019, analysis_date is 2020-01-01 → safe
    mgr.write_strategic_memory(
        **_strategic_item(
            last_updated="2019-06-01T00:00:00+00:00",
            observation="PAST_STRATEGIC_SAFE_MARKER",
        )
    )
    # Future strategic item — last_updated 2022, analysis_date is 2020-01-01 → blocked
    # write_strategic_memory replaces the ticker record, so we write directly to JSONL
    # by writing a second ticker variant then patching via low-level helper.
    # Instead, use a different ticker and re-check that the first is gated correctly,
    # OR write the second strategic record by temporarily swapping tickers.
    # The cleanest approach: write both records as separate source_run_ids to the
    # strategic JSONL directly so both coexist for ticker COMI.
    import json as _json
    future_strategic_raw = {
        "ticker": "COMI",
        "memory_tier": "strategic",
        "structural_observations": ["FUTURE_STRATEGIC_LEAKED_MARKER"],
        "recurring_risk_themes": [],
        "recurring_data_issues": [],
        "recurring_thesis_mistakes": [],
        "source_data_limitations": [],
        "cumulative_accuracy": {},
        "persistent_narrative": "FUTURE_STRATEGIC_LEAKED_MARKER",
        "last_updated": "2022-06-01T00:00:00+00:00",
        "importance": 5,
        "source_run_ids": ["future-strategic-run"],
        "sector_specific_notes": [],
    }
    strategic_path = mgr.strategic_path
    with strategic_path.open("a", encoding="utf-8") as fh:
        fh.write(_json.dumps(future_strategic_raw, sort_keys=True) + "\n")

    report = _report().model_copy(update={"analysis_date": "2020-01-01"})
    concept_llm = _fake_concept_llm()
    thesis_llm = _fake_thesis_llm()

    run_cot_pipeline(
        quick_llm=concept_llm,
        deep_llm=thesis_llm,
        report=report,
        sector_cfg=SectorConfig("COMI"),
        use_memory=True,
        memory_manager=mgr,
    )

    assert concept_llm.prompts, "concept_cot LLM was never invoked"
    assert thesis_llm.prompts, "thesis_cot LLM was never invoked"

    concept_prompt_text = " ".join(
        m.content for m in concept_llm.prompts[0] if hasattr(m, "content")
    )
    thesis_prompt_text = " ".join(
        m.content for m in thesis_llm.prompts[0] if hasattr(m, "content")
    )

    # Past strategic memory must appear in the prompts
    assert "PAST_STRATEGIC_SAFE_MARKER" in concept_prompt_text, (
        "past strategic memory (last_updated < analysis_date) was not injected into concept_cot prompt"
    )
    assert "PAST_STRATEGIC_SAFE_MARKER" in thesis_prompt_text, (
        "past strategic memory (last_updated < analysis_date) was not injected into thesis_cot prompt"
    )

    # Future strategic memory must NOT appear in any prompt
    assert "FUTURE_STRATEGIC_LEAKED_MARKER" not in concept_prompt_text, (
        "temporal leakage: future strategic memory reached concept_cot prompt"
    )
    assert "FUTURE_STRATEGIC_LEAKED_MARKER" not in thesis_prompt_text, (
        "temporal leakage: future strategic memory reached thesis_cot prompt"
    )
