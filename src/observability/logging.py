"""
Structured logging for FieldOpsIQ using structlog.

All logs are emitted as JSON in production (for ingestion by a log
aggregator on the rare occasions a field device has connectivity) and
as readable console output in development. This mirrors the logging
setup used in ManufactureIQ, SupportIQ, BioMedIQ, and InferenceIQ.
"""
from __future__ import annotations

import logging
import sys

import structlog

from src.config.settings import get_settings

_configured = False


def configure_logging() -> None:
    """Idempotent logging configuration. Safe to call multiple times."""
    global _configured
    if _configured:
        return

    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str) -> structlog.BoundLogger:
    """Returns a structlog logger bound to the given module name."""
    configure_logging()
    return structlog.get_logger(name)
