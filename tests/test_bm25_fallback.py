"""Tests for BM25 keyword fallback + seed-corpus bootstrap (PR 8).

The BM25 path activates in two scenarios:
1. ``embeddings_enabled = False`` (DeepSeek / Groq backends — default config).
2. Chroma vector store is empty even though embeddings are enabled.

Both scenarios used to return ``[]``; with the seed corpus + BM25 fallback,
agents now receive a meaningful ``past_memory_str`` from trade #1.
"""

from __future__ import annotations

import pytest

from tradingagents.agents.utils.memory import FinancialSituationMemory


def _config_with_embeddings(persist_dir: str | None) -> dict:
    """OpenAI backend triggers embeddings_enabled=True."""
    return {
        "backend_url": "https://api.openai.com/v1",
        "chroma_persist_dir": persist_dir,
    }


def _config_no_embeddings(persist_dir: str | None) -> dict:
    """DeepSeek backend triggers embeddings_enabled=False."""
    return {
        "backend_url": "https://api.deepseek.com",
        "chroma_persist_dir": persist_dir,
    }


# ─── Seed-corpus auto-load ─────────────────────────────────────────────────────


def test_empty_collection_bootstraps_with_seeds(monkeypatch, tmp_path):
    """When embeddings are disabled, the BM25 corpus is populated from seeds
    on init even though Chroma stays empty."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-dummy-key")
    mem = FinancialSituationMemory("bull_memory", _config_no_embeddings(str(tmp_path)))

    assert mem.embeddings_enabled is False
    # BM25 corpus is filled from seed_memories.py with rows matching agent_name.
    assert len(mem._bm25_corpus) > 0
    for situation, meta in mem._bm25_corpus:
        assert meta["agent_name"] == "bull_memory"
        assert "recommendation" in meta


def test_disable_seed_memories_flag_respected(monkeypatch, tmp_path):
    """``config['disable_seed_memories'] = True`` skips seed loading entirely."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-dummy-key")
    cfg = _config_no_embeddings(str(tmp_path))
    cfg["disable_seed_memories"] = True
    mem = FinancialSituationMemory("bull_memory", cfg)
    assert mem._bm25_corpus == []


# ─── BM25 retrieval (embeddings disabled) ─────────────────────────────────────


def test_bm25_returns_relevant_seed_when_embeddings_disabled(monkeypatch, tmp_path):
    """The headline assertion: a DeepSeek/Groq config that used to return
    ``[]`` now returns relevant seed memories via BM25."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-dummy-key")
    mem = FinancialSituationMemory("bull_memory", _config_no_embeddings(str(tmp_path)))

    # Query about CIB / banks — should rank a banking seed at the top.
    hits = mem.get_memories(
        "CIB profit growth NIM deposit beta", n_matches=2, min_similarity=0.0
    )
    assert len(hits) >= 1
    # Top match must mention banking concepts (NIM, CAR, CBE, COMI) — one of
    # these tokens is overwhelmingly likely to be the top ranker.
    top_text = (hits[0]["matched_situation"] + " " + hits[0]["recommendation"]).lower()
    assert any(term in top_text for term in ("nim", "cib", "comi", "cbe", "bank"))


def test_bm25_where_filter_excludes_other_tickers(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-dummy-key")
    mem = FinancialSituationMemory("bull_memory", _config_no_embeddings(str(tmp_path)))

    # Same broad banking query but filter to TMGH (real estate).
    hits = mem.get_memories(
        "earnings growth", n_matches=5,
        where={"ticker": "TMGH.CA"}, min_similarity=0.0,
    )
    for h in hits:
        assert h["metadata"]["ticker"] == "TMGH.CA"


def test_bm25_similarity_threshold_drops_off_topic_matches(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-dummy-key")
    mem = FinancialSituationMemory("bull_memory", _config_no_embeddings(str(tmp_path)))

    # Garbage query — none of these tokens appear in any seed. With min_similarity > 0
    # we expect no hits because BM25 raw scores are 0.
    hits = mem.get_memories(
        "zzzzzz qqqqq xxxxxx", n_matches=5, min_similarity=0.1
    )
    assert hits == []


def test_bm25_empty_query_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-dummy-key")
    mem = FinancialSituationMemory("bull_memory", _config_no_embeddings(str(tmp_path)))
    assert mem.get_memories("", n_matches=2) == []
    assert mem.get_memories("   ", n_matches=2) == []


def test_bm25_returned_metadata_preserves_canonical_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-dummy-key")
    mem = FinancialSituationMemory("bull_memory", _config_no_embeddings(str(tmp_path)))
    hits = mem.get_memories("rate cut bank", n_matches=2, min_similarity=0.0)
    assert hits
    md = hits[0]["metadata"]
    assert "ticker" in md
    assert "memory_type" in md
    assert "agent_name" in md
    # outcome was JSON-encoded into the seed corpus — survives round-trip as
    # the same string value (Chroma metadata is scalar-only).
    if "outcome" in md:
        import json
        outcome = json.loads(md["outcome"])
        assert "verdict" in outcome


# ─── add_situations integrates BM25 corpus ────────────────────────────────────


def test_add_situations_appends_to_bm25_when_embeddings_disabled(monkeypatch, tmp_path):
    """Reflection writes must be searchable even when embeddings are off."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-dummy-key")
    mem = FinancialSituationMemory("bull_memory", _config_no_embeddings(str(tmp_path)))
    initial_count = len(mem._bm25_corpus)

    mem.add_situations(
        [
            ("Custom thesis about FWRY.CA payment ramp", "Long bias above 8 EGP"),
        ],
        default_metadata={"ticker": "FWRY.CA", "memory_type": "thesis"},
    )

    assert len(mem._bm25_corpus) == initial_count + 1
    # And it's queryable.
    hits = mem.get_memories(
        "FWRY payment", n_matches=1, where={"ticker": "FWRY.CA"}, min_similarity=0.0
    )
    assert len(hits) == 1
    assert "FWRY" in hits[0]["matched_situation"]


# ─── Vector path: empty Chroma falls back to BM25 over seeds ──────────────────


def test_empty_chroma_with_embeddings_falls_back_to_bm25(monkeypatch, tmp_path):
    """When embeddings are enabled but Chroma is empty (e.g., seeding into
    Chroma failed), the vector path still returns BM25 hits over seeds rather
    than silently returning ``[]``."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-dummy-key")
    # Force the Chroma path to look empty by patching count() to 0 even though
    # PR 8 normally seeds it.
    mem = FinancialSituationMemory("bull_memory", _config_with_embeddings(str(tmp_path)))
    monkeypatch.setattr(mem.situation_collection, "count", lambda: 0)

    hits = mem.get_memories("EGX bank thesis", n_matches=2, min_similarity=0.0)
    # Either: (a) seeds were loaded into Chroma successfully (count != 0) but
    # we patched count() to lie → falls into the empty-fallback BM25 branch,
    # or (b) seeds failed and BM25 is the only path. Either way, expect ≥1 hit.
    assert len(hits) >= 1


# ─── Empty-return telemetry ───────────────────────────────────────────────────


def test_empty_return_logged_when_nothing_matches(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("OPENAI_API_KEY", "test-dummy-key")
    cfg = _config_no_embeddings(str(tmp_path))
    cfg["disable_seed_memories"] = True  # empty corpus
    mem = FinancialSituationMemory("bull_memory", cfg)

    with caplog.at_level("INFO", logger="tradingagents.memory"):
        hits = mem.get_memories("anything", n_matches=5)
    assert hits == []
    log_text = "\n".join(rec.message for rec in caplog.records)
    assert "memory.empty_return" in log_text
