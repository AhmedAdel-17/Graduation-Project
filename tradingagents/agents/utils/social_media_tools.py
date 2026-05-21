"""
Social Media Analysis Tools (LangChain Tool Wrappers)
=====================================================
Wraps the social media data collection and sentiment analysis
as LangChain tools that agents can invoke during graph execution.
"""

from langchain_core.tools import tool
from typing import Annotated
import json

from tradingagents.dataflows.social_media_sources.aggregator import get_social_media_data
from tradingagents.dataflows.social_media_sources.sentiment_engine import analyze_social_sentiment


@tool
def get_social_sentiment(
    ticker: Annotated[str, "EGX ticker symbol (e.g., COMI, HRHO)"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "Number of days of social media data to analyze"] = 7,
) -> str:
    """
    Collect social media data from Twitter, Telegram, Reddit and analyze sentiment
    for an EGX stock. Returns structured sentiment analysis including:
    - Overall sentiment score (-1 to 1)
    - Buzz/mention volume score
    - Momentum (sentiment trend)
    - Hype detection
    - Platform breakdown
    - Arabic vs English sentiment comparison
    - Key discussion themes
    
    Use this tool to understand retail investor sentiment and public perception
    of a stock on the Egyptian Exchange.
    
    Args:
        ticker: EGX ticker symbol (e.g., "COMI", "HRHO", "EAST")
        curr_date: Current date in yyyy-mm-dd format
        look_back_days: How many days of data to analyze (default 7)
        
    Returns:
        str: JSON-formatted sentiment analysis report
    """
    # Clamp curr_date to trade_date — prevents future sentiment leakage
    # during historical backtesting (LLM may pass today's date instead of
    # the evaluation date).
    from tradingagents.dataflows.config import get_config
    _trade_date = get_config().get("trade_date")
    if _trade_date and curr_date > _trade_date:
        curr_date = _trade_date

    # Preferred path: v2 pipeline (Facebook Apify + Reddit + transformer
    # sentiment + per-stock aggregation). Returns the agent-compatible
    # JSON directly.
    try:
        from tradingagents.dataflows.social_v2.signal_adapter import fetch_v2_signal_json
        return fetch_v2_signal_json(ticker, curr_date, look_back_days)
    except Exception:
        # Fall through to legacy v1 below.
        pass

    # Step 1: Collect social media data
    social_data = get_social_media_data(ticker, curr_date, look_back_days)
    
    # Step 2: Analyze sentiment
    sentiment = analyze_social_sentiment(social_data)
    
    # Step 3: Build comprehensive report
    report = {
        "ticker": ticker,
        "analysis_date": curr_date,
        "look_back_days": look_back_days,
        
        # Core scores
        "sentiment_score": sentiment.sentiment_score,
        "buzz_score": sentiment.buzz_score,
        "momentum_score": sentiment.momentum_score,
        "confidence": sentiment.confidence,
        
        # Signal breakdown
        "signals": {
            "bullish": sentiment.bullish_signals,
            "bearish": sentiment.bearish_signals,
            "neutral": sentiment.neutral_signals,
            "total_posts": sentiment.total_posts_analyzed,
        },
        
        # Hype detection
        "hype_alert": {
            "detected": sentiment.hype_detected,
            "reasons": sentiment.hype_reasons,
        },
        
        # Platform breakdown
        "platform_sentiment": sentiment.platform_sentiment,
        
        # Language breakdown
        "language_sentiment": {
            "arabic": sentiment.arabic_sentiment,
            "english": sentiment.english_sentiment,
        },
        
        # Key signals
        "top_bullish_signals": sentiment.top_bullish_signals[:3],
        "top_bearish_signals": sentiment.top_bearish_signals[:3],
        
        # Themes
        "key_themes": sentiment.key_themes,
        
        # Data quality
        "data_quality": {
            "sufficient_data": sentiment.data_sufficient,
            "quality_note": sentiment.data_quality_note,
            "platforms_succeeded": social_data.platforms_succeeded,
            "platforms_failed": list(social_data.platforms_failed.keys()),
        },
        
        # Sample posts (top 3 by engagement for context)
        "sample_posts": [
            {
                "text": p.text[:200],
                "platform": p.platform,
                "language": p.language,
                "engagement": sum(p.engagement.values()),
            }
            for p in sorted(
                social_data.posts,
                key=lambda x: sum(x.engagement.values()),
                reverse=True
            )[:3]
        ],
    }
    
    return json.dumps(report, indent=2, ensure_ascii=False, default=str)


@tool
def get_social_media_posts(
    ticker: Annotated[str, "EGX ticker symbol (e.g., COMI, HRHO)"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "Number of days of data to fetch"] = 7,
    platform: Annotated[str, "Specific platform to query (twitter/telegram/reddit/all)"] = "all",
) -> str:
    """
    Fetch raw social media posts about an EGX stock from multiple platforms.
    Returns the actual post text and metadata for manual analysis.
    
    Use this when you need to see the actual content people are posting,
    rather than just the sentiment scores.
    
    Args:
        ticker: EGX ticker symbol
        curr_date: Current date in yyyy-mm-dd format
        look_back_days: How many days of data to fetch
        platform: Specific platform or "all"
        
    Returns:
        str: JSON-formatted list of social media posts with metadata
    """
    # Clamp curr_date to trade_date — prevents future data leakage during
    # historical backtesting.
    from tradingagents.dataflows.config import get_config
    _trade_date = get_config().get("trade_date")
    if _trade_date and curr_date > _trade_date:
        curr_date = _trade_date

    platforms = None if platform == "all" else [platform]

    social_data = get_social_media_data(
        ticker, curr_date, look_back_days,
        platforms=platforms,
        include_market_sentiment=False,
    )
    
    # Format posts for readability
    posts_formatted = []
    for post in social_data.posts[:15]:  # Max 15 posts to avoid token overflow
        posts_formatted.append({
            "text": post.text[:300],
            "platform": post.platform,
            "language": post.language,
            "timestamp": post.timestamp,
            "engagement": post.engagement,
            "author": post.author,
        })
    
    result = {
        "ticker": ticker,
        "total_posts_found": social_data.total_posts,
        "showing": len(posts_formatted),
        "platforms_queried": social_data.platforms_queried,
        "language_distribution": {
            "arabic": social_data.arabic_posts,
            "english": social_data.english_posts,
            "mixed": social_data.mixed_posts,
        },
        "posts": posts_formatted,
    }
    
    return json.dumps(result, indent=2, ensure_ascii=False, default=str)
