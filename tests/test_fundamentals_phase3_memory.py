"""
Phase 3 Memory Test Stubs for EGX Fundamental Analyst.

These tests are SKIPPED until memory_manager.py is fully implemented (Phase 3).
Each stub has a detailed docstring describing the exact behaviour to verify.

Run these once Phase 3 gate (Phase 2B) is passed:
    pytest tests/test_fundamentals_phase3_memory.py -v

The @pytest.mark.skip markers will be removed as each test becomes implementable.

Design references:
  - Plan Section 3: 2-Tier Memory + Reflection
  - OperationalMemoryItem: one item per fiscal period per ticker, retain last 4
  - StrategicMemoryItem: one item per ticker, maintained indefinitely, overwrite on run
  - Period-based eviction (not calendar-day TTL): plan Section 3, "Why period-based"
  - Importance score rules: complete=5, pending=4, ratio-only=3, strategic=always 5
  - Dual backend: ChromaDB (embedding) OR BM25 (keyword fallback via rank_bm25)
"""

import pytest
from typing import Any, Dict, List, Optional


# =============================================================================
# Schema tests — OperationalMemoryItem and StrategicMemoryItem
# =============================================================================

@pytest.mark.skip(reason="Phase 3 not yet implemented — implement after Phase 2B gate passes")
def test_operational_memory_item_schema_valid():
    """
    OperationalMemoryItem must be a TypedDict (or equivalent) with exactly these fields:
      ticker (str)
      fiscal_period (str)           — e.g. "2024-Q3" or "FY2024"
      thesis_direction (str)        — "up" | "down" | "flat"
      thesis_confidence (int)       — 0–100
      thesis_summary (str)          — ≤ 200 chars
      actual_direction (str | None) — "up" | "down" | "flat" | None
      prediction_correct (bool | None)
      ratio_snapshot (dict)         — {metric: value} for 14 core ratios
      importance_score (int)        — 3 | 4 | 5

    This test should:
      1. Import OperationalMemoryItem from memory_manager
      2. Construct a valid item with all required fields
      3. Assert all fields are present and have the correct types
      4. Assert thesis_summary is ≤ 200 characters when set
    """
    from tradingagents.agents.analysts.fundamentals.memory_manager import OperationalMemoryItem
    item: OperationalMemoryItem = {
        "ticker": "COMI",
        "fiscal_period": "FY2024",
        "thesis_direction": "up",
        "thesis_confidence": 72,
        "thesis_summary": "Strong revenue growth and margin improvement expected.",
        "actual_direction": None,
        "prediction_correct": None,
        "ratio_snapshot": {"roe": 0.15, "net_margin": 0.20},
        "importance_score": 4,  # pending — no actual_direction yet
    }
    assert item["ticker"] == "COMI"
    assert item["thesis_direction"] in ("up", "down", "flat")
    assert 0 <= item["thesis_confidence"] <= 100
    assert len(item["thesis_summary"]) <= 200
    assert item["importance_score"] in (3, 4, 5)
    assert item["actual_direction"] is None
    assert item["prediction_correct"] is None


@pytest.mark.skip(reason="Phase 3 not yet implemented — implement after Phase 2B gate passes")
def test_strategic_memory_item_schema_valid():
    """
    StrategicMemoryItem must be a TypedDict (or equivalent) with exactly these fields:
      ticker (str)
      sector (str)
      last_updated_period (str)
      cumulative_accuracy (dict)     — {"total_predictions": int, "correct": int, "hit_rate": float}
      structural_flags (list[str])   — each entry ≤ 120 chars, factual observation
      recurring_data_issues (list[str])
      importance_score (int)         — always 5

    This test should:
      1. Import StrategicMemoryItem from memory_manager
      2. Construct a valid item
      3. Assert importance_score is always 5
      4. Assert all structural_flags entries are ≤ 120 chars
    """
    from tradingagents.agents.analysts.fundamentals.memory_manager import StrategicMemoryItem
    item: StrategicMemoryItem = {
        "ticker": "OCDI",
        "sector": "real_estate",
        "last_updated_period": "FY2024",
        "cumulative_accuracy": {
            "total_predictions": 4,
            "correct": 3,
            "hit_rate": 0.75,
        },
        "structural_flags": [
            "P/B understated ~40% due to historical-cost land valuation",
            "Revenue recognition is lumpy — percent-completion method",
        ],
        "recurring_data_issues": [
            "eps_basic missing in 2 of 4 periods",
        ],
        "importance_score": 5,
    }
    assert item["importance_score"] == 5, "StrategicMemoryItem importance_score must always be 5"
    assert all(len(flag) <= 120 for flag in item["structural_flags"])
    assert all(len(issue) <= 120 for issue in item["recurring_data_issues"])
    assert isinstance(item["cumulative_accuracy"]["hit_rate"], float)


