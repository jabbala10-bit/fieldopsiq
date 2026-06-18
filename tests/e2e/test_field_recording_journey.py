"""
End-to-end test: simulates a full field-technician journey through the
real FastAPI app (no mocked pipeline this time) with a mocked STT model
and mocked Ollama HTTP layer standing in for the two external systems
that genuinely can't run in CI (a multi-hundred-MB Whisper model and a
local Ollama daemon).

This is the closest thing to "click record, see the synced report" that
can run in an automated test environment.
"""
from __future__ import annotations

import io
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from src.api import dependencies as deps
from src.api.main import app
from src.domain.schemas import ConnectivityState
from src.services.llm.structuring_service import OllamaStructuringService
from src.services.pipeline import FieldOpsPipeline
from src.services.storage.sqlite_service import SQLiteStorageService
from src.services.stt.preprocessor import AudioPreprocessor
from src.services.stt.whisper_service import WhisperSTTService
from src.services.sync.connectivity import ConnectivityProbe
from src.services.sync.sync_queue import SyncQueueWorker

VALID_LLM_RESPONSE = {
    "category": "equipment_fault",
    "severity": "high",
    "summary": "Pump 3 bearing failure suspected; technician isolated pump and requested parts.",
    "equipment_id": "PUMP-3",
    "location_detail": "North filtration building, bay 2",
    "action_taken": "Isolated pump, applied lockout tag",
    "follow_up_required": True,
    "follow_up_notes": "Order replacement bearing kit P/N 88213.",
    "extraction_confidence": 0.88,
}


@pytest.fixture
def e2e_client(test_settings, tmp_path):
    """
    Wires the real pipeline (real SQLite, real preprocessing) but with a
    mocked Whisper model and a mocked Ollama HTTP transport, then injects
    it via dependency_overrides so the full FastAPI request path —
    multipart upload, validation, orchestration, persistence, sync
    enqueue — is exercised exactly as a field device would run it.
    """
    fake_segments = [
        SimpleNamespace(
            start=0.0, end=5.0,
            text="Pump three is showing a bearing failure, I isolated it and locked it out, need a replacement bearing kit",
            avg_logprob=-0.25, no_speech_prob=0.01,
        )
    ]
    fake_info = SimpleNamespace(language="en", language_probability=0.96)

    stt = WhisperSTTService(test_settings)
    stt._model = MagicMock()
    stt._model.transcribe.return_value = (fake_segments, fake_info)

    def ollama_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": json.dumps(VALID_LLM_RESPONSE)})

    llm = OllamaStructuringService(
        test_settings,
        client=httpx.Client(transport=httpx.MockTransport(ollama_handler), base_url=test_settings.ollama_base_url),
    )

    storage = SQLiteStorageService(test_settings)

    offline_probe = MagicMock(spec=ConnectivityProbe)
    offline_probe.check.return_value = ConnectivityState.OFFLINE
    offline_probe.last_known_state = ConnectivityState.OFFLINE
    offline_probe.close = MagicMock()
    sync_worker = SyncQueueWorker(test_settings, connectivity=offline_probe)

    pipeline = FieldOpsPipeline(
        settings=test_settings,
        stt_service=stt,
        preprocessor=AudioPreprocessor(),
        structuring_service=llm,
        storage=storage,
        sync_worker=sync_worker,
    )

    app.dependency_overrides[deps.get_pipeline] = lambda: pipeline
    app.dependency_overrides[deps.get_sync_worker] = lambda: sync_worker
    app.dependency_overrides[deps.get_connectivity_probe] = lambda: offline_probe
    app.dependency_overrides[deps.get_settings] = lambda: test_settings

    with TestClient(app) as client:
        yield client, storage, sync_worker

    app.dependency_overrides.clear()


class TestFullFieldRecordingJourney:
    def test_record_transcribe_structure_queue_and_inspect(self, e2e_client, sample_wav_file):
        client, storage, sync_worker = e2e_client

        # 1. Technician submits a voice note from a job site with no signal.
        with open(sample_wav_file, "rb") as f:
            resp = client.post(
                "/jobs",
                data={"technician_id": "TECH-742", "site_id": "SITE-NORTH-FILTRATION"},
                files={"audio_file": ("voice_note.wav", f, "audio/wav")},
            )
        assert resp.status_code == 201
        body = resp.json()
        job_id = body["job"]["job_id"]

        # 2. The report was structured correctly from the (mocked) transcript.
        assert body["report"]["category"] == "equipment_fault"
        assert body["report"]["severity"] == "high"
        assert body["report"]["equipment_id"] == "PUMP-3"
        assert body["sync_status"] == "pending_sync"

        # 3. Because severity is "high", this should be flagged for human review.
        assert any("human review" in w for w in body["warnings"])

        # 4. The transcript and report are independently retrievable.
        transcript_resp = client.get(f"/jobs/{job_id}/transcript")
        assert transcript_resp.status_code == 200
        assert "bearing failure" in transcript_resp.json()["full_text"]

        report_resp = client.get(f"/jobs/{job_id}/report")
        assert report_resp.status_code == 200

        # 5. The report is sitting in the local sync queue, not yet synced
        #    (device is offline at the job site).
        assert sync_worker.queue_depth() == 1

        # 6. Sync status reflects the queued report and offline state.
        status_resp = client.get("/sync/status")
        assert status_resp.json()["queue_depth"] == 1

    def test_job_status_progresses_through_lifecycle(self, e2e_client, sample_wav_file):
        client, storage, sync_worker = e2e_client

        with open(sample_wav_file, "rb") as f:
            resp = client.post(
                "/jobs",
                data={"technician_id": "TECH-742", "site_id": "SITE-NORTH-FILTRATION"},
                files={"audio_file": ("voice_note.wav", f, "audio/wav")},
            )
        job_id = resp.json()["job"]["job_id"]

        job_resp = client.get(f"/jobs/{job_id}")
        assert job_resp.status_code == 200
        assert job_resp.json()["status"] == "pending_sync"
        assert job_resp.json()["duration_seconds"] == pytest.approx(1.0, abs=0.1)
