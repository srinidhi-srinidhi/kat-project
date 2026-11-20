"""Cache Helpers - tiered in-process async LRU caching decorators with hit and miss statistics.

Core helper module: cache_short, cache_medium and cache_long wrap alru_cache with 5-minute, 30-minute and 2-hour TTL presets, and a shared CacheManager reports hit rate and uptime through get_cache_stats().

Features:
    - Async LRU Caching: cache_short/cache_medium/cache_long wrap alru_cache with preset tiers
    - Tiered TTLs: 5-minute, 30-minute and 2-hour presets for volatile, semi-static and static data
    - Cache Statistics: CacheManager tracks reported hits/misses, hit-rate and uptime
    - Global Stats Access: get_cache_stats() exposes the shared cache_manager's current statistics

Classes:
    CacheManager(): Hit/miss counts, hit-rate and uptime; callers report via record_hit()/record_miss()

Functions:
    get_cache_stats(): Return the shared cache_manager's current hit/miss statistics
    cache_short(): alru_cache decorator with a 5-minute TTL, for frequently changing data
    cache_medium(): alru_cache decorator with a 30-minute TTL, for semi-static data
    cache_long(): alru_cache decorator with a 2-hour TTL, for static data

Dependencies:
    - async_lru: alru_cache — the underlying async LRU+TTL cache implementation backing every tier
    - time: Uptime tracking for CacheManager's statistics

Updates:
    10-Mar-2026: Initial creation for performance optimization

Notes:
    Cache sizes and TTLs are tuned for typical search workloads.
    Monitor cache hit rates and adjust sizes as needed for optimal performance.
    Cache statistics are automatically reset on application restart.
"""

import time
import logging
from typing import Dict, Any, Optional
from functools import wraps
from async_lru import alru_cache

logger = logging.getLogger(__name__)


class CacheManager:
    """Centralized cache management with statistics tracking."""
    
    def __init__(self):
        """Zero the hit, miss and request counters and start the uptime clock."""
        self._stats = {
            "hits": 0,
            "misses": 0,
            "total_requests": 0
        }
        self._start_time = time.time()
    
    def record_hit(self):
        """Count one cache hit and one request."""
        self._stats["hits"] += 1
        self._stats["total_requests"] += 1
    
    def record_miss(self):
        """Count one cache miss and one request."""
        self._stats["misses"] += 1
        self._stats["total_requests"] += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """Return hits, misses, total requests, hit-rate percent and uptime seconds."""
        total = self._stats["total_requests"]
        hit_rate = (self._stats["hits"] / total * 100) if total > 0 else 0
        uptime = time.time() - self._start_time
        
        return {
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "total_requests": total,
            "hit_rate_percent": round(hit_rate, 2),
            "uptime_seconds": round(uptime, 2)
        }
    
    def reset_stats(self):
        """Zero the counters and restart the uptime clock."""
        self._stats = {
            "hits": 0,
            "misses": 0,
            "total_requests": 0
        }
        self._start_time = time.time()


cache_manager = CacheManager()


def get_cache_stats() -> Dict[str, Any]:
    """Return the global CacheManager's statistics."""
    return cache_manager.get_stats()


def cache_short(maxsize=500, ttl=300):
    """Return an async LRU cache decorator with a 5-minute default TTL."""
    return alru_cache(maxsize=maxsize, ttl=ttl)


def cache_medium(maxsize=1000, ttl=1800):
    """Return an async LRU cache decorator with a 30-minute default TTL."""
    return alru_cache(maxsize=maxsize, ttl=ttl)


def cache_long(maxsize=2000, ttl=7200):
    """Return an async LRU cache decorator with a 2-hour default TTL."""
    return alru_cache(maxsize=maxsize, ttl=ttl)
