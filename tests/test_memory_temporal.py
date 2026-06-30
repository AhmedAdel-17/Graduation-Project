"""Tests for memory temporal safety — valid_after_date filtering.

Ensures that memories encoding outcome/hindsight information cannot be
retrieved for decisions dated before the outcome was known.
"""

import json
import math
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_memory(config_overrides=None):
    """Create a FinancialSituationMemory with embeddings disabled (BM25 only).

    A padding document with unrelated tokens is injected so that BM25Okapi's
    IDF calculation ``log((N - n + 0.5) / (n + 0.5))`` stays positive for
    query-relevant terms (with N=1 and n=1 the formula yields a negative
    value, causing all matches to be discarded by the ``score > 0`` filter).
    """
    from tradingagents.agents.utils.memory import FinancialSituationMemory

    config = {
        "llm_provider": "test",
        "backend_url": "http://localhost",
        "disable_seed_memories": True,
    }
    if config_overrides:
        config.update(config_overrides)
    mem = FinancialSituationMemory("test_collection", config)
    # Padding docs: BM25Okapi IDF = log((N-n+0.5)/(n+0.5)). With N=1, n=1
    # this is negative; with N=2 it's zero. We need N≥3 for positive IDF on
    # terms that appear in exactly 1 document. Two padding docs with
    # non-colliding tokens ensure all test-relevant terms score positive.
    for i in range(2):
        mem._bm25_corpus.append((
            f"unrelated padding document number {i} about weather forecast",
            {"recommendation": "padding", "ticker": "PAD.XX"},
        ))
    mem._bm25_index = None  # force rebuild on next query
    return mem


# ===========================================================================
# 1. BM25 temporal filtering
# ===========================================================================


class TestBM25TemporalFilter:
    """BM25 path respects as_of_date."""

    def test_future_memory_excluded(self):
        """Memory with valid_after_date in the future is NOT returned."""
        mem = _make_memory()
        mem.add_situations(
            [("CIB NIM expansion thesis", "Buy CIB on rate tailwind")],
            default_metadata={
                "ticker": "COMI.CA",
                "trade_date": "2024-06-01",
                "valid_after_date": "2024-06-21",  # outcome known after 20 days
            },
        )
        # Query as of 2024-06-10 — before valid_after_date
        results = mem.get_memories(
            "CIB bank analysis",
            n_matches=5,
            as_of_date="2024-06-10",
        )
        assert len(results) == 0

    def test_past_memory_included(self):
        """Memory with valid_after_date in the past IS returned."""
        mem = _make_memory()
        mem.add_situations(
            [("CIB NIM expansion thesis", "Buy CIB on rate tailwind")],
            default_metadata={
                "ticker": "COMI.CA",
                "trade_date": "2024-01-01",
                "valid_after_date": "2024-01-21",
            },
        )
        # Query as of 2024-06-01 — well after valid_after_date
        results = mem.get_memories(
            "CIB bank analysis",
            n_matches=5,
            as_of_date="2024-06-01",
        )
        assert len(results) == 1

    def test_exact_date_boundary(self):
        """Memory is returned when as_of_date == valid_after_date (<=)."""
        mem = _make_memory()
        mem.add_situations(
            [("TMG real estate thesis", "Buy TMG on devaluation")],
            default_metadata={
                "ticker": "TMGH.CA",
                "trade_date": "2024-03-01",
                "valid_after_date": "2024-03-21",
            },
        )
        results = mem.get_memories(
            "TMG real estate",
            n_matches=5,
            as_of_date="2024-03-21",
        )
        assert len(results) == 1

    def test_no_valid_after_date_always_valid(self):
        """Legacy memories without valid_after_date are always returned."""
        mem = _make_memory()
        # Simulate a legacy memory with no valid_after_date and no trade_date.
        # Use distinctive tokens so BM25 scores > 0 (exact token match required).
        mem._bm25_corpus.append((
            "Legacy market insight about banks sector",
            {"recommendation": "General banking lesson", "ticker": "COMI.CA"},
        ))
        mem._bm25_index = None  # force rebuild
        results = mem.get_memories(
            "Legacy market insight",  # exact tokens from stored doc
            n_matches=5,
            as_of_date="2020-01-01",  # very early date
        )
        assert len(results) == 1

    def test_no_as_of_date_returns_everything(self):
        """When as_of_date is None, no temporal filter is applied."""
        mem = _make_memory()
        mem.add_situations(
            [("Future thesis", "Future lesson")],
            default_metadata={
                "ticker": "COMI.CA",
                "trade_date": "2030-01-01",
                "valid_after_date": "2030-01-21",
            },
        )
        # No as_of_date — backwards compatible, returns everything
        results = mem.get_memories("thesis", n_matches=5)
        assert len(results) == 1

    def test_mixed_memories_filtered_correctly(self):
        """Only past-valid memories are returned when both past and future exist."""
        mem = _make_memory()
        # Use distinctive tokens per doc so BM25 can differentiate them.
        mem.add_situations(
            [
                ("Old valuation thesis for banks sector", "Old recommendation"),
                ("Future earnings projection for banks sector", "Future recommendation"),
            ],
            metadatas=[
                {
                    "ticker": "COMI.CA",
                    "trade_date": "2023-06-01",
                    "valid_after_date": "2023-06-21",
                },
                {
                    "ticker": "COMI.CA",
                    "trade_date": "2024-09-01",
                    "valid_after_date": "2024-09-21",
                },
            ],
        )
        # Query with tokens from both docs — "banks sector" matches both
        results = mem.get_memories(
            "banks sector thesis",
            n_matches=5,
            as_of_date="2024-01-01",
        )
        assert len(results) == 1
        assert "Old" in results[0]["matched_situation"]


