"""Database Log Handler - queued logging handler that batches records into etown_application_logs.

Logging layer: DatabaseLogHandler queues each record in memory with request, business and performance context from contextvars, and a background thread writes batches to etown_application_logs, with an optional console fallback when the database write fails.

Features:
    - Memory Queue: Ultra-fast log emission using thread-safe deque (< 1ms)
    - Background Worker: Non-blocking database operations in separate thread
    - Context Enrichment: Automatic request context capture from middleware
    - Enhanced Context: Business domain, performance metrics, and agent interaction data
    - Business Context: Provider, service, search, location, and agent operation tracking
    - Performance Metrics: Database queries, API calls, LLM usage, cache performance tracking
    - Incremental Counters: Automatic accumulation of metrics during request processing  
    - Graceful Fallback: Continues working when database is unavailable
    - Thread Safety: Safe concurrent access in async FastAPI environment
    - Context Variables: Uses contextvars for request-scoped data
    - Performance Optimized: Zero-latency logging with background processing
    - Error Resilience: Robust error handling with fallback mechanisms
    - Configurable: Adjustable queue size and flush intervals

Classes:
    EnhancedJSONEncoder: JSON encoder for LangChain messages, dataclasses and other non-native objects
    QueuedLogEntry: Fast log entry for memory queue storage
    LogContext: Enhanced context variables for request-scoped log data
    DatabaseLogHandler: Custom logging handler with memory queue

Functions:
    set_log_context(): Set request context for current async task
    get_log_context(): Get current request context data
    clear_log_context(): Clear request context data
    update_business_context(): Update business domain context data
    update_performance_metrics(): Update performance metrics context data
    increment_performance_counter(): Increment performance counters automatically
    add_to_time_metric(): Add time durations to time-based metrics
    create_database_handler(): Factory function for handler creation

Dependencies:
    - logging: Python standard logging framework
    - threading: Background worker thread management  
    - collections.deque: High-performance queue data structure
    - contextvars: Context variables for async tasks
    - datetime: Timestamp handling and timezone support
    - traceback: Stack trace formatting for errors
    - asyncpg: Direct connection used by the background worker to insert log rows
    - json / zoneinfo / dataclasses: Serialization, IST timestamps and log entry records
    - app.core.config: Application settings and configuration

Updates:
    12-Mar-2026: Initial creation with async database logging; memory queue with background worker.
    13-Mar-2026: Fixed primary key structure; added business, performance and agent context.
    14-Mar-2026: Moved from app.core.database_log_handler to app.logs.database_handler.
    19-Mar-2026: Quieter error handling for transient DNS failures during startup.
    26-Aug-2026: Removed disable-on-failure latch; full-queue drain; safe overflow; reused connection.
"""

import logging
import threading
import traceback
import json
from collections import deque
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any, Deque
from dataclasses import dataclass, asdict

from app.core.config import settings


class EnhancedJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder for enhanced logging data that handles non-serializable objects."""
    
    def default(self, obj):
        """Serialize messages, datetimes, dataclasses, dict()-capable and plain objects, falling back to str."""
        if hasattr(obj, '__class__') and 'Message' in obj.__class__.__name__:
            return {
                'type': obj.__class__.__name__,
                'content': getattr(obj, 'content', str(obj)),
                'additional_kwargs': getattr(obj, 'additional_kwargs', {})
            }
        
        if isinstance(obj, (datetime, timezone)):
            return obj.isoformat()
        
        if hasattr(obj, '__dataclass_fields__'):
            return asdict(obj)
        
        if hasattr(obj, 'dict') and callable(obj.dict):
            try:
                return obj.dict()
            except Exception:
                _handler_logger.debug("log handler: extra_data object dict() call failed; falling back", exc_info=True)
        
        if hasattr(obj, '__dict__'):
            return {
                'type': obj.__class__.__name__,
                'data': {k: v for k, v in obj.__dict__.items() if not k.startswith('_')}
            }
        
        return str(obj)

_handler_logger = logging.getLogger("app.logs.database_handler")


@dataclass
class QueuedLogEntry:
    """Simple log entry for memory queue storage.
    
    Minimal data structure optimized for fast memory operations
    and efficient serialization.
    """
    level: str
    message: str
    timestamp: float
    module: str
    function: str
    lineno: int
    context: 'LogContext'
    stack_trace: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None


@dataclass
class LogContext:
    """Context variables for request-scoped log data.
    
    Basic HTTP/User Context:
        request_id: Unique identifier for request tracing.
        endpoint: API endpoint being accessed.
        method: HTTP method (GET, POST, etc.).
        userprofile_id: User profile identifier if authenticated.
        contact_id: Contact identifier if available.
        ip_address: Client IP address.
        user_agent: Client user agent string.
        status_code: HTTP response status code.
        response_time_ms: Response time in milliseconds.
        
    Business Domain Context:
        provider_id: Active provider identifier for provider operations.
        service_id: Service/booking identifier for service operations.
        search_query: Current search terms or filters being applied.
        location_context: Geographic context (city, coordinates, etc.).
        agent_context: Active agent type and current operation context.
        request_payload: Important request data for business operations.
        response_payload: Key response data for business operations.
        
    Performance Metrics Context:
        db_query_count: Number of database queries executed.
        db_query_time_ms: Total database query execution time.
        external_api_calls: Count of external API calls made.
        external_api_time_ms: Total external API call duration.
        llm_calls: Count of LLM/AI model calls made.
        llm_time_ms: Total LLM response time.
        cache_hits: Number of cache hits during request.
        cache_misses: Number of cache misses during request.
        background_tasks: Count of background tasks created.
        memory_peak_mb: Peak memory usage during request processing.
    """
    request_id: Optional[str] = None
    endpoint: Optional[str] = None
    method: Optional[str] = None
    userprofile_id: Optional[str] = None
    contact_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    status_code: Optional[int] = None
    response_time_ms: Optional[int] = None
    
    provider_id: Optional[str] = None
    service_id: Optional[str] = None
    search_query: Optional[str] = None
    location_context: Optional[Dict[str, Any]] = None
    agent_context: Optional[Dict[str, Any]] = None
    request_payload: Optional[Dict[str, Any]] = None
    response_payload: Optional[Dict[str, Any]] = None
    
    db_query_count: Optional[int] = None
    db_query_time_ms: Optional[float] = None
    external_api_calls: Optional[int] = None
    external_api_time_ms: Optional[float] = None
    llm_calls: Optional[int] = None
    llm_time_ms: Optional[float] = None
    cache_hits: Optional[int] = None
    cache_misses: Optional[int] = None
    background_tasks: Optional[int] = None
    memory_peak_mb: Optional[float] = None


_log_context: ContextVar[LogContext] = ContextVar('log_context', default=LogContext())


def set_log_context(**kwargs):
    """Merge the given non-None LogContext fields into the current task's log context."""
    current_context = _log_context.get()
    
    context_dict = asdict(current_context)
    context_dict.update({k: v for k, v in kwargs.items() if v is not None})
    
    new_context = LogContext(**context_dict)
    _log_context.set(new_context)


def get_log_context() -> LogContext:
    """Return the current task's log context."""
    return _log_context.get()


def clear_log_context():
    """Reset the current task's log context to an empty LogContext."""
    _log_context.set(LogContext())


def update_business_context(
    provider_id: Optional[str] = None,
    service_id: Optional[str] = None,
    search_query: Optional[str] = None,
    location_context: Optional[Dict[str, Any]] = None,
    agent_context: Optional[Dict[str, Any]] = None,
    request_payload: Optional[Dict[str, Any]] = None,
    response_payload: Optional[Dict[str, Any]] = None
):
    """Merge the given non-None business fields into the current task's log context."""
    business_data = {k: v for k, v in {
        'provider_id': provider_id,
        'service_id': service_id,
        'search_query': search_query,
        'location_context': location_context,
        'agent_context': agent_context,
        'request_payload': request_payload,
        'response_payload': response_payload
    }.items() if v is not None}
    
    if business_data:
        set_log_context(**business_data)


