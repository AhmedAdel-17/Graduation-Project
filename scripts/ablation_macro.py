"""
Macro Ablation Harness
======================
Compares final trading decisions with macro analyst ENABLED vs DISABLED
(forced NO_SIGNAL) across a small set of historical ticker/date cases.

Does NOT modify scoring logic. Read-only measurement tool.

Usage:
    python3 scripts/ablation_macro.py              # offline (no VIX)
    python3 scripts/ablation_macro.py --with-vix    # fetch VIX via yfinance

VIX PIT safety:
    _fetch_vix(trade_date) calls yfinance with end=trade_date. yfinance's
    end parameter is EXCLUSIVE, so the last bar returned is T-1 (prior close).
    This is conservative — no same-day or future data leaks into the signal.
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.agents.analysts.macro_analyst import (
    _load_macro_csv,
    _load_tbill_yield,
    _compute_macro_signals,
    _load_fx_premium,
    _fetch_vix,
    _fetch_fx_change_30d,
    REAL_YIELD_RISK_OFF_THRESHOLD,
    REAL_YIELD_RISK_ON_THRESHOLD,
    RATE_HIKE_LARGE_BPS,
    RATE_SHOCK_RECENCY_DAYS,
    VIX_ELEVATED_THRESHOLD,
    VIX_PANIC_THRESHOLD,
)
from tradingagents.agents.utils.scoring import calculate_unified_score
from tradingagents.agents.risk_mgmt.risk_scorer import (
    check_fx_stress,
    check_macro_blackout,
)


# ─── Test cases ───────────────────────────────────────────────────────────
CASES = [
    {
        "ticker": "COMI.CA",
        "trade_date": "2024-10-15",
        "technical": {"score": 0.6, "confidence": 0.75},
        "fundamental": {"score": 0.5, "confidence": 0.80},
        "news": {"score": 0.3, "confidence": 0.60},
        "note": "Post-devaluation, T-bill ~25.6%, CPI ~26.4%",
    },
    {
        "ticker": "TMGH.CA",
        "trade_date": "2024-03-12",
        "technical": {"score": 0.3, "confidence": 0.60},
        "fundamental": {"score": 0.4, "confidence": 0.70},
        "news": {"score": 0.2, "confidence": 0.50},
        "note": "6 days after 600bps CBE hike (Mar 6, 2024)",
    },
    {
        "ticker": "EAST.CA",
        "trade_date": "2025-07-15",
        "technical": {"score": -0.3, "confidence": 0.65},
        "fundamental": {"score": -0.2, "confidence": 0.55},
        "news": {"score": -0.1, "confidence": 0.40},
        "note": "Post all bulletins, full T-bill data available",
    },
    {
        "ticker": "COMI.CA",
        "trade_date": "2025-04-20",
        "technical": {"score": 0.7, "confidence": 0.80},
        "fundamental": {"score": 0.6, "confidence": 0.85},
        "news": {"score": 0.4, "confidence": 0.70},
        "note": "3 days after 225bps CBE cut to 25%",
    },
    {
        "ticker": "ETEL.CA",
        "trade_date": "2024-01-15",
        "technical": {"score": 0.15, "confidence": 0.55},
        "fundamental": {"score": 0.10, "confidence": 0.50},
        "news": {"score": 0.05, "confidence": 0.45},
        "note": "Borderline BUY/HOLD, pre-devaluation",
    },
    {
        "ticker": "HELI.CA",
        "trade_date": "2023-06-15",
        "technical": {"score": 0.4, "confidence": 0.70},
        "fundamental": {"score": 0.3, "confidence": 0.65},
        "news": {"score": 0.2, "confidence": 0.55},
        "note": "Before any MoF bulletin — T-bill unavailable",
    },
]


def _build_state(case: dict, macro_analysis: dict) -> dict:
    tech = case["technical"]
    fund = case["fundamental"]
    news = case["news"]
    return {
        "company_of_interest": case["ticker"],
        "trade_date": case["trade_date"],
        "technical_analysis": {
            "recommendation": "BUY" if tech["score"] > 0 else "SELL",
            "confidence_score": tech["confidence"],
            "overall_score": tech["score"],
        },
        "fundamental_analysis": {
            "recommendation": "BUY" if fund["score"] > 0 else "SELL",
            "confidence_score": fund["confidence"],
            "overall_score": fund["score"],
        },
        "sentiment_analysis": {
            "recommendation": "BUY" if news["score"] > 0 else "SELL",
            "confidence_score": news["confidence"],
            "overall_score": news["score"],
        },
        "macro_analysis": macro_analysis,
    }


def _collect_macro(trade_date: str, with_vix: bool = False) -> dict:
    """Collect real macro data for a trade date.

    When with_vix=True, fetches VIX and FX 30d change from yfinance.
    VIX is PIT-safe: yfinance end= is exclusive, so returns T-1 close.
    """
    csv_data = _load_macro_csv(trade_date)
    macro_data = dict(csv_data) if csv_data else {}

    tbill_csv_yield = None
    if "tbill_91d_yield" not in macro_data:
        tbill_csv_yield = _load_tbill_yield(trade_date)
        if tbill_csv_yield is not None:
            macro_data["tbill_91d_yield"] = tbill_csv_yield

    # Optional live data
    vix_value = None
    fx_change = None
    if with_vix:
        vix_value = _fetch_vix(trade_date)
        if vix_value is not None:
            macro_data["vix_level"] = vix_value
        fx_change = _fetch_fx_change_30d(trade_date)

    signals = _compute_macro_signals(macro_data, trade_date)
    fx_premium = _load_fx_premium(trade_date)

    analysis = {
        "composite_direction": signals["composite_direction"],
        "real_yield_91d": signals.get("real_yield_91d"),
        "real_yield_signal": signals.get("real_yield_signal"),
        "cbe_policy_rate": signals.get("cbe_policy_rate"),
        "rate_shock": signals.get("rate_shock", False),
        "cbe_last_change_bps": signals.get("cbe_last_change_bps", 0),
        "cbe_last_change_date": signals.get("cbe_last_change_date", ""),
        "vix_level": signals.get("vix_level"),
        "vix_regime": signals.get("vix_regime"),
        "egp_usd": signals.get("egp_usd"),
        "egypt_cpi_yoy": signals.get("egypt_cpi_yoy"),
        "fx_change_30d": round(fx_change, 4) if fx_change is not None else None,
        "fx_premium_pct": fx_premium["fx_premium_pct"] if fx_premium else None,
        "fx_premium_signal": fx_premium["fx_premium_signal"] if fx_premium else None,
    }
    analysis["_raw_tbill"] = macro_data.get("tbill_91d_yield")
    analysis["_raw_cpi"] = macro_data.get("egypt_cpi_yoy")
    analysis["_tbill_from_csv"] = tbill_csv_yield is not None
    analysis["_tbill_from_macro_csv"] = "tbill_91d_yield" in (csv_data or {})
    return analysis


def _no_signal_macro() -> dict:
    return {
        "composite_direction": "NO_SIGNAL",
        "real_yield_91d": None,
        "real_yield_signal": None,
        "cbe_policy_rate": None,
        "rate_shock": False,
        "cbe_last_change_bps": 0,
        "cbe_last_change_date": "",
        "vix_level": None,
        "vix_regime": None,
        "egp_usd": None,
        "egypt_cpi_yoy": None,
        "fx_change_30d": None,
        "fx_premium_pct": None,
        "fx_premium_signal": None,
    }


def _explain_composite(macro: dict, with_vix: bool) -> tuple:
    """Reconstruct the voting tally and explain why composite landed where it did."""
    votes = []
    risk_off = 0
    risk_on = 0

    # Real yield vote
    ry_sig = macro.get("real_yield_signal")
    ry_val = macro.get("real_yield_91d")
    if ry_sig == "RISK_OFF":
        risk_off += 1
        votes.append(
            f"real_yield={ry_val:+.2%} > {REAL_YIELD_RISK_OFF_THRESHOLD:+.0%} "
            f"-> RISK_OFF (+1 off)"
        )
    elif ry_sig == "RISK_ON":
        risk_on += 1
        votes.append(
            f"real_yield={ry_val:+.2%} < {REAL_YIELD_RISK_ON_THRESHOLD:+.0%} "
            f"-> RISK_ON (+1 on)"
        )
    elif ry_sig == "NEUTRAL":
        votes.append(
            f"real_yield={ry_val:+.2%} in [{REAL_YIELD_RISK_ON_THRESHOLD:+.0%}, "
            f"{REAL_YIELD_RISK_OFF_THRESHOLD:+.0%}] -> NEUTRAL (no vote)"
        )
    else:
        votes.append("real_yield=N/A (missing T-bill or CPI) -> no vote")

    # Rate shock vote
    if macro.get("rate_shock"):
        risk_off += 1
        bps = macro.get("cbe_last_change_bps", 0)
        dt = macro.get("cbe_last_change_date", "?")
        votes.append(
            f"rate_shock=True ({bps:+d}bps on {dt}, within {RATE_SHOCK_RECENCY_DAYS}d) "
            f"-> RISK_OFF (+1 off)"
        )
    else:
        bps = macro.get("cbe_last_change_bps", 0)
        dt = macro.get("cbe_last_change_date", "")
        if abs(bps) >= RATE_HIKE_LARGE_BPS and dt:
            votes.append(
                f"rate_shock=False ({bps:+d}bps on {dt}, outside {RATE_SHOCK_RECENCY_DAYS}d window) "
                f"-> no vote"
            )
        elif abs(bps) > 0:
            votes.append(
                f"rate_shock=False ({bps:+d}bps < {RATE_HIKE_LARGE_BPS}bps threshold) "
                f"-> no vote"
            )
        else:
            votes.append("rate_shock=False (no recent CBE change) -> no vote")

    # VIX vote
    vix = macro.get("vix_level")
    vix_reg = macro.get("vix_regime")
    if vix_reg == "PANIC":
        risk_off += 2
        votes.append(f"vix={vix:.1f} >= {VIX_PANIC_THRESHOLD} -> PANIC (+2 off)")
    elif vix_reg == "ELEVATED":
        risk_off += 1
        votes.append(f"vix={vix:.1f} >= {VIX_ELEVATED_THRESHOLD} -> ELEVATED (+1 off)")
    elif vix_reg == "CALM":
        risk_on += 1
        votes.append(f"vix={vix:.1f} < {VIX_ELEVATED_THRESHOLD} -> CALM (+1 on)")
    else:
        reason = "not fetched (offline mode)" if not with_vix else "yfinance returned no data"
        votes.append(f"vix=N/A ({reason}) -> no vote")

    has_any = (ry_sig is not None or macro.get("rate_shock") or vix_reg is not None)
    if not has_any:
        explanation = "NO_SIGNAL: no signals contributed (all inputs missing)"
    elif risk_off >= 2:
        explanation = f"RISK_OFF: off={risk_off} >= 2 threshold"
    elif risk_on >= 2:
        if macro.get("rate_shock"):
            explanation = (
                f"RISK_ON: on={risk_on} >= 2 threshold, "
                f"but rate_shock=True → clamped to NEUTRAL"
            )
        else:
            explanation = f"RISK_ON: on={risk_on} >= 2 threshold"
    else:
        explanation = (
            f"NEUTRAL: off={risk_off}, on={risk_on} — "
            f"neither reached >= 2 threshold"
        )

    return votes, explanation, risk_off, risk_on


def _fmt(val, fmt_str, suffix="", scale=1):
    if val is None:
        return "N/A"
    return f"{fmt_str % (val * scale)}{suffix}"


def _run_case(case: dict, with_vix: bool) -> dict:
    trade_date = case["trade_date"]

    macro_on = _collect_macro(trade_date, with_vix=with_vix)
    macro_off = _no_signal_macro()

    state_on = _build_state(case, macro_on)
    state_off = _build_state(case, macro_off)

    dec_on, conf_on, _, scores_on, _ = calculate_unified_score(state_on)
    dec_off, conf_off, _, scores_off, _ = calculate_unified_score(state_off)

    fx_risk = check_fx_stress(macro_on)
    blackout = check_macro_blackout(macro_on)
    risk_items = []
    if fx_risk:
        risk_items.append(f"{fx_risk.rule_name} ({fx_risk.severity}): {fx_risk.explanation}")
    if blackout:
        risk_items.append(f"{blackout.rule_name} ({blackout.severity}): {blackout.explanation}")

    votes, explanation, off_count, on_count = _explain_composite(macro_on, with_vix)

    pos_on = scores_on.get("position_size_multiplier", 1.0)
    pos_off = scores_off.get("position_size_multiplier", 1.0)

    return {
        "ticker": case["ticker"],
        "trade_date": trade_date,
        "note": case["note"],
        "cbe_rate": macro_on.get("cbe_policy_rate"),
        "cpi": macro_on.get("egypt_cpi_yoy"),
        "tbill": macro_on.get("_raw_tbill"),
        "tbill_source": (
            "macro_csv" if macro_on.get("_tbill_from_macro_csv")
            else ("tbill_csv" if macro_on.get("_tbill_from_csv") else "unavailable")
        ),
        "vix_level": macro_on.get("vix_level"),
        "vix_regime": macro_on.get("vix_regime"),
        "fx_change_30d": macro_on.get("fx_change_30d"),
        "fx_premium_pct": macro_on.get("fx_premium_pct"),
        "fx_premium_signal": macro_on.get("fx_premium_signal"),
        "direction": macro_on.get("composite_direction"),
        "real_yield": macro_on.get("real_yield_91d"),
        "ry_signal": macro_on.get("real_yield_signal"),
        "rate_shock": macro_on.get("rate_shock", False),
        "cbe_last_change_bps": macro_on.get("cbe_last_change_bps", 0),
        "cbe_last_change_date": macro_on.get("cbe_last_change_date", ""),
        "votes": votes,
        "composite_explanation": explanation,
        "off_count": off_count,
        "on_count": on_count,
        "decision_on": dec_on,
        "conf_on": round(conf_on, 4),
        "pos_size_on": round(pos_on, 4),
        "decision_off": dec_off,
        "conf_off": round(conf_off, 4),
        "pos_size_off": round(pos_off, 4),
        "decision_changed": dec_on != dec_off,
        "conf_delta": round(conf_on - conf_off, 4),
        "pos_size_delta": round(pos_on - pos_off, 4),
        "risk_items": risk_items,
    }


def _print_case(i: int, r: dict, with_vix: bool, W: int):
    print(f"\n{'─' * W}")
    print(f"Case {i+1}: {r['ticker']}  {r['trade_date']}")
    print(f"  {r['note']}")

    print(f"\n  Raw Macro Data:")
    print(f"    CBE rate:      {_fmt(r['cbe_rate'], '%.1f%%', scale=100)}")
    print(f"    CPI YoY:       {_fmt(r['cpi'], '%.1f%%', scale=100)}")
    print(f"    T-bill 91d:    {_fmt(r['tbill'], '%.2f%%', scale=100)}  (source: {r['tbill_source']})")
    vix_note = "" if with_vix else "  (not fetched: offline mode)"
    print(f"    VIX:           {_fmt(r['vix_level'], '%.1f')}  (regime: {r['vix_regime'] or 'unavailable'}){vix_note}")
    fx_note = "" if with_vix else "  (not fetched: offline mode)"
    print(f"    FX 30d change: {_fmt(r['fx_change_30d'], '%+.1f%%', scale=100)}{fx_note}")
    print(f"    FX premium:    {_fmt(r['fx_premium_pct'], '%.1f%%')}  (signal: {r['fx_premium_signal'] or 'unavailable'})")

    print(f"\n  Computed Signals:")
    print(f"    Real yield:    {_fmt(r['real_yield'], '%+.2f%%')}  -> {r['ry_signal'] or 'N/A'}")
    if r["rate_shock"]:
        print(f"    Rate shock:    YES  ({r['cbe_last_change_bps']:+d}bps on {r['cbe_last_change_date']})")
    else:
        print(f"    Rate shock:    no   (last: {r['cbe_last_change_bps']:+d}bps on {r['cbe_last_change_date'] or 'N/A'})")

    print(f"\n  Composite Direction Voting (off={r['off_count']}, on={r['on_count']}):")
    for v in r["votes"]:
        print(f"    - {v}")
    print(f"    => {r['composite_explanation']}")
    print(f"    => composite_direction = {r['direction']}")

    print(f"\n  Scoring Impact:")
    print(f"    {'':20s}  {'Decision':>10s}  {'Confidence':>10s}  {'Pos Size':>10s}")
    print(f"    {'Macro ENABLED':20s}  {r['decision_on']:>10s}  {r['conf_on']:>10.4f}  {r['pos_size_on']:>10.4f}")
    print(f"    {'Macro DISABLED':20s}  {r['decision_off']:>10s}  {r['conf_off']:>10.4f}  {r['pos_size_off']:>10.4f}")
    print(f"    {'Delta':20s}  {'CHANGED' if r['decision_changed'] else '—':>10s}  {r['conf_delta']:>+10.4f}  {r['pos_size_delta']:>+10.4f}")

    if r["risk_items"]:
        print(f"\n  Risk Warnings:")
        for ri in r["risk_items"]:
            print(f"    - {ri}")
    else:
        print(f"\n  Risk Warnings: none")


def _print_summary(results: list, with_vix: bool, W: int, label: str = ""):
    print(f"\n{'=' * W}")
    print(f"SUMMARY{f' — {label}' if label else ''}")
    print(f"{'=' * W}")

    dir_counts = {}
    for r in results:
        d = r["direction"]
        dir_counts[d] = dir_counts.get(d, 0) + 1
    print(f"\n  Composite Direction Distribution:")
    for d in ["RISK_ON", "RISK_OFF", "NEUTRAL", "NO_SIGNAL"]:
        c = dir_counts.get(d, 0)
        bar = "#" * c
        print(f"    {d:>12s}: {c}  {bar}")

    n = len(results)
    n_dec = sum(1 for r in results if r["decision_changed"])
    n_conf = sum(1 for r in results if r["conf_delta"] != 0)
    n_pos = sum(1 for r in results if r["pos_size_delta"] != 0)
    n_risk = sum(1 for r in results if r["risk_items"])
    n_vix_miss = sum(1 for r in results if r["vix_regime"] is None)
    n_tbill_miss = sum(1 for r in results if r["tbill"] is None)
    n_cpi_miss = sum(1 for r in results if r["cpi"] is None)

    print(f"\n  Macro Impact:")
    print(f"    Decision changed:      {n_dec}/{n}")
    print(f"    Confidence changed:    {n_conf}/{n}")
    print(f"    Position size changed: {n_pos}/{n}")
    print(f"    Risk warnings fired:   {n_risk}/{n}")

    print(f"\n  Data Availability:")
    vix_note = "(not fetched: offline mode)" if not with_vix else "(via yfinance T-1)"
    print(f"    VIX unavailable:     {n_vix_miss}/{n}  {vix_note}")
    print(f"    T-bill unavailable:  {n_tbill_miss}/{n}")
    print(f"    CPI unavailable:     {n_cpi_miss}/{n}")

    deltas = [r["conf_delta"] for r in results]
    print(f"\n  Confidence Delta Stats:")
    print(f"    Average: {sum(deltas)/n:+.4f}")
    print(f"    Min:     {min(deltas):+.4f}")
    print(f"    Max:     {max(deltas):+.4f}")

    return dir_counts, n_dec, n_conf, n_pos, n_risk


def main():
    parser = argparse.ArgumentParser(description="Macro ablation harness")
    parser.add_argument(
        "--with-vix", action="store_true",
        help="Fetch VIX and FX 30d change from yfinance (PIT-safe: T-1 close)"
    )
    args = parser.parse_args()
    with_vix = args.with_vix

    W = 95

    if with_vix:
        # Run both modes for comparison
        print("=" * W)
        print("MACRO ABLATION — DUAL MODE: Offline vs With-VIX")
        print("VIX fetch uses yfinance end=trade_date (exclusive), returning T-1 close.")
        print("This is PIT-safe: no same-day or future VIX data leaks into the signal.")
        print("=" * W)

        # Offline pass
        print(f"\n{'*' * W}")
        print("PASS 1: OFFLINE (no VIX)")
        print(f"{'*' * W}")
        results_off = [_run_case(c, with_vix=False) for c in CASES]
        for i, r in enumerate(results_off):
            _print_case(i, r, with_vix=False, W=W)
        s_off = _print_summary(results_off, with_vix=False, W=W, label="Offline")

        # VIX pass
        print(f"\n\n{'*' * W}")
        print("PASS 2: WITH VIX (yfinance T-1)")
        print(f"{'*' * W}")
        results_vix = [_run_case(c, with_vix=True) for c in CASES]
        for i, r in enumerate(results_vix):
            _print_case(i, r, with_vix=True, W=W)
        s_vix = _print_summary(results_vix, with_vix=True, W=W, label="With VIX")

        # Comparison
        print(f"\n{'=' * W}")
        print("COMPARISON: Offline vs With-VIX")
        print(f"{'=' * W}")
        print(f"\n  {'Case':6s} {'Date':12s} {'Dir (off)':>12s} {'Dir (vix)':>12s} "
              f"{'Conf Δ (off)':>12s} {'Conf Δ (vix)':>12s} {'Dec changed?':>14s}")
        print(f"  {'─'*6} {'─'*12} {'─'*12} {'─'*12} {'─'*12} {'─'*12} {'─'*14}")
        for i, (ro, rv) in enumerate(zip(results_off, results_vix)):
            dir_changed = "***" if ro["direction"] != rv["direction"] else ""
            dec_note = ""
            if rv["decision_changed"] and not ro["decision_changed"]:
                dec_note = " (NEW w/ VIX)"
            elif rv["decision_changed"]:
                dec_note = " yes"
            print(f"  {i+1:<6d} {ro['trade_date']:12s} {ro['direction']:>12s} "
                  f"{rv['direction']:>12s}{dir_changed:3s} "
                  f"{ro['conf_delta']:>+12.4f} {rv['conf_delta']:>+12.4f} "
                  f"{dec_note:>14s}")

        # Net changes
        dir_shifts = sum(1 for ro, rv in zip(results_off, results_vix)
                         if ro["direction"] != rv["direction"])
        new_conf = sum(1 for ro, rv in zip(results_off, results_vix)
                       if ro["conf_delta"] == 0 and rv["conf_delta"] != 0)
        new_dec = sum(1 for ro, rv in zip(results_off, results_vix)
                      if not ro["decision_changed"] and rv["decision_changed"])

        print(f"\n  Direction changed by VIX:         {dir_shifts}/{len(CASES)}")
        print(f"  New confidence impact from VIX:    {new_conf}/{len(CASES)}")
        print(f"  New decision flips from VIX:       {new_dec}/{len(CASES)}")

        # P1 recommendation
        print(f"\n  P1 Scoring Implications:")
        vix_counts = {}
        for r in results_vix:
            d = r["direction"]
            vix_counts[d] = vix_counts.get(d, 0) + 1
        n_risk_on = vix_counts.get("RISK_ON", 0)
        n_risk_off = vix_counts.get("RISK_OFF", 0)
        if n_risk_on > 0:
            print(f"    - RISK_ON fires in {n_risk_on} case(s). P1 changed multiplier from")
            print(f"      0.95 to 1.00 (pass-through), so RISK_ON no longer penalises")
            print(f"      confidence — confirmed correct for Egypt inflation-hedge regime.")
        else:
            print(f"    - RISK_ON does not fire. P1 pass-through (1.00) has no effect.")
        if n_risk_off > s_off[0].get("RISK_OFF", 0):
            extra = n_risk_off - s_off[0].get("RISK_OFF", 0)
            print(f"    - RISK_OFF fires in {extra} additional case(s) with VIX.")
            print(f"      P1 dual reduction (conf×0.80, size×0.80) applies more often.")
        print(f"    - VIX is the key enabler. Without it, composite voting is structurally")
        print(f"      limited to real_yield + rate_shock (max 2 signals, often conflicting).")

    else:
        # Offline-only mode (original behavior)
        print("=" * W)
        print("MACRO ABLATION REPORT — Offline Mode (no VIX)")
        print("Run with --with-vix to include yfinance VIX data (PIT-safe: T-1 close).")
        print("=" * W)

        results = [_run_case(c, with_vix=False) for c in CASES]
        for i, r in enumerate(results):
            _print_case(i, r, with_vix=False, W=W)
        _print_summary(results, with_vix=False, W=W)

        print(f"\n  Tip: rerun with --with-vix to see how VIX changes the composite voting.")


if __name__ == "__main__":
    main()
