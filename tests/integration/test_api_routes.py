"""
Integration tests for the FastAPI application.

Uses FastAPI's TestClient with dependency overrides so routes are
exercised against real Starlette routing/middleware/validation, while
the heavy STT/LLM services remain mocked (no real Whisper model or
Ollama daemon required).
"""
from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api import dependencies as deps
from src.api.main import app
from src.domain.schemas import ConnectivityState, JobStatus, PipelineResult
from src.services.pipeline import FieldOpsPipeline
from src.services.sync.connectivity import ConnectivityProbe
from src.services.sync.sync_queue import SyncQueueWorker


@pytest.fixture
def client(sample_audio_job, sample_transcript, sample_field_report) -> TestClient:
    mock_pipeline = MagicMock(spec=FieldOpsPipeline)
    mock_pipeline.run.return_value = PipelineResult(
        job=sample_audio_job,
        transcript=sample_transcript,
        report=sample_field_report,
        sync_status=JobStatus.PENDING_SYNC,
        warnings=[],
    )

    mock_sync_worker = MagicMock(spec=SyncQueueWorker)
    mock_sync_worker.queue_depth.return_value = 0
    mock_sync_worker.dead_letter_records.return_value = []
    mock_sync_worker.drain_queue.return_value = []

    mock_connectivity = MagicMock(spec=ConnectivityProbe)
    mock_connectivity.last_known_state = ConnectivityState.ONLINE
    mock_connectivity.check.return_value = ConnectivityState.ONLINE

    app.dependency_overrides[deps.get_pipeline] = lambda: mock_pipeline
    app.dependency_overrides[deps.get_sync_worker] = lambda: mock_sync_worker
    app.dependency_overrides[deps.get_connectivity_probe] = lambda: mock_connectivity
    app.dependency_overrides[deps.get_structuring_service] = lambda: MagicMock(health_check=lambda: True)

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


class TestHealthRoutes:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_readiness_returns_ollama_status(self, client):
        resp = client.get("/health/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ollama_reachable"] is True
        assert body["central_server_connectivity"] == "online"


class TestJobRoutes:
    def test_submit_job_returns_pipeline_result(self, client):
        audio_bytes = io.BytesIO(b"\x00" * 100)
        resp = client.post(
            "/jobs",
            data={"technician_id": "TECH-1", "site_id": "SITE-1"},
            files={"audio_file": ("note.wav", audio_bytes, "audio/wav")},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["sync_status"] == "pending_sync"
        assert body["report"] is not None

    def test_submit_job_missing_fields_returns_422(self, client):
        audio_bytes = io.BytesIO(b"\x00" * 100)
        resp = client.post(
            "/jobs",
            data={"technician_id": "TECH-1"},  # missing site_id
            files={"audio_file": ("note.wav", audio_bytes, "audio/wav")},
        )
        assert resp.status_code == 422

    def test_get_nonexistent_job_returns_404(self, client):
        resp = client.get("/jobs/does-not-exist")
        assert resp.status_code == 404


class TestSyncRoutes:
    def test_sync_status_returns_queue_info(self, client):
        resp = client.get("/sync/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["queue_depth"] == 0
        assert body["connectivity"] == "online"

    def test_drain_sync_triggers_worker(self, client):
        resp = client.post("/sync/drain")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_connectivity_check(self, client):
        resp = client.get("/sync/connectivity")
        assert resp.status_code == 200
        assert resp.json()["state"] == "online"


class TestMetricsEndpoint:
    def test_metrics_endpoint_returns_prometheus_format(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "fieldopsiq" in resp.text or resp.text == ""  # may be empty pre-traffic, that's fine
