"""Application Entry Point - FastAPI app with lifespan, middleware, routing and exception handlers.

Entry module for the Etown API: its lifespan opens the database pool and starts and stops every background worker, and it configures CORS and timing middleware, mounts the API router at /api, serves the custom Swagger UI and GET / health check, and logs errors globally.

Features:
    - Application Lifecycle: Database init with retry/backoff, then guarded start/stop of each background worker
    - Background Workers: Offer, plan, purge, insights, metadata, notification, retry, retention, mining, town, uninstall, event status, provider SEO
    - Terminal Log Capture: Logging-handler capture, falling back to stdout/stderr redirection
    - Memory Pre-warm: Initializes the mem0 client at startup
    - Suggestions Cache Pre-warming: Optional SUGGESTIONS_PREWARM pre-populates search suggestions
    - Startup Consistency Checks: Non-fatal warnings for a leftover cascade function or Dataverse entity mismatch
    - CORS Configuration: Cross-origin resource sharing policy setup
    - Performance Monitoring: Request timing and metrics collection middleware
    - Global Exception Handling: Logged 422, HTTPException and unhandled-error handlers
    - Custom Swagger UI: /docs filter searches endpoint paths, not only tags
    - API Routing: Versioned API router plus the well-known endpoints router
    - Graceful Shutdown: Bounded wait for search_provider background tasks, then closes HTTP client and pool
    - Health Check: Root endpoint for application status verification

Classes:
    No classes defined (module uses function-based FastAPI configuration)

Functions:
    lifespan(): Async context manager for application startup and shutdown lifecycle
    swagger_ui_html(): Serve the Swagger UI with path-searching filter
    read_root(): Health check endpoint handler
    _request_summary(): One-line request identity for log messages
    validation_exception_handler(): Log rejected 422 payloads before responding
    http_exception_handler(): Log HTTPExceptions, 5xx with traceback, 4xx at info
    generic_exception_handler(): Global exception handler for unhandled errors

Dependencies:
    - fastapi: Web framework for building APIs with automatic documentation
    - asyncpg: PostgreSQL async database adapter via Database client
    - uvicorn: ASGI server for running the FastAPI application (external runtime)
    - pydantic: Data validation through FastAPI models
    - app.core.config: Application configuration management
    - app.api.v1.router / app.api.wellknown: Versioned API routing and well-known endpoints
    - app.db.client: Database connection pooling and operations
    - app.core.logging: Centralized logging infrastructure
    - app.core.middleware: Custom performance monitoring middleware
    - app.utils.http_client: Shared HTTP client shutdown
    - app.logs.terminal_capture / app.logs.terminal_capture_logging: Terminal console capture
    - app.logs.retention_worker: Background log-retention sweep start/stop
    - app.services.offer_expiry / plan_expiry / plan_retention: Offer and plan lifecycle workers
    - app.services.community_view_retention: Deletes community view rows past the retention window
    - app.services.account_purge / social_insights_collector / town_mapping: Background workers
    - app.etown.mytown.mytown_metadata: Search-metadata refresh worker
    - app.etown.mytown.category_icons: Listener storing a Lucide icon for each new etown_category row
    - app.etown.notifications.core.scheduler / queue.runner / config_reload: Notification workers
    - app.agent_infrastructure.tools.tools_utils.search_notifications: Unverified-provider retry sweeper
    - app.agent_infrastructure.tools.tools_utils.search_provider: _background_tasks drained at shutdown
    - app.agent_infrastructure.classification.remining_worker: Classification re-mining worker
    - app.services.uninstall_detection: Silent-probe worker feeding the app uninstall table
    - app.services.provider_seo_sync: Queue worker mirroring verified providers to ets_providerseometadata
    - app.etown.user.account_deletion_utils: Startup check for a leftover hard-delete cascade function
    - app.core.memory: mem0 client pre-warm
    - app.core.docs: Custom Swagger UI HTML
    - app.etown.user.account_lifecycle_syncing: Startup Dataverse lifecycle-entity consistency check
    - app.etown.suggestions.suggestions_utils: Optional search-suggestions cache pre-warm

Updates:
    27-Feb-2026: Completely updated the documentation with a detailed module description
    11-Mar-2026: Added startup retry logic with exponential backoff for database connection
    14-Mar-2026: Added terminal log capture integration for console output monitoring
    29-Jun-2026: Added graceful shutdown wait for search_provider background notification tasks
    26-Aug-2026: Added 422/HTTPException handlers and logger wiring; database log handler drained in lifespan.
    03-Sep-2026: Starts and stops the town mapping sweep.
    07-Sep-2026: Pre-warms the mem0 client at startup.
    15-Sep-2026: Starts and stops the uninstall detection probe worker.
    08-Oct-2026: Removed Sentry.
    12-Oct-2026: Starts and stops the community view retention worker.
    16-Oct-2026: Starts and stops the category icon listener.
    20-Oct-2026: Starts and stops the event status worker.
    21-Oct-2026: Starts and stops the provider SEO sync worker.
    27-Oct-2026: Closes the read-only data-query pool on shutdown.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.api.v1.router import api_router
from app.core.logging import (
    install_asyncio_exception_handler,
    logger,
    restore_logging_after_server_config,
)
from app.db.client import Database, ReadOnlyDatabase
from app.core.memory import get_memory_client
from app.utils.http_client import close_shared_http_client
from app.logs.terminal_capture import start_terminal_capture, stop_terminal_capture


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Wire logging, open the database pool with retries and start background workers, stopping them all on shutdown."""
    restore_logging_after_server_config()
    install_asyncio_exception_handler()
    logger.info("Logging wired: root handlers, server loggers and the asyncio handler are active")

    max_retries = 5
    retry_delay = 3
    for attempt in range(1, max_retries + 1):
        try:
            await Database.init()
            break
        except (TimeoutError, OSError, Exception) as e:
            if attempt == max_retries:
                logger.critical("Database connection failed after %d attempts: %s", max_retries, e, exc_info=True)
                raise
            logger.warning(
                "Database connection attempt %d/%d failed: %s. Retrying in %ds...",
                attempt, max_retries, e, retry_delay,
            )
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30)
    
    try:
        await asyncio.to_thread(get_memory_client)
        logger.info("Memory client pre-warmed")
    except Exception as e:
        logger.error("Memory client pre-warm failed: %s", e, exc_info=True)

    import os
    prewarm_config = os.getenv("SUGGESTIONS_PREWARM")
    if prewarm_config:
        from app.etown.suggestions.suggestions_utils import get_search_suggestions_paginated
        logger.info("Pre-warming suggestions cache...")
        entries = prewarm_config.split(";")
        for entry in entries:
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split(",")
            if len(parts) >= 2:
                cid = parts[0].strip()
                upid = parts[1].strip()
                page = int(parts[2].strip()) if len(parts) > 2 and parts[2].strip() else 1
                lat = float(parts[3].strip()) if len(parts) > 3 and parts[3].strip() else None
                lon = float(parts[4].strip()) if len(parts) > 4 and parts[4].strip() else None
                try:
                    await get_search_suggestions_paginated(cid, upid, page, lat, lon)
                    logger.info(f"Pre-warmed cache for contact: {cid}, profile: {upid}, page: {page}")
                except Exception as e:
                    logger.error(f"Failed to pre-warm cache for {cid}: {e}", exc_info=True)
    
    from app.services.offer_expiry import start_offer_expiry_worker
    await start_offer_expiry_worker()

    try:
        from app.services.plan_expiry import start_plan_expiry_worker
        await start_plan_expiry_worker()
    except Exception as e:
        logger.error(f"Failed to start plan expiry worker: {e}", exc_info=True)

    try:
        from app.services.plan_retention import start_plan_retention_worker
        await start_plan_retention_worker()
    except Exception as e:
        logger.error(f"Failed to start plan retention worker: {e}", exc_info=True)

    try:
        from app.services.community_view_retention import start_community_view_retention_worker
        await start_community_view_retention_worker()
    except Exception as e:
        logger.error(f"Failed to start community view retention worker: {e}", exc_info=True)

    try:
        from app.services.event_status import start_event_status_worker
        await start_event_status_worker()
    except Exception as e:
        logger.error(f"Failed to start event status worker: {e}", exc_info=True)

    try:
        from app.services.town_mapping import start_town_mapping_worker
        await start_town_mapping_worker()
    except Exception as e:
        logger.error(f"Failed to start town mapping worker: {e}", exc_info=True)

    try:
        from app.etown.user.account_deletion_utils import (
            warn_if_hard_delete_cascade_installed,
        )
        await warn_if_hard_delete_cascade_installed()
    except Exception as e:
        logger.warning(f"Could not check for hard-delete cascade functions: {e}")

    try:
        from app.etown.user.account_lifecycle_syncing import verify_lifecycle_entities

        async def _verify_lifecycle_entities_bg() -> None:
            try:
                await verify_lifecycle_entities()
            except Exception as exc:
                logger.warning(f"Could not verify Dataverse lifecycle entities: {exc}")

        app.state.lifecycle_verify_task = asyncio.create_task(
            _verify_lifecycle_entities_bg(), name="verify_lifecycle_entities"
        )
    except Exception as e:
        logger.warning(f"Could not schedule the Dataverse lifecycle entity check: {e}")

    try:
        from app.services.account_purge import start_account_purge_worker
        await start_account_purge_worker()
    except Exception as e:
        logger.error(f"Failed to start account purge worker: {e}", exc_info=True)

    try:
        from app.services.social_insights_collector import start_social_insights_collector
        await start_social_insights_collector()
    except Exception as e:
        logger.warning(f"Failed to start social insights collector: {e}")

    try:
        from app.etown.mytown.mytown_metadata import start_metadata_refresh_worker
        await start_metadata_refresh_worker()
    except Exception as e:
        logger.warning(f"Failed to start search-metadata refresh worker: {e}")

    try:
        from app.etown.notifications.core.scheduler import start_notification_scheduler
        await start_notification_scheduler()
    except Exception as e:
        logger.warning(f"Failed to start notification scheduler: {e}")

    try:
        from app.etown.notifications.queue.runner import start_queue_runner
        await start_queue_runner()
    except Exception as e:
        logger.warning(f"Failed to start notification queue runner: {e}")

    try:
        from app.etown.notifications.core.config_reload import start_config_reload_listener
        await start_config_reload_listener()
    except Exception as e:
        logger.warning(f"Failed to start notification config reload listener: {e}")

    try:
        from app.etown.mytown.category_icons import start_category_icon_listener
        await start_category_icon_listener()
    except Exception as e:
        logger.warning(f"Failed to start category icon listener: {e}")

    try:
        from app.agent_infrastructure.tools.tools_utils.search_notifications import (
            start_unverified_retry_worker,
        )
        await start_unverified_retry_worker()
    except Exception as e:
        logger.warning(f"Failed to start unverified-provider retry sweeper: {e}")

    try:
        from app.logs.terminal_capture_logging import start_logging_terminal_capture, stop_logging_terminal_capture
        
        await start_logging_terminal_capture()
        logger.info("Logging-based terminal log capture initialized")
    except Exception as e:
        logger.warning(f"Failed to initialize logging-based terminal capture: {e}")
        
        try:
            await start_terminal_capture(
                log_file_path=None,  
                enable_console_capture=True,
                capture_interval=2.0
            )
            logger.info("Terminal log capture initialized (fallback)")
        except Exception as e2:
            logger.warning(f"Failed to initialize terminal capture fallback: {e2}")
                    
    try:
        from app.logs.retention_worker import start_log_retention_worker
        await start_log_retention_worker()
    except Exception as e:
        logger.warning(f"Failed to start log-retention sweep: {e}")
    try:
        from app.agent_infrastructure.classification.remining_worker import start_classification_remining_worker
        await start_classification_remining_worker()
    except Exception as e:
        logger.warning(f"Failed to start classification re-mining worker: {e}")
    try:
        from app.services.uninstall_detection import start_uninstall_detection_worker
        await start_uninstall_detection_worker()
    except Exception as e:
        logger.warning(f"Failed to start uninstall detection worker: {e}")
    try:
        from app.services.provider_seo_sync import start_provider_seo_sync_worker
        await start_provider_seo_sync_worker()
    except Exception as e:
        logger.error(f"Failed to start provider SEO sync worker: {e}", exc_info=True)
    try:
        yield
    finally:
        try:
            from app.logs.retention_worker import stop_log_retention_worker
            await stop_log_retention_worker()
            logger.info("Log-retention sweep stopped")
        except Exception as e:
            logger.debug(f"No log-retention sweep to stop: {e}")

        try:
            from app.agent_infrastructure.classification.remining_worker import stop_classification_remining_worker
            await stop_classification_remining_worker()
            logger.info("Classification re-mining worker stopped")
        except Exception as e:
            logger.debug(f"No classification re-mining worker to stop: {e}")

        try:
            from app.services.uninstall_detection import stop_uninstall_detection_worker
            await stop_uninstall_detection_worker()
            logger.info("Uninstall detection worker stopped")
        except Exception as e:
            logger.debug(f"No uninstall detection worker to stop: {e}")

        try:
            from app.services.provider_seo_sync import stop_provider_seo_sync_worker
            await stop_provider_seo_sync_worker()
        except Exception as e:
            logger.debug(f"No provider SEO sync worker to stop: {e}")

        try:
            from app.logs.terminal_capture_logging import stop_logging_terminal_capture
            await stop_logging_terminal_capture()
            logger.info("Logging-based terminal log capture stopped")
        except Exception as e:
            logger.debug(f"No logging-based terminal capture to stop: {e}")
        
        try:
            await stop_terminal_capture()
            logger.info("Terminal log capture stopped")
        except Exception as e:
            logger.debug(f"No terminal capture to stop: {e}")
        from app.services.offer_expiry import stop_offer_expiry_worker
        await stop_offer_expiry_worker()

        try:
            from app.services.plan_expiry import stop_plan_expiry_worker
            await stop_plan_expiry_worker()
        except Exception as e:
            logger.debug(f"No plan expiry worker to stop: {e}")

        try:
            from app.services.plan_retention import stop_plan_retention_worker
            await stop_plan_retention_worker()
        except Exception as e:
            logger.debug(f"No plan retention worker to stop: {e}")

        try:
            from app.services.community_view_retention import stop_community_view_retention_worker
            await stop_community_view_retention_worker()
        except Exception as e:
            logger.debug(f"No community view retention worker to stop: {e}")

        try:
            from app.services.event_status import stop_event_status_worker
            await stop_event_status_worker()
        except Exception as e:
            logger.debug(f"No event status worker to stop: {e}")

        try:
            from app.services.town_mapping import stop_town_mapping_worker
            await stop_town_mapping_worker()
        except Exception as e:
            logger.debug(f"No town mapping worker to stop: {e}")

        try:
            from app.services.account_purge import stop_account_purge_worker
            await stop_account_purge_worker()
        except Exception as e:
            logger.debug(f"No account purge worker to stop: {e}")

        try:
            from app.services.social_insights_collector import stop_social_insights_collector
            await stop_social_insights_collector()
        except Exception as e:
            logger.debug(f"No social insights collector to stop: {e}")

        try:
            from app.etown.mytown.mytown_metadata import stop_metadata_refresh_worker
            await stop_metadata_refresh_worker()
        except Exception as e:
            logger.debug(f"No search-metadata refresh worker to stop: {e}")

        try:
            from app.etown.notifications.core.scheduler import stop_notification_scheduler
            await stop_notification_scheduler()
        except Exception as e:
            logger.debug(f"No notification scheduler to stop: {e}")

        try:
            from app.etown.notifications.queue.runner import stop_queue_runner
            await stop_queue_runner()
        except Exception as e:
            logger.debug(f"No notification queue runner to stop: {e}")

        try:
            from app.etown.notifications.core.config_reload import stop_config_reload_listener
            await stop_config_reload_listener()
        except Exception as e:
            logger.debug(f"No notification config reload listener to stop: {e}")

        try:
            from app.etown.mytown.category_icons import stop_category_icon_listener
            await stop_category_icon_listener()
        except Exception as e:
            logger.debug(f"No category icon listener to stop: {e}")

        try:
            from app.agent_infrastructure.tools.tools_utils.search_notifications import (
                stop_unverified_retry_worker,
            )
            await stop_unverified_retry_worker()
        except Exception as e:
            logger.debug(f"No unverified-provider retry sweeper to stop: {e}")

        try:
            from app.agent_infrastructure.tools.tools_utils.search_provider import _background_tasks
            if _background_tasks:
                logger.info("[Shutdown] Waiting for %d in-flight notification task(s)...", len(_background_tasks))
                await asyncio.wait(_background_tasks, timeout=30)
        except Exception as e:
            logger.debug("No background notification tasks to await: %s", e)

        await close_shared_http_client()
        await ReadOnlyDatabase.close()
        await Database.close()

        try:
            db_log_handler = getattr(logging.getLogger(), "_db_handler", None)
            if db_log_handler is not None:
                db_log_handler.close()
        except Exception as e:
            logger.warning(f"Database log handler did not shut down cleanly: {e}", exc_info=True)

