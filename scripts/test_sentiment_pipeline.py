#!/usr/bin/env python
"""
scripts/test_sentiment_pipeline.py
EGX Sentiment Pipeline — Manual Test Harness
=============================================

Runs the redesigned sentiment subsystem (Layers A0/A/B/C/E) independently —
NO LLM calls, NO full LangGraph graph — so a human operator can:

  • Observe every stage live with gate pass/fail reasoning
  • Inspect intermediate typed objects and confidence calculations
  • Understand why signals are generated or rejected (NO_SIGNAL cause surfaced)
  • Verify how sentiment blend multipliers affect final trading predictions

Usage
-----
  # Built-in positive signal demo (default):
  python scripts/test_sentiment_pipeline.py

  # NO_SIGNAL scenario (sparse data):
  python scripts/test_sentiment_pipeline.py --scenario no_signal

  # Stock-vs-market conflict (contradicts_market=True):
  python scripts/test_sentiment_pipeline.py --scenario conflict --verbose

  # Small-cap SMALL tier demo:
  python scripts/test_sentiment_pipeline.py --scenario low_liquidity

  # Arabic + English multilingual posts:
  python scripts/test_sentiment_pipeline.py --scenario multilingual

  # Load raw posts from a v2 pipeline results JSON file:
  python scripts/test_sentiment_pipeline.py --ticker COMI.CA \\
      --raw-posts-file scripts/twitter_pipeline/v2/logs/results_20260501.json

  # Full verbose + save JSON report:
  python scripts/test_sentiment_pipeline.py --scenario positive \\
      --verbose --save-report

  # List all built-in scenarios:
  python scripts/test_sentiment_pipeline.py --list-scenarios

Architecture reference: agent_docs/sentiment_architecture.md
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Path setup — ensure tradingagents is importable from any working directory
# ─────────────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ─────────────────────────────────────────────────────────────────────────────
# Logging — show INFO-level pipeline logs from the sentiment modules
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("sentiment_harness")


# ─────────────────────────────────────────────────────────────────────────────
# ANSI colour helpers (no third-party deps)
# ─────────────────────────────────────────────────────────────────────────────

_USE_COLOR = sys.stdout.isatty()


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _USE_COLOR else s


def _ok(s: str) -> str:
    return _c("92", s)      # green


def _warn(s: str) -> str:
    return _c("93", s)      # yellow


def _err(s: str) -> str:
    return _c("91", s)      # red


def _info(s: str) -> str:
    return _c("96", s)      # cyan


def _bold(s: str) -> str:
    return _c("1", s)


def _dim(s: str) -> str:
    return _c("2", s)


# ─────────────────────────────────────────────────────────────────────────────
# Print helpers
# ─────────────────────────────────────────────────────────────────────────────

_WIDTH = 74


def _rule(char: str = "─") -> str:
    return char * _WIDTH


def _section(title: str, char: str = "━") -> None:
    bar = char * _WIDTH
    print(f"\n{_bold(bar)}")
    print(f"{_bold('  ' + title)}")
    print(f"{_bold(bar)}")


def _subsection(title: str) -> None:
    print(f"\n{_info('▶ ' + title)}")
    print("  " + "─" * (_WIDTH - 2))


def _gate(name: str, passed: bool, detail: str = "") -> None:
    icon = _ok("✓ PASS") if passed else _err("✗ FAIL")
    print(f"  {icon}  {name}")
    if detail:
        for line in detail.splitlines():
            print(f"         {_dim(line)}")


def _kv(key: str, value: Any, indent: int = 2) -> None:
    spaces = " " * indent
    print(f"{spaces}{_bold(key + ':'):30s} {value}")


def _json_block(data: Any, indent: int = 4) -> None:
    spaces = " " * indent
    text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    for line in text.splitlines():
        print(f"{spaces}{_dim(line)}")


# ─────────────────────────────────────────────────────────────────────────────
# Timestamp parser (shared helper used in pre-display calculations)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_ts(ts: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp; return None on failure."""
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(ts, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _iso_ago(ref: datetime, hours_ago: float) -> str:
    """Return ISO-8601 string for (ref - hours_ago)."""
    return (ref - timedelta(hours=hours_ago)).replace(microsecond=0).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Built-in scenario data builders
# ─────────────────────────────────────────────────────────────────────────────

def build_positive_signal_data(ticker: str, ref: datetime) -> Dict[str, List]:
    """
    Scenario: positive
    All four layers emit SIGNAL. Models a bullish COMI.CA (banks sector) day
    with CBE rate-hold news, broad retail optimism, and strong COMI mentions.

    All gate thresholds are comfortably exceeded.
    """
    from tradingagents.sentiment.macro import MacroDataPoint
    from tradingagents.sentiment.market import MarketDataPoint
    from tradingagents.sentiment.sector import SectorDataPoint
    from tradingagents.sentiment.stock import StockDataPoint

    # ── Layer A0: Macro — CBE rate hold (RISK_ON) ──────────────────────────
    macro_posts = [
        MacroDataPoint(
            timestamp=_iso_ago(ref, 10),
            source_domain="cbe.org.eg",
            headline="CBE holds policy rates steady at 27.25% — signals H2 easing",
            category="RATE_DECISION",
            direction="RISK_ON",
            sentiment_score=0.42,
            weight=1.0,
            post_id="cbe_2026_001",
            url="https://cbe.org.eg/decisions/2026-05",
        ),
        MacroDataPoint(
            timestamp=_iso_ago(ref, 16),
            source_domain="reuters.com",
            headline="Egypt central bank holds rate; markets eye Q3 cut amid falling inflation",
            category="RATE_DECISION",
            direction="RISK_ON",
            sentiment_score=0.35,
            weight=0.9,
            post_id="reuters_2026_001",
        ),
        MacroDataPoint(
            timestamp=_iso_ago(ref, 20),
            source_domain="bloomberg.com",
            headline="Egypt's monetary pivot expected as CPI slides to 18-month low of 21%",
            category="INFLATION_PRINT",
            direction="RISK_ON",
            sentiment_score=0.30,
            weight=0.9,
            post_id="bloomberg_2026_001",
        ),
    ]

    # ── Layer A: Market — 55 posts, 2 platforms, >30% within 24h ──────────
    market_posts: List = []
    # 40 Facebook posts — spread over 20h (all within 24h)
    for i in range(40):
        market_posts.append(MarketDataPoint(
            timestamp=_iso_ago(ref, i * 0.5),
            platform="facebook",
            sentiment_score=0.28 + (i % 5) * 0.04,
            weight=0.80,
        ))
    # 15 Telegram posts — spread over 15h
    for i in range(15):
        market_posts.append(MarketDataPoint(
            timestamp=_iso_ago(ref, i * 1.0),
            platform="telegram",
            sentiment_score=0.12 + (i % 3) * 0.08,
            weight=0.72,
        ))

    # ── Layer B: Sector — Banks, 4 distinct days, high entity_conf ─────────
    sector_posts: List = []
    for day in range(4):
        day_ref = ref - timedelta(days=day)
        for j in range(5):
            sector_posts.append(SectorDataPoint(
                timestamp=_iso_ago(day_ref, j * 2),
                platform="facebook" if j % 2 == 0 else "telegram",
                sentiment_score=0.22 + (j % 3) * 0.07,
                weight=0.76,
                entity_confidence=0.85 if j < 3 else 0.79,
            ))

    # ── Layer C: Stock — COMI.CA MEGA tier (requires 3 distinct sources)
    # 10 Facebook + 2 Telegram + 2 Reddit = 3 distinct sources; 12 clean + 2 spam
    stock_posts: List = []
    platforms_cycle = ["facebook", "facebook", "telegram", "reddit"]
    for i in range(12):
        stock_posts.append(StockDataPoint(
            timestamp=_iso_ago(ref, i * 3.5),   # spread over 42h, well within 72h
            platform=platforms_cycle[i % len(platforms_cycle)],
            author=f"user_{i:03d}",
            sentiment_score=0.32 + (i % 4) * 0.06,
            weight=0.82,
            entity_confidence=0.88 + (i % 3) * 0.02,
            is_spam_promo=False,
        ))
    # 2 spam posts that are excluded before gate counts
    for k in range(2):
        stock_posts.append(StockDataPoint(
            timestamp=_iso_ago(ref, 1.0 + k),
            platform="facebook",
            author=f"promo_bot_{k}",
            sentiment_score=1.0,
            weight=0.1,
            entity_confidence=0.91,
            is_spam_promo=True,
        ))

    return {
        "macro": macro_posts,
        "market": market_posts,
        "sector": sector_posts,
        "stock": stock_posts,
    }


def build_no_signal_data(ticker: str, ref: datetime) -> Dict[str, List]:
    """
    Scenario: no_signal
    All four layers emit NO_SIGNAL. Models a quiet small-cap day (EFID.CA)
    with only RUMOR-domain macro posts, <50 market posts, and zero stock mentions.

    Expected gate failures:
      A0 → macro.source_credibility (all posts from RUMOR domain)
      A  → market.n_total_posts (8 posts < required 50)
      B  → sector.n_sector_posts (2 posts < required 10)
      C  → stock.n_strong_mentions (0 < required 3 for SMALL)
    """
    from tradingagents.sentiment.macro import MacroDataPoint
    from tradingagents.sentiment.market import MarketDataPoint
    from tradingagents.sentiment.sector import SectorDataPoint
    from tradingagents.sentiment.stock import StockDataPoint

    macro_posts = [
        MacroDataPoint(
            timestamp=_iso_ago(ref, 5),
            source_domain="randomegyptblog.net",   # RUMOR — fails Gate 1
            headline="شائعة: البنك المركزي سيخفض الفائدة الشهر القادم",
            category="RATE_DECISION",
            direction="RISK_ON",
            sentiment_score=0.20,
            weight=0.3,
        ),
    ]

    market_posts = [
        MarketDataPoint(
            timestamp=_iso_ago(ref, i * 2.5),
            platform="facebook",
            sentiment_score=0.10,
            weight=0.5,
        )
        for i in range(8)   # 8 posts — fails Gate 1 (need ≥50)
    ]

    sector_posts = [
        SectorDataPoint(
            timestamp=_iso_ago(ref, i * 5),
            platform="facebook",
            sentiment_score=0.05,
            weight=0.4,
            entity_confidence=0.80,
        )
        for i in range(2)   # 2 posts — fails Gate 1 (need ≥10)
    ]

    stock_posts: List = []  # 0 posts — fails Gate 1 immediately

    return {
        "macro": macro_posts,
        "market": market_posts,
        "sector": sector_posts,
        "stock": stock_posts,
    }


def build_conflict_data(ticker: str, ref: datetime) -> Dict[str, List]:
    """
    Scenario: conflict
    Market is bullish (GREED) while stock sentiment is bearish.
    Macro is RISK_OFF (geopolitical shock).
    Layer C will set contradicts_market=True.

    Real-world meaning: retail market is broadly optimistic but specific
    TMGH discussion is negative — could be project-specific news, debt concerns,
    or insider knowledge. Blend will reduce confidence and position size.
    """
    from tradingagents.sentiment.macro import MacroDataPoint
    from tradingagents.sentiment.market import MarketDataPoint
    from tradingagents.sentiment.sector import SectorDataPoint
    from tradingagents.sentiment.stock import StockDataPoint

    # Macro: geopolitical RISK_OFF corroborated by 2 TIER1 sources within 48h
    macro_posts = [
        MacroDataPoint(
            timestamp=_iso_ago(ref, 8),
            source_domain="reuters.com",
            headline="Red Sea disruption escalates; Egypt FX reserves under renewed pressure",
            category="GEOPOLITICAL",
            direction="RISK_OFF",
            sentiment_score=-0.48,
            weight=1.0,
            post_id="reuters_geo_001",
        ),
        MacroDataPoint(
            timestamp=_iso_ago(ref, 14),
            source_domain="bloomberg.com",
            headline="Egypt faces FX headwinds as regional tensions persist into Q2 2026",
            category="GEOPOLITICAL",
            direction="RISK_OFF",
            sentiment_score=-0.42,
            weight=0.9,
            post_id="bloomberg_geo_001",
        ),
    ]

    # Market: 65 posts — broadly bullish (retail buying dip)
    market_posts: List = []
    for i in range(65):
        market_posts.append(MarketDataPoint(
            timestamp=_iso_ago(ref, i * 0.35),
            platform="facebook" if i % 2 == 0 else "telegram",
            sentiment_score=0.28 + (i % 4) * 0.03,  # moderately bullish
            weight=0.72,
        ))

    # Sector (real_estate): neutral-to-slight-positive
    sector_posts: List = []
    for day in range(4):
        day_ref = ref - timedelta(days=day)
        for j in range(4):
            sector_posts.append(SectorDataPoint(
                timestamp=_iso_ago(day_ref, j * 3),
                platform="facebook",
                sentiment_score=0.04 + (j % 3) * 0.02,
                weight=0.62,
                entity_confidence=0.82,
            ))

    # Stock: TMGH (MEGA tier) bearish — requires n_strong ≥ 8, authors ≥ 5, sources ≥ 3
    stock_posts: List = []
    platforms_cycle = ["facebook", "facebook", "telegram", "reddit"]
    for i in range(10):
        stock_posts.append(StockDataPoint(
            timestamp=_iso_ago(ref, i * 5),    # spread over 50h
            platform=platforms_cycle[i % len(platforms_cycle)],
            author=f"investor_{200 + i}",
            sentiment_score=-0.38 - (i % 3) * 0.06,   # distinctly bearish
            weight=0.78,
            entity_confidence=0.89,
            is_spam_promo=False,
        ))

    return {
        "macro": macro_posts,
        "market": market_posts,
        "sector": sector_posts,
        "stock": stock_posts,
    }


def build_low_liquidity_data(ticker: str, ref: datetime) -> Dict[str, List]:
    """
    Scenario: low_liquidity
    MID-tier ticker (DOMT.CA) where the stock layer passes with near-minimum
    evidence — just above the MID threshold (5 strong, 3 authors, 2 sources).
    Demonstrates how MID thresholds are more lenient than MEGA.

    Real-world: food & beverage names on EGX rarely have ≥8 strong mentions per
    day (MEGA threshold), so MID tier gives them a chance to register a signal.
    Note: DOMT.CA is explicitly classified as MID in liquidity_tiers.py.
    """
    from tradingagents.sentiment.macro import MacroDataPoint
    from tradingagents.sentiment.market import MarketDataPoint
    from tradingagents.sentiment.sector import SectorDataPoint
    from tradingagents.sentiment.stock import StockDataPoint

    macro_posts: List = []   # no macro data — layer A0 gate 1 fails

    market_posts: List = []
    for i in range(62):
        market_posts.append(MarketDataPoint(
            timestamp=_iso_ago(ref, i * 0.38),
            platform="facebook" if i % 3 != 0 else "telegram",
            sentiment_score=0.14 + (i % 5) * 0.03,
            weight=0.64,
        ))

    sector_posts: List = []
    for day in range(4):
        day_ref = ref - timedelta(days=day)
        for j in range(4):
            sector_posts.append(SectorDataPoint(
                timestamp=_iso_ago(day_ref, j * 2),
                platform="facebook",
                sentiment_score=0.08 + (j % 3) * 0.04,
                weight=0.58,
                entity_confidence=0.76,
            ))

    # MID tier: n_strong_mentions ≥ 5, n_distinct_authors ≥ 3, n_distinct_sources ≥ 2
    # Provide 6 clean posts — exactly enough to satisfy MID gates
    stock_posts = [
        StockDataPoint(
            timestamp=_iso_ago(ref, 5),
            platform="facebook",
            author="user_A",
            sentiment_score=0.30,
            weight=0.60,
            entity_confidence=0.88,   # strong mention
            is_spam_promo=False,
        ),
        StockDataPoint(
            timestamp=_iso_ago(ref, 10),
            platform="facebook",
            author="user_B",
            sentiment_score=0.18,
            weight=0.55,
            entity_confidence=0.86,   # strong mention
            is_spam_promo=False,
        ),
        StockDataPoint(
            timestamp=_iso_ago(ref, 18),
            platform="facebook",
            author="user_C",
            sentiment_score=0.25,
            weight=0.60,
            entity_confidence=0.90,   # strong mention
            is_spam_promo=False,
        ),
        StockDataPoint(
            timestamp=_iso_ago(ref, 25),
            platform="telegram",      # 2nd distinct source
            author="user_A",
            sentiment_score=0.35,
            weight=0.65,
            entity_confidence=0.91,   # strong mention
            is_spam_promo=False,
        ),
        StockDataPoint(
            timestamp=_iso_ago(ref, 32),
            platform="telegram",
            author="user_B",
            sentiment_score=0.20,
            weight=0.58,
            entity_confidence=0.87,   # strong mention — this is the 5th
            is_spam_promo=False,
        ),
        StockDataPoint(
            timestamp=_iso_ago(ref, 45),
            platform="telegram",
            author="user_C",          # 3rd distinct author across both platforms
            sentiment_score=0.22,
            weight=0.50,
            entity_confidence=0.84,   # below strong threshold (0.85) — not counted as strong
            is_spam_promo=False,
        ),
    ]

    return {
        "macro": macro_posts,
        "market": market_posts,
        "sector": sector_posts,
        "stock": stock_posts,
    }


def build_multilingual_data(ticker: str, ref: datetime) -> Dict[str, List]:
    """
    Scenario: multilingual
    Mixed Arabic (Egyptian dialect) and English posts for ETEL.CA (telecom/tech).

    IMPORTANT: In a real pipeline, sentiment_score values come from:
      - Arabic text → CAMeLBERT-DA (preferred for Egyptian dialect)
      - English text → FinBERT (financial domain)
      - Mixed text → XLM-R (cross-lingual fallback)
    Here the scores are pre-simulated to represent what those models would produce.
    The harness tests the aggregation logic — not model inference.

    Real-world: EGX retail investors post primarily in Egyptian Arabic dialect
    (العامية المصرية). CAMeLBERT-DA was trained specifically on dialectal Arabic
    and significantly outperforms FinBERT on Arabic financial text.
    """
    from tradingagents.sentiment.macro import MacroDataPoint
    from tradingagents.sentiment.market import MarketDataPoint
    from tradingagents.sentiment.sector import SectorDataPoint
    from tradingagents.sentiment.stock import StockDataPoint

    # TIER2 Arabic news source + TIER2 English — both corroborate RATE_DECISION
    macro_posts = [
        MacroDataPoint(
            timestamp=_iso_ago(ref, 12),
            source_domain="almalnews.com",          # TIER2 — Arabic financial news
            headline="البنك المركزي يُثبت أسعار الفائدة ويُلمح إلى تخفيضات في النصف الثاني",
            category="RATE_DECISION",
            direction="RISK_ON",
            sentiment_score=0.28,
            weight=0.70,
            post_id="almalnews_001",
        ),
        MacroDataPoint(
            timestamp=_iso_ago(ref, 20),
            source_domain="enterprise.press",       # TIER2 — English Egypt business press
            headline="Egypt central bank rate hold: market prepares for 2H 2026 easing cycle",
            category="RATE_DECISION",
            direction="RISK_ON",
            sentiment_score=0.33,
            weight=0.80,
            post_id="enterprise_001",
        ),
    ]

    # Market: Arabic Facebook + English Reddit — 56 posts, all within 24h
    # Simulated post-NLP sentiment scores
    market_posts: List = []
    arabic_scores = [0.35, 0.25, 0.40, -0.10, 0.30, 0.20, 0.45]
    english_scores = [0.28, 0.18, 0.33, 0.22, 0.15]
    all_scores = (arabic_scores * 6 + english_scores * 4)[:56]
    for i, score in enumerate(all_scores):
        market_posts.append(MarketDataPoint(
            timestamp=_iso_ago(ref, i * 0.42),
            platform="facebook" if i % 3 != 0 else "telegram",
            sentiment_score=score,
            weight=0.75,
        ))

    # Sector (telecom_tech): Arabic mentions of "الاتصالات" + English "telecom"
    sector_posts: List = []
    for day in range(4):
        day_ref = ref - timedelta(days=day)
        for j in range(4):
            sector_posts.append(SectorDataPoint(
                timestamp=_iso_ago(day_ref, j * 3),
                platform="facebook",
                sentiment_score=0.18 + (j % 3) * 0.06,
                weight=0.70,
                entity_confidence=0.78 + (j % 2) * 0.04,
            ))

    # Stock: ETEL.CA (MEGA tier) — Arabic author names + mixed content
    # MEGA requires: n_strong ≥ 8, authors ≥ 5, sources ≥ 3
    stock_posts: List = []
    arabic_authors = ["أحمد_001", "محمود_002", "سارة_003", "عمر_004", "نورا_005"]
    for i, author in enumerate(arabic_authors):
        stock_posts.append(StockDataPoint(
            timestamp=_iso_ago(ref, i * 8),
            platform="facebook",
            author=author,
            sentiment_score=0.24 + (i % 4) * 0.06,
            weight=0.73,
            entity_confidence=0.87 + (i % 3) * 0.02,   # all strong mentions
            is_spam_promo=False,
        ))
    # 3 English posts from telegram (2nd platform)
    for i in range(3):
        stock_posts.append(StockDataPoint(
            timestamp=_iso_ago(ref, 45 + i * 5),
            platform="telegram",
            author=f"eng_user_{i}",
            sentiment_score=0.30 + i * 0.04,
            weight=0.68,
            entity_confidence=0.90,
            is_spam_promo=False,
        ))
    # 3 Reddit posts (3rd distinct source — needed for MEGA Gate 3)
    for i in range(3):
        stock_posts.append(StockDataPoint(
            timestamp=_iso_ago(ref, 20 + i * 8),
            platform="reddit",
            author=f"reddit_user_{i}",
            sentiment_score=0.28 + i * 0.03,
            weight=0.65,
            entity_confidence=0.88,
            is_spam_promo=False,
        ))

    return {
        "macro": macro_posts,
        "market": market_posts,
        "sector": sector_posts,
        "stock": stock_posts,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Data loading from v2 pipeline results JSON file
# ─────────────────────────────────────────────────────────────────────────────

def load_from_pipeline_file(
    filepath: str, ticker: str, ref: datetime
) -> Optional[Dict[str, List]]:
    """
    Convert a v2 pipeline results JSON file into typed DataPoint lists.

    The v2 results format (scripts/twitter_pipeline/v2/logs/results_*.json)
    has 'items' with per-post sentiment, mentions, platform, and timestamp.
    This converter routes EGX_MARKET-mentioned posts to market_posts and
    ticker-specific mentions to stock_posts.

    Returns None if the file is missing or invalid.
    """
    from tradingagents.sentiment.market import MarketDataPoint
    from tradingagents.sentiment.stock import StockDataPoint

    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        log.error("File not found: %s", filepath)
        return None
    except json.JSONDecodeError as exc:
        log.error("JSON parse error in %s: %s", filepath, exc)
        return None

    items = data.get("items", [])
    if not items:
        log.warning("No items found in %s", filepath)
        return None

    ticker_bare = ticker.upper().replace(".CA", "")

    market_posts: List[MarketDataPoint] = []
    stock_posts: List[StockDataPoint] = []

    for item in items:
        platform = str(item.get("platform", "unknown"))
        timestamp = str(item.get("timestamp", ""))
        sentiment = item.get("sentiment") or {}
        score = float(sentiment.get("score", 0.0))
        weight = float(item.get("weight", 1.0))
        mentions = item.get("mentions") or []
        is_spam = bool(item.get("is_spam_promo", False))

        # Route to market if it's an EGX_MARKET mention
        is_market_post = any(
            str(m.get("symbol", "")).startswith("EGX_") for m in mentions
        ) or (not mentions and not is_spam)

        if is_market_post:
            market_posts.append(MarketDataPoint(
                timestamp=timestamp,
                platform=platform,
                sentiment_score=score,
                weight=weight,
            ))

        # Route to stock if it mentions the target ticker
        for mention in mentions:
            if str(mention.get("symbol", "")).upper().replace(".CA", "") == ticker_bare:
                stock_posts.append(StockDataPoint(
                    timestamp=timestamp,
                    platform=platform,
                    author=str(item.get("author", "")),
                    sentiment_score=score,
                    weight=weight,
                    entity_confidence=float(mention.get("confidence", 0.5)),
                    is_spam_promo=is_spam,
                ))
                break

    # Fallback: if no market posts were detected but items exist, treat all as market
    if not market_posts and items:
        for item in items:
            sentiment = item.get("sentiment") or {}
            market_posts.append(MarketDataPoint(
                timestamp=str(item.get("timestamp", "")),
                platform=str(item.get("platform", "unknown")),
                sentiment_score=float(sentiment.get("score", 0.0)),
                weight=float(item.get("weight", 1.0)),
            ))

    log.info(
        "Pipeline file loaded: %d market posts, %d stock posts for %s",
        len(market_posts), len(stock_posts), ticker,
    )

    return {
        "macro": [],    # v2 pipeline does not pre-classify macro events
        "market": market_posts,
        "sector": [],   # v2 pipeline does not pre-classify sector posts
        "stock": stock_posts,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Layer runners — execute each layer and print verbose stage results
# ─────────────────────────────────────────────────────────────────────────────

def run_layer_a0(
    macro_posts: List,
    ref: datetime,
    verbose: bool = False,
) -> Any:
    """Execute Layer A0 (MacroSentiment) and display gate results."""
    from tradingagents.sentiment.macro import compute_macro_sentiment
    from tradingagents.sentiment.config import MACRO_GATES

    _subsection("Layer A0 — MacroSentiment (macro-economic events)")
    print(f"  Purpose: detect active macro events (CBE rate decisions, EGP devaluation,")
    print(f"           IMF programs, inflation prints) from credible financial sources.")
    _kv("Input posts", len(macro_posts))

    if verbose and macro_posts:
        print(f"\n  {_dim('Sample input posts (up to 3):')}")
        for p in macro_posts[:3]:
            _json_block(p._asdict(), indent=6)

    result = compute_macro_sentiment(macro_posts, reference_time=ref)

    print(f"\n  {_bold('Gate results:')}")
    is_no_signal = result.composite_regime.value == "NO_SIGNAL"

    if is_no_signal:
        reason = result.reason
        # Determine which gate failed based on gate_failed string
        passed_g1 = reason.gate_failed != "macro.source_credibility"
        passed_g2 = passed_g1 and reason.gate_failed != "macro.corroborating_sources"
        _gate("Gate 1: source_credibility (≥1 OFFICIAL/TIER1/TIER2 post)", passed_g1)
        if passed_g1:
            _gate("Gate 2: corroborating_sources (≥2 distinct domains within 48h)", passed_g2)
        if passed_g2:
            _gate("Gate 3: half_life_expired (≥1 event still within half-life window)", False)

        print(f"\n  Status: {_err('NO_SIGNAL')}")
        _kv("Gate failed", _warn(reason.gate_failed), indent=4)
        _kv("Reason", reason.human_readable, indent=4)
        if verbose and reason.metrics:
            print(f"\n  {_dim('Gate metrics:')}")
            _json_block(reason.metrics, indent=6)
    else:
        _gate("Gate 1: source_credibility", True)
        _gate("Gate 2: corroborating_sources", True)
        _gate("Gate 3: half_life_expired (event still fresh)", True)

        print(f"\n  Status: {_ok('SIGNAL')}")
        _kv("Composite regime", _bold(result.composite_regime.value), indent=4)
        _kv("Active events", len(result.active_events), indent=4)

        print(f"\n  Active macro events:")
        for ev in result.active_events:
            print(f"    • {ev.category.value:20s} → {_bold(ev.direction.value):12s}"
                  f" [{ev.magnitude.value}]  conf={ev.confidence:.3f}")
            print(f"      {_dim(ev.headline[:80])}")

        if verbose:
            print(f"\n  {_dim('Full active events JSON:')}")
            for ev in result.active_events:
                _json_block({
                    "category": ev.category.value,
                    "direction": ev.direction.value,
                    "magnitude": ev.magnitude.value,
                    "source_credibility": ev.source_credibility.value,
                    "half_life_hours": ev.half_life_hours,
                    "confidence": ev.confidence,
                    "headline": ev.headline,
                }, indent=6)

    print(f"\n  {_dim('Macro → Layer E: RISK_OFF→conf×0.80 | RISK_ON→conf×0.95 | NEUTRAL→pass-through')}")
    return result


def run_layer_a(
    market_posts: List,
    ref: datetime,
    verbose: bool = False,
) -> Any:
    """Execute Layer A (MarketSentiment) and display gate results."""
    from tradingagents.sentiment.market import compute_market_sentiment
    from tradingagents.sentiment.config import MARKET_THRESHOLDS

    _subsection("Layer A — MarketSentiment (retail mood / market regime)")
    print(f"  Purpose: assess the broad EGX retail mood from all market-level posts.")
    print(f"           Emits a MarketRegime (EUPHORIA/GREED/NEUTRAL/FEAR/PANIC)")
    print(f"           and a VolatilityMood (CALM/ELEVATED/STRESSED).")

    t = MARKET_THRESHOLDS
    n = len(market_posts)
    platforms = {p.platform for p in market_posts}

    cutoff = ref - timedelta(hours=24)
    n_recent = sum(
        1 for p in market_posts
        if (dt := _parse_ts(p.timestamp)) is not None and dt >= cutoff
    )
    recent_share = n_recent / n if n > 0 else 0.0

    _kv("Input posts", n)
    _kv("Distinct platforms", f"{len(platforms)} ({', '.join(sorted(platforms))})")
    _kv("Posts within 24h", f"{n_recent} / {n} = {recent_share:.1%}")

    print(f"\n  {_bold('Gate thresholds:')}")
    print(f"    Gate 1: n_total_posts ≥ {t['min_total_posts']}")
    print(f"    Gate 2: n_distinct_sources ≥ {t['min_distinct_sources']}")
    print(f"    Gate 3: recent_24h_share ≥ {t['min_recent_24h_share']:.0%}")

    result = compute_market_sentiment(market_posts, reference_time=ref)

    print(f"\n  {_bold('Gate results:')}")
    g1 = n >= t["min_total_posts"]
    _gate(
        f"Gate 1: n_total_posts       {n:3d} ≥ {int(t['min_total_posts'])}",
        g1,
        "" if g1 else f"Got {n}, need ≥{int(t['min_total_posts'])} — add more market posts",
    )
    if g1:
        g2 = len(platforms) >= t["min_distinct_sources"]
        _gate(
            f"Gate 2: n_distinct_sources  {len(platforms):3d} ≥ {int(t['min_distinct_sources'])}",
            g2,
            "" if g2 else f"Got {len(platforms)} platform(s), need ≥{int(t['min_distinct_sources'])}",
        )
        if g2:
            g3 = recent_share >= t["min_recent_24h_share"]
            _gate(
                f"Gate 3: recent_24h_share  {recent_share:.0%} ≥ {t['min_recent_24h_share']:.0%}",
                g3,
                "" if g3 else f"{recent_share:.1%} recent — data too stale",
            )

    if result.status.value == "NO_SIGNAL":
        print(f"\n  Status: {_err('NO_SIGNAL')}")
        _kv("Gate failed", _warn(result.reason.gate_failed), indent=4)
        _kv("Reason", result.reason.human_readable, indent=4)
        if verbose and result.reason.metrics:
            _json_block(result.reason.metrics, indent=6)
    else:
        print(f"\n  Status: {_ok('SIGNAL')}")
        _kv("Score", f"{result.score:+.4f}  (range: -1.0 bearish ↔ +1.0 bullish)", indent=4)
        _kv("Market regime", _bold(result.regime.value), indent=4)
        _kv("Volatility mood", result.volatility_mood.value, indent=4)
        _kv("Confidence", f"{result.confidence:.3f}", indent=4)

        print(f"\n  {_bold('Regime interpretation:')}")
        _explain_regime(result.regime.value)

        print(f"\n  {_dim('Market regime → Layer E multipliers:')}")
        _print_regime_multipliers()

    return result


def run_layer_b(
    sector_enum: Any,
    sector_posts: List,
    verbose: bool = False,
) -> Any:
    """Execute Layer B (SectorSentiment) and display gate results."""
    from tradingagents.sentiment.sector import compute_sector_sentiment
    from tradingagents.sentiment.config import SECTOR_THRESHOLDS

    _subsection(f"Layer B — SectorSentiment (sector: {sector_enum.value})")
    print(f"  Purpose: gauge sentiment tilt within a specific EGX sector.")
    print(f"           Adds ±0.10 confidence shift to Layer E blend.")

    t = SECTOR_THRESHOLDS
    n = len(sector_posts)

    # Pre-compute for display
    dates = set()
    for p in sector_posts:
        dt = _parse_ts(p.timestamp)
        if dt:
            dates.add((dt.year, dt.month, dt.day))
    n_days = len(dates)
    mean_ec = (
        round(sum(p.entity_confidence for p in sector_posts) / n, 3)
        if n > 0 else 0.0
    )

    _kv("Input posts", n)
    _kv("Distinct calendar days", n_days)
    _kv("Mean entity confidence", f"{mean_ec:.3f}")

    print(f"\n  {_bold('Gate thresholds:')}")
    print(f"    Gate 1: n_sector_posts ≥ {t['min_sector_posts']}")
    print(f"    Gate 2: n_distinct_days ≥ {t['min_distinct_days']}")
    print(f"    Gate 3: mean_entity_conf ≥ {t['min_aggregate_entity_confidence']:.2f}")

    result = compute_sector_sentiment(sector_enum, sector_posts)

    print(f"\n  {_bold('Gate results:')}")
    g1 = n >= t["min_sector_posts"]
    _gate(f"Gate 1: n_sector_posts  {n:3d} ≥ {int(t['min_sector_posts'])}", g1)
    if g1:
        g2 = n_days >= t["min_distinct_days"]
        _gate(f"Gate 2: n_distinct_days {n_days:3d} ≥ {int(t['min_distinct_days'])}", g2,
              "" if g2 else "Need posts on at least 3 distinct calendar days (staleness check)")
        if g2:
            g3 = mean_ec >= t["min_aggregate_entity_confidence"]
            _gate(
                f"Gate 3: mean_entity_conf {mean_ec:.3f} ≥ {t['min_aggregate_entity_confidence']:.2f}",
                g3,
                "" if g3 else "Entity model confidence too low — posts don't reliably reference this sector",
            )

    if result.status.value == "NO_SIGNAL":
        print(f"\n  Status: {_err('NO_SIGNAL')}")
        _kv("Gate failed", _warn(result.reason.gate_failed), indent=4)
        _kv("Reason", result.reason.human_readable, indent=4)
    else:
        print(f"\n  Status: {_ok('SIGNAL')}")
        _kv("Score", f"{result.score:+.4f}", indent=4)
        _kv("Confidence", f"{result.confidence:.3f}", indent=4)
        print(f"\n  {_dim('Confidence formula: 40%×size + 35%×spread(days) + 25%×clarity(|score|/0.35)')}")
        _explain_confidence_components(n, n_days, abs(result.score),
                                       int(t["min_sector_posts"]), int(t["min_distinct_days"]))

    return result


def run_layer_c(
    ticker: str,
    stock_posts: List,
    ref: datetime,
    market_score: Optional[float] = None,
    verbose: bool = False,
) -> Any:
    """Execute Layer C (StockSentiment) and display gate results."""
    from tradingagents.sentiment.stock import compute_stock_sentiment
    from tradingagents.sentiment.liquidity_tiers import tier_for
    from tradingagents.sentiment.config import (
        STOCK_THRESHOLDS_BY_TIER,
        MIN_STRONG_ENTITY_CONFIDENCE,
        MIN_RECENT_72H_SHARE,
    )

    tier = tier_for(ticker)
    thresholds = STOCK_THRESHOLDS_BY_TIER[tier]

    _subsection(f"Layer C — StockSentiment ({ticker})  [Tier: {_bold(tier.value)}]")
    print(f"  Purpose: detect ticker-specific sentiment. Intentionally sparse —")
    print(f"           most tickers emit NO_SIGNAL on most trading days (correct behaviour).")
    print(f"\n  {_bold('Liquidity tier explanation:')}")
    print(f"    MEGA  (COMI, TMGH, FWRY, ETEL, HRHO): highest thresholds — most discussed")
    print(f"    MID   (EGX-30/70 remainder):           medium thresholds")
    print(f"    SMALL (all other tickers):              lowest thresholds — fewest false signals")

    # Pre-compute gate inputs for display
    clean = [p for p in stock_posts if not p.is_spam_promo]
    spam_count = len(stock_posts) - len(clean)
    strong = [p for p in clean if p.entity_confidence >= MIN_STRONG_ENTITY_CONFIDENCE]
    authors = {(p.author if p.author else "_anonymous") for p in clean}
    sources = {p.platform for p in clean}
    cutoff = ref - timedelta(hours=72)
    n_recent = sum(
        1 for p in clean
        if (dt := _parse_ts(p.timestamp)) is not None and dt >= cutoff
    )
    recent_share = n_recent / len(clean) if clean else 0.0

    _kv("Total input posts", len(stock_posts))
    _kv("Spam/promo excluded", spam_count, indent=4)
    _kv("Clean posts (counted)", len(clean), indent=4)
    _kv("Strong mentions (entity_conf ≥ 0.85)", len(strong), indent=4)
    _kv("Distinct authors", len(authors), indent=4)
    _kv("Distinct sources (platforms)", len(sources), indent=4)
    _kv("Recent (within 72h)", f"{n_recent}/{len(clean)} = {recent_share:.1%}", indent=4)

    print(f"\n  {_bold(f'Gate thresholds for {tier.value} tier:')}")
    print(f"    Gate 1: n_strong_mentions  ≥ {thresholds['n_strong_mentions']}")
    print(f"    Gate 2: n_distinct_authors ≥ {thresholds['n_distinct_authors']}")
    print(f"    Gate 3: n_distinct_sources ≥ {thresholds['n_distinct_sources']}")
    print(f"    Gate 4: recent_72h_share   ≥ {MIN_RECENT_72H_SHARE:.0%}")

    result = compute_stock_sentiment(ticker, stock_posts, ref, market_score=market_score)

    print(f"\n  {_bold('Gate results:')}")
    g1 = len(strong) >= thresholds["n_strong_mentions"]
    _gate(
        f"Gate 1: n_strong_mentions  {len(strong):3d} ≥ {thresholds['n_strong_mentions']}",
        g1,
        "" if g1 else (
            f"Only {len(strong)} posts have entity_conf ≥ {MIN_STRONG_ENTITY_CONFIDENCE}. "
            f"Low entity confidence = model unsure this post refers to {ticker}."
        ),
    )
    if g1:
        g2 = len(authors) >= thresholds["n_distinct_authors"]
        _gate(
            f"Gate 2: n_distinct_authors {len(authors):3d} ≥ {thresholds['n_distinct_authors']}",
            g2,
            "" if g2 else "Need more distinct authors to prevent single-user manipulation.",
        )
        if g2:
            g3 = len(sources) >= thresholds["n_distinct_sources"]
            _gate(
                f"Gate 3: n_distinct_sources {len(sources):3d} ≥ {thresholds['n_distinct_sources']}",
                g3,
                "" if g3 else "Need posts from multiple platforms (Facebook + Telegram).",
            )
            if g3:
                g4 = recent_share >= MIN_RECENT_72H_SHARE
                _gate(
                    f"Gate 4: recent_72h_share  {recent_share:.0%} ≥ {MIN_RECENT_72H_SHARE:.0%}",
                    g4,
                    "" if g4 else "Too many old posts — recent data required to avoid stale signal.",
                )

    if result.status.value == "NO_SIGNAL":
        print(f"\n  Status: {_err('NO_SIGNAL')}")
        _kv("Gate failed", _warn(result.reason.gate_failed), indent=4)
        _kv("Reason", result.reason.human_readable, indent=4)
        if verbose and result.reason.metrics:
            print(f"  {_dim('Metrics:')}")
            _json_block(result.reason.metrics, indent=6)
        print(f"\n  {_dim('→ Pre-LLM gate: if Layer C is NO_SIGNAL, the LLM explainer is SKIPPED.')}")
        print(f"  {_dim('→ sentiment_report = \"Social sentiment: insufficient data — excluded.\"')}")
        print(f"  {_dim('→ Bull/Bear researchers see: EXCLUDED instruction (not neutral).')}")
    else:
        print(f"\n  Status: {_ok('SIGNAL')}")
        _kv("Score", f"{result.score:+.4f}", indent=4)
        _kv("Confidence", f"{result.confidence:.3f}", indent=4)
        _kv("Tier", result.tier, indent=4)
        _kv("Strong mentions", result.n_strong_mentions, indent=4)
        _kv("Distinct authors", result.n_distinct_authors, indent=4)
        _kv("Distinct sources", result.n_distinct_sources, indent=4)

        if result.contradicts_market:
            print(f"\n  {_warn('⚠  contradicts_market = True')}")
            print(f"     Stock score {result.score:+.3f} opposes market score "
                  f"{market_score:+.3f}.")
            print(f"     Both are ≥ 0.15 in magnitude. This is stock-specific divergence.")
            print(f"     Real-world: earnings risk, management change, or sector rotation.")
            print(f"     The blend still does NOT flip direction — analysts handle interpretation.")

    return result


def run_layer_e(
    macro_result: Any,
    market_result: Any,
    sector_result: Any,
    verbose: bool = False,
) -> Any:
    """Execute Layer E (Blender) and display multiplier cascade."""
    from tradingagents.agents.utils.scoring import blend_sentiment

    _subsection("Layer E — Sentiment Blender (confidence × and position-size × only)")
    print(f"  Purpose: translate sentiment context into non-directional execution modifiers.")
    print(f"  CRITICAL: sentiment NEVER changes BUY/SELL/HOLD direction.")
    print(f"            It only tightens or loosens confidence and position sizing.")

    blend = blend_sentiment(
        macro=macro_result,
        market=market_result,
        sector=sector_result,
    )

    print(f"\n  {_bold('Blend multiplier cascade:')}")
    for part in blend.audit.replace("blend: ", "").replace(
        f" => conf×{blend.confidence_multiplier:.4f}, size×{blend.position_size_multiplier:.4f}", ""
    ).split("; "):
        part = part.strip()
        if not part:
            continue
        is_pass = "pass-through" in part or "absent" in part
        print(f"    {'  ' if not is_pass else ''}{_dim(part) if is_pass else part}")

    print(f"\n  {_bold('Final multipliers:')}")
    _kv(
        "confidence_multiplier",
        _bold(f"{blend.confidence_multiplier:.4f}") +
        f"  {_dim('(applied to analyst confidence before Risk Manager sees it)')}", indent=4
    )
    _kv(
        "position_size_multiplier",
        _bold(f"{blend.position_size_multiplier:.4f}") +
        f"  {_dim('(applied to Trader position-size recommendation)')}", indent=4
    )

    if verbose:
        print(f"\n  {_dim('Full audit string:')}")
        print(f"    {_dim(blend.audit)}")

    print(f"\n  {_dim('Where this multiplier is applied:')}")
    print(f"  {_dim('  propagation.py:propagate_confidence() reads sentiment_blend_result')}")
    print(f"  {_dim('  from AgentState and applies confidence× before the overall score')}")
    print(f"  {_dim('  is computed. Position-size× surfaces in component_scores dict.')}")

    return blend


# ─────────────────────────────────────────────────────────────────────────────
# Supplementary displays
# ─────────────────────────────────────────────────────────────────────────────

def display_contradiction(market_result: Any, stock_result: Any) -> None:
    """Check and explain stock-vs-market sentiment contradiction."""
    _subsection("Contradiction Detection")

    market_signal = market_result.status.value == "SIGNAL"
    stock_signal = stock_result.status.value == "SIGNAL"

    if not market_signal:
        print(f"  {_dim('Market is NO_SIGNAL — contradiction detection skipped.')}")
        return
    if not stock_signal:
        print(f"  {_dim('Stock is NO_SIGNAL — contradiction detection skipped.')}")
        return

    if stock_result.contradicts_market:
        print(f"  {_warn('⚠  CONTRADICTION DETECTED')}")
        print(f"     Market regime: {market_result.regime.value}  (score {market_result.score:+.3f})")
        print(f"     Stock score  :  {stock_result.score:+.3f}")
        print(f"\n  {_bold('What this means for a PM/trader:')}")
        mkt_dir = "bullish" if market_result.score > 0 else "bearish"
        stk_dir = "bearish" if stock_result.score < 0 else "bullish"
        print(f"     Retail EGX market overall is {mkt_dir}, but specific discussion")
        print(f"     about {stock_result.ticker} is {stk_dir}. This divergence suggests:")
        print(f"     • Company-specific news (earnings miss, debt refinancing, CEO change)")
        print(f"     • Sector rotation away from this name")
        print(f"     • Insider knowledge circulating in retail forums")
        print(f"\n  {_dim('The blend does NOT change directional thesis. Researchers decide.')}")
    else:
        mkt_dir = "bullish" if market_result.score > 0 else ("bearish" if market_result.score < 0 else "neutral")
        stk_dir = "bullish" if stock_result.score > 0 else ("bearish" if stock_result.score < 0 else "neutral")
        print(f"  {_ok('✓ No contradiction')} — market ({mkt_dir}) and stock ({stk_dir}) are aligned.")
        _kv("Market score", f"{market_result.score:+.3f}", indent=4)
        _kv("Stock score", f"{stock_result.score:+.3f}", indent=4)


def display_propagation_impact(blend: Any, base_confidence: float = 0.70) -> None:
    """Show how blend multipliers affect a hypothetical unblended confidence."""
    _subsection("Signal Propagation Impact")
    print(f"  How the blend multipliers flow through propagation.py into final decisions.")

    blended = min(1.0, max(0.10, base_confidence * blend.confidence_multiplier))
    size_adj = blend.position_size_multiplier

    print(f"\n  {_bold('Hypothetical scenario:')} unblended analyst confidence = {base_confidence:.2f}")
    print(f"  After sentiment blend (propagation.py:propagate_confidence):")
    _kv("Blended confidence",
        f"{_bold(f'{blended:.3f}')}  ({base_confidence:.2f} × {blend.confidence_multiplier:.4f})",
        indent=4)
    _kv("Position size ×",
        f"{_bold(f'{size_adj:.4f}')}",
        indent=4)

    delta = blended - base_confidence
    if abs(delta) < 0.001:
        print(f"\n  {_ok('✓ No sentiment adjustment — pass-through (confidence unchanged)')}")
    elif delta < 0:
        print(f"\n  {_warn(f'↓ Confidence reduced by {abs(delta):.3f} due to sentiment context')}")
    else:
        print(f"\n  {_ok(f'↑ Confidence boosted by {delta:.3f} due to positive sector tilt')}")

    if size_adj < 1.0:
        adj_shares = int(100 * size_adj)
        print(f"\n  {_bold('Position sizing example:')}")
        print(f"    Trader recommends 100 shares at EGP X.")
        print(f"    After sentiment blend: {_bold(f'{adj_shares} shares')}  "
              f"(100 × {size_adj:.2f})  {_dim(f'= {(1-size_adj)*100:.0f}% size reduction')}")
    else:
        print(f"\n  {_dim('Position size unchanged (no adverse sentiment regime).')}")

    print(f"\n  {_dim('propagation.py also reads overall_status (OK | INSUFFICIENT_DATA).')}")
    print(f"  {_dim('INSUFFICIENT_DATA means quorum (≥2 directional analysts) not met.')}")
    print(f"  {_dim('When INSUFFICIENT_DATA, the position_size_multiplier stays 1.0')}")
    print(f"  {_dim('and the decision defaults to HOLD.')}")


def display_final_summary(
    ticker: str, date_str: str, scenario: str,
    macro_result: Any, market_result: Any,
    sector_result: Any, stock_result: Any,
    blend: Any,
) -> None:
    """Print the consolidated summary."""
    _section("FINAL SUMMARY")

    macro_ok = macro_result.composite_regime.value != "NO_SIGNAL"
    market_ok = market_result.status.value == "SIGNAL"
    sector_ok = sector_result.status.value == "SIGNAL"
    stock_ok = stock_result.status.value == "SIGNAL"

    print(f"\n  Ticker  : {_bold(ticker)}   Date: {date_str}   Scenario: {scenario}")

    print(f"\n  {'Layer':<10} {'Status':<22} {'Key metric'}")
    print(f"  {'─'*10} {'─'*22} {'─'*28}")

    # A0 Macro
    if macro_ok:
        print(f"  {'A0 Macro':<10} {_ok('SIGNAL'):35} {macro_result.composite_regime.value}")
    else:
        r = macro_result.reason
        print(f"  {'A0 Macro':<10} {_err('NO_SIGNAL'):35} gate={r.gate_failed.split('.')[-1]}")

    # A Market
    if market_ok:
        print(f"  {'A  Market':<10} {_ok('SIGNAL  ' + market_result.regime.value):35}"
              f" score={market_result.score:+.3f} conf={market_result.confidence:.3f}")
    else:
        r = market_result.reason
        print(f"  {'A  Market':<10} {_err('NO_SIGNAL'):35} gate={r.gate_failed.split('.')[-1]}")

    # B Sector
    if sector_ok:
        print(f"  {'B  Sector':<10} {_ok('SIGNAL'):35}"
              f" score={sector_result.score:+.3f} conf={sector_result.confidence:.3f}")
    else:
        r = sector_result.reason
        print(f"  {'B  Sector':<10} {_err('NO_SIGNAL'):35} gate={r.gate_failed.split('.')[-1]}")

    # C Stock
    if stock_ok:
        print(f"  {'C  Stock':<10} {_ok('SIGNAL'):35}"
              f" score={stock_result.score:+.3f} conf={stock_result.confidence:.3f}")
    else:
        r = stock_result.reason
        print(f"  {'C  Stock':<10} {_err('NO_SIGNAL'):35} gate={r.gate_failed.split('.')[-1]}")

    print(f"\n  {_bold('Layer E Blend multipliers:')}")
    _kv("confidence ×",  f"{blend.confidence_multiplier:.4f}", indent=4)
    _kv("position-size ×", f"{blend.position_size_multiplier:.4f}", indent=4)

    no_signal_count = sum([not macro_ok, not market_ok, not sector_ok, not stock_ok])
    if no_signal_count == 4:
        print(f"\n  {_err('ALL LAYERS: NO_SIGNAL')} — pass-through blend (no sentiment modifier applied)")
    elif no_signal_count > 0:
        print(f"\n  {_warn(f'{no_signal_count}/4 layer(s) NO_SIGNAL')} "
              f"— those layers contribute pass-through (×1.0), not neutral")
    else:
        print(f"\n  {_ok('All 4 layers: SIGNAL')} — full sentiment context applied to blend")

    print(f"\n  {_bold('KEY PRINCIPLE:')}")
    print(f"  {_dim('Sentiment context modifies EXECUTION, not the investment thesis.')}")
    print(f"  {_dim('The directional BUY/SELL/HOLD comes from Technical + Fundamental + News.')}")
    print(f"  {_dim('Sentiment only answers: \"how much confidence?\" and \"how large?\"')}")


# ─────────────────────────────────────────────────────────────────────────────
# Helper display functions
# ─────────────────────────────────────────────────────────────────────────────

def _explain_regime(regime: str) -> None:
    explanations = {
        "EUPHORIA": ("Extreme retail optimism — often precedes correction. "
                     "Confidence reduced ×0.70, position-size ×0.60."),
        "GREED":    ("Elevated buying sentiment — slight caution warranted. "
                     "Confidence reduced ×0.90, position-size ×0.90."),
        "NEUTRAL":  ("Balanced sentiment — no adjustment. "
                     "Pass-through multipliers (×1.00)."),
        "FEAR":     ("Retail selling pressure — reduce position. "
                     "Confidence ×0.85, position-size ×0.75."),
        "PANIC":    ("Extreme retail fear — major position reduction. "
                     "Confidence ×0.70, position-size ×0.50."),
        "NO_SIGNAL": "No market sentiment signal — pass-through.",
    }
    print(f"  {_dim('  → ' + explanations.get(regime, 'Unknown regime'))}")


def _print_regime_multipliers() -> None:
    table = [
        ("PANIC",    "0.70", "0.50"),
        ("FEAR",     "0.85", "0.75"),
        ("NEUTRAL",  "1.00", "1.00"),
        ("GREED",    "0.90", "0.90"),
        ("EUPHORIA", "0.70", "0.60"),
    ]
    print(f"  {'Regime':<12} {'conf×':>8} {'size×':>8}")
    for regime, conf, size in table:
        print(f"  {_dim(regime):<12} {_dim(conf):>8} {_dim(size):>8}")


def _explain_confidence_components(
    n: int, n_days: int, score_abs: float,
    min_posts: int, min_days: int,
) -> None:
    size_c = min(1.0, n / max(1, 2 * min_posts))
    spread_c = min(1.0, n_days / max(1, 2 * min_days))
    clarity_c = min(1.0, score_abs / 0.35)
    total = 0.40 * size_c + 0.35 * spread_c + 0.25 * clarity_c
    print(f"  {_dim(f'  size component   (40%): {size_c:.3f}  (n={n} / 2×{min_posts}={2*min_posts})')}")
    print(f"  {_dim(f'  spread component (35%): {spread_c:.3f}  (days={n_days} / 2×{min_days}={2*min_days})')}")
    print(f"  {_dim(f'  clarity component(25%): {clarity_c:.3f}  (|score|={score_abs:.3f} / 0.35)')}")
    print(f"  {_dim(f'  total confidence      : {total:.3f}')}")


# ─────────────────────────────────────────────────────────────────────────────
# Structured JSON report builder
# ─────────────────────────────────────────────────────────────────────────────

def build_report(
    ticker: str,
    date_str: str,
    scenario: str,
    macro_result: Any,
    market_result: Any,
    sector_result: Any,
    stock_result: Any,
    blend: Any,
) -> Dict[str, Any]:
    """Build a structured JSON report from all layer results."""
    from tradingagents.sentiment.liquidity_tiers import tier_for

    def _layer_summary(result: Any, layer_name: str) -> Dict:
        status_obj = getattr(result, "status", None)
        regime_obj = getattr(result, "composite_regime", None)
        status_str = (
            status_obj.value if status_obj else
            (regime_obj.value if regime_obj else "UNKNOWN")
        )
        is_no_signal = "NO_SIGNAL" in status_str.upper()
        out: Dict[str, Any] = {"status": status_str, "is_no_signal": is_no_signal}
        if not is_no_signal:
            out["score"] = getattr(result, "score", None)
            out["confidence"] = getattr(result, "confidence", 0.0)
            if layer_name == "market":
                out["regime"] = result.regime.value
                out["volatility_mood"] = result.volatility_mood.value
                out["n_posts"] = result.n_posts
                out["n_distinct_sources"] = result.n_distinct_sources
            if layer_name == "macro":
                out["composite_regime"] = result.composite_regime.value
                out["active_events"] = [
                    {
                        "category": e.category.value,
                        "direction": e.direction.value,
                        "magnitude": e.magnitude.value,
                        "confidence": e.confidence,
                        "headline": e.headline[:80],
                    }
                    for e in result.active_events
                ]
            if layer_name == "stock":
                out["tier"] = result.tier
                out["contradicts_market"] = result.contradicts_market
                out["n_strong_mentions"] = result.n_strong_mentions
                out["n_distinct_authors"] = result.n_distinct_authors
                out["n_distinct_sources"] = result.n_distinct_sources
        else:
            reason = getattr(result, "reason", None)
            if reason:
                out["gate_failed"] = reason.gate_failed
                out["reason"] = reason.human_readable
                out["metrics"] = reason.metrics
        return out

    tier = tier_for(ticker)

    return {
        "ticker": ticker,
        "date": date_str,
        "scenario": scenario,
        "tier": tier.value,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "layers": {
            "macro_a0": _layer_summary(macro_result, "macro"),
            "market_a": _layer_summary(market_result, "market"),
            "sector_b": _layer_summary(sector_result, "sector"),
            "stock_c":  _layer_summary(stock_result, "stock"),
        },
        "blend_e": {
            "confidence_multiplier": blend.confidence_multiplier,
            "position_size_multiplier": blend.position_size_multiplier,
            "audit": blend.audit,
        },
        "summary": {
            "signal_layers": sum([
                macro_result.composite_regime.value != "NO_SIGNAL",
                market_result.status.value == "SIGNAL",
                sector_result.status.value == "SIGNAL",
                stock_result.status.value == "SIGNAL",
            ]),
            "no_signal_layers": sum([
                macro_result.composite_regime.value == "NO_SIGNAL",
                market_result.status.value == "NO_SIGNAL",
                sector_result.status.value == "NO_SIGNAL",
                stock_result.status.value == "NO_SIGNAL",
            ]),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Scenario registry
# ─────────────────────────────────────────────────────────────────────────────

SCENARIOS: Dict[str, Any] = {
    "positive":      build_positive_signal_data,
    "no_signal":     build_no_signal_data,
    "conflict":      build_conflict_data,
    "low_liquidity": build_low_liquidity_data,
    "multilingual":  build_multilingual_data,
}

SCENARIO_DESC: Dict[str, str] = {
    "positive":      "All gates pass — bullish COMI.CA (banks sector)",
    "no_signal":     "All layers NO_SIGNAL — sparse EFID.CA small-cap day",
    "conflict":      "Stock bearish vs market bullish — GEOPOLITICAL RISK_OFF macro",
    "low_liquidity": "SMALL tier DOMT.CA — passes with minimum required evidence",
    "multilingual":  "Arabic + English mix — CAMeLBERT-DA / FinBERT routing demo",
}

SCENARIO_DEFAULT_TICKER: Dict[str, str] = {
    "positive":      "COMI.CA",
    "no_signal":     "EFID.CA",
    "conflict":      "TMGH.CA",
    "low_liquidity": "DOMT.CA",
    "multilingual":  "ETEL.CA",
}


# ─────────────────────────────────────────────────────────────────────────────
# CLI entrypoint
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(
        prog="test_sentiment_pipeline",
        description="EGX Sentiment Pipeline — Manual Test Harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/test_sentiment_pipeline.py
  python scripts/test_sentiment_pipeline.py --scenario no_signal
  python scripts/test_sentiment_pipeline.py --scenario conflict --verbose
  python scripts/test_sentiment_pipeline.py --ticker COMI.CA --date 2026-05-01
  python scripts/test_sentiment_pipeline.py --scenario multilingual --save-report
  python scripts/test_sentiment_pipeline.py --list-scenarios
  python scripts/test_sentiment_pipeline.py --ticker COMI.CA \\
      --raw-posts-file scripts/twitter_pipeline/v2/logs/results_latest.json
        """,
    )
    parser.add_argument("--ticker", default=None,
                        help="EGX ticker (default: scenario-specific, e.g. COMI.CA)")
    parser.add_argument("--date", default=None,
                        help="Reference date YYYY-MM-DD (default: today UTC)")
    parser.add_argument("--scenario", choices=list(SCENARIOS), default="positive",
                        help="Built-in test scenario (default: positive)")
    parser.add_argument("--raw-posts-file", default=None,
                        help="Path to v2 pipeline results JSON file")
    parser.add_argument("--verbose", action="store_true",
                        help="Show full intermediate JSON and extra detail at every stage")
    parser.add_argument("--save-report", action="store_true",
                        help="Save structured JSON report to reports/ directory")
    parser.add_argument("--show-intermediate-json", action="store_true",
                        help="Dump raw Pydantic model JSON for all layer results")
    parser.add_argument("--list-scenarios", action="store_true",
                        help="List all available scenarios and exit")

    args = parser.parse_args()

    if args.list_scenarios:
        print("\nAvailable scenarios:\n")
        for name, desc in SCENARIO_DESC.items():
            ticker = SCENARIO_DEFAULT_TICKER[name]
            print(f"  {_bold(name):<25} {desc}")
            print(f"  {' '*25} Default ticker: {ticker}\n")
        sys.exit(0)

    # ── Resolve reference time ────────────────────────────────────────────────
    if args.date:
        try:
            ref = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"{_err('Error')}: Invalid date '{args.date}'. Use YYYY-MM-DD.")
            sys.exit(1)
    else:
        ref = datetime.now(timezone.utc).replace(hour=14, minute=0, second=0, microsecond=0)
    date_str = ref.strftime("%Y-%m-%d")

    # ── Resolve ticker and sector ─────────────────────────────────────────────
    ticker = args.ticker or SCENARIO_DEFAULT_TICKER[args.scenario]

    from tradingagents.sentiment.taxonomy import ticker_to_sector, SectorEnum
    from tradingagents.sentiment.liquidity_tiers import tier_for

    sector_enum = ticker_to_sector(ticker)
    if sector_enum == SectorEnum.UNKNOWN:
        sector_enum = SectorEnum.INDUSTRY   # safe fallback
    tier = tier_for(ticker)

    verbose = args.verbose or args.show_intermediate_json

    # ── Header ────────────────────────────────────────────────────────────────
    _section("EGX SENTIMENT PIPELINE — MANUAL TEST HARNESS", "═")
    _kv("Ticker",   _bold(ticker))
    _kv("Date",     date_str)
    _kv("Sector",   sector_enum.value)
    _kv("Tier",     f"{tier.value}  {_dim('(liquidity classification)')}")
    _kv("Scenario", f"{_bold(args.scenario)} — {SCENARIO_DESC[args.scenario]}")
    _kv("Verbose",  str(verbose))
    if args.raw_posts_file:
        _kv("Data file", args.raw_posts_file)

    print(f"\n  {_dim('Architecture: agent_docs/sentiment_architecture.md')}")
    print(f"  {_dim('Regression: python -m pytest tests/test_sentiment_harness.py -v')}")

    # ── Load data ─────────────────────────────────────────────────────────────
    _section("DATA LOADING")

    if args.raw_posts_file:
        data = load_from_pipeline_file(args.raw_posts_file, ticker, ref)
        if data is None:
            print(f"  {_warn('Could not load file — falling back to built-in scenario data.')}")
            data = SCENARIOS[args.scenario](ticker, ref)
        else:
            print(f"  {_ok('Loaded from file:')} {args.raw_posts_file}")
    else:
        data = SCENARIOS[args.scenario](ticker, ref)
        print(f"  {_ok('Built-in scenario data generated.')}")

    print(f"\n  Input data summary:")
    _kv("Macro posts",  len(data["macro"]),  indent=4)
    _kv("Market posts", len(data["market"]), indent=4)
    _kv("Sector posts", len(data["sector"]), indent=4)
    _kv("Stock posts",  len(data["stock"]),  indent=4)

    # ── Pipeline execution ────────────────────────────────────────────────────
    _section("PIPELINE EXECUTION — STAGE BY STAGE")

    macro_result  = run_layer_a0(data["macro"], ref, verbose=verbose)
    market_result = run_layer_a(data["market"], ref, verbose=verbose)
    market_score  = market_result.score if market_result.status.value == "SIGNAL" else None
    sector_result = run_layer_b(sector_enum, data["sector"], verbose=verbose)
    stock_result  = run_layer_c(ticker, data["stock"], ref,
                                market_score=market_score, verbose=verbose)
    blend         = run_layer_e(macro_result, market_result, sector_result, verbose=verbose)

    # ── Supplementary analysis ────────────────────────────────────────────────
    display_contradiction(market_result, stock_result)
    display_propagation_impact(blend)

    # ── Intermediate JSON ─────────────────────────────────────────────────────
    if args.show_intermediate_json:
        _section("INTERMEDIATE JSON OBJECTS")
        for name, result in [
            ("market_result (Layer A)", market_result),
            ("sector_result (Layer B)", sector_result),
            ("stock_result (Layer C)", stock_result),
        ]:
            print(f"\n  {_bold(name)}:")
            try:
                _json_block(result.model_dump(), indent=4)
            except Exception:
                print(f"    {_dim(str(result))}")
        print(f"\n  {_bold('macro_result (Layer A0):')}")
        print(f"    composite_regime: {macro_result.composite_regime.value}")
        print(f"\n  {_bold('blend (Layer E):')}")
        _json_block({
            "confidence_multiplier": blend.confidence_multiplier,
            "position_size_multiplier": blend.position_size_multiplier,
            "audit": blend.audit,
        }, indent=4)

    # ── Final summary ─────────────────────────────────────────────────────────
    display_final_summary(
        ticker, date_str, args.scenario,
        macro_result, market_result, sector_result, stock_result, blend,
    )

    # ── Save report ───────────────────────────────────────────────────────────
    if args.save_report:
        report = build_report(
            ticker, date_str, args.scenario,
            macro_result, market_result, sector_result, stock_result, blend,
        )
        reports_dir = PROJECT_ROOT / "reports"
        reports_dir.mkdir(exist_ok=True)
        safe_ticker = ticker.replace(".", "_")
        fname = f"sentiment_{safe_ticker}_{date_str}_{args.scenario}.json"
        report_path = reports_dir / fname
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False, default=str)
        print(f"\n  {_ok('Report saved:')} {report_path}")

    print(f"\n{_rule()}")
    print(f"Harness complete.")
    print(_rule())


if __name__ == "__main__":
    main()
