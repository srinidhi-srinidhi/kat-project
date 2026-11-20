"""HTTP Client Helpers - shared outbound HTTP client with per-request timing.

Utility module providing TrackedHTTPClient, which wraps httpx and aiohttp and records each external call's duration in the request performance context, plus a shared pooled instance from get_http_client() and one-shot tracked request helpers.

Features:
    - Performance tracking: Automatic timing for external API calls
    - Connection reuse: Shared client sessions for efficiency
    - Timeout management: Configurable timeouts with fallbacks
    - Error handling: Comprehensive HTTP error management
    - Async support: Full asyncio compatibility
    - Context integration: Performance metrics stored in request context
    - Async context manager: Proper support for aiohttp async context managers

Classes:
    TrackedHTTPClient: HTTP client with automatic performance tracking
    TrackedHTTPClient._AIOHTTPTrackedRequest: Nested async context manager for aiohttp requests
    TrackedHTTPClient._HTTPXTrackedRequest: Nested async context manager for httpx requests

Functions:
    get_http_client(): Yield the shared TrackedHTTPClient, or a private one for custom settings
    close_shared_http_client(): Close the shared HTTP client pool during application shutdown
    make_tracked_request(): Single tracked request, returning parsed JSON or raw bytes
    tracked_get(): Convenience tracked GET request wrapper
    tracked_post(): Convenience tracked POST request wrapper

Dependencies:
    - httpx: Modern async HTTP client
    - aiohttp: Alternative async HTTP implementation
    - app.utils.performance: Performance context tracking
    - app.core.logging: Request logging utilities

Updates:
    27-Feb-2026: Initial creation with performance tracking integration
    26-Mar-2026: Fixed async context manager protocol for both await and async with patterns
    26-Aug-2026: Logged the httpx/aiohttp/performance import guards.

Notes:
    Provides drop-in replacement for direct httpx/aiohttp usage.
    Automatically tracks timing in performance context for middleware collection.
    Supports both httpx and aiohttp request patterns for compatibility.
    HTTP methods support both await and async with patterns:
    - Legacy: await client.get(url) 
    - Modern: async with client.get(url) as response:
"""

import logging
import time
import asyncio
from typing import Optional, Dict, Any, Union, AsyncGenerator, AsyncContextManager
from contextlib import asynccontextmanager

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    logging.getLogger(__name__).warning("httpx is not installed; the shared HTTP client will fall back to aiohttp")
    HTTPX_AVAILABLE = False

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    logging.getLogger(__name__).warning("aiohttp is not installed; the shared HTTP client will fall back to httpx")
    AIOHTTP_AVAILABLE = False

from app.core.logging import logger

try:
    from app.utils.performance import track_performance_metrics as _track_performance_metrics
except Exception:
    logger.debug("http client: performance tracking unavailable", exc_info=True)
    _track_performance_metrics = None

_DEFAULT_SHARED_TIMEOUT = 30.0
_shared_client: Optional["TrackedHTTPClient"] = None
_shared_client_lock = asyncio.Lock()


