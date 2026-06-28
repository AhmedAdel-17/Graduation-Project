"""Forward paper-trading harness — the only truly look-ahead-free evaluation.

Historical backtests of an LLM system are contaminated by the model's training-data
knowledge of what happened after the trade date (the point-in-time prompt gate in
``agents/utils/temporal.py`` mitigates but cannot erase this). The only way to know
whether the system has real edge is to record a decision *today* — when the future
genuinely does not exist yet — and score it weeks later against the realized price.

Two phases, strictly separated so look-ahead is structurally impossible:

1. **record** (``record_decision``): persist an immutable OPEN decision with the
   entry price as of the run date. The forward return is UNKNOWN here.
2. **score** (``score_matured``): once the horizon has elapsed, read the realized
   price (only ever on/after the target date) and close the decision. Scoring never
   edits the decision itself.

Returns are charged the shared EGX round-trip cost (``egx_costs``), so the metric is
net of what trading actually costs. Only BUY decisions take a paper position; HOLD /
SELL-from-cash are recorded for completeness but scored as ``NO_POSITION`` (the
system's actionable output is BUY-or-cash on long-only EGX).
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

from tradingagents.dataflows.egx_costs import round_trip_cost_pct

logger = logging.getLogger("tradingagents.paper_trading")

DEFAULT_HORIZON_DAYS = 20
DEFAULT_STORE_PATH = "results/paper_trading/decisions.jsonl"

# A move smaller than this (after costs) is treated as a wash, not a win/loss.
_NEUTRAL_BAND = 0.001  # 0.1%

# Realized-result labels
RESULT_WIN = "WIN"
RESULT_LOSS = "LOSS"
RESULT_NEUTRAL = "NEUTRAL"
RESULT_NO_POSITION = "NO_POSITION"


def _iso(d) -> str:
    return str(d)[:10]


def target_eval_date(run_date: str, horizon_days: int) -> str:
    """Calendar date by which ``horizon_days`` trading days have ~elapsed.

    Trading days → calendar days uses a ×1.4 weekend factor (consistent with the
    RL dataset's no-look-ahead window). The price lookup then takes the first
    close on/after this date, so the exact trading-day count is not load-bearing.
    """
    rd = datetime.strptime(_iso(run_date), "%Y-%m-%d")
    return (rd + timedelta(days=round(horizon_days * 1.4))).strftime("%Y-%m-%d")


# ─────────────────────────────────────────────────────────────────────────────
# Record
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class PaperDecision:
    """One immutable forward decision, scored later when its horizon elapses."""

    id: str
    ticker: str
    run_date: str               # the as-of "today" the decision was made on
    decision: str               # BUY / SELL / HOLD
    confidence: Optional[float]
    entry_price: Optional[float]
    low_liquidity: bool
    horizon_days: int
    target_eval_date: str
    round_trip_cost_pct: float

    strategy: str = "llm"       # "llm" or a baseline name (momentum, buy_and_hold, ...)
    status: str = "OPEN"        # OPEN / CLOSED
    exit_price: Optional[float] = None
    gross_return: Optional[float] = None
    net_return: Optional[float] = None
    result: Optional[str] = None  # WIN / LOSS / NEUTRAL / NO_POSITION
    scored_at: Optional[str] = None
    notes: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: Dict) -> "PaperDecision":
        # Only pass keys that are present so newly-added fields with defaults
        # (e.g. ``strategy``) load cleanly from older JSONL rows.
        fields = {
            k: d[k] for k in cls.__dataclass_fields__ if k in d  # type: ignore[attr-defined]
        }
        return cls(**fields)


class PaperTradingStore:
    """Append-only JSONL store with atomic full rewrite on update.

    JSONL keeps the harness runnable without Postgres (graduation environments),
    matching the project's existing JSON-fallback pattern.
    """

    def __init__(self, path: str | Path = DEFAULT_STORE_PATH):
        self.path = Path(path)

    def load(self) -> List[PaperDecision]:
        if not self.path.exists():
            return []
        out: List[PaperDecision] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(PaperDecision.from_dict(json.loads(line)))
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning("paper_trading: skipping malformed row: %s", exc)
        return out

    def append(self, decision: PaperDecision) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(decision.to_json() + "\n")

    def write_all(self, decisions: List[PaperDecision]) -> None:
        """Atomically rewrite the whole store (used after scoring)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for d in decisions:
                f.write(d.to_json() + "\n")
        tmp.replace(self.path)


def record_decision(
    store: PaperTradingStore,
    *,
    ticker: str,
    run_date: str,
    decision: str,
    entry_price: Optional[float],
    confidence: Optional[float] = None,
    low_liquidity: bool = False,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    strategy: str = "llm",
    notes: Optional[str] = None,
    force: bool = False,
) -> PaperDecision:
    """Persist an OPEN forward decision. Idempotent on (ticker, run_date, strategy).

    Returns the existing record (unchanged) if one already exists for the same
    ticker+run_date+strategy and ``force`` is False — re-running ``record`` the same
    day is a no-op rather than a duplicate. The ``strategy`` tag lets the LLM system
    and deterministic baselines coexist in one store and be compared side by side.
    """
    run_date = _iso(run_date)
    decision = (decision or "HOLD").strip().upper()

    if not force:
        for d in store.load():
            if d.ticker == ticker and d.run_date == run_date and d.strategy == strategy:
                logger.info(
                    "paper_trading: %s/%s already recorded for %s; skipping (force=True to override)",
                    ticker, strategy, run_date,
                )
                return d

    rec = PaperDecision(
        id=uuid.uuid4().hex,
        ticker=ticker,
        run_date=run_date,
        decision=decision,
        confidence=confidence,
        entry_price=float(entry_price) if entry_price is not None else None,
        low_liquidity=bool(low_liquidity),
        horizon_days=int(horizon_days),
        target_eval_date=target_eval_date(run_date, horizon_days),
        round_trip_cost_pct=round_trip_cost_pct(low_liquidity),
        strategy=strategy,
        notes=notes,
    )
    store.append(rec)
    logger.info(
        "paper_trading: recorded %s %s @ %s (eval ~%s)",
        decision, ticker, rec.entry_price, rec.target_eval_date,
    )
    return rec


# ─────────────────────────────────────────────────────────────────────────────
# Score
# ─────────────────────────────────────────────────────────────────────────────


def _close_decision(d: PaperDecision, exit_price: float, now: datetime) -> None:
    d.exit_price = float(exit_price)
    d.scored_at = now.strftime("%Y-%m-%d")

    if d.decision == "BUY" and d.entry_price and d.entry_price > 0:
        gross = (exit_price - d.entry_price) / d.entry_price
        net = gross - d.round_trip_cost_pct
        d.gross_return = gross
        d.net_return = net
        if net > _NEUTRAL_BAND:
            d.result = RESULT_WIN
        elif net < -_NEUTRAL_BAND:
            d.result = RESULT_LOSS
        else:
            d.result = RESULT_NEUTRAL
    else:
        # HOLD or SELL-from-cash: no paper position was taken.
        d.gross_return = 0.0
        d.net_return = 0.0
        d.result = RESULT_NO_POSITION

    d.status = "CLOSED"


def score_matured(
    store: PaperTradingStore,
    price_lookup: Callable[[str, str], Optional[float]],
    *,
    now: Optional[datetime] = None,
) -> List[PaperDecision]:
    """Close every OPEN decision whose horizon has elapsed and whose realized
    price is available.

    ``price_lookup(ticker, on_or_after_date)`` must return the close price on/after
    the given date, or ``None`` if not yet available (data not published) — such
    decisions stay OPEN and are retried on the next score run.

    Look-ahead is structurally impossible: a decision is only scored once
    ``target_eval_date <= now``, and the price is only ever read on/after that date.
    """
    now = now or datetime.now()
    decisions = store.load()
    newly_closed: List[PaperDecision] = []

    for d in decisions:
        if d.status != "OPEN":
            continue
        try:
            matured = datetime.strptime(d.target_eval_date, "%Y-%m-%d") <= now
        except ValueError:
            continue
        if not matured:
            continue

        # HOLD / SELL still need an exit price only for BUY P&L; for NO_POSITION
        # we can close immediately once matured (return is 0 regardless).
        if d.decision != "BUY":
            _close_decision(d, exit_price=(d.entry_price or 0.0), now=now)
            newly_closed.append(d)
            continue

        exit_price = price_lookup(d.ticker, d.target_eval_date)
        if exit_price is None or exit_price <= 0:
            logger.info(
                "paper_trading: %s matured (%s) but realized price not yet available; leaving OPEN",
                d.ticker, d.target_eval_date,
            )
            continue
        _close_decision(d, exit_price=exit_price, now=now)
        newly_closed.append(d)

    if newly_closed:
        store.write_all(decisions)
        logger.info("paper_trading: closed %d matured decision(s)", len(newly_closed))
    return newly_closed


# ─────────────────────────────────────────────────────────────────────────────
# Metrics
# ─────────────────────────────────────────────────────────────────────────────


def compute_metrics(
    decisions: List[PaperDecision],
    *,
    benchmark_return: Optional[float] = None,
    strategy: Optional[str] = None,
) -> Dict:
    """Aggregate CLOSED decisions into an honest, cost-net scorecard.

    Headline numbers are BUY-only (the actionable signal): hit rate with a Wilson
    95% CI, mean net return, and — when a benchmark buy-and-hold return is supplied
    — the net excess over benchmark. NEUTRAL outcomes are excluded from the hit-rate
    denominator; NO_POSITION (HOLD/SELL) decisions are reported but not traded.

    Pass ``strategy`` to score only one strategy's decisions (e.g. "llm" vs
    "momentum"); ``None`` scores all of them together.
    """
    if strategy is not None:
        decisions = [d for d in decisions if d.strategy == strategy]
    closed = [d for d in decisions if d.status == "CLOSED"]
    open_n = sum(1 for d in decisions if d.status == "OPEN")
    buys = [d for d in closed if d.decision == "BUY"]

    wins = sum(1 for d in buys if d.result == RESULT_WIN)
    losses = sum(1 for d in buys if d.result == RESULT_LOSS)
    neutrals = sum(1 for d in buys if d.result == RESULT_NEUTRAL)
    decided = wins + losses
    hit_rate = (wins / decided) if decided > 0 else None

    ci_lo = ci_hi = None
    if decided > 0:
        try:
            from tradingagents.rl.walkforward import _wilson_ci
            ci_lo, ci_hi = _wilson_ci(wins, decided)
        except Exception:  # pragma: no cover — CI is best-effort
            ci_lo = ci_hi = None

    net_returns = [d.net_return for d in buys if d.net_return is not None]
    mean_net = (sum(net_returns) / len(net_returns)) if net_returns else None

    metrics: Dict = {
        "n_decisions": len(decisions),
        "n_closed": len(closed),
        "n_open": open_n,
        "n_buy_closed": len(buys),
        "n_hold_or_sell_closed": len(closed) - len(buys),
        "buy_wins": wins,
        "buy_losses": losses,
        "buy_neutrals": neutrals,
        "buy_hit_rate": hit_rate,
        "buy_hit_rate_ci95": [ci_lo, ci_hi] if ci_lo is not None else None,
        "buy_mean_net_return": mean_net,
    }
    if strategy is not None:
        metrics["strategy"] = strategy
    if benchmark_return is not None and mean_net is not None:
        metrics["benchmark_return"] = benchmark_return
        metrics["mean_net_excess_vs_benchmark"] = mean_net - benchmark_return
    return metrics


def group_metrics_by_strategy(
    decisions: List[PaperDecision],
    *,
    benchmark_return: Optional[float] = None,
) -> Dict[str, Dict]:
    """Return ``{strategy: metrics}`` so the LLM system can be read against each
    baseline in one glance. The whole point: if "llm" does not beat "momentum"
    net of costs, the multi-agent stack is not earning its complexity.
    """
    strategies = sorted({d.strategy for d in decisions})
    return {
        s: compute_metrics(decisions, benchmark_return=benchmark_return, strategy=s)
        for s in strategies
    }
