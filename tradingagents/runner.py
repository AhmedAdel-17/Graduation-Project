"""Unified analysis runner — the single front door for every way this system runs.

Historically there were three divergent code paths:

  * "quick analysis"  -> a single throwaway LLM prompt in ``run_egx_prediction.py``
                         that bypassed the multi-agent system entirely;
  * "full pipeline"   -> the real ``TradingAgentsGraph`` invoked ad-hoc in the
                         FastAPI server;
  * "backtest"        -> ``scripts/run_backtest.py`` / ``scenario_backtest``:
                         single decision on start_date, scored at end_date.

This module collapses them into ONE entry point, :func:`run_analysis`, with a
``mode`` selector.  Every surface (the ``main.py`` CLI and the FastAPI
endpoints) should call this instead of re-implementing data fetch + LLM
orchestration.

Three modes, one engine
-----------------------
``quick``     Reduced *real* multi-agent graph — a subset of analysts
              (``market`` + ``fundamentals`` by default, skipping the slow
              news/social scraping) running the genuine debate + risk stages.
              Fast, but it is the real system, not a fake.

``full``      The complete ``TradingAgentsGraph`` (all four analysts -> bull/bear
              debate -> research manager -> trader -> risk manager).

``backtest``  Single-decision scenario backtest (``scripts/run_backtest.py``):
              run market + fundamentals on the **start date** only, then compare
              the prediction vs the realized move and vs EGX30 at the **end
              date**. Local CSV data only — no news/social, no live APIs.

No-look-ahead invariant
-----------------------
The whole point of separating ``backtest`` from ``quick``/``full`` is the data
horizon.  ``quick`` and ``full`` are *live*: they analyse "as of today" and may
hit live/online feeds.  ``backtest`` must NEVER use a live feed — that is what
``backtest_mode`` guarantees inside ``BacktestingEngine``.  Do not add a code
path that runs a backtest through the live graph helpers below.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.dataflows.symbol_utils import normalize_egx_ticker

logger = logging.getLogger("tradingagents.runner")

# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------
MODE_QUICK = "quick"
MODE_FULL = "full"
MODE_BACKTEST = "backtest"
VALID_MODES = (MODE_QUICK, MODE_FULL, MODE_BACKTEST)

# Default analyst sets per mode.  ``quick`` deliberately drops ``news`` and
# ``social`` — those are the slow, network-heavy scraping analysts — while
# keeping the real graph (debate + risk) intact.
FULL_ANALYSTS: List[str] = ["market", "social", "news", "fundamentals"]
QUICK_ANALYSTS: List[str] = ["market", "fundamentals"]

# A progress callback receives (node_name, status, state_keys) per graph node.
ProgressCallback = Callable[[str, str, List[str]], None]


def _last_trading_day() -> str:
    """Most recent weekday as ``YYYY-MM-DD`` (international Sat/Sun convention)."""
    today = date.today()
    if today.weekday() == 5:        # Saturday -> Friday
        today -= timedelta(days=1)
    elif today.weekday() == 6:      # Sunday -> Friday
        today -= timedelta(days=2)
    return today.isoformat()


def _build_run_config(
    base: Optional[Dict[str, Any]],
    *,
    max_debate_rounds: int,
    max_risk_rounds: int,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a fresh config dict for one run (never mutates the caller's)."""
    config = dict(base) if base is not None else DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = max_debate_rounds
    config["max_risk_discuss_rounds"] = max_risk_rounds
    if overrides:
        config.update(overrides)
    return config


