"""Agent-facing entry point for the v2 social sentiment pipeline.

Two key responsibilities:

1. **Shape conversion.** The internal v2 payload uses our own structure
   (`market_sentiment`/`per_stock_sentiment` with `weighted_sentiment`).
   The LangGraph social-media analyst (see
   `tradingagents/agents/analysts/social_media_analyst.py::_try_build_*`)
   expects a flatter shape with `score`, `confidence`, `status`, `regime`,
   etc. This module emits that shape so the analyst can reconstruct typed
   `MarketSentiment` / `SectorSentiment` / `MacroSentiment` objects from
   `prefetched_social_sentiment`.

2. **Backtest honesty.** Live Apify/Reddit only return current posts.
   If the caller asks for a date more than 1 day in the past, we do NOT
   silently scrape today's data and pretend it was historical. Instead:

     a. Try Postgres archive (social_v2_posts) for posts in the requested
        window. If enough posts are present, run the same aggregator on
        them and return a SIGNAL with a clear `historical_archive` marker.
     b. Otherwise return NO_SIGNAL with reason="historical_proxy_only"
        so the agent's blend forces pass-through (1.0 × 1.0).

This is the only place where the proxy/non-proxy decision lives.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import cache as v2_cache
from .pipeline import run_pipeline

log = logging.getLogger("tradingagents.social_v2.signal_adapter")

BACKTEST_TOLERANCE_DAYS = 1


def _today_utc_date():
    return datetime.now(timezone.utc).date()


def _parse_date(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _is_backtest(curr_date: str) -> bool:
    parsed = _parse_date(curr_date)
    if parsed is None:
        return False
    return (_today_utc_date() - parsed).days > BACKTEST_TOLERANCE_DAYS


def _sector_for_ticker(ticker: str) -> str:
    try:
        from tradingagents.sentiment.taxonomy import ticker_to_sector
        return str(ticker_to_sector(ticker).value)
    except Exception:
        return "UNKNOWN"


def _sector_members_of_ticker(ticker: str) -> frozenset:
    """Return the set of bare-symbol peers in the same EGX sector as `ticker`.

    Used by the 3-tier fallback in `_to_agent_shape`: when a stock has no
    direct social coverage, we average its sector-mates' sentiment.
    """
    try:
        from tradingagents.sentiment.taxonomy import (
            ticker_to_sector,
            members_of,
            SectorEnum,
        )
        sector = ticker_to_sector(ticker)
        if sector == SectorEnum.UNKNOWN:
            return frozenset()
        return members_of(sector)
    except Exception:
        return frozenset()


def _aggregate_sector_block(
    ticker: str,
    per_symbol_full: dict,
    sector_name: str,
) -> dict:
    """Average per-stock signals across sector peers (Tier 2 fallback).

    Only includes peers that produced an actual signal (confidence > 0).
    Weights each peer by its post count so a single noisy ticker doesn't
    dominate the sector view.
    """
    peers = _sector_members_of_ticker(ticker)
    if not peers:
        return {
            "status": "NO_SIGNAL",
            "sector": sector_name,
            "score": None,
            "confidence": 0.0,
            "n_posts": 0,
            "n_distinct_days": 0,
            "n_peers_contributing": 0,
        }

    bare = ticker.upper().replace(".CA", "")
    total_posts = 0
    weighted_score_num = 0.0
    weighted_conf_num = 0.0
    peer_count = 0
    days_set: set = set()

    for peer in peers:
        if peer == bare:
            continue  # exclude the stock itself
        entry = per_symbol_full.get(peer) or {}
        peer_conf = float(entry.get("confidence", 0.0) or 0.0)
        peer_n = int(entry.get("n", 0) or 0)
        if peer_conf <= 0.0 or peer_n <= 0:
            continue
        peer_score = float(entry.get("weighted_sentiment", 0.0) or 0.0)
        weighted_score_num += peer_score * peer_n
        weighted_conf_num += peer_conf * peer_n
        total_posts += peer_n
        peer_count += 1
        days_set.add(int(entry.get("n_distinct_days", 0) or 0))

    if peer_count == 0 or total_posts == 0:
        return {
            "status": "NO_SIGNAL",
            "sector": sector_name,
            "score": None,
            "confidence": 0.0,
            "n_posts": 0,
            "n_distinct_days": 0,
            "n_peers_contributing": 0,
        }

    return {
        "status": "SIGNAL",
        "sector": sector_name,
        "score": round(weighted_score_num / total_posts, 4),
        "confidence": round(weighted_conf_num / total_posts, 4),
        "n_posts": total_posts,
        "n_distinct_days": max(days_set) if days_set else 0,
        "n_peers_contributing": peer_count,
    }


def _regime_from_score(score: float, confidence: float) -> str:
    if confidence < 0.2:
        return "NEUTRAL"
    if score >= 0.5:
        return "EUPHORIA"
    if score >= 0.15:
        return "GREED"
    if score <= -0.5:
        return "PANIC"
    if score <= -0.15:
        return "FEAR"
    return "NEUTRAL"


def _macro_direction_from_score(score: float, confidence: float) -> str:
    if confidence < 0.2:
        return "NO_SIGNAL"
    if score >= 0.15:
        return "RISK_ON"
    if score <= -0.15:
        return "RISK_OFF"
    return "NEUTRAL"


def _build_no_signal_payload(ticker: str, reason: str, gate: str) -> dict:
    """Emit a payload shaped so the analyst's _try_build_* builders return
    NO_SIGNAL objects (forces blend to pass-through 1.0 / 1.0)."""
    return {
        "ticker": ticker,
        "status": "NO_SIGNAL",
        "tier": "none",
        "reason": reason,
        "market_sentiment": {
            "status": "NO_SIGNAL",
            "score": None,
            "confidence": 0.0,
            "regime": "NO_SIGNAL",
            "n_posts": 0,
            "n_distinct_sources": 0,
        },
        "macro_sentiment": {"composite_regime": "NO_SIGNAL"},
        "sector_sentiment": {
            "status": "NO_SIGNAL",
            "sector": _sector_for_ticker(ticker),
            "score": None,
            "confidence": 0.0,
            "n_posts": 0,
            "n_distinct_days": 0,
            "tier": "none",
        },
        "per_stock_sentiment": {},
        "metadata": {
            "gate": gate,
            "data_quality_note": reason,
            "sentiment_tier": "none",
        },
    }


def _to_agent_shape(ticker: str, pipeline_output: dict, source_label: str) -> dict:
    """Convert internal v2 payload to the agent's expected JSON shape."""
    market = pipeline_output.get("market_sentiment", {}) or {}
    per_symbol_full = pipeline_output.get("per_symbol_full", {}) or {}
    per_stock = pipeline_output.get("per_stock_sentiment", {}) or {}

    bare = ticker.upper().replace(".CA", "")
    stock_entry = per_stock.get(bare) or per_symbol_full.get(bare)

    market_n = int(market.get("n", 0))
    market_score = float(market.get("weighted_sentiment", 0.0))
    market_confidence = float(market.get("confidence", 0.0))
    market_n_sources = int(per_symbol_full.get("EGX_MARKET", {}).get("n_distinct_sources", 0))

    market_block: dict
    if market_confidence <= 0.0 or market_n == 0:
        market_block = {
            "status": "NO_SIGNAL",
            "score": None,
            "confidence": 0.0,
            "regime": "NO_SIGNAL",
            "n_posts": market_n,
            "n_distinct_sources": market_n_sources,
        }
    else:
        market_block = {
            "status": "SIGNAL",
            "score": round(market_score, 4),
            "confidence": round(market_confidence, 4),
            "regime": _regime_from_score(market_score, market_confidence),
            "n_posts": market_n,
            "n_distinct_sources": market_n_sources,
        }

    # ── 3-tier fallback for sector_sentiment block ───────────────────────────
    # Tier 1: direct stock coverage     → use stock_entry  (tier="stock")
    # Tier 2: sector peer average        → aggregate sector mates (tier="sector")
    # Tier 3: overall market sentiment   → copy market_block (tier="market")
    # The agent reads `tier` to know how strong the signal-to-ticker linkage is.
    sector_name = _sector_for_ticker(ticker)
    tier_used = "none"

    if stock_entry and float(stock_entry.get("confidence", 0.0)) > 0:
        sector_score = float(stock_entry.get("weighted_sentiment", 0.0))
        sector_block = {
            "status": "SIGNAL",
            "score": round(sector_score, 4),
            "confidence": round(float(stock_entry.get("confidence", 0.0)), 4),
            "sector": sector_name,
            "n_posts": int(stock_entry.get("n", 0)),
            "n_distinct_days": int(stock_entry.get("n_distinct_days", 0)),
            "tier": "stock",
        }
        tier_used = "stock"
    else:
        peer_block = _aggregate_sector_block(ticker, per_symbol_full, sector_name)
        if peer_block.get("status") == "SIGNAL":
            sector_block = {**peer_block, "tier": "sector"}
            tier_used = "sector"
        elif market_block.get("status") == "SIGNAL":
            sector_block = {
                "status": "SIGNAL",
                "score": market_block["score"],
                "confidence": round(market_block["confidence"] * 0.5, 4),  # dampened
                "sector": sector_name,
                "n_posts": market_block["n_posts"],
                "n_distinct_days": 0,
                "tier": "market",
                "note": "no stock or sector coverage — proxied from market overall",
            }
            tier_used = "market"
        else:
            sector_block = {
                "status": "NO_SIGNAL",
                "sector": sector_name,
                "score": None,
                "confidence": 0.0,
                "n_posts": 0,
                "n_distinct_days": 0,
                "tier": "none",
            }
            tier_used = "none"

    macro_block = {
        "composite_regime": _macro_direction_from_score(market_score, market_confidence),
    }

    metadata = dict(pipeline_output.get("metadata", {}) or {})
    metadata["sentiment_tier"] = tier_used
    metadata["sentiment_fallback_note"] = {
        "stock": "Direct stock coverage — signal applies to this ticker.",
        "sector": f"No direct stock coverage; using {sector_name} sector peer average.",
        "market": "No stock or sector coverage; proxied from overall market sentiment (confidence halved).",
        "none": "No social signal available at any tier.",
    }[tier_used]

    return {
        "ticker": ticker,
        "status": "SIGNAL" if sector_block.get("status") == "SIGNAL" else "OK",
        "source": source_label,
        "tier": tier_used,
        "market_sentiment": market_block,
        "macro_sentiment": macro_block,
        "sector_sentiment": sector_block,
        "per_stock_sentiment": per_stock,
        "metadata": metadata,
    }


