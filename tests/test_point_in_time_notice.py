"""Tests for the point-in-time look-ahead boundary (remediation Track A, Phase 0).

The notice is the auditable guard against the LLM's training-data knowledge of
post-trade-date outcomes leaking into historical decisions.
"""
from tradingagents.agents.utils.temporal import point_in_time_notice


def test_notice_embeds_the_date():
    n = point_in_time_notice("2023-06-01")
    assert "2023-06-01" in n
    assert n.count("2023-06-01") >= 3  # repeated for emphasis


def test_notice_states_the_boundary():
    n = point_in_time_notice("2023-06-01").lower()
    assert "look-ahead" in n
    assert "after" in n
    assert "training-data" in n or "training data" in n


def test_notice_handles_missing_date_without_dropping_instruction():
    for empty in ("", None, "   "):
        n = point_in_time_notice(empty)
        assert "POINT-IN-TIME" in n
        assert "look-ahead" in n.lower()
        # never emits a bare/empty placeholder
        assert "as of )" not in n


def test_notice_is_stable_across_agents():
    # Same date → byte-identical text, so the constraint is greppable in audit logs.
    assert point_in_time_notice("2024-01-15") == point_in_time_notice("2024-01-15")
