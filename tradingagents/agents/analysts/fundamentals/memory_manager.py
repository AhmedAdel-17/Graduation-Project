"""
2-Tier Period-Based Memory Manager for EGX Fundamental Analyst.

STATUS: Phase 3 STUB — Not yet implemented.
        Implement after Phase 2B validation gate passes.

Architecture (from Plan Section 3):
  - Tier 1 (Operational): one item per fiscal period per ticker, retain last 4 items.
    Eviction is period-based, NOT calendar-day TTL.
  - Tier 2 (Strategic): one item per ticker, maintained indefinitely, overwrite on each run.
    Contains only observations that persist across multiple quarters.

Both tiers support a dual backend:
  - ChromaDB (embedding-based cosine similarity) — primary when available
  - BM25 (keyword-based via rank_bm25) — fallback when ChromaDB is unavailable

Retrieval policy:
  - Recency-first within each tier (sort by fiscal_period descending).
  - Cosine similarity as secondary sort if embedding backend is available.
  - Retrieve top-2 from each tier before Stage 2 (Concept-CoT) call.
  - No composite scoring formula until ablation data supports one.

Importance scores:
  - 5: OperationalMemoryItem with thesis_direction + thesis_confidence + actual_direction
  - 4: OperationalMemoryItem with thesis fields but actual_direction still None
  - 3: OperationalMemoryItem with only ratio_snapshot (deterministic-only run)
  - 5: StrategicMemoryItem (always 5)

References:
  - Plan Section 3: 2-Tier Memory + Reflection
  - FinMem [P2] — memory tier design (adapted, not adopted)
  - Gao (2026) multi-source fusion — supports 2-tier split
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


# =============================================================================
# Type definitions (TypedDict-compatible dicts)
# =============================================================================

class OperationalMemoryItem:
    """
    TypedDict schema for one entry in the Operational tier.
    One item per reported fiscal period per ticker.
    """
    ticker: str
    fiscal_period: str           # e.g. "2024-Q3" or "FY2024"
    thesis_direction: str        # "up" | "down" | "flat"
    thesis_confidence: int       # 0–100 (from Thesis-CoT output)
    thesis_summary: str          # ≤ 200 chars extracted from thesis_text
    actual_direction: Optional[str]       # "up" | "down" | "flat" | None (not yet known)
    prediction_correct: Optional[bool]   # True/False once actual is known; None if pending
    ratio_snapshot: Dict[str, Any]       # {metric: value} for the 14 core ratios at this period
    importance_score: int        # 5=complete, 4=pending, 3=ratio-only


class StrategicMemoryItem:
    """
    TypedDict schema for one entry in the Strategic tier.
    One item per ticker, overwritten on each run.
    """
    ticker: str
    sector: str
    last_updated_period: str
    cumulative_accuracy: Dict[str, Any]   # {"total_predictions": int, "correct": int, "hit_rate": float}
    structural_flags: List[str]           # per-observation strings ≤ 120 chars
    recurring_data_issues: List[str]      # per-observation strings ≤ 120 chars
    importance_score: int                 # always 5


# =============================================================================
# Importance score computation
# =============================================================================

def compute_importance_score(
    thesis_direction: Optional[str],
    thesis_confidence: int,
    actual_direction: Optional[str],
    ratio_snapshot: Dict[str, Any],
) -> int:
    """
    Compute the importance_score for an OperationalMemoryItem.

    Rules (from Plan Section 3):
      5 — item has thesis_direction, thesis_confidence, and actual_direction (full record)
      4 — item has thesis fields but actual_direction is still None (pending outcome)
      3 — only ratio_snapshot stored (no thesis generated — e.g. deterministic-only run)

    Returns int 3, 4, or 5.
    """
    # Full record: thesis + actual
    if (
        thesis_direction is not None
        and thesis_direction in ("up", "down", "flat")
        and actual_direction is not None
        and actual_direction in ("up", "down", "flat")
    ):
        return 5

    # Pending: thesis present but actual not yet known
    if (
        thesis_direction is not None
        and thesis_direction in ("up", "down", "flat")
        and actual_direction is None
    ):
        return 4

    # Ratio-only: no thesis generated
    return 3


# =============================================================================
# MemoryManager stub
# =============================================================================

class MemoryManager:
    """
    2-Tier Period-Based Memory Manager.

    STUB — Not yet implemented. All methods raise NotImplementedError.
    Implement after Phase 2B gate passes.

    Phase 3 implementation requirements:
      1. Constructor accepts backend="chromadb" or backend="bm25"
      2. write_operational(ticker, item) — writes to Operational tier
      3. get_operational(ticker, top_n=2) — retrieves most recent items
      4. write_strategic(ticker, item) — overwrites Strategic tier item for ticker
      5. get_strategic(ticker) — retrieves Strategic item for ticker (list of 0 or 1)
      6. Eviction: operational tier keeps at most 4 items per ticker
      7. Storage: in-memory dict (BM25 path) or ChromaDB collection (embedding path)
    """

    def __init__(self, backend: str = "bm25"):
        """
        Args:
          backend: "chromadb" for embedding-based retrieval (primary);
                   "bm25" for keyword-based retrieval (fallback).
        """
        # STUB: raise NotImplementedError when actually called with real backend
        self._backend = backend
        # Phase 3: replace with ChromaDB collection or rank_bm25.BM25Okapi instance
        self._operational: Dict[str, List[Dict]] = {}
        self._strategic: Dict[str, Dict] = {}

        # Maximum operational items per ticker (plan Section 3)
        self._max_operational = 4

    def write_operational(self, ticker: str, item: Dict[str, Any]) -> None:
        """
        Write an OperationalMemoryItem for a ticker.

        Eviction: if writing would result in > _max_operational items for this ticker,
        remove the oldest item (by fiscal_period ascending sort).

        STUB: In-memory implementation only. Full Phase 3 wires to ChromaDB / BM25.
        """
        if ticker not in self._operational:
            self._operational[ticker] = []

        # Append the new item
        self._operational[ticker].append(dict(item))

        # Evict if over limit: sort by fiscal_period ascending, remove the oldest
        if len(self._operational[ticker]) > self._max_operational:
            self._operational[ticker].sort(key=lambda x: x.get("fiscal_period", ""))
            self._operational[ticker] = self._operational[ticker][-self._max_operational:]

    def get_operational(self, ticker: str, top_n: int = 4) -> List[Dict[str, Any]]:
        """
        Retrieve the most recent operational items for a ticker.

        Returns list sorted by fiscal_period descending (most recent first).
        Returns at most top_n items.

        STUB: In-memory implementation only.
        """
        items = self._operational.get(ticker, [])
        # Sort by fiscal_period descending
        items_sorted = sorted(items, key=lambda x: x.get("fiscal_period", ""), reverse=True)
        return items_sorted[:top_n]

    def write_strategic(self, ticker: str, item: Dict[str, Any]) -> None:
        """
        Write (or overwrite) a StrategicMemoryItem for a ticker.

        The strategic tier holds exactly one item per ticker. If an item exists,
        it is replaced entirely (not merged).

        STUB: In-memory implementation only.
        """
        self._strategic[ticker] = dict(item)

    def get_strategic(self, ticker: str) -> List[Dict[str, Any]]:
        """
        Retrieve the StrategicMemoryItem for a ticker.

        Returns a list of 0 or 1 items (1 if the strategic item exists, 0 otherwise).

        STUB: In-memory implementation only.
        """
        if ticker in self._strategic:
            return [self._strategic[ticker]]
        return []

    def get_prior_thesis_context(
        self,
        ticker: str,
        top_n_operational: int = 2,
    ) -> Dict[str, Any]:
        """
        Retrieve prior thesis context for injection into the Stage 1 evidence pack.

        Returns dict:
          {
            "operational": [list of up to top_n_operational OperationalMemoryItems],
            "strategic": [0 or 1 StrategicMemoryItem],
          }

        Used by data_cot.build_evidence_pack() when memory_manager is provided.

        STUB: In-memory implementation only. Phase 3 will add cosine/BM25 ranking.
        """
        return {
            "operational": self.get_operational(ticker, top_n=top_n_operational),
            "strategic": self.get_strategic(ticker),
        }
