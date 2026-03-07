"""
Report caching for PRISM-INSIGHT.

TTL-based cache for analysis results to avoid redundant LLM calls
for frequently-analyzed stocks or repeated market analysis.
"""

import time
import logging
import threading
from typing import Any, Optional, Dict

logger = logging.getLogger(__name__)


class ReportCache:
    """
    Thread-safe TTL-based cache for analysis reports and data.

    Usage:
        cache = ReportCache(default_ttl=3600)  # 1 hour default

        # Cache a market analysis
        cache.set("market_analysis_20260307", analysis_text, ttl=1800)

        # Retrieve if still fresh
        result = cache.get("market_analysis_20260307")
        if result is not None:
            print("Cache hit!")

        # Invalidate specific entry
        cache.invalidate("market_analysis_20260307")

        # Get stats
        print(cache.stats())
    """

    def __init__(self, default_ttl: int = 3600, max_entries: int = 100):
        """
        Args:
            default_ttl: Default time-to-live in seconds.
            max_entries: Maximum number of cache entries before eviction.
        """
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        """
        Retrieve a cached value if it exists and hasn't expired.

        Args:
            key: Cache key.

        Returns:
            Cached value, or None if not found/expired.
        """
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None

            # Check expiration
            if time.time() > entry["expires_at"]:
                del self._cache[key]
                self._misses += 1
                logger.debug(f"Cache expired: {key}")
                return None

            self._hits += 1
            logger.debug(f"Cache hit: {key}")
            return entry["value"]

    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """
        Store a value in the cache.

        Args:
            key: Cache key.
            value: Value to cache.
            ttl: Time-to-live in seconds (uses default if not specified).
        """
        if ttl is None:
            ttl = self._default_ttl

        with self._lock:
            # Evict oldest entries if at capacity
            if len(self._cache) >= self._max_entries and key not in self._cache:
                self._evict_oldest()

            self._cache[key] = {
                "value": value,
                "created_at": time.time(),
                "expires_at": time.time() + ttl,
                "ttl": ttl,
            }
            logger.debug(f"Cache set: {key} (TTL: {ttl}s)")

    def invalidate(self, key: str) -> bool:
        """
        Remove a specific entry from the cache.

        Args:
            key: Cache key to invalidate.

        Returns:
            True if the key existed and was removed.
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                logger.debug(f"Cache invalidated: {key}")
                return True
            return False

    def invalidate_prefix(self, prefix: str) -> int:
        """
        Remove all entries matching a key prefix.

        Args:
            prefix: Key prefix to match.

        Returns:
            Number of entries removed.
        """
        with self._lock:
            keys_to_remove = [k for k in self._cache if k.startswith(prefix)]
            for key in keys_to_remove:
                del self._cache[key]
            if keys_to_remove:
                logger.debug(f"Cache invalidated {len(keys_to_remove)} entries with prefix: {prefix}")
            return len(keys_to_remove)

    def clear(self):
        """Remove all cache entries."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            logger.info(f"Cache cleared ({count} entries)")

    def _evict_oldest(self):
        """Evict the oldest entry (by creation time)."""
        if not self._cache:
            return
        oldest_key = min(self._cache, key=lambda k: self._cache[k]["created_at"])
        del self._cache[oldest_key]
        logger.debug(f"Cache evicted oldest: {oldest_key}")

    def stats(self) -> dict:
        """Get cache statistics."""
        with self._lock:
            total_requests = self._hits + self._misses
            hit_rate = (self._hits / total_requests * 100) if total_requests > 0 else 0
            return {
                "entries": len(self._cache),
                "max_entries": self._max_entries,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate_pct": round(hit_rate, 1),
                "default_ttl_s": self._default_ttl,
            }

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None


# Module-level singleton for shared caching
_global_cache: Optional[ReportCache] = None


def get_cache(default_ttl: int = 3600) -> ReportCache:
    """
    Get the global report cache singleton.

    Args:
        default_ttl: Default TTL for new cache (only used on first call).

    Returns:
        The global ReportCache instance.
    """
    global _global_cache
    if _global_cache is None:
        _global_cache = ReportCache(default_ttl=default_ttl)
    return _global_cache
