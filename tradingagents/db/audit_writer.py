"""Audit write-through for the LangGraph propagate() pipeline.

Two public helpers:

- ``write_analysis_session()`` — one row per ``propagate()`` invocation, captures
  session_id, ticker, trade_date, final decision, confidence, risk veto,
  the full state snapshot, the LLM model fingerprint, and the optional
  user_id (NULL until auth lands; MEMORY.md §E).

- ``write_agent_events()`` — bulk insert of one row per agent output that
  appears in the final state. Provides the granular per-agent audit trail
  required by MEMORY.md §G (regulatory reconstruction).

Both helpers:

- Short-circuit when Postgres is unavailable (``is_postgres_available()``
  returns False) — never crash the graph.
- Wrap inserts in the centralized pool's transaction context manager
  (auto-commit on success, rollback on exception).
- Log on failure and return ``False``. Callers should not chain on the
  return value; they exist for observability.

Schema requirements:
- ``analysis_sessions`` and ``agent_events`` tables as defined in ``db_schema.sql``
  with the additions in ``scripts/db/apply_schema_v2.sql``:
  ``user_id TEXT NULL`` and ``model_fingerprint JSONB NULL`` on
  ``analysis_sessions``; ``model_fingerprint JSONB NULL`` on ``agent_events``.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from tradingagents.db import cursor as db_cursor
from tradingagents.db import is_postgres_available

logger = logging.getLogger("tradingagents.db.audit")


# Map of canonical agent name → state field. The order is the canonical event
# ordering for replay. Each entry's second element is what we treat as the
# "structured output" payload when available; falling back to the raw report
# text in ``opinion_summary``.
_AGENT_EVENT_MAP = (
    # (agent_name,                event_type,        report_field,           structured_field)
    ("market_analyst",            "analyst_output",  "market_report",        "technical_analysis"),
    ("sentiment_analyst",         "analyst_output",  "sentiment_report",     "sentiment_analysis"),
    ("news_analyst",              "analyst_output",  "news_report",          None),
    ("fundamentals_analyst",      "analyst_output",  "fundamentals_report",  "fundamental_analysis"),
    ("bull_researcher",           "debate",          None,                   None),  # nested below
    ("bear_researcher",           "debate",          None,                   None),
    ("research_manager",          "judge",           None,                   None),
    ("trader",                    "execution_plan",  "trader_investment_plan", "execution_plan"),
    ("risk_aggressive",           "risk_debate",     None,                   None),
    ("risk_conservative",         "risk_debate",     None,                   None),
    ("risk_neutral",              "risk_debate",     None,                   None),
    ("risk_manager",              "risk_judge",      None,                   "risk_assessment"),
    ("final",                     "final_decision",  "final_trade_decision", None),
)


def _truncate(text: Optional[str], limit: int = 1000) -> Optional[str]:
    """Truncate text for the opinion_summary column. ``None`` passes through."""
    if not text:
        return None
    s = str(text)
    return s if len(s) <= limit else s[:limit] + "…"


def _to_jsonb(value: Any) -> Optional[str]:
    """Serialize a Python value to a JSON string suitable for psycopg2 JSONB.

    Returns None for None inputs so the column stores SQL NULL.
    """
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        logger.debug("audit JSON encode fallback (%s): %s", type(value).__name__, exc)
        return json.dumps({"_unserializable": str(value)})


def build_model_fingerprint(config: Dict[str, Any]) -> Dict[str, Any]:
    """Snapshot of the LLM config that produced the agent outputs.

    Captures provider + model identifiers + temperature/seed at the time of
    the run. The seed value is the hardcoded constant used at LLM construction
    in ``TradingAgentsGraph.__init__`` (seed=42 for OpenAI-compatible providers).
    Anthropic and Google providers do not support the seed parameter.
    """
    # The seed is set at LLM construction time in trading_graph.py, not in
    # DEFAULT_CONFIG. Record the actual value used by the OpenAI-compatible path.
    provider = (config.get("llm_provider") or "").lower()
    seed = 42 if provider in ("openai", "ollama", "openrouter") else None

    return {
        "llm_provider": config.get("llm_provider"),
        "deep_think_llm": config.get("deep_think_llm"),
        "quick_think_llm": config.get("quick_think_llm"),
        "backend_url": config.get("backend_url"),
        "temperature": 0,
        "seed": seed,
        "target_market": config.get("target_market"),
    }


def _coerce_trade_date(trade_date: Any) -> Optional[date]:
    """Accept str ISO date / datetime / date objects; return a date or None."""
    if trade_date is None:
        return None
    if isinstance(trade_date, date) and not isinstance(trade_date, datetime):
        return trade_date
    if isinstance(trade_date, datetime):
        return trade_date.date()
    try:
        return date.fromisoformat(str(trade_date)[:10])
    except (TypeError, ValueError):
        return None


def write_analysis_session(
    *,
    session_id: str,
    ticker: str,
    trade_date: Any,
    final_state: Dict[str, Any],
    model_fingerprint: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
) -> bool:
    """Insert one row into ``analysis_sessions``. Returns True on success.

    Never raises into the caller. If Postgres is unavailable or the write
    fails, a warning is logged and ``False`` returned.
    """
    if not is_postgres_available():
        return False

    td = _coerce_trade_date(trade_date)
    if td is None:
        logger.warning(
            "audit write skipped: invalid trade_date %r for session %s",
            trade_date, session_id,
        )
        return False

    confidence_scores = final_state.get("confidence_scores") or {}
    confidence_overall = confidence_scores.get("overall") if isinstance(confidence_scores, dict) else None
    risk_veto = bool(final_state.get("risk_veto", False))
    final_decision = _truncate(final_state.get("final_trade_decision"), limit=2000)

    try:
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO analysis_sessions (
                    session_id, ticker, trade_date, market,
                    final_decision, risk_veto, confidence_overall,
                    confidence_scores, execution_plan, risk_assessment,
                    data_quality, full_state,
                    user_id, model_fingerprint
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s,
                        %s::jsonb, %s::jsonb, %s::jsonb,
                        %s::jsonb, %s::jsonb,
                        %s, %s::jsonb)
                ON CONFLICT (session_id) DO NOTHING
                """,
                (
                    session_id,
                    ticker,
                    td,
                    final_state.get("target_market") or "EGX",
                    final_decision,
                    risk_veto,
                    confidence_overall,
                    _to_jsonb(confidence_scores),
                    _to_jsonb(final_state.get("execution_plan")),
                    _to_jsonb(final_state.get("risk_assessment")),
                    _to_jsonb(final_state.get("data_quality")),
                    _to_jsonb(final_state),
                    user_id,
                    _to_jsonb(model_fingerprint),
                ),
            )
        logger.debug("audit: analysis_sessions row written for %s", session_id)
        return True
    except Exception as exc:
        logger.warning(
            "audit write failed (analysis_sessions, session=%s): %s",
            session_id, exc,
        )
        return False


