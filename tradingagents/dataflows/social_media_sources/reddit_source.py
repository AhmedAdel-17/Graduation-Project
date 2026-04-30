"""
Reddit Data Source for EGX Social Media Analysis
==================================================
Searches Reddit for EGX stock mentions.

Strategy:
- Uses existing reddit_utils.py from the project
- Searches r/Egypt, r/stocks, r/investing for EGX mentions
- Low volume expected — treated as supplementary signal
"""

from typing import List
from datetime import datetime, timedelta
from .schema import SocialPost


def fetch_reddit_data(
    ticker: str,
    curr_date: str,
    look_back_days: int = 7,
) -> List[SocialPost]:
    """
    Fetch Reddit data for an EGX stock.
    
    Uses existing project reddit_utils for compatibility.
    Reddit has very low EGX coverage, so this is supplementary.
    
    Args:
        ticker: EGX ticker symbol
        curr_date: Current date
        look_back_days: Days of data to fetch
        
    Returns:
        List of SocialPost from Reddit
    """
    ticker = ticker.upper().replace(".CA", "").strip()
    posts = []
    
    try:
        from tradingagents.dataflows.local import get_reddit_company_news
        
        end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=look_back_days)
        start_date = start_dt.strftime("%Y-%m-%d")
        
        # Try multiple search terms
        search_terms = [ticker, f"{ticker}.CA", f"{ticker} EGX"]
        
        for term in search_terms:
            try:
                result = get_reddit_company_news(term, start_date, curr_date)
                if result and len(result) > 10:
                    # Parse the text result into posts
                    posts.extend(_parse_reddit_text(result, ticker))
                    break
            except Exception:
                continue
                
    except ImportError:
        pass
    
    return posts


def _parse_reddit_text(text: str, ticker: str) -> List[SocialPost]:
    """Parse Reddit text output into SocialPost objects."""
    posts = []
    
    # Split by markdown headers (### )
    sections = text.split("###")
    
    for section in sections[1:]:  # Skip first empty section
        lines = section.strip().split("\n")
        if not lines:
            continue
        
        title = lines[0].strip()
        content = "\n".join(lines[1:]).strip()
        
        if len(title) < 5:
            continue
        
        posts.append(SocialPost(
            text=f"{title}\n{content}"[:500],
            timestamp=datetime.now().isoformat(),
            platform="reddit",
            engagement={"likes": 0, "shares": 0, "comments": 0, "views": 0},
            ticker=ticker,
            language="en",  # Reddit is predominantly English
            author="reddit_user",
            url=None,
        ))
    
    return posts
