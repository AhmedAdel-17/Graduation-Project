"""Offline end-to-end demo of the Portfolio Assistant deterministic core (P1).

Runs the full numeric pipeline with NO LLM and NO network - fixture prices and a
synthetic/absent covariance - and prints:

  1. portfolio analytics (composition, P&L, concentration, exposures),
  2. the compiled investment policy + any conflict flags,
  3. an optimization proposal (rebalancing actions, before/after, metric deltas),
  4. a what-if diff ("sell all of one position").

This is the roadmap P1 acceptance harness: deterministic, offline, < 5 s.

Usage
-----
    python scripts/portfolio_demo.py                       # built-in example
    python scripts/portfolio_demo.py --portfolio mine.json # your portfolio
    python scripts/portfolio_demo.py --whatif-close FWRY.CA

Portfolio JSON (matches PortfolioSnapshot + optional prices/total)::

    {
      "cash_egp": 50000,
      "total_invested_egp": 250000,          # anchor when holdings give weights only
      "prices": {"ETEL.CA": 38, "FWRY.CA": 12, "TMGH.CA": 65},
      "holdings": [
        {"ticker": "ETEL.CA", "weight_pct": 20, "avg_cost": 35},
        {"ticker": "FWRY.CA", "weight_pct": 30, "avg_cost": 10},
        {"ticker": "TMGH.CA", "weight_pct": 50, "avg_cost": 60}
      ]
    }
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingagents.portfolio import ENGINE_VERSION, SCHEMA_VERSION
from tradingagents.portfolio.analytics import compute_analytics
from tradingagents.portfolio.optimizer import optimize
from tradingagents.portfolio.policy_compiler import compile_policy
from tradingagents.portfolio import scenarios as sc
from tradingagents.portfolio.schemas import (
    ClosePositionOp,
    Horizon,
    InvestmentPolicy,
    Objective,
    PortfolioHolding,
    PortfolioSnapshot,
    RiskTolerance,
    ScenarioPatch,
    SignalLabel,
    SignalView,
)

# Built-in example: the design doc's worked portfolio, with fixture prices + signals.
_EXAMPLE = {
    "cash_egp": 50000,
    "total_invested_egp": 250000,
    "prices": {"ETEL.CA": 38.0, "FWRY.CA": 12.0, "TMGH.CA": 65.0},
    "holdings": [
        {"ticker": "ETEL.CA", "weight_pct": 20, "avg_cost": 35, "name_raw": "Telecom Egypt"},
        {"ticker": "FWRY.CA", "weight_pct": 30, "avg_cost": 10, "name_raw": "Fawry"},
        {"ticker": "TMGH.CA", "weight_pct": 50, "avg_cost": 60, "name_raw": "Talaat Moustafa"},
    ],
}
_EXAMPLE_SIGNALS = {
    "ETEL.CA": ("BUY", 0.62), "FWRY.CA": ("BUY", 0.74), "TMGH.CA": ("SELL", 0.68),
}

_BAR = "=" * 64
_RULE = "-" * 64


def _load(path: str | None) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else _EXAMPLE


def _snapshot(raw: dict) -> PortfolioSnapshot:
    return PortfolioSnapshot(cash_egp=float(raw.get("cash_egp", 0.0)),
                             holdings=[PortfolioHolding(**h) for h in raw.get("holdings", [])])


def _signals(raw: dict) -> dict[str, SignalView]:
    src = raw.get("signals") or _EXAMPLE_SIGNALS
    out = {}
    for t, v in src.items():
        label, conf = (v if isinstance(v, (list, tuple)) else (v, 0.6))
        out[t] = SignalView(ticker=t, label=SignalLabel(label), confidence=float(conf), session_id=f"demo-{t}")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Portfolio Assistant offline demo (P1).")
    p.add_argument("--portfolio", help="Portfolio JSON (defaults to a built-in example).")
    p.add_argument("--whatif-close", metavar="TICKER", help="What-if: sell all of this position.")
    args = p.parse_args(argv)

    raw = _load(args.portfolio)
    snap = _snapshot(raw)
    prices = {k: float(v) for k, v in raw.get("prices", _EXAMPLE["prices"]).items()}
    total = raw.get("total_invested_egp")
    signals = _signals(raw)

    # 1. analytics
    analytics = compute_analytics(snap, prices, total_invested_egp=total, signals=signals)
    print(_BAR)
    print(f"Portfolio Assistant - offline core demo (engine {ENGINE_VERSION}, schema {SCHEMA_VERSION})")
    print(_BAR)
    print(f"total {analytics.total_value_egp:,.0f} EGP | invested {analytics.invested_egp:,.0f} "
          f"| cash {analytics.cash_egp:,.0f} ({analytics.cash_drag_pct:.0f}%) | HHI {analytics.hhi:.2f}")
    print(_RULE)
    for h in analytics.holdings:
        pnl = f"{h.unrealized_pnl_egp:+,.0f} ({h.unrealized_pnl_pct:+.0f}%)" if h.unrealized_pnl_egp is not None else ""
        sig = f"- {h.signal.label.value} {h.signal.confidence:.2f}" if h.signal else ""
        print(f"  {h.ticker:<9} {h.weight_pct:5.1f}%  {h.market_value_egp:>10,.0f} EGP  "
              f"{h.sector:<18} {pnl:>16} {sig}")
    print(f"  sectors: " + ", ".join(f"{k} {v:.0f}%" for k, v in sorted(analytics.sector_exposure.items())))

    # 2. policy + compile
    policy = InvestmentPolicy(objective=Objective.BALANCED, risk_tolerance=RiskTolerance.MEDIUM,
                              horizon=Horizon.Y1_3)
    params, flags = compile_policy(policy, analytics, benchmark_vol=0.30)
    print(_RULE)
    print(f"policy: {policy.objective.value}/{policy.risk_tolerance.value}/{policy.horizon.value} "
          f"-> lambda={params.risk_aversion} cap={params.max_position_pct:.0f}% "
          f"sector_cap={params.max_sector_pct:.0f}% cash_floor={params.min_cash_pct:.0f}% "
          f"shrink={params.view_shrinkage}")
    for f in flags:
        print(f"  [flag:{f.severity.value}] {f.code}: {f.detail}")

    # 3. optimization proposal
    base = optimize(snap, prices, params, signals=signals, total_invested_egp=total, snapshot_id=0)
    print(_RULE)
    print(f"PROPOSAL ({base.solver_status.value}) - "
          f"vol {base.expected_vol_before:.2f}->{base.expected_vol_after:.2f} | "
          f"HHI {base.hhi_before:.2f}->{base.hhi_after:.2f} | "
          f"view-return {base.expected_return_view_annual:+.1%}/yr(view) | "
          f"cost ~{base.est_total_cost_egp:,.0f} EGP")
    if not base.actions:
        print("  (no trades - portfolio already aligned)")
    for a in base.actions:
        print(f"  {a.side.value:<4} {a.shares:>6,} {a.ticker:<9} @ {a.price_used:>7,.2f}  "
              f"~{a.est_value_egp:>10,.0f} EGP   {a.target_weight_pct:5.1f}% target")
    print("  (view) expected return is a MODEL VIEW, not a forecast.")

    # 4. what-if diff
    close_ticker = args.whatif_close or "FWRY.CA"
    if close_ticker in {h.ticker for h in snap.holdings}:
        patch = ScenarioPatch(ops=[ClosePositionOp(ticker=close_ticker)], reference="baseline")
        scen = sc.build_scenario(patch, snap, policy, prices=prices)
        scen_params, _ = compile_policy(scen.derived_policy or policy, analytics, benchmark_vol=0.30)
        scen_prop = optimize(scen.derived_snapshot, prices, scen_params,
                             signals={k: v for k, v in signals.items() if k != close_ticker},
                             total_invested_egp=total, scenario_id=1)
        cmp = sc.diff_proposals(base, scen_prop, reference="baseline", scenario_label=scen.label)
        print(_RULE)
        print(f"WHAT-IF: {scen.label}")
        print("  deltas vs baseline: " + ", ".join(
            f"{k} {v:+.3f}" for k, v in sorted(cmp.metric_deltas.items())))
    print(_BAR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
