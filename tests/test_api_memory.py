"""Tests for the PR5 memory + reflection endpoints.

Covers:
- 400 on unknown agent_name (allowlist enforcement).
- /memory/{agent}/search returns the shape the dashboard consumes, with
  similarity_score from the underlying FinancialSituationMemory.
- /memory/{agent}/entries returns the seeded + learned entries.
- /reflections filters by memory_type='reflection' and respects ticker.

The tests mock out the memory class so they don't need a Chroma instance or
the OpenAI embedding API. The mocks expose the same methods/attributes the
endpoint relies on.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path) -> TestClient:
    """Spin up the FastAPI app with a temp project root and clear caches."""
    import server.api_server as api_server

    monkeypatch.setattr(api_server, "PROJECT_ROOT", tmp_path)
    # Wipe the memory cache between tests so each test gets fresh mocks.
    api_server._MEMORY_INSTANCES.clear()
    yield TestClient(api_server.app)
    api_server._MEMORY_INSTANCES.clear()


def _seed_mock_memory(api_server, agent_name: str, **overrides) -> MagicMock:
    """Inject a MagicMock memory instance into the endpoint's lazy cache."""
    mock = MagicMock()
    mock.embeddings_enabled = overrides.get("embeddings_enabled", True)
    mock.METADATA_KEYS = (
        "ticker", "trade_date", "memory_type",
        "agent_name", "outcome", "confidence",
    )
    mock.get_memories = overrides.get("get_memories", MagicMock(return_value=[]))
    mock.situation_collection = overrides.get(
        "situation_collection",
        MagicMock(get=MagicMock(return_value={"ids": [], "documents": [], "metadatas": []})),
    )
    mock._bm25_corpus = overrides.get("_bm25_corpus", [])
    api_server._MEMORY_INSTANCES[agent_name] = mock
    return mock


def test_memory_search_rejects_unknown_agent(client):
    res = client.get("/api/memory/totally-not-an-agent/search?ticker=COMI.CA")
    assert res.status_code == 400
    assert "Allowed" in res.json()["detail"]


