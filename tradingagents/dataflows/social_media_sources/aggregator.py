"""
Multi-Source Social Media Aggregator for EGX
=============================================
Collects, deduplicates, filters, and normalizes social media data
from all configured sources into a unified SocialMediaResult.

Pipeline:
  1. Fetch from all platforms (with error isolation)
  2. Deduplicate (fuzzy fingerprint, 200-char window)
  3. Filter spam and low-quality posts
  4. Filter by engagement threshold
  5. Detect language on remaining posts
  6. Group by time window for temporal analysis
  7. Compute quality metadata

This is the main entry point for social media data collection.
"""

import re
import math
import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from collections import defaultdict

logger = logging.getLogger("tradingagents.social.aggregator")

from .schema import SocialPost, SocialMediaResult
from .twitter_source import fetch_twitter_data
from .telegram_source import fetch_telegram_data
from .reddit_source import fetch_reddit_data
from .stocktwits_source import fetch_stocktwits_data
from .cached_data import MARKET_WIDE_POSTS, EGX_TICKER_ALIASES
from .historical_source import fetch_historical_posts

# Platform source mapping
PLATFORM_SOURCES = {
    "twitter": fetch_twitter_data,
    "telegram": fetch_telegram_data,
    "reddit": fetch_reddit_data,
    "stocktwits": fetch_stocktwits_data,
}

# Filtering thresholds
MIN_POST_LENGTH = 15              # Chars — shorter is noise
MIN_ENGAGEMENT_TOTAL = 2          # likes + shares + comments
DEDUP_FINGERPRINT_LENGTH = 200    # Chars for fuzzy dedup


def get_social_media_data(
    ticker: str,
    curr_date: str,
    look_back_days: int = 7,
    platforms: List[str] = None,
    include_market_sentiment: bool = True,
) -> SocialMediaResult:
    """
    Collect social media data from all configured platforms.

    This is the PRIMARY entry point for social media data in the system.
    It queries all platforms, deduplicates results, filters spam/noise,
    detects languages, and computes quality metadata.
    """
    ticker = ticker.upper().replace(".CA", "").strip()
    platforms = platforms or list(PLATFORM_SOURCES.keys())

    result = SocialMediaResult(
        ticker=ticker,
        query_date=curr_date,
        look_back_days=look_back_days,
        platforms_queried=platforms,
    )

    all_posts: List[SocialPost] = []

    # ── Backtest guard — prevent live data leakage ──
    # Twitter, Telegram, Reddit etc. only return CURRENT data. If curr_date
    # is in the past (backtest mode), querying them leaks future sentiment
    # into past decisions. Detect this and skip live sources entirely —
    # historical_source / cached data will fill in.
    is_backtest = False
    try:
        _query_dt = datetime.strptime(curr_date, "%Y-%m-%d").date()
        _today = datetime.utcnow().date()
        # Give a 1-day tolerance for timezone / cache freshness.
        if (_today - _query_dt).days > 1:
            is_backtest = True
    except (ValueError, TypeError):
        pass

    if is_backtest:
        logger.info(
            "Backtest mode detected (curr_date=%s); skipping live social "
            "platforms to avoid future-data leakage. Using historical proxy.",
            curr_date,
        )
        for platform in platforms:
            result.platforms_failed[platform] = "skipped_in_backtest_mode"

    # ── Step 1: Fetch from each platform (isolated error handling) ──
    for platform in (platforms if not is_backtest else []):
        if platform not in PLATFORM_SOURCES:
            result.platforms_failed[platform] = f"Unknown platform: {platform}"
            continue

        fetch_fn = PLATFORM_SOURCES[platform]

        try:
            posts = fetch_fn(ticker, curr_date, look_back_days)

            if posts:
                all_posts.extend(posts)
                result.platforms_succeeded.append(platform)
                logger.info(
                    "Fetched %d posts from %s for %s", len(posts), platform, ticker
                )
            else:
                result.platforms_failed[platform] = "No data returned"
                logger.info("No data from %s for %s", platform, ticker)

        except Exception as e:
            result.platforms_failed[platform] = str(e)
            logger.warning(
                "Social media fetch failed for %s/%s: %s", platform, ticker, e
            )

    # ── Step 2: Inject historical news-derived posts if live sources failed ──
    # For backtesting on past dates, real news articles act as the social
    # signal proxy. This gives historically accurate, date-varying signal
    # instead of static cached posts.
    if not result.platforms_succeeded:
        historical_posts = fetch_historical_posts(ticker, curr_date, look_back_days)
        if historical_posts:
            all_posts.extend(historical_posts)
            result.platforms_succeeded.append("news_derived")
            logger.info(
                "Injected %d news-derived historical posts for %s on %s",
                len(historical_posts), ticker, curr_date,
            )
        elif include_market_sentiment:
            # Last resort: static cached posts (timestamps are now date-anchored)
            for post in MARKET_WIDE_POSTS:
                post.author = f"[CACHED] {post.author or 'market_sample'}"
            all_posts.extend(MARKET_WIDE_POSTS)
            logger.info(
                "Injected %d cached market posts (no historical data found)",
                len(MARKET_WIDE_POSTS),
            )

    # ── Step 3: Deduplicate ──
    all_posts = _deduplicate_posts(all_posts)

    # ── Step 4: Filter spam and low-quality posts ──
    all_posts = _filter_spam(all_posts)
    all_posts = _filter_low_quality(all_posts)

    # ── Step 5: Filter by engagement (keep zero-engagement from Telegram
    #            which doesn't always expose metrics) ──
    all_posts = _filter_by_engagement(all_posts)

    # ── Step 6: Ensure ticker + language detection ──
    for post in all_posts:
        if post.ticker is None:
            post.ticker = _extract_ticker(post.text, ticker)
        if post.language == "unknown":
            post.language = _detect_language(post.text)

    result.posts = all_posts
    result.compute_metadata()

    return result


