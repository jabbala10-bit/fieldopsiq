"""
Rate-limiting middleware.

A simple in-memory sliding-window limiter, keyed by client IP. This is
intentionally lightweight (no Redis dependency) because FieldOpsIQ runs
as a single-instance process on a field device — there's no multi-node
deployment to coordinate rate limits across (see ADR-009).
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.config.settings import get_settings
from src.observability.logging import get_logger

logger = get_logger(__name__)

_WINDOW_SECONDS = 60


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Limits each client IP to N requests per rolling 60-second window."""

    def __init__(self, app):
        super().__init__(app)
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()
        limit = settings.rate_limit_per_minute
        client_ip = request.client.host if request.client else "unknown"

        now = time.monotonic()
        window = self._requests[client_ip]
        while window and now - window[0] > _WINDOW_SECONDS:
            window.popleft()

        if len(window) >= limit:
            logger.warning("rate_limit_exceeded", client_ip=client_ip)
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded: {limit} requests per minute."},
            )

        window.append(now)
        return await call_next(request)
