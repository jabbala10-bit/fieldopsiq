"""
Typed domain exceptions for FieldOpsIQ.

Centralizing exceptions (rather than raising bare `Exception` or `ValueError`
everywhere) lets the API layer map failures to correct HTTP status codes,
and lets observability code log structured error categories. Same pattern
as ManufactureIQ's exceptions.py and BioMedIQ's exceptions.py.
"""


class FieldOpsIQError(Exception):
    """Base class for all domain-level errors in FieldOpsIQ."""


# --------------------------------------------------------------------------
# Audio / STT errors
# --------------------------------------------------------------------------

class AudioValidationError(FieldOpsIQError):
    """Raised when an uploaded/queued audio file fails basic validation."""


class UnsupportedAudioFormatError(AudioValidationError):
    """Raised when the audio container/codec is not in the allowed set."""


class AudioTooLongError(AudioValidationError):
    """Raised when audio duration exceeds the configured max (default 30 min)."""


class TranscriptionError(FieldOpsIQError):
    """Raised when the STT engine fails to produce a transcript."""


class ModelNotLoadedError(TranscriptionError):
    """Raised when an inference call is made before the Whisper model is loaded."""


# --------------------------------------------------------------------------
# LLM structuring errors
# --------------------------------------------------------------------------

class StructuringError(FieldOpsIQError):
    """Raised when the LLM fails to produce a parseable structured report."""


class LLMUnavailableError(StructuringError):
    """Raised when Ollama is unreachable (connection refused, timeout)."""


class SchemaValidationError(StructuringError):
    """Raised when the LLM's JSON output fails Pydantic validation after retries."""


# --------------------------------------------------------------------------
# Storage / sync errors
# --------------------------------------------------------------------------

class StorageError(FieldOpsIQError):
    """Raised on SQLite read/write failures."""


class SyncError(FieldOpsIQError):
    """Base class for sync-queue errors."""


class ConnectivityUnavailableError(SyncError):
    """Raised when a sync attempt is made while offline."""


class SyncExhaustedError(SyncError):
    """Raised when a record has exceeded MAX_ATTEMPTS and is moved to dead-letter."""


class RemoteRejectedError(SyncError):
    """Raised when the central server returns a non-2xx response (e.g. 4xx validation)."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


# --------------------------------------------------------------------------
# Config / auth errors
# --------------------------------------------------------------------------

class ConfigurationError(FieldOpsIQError):
    """Raised when required configuration/secrets are missing at startup."""


class AuthenticationError(FieldOpsIQError):
    """Raised when an API request fails authentication."""


class RateLimitExceededError(FieldOpsIQError):
    """Raised when a client exceeds the configured rate limit."""
