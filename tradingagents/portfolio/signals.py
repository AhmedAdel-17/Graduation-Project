"""Signal resolver for the Portfolio Assistant optimizer (roadmap P3).

The optimizer's Black-Litterman blend consumes per-ticker views
(``schemas.SignalView``). This module resolves those views from the platform's
existing signal oracle — the ``analysis_sessions`` table written by
``TradingAgentsGraph`` runs (``db/audit_writer.py``) — applying a freshness
policy:

* **Fresh** (≤ ``pa_signal_max_age_days``, default 7): use the cached agent
  decision as-is (``source=AGENT``).
* **Stale / missing**: optionally *enqueue* a fresh ``TradingAgentsGraph`` run
  (via the injected ``RunLauncher``) AND degrade this turn to a neutral
  **quant-prior** view (``source=QUANT_PRIOR``, ``HOLD`` with low confidence,
  ``is_stale=True``) so the optimizer stays at its equilibrium prior rather than
  acting on a stale thesis. The disclosure rides on the view's provenance flags.

Design constraints honored here
-------------------------------
* No job queue exists (graph runs are in-process threads). "Enqueue" is a
  background thread bounded by a **process-global semaphore** (default 2) so a
  multi-holding refresh can't self-DoS the LLM quota or the demo box
  (roadmap finding #2).
* ``analysis_sessions`` may be near-empty on day one (it was only populated when
  ``POSTGRES_URL`` was set on past runs), so the degrade path is the *common*
  path until the cache warms — it must be cheap and never raise (finding #4).
* The launcher is a narrow ``Protocol``; the API layer (P4) injects the real one
  that reuses the ``/api/analyze`` machinery, tests inject a fake.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.signal_processing import SignalProcessor
from tradingagents.portfolio.schemas import SignalLabel, SignalSource, SignalView

logger = logging.getLogger("tradingagents.portfolio.signals")

#: Confidence assigned when a fresh agent session carries no usable score.
_DEFAULT_AGENT_CONFIDENCE = 0.5
#: Confidence assigned to the neutral quant-prior fallback (≈ no view tilt).
_QUANT_PRIOR_CONFIDENCE = 0.0


class RunLauncher(Protocol):
    """Narrow hook to launch a fresh per-ticker signal run. Fire-and-forget.

    The real implementation (injected by the API layer in P4) reuses the
    ``/api/analyze`` run machinery; tests inject a fake that records calls.
    """

    def launch(self, ticker: str, trade_date: str) -> None:  # pragma: no cover - protocol
        ...


# Process-global bound on concurrently launched refresh runs (finding #2). Module
# scope so it is shared across every resolver/turn in the process.
_RUN_SEMAPHORE = threading.BoundedSemaphore(
    value=max(1, int(DEFAULT_CONFIG.get("pa_max_concurrent_runs", 2)))
)


def _row_query_default(ticker: str) -> Optional[dict]:
    """Latest ``analysis_sessions`` row for ``ticker`` (or ``None``).

    Best-effort: returns ``None`` when Postgres is unavailable so the resolver
    degrades to the quant-prior path instead of raising (finding #4).
    """
    try:
        from tradingagents.db import connection as _conn
        from tradingagents.db import is_postgres_available
    except Exception:  # pragma: no cover — db layer import edge
        return None
    if not is_postgres_available():
        return None
    try:
        with _conn.cursor(dict_cursor=True) as cur:
            cur.execute(
                "SELECT session_id, final_decision, confidence_overall, "
                "created_at, trade_date FROM analysis_sessions "
                "WHERE ticker = %s ORDER BY created_at DESC LIMIT 1;",
                (ticker,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as exc:  # pragma: no cover — query edge
        logger.warning("signal query failed for %s: %s", ticker, exc)
        return None


def _as_aware(dt: Any) -> Optional[datetime]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(dt))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


class SignalResolver:
    """Resolve fresh agent signals; degrade or enqueue when stale (P3)."""

    def __init__(
        self,
        *,
        config: Optional[dict[str, Any]] = None,
        run_launcher: Optional[RunLauncher] = None,
        query_fn: Optional[Callable[[str], Optional[dict]]] = None,
        max_age_days: Optional[float] = None,
        signal_processor: Optional[SignalProcessor] = None,
    ) -> None:
        self.config = config or DEFAULT_CONFIG
        self.run_launcher = run_launcher
        self._query = query_fn or _row_query_default
        self.max_age_days = float(
            max_age_days if max_age_days is not None
            else self.config.get("pa_signal_max_age_days", 7)
        )
        self._processor = signal_processor or SignalProcessor(None)
        #: Tickers for which a refresh run was enqueued this resolver's lifetime.
        self.enqueued: list[str] = []

    def resolve(
        self,
        tickers: Sequence[str],
        *,
        trade_date: Optional[str] = None,
        enqueue_stale: bool = True,
        now: Optional[datetime] = None,
    ) -> dict[str, SignalView]:
        """Return one ``SignalView`` per ticker (fresh agent view or quant-prior).

        ``now`` is injectable for deterministic tests; ``trade_date`` is the date
        a refresh run would be launched for (defaults to today, UTC).
        """
        now = now or datetime.now(timezone.utc)
        trade_date = trade_date or now.strftime("%Y-%m-%d")
        out: dict[str, SignalView] = {}
        for ticker in dict.fromkeys(tickers):  # de-dup, preserve order
            out[ticker] = self._resolve_one(
                ticker, now=now, trade_date=trade_date, enqueue_stale=enqueue_stale
            )
        return out

    def _resolve_one(
        self, ticker: str, *, now: datetime, trade_date: str, enqueue_stale: bool,
    ) -> SignalView:
        row = None
        try:
            row = self._query(ticker)
        except Exception as exc:  # pragma: no cover — query never blocks resolution
            logger.warning("signal query raised for %s: %s", ticker, exc)

        as_of = _as_aware(row.get("created_at")) if row else None
        age_days = (now - as_of).total_seconds() / 86400.0 if as_of else None

        is_fresh = row is not None and age_days is not None and age_days <= self.max_age_days
        if is_fresh:
            return self._fresh_view(ticker, row, as_of=as_of, age_days=age_days)

        # stale or missing → enqueue a refresh (bounded) + degrade to quant-prior
        if enqueue_stale:
            self._maybe_enqueue(ticker, trade_date)
        return SignalView(
            ticker=ticker,
            label=SignalLabel.HOLD,
            confidence=_QUANT_PRIOR_CONFIDENCE,
            source=SignalSource.QUANT_PRIOR,
            session_id=row.get("session_id") if row else None,
            as_of=as_of,
            age_days=age_days,
            is_stale=True,
        )

    def _fresh_view(
        self, ticker: str, row: dict, *, as_of: Optional[datetime], age_days: Optional[float],
    ) -> SignalView:
        label = self._extract_label(row.get("final_decision"))
        conf = row.get("confidence_overall")
        confidence = float(conf) if conf is not None else _DEFAULT_AGENT_CONFIDENCE
        confidence = min(max(confidence, 0.0), 1.0)
        return SignalView(
            ticker=ticker,
            label=label,
            confidence=confidence,
            source=SignalSource.AGENT,
            session_id=row.get("session_id"),
            as_of=as_of,
            age_days=age_days,
            is_stale=False,
        )

    def _extract_label(self, decision_text: Optional[str]) -> SignalLabel:
        raw = self._processor.process_signal(decision_text or "")  # BUY|SELL|HOLD
        try:
            return SignalLabel(raw.upper())
        except ValueError:  # pragma: no cover — processor only emits the 3 values
            return SignalLabel.HOLD

    def _maybe_enqueue(self, ticker: str, trade_date: str) -> None:
        """Launch a bounded background refresh run, if a launcher is wired and
        the global concurrency budget allows it. Never blocks the turn."""
        if self.run_launcher is None:
            return
        if not _RUN_SEMAPHORE.acquire(blocking=False):
            logger.info("signal refresh for %s skipped: concurrency budget full", ticker)
            return
        self.enqueued.append(ticker)

        def _runner() -> None:
            try:
                self.run_launcher.launch(ticker, trade_date)
            except Exception as exc:  # pragma: no cover — launcher best-effort
                logger.warning("signal refresh launch failed for %s: %s", ticker, exc)
            finally:
                _RUN_SEMAPHORE.release()

        threading.Thread(target=_runner, name=f"pa-signal-refresh-{ticker}", daemon=True).start()


__all__ = ["SignalResolver", "RunLauncher"]