def _try_archive_replay(ticker: str, curr_date: str, look_back_days: int) -> Optional[dict]:
    """If Postgres has enough historical posts in the window, aggregate
    them and return the agent-shape payload. Else None."""
    try:
        import psycopg2  # type: ignore
    except Exception:
        return None
    url = os.getenv("POSTGRES_URL")
    if not url:
        return None

    end_date = _parse_date(curr_date)
    if end_date is None:
        return None
    start_date = end_date - timedelta(days=look_back_days)

    try:
        conn = psycopg2.connect(url)
        conn.autocommit = True
    except Exception as exc:
        log.debug("archive replay: connect failed: %s", exc)
        return None

    rows: list = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT platform, source, url, post_timestamp, text, engagement,
                       symbols, intents, content_label, sentiment_score, sentiment_label
                FROM social_v2_posts
                WHERE post_timestamp >= %s AND post_timestamp < %s
                  AND sentiment_score IS NOT NULL
                """,
                (start_date.isoformat(), (end_date + timedelta(days=1)).isoformat()),
            )
            rows = list(cur.fetchall())
    except Exception as exc:
        log.debug("archive replay: query failed: %s", exc)
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if len(rows) < 20:
        return None

    # Rebuild ScoredPost-equivalent records and reuse the aggregator.
    from .aggregator import ScoredPost, aggregate, split_outputs
    from .entities import Mention

    scored = []
    for row in rows:
        (platform, source, url, ts, text, engagement,
         symbols, intents, content_label, sscore, slabel) = row
        mentions = [Mention(symbol=s, confidence=0.85) for s in (symbols or [])]
        scored.append(
            ScoredPost(
                text=text or "",
                url=url or "",
                platform=platform or "unknown",
                source=source or "archive",
                timestamp=ts.isoformat() if ts else "",
                engagement=int(engagement or 0),
                mentions=mentions,
                intent={"intents": list(intents or [])},
                content={"label": content_label or "OTHER",
                         "weight": 0.6 if content_label == "NEWS" else 1.0},
                sentiment={"label": slabel or "neutral",
                           "score": float(sscore or 0.0)},
            )
        )

    per_symbol = aggregate(scored)
    output = split_outputs(per_symbol, total_posts=len(rows), used_posts=len(rows))
    output["per_symbol_full"] = per_symbol
    log.info(
        "Archive replay for %s [%s..%s]: %d rows -> market=%s",
        ticker, start_date, end_date, len(rows),
        output["market_sentiment"]["label"],
    )
    return _to_agent_shape(ticker, output, source_label="historical_archive")


def fetch_v2_signal(
    ticker: str,
    curr_date: str,
    look_back_days: int = 7,
    use_cache: bool = True,
) -> dict:
    """Agent-facing entry point.

    Args:
        ticker: EGX ticker (with or without `.CA`).
        curr_date: YYYY-MM-DD. Used for backtest detection and archive lookup.
        look_back_days: window for archive replay.
        use_cache: when True, returns a recent identical-ticker payload if
            cached (30 min TTL). Set False to force a fresh run.

    Returns:
        dict matching the shape consumed by
        `social_media_analyst._try_build_market_sentiment` etc.
    """
    cache_key = f"signal:{ticker.upper()}:{curr_date}:{look_back_days}"
    if use_cache:
        cached = v2_cache.signal_get(cache_key)
        if cached:
            log.info("fetch_v2_signal: cache hit for %s @ %s", ticker, curr_date)
            return cached

    if _is_backtest(curr_date):
        # Try historical archive first; fall back to honest NO_SIGNAL.
        replay = _try_archive_replay(ticker, curr_date, look_back_days)
        if replay is not None:
            v2_cache.signal_set(cache_key, replay)
            return replay
        log.info(
            "fetch_v2_signal: backtest date %s, no archive data — returning NO_SIGNAL",
            curr_date,
        )
        payload = _build_no_signal_payload(
            ticker,
            reason="historical_proxy_only — no archived social posts in window",
            gate="backtest.no_archive",
        )
        v2_cache.signal_set(cache_key, payload)
        return payload

    try:
        pipeline_output = run_pipeline()
    except Exception as exc:
        log.exception("Pipeline failed for %s: %s", ticker, exc)
        return _build_no_signal_payload(
            ticker, reason=f"pipeline_failure: {exc}", gate="pipeline.exception"
        )

    payload = _to_agent_shape(ticker, pipeline_output, source_label="live_v2")
    v2_cache.signal_set(cache_key, payload)
    return payload


def fetch_v2_signal_json(ticker: str, curr_date: str, look_back_days: int = 7) -> str:
    """Same as fetch_v2_signal but returns the JSON string the prefetcher
    stores in `prefetched_social_sentiment`."""
    payload = fetch_v2_signal(ticker, curr_date, look_back_days)
    return json.dumps(payload, ensure_ascii=False, default=str)
