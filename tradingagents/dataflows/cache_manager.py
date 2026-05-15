"""
Cache Manager for TradingAgents
================================
Provides a unified caching layer using DiskCache (SQLite-backed).

Design:
- Interface-based: can swap DiskCache for Redis later without changing callers
- TTL-based expiration per data type
- Thread-safe and process-safe
- Cache stats for monitoring

No server dependency — just a local SQLite file.
"""

import logging
import time
from typing import Any, Callable, Optional
from pathlib import Path

logger = logging.getLogger("tradingagents.cache")

# Try to import diskcache; fall back to a no-op cache if not installed
try:
    import diskcache
    DISKCACHE_AVAILABLE = True
except ImportError:
    DISKCACHE_AVAILABLE = False
    logger.warning(
        "diskcache not installed. Caching disabled. "
        "Install with: pip install diskcache"
    )


# TTL defaults (seconds) per data type
DEFAULT_TTL = {
    "ohlcv": 4 * 3600,         # 4 hours — delayed data doesn't change intraday
    "realtime_price": 60,       # 1 minute — near-real-time
    "technical_signals": 2 * 3600,  # 2 hours
    "fundamentals": 24 * 3600,  # 24 hours — quarterly data
    "news": 30 * 60,            # 30 minutes — freshness matters
    "social_media": 1 * 3600,   # 1 hour
    "macro": 24 * 3600,         # 24 hours — CBE decisions are infrequent
    "regime": 24 * 3600,        # 24 hours — regime shifts daily at most
    "foreign_flow": 24 * 3600,  # 24 hours — FPI data published daily
    "default": 1 * 3600,        # 1 hour fallback
}


class CacheManager:
    """
    Unified caching layer.
    
    Uses DiskCache (SQLite) by default. Interface is designed so Redis
    can be swapped in by creating a RedisManager with the same methods.
    
    Usage:
        cache = CacheManager("/path/to/cache/dir")
        
        # Direct get/set
        cache.set("COMI:ohlcv:2026-04-10", data, ttl=14400)
        result = cache.get("COMI:ohlcv:2026-04-10")
        
        # get_or_fetch pattern (preferred)
        result = cache.get_or_fetch(
            key="COMI:ohlcv:2026-04-10",
            fetch_fn=lambda: yfinance_provider.fetch("COMI.CA", ...),
            data_type="ohlcv"
        )
    """

    def __init__(self, cache_dir: str, size_limit: int = 500_000_000):
        """
        Args:
            cache_dir: Directory for the cache database
            size_limit: Max cache size in bytes (default 500MB)
        """
        self._enabled = DISKCACHE_AVAILABLE
        self._stats = {"hits": 0, "misses": 0, "errors": 0}

        if self._enabled:
            cache_path = Path(cache_dir) / "_diskcache"
            cache_path.mkdir(parents=True, exist_ok=True)
            self._cache = diskcache.Cache(
                str(cache_path),
                size_limit=size_limit,
                eviction_policy="least-recently-used",
            )
            logger.info("Cache initialized at %s (limit: %d MB)", cache_path, size_limit // 1_000_000)
        else:
            self._cache = None
            logger.info("Cache disabled (diskcache not installed)")

    def _get_ttl(self, data_type: str) -> int:
        """Get TTL for a data type."""
        return DEFAULT_TTL.get(data_type, DEFAULT_TTL["default"])

    def get(self, key: str) -> Optional[Any]:
        """
        Get a value from cache.
        
        Returns None on cache miss or if caching is disabled.
        """
        if not self._enabled:
            return None

        try:
            value = self._cache.get(key, default=None)
            if value is not None:
                self._stats["hits"] += 1
                logger.debug("Cache HIT: %s", key)
            else:
                self._stats["misses"] += 1
                logger.debug("Cache MISS: %s", key)
            return value
        except Exception as e:
            self._stats["errors"] += 1
            logger.warning("Cache get error for key '%s': %s", key, e)
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None, data_type: str = "default") -> bool:
        """
        Set a value in cache with TTL.
        
        Args:
            key: Cache key
            value: Value to store (must be picklable)
            ttl: TTL in seconds (overrides data_type default)
            data_type: Used to look up default TTL if ttl not provided
            
        Returns:
            True if stored successfully
        """
        if not self._enabled:
            return False

        if ttl is None:
            ttl = self._get_ttl(data_type)

        try:
            self._cache.set(key, value, expire=ttl)
            logger.debug("Cache SET: %s (TTL: %ds)", key, ttl)
            return True
        except Exception as e:
            self._stats["errors"] += 1
            logger.warning("Cache set error for key '%s': %s", key, e)
            return False

    def get_or_fetch(
        self,
        key: str,
        fetch_fn: Callable[[], Any],
        data_type: str = "default",
        ttl: Optional[int] = None,
    ) -> Any:
        """
        Get from cache or fetch from source. Primary access pattern.
        
        Args:
            key: Cache key
            fetch_fn: Callable that fetches from the actual data source
            data_type: Data type for TTL lookup
            ttl: Override TTL
            
        Returns:
            Cached or freshly fetched value
        """
        # Try cache first
        cached = self.get(key)
        if cached is not None:
            return cached

        # Cache miss — fetch from source
        start = time.time()
        result = fetch_fn()
        elapsed_ms = (time.time() - start) * 1000
        logger.info("Fetched %s in %.0fms (cache miss)", key, elapsed_ms)

        # Store in cache
        if result is not None:
            self.set(key, result, ttl=ttl, data_type=data_type)

        return result

    def invalidate(self, key: str) -> bool:
        """Remove a specific key from cache."""
        if not self._enabled:
            return False

        try:
            return self._cache.delete(key)
        except Exception as e:
            logger.warning("Cache invalidate error for '%s': %s", key, e)
            return False

    def clear(self) -> None:
        """Clear the entire cache."""
        if self._enabled:
            self._cache.clear()
            logger.info("Cache cleared")

    def stats(self) -> dict:
        """Return cache statistics."""
        result = {**self._stats}
        if self._enabled:
            result["size_bytes"] = self._cache.volume()
            result["total_keys"] = len(self._cache)
        result["enabled"] = self._enabled
        return result

    def close(self) -> None:
        """Close the cache connection."""
        if self._enabled and self._cache:
            self._cache.close()
