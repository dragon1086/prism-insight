"""
Tests for cores/report_cache.py caching functionality.
"""

import time
from cores.report_cache import ReportCache


class TestReportCache:
    """Tests for ReportCache"""

    def test_set_and_get(self):
        cache = ReportCache(default_ttl=60)
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_get_missing_key(self):
        cache = ReportCache()
        assert cache.get("nonexistent") is None

    def test_ttl_expiration(self):
        cache = ReportCache(default_ttl=60)
        cache.set("key1", "value1", ttl=1)  # 1 second TTL
        assert cache.get("key1") == "value1"
        time.sleep(1.1)
        assert cache.get("key1") is None

    def test_custom_ttl(self):
        cache = ReportCache(default_ttl=1)
        cache.set("short", "value", ttl=1)
        cache.set("long", "value", ttl=60)
        time.sleep(1.1)
        assert cache.get("short") is None
        assert cache.get("long") == "value"

    def test_invalidate(self):
        cache = ReportCache()
        cache.set("key1", "value1")
        assert cache.invalidate("key1") is True
        assert cache.get("key1") is None

    def test_invalidate_missing(self):
        cache = ReportCache()
        assert cache.invalidate("nonexistent") is False

    def test_invalidate_prefix(self):
        cache = ReportCache()
        cache.set("market_2026", "data1")
        cache.set("market_2025", "data2")
        cache.set("company_005930", "data3")
        count = cache.invalidate_prefix("market_")
        assert count == 2
        assert cache.get("market_2026") is None
        assert cache.get("company_005930") == "data3"

    def test_clear(self):
        cache = ReportCache()
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.clear()
        assert len(cache) == 0

    def test_max_entries_eviction(self):
        cache = ReportCache(max_entries=3)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")
        cache.set("key4", "value4")  # Should evict oldest
        assert len(cache) == 3
        assert cache.get("key1") is None  # Evicted
        assert cache.get("key4") == "value4"

    def test_contains(self):
        cache = ReportCache()
        cache.set("key1", "value1")
        assert "key1" in cache
        assert "key2" not in cache

    def test_stats(self):
        cache = ReportCache(default_ttl=60, max_entries=50)
        cache.set("key1", "value1")
        cache.get("key1")  # Hit
        cache.get("key2")  # Miss
        stats = cache.stats()
        assert stats["entries"] == 1
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate_pct"] == 50.0

    def test_overwrite_existing_key(self):
        cache = ReportCache()
        cache.set("key1", "old_value")
        cache.set("key1", "new_value")
        assert cache.get("key1") == "new_value"

    def test_complex_values(self):
        cache = ReportCache()
        data = {"report": "# Analysis\n\nContent here", "score": 85.5}
        cache.set("report_005930", data)
        result = cache.get("report_005930")
        assert result["score"] == 85.5
