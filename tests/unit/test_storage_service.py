"""Unit tests for src/services/storage/sqlite_service.py."""
from __future__ import annotations

import pytest

from src.domain.schemas import JobStatus
from src.services.storage.sqlite_service import SQLiteStorageService


@pytest.fixture
def storage(test_settings) -> SQLiteStorageService:
    return SQLiteStorageService(test_settings)


class TestAudioJobPersistence:
    def test_save_and_get_job_roundtrip(self, storage, sample_audio_job):
        storage.save_job(sample_audio_job)
        fetched = storage.get_job(sample_audio_job.job_id)
        assert fetched is not None
        assert fetched.job_id == sample_audio_job.job_id
        assert fetched.technician_id == sample_audio_job.technician_id

    def test_get_nonexistent_job_returns_none(self, storage):
        assert storage.get_job("does-not-exist") is None

    def test_update_job_status_persists(self, storage, sample_audio_job):
        storage.save_job(sample_audio_job)
        storage.update_job_status(sample_audio_job.job_id, JobStatus.TRANSCRIBED)
        fetched = storage.get_job(sample_audio_job.job_id)
        assert fetched.status == JobStatus.TRANSCRIBED

    def test_save_job_upserts_on_conflict(self, storage, sample_audio_job):
        storage.save_job(sample_audio_job)
        sample_audio_job.duration_seconds = 12.5
        storage.save_job(sample_audio_job)
        fetched = storage.get_job(sample_audio_job.job_id)
        assert fetched.duration_seconds == 12.5

    def test_list_jobs_by_status(self, storage, sample_audio_job):
        storage.save_job(sample_audio_job)
        jobs = storage.list_jobs_by_status(JobStatus.QUEUED)
        assert len(jobs) == 1
        assert jobs[0].job_id == sample_audio_job.job_id

    def test_list_jobs_by_status_empty_for_other_status(self, storage, sample_audio_job):
        storage.save_job(sample_audio_job)
        jobs = storage.list_jobs_by_status(JobStatus.SYNCED)
        assert jobs == []


class TestTranscriptPersistence:
    def test_save_and_get_transcript_roundtrip(self, storage, sample_audio_job, sample_transcript):
        storage.save_job(sample_audio_job)
        transcript = sample_transcript.model_copy(update={"job_id": sample_audio_job.job_id})
        storage.save_transcript(transcript)

        fetched = storage.get_transcript(sample_audio_job.job_id)
        assert fetched is not None
        assert fetched.full_text == transcript.full_text
        assert len(fetched.segments) == len(transcript.segments)

    def test_get_nonexistent_transcript_returns_none(self, storage):
        assert storage.get_transcript("missing") is None


class TestFieldReportPersistence:
    def test_save_and_get_report_roundtrip(self, storage, sample_audio_job, sample_field_report):
        storage.save_job(sample_audio_job)
        report = sample_field_report.model_copy(update={"job_id": sample_audio_job.job_id})
        storage.save_report(report)

        fetched = storage.get_report(sample_audio_job.job_id)
        assert fetched is not None
        assert fetched.category == report.category
        assert fetched.severity == report.severity
        assert fetched.extraction_confidence == report.extraction_confidence

    def test_get_nonexistent_report_returns_none(self, storage):
        assert storage.get_report("missing") is None

    def test_follow_up_boolean_roundtrips_correctly(self, storage, sample_audio_job, sample_field_report):
        storage.save_job(sample_audio_job)
        report = sample_field_report.model_copy(
            update={"job_id": sample_audio_job.job_id, "follow_up_required": True}
        )
        storage.save_report(report)
        fetched = storage.get_report(sample_audio_job.job_id)
        assert fetched.follow_up_required is True
