"""Job persistence: ``runtime/jobs/<job_id>/job.json`` + ``inputs/``, ``preview.png``, ``model.glb``.

Thread-safe (the GPU worker thread persists progress while the event loop serves reads). Hands out deep copies.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from models.enums import JobStatus
from models.job import INTERRUPTED_ERROR_CODE, Job, JobError
from storage.json_io import read_json, write_json_atomic
from storage.paths import JOB_METADATA_FILE, RuntimePaths
from utils.files import safe_rmtree
from utils.ids import JOB_PREFIX, is_valid_id
from utils.timeutil import utcnow

logger = logging.getLogger(__name__)


class JobStore:
    def __init__(self, paths: RuntimePaths) -> None:
        self._paths = paths
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()

    @property
    def paths(self) -> RuntimePaths:
        return self._paths

    # ------------------------------------------------------------------ loading
    def load(self) -> int:
        loaded: dict[str, Job] = {}
        if self._paths.jobs.exists():
            for directory in self._paths.jobs.iterdir():
                if not directory.is_dir() or not is_valid_id(directory.name, JOB_PREFIX):
                    continue
                meta = directory / JOB_METADATA_FILE
                if not meta.exists():
                    continue
                try:
                    job = Job.model_validate(read_json(meta))
                except Exception as exc:  # noqa: BLE001 - one broken job must not break startup
                    logger.warning("Skipping unreadable job %s: %s", directory.name, exc)
                    continue
                if job.id != directory.name:
                    logger.warning("Skipping job with mismatched id in %s", directory)
                    continue
                loaded[job.id] = job
        with self._lock:
            self._jobs = loaded
        logger.info("Loaded %d job(s)", len(loaded))
        return len(loaded)

    def recover_interrupted(self) -> list[Job]:
        """Mark jobs left ``queued``/``running`` by a previous process as failed (``interrupted``)."""
        recovered: list[Job] = []
        with self._lock:
            candidates = [j for j in self._jobs.values() if j.status in (JobStatus.QUEUED, JobStatus.RUNNING)]
        for job in candidates:
            job = job.model_copy(deep=True)
            previous = job.status
            now = utcnow()
            job.status = JobStatus.FAILED
            job.finished_at = now
            job.error = JobError(
                code=INTERRUPTED_ERROR_CODE,
                message="The server stopped before this job finished",
                details={"previous_status": str(previous), "stage": str(job.progress.stage)},
            )
            job.progress.message = "Interrupted"
            job.progress.eta_seconds = None
            job.progress.updated_at = now
            self.save(job)
            recovered.append(job)
            logger.warning("Job %s was %s at shutdown; marked failed (interrupted)", job.id, previous)
        return recovered

    # ------------------------------------------------------------------ queries
    def get(self, job_id: str) -> Job | None:
        if not is_valid_id(job_id, JOB_PREFIX):
            return None
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    def list(self, *, status: JobStatus | None = None, limit: int | None = None) -> list[Job]:
        """Jobs newest first, optionally filtered by status."""
        with self._lock:
            items = [j.model_copy(deep=True) for j in self._jobs.values() if status is None or j.status == status]
        items.sort(key=lambda j: (j.created_at, j.id), reverse=True)
        return items[:limit] if limit is not None else items

    def count(self) -> int:
        with self._lock:
            return len(self._jobs)

    def ids(self) -> set[str]:
        """Ids of every indexed job (cheap: no copies)."""
        with self._lock:
            return set(self._jobs)

    # ------------------------------------------------------------------ mutations
    def save(self, job: Job) -> None:
        with self._lock:
            self._paths.job_dir(job.id).mkdir(parents=True, exist_ok=True)
            write_json_atomic(self._paths.job_metadata(job.id), job.model_dump(mode="json"))
            self._jobs[job.id] = job.model_copy(deep=True)

    def delete(self, job_id: str) -> bool:
        if not is_valid_id(job_id, JOB_PREFIX):
            return False
        with self._lock:
            existed = self._jobs.pop(job_id, None) is not None
        safe_rmtree(self._paths.job_dir(job_id))
        return existed

    # ------------------------------------------------------------------ paths
    def job_dir(self, job_id: str) -> Path:
        return self._paths.job_dir(job_id)

    def inputs_dir(self, job_id: str) -> Path:
        return self._paths.job_inputs(job_id)

    def preview_path(self, job_id: str) -> Path:
        return self._paths.job_preview(job_id)

    def model_path(self, job_id: str) -> Path:
        return self._paths.job_model(job_id)
