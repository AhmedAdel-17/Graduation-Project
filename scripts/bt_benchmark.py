"""
EGX Classical Technical Benchmark — Backtrader Implementation
=============================================================
Mirrors the indicator logic of the Technical Analysis Agent (market_analyst.py)
using pure rule-based signals — zero LLM calls.

Indicators (identical to EGX_DAILY_INDICATORS in market_analyst.py):
  - RSI(14):              oversold / overbought momentum
  - MACD(12/26/9):        trend momentum (matches market_analyst parse logic)
  - Bollinger Bands(20):  volatility band positioning
  - SMA(50):              medium-term trend filter

Entry  (BUY):  ≥ 2 of 3 bullish signals fire on the same bar
Exit   (SELL): ≥ 2 of 3 bearish signals fire on the same bar

EGX constraints applied (mirrors backtester.py cost model):
  - Long-only: no short selling
  - Commission: ~0.189% per side (brokerage + stamp duty + FRA)
  - Slippage:   0.1% per side
  - Position sizing: 10% of current portfolio per trade
"""

import os
import sys
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List

import pandas as pd
import numpy as np
import yfinance as yf
import backtrader as bt
import backtrader.analyzers as btanalyzers

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("BTBenchmark")

# Cost model — mirrors constants in backtester.py
EGX_COMMISSION   = 0.00189   # ~0.189% per side (brokerage + stamp duty + FRA)
EGX_SLIPPAGE     = 0.001     # 0.1% per side (normal liquidity)
MAX_POSITION_PCT = 0.10      # 10% of portfolio per trade


# =============================================================================
# Backtrader Strategy — Rule-Based Replication of Technical Analysis Agent
# =============================================================================

class EGXTechnicalStrategy(bt.Strategy):
    """
    Signal logic mirrors parse_technical_signals() and determine_trend_direction()
    from tradingagents/agents/analysts/market_analyst.py:

    Bullish signals (entry conditions):
      1. RSI < rsi_oversold  (35 — conservative vs the agent's 30 for EGX volatility)
      2. MACD > 0            (above zero line → bullish momentum)
      3. Close < lower BB    (oversold vs volatility band → mean-reversion entry)

    Bearish signals (exit conditions):
      1. RSI > rsi_overbought  (65 — conservative vs 70)
      2. MACD < 0              (below zero line → bearish momentum)
      3. Close > upper BB      (overbought vs volatility band → exit)

    Trade triggers:
      BUY  when ≥ min_signals (2) bullish conditions hold simultaneously
      SELL when ≥ min_signals (2) bearish conditions hold simultaneously
    """

    params = (
        ("rsi_period",     14),
        ("rsi_oversold",   35),    # matches analyst's oversold threshold logic
        ("rsi_overbought", 65),    # matches analyst's overbought threshold logic
        ("macd_fast",      12),
        ("macd_slow",      26),
        ("macd_signal_p",  9),
        ("bb_period",      20),
        ("bb_devfactor",   2.0),
        ("sma_period",     50),
        ("min_signals",    2),     # 2-of-3 required to trigger
        ("position_pct",   0.10),  # 10% portfolio per position
        ("printlog",       False),
    )

    def __init__(self):
        self.dataclose = self.datas[0].close

        # Indicators — identical parameters to EGX_DAILY_INDICATORS
        self.rsi  = bt.indicators.RSI(period=self.p.rsi_period)
        self.macd = bt.indicators.MACD(
            period_me1=self.p.macd_fast,
            period_me2=self.p.macd_slow,
            period_signal=self.p.macd_signal_p,
        )
        self.bb = bt.indicators.BollingerBands(
            period=self.p.bb_period,
            devfactor=self.p.bb_devfactor,
        )
        self.sma50 = bt.indicators.SMA(period=self.p.sma_period)

        # Order and trade state
        self.order     = None
        self.buy_price = None
        self.trade_log: List[Dict] = []

    def log(self, txt: str):
        if self.p.printlog:
            dt = self.datas[0].datetime.date(0)
            logger.info(f"[{dt}] {txt}")

    def notify_order(self, order):
        if order.status in (order.Submitted, order.Accepted):
            return

        if order.status == order.Completed:
            dt = str(self.datas[0].datetime.date(0))
            if order.isbuy():
                self.buy_price = order.executed.price
                self.log(
                    f"BUY  @ {order.executed.price:.2f} | "
                    f"Size: {order.executed.size:.0f} | "
                    f"Cost: {order.executed.value:.2f} | "
                    f"Comm: {order.executed.comm:.2f}"
                )
                self.trade_log.append({
                    "date":   dt,
                    "action": "BUY",
                    "price":  round(order.executed.price, 4),
                    "shares": int(order.executed.size),
                    "comm":   round(order.executed.comm, 2),
                    "pnl":    0.0,
                })
            elif order.issell():
                shares = int(abs(order.executed.size))
                pnl = (order.executed.price - (self.buy_price or 0.0)) * shares
                self.log(
                    f"SELL @ {order.executed.price:.2f} | "
                    f"Size: {shares} | "
                    f"PnL:  {pnl:.2f}"
                )
                self.trade_log.append({
                    "date":   dt,
                    "action": "SELL",
                    "price":  round(order.executed.price, 4),
                    "shares": shares,
                    "comm":   round(order.executed.comm, 2),
                    "pnl":    round(pnl, 2),
                })

        elif order.status in (order.Canceled, order.Margin, order.Rejected):
            self.log(f"Order not filled: status={order.status}")

        self.order = None

    def _count_bullish(self) -> int:
        """Count currently active bullish conditions (max 3)."""
        score = 0
        if self.rsi[0] < self.p.rsi_oversold:
            score += 1
        if self.macd.macd[0] > 0:
            score += 1
        if self.dataclose[0] < self.bb.lines.bot[0]:
            score += 1
        return score

    def _count_bearish(self) -> int:
        """Count currently active bearish conditions (max 3)."""
        score = 0
        if self.rsi[0] > self.p.rsi_overbought:
            score += 1
        if self.macd.macd[0] < 0:
            score += 1
        if self.dataclose[0] > self.bb.lines.top[0]:
            score += 1
        return score

    def next(self):
        if self.order:
            return  # Pending order — wait

        if not self.position:
            # No open position — look for BUY
            if self._count_bullish() >= self.p.min_signals:
                size = int(
                    (self.broker.getvalue() * self.p.position_pct) / self.dataclose[0]
                )
                if size > 0:
                    self.log(
                        f"BUY SIGNAL ({self._count_bullish()}/3) | "
                        f"RSI={self.rsi[0]:.1f} | MACD={self.macd.macd[0]:.4f} | "
                        f"Size={size}"
                    )
                    self.order = self.buy(size=size)
        else:
            # In a position — look for SELL
            if self._count_bearish() >= self.p.min_signals:
                self.log(
                    f"SELL SIGNAL ({self._count_bearish()}/3) | "
                    f"RSI={self.rsi[0]:.1f} | MACD={self.macd.macd[0]:.4f}"
                )
                self.order = self.sell(size=self.position.size)

    def stop(self):
        # Force-close any open position at end of test period
        if self.position:
            self.log("End of backtest — force-closing open position.")
            self.close()


