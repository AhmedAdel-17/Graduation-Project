"""
Pluggable scraper sources.  Each source exports `scrape() -> List[Post]`.

Common Post schema (text, username, url, platform, source, timestamp,
engagement) — re-exported from the v1 scraper to stay binary-compatible.
"""

from __future__ import annotations
import os, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_V1   = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _V1 not in sys.path:
    sys.path.insert(0, _V1)

from scraper import Post  # noqa: E402,F401  (re-export)