# =============================================================================
# Retention policy tests
# =============================================================================

@pytest.mark.skip(reason="Phase 3 not yet implemented — implement after Phase 2B gate passes")
def test_period_based_eviction_not_calendar_ttl():
    """
    The operational memory tier must evict by period count, NOT by calendar days.

    Scenario:
      A company that files annually has its most recent data be 11 months old.
      If the memory manager were to evict by calendar TTL of 365 days, it would
      discard data that is still analytically current.
      Period-based eviction retains data until the 5th item is written (evicting the oldest).

    This test should:
      1. Write 4 OperationalMemoryItems for the same ticker with 4 different fiscal_periods
      2. Check that all 4 are retained
      3. Write a 5th item — the oldest must be evicted, leaving exactly 4
      4. Verify the eviction is by fiscal_period order, not by a calendar date field

    Reference: Plan Section 3, "Why period-based, not calendar-day TTL"
    """
    from tradingagents.agents.analysts.fundamentals.memory_manager import MemoryManager

    mgr = MemoryManager()
    ticker = "ETEL"

    periods = ["FY2021", "FY2022", "FY2023", "FY2024"]
    for i, period in enumerate(periods):
        mgr.write_operational(ticker, {
            "ticker": ticker,
            "fiscal_period": period,
            "thesis_direction": "up",
            "thesis_confidence": 60,
            "thesis_summary": f"Period {period} thesis.",
            "actual_direction": "up",
            "prediction_correct": True,
            "ratio_snapshot": {"roe": 0.15},
            "importance_score": 5,
        })

    # All 4 should be retained
    items = mgr.get_operational(ticker)
    assert len(items) == 4

    # Write 5th — oldest (FY2021) must be evicted
    mgr.write_operational(ticker, {
        "ticker": ticker,
        "fiscal_period": "FY2025",
        "thesis_direction": "up",
        "thesis_confidence": 65,
        "thesis_summary": "FY2025 thesis.",
        "actual_direction": None,
        "prediction_correct": None,
        "ratio_snapshot": {"roe": 0.16},
        "importance_score": 4,
    })

    items = mgr.get_operational(ticker)
    assert len(items) == 4, f"Expected 4 items after eviction, got {len(items)}"
    periods_present = {item["fiscal_period"] for item in items}
    assert "FY2021" not in periods_present, "FY2021 must be evicted (oldest)"
    assert "FY2025" in periods_present, "FY2025 must be retained (newest)"


@pytest.mark.skip(reason="Phase 3 not yet implemented — implement after Phase 2B gate passes")
def test_operational_tier_keeps_last_4_periods():
    """
    The operational tier must retain at most 4 items per ticker.

    This is a stricter version of test_period_based_eviction_not_calendar_ttl —
    it specifically tests that writing 8 items results in exactly 4 retained.

    Reference: Plan Section 3, "Operational Tier: Retention: keep last 4 items"
    """
    from tradingagents.agents.analysts.fundamentals.memory_manager import MemoryManager

    mgr = MemoryManager()
    ticker = "JUFO"

    for i in range(8):
        mgr.write_operational(ticker, {
            "ticker": ticker,
            "fiscal_period": f"FY{2017 + i}",
            "thesis_direction": "up",
            "thesis_confidence": 60,
            "thesis_summary": "Thesis.",
            "actual_direction": "up",
            "prediction_correct": True,
            "ratio_snapshot": {},
            "importance_score": 5,
        })

    items = mgr.get_operational(ticker)
    assert len(items) == 4, f"Expected exactly 4 items, got {len(items)}"
    # Should contain the 4 most recent periods
    periods = sorted(item["fiscal_period"] for item in items)
    assert periods == ["FY2021", "FY2022", "FY2023", "FY2024"]


