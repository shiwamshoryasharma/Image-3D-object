"""Access to job outputs: result metadata, GLB model and preview image."""

from __future__ import annotations

from config.settings import Settings
from core.exceptions import ConflictError, NotFoundError
from models.enums import JobStatus
from models.job import Job
from schemas.jobs import JobResultOut
from services.job_service import JobService
from services.reference_service import StoredFile
from storage.job_store import JobStore

GLB_MEDIA_TYPE = "model/gltf-binary"


def export_filename(job_id: str) -> str:
    return f"img3d_{job_id}.glb"


class ResultService:
    def __init__(self, settings: Settings, jobs: JobService, job_store: JobStore) -> None:
        self._settings = settings
        self._jobs = jobs
        self._store = job_store

    def _completed_job(self, job_id: str) -> Job:
        job = self._jobs.get_job(job_id)
        if job.status is not JobStatus.COMPLETED or job.result is None:
            raise ConflictError(
                f"Job is not completed (status: {job.status})", details={"status": str(job.status)}
            )
        return job

    def get_result(self, job_id: str) -> JobResultOut:
        job = self._completed_job(job_id)
        assert job.result is not None
        return JobResultOut.from_domain(job.id, job.result)

    def model_file(self, job_id: str) -> StoredFile:
        job = self._completed_job(job_id)
        path = self._store.model_path(job.id)
        if not path.is_file():
            raise NotFoundError("Model file not found", details={"job_id": job_id})
        return StoredFile(path=path, media_type=GLB_MEDIA_TYPE, filename=export_filename(job.id))

    def preview_file(self, job_id: str) -> StoredFile:
        job = self._jobs.get_job(job_id)
        path = self._store.preview_path(job.id)
        if not path.is_file():
            raise NotFoundError("Preview not available yet", details={"job_id": job_id})
        return StoredFile(path=path, media_type="image/png")
