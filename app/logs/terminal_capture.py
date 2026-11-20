"""Terminal Capture Service - captures console and log-file output for database storage.

Logging service started from the FastAPI lifespan: proxies stdout and stderr and optionally watches a log file, queueing each captured line and handing it to the terminal log storage service that writes etown_terminal_logs.

Features:
    - Real-time file monitoring: Watch log files for new terminal output
    - Live console capture: Redirect stdout/stderr to capture terminal logs
    - Dual capture mode: Support both file-based and console redirection monitoring
    - Batch processing: Efficiently process and store captured log lines
    - Background processing: Non-blocking capture using async background tasks
    - Error resilience: Graceful handling of file access and monitoring issues
    - Configurable monitoring: Adjustable capture interval
    - Optional watchdog: File monitoring is disabled when watchdog is not installed

Classes:
    LogFileHandler: watchdog FileSystemEventHandler that reprocesses the log file on modification events
    ConsoleCapture: stdout/stderr proxy that queues each line while still writing through to the console
    TerminalLogCapture: Main service for terminal output capture and monitoring

Functions:
    start_terminal_capture(): Create (if needed) and start the global TerminalLogCapture service
    stop_terminal_capture(): Gracefully shut down and clear the global capture service
    get_terminal_capture_service(): Return the current global capture service instance, or None

Dependencies:
    - asyncio: Asynchronous programming support for background monitoring
    - sys: System-specific parameters for stdout/stderr redirection
    - io: Input/output stream handling for console capture
    - watchdog: File system event monitoring for log file watching
    - threading: Thread management for file monitoring operations
    - app.logs.terminal_storage: Terminal log storage service integration
    - app.core.config: Configuration settings for capture behavior

Updates:
    14-Mar-2026: Initial creation with dual capture modes; moved from app.services.terminal_log_capture to app.logs.
    26-Aug-2026: Narrowed a bare except and logged the watchdog import guard.
"""

import asyncio
import sys
import io
import os
import threading
import logging
import time
import queue
from datetime import datetime, timezone
from typing import Optional, TextIO, List
from contextlib import redirect_stdout, redirect_stderr

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    logging.getLogger(__name__).warning("watchdog is not installed; terminal log-file monitoring is disabled")

    class FileSystemEventHandler:
        def on_modified(self, event): pass
    class Observer:
        def schedule(self, handler, path, recursive=False): pass
        def start(self): pass
        def stop(self): pass
        def join(self, timeout=None): pass
    WATCHDOG_AVAILABLE = False

from app.logs.terminal_storage import get_terminal_log_storage_service
from app.core.config import settings


class LogFileHandler(FileSystemEventHandler):
    """File system event handler for log file monitoring.
    
    Handles file modification events to detect new log entries
    and process them for storage in the terminal logs database.
    """
    
    def __init__(self, capture_service):
        """Bind the handler to the capture service whose log file it watches."""
        super().__init__()
        self.capture_service = capture_service
        self.logger = logging.getLogger("app.logs.terminal_capture.file_handler")
    
    def on_modified(self, event):
        """Schedule processing of new lines when the watched log file is modified."""
        if not event.is_directory and self.capture_service.log_file_path in event.src_path:
            try:
                asyncio.create_task(self.capture_service._process_file_changes())
            except Exception as e:
                self.logger.error(f"Error processing file changes: {e}", exc_info=True)


class ConsoleCapture:
    """Console output capture handler.
    
    Captures stdout and stderr output to store as terminal logs
    while maintaining normal console output functionality.
    """
    
    def __init__(self, capture_service, stream_name: str, original_stream: TextIO):
        """Wrap an original stream so its writes are also queued on the capture service."""
        self.capture_service = capture_service
        self.stream_name = stream_name
        self.original_stream = original_stream
        self.logger = logging.getLogger(f"app.logs.terminal_capture.console_{stream_name}")
    
    def write(self, data: str):
        """Write to the original stream, queue non-blank data for storage, and append debug lines to debug_capture.log."""
        try:
            with open("debug_capture.log", "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now()}] CAPTURE WRITE: {data[:50]}...\n")
        except Exception:
            pass
        
        if data.strip():
            try:
                self.original_stream.write(data)
                self.original_stream.flush()
                
                self.capture_service._console_queue.put(data.rstrip('\n\r'))
                
                debug_msg = f"Queued: {data[:30]}..."
                with open("debug_capture.log", "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.now()}] {debug_msg}\n")
                
            except Exception as e:
                self.original_stream.write(data)
                self.logger.error(f"Error capturing console output: {e}", exc_info=True)
        else:
            self.original_stream.write(data)
    
    def flush(self):
        """Flush the original stream."""
        self.original_stream.flush()
    
    def __getattr__(self, name):
        """Delegate other attributes to the original stream."""
        return getattr(self.original_stream, name)


