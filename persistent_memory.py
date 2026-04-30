"""
persistent_memory.py
====================
Drop-in replacement for FinancialSituationMemory that stores agent memories
in PostgreSQL + pgvector instead of in-memory ChromaDB.

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

# ── Optional deps: fall back gracefully if postgres is not available ──────────
try:
    import psycopg2
    import psycopg2.extras
    from pgvector.psycopg2 import register_vector
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False
    logger.warning(
        "psycopg2 or pgvector not installed. "
        "Install with: pip install psycopg2-binary pgvector\n"
        "Falling back to in-memory ChromaDB."
    )

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
        self._conn = None
        self._fallback = None  # ChromaDB fallback instance

        postgres_url = config.get("postgres_url") or os.environ.get("POSTGRES_URL")

        if POSTGRES_AVAILABLE and postgres_url:
            try:
                self._conn = psycopg2.connect(postgres_url)
                register_vector(self._conn)
                self._ensure_schema()
                logger.info("PersistentAgentMemory '%s' connected to PostgreSQL", name)
            except Exception as e:
                logger.warning(
                    "PostgreSQL connection failed (%s). Falling back to ChromaDB in-memory: %s",
                    postgres_url, e,
                )
                self._conn = None
                self._init_chromadb_fallback(config)
        else:
            if not postgres_url:
                logger.info(
                    "POSTGRES_URL not set. Using in-memory ChromaDB for '%s'. "
                    "Set POSTGRES_URL to persist memories across restarts.",
                    name,
                )
            self._init_chromadb_fallback(config)

    # ── Schema setup ──────────────────────────────────────────────────────────

    def _ensure_schema(self):
        """Create the agent_memories table and index if they don't exist."""
        with self._conn.cursor() as cur:
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
            self._conn.commit()

    def _try_create_ivfflat_index(self):
        """Create ivfflat vector index once there is enough data (>=100 rows)."""
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM agent_memories WHERE embedding IS NOT NULL;")
                count = cur.fetchone()[0]
                if count >= 100:
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS agent_memories_vec_idx
                        ON agent_memories
                        USING ivfflat (embedding vector_cosine_ops)
                        WITH (lists = 10);
                    """)
                    self._conn.commit()
        except Exception:
            pass  # Non-critical

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

    def add_situations(self, situations_and_advice: List[Tuple[str, str]]):
        """
        Add financial situations and their corresponding advice.
        Parameter: list of (situation, recommendation) tuples.
        """
        if not situations_and_advice:
            return

        if self._conn:
            self._add_to_postgres(situations_and_advice)
        elif self._fallback:
            self._fallback.add_situations(situations_and_advice)

    def get_memories(self, current_situation: str, n_matches: int = 1) -> List[Dict]:
        """
        Find matching recommendations using vector similarity.
        Returns list of dicts: {matched_situation, recommendation, similarity_score}.
        """
        if self._conn:
            return self._query_postgres(current_situation, n_matches)
        elif self._fallback:
            return self._fallback.get_memories(current_situation, n_matches)
        return []

    # ── PostgreSQL internals ───────────────────────────────────────────────────

    def _add_to_postgres(self, situations_and_advice: List[Tuple[str, str]]):
        ticker = self.config.get("company_of_interest")  # may be None at init time
        with self._conn.cursor() as cur:
            for situation, recommendation in situations_and_advice:
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
        self._conn.commit()
        self._try_create_ivfflat_index()
        logger.debug("Stored %d memories in PostgreSQL for '%s'", len(situations_and_advice), self.name)

    def _query_postgres(self, current_situation: str, n_matches: int) -> List[Dict]:
        embedding = self._embedder.embed(current_situation)

        with self._conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            if embedding is not None:
                # Vector similarity search
                cur.execute(
                    """
                    SELECT situation,
                           recommendation,
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
                cur.execute(
                    """
                    SELECT situation, recommendation, 0.5 AS similarity
                    FROM agent_memories
                    WHERE agent_name = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (self.name, n_matches),
                )

            rows = cur.fetchall()

        return [
            {
                "matched_situation": row["situation"],
                "recommendation": row["recommendation"],
                "similarity_score": float(row["similarity"]),
            }
            for row in rows
        ]

    def close(self):
        """Close the database connection."""
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