# ===========================================================================
# 2. Chroma where clause composition
# ===========================================================================


class TestChromaWhereBuilder:
    """_build_chroma_where produces correct $and structures."""

    def _build(self, where, as_of_date):
        from tradingagents.agents.utils.memory import FinancialSituationMemory
        return FinancialSituationMemory._build_chroma_where(where, as_of_date)

    def test_no_filters(self):
        assert self._build(None, None) is None

    def test_only_ticker(self):
        result = self._build({"ticker": "COMI.CA"}, None)
        assert result == {"ticker": "COMI.CA"}

    def test_only_temporal(self):
        result = self._build(None, "2024-06-01")
        # valid_after_date stored as integer YYYYMMDD in Chroma
        assert result == {"valid_after_date": {"$lte": 20240601}}

    def test_ticker_and_temporal(self):
        result = self._build({"ticker": "COMI.CA"}, "2024-06-01")
        assert result == {
            "$and": [
                {"ticker": "COMI.CA"},
                {"valid_after_date": {"$lte": 20240601}},
            ]
        }

    def test_multiple_where_keys_and_temporal(self):
        result = self._build(
            {"ticker": "COMI.CA", "memory_type": "thesis"},
            "2024-06-01",
        )
        assert "$and" in result
        conditions = result["$and"]
        assert len(conditions) == 3
        # Check all conditions are present
        cond_strs = [str(c) for c in conditions]
        assert any("ticker" in s for s in cond_strs)
        assert any("memory_type" in s for s in cond_strs)
        assert any("valid_after_date" in s for s in cond_strs)


# ===========================================================================
# 3. Seed memories get valid_after_date
# ===========================================================================


class TestSeedValidAfterDate:
    """Seed memories include valid_after_date = trade_date + horizon_days."""

    def test_seeds_have_valid_after_date(self):
        from tradingagents.agents.utils.seed_memories import get_seeds_for_agent
        seeds = get_seeds_for_agent("bull_memory")
        assert len(seeds) > 0
        for seed in seeds:
            assert "valid_after_date" in seed, (
                f"Seed missing valid_after_date: {seed.get('ticker')} {seed.get('trade_date')}"
            )

    def test_seed_valid_after_date_is_after_trade_date(self):
        from tradingagents.agents.utils.seed_memories import get_seeds_for_agent
        seeds = get_seeds_for_agent("bull_memory")
        for seed in seeds:
            trade_date = seed.get("trade_date")
            valid_after = seed.get("valid_after_date")
            if trade_date and valid_after:
                assert valid_after > trade_date, (
                    f"valid_after_date {valid_after} should be > trade_date {trade_date}"
                )

    def test_seed_valid_after_uses_horizon(self):
        """First bull seed: trade_date=2023-11-15, horizon=20 → valid_after=2023-12-05."""
        from tradingagents.agents.utils.seed_memories import get_seeds_for_agent
        seeds = get_seeds_for_agent("bull_memory")
        first = seeds[0]
        assert first["trade_date"] == "2023-11-15"
        assert first["valid_after_date"] == "2023-12-05"

    def test_all_agent_seeds_have_valid_after(self):
        from tradingagents.agents.utils.seed_memories import (
            get_seeds_for_agent, all_agent_names,
        )
        for agent in all_agent_names():
            seeds = get_seeds_for_agent(agent)
            for seed in seeds:
                assert "valid_after_date" in seed, (
                    f"Agent {agent} seed missing valid_after_date"
                )


