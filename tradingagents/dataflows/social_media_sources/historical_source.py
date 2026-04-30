"""
Historical Social Media Source (News-Derived)
=============================================
For backtesting, live social media APIs (Twitter, Telegram, Reddit) are
unavailable for historical dates. This module bridges that gap by treating
our real, date-indexed news articles as social signal proxies.

Rationale:
  - News headlines are what drives retail social media discussion.
  - We already have 7,400+ real bilingual articles (2022-2025) in egx_news/csv/.
  - Each article is treated as a "news-derived" social signal for that date.
  - The sentiment engine scores the actual headline text, giving historically
    accurate signal variation across tickers and dates.

Data source: tradingagents/dataflows/data_cache/egx_news/csv/{TICKER}_news.csv
             tradingagents/dataflows/data_cache/egx_news/global/egx_market_news.csv

Post platform label: "news_derived"  (honest — not faked as twitter/telegram)
"""

import csv
import logging
import os
from datetime import datetime, timedelta
from typing import List, Optional

from .schema import SocialPost

logger = logging.getLogger("tradingagents.social.historical")

# Path to the news CSV cache
_NEWS_DIR = os.path.join(
    os.path.dirname(__file__),
    "..", "data_cache", "egx_news", "csv"
)
_GLOBAL_NEWS_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "data_cache", "egx_news", "global", "egx_market_news.csv"
)

# Map CSV language values → schema language codes
_LANG_MAP = {
    "arabic": "ar",
    "english": "en",
    "mixed": "mixed",
    "ar": "ar",
    "en": "en",
}

# Engagement baseline for news articles
# (Real engagement data isn't available from RSS; use modest defaults
#  that pass the aggregator's engagement filter without over-weighting)
_BASE_ENGAGEMENT = {"likes": 10, "shares": 3, "comments": 5, "views": 250}


def fetch_historical_posts(
    ticker: str,
    curr_date: str,
    look_back_days: int = 7,
    max_posts: int = 20,
) -> List[SocialPost]:
    """
    Return news-derived SocialPost objects for the given ticker and date window.

    Reads from the ticker-specific news CSV (if it exists) and the global
    EGX market news CSV, filters rows to [curr_date - look_back_days, curr_date],
    and converts each article to a SocialPost.

    Args:
        ticker:         EGX ticker (e.g., "COMI")
        curr_date:      Backtest date in "YYYY-MM-DD" format
        look_back_days: How many days back to include (default 7)
        max_posts:      Cap on posts returned (default 20)

    Returns:
        List of SocialPost objects, oldest-first.
    """
    ticker = ticker.upper().replace(".CA", "").strip()

    try:
        end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    except ValueError:
        logger.warning("Invalid curr_date '%s', cannot load historical posts", curr_date)
        return []

    start_dt = end_dt - timedelta(days=look_back_days)

    posts: List[SocialPost] = []

    # 1. Ticker-specific news
    ticker_csv = os.path.join(_NEWS_DIR, f"{ticker}_news.csv")
    posts.extend(_load_csv(ticker_csv, ticker, start_dt, end_dt))

    # 2. Global EGX market news (always included — market-wide signal)
    posts.extend(_load_csv(_GLOBAL_NEWS_PATH, None, start_dt, end_dt))

    # Sort oldest → newest, cap at max_posts
    posts.sort(key=lambda p: p.timestamp)
    posts = posts[:max_posts]

    if posts:
        logger.info(
            "[historical] %s: loaded %d news-derived posts for %s (window: %s → %s)",
            ticker, len(posts), curr_date,
            start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"),
        )
    else:
        logger.info(
            "[historical] %s: no news articles found in window %s → %s",
            ticker, start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"),
        )

    return posts


def _load_csv(
    csv_path: str,
    ticker: Optional[str],
    start_dt: datetime,
    end_dt: datetime,
) -> List[SocialPost]:
    """
    Load and filter articles from a single news CSV file.

    Expected CSV columns: date, headline, body, source, language
    """
    if not os.path.isfile(csv_path):
        return []

    posts: List[SocialPost] = []

    try:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                post = _row_to_post(row, ticker, start_dt, end_dt)
                if post is not None:
                    posts.append(post)
    except Exception as e:
        logger.warning("Failed to read %s: %s", csv_path, e)

    return posts


def _row_to_post(
    row: dict,
    ticker: Optional[str],
    start_dt: datetime,
    end_dt: datetime,
) -> Optional[SocialPost]:
    """
    Convert a CSV row to a SocialPost if it falls within the date window.

    Returns None if the row is out of window or malformed.
    """
    raw_date = row.get("date", "").strip()
    if not raw_date:
        return None

    try:
        article_dt = datetime.strptime(raw_date, "%Y-%m-%d")
    except ValueError:
        return None

    # Strict window filter: start_dt <= article_dt <= end_dt
    if not (start_dt <= article_dt <= end_dt):
        return None

    headline = row.get("headline", "").strip()
    if not headline:
        return None

    # Use headline as the post text (body is usually identical in RSS)
    text = headline

    # Timestamp at noon on article date (RSS doesn't give exact time)
    timestamp = article_dt.strftime("%Y-%m-%dT12:00:00")

    raw_lang = row.get("language", "").strip().lower()
    language = _LANG_MAP.get(raw_lang, "unknown")

    source = row.get("source", "news").strip() or "news"

    return SocialPost(
        text=text,
        timestamp=timestamp,
        platform="news_derived",
        engagement=dict(_BASE_ENGAGEMENT),   # copy so mutations don't share state
        ticker=ticker,
        language=language,
        author=source,
    )
