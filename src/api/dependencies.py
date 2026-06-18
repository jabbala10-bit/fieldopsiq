"""
FastAPI dependency providers.

Centralizing service construction here (rather than instantiating inside
each route) makes it trivial to override dependencies in tests via
FastAPI's `app.dependency_overrides`, matching the pattern used in
ManufactureIQ's dependencies.py.
"""
from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Header, HTTPException, status

from src.config.settings import Settings, get_settings
from src.domain.exceptions import AuthenticationError
from src.services.llm.structuring_service import OllamaStructuringService
from src.services.pipeline import FieldOpsPipeline
from src.services.storage.sqlite_service import SQLiteStorageService
from src.services.stt.preprocessor import AudioPreprocessor
from src.services.stt.whisper_service import WhisperSTTService
from src.services.sync.connectivity import ConnectivityProbe
from src.services.sync.sync_queue import SyncQueueWorker


@lru_cache
def get_stt_service() -> WhisperSTTService:
    service = WhisperSTTService(get_settings())
    service.load_model()
    return service


@lru_cache
def get_preprocessor() -> AudioPreprocessor:
    return AudioPreprocessor()


@lru_cache
def get_structuring_service() -> OllamaStructuringService:
    return OllamaStructuringService(get_settings())


@lru_cache
def get_storage_service() -> SQLiteStorageService:
    return SQLiteStorageService(get_settings())


@lru_cache
def get_connectivity_probe() -> ConnectivityProbe:
    return ConnectivityProbe(get_settings())


@lru_cache
def get_sync_worker() -> SyncQueueWorker:
    return SyncQueueWorker(get_settings(), get_connectivity_probe())


def get_pipeline(
    settings: Settings = Depends(get_settings),
    stt: WhisperSTTService = Depends(get_stt_service),
    preprocessor: AudioPreprocessor = Depends(get_preprocessor),
    llm: OllamaStructuringService = Depends(get_structuring_service),
    storage: SQLiteStorageService = Depends(get_storage_service),
    sync: SyncQueueWorker = Depends(get_sync_worker),
) -> FieldOpsPipeline:
    return FieldOpsPipeline(
        settings=settings,
        stt_service=stt,
        preprocessor=preprocessor,
        structuring_service=llm,
        storage=storage,
        sync_worker=sync,
    )


def require_api_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """
    Simple bearer-token check for field-device-to-API-gateway auth.

    Skipped entirely in development to ease local testing; enforced in
    staging/production (see ADR-009 security model — this API typically
    runs on localhost on the field device itself, with the *next* hop
    to the central server being the one with internet-facing auth).
    """
    if settings.environment == "development":
        return
    if not settings.api_auth_token:
        raise AuthenticationError("Server has no API_AUTH_TOKEN configured.")
    expected = f"Bearer {settings.api_auth_token}"
    if authorization != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing token")
