"""Logging Terminal Capture Worker - root-logger handler that ships log records to etown_terminal_logs.

Background logging worker started from the app lifespan that attaches one filtered handler to the root logger, queues each formatted record thread-safely and forwards the queue to the terminal log storage service, draining what remains on shutdown.

Features:
    - Logging Handler Approach: Captures all logs through Python logging system
    - Reliable Capture: More reliable than stdout/stderr redirection
    - Queue-based Processing: Thread-safe queuing for async processing
    - Root Logger Capture: one handler on the root logger, filtered by ExcludeInternalLogs
    - Application Log Capture: Enhanced to capture detailed app logs with function names and line numbers
    - Formatted Output: Maintains proper [APP] prefix format matching console logs
    - Background Processing: Async monitoring loop for queue processing
    - Error Resilience: Graceful handling of logging errors without breaking app
    - Shutdown Drain: remaining queued logs are flushed on stop

Classes:
    TerminalLogHandler: Custom logging handler for log record capture
    LoggingBasedTerminalCapture: Main capture service using logging approach

Functions:
    start_logging_terminal_capture(): Initialize logging-based capture service
    stop_logging_terminal_capture(): Gracefully shutdown logging capture

Dependencies:
    - asyncio: Asynchronous programming support
    - logging: Python standard logging framework for handler integration
    - queue: Thread-safe queue for log processing
    - app.logs.terminal_storage: Terminal log storage service integration
    - app.core.logging: ExcludeInternalLogs filter

Updates:
    14-Mar-2026: Initial creation with logging handler approach
    26-Aug-2026: Capture on the root logger only (it was duplicating rows) and drain the queue on shutdown.
"""

import asyncio
import logging
import queue
import threading
from datetime import datetime, timezone
from typing import Optional

from app.core.logging import ExcludeInternalLogs
from app.logs.terminal_storage import get_terminal_log_storage_service

MAX_QUEUED_RECORDS = 20000


class TerminalLogHandler(logging.Handler):
    """Custom logging handler that captures log records for terminal storage.
    
    This handler intercepts all log records that pass through the logging
    system and queues them for asynchronous storage in the terminal logs table.
    """
    
    def __init__(self, capture_service, level=logging.NOTSET):
        """Create the handler with an empty record queue, set to capture DEBUG and above."""
        super().__init__(level)
        self.capture_service = capture_service
        self._log_queue = queue.Queue()
        self._dropped = 0
        self.setLevel(logging.DEBUG)
        
    def emit(self, record: logging.LogRecord):
        """Queue a formatted record for storage, dropping it and periodically warning when the queue is full."""
        try:
            formatted_message = self.format(record)
            
            timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).replace(tzinfo=None)
            
            if self._log_queue.qsize() >= MAX_QUEUED_RECORDS:
                self._dropped += 1
                if self._dropped % 1000 == 1:
                    self.capture_service.logger.warning(
                        "Terminal capture queue is full; %d records dropped so far", self._dropped
                    )
                return

            self._log_queue.put({
                'message': formatted_message,
                'timestamp': timestamp,
                'level': record.levelname,
                'logger_name': record.name
            })
            
        except Exception:
            self.handleError(record)
    
    def get_queued_logs(self):
        """Drain and return every queued log entry without blocking."""
        logs = []
        while True:
            try:
                log_entry = self._log_queue.get_nowait()
                logs.append(log_entry)
            except queue.Empty:
                break
        return logs


class LoggingBasedTerminalCapture:
    """Terminal log capture using logging handler approach.
    
    This approach is more reliable than stdout/stderr redirection
    as it captures all logs that go through the Python logging system.
    """
    
    def __init__(self):
        """Initialise an idle capture service with no handler or monitor task."""
        self._storage_service = None
        self._log_handler = None
        self._monitoring_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        self.logger = logging.getLogger("app.logs.terminal_capture.logging_based")
        
    async def start(self):
        """Attach the capture handler to the root logger and start the monitor loop, stopping and re-raising on failure."""
        try:
            self._storage_service = await get_terminal_log_storage_service()

            self._log_handler = TerminalLogHandler(self)

            formatter = logging.Formatter('[APP] %(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s')
            self._log_handler.setFormatter(formatter)
            self._log_handler.addFilter(ExcludeInternalLogs())

            logging.getLogger().addHandler(self._log_handler)

            self._shutdown_event.clear()
            self._monitoring_task = asyncio.create_task(self._monitor_loop())

            self.logger.info("Logging-based terminal capture started on the root logger")

        except Exception:
            self.logger.error("Failed to start logging-based terminal capture", exc_info=True)
            await self.stop()
            raise

    async def stop(self):
        """Stop the monitor loop, detach the handler and store any records still queued."""
        try:
            self._shutdown_event.set()

            if self._monitoring_task and not self._monitoring_task.done():
                try:
                    await asyncio.wait_for(self._monitoring_task, timeout=10)
                except asyncio.TimeoutError:
                    self.logger.warning("Terminal capture monitor did not exit within 10s; cancelling it")
                    self._monitoring_task.cancel()
                except asyncio.CancelledError:
                    self.logger.debug("Terminal capture monitor cancelled during shutdown")

            if self._log_handler:
                logging.getLogger().removeHandler(self._log_handler)
                for name in ("app", "uvicorn", "uvicorn.access"):
                    logging.getLogger(name).removeHandler(self._log_handler)

                await self._process_log_queue()
                self._log_handler = None

            self.logger.info("Logging-based terminal capture stopped")

        except Exception:
            self.logger.error("Error stopping logging-based terminal capture", exc_info=True)

    async def _monitor_loop(self):
        """Process the handler queue every second until shutdown, then process it once more."""
        while not self._shutdown_event.is_set():
            try:
                await self._process_log_queue()

                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=1.0
                )
                break
            except asyncio.TimeoutError:
                pass

        await self._process_log_queue()

    async def _process_log_queue(self):
        """Parse and store every record currently in the handler queue, logging errors."""
        if not self._log_handler or not self._storage_service:
            return
        
        try:
            logs = self._log_handler.get_queued_logs()
            
            for log_entry in logs:
                log_line = log_entry['message']
                timestamp = log_entry['timestamp']
                
                await self._storage_service.parse_and_store_log_line(log_line, timestamp)
            
            if logs:
                self.logger.debug(f"Processed {len(logs)} queued log entries")
                
        except Exception as e:
            self.logger.error(f"Error processing log queue: {e}", exc_info=True)


_logging_terminal_capture_service: Optional[LoggingBasedTerminalCapture] = None


async def start_logging_terminal_capture() -> LoggingBasedTerminalCapture:
    """Create the global capture service if needed, start it and return it."""
    global _logging_terminal_capture_service
    
    if _logging_terminal_capture_service is None:
        _logging_terminal_capture_service = LoggingBasedTerminalCapture()
    
    await _logging_terminal_capture_service.start()
    return _logging_terminal_capture_service


async def stop_logging_terminal_capture():
    """Stop and discard the global capture service, if running."""
    global _logging_terminal_capture_service
    
    if _logging_terminal_capture_service:
        await _logging_terminal_capture_service.stop()
        _logging_terminal_capture_service = None