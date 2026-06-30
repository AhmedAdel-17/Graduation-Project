"""
Portfolio-level simulator using existing P7 audit logs.

Reads per-ticker backtest reports and simulates a single multi-stock
portfolio that allocates capital across BUY signals, holds positions,
and exits on SELL signals.

Enhancements over v1:
- T+2 settlement: sell proceeds locked for 2 business days
- Daily mark-to-market NAV using historical close prices
- Max drawdown computed from daily NAV (not eval-date only)
- Concentration cap applies at entry/allocation time only (not rebalanced)
- Equal-weight buy-and-hold benchmark of the same ticker universe

No new LLM calls.  No prompt changes.  Uses audit close prices only.
"""

import json
import os
import sys
from copy import deepcopy
from datetime import datetime, timedelta

import pandas as pd

# ── Cost model (from backtester.py) ─────────────────────────────────────────
EGX_BROKERAGE_RATE = 0.00175
EGX_STAMP_DUTY     = 0.00005
EGX_FRA_FEE        = 0.00009
EGX_SLIPPAGE       = 0.001
COST_PER_SIDE      = EGX_BROKERAGE_RATE + EGX_STAMP_DUTY + EGX_FRA_FEE + EGX_SLIPPAGE
# 0.289% per side, ~0.578% round trip

T_PLUS_2_DAYS = 2  # business days for settlement


def load_audit_logs(report_paths: dict[str, str]) -> dict:
    """Load audit logs from P7 report files.

    Handles duplicate dates (e.g. from --resume) by preferring entries with
    non-None price over None-price entries.
    """
    data = {}
    for ticker, path in report_paths.items():
        with open(path) as f:
            d = json.load(f)
        data[ticker] = {}
        for entry in d.get("audit_log", []):
            date = entry["date"]
            new_price = float(entry.get("price") or 0)
            new_conf = float(entry.get("confidence") or 0)
            # If we already have this date, keep the entry with a real price
            if date in data[ticker] and data[ticker][date]["price"] > 0 and new_price == 0:
                continue
            data[ticker][date] = {
                "decision": entry.get("parsed_decision", "HOLD"),
                "price": new_price,
                "confidence": new_conf,
            }
    return data


def load_egx30_benchmark_points(report_paths: dict[str, str]) -> list[dict]:
    """Extract EGX30 benchmark points from any report."""
    for path in report_paths.values():
        with open(path) as f:
            d = json.load(f)
        bh = d.get("benchmark_history", [])
        if bh:
            return bh
    return []


def load_daily_prices(csv_path: str) -> pd.DataFrame:
    """Load daily close prices from CSV (Date index, ticker columns)."""
    df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    df.columns = [c.replace(".CA", "") for c in df.columns]
    return df


def snap_to_trading_day(date_str: str, trading_dates: list[str]) -> str | None:
    """Snap a calendar date to the nearest FOLLOWING trading day.

    If the date is a trading day, return it. Otherwise return the next one.
    Returns None if no trading day exists on or after the date.
    """
    for td in trading_dates:
        if td >= date_str:
            return td
    return None


def business_days_after(date_str: str, n: int, trading_dates: list[str]) -> str:
    """Return the date that is n business days after date_str in the trading calendar."""
    try:
        idx = trading_dates.index(date_str)
    except ValueError:
        for i, d in enumerate(trading_dates):
            if d >= date_str:
                idx = i
                break
        else:
            return trading_dates[-1]
    target_idx = min(idx + n, len(trading_dates) - 1)
    return trading_dates[target_idx]


def get_daily_price(daily_prices: pd.DataFrame, ticker: str, date_str: str) -> float | None:
    """Get close price for a ticker on a specific date, with forward-fill."""
    try:
        ts = pd.Timestamp(date_str)
        if ts in daily_prices.index and ticker in daily_prices.columns:
            val = daily_prices.loc[ts, ticker]
            if pd.notna(val):
                return float(val)
        # Forward-fill: find most recent date <= target
        mask = daily_prices.index <= ts
        if mask.any() and ticker in daily_prices.columns:
            val = daily_prices.loc[mask, ticker].dropna().iloc[-1]
            return float(val)
    except (KeyError, IndexError):
        pass
    return None


