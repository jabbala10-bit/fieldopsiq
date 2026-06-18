# ADR-003: Validate Before Queue, Not After

## Status
Accepted

## Context
A `FieldReport` must be schema-valid (correct enum values, summary length,
confidence bounds) before it's persisted and queued for sync. There are two
places this validation could happen:

1. **Before enqueue**: validate immediately after the LLM produces the
   report, inside the pipeline orchestrator, using Pydantic's `FieldReport`
   model construction itself as the validation gate.
2. **After enqueue, before sync**: accept whatever the LLM produced into the
   queue as a loosely-typed payload, and validate only when attempting to
   sync to the central server.

## Decision
**Validate before queue.** `OllamaStructuringService.structure()` only ever
returns a fully-validated `FieldReport` Pydantic object — invalid LLM output
is caught and retried inside the structuring service itself (ADR-002), and
if it never validates, a `SchemaValidationError` is raised and the job is
marked `FAILED` before anything reaches the sync queue.

Rationale:
- The sync queue (`SyncQueueWorker`) is the layer responsible for offline
  durability and retry/backoff — it should not also be responsible for
  catching schema errors. Mixing those concerns would mean a malformed
  record could sit in the queue for `SYNC_MAX_ATTEMPTS` retries, each one
  guaranteed to fail, before being dead-lettered — wasted battery and CPU
  cycles on a field device for a failure that was knowable immediately.
- Validating before queue means every record in `sync_queue` is guaranteed
  schema-valid. The dead-letter path (`dead_letter_records()`) therefore only
  ever represents genuine *delivery* failures (network, server rejection),
  not data-quality failures — which makes the dead-letter queue meaningfully
  actionable: "these reports are good, they just couldn't reach the server,"
  versus a mixed queue where some records are dead-on-arrival.
- It surfaces structuring failures to the technician immediately (via the
  `422` response on `POST /jobs`) rather than silently queuing something
  that will never sync — better UX for catching a bad recording at the job
  site, while the technician can still re-record.

## Consequences
- The pipeline's `run()` method must treat structuring failure as terminal
  for that job (no partial/best-effort report is ever queued).
- The sync queue's `payload_json` column can be deserialized with a hard
  `FieldReport.model_validate()` call (see `sync_queue.py`) with the
  confidence that it will always succeed for rows currently in the table —
  any future schema migration must account for already-queued legacy rows.
- If the `FieldReport` schema changes (e.g. a new required field), an
  explicit data migration for any rows already sitting in `sync_queue` is
  needed — this is a known tradeoff of validating early rather than late.