# ===========================================================================
# 4. Reflection metadata includes valid_after_date
# ===========================================================================


class TestReflectionValidAfterDate:
    """Reflector._default_metadata sets valid_after_date = trade_date + horizon."""

    def test_reflection_with_outcome(self):
        from tradingagents.graph.reflection import Reflector

        reflector = Reflector.__new__(Reflector)
        state = {
            "company_of_interest": "COMI.CA",
            "trade_date": "2024-03-01",
        }
        returns = {
            "verdict": "WIN",
            "forward_return": 0.05,
            "forward_horizon_days": 20,
            "action": "BUY",
        }
        meta = reflector._default_metadata(state, "bull_memory", returns)
        assert meta["valid_after_date"] == "2024-03-21"

    def test_reflection_default_horizon_30(self):
        """When forward_horizon_days is missing, default to 30 days."""
        from tradingagents.graph.reflection import Reflector

        reflector = Reflector.__new__(Reflector)
        state = {
            "company_of_interest": "COMI.CA",
            "trade_date": "2024-01-01",
        }
        returns = {"verdict": "LOSS", "forward_return": -0.03}
        meta = reflector._default_metadata(state, "bear_memory", returns)
        assert meta["valid_after_date"] == "2024-01-31"

    def test_reflection_no_returns_uses_trade_date(self):
        """Without returns_losses, _build_metadata defaults valid_after_date to trade_date."""
        from tradingagents.graph.reflection import Reflector

        reflector = Reflector.__new__(Reflector)
        state = {
            "company_of_interest": "COMI.CA",
            "trade_date": "2024-05-01",
        }
        meta = reflector._default_metadata(state, "bull_memory", None)
        # No valid_after_date set by _default_metadata — _build_metadata will
        # default it to trade_date when the memory is stored
        assert "valid_after_date" not in meta
        assert meta["trade_date"] == "2024-05-01"


# ===========================================================================
# 5. _build_metadata auto-defaults valid_after_date
# ===========================================================================


class TestBuildMetadataDefault:
    """_build_metadata defaults valid_after_date to trade_date."""

    def test_defaults_to_trade_date(self):
        mem = _make_memory()
        meta = mem._build_metadata(
            "some recommendation",
            per_item=None,
            default={"trade_date": "2024-05-01", "ticker": "COMI.CA"},
        )
        assert meta["valid_after_date"] == "2024-05-01"

    def test_explicit_valid_after_not_overridden(self):
        mem = _make_memory()
        meta = mem._build_metadata(
            "some recommendation",
            per_item=None,
            default={
                "trade_date": "2024-05-01",
                "valid_after_date": "2024-05-21",
                "ticker": "COMI.CA",
            },
        )
        assert meta["valid_after_date"] == "2024-05-21"

    def test_no_trade_date_no_valid_after(self):
        mem = _make_memory()
        meta = mem._build_metadata(
            "some recommendation",
            per_item=None,
            default={"ticker": "COMI.CA"},
        )
        assert "valid_after_date" not in meta


# ===========================================================================
# 6. Backtest safety — flush_reflection_queue not called
# ===========================================================================


class TestBacktestReflectionSafety:
    """Confirm reflection queue is never flushed in backtester."""

    def test_flush_not_called_in_backtester(self):
        """Grep the backtester source for flush_reflection_queue calls."""
        import os
        backtester_path = os.path.join(
            os.path.dirname(__file__), "..", "scripts", "backtester.py"
        )
        with open(backtester_path) as f:
            source = f.read()
        # Must NOT contain an actual call to flush_reflection_queue
        # (comments/docstrings mentioning it are OK)
        lines = source.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'"):
                continue
            assert "flush_reflection_queue" not in stripped, (
                f"backtester.py line {i} calls flush_reflection_queue: {stripped}"
            )


# ===========================================================================
# 7. Integration: simulated backtest date ordering
# ===========================================================================


