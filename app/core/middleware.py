"""Performance Middleware - times every API request and enriches it with logging context.

ASGI middleware installed in main.py over /api routes: it times each request, merges a performance block into JSON responses, and fills the request-scoped log context (request id, user and contact ids, business data) that the logging layers read.

Features:
    - Performance Tracking: per-endpoint timing via PerformanceCollector, with DB/API/cache breakdown
    - Response Injection: merges a `performance` object into JSON dict bodies lacking one
    - Log Context Capture: sets request id, method, endpoint, user/contact ids, IP and user agent
    - Business Context Capture: provider/service ids, search terms, location and a sanitized payload
    - Payload Sanitization: redacts sensitive keys, truncates long values, caps recursion depth
    - Endpoint Classification: labels requests provider/search/booking/user/agent/general
    - Opt-out Exclusion: skips excluded path prefixes (testing by default) and non-CRUD methods
    - Error Resilience: context capture or injection failures are logged, never break the response

Classes:
    PerformanceMiddleware(): Times tracked requests, manages log context, injects performance metrics

Functions:
    No functions defined (module exposes only the PerformanceMiddleware class)

Dependencies:
    - fastapi: Request/Response middleware framework
    - starlette: ASGI middleware base components
    - json: Response body parsing and modification
    - uuid: Request ID generation for tracing
    - app.utils.performance: PerformanceCollector and clear_request_performance
    - app.core.logging: Application logger
    - app.logs.database_handler: set/clear/update log and business context helpers
    - app.logs.enhanced_logging: Error context enrichment

Updates:
    27-Feb-2026: Initial creation with automatic performance injection
    12-Mar-2026: Added log context capture for enhanced database logging
    13-Mar-2026: request_id uses full UUID; added business context, payload logging and performance metrics.
    26-Aug-2026: Errors re-raise without re-running the request; one enriched error record per 500.

Notes:
    Middleware processes all /api/ routes automatically.
    Preserves existing performance fields in endpoint responses.
    Handles both JSON and non-JSON response formats gracefully.
"""

import json
import uuid
from typing import Optional
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.utils.performance import PerformanceCollector, clear_request_performance
from app.core.logging import logger
from app.logs.database_handler import (
    set_log_context, clear_log_context, update_business_context, update_performance_metrics
)
from app.logs.enhanced_logging import enrich_error_context


class PerformanceMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware for automatic performance metrics injection.
    
    Features:
        - Automatic timing for all API endpoints
        - Granular database and external API breakdown
        - Cache hit/miss detection
        - Seamless response body injection
        - Preserves existing performance metrics
    
    Args:
        app: FastAPI application instance.
        exclude_paths: List of paths to exclude from tracking. Defaults to test endpoints.
        api_prefix: API path prefix to monitor. Defaults to "/api".
    
    Example:
        app.add_middleware(PerformanceMiddleware, exclude_paths=["/api/testing"])
    """
    
    def __init__(self, app: ASGIApp, exclude_paths: Optional[list] = None, api_prefix: str = "/api"):
        """Store the excluded path prefixes, defaulting to the testing routes, and the tracked API prefix."""
        super().__init__(app)
        self.exclude_paths = exclude_paths or ["/api/testing", "/api/v1/testing"]
        self.api_prefix = api_prefix
    
    async def dispatch(self, request: Request, call_next):
        """Set request log context, time the request, log unhandled errors, and inject performance metrics into JSON responses."""
        clear_request_performance()
        
        clear_log_context()
        
        if not self._should_track_performance(request):
            return await call_next(request)
        
        endpoint_name = self._extract_endpoint_name(request)
        
        request_id = str(uuid.uuid4())
        full_endpoint = request.url.path
        if request.url.query:
            full_endpoint += "?" + request.url.query

        userprofile_id = self._extract_userprofile_id(request)
        contact_id = self._extract_contact_id(request)

        set_log_context(
            request_id=request_id,
            endpoint=full_endpoint,
            method=request.method,
            userprofile_id=userprofile_id,
            contact_id=contact_id,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        
        await self._capture_request_context(request)
        
        collector = PerformanceCollector(endpoint_name)
        collector.start_timing()
        
        try:
            response = await call_next(request)

            response_time_ms = int(collector.get_total_time() * 1000) if hasattr(collector, 'get_total_time') else None
            set_log_context(
                status_code=response.status_code,
                response_time_ms=response_time_ms
            )
            
            await self._capture_response_context(response, request)
            
            self._update_performance_metrics(collector)

            if response.headers.get("X-Performance-Injected") == "1":
                return response
            
            return await self._inject_performance_metrics(response, collector)
            
        except Exception as e:
            set_log_context(status_code=500)
            logger.error(
                "Unhandled error on %s %s | request_id=%s | contact_id=%s | userprofile_id=%s | client=%s | %s: %s",
                request.method, full_endpoint, request_id, contact_id, userprofile_id,
                request.client.host if request.client else "unknown",
                type(e).__name__, e,
                extra={'extra_data': {'enhanced_error_context': enrich_error_context(e, {
                    'middleware': 'PerformanceMiddleware',
                    'endpoint': endpoint_name,
                    'request_id': request_id,
                })}},
                exc_info=True,
            )
            request.state.error_logged = True
            raise
        finally:
            clear_request_performance()
            clear_log_context()
    
    def _should_track_performance(self, request: Request) -> bool:
        """Return True for standard-method requests under the API prefix that are not excluded."""
        path = request.url.path
        
        if not path.startswith(self.api_prefix):
            return False
        
        for exclude_path in self.exclude_paths:
            if path.startswith(exclude_path):
                return False
        
        if request.method not in ["GET", "POST", "PUT", "PATCH", "DELETE"]:
            return False
        
        return True
    
    def _extract_endpoint_name(self, request: Request) -> str:
        """Return the path without its /api or /api/v1 prefix, slashes as underscores, or "root"."""
        path = request.url.path
        
        if path.startswith("/api/v1/"):
            path = path[8:]
        elif path.startswith("/api/"):
            path = path[5:]
        
        endpoint_name = path.replace("/", "_").strip("_")
        
        if not endpoint_name:
            endpoint_name = "root"
        
        return endpoint_name
    
    async def _inject_performance_metrics(self, response: Response, collector: PerformanceCollector) -> Response:
        """Rebuild a JSON response with a merged performance block, wrapping non-dict bodies under data."""
        try:
            performance_data = collector.get_metrics()
            
            if not self._is_json_response(response):
                return response
            
            body = b""
            try:
                async for chunk in response.body_iterator:
                    if chunk:
                        body += chunk
            except Exception as e:
                logger.debug(f"Failed to read response body iterator: {e}")
                return response
            
            if not body:
                logger.debug("Response body is empty, skipping metrics injection")
                return response
            
            try:
                body_text = body.decode('utf-8')
                if not body_text.strip():
                    logger.debug("Response body is empty or whitespace only")
                    return response
                    
                response_data = json.loads(body_text)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.debug(f"Failed to parse response body as JSON: {e}")
                return Response(
                    content=body,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type
                )
            
            if isinstance(response_data, dict):
                if 'performance' in response_data:
                    existing_perf = response_data['performance']
                    if isinstance(existing_perf, dict):
                        for key, value in performance_data.items():
                            if key not in existing_perf:
                                existing_perf[key] = value
                    else:
                        response_data['performance'] = performance_data
                else:
                    response_data['performance'] = performance_data
                    
                modified_response_data = response_data
                
            elif isinstance(response_data, list):
                modified_response_data = {
                    "data": response_data,
                    "performance": performance_data
                }
                
            else:
                modified_response_data = {
                    "data": response_data,
                    "performance": performance_data
                }
            
            try:
                new_body_text = json.dumps(modified_response_data, ensure_ascii=False, separators=(',', ':'))
                new_body_bytes = new_body_text.encode('utf-8')
            except (TypeError, ValueError) as e:
                logger.debug(f"Failed to serialize response with metrics: {e}")
                return Response(
                    content=body,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type
                )
            
            headers = dict(response.headers)
            
            headers.pop('content-length', None)
            headers.pop('Content-Length', None)
            
            headers['content-length'] = str(len(new_body_bytes))
            
            logger.debug(f"Injected performance metrics, content-length: {len(new_body_bytes)}")
            
            return Response(
                content=new_body_bytes,
                status_code=response.status_code,
                headers=headers,
                media_type="application/json"
            )
        
        except Exception as e:
            logger.error(f"Failed to inject performance metrics: {e}", exc_info=True)
            try:
                if 'body' in locals() and body:
                    return Response(
                        content=body,
                        status_code=response.status_code,
                        headers=dict(response.headers),
                        media_type=response.media_type
                    )
            except Exception:
                logger.warning("Failed to rebuild the response after metrics injection failed", exc_info=True)
        
        return response

    def _extract_userprofile_id(self, request: Request) -> Optional[str]:
        """Return the userprofile id from known query parameters or x-user-profile-id headers, or None."""
        query_params = request.query_params
        for param in ['user_profile_id', 'userprofileid', 'current_user_profile_id']:
            if param in query_params:
                return query_params[param]
            
        auth_header = request.headers.get('authorization', '')
        if auth_header.startswith('Bearer '):
            pass
        
        user_header = request.headers.get('x-user-profile-id') or request.headers.get('x-userprofile-id')
        if user_header:
            return user_header
        
        return None
    
    def _extract_contact_id(self, request: Request) -> Optional[str]:
        """Return the contact id from known query parameters, headers or cookies, or None."""
        query_params = request.query_params
        for param in ['contact_id', 'contactid', 'current_contact_id']:
            if param in query_params:
                return query_params[param]
        
        contact_header = request.headers.get('x-contact-id') or request.headers.get('x-contact')
        if contact_header:
            return contact_header
        
        if hasattr(request, 'cookies'):
            contact_cookie = request.cookies.get('contactid') or request.cookies.get('contact_id')
            if contact_cookie:
                return contact_cookie
        
        return None
    
    async def _capture_request_context(self, request: Request):
        """Record location, ids, search query, agent headers and a sanitized small body into the log business context."""
        try:
            location_data = {}
            query_params = request.query_params
            
            if 'latitude' in query_params and 'longitude' in query_params:
                location_data['coordinates'] = {
                    'lat': query_params.get('latitude'),
                    'lon': query_params.get('longitude')
                }
            
            if 'city' in query_params or 'location' in query_params:
                location_data['city'] = query_params.get('city') or query_params.get('location')
            
            provider_id = query_params.get('provider_id') or query_params.get('providerservicesid')
            service_id = query_params.get('service_id') or query_params.get('booking_id')
            search_query = query_params.get('query') or query_params.get('search') or query_params.get('term')
            
            agent_type = request.headers.get('x-agent-type') or query_params.get('agent_type')
            operation = request.headers.get('x-operation') or query_params.get('operation')
            
            agent_context_data = {}
            if agent_type:
                agent_context_data['agent_type'] = agent_type
            if operation:
                agent_context_data['operation'] = operation
            
            request_payload = None
            if request.method in ['POST', 'PUT', 'PATCH']:
                try:
                    body = await request.body()
                    if body and len(body) < 10000:
                        body_text = body.decode('utf-8')
                        if body_text.strip():
                            try:
                                request_payload = json.loads(body_text)
                                request_payload = self._sanitize_payload(request_payload)
                            except json.JSONDecodeError:
                                logger.debug("Request body is not JSON; storing a truncated raw copy in the log context")
                                request_payload = {'raw_data': body_text[:500]}
                except Exception:
                    logger.debug("Could not capture the request body for the log context", exc_info=True)
            
            update_business_context(
                provider_id=provider_id,
                service_id=service_id,
                search_query=search_query,
                location_context=location_data if location_data else None,
                agent_context=agent_context_data if agent_context_data else None,
                request_payload=request_payload
            )
            
        except Exception as e:
            logger.debug(f"Failed to capture request context: {e}")
    
    async def _capture_response_context(self, response: Response, request: Request):
        """Record status, content type, endpoint class and size of a successful JSON response into the log context."""
        try:
            if response.status_code < 400 and self._is_json_response(response):
                endpoint = request.url.path.lower()
                
                response_context = {
                    'status_code': response.status_code,
                    'content_type': response.headers.get('content-type'),
                    'endpoint_type': self._classify_endpoint(endpoint)
                }
                
                content_length = response.headers.get('content-length')
                if content_length:
                    response_context['response_size_bytes'] = int(content_length)
                
                update_business_context(response_payload=response_context)
                
        except Exception as e:
            logger.debug(f"Failed to capture response context: {e}")
    
    def _update_performance_metrics(self, collector: PerformanceCollector):
        """Copy timing and cache counters from the collector into the log performance metrics."""
        try:
            if hasattr(collector, 'get_metrics'):
                metrics = collector.get_metrics()
                
                performance_updates = {}
                
                if 'total_time' in metrics:
                    performance_updates['response_time_ms'] = metrics['total_time'] * 1000
                
                if 'database_time' in metrics:
                    performance_updates['db_query_time_ms'] = metrics['database_time'] * 1000
                    
                if 'external_api_time' in metrics:
                    performance_updates['external_api_time_ms'] = metrics['external_api_time'] * 1000
                
                if 'cache_hits' in metrics:
                    performance_updates['cache_hits'] = metrics['cache_hits']
                    
                if 'cache_misses' in metrics:
                    performance_updates['cache_misses'] = metrics['cache_misses']
                
                if performance_updates:
                    update_performance_metrics(**performance_updates)
                    
        except Exception as e:
            logger.debug(f"Failed to update performance metrics: {e}")
    
    def _sanitize_payload(self, payload: dict, max_depth: int = 3) -> dict:
        """Redact sensitive keys and truncate long strings, lists and deep nesting in a payload for logging."""
        if max_depth <= 0:
            return {'[truncated]': 'max_depth_reached'}
        
        if not isinstance(payload, dict):
            return payload
            
        sensitive_keys = {
            'password', 'token', 'secret', 'api_key', 'authorization',
            'private_key', 'access_token', 'refresh_token', 'session_id'
        }
        
        sanitized = {}
        for key, value in payload.items():
            if isinstance(key, str) and any(sensitive in key.lower() for sensitive in sensitive_keys):
                sanitized[key] = '[REDACTED]'
            elif isinstance(value, dict):
                sanitized[key] = self._sanitize_payload(value, max_depth - 1)
            elif isinstance(value, list) and len(value) > 0:
                sanitized[key] = [self._sanitize_payload(item, max_depth - 1) if isinstance(item, dict) else item for item in value[:5]]
                if len(value) > 5:
                    sanitized[key].append(f'[...{len(value) - 5} more items]')
            elif isinstance(value, str) and len(value) > 1000:
                sanitized[key] = value[:1000] + '[truncated]'
            else:
                sanitized[key] = value
                
        return sanitized
    
    def _classify_endpoint(self, endpoint_path: str) -> str:
        """Return an operation class for the path by keyword: provider, search, booking, user, chat or general."""
        if 'provider' in endpoint_path:
            return 'provider_operation'
        elif 'search' in endpoint_path:
            return 'search_operation'
        elif 'booking' in endpoint_path:
            return 'booking_operation'
        elif 'user' in endpoint_path:
            return 'user_operation'
        elif 'chat' in endpoint_path:
            return 'agent_operation'
        else:
            return 'general_operation'
    
    def _is_json_response(self, response: Response) -> bool:
        """Return True when the response content type is application/json."""
        content_type = response.headers.get('content-type', '')
        return 'application/json' in content_type.lower()
