"""Logs Endpoints - HTTP routes for log retrieval, statistics and log-derived analytics.

HTTP layer for /api/logs: unauthenticated read routes that filter stored application logs and captured terminal output, report logging health, compute provider, agent and performance analytics and run read-only SELECTs through the app.logs storage services.

Features:
    - Enhanced Log Retrieval: Advanced filtering by business context, performance metrics, and agent data
    - Business Intelligence: Provider analytics, agent performance analysis, and system metrics
    - JSONB Queries: Efficient PostgreSQL JSONB filtering for complex data structures
    - Performance Analytics: Response time analysis, database query monitoring, LLM usage tracking
    - Agent Analysis: Tool usage statistics, success rates, and error pattern detection
    - Provider Insights: Service request analytics, provider rankings, and geographic distribution
    - Real-time Monitoring: System health checks and logging service status
    - Multi-dimensional Filtering: Combined filtering across multiple log dimensions
    - Statistics Dashboard: Enhanced metrics with business intelligence

Enhanced Endpoints:
    GET /logs: Enhanced log retrieval with business context and performance filtering
    GET /logs/stats: Statistics with business intelligence and performance metrics
    GET /logs/endpoints: Available endpoint listing for filter dropdowns
    GET /logs/health: Logging system health and status monitoring
    GET /logs/terminal: Terminal console output retrieval with filtering and pagination
    GET /logs/analytics/providers: Provider-specific business analytics
    GET /logs/analytics/agents: Agent performance and tool usage analytics
    GET /logs/analytics/performance: System performance metrics and bottleneck analysis
    POST /logs/dbquery: Run a read-only SELECT query and return up to 10 rows

Classes:
    No classes defined (module uses request/response models imported from app.logs.schema)

Functions:
    get_logs(): Retrieve filtered application logs with pagination and JSONB business filters
    get_log_stats(): Return aggregated log statistics (level/endpoint breakdown, error rate)
    get_logged_endpoints(): List distinct logged endpoints with counts, for filter dropdowns
    get_logging_system_health(): Report logging queue/worker/database health status
    get_terminal_logs(): Retrieve captured terminal console output with filtering and pagination
    get_provider_analytics(): Analyze provider bookings/searches/rankings from log data
    get_agent_performance(): Analyze agent tool usage, response times, and error patterns
    get_performance_metrics(): Analyze database/API/LLM performance percentiles and bottlenecks
    run_db_query(): Run one SELECT in a read-only transaction with a timeout and row cap

Dependencies:
    - fastapi: Web framework and dependency injection with enhanced query parameters
    - datetime: Date/time handling for filtering and analytics
    - app.logs.storage: Enhanced log storage service with JSONB filtering support and read-only SELECT runner
    - asyncpg: Postgres error types mapped to HTTP errors for /logs/dbquery
    - app.logs.terminal_storage: Terminal log storage service for console output capture
    - app.logs.schema: Pydantic models for log entries, filters, stats, and analytics responses
    - app.core.config: Configuration settings for analytics features
    - app.core.logging: Application logger and its database handler for /logs/health
    - app.db.client: Direct query for the /logs/endpoints dropdown listing

Updates:
    12-Mar-2026: Initial creation with admin log management endpoints
    13-Mar-2026: Added LogLevel dropdown support; removed /logs/levels endpoint.
    14-Mar-2026: Added business intelligence, performance analytics and JSONB filtering.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    14-Oct-2026: Added POST /logs/dbquery for read-only SELECT queries, capped at 10 rows.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
import json
import re
import time
import asyncpg
from fastapi import APIRouter, HTTPException, Query, Depends, Response

from app.logs.storage import get_log_storage_service, run_readonly_select
from app.logs.terminal_storage import get_terminal_log_storage_service
from app.logs.schema import (
    LogEntry, LogFilterRequest, LogFilterResponse, 
    LogStatsRequest, LogStats, LogLevel,
    TerminalLogEntry, TerminalLogResponse,
    ProviderAnalyticsRequest, ProviderAnalyticsResponse,
    AgentPerformanceRequest, AgentPerformanceResponse,
    PerformanceMetricsRequest, PerformanceMetricsResponse,
    DBQueryRequest
)
from app.core.config import settings
from app.core.logging import logger

router = APIRouter()


@router.get("/", response_model=LogFilterResponse)
async def get_logs(
    start_time: Optional[datetime] = Query(None, description="Filter logs after this timestamp"),
    end_time: Optional[datetime] = Query(None, description="Filter logs before this timestamp"), 
    log_level: Optional[LogLevel] = Query(None, description="Filter by log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"),
    endpoint: Optional[str] = Query(None, description="Filter by endpoint pattern"),
    userprofile_id: Optional[str] = Query(None, description="Filter by user profile identifier"),
    contact_id: Optional[str] = Query(None, description="Filter by contact identifier"),
    request_id: Optional[str] = Query(None, description="Filter by request correlation ID to see all logs from same request"),
    method: Optional[str] = Query(None, description="Filter by HTTP method"),
    status_code: Optional[int] = Query(None, description="Filter by HTTP status code"),
    
    provider_id: Optional[str] = Query(None, description="Filter logs from provider operations"),
    service_type: Optional[str] = Query(None, description="Filter by service type"),
    search_query: Optional[str] = Query(None, description="Filter by search query terms"),
    agent_type: Optional[str] = Query(None, description="Filter by agent type"),
    business_operation: Optional[str] = Query(None, description="Filter by business operation"),
    
    min_response_time: Optional[int] = Query(None, description="Minimum response time filter (ms)"),
    max_response_time: Optional[int] = Query(None, description="Maximum response time filter (ms)"),
    min_db_query_time: Optional[float] = Query(None, description="Minimum database query time (seconds)"),
    max_db_query_time: Optional[float] = Query(None, description="Maximum database query time (seconds)"),
    has_llm_calls: Optional[bool] = Query(None, description="Filter logs with LLM calls"),
    min_llm_calls: Optional[int] = Query(None, description="Minimum LLM call count"),
    
    tool_name: Optional[str] = Query(None, description="Filter by agent tool name"),
    agent_decision: Optional[str] = Query(None, description="Filter by agent decision keywords"),
    extra_data_contains: Optional[str] = Query(None, description="Search text in extra_data JSON"),
    
    limit: int = Query(100, description="Maximum number of logs to return", ge=1, le=1000),
    page: int = Query(1, description="Page number for pagination (starts at 1)", ge=1)
):
    """Handle GET /api/logs/: return a page of application logs matching column and extra_data filters, newest first."""
    try:
        offset = (page - 1) * limit        
        service = await get_log_storage_service()
        
        logs_data = await service.get_logs_filtered(
            start_time=start_time,
            end_time=end_time,
            log_level=log_level,
            endpoint=endpoint,
            userprofile_id=userprofile_id,
            contact_id=contact_id,
            request_id=request_id,
            method=method,
            status_code=status_code,
            
            provider_id=provider_id,
            service_type=service_type,
            search_query=search_query,
            agent_type=agent_type,
            business_operation=business_operation,
            min_response_time=min_response_time,
            max_response_time=max_response_time,
            min_db_query_time=min_db_query_time,
            max_db_query_time=max_db_query_time,
            has_llm_calls=has_llm_calls,
            min_llm_calls=min_llm_calls,
            tool_name=tool_name,
            agent_decision=agent_decision,
            extra_data_contains=extra_data_contains,
            
            limit=limit,
            offset=offset
        )
        
        log_entries = []
        for log_data in logs_data:
            log_data = log_data.copy()
            
            if 'ip_address' in log_data and log_data['ip_address'] is not None:
                log_data['ip_address'] = str(log_data['ip_address'])
            
            if 'extra_data' in log_data and log_data['extra_data'] is not None:
                if isinstance(log_data['extra_data'], str):
                    try:
                        log_data['extra_data'] = json.loads(log_data['extra_data'])
                    except (json.JSONDecodeError, ValueError):
                        logger.debug("logs: extra_data column is not valid JSON; returning it as empty")
                        log_data['extra_data'] = {}
            
            log_entries.append(LogEntry(**log_data))
        
        has_more = len(log_entries) == limit
        
        return LogFilterResponse(
            logs=log_entries,
            total_count=len(log_entries) + offset + (1 if has_more else 0),
            page_size=len(log_entries),
            page=page,
            has_more=has_more
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to retrieve logs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve logs")


@router.get("/stats", response_model=LogStats)
async def get_log_stats(
    start_time: Optional[datetime] = Query(None, description="Statistics start time"),
    end_time: Optional[datetime] = Query(None, description="Statistics end time"),
    group_by: str = Query("hour", description="Grouping interval (hour, day, week)"),
    include_levels: bool = Query(True, description="Include log level breakdown"),
    include_endpoints: bool = Query(True, description="Include endpoint breakdown")
):
    """Handle GET /api/logs/stats: summarise up to 10000 logs by level, endpoint, error rate and response time.

    Raises:
        HTTPException: 400 for an invalid group_by, 500 on failure.
    """
    try:
        if group_by not in ['hour', 'day', 'week', 'month']:
            raise HTTPException(status_code=400, detail="group_by must be one of: hour, day, week, month")
        
        if not end_time:
            end_time = datetime.now(timezone.utc)
        if not start_time:
            start_time = end_time - timedelta(hours=24)
        
        service = await get_log_storage_service()
        
        logs_data = await service.get_logs_filtered(
            start_time=start_time,
            end_time=end_time,
            limit=10000
        )
        
        total_logs = len(logs_data)
        level_breakdown = {}
        endpoint_breakdown = {}
        error_count = 0
        response_times = []
        
        for log_data in logs_data:
            if include_levels:
                level = log_data.get('log_level', 'UNKNOWN')
                level_breakdown[level] = level_breakdown.get(level, 0) + 1
                
                if level in ['ERROR', 'CRITICAL']:
                    error_count += 1
            
            if include_endpoints and log_data.get('endpoint'):
                endpoint = log_data.get('endpoint', 'unknown')
                endpoint_breakdown[endpoint] = endpoint_breakdown.get(endpoint, 0) + 1
            
            if log_data.get('response_time_ms'):
                response_times.append(log_data['response_time_ms'])
        
        error_rate = (error_count / total_logs * 100) if total_logs > 0 else 0.0
        
        avg_response_time = sum(response_times) / len(response_times) if response_times else None
        
        time_series = {start_time.isoformat(): total_logs}
        
        return LogStats(
            total_logs=total_logs,
            level_breakdown=level_breakdown if include_levels else {},
            endpoint_breakdown=endpoint_breakdown if include_endpoints else {},
            time_series=time_series,
            error_rate=round(error_rate, 2),
            avg_response_time=round(avg_response_time, 2) if avg_response_time else None
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to generate log statistics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate log statistics")


@router.get("/endpoints")
async def get_logged_endpoints(
    limit: int = Query(100, description="Maximum number of endpoints to return", ge=1, le=500)
):
    """Handle GET /api/logs/endpoints: return logged endpoints with their log counts, most logged first."""
    try:
        service = await get_log_storage_service()
        
        query = """
            SELECT DISTINCT endpoint, COUNT(*) as log_count
            FROM etown_application_logs 
            WHERE endpoint IS NOT NULL 
            GROUP BY endpoint 
            ORDER BY log_count DESC 
            LIMIT $1
        """
        
        from app.db.client import Database
        rows = await Database.fetch(query, limit)
        
        endpoints = [
            {"endpoint": row["endpoint"], "log_count": row["log_count"]}
            for row in rows
        ]
        
        return {"endpoints": endpoints}
        
    except Exception as e:
        logger.error(f"Failed to retrieve logged endpoints: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve logged endpoints")


@router.get("/health")
async def get_logging_system_health():
    """Handle GET /api/logs/health: report the database log handler's queue and worker status as healthy, degraded or unhealthy."""
    try:
        from app.core.logging import logger
        db_handler = getattr(logger, '_db_handler', None)
        
        health_status = {
            "status": "healthy",
            "database_logging_enabled": settings.ENABLE_DATABASE_LOGGING,
            "handler_available": db_handler is not None,
            "timestamp": datetime.now().isoformat()
        }
        
        if db_handler:
            queue_status = db_handler.get_queue_status()
            health_status.update(queue_status)
            
            if queue_status.get("queue_overflow_count", 0) > 1000:
                health_status["status"] = "degraded"
                health_status["warnings"] = ["High queue overflow count"]
            
            if not queue_status.get("service_available", False):
                health_status["status"] = "unhealthy"
                health_status["errors"] = ["Database service unavailable"]
            
            if not queue_status.get("worker_alive", False):
                health_status["status"] = "unhealthy"
                if "errors" not in health_status:
                    health_status["errors"] = []
                health_status["errors"].append("Background worker not running")
        else:
            health_status["status"] = "degraded"
            health_status["warnings"] = ["Database handler not available"]
        
        return health_status
        
    except Exception as e:
        logger.error(f"Failed to retrieve logging system health: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve logging system health")