def update_performance_metrics(
    db_query_count: Optional[int] = None,
    db_query_time_ms: Optional[float] = None,
    external_api_calls: Optional[int] = None,
    external_api_time_ms: Optional[float] = None,
    llm_calls: Optional[int] = None,
    llm_time_ms: Optional[float] = None,
    cache_hits: Optional[int] = None,
    cache_misses: Optional[int] = None,
    background_tasks: Optional[int] = None,
    memory_peak_mb: Optional[float] = None
):
    """Merge the given non-None performance metrics into the current task's log context."""
    performance_data = {k: v for k, v in {
        'db_query_count': db_query_count,
        'db_query_time_ms': db_query_time_ms,
        'external_api_calls': external_api_calls,
        'external_api_time_ms': external_api_time_ms,
        'llm_calls': llm_calls,
        'llm_time_ms': llm_time_ms,
        'cache_hits': cache_hits,
        'cache_misses': cache_misses,
        'background_tasks': background_tasks,
        'memory_peak_mb': memory_peak_mb
    }.items() if v is not None}
    
    if performance_data:
        set_log_context(**performance_data)


def increment_performance_counter(metric_name: str, value: float = 1.0):
    """Add value to a named counter in the current task's log context."""
    current_context = _log_context.get()
    current_value = getattr(current_context, metric_name, None) or 0
    new_value = current_value + value
    set_log_context(**{metric_name: new_value})


def add_to_time_metric(metric_name: str, time_ms: float):
    """Add milliseconds to a named time metric in the current task's log context."""
    current_context = _log_context.get()
    current_value = getattr(current_context, metric_name, None) or 0.0
    new_value = current_value + time_ms
    set_log_context(**{metric_name: new_value})


