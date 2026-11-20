"""Enhanced Logging Helpers - monitoring decorators and context managers that enrich application logs.

Logging helpers that time functions, database queries, external API calls and LLM calls, capture business context, cache hits and agent decisions, and feed the results into the request log context from database_handler, adding memory and CPU figures when psutil is installed.

Features:
    - Performance Decorators: Automatic timing and metric collection for functions
    - Database Monitoring: Automatic tracking of database query performance
    - API Call Tracking: External API call duration and success rate monitoring
    - LLM Performance: AI model call timing and token usage tracking
    - Business Logic Capture: Automatic business context extraction and logging
    - Agent Interaction Logging: LangGraph tool calls and agent decision tracking
    - Cache Performance: Cache hit/miss ratio and lookup time monitoring
    - Memory Monitoring: Peak memory usage tracking during operations
    - Context Managers: Automatic context setup and cleanup
    - Error Enhancement: Automatic error context enrichment

Classes:
    PerformanceMetrics: Dataclass container for one call's duration/memory/cpu/success measurement

Functions:
    performance_monitor(): Decorator for automatic function performance tracking (timing, memory, CPU)
    _execute_with_monitoring(): Shared timed-execution path used by the performance_monitor() decorator
    db_query_monitor(): Decorator for database query performance monitoring
    api_call_monitor(): Decorator for external API call monitoring
    llm_call_monitor(): Decorator for LLM/AI model call monitoring
    business_operation(): Decorator for business logic context capture
    _sanitize_for_logging(): Redact sensitive keys and truncate long values before a value is logged
    cache_monitor(): Async context manager yielding a cache_hit()/cache_miss() tracker
    log_agent_decision(): Log agent reasoning and decision trails
    enrich_error_context(): Build request/performance context for the caller to attach to an error record

Dependencies:
    - time: High-resolution timing for performance measurement
    - functools: Function decoration and wrapping utilities
    - psutil: Optional system resource monitoring (memory, CPU usage); disabled gracefully if unavailable
    - asyncio: Asynchronous context management
    - typing: Type hints for better code documentation
    - app.logs.database_handler: Business context and performance metric setters

Updates:
    13-Mar-2026: Initial creation with performance monitoring decorators
    14-Mar-2026: Moved from app.core.enhanced_logging to app.logs.enhanced_logging for better organization
    26-Aug-2026: enrich_error_context returns context instead of emitting a rival ERROR record per 500.
"""

import asyncio
import functools
import time
import traceback
import logging
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, Callable, Union, TypeVar, Awaitable
from dataclasses import dataclass

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    logging.getLogger(__name__).warning("psutil is not installed; memory metrics will be omitted from performance logs")
    PSUTIL_AVAILABLE = False

from app.logs.database_handler import (
    update_business_context, 
    update_performance_metrics, 
    increment_performance_counter,
    add_to_time_metric,
    get_log_context
)

F = TypeVar('F', bound=Callable[..., Any])
AF = TypeVar('AF', bound=Callable[..., Awaitable[Any]])

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    """Container for performance measurement data.
    
    Attributes:
        duration_ms: Operation duration in milliseconds.
        memory_peak_mb: Peak memory usage in MB (if available).
        cpu_percent: CPU usage percentage (if available).
        success: Whether the operation completed successfully.
        error_type: Type of error if operation failed.
    """
    duration_ms: float
    memory_peak_mb: Optional[float] = None
    cpu_percent: Optional[float] = None
    success: bool = True
    error_type: Optional[str] = None


def performance_monitor(
    metric_prefix: str = "",
    track_memory: bool = False,
    track_cpu: bool = False,
    log_errors: bool = True
):
    """Return a decorator that times a sync or async function and records its metrics in the log context.

    Args:
        metric_prefix: Prefix for metric names in the context, such as 'db_'.
        track_memory: Whether to measure memory usage during execution.
        track_cpu: Whether to measure CPU usage during execution.
        log_errors: Whether to log failures with timing context.

    Returns:
        The decorator.
    """
    def decorator(func: Union[F, AF]) -> Union[F, AF]:
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                return await _execute_with_monitoring(
                    func, args, kwargs, metric_prefix, track_memory, track_cpu, log_errors
                )
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                return asyncio.run(_execute_with_monitoring(
                    func, args, kwargs, metric_prefix, track_memory, track_cpu, log_errors
                ))
            return sync_wrapper
    return decorator


