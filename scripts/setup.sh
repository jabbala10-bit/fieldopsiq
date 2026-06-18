#!/usr/bin/env bash
# First-time setup helper for FieldOpsIQ.
# Usage: bash scripts/setup.sh
set -euo pipefail

echo "=== FieldOpsIQ setup ==="

if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found. Install Python 3.11+ first." >&2
    exit 1
fi

echo "-> Installing Python dependencies..."
pip install -r requirements.txt

if [ ! -f .env ]; then
    echo "-> Creating .env from .env.example..."
    cp .env.example .env
else
    echo "-> .env already exists, leaving it untouched."
fi

echo "-> Creating local data directories..."
mkdir -p data/audio_inbox data/transcripts data/reports models/whisper

if command -v ollama &> /dev/null; then
    echo "-> Ollama detected. Pulling default model (llama3.1:8b)..."
    ollama pull llama3.1:8b || echo "WARNING: could not pull model — is the Ollama daemon running?"
else
    echo "WARNING: Ollama not found on PATH. Install it from https://ollama.com before running the pipeline."
fi

echo ""
echo "Setup complete. Next steps:"
echo "  1. Review and edit .env"
echo "  2. make run-api   # starts the FastAPI pipeline service on :8000"
echo "  3. make run-ui    # starts the Gradio UI on :7860"