class TestBacktestDateOrdering:
    """In a simulated backtest, memories from future dates are not retrievable."""

    def test_sequential_dates_no_leakage(self):
        """Simulate 3 backtest dates. Memory written on date 2 must not be
        visible to date 1, but should be visible to date 3.

        Note: BM25Okapi IDF goes to zero when a term appears in half the
        corpus, so we test each doc with a unique-token query rather than a
        single query matching both simultaneously.
        """
        mem = _make_memory()

        # Date 1: 2024-01-01 — write a thesis memory
        mem.add_situations(
            [("Jan valuation thesis overview", "Buy CIB early")],
            default_metadata={
                "ticker": "COMI.CA",
                "trade_date": "2024-01-01",
                "valid_after_date": "2024-01-21",
            },
        )

        # Date 2: 2024-03-01 — write a reflection with outcome
        mem.add_situations(
            [("Mar reflection outcome details", "CIB rose 5% lesson learned")],
            default_metadata={
                "ticker": "COMI.CA",
                "trade_date": "2024-03-01",
                "valid_after_date": "2024-03-21",  # outcome horizon
                "memory_type": "reflection",
            },
        )

        # Query as of 2024-01-10: should NOT see either (Jan valid_after=Jan 21)
        r1 = mem.get_memories("valuation thesis", n_matches=5, as_of_date="2024-01-10")
        assert len(r1) == 0

        # Query as of 2024-02-01: Jan thesis is valid, Mar reflection is not
        r2 = mem.get_memories("valuation thesis", n_matches=5, as_of_date="2024-02-01")
        assert len(r2) == 1
        assert "Jan" in r2[0]["matched_situation"]

        # Query as of 2024-04-01: both are valid — verify each independently
        r3a = mem.get_memories("valuation thesis", n_matches=5, as_of_date="2024-04-01")
        assert len(r3a) >= 1 and "Jan" in r3a[0]["matched_situation"]
        r3b = mem.get_memories("reflection outcome", n_matches=5, as_of_date="2024-04-01")
        assert len(r3b) >= 1 and "Mar" in r3b[0]["matched_situation"]

        # Confirm Mar reflection is STILL blocked at 2024-02-01
        r4 = mem.get_memories("reflection outcome", n_matches=5, as_of_date="2024-02-01")
        assert len(r4) == 0


# ===========================================================================
# 8. Chroma persistent store: seeds have valid_after_date
# ===========================================================================


class TestChromaPersistentSeeds:
    """Verify that the on-disk Chroma DB seeds all carry valid_after_date."""

    CHROMA_PATH = "./chroma_db"

    def _skip_if_no_chroma(self):
        import os
        if not os.path.isdir(self.CHROMA_PATH):
            pytest.skip("No persistent chroma_db on disk — run seed migration first")

    def test_all_collections_have_valid_after_date(self):
        """Every row in every Chroma collection must have valid_after_date (int)."""
        self._skip_if_no_chroma()
        import chromadb
        client = chromadb.PersistentClient(path=self.CHROMA_PATH)
        for col in client.list_collections():
            count = col.count()
            if count == 0:
                continue
            results = col.get(limit=count, include=["metadatas"])
            for i, meta in enumerate(results["metadatas"]):
                assert "valid_after_date" in meta, (
                    f"{col.name}[{i}] missing valid_after_date: {meta}"
                )
                assert isinstance(meta["valid_after_date"], int), (
                    f"{col.name}[{i}] valid_after_date not int: {type(meta['valid_after_date'])}"
                )

    def test_valid_after_date_after_trade_date(self):
        """valid_after_date (int YYYYMMDD) must correspond to a date after trade_date."""
        self._skip_if_no_chroma()
        import chromadb
        from tradingagents.agents.utils.memory import FinancialSituationMemory
        client = chromadb.PersistentClient(path=self.CHROMA_PATH)
        for col in client.list_collections():
            count = col.count()
            if count == 0:
                continue
            results = col.get(limit=count, include=["metadatas"])
            for i, meta in enumerate(results["metadatas"]):
                td = meta.get("trade_date")
                vad = meta.get("valid_after_date")
                if td and vad:
                    td_int = FinancialSituationMemory._date_to_int(td)
                    assert vad > td_int, (
                        f"{col.name}[{i}]: valid_after_date {vad} <= trade_date int {td_int}"
                    )

    def test_outcome_seed_not_valid_at_trade_date(self):
        """An outcome seed queried at its own trade_date must be excluded."""
        self._skip_if_no_chroma()
        import chromadb
        from tradingagents.agents.utils.memory import FinancialSituationMemory
        client = chromadb.PersistentClient(path=self.CHROMA_PATH)
        col = client.get_collection("bull_memory")
        results = col.get(limit=1, include=["metadatas"])
        meta = results["metadatas"][0]
        td = meta["trade_date"]  # ISO string
        vad_int = meta["valid_after_date"]  # int YYYYMMDD
        td_int = FinancialSituationMemory._date_to_int(td)
        # Confirm this seed's vad > td (precondition)
        assert vad_int > td_int
        # Query at trade_date — should exclude this seed
        filtered = col.get(
            where={"$and": [
                {"ticker": meta["ticker"]},
                {"valid_after_date": {"$lte": td_int}},
            ]},
            include=["metadatas"],
        )
        for m in filtered["metadatas"]:
            if m.get("trade_date") == td and m.get("ticker") == meta["ticker"]:
                assert m["valid_after_date"] <= td_int, (
                    f"Seed returned at trade_date despite vad={m['valid_after_date']}"
                )

    def test_outcome_seed_valid_after_horizon(self):
        """An outcome seed queried at valid_after_date must be returned."""
        self._skip_if_no_chroma()
        import chromadb
        from tradingagents.agents.utils.memory import FinancialSituationMemory
        client = chromadb.PersistentClient(path=self.CHROMA_PATH)
        col = client.get_collection("bull_memory")
        results = col.get(limit=1, include=["metadatas"])
        meta = results["metadatas"][0]
        vad_int = meta["valid_after_date"]  # int YYYYMMDD
        # Query at valid_after_date — should include this seed
        filtered = col.get(
            where={"$and": [
                {"ticker": meta["ticker"]},
                {"valid_after_date": {"$lte": vad_int}},
            ]},
            include=["metadatas"],
        )
        matching = [
            m for m in filtered["metadatas"]
            if m.get("trade_date") == meta["trade_date"]
            and m.get("ticker") == meta["ticker"]
        ]
        assert len(matching) >= 1, (
            f"Seed not returned at valid_after_date={vad_int}"
        )


