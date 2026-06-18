"""
STT service: wraps faster-whisper for fully offline transcription.

Design notes (see ADR-001 for the full faster-whisper vs whisper.cpp vs
Distil-Whisper comparison):
  - faster-whisper uses CTranslate2 under the hood, giving ~4x speedup
    over openai-whisper on CPU with no accuracy loss.
  - The model is loaded once at service startup and reused across
    requests (ADR-001 also covers why we don't reload per-request).
  - int8 quantization is the default compute type so the pipeline runs
    on a rugged field laptop/tablet CPU without a GPU.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from src.config.settings import Settings, get_settings
from src.domain.constants import (
    LOW_CONFIDENCE_LANGUAGE_PROB_THRESHOLD,
    LOW_CONFIDENCE_LOGPROB_THRESHOLD,
)
from src.domain.exceptions import ModelNotLoadedError, TranscriptionError
from src.domain.schemas import Transcript, TranscriptSegment
from src.observability.logging import get_logger
from src.observability.metrics import STT_DURATION_SECONDS, STT_REQUESTS_TOTAL

logger = get_logger(__name__)


class WhisperSTTService:
    """
    Thin, testable wrapper around faster-whisper's WhisperModel.

    The real `WhisperModel` import is deferred into `load_model()` so that
    unit tests can construct this class and inject a mock without
    requiring the (large) faster-whisper / ctranslate2 native dependency
    to be installed in the test environment.
    """

    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings or get_settings()
        self._model = None  # lazily loaded; type is faster_whisper.WhisperModel

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load_model(self) -> None:
        """
        Load the Whisper model into memory. Call once at app startup.

        Raises:
            TranscriptionError: if the model fails to load (e.g. corrupt
                cache, unsupported compute_type for this hardware).
        """
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel  # deferred import

            logger.info(
                "loading_whisper_model",
                model_size=self._settings.whisper_model_size,
                device=self._settings.whisper_device,
                compute_type=self._settings.whisper_compute_type,
            )
            self._model = WhisperModel(
                self._settings.whisper_model_size,
                device=self._settings.whisper_device,
                compute_type=self._settings.whisper_compute_type,
                download_root=self._settings.whisper_model_cache_dir,
            )
        except Exception as exc:  # noqa: BLE001 - we re-raise as domain error
            raise TranscriptionError(f"Failed to load Whisper model: {exc}") from exc

    def transcribe(self, job_id: str, audio_path: str, language_hint: Optional[str] = None) -> Transcript:
        """
        Transcribe a single audio file fully offline.

        Args:
            job_id: the AudioJob this transcript belongs to.
            audio_path: absolute or relative path to a validated audio file.
            language_hint: optional ISO 639-1 code; None triggers auto-detect.

        Returns:
            A populated Transcript domain object.

        Raises:
            ModelNotLoadedError: if load_model() was never called.
            TranscriptionError: if faster-whisper raises during decoding.
        """
        if self._model is None:
            raise ModelNotLoadedError(
                "Whisper model not loaded — call load_model() at startup before transcribing."
            )
        if not Path(audio_path).exists():
            raise TranscriptionError(f"Audio file not found: {audio_path}")

        start = time.monotonic()
        try:
            segments_iter, info = self._model.transcribe(
                audio_path,
                language=language_hint,
                beam_size=self._settings.whisper_beam_size,
                vad_filter=True,  # voice-activity detection trims silence
            )
            segments: list[TranscriptSegment] = []
            full_text_parts: list[str] = []
            for seg in segments_iter:
                segments.append(
                    TranscriptSegment(
                        start=seg.start,
                        end=seg.end,
                        text=seg.text.strip(),
                        avg_logprob=getattr(seg, "avg_logprob", None),
                        no_speech_prob=getattr(seg, "no_speech_prob", None),
                    )
                )
                full_text_parts.append(seg.text.strip())

            elapsed = time.monotonic() - start
            STT_DURATION_SECONDS.observe(elapsed)
            STT_REQUESTS_TOTAL.labels(status="success").inc()

            transcript = Transcript(
                job_id=job_id,
                full_text=" ".join(full_text_parts).strip(),
                segments=segments,
                detected_language=info.language,
                language_probability=info.language_probability,
                stt_model=f"faster-whisper-{self._settings.whisper_model_size}",
                processing_time_seconds=elapsed,
            )
            if transcript.low_confidence:
                logger.warning(
                    "low_confidence_transcript",
                    job_id=job_id,
                    language_probability=info.language_probability,
                )
            logger.info(
                "transcription_complete",
                job_id=job_id,
                duration_seconds=round(elapsed, 2),
                detected_language=info.language,
                segment_count=len(segments),
            )
            return transcript

        except Exception as exc:  # noqa: BLE001
            STT_REQUESTS_TOTAL.labels(status="error").inc()
            logger.error("transcription_failed", job_id=job_id, error=str(exc))
            raise TranscriptionError(f"Transcription failed for job {job_id}: {exc}") from exc


# Module-level helper for quick low-confidence checks outside the Transcript model,
# kept here so thresholds stay colocated with the service that produces them.
def is_segment_low_confidence(avg_logprob: Optional[float], language_probability: float) -> bool:
    if avg_logprob is not None and avg_logprob < LOW_CONFIDENCE_LOGPROB_THRESHOLD:
        return True
    return language_probability < LOW_CONFIDENCE_LANGUAGE_PROB_THRESHOLD
