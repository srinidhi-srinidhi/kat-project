"""Terminal Log Storage Service - batches captured console lines into etown_terminal_logs.

Logging service that parses captured terminal lines, tags their source, queues them and writes them to etown_terminal_logs in background batches, holding a batch for retry while the pool is not ready and pruning rows past the retention window.

Features:
    - Async terminal log storage: Asynchronous terminal log insertion with batch processing  
    - Line parsing: parse_and_store_log_line() parses a raw console line and queues it
    - Database integration: Uses established Database class with retry logic
    - Source identification: Distinguish between uvicorn and application log sources
    - Batch processing: Background tasks for non-blocking log storage 
    - Error resilience: Fallback handling for database connectivity issues
    - Cleanup management: Automated old terminal log purging with configurable retention
    - Batch re-queue: a failed batch is held and retried when the pool is not ready
    - Thread safety: Safe concurrent access for async environment 
    - Performance optimization: Batched inserts for high-volume terminal output

Classes:
    _PoolNotReady: Raised when the shared pool is unavailable so a batch is held, not lost
    TerminalLogEntry: Dataclass holding one terminal log line's timestamp/text/source
    TerminalLogStorageService: Queues, batches, filters and prunes etown_terminal_logs rows

Functions:
    get_terminal_log_storage_service(): Return the singleton, starting it once the pool is ready
    shutdown_terminal_log_storage_service(): Stop and clear the global TerminalLogStorageService singleton

Dependencies:
    - asyncio: Asynchronous programming support
    - logging: Python standard logging framework
    - datetime: Timestamp handling and date operations
    - typing: Type hints for better code documentation
    - re: Log-source pattern matching
    - app.db.client: Database connection management
    - app.core.config: Application settings and configuration

Updates:
    14-Mar-2026: Created terminal log storage with batch processing; moved from app.services to app.logs.
    26-Aug-2026: Fixed lock deadlock; batched inserts; failed batches re-queued.
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from contextlib import asynccontextmanager

from app.db.client import Database
from app.core.config import settings

logger = logging.getLogger("app.logs.terminal_storage")

MAX_QUEUED_TERMINAL_LOGS = 20000


class _PoolNotReady(RuntimeError):
    """Raised when the shared pool is unavailable, so a batch is held rather than lost."""


@dataclass
class TerminalLogEntry:
    """Data structure for terminal log record information.
    
    Attributes:
        timestamp_ist: IST timestamp when log was created.
        log_line: Full original terminal output line.
        source: Log source identifier (uvicorn or app).
    """
    timestamp_ist: datetime
    log_line: str
    source: str


class TerminalLogStorageService:
    """Main service for terminal log database operations.
    
    Features:
        - Async batch processing for performance
        - Automatic retry with database connection resilience
        - Configurable retention policy for old log cleanup
        - Thread-safe operations for concurrent access
        - Graceful fallback when database unavailable
        - Real-time file monitoring for log capture
        
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
        """Configure batching and retention, falling back to settings for any value not given."""
        self.batch_size = batch_size or getattr(settings, 'TERMINAL_LOG_BATCH_SIZE', 100)
        self.flush_interval = flush_interval or getattr(settings, 'TERMINAL_LOG_FLUSH_INTERVAL', 2.0)
        self.retention_days = retention_days or getattr(settings, 'TERMINAL_LOG_RETENTION_DAYS', 30)
        self._log_queue: List[TerminalLogEntry] = []
        self._dropped_lines = 0
        self._queue_lock = asyncio.Lock()
        self._background_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        self.logger = logging.getLogger("app.logs.terminal_storage")
    
    async def start(self):
        """Start the background batch processor unless it is already running."""
        if self._background_task is None or self._background_task.done():
            self._shutdown_event.clear()
            self._background_task = asyncio.create_task(self._batch_processor())
            self.logger.info(f"Terminal log storage service started with batch_size={self.batch_size}, flush_interval={self.flush_interval}s")
    
    async def stop(self):
        """Signal shutdown, flush the queue and wait for the batch processor to exit."""
        if self._background_task and not self._background_task.done():
            self._shutdown_event.set()
            await self._flush_batch()
            await self._background_task
            self.logger.info("Terminal log storage service stopped")
    
    async def store_terminal_log_async(self, log_entry: TerminalLogEntry):
        """Queue a terminal log entry, flushing immediately once the queue reaches batch size."""
        async with self._queue_lock:
            self._log_queue.append(log_entry)
            
            if len(self._log_queue) >= self.batch_size:
                await self._flush_batch_unlocked()
    
    async def store_terminal_logs_batch(self, log_entries: List[TerminalLogEntry]):
        """Insert entries in batch-size chunks, re-queueing the remainder when a chunk fails."""
        if not log_entries:
            return

        for i in range(0, len(log_entries), self.batch_size):
            chunk = log_entries[i:i + self.batch_size]
            try:
                await self._insert_terminal_logs_to_db(chunk)
            except _PoolNotReady:
                logger.debug("Database pool not ready; queueing %d terminal log lines", len(log_entries) - i)
                async with self._queue_lock:
                    self._requeue_unlocked(log_entries[i:])
                return
            except Exception:
                logger.error(
                    "Failed to store a terminal log chunk of %d lines; queueing the remainder",
                    len(chunk), exc_info=True,
                )
                async with self._queue_lock:
                    self._requeue_unlocked(log_entries[i:])
                return
    
    async def get_terminal_logs_filtered(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        source: Optional[str] = None,
        search_text: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Return a page of terminal logs matching the time, source and text filters, newest first, or [] on error."""
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
            
        if source:
            conditions.append(f"source = ${param_count}")
            params.append(source)
            param_count += 1
            
        if search_text:
            conditions.append(f"log_line ILIKE ${param_count}")
            params.append(f"%{search_text}%")
            param_count += 1
        
        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        
        query = f"""
            SELECT logid, timestamp_ist, log_line, source, created_at
            FROM etown_terminal_logs
            {where_clause}
            ORDER BY timestamp_ist DESC
            LIMIT ${param_count} OFFSET ${param_count + 1}
        """
        params.extend([limit, offset])
        
        try:
            rows = await Database.fetch(query, *params)
            return [dict(row) for row in rows]
        except Exception as e:
            self.logger.error(f"Failed to retrieve filtered terminal logs: {e}", exc_info=True)
            return []
    
    async def get_terminal_logs_count(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        source: Optional[str] = None,
        search_text: Optional[str] = None
    ) -> int:
        """Return the number of terminal logs matching the time, source and text filters, or 0 on error."""
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
            
        if source:
            conditions.append(f"source = ${param_count}")
            params.append(source)
            param_count += 1
            
        if search_text:
            conditions.append(f"log_line ILIKE ${param_count}")
            params.append(f"%{search_text}%")
            param_count += 1
        
        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        
        query = f"""
            SELECT COUNT(*) as count
            FROM etown_terminal_logs
            {where_clause}
        """
        
        try:
            result = await Database.fetchrow(query, *params)
            return result['count'] if result else 0
        except Exception as e:
            self.logger.error(f"Failed to count terminal logs: {e}", exc_info=True)
            return 0
    
    async def cleanup_old_terminal_logs(self):
        """Delete terminal logs older than the retention period, logging any failure."""
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        
        try:
            query = "DELETE FROM etown_terminal_logs WHERE timestamp_ist < $1"
            result = await Database.execute(query, cutoff_date)
            self.logger.info(f"Cleaned up old terminal logs before {cutoff_date}. Affected rows: {result}")
        except Exception as e:
            self.logger.error(f"Failed to cleanup old terminal logs: {e}", exc_info=True)
    
    def identify_log_source(self, log_line: str) -> str:
        """Return 'uvicorn' when a log line matches uvicorn's prefix or messages, otherwise 'app'."""
        log_line_stripped = log_line.strip()
        
        if re.match(r'^(INFO|WARNING|ERROR|DEBUG|CRITICAL):\s+', log_line_stripped):
            return 'uvicorn'
        
        if log_line_stripped.startswith('[APP]'):
            return 'app'
        
        uvicorn_patterns = [
            r'uvicorn\.run',
            r'Will watch for changes',
            r'Uvicorn running on',
            r'Started reloader process',
            r'Started server process',
            r'Waiting for application startup',
            r'Application startup complete',
            r'Waiting for application shutdown',
            r'Application shutdown complete',
            r'Finished server process',
            r'Shutting down'
        ]
        
        for pattern in uvicorn_patterns:
            if re.search(pattern, log_line_stripped, re.IGNORECASE):
                return 'uvicorn'
        
        return 'app'
    
    async def parse_and_store_log_line(self, log_line: str, timestamp_ist: Optional[datetime] = None):
        """Queue a non-blank log line as a terminal log entry tagged with its source, defaulting the timestamp to now."""
        if not log_line.strip():
            return
        
        if timestamp_ist is None:
            timestamp_ist = datetime.now(timezone.utc).replace(tzinfo=None)
        
        source = self.identify_log_source(log_line)
        
        entry = TerminalLogEntry(
            timestamp_ist=timestamp_ist,
            log_line=log_line.rstrip('\n\r'),
            source=source
        )
        
        await self.store_terminal_log_async(entry)
    
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
            await self._insert_terminal_logs_to_db(batch)
        except _PoolNotReady:
            logger.debug("Database pool not ready; holding %d terminal log lines", len(batch))
            self._requeue_unlocked(batch)
        except Exception:
            logger.error("Failed to flush a terminal log batch of %d lines", len(batch), exc_info=True)
            self._requeue_unlocked(batch)

    def _requeue_unlocked(self, batch: List[TerminalLogEntry]):
        """Put an unwritten batch back at the queue head up to the cap, counting and warning about dropped lines."""
        room = MAX_QUEUED_TERMINAL_LOGS - len(self._log_queue)
        if room <= 0:
            self._dropped_lines += len(batch)
        else:
            kept = batch[-room:]
            self._dropped_lines += len(batch) - len(kept)
            self._log_queue[:0] = kept

        if self._dropped_lines and self._dropped_lines % 1000 < len(batch):
            logger.warning(
                "Terminal log queue is full; %d lines dropped so far", self._dropped_lines
            )

    async def _insert_terminal_logs_to_db(self, log_entries: List[TerminalLogEntry]):
        """Insert terminal log entries into etown_terminal_logs with one executemany call.

        Raises:
            _PoolNotReady: When the shared pool is absent or closed.
        """
        if not log_entries:
            return

        pool = getattr(Database, "_pool", None)
        if pool is None or pool._closed:
            raise _PoolNotReady()

        query = """
            INSERT INTO etown_terminal_logs (
                timestamp_ist, log_line, source, created_at
            ) VALUES ($1::timestamp + interval '+05:30', $2, $3, $4)
        """

        current_time = datetime.now(timezone.utc)
        rows = [
            (
                entry.timestamp_ist.replace(tzinfo=None) if entry.timestamp_ist.tzinfo else entry.timestamp_ist,
                entry.log_line,
                entry.source,
                current_time,
            )
            for entry in log_entries
        ]

        async with pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
            await con.executemany(query, rows)


_terminal_log_storage_service: Optional[TerminalLogStorageService] = None


async def get_terminal_log_storage_service() -> TerminalLogStorageService:
    """Return the global terminal log storage service, creating it and starting its processor once the pool is ready."""
    global _terminal_log_storage_service
    
    if _terminal_log_storage_service is None:
        _terminal_log_storage_service = TerminalLogStorageService()
        
        try:
            if hasattr(Database, '_pool') and Database._pool and not Database._pool._closed:
                await _terminal_log_storage_service.start()
            else:
                pass
        except Exception:
            logger.warning("terminal log storage failed to start; terminal logs will queue until it does", exc_info=True)
    
    if _terminal_log_storage_service and (_terminal_log_storage_service._background_task is None or _terminal_log_storage_service._background_task.done()):
        try:
            if hasattr(Database, '_pool') and Database._pool and not Database._pool._closed:
                await _terminal_log_storage_service.start()
        except Exception:
            logger.warning("terminal log storage failed to restart", exc_info=True)
    
    return _terminal_log_storage_service


async def shutdown_terminal_log_storage_service():
    """Stop the global terminal log storage service, flushing queued logs, and discard it."""
    global _terminal_log_storage_service
    
    if _terminal_log_storage_service:
        await _terminal_log_storage_service.stop()
        _terminal_log_storage_service = None