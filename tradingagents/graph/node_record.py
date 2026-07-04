"""
Backtest Node Recorder — write-only audit trail for LLM-backed graph nodes.

Records per-node invocation metadata (prompt hash, raw LLM output, timing,
status, fallback info) to JSON files for reproducibility and audit.

This module is record-only.  It never reads cached outputs or replays them.
Replay mode is a future concern and is NOT implemented here.

Usage:
    # In backtester.py, before the date loop:
    recorder = NodeRecorder(
        run_id="abc123",
        ticker="COMI",
        records_dir="./backtest_records",
        record_full_prompts=False,
    )

    # In each node, around the LLM call:
    recorder.record(
        node_name="trader",
        trade_date="2024-01-15",
        input_state_keys=["market_report", "fundamentals_report", ...],
        input_hash="sha256...",
        prompt_hash="sha256...",
        raw_output="...",
        state_update={...},
        wall_clock_ms=1234.5,
        status="success",
    )
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("tradingagents.graph.node_record")

# Valid status values for a node record
VALID_STATUSES = frozenset({"success", "fallback", "error", "skipped"})


@dataclass
class NodeRecord:
    """A single recorded invocation of an LLM-backed graph node."""

    # ── Identity ─────────────────────────────────────────────────────────
    run_id: str
    ticker: str
    trade_date: str
    node_name: str

    # ── Status ───────────────────────────────────────────────────────────
    status: str  # "success" | "fallback" | "error" | "skipped"
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    fallback_source: Optional[str] = None
    skip_reason: Optional[str] = None

    # ── Input ────────────────────────────────────────────────────────────
    input_state_keys: List[str] = field(default_factory=list)
    input_hash: Optional[str] = None

    # ── Output ───────────────────────────────────────────────────────────
    raw_output: Optional[str] = None
    state_update: Optional[Dict[str, Any]] = None  # actual values written to state
    state_update_keys: List[str] = field(default_factory=list)
    signal: Optional[str] = None  # BUY / SELL / HOLD if applicable

    # ── Prompt ───────────────────────────────────────────────────────────
    prompt_hash: Optional[str] = None
    prompt_text: Optional[str] = None  # only when record_full_prompts=True

    # ── Provenance ───────────────────────────────────────────────────────
    model_id: Optional[str] = None
    temperature: Optional[float] = None
    seed: Optional[int] = None
    manifest_hash: Optional[str] = None
    config_hash: Optional[str] = None
    wall_clock_ms: Optional[float] = None
    timestamp_utc: Optional[str] = None

    # ── Context items (future: prior memory / context injection) ─────────
    context_items: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-compatible dict, dropping None values."""
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}