# ---------------------------------------------------------------------------
# Graph-backed modes (quick / full)
# ---------------------------------------------------------------------------
def _run_graph(
    ticker: str,
    *,
    mode: str,
    as_of_date: str,
    analysts: List[str],
    config: Optional[Dict[str, Any]],
    max_debate_rounds: int,
    max_risk_rounds: int,
    debug: bool,
    include_state: bool,
    progress_cb: Optional[ProgressCallback],
) -> Dict[str, Any]:
    """Run the live multi-agent graph for a single ticker at ``as_of_date``."""
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    run_config = _build_run_config(
        config,
        max_debate_rounds=max_debate_rounds,
        max_risk_rounds=max_risk_rounds,
    )

    logger.info(
        "[runner] mode=%s ticker=%s as_of=%s analysts=%s",
        mode, ticker, as_of_date, analysts,
    )

    graph = TradingAgentsGraph(
        selected_analysts=analysts,
        debug=debug,
        config=run_config,
    )

    if progress_cb is None:
        # Canonical, audited path — writes the analysis_sessions audit row and
        # publishes Redis events itself.
        final_state, signal = graph.propagate(ticker, as_of_date)
    else:
        final_state, signal = _stream_graph(graph, ticker, as_of_date, progress_cb)

    confidence = None
    conf_scores = final_state.get("confidence_scores")
    if isinstance(conf_scores, dict):
        confidence = conf_scores.get("overall")

    result: Dict[str, Any] = {
        "ticker": ticker,
        "mode": mode,
        "as_of_date": as_of_date,
        "analysts": analysts,
        "signal": signal,
        "decision": final_state.get("final_trade_decision", "HOLD"),
        "confidence": confidence,
    }
    if include_state:
        result["state"] = final_state
    return result


def _stream_graph(
    graph: "Any",
    ticker: str,
    as_of_date: str,
    progress_cb: ProgressCallback,
):
    """Stream the graph node-by-node, invoking ``progress_cb`` per node.

    Returns ``(final_state, processed_signal)`` to mirror ``propagate``.  Note
    this path does NOT perform ``propagate``'s audit write-through; callers that
    need the audit row should pass ``progress_cb=None``.
    """
    graph.ticker = ticker
    init_state = graph.propagator.create_initial_state(ticker, as_of_date)
    args = graph.propagator.get_graph_args()
    args["stream_mode"] = "updates"

    final_state: Dict[str, Any] = {}
    for chunk in graph.graph.stream(init_state, **args):
        if not isinstance(chunk, dict):
            continue
        for node, delta in chunk.items():
            if isinstance(delta, dict):
                final_state.update(delta)
                try:
                    progress_cb(node, "completed", list(delta.keys()))
                except Exception:  # never let a UI callback break the run
                    logger.debug("progress_cb raised; ignoring", exc_info=True)

    signal = graph.process_signal(final_state.get("final_trade_decision", "HOLD"))
    return final_state, signal


# ---------------------------------------------------------------------------
# Backtest mode
# ---------------------------------------------------------------------------
def _run_backtest(
    ticker: str,
    *,
    start_date: str,
    end_date: str,
    analysts: List[str],
    initial_capital: float,
    interval_days: int,
    train_end_date: Optional[str],
    resume: bool,
) -> Dict[str, Any]:
    """Run a single-decision scenario backtest (no look-ahead).

    Delegates to :func:`scripts.scenario_backtest.run_single_scenario`: the
    agents see only data on/before ``start_date``; outcome is scored at
    ``end_date``. ``interval_days`` / ``resume`` / ``train_end_date`` are
    ignored (kept for CLI compatibility).
    """
    if not start_date or not end_date:
        raise ValueError("backtest mode requires both start_date and end_date")
    if interval_days != 20 or resume or train_end_date:
        logger.info(
            "[runner] note: interval/resume/train_end are ignored in scenario backtest mode"
        )

    from scripts.scenario_backtest import run_single_scenario

    logger.info(
        "[runner] mode=backtest (scenario) ticker=%s window=%s->%s capital=%.0f",
        ticker, start_date, end_date, initial_capital,
    )

    rec = run_single_scenario(
        ticker,
        start=start_date,
        end=end_date,
        initial_capital=initial_capital,
    )
    if not rec:
        raise RuntimeError(
            f"scenario backtest produced no result for {ticker} "
            f"({start_date} -> {end_date})"
        )

    report_path = _latest_report_path(ticker)
    return {
        "ticker": ticker,
        "mode": MODE_BACKTEST,
        "start_date": start_date,
        "end_date": end_date,
        "analysts": ["market", "fundamentals"],
        "prediction": rec.get("predicted_direction") or rec.get("decision"),
        "executed_action": rec.get("decision"),
        "stock_return_pct": rec.get("stock_return_pct"),
        "follow_return_pct": rec.get("follow_return_pct"),
        "index_return_pct": rec.get("index_return_pct"),
        "prediction_correct": rec.get("prediction_correct"),
        "session_id": rec.get("session_id"),
        "report_path": str(report_path) if report_path else None,
    }


