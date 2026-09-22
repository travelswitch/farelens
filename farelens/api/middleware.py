"""Request-id propagation, security headers and a sliding-window rate limiter."""

from __future__ import annotations

import logging
import time
import uuid
from asyncio import Lock
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from farelens.core.errors import error_response

logger = logging.getLogger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        incoming = (request.headers.get("x-request-id") or "").strip()
        rid = incoming[:128] if incoming else str(uuid.uuid4())
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window limiter for /api/v1, keyed by API key (or client IP)."""

    def __init__(self, app, requests: int = 120, window_seconds: int = 60):
        super().__init__(app)
        self._limit = max(1, requests)
        self._window = max(1, window_seconds)
        self._hits: dict[str, deque[float]] = {}
        self._lock = Lock()
        self._last_prune = time.monotonic()

    @staticmethod
    def _client_key(request: Request) -> str:
        api_key = request.headers.get("x-api-key") or ""
        if not api_key:
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                api_key = auth[7:]
        if api_key:
            return f"key:{api_key[-12:]}"
        forwarded = request.headers.get("x-forwarded-for")
        ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
        return f"ip:{ip}"

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/api/v1"):
            return await call_next(request)

        key = self._client_key(request)
        now = time.monotonic()
        async with self._lock:
            bucket = self._hits.setdefault(key, deque())
            cutoff = now - self._window
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._limit:
                retry_after = int(bucket[0] + self._window - now) + 1
                response = error_response(
                    request,
                    status_code=429,
                    message="Rate limit exceeded.",
                    code="TOO_MANY_REQUESTS",
                    hint=f"Retry after {retry_after}s.",
                )
                response.headers["Retry-After"] = str(retry_after)
                return response
            bucket.append(now)

            if now - self._last_prune > self._window and len(self._hits) > 1000:
                self._hits = {k: v for k, v in self._hits.items() if v and v[-1] >= cutoff}
                self._last_prune = now

        return await call_next(request)
