"""Unit tests for src/services/stt/whisper_service.py.

faster-whisper's native CTranslate2 dependency is heavy and not assumed
to be installed in the unit test environment, so WhisperModel is mocked
at the point of import inside load_model().
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.domain.exceptions import ModelNotLoadedError, TranscriptionError
from src.services.stt.whisper_service import WhisperSTTService


def _make_fake_segment(start, end, text, avg_logprob=-0.2, no_speech_prob=0.01):
    return SimpleNamespace(start=start, end=end, text=text, avg_logprob=avg_logprob, no_speech_prob=no_speech_prob)


class TestLoadModel:
    def test_load_model_succeeds(self, test_settings):
        service = WhisperSTTService(test_settings)
        with patch("faster_whisper.WhisperModel") as MockModel:
            MockModel.return_value = MagicMock()
            service.load_model()
        assert service.is_loaded is True

    def test_load_model_is_idempotent(self, test_settings):
        service = WhisperSTTService(test_settings)
        with patch("faster_whisper.WhisperModel") as MockModel:
            MockModel.return_value = MagicMock()
            service.load_model()
            service.load_model()
            assert MockModel.call_count == 1

    def test_load_model_failure_raises_transcription_error(self, test_settings):
        service = WhisperSTTService(test_settings)
        with patch("faster_whisper.WhisperModel", side_effect=RuntimeError("boom")):
            with pytest.raises(TranscriptionError, match="Failed to load Whisper model"):
                service.load_model()


class TestTranscribe:
    def test_raises_if_model_not_loaded(self, test_settings, sample_wav_file):
        service = WhisperSTTService(test_settings)
        with pytest.raises(ModelNotLoadedError):
            service.transcribe("job-1", sample_wav_file)

    def test_raises_if_audio_missing(self, test_settings, tmp_path):
        service = WhisperSTTService(test_settings)
        service._model = MagicMock()  # bypass load_model for this test
        missing_path = str(tmp_path / "missing.wav")
        with pytest.raises(TranscriptionError, match="not found"):
            service.transcribe("job-1", missing_path)

    def test_successful_transcription_returns_transcript(self, test_settings, sample_wav_file):
        service = WhisperSTTService(test_settings)
        fake_model = MagicMock()
        fake_segments = [
            _make_fake_segment(0.0, 2.0, "unit four compressor"),
            _make_fake_segment(2.0, 4.0, "is making a grinding noise"),
        ]
        fake_info = SimpleNamespace(language="en", language_probability=0.97)
        fake_model.transcribe.return_value = (fake_segments, fake_info)
        service._model = fake_model

        transcript = service.transcribe("job-1", sample_wav_file)

        assert transcript.job_id == "job-1"
        assert "unit four compressor" in transcript.full_text
        assert transcript.detected_language == "en"
        assert transcript.language_probability == 0.97
        assert len(transcript.segments) == 2
        assert transcript.stt_model.startswith("faster-whisper-")

    def test_transcribe_failure_raises_transcription_error(self, test_settings, sample_wav_file):
        service = WhisperSTTService(test_settings)
        fake_model = MagicMock()
        fake_model.transcribe.side_effect = RuntimeError("decode error")
        service._model = fake_model

        with pytest.raises(TranscriptionError, match="Transcription failed"):
            service.transcribe("job-1", sample_wav_file)

    def test_low_confidence_transcript_is_flagged(self, test_settings, sample_wav_file):
        service = WhisperSTTService(test_settings)
        fake_model = MagicMock()
        fake_segments = [_make_fake_segment(0.0, 2.0, "garbled", avg_logprob=-3.0)]
        fake_info = SimpleNamespace(language="en", language_probability=0.4)
        fake_model.transcribe.return_value = (fake_segments, fake_info)
        service._model = fake_model

        transcript = service.transcribe("job-1", sample_wav_file)
        assert transcript.low_confidence is True

    def test_language_hint_is_passed_through(self, test_settings, sample_wav_file):
        service = WhisperSTTService(test_settings)
        fake_model = MagicMock()
        fake_model.transcribe.return_value = ([], SimpleNamespace(language="es", language_probability=0.9))
        service._model = fake_model

        service.transcribe("job-1", sample_wav_file, language_hint="es")

        _, kwargs = fake_model.transcribe.call_args
        assert kwargs["language"] == "es"
