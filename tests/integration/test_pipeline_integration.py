"""
Integration tests for FieldOpsPipeline.

These exercise the real SQLiteStorageService and SyncQueueWorker (real
file I/O against a tmp_path SQLite file) together with mocked STT and
LLM services — the goal is to verify the orchestration logic (status
transitions, warning propagation, persistence across stages) without
requiring an actual faster-whisper model or Ollama daemon.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from src.domain.schemas import (
    ConnectivityState,
    FieldReport,
    JobStatus,
    ReportCategory,
    Severity,
    Transcript,
)
from src.services.llm.structuring_service import OllamaStructuringService
from src.services.pipeline import FieldOpsPipeline
from src.services.storage.sqlite_service import SQLiteStorageService
from src.services.stt.preprocessor import AudioPreprocessor
from src.services.stt.whisper_service import WhisperSTTService
from src.services.sync.connectivity import ConnectivityProbe
from src.services.sync.sync_queue import SyncQueueWorker


@pytest.fixture
def mock_stt_service(sample_transcript) -> WhisperSTTService:
    service = MagicMock(spec=WhisperSTTService)
    service.transcribe.return_value = sample_transcript
    return service


@pytest.fixture
def mock_llm_service(sample_field_report) -> OllamaStructuringService:
    service = MagicMock(spec=OllamaStructuringService)
    service.structure.return_value = sample_field_report
    service.close = MagicMock()
    return service


@pytest.fixture
def offline_connectivity_probe() -> ConnectivityProbe:
    probe = MagicMock(spec=ConnectivityProbe)
    probe.check.return_value = ConnectivityState.OFFLINE
    probe.last_known_state = ConnectivityState.OFFLINE
    probe.close = MagicMock()
    return probe


@pytest.fixture
def online_connectivity_probe() -> ConnectivityProbe:
    probe = MagicMock(spec=ConnectivityProbe)
    probe.check.return_value = ConnectivityState.ONLINE
    probe.last_known_state = ConnectivityState.ONLINE
    probe.close = MagicMock()
    return probe


@pytest.fixture
def real_storage(test_settings) -> SQLiteStorageService:
    return SQLiteStorageService(test_settings)


def _build_pipeline(test_settings, mock_stt_service, mock_llm_service, real_storage, connectivity_probe):
    sync_worker = SyncQueueWorker(test_settings, connectivity=connectivity_probe)
    return FieldOpsPipeline(
        settings=test_settings,
        stt_service=mock_stt_service,
        preprocessor=AudioPreprocessor(),
        structuring_service=mock_llm_service,
        storage=real_storage,
        sync_worker=sync_worker,
    )


class TestPipelineHappyPath:
    def test_full_pipeline_run_persists_all_stages(
        self, test_settings, sample_audio_job, mock_stt_service, mock_llm_service,
        real_storage, offline_connectivity_probe,
    ):
        pipeline = _build_pipeline(
            test_settings, mock_stt_service, mock_llm_service, real_storage, offline_connectivity_probe
        )
        result = pipeline.run(sample_audio_job)

        assert result.sync_status == JobStatus.PENDING_SYNC
        assert result.transcript is not None
        assert result.report is not None

        persisted_job = real_storage.get_job(sample_audio_job.job_id)
        assert persisted_job.status == JobStatus.PENDING_SYNC

        persisted_transcript = real_storage.get_transcript(sample_audio_job.job_id)
        assert persisted_transcript is not None

        persisted_report = real_storage.get_report(sample_audio_job.job_id)
        assert persisted_report is not None

    def test_report_is_enqueued_for_sync(
        self, test_settings, sample_audio_job, mock_stt_service, mock_llm_service,
        real_storage, offline_connectivity_probe,
    ):
        sync_worker = SyncQueueWorker(test_settings, connectivity=offline_connectivity_probe)
        pipeline = FieldOpsPipeline(
            settings=test_settings,
            stt_service=mock_stt_service,
            preprocessor=AudioPreprocessor(),
            structuring_service=mock_llm_service,
            storage=real_storage,
            sync_worker=sync_worker,
        )
        pipeline.run(sample_audio_job)
        assert sync_worker.queue_depth() == 1

    def test_duration_is_probed_and_persisted(
        self, test_settings, sample_audio_job, mock_stt_service, mock_llm_service,
        real_storage, offline_connectivity_probe,
    ):
        pipeline = _build_pipeline(
            test_settings, mock_stt_service, mock_llm_service, real_storage, offline_connectivity_probe
        )
        pipeline.run(sample_audio_job)
        persisted_job = real_storage.get_job(sample_audio_job.job_id)
        assert persisted_job.duration_seconds == pytest.approx(1.0, abs=0.1)


class TestPipelineWarnings:
    def test_low_confidence_transcript_produces_warning(
        self, test_settings, sample_audio_job, mock_llm_service, real_storage, offline_connectivity_probe,
    ):
        low_conf_transcript = Transcript(
            job_id=sample_audio_job.job_id,
            full_text="unclear mumbling",
            segments=[],
            detected_language="en",
            language_probability=0.3,  # below threshold
            stt_model="faster-whisper-small",
            processing_time_seconds=1.0,
        )
        stt = MagicMock(spec=WhisperSTTService)
        stt.transcribe.return_value = low_conf_transcript

        pipeline = _build_pipeline(
            test_settings, stt, mock_llm_service, real_storage, offline_connectivity_probe
        )
        result = pipeline.run(sample_audio_job)

        assert any("low-confidence" in w for w in result.warnings)

    def test_high_severity_report_flags_human_review(
        self, test_settings, sample_audio_job, mock_stt_service, real_storage, offline_connectivity_probe,
    ):
        critical_report = FieldReport(
            job_id=sample_audio_job.job_id,
            category=ReportCategory.SAFETY_INCIDENT,
            severity=Severity.CRITICAL,
            summary="Gas leak detected near unit 7.",
            extraction_confidence=0.95,
            raw_transcript_excerpt="gas leak unit 7",
            llm_model="llama3.1:8b",
        )
        llm = MagicMock(spec=OllamaStructuringService)
        llm.structure.return_value = critical_report
        llm.close = MagicMock()

        pipeline = _build_pipeline(
            test_settings, mock_stt_service, llm, real_storage, offline_connectivity_probe
        )
        result = pipeline.run(sample_audio_job)

        assert any("human review" in w for w in result.warnings)


class TestPipelineFailureHandling:
    def test_invalid_audio_path_marks_job_failed(
        self, test_settings, mock_stt_service, mock_llm_service, real_storage, offline_connectivity_probe,
    ):
        from src.domain.schemas import AudioJob

        bad_job = AudioJob(
            technician_id="T1", site_id="S1", audio_path="/nonexistent/path/file.wav"
        )
        pipeline = _build_pipeline(
            test_settings, mock_stt_service, mock_llm_service, real_storage, offline_connectivity_probe
        )
        result = pipeline.run(bad_job)

        assert result.sync_status == JobStatus.FAILED
        assert result.transcript is None
        assert result.report is None
        assert len(result.warnings) >= 1

        persisted = real_storage.get_job(bad_job.job_id)
        assert persisted.status == JobStatus.FAILED

    def test_stt_failure_marks_job_failed_without_calling_llm(
        self, test_settings, sample_audio_job, mock_llm_service, real_storage, offline_connectivity_probe,
    ):
        from src.domain.exceptions import TranscriptionError

        stt = MagicMock(spec=WhisperSTTService)
        stt.transcribe.side_effect = TranscriptionError("model crashed")

        pipeline = _build_pipeline(
            test_settings, stt, mock_llm_service, real_storage, offline_connectivity_probe
        )
        result = pipeline.run(sample_audio_job)

        assert result.sync_status == JobStatus.FAILED
        mock_llm_service.structure.assert_not_called()

    def test_llm_failure_marks_job_failed_but_transcript_persisted(
        self, test_settings, sample_audio_job, mock_stt_service, real_storage, offline_connectivity_probe,
    ):
        from src.domain.exceptions import LLMUnavailableError

        llm = MagicMock(spec=OllamaStructuringService)
        llm.structure.side_effect = LLMUnavailableError("ollama down")
        llm.close = MagicMock()

        pipeline = _build_pipeline(
            test_settings, mock_stt_service, llm, real_storage, offline_connectivity_probe
        )
        result = pipeline.run(sample_audio_job)

        assert result.sync_status == JobStatus.FAILED
        # Transcript should still be persisted even though structuring failed
        persisted_transcript = real_storage.get_transcript(sample_audio_job.job_id)
        assert persisted_transcript is not None


class TestPipelineWithConnectivity:
    def test_pipeline_run_does_not_block_on_online_sync(
        self, test_settings, sample_audio_job, mock_stt_service, mock_llm_service,
        real_storage, online_connectivity_probe,
    ):
        """
        Even when online_connectivity_probe reports ONLINE, pipeline.run()
        only enqueues — it never calls drain_queue() inline. This is the
        core offline-first guarantee (ADR-007): the UI-facing call must
        never block on network I/O.
        """
        sync_worker = SyncQueueWorker(test_settings, connectivity=online_connectivity_probe)
        pipeline = FieldOpsPipeline(
            settings=test_settings,
            stt_service=mock_stt_service,
            preprocessor=AudioPreprocessor(),
            structuring_service=mock_llm_service,
            storage=real_storage,
            sync_worker=sync_worker,
        )
        result = pipeline.run(sample_audio_job)

        assert result.sync_status == JobStatus.PENDING_SYNC
        online_connectivity_probe.check.assert_not_called()
