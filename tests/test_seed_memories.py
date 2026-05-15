"""Tests for the EGX seed memory corpus (PR 8).

Covers schema integrity, coverage across the 5 agent collections, and the
``get_seeds_for_agent`` filter contract that ``memory.py`` relies on.
"""

from __future__ import annotations

import json

import pytest

from tradingagents.agents.utils.seed_memories import (
    EGX_SEED_MEMORIES,
    all_agent_names,
    get_seeds_for_agent,
)


REQUIRED_KEYS = {
    "agent_name",
    "ticker",
    "memory_type",
    "trade_date",
    "situation",
    "recommendation",
    "outcome",
}

VALID_AGENT_NAMES = {
    "bull_memory",
    "bear_memory",
    "trader_memory",
    "invest_judge_memory",
    "risk_manager_memory",
}

VALID_MEMORY_TYPES = {"thesis", "execution", "risk_decision", "reflection"}


def test_corpus_is_non_empty_and_large_enough():
    """≥ 25 entries — enough to cover all 5 collections × ≥5 tickers."""
    assert len(EGX_SEED_MEMORIES) >= 25


def test_every_entry_has_required_keys():
    for i, entry in enumerate(EGX_SEED_MEMORIES):
        missing = REQUIRED_KEYS - set(entry.keys())
        assert not missing, f"Entry {i} missing keys: {missing}"


def test_every_agent_name_is_valid():
    for entry in EGX_SEED_MEMORIES:
        assert entry["agent_name"] in VALID_AGENT_NAMES, (
            f"unknown agent_name: {entry['agent_name']}"
        )


def test_every_memory_type_is_valid():
    for entry in EGX_SEED_MEMORIES:
        assert entry["memory_type"] in VALID_MEMORY_TYPES, (
            f"unknown memory_type: {entry['memory_type']}"
        )


def test_every_ticker_uses_egx_suffix():
    for entry in EGX_SEED_MEMORIES:
        assert entry["ticker"].endswith(".CA"), (
            f"ticker {entry['ticker']} lacks .CA suffix"
        )


def test_outcomes_are_json_encodable_and_carry_canonical_keys():
    for entry in EGX_SEED_MEMORIES:
        parsed = json.loads(entry["outcome"])
        assert "verdict" in parsed
        assert "forward_return" in parsed
        assert parsed["verdict"] in {"WIN", "LOSS", "NEUTRAL"}
        assert isinstance(parsed["forward_return"], (int, float))


def test_trade_dates_are_iso_format():
    for entry in EGX_SEED_MEMORIES:
        d = entry["trade_date"]
        # Cheap structural check rather than full date parse.
        assert len(d) == 10 and d[4] == "-" and d[7] == "-", (
            f"trade_date {d} not in YYYY-MM-DD format"
        )


def test_all_five_agent_collections_are_seeded():
    """No collection should start empty — that's the whole point of the seed."""
    names = set(all_agent_names())
    assert names == VALID_AGENT_NAMES


def test_get_seeds_for_agent_filters_correctly():
    bulls = get_seeds_for_agent("bull_memory")
    assert all(s["agent_name"] == "bull_memory" for s in bulls)
    assert len(bulls) >= 5  # at least 5 per collection

    bears = get_seeds_for_agent("bear_memory")
    assert all(s["agent_name"] == "bear_memory" for s in bears)
    assert len(bears) >= 5


def test_get_seeds_for_agent_unknown_returns_empty():
    assert get_seeds_for_agent("nonexistent_memory") == []


def test_corpus_covers_at_least_5_distinct_tickers():
    """Cross-ticker breadth — a corpus all on COMI would mass-collide on
    ticker filters and be useless for diversified retrieval."""
    tickers = {e["ticker"] for e in EGX_SEED_MEMORIES}
    assert len(tickers) >= 5


def test_corpus_includes_both_win_and_loss_outcomes():
    """Bias-balanced: agents must see negative examples too. A corpus that's
    100% WINs would teach overconfidence."""
    verdicts = {json.loads(e["outcome"])["verdict"] for e in EGX_SEED_MEMORIES}
    assert "WIN" in verdicts
    assert "LOSS" in verdicts
