"""
fetch_egx_news.py
=================
Fetches REAL historical news from Google News RSS for all 31 EGX tickers
and writes it to the CSV format expected by the news analyst agent.

Output:  data_cache/egx_news/csv/{TICKER}_news.csv
         data_cache/egx_news/global/egx_market_news.csv

Each CSV has columns: date, headline, body, source, language

Strategy:
  - Annual windows: 2022, 2023, 2024, 2025
  - Two searches per window per ticker: English + Arabic
  - Rate-limited (3-7s delay between requests)
  - Resumable: skips tickers that already have >= MIN_REAL_ARTICLES
  - Deduplicates by normalized headline

Usage:
    python scripts/fetch_egx_news.py            # all 31 tickers
    python scripts/fetch_egx_news.py --ticker COMI
    python scripts/fetch_egx_news.py --force     # re-fetch even if CSV exists
"""

import os
import sys
import csv
import re
import time
import random
import logging
import argparse
import urllib.parse
from datetime import datetime
from typing import List, Dict, Optional

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)

from tradingagents.dataflows.config import DATA_DIR

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

NEWS_CSV_DIR    = os.path.join(DATA_DIR, "egx_news", "csv")
NEWS_GLOBAL_DIR = os.path.join(DATA_DIR, "egx_news", "global")

os.makedirs(NEWS_CSV_DIR,    exist_ok=True)
os.makedirs(NEWS_GLOBAL_DIR, exist_ok=True)

# Skip tickers that already have this many real (non-seeded) articles
MIN_REAL_ARTICLES = 20

# Annual windows to search
YEARS = [2022, 2023, 2024, 2025]

# ---------------------------------------------------------------------------
# Company → Search Query mapping
# English query + Arabic query for each ticker
# ---------------------------------------------------------------------------
TICKER_QUERIES = {
    "COMI": {
        "en": "Commercial International Bank CIB Egypt",
        "ar": "البنك التجاري الدولي مصر",
    },
    "EAST": {
        "en": "Eastern Company Egypt tobacco cigarettes",
        "ar": "الشرقية للدخان مصر",
    },
    "FWRY": {
        "en": "Fawry Egypt payment fintech",
        "ar": "فوري للدفع الإلكتروني مصر",
    },
    "TMGH": {
        "en": "Talaat Moustafa Group Egypt real estate",
        "ar": "طلعت مصطفى مجموعة مصر",
    },
    "HRHO": {
        "en": "EFG Hermes Egypt investment bank",
        "ar": "هيرميس مصر للأوراق المالية",
    },
    "ETEL": {
        "en": "Telecom Egypt ETEL",
        "ar": "المصرية للاتصالات",
    },
    "ABUK": {
        "en": "Abu Qir Fertilizers Egypt",
        "ar": "أبو قير للأسمدة مصر",
    },
    "ADIB": {
        "en": "Abu Dhabi Islamic Bank Egypt",
        "ar": "بنك أبوظبي الإسلامي مصر",
    },
    "EFIH": {
        "en": "e-finance Egypt digital payments government",
        "ar": "إي فاينانس مصر الدفع الرقمي",
    },
    "EGAL": {
        "en": "Egyptian Gulf Bank EGB Egypt",
        "ar": "البنك المصري الخليجي",
    },
    "MFPC": {
        "en": "MOPCO Misr Fertilizers Egypt",
        "ar": "موبكو مصر للأسمدة",
    },
    "CCAP": {
        "en": "CI Capital Egypt investment",
        "ar": "سي آي كابيتال مصر استثمار",
    },
    "SKPC": {
        "en": "Sidi Kerir Petrochemicals SIDPEC Egypt",
        "ar": "سيدي كرير للبتروكيماويات مصر",
    },
    "AMOC": {
        "en": "Alexandria Mineral Oils Company Egypt",
        "ar": "الإسكندرية للزيوت المعدنية مصر",
    },
    "ESRS": {
        "en": "Ezz Steel Egypt",
        "ar": "عز للصلب مصر",
    },
    "ORWE": {
        "en": "Oriental Weavers Egypt carpet",
        "ar": "الشرقية للمفروشات مصر سجاد",
    },
    "HELI": {
        "en": "Heliopolis Housing Development Egypt",
        "ar": "مساكن هيليوبوليس مصر",
    },
    "GBCO": {
        "en": "GB Auto Egypt automotive",
        "ar": "جي بي أوتو مصر السيارات",
    },
    "SWDY": {
        "en": "Elsewedy Electric Egypt",
        "ar": "السويدي إليكتريك مصر",
    },
    "ORAS": {
        "en": "Orascom Construction Egypt",
        "ar": "أوراسكوم للإنشاء والصناعة مصر",
    },
    "PHDC": {
        "en": "Palm Hills Development Egypt",
        "ar": "بالم هيلز للتعمير مصر",
    },
    "CIEB": {
        "en": "Credit Agricole Egypt bank",
        "ar": "كريدي أغريكول مصر",
    },
    "ISPH": {
        "en": "Integrated Diagnostics Holdings IDH Egypt",
        "ar": "التشخيص المتكامل IDH مصر",
    },
    "DSCW": {
        "en": "Dice Sport Casual Wear Egypt",
        "ar": "دايس للملابس الرياضية مصر",
    },
    "RMDA": {
        "en": "Rameda Pharmaceuticals Egypt",
        "ar": "راميدا للأدوية مصر",
    },
    "ARCC": {
        "en": "Arab African International Bank Egypt",
        "ar": "العربي الأفريقي الدولي مصر",
    },
    "BTFH": {
        "en": "Beltone Financial Holding Egypt",
        "ar": "بلتون القابضة للخدمات المالية مصر",
    },
    "JUFO": {
        "en": "Juhayna Food Industries Egypt",
        "ar": "جهينة للصناعات الغذائية مصر",
    },
    "ORHD": {
        "en": "Orascom Development Holding Egypt",
        "ar": "أوراسكوم للتطوير القابضة مصر",
    },
    "RAYA": {
        "en": "Raya Holding Egypt technology",
        "ar": "راية القابضة مصر تكنولوجيا",
    },
    "VLMR": {
        "en": "Valore Egypt EGX stock",
        "ar": "فالور مصر البورصة",
    },
}

