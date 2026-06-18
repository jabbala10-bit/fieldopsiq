"""
Domain-level constants for FieldOpsIQ.

Pulling tunable thresholds into one module makes ADR-004 (confidence
thresholds) auditable in one place rather than scattered as magic
numbers across services.
"""
from __future__ import annotations

# --------------------------------------------------------------------------
# Audio constraints
# --------------------------------------------------------------------------

ALLOWED_AUDIO_EXTENSIONS: frozenset[str] = frozenset({".wav", ".mp3", ".m4a", ".flac", ".ogg"})
MAX_AUDIO_DURATION_SECONDS: float = 30 * 60  # 30 minutes
MAX_AUDIO_FILE_SIZE_BYTES: int = 200 * 1024 * 1024  # 200 MB
TARGET_SAMPLE_RATE_HZ: int = 16_000  # faster-whisper's native rate

# --------------------------------------------------------------------------
# STT (faster-whisper) defaults
# --------------------------------------------------------------------------

DEFAULT_WHISPER_MODEL: str = "small"  # base|small|medium|large-v3, see ADR-001
DEFAULT_WHISPER_COMPUTE_TYPE: str = "int8"  # int8|int8_float16|float16 — int8 for CPU edge devices
DEFAULT_BEAM_SIZE: int = 5
LOW_CONFIDENCE_LOGPROB_THRESHOLD: float = -1.0
LOW_CONFIDENCE_LANGUAGE_PROB_THRESHOLD: float = 0.5

# --------------------------------------------------------------------------
# LLM structuring (Ollama + Llama 3.x) defaults
# --------------------------------------------------------------------------

DEFAULT_LLM_MODEL: str = "llama3.1:8b"
DEFAULT_LLM_TEMPERATURE: float = 0.1  # low temperature for deterministic extraction
LLM_MAX_RETRIES: int = 3
LLM_REQUEST_TIMEOUT_SECONDS: int = 60
HUMAN_REVIEW_CONFIDENCE_THRESHOLD: float = 0.65

# --------------------------------------------------------------------------
# Sync queue
# --------------------------------------------------------------------------

SYNC_MAX_ATTEMPTS: int = 8
SYNC_BACKOFF_BASE_SECONDS: float = 2.0  # exponential backoff base
SYNC_BACKOFF_MAX_SECONDS: float = 300.0  # cap at 5 minutes
CONNECTIVITY_CHECK_INTERVAL_SECONDS: float = 15.0
CONNECTIVITY_PROBE_TIMEOUT_SECONDS: float = 3.0
SYNC_BATCH_SIZE: int = 20

# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------

DEFAULT_RATE_LIMIT_PER_MINUTE: int = 30

# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

DEFAULT_SQLITE_PATH: str = "data/fieldopsiq.db"
DEFAULT_AUDIO_INBOX_DIR: str = "data/audio_inbox"
DEFAULT_TRANSCRIPT_DIR: str = "data/transcripts"
DEFAULT_REPORT_DIR: str = "data/reports"
