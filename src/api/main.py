"""
FieldOpsIQ FastAPI application entrypoint.

Run with: uvicorn src.api.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from src.api.dependencies import get_stt_service
from src.api.middleware.audit import AuditLoggingMiddleware
from src.api.middleware.rate_limit import RateLimitMiddleware
from src.api.routes import health, jobs, sync
from src.config.settings import get_settings
from src.domain.exceptions import (
    AuthenticationError,
    FieldOpsIQError,
    RateLimitExceededError,
)
from src.observability.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_directories()
    settings.validate_production_secrets()

    logger.info("startup_begin", environment=settings.environment)
    # Eagerly load the Whisper model at startup rather than on first
    # request, so the first field recording doesn't pay the (multi-second)
    # model load cost.
    get_stt_service()
    logger.info("startup_complete")

    yield

    logger.info("shutdown")


app = FastAPI(
    title="FieldOpsIQ",
    description="Offline STT + LLM pipeline for field operations reporting.",
    version="1.0.0",
    lifespan=lifespan,
)

settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(AuditLoggingMiddleware)

app.include_router(health.router)
app.include_router(jobs.router)
app.include_router(sync.router)


@app.exception_handler(AuthenticationError)
async def handle_auth_error(request: Request, exc: AuthenticationError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": str(exc)})


@app.exception_handler(RateLimitExceededError)
async def handle_rate_limit_error(request: Request, exc: RateLimitExceededError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_429_TOO_MANY_REQUESTS, content={"detail": str(exc)})


@app.exception_handler(FieldOpsIQError)
async def handle_domain_error(request: Request, exc: FieldOpsIQError) -> JSONResponse:
    logger.error("unhandled_domain_error", error=str(exc), error_type=type(exc).__name__)
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": str(exc)})


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
