"""Scenario-tree operations for the What-If layer (roadmap P1, Enhancement 2).

Pure transformations over the typed scenario vocabulary — no store, no LLM. The
copilot service (P3) wires these into the persistent workspace; the optimizer
re-runs on the derived state (a what-if is "fork the inputs, re-run the same pure
functions, diff the outputs" — there is NO separate simulation engine).

* ``apply_patch`` — base (snapshot, policy) + ScenarioPatch → derived (snapshot,
  policy). Structural ops change the snapshot; policy ops change a derived policy.
* ``extract_directives`` — pulls non-structural hints (TARGET_RISK_DELTA) the
  optimizer caller applies as a vol constraint (needs a reference vol it owns).
* ``build_scenario`` — assemble a Scenario node (auto-labels if unlabeled).
* ``promote_to_baseline`` — adopt a scenario's derived snapshot as a new baseline.
* ``diff_proposals`` — metric deltas between two proposals → ScenarioCompareData.

Baseline is never mutated: every transformation returns deep copies.
"""

from __future__ import annotations

import logging
from typing import Mapping, Optional

from tradingagents.portfolio.schemas import (
    AddCashOp,
    ClosePositionOp,
    ExcludeSectorOp,
    ExcludeTickerOp,
    InvestmentPolicy,
    OptimizationProposal,
    OverridePolicyOp,
    PinnedInputSet,
    PortfolioSnapshot,
    RemoveCashOp,
    Scenario,
    ScenarioCompareData,
    ScenarioPatch,
    ScenarioStatus,
    ScalePositionOp,
    SetPositionWeightOp,
    TargetRiskDeltaOp,
)

logger = logging.getLogger("tradingagents.portfolio.scenarios")


def apply_patch(
    snapshot: PortfolioSnapshot,
    policy: InvestmentPolicy,
    patch: ScenarioPatch,
    *,
    prices: Optional[Mapping[str, float]] = None,
) -> tuple[PortfolioSnapshot, Optional[InvestmentPolicy]]:
    """Apply ``patch`` to a base (snapshot, policy), returning derived deep copies.

    ``derived_policy`` is ``None`` when no policy-affecting op is present (the base
    policy still applies). Closing a position credits its proceeds to cash when
    ``prices`` (and the holding's share count) are available.
    """
    snap = snapshot.model_copy(deep=True)
    holdings = {h.ticker: h for h in snap.holdings}

    policy_changed = False
    derived_policy = policy.model_copy(deep=True)

    for op in patch.ops:
        if isinstance(op, AddCashOp):
            snap.cash_egp += op.amount_egp
        elif isinstance(op, RemoveCashOp):
            snap.cash_egp = max(0.0, snap.cash_egp - op.amount_egp)
        elif isinstance(op, ClosePositionOp):
            h = holdings.pop(op.ticker, None)
            if h is not None and prices and h.shares is not None and op.ticker in prices:
                snap.cash_egp += float(h.shares) * float(prices[op.ticker])  # proceeds
        elif isinstance(op, ScalePositionOp):
            h = holdings.get(op.ticker)
            if h is not None:
                if h.shares is not None:
                    h.shares = h.shares * op.factor
                elif h.weight_pct is not None:
                    h.weight_pct = min(100.0, h.weight_pct * op.factor)
        elif isinstance(op, SetPositionWeightOp):
            h = holdings.get(op.ticker)
            if h is not None:
                h.weight_pct = op.weight_pct
                h.shares = None  # becomes a target weight; re-valued at analytics time
        elif isinstance(op, ExcludeTickerOp):
            if op.ticker not in derived_policy.excluded_tickers:
                derived_policy.excluded_tickers = [*derived_policy.excluded_tickers, op.ticker]
                policy_changed = True
        elif isinstance(op, ExcludeSectorOp):
            if op.sector not in derived_policy.excluded_sectors:
                derived_policy.excluded_sectors = [*derived_policy.excluded_sectors, op.sector]
                policy_changed = True
        elif isinstance(op, OverridePolicyOp):
            data = derived_policy.model_dump()
            data[op.field] = op.value  # pydantic coerces (e.g. 'high' -> RiskTolerance.HIGH)
            derived_policy = InvestmentPolicy.model_validate(data)
            policy_changed = True
        elif isinstance(op, TargetRiskDeltaOp):
            pass  # non-structural; surfaced via extract_directives()

    snap.holdings = list(holdings.values())
    if policy_changed:
        derived_policy.version = policy.version + 1
        return snap, derived_policy
    return snap, None