async def _execute_with_monitoring(
    func: Callable,
    args: tuple,
    kwargs: dict,
    metric_prefix: str,
    track_memory: bool,
    track_cpu: bool,
    log_errors: bool
) -> Any:
    """Run func, record its duration and optional memory/CPU metrics, and log then re-raise any exception.

    Args:
        func: Function to execute and monitor.
        args: Positional arguments for func.
        kwargs: Keyword arguments for func.
        metric_prefix: Metric name prefix; duration goes to db_query_time_ms when empty.
        track_memory: Whether to track memory usage.
        track_cpu: Whether to track CPU usage.
        log_errors: Whether to log errors.

    Returns:
        The function's result.
    """
    start_time = time.perf_counter()
    start_memory = None
    start_cpu = None
    
    if PSUTIL_AVAILABLE and (track_memory or track_cpu):
        process = psutil.Process()
        if track_memory:
            start_memory = process.memory_info().rss / 1024 / 1024
        if track_cpu:
            start_cpu = process.cpu_percent()
    
    try:
        if asyncio.iscoroutinefunction(func):
            result = await func(*args, **kwargs)
        else:
            result = func(*args, **kwargs)
        
        duration_ms = (time.perf_counter() - start_time) * 1000
        
        memory_peak_mb = None
        cpu_percent = None
        
        if PSUTIL_AVAILABLE:
            process = psutil.Process()
            if track_memory and start_memory is not None:
                current_memory = process.memory_info().rss / 1024 / 1024
                memory_peak_mb = max(start_memory, current_memory)
            if track_cpu and start_cpu is not None:
                cpu_percent = process.cpu_percent() - start_cpu
        
        metrics = {}
        if metric_prefix:
            metrics[f'{metric_prefix}time_ms'] = duration_ms
            if memory_peak_mb is not None:
                metrics[f'{metric_prefix}memory_mb'] = memory_peak_mb
            if cpu_percent is not None:
                metrics[f'{metric_prefix}cpu_percent'] = cpu_percent
        else:
            add_to_time_metric('db_query_time_ms', duration_ms)
        
        if metrics:
            allowed_keys = {
                'db_query_count', 'db_query_time_ms', 'external_api_calls', 'external_api_time_ms',
                'llm_calls', 'llm_time_ms', 'cache_hits', 'cache_misses', 'background_tasks', 'memory_peak_mb'
            }
            filtered_metrics = {k: v for k, v in metrics.items() if k in allowed_keys}
            if filtered_metrics:
                update_performance_metrics(**filtered_metrics)
        
        return result
        
    except Exception as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        
        if log_errors:
            error_context = {
                'error_type': type(e).__name__,
                'error_message': str(e),
                'duration_ms': duration_ms,
                'function_name': func.__name__
            }
            
            if PSUTIL_AVAILABLE and track_memory and start_memory is not None:
                process = psutil.Process()
                error_context['memory_mb'] = process.memory_info().rss / 1024 / 1024
            
            logger.error(
                f"Function {func.__name__} failed after {duration_ms:.2f}ms",
                extra={'extra_data': {'error_context': error_context}},
                exc_info=True
            )
        
        raise


def db_query_monitor(query_type: str = "unknown"):
    """Return a decorator that counts and times an async DB call and records its query type and result count."""
    def decorator(func: Union[F, AF]) -> Union[F, AF]:
        @performance_monitor('db_', track_memory=True)
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            increment_performance_counter('db_query_count')
            
            update_business_context(
                request_payload={'query_type': query_type, 'function': func.__name__}
            )
            
            try:
                result = await func(*args, **kwargs)
                
                if hasattr(result, '__len__'):
                    result_count = len(result) if result else 0
                    update_business_context(
                        response_payload={'result_count': result_count, 'query_type': query_type}
                    )
                
                return result
                
            except Exception as e:
                update_business_context(
                    response_payload={'error': str(e), 'query_type': query_type}
                )
                raise
                
        return wrapper
    return decorator


def api_call_monitor(service_name: str, endpoint: str = ""):
    """Return a decorator that counts and times an async external API call and records its service and outcome."""
    def decorator(func: Union[F, AF]) -> Union[F, AF]:
        @performance_monitor('external_api_', track_memory=False)
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            increment_performance_counter('external_api_calls')
            
            update_business_context(
                request_payload={
                    'service': service_name, 
                    'endpoint': endpoint, 
                    'function': func.__name__
                }
            )
            
            try:
                result = await func(*args, **kwargs)
                
                update_business_context(
                    response_payload={
                        'success': True, 
                        'service': service_name,
                        'endpoint': endpoint
                    }
                )
                
                return result
                
            except Exception as e:
                update_business_context(
                    response_payload={
                        'success': False,
                        'error': str(e),
                        'service': service_name,
                        'endpoint': endpoint
                    }
                )
                raise
                
        return wrapper
    return decorator


def llm_call_monitor(model: str = "unknown", operation: str = ""):
    """Return a decorator that counts and times an async LLM call and records its model, outcome and token usage."""
    def decorator(func: Union[F, AF]) -> Union[F, AF]:
        @performance_monitor('llm_', track_memory=True)
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            increment_performance_counter('llm_calls')
            
            update_business_context(
                request_payload={
                    'model': model,
                    'operation': operation,
                    'function': func.__name__
                }
            )
            
            try:
                result = await func(*args, **kwargs)
                
                response_data = {'model': model, 'operation': operation, 'success': True}
                
                if hasattr(result, 'usage'):
                    response_data['tokens_used'] = getattr(result.usage, 'total_tokens', None)
                elif isinstance(result, dict) and 'usage' in result:
                    response_data['tokens_used'] = result['usage'].get('total_tokens')
                
                update_business_context(response_payload=response_data)
                
                return result
                
            except Exception as e:
                update_business_context(
                    response_payload={
                        'success': False,
                        'error': str(e),
                        'model': model,
                        'operation': operation
                    }
                )
                raise
                
        return wrapper
    return decorator