@router.get("/terminal", response_model=TerminalLogResponse)
async def get_terminal_logs(
    start_time: Optional[datetime] = Query(None, description="Filter logs after this timestamp"),
    end_time: Optional[datetime] = Query(None, description="Filter logs before this timestamp"),
    source: Optional[str] = Query(None, description="Filter by log source (uvicorn or app)"),
    search_text: Optional[str] = Query(None, description="Search for text within terminal log lines"),
    
    limit: int = Query(100, description="Maximum number of logs to return", ge=1, le=1000),
    page: int = Query(1, description="Page number for pagination (starts at 1)", ge=1)
):
    """Handle GET /api/logs/terminal: return a page of terminal logs filtered by time, source and text, newest first."""
    try:
        offset = (page - 1) * limit
        
        terminal_service = await get_terminal_log_storage_service()
        
        logs_data = await terminal_service.get_terminal_logs_filtered(
            start_time=start_time,
            end_time=end_time,
            source=source,
            search_text=search_text,
            limit=limit,
            offset=offset
        )
        
        total_count = await terminal_service.get_terminal_logs_count(
            start_time=start_time,
            end_time=end_time,
            source=source,
            search_text=search_text
        )
        
        terminal_logs = []
        for log_data in logs_data:
            terminal_logs.append(TerminalLogEntry(
                logid=log_data['logid'],
                timestamp_ist=log_data['timestamp_ist'],
                log_line=log_data['log_line'],
                source=log_data['source'],
                created_at=log_data['created_at']
            ))
        
        page_size = len(terminal_logs)
        has_more = total_count > (offset + page_size)
        
        return TerminalLogResponse(
            logs=terminal_logs,
            total_count=total_count,
            page_size=page_size,
            page=page,
            has_more=has_more
        )
        
    except Exception as e:
        logger.error(f"Failed to retrieve terminal logs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve terminal logs")


