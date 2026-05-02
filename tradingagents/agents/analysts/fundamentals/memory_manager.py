"""
Local 2-tier memory manager for the EGX Fundamental Analyst.

Phase 3 memory is optional, local, and audit-friendly. It does not require
embeddings and it never overwrites deterministic evidence. Retrieval uses
ticker/frequency filters, recency, and a lightweight BM25-style keyword score.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from tradingagents.dataflows.config import get_config

from .memory_schemas import (
    OperationalMemoryItem,
    StrategicMemoryItem,
    utc_now_iso,
)
from .schemas import FundamentalAnalysisReport


OPERATIONAL_THESIS_OUTCOME_IMPORTANCE = 5
THESIS_ACTUAL_DELTA_IMPORTANCE = 4
RATIO_SNAPSHOT_IMPORTANCE = 3
STRATEGIC_OBSERVATION_IMPORTANCE = 5
_QUARTERLY_TTL_DAYS = 365
_MAX_OPERATIONAL_PER_TICKER_FREQUENCY = 4


def compute_importance_score(
    thesis_direction: Optional[str],
    thesis_confidence: int,
    actual_direction: Optional[str],
    ratio_snapshot: Dict[str, Any],
    thesis_vs_actual_delta: Optional[str] = None,
) -> int:
    """Compute deterministic memory importance."""
    if thesis_direction in {"up", "down", "flat"} and actual_direction in {"up", "down", "flat"}:
        return OPERATIONAL_THESIS_OUTCOME_IMPORTANCE
    if thesis_vs_actual_delta:
        return THESIS_ACTUAL_DELTA_IMPORTANCE
    if thesis_direction in {"up", "down", "flat"}:
        return OPERATIONAL_THESIS_OUTCOME_IMPORTANCE
    if ratio_snapshot:
        return RATIO_SNAPSHOT_IMPORTANCE
    return RATIO_SNAPSHOT_IMPORTANCE


def _normalize_ticker(ticker: str) -> str:
    return ticker.upper().replace(".CA", "").strip()


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    match = re.search(r"(20\d{2}|19\d{2})", text)
    if match:
        return datetime(int(match.group(1)), 12, 31, tzinfo=timezone.utc)
    return None


def _period_sort_key(item: Dict[str, Any]) -> datetime:
    return (
        _parse_dt(item.get("period_end_date"))
        or _parse_dt(item.get("write_date"))
        or datetime.min.replace(tzinfo=timezone.utc)
    )


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


def _memory_text(item: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in (
        "ticker",
        "frequency",
        "period_end_date",
        "thesis_text",
        "earnings_direction_prediction",
        "actual_earnings_direction",
        "thesis_vs_actual_delta",
        "persistent_narrative",
    ):
        val = item.get(key)
        if val:
            parts.append(str(val))
    for key in (
        "key_risks",
        "distress_flags",
        "structural_observations",
        "recurring_risk_themes",
        "recurring_data_issues",
        "sector_specific_notes",
        "recurring_thesis_mistakes",
        "source_data_limitations",
    ):
        vals = item.get(key) or []
        parts.extend(str(v) for v in vals if v)
    ratios = item.get("ratio_snapshot") or {}
    parts.extend(f"{k} {v}" for k, v in ratios.items() if v is not None)
    return " ".join(parts)


def _bm25_scores(query: str, items: Sequence[Dict[str, Any]]) -> Dict[int, float]:
    """Small BM25-style score to avoid mandatory embedding dependencies."""
    query_terms = _tokenize(query)
    if not query_terms or not items:
        return {idx: 0.0 for idx in range(len(items))}

    docs = [_tokenize(_memory_text(item)) for item in items]
    avgdl = sum(len(doc) for doc in docs) / max(len(docs), 1)
    df: Counter[str] = Counter()
    for doc in docs:
        df.update(set(doc))

    scores: Dict[int, float] = {}
    k1 = 1.5
    b = 0.75
    n_docs = len(docs)
    for idx, doc in enumerate(docs):
        counts = Counter(doc)
        doc_len = len(doc) or 1
        score = 0.0
        for term in query_terms:
            if counts[term] == 0:
                continue
            idf = math.log(1 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
            denom = counts[term] + k1 * (1 - b + b * doc_len / max(avgdl, 1))
            score += idf * (counts[term] * (k1 + 1)) / denom
        scores[idx] = score
    return scores


class FundamentalMemoryManager:
    """JSONL-backed two-tier memory for the Fundamental Analyst."""

    def __init__(
        self,
        storage_dir: Optional[str | Path] = None,
        use_embeddings: bool = False,
    ) -> None:
        cfg = get_config()
        data_cache_dir = Path(cfg.get("data_cache_dir") or cfg.get("data_dir") or ".")
        self.storage_dir = Path(storage_dir) if storage_dir else data_cache_dir / "fundamentals_memory"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.operational_path = self.storage_dir / "operational_memory.jsonl"
        self.strategic_path = self.storage_dir / "strategic_memory.jsonl"
        self.use_embeddings = bool(use_embeddings)

    def write_operational_memory(
        self,
        item: Optional[OperationalMemoryItem | Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> OperationalMemoryItem:
        """Write or replace one operational memory item."""
        payload = dict(item.model_dump() if isinstance(item, OperationalMemoryItem) else (item or {}))
        payload.update(kwargs)
        payload["memory_tier"] = "operational"
        if "period_end_date" not in payload and "fiscal_period" in payload:
            payload["period_end_date"] = payload["fiscal_period"]
        if "earnings_direction_prediction" not in payload and "thesis_direction" in payload:
            payload["earnings_direction_prediction"] = payload["thesis_direction"]
        if "earnings_direction_confidence" not in payload and "thesis_confidence" in payload:
            payload["earnings_direction_confidence"] = payload["thesis_confidence"]
        if "thesis_text" not in payload and "thesis_summary" in payload:
            payload["thesis_text"] = payload["thesis_summary"]
        if "actual_earnings_direction" not in payload and "actual_direction" in payload:
            payload["actual_earnings_direction"] = payload["actual_direction"]
        payload.setdefault("frequency", "annual")
        payload["ticker"] = _normalize_ticker(payload["ticker"])
        payload.setdefault("write_date", utc_now_iso())
        payload.setdefault(
            "importance",
            compute_importance_score(
                payload.get("earnings_direction_prediction"),
                int(payload.get("earnings_direction_confidence") or 0),
                payload.get("actual_earnings_direction"),
                payload.get("ratio_snapshot") or {},
                payload.get("thesis_vs_actual_delta"),
            ),
        )
        model = OperationalMemoryItem(**payload)

        items = self._read_jsonl(self.operational_path)
        key = (model.ticker, model.frequency, model.period_end_date, model.source_run_id)
        replaced = False
        for idx, existing in enumerate(items):
            existing_key = (
                _normalize_ticker(existing.get("ticker", "")),
                existing.get("frequency"),
                existing.get("period_end_date"),
                existing.get("source_run_id", ""),
            )
            if existing_key == key:
                items[idx] = model.model_dump()
                replaced = True
                break
        if not replaced:
            items.append(model.model_dump())

        items = self._enforce_operational_retention(items)
        self._write_jsonl(self.operational_path, items)
        return model

    def write_strategic_memory(
        self,
        item: Optional[StrategicMemoryItem | Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> StrategicMemoryItem:
        """Write or replace one strategic memory item for a ticker."""
        payload = dict(item.model_dump() if isinstance(item, StrategicMemoryItem) else (item or {}))
        payload.update(kwargs)
        payload["memory_tier"] = "strategic"
        payload["ticker"] = _normalize_ticker(payload["ticker"])
        payload["importance"] = STRATEGIC_OBSERVATION_IMPORTANCE
        payload.setdefault("last_updated", utc_now_iso())
        model = StrategicMemoryItem(**payload)

        items = self._read_jsonl(self.strategic_path)
        items = [i for i in items if _normalize_ticker(i.get("ticker", "")) != model.ticker]
        items.append(model.model_dump())
        self._write_jsonl(self.strategic_path, items)
        return model

    def retrieve_operational_memory(
        self,
        ticker: str,
        frequency: Optional[str] = None,
        query: Optional[str] = None,
        top_n: int = 4,
        as_of_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve recent, relevant operational memory."""
        norm = _normalize_ticker(ticker)
        items = [
            item for item in self._read_jsonl(self.operational_path)
            if _normalize_ticker(item.get("ticker", "")) == norm
            and (frequency is None or item.get("frequency") == frequency)
        ]
        items = self._filter_operational_ttl(items, as_of_date=as_of_date)
        scores = _bm25_scores(query or "", items)
        ranked = sorted(
            enumerate(items),
            key=lambda pair: (
                scores.get(pair[0], 0.0),
                pair[1].get("importance", 0),
                _period_sort_key(pair[1]),
            ),
            reverse=True,
        )
        return [item for _, item in ranked[:top_n]]

    def retrieve_strategic_memory(
        self,
        ticker: str,
        query: Optional[str] = None,
        top_n: int = 1,
        as_of_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve persistent strategic memory for a ticker.

        Args:
            as_of_date: ISO date string (YYYY-MM-DD or full ISO-8601). When
                provided, only records whose last_updated is on or before this
                date are returned.  Records with missing or unparsable
                last_updated are excluded when as_of_date is given (conservative
                backtest behaviour).  When as_of_date is None, all records for
                the ticker are returned (live-mode behaviour, unchanged).
        """
        norm = _normalize_ticker(ticker)
        items = [
            item for item in self._read_jsonl(self.strategic_path)
            if _normalize_ticker(item.get("ticker", "")) == norm
        ]

        if as_of_date is not None:
            as_of = _parse_dt(as_of_date)
            filtered: List[Dict[str, Any]] = []
            for item in items:
                last_upd = _parse_dt(item.get("last_updated"))
                # Conservative: if last_updated is missing/unparsable, exclude
                # the record during historical mode to prevent leakage.
                if last_upd is not None and last_upd <= as_of:
                    filtered.append(item)
            items = filtered

        scores = _bm25_scores(query or "", items)
        ranked = sorted(
            enumerate(items),
            key=lambda pair: (
                scores.get(pair[0], 0.0),
                pair[1].get("importance", 0),
                _parse_dt(pair[1].get("last_updated")) or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
        return [item for _, item in ranked[:top_n]]

    def build_prior_context(
        self,
        ticker: str,
        frequency: Optional[str] = None,
        query: Optional[str] = None,
        top_n_operational: int = 3,
        top_n_strategic: int = 1,
        as_of_date: Optional[str] = None,
    ) -> str:
        """Format prior memory as labeled prompt context.

        Args:
            as_of_date: ISO date string (YYYY-MM-DD). When provided, only memory
                records written on or before this date are returned. Pass
                report.analysis_date (= trade_date) here to prevent temporal
                leakage during backtesting.
        """
        operational = self.retrieve_operational_memory(
            ticker=ticker,
            frequency=frequency,
            query=query,
            top_n=top_n_operational,
            as_of_date=as_of_date,
        )
        strategic = self.retrieve_strategic_memory(
            ticker=ticker,
            query=query,
            top_n=top_n_strategic,
            as_of_date=as_of_date,
        )
        lines = ["PRIOR FUNDAMENTAL MEMORY CONTEXT"]
        if not operational and not strategic:
            lines.append("No prior memory available for this ticker/frequency.")
            return "\n".join(lines)

        if operational:
            lines.append("Operational memory:")
            for item in operational:
                pred = item.get("earnings_direction_prediction") or "unknown"
                actual = item.get("actual_earnings_direction") or "pending"
                conf = item.get("earnings_direction_confidence", 0)
                delta = item.get("thesis_vs_actual_delta") or "not yet known"
                thesis = _shorten(item.get("thesis_text", ""), 260)
                risks = "; ".join((item.get("key_risks") or [])[:3]) or "none"
                lines.append(
                    f"- {item.get('period_end_date')} ({item.get('frequency')}): "
                    f"prediction={pred} ({conf}/100), actual={actual}, "
                    f"delta={delta}, risks={risks}, thesis={thesis or 'none'}"
                )

        if strategic:
            lines.append("Strategic memory:")
            for item in strategic:
                observations = "; ".join((item.get("structural_observations") or [])[:3]) or "none"
                themes = "; ".join((item.get("recurring_risk_themes") or [])[:3]) or "none"
                issues = "; ".join((item.get("recurring_data_issues") or [])[:3]) or "none"
                accuracy = item.get("cumulative_accuracy") or {}
                lines.append(
                    f"- observations={observations}; recurring_risks={themes}; "
                    f"data_issues={issues}; accuracy={accuracy or 'not yet established'}"
                )

        lines.append(
            "Use this memory only as prior context. Do not treat it as current-period evidence "
            "and do not overwrite deterministic fields."
        )
        return "\n".join(lines)

    def record_reflection(
        self,
        report: FundamentalAnalysisReport,
        frequency: str = "annual",
        actual_earnings_direction: Optional[str] = None,
        thesis_vs_actual_delta: Optional[str] = None,
        source_run_id: Optional[str] = None,
    ) -> OperationalMemoryItem:
        """Store prediction now and update reflection fields when actuals become known."""
        source = source_run_id or f"{report.ticker}-{report.fiscal_period}-{report.analysis_date}"
        existing = self._find_operational(
            ticker=report.ticker,
            frequency=frequency,
            period_end_date=report.fiscal_period,
            source_run_id=source,
        )
        payload = existing or self._operational_from_report(report, frequency, source)

        if actual_earnings_direction in {"up", "down", "flat"}:
            payload["actual_earnings_direction"] = actual_earnings_direction
            pred = payload.get("earnings_direction_prediction")
            if thesis_vs_actual_delta:
                payload["thesis_vs_actual_delta"] = thesis_vs_actual_delta
            elif pred:
                payload["thesis_vs_actual_delta"] = (
                    "Prediction matched actual outcome."
                    if pred == actual_earnings_direction
                    else f"Prediction was {pred}, actual was {actual_earnings_direction}."
                )

        payload["importance"] = compute_importance_score(
            payload.get("earnings_direction_prediction"),
            int(payload.get("earnings_direction_confidence") or 0),
            payload.get("actual_earnings_direction"),
            payload.get("ratio_snapshot") or {},
            payload.get("thesis_vs_actual_delta"),
        )
        written = self.write_operational_memory(payload)

        if actual_earnings_direction in {"up", "down", "flat"}:
            self._update_strategic_from_operational(report.ticker)

        return written

    def prune_expired_operational_memory(
        self,
        ticker: Optional[str] = None,
        frequency: Optional[str] = None,
        as_of_date: Optional[str] = None,
    ) -> int:
        """Prune expired operational memory and return removed count."""
        items = self._read_jsonl(self.operational_path)
        target_ticker = _normalize_ticker(ticker) if ticker else None
        keep: List[Dict[str, Any]] = []
        removed = 0
        for item in items:
            applies = (
                (target_ticker is None or _normalize_ticker(item.get("ticker", "")) == target_ticker)
                and (frequency is None or item.get("frequency") == frequency)
            )
            if not applies:
                keep.append(item)
                continue
            filtered = self._filter_operational_ttl([item], as_of_date=as_of_date)
            if filtered:
                keep.extend(filtered)
            else:
                removed += 1
        keep = self._enforce_operational_retention(keep)
        self._write_jsonl(self.operational_path, keep)
        return removed

    # Backward-compatible aliases for older Phase 3 stubs/tests.
    write_operational = write_operational_memory
    write_strategic = write_strategic_memory
    get_operational = retrieve_operational_memory
    get_strategic = retrieve_strategic_memory

    def get_prior_thesis_context(self, ticker: str, top_n_operational: int = 2) -> Dict[str, Any]:
        return {
            "operational": self.retrieve_operational_memory(ticker, top_n=top_n_operational),
            "strategic": self.retrieve_strategic_memory(ticker),
        }

    def _operational_from_report(
        self,
        report: FundamentalAnalysisReport,
        frequency: str,
        source_run_id: str,
    ) -> Dict[str, Any]:
        return {
            "ticker": report.ticker,
            "period_end_date": report.fiscal_period,
            "frequency": frequency,
            "thesis_text": report.thesis_text or "",
            "earnings_direction_prediction": report.earnings_direction or None,
            "earnings_direction_confidence": report.earnings_direction_confidence or 0,
            "actual_earnings_direction": None,
            "thesis_vs_actual_delta": None,
            "ratio_snapshot": report.ratios or {},
            "key_risks": report.key_risks or [],
            "distress_flags": report.distress_flags or [],
            "data_confidence": report.data_confidence,
            "signal_coherence": report.signal_coherence,
            "write_date": utc_now_iso(),
            "source_run_id": source_run_id,
            "memory_tier": "operational",
        }

    def _find_operational(
        self,
        ticker: str,
        frequency: str,
        period_end_date: str,
        source_run_id: str,
    ) -> Optional[Dict[str, Any]]:
        norm = _normalize_ticker(ticker)
        for item in self._read_jsonl(self.operational_path):
            if (
                _normalize_ticker(item.get("ticker", "")) == norm
                and item.get("frequency") == frequency
                and item.get("period_end_date") == period_end_date
                and item.get("source_run_id") == source_run_id
            ):
                return dict(item)
        return None

    def _update_strategic_from_operational(self, ticker: str) -> None:
        norm = _normalize_ticker(ticker)
        operational = [
            item for item in self._read_jsonl(self.operational_path)
            if _normalize_ticker(item.get("ticker", "")) == norm
            and item.get("actual_earnings_direction") in {"up", "down", "flat"}
        ]
        if not operational:
            return

        total = len(operational)
        correct = sum(
            1 for item in operational
            if item.get("earnings_direction_prediction") == item.get("actual_earnings_direction")
        )
        risk_counts: Counter[str] = Counter()
        issue_counts: Counter[str] = Counter()
        mistake_counts: Counter[str] = Counter()
        run_ids: List[str] = []
        for item in operational:
            risk_counts.update(item.get("key_risks") or [])
            issue_counts.update(item.get("distress_flags") or [])
            if item.get("earnings_direction_prediction") != item.get("actual_earnings_direction"):
                mistake_counts.update([item.get("thesis_vs_actual_delta") or "Direction prediction missed actual outcome."])
            if item.get("source_run_id"):
                run_ids.append(item["source_run_id"])

        recurring_risks = [risk for risk, count in risk_counts.items() if count >= 2][:5]
        recurring_issues = [issue for issue, count in issue_counts.items() if count >= 2][:5]
        recurring_mistakes = [mistake for mistake, count in mistake_counts.items() if count >= 2][:5]

        observations: List[str] = []
        if total == 1:
            observations.append("Single reflected outcome available; not yet a structural pattern.")
        elif recurring_risks or recurring_issues or recurring_mistakes:
            observations.append(f"{total} reflected outcomes available for recurring-pattern review.")
        else:
            observations.append(f"{total} reflected outcomes available; no recurring structural pattern identified yet.")

        self.write_strategic_memory(
            ticker=norm,
            structural_observations=observations,
            recurring_risk_themes=recurring_risks,
            recurring_data_issues=recurring_issues,
            recurring_thesis_mistakes=recurring_mistakes,
            cumulative_accuracy={
                "total_predictions": total,
                "correct": correct,
                "hit_rate": correct / total if total else None,
            },
            persistent_narrative=observations[0],
            importance=STRATEGIC_OBSERVATION_IMPORTANCE,
            last_updated=utc_now_iso(),
            source_run_ids=sorted(set(run_ids)),
        )

    def _filter_operational_ttl(
        self,
        items: List[Dict[str, Any]],
        as_of_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        as_of = _parse_dt(as_of_date) or datetime.now(timezone.utc)
        fresh: List[Dict[str, Any]] = []
        for item in items:
            if item.get("frequency") == "quarterly":
                item_dt = _period_sort_key(item)
                age_days = (as_of - item_dt).days
                # Guard: 0 <= age_days ensures future-dated quarterly records
                # (negative age) are excluded when as_of_date is provided.
                if 0 <= age_days <= _QUARTERLY_TTL_DAYS:
                    fresh.append(item)
            else:
                # Annual records have no TTL expiry.
                # When as_of_date is given, exclude records whose write_date is
                # after that date — this prevents temporal leakage in backtesting.
                # Records with no write_date (legacy) are kept (permissive).
                if as_of_date is not None:
                    write_dt = _parse_dt(item.get("write_date"))
                    if write_dt is None or write_dt <= as_of:
                        fresh.append(item)
                else:
                    fresh.append(item)
        return self._enforce_operational_retention(fresh)

    def _enforce_operational_retention(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        grouped: Dict[tuple, List[Dict[str, Any]]] = {}
        for item in items:
            key = (_normalize_ticker(item.get("ticker", "")), item.get("frequency", "annual"))
            grouped.setdefault(key, []).append(item)

        kept: List[Dict[str, Any]] = []
        for group in grouped.values():
            group_sorted = sorted(group, key=_period_sort_key, reverse=True)
            kept.extend(group_sorted[:_MAX_OPERATIONAL_PER_TICKER_FREQUENCY])
        return sorted(kept, key=lambda item: (item.get("ticker", ""), item.get("frequency", ""), _period_sort_key(item)))

    def _read_jsonl(self, path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def _write_jsonl(self, path: Path, rows: Iterable[Dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n")
        tmp_path.replace(path)


def _shorten(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


# Compatibility name used by the Phase 3 planning stubs.
MemoryManager = FundamentalMemoryManager