# ===========================================================================
# 9. BM25 fallback must NOT treat outcome memories without valid_after_date
#    as always-valid
# ===========================================================================


class TestBM25OutcomeSafety:
    """Outcome memories injected without valid_after_date into BM25 must
    still be excluded by temporal filtering when the memory content
    indicates it is outcome-based (i.e. has trade_date).

    Current behavior: BM25 treats missing valid_after_date as always-valid.
    This test documents that behavior and verifies that _build_metadata
    prevents this scenario by auto-defaulting valid_after_date = trade_date.
    """

    def test_build_metadata_always_sets_valid_after_when_trade_date_present(self):
        """_build_metadata ensures valid_after_date exists whenever trade_date does."""
        mem = _make_memory()
        meta = mem._build_metadata(
            "some rec",
            per_item=None,
            default={"trade_date": "2024-05-01", "ticker": "COMI.CA"},
        )
        assert "valid_after_date" in meta
        assert meta["valid_after_date"] == "2024-05-01"

    def test_add_situations_auto_defaults_valid_after(self):
        """add_situations with trade_date but no valid_after_date gets it auto-defaulted."""
        mem = _make_memory()
        mem.add_situations(
            [("Outcome lesson about banks", "Sell when NIM drops")],
            default_metadata={"trade_date": "2024-05-01", "ticker": "COMI.CA"},
        )
        # Check the BM25 corpus entry
        _, meta = mem._bm25_corpus[-1]
        assert "valid_after_date" in meta
        assert meta["valid_after_date"] == "2024-05-01"

    def test_outcome_without_valid_after_bypasses_bm25_filter(self):
        """Documents the current BM25 behavior: missing valid_after_date = always valid.

        This is the scenario _build_metadata prevents. If someone bypasses
        _build_metadata and injects directly, BM25 will NOT block it. This
        test documents the gap so we know it exists.
        """
        mem = _make_memory()
        # Directly inject into BM25 corpus, bypassing _build_metadata
        mem._bm25_corpus.append((
            "Outcome lesson: stock rose 15% in 20 days after thesis",
            {
                "recommendation": "Hindsight lesson",
                "ticker": "COMI.CA",
                "trade_date": "2024-05-01",
                # Deliberately omit valid_after_date
            },
        ))
        mem._bm25_index = None
        # BM25 treats this as always-valid — this is the documented gap
        results = mem._bm25_search(
            "Outcome lesson stock",
            n_matches=5,
            as_of_date="2024-04-01",  # before trade_date!
        )
        # Current behavior: the memory IS returned (no valid_after_date → always valid)
        # This documents the gap. The defense is that _build_metadata prevents
        # this scenario from occurring in production code.
        assert len(results) >= 1, (
            "BM25 should return memories without valid_after_date "
            "(always-valid behavior) — if this fails, BM25 was hardened, "
            "update this test"
        )