# =============================================================================
# DEDUPLICATION
# =============================================================================

def _deduplicate_posts(posts: List[SocialPost]) -> List[SocialPost]:
    """
    Remove duplicate posts using 200-char normalized fingerprints.
    Keeps the version with higher engagement if duplicates found.
    """
    fingerprint_map: Dict[str, SocialPost] = {}

    for post in posts:
        normalized = re.sub(r"\s+", " ", post.text.lower().strip())
        fp = normalized[:DEDUP_FINGERPRINT_LENGTH]

        if fp in fingerprint_map:
            # Keep the one with higher engagement
            existing_eng = sum(fingerprint_map[fp].engagement.values())
            new_eng = sum(post.engagement.values())
            if new_eng > existing_eng:
                fingerprint_map[fp] = post
        else:
            fingerprint_map[fp] = post

    return list(fingerprint_map.values())


# =============================================================================
# SPAM FILTERING
# =============================================================================

# Patterns that indicate spam / bot / promotional content
_SPAM_PATTERNS = [
    re.compile(r"(?:join|انضم|اشترك).{0,20}(?:group|channel|قناة|جروب)", re.IGNORECASE),
    re.compile(r"(?:free|مجان).{0,15}(?:signal|إشارة|توصية)", re.IGNORECASE),
    re.compile(r"(?:DM|message|راسل).{0,10}(?:me|now|الآن)", re.IGNORECASE),
    re.compile(r"(?:100|200|300|500|1000)%\s*(?:profit|ربح|guaranteed|مضمون)", re.IGNORECASE),
    re.compile(r"t\.me/\S+", re.IGNORECASE),
    re.compile(r"(?:bit\.ly|tinyurl|shorturl)\S+", re.IGNORECASE),
    re.compile(r"(🚀){4,}"),           # 4+ consecutive rockets
    re.compile(r"(.)\1{7,}"),           # 8+ repeated characters
]


def _filter_spam(posts: List[SocialPost]) -> List[SocialPost]:
    """Remove posts that match known spam patterns."""
    clean = []
    removed = 0

    for post in posts:
        is_spam = any(pattern.search(post.text) for pattern in _SPAM_PATTERNS)
        if not is_spam:
            clean.append(post)
        else:
            removed += 1

    if removed:
        logger.info("Spam filter removed %d posts", removed)

    return clean


# =============================================================================
# QUALITY FILTERING
# =============================================================================

def _filter_low_quality(posts: List[SocialPost]) -> List[SocialPost]:
    """Remove posts that are too short or have no real content."""
    clean = []
    removed = 0

    for post in posts:
        text = post.text.strip()

        # Too short
        if len(text) < MIN_POST_LENGTH:
            removed += 1
            continue

        # Too few words (emojis alone don't count)
        words = re.findall(r"[a-zA-Z\u0600-\u06FF]{2,}", text)
        if len(words) < 2:
            removed += 1
            continue

        clean.append(post)

    if removed:
        logger.info("Quality filter removed %d posts", removed)

    return clean


# =============================================================================
# ENGAGEMENT FILTERING
# =============================================================================

