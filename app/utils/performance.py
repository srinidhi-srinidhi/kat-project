"""Performance Helpers - request-scoped timing, cache tracking and slow-operation logging.

Utility module that times operations with PerformanceTimer and the time_async decorator, tracks cache hits and misses, and aggregates database, external API and custom timings in a per-request contextvar for logging and response metrics.

Features:
    - Function timing: Decorator to measure execution time
    - Performance logging: Detailed timing metrics logging
    - Benchmark data: Collection of performance metrics
    - Warning thresholds: Alerting for slow operations
    - Database timing: Track database query performance
    - Granular timing: Database and external API operation breakdown
    - Cache tracking: Consistent cache hit/miss detection
    - Performance collection: Middleware-ready metrics aggregation
    - Request context: Per-request performance state management

Classes:
    PerformanceTimer: Context manager for timing operations
    CacheTracker: Cache hit/miss detection utilities
    PerformanceCollector: Aggregate metrics collection for responses

Functions:
    time_async(): Decorator for timing async functions
    log_performance(): Log performance metrics with context
    get_timing_summary(): Generate performance summary report
    get_request_performance(): Retrieve current request performance state
    clear_request_performance(): Reset the request performance context and cache-status tracking
    track_performance_metrics(): Store metrics in request context
    record_custom_timing(): Write named timing values into the request context

Dependencies:
    - time: High-resolution timing measurements
    - functools: Function decoration utilities
    - contextvars: Request-scoped variables
    - datetime: Timestamp generation
    - app.core.logging: Performance metrics logging
    - app.core.cache: cache manager and cache-status reset (lazy)

Updates:
    24-Feb-2026: Initial creation with async function timing
    27-Feb-2026: Added granular timing, cache tracking, performance collection
    05-Jun-2026: Added record_custom_timing() for named per-request timings.
    26-Aug-2026: Logged previously-silent exception handlers with tracebacks.

Notes:
    Designed for production use with configurable logging levels.
    Automatically warns on operations exceeding performance thresholds.
    Provides middleware-ready performance metrics collection.
"""

import time
import functools
from typing import Dict, Any, Optional, Callable, Union
from datetime import datetime, timezone
from contextvars import ContextVar
from app.core.logging import logger


_performance_context: ContextVar[Dict[str, Any]] = ContextVar('performance_context', default={})


