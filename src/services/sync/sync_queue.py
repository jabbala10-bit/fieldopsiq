"""
Sync queue worker.

This is the heart of the offline-first design (ADR-003, ADR-007):
every FieldReport is written to the local sync_queue table the moment
it's validated, independent of whether the device is online. A
background worker drains the queue whenever connectivity is available,
using exponential backoff per-record so a single bad record doesn't
block the rest of the queue.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import httpx

from src.config.settings import Settings, get_settings
from src.domain.constants import (
    SYNC_BACKOFF_BASE_SECONDS,
    SYNC_BACKOFF_MAX_SECONDS,
    SYNC_BATCH_SIZE,
    SYNC_MAX_ATTEMPTS,
)
from src.domain.exceptions import ConnectivityUnavailableError, RemoteRejectedError, StorageError
from src.domain.schemas import ConnectivityState, FieldReport, SyncRecord, SyncResult
from src.observability.logging import get_logger
from src.observability.metrics import SYNC_ATTEMPTS_TOTAL, SYNC_QUEUE_DEPTH
from src.services.sync.connectivity import ConnectivityProbe

logger = get_logger(__name__)


class SyncQueueWorker:
    """SQLite-backed outbound queue with exponential backoff retry."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        connectivity: Optional[ConnectivityProbe] = None,
        client: Optional[httpx.Client] = None,
    ):
        self._settings = settings or get_settings()
        self._connectivity = connectivity or ConnectivityProbe(self._settings)
        self._client = client or httpx.Client(timeout=30)
        self._db_path = self._settings.sqlite_path
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ----------------------------------------------------------------
    # Enqueue
    # ----------------------------------------------------------------

    def enqueue(self, report: FieldReport) -> SyncRecord:
        """
        Adds a validated FieldReport to the sync queue.

        Reports are always enqueued, even when online — sync happens
        asynchronously via drain_queue(), never inline with the request
        that produced the report (ADR-007: never block the field
        operator's UI on network I/O).
        """
        record = SyncRecord(job_id=report.job_id, payload=report)
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO sync_queue (sync_id, job_id, payload_json, attempts, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        record.sync_id,
                        record.job_id,
                        report.model_dump_json(),
                        0,
                        record.created_at.isoformat(),
                    ),
                )
            self._refresh_queue_depth_metric()
            logger.info("report_enqueued", job_id=report.job_id, sync_id=record.sync_id)
            return record
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to enqueue report {report.job_id}: {exc}") from exc

    # ----------------------------------------------------------------
    # Drain
    # ----------------------------------------------------------------

    def drain_queue(self, force_check: bool = True) -> list[SyncResult]:
        """
        Attempts to sync all eligible queued records to the central server.

        Args:
            force_check: if True, performs a live connectivity check first.
                If False, relies on the probe's last cached state (useful
                for tight polling loops that already check separately).

        Returns:
            A list of SyncResult, one per record attempted this call.
        """
        state = self._connectivity.check() if force_check else self._connectivity.last_known_state
        if state == ConnectivityState.OFFLINE:
            logger.info("sync_skipped_offline")
            return []

        records = self._fetch_eligible_records()
        results: list[SyncResult] = []
        for record in records:
            result = self._attempt_sync(record)
            results.append(result)

        self._refresh_queue_depth_metric()
        return results

    def _fetch_eligible_records(self) -> list[SyncRecord]:
        """Fetches records under MAX_ATTEMPTS, oldest first, respecting backoff."""
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM sync_queue
                WHERE attempts < ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (SYNC_MAX_ATTEMPTS, SYNC_BATCH_SIZE),
            ).fetchall()

        eligible: list[SyncRecord] = []
        for row in rows:
            if row["last_attempt_at"] and not self._backoff_elapsed(row, now):
                continue
            payload = FieldReport.model_validate(json.loads(row["payload_json"]))
            eligible.append(
                SyncRecord(
                    sync_id=row["sync_id"],
                    job_id=row["job_id"],
                    payload=payload,
                    attempts=row["attempts"],
                    last_attempt_at=row["last_attempt_at"],
                    last_error=row["last_error"],
                    created_at=row["created_at"],
                )
            )
        return eligible

    @staticmethod
    def _backoff_elapsed(row: sqlite3.Row, now: datetime) -> bool:
        last_attempt = datetime.fromisoformat(row["last_attempt_at"])
        backoff = min(
            SYNC_BACKOFF_BASE_SECONDS * (2 ** row["attempts"]),
            SYNC_BACKOFF_MAX_SECONDS,
        )
        return (now - last_attempt).total_seconds() >= backoff

    def _attempt_sync(self, record: SyncRecord) -> SyncResult:
        try:
            resp = self._client.post(
                self._settings.central_server_url,
                json=record.payload.model_dump(mode="json"),
                headers=self._auth_headers(),
            )
            if 200 <= resp.status_code < 300:
                self._mark_synced(record.sync_id)
                SYNC_ATTEMPTS_TOTAL.labels(status="success").inc()
                logger.info("sync_succeeded", job_id=record.job_id, sync_id=record.sync_id)
                return SyncResult(sync_id=record.sync_id, success=True, http_status=resp.status_code)

            error_msg = f"Remote rejected with HTTP {resp.status_code}: {resp.text[:200]}"
            self._record_failure(record, error_msg)
            SYNC_ATTEMPTS_TOTAL.labels(status="failed").inc()
            logger.warning(
                "sync_rejected", job_id=record.job_id, sync_id=record.sync_id, status=resp.status_code
            )
            return SyncResult(
                sync_id=record.sync_id, success=False, http_status=resp.status_code, error=error_msg
            )

        except httpx.RequestError as exc:
            error_msg = f"Network error during sync: {exc}"
            self._record_failure(record, error_msg)
            SYNC_ATTEMPTS_TOTAL.labels(status="failed").inc()
            logger.warning("sync_network_error", job_id=record.job_id, error=error_msg)
            return SyncResult(sync_id=record.sync_id, success=False, error=error_msg)

    def _auth_headers(self) -> dict[str, str]:
        if self._settings.central_server_api_key:
            return {"Authorization": f"Bearer {self._settings.central_server_api_key}"}
        return {}

    def _mark_synced(self, sync_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sync_queue WHERE sync_id = ?", (sync_id,))

    def _record_failure(self, record: SyncRecord, error: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        new_attempts = record.attempts + 1
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE sync_queue
                SET attempts = ?, last_attempt_at = ?, last_error = ?
                WHERE sync_id = ?
                """,
                (new_attempts, now, error, record.sync_id),
            )
        if new_attempts >= SYNC_MAX_ATTEMPTS:
            SYNC_ATTEMPTS_TOTAL.labels(status="exhausted").inc()
            logger.error(
                "sync_exhausted",
                job_id=record.job_id,
                sync_id=record.sync_id,
                attempts=new_attempts,
            )

    # ----------------------------------------------------------------
    # Introspection
    # ----------------------------------------------------------------

    def queue_depth(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM sync_queue").fetchone()
        return row["c"]

    def dead_letter_records(self) -> list[SyncRecord]:
        """Records that have exhausted all retry attempts — need manual intervention."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sync_queue WHERE attempts >= ?", (SYNC_MAX_ATTEMPTS,)
            ).fetchall()
        return [
            SyncRecord(
                sync_id=r["sync_id"],
                job_id=r["job_id"],
                payload=FieldReport.model_validate(json.loads(r["payload_json"])),
                attempts=r["attempts"],
                last_attempt_at=r["last_attempt_at"],
                last_error=r["last_error"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def _refresh_queue_depth_metric(self) -> None:
        SYNC_QUEUE_DEPTH.set(self.queue_depth())

    def close(self) -> None:
        self._client.close()
        self._connectivity.close()
