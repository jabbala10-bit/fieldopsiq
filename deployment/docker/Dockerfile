# FieldOpsIQ — pipeline API service image.
# Note: this image runs the FastAPI orchestrator. Ollama runs as a separate
# container/service (see docker-compose.yml) since it manages its own model
# weights and has its own release cadence.

FROM python:3.11-slim AS base

# ffmpeg is required by faster-whisper for broad audio-codec support beyond
# what soundfile alone handles (e.g. some .m4a / .mp3 variants from phones).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY pyproject.toml .

RUN mkdir -p data/audio_inbox data/transcripts data/reports models/whisper

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()" || exit 1

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
