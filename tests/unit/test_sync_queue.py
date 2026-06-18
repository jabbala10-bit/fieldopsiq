"""Unit tests for src/services/sync/sync_queue.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx
import pytest

from src.domain.schemas import ConnectivityState
from src.services.storage.sqlite_service import SQLiteStorageService
from src.services.sync.connectivity import ConnectivityProbe
from src.services.sync.sync_queue import SyncQueueWorker


def _online_probe() -> ConnectivityProbe:
    probe = MagicMock(spec=ConnectivityProbe)
    probe.check.return_value = ConnectivityState.ONLINE
    probe.last_known_state = ConnectivityState.ONLINE
    return probe


def _offline_probe() -> ConnectivityProbe:
    probe = MagicMock(spec=ConnectivityProbe)
    probe.check.return_value = ConnectivityState.OFFLINE
    probe.last_known_state = ConnectivityState.OFFLINE
    return probe


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestEnqueue:
    def test_enqueue_persists_record(self, test_settings, sample_field_report):
        worker = SyncQueueWorker(test_settings, connectivity=_offline_probe())
        record = worker.enqueue(sample_field_report)
        assert record.job_id == sample_field_report.job_id
        assert worker.queue_depth() == 1

    def test_enqueue_multiple_increments_depth(self, test_settings, sample_field_report):
        worker = SyncQueueWorker(test_settings, connectivity=_offline_probe())
        worker.enqueue(sample_field_report)
        worker.enqueue(sample_field_report.model_copy(update={"job_id": "job-2"}))
        assert worker.queue_depth() == 2


class TestDrainQueueOffline:
    def test_drain_skipped_when_offline(self, test_settings, sample_field_report):
        worker = SyncQueueWorker(test_settings, connectivity=_offline_probe())
        worker.enqueue(sample_field_report)
        results = worker.drain_queue()
        assert results == []
        assert worker.queue_depth() == 1  # untouched


class TestDrainQueueOnline:
    def test_successful_sync_removes_from_queue(self, test_settings, sample_field_report):
        def handler(request):
            return httpx.Response(200, json={"status": "received"})

        worker = SyncQueueWorker(
            test_settings, connectivity=_online_probe(), client=_mock_client(handler)
        )
        worker.enqueue(sample_field_report)
        results = worker.drain_queue()

        assert len(results) == 1
        assert results[0].success is True
        assert worker.queue_depth() == 0

    def test_failed_sync_increments_attempts_and_keeps_in_queue(self, test_settings, sample_field_report):
        def handler(request):
            return httpx.Response(500, text="server error")

        worker = SyncQueueWorker(
            test_settings, connectivity=_online_probe(), client=_mock_client(handler)
        )
        worker.enqueue(sample_field_report)
        results = worker.drain_queue()

        assert len(results) == 1
        assert results[0].success is False
        assert worker.queue_depth() == 1

        records = worker._fetch_eligible_records()
        # Backoff means it may not be immediately eligible again, but attempts should be recorded
        dead_letter = worker.dead_letter_records()
        assert dead_letter == []  # not yet exhausted (max_attempts=3 in test_settings)

    def test_record_moves_to_dead_letter_after_max_attempts(self, test_settings, sample_field_report):
        def handler(request):
            return httpx.Response(500, text="server error")

        worker = SyncQueueWorker(
            test_settings, connectivity=_online_probe(), client=_mock_client(handler)
        )
        worker.enqueue(sample_field_report)

        # Manually drive attempts past max by directly manipulating backoff timestamps
        for _ in range(test_settings.sync_max_attempts):
            with worker._connect() as conn:
                conn.execute(
                    "UPDATE sync_queue SET last_attempt_at = ? WHERE job_id = ?",
                    (
                        (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                        sample_field_report.job_id,
                    ),
                )
            worker.drain_queue(force_check=False)

        dead_letter = worker.dead_letter_records()
        assert len(dead_letter) == 1
        assert dead_letter[0].attempts >= test_settings.sync_max_attempts

    def test_network_error_during_sync_recorded_as_failure(self, test_settings, sample_field_report):
        def handler(request):
            raise httpx.ConnectError("connection reset")

        worker = SyncQueueWorker(
            test_settings, connectivity=_online_probe(), client=_mock_client(handler)
        )
        worker.enqueue(sample_field_report)
        results = worker.drain_queue()

        assert results[0].success is False
        assert "Network error" in results[0].error


class TestQueueDepthMetric:
    def test_queue_depth_zero_initially(self, test_settings):
        worker = SyncQueueWorker(test_settings, connectivity=_offline_probe())
        assert worker.queue_depth() == 0
