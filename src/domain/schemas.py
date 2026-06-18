"""
Domain schemas for FieldOpsIQ.

These are the core business entities. Nothing in this module depends on
FastAPI, Ollama, faster-whisper, or SQLite — it is pure domain logic,
following the same dependency-inversion principle used in ManufactureIQ,
SupportIQ, BioMedIQ, and InferenceIQ (see ADR-002).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------

class JobStatus(str, Enum):
    """Lifecycle of a single field recording, end to end."""

    QUEUED = "queued"
    TRANSCRIBING = "transcribing"
    TRANSCRIBED = "transcribed"
    STRUCTURING = "structuring"
    STRUCTURED = "structured"
    PENDING_SYNC = "pending_sync"
    SYNCED = "synced"
    FAILED = "failed"


class ReportCategory(str, Enum):
    """Standardized field-report categories used by ops/dispatch teams."""

    SAFETY_INCIDENT = "safety_incident"
    EQUIPMENT_FAULT = "equipment_fault"
    MAINTENANCE_COMPLETED = "maintenance_completed"
    INSPECTION_NOTE = "inspection_note"
    PARTS_REQUEST = "parts_request"
    GENERAL_NOTE = "general_note"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ConnectivityState(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"  # reachable but high latency / packet loss


# --------------------------------------------------------------------------
# Audio + transcription
# --------------------------------------------------------------------------

class AudioJob(BaseModel):
    """A single voice recording captured by a field technician."""

    job_id: str = Field(default_factory=_new_id)
    technician_id: str
    site_id: str
    audio_path: str
    duration_seconds: Optional[float] = None
    language_hint: Optional[str] = Field(
        default=None, description="ISO 639-1 code, e.g. 'en'. None = auto-detect."
    )
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @field_validator("audio_path")
    @classmethod
    def _path_must_have_known_extension(cls, v: str) -> str:
        allowed = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}
        suffix = Path(v).suffix.lower()
        if suffix not in allowed:
            raise ValueError(f"Unsupported audio extension '{suffix}'. Allowed: {allowed}")
        return v


class TranscriptSegment(BaseModel):
    """One timed segment from the STT engine."""

    start: float
    end: float
    text: str
    avg_logprob: Optional[float] = None
    no_speech_prob: Optional[float] = None


class Transcript(BaseModel):
    """Full transcription result for an AudioJob."""

    job_id: str
    full_text: str
    segments: list[TranscriptSegment] = Field(default_factory=list)
    detected_language: str
    language_probability: float
    stt_model: str
    processing_time_seconds: float
    created_at: datetime = Field(default_factory=_utcnow)

    @property
    def low_confidence(self) -> bool:
        """Flag transcripts likely needing human review (ADR-004 threshold)."""
        if not self.segments:
            return self.language_probability < 0.5
        avg = sum(s.avg_logprob or 0.0 for s in self.segments) / len(self.segments)
        return avg < -1.0 or self.language_probability < 0.5


# --------------------------------------------------------------------------
# Structured field report (LLM output)
# --------------------------------------------------------------------------

class StructuredField(BaseModel):
    """A single extracted field with provenance for auditability."""

    value: str
    confidence: float = Field(ge=0.0, le=1.0)


class FieldReport(BaseModel):
    """
    The standardized report produced by the LLM structuring stage.

    This is the artifact that ultimately syncs to the central
    maintenance/ops system — it must be schema-valid before it is ever
    queued for sync (see ADR-003: validate before queue, not after).
    """

    job_id: str
    category: ReportCategory
    severity: Severity
    summary: str = Field(min_length=1, max_length=500)
    equipment_id: Optional[str] = None
    location_detail: Optional[str] = None
    action_taken: Optional[str] = None
    follow_up_required: bool = False
    follow_up_notes: Optional[str] = None
    raw_transcript_excerpt: str = Field(
        max_length=1000,
        description="Short excerpt retained for human verification, not the full transcript.",
    )
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    llm_model: str
    created_at: datetime = Field(default_factory=_utcnow)

    @property
    def needs_human_review(self) -> bool:
        """Below this confidence, route to a human queue instead of auto-sync (ADR-004)."""
        return self.extraction_confidence < 0.65 or self.severity in (
            Severity.HIGH,
            Severity.CRITICAL,
        )


# --------------------------------------------------------------------------
# Sync queue
# --------------------------------------------------------------------------

class SyncRecord(BaseModel):
    """An item waiting to be pushed to the central server."""

    sync_id: str = Field(default_factory=_new_id)
    job_id: str
    payload: FieldReport
    attempts: int = 0
    last_attempt_at: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)

    MAX_ATTEMPTS: int = Field(default=8, exclude=True)

    @property
    def is_exhausted(self) -> bool:
        return self.attempts >= self.MAX_ATTEMPTS


class SyncResult(BaseModel):
    sync_id: str
    success: bool
    http_status: Optional[int] = None
    error: Optional[str] = None
    synced_at: datetime = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------
# Pipeline result (top-level aggregate returned by API)
# --------------------------------------------------------------------------

class PipelineResult(BaseModel):
    job: AudioJob
    transcript: Optional[Transcript] = None
    report: Optional[FieldReport] = None
    sync_status: JobStatus
    warnings: list[str] = Field(default_factory=list)