def _filter_by_engagement(posts: List[SocialPost]) -> List[SocialPost]:
    """
    Remove posts with negligible engagement.

    Exception: Telegram posts often lack engagement metrics,
    so we only filter if the platform reports engagement.
    """
    clean = []

    for post in posts:
        total_engagement = sum(post.engagement.values())

        # Telegram/Reddit/news_derived may not expose real engagement metrics — be lenient
        if post.platform in ("telegram", "reddit", "news_derived"):
            # Accept if views > 0 or any engagement
            if total_engagement >= 1 or post.engagement.get("views", 0) > 0:
                clean.append(post)
                continue

        # For Twitter/StockTwits, require minimum engagement
        if total_engagement >= MIN_ENGAGEMENT_TOTAL:
            clean.append(post)

    return clean


# =============================================================================
# TIME-WINDOW GROUPING
# =============================================================================

def group_posts_by_day(posts: List[SocialPost]) -> Dict[str, List[SocialPost]]:
    """
    Group posts by date for temporal analysis.

    Returns dict mapping date string (YYYY-MM-DD) to list of posts.
    """
    groups: Dict[str, List[SocialPost]] = defaultdict(list)

    for post in posts:
        try:
            ts = post.timestamp.replace("Z", "")
            dt = datetime.fromisoformat(ts)
            day_key = dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            day_key = "unknown"

        groups[day_key].append(post)

    return dict(groups)


def compute_hype_index(posts: List[SocialPost], look_back_days: int = 7) -> float:
    """
    Compute a meaningful hype index [0.0 to 1.0] based on:

    1. Volume anomaly: posts-per-day vs expected baseline
    2. Emoji intensity: rocket/fire emoji density
    3. Extreme language density: strong bullish/bearish terms
    4. Engagement spike: average engagement vs baseline

    For EGX, even 3 posts/day is high activity for most stocks.
    """
    if not posts:
        return 0.0

    n_posts = len(posts)

    # ── Factor 1: Volume anomaly (40% weight) ──
    # Baseline: ~1 post/day for typical EGX stock
    posts_per_day = n_posts / max(look_back_days, 1)
    volume_score = min(1.0, posts_per_day / 5.0)  # 5 posts/day = max

    # ── Factor 2: Emoji intensity (20% weight) ──
    hype_emojis = sum(
        post.text.count("🚀") + post.text.count("🔥") + post.text.count("📈")
        + post.text.count("💰") + post.text.count("💎")
        for post in posts
    )
    emoji_score = min(1.0, hype_emojis / (n_posts * 0.8))

    # ── Factor 3: Extreme language (20% weight) ──
    extreme_terms = [
        "صاروخ", "هيطير", "فرصة العمر", "هينفجر", "كنز",
        "moon", "rocket", "100x", "10x", "to the moon", "gem",
        "انهيار", "كارثة", "نصب", "crash", "scam",
    ]
    extreme_count = sum(
        1 for post in posts
        for term in extreme_terms
        if term.lower() in post.text.lower()
    )
    language_score = min(1.0, extreme_count / max(n_posts * 0.3, 1))

    # ── Factor 4: Engagement spike (20% weight) ──
    if n_posts > 0:
        avg_engagement = sum(
            sum(p.engagement.values()) for p in posts
        ) / n_posts
        # Baseline: 50 engagement/post for EGX
        engagement_score = min(1.0, avg_engagement / 200.0)
    else:
        engagement_score = 0.0

    # Weighted combination
    hype = (
        volume_score * 0.40
        + emoji_score * 0.20
        + language_score * 0.20
        + engagement_score * 0.20
    )

    return round(min(1.0, hype), 3)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _extract_ticker(text: str, default_ticker: str) -> Optional[str]:
    """Extract EGX ticker from social media text."""
    text_upper = text.upper()
    text_lower = text.lower()

    for ticker, aliases in EGX_TICKER_ALIASES.items():
        if re.search(r"\b" + re.escape(ticker) + r"\b", text_upper):
            return ticker
        for alias in aliases:
            if alias.lower() in text_lower:
                return ticker

    return default_ticker


def _detect_language(text: str) -> str:
    """Detect language of social media text."""
    if not text:
        return "unknown"

    arabic_chars = len(re.findall(r"[\u0600-\u06FF]", text))
    latin_chars = len(re.findall(r"[a-zA-Z]", text))

    total = arabic_chars + latin_chars
    if total == 0:
        return "unknown"

    ratio = arabic_chars / total
    if ratio > 0.6:
        return "ar"
    elif ratio > 0.2:
        return "mixed"
    return "en"