class TrackedHTTPClient:
    """HTTP client with automatic performance tracking.
    
    Features:
        - Tracks external API call timing
        - Stores metrics in performance context
        - Provides both httpx and aiohttp interfaces
        - Handles timeouts and retries gracefully
    
    Args:
        timeout: Default timeout in seconds. Defaults to 30.0.
        track_performance: Whether to track timing metrics. Defaults to True.
    
    Example:
        async with TrackedHTTPClient() as client:
            response = await client.get("https://api.example.com/data")
    """
    
    def __init__(self, timeout: float = 30.0, track_performance: bool = True):
        """Store the timeout and tracking flag; sessions are opened on context entry."""
        self.timeout = timeout
        self.track_performance = track_performance
        self._httpx_client: Optional[httpx.AsyncClient] = None
        self._aiohttp_session: Optional[aiohttp.ClientSession] = None
    
    async def __aenter__(self):
        """Open a pooled httpx client, or an aiohttp session as fallback, and return self.

        Raises:
            RuntimeError: When neither httpx nor aiohttp is installed.
        """
        if HTTPX_AVAILABLE:
            limits = httpx.Limits(
                max_keepalive_connections=20,
                max_connections=50,
                keepalive_expiry=30.0
            )
            self._httpx_client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=limits,
                http2=True
            )
        elif AIOHTTP_AVAILABLE:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            connector = aiohttp.TCPConnector(
                limit=50,
                limit_per_host=20,
                ttl_dns_cache=300
            )
            self._aiohttp_session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector
            )
        else:
            raise RuntimeError("No HTTP client available. Install httpx or aiohttp.")
        
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Close whichever httpx client or aiohttp session is open."""
        if self._httpx_client:
            await self._httpx_client.aclose()
        if self._aiohttp_session:
            await self._aiohttp_session.close()
    
    def get(self, url: str, **kwargs) -> Union['_HTTPXTrackedRequest', '_AIOHTTPTrackedRequest']:
        """Return a tracked GET request usable with await or async with."""
        request_obj = self._tracked_request("GET", url, **kwargs)
        return request_obj
    
    def post(self, url: str, **kwargs) -> Union['_HTTPXTrackedRequest', '_AIOHTTPTrackedRequest']:
        """Return a tracked POST request usable with await or async with."""
        request_obj = self._tracked_request("POST", url, **kwargs)
        return request_obj
    
    def put(self, url: str, **kwargs) -> Union['_HTTPXTrackedRequest', '_AIOHTTPTrackedRequest']:
        """Return a tracked PUT request usable with await or async with."""
        request_obj = self._tracked_request("PUT", url, **kwargs)
        return request_obj
    
    def delete(self, url: str, **kwargs) -> Union['_HTTPXTrackedRequest', '_AIOHTTPTrackedRequest']:
        """Return a tracked DELETE request usable with await or async with."""
        request_obj = self._tracked_request("DELETE", url, **kwargs)
        return request_obj
    
    def _tracked_request(self, method: str, url: str, **kwargs) -> Union[AsyncContextManager, 'httpx.Response']:
        """Wrap a request in the tracked-request object for whichever backend is open.

        Raises:
            RuntimeError: When no client session has been opened.
        """
        operation_name = f"http_{method.lower()}_{self._extract_domain(url)}"
        
        if self._httpx_client:
            return self._HTTPXTrackedRequest(self._httpx_client, method, url, operation_name, self, **kwargs)
        elif self._aiohttp_session:
            return self._AIOHTTPTrackedRequest(self._aiohttp_session, method, url, operation_name, self, **kwargs)
        else:
            raise RuntimeError("No HTTP client initialized")
    
    async def _httpx_tracked_request(self, client, method: str, url: str, operation_name: str, **kwargs):
        """Send an httpx request, record its duration under operation_name, and return the response."""
        start_time = time.time()
        try:
            response = await client.request(method, url, **kwargs)
            return response
            
        except Exception as e:
            logger.warning(f"HTTP request failed to {url}: {e}")
            raise
        finally:
            if self.track_performance:
                duration_ms = (time.time() - start_time) * 1000
                self._track_timing(operation_name, duration_ms)
    
    class _AIOHTTPTrackedRequest:
        """Async context manager for aiohttp requests with performance tracking."""
        
        def __init__(self, session, method: str, url: str, operation_name: str, tracker, **kwargs):
            """Store the aiohttp session, request details and the tracker that records timing."""
            self.session = session
            self.method = method
            self.url = url
            self.operation_name = operation_name
            self.tracker = tracker
            self.kwargs = kwargs
            self.start_time = None
            self.context_manager = None
        
        async def __aenter__(self):
            """Start the timer, open the aiohttp request and return its response."""
            self.start_time = time.time()
            self.context_manager = self.session.request(self.method, self.url, **self.kwargs)
            return await self.context_manager.__aenter__()
        
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            """Close the aiohttp request and record its duration."""
            try:
                return await self.context_manager.__aexit__(exc_type, exc_val, exc_tb)
            except Exception as e:
                if exc_type is None:
                    logger.warning(f"HTTP request failed to {self.url}: {e}")
                raise
            finally:
                if self.tracker.track_performance and self.start_time:
                    duration_ms = (time.time() - self.start_time) * 1000
                    self.tracker._track_timing(self.operation_name, duration_ms)
        
        def __await__(self):
            """Await the request directly by running it inside its own context manager."""
            async def _run():
                async with self as response:
                    return response
            return _run().__await__()
    
    class _HTTPXTrackedRequest:
        """Async context manager for httpx requests with performance tracking."""
        
        def __init__(self, client, method: str, url: str, operation_name: str, tracker, **kwargs):
            """Store the httpx client, request details and the tracker that records timing."""
            self.client = client
            self.method = method
            self.url = url
            self.operation_name = operation_name
            self.tracker = tracker
            self.kwargs = kwargs
            self.start_time = None
            self.response = None
        
        async def __aenter__(self):
            """Start the timer, send the httpx request and return its response."""
            self.start_time = time.time()
            try:
                self.response = await self.client.request(self.method, self.url, **self.kwargs)
                return self.response
            except Exception as e:
                logger.warning(f"HTTP request failed to {self.url}: {e}")
                raise
        
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            """Close the httpx response and record its duration."""
            try:
                if self.response and hasattr(self.response, 'aclose'):
                    await self.response.aclose()
            finally:
                if self.tracker.track_performance and self.start_time:
                    duration_ms = (time.time() - self.start_time) * 1000
                    self.tracker._track_timing(self.operation_name, duration_ms)
            return None
        
        def __await__(self):
            """Await the request directly by running it inside its own context manager."""
            async def _run():
                async with self as response:
                    return response
            return _run().__await__()
    
    def _extract_domain(self, url: str) -> str:
        """Return the last two host labels joined by an underscore, 'local' for non-http URLs, or 'unknown'."""
        try:
            if url.startswith('http'):
                domain = url.split('/')[2]
                if ':' in domain:
                    domain = domain.split(':')[0]
                parts = domain.split('.')
                if len(parts) >= 2:
                    return f"{parts[-2]}_{parts[-1]}"
                return domain.replace('.', '_')
            return "local"
        except Exception:
            logger.debug("http client: could not extract a domain from the URL")
            return "unknown"
    
    def _track_timing(self, operation_name: str, duration_ms: float):
        """Record an external_api timing in the performance context, or debug-log it when tracking is unavailable."""
        if _track_performance_metrics is not None:
            _track_performance_metrics("external_api", operation_name, duration_ms)
        else:
            logger.debug(f"[HTTP_PERF] {operation_name}: {duration_ms:.1f}ms")


@asynccontextmanager
async def get_http_client(timeout: float = 30.0, track_performance: bool = True):
    """Yield the lazily created shared client for default settings, or a short-lived client for any other settings."""
    global _shared_client

    should_use_shared = (
        timeout == _DEFAULT_SHARED_TIMEOUT and
        track_performance is True
    )

    if not should_use_shared:
        async with TrackedHTTPClient(timeout=timeout, track_performance=track_performance) as client:
            yield client
        return

    if _shared_client is None:
        async with _shared_client_lock:
            if _shared_client is None:
                shared_client = TrackedHTTPClient(
                    timeout=_DEFAULT_SHARED_TIMEOUT,
                    track_performance=True,
                )
                await shared_client.__aenter__()
                _shared_client = shared_client
                logger.info("Initialized shared HTTP client pool")

    yield _shared_client


async def close_shared_http_client() -> None:
    """Close and discard the shared HTTP client, if one was created."""
    global _shared_client

    async with _shared_client_lock:
        if _shared_client is not None:
            await _shared_client.__aexit__(None, None, None)
            _shared_client = None
            logger.info("Closed shared HTTP client pool")


async def make_tracked_request(method: str, url: str, timeout: float = 30.0, **kwargs) -> Union[Dict[str, Any], bytes]:
    """Send one tracked request and return its JSON body, falling back to raw bytes, or {}."""
    async with get_http_client(timeout=timeout) as client:
        response = await client._tracked_request(method, url, **kwargs)
        
        if hasattr(response, 'json'):
            try:
                return await response.json() if asyncio.iscoroutine(response.json()) else response.json()
            except Exception:
                logger.debug("http client: response body is not JSON", exc_info=True)
        
        if hasattr(response, 'content'):
            return response.content
        elif hasattr(response, 'read'):
            return await response.read()
        
        return {}


async def tracked_get(url: str, timeout: float = 30.0, **kwargs) -> Union[Dict[str, Any], bytes]:
    """Send a tracked GET request and return its JSON body or raw bytes."""
    return await make_tracked_request("GET", url, timeout=timeout, **kwargs)


async def tracked_post(url: str, timeout: float = 30.0, **kwargs) -> Union[Dict[str, Any], bytes]:
    """Send a tracked POST request and return its JSON body or raw bytes."""
    return await make_tracked_request("POST", url, timeout=timeout, **kwargs)
