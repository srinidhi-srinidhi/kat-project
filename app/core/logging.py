"""Logging Config - root logger setup with console, rotating file and database sinks.

Core logging module: configures the root logger with one plain-text formatter shared by a stdout handler, a rotating logs/app.log file handler and an optional batched database handler, repairs handlers after the server's dictConfig, and installs last-resort exception hooks.

Features:
    - Root-Logger Attachment: All handlers sit on the root logger, so library records reach every sink
    - Error Floor: WARNING/ERROR/CRITICAL always pass, whatever LOG_LEVEL is set to
    - Library Noise Control: Chatty third-party loggers pinned to WARNING
    - Server Config Repair: Rebuilds handlers and re-points uvicorn/gunicorn loggers after dictConfig
    - Console Logging: Always-on stdout handler (container log streams depend on it)
    - Rotating File Handler: Always-on file handler (10MB rotation, 3 backups) under logs/app.log
    - Database Logging: Optional queued/batched async handler with console fallback, tuned by DB_LOG_*
    - Recursion Guard: Records from the logging subsystem itself are kept out of the database handler
    - Last-Resort Hooks: Interpreter, thread, unraisable and asyncio exception hooks funnel into this logger
    - Warning Capture: warnings are re-emitted through the log pipeline
    - Function Context: Formatter includes function name and line number on every record
    - Idempotent Setup: setup_logger() and the hook installers are no-ops on repeat calls

Classes:
    ExcludeInternalLogs(): Filter that keeps app.logs.* records out of the database handler

Functions:
    setup_logger(): Initializes and configures the root logger (console/file/database handlers)
    restore_logging_after_server_config(): Repairs handlers and loggers after the server's dictConfig
    install_global_exception_hooks(): Installs interpreter, thread, unraisable and warning hooks
    install_asyncio_exception_handler(): Installs the event-loop exception handler

Dependencies:
    - logging / logging.handlers.RotatingFileHandler: Python standard library logging framework and file rotation
    - sys / threading / warnings: Interpreter, thread and warning hook targets
    - os: Log directory creation and path resolution
    - app.core.config: LOG_LEVEL, DEBUG, ENABLE_DATABASE_LOGGING and DB_LOG_* tuning settings
    - app.logs.database_handler: create_database_handler - lazily imported async DB log handler with console fallback

Updates:
    27-Feb-2026: Completely updated the documentation with a detailed module description and feature enhancement
    12-Mar-2026: Integrated database logging handler with async storage and fallback mechanisms
    26-Aug-2026: Root-logger handlers with last-resort hooks; utf-8 console stream; database handler rebuilt after dictConfig.
"""

import logging
import os
import sys
import threading
import warnings
from logging.handlers import RotatingFileHandler

from app.core.config import settings

LOG_FORMAT = '[APP] %(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'

INTERNAL_LOGGER_PREFIX = "app.logs."

_NOISY_LIBRARY_LOGGERS = (
    "asyncio",
    "asyncpg",
    "azure",
    "azure.core.pipeline.policies.http_logging_policy",
    "azure.identity",
    "httpcore",
    "httpx",
    "langchain",
    "langchain_core",
    "langgraph",
    "msal",
    "msrest",
    "openai",
    "PIL",
    "python_multipart",
    "urllib3",
    "watchfiles",
)

_SERVER_LOGGERS = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "gunicorn",
    "gunicorn.error",
    "gunicorn.access",
)

_hooks_installed = False


class ExcludeInternalLogs(logging.Filter):
    """Keeps the logging subsystem's own records out of the database handler.

    A failure writing a log row is itself logged; without this filter that record would be
    queued, fail the same way, and log again - an unbounded feedback loop. Internal records
    still reach the console and the rotating file.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Return False for records from the logging subsystem's own loggers."""
        return not record.name.startswith(INTERNAL_LOGGER_PREFIX)


def setup_logger():
    """Attach console, rotating-file and database handlers to the root logger once and return the app logger."""
    root_logger = logging.getLogger()
    app_logger = logging.getLogger("app")

    log_level = getattr(logging, settings.LOG_LEVEL, logging.DEBUG)
    if not isinstance(log_level, int):
        log_level = logging.DEBUG
    log_level = min(log_level, logging.WARNING)

    app_logger.setLevel(logging.NOTSET)
    app_logger.propagate = True

    if getattr(root_logger, "_etown_configured", False):
        return app_logger

    root_logger.setLevel(log_level)

    try:
        formatter = logging.Formatter(LOG_FORMAT)

        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(log_level)
        root_logger.addHandler(console_handler)

        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        log_dir = os.path.join(base_dir, 'logs')
        os.makedirs(log_dir, exist_ok=True)
        log_file_path = os.path.join(log_dir, 'app.log')

        file_handler = RotatingFileHandler(log_file_path, maxBytes=10 * 1024 * 1024, backupCount=3, encoding='utf-8')
        file_handler.setFormatter(formatter)
        file_handler.setLevel(log_level)
        root_logger.addHandler(file_handler)

        for name in _NOISY_LIBRARY_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)

        if getattr(settings, 'ENABLE_DATABASE_LOGGING', True):
            try:
                from app.logs.database_handler import create_database_handler

                db_handler = create_database_handler(
                    level=log_level,
                    fallback_to_console=settings.DB_LOG_FALLBACK_TO_CONSOLE and settings.DEBUG,
                    queue_size=settings.DB_LOG_QUEUE_SIZE,
                    flush_interval=settings.DB_LOG_FLUSH_INTERVAL,
                    batch_size=settings.DB_LOG_BATCH_SIZE
                )
                db_handler.addFilter(ExcludeInternalLogs())
                root_logger.addHandler(db_handler)

                root_logger._db_handler = db_handler
                app_logger.info(
                    f"Database logging enabled with queue_size={settings.DB_LOG_QUEUE_SIZE}, "
                    f"flush_interval={settings.DB_LOG_FLUSH_INTERVAL}s"
                )

            except Exception as db_error:
                print(f"Failed to setup database logging: {db_error}")
                app_logger.error(f"Failed to setup database logging: {db_error}", exc_info=True)

        root_logger._etown_configured = True

    except Exception as e:
        print(f"Failed to setup logging: {e}")

    return app_logger


