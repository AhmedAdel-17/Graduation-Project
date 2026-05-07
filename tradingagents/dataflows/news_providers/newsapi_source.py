"""
NewsAPI.org Provider
=====================
Fetches news from NewsAPI.org (free tier: 100 requests/day).

Features:
- Searches for EGX company and market news
- Supports English AND Arabic results (bilingual queries)
- Full coverage: all 30 EGX tickers mapped to company names
- Graceful degradation if API key is missing

Usage:
    articles = fetch_newsapi_articles("COMI", days=7)
"""

import os
import logging
from typing import List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger("tradingagents.news.newsapi")

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

NEWSAPI_BASE_URL = "https://newsapi.org/v2/everything"

# =============================================================================
# EGX company name mappings — ALL 30 tickers in the universe
# Ticker → (English name, Arabic name)
# =============================================================================

EGX_COMPANY_NAMES = {
    # Banks
    "COMI":  ("Commercial International Bank", "البنك التجاري الدولي"),
    "ADIB":  ("Abu Dhabi Islamic Bank Egypt", "بنك أبوظبي الإسلامي مصر"),
    "CIEB":  ("Credit Industriel et Commercial Egypt", "سي اي بي"),
    "EXPA":  ("Export Development Bank of Egypt", "بنك تنمية الصادرات"),
    "HDBK":  ("Housing and Development Bank", "بنك الإسكان والتعمير"),
    "QNBA":  ("QNB Al Ahli", "بنك قطر الوطني الأهلي"),
    "SAUD":  ("Saudi Egyptian Construction", "السعودي المصري للتعمير"),
    # Real Estate
    "TMGH":  ("Talaat Moustafa Group", "مجموعة طلعت مصطفى"),
    "HELI":  ("Heliopolis Housing", "مساكن هليوبوليس"),
    "PHDC":  ("Palm Hills Development", "بالم هيلز للتعمير"),
    "OCDI":  ("Orascom Construction", "أوراسكوم للإنشاء"),
    "ORAS":  ("Orascom Development Egypt", "أوراسكوم للتطوير"),
    "EMFD":  ("Emaar Misr for Development", "إعمار مصر للتنمية"),
    # Industry & Materials
    "EAST":  ("Eastern Company", "الشرقية للدخان"),
    "ESRS":  ("Ezz Steel", "عز للصلب"),
    "SWDY":  ("Elsewedy Electric", "السويدي إلكتريك"),
    "ABUK":  ("Abu Qir Fertilizers", "أبو قير للأسمدة"),
    "MFPC":  ("Misr Fertilizers Production", "موبكو للأسمدة"),
    "EGAL":  ("Egyptian Aluminum", "مصر للألومنيوم"),
    "EGCH":  ("Egyptian Chemical Industries", "كيما للصناعات الكيماوية"),
    "EFIC":  ("Egyptian Financial and Industrial", "المالية والصناعية المصرية"),
    # Telecom & Tech
    "ETEL":  ("Telecom Egypt", "المصرية للاتصالات"),
    "FWRY":  ("Fawry for Banking Technology", "فوري للبنوك والتكنولوجيا"),
    "EFIH":  ("EFG Hermes Holding", "إي إف جي هيرميس القابضة"),
    "RAYA":  ("Raya Holding", "راية القابضة"),
    # Financial Services
    "HRHO":  ("EFG Hermes", "هيرميس للأوراق المالية"),
    "BTFH":  ("Beltone Financial Holding", "بلتون المالية القابضة"),
    "CICH":  ("CI Capital Holding", "سي آي كابيتال القابضة"),
    # Food & Beverage
    "JUFO":  ("Juhayna Food Industries", "جهينة للصناعات الغذائية"),
    "EFID":  ("Egyptian Food Industries", "مصر للصناعات الغذائية"),
    "DOMT":  ("Domty", "دومتي"),
}


def _get_api_key() -> Optional[str]:
    """Get NewsAPI key from environment. Returns None if not set."""
    key = os.getenv("NEWSAPI_KEY", "") or os.getenv("NEWS_API_KEY", "")
    if not key:
        logger.debug("NEWSAPI_KEY not set. NewsAPI provider disabled.")
        return None
    return key


def _build_english_query(ticker: str) -> str:
    """Build an English search query for a given EGX ticker."""
    ticker_clean = ticker.upper().replace(".CA", "").strip()
    names = EGX_COMPANY_NAMES.get(ticker_clean)
    if names:
        en_name, _ = names
        return f'("{en_name}" OR "{ticker_clean}") AND ("EGX" OR "Egypt" OR "Egyptian")'
    return f'"{ticker_clean}" AND ("EGX" OR "Egypt stock" OR "Egyptian exchange")'


def _build_arabic_query(ticker: str) -> Optional[str]:
    """Build an Arabic search query for a given EGX ticker."""
    ticker_clean = ticker.upper().replace(".CA", "").strip()
    names = EGX_COMPANY_NAMES.get(ticker_clean)
    if names:
        _, ar_name = names
        return f'"{ar_name}" OR "البورصة المصرية" OR "EGX"'
    return None


