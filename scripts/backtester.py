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

from dotenv import load_dotenv
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(_project_root, ".env"))

# Windows console encoding fix — only applied when run as a CLI script,
# not when imported as a module (e.g., by the API server background tasks).
if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Load .env so DEEPSEEK_API_KEY (and other keys) are available — every other
# entry point (main.py, run_egx_prediction.py, cli/main.py, api_server.py) does
# this; the backtester previously did not, so it crashed with "DEEPSEEK_API_KEY
# is not set" even when the key was present in .env.
from dotenv import load_dotenv
load_dotenv()

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
# Single source of truth lives in tradingagents/dataflows/egx_costs.py so the
# backtester, the RL reward shaping, and the cost-aware decision logic cannot
# drift apart. Re-exported here by name for backward compatibility.
from tradingagents.dataflows.egx_costs import (  # noqa: E402
    EGX_BROKERAGE_RATE,
    EGX_STAMP_DUTY,
    EGX_FRA_FEE,
    EGX_TOTAL_COST_SIDE,
    EGX_SLIPPAGE_NORMAL,
    EGX_SLIPPAGE_LOW_LIQ,
    EGX_CIRCUIT_BREAKER,
    EGX_SETTLEMENT_DAYS,
    apply_execution_costs as _shared_apply_execution_costs,
)