@router.get("/analytics/providers", response_model=ProviderAnalyticsResponse)
async def get_provider_analytics(
    start_time: Optional[datetime] = Query(None, description="Analysis start time"),
    end_time: Optional[datetime] = Query(None, description="Analysis end time"),
    provider_id: Optional[str] = Query(None, description="Specific provider to analyze"),
    service_types: Optional[str] = Query(None, description="Comma-separated service types to include"),
    include_bookings: bool = Query(True, description="Include booking operation analytics"),
    include_searches: bool = Query(True, description="Include search operation analytics")
):
    """Handle GET /api/logs/analytics/providers: count provider activity, services, bookings and searches from logged business context."""
    try:
        if not end_time:
            end_time = datetime.now(timezone.utc)
        if not start_time:
            start_time = end_time - timedelta(days=7)
            
        service_type_list = None
        if service_types:
            service_type_list = [s.strip() for s in service_types.split(",")]
        
        service = await get_log_storage_service()
        
        logs_data = await service.get_logs_filtered(
            start_time=start_time,
            end_time=end_time,
            provider_id=provider_id,
            limit=5000
        )
        
        total_operations = len(logs_data)
        booking_stats = {}
        search_stats = {}
        popular_services = []
        provider_rankings = []
        geographic_distribution = {}
        
        provider_activity = {}
        service_requests = {}
        
        for log in logs_data:
            extra_data = log.get('extra_data', {})
            business_context = extra_data.get('business_context', {})
            
            if business_context.get('provider_id'):
                pid = business_context['provider_id']
                provider_activity[pid] = provider_activity.get(pid, 0) + 1
            
            if business_context.get('service_type'):
                service = business_context['service_type']
                service_requests[service] = service_requests.get(service, 0) + 1
            
            operation = business_context.get('operation', '')
            if include_bookings and 'book' in operation.lower():
                booking_stats['total_bookings'] = booking_stats.get('total_bookings', 0) + 1
            if include_searches and 'search' in operation.lower():
                search_stats['total_searches'] = search_stats.get('total_searches', 0) + 1
        
        popular_services = [
            {"service_type": k, "request_count": v}
            for k, v in sorted(service_requests.items(), key=lambda x: x[1], reverse=True)
        ]
        
        provider_rankings = [
            {"provider_id": k, "activity_count": v}
            for k, v in sorted(provider_activity.items(), key=lambda x: x[1], reverse=True)
        ]
        
        return ProviderAnalyticsResponse(
            total_operations=total_operations,
            booking_stats=booking_stats,
            search_stats=search_stats,
            popular_services=popular_services[:10],
            provider_rankings=provider_rankings[:20],
            geographic_distribution=geographic_distribution
        )
        
    except Exception as e:
        logger.error(f"Failed to generate provider analytics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate provider analytics")