app = FastAPI(
    title=settings.PROJECT_NAME,
    description=settings.PROJECT_DESCRIPTION,
    summary=settings.PROJECT_SUMMARY,
    version=settings.PROJECT_VERSION,
    lifespan=lifespan,
    docs_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=settings.CORS_ALLOW_METHODS,
    allow_headers=settings.CORS_ALLOW_HEADERS,
)

from app.core.middleware import PerformanceMiddleware
app.add_middleware(
    PerformanceMiddleware,
    exclude_paths=["/api/testing", "/api/v1/testing"],
    api_prefix="/api"
)

app.include_router(api_router, prefix="/api")

from app.api import wellknown
app.include_router(wellknown.router)

from app.core.docs import get_docs_html

@app.get("/docs", include_in_schema=False)
async def swagger_ui_html(request: Request) -> HTMLResponse:
    """Handle GET /docs: serve Swagger UI whose filter box searches endpoint paths as well as tags."""
    root_path = request.scope.get("root_path", "").rstrip("/")
    return get_docs_html(
        openapi_url=root_path + app.openapi_url,
        title=app.title + " - Swagger UI",
    )

@app.get("/")
def read_root():
    """Handle GET /: return a welcome message as the health check."""
    logger.info("Root endpoint accessed")
    return {"message": "Welcome to the Etown application!"}

