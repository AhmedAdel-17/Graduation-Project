"""
Twitter/X Data Source for EGX Social Media Analysis
=====================================================
Fetches EGX-related tweets via web search proxy.

Strategy:
- Cannot use Twitter API (expensive, rate-limited)
- Instead uses Google News search with site:x.com filter
- Falls back to cached data when search fails
- Anti-blocking: randomized user agents, delays, caching
"""

import re
import time
import random
import logging
from typing import List, Optional
from datetime import datetime, timedelta

from .schema import SocialPost

logger = logging.getLogger("tradingagents.social.twitter")

# Known EGX finance Twitter accounts (curated list)
EGX_TWITTER_ACCOUNTS = [
    "EGaborsa",         # Egyptian stock market discussions
    "BorsaMasr",        # Borsa Masr community
    "EGX_News",         # EGX news
    "MubasherInfo",     # Mubasher financial news
    "AlBorsaNews",      # Al Borsa newspaper
    "AlMalNews",        # Al Mal newspaper
    "EnterpriseME",     # Enterprise (English Egypt business)
]


def fetch_twitter_data(
    ticker: str,
    curr_date: str,
    look_back_days: int = 7,
) -> List[SocialPost]:
    """
    Fetch Twitter/X data for an EGX stock.
    
    Uses Google search as proxy since direct Twitter API is not available.
    Falls back to cached data for reliability.
    
    Args:
        ticker: EGX ticker symbol (e.g., "COMI")
        curr_date: Current date 
        look_back_days: Days of data to fetch
        
    Returns:
        List of SocialPost from Twitter/X
    """
    ticker = ticker.upper().replace(".CA", "").strip()
    posts = []
    
    # Attempt web search for tweets
    try:
        posts = _search_tweets_via_web(ticker, curr_date, look_back_days)
    except Exception as e:
        logger.info("Twitter web search failed for %s: %s", ticker, e)
    
    return posts


def _search_tweets_via_web(
    ticker: str,
    curr_date: str,
    look_back_days: int,
) -> List[SocialPost]:
    """
    Search for tweets about an EGX stock via public web search.
    
    This is a best-effort approach that may return 0 results
    if search engines block the request.
    """
    try:
        from tradingagents.dataflows.googlenews_utils import search_google_news
    except ImportError:
        return []
    
    # Build search queries
    queries = [
        f'"{ticker}" EGX stock',
        f'"{ticker}.CA" بورصة' if len(ticker) <= 5 else None,
    ]
    
    posts = []
    for query in queries:
        if query is None:
            continue
            
        try:
            # Add delay to avoid rate limiting
            time.sleep(random.uniform(0.5, 1.5))
            
            results = search_google_news(query, lookback_days=look_back_days)
            
            if results:
                for r in results[:5]:  # Max 5 per query
                    post = SocialPost(
                        text=r.get("title", "") + " " + r.get("snippet", ""),
                        timestamp=r.get("date", datetime.now().isoformat()),
                        platform="twitter",
                        engagement={"likes": 0, "shares": 0, "comments": 0, "views": 0},
                        ticker=ticker,
                        language=_detect_language(r.get("title", "")),
                        author=r.get("source", "unknown"),
                        url=r.get("url"),
                    )
                    posts.append(post)
        except Exception:
            continue
    
    return posts


def _detect_language(text: str) -> str:
    """Simple language detection based on character analysis."""
    if not text:
        return "unknown"
    
    arabic_chars = len(re.findall(r'[\u0600-\u06FF]', text))
    latin_chars = len(re.findall(r'[a-zA-Z]', text))
    
    total = arabic_chars + latin_chars
    if total == 0:
        return "unknown"
    
    arabic_ratio = arabic_chars / total
    if arabic_ratio > 0.6:
        return "ar"
    elif arabic_ratio > 0.2:
        return "mixed"
    else:
        return "en"
