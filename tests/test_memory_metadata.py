"""Tests for FinancialSituationMemory metadata + similarity threshold (PR 4).

Covers:
- Round-trip of per-row + default metadata via add_situations.
- ``where`` filter excludes rows with non-matching metadata.
- ``min_similarity`` drops sub-threshold matches.
- Unrecognized metadata keys are silently dropped.
- Backwards compatibility: existing single-tuple add_situations still works.
"""

from __future__ import annotations

import pytest

from tradingagents.agents.utils.memory import FinancialSituationMemory


def _config(persist_dir: str | None) -> dict:
    return {
        "backend_url": "https://api.openai.com/v1",
        "chroma_persist_dir": persist_dir,
        # PR 8 auto-seeds collections on init; opt out so the metadata-mechanics
        # tests below start with a truly empty collection.
        "disable_seed_memories": True,
    }


@pytest.fixture
def stub_embedding(monkeypatch):
    """Deterministic 1536-dim embedding stub so tests are offline + fast."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-dummy-key")

    def _fake_embed(self, text):  # noqa: ARG001
        repeat_count = max(1, 1 + 1536 // max(1, len(text)))
        base = (text * repeat_count)[:1536]
        if len(base) < 1536:
            base = (base + ("a" * 1536))[:1536]
        return [float(ord(c) % 31) / 31.0 for c in base]

    monkeypatch.setattr(FinancialSituationMemory, "get_embedding", _fake_embed)
    return _fake_embed


def test_backward_compat_old_two_tuple_signature(tmp_path, stub_embedding):
    """Callers passing only (situation, recommendation) tuples still work."""
    mem = FinancialSituationMemory("bull_memory", _config(str(tmp_path)))
    mem.add_situations(
        [
            ("CIB rate cut benefits", "Long bias"),
            ("Real estate slowdown", "Trim"),
        ]
    )
    assert mem.situation_collection.count() == 2

    hits = mem.get_memories("CIB rate", n_matches=2)
    assert len(hits) >= 1
    # Old call-sites only depend on these two keys; preserve them.
    assert "matched_situation" in hits[0]
    assert "recommendation" in hits[0]
    assert "similarity_score" in hits[0]
    # New shape adds metadata block; empty when old call-sites wrote no metadata.
    assert "metadata" in hits[0]


def test_default_metadata_applied_to_every_row(tmp_path, stub_embedding):
    mem = FinancialSituationMemory("bull_memory", _config(str(tmp_path)))
    mem.add_situations(
        [
            ("EGX bank thesis A", "Long"),
            ("EGX bank thesis B", "Hold"),
        ],
        default_metadata={
            "ticker": "COMI.CA",
            "agent_name": "bull_memory",
            "memory_type": "reflection",
            "trade_date": "2025-12-01",
        },
    )

    hits = mem.get_memories("EGX bank", n_matches=2)
    assert len(hits) == 2
    for h in hits:
        m = h["metadata"]
        assert m["ticker"] == "COMI.CA"
        assert m["memory_type"] == "reflection"
        assert m["trade_date"] == "2025-12-01"
        assert m["agent_name"] == "bull_memory"


def test_per_item_metadata_overrides_default(tmp_path, stub_embedding):
    mem = FinancialSituationMemory("bull_memory", _config(str(tmp_path)))
    mem.add_situations(
        [
            ("Banking thesis", "Long"),
            ("Real estate thesis", "Hold"),
        ],
        metadatas=[
            {"ticker": "COMI.CA"},
            {"ticker": "TMGH.CA"},
        ],
        default_metadata={"memory_type": "thesis"},
    )

    hits_comi = mem.get_memories("Banking", n_matches=5, where={"ticker": "COMI.CA"})
    hits_tmgh = mem.get_memories("Real estate", n_matches=5, where={"ticker": "TMGH.CA"})

    assert len(hits_comi) == 1
    assert hits_comi[0]["metadata"]["ticker"] == "COMI.CA"
    assert hits_comi[0]["metadata"]["memory_type"] == "thesis"

    assert len(hits_tmgh) == 1
    assert hits_tmgh[0]["metadata"]["ticker"] == "TMGH.CA"


def test_where_filter_excludes_non_matching_rows(tmp_path, stub_embedding):
    mem = FinancialSituationMemory("bull_memory", _config(str(tmp_path)))
    mem.add_situations(
        [("COMI thesis", "Long")],
        default_metadata={"ticker": "COMI.CA"},
    )
    mem.add_situations(
        [("TMGH thesis", "Hold")],
        default_metadata={"ticker": "TMGH.CA"},
    )

    only_comi = mem.get_memories("thesis", n_matches=5, where={"ticker": "COMI.CA"})
    assert len(only_comi) == 1
    assert only_comi[0]["metadata"]["ticker"] == "COMI.CA"

    only_tmgh = mem.get_memories("thesis", n_matches=5, where={"ticker": "TMGH.CA"})
    assert len(only_tmgh) == 1
    assert only_tmgh[0]["metadata"]["ticker"] == "TMGH.CA"

    none = mem.get_memories("thesis", n_matches=5, where={"ticker": "NOPE.CA"})
    assert none == []


def test_min_similarity_drops_low_scoring_matches(tmp_path, stub_embedding):
    """The threshold parameter must drop sub-threshold matches.

    Strategy: store one document, query with identical text → similarity is
    very high (≈1.0). A threshold of 0.99 keeps it; a threshold above the
    actual similarity drops it. ``None`` disables filtering entirely.
    """
    mem = FinancialSituationMemory("bull_memory", _config(str(tmp_path)))
    mem.add_situations([("EGX banking thesis", "Long bias")])

    # No filter — the one row comes back.
    no_filter = mem.get_memories("EGX banking thesis", n_matches=1, min_similarity=None)
    assert len(no_filter) == 1
    baseline_sim = no_filter[0]["similarity_score"]

    # Same query, threshold just below the row's actual similarity → still 1 hit.
    keep_at_threshold = mem.get_memories(
        "EGX banking thesis", n_matches=1, min_similarity=baseline_sim - 0.01
    )
    assert len(keep_at_threshold) == 1

    # Threshold above the actual similarity → 0 hits.
    drop_above_threshold = mem.get_memories(
        "EGX banking thesis", n_matches=1, min_similarity=baseline_sim + 0.01
    )
    assert drop_above_threshold == []


def test_unrecognised_metadata_keys_are_silently_dropped(tmp_path, stub_embedding):
    """Callers passing non-canonical keys don't pollute the collection or crash."""
    mem = FinancialSituationMemory("bull_memory", _config(str(tmp_path)))
    mem.add_situations(
        [("test", "rec")],
        default_metadata={
            "ticker": "COMI.CA",
            "random_garbage_key": "ignore me",
            "another_extra": 42,
        },
    )

    hits = mem.get_memories("test", n_matches=1)
    assert len(hits) == 1
    meta = hits[0]["metadata"]
    assert meta == {"ticker": "COMI.CA"}  # only the canonical key survived


