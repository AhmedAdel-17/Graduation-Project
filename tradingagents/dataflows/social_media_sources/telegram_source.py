"""
Telegram Data Source for EGX Social Media Analysis
====================================================
Scrapes public Telegram channels focused on EGX trading.

Strategy:
- Uses t.me/s/{channel} web preview (no API key needed)
- Public channels only — no private groups
- Parses HTML from Telegram's web preview pages
- Anti-blocking: respectful rate limiting, user-agent rotation
"""

import re
import time
import random
import logging
from typing import List, Optional, Dict
from datetime import datetime, timedelta

from .schema import SocialPost

logger = logging.getLogger("tradingagents.social.telegram")

# =============================================================================
# Known Public EGX Telegram Channels
# =============================================================================
# These are real public channels that discuss EGX stocks.
# The system will attempt to scrape their web previews.

EGX_TELEGRAM_CHANNELS = [
    {"id": "egyptstockmarket", "name": "Egypt Stock Market", "language": "ar"},
    {"id": "egx_analysis", "name": "EGX Analysis", "language": "mixed"},
    {"id": "borsamasr", "name": "Borsa Masr", "language": "ar"},
    {"id": "cairo_trading", "name": "Cairo Trading", "language": "mixed"},
    {"id": "egx_signals", "name": "EGX Signals", "language": "ar"},
]

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0',
]


def fetch_telegram_data(
    ticker: str,
    curr_date: str,
    look_back_days: int = 7,
    channels: List[Dict] = None,
) -> List[SocialPost]:
    """
    Fetch Telegram data for an EGX stock from public channels.
    
    Scrapes t.me/s/{channel} web previews for messages mentioning
    the target ticker or its aliases.
    
    Args:
        ticker: EGX ticker symbol
        curr_date: Current date
        look_back_days: Days of data to fetch
        channels: Override default channel list
        
    Returns:
        List of SocialPost from Telegram
    """
    ticker = ticker.upper().replace(".CA", "").strip()
    channels = channels or EGX_TELEGRAM_CHANNELS
    posts = []
    
    # Get ticker aliases for broader matching
    from .cached_data import EGX_TICKER_ALIASES
    aliases = EGX_TICKER_ALIASES.get(ticker, [ticker])
    search_terms = [ticker] + aliases
    
    for channel in channels:
        try:
            channel_posts = _scrape_telegram_channel(
                channel["id"], 
                search_terms, 
                ticker,
                channel.get("language", "ar"),
            )
            posts.extend(channel_posts)
            
            # Respectful rate limiting
            time.sleep(random.uniform(1.0, 2.0))
            
        except Exception as e:
            logger.info("Telegram scrape failed for %s: %s", channel['id'], e)
            continue
    
    return posts


def _scrape_telegram_channel(
    channel_id: str,
    search_terms: List[str],
    ticker: str,
    default_language: str = "ar",
) -> List[SocialPost]:
    """
    Scrape a public Telegram channel via its web preview.
    
    Uses t.me/s/{channel} which shows recent messages
    without requiring Telegram API access.
    """
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        logger.info("requests/bs4 not available for Telegram scraping")
        return []
    
    url = f"https://t.me/s/{channel_id}"
    headers = {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            return []
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Telegram web preview uses specific classes for messages
        message_elements = soup.find_all('div', class_='tgme_widget_message_text')
        
        posts = []
        for msg_el in message_elements:
            text = msg_el.get_text(strip=True)
            
            if not text or len(text) < 10:
                continue
            
            # Check if message mentions our ticker or aliases
            text_lower = text.lower()
            mentions_ticker = any(
                term.lower() in text_lower 
                for term in search_terms
            )
            
            if not mentions_ticker:
                continue
            
            # Extract timestamp
            timestamp = datetime.now().isoformat()
            time_el = msg_el.find_parent('div', class_='tgme_widget_message')
            if time_el:
                time_tag = time_el.find('time')
                if time_tag and time_tag.get('datetime'):
                    timestamp = time_tag['datetime']
            
            # Extract engagement (views)
            views = 0
            if time_el:
                views_el = time_el.find('span', class_='tgme_widget_message_views')
                if views_el:
                    views_text = views_el.get_text(strip=True)
                    views = _parse_compact_number(views_text)
            
            # Detect language
            language = _detect_language(text) or default_language
            
            posts.append(SocialPost(
                text=text[:500],  # Truncate long messages
                timestamp=timestamp,
                platform="telegram",
                engagement={
                    "likes": 0, 
                    "shares": 0,
                    "comments": 0, 
                    "views": views
                },
                ticker=ticker,
                language=language,
                author=channel_id,
                url=f"https://t.me/{channel_id}",
            ))
        
        return posts
        
    except Exception as e:
        logger.debug("Telegram scrape error for %s: %s", channel_id, e)
        return []


def _parse_compact_number(text: str) -> int:
    """Parse compact numbers like '1.2K' or '3.5M'."""
    if not text:
        return 0
    text = text.strip().upper()
    try:
        if text.endswith('K'):
            return int(float(text[:-1]) * 1000)
        elif text.endswith('M'):
            return int(float(text[:-1]) * 1000000)
        else:
            return int(text.replace(',', ''))
    except (ValueError, TypeError):
        return 0


def _detect_language(text: str) -> str:
    """Simple Arabic/English detection."""
    if not text:
        return "unknown"
    arabic_chars = len(re.findall(r'[\u0600-\u06FF]', text))
    latin_chars = len(re.findall(r'[a-zA-Z]', text))
    total = arabic_chars + latin_chars
    if total == 0:
        return "unknown"
    ratio = arabic_chars / total
    if ratio > 0.6:
        return "ar"
    elif ratio > 0.2:
        return "mixed"
    return "en"