def _request_summary(request: Request) -> str:
    """Return the request method, path, query and client host as one log-friendly line."""
    client = request.client.host if request.client else "unknown"
    return (
        f"{request.method} {request.url.path}"
        f"{'?' + request.url.query if request.url.query else ''} | client={client}"
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Log a request validation failure and return 422 with the validation errors."""
    logger.warning(
        "422 validation error | %s | errors=%s",
        _request_summary(request), exc.errors(),
    )
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Log an HTTP exception, as error for 5xx and info for 4xx, and return its status and detail."""
    if exc.status_code >= 500:
        logger.error(
            "%d response | %s | detail=%s", exc.status_code, _request_summary(request), exc.detail,
            exc_info=True,
        )
    else:
        logger.info(
            "%d response | %s | detail=%s", exc.status_code, _request_summary(request), exc.detail
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Log an unhandled exception unless middleware already did, and return a generic 500."""
    error_message = "An unexpected error occurred."

    if not getattr(request.state, "error_logged", False):
        logger.error(
            "Unhandled %s | %s | %s", type(exc).__name__, _request_summary(request), exc,
            exc_info=exc,
        )
    else:
        logger.debug(
            "Returning 500 for an error already logged by the middleware | %s",
            _request_summary(request),
        )

    return JSONResponse(
        status_code=500,
        content={"message": error_message},
    )
