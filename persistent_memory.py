"""
persistent_memory.py
====================
Optional drop-in replacement for FinancialSituationMemory that stores agent
memories in PostgreSQL + pgvector. ChromaDB is the default memory backend for
this project; use this class only when TRADINGAGENTS_MEMORY_BACKEND=postgres.

Compatible with v17.4 config structure (DeepSeek backend, EGX market).

Installation:
    pip install psycopg2-binary pgvector openai

Usage (in trading_graph.py — replace FinancialSituationMemory with this):
    from persistent_memory import PersistentAgentMemory

    self.bull_memory        = PersistentAgentMemory("bull_memory",        self.config)
    self.bear_memory        = PersistentAgentMemory("bear_memory",        self.config)
    self.trader_memory      = PersistentAgentMemory("trader_memory",      self.config)
    self.invest_judge_memory = PersistentAgentMemory("invest_judge_memory", self.config)
    self.risk_manager_memory = PersistentAgentMemory("risk_manager_memory", self.config)

The interface is identical to FinancialSituationMemory:
    memory.add_situations([(situation, recommendation), ...])
    memory.get_memories(current_situation, n_matches=1)
"""

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("tradingagents.memory")

# All Postgres access routes through the centralized pool in tradingagents.db.
# We still need the psycopg2.extras module locally for DictCursor row access,
# and pgvector availability for the embedding column type.
try:
    import psycopg2.extras

    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False
    logger.warning(
        "psycopg2 not installed. "
        "Install via: pip install -e .[postgres]\n"
        "Falling back to in-memory ChromaDB."
    )

from tradingagents.db import cursor as db_cursor
from tradingagents.db import is_postgres_available

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


# ── Embedding helper ──────────────────────────────────────────────────────────

class _EmbeddingClient:
    """Thin wrapper around embedding APIs, compatible with v17.4 config."""

    def __init__(self, config: Dict[str, Any]):
        backend_url = config.get("backend_url", "")
        self.enabled = False
        self.client = None
        self.model = None

        if not OPENAI_AVAILABLE:
            return

        # v17.4 uses DeepSeek — DeepSeek does not have an embedding endpoint.
        # Prefer the OpenAI embedding endpoint when OPENAI_API_KEY is present.
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if "openai.com" in backend_url or (openai_key and not openai_key.startswith("sk-23")):
            # Real OpenAI key available
            self.client = OpenAI()  # uses OPENAI_API_KEY env var
            self.model = "text-embedding-3-small"
            self.enabled = True
        elif "localhost:11434" in backend_url:
            # Ollama — use nomic-embed-text
            self.client = OpenAI(base_url=backend_url, api_key="ollama")
            self.model = "nomic-embed-text"
            self.enabled = True
        else:
            # DeepSeek / Groq / other providers don't support embeddings.
            # Embeddings disabled — memories stored but similarity search returns empty.
            logger.info(
                "Embedding disabled for backend '%s'. "
                "Agent memories will be stored but similarity search won't work. "
                "Set OPENAI_API_KEY to enable semantic memory retrieval.",
                backend_url,
            )

    def embed(self, text: str) -> Optional[List[float]]:
        if not self.enabled or not self.client:
            return None
        try:
            response = self.client.embeddings.create(model=self.model, input=text)
            return response.data[0].embedding
        except Exception as e:
            logger.warning("Embedding failed: %s", e)
            return None


# ── Main persistent memory class ──────────────────────────────────────────────