@pytest.mark.skip(reason="Phase 3 not yet implemented — implement after Phase 2B gate passes")
def test_strategic_tier_overwrites_not_appends():
    """
    The strategic tier must overwrite the existing StrategicMemoryItem on each run.

    Scenario:
      Write a StrategicMemoryItem for COMI with cumulative_accuracy.total_predictions=3.
      Then write another item for COMI with total_predictions=4 (updated after new quarter).
      The store must contain exactly ONE item for COMI, with total_predictions=4.

    Reference: Plan Section 3, "Strategic Tier: Indefinite; overwrite on each run"
    """
    from tradingagents.agents.analysts.fundamentals.memory_manager import MemoryManager

    mgr = MemoryManager()
    ticker = "COMI"

    mgr.write_strategic(ticker, {
        "ticker": ticker,
        "sector": "banks",
        "last_updated_period": "FY2023",
        "cumulative_accuracy": {"total_predictions": 3, "correct": 2, "hit_rate": 0.667},
        "structural_flags": [],
        "recurring_data_issues": [],
        "importance_score": 5,
    })

    # Update after new quarter
    mgr.write_strategic(ticker, {
        "ticker": ticker,
        "sector": "banks",
        "last_updated_period": "FY2024",
        "cumulative_accuracy": {"total_predictions": 4, "correct": 3, "hit_rate": 0.75},
        "structural_flags": ["D/E 6-9× structurally normal for COMI"],
        "recurring_data_issues": [],
        "importance_score": 5,
    })

    items = mgr.get_strategic(ticker)
    assert len(items) == 1, f"Strategic tier must hold exactly 1 item per ticker, got {len(items)}"
    assert items[0]["cumulative_accuracy"]["total_predictions"] == 4
    assert items[0]["last_updated_period"] == "FY2024"


# =============================================================================
# Memory injection tests
# =============================================================================

@pytest.mark.skip(reason="Phase 3 not yet implemented — implement after Phase 2B gate passes")
def test_memory_injection_into_evidence_pack():
    """
    When memory is available, the evidence pack (Stage 1) must include
    a 'prior_thesis_context' key with at most top-2 items from each tier.

    Scenario:
      1. Pre-populate memory for COMI with 2 operational items + 1 strategic item
      2. Call build_evidence_pack() with memory_manager wired in
      3. Assert the evidence pack contains 'prior_thesis_context' with the items

    Reference: Plan Section 2 Architecture diagram:
      "Stage 1 — data_cot.py: prior thesis context (Phase 3+)"
    """
    from tradingagents.agents.analysts.fundamentals.memory_manager import MemoryManager
    from tradingagents.agents.analysts.fundamentals.data_cot import build_evidence_pack
    from tradingagents.agents.analysts.fundamentals.schemas import FundamentalAnalysisReport
    from tradingagents.agents.analysts.fundamentals.sector_config import SectorConfig

    mgr = MemoryManager()
    mgr.write_operational("COMI", {
        "ticker": "COMI",
        "fiscal_period": "FY2023",
        "thesis_direction": "up",
        "thesis_confidence": 70,
        "thesis_summary": "Strong NIM expansion expected.",
        "actual_direction": "up",
        "prediction_correct": True,
        "ratio_snapshot": {"roe": 0.20},
        "importance_score": 5,
    })

    report = FundamentalAnalysisReport(
        ticker="COMI",
        analysis_date="2024-01-01",
        fiscal_period="FY2024",
        sector="banks",
        ratios={"roe": 0.22, "net_margin": 0.28},
        preprocessing={},
        distress_flags=[],
        data_confidence=85,
        signal_coherence=100,
        financial_health="healthy",
        pipeline_mode="deterministic",
    )

    sector_cfg = SectorConfig("COMI")
    pack = build_evidence_pack(report, sector_cfg, memory_manager=mgr)

    assert "prior_thesis_context" in pack, "Evidence pack must include prior_thesis_context when memory is available"
    prior = pack["prior_thesis_context"]
    assert isinstance(prior, dict)
    assert "operational" in prior or "strategic" in prior


