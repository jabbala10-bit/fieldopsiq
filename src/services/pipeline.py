"""
Pipeline orchestrator: wires preprocessing -> STT -> LLM structuring ->
storage -> sync-queue enqueue into a single end-to-end flow.

This is intentionally a plain Python class, not a LangGraph state
machine — unlike the RAG-heavy case studies (SupportIQ, etc.), this
pipeline is a linear sequence with no branching/looping agent
behavior, so a state machine would add complexity without benefit
(ADR-008 covers this choice explicitly).
"""
from __future__ import annotations

from typing import Optional

from src.config.settings import Settings, get_settings
from src.domain.constants import DEFAULT_TRANSCRIPT_DIR
from src.domain.exceptions import FieldOpsIQError
from src.domain.schemas import AudioJob, JobStatus, PipelineResult
from src.observability.logging import get_logger
from src.observability.metrics import HUMAN_REVIEW_QUEUE_DEPTH, PIPELINE_JOBS_TOTAL
from src.services.llm.structuring_service import OllamaStructuringService
from src.services.storage.sqlite_service import SQLiteStorageService
from src.services.stt.preprocessor import AudioPreprocessor
from src.services.stt.whisper_service import WhisperSTTService
from src.services.sync.sync_queue import SyncQueueWorker

logger = get_logger(__name__)


class FieldOpsPipeline:
    """
    End-to-end orchestration for a single AudioJob.

    Each stage updates and persists JobStatus so that a crash/restart
    mid-pipeline can resume from the last completed stage rather than
    reprocessing from scratch (important on battery-constrained field
    devices where the process may be killed at any time).
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        stt_service: Optional[WhisperSTTService] = None,
        preprocessor: Optional[AudioPreprocessor] = None,
        structuring_service: Optional[OllamaStructuringService] = None,
        storage: Optional[SQLiteStorageService] = None,
        sync_worker: Optional[SyncQueueWorker] = None,
    ):
        self._settings = settings or get_settings()
        self._stt = stt_service or WhisperSTTService(self._settings)
        self._preprocessor = preprocessor or AudioPreprocessor()
        self._llm = structuring_service or OllamaStructuringService(self._settings)
        self._storage = storage or SQLiteStorageService(self._settings)
        self._sync = sync_worker or SyncQueueWorker(self._settings)

    def run(self, job: AudioJob) -> PipelineResult:
        """
        Runs the full pipeline for one AudioJob: validate -> normalize ->
        transcribe -> structure -> persist -> enqueue for sync.

        Never raises on expected domain failures — instead returns a
        PipelineResult with sync_status=FAILED and a warning, so the API
        layer can decide how to respond (e.g. 422 vs 500) without this
        method needing HTTP knowledge.
        """
        warnings: list[str] = []
        self._storage.save_job(job)

        try:
            self._preprocessor.validate(job.audio_path)
            duration = self._preprocessor.probe_duration_seconds(job.audio_path)
            job.duration_seconds = duration
            self._storage.save_job(job)

            self._storage.update_job_status(job.job_id, JobStatus.TRANSCRIBING)
            transcript = self._stt.transcribe(job.job_id, job.audio_path, job.language_hint)
            self._storage.save_transcript(transcript)
            self._storage.update_job_status(job.job_id, JobStatus.TRANSCRIBED)

            if transcript.low_confidence:
                warnings.append("Transcript flagged low-confidence; recommend human review.")

            self._storage.update_job_status(job.job_id, JobStatus.STRUCTURING)
            report = self._llm.structure(job.job_id, transcript.full_text)
            self._storage.save_report(report)
            self._storage.update_job_status(job.job_id, JobStatus.STRUCTURED)

            if report.needs_human_review:
                warnings.append(
                    f"Report flagged for human review (confidence={report.extraction_confidence}, "
                    f"severity={report.severity.value})."
                )
                HUMAN_REVIEW_QUEUE_DEPTH.inc()

            self._sync.enqueue(report)
            self._storage.update_job_status(job.job_id, JobStatus.PENDING_SYNC)

            PIPELINE_JOBS_TOTAL.labels(final_status=JobStatus.PENDING_SYNC.value).inc()
            logger.info("pipeline_complete", job_id=job.job_id, warnings=warnings)

            return PipelineResult(
                job=job,
                transcript=transcript,
                report=report,
                sync_status=JobStatus.PENDING_SYNC,
                warnings=warnings,
            )

        except FieldOpsIQError as exc:
            self._storage.update_job_status(job.job_id, JobStatus.FAILED)
            PIPELINE_JOBS_TOTAL.labels(final_status=JobStatus.FAILED.value).inc()
            logger.error("pipeline_failed", job_id=job.job_id, error=str(exc))
            warnings.append(str(exc))
            return PipelineResult(
                job=job,
                transcript=None,
                report=None,
                sync_status=JobStatus.FAILED,
                warnings=warnings,
            )

    def close(self) -> None:
        self._llm.close()
        self._sync.close()