@router.get("/analytics/agents", response_model=AgentPerformanceResponse)
async def get_agent_performance(
    start_time: Optional[datetime] = Query(None, description="Analysis start time"),
    end_time: Optional[datetime] = Query(None, description="Analysis end time"),
    agent_type: Optional[str] = Query(None, description="Agent type to analyze (member/provider)"),
    tool_name: Optional[str] = Query(None, description="Specific tool to analyze"),
    include_llm_metrics: bool = Query(True, description="Include LLM usage metrics"),
    include_error_analysis: bool = Query(True, description="Include error pattern analysis")
):
    """Handle GET /api/logs/analytics/agents: compute per-tool usage, response times, success rates, LLM calls and recent errors from logs."""
    try:
        if not end_time:
            end_time = datetime.now(timezone.utc)
        if not start_time:
            start_time = end_time - timedelta(hours=24)
        
        service = await get_log_storage_service()
        
        logs_data = await service.get_logs_filtered(
            start_time=start_time,
            end_time=end_time,
            agent_type=agent_type,
            tool_name=tool_name,
            limit=10000
        )
        
        total_agent_operations = len(logs_data)
        tool_usage_stats = {}
        average_response_times = {}
        success_rates = {}
        llm_usage = {}
        error_patterns = []
        performance_trends = {}
        
        response_times_by_tool = {}
        success_counts = {}
        total_counts = {}
        llm_calls_total = 0
        llm_cost_total = 0.0
        
        for log in logs_data:
            extra_data = log.get('extra_data', {})
            agent_context = extra_data.get('agent_context', {})
            performance_metrics = extra_data.get('performance_metrics', {})
            business_context = extra_data.get('business_context', {})
            
            tool = agent_context.get('tool_name') or business_context.get('operation', 'unknown')
            tool_usage_stats[tool] = tool_usage_stats.get(tool, 0) + 1
            
            response_time = log.get('response_time_ms')
            if response_time:
                if tool not in response_times_by_tool:
                    response_times_by_tool[tool] = []
                response_times_by_tool[tool].append(response_time)
            
            total_counts[tool] = total_counts.get(tool, 0) + 1
            if log.get('log_level') not in ['ERROR', 'CRITICAL']:
                success_counts[tool] = success_counts.get(tool, 0) + 1
            
            if include_llm_metrics:
                llm_calls = performance_metrics.get('llm_calls', 0)
                if isinstance(llm_calls, (int, float)) and llm_calls > 0:
                    llm_calls_total += llm_calls
            
            if include_error_analysis and log.get('log_level') in ['ERROR', 'CRITICAL']:
                error_patterns.append({
                    "tool": tool,
                    "error_message": log.get('message', '')[:200],
                    "timestamp": log.get('timestamp_ist')
                })
        
        for tool in response_times_by_tool:
            times = response_times_by_tool[tool]
            average_response_times[tool] = sum(times) / len(times)
        
        for tool in total_counts:
            success_count = success_counts.get(tool, 0)
            success_rates[tool] = (success_count / total_counts[tool]) * 100
        
        if include_llm_metrics:
            llm_usage = {
                "total_calls": llm_calls_total,
                "average_calls_per_operation": llm_calls_total / total_agent_operations if total_agent_operations > 0 else 0
            }
        
        return AgentPerformanceResponse(
            total_agent_operations=total_agent_operations,
            tool_usage_stats=tool_usage_stats,
            average_response_times=average_response_times,
            success_rates=success_rates,
            llm_usage=llm_usage,
            error_patterns=error_patterns[-20:],
            performance_trends=performance_trends
        )
        
    except Exception as e:
        logger.error(f"Failed to generate agent performance analytics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate agent performance analytics")


