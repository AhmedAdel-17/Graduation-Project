"""
Shared data model for the EGX social sentiment pipeline.

The Post dataclass is the canonical unit passed between all pipeline stages:
  SCRAPE → RELEVANCE → ENRICH → QUALITY GATE → SENTIMENT → AGGREGATE
"""

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class Post:
    text: str
    username: str
    timestamp: str
    url: str
    platform: str       # "facebook" | "reddit" | "telegram"
    source: str         # specific scraper that produced the row
    engagement: int = 0 # likes/score, when available

    def to_dict(self) -> dict:
        return asdict(self)
