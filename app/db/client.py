"""Database Client - shared asyncpg pool and query methods for Azure PostgreSQL.

Data-access layer under every domain util: the Database classmethods run queries on one shared asyncpg pool, retry transient errors with exponential backoff (fetch_once skips retry for expensive reads), open LISTEN/NOTIFY connections outside the pool and refuse requests with a 503 during shutdown.

Features:
    - Connection Pooling: Async connection pool management with configurable sizing
    - Retry Mechanism: Exponential backoff retry decorator for transient database errors
    - Performance Tracking: Automatic timing metrics for all database operations
    - Error Handling: Robust error handling with detailed logging and graceful degradation
    - Thread Safety: Thread-safe initialization with async locks
    - Connection Lifecycle: Proper connection initialization, management, and cleanup
    - Query Parallelization: Support for concurrent query execution
    - Health Monitoring: Database ping functionality for availability checks
    - No-retry Reads: fetch_once() for expensive read paths
    - LISTEN/NOTIFY: Dedicated listener connections outside the pool
    - Shutdown Gate: assert_available() raises a 503 while the pool is closing
    - Read-only Pool: ReadOnlyDatabase runs Chaatie's generated queries as a separate low-privilege login

Classes:
    DatabaseUnavailableError: HTTPException 503 for a request that raced pool shutdown
    Database: Main database client class with async connection pooling and operation methods
    DataQueryCostError: A generated query's planned cost exceeded the data-query ceiling
    ReadOnlyDatabase: Lazy read-only pool running caller-scoped generated SELECTs with a cost check

Functions:
    with_retry(): Exponential backoff retry decorator for transient database errors
    get_last_n_days_range(): Utility function for date range calculations

Dependencies:
    - asyncpg: PostgreSQL async database adapter, connection pool and Record results
    - tenacity: Exponential-backoff retry used by with_retry()
    - app.core.config: DB host/credentials and pool-sizing settings
    - fastapi: HTTPException base for DatabaseUnavailableError
    - app.utils.performance: optional track_performance_metrics() used by with_retry()
    - app.logs.enhanced_logging: optional db_query_monitor() decorator on execute_query()

Updates:
    27-Feb-2026: Completely updated the documentation with a detailed module description
    09-Mar-2026: Added EtownDB with a separate pool; moved user SQL to app.etown.user_utils.
    11-Mar-2026: Configurable EtownDB pool sizing; lifecycle methods became shared-pool wrappers.
    09-Jul-2026: Added open_listener_connection() for LISTEN/NOTIFY (event-driven queue runner)
    03-Aug-2026: Added fetch_once() - a no-retry fetch for expensive read paths.
    26-Aug-2026: Logged the optional-import fallbacks for performance tracking and query monitoring.
    16-Sep-2026: Added Database.assert_available() and _shutting_down for a 503 during pool close.
    08-Oct-2026: Removed LangSmith tracing.
    27-Oct-2026: Added ReadOnlyDatabase and DataQueryCostError for Chaatie data queries.
"""

import asyncpg
import json
from fastapi import HTTPException
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

import asyncio
import logging
import time
from typing import Any, List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

from app.core.config import settings

try:
    from app.utils.performance import track_performance_metrics as _track_performance_metrics
except Exception:
    logger.warning("Performance tracking unavailable; DB timings will only be logged at debug level", exc_info=True)
    _track_performance_metrics = None

try:
    from app.logs.enhanced_logging import db_query_monitor
except ImportError:
    logger.warning("db_query_monitor unavailable; DB query monitoring is disabled", exc_info=True)
    def db_query_monitor(query_type: str = "unknown"):
        def decorator(func):
            return func
        return decorator


_TRANSIENT_ERRORS = (asyncpg.PostgresError, asyncio.TimeoutError)

