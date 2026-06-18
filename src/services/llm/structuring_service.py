"""
LLM structuring service: converts a raw transcript into a validated
FieldReport using a local Ollama model (Llama 3.x by default).

Design notes (see ADR-002 for full model selection rationale):
  - Runs fully offline against a local Ollama daemon — no cloud LLM call
    ever happens, which is the core privacy/compliance requirement for
    field operations data (often contains site/equipment/safety info).
  - Uses Ollama's structured `format=json` mode plus a strict Pydantic
    parse-and-retry loop, because LLMs occasionally emit near-valid JSON
    (trailing commas, missing fields) that needs one repair pass before
    being treated as a hard failure.
  - Temperature is fixed low (0.1) for deterministic field extraction —
    this is a structured-extraction task, not creative generation.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

import httpx

from src.config.settings import Settings, get_settings
from src.domain.constants import LLM_MAX_RETRIES, LLM_REQUEST_TIMEOUT_SECONDS
from src.domain.exceptions import LLMUnavailableError, SchemaValidationError, StructuringError
from src.domain.schemas import FieldReport, ReportCategory, Severity
from src.observability.logging import get_logger
from src.observability.metrics import LLM_REQUESTS_TOTAL, LLM_STRUCTURING_DURATION_SECONDS

logger = get_logger(__name__)

_SYSTEM_PROMPT = """You are a field-operations report extraction assistant.
You convert raw voice-transcribed technician notes into structured JSON reports.

Respond with ONLY a JSON object, no markdown fences, no commentary, matching this schema:
{
  "category": one of ["safety_incident", "equipment_fault", "maintenance_completed", "inspection_note", "parts_request", "general_note"],
  "severity": one of ["low", "medium", "high", "critical"],
  "summary": "one or two sentence summary, max 500 chars",
  "equipment_id": "equipment identifier mentioned, or null",
  "location_detail": "specific location detail mentioned, or null",
  "action_taken": "action the technician took, or null",
  "follow_up_required": true or false,
  "follow_up_notes": "details if follow_up_required is true, else null",
  "extraction_confidence": a number between 0.0 and 1.0 representing your confidence in this extraction
}

Rules:
- If the transcript is ambiguous or fragments are unclear, lower extraction_confidence accordingly.
- Never invent equipment IDs or locations not mentioned in the transcript.
- severity "critical" is reserved for immediate safety risk to personnel.
"""


class OllamaStructuringService:
    """Wraps an Ollama HTTP client to produce validated FieldReport objects."""

    def __init__(self, settings: Optional[Settings] = None, client: Optional[httpx.Client] = None):
        self._settings = settings or get_settings()
        self._client = client or httpx.Client(
            base_url=self._settings.ollama_base_url,
            timeout=LLM_REQUEST_TIMEOUT_SECONDS,
        )

    def health_check(self) -> bool:
        """Returns True if the Ollama daemon is reachable."""
        try:
            resp = self._client.get("/api/tags")
            return resp.status_code == 200
        except httpx.RequestError:
            return False

    def structure(self, job_id: str, transcript_text: str) -> FieldReport:
        """
        Convert raw transcript text into a validated FieldReport.

        Retries up to LLM_MAX_RETRIES times on JSON parse / schema
        validation failure, feeding the parse error back to the model
        on retry so it can self-correct (ADR-002's "repair loop").

        Raises:
            LLMUnavailableError: Ollama daemon unreachable.
            SchemaValidationError: model output never validated after all retries.
        """
        last_error: Optional[str] = None
        start = time.monotonic()

        for attempt in range(1, LLM_MAX_RETRIES + 1):
            try:
                raw_json = self._call_ollama(transcript_text, repair_hint=last_error)
                report = self._parse_and_validate(job_id, transcript_text, raw_json)

                elapsed = time.monotonic() - start
                LLM_STRUCTURING_DURATION_SECONDS.observe(elapsed)
                LLM_REQUESTS_TOTAL.labels(status="success").inc()
                logger.info(
                    "structuring_complete",
                    job_id=job_id,
                    attempt=attempt,
                    category=report.category.value,
                    confidence=report.extraction_confidence,
                )
                return report

            except httpx.RequestError as exc:
                LLM_REQUESTS_TOTAL.labels(status="unavailable").inc()
                raise LLMUnavailableError(
                    f"Could not reach Ollama at {self._settings.ollama_base_url}: {exc}"
                ) from exc

            except (json.JSONDecodeError, ValueError) as exc:
                last_error = str(exc)
                logger.warning(
                    "structuring_retry",
                    job_id=job_id,
                    attempt=attempt,
                    error=last_error,
                )
                continue

        LLM_REQUESTS_TOTAL.labels(status="schema_failure").inc()
        raise SchemaValidationError(
            f"LLM output failed schema validation after {LLM_MAX_RETRIES} attempts "
            f"for job {job_id}. Last error: {last_error}"
        )

    def _call_ollama(self, transcript_text: str, repair_hint: Optional[str]) -> dict[str, Any]:
        user_prompt = f"Transcript:\n{transcript_text}"
        if repair_hint:
            user_prompt += (
                f"\n\nYour previous response was invalid JSON or failed schema validation "
                f"with error: '{repair_hint}'. Produce a corrected JSON object only."
            )

        try:
            resp = self._client.post(
                "/api/generate",
                json={
                    "model": self._settings.llm_model,
                    "system": _SYSTEM_PROMPT,
                    "prompt": user_prompt,
                    "format": "json",
                    "stream": False,
                    "options": {"temperature": self._settings.llm_temperature},
                },
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise StructuringError(f"Ollama returned HTTP {exc.response.status_code}") from exc

        body = resp.json()
        response_text = body.get("response", "")
        return json.loads(response_text)  # may raise JSONDecodeError -> caught by caller

    def _parse_and_validate(
        self, job_id: str, transcript_text: str, raw: dict[str, Any]
    ) -> FieldReport:
        excerpt = transcript_text[:1000]
        try:
            return FieldReport(
                job_id=job_id,
                category=ReportCategory(raw["category"]),
                severity=Severity(raw["severity"]),
                summary=raw["summary"],
                equipment_id=raw.get("equipment_id"),
                location_detail=raw.get("location_detail"),
                action_taken=raw.get("action_taken"),
                follow_up_required=bool(raw.get("follow_up_required", False)),
                follow_up_notes=raw.get("follow_up_notes"),
                raw_transcript_excerpt=excerpt,
                extraction_confidence=float(raw["extraction_confidence"]),
                llm_model=self._settings.llm_model,
            )
        except (KeyError, ValueError) as exc:
            # Re-raised as ValueError so the retry loop above catches it uniformly
            raise ValueError(f"Schema validation failed: {exc}") from exc

    def close(self) -> None:
        self._client.close()
