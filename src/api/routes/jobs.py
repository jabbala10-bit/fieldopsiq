"""
Job routes: submit audio, run the pipeline, fetch results.

File uploads are saved to the audio_inbox directory before processing
so the original recording is preserved even if the pipeline fails
partway (field recordings can be irreplaceable — the technician may
have already left the site).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from src.api.dependencies import get_pipeline, get_storage_service, require_api_token
from src.config.settings import Settings, get_settings
from src.domain.schemas import AudioJob, FieldReport, JobStatus, PipelineResult, Transcript
from src.observability.logging import get_logger
from src.services.pipeline import FieldOpsPipeline
from src.services.storage.sqlite_service import SQLiteStorageService

logger = get_logger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_api_token)])


@router.post("", response_model=PipelineResult, status_code=status.HTTP_201_CREATED)
async def submit_job(
    technician_id: str = Form(...),
    site_id: str = Form(...),
    language_hint: Optional[str] = Form(default=None),
    audio_file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    pipeline: FieldOpsPipeline = Depends(get_pipeline),
) -> PipelineResult:
    """
    Accepts a field recording and runs it through the full pipeline:
    transcribe -> structure -> persist -> enqueue for sync.

    Returns the PipelineResult synchronously. For large files or
    slower hardware, consider polling via GET /jobs/{job_id} instead
    of holding the connection open — see docs/api-usage.md.
    """
    inbox_dir = Path(settings.audio_inbox_dir)
    inbox_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(audio_file.filename or "audio.wav").suffix
    job = AudioJob(
        technician_id=technician_id,
        site_id=site_id,
        language_hint=language_hint,
        audio_path=str(inbox_dir / f"upload{suffix}"),  # placeholder, set below
    )
    saved_path = inbox_dir / f"{job.job_id}{suffix}"
    job.audio_path = str(saved_path)

    contents = await audio_file.read()
    saved_path.write_bytes(contents)
    logger.info("audio_received", job_id=job.job_id, size_bytes=len(contents))

    result = pipeline.run(job)
    if result.sync_status == JobStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"job_id": job.job_id, "warnings": result.warnings},
        )
    return result


@router.get("/{job_id}", response_model=AudioJob)
def get_job(job_id: str, storage: SQLiteStorageService = Depends(get_storage_service)) -> AudioJob:
    job = storage.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


@router.get("/{job_id}/transcript", response_model=Transcript)
def get_transcript(
    job_id: str, storage: SQLiteStorageService = Depends(get_storage_service)
) -> Transcript:
    transcript = storage.get_transcript(job_id)
    if transcript is None:
        raise HTTPException(status_code=404, detail=f"Transcript for job {job_id} not found")
    return transcript


@router.get("/{job_id}/report", response_model=FieldReport)
def get_report(job_id: str, storage: SQLiteStorageService = Depends(get_storage_service)) -> FieldReport:
    report = storage.get_report(job_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Report for job {job_id} not found")
    return report


@router.get("", response_model=list[AudioJob])
def list_jobs(
    status_filter: JobStatus = JobStatus.PENDING_SYNC,
    limit: int = 50,
    storage: SQLiteStorageService = Depends(get_storage_service),
) -> list[AudioJob]:
    return storage.list_jobs_by_status(status_filter, limit=limit)