# =============================================================================
# Data Loading
# =============================================================================

def _load_ohlcv(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    Download daily OHLCV via yfinance.
    Handles the MultiIndex column structure introduced in yfinance ≥0.2.
    Returns a clean DataFrame with lowercase column names suitable for
    bt.feeds.PandasData.
    """
    logger.info(f"Downloading {ticker} ({start_date} → {end_date})...")
    df = yf.download(ticker, start=start_date, end=end_date, auto_adjust=True, progress=False)

    if df.empty:
        raise ValueError(f"No price data for {ticker} between {start_date} and {end_date}")

    # yfinance ≥0.2 returns MultiIndex columns for single-ticker downloads
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)

    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)

    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing OHLCV columns in yfinance response: {missing}")

    return df[["open", "high", "low", "close", "volume"]]


# =============================================================================
# Benchmark Runner
# =============================================================================

def run_bt_benchmark(
    ticker: str,
    start_date: str,
    end_date: str,
    initial_capital: float = 1_000_000.0,
    save_results: bool = True,
) -> Dict:
    """
    Run the EGX classical technical strategy via Backtrader.

    The lookback window is extended by 90 days before start_date so that
    indicators (especially SMA50) have enough warm-up data before the
    first trade window opens.

    Args:
        ticker:          EGX ticker (e.g., "COMI.CA")
        start_date:      Backtest start date — no trades fire before this date
        end_date:        Backtest end date
        initial_capital: Starting capital in EGP
        save_results:    Write JSON report to backtest_results/

    Returns:
        Dict with keys:
          "metrics" — same metric names as BacktestingEngine._calculate_metrics()
          "trades"  — list of trade records from EGXTechnicalStrategy.trade_log
    """
    logger.info(
        f"\nBacktrader benchmark: {ticker} | "
        f"{start_date} → {end_date} | Capital: {initial_capital:,.2f} EGP"
    )

    # Extend lookback for indicator warmup (SMA50 needs at least 50 bars)
    warmup_start = (
        datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=90)
    ).strftime("%Y-%m-%d")

    df = _load_ohlcv(ticker, warmup_start, end_date)

    data_feed = bt.feeds.PandasData(
        dataname=df,
        fromdate=datetime.strptime(start_date, "%Y-%m-%d"),
        todate=datetime.strptime(end_date, "%Y-%m-%d"),
    )

    # ---- Cerebro setup ----
    cerebro = bt.Cerebro()
    cerebro.adddata(data_feed, name=ticker)
    cerebro.addstrategy(EGXTechnicalStrategy)

    # Broker — apply the same cost model as backtester.py
    cerebro.broker.setcash(initial_capital)
    cerebro.broker.setcommission(commission=EGX_COMMISSION)
    cerebro.broker.set_slippage_perc(perc=EGX_SLIPPAGE, slip_open=False)

    # Analyzers
    cerebro.addanalyzer(
        btanalyzers.SharpeRatio, _name="sharpe",
        timeframe=bt.TimeFrame.Days, annualize=True, riskfreerate=0.05,
    )
    cerebro.addanalyzer(btanalyzers.DrawDown,      _name="drawdown")
    cerebro.addanalyzer(btanalyzers.TradeAnalyzer, _name="trades")
    cerebro.addanalyzer(
        btanalyzers.TimeReturn, _name="timereturn",
        timeframe=bt.TimeFrame.Days,
    )

    # ---- Run ----
    results  = cerebro.run()
    strat    = results[0]

    final_value  = cerebro.broker.getvalue()
    total_return = (final_value - initial_capital) / initial_capital

    # ---- Parse analyzer outputs ----
    sharpe_raw = strat.analyzers.sharpe.get_analysis()
    dd_raw     = strat.analyzers.drawdown.get_analysis()
    trade_raw  = strat.analyzers.trades.get_analysis()
    tr_raw     = strat.analyzers.timereturn.get_analysis()

    sharpe_ratio = sharpe_raw.get("sharperatio") or 0.0

    # DrawDown analyzer returns percentage, convert to ratio
    max_dd_pct   = dd_raw.get("max", {}).get("drawdown", 0.0)
    max_drawdown = -(max_dd_pct / 100.0)

    total_trades = trade_raw.get("total", {}).get("closed", 0)
    won_trades   = trade_raw.get("won",   {}).get("total",  0)
    win_rate     = won_trades / total_trades if total_trades > 0 else 0.0

    # Build daily equity curve from TimeReturn analyzer
    daily_portfolio: List[Dict] = []
    pv = initial_capital
    for dt in sorted(tr_raw.keys()):
        pv *= (1 + tr_raw[dt])
        date_str = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]
        daily_portfolio.append({"date": date_str, "portfolio_value": round(pv, 2)})

    # Calmar Ratio
    n_days     = len(daily_portfolio)
    ann_return = (1 + total_return) ** (252 / max(n_days, 1)) - 1
    calmar     = ann_return / abs(max_drawdown) if max_drawdown != 0 else 0.0

    # Total commissions from trade log
    total_commissions = sum(t.get("comm", 0.0) for t in strat.trade_log)

    metrics = {
        "Total Return":      f"{total_return:.2%}",
        "Win Rate":          f"{win_rate:.2%}",
        "Max Drawdown":      f"{max_drawdown:.2%}",
        "Sharpe Ratio":      f"{sharpe_ratio:.2f}",
        "Calmar Ratio":      f"{calmar:.2f}",
        "Total Trades":      total_trades,
        "Total Commissions": f"{total_commissions:,.2f} EGP",
        "Final Portfolio":   f"{final_value:,.2f} EGP",
    }

    logger.info("\n" + "=" * 64)
    logger.info("BACKTRADER BENCHMARK COMPLETE")
    logger.info("=" * 64)
    for k, v in metrics.items():
        logger.info(f"  {k:<26}: {v}")

    if save_results:
        os.makedirs("backtest_results", exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        report = {
            "session":         ticker,
            "engine":          "backtrader_classical_technical",
            "strategy":        "EGXTechnicalStrategy",
            "metrics":         metrics,
            "trades":          strat.trade_log,
            "daily_portfolio": daily_portfolio,
            "cost_model": {
                "commission_per_side": f"{EGX_COMMISSION:.4%}",
                "slippage":            f"{EGX_SLIPPAGE:.3%}",
            },
        }
        path = f"backtest_results/bt_report_{ticker}_{ts}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=4)
        logger.info(f"Saved Backtrader report → {path}")

    return {"metrics": metrics, "trades": strat.trade_log, "daily_portfolio": daily_portfolio}


# =============================================================================
# CLI Entry Point
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run EGX classical technical benchmark via Backtrader"
    )
    parser.add_argument("--ticker",  type=str,   default="COMI.CA",    help="EGX ticker")
    parser.add_argument("--start",   type=str,   default="2023-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end",     type=str,   default="2023-06-01", help="End date (YYYY-MM-DD)")
    parser.add_argument("--capital", type=float, default=1_000_000.0,  help="Starting capital (EGP)")
    parser.add_argument("--log",     action="store_true",              help="Print per-bar trade log")
    args = parser.parse_args()

    run_bt_benchmark(args.ticker, args.start, args.end, args.capital)
