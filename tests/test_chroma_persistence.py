"""Tests for ChromaDB persistence in FinancialSituationMemory.

Covers:
- PersistentClient is used when ``chroma_persist_dir`` is set.
- Documents written by one instance survive a fresh instance on the same path.
- Empty / unset ``chroma_persist_dir`` falls back to the legacy in-memory client.
- ``_runtime_diagnostics()`` reflects the persistence state correctly.

Embedding is stubbed (no LLM call) — these tests cover storage durability,
not semantic retrieval quality.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tradingagents.agents.utils.memory import FinancialSituationMemory


def _config(persist_dir: str | None) -> dict:
    """Build a memory config that uses OpenAI-compatible backend_url so the
    embeddings_enabled path is exercised, but we will stub get_embedding
    to avoid any real API call.
    """
    return {
        "backend_url": "https://api.openai.com/v1",
        "chroma_persist_dir": persist_dir,
        # PR 8 auto-seeds collections on init; opt out so persistence-mechanics
        # tests count only the rows we explicitly add.
        "disable_seed_memories": True,
    }


@pytest.fixture
def stub_embedding(monkeypatch):
    """Replace get_embedding with a deterministic 1536-dim stub so we can
    add and query documents without hitting any real LLM API.

    Also sets a dummy OPENAI_API_KEY so the ``OpenAI()`` constructor (which
    requires *some* key at build time, even if no network call is made) does
    not refuse to instantiate during the test.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test-dummy-key")

    def _fake_embed(self, text):  # noqa: ARG001 — match real signature
        # Deterministic, fixed-dim (1536) content-based vector. We repeat the
        # input enough times to comfortably exceed the dimension, then truncate.
        repeat_count = max(1, 1 + 1536 // max(1, len(text)))
        base = (text * repeat_count)[:1536]
        # Pad with zeros if the text was so short that even the repeat couldn't
        # fill 1536 chars (defensive — should not happen with repeat_count math).
        if len(base) < 1536:
            base = (base + ("a" * 1536))[:1536]
        return [float(ord(c) % 31) / 31.0 for c in base]

    monkeypatch.setattr(FinancialSituationMemory, "get_embedding", _fake_embed)
    return _fake_embed


def test_persistent_client_used_when_path_set(tmp_path, stub_embedding):
    """When chroma_persist_dir is set, the on-disk directory is created and
    the chroma_persist_dir attribute is exposed. (chromadb.PersistentClient is
    a factory that returns a generic Client, so we assert on the filesystem
    rather than the class name.)"""
    persist_dir = tmp_path / "chroma_db"
    mem = FinancialSituationMemory("test_collection", _config(str(persist_dir)))
    # Force at least one collection write so chroma initializes the sqlite store.
    mem.add_situations([("seed-text", "seed-rec")])

    assert mem.chroma_persist_dir == str(persist_dir)
    assert persist_dir.is_dir(), "PersistentClient should have created the dir"
    # chromadb writes a sqlite database file under the persist dir.
    sqlite_files = list(persist_dir.glob("**/*.sqlite*"))
    assert sqlite_files, "PersistentClient should have created a sqlite file"


def test_in_memory_client_when_path_unset(tmp_path, stub_embedding):
    """Empty / unset chroma_persist_dir falls back to the legacy in-memory
    client (no on-disk files written)."""
    mem_no_path = FinancialSituationMemory("test_empty", _config(None))
    mem_blank = FinancialSituationMemory("test_blank", _config(""))

    assert mem_no_path.chroma_persist_dir is None
    assert mem_blank.chroma_persist_dir is None
    # An in-memory client must not leave any tmp_path-related artifacts behind.
    # (We exercise this just by ensuring add+query works without a persist dir.)
    mem_no_path.add_situations([("ephemeral", "rec")])
    assert mem_no_path.situation_collection.count() == 1


def test_added_situations_survive_restart(tmp_path, stub_embedding):
    """The smoke test for the whole point of this PR: writing in one
    instance, dropping it, opening a fresh instance on the same path —
    the data must still be there."""
    persist_dir = str(tmp_path / "chroma_db")
    config = _config(persist_dir)

    mem_a = FinancialSituationMemory("bull_memory", config)
    mem_a.add_situations(
        [
            ("EGX bank stock recovering after rate cut", "Add to position"),
            ("Real estate slowdown amid pound devaluation", "Trim exposure"),
        ]
    )
    assert mem_a.situation_collection.count() == 2

    # Drop the old instance; the on-disk store stays.
    del mem_a

    mem_b = FinancialSituationMemory("bull_memory", config)
    assert mem_b.situation_collection.count() == 2

    hits = mem_b.get_memories("Bank recovery EGX", n_matches=2)
    assert len(hits) == 2
    recommendations = {h["recommendation"] for h in hits}
    assert "Add to position" in recommendations or "Trim exposure" in recommendations


def test_runtime_diagnostics_reports_chroma_persistence(tmp_path, stub_embedding, monkeypatch):
    """_runtime_diagnostics() exposes chroma_persist_dir, chroma_persistent,
    chroma_collection_counts, chroma_total_documents."""
    persist_dir = str(tmp_path / "chroma_db")
    # Seed one document into the bull_memory collection so the count is non-zero.
    mem = FinancialSituationMemory("bull_memory", _config(persist_dir))
    mem.add_situations([("seeded", "use this")])
    # Close the client so the on-disk sqlite handle is released before another
    # PersistentClient opens it inside _runtime_diagnostics.
    del mem

    # Point the config used by _runtime_diagnostics at our temp directory.
    from server import api_server

    monkeypatch.setenv("CHROMA_PERSIST_DIR", persist_dir)

    # Make get_config return a config that points at the temp dir, in case
    # the api_server has a cached config.
    original_get_config = api_server.get_config

    def _patched_get_config():
        cfg = dict(original_get_config())
        cfg["chroma_persist_dir"] = persist_dir
        cfg["memory_backend"] = "chroma"
        return cfg

    monkeypatch.setattr(api_server, "get_config", _patched_get_config)

    diag = api_server._runtime_diagnostics()
    assert diag["memory"]["backend"] == "chroma"
    assert diag["memory"]["chroma_persist_dir"] == persist_dir
    assert diag["memory"]["chroma_persistent"] is True
    counts = diag["memory"]["chroma_collection_counts"]
    assert isinstance(counts, dict)
    assert counts.get("bull_memory") == 1
    assert diag["memory"]["chroma_total_documents"] >= 1
    # No degraded reason for chroma-in-memory should fire when persistent.
    assert "chroma_memory_in_memory_only" not in diag["degraded_reasons"]


def test_runtime_diagnostics_flags_in_memory_chroma(monkeypatch):
    """When chroma is the backend but no persist dir is configured, the
    diagnostics flag the run as degraded."""
    from server import api_server

    monkeypatch.delenv("CHROMA_PERSIST_DIR", raising=False)

    original_get_config = api_server.get_config

    def _patched_get_config():
        cfg = dict(original_get_config())
        cfg["chroma_persist_dir"] = ""
        cfg["memory_backend"] = "chroma"
        return cfg

    monkeypatch.setattr(api_server, "get_config", _patched_get_config)

    diag = api_server._runtime_diagnostics()
    assert diag["memory"]["chroma_persistent"] is False
    assert diag["memory"]["chroma_persist_dir"] in (None, "")
    assert "chroma_memory_in_memory_only" in diag["degraded_reasons"]
    assert diag["degraded"] is True