class PerformanceTimer:
    """Enhanced context manager for measuring operation timing with granular categories.
    
    Args:
        operation_name: Name of the operation being measured.
        category: Performance category ('database', 'external_api', 'general'). Defaults to 'general'.
        warn_threshold_ms: Warning threshold in milliseconds. Defaults to 1000.
        track_in_context: Whether to store timing in request context. Defaults to True.
    
    Example:
        async with PerformanceTimer("database_query", category="database") as timer:
            results = await database.fetch(query)
        print(f"Query took {timer.duration_ms:.1f}ms")
    """
    
    def __init__(self, operation_name: str, category: str = "general", 
                 warn_threshold_ms: float = 1000, track_in_context: bool = True):
        """Store the operation name, category, slow-warning threshold and context-tracking flag."""
        self.operation_name = operation_name
        self.category = category
        self.warn_threshold_ms = warn_threshold_ms
        self.track_in_context = track_in_context
        self.start_time: float = 0
        self.end_time: float = 0
        self.duration_ms: float = 0
    
    async def __aenter__(self):
        """Record the start time and return the timer."""
        self.start_time = time.time()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Compute the duration, log it (warning when slow) and append it to the request context."""
        self.end_time = time.time()
        self.duration_ms = (self.end_time - self.start_time) * 1000
        
        if self.duration_ms > self.warn_threshold_ms:
            logger.warning(f"[PERFORMANCE] Slow {self.operation_name}: {self.duration_ms:.1f}ms")
        else:
            logger.info(f"[PERFORMANCE] {self.operation_name}: {self.duration_ms:.1f}ms")
        
        if self.track_in_context:
            try:
                context = _performance_context.get({})
                if self.category not in context:
                    context[self.category] = []
                context[self.category].append({
                    'operation': self.operation_name,
                    'duration_ms': self.duration_ms
                })
                _performance_context.set(context)
            except Exception as e:
                logger.debug(f"Failed to store performance context: {e}")


class CacheTracker:
    """Utility class for consistent cache hit/miss tracking.
    
    Example:
        cache_tracker = CacheTracker()
        result = cache_tracker.check_and_track(cache_key, fetch_function)
        print(f"Cache hit: {cache_tracker.was_hit}")
    """
    
    def __init__(self):
        """Start with no hit recorded and an empty cache key."""
        self.was_hit: bool = False
        self.cache_key: str = ""
    
    def track_hit(self, cache_key: str = ""):
        """Record a cache hit for the given key."""
        self.was_hit = True
        self.cache_key = cache_key
        logger.debug(f"[CACHE] Hit: {cache_key}")
    
    def track_miss(self, cache_key: str = ""):
        """Record a cache miss for the given key."""
        self.was_hit = False
        self.cache_key = cache_key
        logger.debug(f"[CACHE] Miss: {cache_key}")
    
    def get_status(self) -> bool:
        """Return whether the last tracked lookup was a hit."""
        return self.was_hit


class PerformanceCollector:
    """Collect and aggregate performance metrics for API responses.
    
    Example:
        collector = PerformanceCollector("search_endpoint")
        collector.start_timing()
        # ... perform operations ...
        metrics = collector.get_metrics()
    """
    
    def __init__(self, endpoint_name: str):
        """Initialise zeroed timings and a cache tracker for the named endpoint."""
        self.endpoint_name = endpoint_name
        self.start_time: float = 0
        self.database_time_ms: float = 0
        self.external_api_time_ms: float = 0
        self.cache_tracker = CacheTracker()
        self.timestamp: str = ""
    
    def start_timing(self):
        """Record the request start time and UTC timestamp."""
        self.start_time = time.time()
        self.timestamp = datetime.now(timezone.utc).isoformat()
    
    def collect_context_timings(self):
        """Set database time to the slowest database operation and external API time to the sum of API calls."""
        try:
            context = _performance_context.get({})
            
            if 'database' in context:
                operations = context['database']
                if operations:
                    self.database_time_ms = max(op['duration_ms'] for op in operations)
            
            if 'external_api' in context:
                self.external_api_time_ms = sum(op['duration_ms'] for op in context['external_api'])
                
        except Exception as e:
            logger.debug(f"Failed to collect context timings: {e}")
    
    def get_metrics(self) -> Dict[str, Union[float, bool, str]]:
        """Return response, database and API timings, cache hit, timestamp and any custom timings for the request."""
        self.collect_context_timings()
        
        cache_hit = False
        try:
            context = _performance_context.get({})
            cache_hit = context.get('cache_hit', False)
            
            if not cache_hit:
                from app.core.cache import get_cache_manager
                cache_manager = get_cache_manager()
                cache_hit = cache_manager.get_overall_status()
        except ImportError:
            logger.debug("performance: cache manager unavailable; cache_hit not recorded")
        
        total_time = (time.time() - self.start_time) if self.start_time > 0 else 0

        metrics: Dict[str, Any] = {
            'response_time_seconds': round(total_time, 3),
            'database_time_ms': round(self.database_time_ms, 1),
            'external_api_time_ms': round(self.external_api_time_ms, 1),
            'cache_hit': cache_hit,
            'timestamp': self.timestamp,
            'endpoint_name': self.endpoint_name
        }

        try:
            context = _performance_context.get({})
            for key, value in context.get('custom', {}).items():
                metrics[key] = value
        except Exception:
            logger.debug("performance: could not merge the custom performance context", exc_info=True)

        return metrics

def record_custom_timing(key: str, value_ms: float) -> None:
    """Store a named millisecond timing in the request context so get_metrics reports it as a top-level key."""
    try:
        context = _performance_context.get({})
        custom = context.setdefault('custom', {})
        custom[key] = round(value_ms, 1)
        _performance_context.set(context)
    except Exception as e:
        logger.debug("Failed to record custom timing %s: %s", key, e)


def time_async(operation_name: Optional[str] = None, warn_threshold_ms: float = 1000):
    """Return a decorator that logs an async function's duration, warning above warn_threshold_ms."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            op_name = operation_name or f"{func.__module__}.{func.__name__}"
            
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                duration_ms = (time.time() - start_time) * 1000
                
                if duration_ms > warn_threshold_ms:
                    logger.warning(f"[PERF] Slow {op_name}: {duration_ms:.1f}ms")
                else:
                    logger.info(f"[PERF] {op_name}: {duration_ms:.1f}ms")
        
        return async_wrapper
    return decorator


def log_performance(operation: str, duration_ms: float, context: Optional[Dict[str, Any]] = None):
    """Log an operation's duration with optional context, warning above 1000 ms and flagging SLOW above 2000 ms."""
    context_str = f" | {context}" if context else ""
    
    if duration_ms > 2000:
        logger.warning(f"[PERF] SLOW {operation}: {duration_ms:.1f}ms{context_str}")
    elif duration_ms > 1000:
        logger.warning(f"[PERF] {operation}: {duration_ms:.1f}ms{context_str}")
    else:
        logger.info(f"[PERF] {operation}: {duration_ms:.1f}ms{context_str}")


def get_timing_summary(timings: Dict[str, float]) -> str:
    """Return a multi-line report of timings sorted slowest first, with each one's share of the total."""
    total = sum(timings.values())
    lines = [f"Performance Summary (Total: {total:.1f}ms):"]
    
    for operation, duration in sorted(timings.items(), key=lambda x: x[1], reverse=True):
        percentage = (duration / total * 100) if total > 0 else 0
        lines.append(f"  {operation}: {duration:.1f}ms ({percentage:.1f}%)")
    
    return "\n".join(lines)


def get_request_performance() -> Dict[str, Any]:
    """Return the current request's performance context dict."""
    return _performance_context.get({})


def clear_request_performance():
    """Reset the request's performance context and clear the cache status."""
    _performance_context.set({})
    
    try:
        from app.core.cache import clear_cache_status
        clear_cache_status()
    except ImportError:
        logger.debug("performance: cache status could not be cleared")


def track_performance_metrics(category: str, operation: str, duration_ms: float):
    """Append an operation's duration under a category in the request performance context."""
    try:
        context = _performance_context.get({})
        if category not in context:
            context[category] = []
        context[category].append({
            'operation': operation,
            'duration_ms': duration_ms
        })
        _performance_context.set(context)
    except Exception as e:
        logger.debug(f"Failed to track performance metrics: {e}")
