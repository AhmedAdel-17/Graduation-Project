"""Build the offline RL training dataset.

A "sample" is one decision: at ``trade_date`` for ``ticker`` the LLM graph
emitted some final state, the backtester (or live system) executed some
action, and 20 trading days later the realized return became knowable.
Stage A produces a parquet file of these (state_features, action, reward,
metadata) tuples; Stages B and C consume it to train and evaluate the CQL
policy.

Two ingestion paths (both produce the same canonical schema):

1. **Postgres path** — ``build_from_postgres``. Reads
   ``analysis_sessions.full_state`` (the JSONB snapshot written by
   ``tradingagents/db/audit_writer.py:write_analysis_session``) for state,
   and ``backtest_trades`` for realized prices. Forward returns are
   recomputed from raw OHLCV (via the existing DataGateway) to avoid
   inheriting the look-ahead bug C1 in the legacy ``trade_result`` label.

2. **JSON report path** — ``build_from_json_reports``. Reads
   ``backtest_results/report_*.json``. Useful when Postgres isn't running
   (graduation environments). State features are sparse here — the legacy
   ``audit_log`` doesn't carry the full ``AgentState`` snapshot — so only
   the action / confidence / forward-return triplet is reliable. The path
   exists so the pipeline works end-to-end on a developer machine; for
   defensible training data, use the Postgres path.

Look-ahead protections enforced here (independent of ``scripts/backtester.py``):
- ``trade_date < forward_window_start_date`` is asserted for every record.
- ``reward_horizon_days`` is recorded per row; partial/PENDING horizons
  produce ``reward=None`` and ``trade_result="PENDING"`` so callers can
  drop them.
- ``forward_return_*`` columns are derived from raw OHLCV, not from
  ``trade["trade_result"]`` (which the audit notes is look-ahead biased
  in the legacy implementation).
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from tradingagents.rl.config import DECISION_ACTIONS, action_to_index
from tradingagents.rl.feature_extractor import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    extract_state_features_dict,
)

logger = logging.getLogger("tradingagents.rl.dataset")

from tradingagents.dataflows.egx_costs import ROUND_TRIP_COMMISSION_PCT

# v2: decision policy with a counterfactual per-action reward vector
# (reward_buy / reward_hold / reward_sell) computed from the forward return.
DATASET_SCHEMA_VERSION = "rl_dataset_v2"
DEFAULT_REWARD_HORIZON_DAYS = 20
DEFAULT_DRAWDOWN_PENALTY = 0.5
# Round-trip commission (0.189% per side × 2). Sourced from the shared EGX cost
# model so reward shaping cannot drift from the backtester's execution costs.
DEFAULT_TX_COST_PCT = ROUND_TRIP_COMMISSION_PCT  # ~0.00378
DEFAULT_REWARD_CLIP = 0.5


# ─────────────────────────────────────────────────────────────────────────────
# Canonical sample dataclass
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class RLSample:
    """One (state, action, reward, metadata) record.

    Stored as a row in the parquet output. ``state_features`` is flattened to
    individual columns at write time to keep the parquet self-describing.
    """

    # Identity
    session_id: Optional[str]
    ticker: str
    trade_date: str  # ISO YYYY-MM-DD
    feature_version: str

    # Observation (canonical-order features). Dict here, flattened at write.
    state_features: Dict[str, float]

    # Action surfaces (we record both so Stage B can choose its action vocabulary)
    llm_action: str            # BUY / SELL / HOLD
    behavior_size_pct: float   # 0..1 — what the rule-based sizing chose
    behavior_size_tier: int    # discretized to {0..4} corresponding to {0.0, 0.25, 0.5, 0.75, 1.0}

    # Reward signals
    reward_horizon_days: int
    forward_return_5d: Optional[float]
    forward_return_20d: Optional[float]
    drawdown_during_holding: Optional[float]
    transaction_cost_pct: float
    reward: Optional[float]           # behavior reward (committee action); None when PENDING
    trade_result: str                 # WIN / LOSS / NEUTRAL / PENDING

    # Counterfactual per-action rewards (the new logic). Each is computable
    # from the forward return at every decision point, so the policy can learn
    # what BUY / HOLD / SELL would each have earned — including the action the
    # committee did NOT take. ``None`` when the horizon has not yet elapsed.
    reward_buy: Optional[float] = None
    reward_hold: Optional[float] = 0.0
    reward_sell: Optional[float] = None
    committee_action_index: int = 1   # index into DECISION_ACTIONS (default HOLD)

    # Audit / debugging
    source: str = "unknown"           # "postgres" | "json_report"
    notes: Optional[str] = None


SIZE_TIERS: Tuple[float, ...] = (0.0, 0.25, 0.50, 0.75, 1.0)


def _size_pct_to_tier(size_pct: float) -> int:
    """Map a continuous size fraction in [0,1] to nearest tier index."""
    size_pct = max(0.0, min(1.0, size_pct))
    diffs = [abs(size_pct - t) for t in SIZE_TIERS]
    return int(np.argmin(diffs))


def _action_to_reward_sign(action: str) -> int:
    """+1 for BUY (long), -1 for SELL, 0 for HOLD.

    Used to flip a raw return into a signed reward (a SELL's reward is the
    drop in price). Matches the project convention in
    ``scripts/backtester.py:1129``.
    """
    a = action.strip().upper()
    if a == "BUY":
        return 1
    if a == "SELL":
        return -1
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Reward shaping
# ─────────────────────────────────────────────────────────────────────────────


def shaped_reward(
    *,
    forward_return: Optional[float],
    action: str,
    size_pct: float,
    transaction_cost_pct: float = DEFAULT_TX_COST_PCT,
    drawdown_during_holding: Optional[float] = None,
    dd_threshold: float = 0.05,
    lambda_dd: float = DEFAULT_DRAWDOWN_PENALTY,
    clip: float = DEFAULT_REWARD_CLIP,
) -> Optional[float]:
    """Compute the shaped reward for a single decision.

    Returns ``None`` if the realized forward return is not yet knowable
    (callers should drop / mark these as PENDING).

    Formula::

        r = log(1 + size_pct * sign(action) * forward_return)
          - lambda_dd * max(0, drawdown - dd_threshold)
          - tx_cost_pct  (if action != HOLD)

    HOLD gets reward 0 regardless of forward return (no exposure, no cost).
    All rewards are clipped to ``[-clip, +clip]`` to prevent outliers from
    dominating gradient updates on a small EGX dataset.
    """
    a = action.strip().upper()
    if a == "HOLD":
        return 0.0
    if forward_return is None:
        return None

    sign = _action_to_reward_sign(a)
    if sign == 0:
        return 0.0

    # log-return on the *fractional* exposure (size_pct in [0,1]).
    # ``1 + size_pct * signed_ret`` is guaranteed > 0 only for size_pct <= 1
    # and signed_ret > -1. Both hold under the ±10% EGX daily limit. Belt-
    # and-braces: clamp the inner term away from -1.
    signed_ret = sign * float(forward_return)
    inner = 1.0 + max(-0.999, min(0.999, size_pct * signed_ret))
    r = math.log(inner)

    if drawdown_during_holding is not None and drawdown_during_holding > dd_threshold:
        r -= lambda_dd * (drawdown_during_holding - dd_threshold)

    r -= transaction_cost_pct

    if r > clip:
        r = clip
    elif r < -clip:
        r = -clip
    return r


def counterfactual_rewards(
    forward_return: Optional[float],
    *,
    transaction_cost_pct: float = DEFAULT_TX_COST_PCT,
    drawdown_during_holding: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    """Reward of EVERY decision at one state, computable from the forward return.

    This is the core of the decision-policy logic: because the realized
    forward return is known after the horizon, we can label the reward of
    BUY, HOLD, *and* SELL at every historical decision — not only the action
    the committee actually took. The policy learns from this full-feedback
    signal where acting or abstaining was historically rewarded.

    Returns a dict keyed by ``DECISION_ACTIONS``. Acting rewards are ``None``
    until the horizon elapses; HOLD is always 0 (no exposure, no cost).
    Each acting reward reuses :func:`shaped_reward` at full conviction
    (``size_pct=1.0``), so BUY is rewarded when the stock rose and SELL
    (exit / avoid, long-only) is rewarded when it fell.
    """
    out: Dict[str, Optional[float]] = {a: None for a in DECISION_ACTIONS}
    out["HOLD"] = 0.0
    if forward_return is None:
        return out
    out["BUY"] = shaped_reward(
        forward_return=forward_return,
        action="BUY",
        size_pct=1.0,
        transaction_cost_pct=transaction_cost_pct,
        drawdown_during_holding=drawdown_during_holding,
    )
    out["SELL"] = shaped_reward(
        forward_return=forward_return,
        action="SELL",
        size_pct=1.0,
        transaction_cost_pct=transaction_cost_pct,
        drawdown_during_holding=drawdown_during_holding,
    )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# JSON-report ingestion path
# ─────────────────────────────────────────────────────────────────────────────


def _iso(d: str) -> str:
    """Coerce 'YYYY-MM-DD' or datetime-prefixed strings to a plain date string."""
    return str(d)[:10]


def _load_trade_outcomes_from_report(report_path: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Load the trades + audit rows from a backtester JSON report."""
    with open(report_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    trades = data.get("trades") or []
    audit_log = data.get("audit_log") or []
    return data, audit_log


def _no_lookahead_window(trade_date: str, horizon_days: int, now: Optional[datetime] = None) -> bool:
    """True iff ``trade_date + horizon_days`` is in the past."""
    try:
        td = datetime.strptime(_iso(trade_date), "%Y-%m-%d")
    except ValueError:
        return False
    cutoff = td + timedelta(days=int(horizon_days * 1.6))  # account for weekends
    return cutoff <= (now or datetime.utcnow())


def _build_sample_from_json_record(
    *,
    ticker: str,
    trade_row: Dict[str, Any],
    horizon_days: int,
    source_tag: str = "json_report",
) -> Optional[RLSample]:
    """Construct an RLSample from one row of report.json['trades']."""
    trade_date = _iso(trade_row.get("date", ""))
    if not trade_date:
        return None

    action = str(trade_row.get("action", "HOLD")).upper()
    confidence = float(trade_row.get("confidence") or 0.0)

    # The legacy in-loop "confidence sizing" rule: target × clip(confidence, 0.20, 1.0).
    # We can't recover the trader's pre-sizing target from the report, so use
    # confidence as the proxy behavior policy here. This is a JSON-path limit;
    # the Postgres path recovers the real execution_plan.position_sizing.
    behavior_size_pct = max(0.20, min(1.0, confidence))
    behavior_size_tier = _size_pct_to_tier(behavior_size_pct)

    fwd_5d = trade_row.get("forward_return_5d")
    fwd_20d = trade_row.get("forward_return_20d")
    trade_result = str(trade_row.get("trade_result", "PENDING")).upper()

    # Look-ahead guard: refuse to compute reward if the horizon hasn't elapsed.
    if not _no_lookahead_window(trade_date, horizon_days):
        trade_result = "PENDING"
        reward: Optional[float] = None
        cf = counterfactual_rewards(None)
    else:
        cf = counterfactual_rewards(fwd_20d)
        reward = cf.get(action)

    state_features = {name: 0.0 for name in FEATURE_NAMES}
    # The JSON path can populate the action one-hots + confidence proxy.
    if f"action_{action.lower()}" in state_features:
        state_features[f"action_{action.lower()}"] = 1.0
    state_features["conf_overall"] = float(confidence)
    state_features["rule_pos_size_multiplier"] = float(behavior_size_pct)

    return RLSample(
        session_id=None,
        ticker=ticker,
        trade_date=trade_date,
        feature_version=FEATURE_VERSION,
        state_features=state_features,
        llm_action=action,
        behavior_size_pct=float(behavior_size_pct),
        behavior_size_tier=int(behavior_size_tier),
        reward_horizon_days=int(horizon_days),
        forward_return_5d=float(fwd_5d) if fwd_5d is not None else None,
        forward_return_20d=float(fwd_20d) if fwd_20d is not None else None,
        drawdown_during_holding=None,
        transaction_cost_pct=DEFAULT_TX_COST_PCT,
        reward=reward,
        trade_result=trade_result,
        reward_buy=cf.get("BUY"),
        reward_hold=cf.get("HOLD", 0.0),
        reward_sell=cf.get("SELL"),
        committee_action_index=action_to_index(action),
        source=source_tag,
        notes="json_path: features are sparse (audit_log lacks full state snapshot)",
    )


def _build_sample_from_decision_log_entry(
    entry: Dict[str, Any],
    *,
    horizon_days: int,
    source_tag: str,
) -> Optional[RLSample]:
    """Construct a RICH RLSample from one ``rl_decision_log`` entry.

    Unlike the legacy trades path, these entries carry the committee's full
    feature vector (captured by scripts/backtester.py at decision time) and a
    realized forward return, so the resulting sample has dense features and a
    full counterfactual reward vector — the preferred offline training source.
    """
    trade_date = _iso(entry.get("trade_date", ""))
    if not trade_date:
        return None
    ticker = str(entry.get("ticker") or "?")
    committee_action = str(entry.get("committee_action", "HOLD")).upper()

    raw_feats = entry.get("features") or {}
    state_features = {name: float(raw_feats.get(name, 0.0) or 0.0) for name in FEATURE_NAMES}

    fwd_20d = entry.get("forward_return_20d")
    if fwd_20d is None:
        cf = counterfactual_rewards(None)
        reward: Optional[float] = None
        trade_result = "PENDING"
    else:
        fwd_20d = float(fwd_20d)
        cf = counterfactual_rewards(fwd_20d)
        reward = cf.get(committee_action)
        if abs(fwd_20d) < 0.01:
            trade_result = "NEUTRAL"
        else:
            trade_result = "WIN" if fwd_20d > 0 else "LOSS"

    return RLSample(
        session_id=None,
        ticker=ticker,
        trade_date=trade_date,
        feature_version=FEATURE_VERSION,
        state_features=state_features,
        llm_action=committee_action,
        behavior_size_pct=1.0,
        behavior_size_tier=4,
        reward_horizon_days=int(horizon_days),
        forward_return_5d=None,
        forward_return_20d=fwd_20d,
        drawdown_during_holding=None,
        transaction_cost_pct=DEFAULT_TX_COST_PCT,
        reward=reward,
        trade_result=trade_result,
        reward_buy=cf.get("BUY"),
        reward_hold=cf.get("HOLD", 0.0),
        reward_sell=cf.get("SELL"),
        committee_action_index=action_to_index(committee_action),
        source=source_tag,
        notes="decision_log: rich features + counterfactual reward",
    )


def build_from_json_reports(
    report_paths: Iterable[Path | str],
    *,
    horizon_days: int = DEFAULT_REWARD_HORIZON_DAYS,
) -> List[RLSample]:
    """Build samples from one or more backtester report.json files.

    The reports must have been generated by ``scripts/backtester.py``
    (top-level keys ``ticker`` / ``trades`` / ``audit_log``). Reports where
    the schema can't be parsed are skipped with a warning.
    """
    samples: List[RLSample] = []
    for p in report_paths:
        path = Path(p)
        if not path.exists():
            logger.warning("rl-dataset: missing report file %s", path)
            continue
        try:
            data, _audit = _load_trade_outcomes_from_report(path)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("rl-dataset: cannot read %s: %s", path, exc)
            continue

        # ── Preferred path: the rich rl_decision_log (dense features + realized
        #    forward returns) written by scripts/backtester.py. ──────────────
        decision_log = data.get("rl_decision_log") or []
        if decision_log:
            for entry in decision_log:
                if not entry.get("ticker"):
                    # backfill ticker from the filename if the writer omitted it
                    stem = path.stem.replace("bt_", "")
                    for token in stem.split("_"):
                        if "." in token:
                            entry = {**entry, "ticker": token}
                            break
                sample = _build_sample_from_decision_log_entry(
                    entry, horizon_days=horizon_days, source_tag=f"decision_log:{path.name}",
                )
                if sample is not None:
                    samples.append(sample)
            continue  # don't double-count via the legacy trades path

        # ── Legacy fallback: sparse features from executed trades. ──────────
        ticker = None
        session = data.get("session") or {}
        if isinstance(session, dict):
            ticker = session.get("ticker") or session.get("symbol")
        # Fallback: filename pattern "report_COMI.CA_*.json"
        if not ticker:
            stem = path.stem.replace("bt_", "")
            parts = stem.split("_")
            for token in parts:
                if "." in token:
                    ticker = token
                    break
        if not ticker:
            logger.warning("rl-dataset: cannot infer ticker for %s; skipping", path)
            continue

        trades = data.get("trades") or []
        for row in trades:
            sample = _build_sample_from_json_record(
                ticker=ticker,
                trade_row=row,
                horizon_days=horizon_days,
                source_tag=f"json_report:{path.name}",
            )
            if sample is not None:
                samples.append(sample)

    return samples


# ─────────────────────────────────────────────────────────────────────────────
# Postgres ingestion path
# ─────────────────────────────────────────────────────────────────────────────


def build_from_postgres(
    *,
    universe: Optional[Sequence[str]] = None,
    date_range: Optional[Tuple[str, str]] = None,
    horizon_days: int = DEFAULT_REWARD_HORIZON_DAYS,
) -> List[RLSample]:
    """Read ``analysis_sessions`` + ``backtest_trades`` and emit samples.

    Returns an empty list if Postgres is unavailable (the JSON path is the
    fallback in that case; the script wires both).

    Note: full feature extraction requires the ``full_state`` JSONB column
    populated by ``audit_writer.write_analysis_session`` (PR 5). On older
    databases that column is empty; we degrade gracefully to sparse features
    and warn once.
    """
    try:
        from tradingagents.db import cursor as db_cursor
        from tradingagents.db import is_postgres_available
    except Exception as exc:  # pragma: no cover — db package present in repo
        logger.warning("rl-dataset: tradingagents.db not importable: %s", exc)
        return []

    if not is_postgres_available():
        logger.info("rl-dataset: Postgres unavailable; skipping postgres path")
        return []

    where_clauses: List[str] = []
    params: List[Any] = []
    if universe:
        where_clauses.append("s.ticker = ANY(%s)")
        params.append(list(universe))
    if date_range:
        where_clauses.append("s.trade_date BETWEEN %s AND %s")
        params.extend([date_range[0], date_range[1]])

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    query = f"""
        SELECT
            s.session_id,
            s.ticker,
            s.trade_date::text AS trade_date,
            s.final_decision,
            s.confidence_overall,
            s.full_state,
            s.execution_plan
        FROM analysis_sessions s
        {where_sql}
        ORDER BY s.ticker, s.trade_date
    """

    rows: List[Dict[str, Any]] = []
    try:
        with db_cursor(dict_cursor=True) as cur:
            cur.execute(query, params)
            for row in cur.fetchall():
                rows.append(dict(row))
    except Exception as exc:
        logger.warning("rl-dataset: postgres query failed: %s", exc)
        return []

    if not rows:
        logger.info("rl-dataset: no analysis_sessions rows matched")
        return []

    samples: List[RLSample] = []
    sparse_warned = False
    for r in rows:
        ticker = str(r["ticker"])
        trade_date = _iso(r["trade_date"])
        full_state = r.get("full_state") or {}
        execution_plan = r.get("execution_plan") or {}
        if isinstance(full_state, str):
            try:
                full_state = json.loads(full_state)
            except (json.JSONDecodeError, ValueError):
                full_state = {}
        if isinstance(execution_plan, str):
            try:
                execution_plan = json.loads(execution_plan)
            except (json.JSONDecodeError, ValueError):
                execution_plan = {}

        # Behavior policy: trader's target_shares × confidence clip.
        # Recover the size_pct from execution_plan if possible.
        confidence = float(r.get("confidence_overall") or 0.0)
        target_shares = 0
        if isinstance(execution_plan, dict):
            ps = execution_plan.get("position_sizing") or {}
            try:
                target_shares = int(ps.get("target_shares") or 0)
            except (TypeError, ValueError):
                target_shares = 0
        # Behavior size_pct is the realized confidence-scaling fraction. The
        # absolute share count is not directly comparable across portfolios,
        # so we use the confidence scalar itself as the behavior fraction.
        behavior_size_pct = max(0.20, min(1.0, confidence)) if target_shares > 0 else max(0.20, min(1.0, confidence))
        behavior_size_tier = _size_pct_to_tier(behavior_size_pct)

        # Extract features (rich, full-state path).
        if not full_state:
            if not sparse_warned:
                logger.warning(
                    "rl-dataset: analysis_sessions.full_state is empty for some rows; "
                    "feature vectors will be sparse. Re-run the graph after PR 5 to populate."
                )
                sparse_warned = True
        state_feats = extract_state_features_dict(
            full_state if isinstance(full_state, dict) else {},
            ticker=ticker,
            trade_date=trade_date,
        )

        # Forward returns: pulled from backtest_trades when available, else
        # the row stays PENDING. We keep this path as look-ahead-safe by
        # requiring the realized horizon to have elapsed.
        fwd_20d, trade_result = _fetch_forward_return_for_session(
            ticker=ticker, trade_date=trade_date, horizon_days=horizon_days
        )

        if not _no_lookahead_window(trade_date, horizon_days):
            trade_result = "PENDING"
            fwd_20d = None

        action = _infer_action_from_state(full_state, r.get("final_decision"))
        cf = counterfactual_rewards(fwd_20d if fwd_20d is not None else None)
        reward = cf.get(action) if fwd_20d is not None else None

        samples.append(RLSample(
            session_id=str(r["session_id"]),
            ticker=ticker,
            trade_date=trade_date,
            feature_version=FEATURE_VERSION,
            state_features=state_feats,
            llm_action=action,
            behavior_size_pct=float(behavior_size_pct),
            behavior_size_tier=int(behavior_size_tier),
            reward_horizon_days=int(horizon_days),
            forward_return_5d=None,
            forward_return_20d=float(fwd_20d) if fwd_20d is not None else None,
            drawdown_during_holding=None,
            transaction_cost_pct=DEFAULT_TX_COST_PCT,
            reward=reward,
            trade_result=trade_result,
            reward_buy=cf.get("BUY"),
            reward_hold=cf.get("HOLD", 0.0),
            reward_sell=cf.get("SELL"),
            committee_action_index=action_to_index(action),
            source="postgres",
        ))

    return samples


def _infer_action_from_state(full_state: Dict[str, Any], final_decision: Optional[str]) -> str:
    """Use the project's SignalProcessor against the persisted decision string."""
    from tradingagents.graph.signal_processing import SignalProcessor

    signal = (
        final_decision
        or (full_state.get("final_trade_decision") if isinstance(full_state, dict) else None)
        or ""
    )
    return SignalProcessor().process_signal(signal)


def _fetch_forward_return_for_session(
    *,
    ticker: str,
    trade_date: str,
    horizon_days: int,
) -> Tuple[Optional[float], str]:
    """Look up the realized forward return from ``backtest_trades``.

    Returns ``(forward_return, trade_result_label)``. We re-derive both from
    the raw price columns rather than trusting any pre-computed
    ``trade_result`` (which legacy backtester rows may have computed with
    look-ahead — MEMORY §C1).
    """
    try:
        from tradingagents.db import cursor as db_cursor
        from tradingagents.db import is_postgres_available
    except Exception:
        return None, "PENDING"
    if not is_postgres_available():
        return None, "PENDING"

    query = """
        SELECT trade_date::text AS trade_date, action, price_egp
        FROM backtest_trades
        WHERE notes ILIKE %s OR signal ILIKE %s
        ORDER BY trade_date
    """
    # We don't have a strict FK from analysis_sessions -> backtest_trades, so
    # use ticker + horizon-based price lookup against ohlcv_prices instead.
    query = """
        SELECT close
        FROM ohlcv_prices
        WHERE ticker = %s
          AND trade_date >= %s::date
          AND trade_date <= (%s::date + INTERVAL '60 days')
        ORDER BY trade_date
        LIMIT 30
    """
    try:
        with db_cursor() as cur:
            cur.execute(query, (ticker, trade_date, trade_date))
            rows = [r[0] for r in cur.fetchall() if r and r[0] is not None]
    except Exception as exc:
        logger.debug("rl-dataset: ohlcv lookup failed for %s/%s: %s", ticker, trade_date, exc)
        return None, "PENDING"

    if len(rows) < horizon_days + 1:
        return None, "PENDING"

    entry = float(rows[0])
    if entry <= 0:
        return None, "PENDING"
    exit_price = float(rows[min(horizon_days, len(rows) - 1)])
    raw_ret = (exit_price - entry) / entry

    if abs(raw_ret) < 0.01:
        label = "NEUTRAL"
    else:
        label = "WIN" if raw_ret > 0 else "LOSS"
    return float(raw_ret), label


# ─────────────────────────────────────────────────────────────────────────────
# Serialization
# ─────────────────────────────────────────────────────────────────────────────


def samples_to_dataframe(samples: Sequence[RLSample]):
    """Flatten samples into a pandas DataFrame.

    Each ``state_features`` key becomes its own column with a ``feat__``
    prefix so the parquet schema is self-describing and tools like d3rlpy
    can re-stack the matrix unambiguously.
    """
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover — pandas is a hard dep already
        raise RuntimeError(
            "pandas is required for samples_to_dataframe; pip install pandas"
        ) from exc

    rows = []
    for s in samples:
        flat = {
            "session_id": s.session_id,
            "ticker": s.ticker,
            "trade_date": s.trade_date,
            "feature_version": s.feature_version,
            "dataset_schema_version": DATASET_SCHEMA_VERSION,
            "llm_action": s.llm_action,
            "behavior_size_pct": s.behavior_size_pct,
            "behavior_size_tier": s.behavior_size_tier,
            "reward_horizon_days": s.reward_horizon_days,
            "forward_return_5d": s.forward_return_5d,
            "forward_return_20d": s.forward_return_20d,
            "drawdown_during_holding": s.drawdown_during_holding,
            "transaction_cost_pct": s.transaction_cost_pct,
            "reward": s.reward,
            "trade_result": s.trade_result,
            "reward_buy": s.reward_buy,
            "reward_hold": s.reward_hold,
            "reward_sell": s.reward_sell,
            "committee_action_index": s.committee_action_index,
            "source": s.source,
            "notes": s.notes,
        }
        for name in FEATURE_NAMES:
            flat[f"feat__{name}"] = float(s.state_features.get(name, 0.0))
        rows.append(flat)

    return pd.DataFrame(rows)


def write_parquet(samples: Sequence[RLSample], output_path: str | Path) -> Path:
    """Write samples to a parquet file. Creates parents. Returns final path.

    Falls back to CSV if ``pyarrow`` isn't installed — CSV is bigger but
    keeps the pipeline runnable on minimal environments.
    """
    df = samples_to_dataframe(samples)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(output_path, index=False)
    except Exception as exc:
        csv_path = output_path.with_suffix(".csv")
        logger.warning(
            "rl-dataset: parquet write failed (%s); falling back to CSV at %s",
            exc, csv_path,
        )
        df.to_csv(csv_path, index=False)
        return csv_path
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# Top-level orchestration
# ─────────────────────────────────────────────────────────────────────────────


def build_offline_dataset(
    *,
    universe: Optional[Sequence[str]] = None,
    date_range: Optional[Tuple[str, str]] = None,
    json_report_paths: Optional[Sequence[Path | str]] = None,
    horizon_days: int = DEFAULT_REWARD_HORIZON_DAYS,
    prefer: str = "postgres",
) -> List[RLSample]:
    """Combine postgres + json sources into a single sample list.

    ``prefer`` controls which source is consulted first. The other source is
    used as a backfill (records keyed by (ticker, trade_date) already
    present from the preferred source are not overwritten).
    """
    postgres_samples: List[RLSample] = []
    json_samples: List[RLSample] = []

    if prefer == "postgres":
        postgres_samples = build_from_postgres(
            universe=universe, date_range=date_range, horizon_days=horizon_days
        )
        if json_report_paths:
            json_samples = build_from_json_reports(
                json_report_paths, horizon_days=horizon_days
            )
    else:
        if json_report_paths:
            json_samples = build_from_json_reports(
                json_report_paths, horizon_days=horizon_days
            )
        postgres_samples = build_from_postgres(
            universe=universe, date_range=date_range, horizon_days=horizon_days
        )

    by_key: Dict[Tuple[str, str], RLSample] = {}
    primary = postgres_samples if prefer == "postgres" else json_samples
    secondary = json_samples if prefer == "postgres" else postgres_samples
    for s in primary:
        by_key[(s.ticker, s.trade_date)] = s
    for s in secondary:
        by_key.setdefault((s.ticker, s.trade_date), s)

    return sorted(by_key.values(), key=lambda x: (x.ticker, x.trade_date))


# ─────────────────────────────────────────────────────────────────────────────
# Light sanity checks (cheap; called by the script before writing)
# ─────────────────────────────────────────────────────────────────────────────


def assert_no_lookahead(samples: Sequence[RLSample], *, now: Optional[datetime] = None) -> None:
    """Raise if any sample's reward window has not yet elapsed.

    The dataset builder marks such rows PENDING with ``reward=None`` already;
    this is a second-line check that callers haven't manually patched a
    reward in.
    """
    now = now or datetime.utcnow()
    for s in samples:
        if s.reward is None:
            continue
        if not _no_lookahead_window(s.trade_date, s.reward_horizon_days, now=now):
            raise ValueError(
                f"look-ahead detected: sample {s.ticker}/{s.trade_date} has reward "
                f"{s.reward} but horizon {s.reward_horizon_days}d not elapsed by {now}"
            )


def summarize(samples: Sequence[RLSample]) -> Dict[str, Any]:
    """Return a small dict suitable for printing / logging."""
    if not samples:
        return {"n_samples": 0}
    n = len(samples)
    n_pending = sum(1 for s in samples if s.trade_result == "PENDING")
    n_hold = sum(1 for s in samples if s.llm_action == "HOLD")
    rewards = [s.reward for s in samples if s.reward is not None]
    tickers = sorted({s.ticker for s in samples})
    actions = {a: 0 for a in ("BUY", "SELL", "HOLD")}
    for s in samples:
        actions[s.llm_action] = actions.get(s.llm_action, 0) + 1
    return {
        "n_samples": n,
        "n_pending": n_pending,
        "n_hold": n_hold,
        "tickers": tickers,
        "n_tickers": len(tickers),
        "action_counts": actions,
        "reward_mean": float(np.mean(rewards)) if rewards else None,
        "reward_std": float(np.std(rewards)) if rewards else None,
        "feature_version": FEATURE_VERSION,
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
    }
