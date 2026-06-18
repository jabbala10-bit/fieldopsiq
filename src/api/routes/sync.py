"""Sync queue routes: trigger drain, inspect queue depth, dead-letter records."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from src.api.dependencies import get_connectivity_probe, get_sync_worker, require_api_token
from src.domain.schemas import ConnectivityState, SyncRecord, SyncResult
from src.services.sync.connectivity import ConnectivityProbe
from src.services.sync.sync_queue import SyncQueueWorker

router = APIRouter(prefix="/sync", tags=["sync"], dependencies=[Depends(require_api_token)])


@router.post("/drain", response_model=list[SyncResult])
def drain_sync_queue(sync_worker: SyncQueueWorker = Depends(get_sync_worker)) -> list[SyncResult]:
    """Manually trigger a sync attempt — useful right after connectivity returns."""
    return sync_worker.drain_queue(force_check=True)


@router.get("/status")
def sync_status(
    sync_worker: SyncQueueWorker = Depends(get_sync_worker),
    connectivity: ConnectivityProbe = Depends(get_connectivity_probe),
) -> dict:
    return {
        "queue_depth": sync_worker.queue_depth(),
        "connectivity": connectivity.last_known_state.value,
        "dead_letter_count": len(sync_worker.dead_letter_records()),
    }


@router.get("/dead-letter", response_model=list[SyncRecord])
def dead_letter_queue(sync_worker: SyncQueueWorker = Depends(get_sync_worker)) -> list[SyncRecord]:
    """Records that exhausted all retry attempts — require manual review/resync."""
    return sync_worker.dead_letter_records()


@router.get("/connectivity")
def check_connectivity(connectivity: ConnectivityProbe = Depends(get_connectivity_probe)) -> dict:
    state: ConnectivityState = connectivity.check()
    return {"state": state.value}