@router.get("/analytics/performance", response_model=PerformanceMetricsResponse)
async def get_performance_metrics(
    start_time: Optional[datetime] = Query(None, description="Analysis start time"),
    end_time: Optional[datetime] = Query(None, description="Analysis end time"),
    metric_type: Optional[str] = Query(None, description="Metric type (database, api, llm)"),
    percentile: int = Query(95, description="Percentile for analysis (50, 95, 99)"),
    include_outliers: bool = Query(False, description="Include outlier analysis")
):
    """Handle GET /api/logs/analytics/performance: summarise DB, API and LLM metrics from logs and flag slow bottlenecks.

    Raises:
        HTTPException: 500 on failure, including an invalid percentile.
    """
    try:
        if not end_time:
            end_time = datetime.now(timezone.utc)
        if not start_time:
            start_time = end_time - timedelta(hours=6)
        
        if percentile not in [50, 95, 99]:
            raise HTTPException(status_code=400, detail="Percentile must be 50, 95, or 99")
        
        service = await get_log_storage_service()
        
        logs_data = await service.get_logs_filtered(
            start_time=start_time,
            end_time=end_time,
            limit=15000
        )
        
        metric_summary = {}
        database_metrics = {}
        api_metrics = {}
        llm_metrics = {}
        bottlenecks = []
        recommendations = []
        
        db_query_times = []
        api_response_times = []
        llm_call_counts = []
        
        for log in logs_data:
            extra_data = log.get('extra_data', {})
            performance_metrics = extra_data.get('performance_metrics', {})
            
            db_time = performance_metrics.get('db_query_time')
            if db_time and isinstance(db_time, (int, float)):
                db_query_times.append(db_time)
            
            response_time = log.get('response_time_ms')
            if response_time and isinstance(response_time, (int, float)):
                api_response_times.append(response_time)
            
            llm_calls = performance_metrics.get('llm_calls')
            if llm_calls and isinstance(llm_calls, (int, float)):
                llm_call_counts.append(llm_calls)
        
        if db_query_times:
            db_query_times.sort()
            p_idx = int((percentile / 100) * len(db_query_times))
            database_metrics = {
                "total_queries": len(db_query_times),
                "average_time": sum(db_query_times) / len(db_query_times),
                f"p{percentile}_time": db_query_times[p_idx],
                "max_time": max(db_query_times)
            }
        
        if api_response_times:
            api_response_times.sort()
            p_idx = int((percentile / 100) * len(api_response_times))
            api_metrics = {
                "total_requests": len(api_response_times),
                "average_response_time": sum(api_response_times) / len(api_response_times),
                f"p{percentile}_response_time": api_response_times[p_idx],
                "max_response_time": max(api_response_times)
            }
        
        if llm_call_counts:
            llm_metrics = {
                "total_operations_with_llm": len(llm_call_counts),
                "total_llm_calls": sum(llm_call_counts),
                "average_calls_per_operation": sum(llm_call_counts) / len(llm_call_counts)
            }
        
        metric_summary = {
            "analysis_period_hours": (end_time - start_time).total_seconds() / 3600,
            "total_logs_analyzed": len(logs_data),
            **{f"db_{k}": v for k, v in database_metrics.items()},
            **{f"api_{k}": v for k, v in api_metrics.items()},
            **{f"llm_{k}": v for k, v in llm_metrics.items()}
        }
        
        if database_metrics.get("max_time", 0) > 5.0:
            bottlenecks.append({
                "type": "database",
                "severity": "high",
                "metric": "query_time",
                "value": database_metrics["max_time"],
                "description": "Slow database queries detected"
            })
            recommendations.append("Review and optimize slow database queries")
        
        if api_metrics.get("max_response_time", 0) > 10000:
            bottlenecks.append({
                "type": "api",
                "severity": "high", 
                "metric": "response_time",
                "value": api_metrics["max_response_time"],
                "description": "High API response times detected"
            })
            recommendations.append("Investigate high response time endpoints")
        
        return PerformanceMetricsResponse(
            metric_summary=metric_summary,
            database_metrics=database_metrics,
            api_metrics=api_metrics,
            llm_metrics=llm_metrics,
            bottlenecks=bottlenecks,
            recommendations=recommendations
        )
        
    except Exception as e:
        logger.error(f"Failed to generate performance metrics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate performance metrics")