def test_mismatched_metadatas_length_raises(tmp_path, stub_embedding):
    mem = FinancialSituationMemory("bull_memory", _config(str(tmp_path)))
    with pytest.raises(ValueError, match="metadatas length must equal"):
        mem.add_situations(
            [("a", "ra"), ("b", "rb")],
            metadatas=[{"ticker": "X"}],  # length mismatch
        )


def test_empty_collection_returns_empty_list(tmp_path, stub_embedding):
    mem = FinancialSituationMemory("bull_memory", _config(str(tmp_path)))
    assert mem.get_memories("anything", n_matches=5) == []
    assert mem.get_memories("anything", n_matches=5, where={"ticker": "X"}) == []


def test_where_filter_on_partially_tagged_store(tmp_path, stub_embedding):
    """Mix of tagged + untagged rows: filtered query returns only tagged ones."""
    mem = FinancialSituationMemory("bull_memory", _config(str(tmp_path)))
    # Tagged row.
    mem.add_situations(
        [("tagged thesis", "long")],
        default_metadata={"ticker": "COMI.CA"},
    )
    # Untagged row (legacy / old data).
    mem.add_situations([("untagged thesis", "hold")])

    tagged_only = mem.get_memories(
        "thesis", n_matches=5, where={"ticker": "COMI.CA"}
    )
    assert len(tagged_only) == 1
    assert tagged_only[0]["metadata"]["ticker"] == "COMI.CA"

    all_results = mem.get_memories("thesis", n_matches=5)
    assert len(all_results) == 2