def with_retry() -> Any:
    """Return a decorator that retries transient DB errors with exponential backoff and records each call's duration."""
    
    def retry_decorator(func):
        @retry(
            retry=retry_if_exception_type(_TRANSIENT_ERRORS),
            wait=wait_exponential(multiplier=0.2, min=0.2, max=5),
            stop=stop_after_attempt(5),
            reraise=True,
        )
        async def wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            operation_name = f"db_{func.__name__}"
            
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                duration_ms = (time.perf_counter() - start_time) * 1000
                
                if _track_performance_metrics is not None:
                    _track_performance_metrics("database", operation_name, duration_ms)
                else:
                    logger.debug(f"[DB_PERF] {operation_name}: {duration_ms:.1f}ms")
        
        return wrapper
    
    return retry_decorator

def get_last_n_days_range(n):
    """Return a (start, end) tuple spanning the last n days up to now."""
    end = datetime.now()
    start = end - timedelta(days=n)
    return start, end

class DatabaseUnavailableError(HTTPException):
    """503 for a request that arrived while the connection pool was going away.

    Subclasses HTTPException so the ``except HTTPException: raise`` line the endpoints and
    service methods already have re-raises it untouched, instead of the blanket
    ``except Exception`` underneath relabelling it a 500. A request that lost a race with
    shutdown has not failed - it needs retrying against the next process, which is what 503
    says and what 500 does not.
    """

    def __init__(self, detail: str = "Database is shutting down. Retry the request.") -> None:
        """Build a 503 HTTPException with the given detail."""
        super().__init__(status_code=503, detail=detail)


