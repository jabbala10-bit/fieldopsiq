"""Shared pytest fixtures for FieldOpsIQ tests."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from src.config.settings import Settings
from src.domain.schemas import AudioJob, FieldReport, ReportCategory, Severity, Transcript, TranscriptSegment


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test_fieldopsiq.db")


@pytest.fixture
def test_settings(tmp_path: Path, tmp_db_path: str) -> Settings:
    return Settings(
        environment="development",
        sqlite_path=tmp_db_path,
        audio_inbox_dir=str(tmp_path / "audio_inbox"),
        transcript_dir=str(tmp_path / "transcripts"),
        report_dir=str(tmp_path / "reports"),
        whisper_model_cache_dir=str(tmp_path / "models"),
        ollama_base_url="http://localhost:11434",
        central_server_url="https://central.example.com/api/v1/reports",
        sync_max_attempts=3,
    )


@pytest.fixture
def sample_wav_file(tmp_path: Path) -> str:
    """Generates a tiny real WAV file (1 second of silence) for preprocessing tests."""
    import numpy as np
    import soundfile as sf

    path = tmp_path / "sample.wav"
    samplerate = 16000
    data = np.zeros(samplerate, dtype="float32")
    sf.write(str(path), data, samplerate)
    return str(path)


@pytest.fixture
def sample_audio_job(sample_wav_file: str) -> AudioJob:
    return AudioJob(
        technician_id="TECH-001",
        site_id="SITE-001",
        audio_path=sample_wav_file,
    )


@pytest.fixture
def sample_transcript() -> Transcript:
    return Transcript(
        job_id="job-123",
        full_text="The compressor on unit 4 is making a grinding noise, I shut it down and tagged it out.",
        segments=[
            TranscriptSegment(start=0.0, end=4.5, text="The compressor on unit 4 is making a grinding noise", avg_logprob=-0.2, no_speech_prob=0.01),
            TranscriptSegment(start=4.5, end=7.0, text="I shut it down and tagged it out.", avg_logprob=-0.3, no_speech_prob=0.02),
        ],
        detected_language="en",
        language_probability=0.98,
        stt_model="faster-whisper-small",
        processing_time_seconds=1.23,
    )


@pytest.fixture
def sample_field_report() -> FieldReport:
    return FieldReport(
        job_id="job-123",
        category=ReportCategory.EQUIPMENT_FAULT,
        severity=Severity.MEDIUM,
        summary="Compressor on unit 4 making grinding noise; shut down and tagged out.",
        equipment_id="UNIT-4-COMPRESSOR",
        location_detail=None,
        action_taken="Shut down and tagged out the unit.",
        follow_up_required=True,
        follow_up_notes="Needs maintenance inspection before restart.",
        raw_transcript_excerpt="The compressor on unit 4 is making a grinding noise...",
        extraction_confidence=0.91,
        llm_model="llama3.1:8b",
    )
