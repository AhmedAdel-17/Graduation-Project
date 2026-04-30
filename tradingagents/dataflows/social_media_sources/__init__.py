# =============================================================================
# Social Media Data Sources for EGX Trading Analysis
# =============================================================================
# Provides multi-platform social media data collection and sentiment analysis
# optimized for the Egyptian Stock Exchange (EGX).
#
# Supported platforms:
#   - Twitter/X (via web search proxy)
#   - Telegram (public channel scraping)
#   - Reddit (r/Egypt, r/stocks)
#   - StockTwits (US fallback)
#
# All data is normalized to SocialPost schema before analysis.
# =============================================================================

from .schema import SocialPost, SocialMediaResult
from .aggregator import get_social_media_data
from .sentiment_engine import analyze_social_sentiment, SentimentResult

__all__ = [
    "SocialPost",
    "SocialMediaResult",
    "get_social_media_data",
    "analyze_social_sentiment",
    "SentimentResult",
]