GLOBAL_QUERIES = {
    "en": "Egypt stock exchange EGX market economy",
    "ar": "البورصة المصرية اقتصاد مصر",
}

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

def detect_language(text: str) -> str:
    if not text:
        return "unknown"
    arabic = len(re.findall(r"[\u0600-\u06FF]", text))
    latin  = len(re.findall(r"[a-zA-Z]", text))
    total  = arabic + latin
    if total == 0:
        return "unknown"
    ratio = arabic / total
    if ratio > 0.5:
        return "arabic"
    return "english"


# ---------------------------------------------------------------------------
# Google News RSS fetcher (with rate limiting)
# ---------------------------------------------------------------------------

def _fetch_rss(query: str, start_date: str, end_date: str, lang: str = "en") -> List[Dict]:
    """
    Fetch Google News RSS for a query + date range.
    lang: "en" → English locale, "ar" → Arabic/Egypt locale
    """
    search_q = f"{query} after:{start_date} before:{end_date}"
    q = urllib.parse.quote(search_q)

    if lang == "ar":
        url = f"https://news.google.com/rss/search?q={q}&hl=ar&gl=EG&ceid=EG:ar"
    else:
        url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=EG&ceid=EG:en"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    delay = random.uniform(3, 7)
    log.debug("Sleeping %.1fs before request...", delay)
    time.sleep(delay)

    articles = []
    try:
        resp = requests.get(url, headers=headers, timeout=20)

        if resp.status_code == 429:
            log.warning("Rate-limited (429). Sleeping 30s...")
            time.sleep(30)
            resp = requests.get(url, headers=headers, timeout=20)

        if resp.status_code != 200:
            log.warning("HTTP %d for query: %s", resp.status_code, query)
            return []

        soup = BeautifulSoup(resp.content, "xml")
        for item in soup.find_all("item"):
            title  = item.title.text.strip() if item.title else ""
            source = item.source.text.strip() if item.find("source") else "Google News"
            date_raw = item.pubDate.text if item.pubDate else end_date

            try:
                dt   = datetime.strptime(date_raw, "%a, %d %b %Y %H:%M:%S %Z")
                date = dt.strftime("%Y-%m-%d")
            except Exception:
                date = end_date

            if not title:
                continue

            articles.append({
                "date":     date,
                "headline": title,
                "body":     title,   # RSS doesn't provide full body
                "source":   source,
                "language": detect_language(title),
            })

    except Exception as e:
        log.warning("Fetch failed for '%s': %s", query, e)

    return articles


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())[:120]


def deduplicate(articles: List[Dict]) -> List[Dict]:
    seen = set()
    result = []
    for a in articles:
        fp = _normalize(a["headline"])
        if fp not in seen:
            seen.add(fp)
            result.append(a)
    return result


# ---------------------------------------------------------------------------
# Check if ticker already has real (non-seeded) data
# ---------------------------------------------------------------------------

def _is_seeded(csv_path: str) -> bool:
    """
    Returns True if the CSV looks like it was written by seed_egx_news.py
    (all headlines are identical repeated templates).
    """
    if not os.path.exists(csv_path):
        return False
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        if len(rows) < 5:
            return True   # Too sparse — treat as seeded
        # If the same headline appears multiple times it's seeded
        headlines = [r.get("headline", "") for r in rows]
        unique = len(set(headlines))
        # Seeded data has very few unique headlines (same 3-4 cycling)
        return unique < len(headlines) * 0.6
    except Exception:
        return True


def has_enough_real_news(csv_path: str, min_articles: int) -> bool:
    if not os.path.exists(csv_path):
        return False
    if _is_seeded(csv_path):
        return False
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        return len(rows) >= min_articles
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Save CSV
# ---------------------------------------------------------------------------

def save_csv(path: str, articles: List[Dict]):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "headline", "body", "source", "language"])
        writer.writeheader()
        # Sort by date descending (most recent first)
        for a in sorted(articles, key=lambda x: x["date"], reverse=True):
            writer.writerow(a)
    log.info("Saved %d articles → %s", len(articles), path)


