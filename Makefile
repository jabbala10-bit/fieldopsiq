.PHONY: dev models test test-unit test-integration test-e2e lint format docker-build docker-up docker-down clean

# Install Python dependencies and set up local dirs/.env
dev:
	pip install -r requirements.txt
	test -f .env || cp .env.example .env
	mkdir -p data/audio_inbox data/transcripts data/reports models/whisper
	@echo "Done. Edit .env, then run 'make models' before starting the API."

# Pull the default Ollama model (requires Ollama running locally or via docker-up)
models:
	ollama pull llama3.1:8b

test:
	pytest

test-unit:
	pytest tests/unit -v

test-integration:
	pytest tests/integration -v

test-e2e:
	pytest tests/e2e -v

lint:
	ruff check src tests
	mypy src --ignore-missing-imports

format:
	ruff check --fix src tests
	ruff format src tests

run-api:
	uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000

run-ui:
	python src/ui/app.py

docker-build:
	docker build -f deployment/docker/Dockerfile -t fieldopsiq-api:latest .
	docker build -f deployment/docker/Dockerfile.ui -t fieldopsiq-ui:latest .

docker-up:
	docker compose -f deployment/docker/docker-compose.yml up -d

docker-down:
	docker compose -f deployment/docker/docker-compose.yml down

clean:
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache htmlcov .coverage .mypy_cache .ruff_cache
