"""Logs Package - groups log storage, terminal capture, database handlers and retention.

Package for the logging subsystem that ships application and terminal output to Postgres; it re-exports the log schema models, and its submodules hold storage, terminal capture, the async database handler, logging decorators and the retention worker.

Features:
    - Centralized log storage and retrieval services
    - Terminal log capture and monitoring
    - Database logging handlers with async support
    - Enhanced logging decorators and utilities
    - Comprehensive log schemas and models
    - Re-exports LogLevel, LogEntry, LogFilterRequest and LogFilterResponse from schema

Modules:
    schema: Log-related Pydantic models and schemas
    storage: Main log storage service with business intelligence
    terminal_storage: Terminal log storage and monitoring
    terminal_capture: Terminal log capture mechanisms
    terminal_capture_logging: Logging handler for terminal capture
    database_handler: Async database logging handler
    enhanced_logging: Performance and business context decorators
    retention_worker: Background sweep that prunes application and terminal logs past retention

Dependencies:
    - app.logs.schema: Log models re-exported at package level

Updates:
    14-Mar-2026: Initial creation with consolidated log functionality
"""

from .schema import LogLevel, LogEntry, LogFilterRequest, LogFilterResponse

__all__ = [
    "LogLevel",
    "LogEntry", 
    "LogFilterRequest",
    "LogFilterResponse",
]