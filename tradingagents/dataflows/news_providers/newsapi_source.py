"""
NewsAPI.org Provider
=====================
Fetches news from NewsAPI.org (free tier: 100 requests/day).

Features:
- Searches for EGX company and market news
- Supports English and Arabic results
- Graceful degradation if API key is missing

Usage:
    articles = fetch_newsapi_articles("COMI", days=7)
"""

import os
import logging
from typing import List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger("tradingagents.news.newsapi")

# Try to import requests
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

NEWSAPI_BASE_URL = "https://newsapi.org/v2/everything"

# EGX company name mappings for better search results
# Ticker → (English name, Arabic name)
EGX_COMPANY_NAMES = {
    "COMI": ("Commercial International Bank", "البنك التجاري الدولي"),
    "HRHO": ("Hermes Holding", "هيرميس القابضة"),
    "EAST": ("Eastern Company", "الشرقية ايسترن كومباني"),
    "EFIH": ("EFG Hermes", "إي اف جي هيرميس"),
    "SWDY": ("Elsewedy Electric", "السويدي اليكتريك"),
    "TMGH": ("Talaat Moustafa Group", "مجموعة طلعت مصطفى"),
    "ORWE": ("Oriental Weavers", "السجاد الشرقية"),
    "PHDC": ("Palm Hills Development", "بالم هيلز للتعمير"),
    "MNHD": ("Madinet Nasr Housing", "مدينة نصر للاسكان"),
    "ABUK": ("Abu Qir Fertilizers", "أبو قير للأسمدة"),
    "ETEL": ("Telecom Egypt", "المصرية للاتصالات"),
    "EKHO": ("Egyptian Kuwaiti Holding", "المصرية الكويتية"),
    "CLHO": ("Cleopatra Hospital", "مستشفى كليوباترا"),
}


def _get_api_key() -> Optional[str]:
    """Get NewsAPI key from environment. Returns None if not set."""
    key = os.getenv("NEWSAPI_KEY", "")
    if not key:
        logger.debug("NEWSAPI_KEY not set. NewsAPI provider disabled.")
        return None
    return key


def _build_query(ticker: str) -> str:
    """
    Build a search query optimized for EGX company news.
    
    Combines ticker, company name, and market context for comprehensive results.
    """
    ticker_clean = ticker.upper().replace(".CA", "").strip()
    
    # Get company names if available
    names = EGX_COMPANY_NAMES.get(ticker_clean, None)
    
    if names:
        en_name, ar_name = names
        # Search for: (company name OR ticker) AND (EGX OR Egypt stock)
        return f'("{en_name}" OR "{ticker_clean}") AND ("EGX" OR "Egypt" OR "Egyptian")'
    else:
        # Fallback for unknown tickers
        return f'"{ticker_clean}" AND ("EGX" OR "Egypt stock" OR "Egyptian exchange")'


def fetch_newsapi_articles(
    ticker: str,
    days: int = 7,
    max_articles: int = 10,
    language: str = "en",
) -> List[dict]:
    """
    Fetch news articles from NewsAPI.org for an EGX ticker.
    
    Args:
        ticker: EGX ticker symbol (e.g., "COMI" or "COMI.CA")
        days: Number of days to look back
        max_articles: Maximum articles to return
        language: Language filter ("en", "ar", or None for all)
        
    Returns:
        List of article dicts with keys: title, summary, source, 
        published_at, url, language
        
    Raises:
        RuntimeError: If API key missing or request fails
    """
    if not REQUESTS_AVAILABLE:
        raise RuntimeError("requests library not installed")
    
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("NEWSAPI_KEY not set in environment or .env file")
    
    query = _build_query(ticker)
    from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    
    params = {
        "q": query,
        "from": from_date,
        "sortBy": "relevancy",
        "pageSize": min(max_articles, 100),  # NewsAPI max is 100
        "apiKey": api_key,
    }
    
    # Only filter by language if specified
    if language:
        params["language"] = language
    
    try:
        logger.info("NewsAPI query: %s (from %s, lang=%s)", query[:60], from_date, language)
        response = requests.get(NEWSAPI_BASE_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"NewsAPI request failed: {e}")
    
    if data.get("status") != "ok":
        error_msg = data.get("message", "Unknown error")
        raise RuntimeError(f"NewsAPI error: {error_msg}")
    
    # Parse articles into our standard format
    articles = []
    for raw in data.get("articles", []):
        # Skip removed/unavailable articles
        if raw.get("title") == "[Removed]":
            continue
            
        articles.append({
            "title": raw.get("title", ""),
            "summary": (raw.get("description", "") or "")[:500],
            "source": raw.get("source", {}).get("name", "NewsAPI"),
            "published_at": raw.get("publishedAt", ""),
            "url": raw.get("url", ""),
            "language": language or "unknown",
        })
    
    logger.info("NewsAPI returned %d articles for '%s'", len(articles), ticker)
    return articles


def fetch_newsapi_global(
    days: int = 7,
    max_articles: int = 5,
) -> List[dict]:
    """
    Fetch global Egyptian market news from NewsAPI.org.
    
    Searches for broad EGX/Egyptian economy news rather than 
    company-specific articles.
    """
    if not REQUESTS_AVAILABLE:
        raise RuntimeError("requests library not installed")
    
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("NEWSAPI_KEY not set")
    
    from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    
    params = {
        "q": '"Egyptian Stock Exchange" OR "EGX" OR "Egypt economy" OR "البورصة المصرية"',
        "from": from_date,
        "sortBy": "publishedAt",
        "pageSize": min(max_articles, 100),
        "apiKey": api_key,
    }
    
    try:
        response = requests.get(NEWSAPI_BASE_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"NewsAPI global request failed: {e}")
    
    if data.get("status") != "ok":
        raise RuntimeError(f"NewsAPI error: {data.get('message', 'Unknown')}")
    
    articles = []
    for raw in data.get("articles", []):
        if raw.get("title") == "[Removed]":
            continue
        articles.append({
            "title": raw.get("title", ""),
            "summary": (raw.get("description", "") or "")[:500],
            "source": raw.get("source", {}).get("name", "NewsAPI"),
            "published_at": raw.get("publishedAt", ""),
            "url": raw.get("url", ""),
            "language": "en",
        })
    
    logger.info("NewsAPI global returned %d articles", len(articles))
    return articles
