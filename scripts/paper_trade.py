"""Forward paper-trading CLI — record live decisions, score them once matured.

This is the only look-ahead-free way to evaluate the LLM system: a decision is
recorded *today* (when the future does not exist) and scored weeks later against the
realized price. Run ``record`` on a schedule (e.g. daily/weekly), ``score``
periodically, and ``report`` any time to see the cost-net, Wilson-CI scorecard.

Usage:
    # Record today's decision for a set of tickers (needs LLM API keys + network)
    python scripts/paper_trade.py record --tickers COMI.CA,ETEL.CA --horizon 20

    # Score every decision whose horizon has elapsed (needs network for prices)
    python scripts/paper_trade.py score

    # Print the current scorecard (no network)
    python scripts/paper_trade.py report

The harness logic + persistence live in ``tradingagents/paper_trading.py``; this
file is the glue to the TradingAgentsGraph and the yfinance price feed.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Load repo .env so the live graph (record) has LLM API keys regardless of CWD —
# this CLI is invoked directly / by the scheduled task, not via an entry script
# that already calls load_dotenv().
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass

from tradingagents.paper_trading import (
    PaperTradingStore,
    record_decision,
    score_matured,
    compute_metrics,
    DEFAULT_HORIZON_DAYS,
    DEFAULT_STORE_PATH,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("paper_trade")


# ─────────────────────────────────────────────────────────────────────────────
# Price feed — fetch each ticker's history ONCE, cache, and derive everything
# from it. The first version hit yfinance 3x per ticker (entry + baselines +
# factor) which, across a 10-ticker run, tripped rate-limiting and returned empty
# (the seed run recorded zero decisions). One cached fetch per ticker + retry with
# backoff fixes that.
# ─────────────────────────────────────────────────────────────────────────────

_HISTORY_CACHE: Dict[str, List[Tuple[str, float]]] = {}


def _fetch_history(ticker: str, period: str = "2y", retries: int = 3) -> List[Tuple[str, float]]:
    """Fetch (date, close) ascending for ~2 years via yfinance, with retry/backoff."""
    import time
    try:
        import yfinance as yf
    except Exception as exc:  # pragma: no cover
        logger.warning("yfinance import failed: %s", exc)
        return []
    for attempt in range(1, retries + 1):
        try:
            df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
            if df is not None and not df.empty and "Close" in df.columns:
                closes = df["Close"].dropna()
                out: List[Tuple[str, float]] = []
                for idx, val in closes.items():
                    try:
                        price = float(val.iloc[0]) if hasattr(val, "iloc") else float(val)
                    except (TypeError, ValueError):
                        continue
                    out.append((str(idx)[:10], price))
                if out:
                    return out
            logger.warning("yfinance empty for %s (attempt %d/%d)", ticker, attempt, retries)
        except Exception as exc:  # pragma: no cover — network path
            logger.warning("yfinance fetch failed for %s (attempt %d/%d): %s",
                           ticker, attempt, retries, exc)
        time.sleep(2 * attempt)  # linear backoff: 2s, 4s, 6s
    return []


def _series_to_pairs(series) -> List[Tuple[str, float]]:
    out: List[Tuple[str, float]] = []
    for idx, val in series.dropna().items():
        try:
            out.append((str(idx)[:10], float(val)))
        except (TypeError, ValueError):
            continue
    return out


def prefetch_history(tickers: List[str], period: str = "2y", retries: int = 3) -> None:
    """Fetch ALL tickers in ONE yfinance request and populate the cache.

    Ten separate single-ticker downloads get rate-limited fast; a single batch
    request is far friendlier and is what made the seed run reliable. Falls back
    to per-ticker fetch (via ``_history``) for anything the batch missed.
    """
    import time
    need = [t for t in dict.fromkeys(tickers) if t not in _HISTORY_CACHE]
    if not need:
        return
    try:
        import yfinance as yf
    except Exception:
        return
    for attempt in range(1, retries + 1):
        try:
            df = yf.download(need, period=period, progress=False,
                             auto_adjust=True, group_by="column")
            if df is not None and not df.empty:
                # Multi-ticker → df["Close"] is a DataFrame (cols=tickers);
                # single-ticker → a Series.
                close = df["Close"] if "Close" in getattr(df.columns, "get_level_values", lambda *_: df.columns)(0) else None
                got = 0
                if close is not None and hasattr(close, "columns"):
                    for t in need:
                        if t in close.columns:
                            pairs = _series_to_pairs(close[t])
                            if pairs:
                                _HISTORY_CACHE[t] = pairs
                                got += 1
                elif close is not None:  # single ticker Series
                    pairs = _series_to_pairs(close)
                    if pairs:
                        _HISTORY_CACHE[need[0]] = pairs
                        got += 1
                if got:
                    logger.info("prefetch_history: %d/%d tickers via batch", got, len(need))
                    return
            logger.warning("prefetch_history: batch empty (attempt %d/%d)", attempt, retries)
        except Exception as exc:  # pragma: no cover — network path
            logger.warning("prefetch_history: batch failed (attempt %d/%d): %s", attempt, retries, exc)
        time.sleep(3 * attempt)


def _history(ticker: str) -> List[Tuple[str, float]]:
    """Cached (date, close) history for the current process."""
    if ticker not in _HISTORY_CACHE:
        _HISTORY_CACHE[ticker] = _fetch_history(ticker)
    return _HISTORY_CACHE[ticker]


def _last_close_on_or_before(ticker: str, as_of: str) -> Optional[float]:
    """Entry price: the most recent close on/before ``as_of``."""
    rows = [(d, p) for d, p in _history(ticker) if d <= as_of]
    return rows[-1][1] if rows else None


def _first_close_on_or_after(ticker: str, on_or_after: str) -> Optional[float]:
    """Exit price: the first close on/after the target evaluation date (else None)."""
    rows = [(d, p) for d, p in _history(ticker) if d >= on_or_after]
    return rows[0][1] if rows else None


def _trailing_closes(ticker: str, as_of: str, lookback_days: int = 420) -> List[float]:
    """Closes up to and INCLUDING ``as_of`` (no future bars) — feed for factors."""
    return [p for d, p in _history(ticker) if d <= as_of]


# ─────────────────────────────────────────────────────────────────────────────
# Graph runner (lazy import)
# ─────────────────────────────────────────────────────────────────────────────


def _run_graph_decision(ticker: str, run_date: str, analysts: List[str]):
    """Run the TradingAgentsGraph once and return (decision, confidence, low_liquidity)."""
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.graph.signal_processing import SignalProcessor

    graph = TradingAgentsGraph(selected_analysts=analysts, debug=False)
    final_state, _ = graph.propagate(ticker, run_date)

    raw = final_state.get("final_trade_decision", "HOLD")
    decision = SignalProcessor().process_signal(raw if isinstance(raw, str) else str(raw))
    confidence = (final_state.get("confidence_scores") or {}).get("overall")
    try:
        confidence = float(confidence) / 100.0 if confidence and confidence > 1.0 else confidence
    except (TypeError, ValueError):
        confidence = None
    low_liquidity = bool(final_state.get("low_liquidity", False))
    return decision, confidence, low_liquidity


# ─────────────────────────────────────────────────────────────────────────────
# Subcommands
# ─────────────────────────────────────────────────────────────────────────────


def cmd_record(args) -> None:
    store = PaperTradingStore(args.store)
    run_date = args.date or datetime.now().strftime("%Y-%m-%d")
    analysts = [a.strip() for a in args.analysts.split(",") if a.strip()]
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]

    import time
    from tradingagents.eval.baselines import STRATEGIES

    prefetch_history(tickers)  # one batch yfinance request for the whole universe
    n_llm = n_base = 0
    for i, ticker in enumerate(tickers):
        # One cached price fetch per ticker drives entry + baselines + factor.
        entry_price = _last_close_on_or_before(ticker, run_date)
        if entry_price is None:
            logger.warning("record: no price data for %s; skipping", ticker)
            continue
        closes = _trailing_closes(ticker, run_date)

        # --- LLM arm (the expensive, rate-limited one) ------------------------
        low_liq = False
        if not args.no_llm:
            try:
                decision, confidence, low_liq = _run_graph_decision(ticker, run_date, analysts)
                record_decision(
                    store, ticker=ticker, run_date=run_date, decision=decision,
                    entry_price=entry_price, confidence=confidence, low_liquidity=low_liq,
                    horizon_days=args.horizon, strategy="llm", force=args.force,
                )
                n_llm += 1
            except Exception as exc:
                # Do NOT skip the ticker — baselines/factor still seed without the LLM.
                logger.error("record: LLM graph failed for %s (recording baselines anyway): %s",
                             ticker, exc)
            # Pace between graph runs to avoid API rate-limits (429).
            if args.delay_seconds and i < len(tickers) - 1:
                time.sleep(args.delay_seconds)

        # --- deterministic baselines (cheap; always recorded if we have prices) ---
        if args.baselines and closes:
            for name, fn in STRATEGIES.items():
                record_decision(
                    store, ticker=ticker, run_date=run_date, decision=fn(closes),
                    entry_price=entry_price, low_liquidity=low_liq,
                    horizon_days=args.horizon, strategy=name, force=args.force,
                )
                n_base += 1

    # Cross-sectional factor strategy: ranks ALL tickers against each other, so it
    # runs once after the per-ticker loop (needs >= 2 names with enough history).
    if args.factor_rank:
        _record_factor_strategy(store, tickers, run_date, args.horizon,
                                args.factor_top_quantile, args.force)

    logger.info("record: done — %d ticker(s), %d LLM + %d baseline decisions recorded.",
                len(tickers), n_llm, n_base)


def _record_factor_strategy(store, tickers, run_date, horizon, top_quantile, force) -> None:
    # build_factor_inputs pulls point-in-time fundamentals (value/quality) and
    # degrades to a price-only (momentum+low-vol) model when they're unavailable.
    from tradingagents.factors import (
        build_factor_inputs, rank_universe, rank_to_decisions, sector_map_for,
    )

    inputs = []
    entry_by_ticker = {}
    for ticker in tickers:
        closes = _trailing_closes(ticker, run_date, lookback_days=420)  # ~12mo for momentum
        if len(closes) < 60:
            logger.warning("factor-rank: insufficient history for %s; skipping", ticker)
            continue
        inputs.append(build_factor_inputs(ticker, closes, run_date))
        entry_by_ticker[ticker] = closes[-1]

    if len(inputs) < 2:
        logger.warning("factor-rank: need >=2 tickers with history; skipping factor strategy")
        return

    # Sector-neutral z-scoring so e.g. bank leverage isn't penalized vs industrials.
    sector_map = sector_map_for([fi.ticker for fi in inputs])
    scores = rank_universe(inputs, sector_map=sector_map)
    decisions = rank_to_decisions(scores, top_quantile=top_quantile)
    for ticker, dec in decisions.items():
        record_decision(
            store, ticker=ticker, run_date=run_date, decision=dec,
            entry_price=entry_by_ticker[ticker], horizon_days=horizon,
            strategy="factor", force=force,
        )
    logger.info("factor-rank: recorded %d factor decision(s) across the universe.", len(decisions))


def cmd_score(args) -> None:
    store = PaperTradingStore(args.store)
    open_tickers = sorted({d.ticker for d in store.load() if d.status == "OPEN"})
    if open_tickers:
        prefetch_history(open_tickers)  # one batch request for all open names
    closed = score_matured(store, _first_close_on_or_after)
    logger.info("score: closed %d newly-matured decision(s).", len(closed))


def _print_strategy_line(name: str, m: dict) -> None:
    hr = m.get("buy_hit_rate")
    if hr is None:
        print(f"  {name:<14} no closed BUY decisions yet")
        return
    ci = m.get("buy_hit_rate_ci95") or [None, None]
    decided = m["buy_wins"] + m["buy_losses"]
    line = (
        f"  {name:<14} hit {hr:.1%} (CI {ci[0]:.0%}-{ci[1]:.0%}, n={decided}) | "
        f"mean net {m.get('buy_mean_net_return') or 0:+.2%}"
    )
    if m.get("mean_net_excess_vs_benchmark") is not None:
        line += f" | excess {m['mean_net_excess_vs_benchmark']:+.2%}"
    print(line)


def cmd_report(args) -> None:
    from tradingagents.paper_trading import group_metrics_by_strategy

    store = PaperTradingStore(args.store)
    decisions = store.load()
    overall = compute_metrics(decisions, benchmark_return=args.benchmark_return)
    print(json.dumps(overall, indent=2, default=str))

    if overall.get("n_closed", 0) == 0:
        print("\nNo closed decisions yet -- record decisions and re-run `score` after the horizon elapses.")
        return

    by_strat = group_metrics_by_strategy(decisions, benchmark_return=args.benchmark_return)
    print("\nBy strategy (the LLM must beat these baselines net of cost to earn its keep):")
    for name in ["llm"] + [s for s in by_strat if s != "llm"]:
        if name in by_strat:
            _print_strategy_line(name, by_strat[name])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Forward paper-trading harness for the EGX agent system.")
    p.add_argument("--store", default=DEFAULT_STORE_PATH, help="Path to the JSONL decision store.")
    sub = p.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("record", help="Run the graph today and record OPEN decisions.")
    pr.add_argument("--tickers", required=True, help="Comma-separated EGX tickers (e.g. COMI.CA,ETEL.CA).")
    pr.add_argument("--date", default=None, help="As-of date YYYY-MM-DD (default: today).")
    pr.add_argument("--horizon", type=int, default=DEFAULT_HORIZON_DAYS, help="Scoring horizon in trading days.")
    pr.add_argument("--analysts", default="market,fundamentals,news,social", help="Analyst team to run.")
    pr.add_argument("--baselines", action="store_true",
                    help="Also record deterministic baselines (momentum, buy_and_hold, sma_crossover) per ticker.")
    pr.add_argument("--factor-rank", action="store_true",
                    help="Also record the cross-sectional factor strategy ranked across all --tickers.")
    pr.add_argument("--factor-top-quantile", type=float, default=0.30,
                    help="Top fraction of the factor-ranked universe to BUY (default 0.30).")
    pr.add_argument("--no-llm", action="store_true",
                    help="Skip the LLM graph; record only baselines + factor (cheap, no API).")
    pr.add_argument("--delay-seconds", type=float, default=10.0,
                    help="Pause between LLM graph runs to avoid API rate-limits (default 10s).")
    pr.add_argument("--force", action="store_true", help="Overwrite an existing same-day decision.")
    pr.set_defaults(func=cmd_record)

    ps = sub.add_parser("score", help="Score every decision whose horizon has elapsed.")
    ps.set_defaults(func=cmd_score)

    prep = sub.add_parser("report", help="Print the current scorecard.")
    prep.add_argument("--benchmark-return", type=float, default=None,
                      help="Optional benchmark buy-and-hold return (fraction) to compute net excess.")
    prep.set_defaults(func=cmd_report)
    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
