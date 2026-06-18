"""
Centralized configuration for FieldOpsIQ using Pydantic Settings.

All tunables are environment-variable overridable so the same Docker
image can run in dev / staging / field-edge-device modes without a
rebuild (ADR-006: 12-factor config).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.domain.constants import (
    DEFAULT_AUDIO_INBOX_DIR,
    DEFAULT_BEAM_SIZE,
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_TEMPERATURE,
    DEFAULT_RATE_LIMIT_PER_MINUTE,
    DEFAULT_REPORT_DIR,
    DEFAULT_SQLITE_PATH,
    DEFAULT_TRANSCRIPT_DIR,
    DEFAULT_WHISPER_COMPUTE_TYPE,
    DEFAULT_WHISPER_MODEL,
    SYNC_MAX_ATTEMPTS,
)


class Settings(BaseSettings):
    """Application settings. Override any field via env var of the same name."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name: str = "FieldOpsIQ"
    environment: str = Field(default="development")  # development|staging|production
    debug: bool = False
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # STT (faster-whisper)
    whisper_model_size: str = DEFAULT_WHISPER_MODEL
    whisper_device: str = "cpu"  # cpu|cuda — edge devices default to cpu
    whisper_compute_type: str = DEFAULT_WHISPER_COMPUTE_TYPE
    whisper_beam_size: int = DEFAULT_BEAM_SIZE
    whisper_model_cache_dir: str = "models/whisper"

    # LLM structuring (Ollama)
    ollama_base_url: str = "http://localhost:11434"
    llm_model: str = DEFAULT_LLM_MODEL
    llm_temperature: float = DEFAULT_LLM_TEMPERATURE

    # Sync
    central_server_url: str = "https://ops-central.example.com/api/v1/reports"
    central_server_api_key: str = Field(default="", repr=False)
    sync_max_attempts: int = SYNC_MAX_ATTEMPTS
    sync_enabled: bool = True

    # Storage paths
    sqlite_path: str = DEFAULT_SQLITE_PATH
    audio_inbox_dir: str = DEFAULT_AUDIO_INBOX_DIR
    transcript_dir: str = DEFAULT_TRANSCRIPT_DIR
    report_dir: str = DEFAULT_REPORT_DIR

    # Security
    api_auth_token: str = Field(default="", repr=False)
    rate_limit_per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE
    cors_allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:7860"])

    # Observability
    log_level: str = "INFO"
    log_format: str = "json"  # json|console
    metrics_enabled: bool = True

    @field_validator("environment")
    @classmethod
    def _validate_environment(cls, v: str) -> str:
        allowed = {"development", "staging", "production"}
        if v not in allowed:
            raise ValueError(f"environment must be one of {allowed}, got '{v}'")
        return v

    @field_validator("whisper_device")
    @classmethod
    def _validate_device(cls, v: str) -> str:
        allowed = {"cpu", "cuda"}
        if v not in allowed:
            raise ValueError(f"whisper_device must be one of {allowed}, got '{v}'")
        return v

    def validate_production_secrets(self) -> None:
        """
        Called at startup when environment == production.

        Fails fast rather than silently running with an empty auth token
        or API key, mirroring BioMedIQ's ConfigurationError pattern.
        """
        from src.domain.exceptions import ConfigurationError

        if self.environment != "production":
            return
        missing = []
        if not self.api_auth_token:
            missing.append("API_AUTH_TOKEN")
        if self.sync_enabled and not self.central_server_api_key:
            missing.append("CENTRAL_SERVER_API_KEY")
        if missing:
            raise ConfigurationError(
                f"Missing required production secrets: {', '.join(missing)}"
            )

    def ensure_directories(self) -> None:
        """Create local storage directories if they don't already exist."""
        for path_str in (
            self.audio_inbox_dir,
            self.transcript_dir,
            self.report_dir,
            self.whisper_model_cache_dir,
        ):
            Path(path_str).mkdir(parents=True, exist_ok=True)
        Path(self.sqlite_path).parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton — import this, don't instantiate Settings() directly."""
    return Settings()
