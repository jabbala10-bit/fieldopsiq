"""
Audio preprocessing service.

Field recordings arrive in inconsistent formats (phone voice memos,
rugged-tablet recorders, walkie-talkie dongles), so every file is
normalized to 16kHz mono PCM before it ever reaches faster-whisper.
This also validates against the size/duration/format limits so a
30-minute or corrupted file fails fast with a clear domain error
instead of hanging the STT model.
"""
from __future__ import annotations

from pathlib import Path

from src.domain.constants import (
    ALLOWED_AUDIO_EXTENSIONS,
    MAX_AUDIO_DURATION_SECONDS,
    MAX_AUDIO_FILE_SIZE_BYTES,
    TARGET_SAMPLE_RATE_HZ,
)
from src.domain.exceptions import (
    AudioTooLongError,
    AudioValidationError,
    UnsupportedAudioFormatError,
)
from src.observability.logging import get_logger

logger = get_logger(__name__)


class AudioPreprocessor:
    """Validates and normalizes raw field audio before STT."""

    def validate(self, audio_path: str) -> None:
        """
        Run cheap, fast checks before any decoding happens.

        Raises:
            UnsupportedAudioFormatError: wrong extension.
            AudioValidationError: missing file or zero-byte file.
        """
        path = Path(audio_path)
        if not path.exists():
            raise AudioValidationError(f"Audio file does not exist: {audio_path}")

        suffix = path.suffix.lower()
        if suffix not in ALLOWED_AUDIO_EXTENSIONS:
            raise UnsupportedAudioFormatError(
                f"Unsupported audio format '{suffix}'. Allowed: {sorted(ALLOWED_AUDIO_EXTENSIONS)}"
            )

        size_bytes = path.stat().st_size
        if size_bytes == 0:
            raise AudioValidationError(f"Audio file is empty: {audio_path}")
        if size_bytes > MAX_AUDIO_FILE_SIZE_BYTES:
            raise AudioValidationError(
                f"Audio file too large ({size_bytes} bytes). "
                f"Max allowed: {MAX_AUDIO_FILE_SIZE_BYTES} bytes"
            )

    def probe_duration_seconds(self, audio_path: str) -> float:
        """
        Returns audio duration in seconds using soundfile (no full decode needed).

        Raises:
            AudioTooLongError: if duration exceeds MAX_AUDIO_DURATION_SECONDS.
            AudioValidationError: if the file can't be read/probed.
        """
        try:
            import soundfile as sf

            info = sf.info(audio_path)
            duration = info.frames / float(info.samplerate)
        except Exception as exc:  # noqa: BLE001
            raise AudioValidationError(f"Could not probe audio file '{audio_path}': {exc}") from exc

        if duration > MAX_AUDIO_DURATION_SECONDS:
            raise AudioTooLongError(
                f"Audio duration {duration:.0f}s exceeds max {MAX_AUDIO_DURATION_SECONDS:.0f}s"
            )
        return duration

    def normalize(self, audio_path: str, output_path: str) -> str:
        """
        Resample to 16kHz mono PCM WAV, the format faster-whisper expects natively.

        Re-encoding up front (rather than letting faster-whisper/ffmpeg do it
        implicitly per-call) makes preprocessing failures explicit and testable,
        and lets us cache the normalized file alongside the original.

        Returns:
            The output_path, for chaining.
        """
        try:
            import soundfile as sf
            from scipy.signal import resample_poly

            data, samplerate = sf.read(audio_path, always_2d=False)

            # Downmix to mono if stereo
            if data.ndim > 1:
                data = data.mean(axis=1)

            if samplerate != TARGET_SAMPLE_RATE_HZ:
                gcd = _gcd(samplerate, TARGET_SAMPLE_RATE_HZ)
                up = TARGET_SAMPLE_RATE_HZ // gcd
                down = samplerate // gcd
                data = resample_poly(data, up, down)

            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            sf.write(output_path, data, TARGET_SAMPLE_RATE_HZ, subtype="PCM_16")
            logger.info(
                "audio_normalized",
                source=audio_path,
                output=output_path,
                target_sample_rate=TARGET_SAMPLE_RATE_HZ,
            )
            return output_path
        except Exception as exc:  # noqa: BLE001
            raise AudioValidationError(f"Failed to normalize audio '{audio_path}': {exc}") from exc


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a