def restore_logging_after_server_config():
    """Re-create a closed database handler, restore missing console and file handlers, and route server loggers to root."""
    root_logger = logging.getLogger()
    app_logger = logging.getLogger("app")

    log_level = getattr(logging, settings.LOG_LEVEL, logging.DEBUG)
    if not isinstance(log_level, int):
        log_level = logging.DEBUG
    log_level = min(log_level, logging.WARNING)

    db_handler = getattr(root_logger, "_db_handler", None)
    if db_handler is not None and not getattr(db_handler, "_enabled", True):
        root_logger.removeHandler(db_handler)
        root_logger._db_handler = None
        try:
            from app.logs.database_handler import create_database_handler

            fresh = create_database_handler(
                level=log_level,
                fallback_to_console=settings.DB_LOG_FALLBACK_TO_CONSOLE and settings.DEBUG,
                queue_size=settings.DB_LOG_QUEUE_SIZE,
                flush_interval=settings.DB_LOG_FLUSH_INTERVAL,
                batch_size=settings.DB_LOG_BATCH_SIZE,
            )
            fresh.addFilter(ExcludeInternalLogs())
            root_logger.addHandler(fresh)
            root_logger._db_handler = fresh
            app_logger.info("Database log handler re-created; the server's logging config had closed it")
        except Exception:
            app_logger.error("Could not re-create the database log handler", exc_info=True)

    have_console = any(type(h) is logging.StreamHandler for h in root_logger.handlers)
    have_file = any(isinstance(h, RotatingFileHandler) for h in root_logger.handlers)
    if not (have_console and have_file):
        formatter = logging.Formatter(LOG_FORMAT)
        if not have_console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            console_handler.setLevel(log_level)
            root_logger.addHandler(console_handler)
        if not have_file:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            log_dir = os.path.join(base_dir, "logs")
            os.makedirs(log_dir, exist_ok=True)
            file_handler = RotatingFileHandler(
                os.path.join(log_dir, "app.log"), maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8")
            file_handler.setFormatter(formatter)
            file_handler.setLevel(log_level)
            root_logger.addHandler(file_handler)
        app_logger.info("Re-attached logging handlers dropped by the server's logging config")

    root_logger.setLevel(log_level)
    for name in _NOISY_LIBRARY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    for name in _SERVER_LOGGERS:
        server_logger = logging.getLogger(name)
        for handler in list(server_logger.handlers):
            server_logger.removeHandler(handler)
        server_logger.propagate = True


def install_global_exception_hooks():
    """Install once the sys, thread, unraisable and warnings hooks that route failures into the logger."""
    global _hooks_installed
    if _hooks_installed:
        return
    _hooks_installed = True

    hook_logger = logging.getLogger("app.core.logging")
    previous_excepthook = sys.excepthook
    previous_threadhook = getattr(threading, "excepthook", None)
    previous_unraisablehook = getattr(sys, "unraisablehook", None)
    previous_showwarning = warnings.showwarning

    def _excepthook(exc_type, exc_value, exc_traceback):
        if not issubclass(exc_type, KeyboardInterrupt):
            try:
                hook_logger.critical(
                    "Unhandled exception in main thread: %s: %s",
                    exc_type.__name__, exc_value,
                    exc_info=(exc_type, exc_value, exc_traceback),
                )
            except Exception:
                pass
        previous_excepthook(exc_type, exc_value, exc_traceback)

    def _threadhook(args):
        if args.exc_type is not SystemExit:
            try:
                hook_logger.error(
                    "Unhandled exception in thread %s: %s: %s",
                    getattr(args.thread, "name", "unknown"), args.exc_type.__name__, args.exc_value,
                    exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
                )
            except Exception:
                pass
        if previous_threadhook is not None:
            previous_threadhook(args)

    def _unraisablehook(args):
        try:
            hook_logger.error(
                "Unraisable exception in %r: %s: %s",
                args.object, getattr(args.exc_type, "__name__", args.exc_type), args.exc_value,
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        except Exception:
            pass
        if previous_unraisablehook is not None:
            previous_unraisablehook(args)

    def _showwarning(message, category, filename, lineno, file=None, line=None):
        try:
            hook_logger.warning(
                "%s:%s: %s: %s", filename, lineno, getattr(category, "__name__", category), message
            )
        except Exception:
            previous_showwarning(message, category, filename, lineno, file, line)

    sys.excepthook = _excepthook
    if previous_threadhook is not None:
        threading.excepthook = _threadhook
    if previous_unraisablehook is not None:
        sys.unraisablehook = _unraisablehook
    warnings.showwarning = _showwarning


def install_asyncio_exception_handler(loop=None):
    """Set an event-loop exception handler that logs unhandled asyncio errors, doing nothing without a running loop."""
    import asyncio

    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

    loop_logger = logging.getLogger("app.core.logging")

    def _handler(running_loop, context):
        exception = context.get("exception")
        message = context.get("message") or "Unhandled error in the event loop"
        task = context.get("task") or context.get("future")
        try:
            loop_logger.error(
                "Unhandled asyncio error: %s%s",
                message,
                f" (task={task!r})" if task is not None else "",
                exc_info=exception if exception is not None else False,
            )
        except Exception:
            running_loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)


logger = setup_logger()
install_global_exception_hooks()