# =============================================================================
# Backend tests
# =============================================================================

@pytest.mark.skip(reason="Phase 3 not yet implemented — implement after Phase 2B gate passes")
def test_bm25_fallback_when_no_embedding_backend():
    """
    When ChromaDB is unavailable, MemoryManager must automatically fall back to
    BM25 (via rank_bm25) without raising an exception.

    This test should:
      1. Instantiate MemoryManager with use_chromadb=False (or equivalent flag)
      2. Write and retrieve operational items
      3. Assert that retrieval still works and returns correct items

    Reference: Plan Section 3, "Groq compatibility: BM25 backend via rank_bm25"
    """
    from tradingagents.agents.analysts.fundamentals.memory_manager import MemoryManager

    # Force BM25 backend
    mgr = MemoryManager(backend="bm25")

    ticker = "HRHO"
    mgr.write_operational(ticker, {
        "ticker": ticker,
        "fiscal_period": "FY2024",
        "thesis_direction": "flat",
        "thesis_confidence": 45,
        "thesis_summary": "Holding company with mixed subsidiary performance.",
        "actual_direction": "flat",
        "prediction_correct": True,
        "ratio_snapshot": {"roe": 0.10},
        "importance_score": 5,
    })

    items = mgr.get_operational(ticker)
    assert len(items) == 1
    assert items[0]["ticker"] == ticker
    assert items[0]["fiscal_period"] == "FY2024"


# =============================================================================
# Importance score tests
# =============================================================================

@pytest.mark.skip(reason="Phase 3 not yet implemented — implement after Phase 2B gate passes")
def test_importance_score_rules_complete_record_scores_5():
    """
    An OperationalMemoryItem with thesis_direction, thesis_confidence,
    AND actual_direction all set must have importance_score = 5.

    Reference: Plan Section 3, "Importance score: 5 — complete (thesis + actual)"
    """
    from tradingagents.agents.analysts.fundamentals.memory_manager import compute_importance_score

    score = compute_importance_score(
        thesis_direction="up",
        thesis_confidence=72,
        actual_direction="up",     # actual is known
        ratio_snapshot={"roe": 0.15},
    )
    assert score == 5, f"Complete record must score 5, got {score}"


@pytest.mark.skip(reason="Phase 3 not yet implemented — implement after Phase 2B gate passes")
def test_importance_score_rules_pending_outcome_scores_4():
    """
    An OperationalMemoryItem with thesis fields but actual_direction=None (pending)
    must have importance_score = 4.

    Reference: Plan Section 3, "Importance score: 4 — pending outcome"
    """
    from tradingagents.agents.analysts.fundamentals.memory_manager import compute_importance_score

    score = compute_importance_score(
        thesis_direction="down",
        thesis_confidence=60,
        actual_direction=None,     # pending — outcome not yet known
        ratio_snapshot={"roe": 0.12},
    )
    assert score == 4, f"Pending outcome must score 4, got {score}"


@pytest.mark.skip(reason="Phase 3 not yet implemented — implement after Phase 2B gate passes")
def test_importance_score_rules_ratio_only_scores_3():
    """
    An OperationalMemoryItem with only ratio_snapshot (no thesis — deterministic-only run)
    must have importance_score = 3.

    Reference: Plan Section 3, "Importance score: 3 — ratio-only"
    """
    from tradingagents.agents.analysts.fundamentals.memory_manager import compute_importance_score

    score = compute_importance_score(
        thesis_direction=None,      # no thesis generated
        thesis_confidence=0,
        actual_direction=None,
        ratio_snapshot={"roe": 0.10, "net_margin": 0.05},
    )
    assert score == 3, f"Ratio-only record must score 3, got {score}"