def test_memory_search_routes_query_and_threshold(client):
    """The endpoint must pass `ticker` as a Chroma `where` filter and
    forward `min_similarity` down to FinancialSituationMemory.get_memories."""
    import server.api_server as api_server

    mock_results = [
        {
            "matched_situation": "COMI.CA bull thesis on rate cut tailwind",
            "recommendation": "BUY with 0.5x sizing",
            "similarity_score": 0.82,
            "metadata": {
                "ticker": "COMI.CA",
                "trade_date": "2025-04-01",
                "memory_type": "thesis",
                "agent_name": "bull_memory",
            },
        }
    ]
    get_memories = MagicMock(return_value=mock_results)
    _seed_mock_memory(api_server, "bull_memory", get_memories=get_memories)

    res = client.get(
        "/api/memory/bull_memory/search?ticker=COMI.CA&k=3&min_similarity=0.3"
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["agent_name"] == "bull_memory"
    assert body["ticker"] == "COMI.CA"
    assert body["k"] == 3
    assert body["min_similarity"] == pytest.approx(0.3)
    assert len(body["results"]) == 1
    assert body["results"][0]["similarity_score"] == pytest.approx(0.82)

    # Verify the call routed the where filter + threshold through.
    get_memories.assert_called_once()
    kwargs = get_memories.call_args.kwargs
    assert kwargs["where"] == {"ticker": "COMI.CA"}
    assert kwargs["min_similarity"] == pytest.approx(0.3)
    assert kwargs["n_matches"] == 3


def test_memory_entries_dumps_chroma(client):
    """The /entries endpoint should hit collection.get() with the right
    where filter and project rows into the response shape."""
    import server.api_server as api_server

    chroma_get = MagicMock(return_value={
        "ids": ["seed_0", "12"],
        "documents": ["Bull thesis seed", "Reflection from 2025-03"],
        "metadatas": [
            {
                "ticker": "COMI.CA",
                "memory_type": "thesis",
                "recommendation": "BUY",
                "trade_date": "2024-09-01",
            },
            {
                "ticker": "COMI.CA",
                "memory_type": "reflection",
                "recommendation": "size smaller next time",
                "trade_date": "2025-03-15",
                "confidence": 0.6,
            },
        ],
    })
    _seed_mock_memory(
        api_server,
        "bear_memory",
        situation_collection=MagicMock(get=chroma_get),
    )

    res = client.get("/api/memory/bear_memory/entries?ticker=COMI.CA&limit=10")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["agent_name"] == "bear_memory"
    assert body["source"] == "chroma"
    assert body["total"] == 2
    seeded = [e for e in body["entries"] if e["seeded"]]
    learned = [e for e in body["entries"] if not e["seeded"]]
    assert len(seeded) == 1
    assert seeded[0]["id"] == "seed_0"
    assert len(learned) == 1
    assert learned[0]["metadata"]["memory_type"] == "reflection"

    # The where filter was honored.
    chroma_get.assert_called_once()
    kw = chroma_get.call_args.kwargs
    assert kw["where"] == {"ticker": "COMI.CA"}
    assert kw["limit"] == 10


def test_memory_entries_bm25_fallback(client):
    """When the backend is embedding-disabled, /entries reads from the
    in-memory BM25 corpus instead of Chroma."""
    import server.api_server as api_server

    _seed_mock_memory(
        api_server,
        "trader_memory",
        embeddings_enabled=False,
        _bm25_corpus=[
            (
                "Cut TMGH.CA at -8% after thesis invalidation",
                {
                    "ticker": "TMGH.CA",
                    "memory_type": "reflection",
                    "recommendation": "exit",
                    "trade_date": "2025-02-12",
                },
            ),
            (
                "Generic seed about real estate sector",
                {
                    "ticker": "TMGH.CA",
                    "memory_type": "seed",
                    "recommendation": "WAIT",
                },
            ),
        ],
    )

    res = client.get("/api/memory/trader_memory/entries?ticker=TMGH.CA")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "bm25"
    assert body["total"] == 2
    # First row is a reflection (learned), second is a seed.
    assert body["entries"][0]["seeded"] is False
    assert body["entries"][1]["seeded"] is True


def test_reflections_walks_all_five_collections(client):
    """`/reflections` should pull reflection rows from every allowlisted
    collection, merge, and sort by trade_date desc."""
    import server.api_server as api_server

    # Bull memory returns one reflection; trader_memory another; the rest empty.
    def _coll(reflection_rows):
        chroma = MagicMock()
        chroma.get = MagicMock(return_value={
            "ids": [f"id_{i}" for i, _ in enumerate(reflection_rows)],
            "documents": [r["doc"] for r in reflection_rows],
            "metadatas": [r["meta"] for r in reflection_rows],
        })
        return chroma

    bull_reflections = [{
        "doc": "Bull thesis on COMI overestimated rate-cut speed",
        "meta": {
            "ticker": "COMI.CA",
            "memory_type": "reflection",
            "recommendation": "downgrade conviction",
            "trade_date": "2025-04-15",
        },
    }]
    trader_reflections = [{
        "doc": "Position sized too aggressively in TMGH",
        "meta": {
            "ticker": "TMGH.CA",
            "memory_type": "reflection",
            "recommendation": "halve next time",
            "trade_date": "2025-05-01",
        },
    }]

    _seed_mock_memory(
        api_server, "bull_memory",
        situation_collection=_coll(bull_reflections),
    )
    _seed_mock_memory(
        api_server, "trader_memory",
        situation_collection=_coll(trader_reflections),
    )
    for name in ("bear_memory", "invest_judge_memory", "risk_manager_memory"):
        _seed_mock_memory(api_server, name, situation_collection=_coll([]))

    res = client.get("/api/reflections?limit=10")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 2
    # Sorted by trade_date desc → trader (May) first, bull (April) second.
    assert body["reflections"][0]["agent_name"] == "trader_memory"
    assert body["reflections"][1]["agent_name"] == "bull_memory"
    assert body["reflections"][0]["trade_date"] == "2025-05-01"

    # Ticker filter must be pushed down to Chroma as a $and clause so the
    # store does the actual filtering. The mock ignores `where`, so we
    # verify the contract by inspecting the call.
    res2 = client.get("/api/reflections?ticker=COMI.CA")
    assert res2.status_code == 200
    bull_get = api_server._MEMORY_INSTANCES["bull_memory"].situation_collection.get
    last_call = bull_get.call_args
    assert last_call.kwargs["where"] == {
        "$and": [
            {"memory_type": "reflection"},
            {"ticker": "COMI.CA"},
        ]
    }
