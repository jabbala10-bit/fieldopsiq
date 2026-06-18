"""Unit tests for src/services/stt/preprocessor.py."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.domain.exceptions import AudioTooLongError, AudioValidationError, UnsupportedAudioFormatError
from src.services.stt.preprocessor import AudioPreprocessor


@pytest.fixture
def preprocessor() -> AudioPreprocessor:
    return AudioPreprocessor()


class TestValidate:
    def test_valid_wav_passes(self, preprocessor, sample_wav_file):
        preprocessor.validate(sample_wav_file)  # should not raise

    def test_missing_file_raises(self, preprocessor, tmp_path):
        missing = tmp_path / "does_not_exist.wav"
        with pytest.raises(AudioValidationError, match="does not exist"):
            preprocessor.validate(str(missing))

    def test_unsupported_extension_raises(self, preprocessor, tmp_path):
        bad_file = tmp_path / "notes.txt"
        bad_file.write_text("not audio")
        with pytest.raises(UnsupportedAudioFormatError):
            preprocessor.validate(str(bad_file))

    def test_empty_file_raises(self, preprocessor, tmp_path):
        empty = tmp_path / "empty.wav"
        empty.write_bytes(b"")
        with pytest.raises(AudioValidationError, match="empty"):
            preprocessor.validate(str(empty))

    def test_oversized_file_raises(self, preprocessor, tmp_path, monkeypatch):
        big_file = tmp_path / "big.wav"
        big_file.write_bytes(b"\x00" * 100)

        import src.services.stt.preprocessor as mod

        monkeypatch.setattr(mod, "MAX_AUDIO_FILE_SIZE_BYTES", 10)
        with pytest.raises(AudioValidationError, match="too large"):
            preprocessor.validate(str(big_file))


class TestProbeDuration:
    def test_duration_matches_known_file(self, preprocessor, sample_wav_file):
        duration = preprocessor.probe_duration_seconds(sample_wav_file)
        assert duration == pytest.approx(1.0, abs=0.05)

    def test_unreadable_file_raises_validation_error(self, preprocessor, tmp_path):
        fake = tmp_path / "fake.wav"
        fake.write_bytes(b"not a real wav file")
        with pytest.raises(AudioValidationError):
            preprocessor.probe_duration_seconds(str(fake))

    def test_too_long_audio_raises(self, preprocessor, sample_wav_file, monkeypatch):
        import src.services.stt.preprocessor as mod

        monkeypatch.setattr(mod, "MAX_AUDIO_DURATION_SECONDS", 0.01)
        with pytest.raises(AudioTooLongError):
            preprocessor.probe_duration_seconds(sample_wav_file)


class TestNormalize:
    def test_normalize_produces_target_sample_rate(self, preprocessor, sample_wav_file, tmp_path):
        import soundfile as sf

        output_path = str(tmp_path / "normalized.wav")
        result_path = preprocessor.normalize(sample_wav_file, output_path)

        assert result_path == output_path
        assert Path(output_path).exists()
        info = sf.info(output_path)
        assert info.samplerate == 16000
        assert info.channels == 1

    def test_normalize_creates_parent_dirs(self, preprocessor, sample_wav_file, tmp_path):
        nested_output = str(tmp_path / "nested" / "dir" / "out.wav")
        preprocessor.normalize(sample_wav_file, nested_output)
        assert Path(nested_output).exists()

    def test_normalize_invalid_input_raises(self, preprocessor, tmp_path):
        fake = tmp_path / "fake.wav"
        fake.write_bytes(b"garbage")
        with pytest.raises(AudioValidationError):
            preprocessor.normalize(str(fake), str(tmp_path / "out.wav"))
