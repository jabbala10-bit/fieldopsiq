"""
SQLite storage service.

SQLite (not Postgres/MySQL) is the deliberate choice for the edge node
(see ADR-005): zero-ops, single-file, survives device reboot, and is
plenty fast for the low write-volume of field recordings. The central
server the data eventually syncs to can be Postgres — that's a separate
concern from this edge-local store.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from src.config.settings import Settings, get_settings
from src.domain.exceptions import StorageError
from src.domain.schemas import AudioJob, FieldReport, JobStatus, Transcript
from src.observability.logging import get_logger

logger = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audio_jobs (
    job_id TEXT PRIMARY KEY,
    technician_id TEXT NOT NULL,
    site_id TEXT NOT NULL,
    audio_path TEXT NOT NULL,
    duration_seconds REAL,
    language_hint TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcripts (
    job_id TEXT PRIMARY KEY,
    full_text TEXT NOT NULL,
    segments_json TEXT NOT NULL,
    detected_language TEXT NOT NULL,
    language_probability REAL NOT NULL,
    stt_model TEXT NOT NULL,
    processing_time_seconds REAL NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES audio_jobs (job_id)
);

CREATE TABLE IF NOT EXISTS field_reports (
    job_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    summary TEXT NOT NULL,
    equipment_id TEXT,
    location_detail TEXT,
    action_taken TEXT,
    follow_up_required INTEGER NOT NULL,
    follow_up_notes TEXT,
    raw_transcript_excerpt TEXT NOT NULL,
    extraction_confidence REAL NOT NULL,
    llm_model TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES audio_jobs (job_id)
);

CREATE TABLE IF NOT EXISTS sync_queue (
    sync_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES audio_jobs (job_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON audio_jobs (status);
CREATE INDEX IF NOT EXISTS idx_sync_attempts ON sync_queue (attempts);
"""


class SQLiteStorageService:
    """Connection-per-call SQLite storage with WAL mode for crash safety."""

    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings or get_settings()
        self._db_path = self._settings.sqlite_path
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")  # crash-safe writes
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ----------------------------------------------------------------
    # Audio jobs
    # ----------------------------------------------------------------

    def save_job(self, job: AudioJob) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO audio_jobs
                        (job_id, technician_id, site_id, audio_path, duration_seconds,
                         language_hint, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET
                        status=excluded.status,
                        duration_seconds=excluded.duration_seconds,
                        updated_at=excluded.updated_at
                    """,
                    (
                        job.job_id,
                        job.technician_id,
                        job.site_id,
                        job.audio_path,
                        job.duration_seconds,
                        job.language_hint,
                        job.status.value,
                        job.created_at.isoformat(),
                        job.updated_at.isoformat(),
                    ),
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to save job {job.job_id}: {exc}") from exc

    def update_job_status(self, job_id: str, status: JobStatus) -> None:
        from datetime import datetime, timezone

        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE audio_jobs SET status = ?, updated_at = ? WHERE job_id = ?",
                    (status.value, datetime.now(timezone.utc).isoformat(), job_id),
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to update status for job {job_id}: {exc}") from exc

    def get_job(self, job_id: str) -> Optional[AudioJob]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM audio_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        return AudioJob(
            job_id=row["job_id"],
            technician_id=row["technician_id"],
            site_id=row["site_id"],
            audio_path=row["audio_path"],
            duration_seconds=row["duration_seconds"],
            language_hint=row["language_hint"],
            status=JobStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_jobs_by_status(self, status: JobStatus, limit: int = 50) -> list[AudioJob]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audio_jobs WHERE status = ? ORDER BY created_at ASC LIMIT ?",
                (status.value, limit),
            ).fetchall()
        return [
            AudioJob(
                job_id=r["job_id"],
                technician_id=r["technician_id"],
                site_id=r["site_id"],
                audio_path=r["audio_path"],
                duration_seconds=r["duration_seconds"],
                language_hint=r["language_hint"],
                status=JobStatus(r["status"]),
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    # ----------------------------------------------------------------
    # Transcripts
    # ----------------------------------------------------------------

    def save_transcript(self, transcript: Transcript) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO transcripts
                        (job_id, full_text, segments_json, detected_language,
                         language_probability, stt_model, processing_time_seconds, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET
                        full_text=excluded.full_text,
                        segments_json=excluded.segments_json
                    """,
                    (
                        transcript.job_id,
                        transcript.full_text,
                        json.dumps([s.model_dump() for s in transcript.segments]),
                        transcript.detected_language,
                        transcript.language_probability,
                        transcript.stt_model,
                        transcript.processing_time_seconds,
                        transcript.created_at.isoformat(),
                    ),
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to save transcript for job {transcript.job_id}: {exc}") from exc

    def get_transcript(self, job_id: str) -> Optional[Transcript]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM transcripts WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        return Transcript(
            job_id=row["job_id"],
            full_text=row["full_text"],
            segments=json.loads(row["segments_json"]),
            detected_language=row["detected_language"],
            language_probability=row["language_probability"],
            stt_model=row["stt_model"],
            processing_time_seconds=row["processing_time_seconds"],
            created_at=row["created_at"],
        )

    # ----------------------------------------------------------------
    # Field reports
    # ----------------------------------------------------------------

    def save_report(self, report: FieldReport) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO field_reports
                        (job_id, category, severity, summary, equipment_id, location_detail,
                         action_taken, follow_up_required, follow_up_notes,
                         raw_transcript_excerpt, extraction_confidence, llm_model, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET
                        category=excluded.category,
                        severity=excluded.severity,
                        summary=excluded.summary
                    """,
                    (
                        report.job_id,
                        report.category.value,
                        report.severity.value,
                        report.summary,
                        report.equipment_id,
                        report.location_detail,
                        report.action_taken,
                        int(report.follow_up_required),
                        report.follow_up_notes,
                        report.raw_transcript_excerpt,
                        report.extraction_confidence,
                        report.llm_model,
                        report.created_at.isoformat(),
                    ),
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to save report for job {report.job_id}: {exc}") from exc

    def get_report(self, job_id: str) -> Optional[FieldReport]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM field_reports WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        return FieldReport(
            job_id=row["job_id"],
            category=row["category"],
            severity=row["severity"],
            summary=row["summary"],
            equipment_id=row["equipment_id"],
            location_detail=row["location_detail"],
            action_taken=row["action_taken"],
            follow_up_required=bool(row["follow_up_required"]),
            follow_up_notes=row["follow_up_notes"],
            raw_transcript_excerpt=row["raw_transcript_excerpt"],
            extraction_confidence=row["extraction_confidence"],
            llm_model=row["llm_model"],
            created_at=row["created_at"],
        )