class DatabaseLogHandler(logging.Handler):
    """Custom logging handler with memory queue for ultra-fast logging.
    
    Features:
        - Memory-only queue for instant log emission (sub-millisecond)
        - Background worker thread for database operations
        - Automatic context enrichment from request data
        - Graceful fallback when database service unavailable
        - Thread-safe operations for concurrent access
        - Configurable queue size and flush intervals
        
    Args:
        level: Minimum log level to handle. Defaults to logging.DEBUG.
        fallback_handler: Optional fallback handler for errors.
        queue_size: Maximum queue size before dropping logs. Defaults to 10000.
        flush_interval: Background flush interval in seconds. Defaults to 2.0.
    
    Example:
        handler = DatabaseLogHandler(level=logging.INFO, queue_size=5000)
        logger.addHandler(handler)
    """
    
    def __init__(
        self, 
        level=logging.DEBUG, 
        fallback_handler: Optional[logging.Handler] = None,
        queue_size: Optional[int] = None,
        flush_interval: Optional[float] = None,
        batch_size: Optional[int] = None
    ):
        """Configure the queue from arguments or DB_LOG_* settings and start the background worker thread."""
        super().__init__(level)
        self.fallback_handler = fallback_handler
        self.formatter = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
        
        self.queue_size = queue_size or settings.DB_LOG_QUEUE_SIZE
        self.flush_interval = flush_interval or settings.DB_LOG_FLUSH_INTERVAL
        self.batch_size = batch_size or settings.DB_LOG_BATCH_SIZE
        
        self._log_queue: Deque[QueuedLogEntry] = deque()
        self._queue_lock = threading.Lock()
        
        self._worker_thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()
        self._enabled = True
        self._service_available = True
        self._retry_not_before = 0.0
        self._queue_overflow_count = 0
        self._error_drop_count = 0
        self._shutting_down = False
        self._loop = None
        self._connection = None
        
        self.start_worker()
        _handler_logger.info(f"Database log handler initialized with queue_size={self.queue_size}, flush_interval={self.flush_interval}s, batch_size={self.batch_size}")
    
    def emit(self, record: logging.LogRecord):
        """Queue a record with the current log context, evicting by severity when full, and use the fallback on error."""
        if not self._enabled:
            return
        
        try:
            context = get_log_context()
            
            stack_trace = None
            if record.exc_info:
                stack_trace = ''.join(traceback.format_exception(*record.exc_info))
            elif record.stack_info:
                stack_trace = record.stack_info
            
            entry = QueuedLogEntry(
                level=record.levelname,
                message=record.getMessage(),
                timestamp=record.created,
                module=getattr(record, 'module', record.name),
                function=record.funcName,
                lineno=record.lineno,
                context=context,
                stack_trace=stack_trace,
                extra=getattr(record, 'extra_data', None)
            )
            
            with self._queue_lock:
                while len(self._log_queue) >= self.queue_size:
                    if not self._evict_one(entry.level):
                        return
                
                self._log_queue.append(entry)
            
        except Exception:
            _handler_logger.error("log handler: failed to queue a log record", exc_info=True)
            if self.fallback_handler:
                try:
                    self.fallback_handler.emit(record)
                except Exception:
                    pass
    
    def _evict_one(self, incoming_level: str) -> bool:
        """Drop the oldest non-error entry, or the head for an incoming error; return False to drop the incoming record."""
        severe = {"ERROR", "CRITICAL"}

        for index in range(min(len(self._log_queue), self.batch_size)):
            if self._log_queue[index].level not in severe:
                del self._log_queue[index]
                self._queue_overflow_count += 1
                self._report_overflow()
                return True

        if incoming_level not in severe:
            self._queue_overflow_count += 1
            self._report_overflow()
            return False

        dropped = self._log_queue.popleft()
        self._queue_overflow_count += 1
        self._error_drop_count += 1
        self._report_overflow()
        _handler_logger.critical(
            "Database log queue is saturated with errors; dropped a %s record from %s:%s (%d lost so far)",
            dropped.level, dropped.module, dropped.lineno, self._error_drop_count,
        )
        return True

    def _report_overflow(self):
        """Log a warning on every hundredth dropped record."""
        if self._queue_overflow_count % 100 == 0:
            _handler_logger.warning(
                "Database log queue overflow: %d records dropped so far (%d of them errors)",
                self._queue_overflow_count, self._error_drop_count,
            )

    def start_worker(self):
        """Start the daemon worker thread unless it is already running."""
        if self._worker_thread is None or not self._worker_thread.is_alive():
            self._shutdown_event.clear()
            self._worker_thread = threading.Thread(
                target=self._background_worker,
                name="DatabaseLogWorker",
                daemon=True
            )
            self._worker_thread.start()
    
    def stop_worker(self):
        """Signal the worker to drain and exit, then wait for it."""
        if self._worker_thread and self._worker_thread.is_alive():
            self._shutdown_event.set()
            timeout = 15.0 if self._log_queue else 2.0
            self._worker_thread.join(timeout=timeout)
            if self._worker_thread.is_alive() and self._log_queue:
                _handler_logger.warning(
                    "Database log worker did not finish draining within %.0fs; %d records may be lost",
                    timeout, len(self._log_queue),
                )

    def _background_worker(self):
        """Flush queued records to the database until shutdown, then drain what is left."""
        import time

        time.sleep(2.0)
        while not self._shutdown_event.is_set():
            try:
                if self._shutdown_event.wait(timeout=self.flush_interval):
                    break

                self._flush_queue()

            except Exception:
                _handler_logger.error("log handler: background flush cycle failed", exc_info=True)

        try:
            self._flush_queue(final=True)
        except Exception:
            _handler_logger.error("log handler: final flush on shutdown failed", exc_info=True)

        if self._log_queue:
            self._spill_to_disk()

        try:
            if self._loop is not None and not self._loop.is_closed():
                self._loop.run_until_complete(self._discard_connection())
                self._loop.close()
        except Exception:
            _handler_logger.debug("log handler: worker loop teardown raised", exc_info=True)

    def _spill_to_disk(self):
        """Append records still queued after the final drain to logs/unflushed_app_logs.jsonl, reporting via print."""
        import json as _json
        import os as _os
        import traceback as _traceback

        with self._queue_lock:
            batch, self._log_queue = list(self._log_queue), type(self._log_queue)()

        if not batch:
            return

        try:
            base_dir = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
            log_dir = _os.path.join(base_dir, "logs")
            _os.makedirs(log_dir, exist_ok=True)
            path = _os.path.join(log_dir, "unflushed_app_logs.jsonl")

            with open(path, "a", encoding="utf-8") as fh:
                for entry in batch:
                    fh.write(_json.dumps({
                        "level": entry.level,
                        "message": entry.message,
                        "timestamp": entry.timestamp,
                        "module": entry.module,
                        "function": entry.function,
                        "lineno": entry.lineno,
                        "request_id": getattr(entry.context, "request_id", None),
                        "endpoint": getattr(entry.context, "endpoint", None),
                        "stack_trace": entry.stack_trace,
                    }, default=str) + "\n")

            print(
                f"[APP] {len(batch)} log record(s) could not reach the database at shutdown; "
                f"spilled to {path}"
            )
        except Exception:
            print("[APP] Failed to spill unflushed log records:")
            _traceback.print_exc()

    def _flush_queue(self, final: bool = False):
        """Write queued batches until empty, re-queueing a failed batch and backing off with a capped cooldown."""
        import time

        if self._shutting_down:
            return
        if not final and not self._service_available and time.monotonic() < self._retry_not_before:
            return

        while True:
            batch = []
            with self._queue_lock:
                for _ in range(min(self.batch_size, len(self._log_queue))):
                    batch.append(self._log_queue.popleft())

            if not batch:
                return

            try:
                success = self._store_batch_to_database(batch)
            except Exception:
                _handler_logger.error("Database flush handler failed", exc_info=True)
                success = False

            if success:
                if not self._service_available:
                    self._service_available = True
                    _handler_logger.info(
                        "Database logging recovered after %d consecutive failures",
                        getattr(self, "_consecutive_failures", 0),
                    )
                self._consecutive_failures = 0
                self._retry_not_before = 0.0
                continue

            self._requeue(batch)

            self._service_available = False
            self._consecutive_failures = getattr(self, "_consecutive_failures", 0) + 1
            cooldown = min(60.0, self.flush_interval * (2 ** min(self._consecutive_failures, 6)))
            self._retry_not_before = time.monotonic() + cooldown

            if self._consecutive_failures == 1:
                _handler_logger.warning(
                    "Database logging unavailable; retrying in %.1fs (%d records held)",
                    cooldown, len(self._log_queue),
                )
            elif self._consecutive_failures % 10 == 0:
                _handler_logger.error(
                    "Database logging still unavailable after %d attempts; %d records held, retrying in %.1fs",
                    self._consecutive_failures, len(self._log_queue), cooldown,
                )
            return

    def _requeue(self, batch: list) -> None:
        """Put an unwritten batch back at the queue head in order, counting entries that no longer fit as drops."""
        with self._queue_lock:
            for entry in reversed(batch):
                if len(self._log_queue) >= self.queue_size:
                    self._queue_overflow_count += 1
                    if entry.level in ("ERROR", "CRITICAL"):
                        self._error_drop_count += 1
                    continue
                self._log_queue.appendleft(entry)
    
    async def _get_connection(self):
        """Return the worker's dedicated asyncpg connection, opening a new one when missing or closed."""
        import asyncpg

        if self._connection is not None and not self._connection.is_closed():
            return self._connection

        self._connection = await asyncpg.connect(
            host=settings.DBHOST,
            port=int(settings.DBPORT),
            database=settings.DATABASENAME,
            user=settings.DBUSERNAME,
            password=settings.DBPASSWORD,
            ssl=True,
            timeout=15.0,
            command_timeout=30.0,
            server_settings={"application_name": "etown_logs_handler"},
        )
        return self._connection

    async def _discard_connection(self):
        """Close and forget the cached connection so the next flush reconnects."""
        conn, self._connection = self._connection, None
        if conn is None:
            return
        try:
            await conn.close()
        except Exception:
            _handler_logger.debug("log handler: closing the failed connection raised", exc_info=True)

    def _run(self, coro):
        """Run a coroutine on the worker thread's persistent event loop, creating the loop if needed."""
        import asyncio

        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        return self._loop.run_until_complete(coro)

    def _build_rows(self, batch: list) -> list:
        """Build INSERT parameter tuples, folding business context and performance metrics into extra_data JSON."""
        rows = []
        for entry in batch:
            utc_timestamp = datetime.fromtimestamp(entry.timestamp, tz=timezone.utc)
            utc_timestamp_naive = utc_timestamp.replace(tzinfo=None)

            enhanced_extra_data = {}

            if entry.extra:
                enhanced_extra_data.update(entry.extra)

            business_context = {}
            if entry.context.provider_id:
                business_context['provider_id'] = entry.context.provider_id
            if entry.context.service_id:
                business_context['service_id'] = entry.context.service_id
            if entry.context.search_query:
                business_context['search_query'] = entry.context.search_query
            if entry.context.location_context:
                business_context['location_context'] = entry.context.location_context
            if entry.context.agent_context:
                business_context['agent_context'] = entry.context.agent_context
            if entry.context.request_payload:
                business_context['request_payload'] = entry.context.request_payload
            if entry.context.response_payload:
                business_context['response_payload'] = entry.context.response_payload

            if business_context:
                enhanced_extra_data['business_context'] = business_context

            performance_metrics = {}
            if entry.context.db_query_count:
                performance_metrics['db_query_count'] = entry.context.db_query_count
            if entry.context.db_query_time_ms:
                performance_metrics['db_query_time_ms'] = entry.context.db_query_time_ms
            if entry.context.external_api_calls:
                performance_metrics['external_api_calls'] = entry.context.external_api_calls
            if entry.context.external_api_time_ms:
                performance_metrics['external_api_time_ms'] = entry.context.external_api_time_ms
            if entry.context.llm_calls:
                performance_metrics['llm_calls'] = entry.context.llm_calls
            if entry.context.llm_time_ms:
                performance_metrics['llm_time_ms'] = entry.context.llm_time_ms
            if entry.context.cache_hits:
                performance_metrics['cache_hits'] = entry.context.cache_hits
            if entry.context.cache_misses:
                performance_metrics['cache_misses'] = entry.context.cache_misses
            if entry.context.background_tasks:
                performance_metrics['background_tasks'] = entry.context.background_tasks
            if entry.context.memory_peak_mb:
                performance_metrics['memory_peak_mb'] = entry.context.memory_peak_mb

            if performance_metrics:
                enhanced_extra_data['performance_metrics'] = performance_metrics

            try:
                extra_data_json = json.dumps(enhanced_extra_data, cls=EnhancedJSONEncoder) if enhanced_extra_data else None
            except Exception:
                _handler_logger.warning(
                    "log handler: extra_data for %s:%s is not serialisable; storing the row without it",
                    entry.module, entry.lineno, exc_info=True,
                )
                extra_data_json = None

            rows.append((
                entry.context.request_id,
                entry.level,
                entry.message,
                utc_timestamp_naive,
                entry.module,
                entry.function,
                entry.lineno,
                entry.context.endpoint,
                entry.context.method,
                entry.context.userprofile_id,
                entry.context.contact_id,
                entry.context.ip_address,
                entry.context.user_agent,
                entry.context.status_code,
                entry.context.response_time_ms,
                entry.stack_trace,
                extra_data_json,
            ))
        return rows

    def _store_batch_to_database(self, batch: list[QueuedLogEntry]) -> bool:
        """Write a batch to etown_application_logs, returning False when the caller should re-queue it."""
        if self._shutting_down:
            return False

        insert_query = """
            INSERT INTO etown_application_logs (
                id, request_id, log_level, message, timestamp_ist, module_name, function_name, line_number,
                endpoint, method, userprofile_id, contact_id, ip_address,
                user_agent, status_code, response_time_ms, stack_trace, extra_data
            ) VALUES (gen_random_uuid(), $1, $2, $3, $4::timestamp + interval '+05:30', $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
        """

        try:
            rows = self._build_rows(batch)
        except Exception:
            _handler_logger.error(
                "log handler: could not build INSERT rows for %d records; dropping the batch",
                len(batch), exc_info=True,
            )
            return True

        async def store():
            conn = await self._get_connection()
            try:
                await conn.executemany(insert_query, rows)
            except Exception:
                await self._discard_connection()
                raise
            return True

        try:
            result = self._run(store())
            _handler_logger.debug("Wrote %d log records to etown_application_logs", len(rows))
            return result
        except RuntimeError as e:
            if "cannot schedule new futures after interpreter shutdown" in str(e):
                _handler_logger.warning("Database log flush skipped during interpreter shutdown")
                self._shutting_down = True
                return False
            _handler_logger.error("Database log flush hit a runtime error", exc_info=True)
            return False
        except Exception as e:
            error_message = str(e).lower()
            error_type = type(e).__name__
            is_network_error = (
                "gaierror" in error_type.lower()
                or "getaddrinfo failed" in error_message
                or "network" in error_message
                or "timeout" in error_message
                or "connection refused" in error_message
            )
            consecutive_failures = getattr(self, '_consecutive_failures', 0)

            if is_network_error and consecutive_failures < 2:
                _handler_logger.warning(
                    "Database unreachable while flushing %d log records: %s: %s", len(rows), error_type, e
                )
            else:
                _handler_logger.error(
                    "Database log flush failed for %d records: %s: %s", len(rows), error_type, e, exc_info=True
                )
            return False

    def force_flush(self):
        """Flush the queue immediately on the calling thread and log how many records were written."""
        if hasattr(self, '_flush_queue'):
            try:
                initial_queue_size = len(self._log_queue)
                _handler_logger.info(f"Force flush starting: {initial_queue_size} logs in queue")
                
                self._flush_queue()
                
                final_queue_size = len(self._log_queue)
                processed_logs = initial_queue_size - final_queue_size
                _handler_logger.info(f"Force flush completed: {processed_logs} logs processed, {final_queue_size} logs remaining in queue")
                
                if final_queue_size == initial_queue_size and initial_queue_size > 0:
                    _handler_logger.warning("No logs were processed during force flush - check error messages above")
                
            except Exception as e:
                _handler_logger.error(f"Force flush failed with exception: {e}", exc_info=True)
        else:
            _handler_logger.error("Force flush not available: _flush_queue method not found")
    
    def get_queue_status(self) -> Dict[str, Any]:
        """Return queue size, limits, worker liveness, availability and overflow count."""
        return {
            "queue_size": len(self._log_queue) if hasattr(self, '_log_queue') else 0,
            "max_queue_size": getattr(self, 'queue_size', 0),
            "batch_size": getattr(self, 'batch_size', 0),
            "flush_interval": getattr(self, 'flush_interval', 0),
            "worker_alive": self._worker_thread and self._worker_thread.is_alive() if hasattr(self, '_worker_thread') else False,
            "service_available": getattr(self, '_service_available', False),
            "handler_enabled": getattr(self, '_enabled', False),
            "queue_overflow_count": getattr(self, '_queue_overflow_count', 0)
        }
    
    def disable(self):
        """Stop queueing new records until enable() is called."""
        self._enabled = False
    
    def enable(self):
        """Resume queueing records after disable()."""
        self._enabled = True
    
    def close(self):
        """Disable the handler, stop the worker after it drains, and close the handler."""
        self._enabled = False
        self.stop_worker()
        self._shutting_down = True
        super().close()


def create_database_handler(
    level: int = logging.DEBUG,
    fallback_to_console: Optional[bool] = None,
    queue_size: Optional[int] = None,
    flush_interval: Optional[float] = None,
    batch_size: Optional[int] = None
) -> DatabaseLogHandler:
    """Create a DatabaseLogHandler with an optional console fallback handler, defaulting options from settings."""
    fallback_handler = None
    
    use_fallback = fallback_to_console if fallback_to_console is not None else settings.DB_LOG_FALLBACK_TO_CONSOLE
    
    if use_fallback:
        fallback_handler = logging.StreamHandler()
        fallback_handler.setLevel(level)
        fallback_handler.setFormatter(
            logging.Formatter(
                '[DB-FALLBACK] %(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
            )
        )
    
    db_handler = DatabaseLogHandler(
        level=level, 
        fallback_handler=fallback_handler,
        queue_size=queue_size,
        flush_interval=flush_interval,
        batch_size=batch_size
    )
    db_handler.setFormatter(
        logging.Formatter('%(name)s - %(levelname)s - %(message)s')
    )
    
    return db_handler