# ---------------------------------------------------------------------------
# Fetch one ticker
# ---------------------------------------------------------------------------

def fetch_ticker_news(ticker: str, force: bool = False) -> int:
    """
    Fetch news for one EGX ticker across all years.
    Returns total article count saved.
    """
    csv_path = os.path.join(NEWS_CSV_DIR, f"{ticker}_news.csv")

    if not force and has_enough_real_news(csv_path, MIN_REAL_ARTICLES):
        log.info("[%s] Already has real news — skipping.", ticker)
        return 0

    queries = TICKER_QUERIES.get(ticker)
    if not queries:
        log.warning("[%s] No query mapping — skipping.", ticker)
        return 0

    all_articles: List[Dict] = []

    for year in YEARS:
        start = f"{year}-01-01"
        end   = f"{year}-12-31"

        log.info("[%s] Fetching EN %d...", ticker, year)
        en_articles = _fetch_rss(queries["en"], start, end, lang="en")
        log.info("[%s] EN %d → %d articles", ticker, year, len(en_articles))
        all_articles.extend(en_articles)

        log.info("[%s] Fetching AR %d...", ticker, year)
        ar_articles = _fetch_rss(queries["ar"], start, end, lang="ar")
        log.info("[%s] AR %d → %d articles", ticker, year, len(ar_articles))
        all_articles.extend(ar_articles)

    all_articles = deduplicate(all_articles)

    if not all_articles:
        log.warning("[%s] No articles found — keeping existing CSV if any.", ticker)
        return 0

    save_csv(csv_path, all_articles)
    return len(all_articles)


# ---------------------------------------------------------------------------
# Fetch global / market-wide news
# ---------------------------------------------------------------------------

def fetch_global_news(force: bool = False) -> int:
    csv_path = os.path.join(NEWS_GLOBAL_DIR, "egx_market_news.csv")

    if not force and has_enough_real_news(csv_path, MIN_REAL_ARTICLES):
        log.info("[GLOBAL] Already has real news — skipping.")
        return 0

    all_articles: List[Dict] = []

    for year in YEARS:
        start = f"{year}-01-01"
        end   = f"{year}-12-31"

        log.info("[GLOBAL] Fetching EN %d...", year)
        en_articles = _fetch_rss(GLOBAL_QUERIES["en"], start, end, lang="en")
        log.info("[GLOBAL] EN %d → %d articles", year, len(en_articles))
        all_articles.extend(en_articles)

        log.info("[GLOBAL] Fetching AR %d...", year)
        ar_articles = _fetch_rss(GLOBAL_QUERIES["ar"], start, end, lang="ar")
        log.info("[GLOBAL] AR %d → %d articles", year, len(ar_articles))
        all_articles.extend(ar_articles)

    all_articles = deduplicate(all_articles)
    if all_articles:
        save_csv(csv_path, all_articles)
    return len(all_articles)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

ALL_TICKERS = list(TICKER_QUERIES.keys())


def main():
    parser = argparse.ArgumentParser(
        description="Fetch real Google News for EGX tickers and save as CSV."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ticker", type=str, help="Single ticker (e.g. COMI)")
    group.add_argument("--all",    action="store_true", default=True,
                       help="Fetch all 31 tickers (default)")
    parser.add_argument("--force", action="store_true",
                        help="Re-fetch even if real news already exists")
    parser.add_argument("--global-only", action="store_true",
                        help="Only fetch global market news")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("  EGX NEWS FETCHER — Google News RSS")
    log.info("  Years: %s | Force: %s", YEARS, args.force)
    log.info("=" * 60)

    results = {}

    # Always fetch global news
    if not args.ticker:
        log.info("\n[GLOBAL] Fetching market-wide news...")
        global_count = fetch_global_news(force=args.force)
        results["GLOBAL"] = global_count

    if args.global_only:
        print(f"\nGlobal news: {results.get('GLOBAL', 0)} articles")
        return

    tickers = [args.ticker.upper()] if args.ticker else ALL_TICKERS

    log.info("\nFetching news for %d tickers...", len(tickers))

    for i, ticker in enumerate(tickers, 1):
        log.info("\n[%d/%d] ── %s ──", i, len(tickers), ticker)
        count = fetch_ticker_news(ticker, force=args.force)
        results[ticker] = count

    # Summary
    print("\n" + "=" * 60)
    print("  FETCH SUMMARY")
    print("=" * 60)
    total_articles = 0
    for t, count in results.items():
        csv_path = (
            os.path.join(NEWS_GLOBAL_DIR, "egx_market_news.csv")
            if t == "GLOBAL"
            else os.path.join(NEWS_CSV_DIR, f"{t}_news.csv")
        )
        # Get actual file count
        try:
            with open(csv_path, newline="", encoding="utf-8") as f:
                actual = sum(1 for _ in csv.DictReader(f))
        except Exception:
            actual = 0
        status = f"{actual} articles in file"
        total_articles += actual
        print(f"  {t}: {status}")
    print(f"\n  Total articles across all files: {total_articles}")
    print("=" * 60)


if __name__ == "__main__":
    main()
