"""Canonical Post dataclass shared by every source in social_v2."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class Post:
    text: str
    username: str
    timestamp: str
    url: str
    platform: str
    source: str
    engagement: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "username": self.username,
            "timestamp": self.timestamp,
            "url": self.url,
            "platform": self.platform,
            "source": self.source,
            "engagement": int(self.engagement or 0),
        }
