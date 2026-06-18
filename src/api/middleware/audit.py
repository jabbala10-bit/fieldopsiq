"""
Audit logging middleware.

Logs every request with method, path, status, latency, and client IP in
structured JSON — required for ISO 27001 / SOC 2 access-logging controls
(ADR-009 covers the full compliance mapping). Never logs request bodies
since they may contain transcript text with PII from field sites.
"""
from __future__ import annotations

import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.observability.logging import get_logger

logger = get_logger("audit")


class AuditLoggingMiddleware(BaseHTTPMiddleware):
    """Structured audit trail for every API request."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = str(uuid.uuid4())
        start = time.monotonic()
        client_ip = request.client.host if request.client else "unknown"

        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = round((time.monotonic() - start) * 1000, 2)
            logger.error(
                "request_unhandled_exception",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                client_ip=client_ip,
                elapsed_ms=elapsed_ms,
                error=str(exc),
            )
            raise

        elapsed_ms = round((time.monotonic() - start) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_completed",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            client_ip=client_ip,
            elapsed_ms=elapsed_ms,
        )
        return response
