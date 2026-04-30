"""
TradingAgents - EGX Backtesting Engine
======================================
Simulates historical trades using the full agent graph.
Tracks portfolio metrics such as returns, drawdowns, and win rates.

Phase 1 Enhancements:
- EGX commission + stamp duty + FRA fee modeling (~0.189% per side)
- Execution slippage (0.1% normal, 0.5% low-liquidity)
- T+2 settlement (sell proceeds held for 2 business days before reuse)
- Circuit breaker simulation (skip trade if price moves ±10% vs prior close)
- Benchmark comparison (vs ^EGX30 or custom index)
- Extended metrics: Alpha, Calmar Ratio, total commissions paid
"""

import os
import sys
import io
import json
import re
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

# Windows console encoding fix — only applied when run as a CLI script,
# not when imported as a module (e.g., by the API server background tasks).
if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.dataflows.config import get_config, set_config
from tradingagents.dataflows.gateway import DataGateway
from tradingagents.ablation.schemas import InstrumentedLLM, LLMCallLog
from tradingagents.ablation.runner import _rebuild_graph

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("Backtester")

# =============================================================================
# EGX Market Cost Model Constants
# =============================================================================
# Sources: EGX fee schedule + FRA regulatory requirements
EGX_BROKERAGE_RATE  = 0.00175   # 0.175% institutional brokerage fee
EGX_STAMP_DUTY      = 0.00005   # 0.005% stamp duty (applied on both sides)
EGX_FRA_FEE         = 0.00009   # 0.009% FRA (Financial Regulatory Authority) levy
EGX_TOTAL_COST_SIDE = EGX_BROKERAGE_RATE + EGX_STAMP_DUTY + EGX_FRA_FEE  # ~0.189% per side

EGX_SLIPPAGE_NORMAL  = 0.001    # 0.1%  — normal liquidity stocks
EGX_SLIPPAGE_LOW_LIQ = 0.005    # 0.5%  — low-liquidity stocks (wider spreads)

EGX_CIRCUIT_BREAKER  = 0.10     # ±10% daily price move halts trading on EGX
EGX_SETTLEMENT_DAYS  = 2        # T+2: cash from a SELL settles after 2 business days


def _compute_reasoning_score(final_state: dict) -> int:
    """
    Structural quality score (0–4) derived from the pipeline's final state.
      +1  Bull thesis contains explicit invalidation conditions
      +1  Research Manager output mentions both bull and bear sides
      +2  Risk debate contains 2–3 distinct perspectives (RISK / SAFE / NEUTRAL)
    """
    score = 0
    debate = final_state.get("investment_debate_state") or {}
    bull_thesis = debate.get("bull_thesis") or {}
    if bull_thesis.get("invalidation_conditions"):
        score += 1

    plan = final_state.get("investment_plan") or ""
    if re.search(r"bull|positive", plan, re.I) and re.search(r"bear|negative", plan, re.I):
        score += 1

    risk_history = (final_state.get("risk_debate_state") or {}).get("history", "")
    if risk_history:
        parts = sum([
            bool(re.search(r"RISK|🔴", risk_history, re.I)),
            bool(re.search(r"SAFE|🟢", risk_history, re.I)),
            bool(re.search(r"NEUTRAL|🟡", risk_history, re.I)),
        ])
        score += min(parts, 2)

    return score


