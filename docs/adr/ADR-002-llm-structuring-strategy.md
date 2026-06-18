# ADR-002: Local LLM Selection & Structured-Output Strategy — Ollama + Llama 3.x

## Status
Accepted

## Context
Raw transcripts are unstructured speech-to-text output. To be useful to
dispatch/ops systems, they must become a validated `FieldReport` (category,
severity, equipment ID, action taken, etc.). This structuring step must also
run fully offline — field operations data (site details, safety incidents,
equipment status) often cannot leave the device until it reaches the
authenticated central server, and there may be no connectivity at all when
the report is generated.

Candidates evaluated:

| Option | Pros | Cons |
|---|---|---|
| **Ollama + Llama 3.x** | General-purpose, strong instruction-following, good JSON-mode support via Ollama, widely deployed/documented, easy model swaps | Not specifically tuned for structured extraction; needs a repair loop for occasional malformed JSON |
| **Ollama + Qwen2.5** | Often benchmarks slightly better on structured/JSON-heavy tasks | Smaller community footprint in US enterprise field-ops deployments; less familiar to most platform teams |
| **llama.cpp direct** | Lowest overhead, no daemon process | Loses Ollama's model management (pull/swap/version), health-check endpoint, and multi-model serving — all useful for an FDE-style deployment that may host multiple models |

## Decision
Use **Ollama serving Llama 3.x (default `llama3.1:8b`)** as the structuring
engine, accessed via Ollama's HTTP API with `format=json` mode, wrapped in a
**parse-validate-repair retry loop** (`LLM_MAX_RETRIES = 3`).

Rationale:
- Ollama gives us a stable local HTTP interface (`/api/generate`,
  `/api/tags`) that the rest of the system treats as just another backing
  service — this matches the deployment pattern used in BioMedIQ and
  InferenceIQ, keeping operational tooling (health checks, Docker Compose
  service definitions) consistent across all case studies.
- Llama 3.x's instruction-following is reliable enough that with a low
  temperature (0.1) and a strict system prompt, malformed JSON is rare but
  not zero — hence the repair loop rather than assuming first-pass success.
- The repair loop feeds the previous parse/validation error back into the
  prompt on retry ("Your previous response was invalid JSON... Produce a
  corrected JSON object only"), which in practice resolves the majority of
  first-attempt failures (missing field, trailing comma) without needing a
  larger or more specialized model.
- `extraction_confidence` is a model-self-reported field, not a calibrated
  probability — it's treated as a heuristic signal for the human-review gate
  (ADR-004), not a guarantee.

## Consequences
- A 3-attempt cap means a sufficiently degraded transcript (e.g. very low
  STT confidence feeding into the LLM) can still hard-fail structuring; this
  is intentional — `SchemaValidationError` propagates to `PipelineResult` as
  a failed job rather than silently producing a low-quality report.
- Swapping to Qwen2.5 or any other Ollama-served model is a one-line config
  change (`LLM_MODEL` env var) since the service only depends on Ollama's
  generic `/api/generate` contract, not anything Llama-specific.
- If a future deployment needs higher structured-output reliability without
  the repair loop's latency cost, a constrained-decoding approach (e.g.
  grammar-constrained sampling) could replace the retry loop — left as a
  follow-up, not pursued here to keep the stack consistent with Ollama's
  out-of-the-box capabilities.
