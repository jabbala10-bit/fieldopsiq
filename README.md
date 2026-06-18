# FieldOpsIQ

**CS-06 — Offline STT + LLM Pipeline for Field Operations**

Field technicians in utilities, telecom, oil & gas, and manufacturing
maintenance routinely work in locations with no or intermittent
connectivity — basements, remote substations, underground vaults. FieldOpsIQ
lets a technician record a voice note on-site and get back a structured,
standardized field report — **fully offline**, with reports queued locally
and synced to a central ops system automatically once connectivity returns.

```
Voice note → faster-whisper (local STT) → Llama 3.x via Ollama (local LLM)
           → validated FieldReport → SQLite sync queue → central server
```

## Why this case study

This is one of ten Forward-Deployed-Engineer-style case studies in a
portfolio spanning manufacturing QA, defect detection, adaptive RAG support
systems, biomedical fine-tuning, multi-GPU inference, and — here — an
offline-first edge pipeline. The architectural problem this one is built to
demonstrate: **how do you design software that has to work when the network
doesn't?** Every design decision below traces back to that constraint.

## Architecture

See [`docs/diagrams/architecture.md`](docs/diagrams/architecture.md) for C4
context/container diagrams, the end-to-end sequence diagram, and the sync
queue / job lifecycle state machines.

```
src/
├── domain/          # Pydantic schemas, exceptions, constants — no framework deps
├── config/          # 12-factor Settings (ADR-006)
├── services/
│   ├── stt/         # faster-whisper wrapper + audio preprocessing (ADR-001)
│   ├── llm/         # Ollama structuring service with repair-loop retries (ADR-002)
│   ├── storage/      # SQLite persistence, WAL mode (ADR-005)
│   ├── sync/        # Connectivity probe + offline-first sync queue (ADR-007)
│   └── pipeline.py  # Linear orchestrator tying it all together (ADR-008)
├── api/             # FastAPI routes, middleware (auth, rate limit, audit)
├── ui/              # Gradio UI for the technician (record, view, sync)
└── observability/   # structlog + Prometheus metrics
```

Every architecture decision worth defending in an interview is written up as
an ADR in [`docs/adr/`](docs/adr/):

| ADR | Decision |
|---|---|
| [001](docs/adr/ADR-001-stt-engine-selection.md) | faster-whisper over whisper.cpp / Distil-Whisper |
| [002](docs/adr/ADR-002-llm-structuring-strategy.md) | Ollama + Llama 3.x with a parse-validate-repair loop |
| [003](docs/adr/ADR-003-validate-before-queue.md) | Validate the report schema *before* it ever reaches the sync queue |
| [004](docs/adr/ADR-004-confidence-thresholds.md) | Concrete numeric thresholds for low-confidence / human-review flags |
| [005](docs/adr/ADR-005-sqlite-edge-storage.md) | SQLite (not Postgres) for the edge node |
| [006](docs/adr/ADR-006-twelve-factor-config.md) | 12-factor, environment-based configuration |
| [007](docs/adr/ADR-007-probe-then-sync.md) | Never block the technician's UI on network I/O |
| [008](docs/adr/ADR-008-plain-orchestrator-not-langgraph.md) | Plain orchestrator class, not a LangGraph state machine |
| [009](docs/adr/ADR-009-security-model.md) | Localhost-scoped auth model + compliance mapping |

## Quickstart

```bash
make dev            # installs deps, creates .env and data dirs
# edit .env if needed (defaults work for local dev)
make models         # pulls llama3.1:8b via Ollama (requires Ollama installed)
make run-api         # starts FastAPI on :8000
make run-ui          # in another terminal: starts Gradio UI on :7860
```

Or via Docker Compose (includes Ollama as a service):

```bash
export API_AUTH_TOKEN=your-token-here
docker compose -f deployment/docker/docker-compose.yml up -d
docker compose -f deployment/docker/docker-compose.yml exec ollama ollama pull llama3.1:8b
```

Then open `http://localhost:7860` and record a voice note.

## Testing

```bash
make test              # full suite: unit + integration + e2e
make test-unit         # fast, fully mocked (no Whisper model, no Ollama needed)
make test-integration  # real SQLite, mocked STT/LLM, real FastAPI routing
make test-e2e          # full request-to-sync-queue journey through the API
```

Unit tests never require the actual faster-whisper model or a running Ollama
daemon — `WhisperModel` and the Ollama HTTP client are mocked at their
respective boundaries, so the suite runs in seconds on a CI runner with no
GPU and no multi-GB model downloads.

## API surface

| Route | Purpose |
|---|---|
| `POST /jobs` | Submit a voice recording; runs the full pipeline synchronously |
| `GET /jobs/{job_id}` | Job status |
| `GET /jobs/{job_id}/transcript` | Raw transcript |
| `GET /jobs/{job_id}/report` | Structured field report |
| `GET /jobs?status_filter=...` | List jobs by status |
| `POST /sync/drain` | Manually trigger a sync attempt |
| `GET /sync/status` | Queue depth, connectivity, dead-letter count |
| `GET /sync/dead-letter` | Records that exhausted retry attempts |
| `GET /health`, `GET /health/ready` | Liveness / readiness |
| `GET /metrics` | Prometheus metrics |

## Observability

Structured JSON logs (via `structlog`) and Prometheus metrics covering STT
duration, LLM structuring duration/failures, sync attempt outcomes, queue
depth, connectivity state, and human-review queue depth — see
[`src/observability/metrics.py`](src/observability/metrics.py) for the full
list.

## Known limitations / honest caveats

- This was built and validated in a sandboxed environment without outbound
  network access, so `pytest` could not actually be executed here — every
  file was syntax-validated (`py_compile`) and the test suite follows the
  same mocking patterns (no real network calls, deferred imports for heavy
  native deps) used successfully in the other case studies in this
  portfolio. Run `make test` in an environment with `pip install` access to
  get real pass/fail results.
- The central ops server FieldOpsIQ syncs to is explicitly out of scope —
  `CENTRAL_SERVER_URL` points at a placeholder in `.env.example`.
- The Kubernetes manifests model a single-replica deployment by design
  (SQLite is single-writer) — see the note at the top of
  `deployment/k8s/api-deployment.yaml` for when k8s is/isn't the right fit
  for this architecture.
