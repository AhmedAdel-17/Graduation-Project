"""
Unified Schema for Social Media Data
=====================================
All social media sources normalize their output to this schema
before passing data to the sentiment engine and agents.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from datetime import datetime


@dataclass
class SocialPost:
    """
    A single social media post normalized to a unified schema.
    
    This is the atomic unit of social media data in the system.
    All platform-specific data formats are converted to this schema.
    """
    text: str                           # Post content (Arabic or English)
    timestamp: str                      # ISO format: YYYY-MM-DDTHH:MM:SS
    platform: str                       # "twitter", "telegram", "reddit", "stocktwits", "facebook"
    engagement: Dict[str, int] = field(  # Engagement metrics
        default_factory=lambda: {
            "likes": 0,
            "shares": 0, 
            "comments": 0,
            "views": 0
        }
    )
    ticker: Optional[str] = None        # Extracted EGX ticker (e.g., "COMI")
    language: str = "unknown"           # "ar", "en", "mixed"
    author: Optional[str] = None        # Username/handle
    url: Optional[str] = None           # Direct link to post
    raw_sentiment: Optional[float] = None  # Pre-computed sentiment [-1.0, 1.0]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SocialPost":
        """Create from dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class SocialMediaResult:
    """
    Aggregated result from all social media sources for a single query.
    
    Contains the raw posts plus metadata about the collection process.
    """
    ticker: str                          # Target ticker
    query_date: str                      # Date of the query
    look_back_days: int                  # How many days back we searched
    posts: List[SocialPost] = field(default_factory=list)
    
    # Metadata
    platforms_queried: List[str] = field(default_factory=list)
    platforms_succeeded: List[str] = field(default_factory=list)
    platforms_failed: Dict[str, str] = field(default_factory=dict)  # platform -> error message
    total_posts: int = 0
    
    # Language distribution
    arabic_posts: int = 0
    english_posts: int = 0
    mixed_posts: int = 0
    
    # Quality indicators
    has_sufficient_data: bool = False    # True if >= 5 posts found
    data_quality_score: float = 0.0     # 0-100 based on volume, freshness, engagement
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        result = {
            "ticker": self.ticker,
            "query_date": self.query_date,
            "look_back_days": self.look_back_days,
            "total_posts": self.total_posts,
            "platforms_queried": self.platforms_queried,
            "platforms_succeeded": self.platforms_succeeded,
            "platforms_failed": self.platforms_failed,
            "language_distribution": {
                "arabic": self.arabic_posts,
                "english": self.english_posts,
                "mixed": self.mixed_posts,
            },
            "has_sufficient_data": self.has_sufficient_data,
            "data_quality_score": self.data_quality_score,
            "posts": [p.to_dict() for p in self.posts],
        }
        return result
    
    def compute_metadata(self):
        """Recompute metadata fields from the current posts list."""
        self.total_posts = len(self.posts)
        self.arabic_posts = sum(1 for p in self.posts if p.language == "ar")
        self.english_posts = sum(1 for p in self.posts if p.language == "en")
        self.mixed_posts = sum(1 for p in self.posts if p.language == "mixed")
        self.has_sufficient_data = self.total_posts >= 5
        
        # Calculate quality score
        # Factors: volume (40%), freshness (30%), engagement (30%)
        volume_score = min(self.total_posts / 20, 1.0) * 40
        
        # Freshness: proportion of posts from last 3 days
        if self.posts:
            try:
                now = datetime.now()
                recent = sum(
                    1 for p in self.posts
                    if (now - datetime.fromisoformat(p.timestamp.replace("Z", ""))).days <= 3
                )
                freshness_score = (recent / self.total_posts) * 30
            except (ValueError, TypeError):
                freshness_score = 15  # Assume moderate freshness if parsing fails
        else:
            freshness_score = 0
        
        # Engagement: average engagement per post
        if self.posts:
            total_engagement = sum(
                sum(p.engagement.values()) for p in self.posts
            )
            avg_engagement = total_engagement / self.total_posts
            engagement_score = min(avg_engagement / 100, 1.0) * 30
        else:
            engagement_score = 0
        
        self.data_quality_score = round(volume_score + freshness_score + engagement_score, 1)