class Database:
    _pool: asyncpg.Pool | None = None
    _init_lock: asyncio.Lock = asyncio.Lock()
    _shutting_down: bool = False
    DEFAULT_SCHEMA: str = settings.DEFAULT_SCHEMA
    _acquire_timeout_seconds: float = settings.DB_POOL_ACQUIRE_TIMEOUT_SECONDS

    @classmethod
    def assert_available(cls) -> None:
        """Raise a 503 before any acquire when the pool is shutting down or mid-close.

        Raises:
            DatabaseUnavailableError: When close() has started or the pool is mid-close.
        """
        pool = cls._pool
        if cls._shutting_down or (pool is not None and getattr(pool, "_closing", False)):
            raise DatabaseUnavailableError()

    @classmethod
    def _validate_db_settings(cls) -> None:
        """Raise RuntimeError naming any missing host, database, user or password setting."""
        required = {
            "HOST": settings.DBHOST,
            "DATABASE": settings.DATABASENAME,
            "USER": settings.DBUSERNAME,
            "PASSWORD": settings.DBPASSWORD,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"Missing required database settings: {', '.join(missing)}")

    @classmethod
    async def _configure_connection(cls, connection: asyncpg.Connection) -> None:
        """Set statement_timeout on a new pooled connection when DB_STATEMENT_TIMEOUT_MS is positive."""
        if settings.DB_STATEMENT_TIMEOUT_MS > 0:
            await connection.execute(f"SET statement_timeout = {int(settings.DB_STATEMENT_TIMEOUT_MS)}")

    @classmethod
    async def init(
        cls,
        *,
        min_size: Optional[int] = None,
        max_size: Optional[int] = None,
        stmt_cache: Optional[int] = None,
        command_timeout: Optional[float] = None,
        connect_timeout: Optional[float] = None,
        idle_lifetime: Optional[float] = None,
        max_queries: Optional[int] = None,
        acquire_timeout: Optional[float] = None,
    ) -> None:
        """Create the shared asyncpg pool under the init lock if it is missing or closed; None arguments use settings.

        Args:
            min_size: Minimum pool connections, clamped to max_size.
            max_size: Maximum pool connections.
            stmt_cache: Statement cache size per connection.
            command_timeout: Per-query timeout in seconds.
            connect_timeout: Connection establishment timeout in seconds.
            idle_lifetime: Seconds before an idle connection is recycled.
            max_queries: Queries per connection before it is replaced.
            acquire_timeout: Seconds to wait when acquiring a pooled connection.

        Raises:
            RuntimeError: When required database settings are missing.
        """
        logger.info("Acquiring DB init lock...")
        async with cls._init_lock:
            if cls._pool is None or cls._pool._closed: 
                logger.info("Initializing DB pool...")
                try:
                    cls._validate_db_settings()

                    effective_min_size = settings.DB_POOL_MIN_SIZE if min_size is None else max(1, min_size)
                    effective_max_size = settings.DB_POOL_MAX_SIZE if max_size is None else max(1, max_size)
                    if effective_min_size > effective_max_size:
                        effective_min_size = effective_max_size

                    effective_stmt_cache = settings.DB_POOL_STATEMENT_CACHE_SIZE if stmt_cache is None else max(0, stmt_cache)
                    effective_command_timeout = settings.DB_POOL_COMMAND_TIMEOUT_SECONDS if command_timeout is None else command_timeout
                    effective_connect_timeout = settings.DB_POOL_CONNECT_TIMEOUT_SECONDS if connect_timeout is None else connect_timeout
                    effective_idle_lifetime = settings.DB_POOL_IDLE_LIFETIME_SECONDS if idle_lifetime is None else idle_lifetime
                    effective_max_queries = settings.DB_POOL_MAX_QUERIES if max_queries is None else max_queries
                    cls._acquire_timeout_seconds = settings.DB_POOL_ACQUIRE_TIMEOUT_SECONDS if acquire_timeout is None else acquire_timeout

                    cls._pool = await asyncpg.create_pool(
                        host=settings.DBHOST,
                        port=int(settings.DBPORT),
                        database=settings.DATABASENAME,
                        user=settings.DBUSERNAME,
                        password=settings.DBPASSWORD,
                        ssl="require",
                        timeout=effective_connect_timeout,
                        command_timeout=effective_command_timeout,
                        min_size=effective_min_size,
                        max_size=effective_max_size,
                        max_inactive_connection_lifetime=effective_idle_lifetime,
                        max_queries=effective_max_queries,
                        statement_cache_size=effective_stmt_cache,
                        init=cls._configure_connection,
                        server_settings={"application_name": "etown_agents_api"},
                    )
                    cls._shutting_down = False
                    logger.info(
                        "Connected to Postgres - pool %s-%s (acquire_timeout=%ss, command_timeout=%ss)",
                        effective_min_size,
                        effective_max_size,
                        cls._acquire_timeout_seconds,
                        effective_command_timeout,
                    )
                except Exception as e:
                    logger.critical(f"Failed to initialize DB pool: {e}", exc_info=True)
                    raise

    @classmethod
    async def close(cls) -> None:
        """Mark the pool as shutting down and close it under the init lock, logging rather than raising errors."""
        logger.info("Closing DB pool...")
        cls._shutting_down = True
        async with cls._init_lock:
            if cls._pool and not cls._pool._closed:
                try:
                    await cls._pool.close()
                    cls._pool = None
                    logger.info("Pool closed")
                except Exception as e:
                    logger.error(f"Error closing DB pool: {e}", exc_info=True)
            else:
                logger.info("DB pool already closed or not initialized.")

    @classmethod
    async def open_listener_connection(cls) -> asyncpg.Connection:
        """Open a standalone connection outside the pool for LISTEN/NOTIFY; the caller owns and closes it.

        Raises:
            RuntimeError: When required database settings are missing.
        """
        cls._validate_db_settings()
        return await asyncpg.connect(
            host=settings.DBHOST,
            port=int(settings.DBPORT),
            database=settings.DATABASENAME,
            user=settings.DBUSERNAME,
            password=settings.DBPASSWORD,
            ssl="require",
            timeout=settings.DB_POOL_CONNECT_TIMEOUT_SECONDS,
            server_settings={"application_name": "etown_agents_api_listener"},
        )

    @classmethod
    @with_retry()
    async def fetch(cls, query:str, *args) -> list[asyncpg.Record]:
        """Run a query with transient-error retry and return all records.

        Raises:
            DatabaseUnavailableError: When the pool is shutting down.
            RuntimeError: When the pool is not initialized or is closed.
        """
        cls.assert_available()
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
        async with cls._pool.acquire(timeout=cls._acquire_timeout_seconds) as con:
            return await con.fetch(query, *args)

    @classmethod
    async def fetch_once(cls, query: str, *args) -> list[asyncpg.Record]:
        """Run a query once, without retry, and return all records.

        Raises:
            DatabaseUnavailableError: When the pool is shutting down.
            RuntimeError: When the pool is not initialized or is closed.
            PostgresError: Propagated as-is so the caller can degrade instead of re-running.
        """
        cls.assert_available()
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
        async with cls._pool.acquire(timeout=cls._acquire_timeout_seconds) as con:
            return await con.fetch(query, *args)

    @classmethod
    @with_retry()
    async def fetchrow(cls, query:str, *args) -> asyncpg.Record|None:
        """Run a query with transient-error retry and return the first record, or None."""
        cls.assert_available()
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
        async with cls._pool.acquire(timeout=cls._acquire_timeout_seconds) as con:
            return await con.fetchrow(query, *args)

    @classmethod
    @with_retry()
    async def execute(cls, query:str, *args) -> str:
        """Run a SQL command with transient-error retry and return its status string, such as UPDATE 5."""
        cls.assert_available()
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
        async with cls._pool.acquire(timeout=cls._acquire_timeout_seconds) as con:
            return await con.execute(query, *args)
    
    @classmethod
    @with_retry()
    async def fetchval(cls, query:str, *args):
        """Run a query with transient-error retry and return the first column of the first row, or None."""
        cls.assert_available()
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
        async with cls._pool.acquire(timeout=cls._acquire_timeout_seconds) as con:
            return await con.fetchval(query, *args)
    
    @classmethod
    @with_retry()
    async def execute_queries(cls, queries: List[str], *args) -> List[list]:
        """Run several queries concurrently with the same arguments and return their dict rows in order.

        Args:
            queries: SQL statements to run in parallel.
            *args: Parameters passed to every query.

        Returns:
            One list of dict rows per query, or ["Error: ..."] on a non-pool failure.

        Raises:
            RuntimeError: When the pool is not initialized or is closed.
        """
        try:
            tasks = [cls.execute_query(query, *args) for query in queries]
            results = await asyncio.gather(*tasks)
            logger.info(f"Successfully executed {len(queries)} queries in parallel.")
            return results
        except RuntimeError as e:
            logger.error(f"Database pool error when executing queries: {e}", exc_info=True)
            raise 
        except Exception as e:
            logger.error(f"Error executing queries: {e}", exc_info=True)
            return [f"Error: {e}"]

    @classmethod
    @db_query_monitor('generic_sql')
    async def execute_query(cls, query: str, *args) -> list:
        """Run one query without retry and return its rows as a list of dicts.

        Raises:
            DatabaseUnavailableError: When the pool is shutting down.
            RuntimeError: When the pool is not initialized or is closed.
        """
        cls.assert_available()
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
        async with cls._pool.acquire(timeout=cls._acquire_timeout_seconds) as con:
            rows = await con.fetch(query, *args)
            return [dict(row) for row in rows]

    @classmethod
    @with_retry()
    async def ping(cls) -> bool:
        """Run SELECT 1 on a pooled connection with an acquire timeout of at most 5s and return True on success, False otherwise."""
        if cls._pool is None or cls._pool._closed:
            logger.warning("Ping failed: Database pool is not initialized or is closed.")
            return False
        try:
            if hasattr(cls._pool, '_closed') and cls._pool._closed:
                logger.warning("Ping failed: Database pool is closed.")
                return False
            
            if hasattr(cls._pool, '_holders') and len(cls._pool._holders) == 0:
                logger.warning("Ping warning: No active database connections available.")
            async with cls._pool.acquire(timeout=min(5, cls._acquire_timeout_seconds)) as con:
                result = await con.fetch("SELECT 1 as health_check, current_timestamp;")
                if result and len(result) > 0:
                    logger.info(f"Ping successful - database is available. Pool size: {getattr(cls._pool, '_queue', {}).qsize() if hasattr(cls._pool, '_queue') else 'unknown'}")
                    return True
        except asyncio.TimeoutError:
            logger.error("Ping failed: Connection acquisition timed out.", exc_info=True)
            return False
        except Exception as e:
            logger.error(f"Ping failed: {type(e).__name__}: {e}", exc_info=True)
            return False
        return False