def _build_agent_event_rows(
    *,
    session_id: str,
    final_state: Dict[str, Any],
    model_fingerprint_json: Optional[str],
) -> List[tuple]:
    """Construct one tuple per agent output present in the final state.

    Pulled out as a helper so we can unit-test the row construction without
    a database.
    """
    rows: List[tuple] = []
    investment_debate = final_state.get("investment_debate_state") or {}
    risk_debate = final_state.get("risk_debate_state") or {}

    for agent_name, event_type, report_field, structured_field in _AGENT_EVENT_MAP:
        # Resolve the report text + structured output for each known agent.
        if agent_name == "bull_researcher":
            report_text = investment_debate.get("bull_history")
            structured = investment_debate.get("bull_thesis")
        elif agent_name == "bear_researcher":
            report_text = investment_debate.get("bear_history")
            structured = investment_debate.get("bear_thesis")
        elif agent_name == "research_manager":
            report_text = investment_debate.get("judge_decision")
            structured = None
        elif agent_name == "risk_aggressive":
            report_text = risk_debate.get("risky_history")
            structured = None
        elif agent_name == "risk_conservative":
            report_text = risk_debate.get("safe_history")
            structured = None
        elif agent_name == "risk_neutral":
            report_text = risk_debate.get("neutral_history")
            structured = None
        else:
            report_text = final_state.get(report_field) if report_field else None
            structured = final_state.get(structured_field) if structured_field else None

        if not report_text and structured is None:
            continue  # skip agents that produced nothing this run

        # Pull a confidence score if the agent's structured output carries one.
        confidence_score = None
        if isinstance(structured, dict):
            for key in ("confidence_score", "confidence", "overall_confidence"):
                if key in structured and isinstance(structured[key], (int, float)):
                    confidence_score = float(structured[key])
                    break

        rows.append(
            (
                session_id,
                event_type,
                agent_name,
                None,  # opinion_type — not parsed yet (PR §C5 future work)
                _truncate(report_text, limit=1000),
                confidence_score,
                _to_jsonb(structured),
                model_fingerprint_json,
            )
        )

    return rows


