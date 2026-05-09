"""
Pluggable scraper sources.  Each source exports `scrape() -> List[Post]`.

Common Post schema (text, username, url, platform, source, timestamp,
engagement) — defined in social_pipeline/models.py.
"""

from __future__ import annotations
import os, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))  # scripts/social_pipeline/
if _PIPELINE_ROOT not in sys.path:
    sys.path.insert(0, _PIPELINE_ROOT)

from models import Post  # noqa: E402,F401  (re-export)
