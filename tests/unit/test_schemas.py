"""Unit tests for src/domain/schemas.py."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.domain.schemas import (
    AudioJob,
    FieldReport,
    JobStatus,
    ReportCategory,
    Severity,
    SyncRecord,
    Transcript,
    TranscriptSegment,
)


class TestAudioJob:
    def test_valid_audio_job_is_created(self):
        job = AudioJob(technician_id="T1", site_id="S1", audio_path="recording.wav")
        assert job.status == JobStatus.QUEUED
        assert job.job_id  # auto-generated UUID

    @pytest.mark.parametrize("ext", [".wav", ".mp3", ".m4a", ".flac", ".ogg"])
    def test_allowed_extensions_pass(self, ext):
        job = AudioJob(technician_id="T1", site_id="S1", audio_path=f"recording{ext}")
        assert job.audio_path.endswith(ext)

    @pytest.mark.parametrize("ext", [".txt", ".mp4", ".exe", ""])
    def test_disallowed_extensions_raise(self, ext):
        with pytest.raises(ValidationError):
            AudioJob(technician_id="T1", site_id="S1", audio_path=f"recording{ext}")


class TestTranscript:
    def test_low_confidence_true_when_logprob_poor(self):
        t = Transcript(
            job_id="j1",
            full_text="mumble mumble",
            segments=[TranscriptSegment(start=0, end=1, text="x", avg_logprob=-2.5)],
            detected_language="en",
            language_probability=0.9,
            stt_model="m",
            processing_time_seconds=1.0,
        )
        assert t.low_confidence is True

    def test_low_confidence_false_when_clear(self):
        t = Transcript(
            job_id="j1",
            full_text="clear speech",
            segments=[TranscriptSegment(start=0, end=1, text="x", avg_logprob=-0.1)],
            detected_language="en",
            language_probability=0.99,
            stt_model="m",
            processing_time_seconds=1.0,
        )
        assert t.low_confidence is False

    def test_low_confidence_uses_language_probability_when_no_segments(self):
        t = Transcript(
            job_id="j1",
            full_text="",
            segments=[],
            detected_language="en",
            language_probability=0.3,
            stt_model="m",
            processing_time_seconds=1.0,
        )
        assert t.low_confidence is True


class TestFieldReport:
    def test_needs_human_review_low_confidence(self, sample_field_report):
        report = sample_field_report.model_copy(update={"extraction_confidence": 0.4})
        assert report.needs_human_review is True

    def test_needs_human_review_critical_severity_even_if_confident(self, sample_field_report):
        report = sample_field_report.model_copy(
            update={"extraction_confidence": 0.99, "severity": Severity.CRITICAL}
        )
        assert report.needs_human_review is True

    def test_no_review_needed_when_confident_and_low_severity(self, sample_field_report):
        report = sample_field_report.model_copy(
            update={"extraction_confidence": 0.95, "severity": Severity.LOW}
        )
        assert report.needs_human_review is False

    def test_summary_max_length_enforced(self, sample_field_report):
        with pytest.raises(ValidationError):
            sample_field_report.model_copy(update={"summary": "x" * 501})


class TestSyncRecord:
    def test_is_exhausted_false_below_max(self, sample_field_report):
        record = SyncRecord(job_id="j1", payload=sample_field_report, attempts=2)
        assert record.is_exhausted is False

    def test_is_exhausted_true_at_max(self, sample_field_report):
        record = SyncRecord(job_id="j1", payload=sample_field_report, attempts=8)
        assert record.is_exhausted is True
