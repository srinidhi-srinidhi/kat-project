"""Debug Perf Endpoints - HTTP routes for database latency probes and runtime settings.

HTTP layer mounted at the /api root: GET /api/db-latency-test times sequential and parallel SELECT 1 queries on the shared pool, and GET /api/runtime-settings reports non-secret pool, worker and environment settings. Neither route requires the bearer token.

Features:
    - Database Latency Testing: Sequential and parallel query timing measurements
    - Connection Pool Monitoring: Real-time pool size and connection metrics
    - Runtime Configuration: Safe exposure of application configuration settings
    - Performance Metrics: Detailed timing analysis for troubleshooting
    - Pool Health Checking: Connection pool status and utilization monitoring
    - Environment Validation: Runtime environment and process information
    - Gunicorn Integration: Web server configuration and worker process details
    - Safe Data Exposure: Configuration details without sensitive information

Classes:
    No classes defined (module exposes two diagnostic endpoint functions)

Endpoints:
    GET /db-latency-test: Execute database performance tests and return timing metrics
    GET /runtime-settings: Retrieve safe runtime configuration and pool status

Functions:
    db_latency_test(): Execute various database query patterns and measure performance
    runtime_settings(): Collect and return safe runtime configuration information

Dependencies:
    - fastapi: API framework and routing
    - app.db.client: Database operations and connection pool management
    - asyncio: Concurrent operations for parallel query testing
    - os: System information and environment variable access
    - socket: Hostname information for process identification
    - datetime: Timezone-aware timestamp generation
    - app.core.config: Application configuration management
    - app.core.logging: Error logging for failed latency probes

Updates:
    11-Mar-2026: Added comprehensive module docstring and function documentation
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""
from fastapi import APIRouter
import time
from app.db.client import Database
import asyncio
import os
import socket
from datetime import datetime, timezone
from app.core.config import settings
from app.core.logging import logger

router = APIRouter()

@router.get("/db-latency-test")
async def db_latency_test():
    """Handle GET /api/db-latency-test: time two serial and six parallel SELECT 1 queries and report pool stats."""
    start_total = time.time()
    
    try:
        t0 = time.time()
        await Database.fetch("SELECT 1")
        seq1_ms = round((time.time() - t0) * 1000, 2)
        
        t0 = time.time()
        await Database.fetch("SELECT 1")
        seq2_ms = round((time.time() - t0) * 1000, 2)
    except Exception as e:
        logger.error("[debug_perf] sequential DB latency probe failed", exc_info=True)
        seq1_ms = f"Error: {e}"
        seq2_ms = 0
        
    try:
        t0 = time.time()
        tasks = [Database.fetch("SELECT 1") for _ in range(6)]
        await asyncio.gather(*tasks)
        parallel_6_ms = round((time.time() - t0) * 1000, 2)
    except Exception as e:
        logger.error("[debug_perf] parallel DB latency probe failed", exc_info=True)
        parallel_6_ms = f"Error: {e}"

    pool_stats = {}
    if Database._pool:
        pool_stats = {
            "size": Database._pool.get_size(),
            "idle": Database._pool.get_idle_size(),
            "max": Database._pool.get_max_size(),
            "min": Database._pool.get_min_size()
        }

    return {
        "azure_db_latency": {
            "serial_query_1_ms": seq1_ms,
            "serial_query_2_ms": seq2_ms,
            "parallel_6_queries_ms": parallel_6_ms
        },
        "pool_metrics": pool_stats,
        "total_test_ms": round((time.time() - start_total) * 1000, 2)
    }


@router.get("/runtime-settings")
async def runtime_settings():
    """Handle GET /api/runtime-settings: return process, gunicorn env, DB pool config and live pool state, without secrets."""
    pool_runtime = {
        "initialized": False,
        "is_closed": True,
    }

    if Database._pool:
        pool_runtime = {
            "initialized": True,
            "is_closed": Database._pool._closed,
            "size": Database._pool.get_size(),
            "idle": Database._pool.get_idle_size(),
            "max": Database._pool.get_max_size(),
            "min": Database._pool.get_min_size(),
        }

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "process": {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
        },
        "gunicorn": {
            "web_concurrency_env": os.getenv("WEB_CONCURRENCY"),
            "timeout": os.getenv("GUNICORN_TIMEOUT"),
            "graceful_timeout": os.getenv("GUNICORN_GRACEFUL_TIMEOUT"),
            "keepalive": os.getenv("GUNICORN_KEEPALIVE"),
            "max_requests": os.getenv("GUNICORN_MAX_REQUESTS"),
            "max_requests_jitter": os.getenv("GUNICORN_MAX_REQUESTS_JITTER"),
        },
        "db_pool_config": {
            "web_concurrency_effective": settings.WEB_CONCURRENCY,
            "pool_total_max_conn": settings.DB_POOL_TOTAL_MAX_CONN,
            "pool_min_size": settings.DB_POOL_MIN_SIZE,
            "pool_max_size": settings.DB_POOL_MAX_SIZE,
            "pool_connect_timeout_seconds": settings.DB_POOL_CONNECT_TIMEOUT_SECONDS,
            "pool_acquire_timeout_seconds": settings.DB_POOL_ACQUIRE_TIMEOUT_SECONDS,
            "pool_command_timeout_seconds": settings.DB_POOL_COMMAND_TIMEOUT_SECONDS,
            "pool_idle_lifetime_seconds": settings.DB_POOL_IDLE_LIFETIME_SECONDS,
            "pool_max_queries": settings.DB_POOL_MAX_QUERIES,
            "statement_cache_size": settings.DB_POOL_STATEMENT_CACHE_SIZE,
            "statement_timeout_ms": settings.DB_STATEMENT_TIMEOUT_MS,
        },
        "db_pool_runtime": pool_runtime,
    }