class BacktestingEngine:
    def __init__(
        self,
        initial_capital: float = 1_000_000.0,
        target_market: str = "EGX",
        benchmark_ticker: Optional[str] = "^EGX30",
    ):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.portfolio_value = initial_capital
        self.positions: Dict[str, Dict] = {}  # {ticker: {"shares": int, "avg_cost": float}}
        self.trade_history: List[Dict] = []
        self.daily_history: List[Dict] = []   # For drawdown and Sharpe tracking

        # T+2 settlement queue: list of (settle_date_str, amount_egp)
        self.pending_cash_settlements: List[Tuple[str, float]] = []

        # Previous close prices for circuit breaker checks: {ticker: price}
        self.prev_prices: Dict[str, float] = {}

        # Benchmark tracking (EGX30 external index — may be unavailable)
        self.benchmark_ticker = benchmark_ticker
        self.benchmark_start_price: Optional[float] = None
        self.benchmark_history: List[Dict] = []   # [{date, price, value}]
        self._bm_data_map: Dict[str, float] = {}   # {date_str: close_price}

        # Buy-and-hold benchmark using the same ticker being tested
        self.buyhold_start_price: Optional[float] = None
        self.buyhold_history: List[Dict] = []

        # Decision audit trail — one entry per evaluation date
        self.audit_log: List[Dict] = []

        # Configure system
        set_config({
            "target_market": target_market,
            "trading_currency": "EGP" if target_market == "EGX" else "USD",
            "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
            "backtest_mode": True,
        })
        config = get_config()
        self.gateway = DataGateway(config)

    # =========================================================================
    # Phase 1 — T+2 Settlement
    # =========================================================================

    @staticmethod
    def _is_egx_trading_day(dt: datetime) -> bool:
        """EGX trades Sunday–Thursday (weekday 6, 0, 1, 2, 3). Friday & Saturday are off."""
        return dt.weekday() in (0, 1, 2, 3, 6)  # Mon=0 … Thu=3, Sun=6

    def _get_settlement_date(self, trade_date_str: str) -> str:
        """Return the T+2 settlement date, skipping EGX non-trading days (Fri & Sat)."""
        trade_dt = datetime.strptime(trade_date_str, "%Y-%m-%d")
        days_added = 0
        settle_dt = trade_dt
        while days_added < EGX_SETTLEMENT_DAYS:
            settle_dt += timedelta(days=1)
            if self._is_egx_trading_day(settle_dt):
                days_added += 1
        return settle_dt.strftime("%Y-%m-%d")

    def _settle_pending_cash(self, current_date_str: str):
        """Release any SELL proceeds whose settlement date has been reached."""
        still_pending = []
        for settle_date, amount in self.pending_cash_settlements:
            if settle_date <= current_date_str:
                self.cash += amount
                logger.info(
                    f"[T+2 SETTLED] +{amount:,.2f} EGP released on {current_date_str}"
                )
            else:
                still_pending.append((settle_date, amount))
        self.pending_cash_settlements = still_pending

    # =========================================================================
    # Phase 1 — Circuit Breaker
    # =========================================================================

    def _check_circuit_breaker(self, ticker: str, current_price: float) -> bool:
        """
        Returns True if the EGX ±10% daily circuit breaker is triggered.
        When True, trade execution is skipped for this date.
        """
        prev = self.prev_prices.get(ticker)
        if prev is None or prev <= 0:
            return False
        change_pct = abs(current_price - prev) / prev
        if change_pct >= EGX_CIRCUIT_BREAKER:
            direction = "UP" if current_price > prev else "DOWN"
            logger.warning(
                f"[CIRCUIT BREAKER] {ticker} moved {change_pct:.1%} {direction} "
                f"({prev:,.2f} → {current_price:,.2f}). Trading halted."
            )
            return True
        return False

    # =========================================================================
    # Phase 1 — Execution Cost Model (Slippage + Commission)
    # =========================================================================

    def _apply_execution_costs(
        self,
        action: str,
        shares: int,
        close_price: float,
        low_liquidity: bool = False,
    ) -> Tuple[float, float]:
        """
        Apply slippage and commission to produce a realistic execution price.

        Slippage works against the trader on both sides:
          BUY  → exec_price = close_price * (1 + slippage)   — pays more
          SELL → exec_price = close_price * (1 - slippage)   — receives less

        Commission is calculated on the execution value (not close price).

        Returns:
            (exec_price, total_commission_egp)
        """
        slippage = EGX_SLIPPAGE_LOW_LIQ if low_liquidity else EGX_SLIPPAGE_NORMAL

        if action == "BUY":
            exec_price = close_price * (1.0 + slippage)
        else:
            exec_price = close_price * (1.0 - slippage)

        total_commission = shares * exec_price * EGX_TOTAL_COST_SIDE
        return exec_price, total_commission

    # =========================================================================
    # EGX30 CSV Benchmark Loader
    # =========================================================================

    def _load_egx30_csv(self, csv_path: str) -> bool:
        """
        Load EGX 30 index data from a local CSV file.

        Expected format (Investing.com export):
          "Date","Price","Open","High","Low","Vol.","Change %"
          "09/04/2024","30,998.19",...

        Dates are MM/DD/YYYY; prices are comma-formatted strings.
        Populates self._bm_data_map {YYYY-MM-DD: float} and sets
        self.benchmark_start_price to the earliest entry in the file.

        Returns True on success, False on any parse failure.
        """
        try:
            import csv as _csv
            data: Dict[str, float] = {}
            with open(csv_path, newline="", encoding="utf-8-sig") as f:
                reader = _csv.DictReader(f)
                for row in reader:
                    raw_date = row.get("Date", "").strip().strip('"')
                    raw_price = row.get("Price", "").strip().strip('"').replace(",", "")
                    if not raw_date or not raw_price:
                        continue
                    try:
                        dt = datetime.strptime(raw_date, "%m/%d/%Y")
                        price = float(raw_price)
                        data[dt.strftime("%Y-%m-%d")] = price
                    except (ValueError, TypeError):
                        continue
            if not data:
                logger.warning("EGX30 CSV parsed but contained no valid rows.")
                return False
            self._bm_data_map = data
            # benchmark_start_price = price on earliest date in the map
            self.benchmark_start_price = data[min(data.keys())]
            logger.info(
                f"EGX30 CSV loaded: {len(data)} points, "
                f"range {min(data.keys())} → {max(data.keys())}, "
                f"start price: {self.benchmark_start_price:,.2f}"
            )
            return True
        except Exception as e:
            logger.warning(f"EGX30 CSV load failed ({e}).")
            return False

    # =========================================================================
    # Metrics
    # =========================================================================

    def _calculate_metrics_for_split(self, split: str) -> Dict:
        """
        Same as _calculate_metrics() but restricted to records tagged with
        the given split label ('train', 'test', or 'full').
        """
        daily = [r for r in self.daily_history if r.get("split") == split]
        trades = [t for t in self.trade_history if t.get("split") == split]

        if not daily:
            return {"Note": f"No data for split '{split}'"}

        df = pd.DataFrame(daily)
        df["returns"] = df["portfolio_value"].pct_change()

        start_value = daily[0]["portfolio_value"]
        end_value   = daily[-1]["portfolio_value"]
        total_return = (end_value - start_value) / start_value

        winning_trades = len([t for t in trades if t.get("realized_pnl", 0) > 0])
        total_closed   = len([t for t in trades if t["action"] == "SELL"])
        win_rate = winning_trades / total_closed if total_closed > 0 else 0.0

        df["cummax"] = df["portfolio_value"].cummax()
        df["drawdown"] = (df["portfolio_value"] - df["cummax"]) / df["cummax"]
        max_drawdown = df["drawdown"].min() if not df.empty else 0.0

        mean_ret = df["returns"].mean()
        std_ret  = df["returns"].std()
        sharpe = (mean_ret * 252 - 0.05) / (std_ret * np.sqrt(252)) if std_ret > 0 else 0.0

        n_days = len(df)
        ann_return = (1 + total_return) ** (252 / max(n_days, 1)) - 1
        calmar = ann_return / abs(max_drawdown) if max_drawdown != 0 else 0.0

        total_commissions = sum(t.get("commission", 0.0) for t in trades)

        outcome_wins    = sum(1 for t in trades if t.get("trade_result") == "WIN")
        outcome_losses  = sum(1 for t in trades if t.get("trade_result") == "LOSS")
        outcome_neutral = sum(1 for t in trades if t.get("trade_result") == "NEUTRAL")
        outcome_pending = sum(1 for t in trades if t.get("trade_result") == "PENDING")
        evaluated = outcome_wins + outcome_losses + outcome_neutral
        hit_rate  = f"{outcome_wins / evaluated:.2%}" if evaluated else "N/A"

        return {
            "Period":             f"{daily[0]['date']} → {daily[-1]['date']}",
            "Total Return":       f"{total_return:.2%}",
            "Win Rate":           f"{win_rate:.2%}",
            "Max Drawdown":       f"{max_drawdown:.2%}",
            "Sharpe Ratio":       f"{sharpe:.2f}",
            "Calmar Ratio":       f"{calmar:.2f}",
            "Total Trades":       len(trades),
            "Total Commissions":  f"{total_commissions:,.2f} EGP",
            "Outcome WIN":        outcome_wins,
            "Outcome LOSS":       outcome_losses,
            "Outcome NEUTRAL":    outcome_neutral,
            "Outcome PENDING":    outcome_pending,
            "Hit Rate (fwd)":     hit_rate,
            "End Portfolio":      f"{end_value:,.2f} EGP",
        }

    def _calculate_metrics(self) -> Dict:
        """Calculate all performance metrics including Phase 1 additions."""
        if not self.daily_history:
            return {}

        df = pd.DataFrame(self.daily_history)
        df["returns"] = df["portfolio_value"].pct_change()

        total_return = (self.portfolio_value - self.initial_capital) / self.initial_capital

        # Win rate
        winning_trades = len([t for t in self.trade_history if t.get("realized_pnl", 0) > 0])
        total_closed = len([t for t in self.trade_history if t["action"] == "SELL"])
        win_rate = winning_trades / total_closed if total_closed > 0 else 0.0

        # Max Drawdown
        df["cummax"] = df["portfolio_value"].cummax()
        df["drawdown"] = (df["portfolio_value"] - df["cummax"]) / df["cummax"]
        max_drawdown = df["drawdown"].min() if not df.empty else 0.0

        # Sharpe Ratio (annualized, 5% EGP risk-free rate proxy)
        mean_ret = df["returns"].mean()
        std_ret = df["returns"].std()
        sharpe = (mean_ret * 252 - 0.05) / (std_ret * np.sqrt(252)) if std_ret > 0 else 0.0

        # Calmar Ratio (annualized return / max drawdown magnitude)
        n_days = len(df)
        ann_return = (1 + total_return) ** (252 / max(n_days, 1)) - 1
        calmar = ann_return / abs(max_drawdown) if max_drawdown != 0 else 0.0

        # Benchmark comparison — Alpha
        benchmark_return = 0.0
        if self.benchmark_history and self.benchmark_start_price:
            last_bm_price = self.benchmark_history[-1]["price"]
            benchmark_return = (
                (last_bm_price - self.benchmark_start_price) / self.benchmark_start_price
            )
        alpha = total_return - benchmark_return

        # Buy-and-hold benchmark (same ticker)
        buyhold_return = 0.0
        if self.buyhold_start_price and self.buyhold_history:
            last_bh_price = self.buyhold_history[-1]["price"]
            buyhold_return = (last_bh_price - self.buyhold_start_price) / self.buyhold_start_price
        # Strategy alpha over buy-and-hold
        strategy_alpha = total_return - buyhold_return

        # Total commissions paid across all trades
        total_commissions = sum(t.get("commission", 0.0) for t in self.trade_history)

        return {
            "Total Return":       f"{total_return:.2%}",
            "Benchmark Return":   f"{benchmark_return:.2%}",
            "Alpha":              f"{alpha:.2%}",
            "Buy&Hold Return":    f"{buyhold_return:.2%}",
            "Strategy Alpha":     f"{strategy_alpha:.2%}",
            "Win Rate":           f"{win_rate:.2%}",
            "Max Drawdown":       f"{max_drawdown:.2%}",
            "Sharpe Ratio":       f"{sharpe:.2f}",
            "Calmar Ratio":       f"{calmar:.2f}",
            "Total Trades":       len(self.trade_history),
            "Total Commissions":  f"{total_commissions:,.2f} EGP",
            "Final Portfolio":    f"{self.portfolio_value:,.2f} EGP",
        }

    # =========================================================================
    # Trade Execution
    # =========================================================================

    def execute_trade(
        self,
        date: str,
        ticker: str,
        decision: str,
        close_price: float,
        execution_plan: dict,
        confidence: float,
        reasoning: str,
        low_liquidity: bool = False,
    ):
        """
        Execute portfolio changes based on the agent decision.

        Phase 1 additions vs original:
        - Slippage applied to execution price (BUY pays more, SELL receives less)
        - Commission (brokerage + stamp duty + FRA) deducted from each trade
        - SELL proceeds are NOT added to cash immediately — queued for T+2 settlement
        - BUY cash check uses only settled cash (self.cash), not pending proceeds
        """
        if close_price <= 0:
            return

        action = "HOLD"
        shares_to_transact = 0
        realized_pnl = 0.0
        exec_price = close_price  # will be overwritten in BUY/SELL branches
        commission = 0.0

        pos = self.positions.get(ticker, {"shares": 0, "avg_cost": 0.0})

        if "BUY" in decision and "VETO" not in decision:
            # Start from the trader's suggested position size
            target_shares = execution_plan.get("position_sizing", {}).get("target_shares", 0)
            if target_shares <= 0:
                # Fallback: allocate 10% of portfolio
                target_shares = int((self.portfolio_value * 0.10) / close_price)

            # Scale position by confidence (min 20% of target, max 100%).
            # confidence=0 or None means no scaling — use full position size.
            if confidence is not None and confidence > 0:
                confidence_scalar = max(0.20, min(1.0, confidence))
                original_target = target_shares
                target_shares = max(1, int(target_shares * confidence_scalar))
                if target_shares != original_target:
                    logger.info(
                        f"[CONFIDENCE SIZING] {original_target} → {target_shares} shares "
                        f"(confidence={confidence:.2f}, scalar={confidence_scalar:.2f})"
                    )

            exec_price, commission = self._apply_execution_costs(
                "BUY", target_shares, close_price, low_liquidity
            )
            total_cost = target_shares * exec_price + commission

            # Clamp to 90% of settled cash (T+2: pending proceeds not yet available).
            # Reserving 10% as a cash buffer ensures dry powder for opportunistic
            # re-entries and covers unexpected costs without going fully illiquid.
            max_allocatable = self.cash * 0.90
            if total_cost > max_allocatable:
                # Recalculate how many shares we can afford within the 90% cap
                target_shares = int(
                    max_allocatable / (close_price * (1 + EGX_SLIPPAGE_NORMAL + EGX_TOTAL_COST_SIDE))
                )
                if target_shares > 0:
                    exec_price, commission = self._apply_execution_costs(
                        "BUY", target_shares, close_price, low_liquidity
                    )
                    total_cost = target_shares * exec_price + commission

            if target_shares > 0 and total_cost <= max_allocatable:
                prev_total = pos["shares"] * pos["avg_cost"]
                pos["shares"] += target_shares
                pos["avg_cost"] = (prev_total + target_shares * exec_price) / pos["shares"]
                self.cash -= total_cost
                self.positions[ticker] = pos
                action = "BUY"
                shares_to_transact = target_shares

        elif "SELL" in decision and pos["shares"] > 0:
            shares_to_transact = pos["shares"]
            exec_price, commission = self._apply_execution_costs(
                "SELL", shares_to_transact, close_price, low_liquidity
            )
            gross_proceeds = shares_to_transact * exec_price
            net_proceeds = gross_proceeds - commission

            realized_pnl = net_proceeds - (shares_to_transact * pos["avg_cost"])

            # T+2: do NOT add to self.cash yet — schedule for settlement
            settle_date = self._get_settlement_date(date)
            self.pending_cash_settlements.append((settle_date, net_proceeds))
            logger.info(
                f"[T+2 QUEUED] {net_proceeds:,.2f} EGP to settle on {settle_date}"
            )

            self.positions[ticker] = {"shares": 0, "avg_cost": 0.0}
            action = "SELL"

        if action != "HOLD":
            _split = (
                "train" if (getattr(self, "_train_end_date", None) and date <= self._train_end_date)
                else "test" if getattr(self, "_train_end_date", None)
                else "full"
            )
            trade_record = {
                "date":          date,
                "ticker":        ticker,
                "action":        action,
                "split":         _split,
                "shares":        shares_to_transact,
                "close_price":   close_price,
                "exec_price":    round(exec_price, 4),
                "value":         round(shares_to_transact * exec_price, 2),
                "commission":    round(commission, 2),
                "realized_pnl":  round(realized_pnl, 2),
                "confidence":    confidence,
                "reasoning":     reasoning,
            }
            self.trade_history.append(trade_record)
            logger.info(
                f"[TRADE] {action} {shares_to_transact}x {ticker} "
                f"@ {exec_price:,.2f} EGP (close: {close_price:,.2f}, "
                f"commission: {commission:,.2f}) | Realized PnL: {realized_pnl:,.2f}"
            )

    # =========================================================================
    # Backtest Loop
    # =========================================================================

    def run_backtest(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        interval_days: int = 5,
        analysts: list = None,
        cooldown: int = 5,
        train_end_date: Optional[str] = None,
    ):
        """
        Run the backtest loop for a specific ticker.
        interval_days: calendar days between each agent evaluation.
        cooldown: seconds to sleep between evaluations (rate limit guard).
        train_end_date: optional split date (YYYY-MM-DD). Records on or before
            this date are labelled split='train'; later records are split='test'.
            If None, all records are labelled split='full'.
        """
        if analysts is None:
            analysts = ["market", "fundamentals", "news", "social"]

        self._train_end_date = train_end_date  # stored for tagging inside the loop

        split_info = (
            f" | Train ≤ {train_end_date} / Test > {train_end_date}"
            if train_end_date else ""
        )
        logger.info(
            f"\nStarting backtest: {ticker} | {start_date} → {end_date} | "
            f"Capital: {self.initial_capital:,.2f} EGP | Analysts: {analysts}"
            f"{split_info}"
        )

        # Track whether we successfully evaluated at least one date with price data.
        # If not, we still emit a report with a clear error so the dashboard doesn't
        # look "stuck" or empty for tickers without historical coverage.
        self._run_error: Optional[str] = None
        evaluated_dates = 0

        # ---- Fetch benchmark data upfront ----
        if self.benchmark_ticker:
            logger.info(f"Fetching benchmark: {self.benchmark_ticker}")
            # Try local EGX30 CSV first (avoids unreliable yfinance ^EGX30 feed)
            _csv_candidates = [
                os.path.join(os.path.dirname(__file__), "..", "EGX 30 Historical Data.csv"),
                os.path.join(os.path.dirname(__file__), "..", "egx30.csv"),
            ]
            _csv_loaded = False
            for _csv_path in _csv_candidates:
                _csv_path = os.path.abspath(_csv_path)
                if os.path.exists(_csv_path):
                    _csv_loaded = self._load_egx30_csv(_csv_path)
                    if _csv_loaded:
                        # Override start price: use price at/near backtest start_date
                        # so benchmark return is measured over the same window
                        _window_dates = sorted(
                            d for d in self._bm_data_map if d >= start_date
                        )
                        if _window_dates:
                            self.benchmark_start_price = self._bm_data_map[_window_dates[0]]
                            logger.info(
                                f"Benchmark start price set to {_window_dates[0]}: "
                                f"{self.benchmark_start_price:,.2f}"
                            )
                        break
            if not _csv_loaded:
                # Fall back to yfinance/gateway
                try:
                    bm_raw = self.gateway.fetch_stock_data(
                        self.benchmark_ticker, start_date=start_date, end_date=end_date
                    )
                    if bm_raw.get("data"):
                        self.benchmark_start_price = bm_raw["data"][0].get("close")
                        self._bm_data_map = {
                            row["date"]: row["close"]
                            for row in bm_raw["data"]
                            if "date" in row and "close" in row
                        }
                        logger.info(
                            f"Benchmark loaded via gateway: {len(self._bm_data_map)} points, "
                            f"start price: {self.benchmark_start_price}"
                        )
                    else:
                        logger.warning("No benchmark data returned. Disabling benchmark.")
                        self.benchmark_ticker = None
                except Exception as e:
                    logger.warning(f"Benchmark fetch failed ({e}). Proceeding without benchmark.")
                    self.benchmark_ticker = None

        # ---- Initialize agent graph ----
        logger.info(f"Initializing Agent Graph (analysts: {analysts})...")
        graph = TradingAgentsGraph(selected_analysts=analysts, debug=False)

        # Wrap LLMs with InstrumentedLLM so per-call token counts and latency
        # are captured without affecting the graph's compiled closures.
        # _rebuild_graph re-compiles the graph with instrumented LLMs baked in.
        _call_log: List[LLMCallLog] = []
        graph.graph = _rebuild_graph(graph, {}, _call_log)
        logger.info("Agent Graph instrumented for per-date LLM tracking.")

        # ---- Build list of evaluation dates ----
        # EGX trades Sunday–Thursday. Advance by interval_days and snap forward
        # to the nearest EGX trading day if the landed date falls on Fri or Sat.
        current_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        test_dates = []
        while current_dt <= end_dt:
            # Snap to next EGX trading day if needed (Fri→Sun, Sat→Sun)
            while not self._is_egx_trading_day(current_dt):
                current_dt += timedelta(days=1)
            if current_dt <= end_dt:
                test_dates.append(current_dt.strftime("%Y-%m-%d"))
            current_dt += timedelta(days=interval_days)

        import time
        for i, date in enumerate(test_dates):
            logger.info(f"\n{'='*64}")
            logger.info(f"[{date}]  Evaluating {ticker}  ({i+1}/{len(test_dates)})")

            # ---- Release any T+2 proceeds that have now settled ----
            self._settle_pending_cash(date)

            if i > 0 and cooldown > 0:
                logger.info(f"Cooling off {cooldown}s (rate limit)...")
                time.sleep(cooldown)

            # ---- Fetch price data (1-year lookback for SMA200) ----
            fetch_start = (
                datetime.strptime(date, "%Y-%m-%d") - timedelta(days=252)
            ).strftime("%Y-%m-%d")
            stock_data = self.gateway.fetch_stock_data(
                ticker, start_date=fetch_start, end_date=date
            )

            if not stock_data.get("data"):
                logger.warning(f"No price data for {date}. Skipping.")
                continue

            evaluated_dates += 1

            current_price = stock_data["data"][-1].get("close", 0.0)
            low_liquidity = stock_data.get("low_liquidity", False)

            # ---- Track buy-and-hold benchmark (same ticker) ----
            if self.buyhold_start_price is None:
                self.buyhold_start_price = current_price
            buyhold_value = self.initial_capital * (current_price / self.buyhold_start_price)
            self.buyhold_history.append({
                "date": date,
                "price": current_price,
                "value": buyhold_value,
            })

            # ---- Circuit breaker check ----
            circuit_halted = self._check_circuit_breaker(ticker, current_price)
            self.prev_prices[ticker] = current_price  # store for next iteration

            # ---- Mark portfolio to market ----
            position_value = sum(
                pdata["shares"] * (current_price if t == ticker else 0.0)
                for t, pdata in self.positions.items()
            )
            # self.cash only holds SETTLED funds; pending T+2 proceeds are excluded
            self.portfolio_value = self.cash + position_value

            pending_total = sum(amt for _, amt in self.pending_cash_settlements)
            _split = (
                "train" if (self._train_end_date and date <= self._train_end_date)
                else "test" if self._train_end_date
                else "full"
            )
            self.daily_history.append({
                "date":            date,
                "portfolio_value": self.portfolio_value,
                "split":           _split,
            })

            # ---- Track benchmark value ----
            if self.benchmark_ticker and self._bm_data_map and self.benchmark_start_price:
                bm_price = self._bm_data_map.get(date)
                if bm_price is None:
                    # Use nearest earlier date in the benchmark series
                    earlier = [d for d in sorted(self._bm_data_map) if d <= date]
                    bm_price = self._bm_data_map[earlier[-1]] if earlier else None
                if bm_price:
                    bm_value = self.initial_capital * (bm_price / self.benchmark_start_price)
                    self.benchmark_history.append({
                        "date": date, "price": bm_price, "value": bm_value
                    })

            logger.info(
                f"Price: {current_price:,.2f} | Portfolio: {self.portfolio_value:,.2f} | "
                f"Cash (settled): {self.cash:,.2f} | Pending T+2: {pending_total:,.2f}"
            )

            if circuit_halted:
                logger.info(f"Circuit breaker active — skipping agent run for {date}.")
                continue

            # ---- Run Agent Graph ----
            try:
                if not hasattr(graph.propagator, "_original_create_initial_state"):
                    graph.propagator._original_create_initial_state = (
                        graph.propagator.create_initial_state
                    )

                def patched_create_initial_state(company_name, trade_date):
                    base_state = graph.propagator._original_create_initial_state(
                        company_name, trade_date
                    )
                    # Build current position context so the LLM knows whether
                    # the portfolio already holds shares or is sitting in cash.
                    pos = self.positions.get(ticker, {})
                    if pos.get("shares", 0) > 0:
                        current_position = {
                            "shares": pos["shares"],
                            "avg_cost": round(pos["avg_cost"], 2),
                            "market_value": round(pos["shares"] * current_price, 2),
                            "unrealised_pnl": round(
                                pos["shares"] * (current_price - pos["avg_cost"]), 2
                            ),
                        }
                    else:
                        current_position = {}   # no position — portfolio is cash only
                    base_state.update({
                        "portfolio_value":  self.portfolio_value,
                        "current_price":    current_price,
                        "avg_daily_volume": stock_data.get("avg_daily_volume", 0),
                        "low_liquidity":    low_liquidity,
                        "volume_missing":   stock_data.get("volume_missing", False),
                        "current_position": current_position,
                    })
                    return base_state

                graph.propagator.create_initial_state = patched_create_initial_state

                _call_log.clear()
                _t0 = time.perf_counter()
                final_state, _ = graph.propagate(ticker, date)
                _trade_time_s = time.perf_counter() - _t0
                _llm_calls = len(_call_log)
                _reasoning_score = _compute_reasoning_score(final_state)

                # ---- Extract decision from final state ----
                decision = "HOLD"  # Safe default
                raw_decision = final_state.get("final_trade_decision", "HOLD")
                execution_plan = final_state.get("execution_plan", {})
                risk_assessment = final_state.get("risk_assessment", {})
                if isinstance(execution_plan, dict) and "execution_plan" in execution_plan:
                    execution_plan = execution_plan["execution_plan"]

                # Priority 1: If risk assessment vetoed, force HOLD
                if isinstance(risk_assessment, dict) and not risk_assessment.get("approved", True):
                    decision = "HOLD"
                    logger.info("Risk VETO active — forcing HOLD")

                # Priority 2: Parse clean action from risk manager output
                elif isinstance(raw_decision, str) and raw_decision.strip().upper() in ("BUY", "SELL", "HOLD"):
                    decision = raw_decision.strip().upper()

                # Priority 3: Search for JSON block in free text
                elif isinstance(raw_decision, str):
                    json_match = re.search(
                        r'\{[^{}]*"action"\s*:\s*"(BUY|SELL|HOLD)"[^{}]*\}',
                        raw_decision,
                        re.IGNORECASE,
                    )
                    if json_match:
                        decision = json_match.group(1).upper()
                    elif "BUY" in raw_decision.upper() and "VETO" not in raw_decision.upper():
                        decision = "BUY"
                    elif "SELL" in raw_decision.upper() and "VETO" not in raw_decision.upper():
                        decision = "SELL"

                # Priority 4: Fall back to trader's execution plan decision
                if decision == "HOLD" and isinstance(risk_assessment, dict) and risk_assessment.get("approved", False):
                    plan_decision = ""
                    if isinstance(execution_plan, dict):
                        plan_decision = (execution_plan.get("decision", "") or "").upper()
                    if plan_decision in ("BUY", "SELL"):
                        logger.info(f"Risk approved, using trader plan decision: {plan_decision}")
                        decision = plan_decision

                confidence = final_state.get("confidence_scores", {}).get("overall", 50.0)
                if confidence is None:
                    confidence = 50.0
                reasoning = (
                    risk_assessment.get("veto_explanation")
                    or "Standard execution"
                )

                logger.info(f"Agent Decision: {decision} | Confidence: {confidence:.2f}")

                # ---- Decision Audit Log ----
                audit_entry = {
                    "date": date,
                    "price": current_price,
                    "raw_decision": str(raw_decision)[:200],  # Truncate long LLM text
                    "parsed_decision": decision,
                    "risk_approved": risk_assessment.get("approved") if isinstance(risk_assessment, dict) else None,
                    "risk_violations": risk_assessment.get("total_violations", 0) if isinstance(risk_assessment, dict) else 0,
                    "critical_violations": risk_assessment.get("critical_violations", 0) if isinstance(risk_assessment, dict) else 0,
                    "execution_plan_decision": execution_plan.get("decision", "N/A") if isinstance(execution_plan, dict) else "N/A",
                    "confidence": confidence,
                    "llm_calls": _llm_calls,
                    "trade_time_s": round(_trade_time_s, 1),
                    "reasoning_score": _reasoning_score,
                }
                logger.info(f"[AUDIT] {json.dumps(audit_entry, default=str)}")
                self.audit_log.append(audit_entry)

                self.execute_trade(
                    date, ticker, decision, current_price,
                    execution_plan, confidence, reasoning, low_liquidity,
                )

                # Resolve the action actually taken (execute_trade doesn't return it)
                action = "HOLD"
                if self.trade_history and self.trade_history[-1]["date"] == date:
                    action = self.trade_history[-1]["action"]

                # ---- Reflect on this decision for future learning ----
                try:
                    if action != "HOLD" and len(self.trade_history) > 0:
                        last_trade = self.trade_history[-1]
                        returns_losses = {
                            "action": last_trade["action"],
                            "realized_pnl": last_trade.get("realized_pnl", 0),
                            "confidence": last_trade.get("confidence", 0),
                            "date": date,
                        }
                    else:
                        # Even HOLD decisions get reflected on
                        price_change_pct = 0.0
                        if i > 0 and len(self.daily_history) >= 2:
                            prev_val = self.daily_history[-2]["portfolio_value"]
                            curr_val = self.daily_history[-1]["portfolio_value"]
                            price_change_pct = (curr_val - prev_val) / prev_val if prev_val > 0 else 0
                        returns_losses = {
                            "action": "HOLD",
                            "missed_opportunity": price_change_pct,
                            "date": date,
                        }

                    graph.reflect_and_remember(returns_losses)
                    logger.info(f"[REFLECTION] Agents reflected on {decision} decision")
                except Exception as e:
                    logger.warning(f"Reflection failed (non-critical): {e}")

            except Exception as e:
                import traceback
                logger.error(f"Agent Graph failed on {date}: {e}\n{traceback.format_exc()}")

        # ---- End of loop: force-settle all remaining T+2 proceeds ----
        logger.info("\nForce-settling remaining T+2 proceeds at end of backtest...")
        for _, amount in self.pending_cash_settlements:
            self.cash += amount
        self.pending_cash_settlements = []

        # ---- Post-hoc trade outcome evaluation ----
        # For each trade, look forward in the price series to judge whether
        # the decision was profitable. Uses future data *intentionally* —
        # this is evaluation, not signal generation.
        self._evaluate_trade_outcomes(ticker, end_date)

        logger.info("\n" + "=" * 64)
        logger.info("BACKTEST COMPLETE")
        logger.info("=" * 64)
        metrics = self._calculate_metrics()
        for k, v in metrics.items():
            logger.info(f"  {k:<26}: {v}")

        # ---- Trade outcome summary ----
        if any("forward_return_20d" in t for t in self.trade_history):
            wins = sum(1 for t in self.trade_history if t.get("trade_result") == "WIN")
            losses = sum(1 for t in self.trade_history if t.get("trade_result") == "LOSS")
            neutral = sum(1 for t in self.trade_history if t.get("trade_result") == "NEUTRAL")
            pending = sum(1 for t in self.trade_history if t.get("trade_result") == "PENDING")
            total_eval = wins + losses + neutral
            hit_rate = wins / total_eval if total_eval > 0 else 0.0
            logger.info("")
            logger.info("  Trade Outcomes (forward-looking):")
            logger.info(f"    {'WIN':<24}: {wins}")
            logger.info(f"    {'LOSS':<24}: {losses}")
            logger.info(f"    {'NEUTRAL':<24}: {neutral}")
            logger.info(f"    {'PENDING (no fwd data)':<24}: {pending}")
            logger.info(f"    {'Hit Rate':<24}: {hit_rate:.2%}")

        # If we couldn't evaluate even a single date, emit a minimal baseline series
        # so the dashboard can still render (and show a clear error message).
        if evaluated_dates == 0:
            self._run_error = (
                f"No historical price data returned for {ticker} "
                f"in window {start_date} → {end_date}."
            )
            if not self.daily_history:
                self.daily_history = [
                    {"date": start_date, "portfolio_value": float(self.initial_capital), "split": "full"},
                    {"date": end_date, "portfolio_value": float(self.initial_capital), "split": "full"},
                ]
            logger.warning(self._run_error)

        self.save_results(ticker)

    # =========================================================================
    # Post-hoc Trade Outcome Evaluation
    # =========================================================================

    def _evaluate_trade_outcomes(
        self,
        ticker: str,
        backtest_end_date: str,
        horizons_days: Tuple[int, int] = (5, 20),
        neutral_threshold: float = 0.01,
    ):
        """
        For each trade in self.trade_history, fetch the forward price series
        and annotate:
          - price_at_+Nd  (close N trading days after the trade)
          - forward_return_Nd  (signed return for the trade action)
          - trade_result  (WIN / LOSS / NEUTRAL / PENDING)

        A BUY is WIN if price rose by more than neutral_threshold within the
        primary horizon (second value in horizons_days). A SELL is WIN if
        price fell by more than neutral_threshold (i.e., we avoided a drop).

        Forward data is fetched up to today, so trades too close to the
        backtest end get PENDING for missing horizons.
        """
        if not self.trade_history:
            return

        primary_horizon = horizons_days[-1]

        # Fetch a single extended price window covering every trade + the
        # longest horizon, so we only hit the data gateway once.
        first_trade_date = min(t["date"] for t in self.trade_history)
        # Pad by 2x primary horizon in calendar days to safely cover weekends
        pad_days = primary_horizon * 2 + 10
        fetch_end = (
            datetime.strptime(backtest_end_date, "%Y-%m-%d") + timedelta(days=pad_days)
        ).strftime("%Y-%m-%d")
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if fetch_end > today:
            fetch_end = today

        try:
            price_data = self.gateway.fetch_stock_data(
                ticker, start_date=first_trade_date, end_date=fetch_end
            )
        except Exception as e:
            logger.warning(f"Forward-price fetch failed for outcome eval: {e}")
            return

        rows = price_data.get("data") if isinstance(price_data, dict) else None
        if not rows:
            logger.warning("No forward-price data returned; skipping outcome eval.")
            return

        # Index: ordered list of (date_str, close)
        series = [
            (r["date"], r["close"])
            for r in rows
            if "date" in r and "close" in r and r.get("close")
        ]
        series.sort(key=lambda x: x[0])
        date_to_idx = {d: i for i, (d, _) in enumerate(series)}

        def _price_n_bars_ahead(trade_date: str, n_bars: int) -> Optional[float]:
            """Return close price n trading bars after trade_date, or None."""
            # Walk forward to the first bar >= trade_date
            idx = date_to_idx.get(trade_date)
            if idx is None:
                # Find earliest bar > trade_date
                candidates = [i for i, (d, _) in enumerate(series) if d > trade_date]
                if not candidates:
                    return None
                idx = candidates[0] - 1  # treat as "just before" trade
            target_idx = idx + n_bars
            if target_idx >= len(series):
                return None
            return series[target_idx][1]

        for trade in self.trade_history:
            entry_price = trade.get("exec_price") or trade.get("close_price") or 0.0
            if entry_price <= 0:
                trade["trade_result"] = "PENDING"
                continue

            for h in horizons_days:
                fwd_price = _price_n_bars_ahead(trade["date"], h)
                if fwd_price is None:
                    trade[f"price_at_+{h}d"] = None
                    trade[f"forward_return_{h}d"] = None
                else:
                    raw_ret = (fwd_price - entry_price) / entry_price
                    # For SELL, a price DROP is favorable — flip the sign
                    signed = raw_ret if trade["action"] == "BUY" else -raw_ret
                    trade[f"price_at_+{h}d"] = round(fwd_price, 4)
                    trade[f"forward_return_{h}d"] = round(signed, 6)

            primary_ret = trade.get(f"forward_return_{primary_horizon}d")
            if primary_ret is None:
                trade["trade_result"] = "PENDING"
            elif primary_ret > neutral_threshold:
                trade["trade_result"] = "WIN"
            elif primary_ret < -neutral_threshold:
                trade["trade_result"] = "LOSS"
            else:
                trade["trade_result"] = "NEUTRAL"

            logger.info(
                f"[OUTCOME] {trade['date']} {trade['action']} → "
                f"{trade['trade_result']} "
                f"(+{primary_horizon}d return: "
                f"{primary_ret if primary_ret is not None else 'N/A'})"
            )

    # =========================================================================
    # Results Persistence
    # =========================================================================

    def save_results(self, session_name: str):
        """Save trade log, daily portfolio history, and metrics to CSV and JSON."""
        # Always write results to project-root/backtest_results so API + dashboard
        # can discover them reliably (FastAPI background tasks may have a different CWD).
        results_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backtest_results"))
        os.makedirs(results_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if self.trade_history:
            df = pd.DataFrame(self.trade_history)
            csv_path = os.path.join(results_dir, f"trades_{session_name}_{timestamp}.csv")
            df.to_csv(csv_path, index=False)
            logger.info(f"Saved trade log → {csv_path}")

            # Save per-split CSVs when a train/test split was used
            train_end = getattr(self, "_train_end_date", None)
            if train_end:
                for split_label in ("train", "test"):
                    split_df = df[df["split"] == split_label]
                    if not split_df.empty:
                        sp_path = (
                            os.path.join(
                                results_dir,
                                f"trades_{session_name}_{split_label}_{timestamp}.csv",
                            )
                        )
                        split_df.to_csv(sp_path, index=False)
                        logger.info(f"Saved {split_label} trade log → {sp_path}")

        metrics = self._calculate_metrics()

        # Per-split metrics (train / test)
        train_end = getattr(self, "_train_end_date", None)
        split_metrics = {}
        if train_end:
            for split_label in ("train", "test"):
                sm = self._calculate_metrics_for_split(split_label)
                split_metrics[split_label] = sm
                logger.info(f"\n  ── {split_label.upper()} SPLIT ──")
                for k, v in sm.items():
                    logger.info(f"    {k:<26}: {v}")

        # Per-trade outcome summary (post-hoc forward-looking eval)
        outcome_summary = {}
        if any("trade_result" in t for t in self.trade_history):
            wins = sum(1 for t in self.trade_history if t.get("trade_result") == "WIN")
            losses = sum(1 for t in self.trade_history if t.get("trade_result") == "LOSS")
            neutral = sum(1 for t in self.trade_history if t.get("trade_result") == "NEUTRAL")
            pending = sum(1 for t in self.trade_history if t.get("trade_result") == "PENDING")
            evaluated = wins + losses + neutral
            outcome_summary = {
                "wins":     wins,
                "losses":   losses,
                "neutral":  neutral,
                "pending":  pending,
                "hit_rate": f"{(wins / evaluated):.2%}" if evaluated else "N/A",
            }

        # ---- Pipeline efficiency summary (aggregated from audit_log) ----
        efficiency_summary = {}
        audit_with_llm = [a for a in self.audit_log if "llm_calls" in a]
        if audit_with_llm:
            llm_call_vals = [a["llm_calls"] for a in audit_with_llm]
            time_vals = [a["trade_time_s"] for a in audit_with_llm]
            score_vals = [a["reasoning_score"] for a in audit_with_llm]
            efficiency_summary = {
                "dates_evaluated": len(audit_with_llm),
                "total_llm_calls": sum(llm_call_vals),
                "avg_llm_calls_per_date": round(sum(llm_call_vals) / len(llm_call_vals), 1),
                "avg_trade_time_s": round(sum(time_vals) / len(time_vals), 1),
                "total_trade_time_s": round(sum(time_vals), 1),
                "avg_reasoning_score": round(sum(score_vals) / len(score_vals), 2),
                "reasoning_score_dist": {
                    str(s): sum(1 for v in score_vals if v == s)
                    for s in sorted(set(score_vals))
                },
            }

        report = {
            "session":              session_name,
            "error":                getattr(self, "_run_error", None),
            "metrics":              metrics,
            "split_metrics":        split_metrics,
            "trade_outcomes":       outcome_summary,
            "pipeline_efficiency":  efficiency_summary,
            "trades":               self.trade_history,
            "daily_portfolio":      self.daily_history,
            "benchmark_history":    self.benchmark_history,
            "buyhold_history":      self.buyhold_history,
            "audit_log":            self.audit_log,
            "cost_model": {
                "commission_per_side": f"{EGX_TOTAL_COST_SIDE:.4%}",
                "slippage_normal":     f"{EGX_SLIPPAGE_NORMAL:.3%}",
                "slippage_low_liq":    f"{EGX_SLIPPAGE_LOW_LIQ:.3%}",
                "settlement":          f"T+{EGX_SETTLEMENT_DAYS}",
                "circuit_breaker":     f"±{EGX_CIRCUIT_BREAKER:.0%}",
            },
        }
        json_path = os.path.join(results_dir, f"report_{session_name}_{timestamp}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=4)
        logger.info(f"Saved full report  → {json_path}")


# =============================================================================
# CLI Entry Point
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run EGX trading backtester")
    parser.add_argument("--ticker",     type=str,   default="COMI.CA",
                        help="Ticker symbol to backtest")
    parser.add_argument("--start",      type=str,   default="2022-01-01",
                        help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end",        type=str,   default="2023-06-01",
                        help="End date (YYYY-MM-DD)")
    parser.add_argument("--train-end",  type=str,   default="2022-12-31",
                        help="Last date of the training split (YYYY-MM-DD). "
                             "Records after this date belong to the test split. "
                             "Pass 'none' to disable train/test splitting.")
    parser.add_argument("--interval",   type=int,   default=7,
                        help="Calendar days between agent evaluations")
    parser.add_argument("--capital",    type=float, default=1_000_000.0,
                        help="Starting capital in EGP")
    parser.add_argument("--analysts",   type=str,   default="market,fundamentals,news,social",
                        help="Comma-separated list of analysts to enable")
    parser.add_argument("--benchmark",  type=str,   default="^EGX30",
                        help="Benchmark ticker for Alpha calculation (pass 'none' to disable)")
    parser.add_argument("--cooldown",   type=int,   default=5,
                        help="Seconds to wait between evaluations (rate limit)")

    args = parser.parse_args()

    analysts_list = args.analysts.split(",")
    benchmark  = None if args.benchmark.lower()  == "none" else args.benchmark
    train_end  = None if args.train_end.lower()  == "none" else args.train_end

    engine = BacktestingEngine(
        initial_capital=args.capital,
        benchmark_ticker=benchmark,
    )
    engine.run_backtest(
        args.ticker, args.start, args.end,
        interval_days=args.interval,
        analysts=analysts_list,
        cooldown=args.cooldown,
        train_end_date=train_end,
    )