class DataQueryCostError(Exception):
    """A generated query's planned total cost exceeded DATA_QUERY_MAX_PLAN_COST."""


class ReadOnlyDatabase:
    """Lazily created low-privilege pool that runs Chaatie's validated, caller-scoped generated queries."""

    _pool: asyncpg.Pool | None = None
    _init_lock: asyncio.Lock = asyncio.Lock()

    @classmethod
    def is_configured(cls) -> bool:
        """Return True when the read-only login name and its password are both configured."""
        return bool(settings.DATA_QUERY_DB_USER and settings.DATA_QUERY_DB_PASSWORD)

    @classmethod
    async def _get_pool(cls) -> asyncpg.Pool:
        """Create the read-only pool on first use under the init lock and return it.

        Raises:
            RuntimeError: When the read-only credentials are not configured.
        """
        if cls._pool is not None and not cls._pool._closed:
            return cls._pool
        if not cls.is_configured():
            raise RuntimeError("Read-only data-query credentials are not configured.")
        async with cls._init_lock:
            if cls._pool is None or cls._pool._closed:
                cls._pool = await asyncpg.create_pool(
                    host=settings.DBHOST,
                    port=int(settings.DBPORT),
                    database=settings.DATABASENAME,
                    user=settings.DATA_QUERY_DB_USER,
                    password=settings.DATA_QUERY_DB_PASSWORD,
                    ssl="require",
                    timeout=settings.DB_POOL_CONNECT_TIMEOUT_SECONDS,
                    min_size=0,
                    max_size=settings.DATA_QUERY_POOL_MAX_SIZE,
                    statement_cache_size=0,
                    server_settings={
                        "application_name": "etown_chaatie_data_query",
                        "default_transaction_read_only": "on",
                    },
                )
                logger.info("Read-only data-query pool created (max %s)", settings.DATA_QUERY_POOL_MAX_SIZE)
        return cls._pool

    @classmethod
    async def fetch_scoped(
        cls,
        query: str,
        *,
        contact_id: str,
        user_profile_id: str,
        timeout_ms: int,
        max_cost: float,
    ) -> tuple[list[dict], float]:
        """Run one validated SELECT in a read-only transaction scoped to the caller, after an EXPLAIN cost check.

        Args:
            query: The validator's rewritten SQL.
            contact_id: The verified caller's contact id, bound to chaatie.contact_id.
            user_profile_id: The verified caller's profile id, bound to chaatie.user_profile_id.
            timeout_ms: statement_timeout for this transaction.
            max_cost: Highest planner total cost allowed before execution.

        Returns:
            The rows as dicts, and the planner's total cost.

        Raises:
            ValueError: When either identity value is empty.
            DataQueryCostError: When the plan is costlier than max_cost.
            RuntimeError: When the read-only credentials are not configured.
        """
        if not contact_id or not user_profile_id:
            raise ValueError("A scoped data query needs both contact_id and user_profile_id.")
        pool = await cls._get_pool()
        async with pool.acquire(timeout=settings.DB_POOL_ACQUIRE_TIMEOUT_SECONDS) as con:
            async with con.transaction(readonly=True):
                await con.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
                await con.fetchval(
                    "SELECT set_config('chaatie.contact_id', $1, true), "
                    "set_config('chaatie.user_profile_id', $2, true)",
                    contact_id,
                    user_profile_id,
                )
                plan = await con.fetchval("EXPLAIN (FORMAT JSON) " + query)
                plan = json.loads(plan) if isinstance(plan, str) else plan
                cost = float(plan[0]["Plan"]["Total Cost"])
                if cost > max_cost:
                    raise DataQueryCostError(f"planned cost {cost:.0f} exceeds {max_cost:.0f}")
                rows = await con.fetch(query, timeout=timeout_ms / 1000.0 + 1.0)
        return [dict(row) for row in rows], cost

    @classmethod
    async def close(cls) -> None:
        """Close the read-only pool if it was ever created, logging rather than raising errors."""
        async with cls._init_lock:
            if cls._pool is not None and not cls._pool._closed:
                try:
                    await cls._pool.close()
                    logger.info("Read-only data-query pool closed")
                except Exception as e:
                    logger.error(f"Error closing read-only data-query pool: {e}", exc_info=True)
            cls._pool = None
    
    