def _latest_report_path(ticker: str):
    """Return the newest ``report_<ticker>_*.json`` the backtester just wrote."""
    from pathlib import Path

    results_dir = Path(__file__).resolve().parent.parent / "backtest_results"
    if not results_dir.exists():
        return None
    reports = sorted(
        results_dir.glob(f"report_{ticker}_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return reports[0] if reports else None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def run_analysis(
    ticker: str,
    mode: str = MODE_FULL,
    *,
    as_of_date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    analysts: Optional[List[str]] = None,
    config: Optional[Dict[str, Any]] = None,
    initial_capital: float = 1_000_000.0,
    interval_days: int = 20,
    train_end_date: Optional[str] = None,
    resume: bool = False,
    max_debate_rounds: int = 1,
    max_risk_rounds: int = 1,
    debug: bool = False,
    include_state: bool = False,
    progress_cb: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    """Run the EGX multi-agent system in one of three modes.

    Args:
        ticker: EGX ticker (normalised to ``SYMBOL.CA``).
        mode: ``"quick"``, ``"full"``, or ``"backtest"``.
        as_of_date: point-in-time date for ``quick``/``full`` (defaults to the
            last trading day). Ignored for ``backtest``.
        start_date / end_date: required for ``backtest``; the historical window.
        analysts: analyst subset. Defaults per mode (quick -> market+fundamentals,
            full/backtest -> all four).
        config: optional base config dict (defaults to ``DEFAULT_CONFIG``).
        initial_capital / interval_days / train_end_date / resume: backtest knobs.
        max_debate_rounds / max_risk_rounds: graph debate depth (quick/full).
        debug: verbose graph logging.
        include_state: include the full LangGraph ``final_state`` in the result
            (graph modes only). Large — off by default.
        progress_cb: optional per-node callback for streaming UIs (graph modes).

    Returns:
        A normalised result dict. Graph modes return ``signal``/``decision``/
        ``confidence``; backtest returns ``report_path``.

    Raises:
        ValueError: on an unknown mode or missing backtest dates.
    """
    mode = (mode or MODE_FULL).strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {VALID_MODES}")

    ticker = normalize_egx_ticker(ticker)

    if mode == MODE_BACKTEST:
        return _run_backtest(
            ticker,
            start_date=start_date,
            end_date=end_date,
            analysts=["market", "fundamentals"],
            initial_capital=initial_capital,
            interval_days=interval_days,
            train_end_date=train_end_date,
            resume=resume,
        )

    # quick / full — live, point-in-time analysis.
    if analysts is None:
        analysts = QUICK_ANALYSTS if mode == MODE_QUICK else FULL_ANALYSTS
    resolved_date = as_of_date or _last_trading_day()

    return _run_graph(
        ticker,
        mode=mode,
        as_of_date=resolved_date,
        analysts=analysts,
        config=config,
        max_debate_rounds=max_debate_rounds,
        max_risk_rounds=max_risk_rounds,
        debug=debug,
        include_state=include_state,
        progress_cb=progress_cb,
    )