def simulate_portfolio(
    audit_data: dict,
    daily_prices: pd.DataFrame,
    initial_capital: float = 1_000_000.0,
    max_position_pct: float = 1.0,
    nav_end_date: str | None = None,
) -> dict:
    """Run portfolio simulation with T+2 settlement and daily mark-to-market.

    Parameters
    ----------
    max_position_pct : float
        Maximum fraction of *initial_capital* that can be allocated to a
        single ticker at entry time.  Not continuously rebalanced —
        a position that appreciates past the cap is NOT trimmed.
    nav_end_date : str | None
        If set, truncate daily NAV to this date (inclusive).  The simulation
        still processes all eval dates, but the daily loop stops here.
    """
    tickers = sorted(audit_data.keys())
    max_alloc = initial_capital * max_position_pct

    # Build trading calendar from daily prices
    trading_dates = sorted(daily_prices.index.strftime("%Y-%m-%d").tolist())
    if nav_end_date:
        trading_dates = [d for d in trading_dates if d <= nav_end_date]

    # Collect all evaluation dates and snap to nearest trading day
    raw_eval_dates = set()
    for t in tickers:
        raw_eval_dates.update(audit_data[t].keys())

    # Map each raw eval date to its snapped trading day
    eval_date_map = {}  # snapped_date -> raw_date
    for raw_date in sorted(raw_eval_dates):
        snapped = snap_to_trading_day(raw_date, trading_dates)
        if snapped:
            eval_date_map[snapped] = raw_date

    eval_trading_dates = set(eval_date_map.keys())

    # Portfolio state
    cash = initial_capital
    positions = {}  # {ticker: {shares, avg_cost, entry_date}}
    total_commissions = 0.0
    trade_log = []

    # T+2 settlement: pending proceeds not yet available
    pending_settlement = []  # [(available_date, amount)]

    # Daily NAV series
    daily_nav = []

    # Eval-date snapshots (recorded DURING the loop)
    eval_snapshots = []

    def settle_pending(current_date: str):
        nonlocal cash
        still_pending = []
        for avail_date, amount in pending_settlement:
            if current_date >= avail_date:
                cash += amount
            else:
                still_pending.append((avail_date, amount))
        pending_settlement.clear()
        pending_settlement.extend(still_pending)

    def pending_total() -> float:
        return sum(amt for _, amt in pending_settlement)

    def mark_to_market(date_str: str) -> tuple[float, dict]:
        pos_value = 0.0
        detail = {}
        for ticker, pos in positions.items():
            price = get_daily_price(daily_prices, ticker, date_str)
            if price is not None and price > 0:
                val = pos["shares"] * price
            else:
                val = pos["shares"] * pos["avg_cost"]
                price = pos["avg_cost"]
            pos_value += val
            detail[ticker] = {"shares": pos["shares"], "price": price, "value": val}
        return pos_value, detail

    # ── Process each trading day ──────────────────────────────────────────
    for date_str in trading_dates:
        # Settle any T+2 proceeds that have matured
        settle_pending(date_str)

        # If this is a snapped eval date, process signals
        if date_str in eval_trading_dates:
            raw_date = eval_date_map[date_str]

            signals = {}
            for ticker in tickers:
                entry = audit_data[ticker].get(raw_date)
                if entry:
                    signals[ticker] = entry
                else:
                    signals[ticker] = {"decision": "HOLD", "price": None}

            # ── Step 1: Process SELLs ─────────────────────────────────
            for ticker in tickers:
                sig = signals[ticker]
                if sig["decision"] == "SELL" and ticker in positions:
                    pos = positions[ticker]
                    # Use the audit log price (which is the price the
                    # backtester actually used for that decision)
                    sell_price = sig["price"]
                    if sell_price and sell_price > 0:
                        gross_proceeds = pos["shares"] * sell_price
                        cost = gross_proceeds * COST_PER_SIDE
                        net_proceeds = gross_proceeds - cost
                        total_commissions += cost

                        # T+2: proceeds locked
                        avail_date = business_days_after(
                            date_str, T_PLUS_2_DAYS, trading_dates
                        )
                        pending_settlement.append((avail_date, net_proceeds))

                        trade_log.append({
                            "date": date_str,
                            "raw_eval_date": raw_date,
                            "ticker": ticker,
                            "action": "SELL",
                            "shares": pos["shares"],
                            "price": sell_price,
                            "gross": gross_proceeds,
                            "cost": cost,
                            "net": net_proceeds,
                            "pnl": net_proceeds - (pos["shares"] * pos["avg_cost"]),
                            "settlement_date": avail_date,
                        })
                        del positions[ticker]

            # ── Step 2: New BUY candidates ────────────────────────────
            new_buys = []
            for ticker in tickers:
                sig = signals[ticker]
                if sig["decision"] == "BUY" and ticker not in positions:
                    if sig["price"] and sig["price"] > 0:
                        new_buys.append((ticker, sig["price"]))

            # ── Step 3: Allocate available cash to BUYs ───────────────
            # Minimum allocation: 0.1% of initial capital (filters rounding dust)
            min_alloc = initial_capital * 0.001
            if new_buys and cash > min_alloc:
                equal_split = cash / len(new_buys)
                alloc_per_ticker = min(equal_split, max_alloc)
                for ticker, price in new_buys:
                    if cash <= min_alloc:
                        break
                    this_alloc = min(alloc_per_ticker, cash)
                    effective_price = price * (1 + COST_PER_SIDE)
                    shares = this_alloc / effective_price
                    cost_amount = shares * price * COST_PER_SIDE
                    total_commissions += cost_amount

                    positions[ticker] = {
                        "shares": shares,
                        "avg_cost": effective_price,
                        "entry_date": date_str,
                    }
                    cash -= (shares * price + cost_amount)

                    trade_log.append({
                        "date": date_str,
                        "raw_eval_date": raw_date,
                        "ticker": ticker,
                        "action": "BUY",
                        "shares": shares,
                        "price": price,
                        "gross": shares * price,
                        "cost": cost_amount,
                        "net": shares * price + cost_amount,
                    })

            # ── Snapshot at eval date (DURING loop) ───────────────────
            pos_value, detail = mark_to_market(date_str)
            nav_at = cash + pending_total() + pos_value
            eval_snapshots.append({
                "date": date_str,
                "raw_eval_date": raw_date,
                "nav": nav_at,
                "cash": cash,
                "pending": pending_total(),
                "positions_value": pos_value,
                "n_positions": len(positions),
                "holdings": {t: d for t, d in detail.items()},
            })

        # ── Daily mark-to-market (every trading day) ──────────────────
        pos_value, _ = mark_to_market(date_str)
        nav = cash + pending_total() + pos_value
        daily_nav.append({
            "date": date_str,
            "nav": nav,
            "cash": cash,
            "pending": pending_total(),
            "invested": pos_value,
        })

    # ── Compute metrics from daily NAV ─────────────────────────────────
    end_nav = daily_nav[-1]["nav"] if daily_nav else initial_capital
    total_return_pct = (end_nav / initial_capital - 1) * 100

    # Max drawdown from daily NAV
    peak = initial_capital
    max_dd = 0.0
    max_dd_date = ""
    for entry in daily_nav:
        if entry["nav"] > peak:
            peak = entry["nav"]
        dd = (entry["nav"] / peak - 1) * 100
        if dd < max_dd:
            max_dd = dd
            max_dd_date = entry["date"]

    # Trade counts
    buy_trades = [t for t in trade_log if t["action"] == "BUY"]
    sell_trades = [t for t in trade_log if t["action"] == "SELL"]
    closed_pnl = [t["pnl"] for t in trade_log if t["action"] == "SELL" and "pnl" in t]
    wins = sum(1 for p in closed_pnl if p > 0)
    losses = sum(1 for p in closed_pnl if p <= 0)

    return {
        "initial_capital": initial_capital,
        "final_nav": round(end_nav, 2),
        "total_return_pct": round(total_return_pct, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "max_drawdown_date": max_dd_date,
        "total_commissions": round(total_commissions, 2),
        "total_trades": len(trade_log),
        "buy_trades": len(buy_trades),
        "sell_trades": len(sell_trades),
        "closed_trades": len(closed_pnl),
        "winning_trades": wins,
        "losing_trades": losses,
        "final_cash": round(cash, 2),
        "pending_settlement": round(pending_total(), 2),
        "final_positions": {
            t: {
                "shares": round(positions[t]["shares"], 2),
                "avg_cost": round(positions[t]["avg_cost"], 4),
                "entry_date": positions[t]["entry_date"],
            }
            for t in positions
        },
        "daily_nav": daily_nav,
        "eval_snapshots": eval_snapshots,
        "trade_log": trade_log,
        "daily_nav_count": len(daily_nav),
    }


def compute_equal_weight_benchmark(
    daily_prices: pd.DataFrame,
    tickers: list[str],
    initial_capital: float,
    nav_end_date: str | None = None,
) -> list[dict]:
    """Equal-weight buy-and-hold of all tickers from day 1 (with buy cost)."""
    n = len(tickers)
    alloc_per = initial_capital / n
    holdings = {}

    first_date = daily_prices.index[0]
    for ticker in tickers:
        price = daily_prices.loc[first_date, ticker]
        if pd.notna(price) and price > 0:
            effective_price = price * (1 + COST_PER_SIDE)
            holdings[ticker] = alloc_per / effective_price
        else:
            holdings[ticker] = 0

    nav_series = []
    for date in daily_prices.index:
        date_str = date.strftime("%Y-%m-%d")
        if nav_end_date and date_str > nav_end_date:
            break
        total = 0.0
        for ticker in tickers:
            price = daily_prices.loc[date, ticker]
            if pd.notna(price):
                total += holdings[ticker] * price
        nav_series.append({"date": date_str, "nav": total})
    return nav_series


def report_matches_window(path: str, start: str | None, end: str | None) -> bool:
    """Return True if a report's audit log matches the requested date window."""
    if start is None and end is None:
        return True
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False

    dates = sorted(
        entry.get("date")
        for entry in data.get("audit_log", [])
        if entry.get("date")
    )
    if not dates:
        return False
    if start is not None and dates[0] != start:
        return False
    if end is not None and dates[-1] != end:
        return False
    return True


def discover_reports(
    report_dir: str,
    tickers: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, str]:
    """Auto-discover the latest report for each ticker in a directory.

    If tickers is None, discovers all tickers with reports.
    If start/end are provided, only reports whose audit logs match that
    window are considered. This prevents accidentally selecting an older or
    newer run for the same ticker.
    For each ticker, picks the report with the latest timestamp in the filename.

    Returns {ticker_short: path}, e.g. {"COMI": "./backtest_results/report_COMI.CA_20260619_001734.json"}
    """
    import glob as glob_mod
    import re

    pattern = os.path.join(report_dir, "report_*.json")
    all_reports = sorted(glob_mod.glob(pattern))

    # Group by ticker
    ticker_reports: dict[str, list[str]] = {}
    for path in all_reports:
        basename = os.path.basename(path)
        # Extract ticker from report_TICKER_TIMESTAMP.json
        m = re.match(r"report_(.+?)_(\d{8}_\d{6})\.json", basename)
        if m:
            ticker_full = m.group(1)  # e.g. "COMI.CA"
            ticker_short = ticker_full.replace(".CA", "")
            if tickers is None or ticker_full in tickers or ticker_short in tickers:
                if not report_matches_window(path, start, end):
                    continue
                ticker_reports.setdefault(ticker_short, []).append(path)

    # Pick the latest (last in sorted order = latest timestamp)
    result = {}
    for ticker_short, paths in ticker_reports.items():
        result[ticker_short] = paths[-1]  # sorted by timestamp, last = newest

    return result


def discover_reports_for_run(
    report_dir: str,
    tickers: list[str],
    run_label: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, str]:
    """Discover reports for a specific set of tickers.

    Prints what was found and errors for missing tickers.
    Returns {ticker_short: path}.
    """
    reports = discover_reports(report_dir, tickers, start=start, end=end)

    ticker_shorts = {t.replace(".CA", "") for t in tickers}
    found = set(reports.keys())
    missing = ticker_shorts - found

    if run_label:
        print(f"  Run: {run_label}")
    if start or end:
        print(f"  Window filter: {start or 'ANY'} → {end or 'ANY'}")
    print(f"  Reports found: {len(found)}/{len(ticker_shorts)}")
    for t in sorted(found):
        print(f"    ✓ {t}: {os.path.basename(reports[t])}")
    for t in sorted(missing):
        print(f"    ✗ {t}: NO REPORT FOUND")

    if missing:
        print(f"\n  WARNING: {len(missing)} tickers missing reports. Proceeding with {len(found)}.")

    return reports


def fetch_daily_prices_for_tickers(
    tickers: list[str],
    start: str,
    end: str,
) -> pd.DataFrame:
    """Fetch daily close prices from yfinance for a list of tickers."""
    import yfinance as yf
    tickers_yf = [t if ".CA" in t else f"{t}.CA" for t in tickers]
    data = yf.download(tickers_yf, start=start, end=end, progress=False)
    close = data["Close"]
    # Normalize column names to short form
    close.columns = [c.replace(".CA", "") for c in close.columns]
    return close


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Portfolio-level simulator using backtest audit logs")
    parser.add_argument("--report-dir", default="./backtest_results",
                        help="Directory containing report_*.json files")
    parser.add_argument("--tickers", default=None,
                        help="Comma-separated ticker list (default: auto-discover all)")
    parser.add_argument("--label", default=None,
                        help="Run label for display (e.g. 'P7 in-sample' or 'P7 OOS')")
    parser.add_argument("--variants", default="50,30,20",
                        help="Comma-separated concentration cap percentages (default: 50,30,20)")
    parser.add_argument("--start", default=None,
                        help="Optional audit-log start date filter (YYYY-MM-DD)")
    parser.add_argument("--end", default=None,
                        help="Optional audit-log end date filter (YYYY-MM-DD)")
    parser.add_argument("--nav-end", default=None,
                        help="Strict end date for daily NAV (default: use all available prices)")
    args = parser.parse_args()

    # ── Discover reports ───────────────────────────────────────────────────
    print("=" * 90)
    print("REPORT DISCOVERY")
    print("=" * 90)

    ticker_list = args.tickers.split(",") if args.tickers else None
    reports = discover_reports_for_run(
        args.report_dir,
        ticker_list,
        args.label,
        start=args.start,
        end=args.end,
    ) if ticker_list else discover_reports(args.report_dir, start=args.start, end=args.end)

    if not ticker_list:
        # Auto-discovered: show what we found
        print(f"  Auto-discovered {len(reports)} tickers:")
        for t in sorted(reports):
            print(f"    ✓ {t}: {os.path.basename(reports[t])}")

    if not reports:
        print("ERROR: No reports found. Check --report-dir and --tickers.")
        sys.exit(1)
    print()

    # ── Load data ──────────────────────────────────────────────────────────
    audit_data = load_audit_logs(reports)

    # Determine date range from audit data
    all_dates_flat = [d for t in audit_data.values() for d in t.keys()]
    date_start = min(all_dates_flat)
    date_end = max(all_dates_flat)
    price_end = (pd.Timestamp(date_end) + pd.Timedelta(days=7)).strftime("%Y-%m-%d")

    # Fetch daily prices (with margin for mark-to-market)
    tickers_for_prices = [f"{t}.CA" for t in sorted(audit_data.keys())]
    print(f"Fetching daily prices for {len(tickers_for_prices)} tickers ({date_start} → {date_end})...")
    daily_prices = fetch_daily_prices_for_tickers(
        list(audit_data.keys()), date_start, price_end
    )
    print(f"  Got {len(daily_prices)} trading days × {len(daily_prices.columns)} tickers")
    print()

    # Build trading calendar for date-snap display
    trading_dates = sorted(daily_prices.index.strftime("%Y-%m-%d").tolist())

    # EGX30 benchmark from report
    egx30_points = load_egx30_benchmark_points(reports)
    if args.nav_end:
        egx30_points = [p for p in egx30_points if p.get("date", "") <= args.nav_end]
    if egx30_points:
        egx30_start = egx30_points[0]["value"]
        egx30_end = egx30_points[-1]["value"]
        egx30_return = (egx30_end / egx30_start - 1) * 100
    else:
        egx30_return = None

    # Equal-weight buy-and-hold benchmark
    ew_tickers = sorted(audit_data.keys())
    ew_benchmark = compute_equal_weight_benchmark(
        daily_prices, ew_tickers, 1_000_000.0, nav_end_date=args.nav_end
    )
    ew_return = (ew_benchmark[-1]["nav"] / ew_benchmark[0]["nav"] - 1) * 100
    ew_peak = ew_benchmark[0]["nav"]
    ew_max_dd = 0.0
    for entry in ew_benchmark:
        if entry["nav"] > ew_peak:
            ew_peak = entry["nav"]
        dd = (entry["nav"] / ew_peak - 1) * 100
        if dd < ew_max_dd:
            ew_max_dd = dd

    # ── Per-ticker single-stock metrics ────────────────────────────────────
    print("=" * 90)
    print("PER-TICKER SINGLE-STOCK METRICS")
    print("=" * 90)
    print()
    print(f"  {'Ticker':<8} {'Dates':>6} {'BUY':>5} {'HOLD':>5} {'SELL':>5} {'B&H Ret':>9} {'Bench':>9}")
    print(f"  {'-' * 55}")
    total_buy = total_hold = total_sell = 0
    for ticker in sorted(audit_data.keys()):
        entries = audit_data[ticker]
        decisions = [e["decision"] for e in entries.values()]
        n_buy = decisions.count("BUY")
        n_hold = decisions.count("HOLD")
        n_sell = decisions.count("SELL")
        total_buy += n_buy
        total_hold += n_hold
        total_sell += n_sell
        # Per-ticker return from audit (first to last price)
        # Fall back to daily_prices when audit price is 0 (None in source)
        dates_sorted = sorted(entries.keys())
        if dates_sorted:
            first_price = entries[dates_sorted[0]]["price"]
            last_price = entries[dates_sorted[-1]]["price"]
            if first_price <= 0:
                p = get_daily_price(daily_prices, ticker, dates_sorted[0])
                first_price = p if p else 0
            if last_price <= 0:
                p = get_daily_price(daily_prices, ticker, dates_sorted[-1])
                last_price = p if p else 0
            ticker_ret = (last_price / first_price - 1) * 100 if first_price > 0 else 0
        else:
            ticker_ret = 0
        bench_str = f"{egx30_return:>+.2f}%" if egx30_return is not None else "N/A"
        print(f"  {ticker:<8} {len(entries):>6} {n_buy:>5} {n_hold:>5} {n_sell:>5} {ticker_ret:>+8.2f}% {bench_str:>9}")

    total_decisions = total_buy + total_hold + total_sell
    hold_rate = total_hold / total_decisions * 100 if total_decisions > 0 else 0
    print()
    print(f"  Aggregate: BUY={total_buy}  HOLD={total_hold}  SELL={total_sell}  "
          f"HOLD rate={hold_rate:.1f}%")
    print()

    # ── Show eval-date snapping ────────────────────────────────────────────
    tickers = sorted(audit_data.keys())
    raw_eval_dates = sorted(set(d for t in audit_data.values() for d in t.keys()))

    run_label = args.label or f"{len(tickers)}-ticker backtest"
    print("=" * 90)
    print(f"PORTFOLIO SIMULATION v2 — {run_label}")
    print("  T+2 settlement | Daily mark-to-market | Concentration cap at entry only")
    print("=" * 90)
    print()

    # Show date snapping
    print("Eval date snapping (calendar → nearest trading day):")
    for raw_date in raw_eval_dates:
        snapped = snap_to_trading_day(raw_date, trading_dates)
        if snapped and snapped != raw_date:
            print(f"  {raw_date} ({pd.Timestamp(raw_date).strftime('%A')}) → {snapped} ({pd.Timestamp(snapped).strftime('%A')})")
    has_snaps = any(
        snap_to_trading_day(d, trading_dates) != d
        for d in raw_eval_dates
        if snap_to_trading_day(d, trading_dates)
    )
    if not has_snaps:
        print("  (all eval dates are trading days — no snapping needed)")
    print()

    # Decision matrix
    if len(tickers) <= 6:
        # Wide format for small universes
        print("Decision Matrix:")
        print(f"{'Date':<12}", end="")
        for t in tickers:
            print(f"  {t:>12}", end="")
        print()
        print("-" * (12 + 14 * len(tickers)))
        for date in raw_eval_dates:
            print(f"{date:<12}", end="")
            for t in tickers:
                entry = audit_data[t].get(date)
                if entry:
                    print(f"  {entry['decision']:>4}@{entry['price']:>6.2f}", end="")
                else:
                    print(f"  {'--':>12}", end="")
            print()
    else:
        # Compact format for large universes
        print(f"Decision Matrix ({len(tickers)} tickers × {len(raw_eval_dates)} eval dates):")
        print(f"{'Date':<12}", end="")
        for t in tickers:
            print(f" {t:>6}", end="")
        print()
        print("-" * (12 + 7 * len(tickers)))
        for date in raw_eval_dates:
            print(f"{date:<12}", end="")
            for t in tickers:
                entry = audit_data[t].get(date)
                if entry:
                    d = entry["decision"][0]  # B/H/S
                    print(f"    {d:>2}", end="")
                else:
                    print(f"    --", end="")
            print()

    print()
    print(f"Cost model: {COST_PER_SIDE*100:.3f}% per side (brokerage + stamp + FRA + slippage)")
    print(f"T+2 settlement: MODELLED — sell proceeds locked for {T_PLUS_2_DAYS} business days")
    print(f"Daily prices: {len(daily_prices)} trading days ({daily_prices.index[0].strftime('%Y-%m-%d')} → {daily_prices.index[-1].strftime('%Y-%m-%d')})")
    print(f"Concentration cap: applied at ENTRY/ALLOCATION time only (no continuous rebalancing)")
    print()

    # ── Benchmarks ─────────────────────────────────────────────────────────
    print("=" * 90)
    print("BENCHMARKS")
    print("=" * 90)
    if egx30_return is not None:
        print(f"  EGX30 buy-and-hold:              {egx30_return:>+7.2f}%  (from report benchmark_history)")
    else:
        print(f"  EGX30 buy-and-hold:              N/A (no benchmark_history in reports)")
    print(f"  Equal-weight {len(ew_tickers)}-stock B&H:   {ew_return:>+7.2f}%  (max DD: {ew_max_dd:.2f}%)")
    print(f"    Tickers: {', '.join(ew_tickers)}")
    print()

    # ── Run variants ───────────────────────────────────────────────────────
    cap_pcts = [int(x) for x in args.variants.split(",")]
    variants = [(f"{p}% cap", p / 100.0) for p in cap_pcts]

    results = []
    for label, cap in variants:
        result = simulate_portfolio(audit_data, daily_prices, max_position_pct=cap, nav_end_date=args.nav_end)
        alpha_egx = (result["total_return_pct"] - egx30_return) if egx30_return is not None else None
        alpha_ew = result["total_return_pct"] - ew_return
        results.append((label, cap, result, alpha_egx, alpha_ew))

    # ── Summary table ──────────────────────────────────────────────────────
    print("=" * 90)
    print("CONCENTRATION SENSITIVITY (T+2, daily mark-to-market)")
    print("=" * 90)
    print()
    print(f"{'Variant':<12} {'Return':>9} {'α EGX30':>9} {'α EqWt':>9} {'Max DD':>9} {'DD Date':>12} {'Trades':>7} {'Cash%':>7} {'Beats':>7}")
    print("-" * 90)
    for label, cap, result, alpha_egx, alpha_ew in results:
        final_liquid = result["final_cash"] + result["pending_settlement"]
        cash_pct = final_liquid / result["final_nav"] * 100 if result["final_nav"] > 0 else 0
        egx_str = f"{alpha_egx:>+8.2f}%" if alpha_egx is not None else "     N/A"
        beats_egx = (alpha_egx is not None and alpha_egx > 0)
        beats_ew = alpha_ew > 0
        if beats_egx and beats_ew:
            beats = "BOTH"
        elif beats_egx:
            beats = "EGX30"
        elif beats_ew:
            beats = "EqWt"
        else:
            beats = "NONE"
        print(
            f"{label:<12} {result['total_return_pct']:>8.2f}% {egx_str} {alpha_ew:>+8.2f}% "
            f"{result['max_drawdown_pct']:>8.2f}% {result['max_drawdown_date']:>12} "
            f"{result['total_trades']:>7d} {cash_pct:>6.1f}% {beats:>7}"
        )

    print()
    if egx30_return is not None:
        print(f"  EGX30 return:             {egx30_return:>+7.2f}%")
    print(f"  Equal-weight {len(ew_tickers)}-stock B&H: {ew_return:>+7.2f}%")
    print()

    # ── Per-variant detail ─────────────────────────────────────────────────
    for label, cap, result, alpha_egx, alpha_ew in results:
        print("=" * 90)
        print(f"VARIANT: {label} (max_position_pct = {cap:.0%})")
        print("=" * 90)
        print()
        print(f"  Initial capital:       {result['initial_capital']:>14,.2f} EGP")
        print(f"  Final NAV:             {result['final_nav']:>14,.2f} EGP")
        print(f"  Total return:                {result['total_return_pct']:.2f}%")
        if alpha_egx is not None:
            print(f"  Alpha vs EGX30:             {alpha_egx:>+.2f}%")
        print(f"  Alpha vs EqWt B&H:          {alpha_ew:>+.2f}%")
        print(f"  Max drawdown (daily):        {result['max_drawdown_pct']:.2f}% (on {result['max_drawdown_date']})")
        print(f"  Daily NAV data points:       {result['daily_nav_count']}")
        print(f"  Total trades:                {result['total_trades']}")
        print(f"    BUY:                       {result['buy_trades']}")
        print(f"    SELL:                      {result['sell_trades']}")
        print(f"  Closed round-trips:          {result['closed_trades']}")
        print(f"    Winners:                   {result['winning_trades']}")
        print(f"    Losers:                    {result['losing_trades']}")
        print(f"  Total commissions:     {result['total_commissions']:>14,.2f} EGP")
        print()

        # T+2 settlement events
        t2_trades = [t for t in result["trade_log"] if t["action"] == "SELL"]
        if t2_trades:
            print("  T+2 Settlement log:")
            for t in t2_trades:
                print(f"    SELL {t['ticker']} on {t['date']} (eval {t.get('raw_eval_date', '?')}) "
                      f"→ proceeds available {t.get('settlement_date', '?')}")
            print()

        # Final holdings
        last_date = result["daily_nav"][-1]["date"] if result["daily_nav"] else "?"
        print(f"  Final holdings (as of {last_date}):")
        if result["final_positions"]:
            for ticker, pos in sorted(result["final_positions"].items()):
                price = get_daily_price(daily_prices, ticker, last_date)
                val = pos["shares"] * price if price else 0
                pct = val / result["final_nav"] * 100 if result["final_nav"] > 0 else 0
                print(f"    {ticker}: {pos['shares']:,.2f} sh @ avg {pos['avg_cost']:.2f}, "
                      f"mkt {price:.2f}, val {val:,.0f} EGP ({pct:.1f}% NAV)")
        else:
            print("    (all cash)")
        print(f"  Final cash:            {result['final_cash']:>14,.2f} EGP")
        print(f"  Pending settlement:    {result['pending_settlement']:>14,.2f} EGP")
        print()

        # Eval-date NAV snapshots (recorded during loop — correct positions)
        print("  Eval-date NAV snapshots:")
        print(f"  {'Date':<12} {'Cash':>12} {'Pending':>10} {'Invested':>12} {'NAV':>14} {'Return':>8} {'#Pos':>5}  Holdings")
        print(f"  {'-' * 85}")
        for snap in result["eval_snapshots"]:
            ret = (snap["nav"] / result["initial_capital"] - 1) * 100
            holdings_str = ", ".join(sorted(snap["holdings"].keys()))
            date_label = snap["date"]
            if snap.get("raw_eval_date") and snap["raw_eval_date"] != snap["date"]:
                date_label = f"{snap['raw_eval_date']}→{snap['date']}"
            print(
                f"  {date_label:<12} {snap['cash']:>12,.0f} {snap['pending']:>10,.0f} "
                f"{snap['positions_value']:>12,.0f} {snap['nav']:>14,.0f} {ret:>7.2f}% {snap['n_positions']:>5}  [{holdings_str}]"
            )
        print()

        # Concentration at entry
        print("  Concentration at entry (% of initial_capital allocated per ticker):")
        buy_entries = [t for t in result["trade_log"] if t["action"] == "BUY"]
        for be in buy_entries:
            alloc_pct = be["net"] / result["initial_capital"] * 100
            print(f"    {be['date']} {be['ticker']}: {alloc_pct:.1f}% of initial capital")
        print()

        # Max daily concentration profile (sample 5 dates)
        print("  Daily concentration peaks (sampled every ~25 days):")
        step = max(1, len(result["daily_nav"]) // 5)
        for i in range(0, len(result["daily_nav"]), step):
            dn = result["daily_nav"][i]
            _, detail = mark_to_market_static(daily_prices, result, dn["date"])
            if detail and dn["nav"] > 0:
                max_ticker = max(detail, key=lambda t: detail[t]["value"])
                max_pct = detail[max_ticker]["value"] / dn["nav"] * 100
                print(f"    {dn['date']}: max = {max_pct:.1f}% ({max_ticker}), "
                      f"NAV = {dn['nav']:,.0f}")
        print()

    # ── Final verdict ──────────────────────────────────────────────────────
    print("=" * 90)
    print(f"FINAL VERDICT (v2: T+2, daily drawdown, {len(tickers)}-ticker portfolio)")
    print("=" * 90)
    print()
    print(f"  {'Variant':<12} {'α EGX30':>10} {'α EqWt':>10} {'Max DD':>10}  Status")
    print(f"  {'-' * 60}")
    for label, cap, result, alpha_egx, alpha_ew in results:
        egx_str = f"{alpha_egx:>+9.2f}%" if alpha_egx is not None else "      N/A"
        beats_egx = (alpha_egx is not None and alpha_egx > 0)
        beats_ew = alpha_ew > 0
        if beats_egx and beats_ew:
            status = "BEATS BOTH"
        elif beats_egx:
            status = "BEATS EGX30 only"
        elif beats_ew:
            status = "BEATS EqWt only"
        else:
            status = "TRAILS BOTH"
        print(f"  {label:<12} {egx_str} {alpha_ew:>+9.2f}% {result['max_drawdown_pct']:>9.2f}%  {status}")

    print()
    print("  Caveats:")
    print(f"  - {len(tickers)} tickers, single run (LLM variance not averaged)")
    print("  - T+2 modelled for sell proceeds only; buy settlement not modelled")
    print("  - Eval dates on EGX holidays snapped to next trading day")
    print("  - Concentration cap at entry only (no continuous rebalancing)")
    print()


def mark_to_market_static(daily_prices, result, date_str):
    """Static mark-to-market for display purposes — reconstructs from trade log."""
    # Reconstruct positions at a given date from trade log
    positions = {}
    for trade in result["trade_log"]:
        if trade["date"] > date_str:
            break
        if trade["action"] == "BUY":
            positions[trade["ticker"]] = {
                "shares": trade["shares"],
                "avg_cost": trade["price"] * (1 + COST_PER_SIDE),
            }
        elif trade["action"] == "SELL" and trade["ticker"] in positions:
            del positions[trade["ticker"]]

    detail = {}
    for ticker, pos in positions.items():
        price = get_daily_price(daily_prices, ticker, date_str)
        if price and price > 0:
            detail[ticker] = {"value": pos["shares"] * price}
        else:
            detail[ticker] = {"value": pos["shares"] * pos["avg_cost"]}
    return positions, detail


if __name__ == "__main__":
    main()
