# ADR-007: Probe-Then-Sync — Never Block the Field Operator's UI on Network I/O

## Status
Accepted

## Context
Field devices move between offline, intermittently connected, and online
states unpredictably — a technician might be in a basement (offline), step
outside (degraded cellular), then later connect to site WiFi (online). The
naive approach — attempt to sync the moment a report is created, and let the
HTTP call time out if there's no connectivity — would mean every single
`POST /jobs` request risks hanging for the full timeout duration on the
common case of "no signal."

## Decision
Adopt a strict **separation between report creation and report sync**:

1. `FieldOpsPipeline.run()` always enqueues the validated report locally
   (`SyncQueueWorker.enqueue()`) and returns immediately — it never calls
   `drain_queue()` inline, and never makes a network call to the central
   server as part of handling a recording.
2. A separate, explicit action — either a background poller calling
   `drain_queue()` on an interval, or the operator pressing "Sync Now" in
   the UI (`POST /sync/drain`) — is responsible for attempting delivery.
3. `drain_queue()` itself checks connectivity *first* via `ConnectivityProbe`
   and skips entirely (returning `[]`) if offline, rather than attempting
   HTTP calls that are expected to fail.

Rationale:
- The technician's primary workflow — record a voice note, get a structured
  report back — must work identically whether the device has full signal or
  none at all. Coupling report creation to sync success would mean a
  technician in a dead zone either can't generate reports at all, or has to
  wait through a timeout on every single recording.
- `ConnectivityProbe.check()` makes one lightweight request (to a `/health`
  endpoint, 3-second timeout) rather than letting every queued record's sync
  attempt independently discover "we're offline" via its own timeout — this
  is far cheaper, especially when there are many queued records.
- This is testable in isolation: `TestPipelineWithConnectivity::
  test_pipeline_run_does_not_block_on_online_sync` in the integration suite
  explicitly asserts that `pipeline.run()` never calls `connectivity.check()`
  — that responsibility belongs entirely to the sync layer.

## Consequences
- There is necessarily a window between "report created" and "report
  synced" — `JobStatus.PENDING_SYNC` is a real, potentially long-lived state,
  not a transient one. The UI's "Sync Status" tab exists specifically to
  make this window visible and actionable to the technician rather than
  hiding it.
- A background scheduler (e.g. a periodic task hitting `drain_queue()` every
  `CONNECTIVITY_CHECK_INTERVAL_SECONDS`) is the expected production pattern,
  though this case study exposes manual triggering (`POST /sync/drain`, the
  UI's "Sync Now" button) rather than building a full scheduler — that's a
  reasonable next increment, not a gap in the core architecture.
- Connectivity state itself is cached (`last_known_state`) so routes like
  `GET /sync/status` can report current state without making a fresh network
  call on every poll from the UI.