def fetch_newsapi_articles(
    ticker: str,
    days: int = 7,
    max_articles: int = 10,
) -> List[dict]:
    """
    Fetch news articles from NewsAPI.org for an EGX ticker.

    Runs TWO queries per ticker: one English, one Arabic (if available).
    Results are combined and returned as a flat list.

    Args:
        ticker: EGX ticker symbol (e.g., "COMI" or "COMI.CA")
        days: Number of days to look back
        max_articles: Maximum articles to return (across both languages)

    Returns:
        List of article dicts: title, summary, source, published_at, url, language

    Raises:
        RuntimeError: If API key missing or both requests fail
    """
    if not REQUESTS_AVAILABLE:
        raise RuntimeError("requests library not installed")

    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("NEWSAPI_KEY not set in environment or .env file")

    from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    articles: List[dict] = []

    # ── English query ────────────────────────────────────────────────────────
    en_query = _build_english_query(ticker)
    en_params = {
        "q": en_query,
        "from": from_date,
        "sortBy": "relevancy",
        "pageSize": min(max_articles, 100),
        "language": "en",
        "apiKey": api_key,
    }
    try:
        logger.info("NewsAPI EN query: %s (from %s)", en_query[:70], from_date)
        resp = requests.get(NEWSAPI_BASE_URL, params=en_params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "ok":
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
            logger.info("NewsAPI EN: %d articles for %s", len(articles), ticker)
        else:
            logger.warning("NewsAPI EN error: %s", data.get("message"))
    except requests.exceptions.RequestException as exc:
        logger.warning("NewsAPI EN request failed: %s", exc)

    # ── Arabic query ─────────────────────────────────────────────────────────
    ar_query = _build_arabic_query(ticker)
    if ar_query:
        ar_params = {
            "q": ar_query,
            "from": from_date,
            "sortBy": "relevancy",
            "pageSize": min(max_articles, 100),
            # No language filter — NewsAPI Arabic coverage is mixed-language
            "apiKey": api_key,
        }
        ar_before = len(articles)
        try:
            logger.info("NewsAPI AR query: %s (from %s)", ar_query[:70], from_date)
            resp = requests.get(NEWSAPI_BASE_URL, params=ar_params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "ok":
                for raw in data.get("articles", []):
                    if raw.get("title") == "[Removed]":
                        continue
                    articles.append({
                        "title": raw.get("title", ""),
                        "summary": (raw.get("description", "") or "")[:500],
                        "source": raw.get("source", {}).get("name", "NewsAPI"),
                        "published_at": raw.get("publishedAt", ""),
                        "url": raw.get("url", ""),
                        "language": "ar",
                    })
                logger.info("NewsAPI AR: %d articles added for %s",
                            len(articles) - ar_before, ticker)
            else:
                logger.warning("NewsAPI AR error: %s", data.get("message"))
        except requests.exceptions.RequestException as exc:
            logger.warning("NewsAPI AR request failed: %s", exc)

    if not articles:
        raise RuntimeError(
            f"NewsAPI returned zero articles for {ticker} "
            f"(both EN and AR queries failed or returned nothing)"
        )

    return articles[:max_articles * 2]  # allow up to 2x since we have two languages


def fetch_newsapi_global(
    days: int = 7,
    max_articles: int = 5,
) -> List[dict]:
    """
    Fetch global Egyptian market news from NewsAPI.org.
    Searches for broad EGX/Egyptian economy news in both languages.
    """
    if not REQUESTS_AVAILABLE:
        raise RuntimeError("requests library not installed")

    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("NEWSAPI_KEY not set")

    from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    articles: List[dict] = []

    queries = [
        ("en", '"Egyptian Stock Exchange" OR "EGX" OR "Egypt economy"'),
        ("ar", '"البورصة المصرية" OR "اقتصاد مصر" OR "EGX"'),
    ]

    for lang, query in queries:
        params = {
            "q": query,
            "from": from_date,
            "sortBy": "publishedAt",
            "pageSize": min(max_articles, 100),
            "apiKey": api_key,
        }
        if lang == "en":
            params["language"] = "en"

        try:
            resp = requests.get(NEWSAPI_BASE_URL, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "ok":
                for raw in data.get("articles", []):
                    if raw.get("title") == "[Removed]":
                        continue
                    articles.append({
                        "title": raw.get("title", ""),
                        "summary": (raw.get("description", "") or "")[:500],
                        "source": raw.get("source", {}).get("name", "NewsAPI"),
                        "published_at": raw.get("publishedAt", ""),
                        "url": raw.get("url", ""),
                        "language": lang,
                    })
        except requests.exceptions.RequestException as exc:
            logger.warning("NewsAPI global (%s) request failed: %s", lang, exc)

    logger.info("NewsAPI global returned %d articles", len(articles))
    return articles[:max_articles * 2]
