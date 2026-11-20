"""Log Schemas - Pydantic models for log entries, filters, statistics and analytics.

Request and response models for the /api/logs routes and the log storage services, covering application and terminal log entries, filter and pagination envelopes, aggregated statistics and the provider, agent and performance analytics responses.

Features:
    - LogLevel: Literal type alias for log level filtering and validation
    - LogEntry: Complete log entry response model with UUID primary key
    - LogFilterRequest: Comprehensive filtering options with business intelligence
    - LogFilterResponse: Paginated log response with metadata
    - LogStatsRequest: Statistics request with grouping and time periods
    - LogStats: Aggregated statistics with business intelligence metrics
    - TerminalLogEntry: Terminal console output log model
    - TerminalLogResponse: Paginated terminal log response
    - Business Intelligence Models: Provider, agent, and performance analytics

Classes:
    LogEntry: Main log entry model with all metadata and context
    LogFilterRequest: Advanced filtering with business context and performance metrics
    LogFilterResponse: Response wrapper with pagination metadata
    LogStatsRequest: Statistics request parameters with validation
    LogStats: Comprehensive statistics with business intelligence
    TerminalLogEntry: Raw terminal output log entry
    TerminalLogResponse: Paginated terminal log response
    ProviderAnalyticsRequest: Provider-specific analytics request
    ProviderAnalyticsResponse: Provider analytics with rankings and distribution
    AgentPerformanceRequest: Agent performance analysis request
    AgentPerformanceResponse: Agent performance metrics and trends
    PerformanceMetricsRequest: System performance metrics request
    PerformanceMetricsResponse: Performance data with bottlenecks and recommendations
    DBQueryRequest: Read-only SQL query request for /logs/dbquery

Functions:
    No functions defined at module level (LogStatsRequest.validate_group_by is a
    @field_validator method, not a module-level function)

Dependencies:
    - pydantic: Data validation and serialization framework
    - typing: Type hints for complex data structures
    - datetime: Date and time field validation
    - uuid: UUID field validation for primary keys

Updates:
    14-Mar-2026: Initial creation with log-related schemas and models
    14-Oct-2026: Added DBQueryRequest for /logs/dbquery.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime
from uuid import UUID

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

class LogEntry(BaseModel):
    """Log entry response model for API endpoints.
    
    Represents a single log record retrieved from the database with all
    associated metadata and context information.
    """
    id: UUID = Field(..., description="Unique log entry identifier (UUID primary key)")
    request_id: Optional[str] = Field(None, description="Request correlation identifier for tracing multiple logs from same request")
    log_level: LogLevel = Field(..., description="Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)")
    message: str = Field(..., description="Log message content")
    timestamp_ist: datetime = Field(..., description="Log timestamp converted to IST timezone for display")
    module_name: Optional[str] = Field(None, description="Python module name where log originated")
    function_name: Optional[str] = Field(None, description="Function name where log was called")
    line_number: Optional[int] = Field(None, description="Line number in source file")
    endpoint: Optional[str] = Field(None, description="API endpoint being accessed")
    method: Optional[str] = Field(None, description="HTTP method (GET, POST, etc.)")
    userprofile_id: Optional[str] = Field(None, description="User profile identifier if available")
    contact_id: Optional[str] = Field(None, description="Contact identifier if available")
    ip_address: Optional[str] = Field(None, description="Client IP address")
    user_agent: Optional[str] = Field(None, description="Client user agent string")
    status_code: Optional[int] = Field(None, description="HTTP response status code")
    response_time_ms: Optional[int] = Field(None, description="Response time in milliseconds") 
    stack_trace: Optional[str] = Field(None, description="Exception stack trace if available")
    extra_data: Optional[Dict[str, Any]] = Field(None, description="Additional context data")
    created_at: Optional[datetime] = Field(None, description="Database record creation timestamp")


class LogFilterRequest(BaseModel):
    """Log filtering request model for API endpoints.
    
    Provides comprehensive filtering options for log retrieval with
    pagination support and common search patterns. Enhanced to support
    business context and performance metrics filtering.
    """
    start_time: Optional[datetime] = Field(None, description="Filter logs after this timestamp") 
    end_time: Optional[datetime] = Field(None, description="Filter logs before this timestamp")
    log_level: Optional[LogLevel] = Field(None, description="Filter by log level - select from dropdown")
    endpoint: Optional[str] = Field(None, description="Filter by endpoint pattern (supports partial matching)")
    userprofile_id: Optional[str] = Field(None, description="Filter by user profile identifier")
    contact_id: Optional[str] = Field(None, description="Filter by contact identifier")
    request_id: Optional[str] = Field(None, description="Filter by request correlation ID to see all logs from same request")
    method: Optional[str] = Field(None, description="Filter by HTTP method")
    status_code: Optional[int] = Field(None, description="Filter by HTTP status code")
    
    provider_id: Optional[str] = Field(None, description="Filter logs from provider-related operations")
    service_type: Optional[str] = Field(None, description="Filter by service type (plumber, electrician, etc.)")
    search_query: Optional[str] = Field(None, description="Filter by search query terms")
    agent_type: Optional[str] = Field(None, description="Filter by agent type (member, provider)")
    business_operation: Optional[str] = Field(None, description="Filter by business operation (search, booking, etc.)")
    
    min_response_time: Optional[int] = Field(None, description="Filter logs with response time >= milliseconds")
    max_response_time: Optional[int] = Field(None, description="Filter logs with response time <= milliseconds")
    min_db_query_time: Optional[float] = Field(None, description="Filter logs with database query time >= seconds")
    max_db_query_time: Optional[float] = Field(None, description="Filter logs with database query time <= seconds")
    has_llm_calls: Optional[bool] = Field(None, description="Filter logs that have LLM API calls")
    min_llm_calls: Optional[int] = Field(None, description="Filter logs with LLM call count >= value")
    
    tool_name: Optional[str] = Field(None, description="Filter logs from specific agent tool execution")
    agent_decision: Optional[str] = Field(None, description="Filter logs containing agent decision keywords")
    
    extra_data_contains: Optional[str] = Field(None, description="Filter logs where extra_data contains text (JSON search)")
    
    limit: int = Field(100, description="Maximum number of logs to return", ge=1, le=1000)
    page: int = Field(1, description="Page number for pagination (starts at 1)", ge=1)


class LogFilterResponse(BaseModel):
    """Log filtering response model for API endpoints.
    
    Contains the filtered log entries along with pagination metadata
    and total count information for UI display.
    """
    logs: List[LogEntry] = Field([], description="Filtered log entries")
    total_count: int = Field(0, description="Total number of logs matching filter criteria")
    page_size: int = Field(0, description="Number of logs returned in this page")
    page: int = Field(1, description="Current page number")
    has_more: bool = Field(False, description="Whether more logs are available")


class LogStatsRequest(BaseModel):
    """Log statistics request model.
    
    Provides parameters for generating log statistics and aggregations
    over specified time periods and grouping criteria.
    """
    start_time: Optional[datetime] = Field(None, description="Statistics start time")
    end_time: Optional[datetime] = Field(None, description="Statistics end time") 
    group_by: str = Field("hour", description="Grouping interval (hour, day, week)")
    include_levels: bool = Field(True, description="Include log level breakdown")
    include_endpoints: bool = Field(True, description="Include endpoint breakdown")
    
    @field_validator('group_by')
    @classmethod
    def validate_group_by(cls, v):
        """Return group_by unchanged if it is hour, day, week or month, else raise ValueError."""
        if v not in ['hour', 'day', 'week', 'month']:
            raise ValueError('group_by must be one of: hour, day, week, month')
        return v


class LogStats(BaseModel):
    """Log statistics response model.
    
    Contains aggregated log statistics including counts by level,
    endpoint, and time period for dashboard displays. Enhanced with
    business intelligence and performance metrics.
    """
    total_logs: int = Field(0, description="Total number of logs in period")
    level_breakdown: Dict[str, int] = Field({}, description="Log counts by level")
    endpoint_breakdown: Dict[str, int] = Field({}, description="Log counts by endpoint")
    time_series: Dict[str, int] = Field({}, description="Log counts by time period")
    error_rate: float = Field(0.0, description="Percentage of error/warning logs")
    avg_response_time: Optional[float] = Field(None, description="Average response time in ms")
    
    service_type_breakdown: Dict[str, int] = Field({}, description="Log counts by service type")
    agent_operation_breakdown: Dict[str, int] = Field({}, description="Log counts by agent operation")
    provider_activity: Dict[str, int] = Field({}, description="Log counts by provider activity")
    search_patterns: Dict[str, int] = Field({}, description="Most common search terms")
    
    database_performance: Optional[Dict[str, float]] = Field(None, description="Database query performance metrics")
    llm_usage_stats: Optional[Dict[str, int]] = Field(None, description="LLM API usage statistics")
    tool_usage_breakdown: Dict[str, int] = Field({}, description="Agent tool usage counts")
    response_time_percentiles: Optional[Dict[str, float]] = Field(None, description="Response time percentiles (p50, p95, p99)")


class TerminalLogEntry(BaseModel):
    """Terminal log entry response model for API endpoints.
    
    Represents a single terminal log record with raw console output
    including uvicorn server logs and application logs.
    """
    logid: UUID = Field(..., description="Unique terminal log identifier (UUID primary key)")
    timestamp_ist: datetime = Field(..., description="Log timestamp in IST timezone")
    log_line: str = Field(..., description="Full original terminal output line")
    source: str = Field(..., description="Log source (uvicorn or app)")
    created_at: datetime = Field(..., description="Database record creation timestamp")


class TerminalLogResponse(BaseModel):
    """Terminal log response model for API endpoints.
    
    Contains terminal log entries with pagination metadata
    for console output retrieval and monitoring.
    """
    logs: List[TerminalLogEntry] = Field([], description="Terminal log entries")
    total_count: int = Field(0, description="Total number of terminal logs")
    page_size: int = Field(0, description="Number of logs returned in this page")
    page: int = Field(1, description="Current page number")
    has_more: bool = Field(False, description="Whether more logs are available")


class ProviderAnalyticsRequest(BaseModel):
    """Request model for provider-specific log analytics."""
    start_time: Optional[datetime] = Field(None, description="Analysis start time")
    end_time: Optional[datetime] = Field(None, description="Analysis end time")
    provider_id: Optional[str] = Field(None, description="Specific provider to analyze")
    service_types: Optional[List[str]] = Field(None, description="Service types to include")
    include_bookings: bool = Field(True, description="Include booking operation analytics")
    include_searches: bool = Field(True, description="Include search operation analytics")


class ProviderAnalyticsResponse(BaseModel):
    """Response model for provider analytics."""
    total_operations: int = Field(0, description="Total provider operations logged")
    booking_stats: Dict[str, Any] = Field({}, description="Booking operation statistics")
    search_stats: Dict[str, Any] = Field({}, description="Search operation statistics")
    popular_services: List[Dict[str, Any]] = Field([], description="Most requested services")
    provider_rankings: List[Dict[str, Any]] = Field([], description="Provider activity rankings")
    geographic_distribution: Dict[str, int] = Field({}, description="Activity by location")


class AgentPerformanceRequest(BaseModel):
    """Request model for agent performance analytics."""
    start_time: Optional[datetime] = Field(None, description="Analysis start time")
    end_time: Optional[datetime] = Field(None, description="Analysis end time")
    agent_type: Optional[str] = Field(None, description="Agent type to analyze (member/provider)")
    tool_name: Optional[str] = Field(None, description="Specific tool to analyze")
    include_llm_metrics: bool = Field(True, description="Include LLM usage metrics")
    include_error_analysis: bool = Field(True, description="Include error pattern analysis")


class AgentPerformanceResponse(BaseModel):
    """Response model for agent performance analytics."""
    total_agent_operations: int = Field(0, description="Total agent operations")
    tool_usage_stats: Dict[str, int] = Field({}, description="Usage count by tool")
    average_response_times: Dict[str, float] = Field({}, description="Average response times by operation")
    success_rates: Dict[str, float] = Field({}, description="Success rates by operation")
    llm_usage: Dict[str, Any] = Field({}, description="LLM API usage statistics")
    error_patterns: List[Dict[str, Any]] = Field([], description="Common error patterns")
    performance_trends: Dict[str, Any] = Field({}, description="Performance trends over time")


class PerformanceMetricsRequest(BaseModel):
    """Request model for system performance metrics."""
    start_time: Optional[datetime] = Field(None, description="Analysis start time")
    end_time: Optional[datetime] = Field(None, description="Analysis end time")
    metric_type: Optional[str] = Field(None, description="Metric type (database, api, llm)")
    percentile: Optional[int] = Field(95, description="Percentile for analysis (50, 95, 99)")
    include_outliers: bool = Field(False, description="Include outlier analysis")


class PerformanceMetricsResponse(BaseModel):
    """Response model for performance metrics."""
    metric_summary: Dict[str, float] = Field({}, description="Summary statistics")
    database_metrics: Dict[str, Any] = Field({}, description="Database performance data")
    api_metrics: Dict[str, Any] = Field({}, description="API performance data")
    llm_metrics: Dict[str, Any] = Field({}, description="LLM performance data")
    bottlenecks: List[Dict[str, Any]] = Field([], description="Identified performance bottlenecks")
    recommendations: List[str] = Field([], description="Performance improvement recommendations")


class DBQueryRequest(BaseModel):
    """Request body for POST /api/logs/dbquery: one read-only SELECT statement to run against the database."""
    query: str = Field(..., min_length=1, description="A single SELECT (or WITH ... SELECT) statement; a trailing semicolon is allowed")
    max_rows: int = Field(10, description="Maximum number of rows to return", ge=1, le=10)
    timeout_seconds: int = Field(30, description="Statement timeout for this query in seconds", ge=1, le=120)