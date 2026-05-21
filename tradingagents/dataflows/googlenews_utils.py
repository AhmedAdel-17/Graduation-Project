import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import time
import random
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    retry_if_result,
)


# Bounded HTTP timeout for the Google News RSS scrape. Without this, a single
# slow/hanging socket inside the news-prefetch ThreadPoolExecutor would block
# the entire pipeline indefinitely (the executor's `__exit__` waits on all
# threads, so even a per-future result timeout doesn't save us).
GOOGLENEWS_HTTP_TIMEOUT = 10  # seconds


def is_rate_limited(response):
    """Check if the response indicates rate limiting (status code 429)"""
    return response.status_code == 429


@retry(
    retry=(retry_if_result(is_rate_limited)),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(3),
)
def make_request(url, headers):
    """Make a request with retry logic for rate limiting."""
    # Short randomized delay to soften bot detection — keep below 2s so the
    # 3-attempt retry envelope stays inside the prefetcher's 30s budget.
    time.sleep(random.uniform(0.5, 1.5))
    response = requests.get(url, headers=headers, timeout=GOOGLENEWS_HTTP_TIMEOUT)
    return response


def getNewsData(query, start_date, end_date):
    """
    Scrape Google News search results for a given query and date range using RSS endpoint.
    query: str - search query
    start_date: str - start date in the format yyyy-mm-dd or mm/dd/yyyy
    end_date: str - end date in the format yyyy-mm-dd or mm/dd/yyyy
    """
    if "/" in start_date:
        start_date = datetime.strptime(start_date, "%m/%d/%Y").strftime("%Y-%m-%d")
    if "/" in end_date:
        end_date = datetime.strptime(end_date, "%m/%d/%Y").strftime("%Y-%m-%d")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/101.0.4951.54 Safari/537.36"
        )
    }

    # Format query with date restrictions
    search_query = f"{query} after:{start_date} before:{end_date}"
    import urllib.parse
    q = urllib.parse.quote(search_query)
    url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

    news_results = []
    try:
        response = make_request(url, headers)
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, "xml")
            items = soup.find_all("item")
            
            for item in items:
                title = item.title.text if item.title else ""
                link = item.link.text if item.link else ""
                date_str = item.pubDate.text if item.pubDate else end_date
                
                # convert typical RSS date format to simple YYYY-MM-DD
                try:
                    # Example: "Tue, 07 Apr 2026 17:16:02 GMT"
                    dt = datetime.strptime(date_str, "%a, %d %b %Y %H:%M:%S %Z")
                    date = dt.strftime("%Y-%m-%d")
                except Exception:
                    date = end_date
                    
                source = item.source.text if item.find("source") else "Google News"
                
                news_results.append({
                    "link": link,
                    "title": title,
                    "snippet": title, # RSS doesn't give large snippets, use title
                    "date": date,
                    "source": source,
                })
    except Exception as e:
        print(f"Failed to fetch Google News RSS: {e}")

    return news_results
