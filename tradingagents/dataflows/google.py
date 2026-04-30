from typing import Annotated
from datetime import datetime
from dateutil.relativedelta import relativedelta
from .googlenews_utils import getNewsData


def get_google_news(
    query: Annotated[str, "Query to search with"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    query = query.replace(" ", "+")

    # Use arguments directly (interface passes start_date, end_date)
    before = start_date
    curr_date = end_date

    try:
        news_results = getNewsData(query, before, curr_date)
    except Exception as e:
        print(f"WARNING: get_google_news failed: {e}")
        return "## Google News: No data available or error occurred."

    news_str = ""

    MAX_ARTICLES = 5
    article_count = 0
    
    for news in news_results[:MAX_ARTICLES]:
        snippet = news.get("snippet", "")
        if len(snippet) > 300:
            snippet = snippet[:300] + "..."
            
        news_str += (
            f"### {news['title']} (source: {news['source']}) \n\n{snippet}\n\n"
        )
        article_count += 1

    if len(news_results) == 0:
        return ""

    return f"## {query} Google News, from {before} to {curr_date}:\n\n{news_str}"