def business_operation(operation_type: str, capture_args: bool = False, capture_result: bool = False):
    """Return a decorator that records an async operation's type, outcome and optionally sanitised args and result."""
    def decorator(func: Union[F, AF]) -> Union[F, AF]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            context_data = {
                'operation_type': operation_type,
                'function': func.__name__
            }
            
            if capture_args:
                safe_args = _sanitize_for_logging(args)
                safe_kwargs = _sanitize_for_logging(kwargs)
                context_data['args'] = safe_args
                context_data['kwargs'] = safe_kwargs
            
            update_business_context(request_payload=context_data)
            
            try:
                result = await func(*args, **kwargs)
                
                if capture_result:
                    safe_result = _sanitize_for_logging(result)
                    update_business_context(
                        response_payload={
                            'operation_type': operation_type,
                            'success': True,
                            'result': safe_result
                        }
                    )
                else:
                    update_business_context(
                        response_payload={'operation_type': operation_type, 'success': True}
                    )
                
                return result
                
            except Exception as e:
                update_business_context(
                    response_payload={
                        'operation_type': operation_type,
                        'success': False,
                        'error': str(e)
                    }
                )
                raise
                
        return wrapper
    return decorator


def _sanitize_for_logging(data: Any, max_length: int = 500) -> Any:
    """Return data with sensitive keys redacted, lists capped at 10 items and strings truncated to max_length."""
    if isinstance(data, dict):
        sensitive_keys = {'password', 'token', 'secret', 'api_key', 'authorization'}
        sanitized = {}
        for k, v in data.items():
            if isinstance(k, str) and any(key in k.lower() for key in sensitive_keys):
                sanitized[k] = '[REDACTED]'
            else:
                sanitized[k] = _sanitize_for_logging(v, max_length)
        return sanitized
    elif isinstance(data, (list, tuple)):
        return [_sanitize_for_logging(item, max_length) for item in data[:10]]
    elif isinstance(data, str):
        return data[:max_length] + '...' if len(data) > max_length else data
    else:
        return data


@asynccontextmanager
async def cache_monitor(cache_name: str = "default"):
    """Yield a hit/miss counter for a cache and record its hit ratio and duration on exit.

    Args:
        cache_name: Name of the cache being monitored.

    Yields:
        An object with cache_hit() and cache_miss() methods.
    """
    start_time = time.perf_counter()
    hits = 0
    misses = 0
    
    class CacheMonitorContext:
        def cache_hit(self):
            nonlocal hits
            hits += 1
            increment_performance_counter('cache_hits')
        
        def cache_miss(self):
            nonlocal misses
            misses += 1
            increment_performance_counter('cache_misses')
    
    context = CacheMonitorContext()
    
    try:
        yield context
    finally:
        duration_ms = (time.perf_counter() - start_time) * 1000
        
        hit_ratio = hits / (hits + misses) if (hits + misses) > 0 else 0
        
        update_business_context(
            response_payload={
                'cache_name': cache_name,
                'cache_hits': hits,
                'cache_misses': misses,
                'hit_ratio': hit_ratio,
                'duration_ms': duration_ms
            }
        )


def log_agent_decision(agent_type: str, operation: str, decision_data: Dict[str, Any]):
    """Store an agent decision in the business log context and log it at INFO."""
    agent_context = {
        'agent_type': agent_type,
        'operation': operation,
        'timestamp': time.time(),
        **decision_data
    }
    
    update_business_context(agent_context=agent_context)
    
    logger.info(
        f"Agent decision: {agent_type} performed {operation}",
        extra={'extra_data': {'agent_interaction': agent_context}}
    )


def enrich_error_context(error: Exception, additional_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return an error's type, message, request identity and performance counters, merged with additional_context."""
    current_context = get_log_context()

    error_context = {
        'error_type': type(error).__name__,
        'error_message': str(error),
        'request_id': current_context.request_id,
        'endpoint': current_context.endpoint,
        'user_id': current_context.userprofile_id,
        'performance_metrics': {
            'db_queries': current_context.db_query_count,
            'db_time_ms': current_context.db_query_time_ms,
            'api_calls': current_context.external_api_calls,
            'api_time_ms': current_context.external_api_time_ms,
            'llm_calls': current_context.llm_calls,
            'llm_time_ms': current_context.llm_time_ms,
        }
    }

    if additional_context:
        error_context.update(additional_context)

    return error_context
