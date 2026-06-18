"""Health and readiness routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from src.api.dependencies import get_connectivity_probe, get_structuring_service
from src.config.settings import Settings, get_settings
from src.services.llm.structuring_service import OllamaStructuringService
from src.services.sync.connectivity import ConnectivityProbe

router = APIRouter(tags=["health"])


@router.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict:
    """Liveness probe — does not check downstream dependencies."""
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}


@router.get("/health/ready")
def readiness(
    llm: OllamaStructuringService = Depends(get_structuring_service),
    connectivity: ConnectivityProbe = Depends(get_connectivity_probe),
) -> dict:
    """
    Readiness probe — checks that Ollama is reachable and reports current
    connectivity state to the central server. Whisper model load state is
    intentionally not blocked on here since it's loaded once at startup
    and a failure there should fail the container, not just this route.
    """
    ollama_ok = llm.health_check()
    conn_state = connectivity.last_known_state.value
    return {
        "status": "ok" if ollama_ok else "degraded",
        "ollama_reachable": ollama_ok,
        "central_server_connectivity": conn_state,
    }