def _build_llm_fingerprint(config: Dict[str, Any]) -> Dict[str, Any]:
    """Single source of truth for the LLM fingerprint in backtest reports.

    Delegates to audit_writer.build_model_fingerprint so the JSON report
    and the Postgres audit row always agree.
    """
    from tradingagents.db.audit_writer import build_model_fingerprint
    return build_model_fingerprint(config)


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
        decision_profile: str = "live_faithful",
        decision_rfr_override: Optional[float] = None,
        record: bool = False,
        record_prompts: bool = False,
        records_dir: str = "./backtest_records",
        output_dir: str = "",
    ):
        # decision_profile: "live_faithful" (default; untouched decision logic) or
        # "tuned" (disclosed sensitivity — lowers the required-return the
        # fundamentals analyst compares earnings yield against, so the system
        # expresses more directional conviction). The tuned profile ONLY changes
        # the decision context; the Sharpe/metrics risk-free rate below is always
        # the true CBE proxy. See rate_lookup.get_egx_risk_free_rate_as_of Tier 0.
        self.decision_profile = decision_profile
        if decision_profile == "tuned" and decision_rfr_override is None:
            decision_rfr_override = float(os.getenv("BACKTEST_DECISION_RFR", "0.12"))
        self.decision_rfr_override = (
            decision_rfr_override if decision_profile == "tuned" else None
        )
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
        self._requested_benchmark: Optional[str] = benchmark_ticker  # original request (never cleared)
        self.benchmark_start_price: Optional[float] = None
        self.benchmark_history: List[Dict] = []   # [{date, price, value}]
        self._bm_data_map: Dict[str, float] = {}   # {date_str: close_price}
        self._benchmark_error: Optional[str] = None  # set when benchmark loading fails

        # Buy-and-hold benchmark using the same ticker being tested
        self.buyhold_start_price: Optional[float] = None
        self.buyhold_history: List[Dict] = []

        # Decision audit trail — one entry per evaluation date
        self.audit_log: List[Dict] = []

        # Recording state — stored as instance vars so they survive
        # TradingAgentsGraph.__init__ clobbering the global config.
        self._record = record
        self._record_prompts = record_prompts
        self._records_dir = records_dir

        # Output directory for reports, trade CSVs, and partial checkpoints.
        # Empty string → default (backtest_results/ next to this script).
        self._output_dir = output_dir

        # Configure system
        _bt_config = {
            "target_market": target_market,
            "trading_currency": "EGP" if target_market == "EGX" else "USD",
            "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
            "backtest_mode": True,
        }
        # Tuned-profile decision lever (disclosed). Unset for live_faithful so the
        # decision path is bit-for-bit identical to the live system.
        if self.decision_rfr_override is not None:
            _bt_config["decision_rfr_override"] = float(self.decision_rfr_override)
            logger.info(
                "[Backtest] TUNED profile: decision_rfr_override=%.4f (decision "
                "context only; Sharpe/metrics risk-free rate unchanged).",
                self.decision_rfr_override,
            )

        # ── Backtest LLM endpoint preference ───────────────────────────────────
        # Backtests are BURST-heavy (~6-10 LLM calls/date). On the supplied free
        # keys the NVIDIA endpoint 429/504s under that burst — and each NVIDIA 504
        # costs ~5 MINUTES of timeout before the failover even fires, so making it
        # primary makes every date crawl. DeepSeek-direct (deepseek-chat) is the
        # only supplied key that sustains the burst (verified 6/6 rapid calls), so
        # when it is available we put it FIRST for backtests only. Live runs are
        # untouched (they keep NVIDIA DeepSeek-V4-Pro primary). Respect an explicit
        # LLM_FAILOVER_PRIORITY env override if the operator set one.
        if not os.getenv("LLM_FAILOVER_PRIORITY") and os.getenv("DEEPSEEK_API_KEY"):
            _bt_config["llm_failover_priority"] = ["deepseek", "nvidia", "google", "groq"]
            _bt_config["deep_think_llm"] = os.getenv("BACKTEST_DEEP_THINK_LLM", "deepseek-chat")
            _bt_config["quick_think_llm"] = os.getenv("BACKTEST_QUICK_THINK_LLM", "deepseek-chat")
            _bt_config["backend_url"] = "https://api.deepseek.com"
            logger.info(
                "[Backtest] DeepSeek-direct set as primary LLM (deepseek-chat) — "
                "the only supplied free key that sustains the per-date burst. "
                "Live runs are unaffected. Override with LLM_FAILOVER_PRIORITY."
            )

        set_config(_bt_config)
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

    @staticmethod
    def _prev_trading_day_close(ohlcv_rows: List[Dict]) -> Optional[float]:
        """Return the close price of the second-to-last OHLCV row.

        This gives the *previous trading day's* close — the correct reference
        price for an EGX daily circuit-breaker check.  Returns ``None`` when
        fewer than 2 rows are available.
        """
        if len(ohlcv_rows) < 2:
            return None
        close = ohlcv_rows[-2].get("close")
        if close is not None and float(close) > 0:
            return float(close)
        return None

    def _check_circuit_breaker(
        self,
        ticker: str,
        current_price: float,
        ohlcv_rows: Optional[List[Dict]] = None,
    ) -> bool:
        """Check if the EGX ±10% *daily* circuit breaker is triggered.

        The correct comparison is current evaluation-date close vs the
        **previous trading day's** close (extracted from the daily OHLCV
        window that was already fetched for this evaluation date).

        Previous versions compared against ``self.prev_prices[ticker]``
        which held the *previous evaluation date* price.  With
        ``interval > 1``, that incorrectly treated multi-day cumulative
        moves as single-day circuit-breaker events, causing false positives
        on trending stocks (e.g. TMGH +30 % over 20 days).
        """
        prev_close = None
        if ohlcv_rows:
            prev_close = self._prev_trading_day_close(ohlcv_rows)

        if prev_close is None:
            # Fallback: no daily OHLCV available — skip circuit breaker
            # rather than comparing against an old evaluation-date price.
            return False

        change_pct = abs(current_price - prev_close) / prev_close
        if change_pct >= EGX_CIRCUIT_BREAKER:
            direction = "UP" if current_price > prev_close else "DOWN"
            logger.warning(
                f"[CIRCUIT BREAKER] {ticker} daily move {change_pct:.1%} {direction} "
                f"(prev trading day close {prev_close:,.2f} → {current_price:,.2f}). "
                f"Trading halted."
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
        # Delegate to the shared single-source cost model (egx_costs.py).
        return _shared_apply_execution_costs(
            action, shares, close_price, low_liquidity
        )

    # =========================================================================
    # EGX30 CSV Benchmark Loader
    # =========================================================================

    # Date formats supported by the EGX30 CSV parser (tried in order).
    _CSV_DATE_FORMATS = ["%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d"]

    @staticmethod
    def _parse_csv_date(raw: str) -> Optional[str]:
        """Try multiple date formats, return YYYY-MM-DD or None."""
        for fmt in BacktestingEngine._CSV_DATE_FORMATS:
            try:
                return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None

    def _load_egx30_csv(self, csv_path: str) -> bool:
        """
        Load EGX 30 index data from a local CSV file.

        Expected format (Investing.com export):
          "Date","Price","Open","High","Low","Vol.","Change %"
          "09/04/2024","30,998.19",...

        Supported date formats: MM/DD/YYYY, DD/MM/YYYY, YYYY-MM-DD.
        Prices may contain commas (e.g., "30,998.19").

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
                        date_str = self._parse_csv_date(raw_date)
                        if date_str is None:
                            continue
                        price = float(raw_price)
                        data[date_str] = price
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

    def _results_dir(self) -> str:
        """Return the output directory for reports, CSVs, and partials."""
        if self._output_dir:
            d = os.path.abspath(self._output_dir)
        else:
            d = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "backtest_results")
            )
        os.makedirs(d, exist_ok=True)
        return d

    def _partial_path(self, ticker: str) -> str:
        """Disk path for the per-ticker resumable checkpoint."""
        d = self._results_dir()
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

    def _bm_price_asof(self, date: str) -> Optional[float]:
        """Forward-filled EGX30 close on or before ``date``.

        Look-ahead-safe: only ever returns a close dated <= the strategy date,
        so it cannot leak future index information into the benchmark. This is
        the correct way to price a daily strategy against a coarser (e.g.
        monthly) index series — unlike a "nearest date" lookup, which could pick
        a future close (the drift MEMORY §C4 warned about).
        """
        if not self._bm_data_map:
            return None
        import bisect
        cached = getattr(self, "_bm_sorted_dates", None)
        if cached is None or len(cached) != len(self._bm_data_map):
            self._bm_sorted_dates = sorted(self._bm_data_map.keys())
        i = bisect.bisect_right(self._bm_sorted_dates, date) - 1
        if i < 0:
            return None
        price = self._bm_data_map.get(self._bm_sorted_dates[i])
        return price if price and price > 0 else None

    def _align_benchmark_to_strategy(self) -> Dict:
        """Compute a structured EGX30 benchmark block aligned to the strategy's
        daily_history via FORWARD-FILL (the most recent benchmark close on or
        before each strategy date).

        Why forward-fill instead of an exact-date intersection: the local EGX30
        series can be coarse (the bundled CSV is monthly), so requiring an exact
        YYYY-MM-DD match left only ~1 aligned day and made benchmark_return /
        alpha unreportable. Forward-fill is the standard way to compare a daily
        strategy against a lower-frequency index: every strategy day is priced
        at the last known index close, so the endpoint return is correct.

        Returns a dict suitable for the report's ``benchmark`` key. Empty dict
        when no benchmark data is available.
        """
        if not (self.benchmark_ticker and self._bm_data_map and self.daily_history):
            return {}

        strat_dates = [r["date"] for r in self.daily_history if r.get("date")]
        if not strat_dates:
            return {}

        # Forward-filled benchmark aligned to each strategy date.
        aligned = [(d, self._bm_price_asof(d)) for d in strat_dates]
        aligned = [(d, p) for d, p in aligned if p is not None]
        coverage = len(aligned) / max(len(strat_dates), 1)
        n_exact = sum(1 for d in strat_dates if d in self._bm_data_map)
        if coverage < 0.80:
            logger.warning(
                "Benchmark forward-fill coverage %.0f%% < 80%% (%d / %d strategy "
                "dates priced; %d exact matches). EGX30 series may start after the "
                "backtest window.",
                coverage * 100.0, len(aligned), len(strat_dates), n_exact,
            )
        if len(aligned) < 2:
            return {
                "name": "EGX30",
                "n_aligned_days": len(aligned),
                "note": "insufficient_benchmark_overlap",
            }

        first_date, bm_first = aligned[0]
        last_date, bm_last = aligned[-1]
        if bm_first <= 0:
            return {"name": "EGX30", "n_aligned_days": len(aligned),
                    "note": "benchmark_first_price_invalid"}

        bm_total_return = (bm_last - bm_first) / bm_first

        # Daily returns on the forward-filled aligned series
        bm_series = [p for _d, p in aligned]
        strat_by_date = {r["date"]: r["portfolio_value"] for r in self.daily_history}
        strat_series = [strat_by_date[d] for d, _p in aligned if d in strat_by_date]
        intersected = [d for d, _p in aligned]

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
            "alignment": "forward_fill",
            "benchmark_granularity_note": (
                "EGX30 series aligned by forward-fill (last close on/before each "
                "strategy date); endpoint return is exact, intra-period daily "
                "tracking is approximate when the index series is coarse."
            ),
            "first_aligned_date": first_date,
            "last_aligned_date": last_date,
            "n_aligned_days": n_days,
            "n_exact_matches": n_exact,
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
        # number is interpretable on small samples.
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

        # Sortino Ratio — same numerator as Sharpe but penalises only DOWNSIDE
        # volatility (the deviation of negative daily returns). Reported because
        # an upside-heavy strategy is unfairly punished by Sharpe's total-vol
        # denominator. Uses the same annualization (×252 / ×√252) convention.
        downside = df["returns"][df["returns"] < 0]
        downside_dev = downside.std()
        sortino = (
            (mean_ret * 252 - self.risk_free_rate) / (downside_dev * np.sqrt(252))
            if downside_dev and downside_dev > 0 else 0.0
        )

        # Calmar Ratio (annualized return / max drawdown magnitude)
        n_days = len(df)
        ann_return = (1 + total_return) ** (252 / max(n_days, 1)) - 1
        calmar = ann_return / abs(max_drawdown) if max_drawdown != 0 else 0.0

        # Benchmark comparison — Alpha.
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

        # Buy-and-hold benchmark (same ticker).
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
            "Sortino Ratio":        f"{sortino:.2f}",
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

        # Honesty: how many evaluation dates FAILED in the agent pipeline (LLM
        # rate-limit / 5xx) and were recorded as placeholder HOLDs. Computed from
        # the audit log so it lands in BOTH the run log and the saved report.
        n_agent_err = sum(
            1 for a in self.audit_log if a.get("decision_status") == "agent_error"
        )
        n_eval_dates = sum(1 for a in self.audit_log if a.get("date"))
        out["Agent Errors"] = f"{n_agent_err}/{n_eval_dates}"
        return out

    def _calculate_directional_accuracy(self, hold_band_pct: float = 0.01) -> Dict[str, Any]:
        """Post-hoc directional hit-rate — REPORTING ONLY, leak-safe.

        For each evaluation date's decision, compare it to the realized forward
        move to the NEXT evaluation date. Every price used is <= end_date and was
        already collected during the walk-forward loop; this runs AFTER the loop
        and is NEVER fed back to the agents or reflection memory (feeding forward
        returns back into memory was the look-ahead bug deleted in MEMORY §C1).
        It answers the user's question directly: "was each call right?"

            BUY  correct if forward return >  +band
            SELL correct if forward return <  -band
            HOLD correct if |forward return| <= band
        """
        price_by_date = {
            h["date"]: h["price"]
            for h in self.buyhold_history
            if h.get("price")
        }
        # One decision per date (keep last), chronological. Exclude dates whose
        # agent run errored out (decision_status="agent_error") — those carry a
        # placeholder "HOLD" but are NOT a real decision, so scoring them would
        # be measuring infra failures, not the strategy.
        by_date = {
            a["date"]: str(a.get("parsed_decision") or "HOLD").upper()
            for a in self.audit_log
            if a.get("date") in price_by_date
            and a.get("parsed_decision")
            and a.get("decision_status") != "agent_error"
        }
        ordered = sorted(by_date)

        per_class = {k: {"n": 0, "correct": 0} for k in ("BUY", "SELL", "HOLD")}
        details: List[Dict[str, Any]] = []
        evaluated = correct = 0

        for i in range(len(ordered) - 1):   # last date has no forward point in-window
            d, d_next = ordered[i], ordered[i + 1]
            p0, p1 = price_by_date[d], price_by_date[d_next]
            if not p0 or p0 <= 0:
                continue
            fwd = (p1 - p0) / p0
            dec = by_date[d]
            if dec == "BUY":
                ok = fwd > hold_band_pct
            elif dec == "SELL":
                ok = fwd < -hold_band_pct
            else:
                ok = abs(fwd) <= hold_band_pct
            evaluated += 1
            correct += int(ok)
            if dec in per_class:
                per_class[dec]["n"] += 1
                per_class[dec]["correct"] += int(ok)
            details.append({
                "date": d, "decision": dec, "next_date": d_next,
                "fwd_return_pct": round(fwd * 100, 3), "correct": ok,
            })

        actionable_n = per_class["BUY"]["n"] + per_class["SELL"]["n"]
        actionable_correct = per_class["BUY"]["correct"] + per_class["SELL"]["correct"]
        return {
            "horizon": "next_evaluation_date",
            "hold_band_pct": round(hold_band_pct * 100, 3),
            "evaluated_decisions": evaluated,
            "overall_hit_rate": round(correct / evaluated, 4) if evaluated else None,
            "actionable_hit_rate": (
                round(actionable_correct / actionable_n, 4) if actionable_n else None
            ),
            "by_decision": per_class,
            "note": "Reporting-only; computed after the run, never fed to agents (leak-safe).",
            "detail": details,
        }

    # =========================================================================
    # Decision-quality evaluation (fixed-horizon, leak-safe, reporting-only)
    # =========================================================================

    def _dense_price_series(
        self, ticker: str, start_date: str, end_date: str, horizon_buffer_days: int = 45
    ) -> Dict[str, float]:
        """Dense daily ``{date: close}`` map over [start, end + buffer], used ONLY
        to score decisions post-hoc.

        The buffer extends past ``end_date`` so the latest decisions can still be
        scored at the longest horizon. This is NOT look-ahead: the agents already
        decided; we are merely measuring what actually happened next. The forward
        prices are never fed back to any agent or to memory (that was the §C1 bug)
        — this runs after the walk-forward loop and feeds the report only.
        """
        dense: Dict[str, float] = {}

        # 1. Daily closes accumulated during the walk-forward loop — densely covers
        #    the whole window (each per-date fetch had a 252-day lookback) without
        #    any extra request. This is the reliable backbone: the provider returns
        #    capped/partial windows for a single large-range fetch.
        for d, c in getattr(self, "_seen_closes", {}).items():
            try:
                if c and float(c) > 0:
                    dense[str(d)] = float(c)
            except (TypeError, ValueError):
                continue

        # 2. Forward fetch PAST end_date so the latest decisions can be scored at
        #    the longest horizon. CRITICAL: the loop leaves config["trade_date"]
        #    pinned to the last eval date (the look-ahead clamp); it would cap this
        #    fetch at that date, leaving no forward prices. Clearing it is correct
        #    and leak-safe — these prices score decisions ALREADY made and are never
        #    fed back to any agent. Restored afterwards.
        buf_end = (
            datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=horizon_buffer_days)
        ).strftime("%Y-%m-%d")
        _saved_trade_date = get_config().get("trade_date")
        try:
            set_config({"trade_date": None})
            raw = self._fetch_stock_data_with_retry(ticker, end_date, buf_end)
        finally:
            set_config({"trade_date": _saved_trade_date})
        for row in (raw.get("data") or []):
            d, c = row.get("date"), row.get("close")
            try:
                if d and c is not None and float(c) > 0:
                    dense[str(d)] = float(c)
            except (TypeError, ValueError):
                continue

        # 3. Decision-date closes from buyhold_history — guarantees every decision
        #    date is present even on --resume (when _seen_closes wasn't restored).
        for h in self.buyhold_history:
            if h.get("date") and h.get("price"):
                try:
                    dense.setdefault(str(h["date"]), float(h["price"]))
                except (TypeError, ValueError):
                    continue
        return dense

    def _build_decision_quality(
        self, ticker: str, start_date: str, end_date: str
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Return ``(decision_quality_block, predictions_rows)``.

        ``predictions_rows`` is one row per real decision (excludes infra
        ``agent_error`` dates), carrying ``session_id`` so the dashboard can open
        each prediction's full reasoning trace.
        """
        from tradingagents.backtest.decision_metrics import (
            compute_decision_quality,
            per_decision_detail,
        )

        decisions = [
            {
                "date": a.get("date"),
                "decision": a.get("parsed_decision"),
                "confidence": a.get("confidence"),
                "session_id": a.get("session_id"),
            }
            for a in self.audit_log
            if a.get("date")
            and a.get("parsed_decision")
            and a.get("decision_status") != "agent_error"
        ]
        if not decisions:
            return {"note": "no scoreable decisions (all dates errored or empty)"}, []

        dense = self._dense_price_series(ticker, start_date, end_date)
        dq = compute_decision_quality(decisions, dense, horizons=(5, 10, 20))
        preds = per_decision_detail(
            decisions, dense, horizons=(5, 10, 20), primary_horizon=10
        )
        return dq, preds

    # =========================================================================
    # Transient-error-resilient propagate
    # =========================================================================
    # The NVIDIA free DeepSeek endpoint is flaky under the burst of ~10 LLM calls
    # per evaluation date — it throws BOTH 429 (rate limit) AND 5xx server errors
    # (500/502/503/504, surfaced by the OpenAI SDK as InternalServerError). Without
    # retry, any of these fails the date and the outer loop records a PHANTOM HOLD
    # (decision_status="agent_error"), which the dashboard then renders as a
    # deliberate "stay in cash" verdict — making a failed run look like a real
    # 0%/0-trade result. We retry only TRANSIENT infra errors so the agents
    # actually complete; genuine errors still raise immediately and surface honestly.
    _TRANSIENT_ERROR_MARKERS = (
        "429", "too many requests", "rate limit", "ratelimit", "overloaded",
        "timeout", "timed out", "temporarily unavailable", "service unavailable",
        "internal server error", "bad gateway", "gateway timeout",
        "error code: 500", "error code: 502", "error code: 503", "error code: 504",
        "connection error", "apiconnection",
    )
    # OpenAI-SDK exception class names that are always transient infra failures.
    _TRANSIENT_ERROR_NAMES = (
        "ratelimit", "timeout", "apiconnection", "serviceunavailable",
        "internalservererror", "apitimeout",
    )

    def _is_transient_llm_error(self, exc: Exception) -> bool:
        name = type(exc).__name__.lower()
        if any(k in name for k in self._TRANSIENT_ERROR_NAMES):
            return True
        # OpenAI APIStatusError carries a numeric .status_code — treat any 5xx
        # (and 429) as transient regardless of message wording.
        code = getattr(exc, "status_code", None)
        if isinstance(code, int) and (code == 429 or 500 <= code < 600):
            return True
        msg = str(exc).lower()
        return any(m in msg for m in self._TRANSIENT_ERROR_MARKERS)

    def _propagate_with_retry(
        self, graph, ticker: str, date: str,
        *, max_attempts: int = 4, base_delay: float = 20.0,
    ):
        """Run ``graph.propagate`` with backoff on transient LLM errors.

        Returns ``(final_state, signal)``. Re-raises non-transient errors and the
        final transient error after exhausting ``max_attempts`` (so the outer
        loop still records an honest ``agent_error``).
        """
        import time
        last_exc: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                return graph.propagate(ticker, date, run_type="backtest")
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                last_exc = exc
                if attempt >= max_attempts or not self._is_transient_llm_error(exc):
                    raise
                delay = base_delay * (2 ** (attempt - 1))   # 20s, 40s, 80s
                logger.warning(
                    "[%s %s] transient LLM error (%s) — retry %d/%d after %.0fs: %s",
                    ticker, date, type(exc).__name__, attempt, max_attempts - 1,
                    delay, str(exc)[:160],
                )
                time.sleep(delay)
        assert last_exc is not None
        raise last_exc

    # =========================================================================
    # Per-decision EGX30-relative metrics (REPORT-ONLY — §Phase B)
    # =========================================================================

    def _calculate_decision_metrics(self) -> Dict[str, Any]:
        """Compute per-decision quality metrics relative to EGX30.

        Uses the ``fwd_excess_return`` fields that were backfilled into
        audit_log entries during the backtest loop.  These are strictly
        report-only — they are never fed into agent state, prompts, or
        memory.

        Returns a dict suitable for the ``"decision_quality"`` key in
        the JSON report.  Empty dict when no measurable decisions exist.
        """
        from tradingagents.rl.walkforward import _wilson_ci

        # Only consider entries that have a forward return computed
        measurable = [
            e for e in self.audit_log
            if "fwd_excess_return" in e
            and e.get("parsed_decision") in ("BUY", "SELL", "HOLD")
        ]
        if not measurable:
            return {}

        buy_entries = [e for e in measurable if e["parsed_decision"] == "BUY"]
        hold_entries = [e for e in measurable if e["parsed_decision"] in ("HOLD", "SELL")]

        # --- BUY metrics ---
        n_buy = len(buy_entries)
        buy_hits = [e for e in buy_entries if e["fwd_excess_return"] > 0]
        buy_misses = [e for e in buy_entries if e["fwd_excess_return"] <= 0]
        n_buy_hit = len(buy_hits)
        n_buy_miss = len(buy_misses)

        buy_hit_rate = n_buy_hit / n_buy if n_buy > 0 else None
        false_buy_rate = n_buy_miss / n_buy if n_buy > 0 else None
        buy_mean_excess = (
            sum(e["fwd_excess_return"] for e in buy_entries) / n_buy
            if n_buy > 0 else None
        )

        buy_hit_ci = _wilson_ci(n_buy_hit, n_buy) if n_buy > 0 else (None, None)

        # --- HOLD/SELL rejection metrics ---
        n_hold = len(hold_entries)
        # Correct rejection: ticker underperformed EGX30 (negative excess)
        hold_correct = [e for e in hold_entries if e["fwd_excess_return"] <= 0]
        # Missed opportunity: ticker outperformed EGX30 (positive excess)
        hold_missed = [e for e in hold_entries if e["fwd_excess_return"] > 0]
        n_hold_correct = len(hold_correct)
        n_hold_missed = len(hold_missed)

        hold_rejection_quality = n_hold_correct / n_hold if n_hold > 0 else None
        hold_miss_rate = n_hold_missed / n_hold if n_hold > 0 else None
        hold_rejection_ci = _wilson_ci(n_hold_correct, n_hold) if n_hold > 0 else (None, None)

        result: Dict[str, Any] = {
            "total_measurable_decisions": len(measurable),
            "n_buy": n_buy,
            "n_hold_sell": n_hold,
        }

        if buy_hit_rate is not None:
            result["buy_hit_rate_vs_egx30"] = round(buy_hit_rate, 4)
            result["buy_hit_rate_ci_lo"] = round(buy_hit_ci[0], 4) if buy_hit_ci[0] is not None else None
            result["buy_hit_rate_ci_hi"] = round(buy_hit_ci[1], 4) if buy_hit_ci[1] is not None else None
        if false_buy_rate is not None:
            result["false_buy_rate"] = round(false_buy_rate, 4)
        if buy_mean_excess is not None:
            result["buy_mean_excess_return"] = round(buy_mean_excess, 6)

        if hold_rejection_quality is not None:
            result["hold_rejection_quality"] = round(hold_rejection_quality, 4)
            result["hold_rejection_ci_lo"] = round(hold_rejection_ci[0], 4) if hold_rejection_ci[0] is not None else None
            result["hold_rejection_ci_hi"] = round(hold_rejection_ci[1], 4) if hold_rejection_ci[1] is not None else None
        if hold_miss_rate is not None:
            result["hold_miss_rate"] = round(hold_miss_rate, 4)

        # Per-decision detail (for downstream analysis)
        per_decision = []
        for e in measurable:
            decision = e.get("parsed_decision")
            excess = e["fwd_excess_return"]
            if decision == "BUY":
                quality_label = "HIT" if excess > 0 else "MISS"
            else:
                # HOLD or SELL: positive excess = missed opportunity
                quality_label = "MISSED_OPPORTUNITY" if excess > 0 else "CORRECT_REJECTION"
            per_decision.append({
                "date": e.get("date"),
                "decision": decision,
                "ticker_price": e.get("price"),
                "egx30_price": e.get("egx30_price"),
                "fwd_ticker_return": round(e.get("fwd_ticker_return", 0), 6),
                "fwd_egx30_return": round(e.get("fwd_egx30_return", 0), 6),
                "fwd_excess_return": round(excess, 6),
                "holding_days": e.get("holding_days"),
                "quality_label": quality_label,
            })
        result["per_decision"] = per_decision

        return result

    # =========================================================================
    # Decision resolution (LLM judge + trader plan + deterministic veto)
    # =========================================================================

    @staticmethod
    def _extract_risk_judge_confidence(final_state: Dict) -> Optional[float]:
        """Parse the risk judge's confidence from ``risk_debate_state``.

        The LLM Constitutional Judge (risk_manager.py) emits a free-text
        response stored at ``risk_debate_state["judge_decision"]``.  Near the
        end of that text it writes a JSON block like::

            ```json
            {"action": "HOLD", "confidence": 0.0}
            ```

        This helper extracts the ``confidence`` float.  Returns ``None`` if
        parsing fails — callers must treat ``None`` as "no opinion" (i.e.
        allow fallback by default), never as 0.0.
        """
        risk_debate = final_state.get("risk_debate_state")
        if not isinstance(risk_debate, dict):
            return None
        judge_text = risk_debate.get("judge_decision")
        if not isinstance(judge_text, str) or not judge_text.strip():
            return None
        # Scan for ALL {"action": ..., "confidence": ...} blocks — the risk
        # judge's final decision is typically the last one in the response.
        pattern = re.compile(
            r'\{\s*"action"\s*:\s*"[^"]*"\s*,\s*"confidence"\s*:\s*'
            r'([0-9]+(?:\.[0-9]+)?)\s*\}',
            re.IGNORECASE,
        )
        matches = pattern.findall(judge_text)
        if not matches:
            # Try reversed key order: {"confidence": ..., "action": ...}
            pattern_rev = re.compile(
                r'\{\s*"confidence"\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*,\s*'
                r'"action"\s*:\s*"[^"]*"\s*\}',
                re.IGNORECASE,
            )
            matches = pattern_rev.findall(judge_text)
        if not matches:
            return None
        try:
            return float(matches[-1])
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _extract_risk_judge_verdict(
        final_state: Dict,
    ) -> Tuple[Optional[str], Optional[float]]:
        """Extract both action and confidence from the risk judge's JSON.

        Returns ``(action, confidence)`` — e.g. ``("SELL", 0.70)``.
        Either or both may be ``None`` if parsing fails.
        """
        risk_debate = final_state.get("risk_debate_state")
        if not isinstance(risk_debate, dict):
            return None, None
        judge_text = risk_debate.get("judge_decision")
        if not isinstance(judge_text, str) or not judge_text.strip():
            return None, None

        # Pattern: {"action": "...", "confidence": ...}
        pattern = re.compile(
            r'\{\s*"action"\s*:\s*"([^"]*)"\s*,\s*"confidence"\s*:\s*'
            r'([0-9]+(?:\.[0-9]+)?)\s*\}',
            re.IGNORECASE,
        )
        matches = pattern.findall(judge_text)
        if not matches:
            # Reversed key order: {"confidence": ..., "action": "..."}
            pattern_rev = re.compile(
                r'\{\s*"confidence"\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*,\s*'
                r'"action"\s*:\s*"([^"]*)"\s*\}',
                re.IGNORECASE,
            )
            rev_matches = pattern_rev.findall(judge_text)
            if rev_matches:
                # Reversed capture groups: (confidence, action)
                conf_str, action_str = rev_matches[-1]
                try:
                    return action_str.upper(), float(conf_str)
                except (ValueError, IndexError):
                    return None, None
            return None, None

        # Normal order: last match wins (final decision)
        action_str, conf_str = matches[-1]
        try:
            return action_str.upper(), float(conf_str)
        except (ValueError, IndexError):
            return None, None

    def _get_previous_ticker_decision(self) -> Optional[Dict]:
        """Return the last *active* decision from the audit log.

        An "active" decision is the last BUY or SELL.  HOLD entries that
        were created by an anti-churn override (``anti_churn_applied=True``)
        are skipped — they represent a gate blocking a reversal, not a
        deliberate neutral stance.  This prevents the feedback loop where
        anti-churn HOLD → ``previous_decision=HOLD`` → continuity prompt
        says "hold the course" → perpetual HOLD.

        Falls back to the most recent genuine HOLD (one where the agent
        truly chose HOLD, not an override) if no BUY/SELL exists yet.
        """
        last_genuine_hold = None
        for entry in reversed(self.audit_log):
            sig = entry.get("parsed_decision")
            if sig in ("BUY", "SELL"):
                return {
                    "signal": sig,
                    "date": entry["date"],
                    "confidence": entry.get("confidence"),
                }
            if sig == "HOLD" and not entry.get("anti_churn_applied", False):
                if last_genuine_hold is None:
                    last_genuine_hold = {
                        "signal": "HOLD",
                        "date": entry["date"],
                        "confidence": entry.get("confidence"),
                    }
        return last_genuine_hold

    @staticmethod
    def _resolve_decision(
        final_state: Dict,
        execution_plan: Dict,
        current_date: str = "",
        config: Dict = None,
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

             **P5 safety gate (2026-06-18):** Before allowing fallback, parse
             the risk judge's confidence from ``risk_debate_state``.  If the
             confidence is explicitly parseable and effectively zero (< 0.05),
             block the fallback — the risk judge actively flagged the trade as
             unacceptable even though the deterministic layer cleared it.
             When confidence is missing or unparseable, allow fallback by
             default (no false blocks from parse failures).

        Returns ``(decision, path)`` where ``path`` is one of
        ``{"deterministic_veto", "judge_bare", "judge_json", "judge_freetext",
        "trader_fallback", "fallback_blocked_risk_confidence",
        "default_hold"}`` for audit-log readability.
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
        #
        # P5 safety gate: if the risk judge's confidence is explicitly zero
        # (< 0.05), it means the Constitutional Judge actively rejected the
        # trade on qualitative grounds (e.g. position concentration, stale
        # data) that the deterministic layer doesn't check. In that case,
        # block the fallback and respect the HOLD.
        if judge_decision == "HOLD":
            plan_decision = ""
            if isinstance(execution_plan, dict):
                plan_decision = (execution_plan.get("decision", "") or "").upper()
            if plan_decision in ("BUY", "SELL"):
                rj_conf = BacktestingEngine._extract_risk_judge_confidence(
                    final_state
                )
                if rj_conf is not None and rj_conf < 0.05:
                    return "HOLD", "fallback_blocked_risk_confidence"
                judge_decision = plan_decision
                judge_path = "trader_fallback"

        # ── Fix A (P8): Anti-churn reversal gate ─────────────────────────
        # Fires AFTER normal decision resolution but BEFORE the final return.
        # A reversal is BUY→SELL or SELL→BUY (not HOLD transitions).
        # Always writes audit fields into final_state so the report can prove
        # exactly why anti-churn did or did not fire.
        if config and config.get("anti_churn_enabled") and judge_decision in ("BUY", "SELL"):
            prev = final_state.get("previous_decision")
            conf_scores = final_state.get("confidence_scores") or {}
            conf_propagator = conf_scores.get("overall")
            variant = config.get("anti_churn_variant", "A2")
            threshold = config.get("anti_churn_reversal_confidence_threshold", 0.55)

            # Prefer risk judge confidence when the judge's action matches the
            # current decision.  This avoids using a compressed propagator score
            # (~0.50) that blocks every reversal.  If the judge said a different
            # action (e.g. judge=HOLD but trader_fallback flipped to BUY), fall
            # back to propagator — the judge's confidence is for its own action,
            # not the overridden one.
            rj_action, rj_conf = BacktestingEngine._extract_risk_judge_verdict(
                final_state
            )
            if (
                rj_conf is not None
                and 0.0 <= rj_conf <= 1.0
                and rj_action == judge_decision
            ):
                conf_overall = rj_conf
                _ac_conf_source = "risk_judge"
            else:
                conf_overall = conf_propagator
                _ac_conf_source = "propagator"

            is_reversal = (
                (prev == "BUY" and judge_decision == "SELL")
                or (prev == "SELL" and judge_decision == "BUY")
            )

            # Always record audit fields so the report shows the gate's reasoning
            final_state["_anti_churn_audit"] = {
                "checked": True,
                "prev_decision": prev,
                "current_decision": judge_decision,
                "is_reversal": is_reversal,
                "confidence_used": conf_overall,
                "confidence_source": _ac_conf_source,
                "confidence_propagator": conf_propagator,
                "confidence_risk_judge": rj_conf,
                "risk_judge_action": rj_action,
                "threshold": threshold,
                "variant": variant,
                "applied": False,
                "reason": None,
            }

            if is_reversal:
                if variant == "A1":
                    # Hard minimum hold period
                    prev_date = final_state.get("previous_decision_date")
                    if prev_date and current_date:
                        from datetime import datetime as _dt
                        try:
                            days_held = (_dt.strptime(current_date, "%Y-%m-%d")
                                         - _dt.strptime(prev_date, "%Y-%m-%d")).days
                            min_hold = config.get("anti_churn_min_hold_days", 20)
                            if days_held < min_hold:
                                final_state["_anti_churn_audit"]["applied"] = True
                                final_state["_anti_churn_audit"]["reason"] = (
                                    f"A1: held {days_held}d < min {min_hold}d"
                                )
                                return "HOLD", "anti_churn_A1_hold_Nd"
                        except (ValueError, TypeError):
                            pass

                elif variant == "A2":
                    # Conviction-gated reversal (primary)
                    if conf_overall is not None and conf_overall < threshold:
                        final_state["_anti_churn_audit"]["applied"] = True
                        final_state["_anti_churn_audit"]["reason"] = (
                            f"A2: conf {conf_overall:.3f} < threshold {threshold} "
                            f"(source={_ac_conf_source})"
                        )
                        return "HOLD", "anti_churn_A2_low_conf"
                    else:
                        final_state["_anti_churn_audit"]["reason"] = (
                            f"A2: reversal allowed — conf {conf_overall} >= {threshold} "
                            f"(source={_ac_conf_source})"
                            if conf_overall is not None
                            else "A2: reversal allowed — conf_overall is None (no gate)"
                        )

                elif variant == "A3":
                    # Partial-size reversal — don't change decision, mark for
                    # partial exit in execute_trade
                    final_state["_partial_exit"] = True
                    final_state["_anti_churn_audit"]["applied"] = True
                    final_state["_anti_churn_audit"]["reason"] = "A3: partial exit marked"

                elif variant == "A4":
                    # Regime-aware reversal gating (requires Fix C)
                    breadth = final_state.get("market_breadth") or {}
                    regime = breadth.get("regime", "sideways")
                    regime_opposes = (
                        (regime == "rally" and judge_decision == "SELL")
                        or (regime == "downturn" and judge_decision == "BUY")
                    )
                    if regime_opposes and conf_overall is not None and conf_overall < threshold:
                        final_state["_anti_churn_audit"]["applied"] = True
                        final_state["_anti_churn_audit"]["reason"] = (
                            f"A4: regime={regime} opposes {judge_decision}, "
                            f"conf {conf_overall:.3f} < {threshold}"
                        )
                        return "HOLD", "anti_churn_A4_regime_gate"

        # ── Fix C (P8): Direction-aware breadth confidence dampening ──────
        # Once we know the decision, dampen confidence for regime-opposed signals.
        # This reduces position size without blocking the trade.
        if config and config.get("market_breadth_enabled") and judge_decision in ("BUY", "SELL"):
            breadth = final_state.get("market_breadth") or {}
            regime = breadth.get("regime", "sideways")
            dampening = config.get("market_breadth_dampening_factor", 0.75)
            conf_scores = final_state.get("confidence_scores") or {}
            conf = conf_scores.get("overall")
            if conf is not None:
                if (regime == "rally" and judge_decision == "SELL") or \
                   (regime == "downturn" and judge_decision == "BUY"):
                    final_state["_breadth_adjusted_confidence"] = conf * dampening

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
            # Fix C (P8): use breadth-adjusted confidence when available.
            # B4 (P8): lower sizing floor from 20% to 5% when enabled.
            effective_conf = final_state.get("_breadth_adjusted_confidence", confidence) if final_state else confidence
            sizing_floor = 0.05 if self.config.get("b4_sizing_floor_enabled") else 0.20
            if effective_conf is not None and effective_conf > 0:
                confidence_scalar = max(sizing_floor, min(1.0, effective_conf))
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
            # A3 partial exit: sell only a fraction of the position
            if final_state and final_state.get("_partial_exit"):
                frac = self.config.get("anti_churn_partial_exit_frac", 0.50)
                shares_to_transact = max(1, int(pos["shares"] * frac))
            else:
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

            remaining = pos["shares"] - shares_to_transact
            self.positions[ticker] = {
                "shares": remaining,
                "avg_cost": pos["avg_cost"] if remaining > 0 else 0.0,
            }
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

        # Backtest-only: drop the news + social analysts. In a backtest there is
        # no historical EGX news archive and the social pipeline returns
        # NO_SIGNAL for past dates (live-only) — so both analysts contribute
        # nothing but still cost ~2 LLM calls/date each, inflating the per-date
        # burst that the throttled endpoints choke on. The market + fundamentals
        # analysts run; the bull/bear debate, research manager, trader and risk
        # stages downstream run NORMALLY on the resulting reports.
        # Override with BACKTEST_INCLUDE_NEWS_SOCIAL=1 if you ever wire a real
        # historical news/social archive.
        if os.getenv("BACKTEST_INCLUDE_NEWS_SOCIAL", "0").strip() not in ("1", "true", "True"):
            _dropped = [a for a in analysts if a in ("news", "social")]
            analysts = [a for a in analysts if a not in ("news", "social")]
            if not analysts:
                analysts = ["market", "fundamentals"]
            if _dropped:
                logger.info(
                    "[Backtest] news/social analysts disabled (%s) — running %s only. "
                    "Set BACKTEST_INCLUDE_NEWS_SOCIAL=1 to re-enable.",
                    ", ".join(_dropped), analysts,
                )

        self._train_end_date = train_end_date  # stored for tagging inside the loop
        # Retained so save_results() can pass them to the Postgres backtest writer.
        self._bt_ticker = ticker
        self._bt_start_date = start_date
        self._bt_end_date = end_date
        self._bt_analysts = list(analysts)

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

        # Accumulate every daily close the per-date fetches return (each has a
        # 252-day lookback, so this densely covers the whole window for free).
        # Used post-hoc by _build_decision_quality to score fixed-horizon forward
        # returns reliably — the provider returns capped/partial windows for a
        # single large-range fetch, so we reuse the data we already paid for.
        if not hasattr(self, "_seen_closes"):
            self._seen_closes: Dict[str, float] = {}

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
                        _err = (
                            f"BENCHMARK UNAVAILABLE: '{self.benchmark_ticker}' returned no data "
                            f"from any source (no local CSV found, gateway returned empty). "
                            f"Alpha vs EGX30 will NOT be computed for this run."
                        )
                        logger.error(_err)
                        self._benchmark_error = _err
                        self.benchmark_ticker = None
                except Exception as e:
                    _err = (
                        f"BENCHMARK UNAVAILABLE: '{self.benchmark_ticker}' fetch failed ({e}). "
                        f"Alpha vs EGX30 will NOT be computed for this run."
                    )
                    logger.error(_err)
                    self._benchmark_error = _err
                    self.benchmark_ticker = None

        # ---- Initialize agent graph ----
        logger.info(f"Initializing Agent Graph (analysts: {analysts})...")
        graph = TradingAgentsGraph(selected_analysts=analysts, debug=False)

        # Re-apply recording config.  TradingAgentsGraph.__init__ calls
        # set_config(DEFAULT_CONFIG) which overwrites backtest_record_outputs.
        if self._record:
            set_config({
                "backtest_record_outputs": True,
                "record_full_prompts": self._record_prompts,
                "backtest_records_dir": self._records_dir,
            })

        # Log fundamentals mode for traceability
        _fund_mode = get_config().get("use_hybrid_fundamental_analyst", False)
        logger.info(
            "Fundamentals mode: %s (extra CoT LLM calls: %s)",
            "hybrid (deterministic + CoT)" if _fund_mode else "deterministic-only",
            "yes, +2-4 per evaluation" if _fund_mode else "none",
        )

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

            # Capture the full daily close series this fetch returned (dense,
            # leak-safe — all dates <= the eval date) for post-hoc scoring.
            for _row in stock_data["data"]:
                _d, _c = _row.get("date"), _row.get("close")
                try:
                    if _d and _c is not None and float(_c) > 0:
                        self._seen_closes[str(_d)] = float(_c)
                except (TypeError, ValueError):
                    continue

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

            # ---- Circuit breaker check (daily close vs previous trading day) ----
            circuit_halted = self._check_circuit_breaker(
                ticker, current_price, ohlcv_rows=stock_data.get("data"),
            )
            self.prev_prices[ticker] = current_price  # diagnostic / portfolio tracking

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

            # ---- Track benchmark value (forward-fill, look-ahead-safe) --------
            # Price each strategy date at the most recent EGX30 close on or
            # BEFORE that date (_bm_price_asof). This never uses a future close
            # (so it respects MEMORY §C4's no-drift intent) while still working
            # when the index series is coarser than the daily strategy — the
            # exact-match-only rule left the monthly CSV with ~0 aligned days
            # and reported a bogus 0% benchmark return.
            if (
                self.benchmark_ticker
                and self._bm_data_map
                and self.benchmark_start_price
                and self.benchmark_start_price > 0
            ):
                bm_price = self._bm_price_asof(date)
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
                    # Fix A (P8): inject previous decision for anti-churn gating
                    if self.config.get("anti_churn_enabled"):
                        prev = self._get_previous_ticker_decision()
                        base_state["previous_decision"] = prev.get("signal") if prev else None
                        base_state["previous_decision_date"] = prev.get("date") if prev else None
                    return base_state

                graph.propagator.create_initial_state = patched_create_initial_state

                _call_log.clear()
                _t0 = time.perf_counter()
                final_state, _ = self._propagate_with_retry(graph, ticker, date)
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

                decision, _decision_path = self._resolve_decision(
                    final_state, execution_plan,
                    current_date=date, config=self.config,
                )
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
                # EGX30 price at this eval date (report-only, for
                # per-decision forward-return metrics).
                _egx30_price_at_date = self._bm_data_map.get(date)

                # Risk judge verdict (report-only audit field)
                _rj_action, _rj_conf = self._extract_risk_judge_verdict(
                    final_state
                )

                audit_entry = {
                    "date": date,
                    "price": current_price,
                    "session_id": getattr(graph, "session_id", None),
                    "egx30_price": _egx30_price_at_date,
                    "raw_decision": str(raw_decision)[:200],  # Truncate long LLM text
                    "parsed_decision": decision,
                    "decision_path": _decision_path,
                    "risk_action": final_state.get("risk_action"),
                    "risk_approved": risk_assessment.get("approved") if isinstance(risk_assessment, dict) else None,
                    "risk_violations": risk_assessment.get("total_violations", 0) if isinstance(risk_assessment, dict) else 0,
                    "critical_violations": risk_assessment.get("critical_violations", 0) if isinstance(risk_assessment, dict) else 0,
                    "execution_plan_decision": execution_plan.get("decision", "N/A") if isinstance(execution_plan, dict) else "N/A",
                    "confidence": confidence,
                    "risk_judge_confidence": _rj_conf,
                    "risk_judge_action": _rj_action,
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

                # ---- Fundamentals quality tracking ----
                _fund_analysis = final_state.get("fundamental_analysis") or {}
                _fund_qs = _fund_analysis.get("quality_status") or {}
                audit_entry["fundamentals_quality"] = _fund_qs.get("level", "unknown")
                audit_entry["fundamentals_effective_confidence"] = _fund_analysis.get("effective_confidence")
                audit_entry["fundamentals_pipeline_mode"] = _fund_analysis.get("pipeline_mode", "unknown")

                # ---- P3 Momentum Debug Instrumentation ----
                # Persist compact momentum fields so smoke tests can prove
                # momentum_pack reached the LLM prompt path.
                _tech = final_state.get("technical_analysis") or {}
                _mpack = _tech.get("momentum") if isinstance(_tech, dict) else None
                if isinstance(_mpack, dict) and _mpack:
                    audit_entry["p3_momentum_debug"] = {
                        "return_20d": _mpack.get("return_20d"),
                        "return_60d": _mpack.get("return_60d"),
                        "return_120d": _mpack.get("return_120d"),
                        "momentum_label": _mpack.get("momentum_label"),
                        "rs_60d": _mpack.get("rs_60d"),
                        "rs_label": _mpack.get("rs_label"),
                        "volume_confirmed": _mpack.get("volume_confirmed"),
                    }
                    # The evidence narrative includes "PRICE MOMENTUM" section
                    # whenever momentum_pack is non-null in data_cot.
                    audit_entry["p3_evidence_narrative_contains_momentum_section"] = True
                else:
                    audit_entry["p3_momentum_debug"] = None
                    audit_entry["p3_evidence_narrative_contains_momentum_section"] = False

                # ---- Anti-churn audit fields ----
                _ac_audit = final_state.get("_anti_churn_audit")
                if _ac_audit and isinstance(_ac_audit, dict):
                    audit_entry["anti_churn_checked"] = _ac_audit.get("checked", False)
                    audit_entry["anti_churn_prev_decision"] = _ac_audit.get("prev_decision")
                    audit_entry["anti_churn_current_decision"] = _ac_audit.get("current_decision")
                    audit_entry["anti_churn_confidence_used"] = _ac_audit.get("confidence_used")
                    audit_entry["anti_churn_confidence_source"] = _ac_audit.get("confidence_source")
                    audit_entry["anti_churn_confidence_propagator"] = _ac_audit.get("confidence_propagator")
                    audit_entry["anti_churn_confidence_risk_judge"] = _ac_audit.get("confidence_risk_judge")
                    audit_entry["anti_churn_risk_judge_action"] = _ac_audit.get("risk_judge_action")
                    audit_entry["anti_churn_threshold"] = _ac_audit.get("threshold")
                    audit_entry["anti_churn_is_reversal"] = _ac_audit.get("is_reversal", False)
                    audit_entry["anti_churn_applied"] = _ac_audit.get("applied", False)
                    audit_entry["anti_churn_reason"] = _ac_audit.get("reason")

                logger.info(f"[AUDIT] {json.dumps(audit_entry, default=str)}")
                self.audit_log.append(audit_entry)

                # ---- Per-decision forward return (REPORT-ONLY) ----
                # Now that date t+1 is processed, backfill the forward
                # return on the PREVIOUS audit entry (date t).  These
                # fields are strictly for the decision_quality report
                # block — they are NEVER fed into agent state, prompts,
                # memory, or later decisions.
                if len(self.audit_log) >= 2:
                    prev_entry = self.audit_log[-2]
                    prev_price = prev_entry.get("price")
                    prev_egx = prev_entry.get("egx30_price")
                    cur_price_now = audit_entry.get("price")
                    cur_egx_now = audit_entry.get("egx30_price")
                    if (
                        prev_price and prev_price > 0
                        and cur_price_now and cur_price_now > 0
                    ):
                        fwd_ticker = (cur_price_now / prev_price) - 1.0
                        prev_entry["fwd_ticker_return"] = fwd_ticker
                        if (
                            prev_egx and prev_egx > 0
                            and cur_egx_now and cur_egx_now > 0
                        ):
                            fwd_egx = (cur_egx_now / prev_egx) - 1.0
                            prev_entry["fwd_egx30_return"] = fwd_egx
                            prev_entry["fwd_excess_return"] = fwd_ticker - fwd_egx
                        # Holding days
                        try:
                            d_prev = datetime.strptime(prev_entry["date"], "%Y-%m-%d")
                            d_cur = datetime.strptime(audit_entry["date"], "%Y-%m-%d")
                            prev_entry["holding_days"] = (d_cur - d_prev).days
                        except (ValueError, KeyError):
                            pass

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

        # ---- Honesty gate: don't let a rate-limited / failed run masquerade
        # as a clean all-HOLD 0% result. Count dates whose agent run errored
        # (decision_status="agent_error") and surface it at the run level so the
        # report/dashboard show a failure, not a deliberate verdict.
        agent_errors = [a for a in self.audit_log if a.get("decision_status") == "agent_error"]
        n_err = len(agent_errors)
        n_eval = max(evaluated_dates, 1)
        # ("Agent Errors" metric is added inside _calculate_metrics so it lands
        # in the saved report too; here we only set the run-level error string.)
        if n_err:
            err_classes = sorted({a.get("error_class", "?") for a in agent_errors})
            msg = (
                f"{n_err}/{evaluated_dates} evaluation dates FAILED in the agent "
                f"pipeline ({', '.join(err_classes)}) and were recorded as "
                f"placeholder HOLDs — these are NOT real decisions. Likely an LLM "
                f"rate-limit/timeout; re-run (--resume) or use a less throttled "
                f"endpoint. Treat trade/return figures as unreliable until clean."
            )
            logger.warning(msg)
            # If MOST dates failed, the whole run is untrustworthy → set run error.
            if n_err >= n_eval * 0.5 or n_err == len(self.audit_log):
                self._run_error = (self._run_error + " | " if getattr(self, "_run_error", None) else "") + msg

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

        # Decision-quality scoring + per-prediction rows (post-hoc, leak-safe).
        # This is the PRIMARY thesis evidence ("skillful, not random"); it works
        # even when the strategy mostly HOLDs and the equity curve is flat.
        try:
            self._decision_quality, self._predictions = self._build_decision_quality(
                ticker, start_date, end_date
            )
            dq_h = (self._decision_quality.get("horizons") or {}).get("10") or {}
            logger.info(
                "Decision quality @10d: actionable hit-rate=%s (n=%s), IC=%s, "
                "binomial p vs 50%%=%s",
                dq_h.get("actionable_hit_rate"), dq_h.get("actionable_n"),
                dq_h.get("information_coefficient"),
                dq_h.get("actionable_binomial_p_vs_50pct"),
            )
        except Exception as exc:
            logger.warning("Decision-quality scoring failed: %s", exc)
            self._decision_quality, self._predictions = {}, []

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
        results_dir = self._results_dir()
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

        # ---- Fundamentals quality distribution (aggregated from audit_log) ----
        fundamentals_summary = {}
        audit_with_fund = [a for a in self.audit_log if "fundamentals_quality" in a]
        if audit_with_fund:
            quality_counts = {}
            eff_conf_vals = []
            for a in audit_with_fund:
                q = a.get("fundamentals_quality", "unknown")
                quality_counts[q] = quality_counts.get(q, 0) + 1
                ec = a.get("fundamentals_effective_confidence")
                if ec is not None:
                    eff_conf_vals.append(ec)
            fundamentals_summary = {
                "hybrid_enabled": bool(get_config().get("use_hybrid_fundamental_analyst", False)),
                "dates_with_fundamentals": len(audit_with_fund),
                "quality_distribution": quality_counts,
                "avg_effective_confidence": round(sum(eff_conf_vals) / len(eff_conf_vals), 1) if eff_conf_vals else None,
                "min_effective_confidence": min(eff_conf_vals) if eff_conf_vals else None,
                "max_effective_confidence": max(eff_conf_vals) if eff_conf_vals else None,
            }

        # Benchmark status block — explicit enabled/disabled flag
        _bm_block = getattr(self, "_benchmark_block", {})
        _bm_error = getattr(self, "_benchmark_error", None)
        _bm_requested = getattr(self, "_requested_benchmark", None)
        benchmark_status = {
            "benchmark_requested": _bm_requested,
            "benchmark_enabled": bool(_bm_block and _bm_block.get("n_aligned_days", 0) > 0),
            "benchmark_error": _bm_error,
            **_bm_block,
        }

        # Print a final loud warning if benchmark was requested but unavailable
        if _bm_requested and not benchmark_status["benchmark_enabled"]:
            _msg = (
                f"\n{'='*70}\n"
                f"  ⚠ BENCHMARK WARNING: '{_bm_requested}' was requested but is NOT available.\n"
                f"  Alpha vs EGX30 was NOT computed for this run.\n"
                f"  Reason: {_bm_error or 'no aligned days between strategy and benchmark'}\n"
                f"{'='*70}\n"
            )
            logger.warning(_msg)
            print(_msg)

        # Per-decision EGX30-relative quality metrics (report-only)
        decision_quality = self._calculate_decision_metrics()

        report = {
            "session":              session_name,
            "error":                getattr(self, "_run_error", None),
            "run_config": {
                "decision_profile":      getattr(self, "decision_profile", "live_faithful"),
                "decision_rfr_override":  getattr(self, "decision_rfr_override", None),
                "initial_capital":        self.initial_capital,
                "start_date":             getattr(self, "_bt_start_date", None),
                "end_date":               getattr(self, "_bt_end_date", None),
                "analysts":               getattr(self, "_bt_analysts", None),
            },
            "metrics":              metrics,
            "split_metrics":        split_metrics,
            "directional_accuracy": self._calculate_directional_accuracy(),
            "decision_quality":     decision_quality,
            "benchmark":            benchmark_status,
            "pipeline_efficiency":  efficiency_summary,
            "fundamentals_quality": fundamentals_summary,
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
            "llm_fingerprint": _build_llm_fingerprint(self.config),
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
    parser.add_argument("--profile",    type=str,   default="live_faithful",
                        choices=["live_faithful", "tuned"],
                        help="Decision profile. 'live_faithful' = untouched live "
                             "decision logic (primary thesis result). 'tuned' = "
                             "disclosed sensitivity config that lowers the "
                             "required-return the fundamentals analyst compares "
                             "earnings yield against (decision context only; "
                             "Sharpe/metrics risk-free rate unchanged).")
    parser.add_argument("--decision-rfr", type=float, default=None,
                        help="Explicit decision-RFR override for --profile tuned "
                             "(default 0.12 or BACKTEST_DECISION_RFR env).")
    parser.add_argument("--resume",     action="store_true",
                        help="Resume from the per-ticker partial checkpoint if "
                             "one exists in backtest_results/. Skips dates that "
                             "have already been evaluated.")
    parser.add_argument("--rl-model",   type=str,   default=None,
                        help="Path to an RL position-sizing policy checkpoint "
                             "(offline `models/rl_sizing.pt` OR an online "
                             "`models/rl_sizing_online.pt`). When given, the RL "
                             "meta-policy is enabled and scales position size "
                             "(can only shrink; the risk veto still wins). "
                             "Omit to run with the identity policy (size×1.0).")
    parser.add_argument("--record",     action="store_true",
                        help="Enable per-node LLM output recording for audit/replay. "
                             "Writes JSON records to --records-dir.")
    parser.add_argument("--record-prompts", action="store_true",
                        help="Also save full prompt text in records (large). "
                             "Requires --record.")
    parser.add_argument("--records-dir", type=str, default="./backtest_records",
                        help="Directory for node records (default: ./backtest_records)")
    parser.add_argument("--output-dir", type=str, default="",
                        help="Directory for reports, trades CSVs, and partial checkpoints. "
                             "Default: backtest_results/ next to this script.")
    parser.add_argument("--no-hybrid-fundamentals", action="store_true",
                        help="Disable CoT enrichment for fundamentals (deterministic-only). "
                             "Default is hybrid (deterministic + 3-stage CoT).")

    args = parser.parse_args()

    # Initialize structured logging
    from tradingagents.observability import setup_logging
    setup_logging()

    analysts_list = args.analysts.split(",")
    benchmark  = None if args.benchmark.lower()  == "none" else args.benchmark
    train_end  = None if args.train_end.lower()  == "none" else args.train_end

    # Wire the RL meta-policy from the CLI.
    if args.rl_model:
        set_config({"rl_meta_policy_enabled": True, "rl_model_path": args.rl_model})

    # Apply recording config if --record is set
    if args.record:
        set_config({
            "backtest_record_outputs": True,
            "record_full_prompts": args.record_prompts,
            "backtest_records_dir": args.records_dir,
        })

    # Apply hybrid fundamentals override if --no-hybrid-fundamentals is set.
    if args.no_hybrid_fundamentals:
        from tradingagents.default_config import DEFAULT_CONFIG as _DC
        _DC["use_hybrid_fundamental_analyst"] = False
        set_config({"use_hybrid_fundamental_analyst": False})

    engine = BacktestingEngine(
        initial_capital=args.capital,
        benchmark_ticker=benchmark,
        decision_profile=args.profile,
        decision_rfr_override=args.decision_rfr,
        record=args.record,
        record_prompts=args.record_prompts,
        records_dir=args.records_dir,
        output_dir=args.output_dir,
    )
    engine.run_backtest(
        args.ticker, args.start, args.end,
        interval_days=args.interval,
        analysts=analysts_list,
        cooldown=args.cooldown,
        train_end_date=train_end,
        resume=args.resume,
    )