def extract_directives(patch: ScenarioPatch) -> dict:
    """Non-structural optimizer hints derivable from the patch. Currently the
    target-vol delta from TARGET_RISK_DELTA (e.g. -20 → multiply reference vol by
    0.8); the optimizer caller turns this into an absolute ``vol_target``."""
    directives: dict = {}
    for op in patch.ops:
        if isinstance(op, TargetRiskDeltaOp):
            directives["target_vol_mult"] = 1.0 + op.vol_delta_pct / 100.0
    return directives


def _auto_label(patch: ScenarioPatch) -> str:
    parts: list[str] = []
    for op in patch.ops:
        if isinstance(op, AddCashOp):
            parts.append(f"+{op.amount_egp:,.0f} EGP cash")
        elif isinstance(op, RemoveCashOp):
            parts.append(f"-{op.amount_egp:,.0f} EGP cash")
        elif isinstance(op, ClosePositionOp):
            parts.append(f"Sell all {op.ticker}")
        elif isinstance(op, ScalePositionOp):
            parts.append(f"{op.ticker} ×{op.factor:g}")
        elif isinstance(op, SetPositionWeightOp):
            parts.append(f"{op.ticker} → {op.weight_pct:g}%")
        elif isinstance(op, ExcludeTickerOp):
            parts.append(f"Exclude {op.ticker}")
        elif isinstance(op, ExcludeSectorOp):
            parts.append(f"Exclude {op.sector}")
        elif isinstance(op, OverridePolicyOp):
            parts.append(f"{op.field}={op.value}")
        elif isinstance(op, TargetRiskDeltaOp):
            parts.append(f"risk {op.vol_delta_pct:+g}%")
    return " + ".join(parts) or "scenario"


def build_scenario(
    patch: ScenarioPatch,
    base_snapshot: PortfolioSnapshot,
    base_policy: InvestmentPolicy,
    *,
    prices: Optional[Mapping[str, float]] = None,
    parent_scenario_id: Optional[int] = None,
    base_snapshot_id: Optional[int] = None,
    input_set: Optional[PinnedInputSet] = None,
) -> Scenario:
    """Assemble a Scenario node from a patch applied to a base state."""
    derived_snap, derived_pol = apply_patch(base_snapshot, base_policy, patch, prices=prices)
    return Scenario(
        parent_scenario_id=parent_scenario_id,
        base_snapshot_id=base_snapshot_id,
        patch=patch,
        derived_snapshot=derived_snap,
        derived_policy=derived_pol,
        input_set=input_set,
        status=ScenarioStatus.ACTIVE,
        label=patch.label or _auto_label(patch),
    )


def promote_to_baseline(scenario: Scenario, *, version: int) -> PortfolioSnapshot:
    """Adopt a scenario's derived snapshot as a new confirmed baseline snapshot."""
    return scenario.derived_snapshot.model_copy(update={
        "snapshot_id": None, "version": version, "confirmed_by_user": True,
        "promoted_from_scenario": scenario.scenario_id,
    })


def _cash_pct(weights: Mapping[str, float]) -> float:
    return 100.0 - sum(weights.values())


def diff_proposals(
    base: OptimizationProposal, scenario: OptimizationProposal, *,
    reference: str = "baseline", scenario_label: str = "",
) -> ScenarioCompareData:
    """Metric deltas (scenario − base) for the scenario_compare block. None-safe."""
    def d(a: Optional[float], b: Optional[float]) -> Optional[float]:
        return None if a is None or b is None else b - a

    deltas: dict[str, float] = {}
    for key, val in {
        "vol": d(base.expected_vol_after, scenario.expected_vol_after),
        "hhi": d(base.hhi_after, scenario.hhi_after),
        "expected_return_view": d(base.expected_return_view_annual, scenario.expected_return_view_annual),
        "cash_pct": _cash_pct(scenario.target_weights) - _cash_pct(base.target_weights),
    }.items():
        if val is not None:
            deltas[key] = float(val)
    ref = reference if reference in ("active", "baseline") else "baseline"
    return ScenarioCompareData(reference=ref, scenario_label=scenario_label, metric_deltas=deltas)


__all__ = [
    "apply_patch", "extract_directives", "build_scenario",
    "promote_to_baseline", "diff_proposals",
]
