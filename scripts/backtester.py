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
from typing import Any, Dict, List, Optional, Tuple
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


def _summarize_thesis(thesis: dict) -> Optional[dict]:
    """Compact a bull/bear thesis dict to the fields the dashboard renders.

    The raw thesis is multi-KB JSON with full prose. We keep only the
    operational summary so each audit row stays under a few hundred bytes.
    Returns None when the thesis is empty / not a dict.
    """
    if not isinstance(thesis, dict) or not thesis:
        return None
    out: Dict[str, Any] = {
        "conviction_level": thesis.get("conviction_level"),
        "time_horizon": (thesis.get("time_horizon") or {}).get("primary"),
        "alignment_score": (thesis.get("signal_summary") or {}).get("alignment_score"),
    }
    catalysts = thesis.get("key_catalysts") or thesis.get("key_risks") or []
    if isinstance(catalysts, list):
        out["catalysts"] = [str(c)[:160] for c in catalysts[:5]]
    invalidation = thesis.get("invalidation_conditions") or []
    if isinstance(invalidation, list):
        out["invalidation"] = [str(c)[:160] for c in invalidation[:5]]
    upside = thesis.get("upside_scenario") or {}
    if isinstance(upside, dict):
        out["base_case_upside_pct"] = upside.get("base_case_upside_pct")
        out["downside_risk_pct"] = upside.get("downside_risk_pct")
    # Drop None values so the JSON stays tight.
    return {k: v for k, v in out.items() if v not in (None, [], {})} or None


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
        self.config = config
        self.gateway = DataGateway(config)

        # MEMORY.md §C3 — risk-free rate is config-driven, not hardcoded 0.05.
        # default_config["egx_risk_free_rate"] = 0.275 (CBE policy proxy).
        # Falls back to walkforward.default_risk_free_rate() (0.24) if config
        # is somehow missing the key, so Sharpe is never computed against 0.05.
        try:
            from tradingagents.rl.walkforward import default_risk_free_rate as _default_rfr
            _fallback_rfr = _default_rfr()
        except Exception:
            _fallback_rfr = 0.24
        self.risk_free_rate: float = float(config.get("egx_risk_free_rate", _fallback_rfr))

        # ── Stage C: RL meta-policy (opt-in, fail-closed) ──────────────────
        # Default is the identity policy (size_multiplier = 1.0), which gives
        # bit-for-bit identical behavior to pre-RL main when the feature flag
        # is unset OR the model file is missing OR loading fails. The policy
        # can only shrink size; the deterministic risk veto still wins below.
        from tradingagents.rl.policy import RLSizingPolicy, identity_policy

        self.rl_policy = identity_policy()
        self.rl_policy_enabled = bool(config.get("rl_meta_policy_enabled", False))
        rl_model_path = (config.get("rl_model_path") or "").strip()
        if self.rl_policy_enabled and rl_model_path:
            try:
                self.rl_policy = RLSizingPolicy.load(rl_model_path)
                logger.info(
                    "[RL] meta-policy loaded from %s (fp=%s)",
                    rl_model_path,
                    self.rl_policy.model_fingerprint.get("weights_sha256_16", "?"),
                )
            except Exception as exc:
                logger.warning(
                    "[RL] failed to load meta-policy from %s; falling back to "
                    "identity (size_multiplier=1.0): %s",
                    rl_model_path, exc,
                )
                self.rl_policy = identity_policy()
        elif self.rl_policy_enabled and not rl_model_path:
            logger.warning(
                "[RL] rl_meta_policy_enabled=True but rl_model_path is empty; "
                "using identity policy (no-op)"
            )

    # =========================================================================
    # Stage C — RL meta-policy helpers
    # =========================================================================

    def _portfolio_drawdown_so_far_pct(self) -> float:
        """Peak-to-current drawdown from the daily portfolio history.

        Returns 0.0 when there is no history yet. Capped at 1.0.
        """
        if not self.daily_history:
            return 0.0
        peak = max(
            (row.get("portfolio_value", 0.0) for row in self.daily_history),
            default=self.portfolio_value,
        )
        if peak <= 0:
            return 0.0
        dd = (peak - self.portfolio_value) / peak
        return max(0.0, min(1.0, float(dd)))

    def _settled_cash_pct(self) -> float:
        """Settled cash / portfolio value, clipped to ``[0, 1]``."""
        if self.portfolio_value <= 0:
            return 0.0
        return max(0.0, min(1.0, float(self.cash) / float(self.portfolio_value)))

    def _build_rl_portfolio_ctx(self) -> Dict[str, float]:
        return {
            "portfolio_drawdown_so_far_pct": self._portfolio_drawdown_so_far_pct(),
            "settled_cash_pct": self._settled_cash_pct(),
        }

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
    # Data Fetch (retry-wrapped) + Resume Checkpointing
    # =========================================================================

    def _fetch_stock_data_with_retry(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        max_attempts: int = 3,
        base_wait_s: float = 1.0,
        max_wait_s: float = 8.0,
    ) -> Dict:
        """Wrap gateway.fetch_stock_data with retry+backoff.

        Returns the dict on success (possibly with empty ``data``), or ``{}``
        on irrecoverable failure. The caller treats both as a date skip so a
        transient network error never aborts a multi-hour run.
        """
        last_exc: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                return self.gateway.fetch_stock_data(
                    ticker, start_date=start_date, end_date=end_date
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < max_attempts:
                    wait_s = min(base_wait_s * (2 ** (attempt - 1)), max_wait_s)
                    logger.warning(
                        "fetch_stock_data attempt %d/%d for %s failed: %s "
                        "(retry in %.1fs)",
                        attempt, max_attempts, ticker, exc, wait_s,
                    )
                    import time as _t
                    _t.sleep(wait_s)
        logger.error(
            "fetch_stock_data exhausted %d attempts for %s [%s → %s]: %s",
            max_attempts, ticker, start_date, end_date, last_exc,
        )
        return {}

    def _partial_path(self, ticker: str) -> str:
        """Disk path for the per-ticker resumable checkpoint."""
        d = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "backtest_results")
        )
        os.makedirs(d, exist_ok=True)
        safe = ticker.replace("/", "_").replace("\\", "_")
        return os.path.join(d, f"partial_{safe}.json")

    def _maybe_write_partial(self, ticker: str) -> None:
        """Atomically persist per-date progress so --resume can pick up.

        Best-effort: never raises into the loop. The .partial.json holds
        daily_history, trade_history, audit_log, positions, cash, and
        cumulative settlement queue so the next run can recover state.
        """
        try:
            payload = {
                "ticker": ticker,
                "initial_capital": self.initial_capital,
                "cash": self.cash,
                "portfolio_value": self.portfolio_value,
                "positions": self.positions,
                "pending_cash_settlements": [
                    list(t) for t in self.pending_cash_settlements
                ],
                "prev_prices": self.prev_prices,
                "trade_history": self.trade_history,
                "daily_history": self.daily_history,
                "buyhold_history": self.buyhold_history,
                "benchmark_history": self.benchmark_history,
                "audit_log": self.audit_log,
                "completed_dates": [r.get("date") for r in self.daily_history],
            }
            path = self._partial_path(ticker)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, default=str)
            os.replace(tmp, path)
        except Exception as exc:
            logger.debug("partial write skipped (%s): %s", ticker, exc)

    def _load_partial_if_resume(
        self, ticker: str, resume: bool
    ) -> set:
        """Restore engine state from a previous partial. Returns the set of
        already-completed dates so the main loop can skip them.

        When ``resume=False`` (default), no-op — returns an empty set.
        """
        if not resume:
            return set()
        path = self._partial_path(ticker)
        if not os.path.exists(path):
            logger.info("No partial checkpoint for %s — starting fresh.", ticker)
            return set()
        try:
            with open(path, "r", encoding="utf-8") as f:
                p = json.load(f)
            self.cash = float(p.get("cash", self.cash))
            self.portfolio_value = float(p.get("portfolio_value", self.portfolio_value))
            self.positions = p.get("positions", {}) or {}
            self.pending_cash_settlements = [
                tuple(x) for x in p.get("pending_cash_settlements", [])
            ]
            self.prev_prices = p.get("prev_prices", {}) or {}
            self.trade_history = p.get("trade_history", []) or []
            self.daily_history = p.get("daily_history", []) or []
            self.buyhold_history = p.get("buyhold_history", []) or []
            self.benchmark_history = p.get("benchmark_history", []) or []
            self.audit_log = p.get("audit_log", []) or []
            seen = set(p.get("completed_dates", []))
            logger.info(
                "Resumed %s from partial: %d completed dates, cash=%.2f, "
                "positions=%d",
                ticker, len(seen), self.cash, len(self.positions),
            )
            return seen
        except Exception as exc:
            logger.warning("Partial load failed for %s: %s — starting fresh.", ticker, exc)
            return set()

    # =========================================================================
    # Benchmark window alignment (MEMORY §C4)
    # =========================================================================

    def _align_benchmark_to_strategy(self) -> Dict:
        """Compute a structured EGX30 benchmark block on the exact date
        intersection between the strategy's daily_history and the
        benchmark price map.

        Returns a dict suitable for the report's ``benchmark`` key. Empty
        dict when no benchmark data is available.
        """
        if not (self.benchmark_ticker and self._bm_data_map and self.daily_history):
            return {}

        strat_dates = [r["date"] for r in self.daily_history if r.get("date")]
        if not strat_dates:
            return {}

        intersected = [d for d in strat_dates if d in self._bm_data_map]
        coverage = len(intersected) / max(len(strat_dates), 1)
        if coverage < 0.80:
            logger.warning(
                "Benchmark coverage %.0f%% < 80%% (%d / %d strategy dates "
                "have a matching EGX30 close). Reporting alpha but flagging "
                "low coverage.",
                coverage * 100.0, len(intersected), len(strat_dates),
            )
        if not intersected:
            return {
                "name": "EGX30",
                "n_aligned_days": 0,
                "note": "no_overlap_with_benchmark_series",
            }

        first_date = intersected[0]
        last_date = intersected[-1]
        bm_first = self._bm_data_map.get(first_date) or 0.0
        bm_last = self._bm_data_map.get(last_date) or 0.0
        if bm_first <= 0:
            return {"name": "EGX30", "n_aligned_days": len(intersected),
                    "note": "benchmark_first_price_invalid"}

        bm_total_return = (bm_last - bm_first) / bm_first

        # Daily returns on the aligned intersection
        bm_series = [self._bm_data_map[d] for d in intersected if self._bm_data_map[d] > 0]
        strat_by_date = {r["date"]: r["portfolio_value"] for r in self.daily_history}
        strat_series = [strat_by_date[d] for d in intersected if d in strat_by_date]

        tracking_error_pct: Optional[float] = None
        if len(bm_series) > 2 and len(strat_series) == len(bm_series):
            bm_rets = np.array(bm_series[1:]) / np.array(bm_series[:-1]) - 1.0
            st_rets = np.array(strat_series[1:]) / np.array(strat_series[:-1]) - 1.0
            diff = st_rets - bm_rets
            if diff.std(ddof=1) > 0:
                tracking_error_pct = float(diff.std(ddof=1) * np.sqrt(252) * 100.0)

        n_days = len(intersected)
        ann_return_pct: Optional[float] = None
        if n_days > 1:
            ann_return_pct = float(((1.0 + bm_total_return) ** (252 / n_days) - 1.0) * 100.0)

        strat_total_return = (
            (self.portfolio_value - self.initial_capital) / self.initial_capital
            if self.initial_capital > 0 else 0.0
        )
        alpha_pct = float((strat_total_return - bm_total_return) * 100.0)

        return {
            "name": "EGX30",
            "source": "local_csv",
            "first_aligned_date": first_date,
            "last_aligned_date": last_date,
            "n_aligned_days": n_days,
            "coverage_pct": round(coverage * 100.0, 2),
            "total_return_pct": round(bm_total_return * 100.0, 4),
            "annualized_return_pct": round(ann_return_pct, 4) if ann_return_pct is not None else None,
            "alpha_pct": round(alpha_pct, 4),
            "tracking_error_pct": round(tracking_error_pct, 4) if tracking_error_pct is not None else None,
        }

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
        sharpe = (mean_ret * 252 - self.risk_free_rate) / (std_ret * np.sqrt(252)) if std_ret > 0 else 0.0

        n_days = len(df)
        ann_return = (1 + total_return) ** (252 / max(n_days, 1)) - 1
        calmar = ann_return / abs(max_drawdown) if max_drawdown != 0 else 0.0

        total_commissions = sum(t.get("commission", 0.0) for t in trades)

        return {
            "Period":             f"{daily[0]['date']} → {daily[-1]['date']}",
            "Total Return":       f"{total_return:.2%}",
            "Win Rate":           f"{win_rate:.2%}",
            "Max Drawdown":       f"{max_drawdown:.2%}",
            "Sharpe Ratio":       f"{sharpe:.2f}",
            "Calmar Ratio":       f"{calmar:.2f}",
            "Total Trades":       len(trades),
            "Total Commissions":  f"{total_commissions:,.2f} EGP",
            "Risk-Free Rate Used": f"{self.risk_free_rate:.4f}",
            "End Portfolio":      f"{end_value:,.2f} EGP",
        }

    def _calculate_metrics(self) -> Dict:
        """Calculate all performance metrics including Phase 1 additions."""
        if not self.daily_history:
            return {}

        df = pd.DataFrame(self.daily_history)
        df["returns"] = df["portfolio_value"].pct_change()

        total_return = (self.portfolio_value - self.initial_capital) / self.initial_capital

        # MEMORY §C1 — closed-trade win rate uses ONLY realized PnL on SELL
        # trades (no look-ahead). Wilson 95% CI is reported alongside so the
        # number is interpretable on small samples. Helpers are reused from
        # tradingagents.rl.walkforward (consumer of the same backtest JSONs).
        from tradingagents.rl.walkforward import _closed_trade_winrate, _wilson_ci
        wr_pair = _closed_trade_winrate(self.trade_history)
        wr_val, wins, total_closed = wr_pair
        win_rate = wr_val if wr_val is not None else 0.0
        if total_closed > 0:
            wr_ci_lo, wr_ci_hi = _wilson_ci(wins, total_closed)
        else:
            wr_ci_lo, wr_ci_hi = None, None

        # Max Drawdown
        df["cummax"] = df["portfolio_value"].cummax()
        df["drawdown"] = (df["portfolio_value"] - df["cummax"]) / df["cummax"]
        max_drawdown = df["drawdown"].min() if not df.empty else 0.0

        # Sharpe Ratio (annualized, config-driven EGP risk-free rate — MEMORY §C3)
        mean_ret = df["returns"].mean()
        std_ret = df["returns"].std()
        sharpe = (mean_ret * 252 - self.risk_free_rate) / (std_ret * np.sqrt(252)) if std_ret > 0 else 0.0

        # Calmar Ratio (annualized return / max drawdown magnitude)
        n_days = len(df)
        ann_return = (1 + total_return) ** (252 / max(n_days, 1)) - 1
        calmar = ann_return / abs(max_drawdown) if max_drawdown != 0 else 0.0

        # Benchmark comparison — Alpha.
        # Only report Benchmark Return / Alpha when we actually have benchmark
        # prices. The old behaviour wrote 0.00% even when the EGX30 CSV was
        # missing, which made the dashboard show "EGX30 flat 0%" instead of
        # "data unavailable" and gave a misleading apples-to-apples comparison.
        has_benchmark_data = bool(
            self.benchmark_history
            and self.benchmark_start_price
            and self.benchmark_start_price > 0
        )
        benchmark_return: Optional[float] = None
        alpha: Optional[float] = None
        if has_benchmark_data:
            last_bm_price = self.benchmark_history[-1]["price"]
            benchmark_return = (
                (last_bm_price - self.benchmark_start_price) / self.benchmark_start_price
            )
            alpha = total_return - benchmark_return

        # Buy-and-hold benchmark (same ticker). Available almost always
        # because it only needs the agent's own price series.
        has_buyhold_data = bool(
            self.buyhold_start_price
            and self.buyhold_start_price > 0
            and self.buyhold_history
        )
        buyhold_return: Optional[float] = None
        strategy_alpha: Optional[float] = None
        if has_buyhold_data:
            last_bh_price = self.buyhold_history[-1]["price"]
            buyhold_return = (last_bh_price - self.buyhold_start_price) / self.buyhold_start_price
            strategy_alpha = total_return - buyhold_return

        # Total commissions paid across all trades
        total_commissions = sum(t.get("commission", 0.0) for t in self.trade_history)

        ci_str = (
            f" [95% CI {wr_ci_lo:.2%}–{wr_ci_hi:.2%}]"
            if (wr_ci_lo is not None and wr_ci_hi is not None) else ""
        )
        out: Dict[str, Any] = {
            "Total Return":         f"{total_return:.2%}",
            "Win Rate":             f"{win_rate:.2%}{ci_str}",
            "Win Rate CI Lo":       wr_ci_lo,
            "Win Rate CI Hi":       wr_ci_hi,
            "Closed Trades":        total_closed,
            "Max Drawdown":         f"{max_drawdown:.2%}",
            "Sharpe Ratio":         f"{sharpe:.2f}",
            "Calmar Ratio":         f"{calmar:.2f}",
            "Total Trades":         len(self.trade_history),
            "Total Commissions":    f"{total_commissions:,.2f} EGP",
            "Risk-Free Rate Used":  f"{self.risk_free_rate:.4f}",
            "Final Portfolio":      f"{self.portfolio_value:,.2f} EGP",
        }
        if has_benchmark_data:
            out["Benchmark Return"] = f"{benchmark_return:.2%}"
            out["Alpha"] = f"{alpha:.2%}"
        if has_buyhold_data:
            out["Buy&Hold Return"] = f"{buyhold_return:.2%}"
            out["Strategy Alpha"] = f"{strategy_alpha:.2%}"
        return out

    # =========================================================================
    # Decision resolution (LLM judge + trader plan + deterministic veto)
    # =========================================================================

    @staticmethod
    def _resolve_decision(
        final_state: Dict,
        execution_plan: Dict,
    ) -> Tuple[str, str]:
        """Reduce the final state to a single ``BUY`` / ``SELL`` / ``HOLD`` action.

        Priority order (each step is short-circuiting):

          1. **Deterministic veto** — if the Risk Scorer set
             ``final_state['risk_action'] == 'VETO'`` OR the risk-manager dict
             reports ``approved=False`` OR ``critical_violations > 0``, force
             HOLD. This is the safety floor — regulatory and capital-limit
             constraints. Never bypassed.

          2. **LLM judge bare action** — if ``final_trade_decision`` is the
             literal string ``BUY``/``SELL``/``HOLD``, take it as the LLM
             judge's verdict.

          3. **LLM judge JSON / free-text** — extract ``{"action": "..."}``
             from the raw judge output, or fall back to BUY/SELL keyword
             scanning (excluding ``VETO`` context).

          4. **Trust-the-trader fallback** — when the LLM judge returns HOLD
             *and* there is no deterministic veto *and* the trader's
             ``execution_plan.decision`` is BUY or SELL, prefer the trader's
             plan. This handles the common case where the LLM Risk Judge
             rubber-stamps "HOLD" as a default text response even though the
             deterministic gate cleared the trade.

        Returns ``(decision, path)`` where ``path`` is one of
        ``{"deterministic_veto", "judge_bare", "judge_json", "judge_freetext",
        "trader_fallback", "default_hold"}`` for audit-log readability.
        """
        risk_assessment = final_state.get("risk_assessment", {}) or {}
        risk_action = str(final_state.get("risk_action", "") or "").upper()

        critical_v = 0
        approved_explicit_false = False
        if isinstance(risk_assessment, dict):
            critical_v = int(risk_assessment.get("critical_violations", 0) or 0)
            # Only treat `approved=False` as a veto when the key is present and
            # explicitly False. Missing keys / None must not block trades.
            if risk_assessment.get("approved", None) is False:
                approved_explicit_false = True

        deterministic_veto = (
            risk_action == "VETO"
            or approved_explicit_false
            or critical_v > 0
        )
        if deterministic_veto:
            return "HOLD", "deterministic_veto"

        raw_decision = final_state.get("final_trade_decision", "HOLD")

        # Step 2 — bare BUY/SELL/HOLD string
        if isinstance(raw_decision, str) and raw_decision.strip().upper() in ("BUY", "SELL", "HOLD"):
            judge_decision = raw_decision.strip().upper()
            judge_path = "judge_bare"
        elif isinstance(raw_decision, str):
            # Step 3 — JSON action, then free-text BUY/SELL scan
            judge_decision = "HOLD"
            judge_path = "judge_freetext"
            json_match = re.search(
                r'\{[^{}]*"action"\s*:\s*"(BUY|SELL|HOLD)"[^{}]*\}',
                raw_decision,
                re.IGNORECASE,
            )
            if json_match:
                judge_decision = json_match.group(1).upper()
                judge_path = "judge_json"
            elif "BUY" in raw_decision.upper() and "VETO" not in raw_decision.upper():
                judge_decision = "BUY"
            elif "SELL" in raw_decision.upper() and "VETO" not in raw_decision.upper():
                judge_decision = "SELL"
        else:
            judge_decision = "HOLD"
            judge_path = "default_hold"

        # Step 4 — when the LLM judge gave HOLD but the trader had a real
        # plan and the deterministic gate is green, trust the trader. This
        # closes the silent-downgrade bug reported on the dashboard run where
        # trader=BUY + 0 violations + judge="HOLD" became HOLD on every date.
        if judge_decision == "HOLD":
            plan_decision = ""
            if isinstance(execution_plan, dict):
                plan_decision = (execution_plan.get("decision", "") or "").upper()
            if plan_decision in ("BUY", "SELL"):
                return plan_decision, "trader_fallback"

        return judge_decision, judge_path

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
        final_state: Optional[Dict] = None,
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

        # ── Stage C: RL meta-policy prediction (computed once per call) ───
        # The prediction is consumed only inside the BUY branch — SELLs are
        # always full exits, HOLDs do nothing. But we compute it here so the
        # audit record always carries the meta-policy's intended size_mult
        # even for HOLD decisions (useful for offline eval).
        rl_size_mult: float = 1.0
        rl_prediction = None
        if (
            self.rl_policy_enabled
            and self.rl_policy.is_loaded
            and isinstance(final_state, dict)
        ):
            try:
                rl_prediction = self.rl_policy.predict(
                    final_state,
                    ticker=ticker,
                    trade_date=date,
                    portfolio_ctx=self._build_rl_portfolio_ctx(),
                )
                # Belt-and-braces clamp: policy already enforces this but the
                # backtester re-checks as defense in depth (Plan §3.1).
                rl_size_mult = max(0.0, min(1.0, float(rl_prediction.size_multiplier)))
            except Exception as exc:
                # Fail-closed: any error in the meta-policy reverts to identity.
                logger.warning(
                    "[RL] predict failed for %s on %s; falling back to identity: %s",
                    ticker, date, exc,
                )
                rl_size_mult = 1.0
                rl_prediction = None

        # Expose the most recent prediction so the outer loop can write the
        # audit row alongside the graph's session_id.
        self._last_rl_prediction = rl_prediction

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

            # ── Stage C: apply RL meta-policy size multiplier ───────────────
            # Identity (1.0) when the flag is off, the model is missing, or
            # the predictor errored. The multiplier can only shrink, never
            # amplify. We accept target_shares==0 as a valid "skip this BUY"
            # signal from the RL policy — the BUY branch then no-ops cleanly.
            if rl_size_mult < 1.0:
                pre_rl_target = target_shares
                target_shares = int(target_shares * rl_size_mult)
                if target_shares < pre_rl_target:
                    logger.info(
                        f"[RL SIZING] {pre_rl_target} → {target_shares} shares "
                        f"(size_mult={rl_size_mult:.4f})"
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
            # Stage C: RL audit. Always present (= 1.0 when flag off) so
            # downstream readers see a uniform schema.
            trade_record["rl_meta_policy_enabled"] = bool(self.rl_policy_enabled)
            trade_record["rl_size_multiplier"] = round(float(rl_size_mult), 4)
            # Exit plan from the trader's structured execution_plan. The
            # dashboard's expandable trade-row reveals these targets so the
            # user can see what the agent said about WHEN to sell after BUY.
            exit_plan = getattr(self, "_next_exit_plan", None)
            if isinstance(exit_plan, dict):
                trade_record["exit_plan"] = exit_plan
            if rl_prediction is not None:
                fp = rl_prediction.model_fingerprint or {}
                trade_record["rl_action_index"] = int(rl_prediction.action_index)
                trade_record["rl_model_fingerprint"] = fp.get("weights_sha256_16")
                trade_record["rl_feature_version"] = rl_prediction.feature_version
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
        resume: bool = False,
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
        # Retained so save_results() can pass them to the Postgres backtest writer.
        self._bt_ticker = ticker
        self._bt_start_date = start_date
        self._bt_end_date = end_date

        # Workstream A — resume from a previous partial if requested.
        # Returns the set of completed date strings to skip in the main loop.
        seen_dates = self._load_partial_if_resume(ticker, resume)

        # MEMORY §C1 (this PR): the old forward-return reflection batch
        # depended on _evaluate_trade_outcomes, which used look-ahead prices.
        # That function has been deleted. Reflection memory is not updated
        # from backtests anymore — production realized outcomes are the
        # only legitimate training signal for agent memory.

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
            # Project-root filenames the EGX30 CSV loader will accept. Investing.com
            # exports come in several flavours — index history, ETF history — so we
            # accept all the common ones. First match wins.
            _csv_candidates = [
                os.path.join(os.path.dirname(__file__), "..", "EGX 30 Historical Data.csv"),
                os.path.join(os.path.dirname(__file__), "..", "EGX30ETF ETF Stock Price History.csv"),
                os.path.join(os.path.dirname(__file__), "..", "EGX30 ETF Stock Price History.csv"),
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
            if date in seen_dates:
                logger.info(f"[RESUME] Skipping already-completed date {date}")
                continue

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
            stock_data = self._fetch_stock_data_with_retry(
                ticker, fetch_start, date
            )

            if not stock_data or not stock_data.get("data"):
                logger.warning(f"No price data for {date}. Skipping.")
                self.audit_log.append({
                    "date": date,
                    "decision_status": "data_fetch_failed",
                    "parsed_decision": "HOLD",
                    "confidence": None,
                })
                self._maybe_write_partial(ticker)
                continue

            # Defensive: vendor returned a non-empty list but the last row has
            # no usable close. Skip rather than crash on indexing.
            last_row = stock_data["data"][-1] if stock_data["data"] else {}
            current_price = float(last_row.get("close") or 0.0)
            if current_price <= 0:
                logger.warning(f"Invalid close price for {date}: {current_price}. Skipping.")
                self.audit_log.append({
                    "date": date,
                    "decision_status": "invalid_close_price",
                    "parsed_decision": "HOLD",
                    "confidence": None,
                })
                self._maybe_write_partial(ticker)
                continue

            evaluated_dates += 1

            low_liquidity = stock_data.get("low_liquidity", False)

            # ---- Track buy-and-hold benchmark (same ticker) ----
            if self.buyhold_start_price is None or self.buyhold_start_price <= 0:
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

            # ---- Track benchmark value (MEMORY §C4 — strict date-intersect) ----
            # The legacy "nearest earlier date" fallback drifted alpha when the
            # EGX30 CSV had a gap. Now we only record benchmark history when
            # the exact strategy date has a matching benchmark close. The
            # _align_benchmark_to_strategy() step in save_results does the
            # final intersection across the whole series.
            if (
                self.benchmark_ticker
                and self._bm_data_map
                and self.benchmark_start_price
                and self.benchmark_start_price > 0
            ):
                bm_price = self._bm_data_map.get(date)
                if bm_price and bm_price > 0:
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
                # Delegated to _resolve_decision() so the priority logic is
                # unit-testable in isolation. See:
                #   tests/test_backtester_robustness.py::test_decision_priority_*
                raw_decision = final_state.get("final_trade_decision", "HOLD")
                execution_plan = final_state.get("execution_plan", {})
                risk_assessment = final_state.get("risk_assessment", {})
                if isinstance(execution_plan, dict) and "execution_plan" in execution_plan:
                    execution_plan = execution_plan["execution_plan"]

                decision, _decision_path = self._resolve_decision(final_state, execution_plan)
                logger.info(
                    "Resolved decision: %s (path=%s, raw_judge=%r, "
                    "plan_decision=%r, risk_action=%r, critical_violations=%s)",
                    decision,
                    _decision_path,
                    str(raw_decision)[:60],
                    (execution_plan.get("decision") if isinstance(execution_plan, dict) else None),
                    final_state.get("risk_action"),
                    (risk_assessment.get("critical_violations", 0)
                     if isinstance(risk_assessment, dict) else 0),
                )

                confidence = final_state.get("confidence_scores", {}).get("overall", 50.0)
                if confidence is None:
                    confidence = 50.0

                # ---- Capture real agent text from the final state ----
                # The dashboard previously showed templated "Standard execution"
                # strings because the trade-record reasoning field never pulled
                # the actual agent output. The text is all in final_state — we
                # just need to surface it.
                investment_debate = final_state.get("investment_debate_state", {}) or {}
                risk_debate = final_state.get("risk_debate_state", {}) or {}
                # The Bull/Bear researchers write their structured theses INTO
                # investment_debate_state, not the top-level state keys (those
                # stay None — no node assigns them). Reading the top-level keys
                # made `bear_thesis_present` always False, so the dashboard
                # showed a stale "bear produced no structured thesis" warning
                # even when the bear thesis was fully populated.
                bull_thesis = (
                    investment_debate.get("bull_thesis")
                    or final_state.get("bull_thesis")
                    or {}
                )
                bear_thesis = (
                    investment_debate.get("bear_thesis")
                    or final_state.get("bear_thesis")
                    or {}
                )

                # Risk Judge's clause-by-clause Constitutional analysis lives
                # here when the structured `risk_assessment` dict isn't filled
                # (which is the common case — risk_assessment ends up `{}`).
                risk_judge_text = (
                    (risk_debate.get("judge_decision") if isinstance(risk_debate, dict) else "")
                    or (investment_debate.get("judge_decision") if isinstance(investment_debate, dict) else "")
                    or ""
                )

                # Choose the best available reasoning string for the trade
                # record. Priority: explicit veto explanation → execution_plan
                # entry-logic timing/conditions → Risk Judge first paragraph →
                # fallback boilerplate.
                def _first_paragraph(s: str, limit: int = 400) -> str:
                    if not isinstance(s, str):
                        return ""
                    s2 = s.strip()
                    if not s2:
                        return ""
                    p = s2.split("\n\n", 1)[0]
                    return p[:limit].strip()

                entry_logic = (
                    execution_plan.get("entry_logic", {})
                    if isinstance(execution_plan, dict) else {}
                )
                reasoning = (
                    (risk_assessment.get("veto_explanation") if isinstance(risk_assessment, dict) else None)
                    or entry_logic.get("timing")
                    or _first_paragraph(risk_judge_text)
                    or "Standard execution"
                )

                # The Research Manager (debate judge) ends its verdict with a
                # JSON block {"decision","confidence","rationale"}. Surface the
                # one-line rationale so HOLD dates carry an explicit "why" — a
                # HOLD is a real decision, not an absence of one.
                judge_text = (
                    investment_debate.get("judge_decision")
                    if isinstance(investment_debate, dict) else ""
                ) or ""
                judge_rationale = None
                _jm = re.search(
                    r'"rationale"\s*:\s*"([^"]+)"', judge_text
                )
                if _jm:
                    judge_rationale = _jm.group(1).strip()

                logger.info(f"Agent Decision: {decision} | Confidence: {confidence:.2f}")

                # ---- Decision Audit Log ----
                audit_entry = {
                    "date": date,
                    "price": current_price,
                    "raw_decision": str(raw_decision)[:200],  # Truncate long LLM text
                    "parsed_decision": decision,
                    "decision_path": _decision_path,
                    "risk_action": final_state.get("risk_action"),
                    "risk_approved": risk_assessment.get("approved") if isinstance(risk_assessment, dict) else None,
                    "risk_violations": risk_assessment.get("total_violations", 0) if isinstance(risk_assessment, dict) else 0,
                    "critical_violations": risk_assessment.get("critical_violations", 0) if isinstance(risk_assessment, dict) else 0,
                    "execution_plan_decision": execution_plan.get("decision", "N/A") if isinstance(execution_plan, dict) else "N/A",
                    "confidence": confidence,
                    "llm_calls": _llm_calls,
                    "trade_time_s": round(_trade_time_s, 1),
                    "reasoning_score": _reasoning_score,
                    # Real agent text — capped so the JSON stays small. The
                    # dashboard renders these directly in the Risk / Bull /
                    # Bear cards instead of the templated boilerplate.
                    "risk_judge_text": (risk_judge_text or "")[:3000] or None,
                    "bull_thesis_summary": _summarize_thesis(bull_thesis) if bull_thesis else None,
                    "bear_thesis_summary": _summarize_thesis(bear_thesis) if bear_thesis else None,
                    "bear_thesis_present": bool(bear_thesis),
                    # Per-date "why" — populated for every evaluation including
                    # HOLD dates, so the dashboard can explain a no-trade verdict
                    # instead of rendering an unexplained row of zeros.
                    "reasoning": (
                        None if str(reasoning).strip() in ("", "Standard execution", "N/A")
                        else str(reasoning)[:1500]
                    ),
                    "judge_rationale": judge_rationale,
                }
                logger.info(f"[AUDIT] {json.dumps(audit_entry, default=str)}")
                self.audit_log.append(audit_entry)

                # ---- Attach exit plan to the upcoming trade record ----
                # When the trader recommends BUY, the execution_plan carries
                # take-profit / stop-loss / time-stop. We stash a compact view
                # on the engine so execute_trade can copy it into the next
                # trade_record. SELLs reuse the same field as a passthrough.
                if isinstance(execution_plan, dict):
                    exit_logic = execution_plan.get("exit_logic", {}) or {}
                    invalidation = execution_plan.get("invalidation_triggers", []) or []
                    self._next_exit_plan = {
                        "take_profit": exit_logic.get("take_profit") or {},
                        "stop_loss": exit_logic.get("stop_loss") or {},
                        "time_stop": exit_logic.get("time_stop"),
                        "invalidation_triggers": list(invalidation)[:8],
                        "conviction": execution_plan.get("conviction"),
                    }
                else:
                    self._next_exit_plan = None

                self.execute_trade(
                    date, ticker, decision, current_price,
                    execution_plan, confidence, reasoning, low_liquidity,
                    final_state=final_state,
                )

                # ── Stage C: RL meta-policy audit row ──────────────────────
                # Append the meta-policy decision into the same agent_events
                # timeline as the graph itself. Best-effort: never crashes the
                # backtest if Postgres is unavailable or the prediction is None.
                _rl_pred = getattr(self, "_last_rl_prediction", None)
                _session_id = getattr(graph, "session_id", None)
                if _rl_pred is not None and _session_id:
                    try:
                        from tradingagents.db import audit_writer as _audit
                        _audit.write_rl_meta_event(
                            session_id=_session_id,
                            prediction=_rl_pred,
                            ticker=ticker,
                            trade_date=date,
                        )
                    except Exception as _e:
                        logger.debug(
                            "[RL] audit write skipped (%s/%s): %s", ticker, date, _e
                        )

                # Reflect Stage C audit fields into the audit_log row too so
                # the JSON report carries the same info as the trade record.
                if _rl_pred is not None:
                    fp_short = (_rl_pred.model_fingerprint or {}).get("weights_sha256_16")
                    audit_entry["rl_size_multiplier"] = round(float(_rl_pred.size_multiplier), 4)
                    audit_entry["rl_action_index"] = int(_rl_pred.action_index)
                    audit_entry["rl_model_fingerprint"] = fp_short
                else:
                    audit_entry["rl_size_multiplier"] = 1.0
                    audit_entry["rl_action_index"] = None
                    audit_entry["rl_model_fingerprint"] = None
                audit_entry["rl_meta_policy_enabled"] = bool(self.rl_policy_enabled)

            except (KeyboardInterrupt, SystemExit):
                # Don't swallow user-driven aborts. The partial JSON we wrote
                # at the end of the previous date lets `--resume` pick up.
                self._maybe_write_partial(ticker)
                raise
            except Exception as e:
                # MEMORY §A (replaces bare except). Surface the failure in the
                # audit log so a phantom HOLD doesn't masquerade as a real
                # decision. The loop continues to the next trade date.
                logger.exception("Agent Graph failed on %s for %s", date, ticker)
                err_entry = {
                    "date": date,
                    "price": current_price,
                    "decision_status": "agent_error",
                    "error_class": type(e).__name__,
                    "error_message": str(e)[:300],
                    "parsed_decision": "HOLD",
                    "confidence": None,
                }
                self.audit_log.append(err_entry)

            # Always checkpoint, even on agent_error / circuit_halted, so the
            # next --resume run skips this date and we never replay an LLM call.
            self._maybe_write_partial(ticker)

        # ---- End of loop: force-settle all remaining T+2 proceeds ----
        logger.info("\nForce-settling remaining T+2 proceeds at end of backtest...")
        for _, amount in self.pending_cash_settlements:
            self.cash += amount
        self.pending_cash_settlements = []

        # MEMORY §C1 — deleted: _evaluate_trade_outcomes (look-ahead "Hit Rate")
        # and _flush_reflection_with_forward_returns (depended on it).
        # The only honest win-rate metric is realized PnL on closed (SELL)
        # trades, with a Wilson CI for small-sample interpretability.
        # See _calculate_metrics() above.

        logger.info("\n" + "=" * 64)
        logger.info("BACKTEST COMPLETE")
        logger.info("=" * 64)
        metrics = self._calculate_metrics()
        for k, v in metrics.items():
            logger.info(f"  {k:<26}: {v}")

        # If we couldn't evaluate even a single date, emit a minimal baseline series
        # so the dashboard can still render (and show a clear error message).
        if evaluated_dates == 0 and not self.daily_history:
            self._run_error = (
                f"No historical price data returned for {ticker} "
                f"in window {start_date} → {end_date}."
            )
            self.daily_history = [
                {"date": start_date, "portfolio_value": float(self.initial_capital), "split": "full"},
                {"date": end_date, "portfolio_value": float(self.initial_capital), "split": "full"},
            ]
            logger.warning(self._run_error)

        # MEMORY §C4 + Workstream C — compute the structured benchmark block
        # on the exact strategy/benchmark date intersection.
        try:
            self._benchmark_block = self._align_benchmark_to_strategy()
        except Exception as exc:
            logger.warning("Benchmark alignment failed: %s", exc)
            self._benchmark_block = {}

        self.save_results(ticker)

        # Clean up partial checkpoint once the full run + save_results
        # succeeded — the canonical JSON now holds the same data.
        try:
            ppath = self._partial_path(ticker)
            if os.path.exists(ppath):
                os.remove(ppath)
        except Exception:
            pass

    # =========================================================================
    # (Removed) Post-hoc Reflection Batch + Trade Outcome Evaluation — §C1
    # =========================================================================
    # _flush_reflection_with_forward_returns and _evaluate_trade_outcomes were
    # both deleted in the §C1 fix: they fetched prices AFTER the backtest's
    # end_date to label trades WIN/LOSS and to feed forward returns back into
    # agent memory. Both are look-ahead by definition. Backtests no longer
    # update agent memory — production realized live trades are the only
    # legitimate training signal. Win-rate uses realized PnL on SELL trades
    # (see _calculate_metrics).
    #
    # Regression-gate tests assert these attributes no longer exist:
    #   tests/test_backtester_robustness.py::test_no_lookahead_functions

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

        # MEMORY §C1 — removed: outcome_summary used to consume the look-ahead
        # `trade_result` annotations from _evaluate_trade_outcomes. The win-rate
        # in `metrics` (with Wilson CI) is the only honest substitute.

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
            "benchmark":            getattr(self, "_benchmark_block", {}),
            "pipeline_efficiency":  efficiency_summary,
            "trades":               self.trade_history,
            "daily_portfolio":      self.daily_history,
            "benchmark_history":    self.benchmark_history,
            "buyhold_history":      self.buyhold_history,
            "audit_log":            self.audit_log,
            "risk_free_rate_used":  self.risk_free_rate,
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

        # ── Postgres persistence (best-effort) ────────────────────────────────
        # Mirrors the JSON report into backtest_runs + backtest_trades so the
        # dashboard, audit-log endpoint, and backtest_comparison view have
        # structured data to query. Errors are logged and swallowed — the
        # JSON file remains the authoritative on-disk record either way.
        try:
            import uuid
            from tradingagents.db import backtest_writer

            run_id = uuid.uuid4().hex
            ticker_for_run = getattr(self, "_bt_ticker", session_name)
            start_for_run = getattr(self, "_bt_start_date", None)
            end_for_run = getattr(self, "_bt_end_date", None)
            config_snapshot = getattr(self, "config", None) or getattr(self, "_config", None)

            if backtest_writer.write_backtest_run(
                run_id=run_id,
                ticker=ticker_for_run,
                strategy="llm",
                start_date=start_for_run,
                end_date=end_for_run,
                metrics=metrics,
                config=config_snapshot,
            ):
                rows_written = backtest_writer.write_backtest_trades(
                    run_id=run_id,
                    trades=self.trade_history,
                    daily_portfolio=self.daily_history,
                )
                logger.info(
                    "Persisted backtest_runs row + %d backtest_trades for run_id=%s",
                    rows_written,
                    run_id,
                )
                # Expose the run_id so external callers (CLI, tests) can join the
                # Postgres rows back to the JSON file on disk.
                self._bt_run_id = run_id
        except Exception as _e:  # pragma: no cover — defensive guard
            logger.warning("Backtest Postgres persistence failed: %s", _e)


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
    parser.add_argument("--resume",     action="store_true",
                        help="Resume from the per-ticker partial checkpoint if "
                             "one exists in backtest_results/. Skips dates that "
                             "have already been evaluated.")

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
        resume=args.resume,
    )
