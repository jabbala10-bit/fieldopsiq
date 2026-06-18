# ADR-005: SQLite for Edge-Local Storage

## Status
Accepted

## Context
FieldOpsIQ runs as a single-instance process on an individual field
technician's device. It needs durable local storage for audio job metadata,
transcripts, structured reports, and the outbound sync queue — durable enough
to survive process crashes, device reboots, and battery death mid-write.

Candidates: SQLite, Postgres, MySQL, a flat-file/JSON store, or an embedded
KV store (e.g. LevelDB/RocksDB).

## Decision
Use **SQLite** in WAL (write-ahead-log) mode as the sole storage engine for
the edge device.

Rationale:
- **Zero operational footprint.** There's no database server to install,
  configure, or keep running on a field laptop/tablet — exactly the kind of
  device where "another background service" is an availability and battery
  liability. Postgres/MySQL would require running and monitoring a separate
  daemon for what is, in this deployment, a single-writer, low-volume
  workload (a handful of voice recordings per shift, not a high-throughput
  multi-tenant system).
- **Crash safety via WAL mode.** SQLite's WAL journal mode (`PRAGMA
  journal_mode=WAL`, set in `SQLiteStorageService._init_schema()`) means a
  process killed mid-write doesn't corrupt the database — the next startup
  recovers cleanly. This matters because field devices are routinely
  suspended, killed, or run out of battery without a graceful shutdown.
- **Single file = trivial backup/transport.** The entire local state is one
  `.db` file. If a device needs to be replaced or a technician needs to hand
  off queued-but-unsynced reports, that file can be copied wholesale.
- **Sufficient performance at this scale.** This is not a high-concurrency
  workload — one technician, one device, occasional writes. SQLite's
  single-writer model is not a constraint here the way it would be for a
  multi-tenant server.

This decision is specific to the **edge node**. The central server that
FieldOpsIQ ultimately syncs reports *to* is explicitly out of scope for this
case study and would reasonably be Postgres or another server-grade database
in a real deployment — SQLite's tradeoffs (single-writer, no built-in
replication) are the right fit for the edge, not for a multi-technician
aggregation server.

## Consequences
- `SQLiteStorageService` uses connection-per-call rather than a long-lived
  connection pool — appropriate for SQLite's lightweight connection cost and
  avoids cross-thread connection-sharing pitfalls.
- All writes use `INSERT ... ON CONFLICT DO UPDATE` (upsert) rather than
  separate insert/update code paths, simplifying the crash-resume logic in
  the pipeline (re-running `save_job()` after a partial failure is always
  safe).
- If a future deployment needs multi-device write coordination on a single
  shared edge node (e.g. a site-level gateway serving multiple technicians'
  tablets), this decision should be revisited — SQLite's single-writer model
  would become a real constraint at that point.
