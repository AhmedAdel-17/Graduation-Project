"""
Decision-quality metrics for the EGX multi-agent backtester
===========================================================

This module answers the core thesis question — *"are the agents' BUY / HOLD /
SELL calls skillful, or no better than chance?"* — independently of portfolio
P&L. It is the primary evidence for Chapter 8 because it survives the system's
rational HOLD-bias (a portfolio that mostly holds cash has a flat equity curve,
but its *predictions* can still be scored).

Design rules (mirror ``agents/utils/scoring.py`` discipline):

* **Pure & deterministic.** No LLM, no network, no global state. Identical
  output for identical input — unit-testable with synthetic data.
* **Leak-safe.** Forward returns are computed *post-hoc* from a price series the
  caller supplies. Nothing here is ever fed back to the agents or to memory
  (feeding forward returns into memory was the look-ahead bug removed in
  MEMORY.md §C1). It is reporting only.
* **Fixed forward horizons** (default 5 / 10 / 20 trading days) rather than the
  variable "next evaluation date" horizon of the old
  ``backtester._calculate_directional_accuracy`` — fixed horizons make the
  hit-rate comparable across runs and tickers.

Correctness convention (``band`` = HOLD dead-band, default ±1%):

    realized direction:  UP  if fwd_ret >  +band
                         DOWN if fwd_ret <  -band
                         FLAT otherwise
    BUY  correct  ⇔ realized == UP
    SELL correct  ⇔ realized == DOWN
    HOLD correct  ⇔ realized == FLAT

Statistical comparators reported per horizon:

* actionable (BUY+SELL) hit-rate with a Wilson 95% CI,
* a one-sided binomial test of that hit-rate vs a 50% coin-flip,
* the Information Coefficient (Spearman ρ of the signed signal vs forward return)
  with its p-value,
* an "always-BUY" baseline (correct exactly on UP moves) and a Monte-Carlo
  random-decision baseline (same class frequencies), so the chapter can state
  "the agent beats random by X (p < Y)".
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Reuse the SAME Wilson CI the backtester / RL eval already use so every win-rate
# in the project is computed identically (MEMORY.md §C1).
from tradingagents.rl.walkforward import _wilson_ci

try:  # SciPy is a hard dependency of the project, but degrade gracefully.
    from scipy import stats as _scipy_stats  # type: ignore
except Exception:  # pragma: no cover - scipy missing
    _scipy_stats = None


DEFAULT_HORIZONS: Tuple[int, ...] = (5, 10, 20)
DEFAULT_HOLD_BAND_PCT: float = 0.01  # ±1% dead-band for "flat"
# Confidence-bucket edges for the calibration curve (agent confidence in [0,1]).
DEFAULT_CONF_BUCKETS: Tuple[float, ...] = (0.0, 0.4, 0.55, 0.7, 0.85, 1.0)
_SIGNAL_VALUE = {"BUY": 1.0, "HOLD": 0.0, "SELL": -1.0}


# ─────────────────────────────────────────────────────────────────────────────
# Inputs
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Decision:
    """One agent decision on one evaluation date.

    ``confidence`` is optional and normalized to [0, 1] (values > 1 are treated
    as a 0–100 scale and divided by 100). ``session_id`` is carried through only
    so callers can join a scored decision back to its reasoning trace.
    """

    date: str
    action: str
    confidence: Optional[float] = None
    session_id: Optional[str] = None

    @property
    def norm_action(self) -> str:
        a = (self.action or "HOLD").strip().upper()
        if a in ("STRONG_BUY", "BUY"):
            return "BUY"
        if a in ("STRONG_SELL", "SELL"):
            return "SELL"
        return "HOLD"

    @property
    def norm_confidence(self) -> Optional[float]:
        c = self.confidence
        if c is None:
            return None
        try:
            c = float(c)
        except (TypeError, ValueError):
            return None
        if c > 1.0:  # 0–100 scale → 0–1
            c = c / 100.0
        return max(0.0, min(1.0, c))


def _coerce_decisions(raw: Sequence[Any]) -> List[Decision]:
    out: List[Decision] = []
    for r in raw:
        if isinstance(r, Decision):
            out.append(r)
        elif isinstance(r, dict):
            out.append(
                Decision(
                    date=str(r.get("date") or r.get("trade_date") or ""),
                    action=str(
                        r.get("action")
                        or r.get("decision")
                        or r.get("parsed_decision")
                        or "HOLD"
                    ),
                    confidence=r.get("confidence"),
                    session_id=r.get("session_id"),
                )
            )
    return [d for d in out if d.date]


# ─────────────────────────────────────────────────────────────────────────────
# Forward-return engine (fixed horizon in trading days)
# ─────────────────────────────────────────────────────────────────────────────


def _sorted_series(price_series: Dict[str, float]) -> Tuple[List[str], List[float]]:
    """Sort a ``{date: close}`` map into parallel (dates, prices) lists,
    dropping non-positive prices."""
    items = sorted(
        (d, float(p))
        for d, p in price_series.items()
        if p is not None and float(p) > 0.0
    )
    dates = [d for d, _ in items]
    prices = [p for _, p in items]
    return dates, prices


def _index_at_or_before(dates: List[str], target: str) -> int:
    """Position of the last date <= ``target`` (bisect_right - 1), or -1."""
    import bisect

    i = bisect.bisect_right(dates, target) - 1
    return i


def forward_returns_for_decisions(
    decisions: Sequence[Any],
    price_series: Dict[str, float],
    horizon_days: int,
) -> List[Tuple[Decision, Optional[float]]]:
    """For each decision, the realized return over ``horizon_days`` *trading
    days* forward, using ``price_series`` (a dense daily ``{date: close}`` map).

    The decision date is snapped to the last available close on/before it; the
    forward point is ``horizon_days`` positions later in the sorted series.
    Decisions whose forward point lies beyond the series are returned with
    ``None`` (not evaluable at this horizon — never silently dropped).
    """
    decs = _coerce_decisions(decisions)
    dates, prices = _sorted_series(price_series)
    out: List[Tuple[Decision, Optional[float]]] = []
    if len(dates) < 2:
        return [(d, None) for d in decs]
    for d in decs:
        i0 = _index_at_or_before(dates, d.date)
        if i0 < 0:
            out.append((d, None))
            continue
        i1 = i0 + int(horizon_days)
        if i1 >= len(prices) or prices[i0] <= 0:
            out.append((d, None))
            continue
        fwd = prices[i1] / prices[i0] - 1.0
        out.append((d, float(fwd)))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Stats helpers
# ─────────────────────────────────────────────────────────────────────────────


def _realized_direction(fwd: float, band: float) -> str:
    if fwd > band:
        return "UP"
    if fwd < -band:
        return "DOWN"
    return "FLAT"


def _is_correct(action: str, fwd: float, band: float) -> bool:
    direction = _realized_direction(fwd, band)
    if action == "BUY":
        return direction == "UP"
    if action == "SELL":
        return direction == "DOWN"
    return direction == "FLAT"  # HOLD


def _spearman(xs: List[float], ys: List[float]) -> Tuple[Optional[float], Optional[float]]:
    """Spearman ρ + two-sided p-value. SciPy if available, else a rank-Pearson
    fallback (ρ only, p-value None)."""
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None, None
    if _scipy_stats is not None:
        res = _scipy_stats.spearmanr(xs, ys)
        rho = float(res.statistic) if res.statistic is not None else None
        pval = float(res.pvalue) if res.pvalue is not None else None
        if rho is not None and math.isnan(rho):
            return None, None
        return rho, pval
    # Fallback: Pearson on ranks.
    def _rank(v: List[float]) -> List[float]:
        order = sorted(range(len(v)), key=lambda k: v[k])
        ranks = [0.0] * len(v)
        for pos, idx in enumerate(order):
            ranks[idx] = float(pos)
        return ranks

    rx, ry = _rank(xs), _rank(ys)
    n = len(rx)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if vx == 0 or vy == 0:
        return None, None
    return cov / (vx * vy), None


def _binom_greater_p(wins: int, n: int, p0: float = 0.5) -> Optional[float]:
    """One-sided binomial p-value: P(X >= wins) under Binom(n, p0)."""
    if n <= 0:
        return None
    if _scipy_stats is not None:
        try:
            return float(
                _scipy_stats.binomtest(wins, n, p0, alternative="greater").pvalue
            )
        except Exception:
            pass
    # Exact fallback via the survival sum.
    from math import comb

    return float(
        sum(comb(n, k) * (p0 ** k) * ((1 - p0) ** (n - k)) for k in range(wins, n + 1))
    )


def _monte_carlo_random_baseline(
    realized_dirs: List[str],
    class_freq: Dict[str, float],
    band_correct_for: Dict[str, str],
    n_samples: int = 2000,
    seed: int = 42,
) -> Dict[str, float]:
    """Distribution of the hit-rate a RANDOM agent would achieve, drawing each
    decision's action from the agent's own class frequencies. Reported so the
    thesis can quote "agent vs random" honestly rather than vs an arbitrary 50%.
    """
    if not realized_dirs:
        return {"mean": 0.0, "std": 0.0, "p95": 0.0}
    rng = random.Random(seed)
    actions = list(class_freq.keys())
    weights = [class_freq[a] for a in actions]
    n = len(realized_dirs)
    hit_rates: List[float] = []
    for _ in range(n_samples):
        correct = 0
        for direction in realized_dirs:
            a = rng.choices(actions, weights=weights, k=1)[0]
            if band_correct_for[a] == direction:
                correct += 1
        hit_rates.append(correct / n)
    hit_rates.sort()
    mean = sum(hit_rates) / len(hit_rates)
    var = sum((h - mean) ** 2 for h in hit_rates) / len(hit_rates)
    p95 = hit_rates[min(len(hit_rates) - 1, int(0.95 * len(hit_rates)))]
    return {"mean": round(mean, 4), "std": round(math.sqrt(var), 4), "p95": round(p95, 4)}


# ─────────────────────────────────────────────────────────────────────────────
# Per-horizon scoring
# ─────────────────────────────────────────────────────────────────────────────


def _score_horizon(
    scored: List[Tuple[Decision, Optional[float]]],
    horizon_days: int,
    band: float,
) -> Dict[str, Any]:
    pairs = [(d, f) for d, f in scored if f is not None]
    n_eval = len(pairs)
    per_class = {k: {"n": 0, "correct": 0} for k in ("BUY", "SELL", "HOLD")}
    confusion = {
        a: {"UP": 0, "FLAT": 0, "DOWN": 0} for a in ("BUY", "HOLD", "SELL")
    }
    realized_dirs: List[str] = []
    sig_vals: List[float] = []
    fwd_vals: List[float] = []
    overall_correct = 0

    for d, fwd in pairs:
        a = d.norm_action
        direction = _realized_direction(fwd, band)
        ok = _is_correct(a, fwd, band)
        per_class[a]["n"] += 1
        per_class[a]["correct"] += int(ok)
        confusion[a][direction] += 1
        overall_correct += int(ok)
        realized_dirs.append(direction)
        sig_vals.append(_SIGNAL_VALUE[a])
        fwd_vals.append(fwd)

    for k in per_class:
        n = per_class[k]["n"]
        per_class[k]["hit_rate"] = (
            round(per_class[k]["correct"] / n, 4) if n else None
        )

    actionable_n = per_class["BUY"]["n"] + per_class["SELL"]["n"]
    actionable_correct = per_class["BUY"]["correct"] + per_class["SELL"]["correct"]
    actionable_hit = (
        round(actionable_correct / actionable_n, 4) if actionable_n else None
    )
    ci_lo, ci_hi = (
        _wilson_ci(actionable_correct, actionable_n) if actionable_n else (None, None)
    )
    binom_p = _binom_greater_p(actionable_correct, actionable_n, 0.5) if actionable_n else None

    ic, ic_p = _spearman(sig_vals, fwd_vals)

    n_up = sum(1 for x in realized_dirs if x == "UP")
    base_rate_up = round(n_up / n_eval, 4) if n_eval else None

    class_freq = {
        a: (per_class[a]["n"] / n_eval if n_eval else 0.0)
        for a in ("BUY", "HOLD", "SELL")
    }
    band_correct_for = {"BUY": "UP", "SELL": "DOWN", "HOLD": "FLAT"}
    random_bl = _monte_carlo_random_baseline(
        realized_dirs, class_freq, band_correct_for
    )
    # Always-BUY is correct exactly on UP moves.
    always_buy_hit = base_rate_up

    return {
        "horizon_days": horizon_days,
        "n_evaluated": n_eval,
        "n_not_evaluable": len(scored) - n_eval,
        "overall_hit_rate": round(overall_correct / n_eval, 4) if n_eval else None,
        "actionable_n": actionable_n,
        "actionable_hit_rate": actionable_hit,
        "actionable_ci_lo": round(ci_lo, 4) if ci_lo is not None else None,
        "actionable_ci_hi": round(ci_hi, 4) if ci_hi is not None else None,
        "actionable_binomial_p_vs_50pct": round(binom_p, 5) if binom_p is not None else None,
        "information_coefficient": round(ic, 4) if ic is not None else None,
        "ic_p_value": round(ic_p, 5) if ic_p is not None else None,
        "by_decision": per_class,
        "confusion_matrix": confusion,
        "base_rate_up": base_rate_up,
        "baseline_always_buy_hit_rate": always_buy_hit,
        "baseline_random": random_bl,
    }


def _calibration(
    scored: List[Tuple[Decision, Optional[float]]],
    band: float,
    buckets: Tuple[float, ...],
) -> List[Dict[str, Any]]:
    """Hit-rate per agent-confidence bucket (actionable decisions only).

    Answers: does higher stated confidence actually predict higher accuracy?
    """
    out: List[Dict[str, Any]] = []
    for lo, hi in zip(buckets[:-1], buckets[1:]):
        in_bucket = [
            (d, f)
            for d, f in scored
            if f is not None
            and d.norm_action in ("BUY", "SELL")
            and d.norm_confidence is not None
            and (lo <= d.norm_confidence < hi or (hi == buckets[-1] and d.norm_confidence == hi))
        ]
        n = len(in_bucket)
        correct = sum(1 for d, f in in_bucket if _is_correct(d.norm_action, f, band))
        out.append(
            {
                "bucket": f"{lo:.2f}-{hi:.2f}",
                "n": n,
                "hit_rate": round(correct / n, 4) if n else None,
            }
        )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────


def per_decision_detail(
    decisions: Sequence[Any],
    price_series: Dict[str, float],
    *,
    horizons: Tuple[int, ...] = DEFAULT_HORIZONS,
    primary_horizon: int = 10,
    hold_band_pct: float = DEFAULT_HOLD_BAND_PCT,
) -> List[Dict[str, Any]]:
    """One row per decision with its forward returns at every horizon and a
    correctness flag at ``primary_horizon``.

    This is what the dashboard's per-prediction list and the thesis CSV consume.
    ``session_id`` is preserved so each row links straight to its reasoning
    trace (``/api/sessions/{id}/trace``).
    """
    decs = _coerce_decisions(decisions)
    band = float(hold_band_pct)
    fwd_by_h: Dict[int, Dict[str, Optional[float]]] = {}
    for h in horizons:
        fwd_by_h[h] = {
            id(d): f for d, f in forward_returns_for_decisions(decs, price_series, h)
        }
    rows: List[Dict[str, Any]] = []
    for d in decs:
        primary_fwd = fwd_by_h.get(primary_horizon, {}).get(id(d))
        correct: Optional[bool] = None
        realized: Optional[str] = None
        if primary_fwd is not None:
            correct = _is_correct(d.norm_action, primary_fwd, band)
            realized = _realized_direction(primary_fwd, band)
        row: Dict[str, Any] = {
            "date": d.date,
            "session_id": d.session_id,
            "decision": d.norm_action,
            "confidence": d.norm_confidence,
            "correct": correct,
            "realized_direction": realized,
            "primary_horizon_days": primary_horizon,
        }
        for h in horizons:
            fv = fwd_by_h[h].get(id(d))
            row[f"forward_return_{h}d"] = round(fv * 100, 4) if fv is not None else None
        rows.append(row)
    return rows


def compute_decision_quality(
    decisions: Sequence[Any],
    price_series: Dict[str, float],
    *,
    horizons: Tuple[int, ...] = DEFAULT_HORIZONS,
    hold_band_pct: float = DEFAULT_HOLD_BAND_PCT,
    primary_horizon: Optional[int] = None,
    conf_buckets: Tuple[float, ...] = DEFAULT_CONF_BUCKETS,
) -> Dict[str, Any]:
    """Score a list of agent decisions against realized forward returns.

    Parameters
    ----------
    decisions
        ``Decision`` instances or dicts with ``date`` + ``action``/``decision``
        (and optional ``confidence``, ``session_id``).
    price_series
        Dense daily ``{date: close}`` map for the ticker. To score the latest
        decisions at the longest horizon it should extend ``max(horizons)``
        trading days past the last decision date. Supplying it is the caller's
        responsibility; this function never fetches data.
    horizons
        Forward horizons in trading days (default 5/10/20).
    hold_band_pct
        Dead-band that separates UP/DOWN from FLAT (default ±1%).

    Returns a JSON-serializable dict with one block per horizon plus a
    calibration curve at the primary horizon.
    """
    decs = _coerce_decisions(decisions)
    band = float(hold_band_pct)
    primary = primary_horizon or (horizons[len(horizons) // 2] if horizons else 10)

    per_horizon: Dict[str, Any] = {}
    scored_primary: List[Tuple[Decision, Optional[float]]] = []
    for h in horizons:
        scored = forward_returns_for_decisions(decs, price_series, h)
        per_horizon[str(h)] = _score_horizon(scored, h, band)
        if h == primary:
            scored_primary = scored
    if not scored_primary and horizons:
        scored_primary = forward_returns_for_decisions(decs, price_series, primary)

    action_counts = {a: 0 for a in ("BUY", "SELL", "HOLD")}
    for d in decs:
        action_counts[d.norm_action] += 1

    return {
        "method": "fixed_horizon_forward_return",
        "hold_band_pct": round(band * 100, 4),
        "primary_horizon_days": primary,
        "n_decisions": len(decs),
        "action_distribution": action_counts,
        "horizons": per_horizon,
        "calibration_primary_horizon": _calibration(scored_primary, band, conf_buckets),
        "note": (
            "Leak-safe / reporting-only. Forward returns are computed post-hoc "
            "from the supplied price series and are never fed back to the agents "
            "or memory (see MEMORY.md §C1). HOLD is a scored decision: correct "
            "when the realized move stays within the dead-band."
        ),
    }