def write_rl_meta_event(
    *,
    session_id: str,
    prediction: Any,
    ticker: Optional[str] = None,
    trade_date: Optional[Any] = None,
) -> bool:
    """Persist one RL meta-policy decision as an ``agent_events`` row.

    The RL meta-policy fires *after* the graph's ``propagate()`` finishes
    (it consumes the graph's final_state to choose a size multiplier), so
    its decision can't be written by ``write_agent_events``. This helper
    appends one extra row with ``agent_name='rl_meta_policy'`` and
    ``event_type='rl_meta_size_adjustment'`` so the post-graph adjustment
    is auditable in the same ``agent_events`` timeline.

    ``prediction`` should be a ``tradingagents.rl.policy.PolicyPrediction``
    (or any object exposing ``size_multiplier``, ``action_index``,
    ``q_values``, ``feature_version``, ``model_fingerprint``). Strings are
    accepted for forward compatibility and stored verbatim.

    Returns True on a successful write, False otherwise. Never raises
    into the caller. Silently no-ops when Postgres is unavailable so the
    backtester keeps running on a developer machine without a DB.
    """
    if not is_postgres_available():
        return False
    if session_id is None:
        return False

    # Pull a structured payload from the prediction object. We accept dicts
    # too so non-RL callers (or test doubles) can use the same helper.
    structured: Dict[str, Any]
    if hasattr(prediction, "size_multiplier"):
        try:
            q_vals = list(getattr(prediction, "q_values", ()) or ())
            structured = {
                "size_multiplier": float(prediction.size_multiplier),
                "action_index": int(getattr(prediction, "action_index", -1)),
                "q_values": [float(v) for v in q_vals],
                "feature_version": str(getattr(prediction, "feature_version", "")),
                "model_fingerprint": dict(getattr(prediction, "model_fingerprint", {}) or {}),
            }
        except (TypeError, ValueError) as exc:
            logger.debug("rl audit: failed to coerce prediction: %s", exc)
            structured = {"raw": repr(prediction)}
    elif isinstance(prediction, dict):
        structured = prediction
    else:
        structured = {"raw": repr(prediction)}

    if ticker:
        structured.setdefault("ticker", str(ticker))
    if trade_date:
        structured.setdefault("trade_date", str(trade_date))

    confidence_score = None
    fp = structured.get("model_fingerprint") if isinstance(structured, dict) else None
    if isinstance(fp, dict):
        # Surface the policy's best-val TD loss into confidence_score so
        # existing dashboards / queries that read agent_events.confidence_score
        # find a usable number.
        raw_conf = fp.get("best_val_td_loss")
        if raw_conf is not None:
            try:
                confidence_score = float(raw_conf)
            except (TypeError, ValueError):
                confidence_score = None

    try:
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_events (
                    session_id, event_type, agent_name, opinion_type,
                    opinion_summary, confidence_score, structured_output,
                    model_fingerprint
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                """,
                (
                    session_id,
                    "rl_meta_size_adjustment",
                    "rl_meta_policy",
                    "post_graph_sizing",
                    _truncate(structured.get("model_fingerprint", {}).get("algorithm_version") if isinstance(structured.get("model_fingerprint"), dict) else None, limit=200)
                    or _truncate(f"size_multiplier={structured.get('size_multiplier')}", limit=200),
                    confidence_score,
                    _to_jsonb(structured),
                    _to_jsonb(structured.get("model_fingerprint") if isinstance(structured, dict) else None),
                ),
            )
        logger.debug("rl audit: wrote rl_meta_size_adjustment row for %s", session_id)
        return True
    except Exception as exc:
        logger.warning(
            "rl audit write failed (session=%s): %s", session_id, exc,
        )
        return False


def write_agent_events(
    *,
    session_id: str,
    final_state: Dict[str, Any],
    model_fingerprint: Optional[Dict[str, Any]] = None,
) -> int:
    """Bulk-insert one row per agent output. Returns number of rows written.

    Returns 0 (not False) on failure so callers can log the count cleanly.
    Never raises into the caller.
    """
    if not is_postgres_available():
        return 0

    fp_json = _to_jsonb(model_fingerprint)
    rows = _build_agent_event_rows(
        session_id=session_id,
        final_state=final_state,
        model_fingerprint_json=fp_json,
    )
    if not rows:
        return 0

    try:
        # We use psycopg2.extras.execute_values for a single round-trip insert.
        import psycopg2.extras as _pg_extras

        with db_cursor() as cur:
            _pg_extras.execute_values(
                cur,
                """
                INSERT INTO agent_events (
                    session_id, event_type, agent_name, opinion_type,
                    opinion_summary, confidence_score, structured_output,
                    model_fingerprint
                )
                VALUES %s
                """,
                rows,
                template="(%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)",
            )
        logger.debug("audit: %d agent_events rows written for %s", len(rows), session_id)
        return len(rows)
    except Exception as exc:
        logger.warning(
            "audit write failed (agent_events, session=%s): %s",
            session_id, exc,
        )
        return 0