class PersistentAgentMemory:
    """
    Persistent vector memory backed by PostgreSQL + pgvector.

    Drop-in replacement for FinancialSituationMemory. Falls back to
    in-memory ChromaDB when PostgreSQL is unavailable so existing dev
    workflows are unaffected.

    PostgreSQL connection is configured via the POSTGRES_URL env var:
        export POSTGRES_URL="postgresql://user:password@localhost:5432/egx_trading"

    Or provide it in config:
        config["postgres_url"] = "postgresql://..."
    """

    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name  # e.g. "bull_memory", "bear_memory"
        self.config = config
        self._embedder = _EmbeddingClient(config)
        self._use_postgres = False
        self._fallback = None  # ChromaDB fallback instance

        # A config-provided URL takes precedence; promote it into the env so the
        # shared pool picks it up.
        cfg_url = config.get("postgres_url")
        if cfg_url and not os.environ.get("POSTGRES_URL"):
            os.environ["POSTGRES_URL"] = cfg_url

        if POSTGRES_AVAILABLE and is_postgres_available():
            try:
                self._ensure_schema()
                self._use_postgres = True
                logger.info(
                    "PersistentAgentMemory '%s' using pooled PostgreSQL", name
                )
            except Exception as e:
                logger.warning(
                    "PostgreSQL memory init failed. "
                    "Falling back to ChromaDB in-memory: %s",
                    e,
                )
                self._init_chromadb_fallback(config)
        else:
            if not os.environ.get("POSTGRES_URL"):
                logger.info(
                    "POSTGRES_URL not set. Using in-memory ChromaDB for '%s'. "
                    "Set POSTGRES_URL to persist memories across restarts.",
                    name,
                )
            self._init_chromadb_fallback(config)

    # ── Schema setup ──────────────────────────────────────────────────────────

    def _ensure_schema(self):
        """Create the agent_memories table and index if they don't exist."""
        with db_cursor(register_pgvector=True) as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS agent_memories (
                    id          SERIAL PRIMARY KEY,
                    agent_name  TEXT        NOT NULL,
                    ticker      TEXT,
                    situation   TEXT        NOT NULL,
                    recommendation TEXT     NOT NULL,
                    embedding   vector(1536),
                    created_at  TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_agent_memories_agent
                ON agent_memories (agent_name);
            """)

    def _try_create_ivfflat_index(self):
        """Create ivfflat vector index once there is enough data (>=100 rows)."""
        try:
            with db_cursor(register_pgvector=True) as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM agent_memories WHERE embedding IS NOT NULL;"
                )
                count = cur.fetchone()[0]
                if count >= 100:
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS agent_memories_vec_idx
                        ON agent_memories
                        USING ivfflat (embedding vector_cosine_ops)
                        WITH (lists = 10);
                    """)
        except Exception as exc:
            logger.debug("ivfflat index creation skipped: %s", exc)

    # ── ChromaDB fallback ──────────────────────────────────────────────────────

    def _init_chromadb_fallback(self, config: Dict[str, Any]):
        """Initialize in-memory ChromaDB for environments without PostgreSQL."""
        try:
            from tradingagents.agents.utils.memory import FinancialSituationMemory
            self._fallback = FinancialSituationMemory(self.name, config)
            logger.debug("ChromaDB fallback initialized for '%s'", self.name)
        except Exception as e:
            logger.warning("ChromaDB fallback failed: %s. Memory disabled.", e)
            self._fallback = None

    # ── Public interface (identical to FinancialSituationMemory) ──────────────

    def add_situations(
        self,
        situations_and_advice: List[Tuple[str, str]],
        metadatas: Optional[List[Dict]] = None,
        *,
        default_metadata: Optional[Dict] = None,
    ):
        """
        Add financial situations and their corresponding advice.

        Args:
            situations_and_advice: list of (situation, recommendation) tuples.
            metadatas: optional list of dicts parallel to ``situations_and_advice``.
            default_metadata: optional dict applied to every row in the batch.

        Note: until the Postgres ``agent_memories`` schema is extended (PR 6),
        only the ``ticker`` key from metadata is persisted (existing column).
        Other canonical keys (memory_type, trade_date, outcome, confidence) are
        accepted on the API but logged at DEBUG and dropped.
        """
        if not situations_and_advice:
            return

        if self._use_postgres:
            self._add_to_postgres(
                situations_and_advice,
                metadatas=metadatas,
                default_metadata=default_metadata,
            )
        elif self._fallback:
            self._fallback.add_situations(
                situations_and_advice,
                metadatas=metadatas,
                default_metadata=default_metadata,
            )

    def get_memories(
        self,
        current_situation: str,
        n_matches: int = 1,
        *,
        where: Optional[Dict] = None,
        min_similarity: Optional[float] = None,
    ) -> List[Dict]:
        """
        Find matching recommendations using vector similarity.

        Args:
            current_situation: free-text query.
            n_matches: top-k.
            where: optional metadata filter. Currently only ``{"ticker": ...}``
                is honoured by the Postgres backend; other keys are ignored
                with a debug log. The Chroma fallback supports all canonical
                keys.
            min_similarity: drop rows with cosine similarity below this value.

        Returns list of dicts: {matched_situation, recommendation,
        similarity_score, metadata}.
        """
        if self._use_postgres:
            return self._query_postgres(
                current_situation,
                n_matches,
                where=where,
                min_similarity=min_similarity,
            )
        elif self._fallback:
            return self._fallback.get_memories(
                current_situation,
                n_matches,
                where=where,
                min_similarity=min_similarity,
            )
        return []

    # ── PostgreSQL internals ───────────────────────────────────────────────────

    _SUPPORTED_PG_METADATA = ("ticker",)

    def _resolve_ticker(
        self,
        per_item: Optional[Dict],
        default: Optional[Dict],
    ) -> Optional[str]:
        """Pick the ticker for a row: per-item override > default > config."""
        for source in (per_item, default):
            if source and source.get("ticker"):
                return str(source["ticker"])
        return self.config.get("company_of_interest")

    def _add_to_postgres(
        self,
        situations_and_advice: List[Tuple[str, str]],
        metadatas: Optional[List[Dict]] = None,
        default_metadata: Optional[Dict] = None,
    ):
        # Log unsupported metadata keys once per batch so users know what's dropped
        # until PR 6 extends the schema.
        if default_metadata:
            extras = [
                k for k in default_metadata
                if k not in self._SUPPORTED_PG_METADATA and default_metadata[k] is not None
            ]
            if extras:
                logger.debug(
                    "Postgres backend dropped metadata keys %s — schema upgrade pending (PR 6)",
                    extras,
                )

        with db_cursor(register_pgvector=True) as cur:
            for i, (situation, recommendation) in enumerate(situations_and_advice):
                per_item = metadatas[i] if metadatas else None
                ticker = self._resolve_ticker(per_item, default_metadata)
                embedding = self._embedder.embed(situation)
                cur.execute(
                    """
                    INSERT INTO agent_memories
                        (agent_name, ticker, situation, recommendation, embedding)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        self.name,
                        ticker,
                        situation,
                        recommendation,
                        embedding,  # psycopg2+pgvector accepts list or None
                    ),
                )
        self._try_create_ivfflat_index()
        logger.debug(
            "Stored %d memories in PostgreSQL for '%s'",
            len(situations_and_advice),
            self.name,
        )

    def _query_postgres(
        self,
        current_situation: str,
        n_matches: int,
        *,
        where: Optional[Dict] = None,
        min_similarity: Optional[float] = None,
    ) -> List[Dict]:
        embedding = self._embedder.embed(current_situation)

        # Resolve metadata filter. Only ticker is supported in the Postgres
        # schema today; other keys are logged + ignored.
        ticker_filter: Optional[str] = None
        if where:
            unsupported = [
                k for k in where if k not in self._SUPPORTED_PG_METADATA and where[k] is not None
            ]
            if unsupported:
                logger.debug(
                    "Postgres backend ignored filter keys %s — schema upgrade pending (PR 6)",
                    unsupported,
                )
            if where.get("ticker"):
                ticker_filter = str(where["ticker"])

        with db_cursor(dict_cursor=True, register_pgvector=True) as cur:
            if embedding is not None:
                # Vector similarity search
                if ticker_filter:
                    cur.execute(
                        """
                        SELECT ticker, situation, recommendation,
                               1 - (embedding <=> %s::vector) AS similarity
                        FROM agent_memories
                        WHERE agent_name = %s
                          AND embedding IS NOT NULL
                          AND ticker = %s
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                        """,
                        (embedding, self.name, ticker_filter, embedding, n_matches),
                    )
                else:
                    cur.execute(
                        """
                        SELECT ticker, situation, recommendation,
                               1 - (embedding <=> %s::vector) AS similarity
                        FROM agent_memories
                        WHERE agent_name = %s
                          AND embedding IS NOT NULL
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                        """,
                        (embedding, self.name, embedding, n_matches),
                    )
            else:
                # No embeddings — return most recent memories
                if ticker_filter:
                    cur.execute(
                        """
                        SELECT ticker, situation, recommendation, 0.5 AS similarity
                        FROM agent_memories
                        WHERE agent_name = %s AND ticker = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (self.name, ticker_filter, n_matches),
                    )
                else:
                    cur.execute(
                        """
                        SELECT ticker, situation, recommendation, 0.5 AS similarity
                        FROM agent_memories
                        WHERE agent_name = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (self.name, n_matches),
                    )

            rows = cur.fetchall()

        results = []
        for row in rows:
            similarity = float(row["similarity"])
            if min_similarity is not None and similarity < min_similarity:
                continue
            results.append(
                {
                    "matched_situation": row["situation"],
                    "recommendation": row["recommendation"],
                    "similarity_score": similarity,
                    "metadata": {"ticker": row["ticker"]} if row.get("ticker") else {},
                }
            )
        return results

    def close(self):
        """No-op: connections are owned by the shared pool now."""
        return None
