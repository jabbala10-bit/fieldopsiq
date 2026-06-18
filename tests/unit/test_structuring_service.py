"""Unit tests for src/services/llm/structuring_service.py."""
from __future__ import annotations

import json

import httpx
import pytest

from src.domain.exceptions import LLMUnavailableError, SchemaValidationError
from src.services.llm.structuring_service import OllamaStructuringService

VALID_LLM_RESPONSE = {
    "category": "equipment_fault",
    "severity": "medium",
    "summary": "Compressor on unit 4 making grinding noise; shut down and tagged out.",
    "equipment_id": "UNIT-4-COMPRESSOR",
    "location_detail": None,
    "action_taken": "Shut down and tagged out the unit.",
    "follow_up_required": True,
    "follow_up_notes": "Needs maintenance inspection before restart.",
    "extraction_confidence": 0.91,
}


def _mock_transport(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://localhost:11434")


class TestHealthCheck:
    def test_health_check_true_on_200(self, test_settings):
        def handler(request):
            return httpx.Response(200, json={"models": []})

        client = _mock_transport(handler)
        service = OllamaStructuringService(test_settings, client=client)
        assert service.health_check() is True

    def test_health_check_false_on_connection_error(self, test_settings):
        def handler(request):
            raise httpx.ConnectError("refused")

        client = _mock_transport(handler)
        service = OllamaStructuringService(test_settings, client=client)
        assert service.health_check() is False


class TestStructure:
    def test_valid_response_produces_field_report(self, test_settings):
        def handler(request):
            return httpx.Response(200, json={"response": json.dumps(VALID_LLM_RESPONSE)})

        client = _mock_transport(handler)
        service = OllamaStructuringService(test_settings, client=client)

        report = service.structure("job-1", "The compressor on unit 4 is making a grinding noise.")

        assert report.job_id == "job-1"
        assert report.category.value == "equipment_fault"
        assert report.severity.value == "medium"
        assert report.extraction_confidence == 0.91
        assert report.llm_model == test_settings.llm_model

    def test_connection_error_raises_llm_unavailable(self, test_settings):
        def handler(request):
            raise httpx.ConnectError("refused")

        client = _mock_transport(handler)
        service = OllamaStructuringService(test_settings, client=client)

        with pytest.raises(LLMUnavailableError):
            service.structure("job-1", "some transcript")

    def test_malformed_json_retries_then_raises_schema_error(self, test_settings):
        call_count = {"n": 0}

        def handler(request):
            call_count["n"] += 1
            return httpx.Response(200, json={"response": "not valid json {{{"})

        client = _mock_transport(handler)
        service = OllamaStructuringService(test_settings, client=client)

        with pytest.raises(SchemaValidationError):
            service.structure("job-1", "some transcript")
        assert call_count["n"] == 3  # LLM_MAX_RETRIES

    def test_recovers_after_one_bad_attempt(self, test_settings):
        call_count = {"n": 0}

        def handler(request):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return httpx.Response(200, json={"response": "{invalid"})
            return httpx.Response(200, json={"response": json.dumps(VALID_LLM_RESPONSE)})

        client = _mock_transport(handler)
        service = OllamaStructuringService(test_settings, client=client)

        report = service.structure("job-1", "transcript text")
        assert report.category.value == "equipment_fault"
        assert call_count["n"] == 2

    def test_missing_required_field_triggers_retry_then_fails(self, test_settings):
        incomplete = {k: v for k, v in VALID_LLM_RESPONSE.items() if k != "severity"}

        def handler(request):
            return httpx.Response(200, json={"response": json.dumps(incomplete)})

        client = _mock_transport(handler)
        service = OllamaStructuringService(test_settings, client=client)

        with pytest.raises(SchemaValidationError):
            service.structure("job-1", "transcript text")

    def test_repair_hint_included_on_retry(self, test_settings):
        prompts_seen = []

        def handler(request):
            body = json.loads(request.content)
            prompts_seen.append(body["prompt"])
            if len(prompts_seen) == 1:
                return httpx.Response(200, json={"response": "bad json"})
            return httpx.Response(200, json={"response": json.dumps(VALID_LLM_RESPONSE)})

        client = _mock_transport(handler)
        service = OllamaStructuringService(test_settings, client=client)
        service.structure("job-1", "transcript text")

        assert "previous response was invalid" in prompts_seen[1]