def hash_string(text: str) -> str:
    """SHA-256 hash of a string, returned as hex digest."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def hash_state_slice(state: Dict[str, Any], keys: List[str]) -> str:
    """SHA-256 hash of selected state keys (sorted, JSON-serialized).

    Non-serializable values are converted to their repr() string.
    """
    slice_dict = {}
    for k in sorted(keys):
        v = state.get(k)
        try:
            json.dumps(v)
            slice_dict[k] = v
        except (TypeError, ValueError):
            slice_dict[k] = repr(v)
    return hashlib.sha256(
        json.dumps(slice_dict, sort_keys=True, default=str).encode()
    ).hexdigest()


def hash_config_subset(config: Dict[str, Any]) -> str:
    """Hash the config keys that affect LLM behavior."""
    relevant_keys = [
        "llm_provider", "deep_think_llm", "quick_think_llm",
        "backend_url", "target_market", "long_only",
        "allow_short_selling", "allow_leverage",
        "daily_price_limit_pct", "max_position_pct_adv",
        "backtest_mode", "thesis_cot_mode",
        "egx_risk_free_rate", "max_debate_rounds",
    ]
    subset = {k: config.get(k) for k in relevant_keys if k in config}
    return hashlib.sha256(
        json.dumps(subset, sort_keys=True, default=str).encode()
    ).hexdigest()


class NodeRecorder:
    """Write-only recorder for LLM-backed node invocations.

    Creates one JSON file per (ticker, trade_date, node_name) at:
        {records_dir}/{run_id}/{ticker}/{trade_date}/{node_name}.json

    Thread-safe: writes to a temp file first, then atomically renames.
    """

    def __init__(
        self,
        run_id: str,
        ticker: str,
        records_dir: str = "./backtest_records",
        record_full_prompts: bool = False,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.run_id = run_id
        self.ticker = ticker
        self.records_dir = records_dir
        self.record_full_prompts = record_full_prompts
        self._config = config or {}
        self._config_hash = hash_config_subset(self._config) if config else None

        # Extract model provenance from config
        self._model_id = self._config.get("quick_think_llm") or self._config.get("deep_think_llm")
        self._temperature = 0.0  # enforced at construction in trading_graph.py
        self._seed = 42  # enforced at construction in trading_graph.py

    def record(
        self,
        *,
        node_name: str,
        trade_date: str,
        input_state_keys: List[str],
        input_hash: str,
        prompt_hash: Optional[str] = None,
        prompt_text: Optional[str] = None,
        raw_output: Optional[str] = None,
        state_update: Optional[Dict[str, Any]] = None,
        state_update_keys: Optional[List[str]] = None,
        signal: Optional[str] = None,
        wall_clock_ms: Optional[float] = None,
        status: str = "success",
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        fallback_source: Optional[str] = None,
        skip_reason: Optional[str] = None,
        context_items: Optional[List[Dict[str, Any]]] = None,
        manifest_hash: Optional[str] = None,
    ) -> Optional[str]:
        """Record a single node invocation and write to disk.

        Returns the path to the written JSON file, or None if the write
        failed (in which case a warning is logged but the backtest
        continues — recording failures never crash the run).
        """
        if status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status {status!r}; must be one of {sorted(VALID_STATUSES)}"
            )

        # Sanitize state_update: make it JSON-safe, strip private keys
        safe_state_update = _sanitize_state_update(state_update) if state_update else None

        rec = NodeRecord(
            run_id=self.run_id,
            ticker=self.ticker,
            trade_date=trade_date,
            node_name=node_name,
            status=status,
            error_type=error_type,
            error_message=error_message,
            fallback_source=fallback_source,
            skip_reason=skip_reason,
            input_state_keys=input_state_keys,
            input_hash=input_hash,
            raw_output=raw_output,
            state_update=safe_state_update,
            state_update_keys=state_update_keys or [],
            signal=signal,
            prompt_hash=prompt_hash,
            prompt_text=prompt_text if self.record_full_prompts else None,
            model_id=self._model_id,
            temperature=self._temperature,
            seed=self._seed,
            manifest_hash=manifest_hash,
            config_hash=self._config_hash,
            wall_clock_ms=wall_clock_ms,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            context_items=context_items or [],
        )

        try:
            return self._write_record(rec, trade_date, node_name)
        except Exception as e:
            logger.warning(
                "node_record: failed to write record for %s/%s/%s: %s "
                "(backtest continues — recording is best-effort)",
                self.ticker, trade_date, node_name, e,
            )
            return None

    def _write_record(self, rec: NodeRecord, trade_date: str, node_name: str) -> str:
        """Write a NodeRecord to disk as JSON (atomic via temp + rename)."""
        out_dir = os.path.join(
            self.records_dir, self.run_id, self.ticker, trade_date
        )
        os.makedirs(out_dir, exist_ok=True)

        out_path = os.path.join(out_dir, f"{node_name}.json")

        # Write to temp file in the same directory, then rename for atomicity
        fd, tmp_path = tempfile.mkstemp(
            suffix=".json.tmp", prefix=f"{node_name}_", dir=out_dir
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(rec.to_dict(), f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, out_path)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        logger.debug("node_record: wrote %s", out_path)
        return out_path


# ── Private-key prefix used for infrastructure fields in AgentState ───────
_PRIVATE_KEY_PREFIX = "_"


def _sanitize_state_update(update: Dict[str, Any]) -> Dict[str, Any]:
    """Make a state_update dict safe for JSON serialization.

    - Strips keys starting with ``_`` (infrastructure, e.g. ``_node_recorder``)
    - Converts non-serializable values to their ``repr()``
    - Converts LangChain message objects to their string representation
    """
    safe: Dict[str, Any] = {}
    for k, v in update.items():
        if k.startswith(_PRIVATE_KEY_PREFIX):
            continue
        try:
            json.dumps(v)
            safe[k] = v
        except (TypeError, ValueError):
            # LangChain messages, custom objects, etc.
            safe[k] = repr(v)
    return safe


def strip_private_state_keys(state: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of state with infrastructure keys (``_``-prefixed) removed.

    Use before serializing state to JSON logs, Postgres audit rows, or any
    external system. Prevents leaking ``_node_recorder`` and similar objects.
    """
    return {k: v for k, v in state.items() if not k.startswith(_PRIVATE_KEY_PREFIX)}


def get_recorder(state: Dict[str, Any]) -> Optional[NodeRecorder]:
    """Extract the NodeRecorder from graph state, or None if not recording."""
    return state.get("_node_recorder")