_DBQUERY_ALLOWED_START = re.compile(r"^(select|with)\b", re.IGNORECASE)


@router.post("/dbquery", response_model=List[Dict[str, Any]])
async def run_db_query(request: DBQueryRequest):
    """Handle POST /api/logs/dbquery: run one SELECT statement in a read-only transaction and return up to 10 rows.

    Raises:
        HTTPException: 400 for a non-SELECT or failing query, 408 when the statement timeout is hit, 500 on other failures.
    """
    query = request.query.strip().rstrip(";").strip()
    if not _DBQUERY_ALLOWED_START.match(query):
        raise HTTPException(status_code=400, detail="Only SELECT or WITH ... SELECT queries are allowed")

    try:
        started = time.perf_counter()
        rows = await run_readonly_select(query, request.max_rows, request.timeout_seconds)
        execution_time_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.info(f"logs.dbquery | rows={len(rows)} | ms={execution_time_ms} | query={query[:500]}")
        return rows

    except asyncpg.QueryCanceledError as e:
        logger.warning(f"logs.dbquery timed out after {request.timeout_seconds}s | query={query[:500]}")
        raise HTTPException(status_code=408, detail=f"Query exceeded the {request.timeout_seconds}s timeout: {e}")
    except (asyncpg.PostgresError, asyncpg.InterfaceError) as e:
        logger.warning(f"logs.dbquery rejected | {type(e).__name__}: {e} | query={query[:500]}")
        raise HTTPException(status_code=400, detail=f"{type(e).__name__}: {e}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to run DB query: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to run DB query")