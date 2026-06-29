"""
Drag Decomposition & Benchmark Skew Analysis for OOS Portfolio Simulation.

TASK A: Reproducible drag decomposition for 30% cap OOS portfolio.
TASK B: Equal-weight benchmark skew analysis (outlier sensitivity).

Uses portfolio_sim.py functions — does NOT modify them.
"""

import json
import os
import sys

# Add project root to path so we can import portfolio_sim
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from scripts.portfolio_sim import (
    COST_PER_SIDE,
    compute_equal_weight_benchmark,
    discover_reports,
    fetch_daily_prices_for_tickers,
    load_audit_logs,
    load_egx30_benchmark_points,
    simulate_portfolio,
)

# ── Constants ─────────────────────────────────────────────────────────────────
OOS_START = "2024-07-15"
NAV_END_DATE = "2024-12-29"
INITIAL_CAPITAL = 1_000_000.0
MAX_POSITION_PCT = 0.30  # 30% cap

OOS_TICKERS = [
    "COMI.CA", "ETEL.CA", "TMGH.CA", "SWDY.CA", "FWRY.CA",
    "ADIB.CA", "EAST.CA", "PHDC.CA", "EFIH.CA", "HRHO.CA",
]


def main():
    report_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "backtest_results",
    )

    # ══════════════════════════════════════════════════════════════════════════
    # DISCOVER REPORTS
    # ══════════════════════════════════════════════════════════════════════════
    print("=" * 90)
    print("REPORT DISCOVERY (OOS window: start = 2024-07-15)")
    print("=" * 90)

    reports = discover_reports(report_dir, OOS_TICKERS, start=OOS_START)

    if not reports:
        print("ERROR: No OOS reports found.")
        sys.exit(1)

    for t in sorted(reports):
        print(f"  {t}: {os.path.basename(reports[t])}")
    print(f"\n  Found {len(reports)}/10 tickers")
    print()

    # ══════════════════════════════════════════════════════════════════════════
    # LOAD DATA
    # ══════════════════════════════════════════════════════════════════════════
    audit_data = load_audit_logs(reports)
    tickers = sorted(audit_data.keys())

    # Date range
    all_dates = [d for t in audit_data.values() for d in t.keys()]
    date_start = min(all_dates)
    date_end = max(all_dates)
    price_end = (pd.Timestamp(date_end) + pd.Timedelta(days=7)).strftime("%Y-%m-%d")

    print(f"Fetching daily prices for {len(tickers)} tickers ({date_start} -> {price_end})...")
    daily_prices = fetch_daily_prices_for_tickers(tickers, date_start, price_end)
    print(f"  Got {len(daily_prices)} trading days x {len(daily_prices.columns)} tickers")
    print()

    # EGX30 benchmark
    egx30_points = load_egx30_benchmark_points(reports)
    egx30_points = [p for p in egx30_points if p.get("date", "") <= NAV_END_DATE]
    if egx30_points:
        egx30_start_val = egx30_points[0]["value"]
        egx30_end_val = egx30_points[-1]["value"]
        egx30_return = (egx30_end_val / egx30_start_val - 1) * 100
    else:
        egx30_return = 5.88  # fallback from report
        print("  WARNING: No EGX30 benchmark_history found, using 5.88% from reports")

    # ══════════════════════════════════════════════════════════════════════════
    # TASK A: DRAG DECOMPOSITION (30% cap)
    # ══════════════════════════════════════════════════════════════════════════
    print("=" * 90)
    print("TASK A: DRAG DECOMPOSITION — 30% concentration cap")
    print("=" * 90)
    print()

    result = simulate_portfolio(
        audit_data,
        daily_prices,
        initial_capital=INITIAL_CAPITAL,
        max_position_pct=MAX_POSITION_PCT,
        nav_end_date=NAV_END_DATE,
    )

    # Equal-weight benchmark for the same 10 tickers
    ew_nav = compute_equal_weight_benchmark(
        daily_prices, tickers, INITIAL_CAPITAL, nav_end_date=NAV_END_DATE
    )
    ew_return = (ew_nav[-1]["nav"] / ew_nav[0]["nav"] - 1) * 100

    # ── Part 1: OBSERVED values (directly from simulator output) ─────────
    portfolio_return = result["total_return_pct"]
    total_commissions = result["total_commissions"]
    final_nav = result["final_nav"]
    final_cash = result["final_cash"]
    pending = result["pending_settlement"]
    total_trades = result["total_trades"]
    buy_count = result["buy_trades"]
    sell_count = result["sell_trades"]

    final_cash_pct = (final_cash + pending) / final_nav * 100 if final_nav > 0 else 0
    commissions_pct = total_commissions / INITIAL_CAPITAL * 100

    print("OBSERVED (directly from simulator output):")
    print("-" * 60)
    print(f"  Portfolio return:        {portfolio_return:>+8.2f}%")
    print(f"  Final NAV:               {final_nav:>14,.2f} EGP")
    print(f"  Total commissions:       {total_commissions:>14,.2f} EGP  ({commissions_pct:.4f}%)")
    print(f"  Final cash:              {final_cash:>14,.2f} EGP")
    print(f"  Pending settlement:      {pending:>14,.2f} EGP")
    print(f"  Final cash+pending:      {(final_cash + pending):>14,.2f} EGP  ({final_cash_pct:.1f}% of NAV)")
    print(f"  Total trades:            {total_trades:>6d}")
    print(f"    BUY:                   {buy_count:>6d}")
    print(f"    SELL:                  {sell_count:>6d}")
    print(f"  Daily NAV entries:       {result['daily_nav_count']:>6d}")
    print()

    # ── Part 2: DERIVED values with explicit formulas ────────────────────
    # Gross return = portfolio_return + (total_commissions / initial_capital * 100)
    gross_return = portfolio_return + commissions_pct

    # Average invested fraction across all daily NAV entries
    daily_nav_entries = result["daily_nav"]
    n_days = len(daily_nav_entries)
    invested_fractions = []
    for entry in daily_nav_entries:
        nav_day = entry["nav"]
        invested_day = entry["invested"]
        if nav_day > 0:
            invested_fractions.append(invested_day / nav_day)
        else:
            invested_fractions.append(0.0)

    avg_invested_pct = sum(invested_fractions) / len(invested_fractions) if invested_fractions else 0.0

    # Return on invested capital
    roic = gross_return / avg_invested_pct if avg_invested_pct > 0 else 0.0

    # Signal alpha
    signal_alpha_egx = roic - egx30_return
    signal_alpha_ew = roic - ew_return

    # Cash drag = roic * (1 - avg_invested_pct)
    cash_drag = roic * (1 - avg_invested_pct)

    # Transaction cost drag = commissions / initial_capital * 100
    cost_drag = commissions_pct

    # Total alpha (observed)
    total_alpha_egx = portfolio_return - egx30_return
    total_alpha_ew = portfolio_return - ew_return

    # Decomposition check: total_alpha = signal_alpha - cash_drag - cost_drag
    # i.e. signal_alpha_egx - cash_drag - cost_drag should == total_alpha_egx
    check_egx = signal_alpha_egx - cash_drag - cost_drag
    check_ew = signal_alpha_ew - cash_drag - cost_drag

    print("DERIVED (with explicit formulas):")
    print("-" * 60)
    print(f"  Gross return:            {gross_return:>+8.4f}%")
    print(f"    Formula: portfolio_return + (total_commissions / initial_capital * 100)")
    print(f"    = {portfolio_return:.4f} + ({total_commissions:.2f} / {INITIAL_CAPITAL:.0f} * 100)")
    print(f"    = {portfolio_return:.4f} + {commissions_pct:.4f}")
    print()

    print(f"  Average invested fraction: {avg_invested_pct:.6f}  ({avg_invested_pct * 100:.2f}%)")
    print(f"    Formula: mean(daily_invested / daily_nav) across all {n_days} trading days")
    print()

    print(f"  ROIC (Return on Invested Capital): {roic:>+8.4f}%")
    print(f"    Formula: gross_return / avg_invested_pct")
    print(f"    = {gross_return:.4f} / {avg_invested_pct:.6f}")
    print()

    print(f"  EGX30 return:            {egx30_return:>+8.4f}%")
    print(f"  Equal-weight return:     {ew_return:>+8.4f}%")
    print()

    print(f"  Signal alpha vs EGX30:   {signal_alpha_egx:>+8.4f} pp")
    print(f"    Formula: roic - egx30_return = {roic:.4f} - {egx30_return:.4f}")
    print()

    print(f"  Signal alpha vs EqWt:    {signal_alpha_ew:>+8.4f} pp")
    print(f"    Formula: roic - ew_return = {roic:.4f} - {ew_return:.4f}")
    print()

    print(f"  Cash + T+2 drag:         {cash_drag:>+8.4f} pp")
    print(f"    Formula: roic * (1 - avg_invested_pct)")
    print(f"    = {roic:.4f} * (1 - {avg_invested_pct:.6f})")
    print(f"    = {roic:.4f} * {(1 - avg_invested_pct):.6f}")
    print()

    print(f"  Transaction cost drag:   {cost_drag:>+8.4f} pp")
    print(f"    Formula: total_commissions / initial_capital * 100")
    print(f"    = {total_commissions:.2f} / {INITIAL_CAPITAL:.0f} * 100")
    print()

    # ── Part 3: Decomposition Table ──────────────────────────────────────
    print("=" * 90)
    print("DECOMPOSITION TABLE — vs EGX30")
    print("=" * 90)
    print()
    print(f"  {'Component':<30} {'Value (pp)':>12} {'Source':>10}  Formula")
    print(f"  {'-' * 85}")
    print(f"  {'Portfolio return':<30} {portfolio_return:>+12.4f} {'OBSERVED':>10}  (final_nav / initial - 1) * 100")
    print(f"  {'+ Commissions (add back)':<30} {commissions_pct:>+12.4f} {'OBSERVED':>10}  total_commissions / initial * 100")
    print(f"  {'= Gross return':<30} {gross_return:>+12.4f} {'DERIVED':>10}  portfolio_return + commissions_pct")
    print(f"  {'Avg invested fraction':<30} {avg_invested_pct * 100:>11.2f}% {'DERIVED':>10}  mean(daily_invested/daily_nav)")
    print(f"  {'= ROIC':<30} {roic:>+12.4f} {'DERIVED':>10}  gross_return / avg_invested_frac")
    print(f"  {'EGX30 return':<30} {egx30_return:>+12.4f} {'OBSERVED':>10}  from benchmark_history")
    print(f"  {'Signal alpha vs EGX30':<30} {signal_alpha_egx:>+12.4f} {'DERIVED':>10}  roic - egx30_return")
    print(f"  {'- Cash + T+2 drag':<30} {cash_drag:>+12.4f} {'DERIVED':>10}  roic * (1 - avg_invested_pct)")
    print(f"  {'- Transaction cost drag':<30} {cost_drag:>+12.4f} {'DERIVED':>10}  commissions / initial * 100")
    print(f"  {'= Total alpha vs EGX30':<30} {total_alpha_egx:>+12.4f} {'CHECK':>10}  portfolio_return - egx30_return")
    print(f"  {'  Check (signal-cash-cost)':<30} {check_egx:>+12.4f} {'CHECK':>10}  should match total alpha above")
    print()

    residual_egx = abs(total_alpha_egx - check_egx)
    print(f"  Decomposition residual: {residual_egx:.6f} pp {'(OK - exact)' if residual_egx < 0.001 else '(WARNING - mismatch)'}")
    print()

    print("=" * 90)
    print("DECOMPOSITION TABLE — vs Equal-Weight B&H")
    print("=" * 90)
    print()
    print(f"  {'Component':<30} {'Value (pp)':>12} {'Source':>10}  Formula")
    print(f"  {'-' * 85}")
    print(f"  {'Portfolio return':<30} {portfolio_return:>+12.4f} {'OBSERVED':>10}  (final_nav / initial - 1) * 100")
    print(f"  {'+ Commissions (add back)':<30} {commissions_pct:>+12.4f} {'OBSERVED':>10}  total_commissions / initial * 100")
    print(f"  {'= Gross return':<30} {gross_return:>+12.4f} {'DERIVED':>10}  portfolio_return + commissions_pct")
    print(f"  {'Avg invested fraction':<30} {avg_invested_pct * 100:>11.2f}% {'DERIVED':>10}  mean(daily_invested/daily_nav)")
    print(f"  {'= ROIC':<30} {roic:>+12.4f} {'DERIVED':>10}  gross_return / avg_invested_frac")
    print(f"  {'Equal-weight return':<30} {ew_return:>+12.4f} {'OBSERVED':>10}  from compute_equal_weight_benchmark")
    print(f"  {'Signal alpha vs EqWt':<30} {signal_alpha_ew:>+12.4f} {'DERIVED':>10}  roic - ew_return")
    print(f"  {'- Cash + T+2 drag':<30} {cash_drag:>+12.4f} {'DERIVED':>10}  roic * (1 - avg_invested_pct)")
    print(f"  {'- Transaction cost drag':<30} {cost_drag:>+12.4f} {'DERIVED':>10}  commissions / initial * 100")
    print(f"  {'= Total alpha vs EqWt':<30} {total_alpha_ew:>+12.4f} {'CHECK':>10}  portfolio_return - ew_return")
    print(f"  {'  Check (signal-cash-cost)':<30} {check_ew:>+12.4f} {'CHECK':>10}  should match total alpha above")
    print()

    residual_ew = abs(total_alpha_ew - check_ew)
    print(f"  Decomposition residual: {residual_ew:.6f} pp {'(OK - exact)' if residual_ew < 0.001 else '(WARNING - mismatch)'}")
    print()

    # ── Summary block ────────────────────────────────────────────────────
    print("=" * 90)
    print("DRAG DECOMPOSITION SUMMARY")
    print("=" * 90)
    print()
    print(f"  The portfolio earned {portfolio_return:+.2f}% net return.")
    print(f"  Of the gross return ({gross_return:+.4f}%), only {avg_invested_pct * 100:.2f}% of NAV was invested on average.")
    print(f"  The ROIC (what the invested capital actually earned) was {roic:+.4f}%.")
    print()
    print(f"  vs EGX30 ({egx30_return:+.2f}%):")
    print(f"    Signal alpha:       {signal_alpha_egx:+.4f} pp  (the quality of BUY/SELL timing)")
    print(f"    Cash drag:          {cash_drag:+.4f} pp  (return lost to uninvested capital)")
    print(f"    Cost drag:          {cost_drag:+.4f} pp  (commissions, stamp duty, slippage)")
    print(f"    Net alpha:          {total_alpha_egx:+.4f} pp")
    print()
    print(f"  vs Equal-Weight ({ew_return:+.2f}%):")
    print(f"    Signal alpha:       {signal_alpha_ew:+.4f} pp")
    print(f"    Cash drag:          {cash_drag:+.4f} pp")
    print(f"    Cost drag:          {cost_drag:+.4f} pp")
    print(f"    Net alpha:          {total_alpha_ew:+.4f} pp")
    print()

    # ══════════════════════════════════════════════════════════════════════════
    # TASK B: EQUAL-WEIGHT BENCHMARK SKEW ANALYSIS
    # ══════════════════════════════════════════════════════════════════════════
    print("=" * 90)
    print("TASK B: EQUAL-WEIGHT BENCHMARK SKEW ANALYSIS")
    print("=" * 90)
    print()

    # Define universe variants
    all_10 = sorted(tickers)
    universes = [
        ("All 10 tickers", all_10),
        ("Excl EAST", [t for t in all_10 if t != "EAST"]),
        ("Excl SWDY", [t for t in all_10 if t != "SWDY"]),
        ("Excl EAST+SWDY", [t for t in all_10 if t not in ("EAST", "SWDY")]),
        ("Excl EAST+SWDY+PHDC+FWRY", [t for t in all_10 if t not in ("EAST", "SWDY", "PHDC", "FWRY")]),
    ]

    print(f"  Portfolio 30% cap return: {portfolio_return:+.2f}%")
    print(f"  nav_end_date: {NAV_END_DATE}")
    print(f"  Buy-side cost: {COST_PER_SIDE * 100:.3f}%")
    print()

    # Compute per-ticker B&H returns for context
    print("  Per-ticker buy-and-hold returns (with 0.289% buy cost):")
    ticker_returns = {}
    for ticker in all_10:
        ew_single = compute_equal_weight_benchmark(
            daily_prices, [ticker], INITIAL_CAPITAL, nav_end_date=NAV_END_DATE
        )
        if ew_single:
            ret = (ew_single[-1]["nav"] / ew_single[0]["nav"] - 1) * 100
            ticker_returns[ticker] = ret
            print(f"    {ticker:<8} {ret:>+8.2f}%")
        else:
            ticker_returns[ticker] = 0.0
            print(f"    {ticker:<8}      N/A")
    print()

    # Equal-weight for each universe
    print(f"  {'Universe':<35} {'Tickers':>3} {'Return':>10} {'vs Portfolio 30% cap ({portfolio_return:+.2f}%)'}")
    print(f"  {'-' * 75}")

    for label, ticker_list in universes:
        ew = compute_equal_weight_benchmark(
            daily_prices, ticker_list, INITIAL_CAPITAL, nav_end_date=NAV_END_DATE
        )
        if ew:
            ret = (ew[-1]["nav"] / ew[0]["nav"] - 1) * 100
            diff = portfolio_return - ret
            print(f"  {label:<35} {len(ticker_list):>3}   {ret:>+8.2f}%   portfolio is {diff:>+8.2f} pp vs this benchmark")
        else:
            print(f"  {label:<35} {len(ticker_list):>3}       N/A")

    print()

    # Interpretation
    print("=" * 90)
    print("INTERPRETATION")
    print("=" * 90)
    print()

    # Sort tickers by return
    sorted_by_return = sorted(ticker_returns.items(), key=lambda x: x[1], reverse=True)
    top4 = [t for t, _ in sorted_by_return[:4]]
    bottom4 = [t for t, _ in sorted_by_return[-4:]]

    print("  Tickers ranked by buy-and-hold return:")
    for i, (ticker, ret) in enumerate(sorted_by_return, 1):
        marker = " <-- excluded in 4-outlier test" if ticker in ("EAST", "SWDY", "PHDC", "FWRY") else ""
        print(f"    {i:>2}. {ticker:<8} {ret:>+8.2f}%{marker}")
    print()
    print(f"  Top 4 by B&H return:    {', '.join(top4)}")
    print(f"  Bottom 4 by B&H return: {', '.join(bottom4)}")
    print()
    print("  If removing the top outliers sharply lowers the equal-weight benchmark,")
    print("  then the system's underperformance is concentrated in missing a few big winners,")
    print("  NOT in broad stock-picking weakness.")
    print()


if __name__ == "__main__":
    main()
