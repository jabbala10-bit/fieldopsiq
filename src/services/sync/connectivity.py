"""
Connectivity probe.

Field devices move between fully offline, intermittently connected
(satellite/cellular dead zones), and online. Rather than letting every
sync attempt time out individually, we run a lightweight periodic probe
and let the sync worker check cached state (ADR-007: probe-then-sync,
not sync-and-hope).
"""
from __future__ import annotations

import time

import httpx

from src.config.settings import Settings, get_settings
from src.domain.constants import CONNECTIVITY_PROBE_TIMEOUT_SECONDS
from src.domain.schemas import ConnectivityState
from src.observability.logging import get_logger
from src.observability.metrics import CONNECTIVITY_STATE

logger = get_logger(__name__)

# Latency above this, while still reachable, is reported as "degraded" so
# the UI can warn the operator before a large batch sync is attempted.
_DEGRADED_LATENCY_THRESHOLD_SECONDS = 2.0


class ConnectivityProbe:
    """Checks reachability of the central server's health endpoint."""

    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None):
        self._settings = settings or get_settings()
        self._client = client or httpx.Client(timeout=CONNECTIVITY_PROBE_TIMEOUT_SECONDS)
        self._last_state: ConnectivityState = ConnectivityState.OFFLINE

    @property
    def last_known_state(self) -> ConnectivityState:
        """Returns the most recently observed state without making a new request."""
        return self._last_state

    def check(self) -> ConnectivityState:
        """
        Performs a live reachability check against the central server.

        This makes a real network call — callers on a tight loop should
        prefer `last_known_state` and only call `check()` on the
        configured interval (CONNECTIVITY_CHECK_INTERVAL_SECONDS).
        """
        base_url = self._settings.central_server_url
        health_url = self._derive_health_url(base_url)

        start = time.monotonic()
        try:
            resp = self._client.get(health_url)
            elapsed = time.monotonic() - start
            if resp.status_code < 500:
                state = (
                    ConnectivityState.DEGRADED
                    if elapsed > _DEGRADED_LATENCY_THRESHOLD_SECONDS
                    else ConnectivityState.ONLINE
                )
            else:
                state = ConnectivityState.OFFLINE
        except httpx.RequestError as exc:
            logger.debug("connectivity_probe_failed", error=str(exc))
            state = ConnectivityState.OFFLINE

        self._last_state = state
        CONNECTIVITY_STATE.set(1 if state == ConnectivityState.ONLINE else 0)
        logger.info("connectivity_checked", state=state.value)
        return state

    @staticmethod
    def _derive_health_url(base_url: str) -> str:
        """Derives a /health endpoint from the configured reports URL."""
        if base_url.rstrip("/").endswith("/health"):
            return base_url
        # e.g. https://ops-central.example.com/api/v1/reports -> .../health
        parts = base_url.rstrip("/").split("/")
        if len(parts) >= 2:
            parts[-1] = "health"
            return "/".join(parts)
        return base_url.rstrip("/") + "/health"

    def close(self) -> None:
        self._client.close()