class TerminalLogCapture:
    """Main service for terminal log capture and monitoring.
    
    Provides multiple capture modes including file monitoring and console
    redirection to comprehensively capture all terminal output.
    
    Args:
        log_file_path: Path to log file to monitor (None disables file monitoring).
        enable_console_capture: Whether to redirect stdout/stderr for capture.
        capture_interval: Seconds between file monitoring checks.
    """
    
    def __init__(
        self, 
        log_file_path: Optional[str] = None,
        enable_console_capture: bool = True,
        capture_interval: float = 1.0
    ):
        """Store the capture settings and initialise monitoring state and the console queue."""
        self.log_file_path = log_file_path
        self.enable_console_capture = enable_console_capture
        self.capture_interval = capture_interval
        
        self._observer: Optional[Observer] = None
        self._monitoring_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        self._last_file_size = 0
        self._last_file_position = 0
        
        self._original_stdout = None
        self._original_stderr = None
        self._console_capture_active = False
        
        self.logger = logging.getLogger("app.logs.terminal_capture")
        
        self._storage_service = None
        
        self._console_queue = queue.Queue()
    
    async def start(self):
        """Start file monitoring and console capture as configured plus the monitor loop, stopping and re-raising on failure."""
        try:
            self._storage_service = await get_terminal_log_storage_service()
            
            if self.log_file_path and os.path.exists(self.log_file_path) and WATCHDOG_AVAILABLE:
                await self._start_file_monitoring()
            
            if self.enable_console_capture:
                self._start_console_capture()
            
            self._shutdown_event.clear()
            self._monitoring_task = asyncio.create_task(self._monitor_loop())
            
            self.logger.info(f"Terminal log capture started - file: {bool(self.log_file_path)}, console: {self.enable_console_capture}")
            
        except Exception as e:
            self.logger.error(f"Failed to start terminal log capture: {e}", exc_info=True)
            await self.stop()
            raise
    
    async def stop(self):
        """Stop the file observer, restore the console streams, and await the monitor loop."""
        try:
            self._shutdown_event.set()
            
            if self._observer:
                self._observer.stop()
                self._observer.join(timeout=5)
                self._observer = None
            
            if self._console_capture_active:
                self._stop_console_capture()
            
            if self._monitoring_task and not self._monitoring_task.done():
                await self._monitoring_task
            
            self.logger.info("Terminal log capture stopped")
            
        except Exception as e:
            self.logger.error(f"Error stopping terminal log capture: {e}", exc_info=True)
    
    async def _start_file_monitoring(self):
        """Start a watchdog observer on the log file's directory, reading only content added from now on."""
        if not WATCHDOG_AVAILABLE:
            self.logger.warning("Watchdog library not available - file monitoring disabled")
            return
        
        try:
            if os.path.exists(self.log_file_path):
                stat = os.stat(self.log_file_path)
                self._last_file_size = stat.st_size
                self._last_file_position = stat.st_size
            
            event_handler = LogFileHandler(self)
            self._observer = Observer()
            self._observer.schedule(
                event_handler, 
                os.path.dirname(self.log_file_path) or '.', 
                recursive=False
            )
            self._observer.start()
            
            self.logger.info(f"File monitoring started for: {self.log_file_path}")
            
        except Exception as e:
            self.logger.error(f"Failed to start file monitoring: {e}", exc_info=True)
    
    def _start_console_capture(self):
        """Replace sys.stdout and sys.stderr with ConsoleCapture wrappers."""
        try:
            self._original_stdout = sys.stdout
            self._original_stderr = sys.stderr
            
            stdout_capture = ConsoleCapture(self, "stdout", self._original_stdout)
            stderr_capture = ConsoleCapture(self, "stderr", self._original_stderr)
            
            sys.stdout = stdout_capture
            sys.stderr = stderr_capture
            
            self._console_capture_active = True
            self.logger.info("Console capture started")
            
        except Exception as e:
            self.logger.error(f"Failed to start console capture: {e}", exc_info=True)
            self._stop_console_capture()
    
    def _stop_console_capture(self):
        """Stop console output capture and restore original streams."""
        try:
            if self._console_capture_active:
                if self._original_stdout:
                    sys.stdout = self._original_stdout
                if self._original_stderr:
                    sys.stderr = self._original_stderr
                
                self._console_capture_active = False
                self.logger.info("Console capture stopped")
                
        except Exception as e:
            self.logger.error(f"Error stopping console capture: {e}", exc_info=True)
    
    async def _monitor_loop(self):
        """Drain the console queue and poll the log file every capture interval until shutdown."""
        while not self._shutdown_event.is_set():
            try:
                await self._process_console_queue()
                
                await asyncio.wait_for(
                    self._shutdown_event.wait(), 
                    timeout=self.capture_interval
                )
                break
            except asyncio.TimeoutError:
                if self.log_file_path and os.path.exists(self.log_file_path):
                    await self._check_file_changes()
    
    async def _process_console_queue(self):
        """Store every line currently in the console queue."""
        try:
            processed_count = 0
            while True:
                try:
                    line = self._console_queue.get_nowait()
                    await self._process_log_line(line)
                    processed_count += 1
                    self._console_queue.task_done()
                except queue.Empty:
                    break
            
            if processed_count > 0:
                self.logger.info(f"Processed {processed_count} console queue items")
            
        except Exception as e:
            self.logger.error(f"Error processing console queue: {e}", exc_info=True)
    
    async def _check_file_changes(self):
        """Process new log-file content when the file has grown since the last check."""
        try:
            current_size = os.path.getsize(self.log_file_path)
            
            if current_size > self._last_file_size:
                self._last_file_size = current_size
                await self._process_file_changes()
        except Exception as e:
            self.logger.error(f"Error checking file changes: {e}", exc_info=True)
    
    async def _process_file_changes(self):
        """Read lines appended to the log file since the last position and store each one."""
        try:
            if not os.path.exists(self.log_file_path):
                return
            
            with open(self.log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                f.seek(self._last_file_position)
                
                new_lines = f.readlines()
                
                self._last_file_position = f.tell()
                
                for line in new_lines:
                    await self._process_log_line(line.rstrip('\n\r'))
                
        except Exception as e:
            self.logger.error(f"Error processing file changes: {e}", exc_info=True)
    
    async def _process_log_line(self, line: str):
        """Parse and store one non-blank terminal line through the storage service."""
        if not line.strip() or not self._storage_service:
            return
        
        try:
            self.logger.debug("Storing a captured terminal line (%d chars)", len(line))
            
            await self._storage_service.parse_and_store_log_line(line)
        except Exception as e:
            self.logger.error(f"Error storing log line: {e}", exc_info=True)


_terminal_capture_service: Optional[TerminalLogCapture] = None


async def start_terminal_capture(
    log_file_path: Optional[str] = None,
    enable_console_capture: bool = True,
    capture_interval: float = 1.0
) -> TerminalLogCapture:
    """Create the global terminal capture service if needed, start it unless running, and return it.

    Args:
        log_file_path: Log file to monitor; None disables file monitoring.
        enable_console_capture: Whether to redirect stdout and stderr for capture.
        capture_interval: Seconds between file monitoring checks.
    """
    global _terminal_capture_service
    
    if _terminal_capture_service is None:
        _terminal_capture_service = TerminalLogCapture(
            log_file_path=log_file_path,
            enable_console_capture=enable_console_capture,
            capture_interval=capture_interval
        )
    
    if _terminal_capture_service._monitoring_task is None or _terminal_capture_service._monitoring_task.done():
        await _terminal_capture_service.start()
    
    return _terminal_capture_service


async def stop_terminal_capture():
    """Stop and clear the global terminal capture service."""
    global _terminal_capture_service
    
    if _terminal_capture_service:
        await _terminal_capture_service.stop()
        _terminal_capture_service = None


def get_terminal_capture_service() -> Optional[TerminalLogCapture]:
    """Return the global terminal capture service, or None if not started."""
    return _terminal_capture_service