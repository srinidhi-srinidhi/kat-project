"""Log Storage Service - queued, batched persistence and querying of application logs.

Logging service that buffers enriched log entries, writes them to etown_application_logs in background batches through the shared Database pool, serves the filtered reads behind /api/logs, runs read-only SELECTs for /api/logs/dbquery and prunes rows past the retention window.

Features:
    - Async log storage: Asynchronous log insertion with batch processing
    - Database integration: Uses established Database class with retry logic
    - Context enrichment: Enhanced log entries with request and user context
    - Enhanced Context Support: Business domain data, performance metrics, and agent interactions
    - Business Domain Logging: Provider operations, search queries, booking context, location data
    - Performance Metrics Storage: Database queries, API calls, LLM usage, cache performance
    - Agent Interaction Tracking: Tool usage, decision logging, agent context preservation
    - Performance optimization: Background tasks for non-blocking log storage
    - Error resilience: Fallback handling for database connectivity issues
    - Cleanup management: Automated old log purging with configurable retention
    - Individual processing: Multiple insert statements for database compatibility
    - Thread safety: Safe concurrent access for async environment
    - Advanced Filtering: Enhanced query capabilities for business and performance data

Classes:
    LogEntry: Dataclass holding one log record's fields for insertion
    LogStorageService: Queues, batches, filters and prunes etown_application_logs rows

Functions:
    get_log_storage_service(): Return the LogStorageService singleton, starting it once the pool is ready
    shutdown_log_storage_service(): Stop and clear the global LogStorageService singleton
    run_readonly_select(): Run one SELECT in a read-only transaction and return up to max_rows rows

Dependencies:
    - asyncio: Asynchronous programming support
    - logging: Python standard logging framework
    - datetime: Timestamp handling and date operations
    - typing: Type hints for better code documentation
    - app.db.client: Database connection management
    - app.core.config: Application settings and configuration

Updates:
    12-Mar-2026: Created async batch log storage; IST timezone conversion; UUID request_id primary key.
    13-Mar-2026: Fixed id/request_id selection; added business context, performance and agent data.
    14-Mar-2026: Moved from app.services.log_storage to app.logs.storage.
    26-Aug-2026: Logged storage-service start failures instead of swallowing them.
    14-Oct-2026: Added run_readonly_select for the /logs/dbquery route.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from contextlib import asynccontextmanager

from app.db.client import Database
from app.core.config import settings
logger = logging.getLogger("app.logs.storage")


@dataclass
class LogEntry:
    """Data structure for log record information.
    
    Attributes:
        log_level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        message: Log message content.
        timestamp_ist: IST timestamp when log was created.
        module_name: Python module name where log originated.
        function_name: Function name where log was called.
        line_number: Line number in source file.
        request_id: Unique request identifier for tracing.
        endpoint: API endpoint being accessed.
        method: HTTP method (GET, POST, etc.).
        user_id: User identifier if available.
        session_id: Session identifier if available.
        ip_address: Client IP address.
        user_agent: Client user agent string.
        status_code: HTTP response status code.
        response_time_ms: Response time in milliseconds.
        stack_trace: Exception stack trace if available.
        extra_data: Additional context data as JSON.
    """
    log_level: str
    message: str
    timestamp_ist: datetime
    module_name: Optional[str] = None
    function_name: Optional[str] = None
    line_number: Optional[int] = None
    request_id: Optional[str] = None
    endpoint: Optional[str] = None
    method: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    status_code: Optional[int] = None
    response_time_ms: Optional[int] = None
    stack_trace: Optional[str] = None
    extra_data: Optional[Dict[str, Any]] = None


class LogStorageService:
    """Main service for database log operations.
    
    Features:
        - Async batch processing for performance
        - Automatic retry with database connection resilience  
        - Configurable retention policy for old log cleanup
        - Thread-safe operations for concurrent access
        - Graceful fallback when database unavailable
        
    Args:
        batch_size: Number of logs to process in each batch. Uses settings default if None.
        flush_interval: Seconds between batch flushes. Uses settings default if None.
        retention_days: Days to keep logs before cleanup. Uses settings default if None.
    """
    
    def __init__(
        self, 
        batch_size: Optional[int] = None, 
        flush_interval: Optional[float] = None, 
        retention_days: Optional[int] = None
    ):
        """Configure batching and retention, falling back to the DB_LOG_* settings for any value not given."""
        self.batch_size = batch_size or settings.DB_LOG_BATCH_SIZE
        self.flush_interval = flush_interval or settings.DB_LOG_FLUSH_INTERVAL
        self.retention_days = retention_days or settings.DB_LOG_RETENTION_DAYS
        self._log_queue: List[LogEntry] = []
        self._queue_lock = asyncio.Lock()
        self._background_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        self.logger = logging.getLogger("app.logs.storage")
    
    async def start(self):
        """Start the background batch processor unless it is already running."""
        if self._background_task is None or self._background_task.done():
            self._shutdown_event.clear()
            self._background_task = asyncio.create_task(self._batch_processor())
            self.logger.info(f"Log storage service started with batch_size={self.batch_size}, flush_interval={self.flush_interval}s")
    
    async def stop(self):
        """Signal shutdown, flush the queue and wait for the batch processor to exit."""
        if self._background_task and not self._background_task.done():
            self._shutdown_event.set()
            await self._flush_batch()
            await self._background_task
            self.logger.info("Log storage service stopped")
    
    async def store_log_async(self, log_entry: LogEntry):
        """Queue a log entry, flushing immediately once the queue reaches batch size."""
        async with self._queue_lock:
            self._log_queue.append(log_entry)
            
            if len(self._log_queue) >= self.batch_size:
                await self._flush_batch_unlocked()
    
    async def store_logs_batch(self, log_entries: List[LogEntry]):
        """Insert log entries in batch-size chunks, logging and abandoning the rest on the first failure."""
        if not log_entries:
            return
            
        try:
            for i in range(0, len(log_entries), self.batch_size):
                chunk = log_entries[i:i + self.batch_size]
                await self._insert_logs_to_db(chunk)
                
        except Exception as e:
            self.logger.error(f"Failed to store log batch: {e}", exc_info=True)
    
    async def get_logs_filtered(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        log_level: Optional[str] = None,
        endpoint: Optional[str] = None,
        userprofile_id: Optional[str] = None,
        contact_id: Optional[str] = None,
        request_id: Optional[str] = None,
        method: Optional[str] = None,
        status_code: Optional[int] = None,
        
        provider_id: Optional[str] = None,
        service_type: Optional[str] = None,
        search_query: Optional[str] = None,
        agent_type: Optional[str] = None,
        business_operation: Optional[str] = None,
        
        min_response_time: Optional[int] = None,
        max_response_time: Optional[int] = None,
        min_db_query_time: Optional[float] = None,
        max_db_query_time: Optional[float] = None,
        has_llm_calls: Optional[bool] = None,
        min_llm_calls: Optional[int] = None,
        
        tool_name: Optional[str] = None,
        agent_decision: Optional[str] = None,
        extra_data_contains: Optional[str] = None,
        
        limit: int = 1000,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Return a page of etown_application_logs rows matching column and extra_data JSONB filters, newest first, or [] on error."""
        conditions = []
        params = []
        param_count = 1
        
        if start_time:
            conditions.append(f"timestamp_ist >= ${param_count}")
            params.append(start_time)
            param_count += 1
            
        if end_time:
            conditions.append(f"timestamp_ist <= ${param_count}")
            params.append(end_time)
            param_count += 1
            
        if log_level:
            conditions.append(f"log_level = ${param_count}")
            params.append(log_level.upper())
            param_count += 1
            
        if endpoint:
            conditions.append(f"endpoint LIKE ${param_count}")
            params.append(f"%{endpoint}%")
            param_count += 1
            
        if userprofile_id:
            conditions.append(f"userprofile_id = ${param_count}")
            params.append(userprofile_id)
            param_count += 1
            
        if contact_id:
            conditions.append(f"contact_id = ${param_count}")
            params.append(contact_id)
            param_count += 1
            
        if request_id:
            conditions.append(f"request_id = ${param_count}")
            params.append(request_id)
            param_count += 1
            
        if method:
            conditions.append(f"method = ${param_count}")
            params.append(method.upper())
            param_count += 1
            
        if status_code:
            conditions.append(f"status_code = ${param_count}")
            params.append(status_code)
            param_count += 1
        
        if provider_id:
            conditions.append(f"extra_data->'business_context'->>'provider_id' = ${param_count}")
            params.append(provider_id)
            param_count += 1
            
        if service_type:
            conditions.append(f"extra_data->'business_context'->>'service_type' ILIKE ${param_count}")
            params.append(f"%{service_type}%")
            param_count += 1
            
        if search_query:
            conditions.append(f"extra_data->'business_context'->>'search_query' ILIKE ${param_count}")
            params.append(f"%{search_query}%")
            param_count += 1
            
        if agent_type:
            conditions.append(f"extra_data->'business_context'->>'agent_type' = ${param_count}")
            params.append(agent_type)
            param_count += 1
            
        if business_operation:
            conditions.append(f"extra_data->'business_context'->>'operation' ILIKE ${param_count}")
            params.append(f"%{business_operation}%")
            param_count += 1
        
        if min_response_time is not None:
            conditions.append(f"response_time_ms >= ${param_count}")
            params.append(min_response_time)
            param_count += 1
            
        if max_response_time is not None:
            conditions.append(f"response_time_ms <= ${param_count}")
            params.append(max_response_time)
            param_count += 1
            
        if min_db_query_time is not None:
            conditions.append(f"CAST(extra_data->'performance_metrics'->>'db_query_time' AS FLOAT) >= ${param_count}")
            params.append(min_db_query_time)
            param_count += 1
            
        if max_db_query_time is not None:
            conditions.append(f"CAST(extra_data->'performance_metrics'->>'db_query_time' AS FLOAT) <= ${param_count}")
            params.append(max_db_query_time)
            param_count += 1
            
        if has_llm_calls is not None:
            if has_llm_calls:
                conditions.append(f"CAST(extra_data->'performance_metrics'->>'llm_calls' AS INTEGER) > 0")
            else:
                conditions.append(f"(extra_data->'performance_metrics'->>'llm_calls' IS NULL OR CAST(extra_data->'performance_metrics'->>'llm_calls' AS INTEGER) = 0)")
            
        if min_llm_calls is not None:
            conditions.append(f"CAST(extra_data->'performance_metrics'->>'llm_calls' AS INTEGER) >= ${param_count}")
            params.append(min_llm_calls)
            param_count += 1
        
        if tool_name:
            conditions.append(f"extra_data->'agent_context'->>'tool_name' = ${param_count}")
            params.append(tool_name)
            param_count += 1
            
        if agent_decision:
            conditions.append(f"extra_data->'agent_context'->>'decision' ILIKE ${param_count}")
            params.append(f"%{agent_decision}%")
            param_count += 1
            
        if extra_data_contains:
            conditions.append(f"extra_data::text ILIKE ${param_count}")
            params.append(f"%{extra_data_contains}%")
            param_count += 1
        
        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        
        query = f"""
            SELECT id, request_id, log_level, message, 
                   timezone('Asia/Kolkata', timestamp_ist) as timestamp_ist,
                   module_name, function_name, line_number,
                   endpoint, method, userprofile_id, contact_id,
                   ip_address, user_agent, status_code, response_time_ms,
                   stack_trace, extra_data, created_at
            FROM etown_application_logs
            {where_clause}
            ORDER BY timestamp_ist DESC
            LIMIT ${param_count} OFFSET ${param_count + 1}
        """
        params.extend([limit, offset])
        
        try:
            rows = await Database.fetch(query, *params)
            return [dict(row) for row in rows]
        except Exception as e:
            self.logger.error(f"Failed to retrieve filtered logs: {e}", exc_info=True)
            return []
    
    async def cleanup_old_logs(self):
        """Delete application logs older than the retention period, logging any failure."""
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        
        try:
            query = "DELETE FROM etown_application_logs WHERE timestamp_ist < $1"
            result = await Database.execute(query, cutoff_date)
            self.logger.info(f"Cleaned up old logs before {cutoff_date}. Affected rows: {result}")
        except Exception as e:
            self.logger.error(f"Failed to cleanup old logs: {e}", exc_info=True)
    
    async def _batch_processor(self):
        """Flush the queue every flush interval until shutdown, then flush once more."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(), 
                    timeout=self.flush_interval
                )
                break
            except asyncio.TimeoutError:
                await self._flush_batch()
        
        await self._flush_batch()
    
    async def _flush_batch(self):
        """Flush the queued entries to the database while holding the queue lock."""
        async with self._queue_lock:
            await self._flush_batch_unlocked()
    
    async def _flush_batch_unlocked(self):
        """Write the queued entries to the database, re-queueing them on failure; the caller holds the lock."""
        if not self._log_queue:
            return
            
        batch = self._log_queue.copy()
        self._log_queue.clear()
        
        try:
            await self._insert_logs_to_db(batch)
        except Exception as e:
            if "Database pool not ready" not in str(e):
                self.logger.error(f"Failed to flush log batch: {e}", exc_info=True)
                self._log_queue.extend(batch)
    
    async def _insert_logs_to_db(self, log_entries: List[LogEntry]):
        """Insert log entries into etown_application_logs one row at a time, re-queueing them when the pool is not ready."""
        if not log_entries:
            return
        
        try:
            from app.db.client import Database
            if not hasattr(Database, '_pool') or Database._pool is None or Database._pool._closed:
                self.logger.debug("Database pool not ready, re-queuing log entries")
                async with self._queue_lock:
                    self._log_queue.extend(log_entries)
                return
        except Exception as e:
            self.logger.debug(f"Database check failed: {e}")
            return
        
        query = """
            INSERT INTO etown_application_logs (
                log_level, message, timestamp_ist, module_name, function_name, 
                line_number, request_id, endpoint, method, user_id, session_id,
                ip_address, user_agent, status_code, response_time_ms, 
                stack_trace, extra_data, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
        """
        
        try:
            for entry in log_entries:
                await Database.execute(
                    query,
                    entry.log_level,
                    entry.message,
                    entry.timestamp_ist,
                    entry.module_name,
                    entry.function_name,
                    entry.line_number,
                    entry.request_id,
                    entry.endpoint,
                    entry.method,
                    entry.user_id,
                    entry.session_id,
                    str(entry.ip_address) if entry.ip_address else None,
                    entry.user_agent,
                    entry.status_code,
                    entry.response_time_ms,
                    entry.stack_trace,
                    entry.extra_data,
                    datetime.now(timezone.utc)
                )
        except Exception as e:
            self.logger.error(f"Database insert failed: {e}", exc_info=True)
            raise


_log_storage_service: Optional[LogStorageService] = None


async def get_log_storage_service() -> LogStorageService:
    """Return the global log storage service, creating it and starting its processor once the pool is ready."""
    global _log_storage_service
    
    if _log_storage_service is None:
        _log_storage_service = LogStorageService()
        
        try:
            from app.db.client import Database
            if hasattr(Database, '_pool') and Database._pool and not Database._pool._closed:
                await _log_storage_service.start()
            else:
                pass
        except Exception:
            logger.warning("log storage service failed to start; application logs will queue until it does", exc_info=True)
    
    if _log_storage_service and (_log_storage_service._background_task is None or _log_storage_service._background_task.done()):
        try:
            from app.db.client import Database
            if hasattr(Database, '_pool') and Database._pool and not Database._pool._closed:
                await _log_storage_service.start()
        except Exception:
            logger.warning("log storage service failed to restart", exc_info=True)
    
    return _log_storage_service


async def shutdown_log_storage_service():
    """Stop the global log storage service, flushing queued logs, and discard it."""
    global _log_storage_service
    
    if _log_storage_service:
        await _log_storage_service.stop()
        _log_storage_service = None


async def run_readonly_select(query: str, max_rows: int, timeout_seconds: int) -> List[Dict[str, Any]]:
    """Run one SELECT in a read-only transaction with a statement timeout and return up to max_rows JSON-safe rows.

    Args:
        query: A single SELECT or WITH ... SELECT statement without a trailing semicolon.
        max_rows: Maximum number of rows to fetch.
        timeout_seconds: Statement timeout applied to this query only.

    Returns:
        Rows as dicts keyed by column name, with values converted to JSON-safe types.

    Raises:
        asyncpg.PostgresError: When the query fails, writes, or exceeds the timeout.
        asyncpg.InterfaceError: When the query cannot be prepared, such as multiple statements or $n parameters.
    """
    from fastapi.encoders import jsonable_encoder

    def to_json_value(value: Any) -> Any:
        """Convert one database value to a JSON-safe value, falling back to str() for unencodable types."""
        try:
            return jsonable_encoder(value, custom_encoder={bytes: lambda b: b.hex()})
        except Exception:
            return str(value)

    Database.assert_available()
    pool = Database._pool
    if pool is None or pool._closed:
        raise RuntimeError("Database pool is not initialized or is closed.")

    async with pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
        async with con.transaction(readonly=True):
            await con.execute(f"SET LOCAL statement_timeout = {int(timeout_seconds) * 1000}")
            stmt = await con.prepare(query)
            cursor = await stmt.cursor()
            records = await cursor.fetch(max_rows)

    return [
        {key: to_json_value(value) for key, value in record.items()}
        for record in records
    ]