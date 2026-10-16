"""Auto Mesh Tuner use-cases: create/list/get/cancel/delete tuning runs and serve their files (DESIGN.md 6)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Final

from config.settings import Settings
from core.exceptions import AppError, ConflictError, NotFoundError, QueueFullError, StorageError
from core.feature_errors import FeatureDisabledError
from models.enums import JobStatus
from models.tuning import (
    LOG_FILE,
    MODEL_FILE,
    PREVIEWS_DIR,
    REPORT_FILE,
    TUNING_PREFIX,
    TuningProgress,
    TuningRun,
    default_steps,
)
from schemas.mesh_tuner import TuningOptionsIn, TuningRunOut
from services.blender_runtime_service import BlenderRuntimeService, BlenderUnavailableAppError
from services.job_service import JobService
from services.reference_service import StoredFile
from services.result_service import GLB_MEDIA_TYPE, ResultService
from storage.json_io import read_json
from storage.tuning_store import TuningStore
from utils.ids import is_valid_id, new_id
from utils.jsonsafe import to_jsonable
from utils.timeutil import utcnow
from workers.queue import JobQueue
from workers.tuning_worker import (
    JOB_DELETED_REASON,
    PREVIEW_NAME_RE,
    USER_CANCEL_REASON,
    TuningEventPublisher,
    TuningWorker,
)

logger = logging.getLogger(__name__)

LOG_TAIL_BYTES: Final = 256 * 1024
JOB_DELETE_WAIT_S: Final = 30.0


def tuned_export_filename(job_id: str, tuning_id: str) -> str:
    return f"img3d_{job_id}_tuned_{tuning_id}.glb"


class MeshTunerService:
    def __init__(
        self,
        *,
        settings: Settings,
        runtime: BlenderRuntimeService,
        job_service: JobService,
        result_service: ResultService,
        tuning_store: TuningStore,
        queue: JobQueue,
        worker: TuningWorker,
        events: TuningEventPublisher,
    ) -> None:
        self._settings = settings
        self._runtime = runtime
        self._jobs = job_service
        self._results = result_service
        self._store = tuning_store
        self._queue = queue
        self._worker = worker
        self._events = events

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def to_out(run: TuningRun) -> TuningRunOut:
        return TuningRunOut.from_domain(run)

    def get_run(self, tuning_id: str) -> TuningRun:
        """Current state of a run (live in-memory state while it runs). 404 for unknown/invalid ids."""
        if not is_valid_id(tuning_id, TUNING_PREFIX):
            raise NotFoundError("Tuning run not found", details={"tuning_id": tuning_id})
        live = self._worker.live_snapshot(tuning_id)
        if live is not None:
            return live
        run = self._store.get(tuning_id)
        if run is None:
            raise NotFoundError("Tuning run not found", details={"tuning_id": tuning_id})
        return run

    def list_runs(self, job_id: str) -> list[TuningRun]:
        self._jobs.get_job(job_id)  # 404 for unknown jobs
        return [self._worker.live_snapshot(r.id) or r for r in self._store.list_for_job(job_id)]

    # ------------------------------------------------------------------ create
    async def create_run(self, job_id: str, options: TuningOptionsIn | None) -> TuningRun:
        """Queue a tuning run for a completed job (409 not completed, 503 no Blender, 429 queue full)."""
        self._results.model_file(job_id)  # 404 unknown job / 409 not completed / 404 model missing
        if not self._settings.mesh_tuning_enabled:  # paused until the user approves the part-aware milestone
            raise FeatureDisabledError(
                "Auto Mesh Tuner is paused until approved", feature="mesh_tuning", flag="IMG3D_MESH_TUNING_ENABLED"
            )
        if self._worker.stopping or self._queue.closed:
            raise BlenderUnavailableAppError("The server is shutting down")
        await self._runtime.require_runtime()  # 503 blender_unavailable with the runtime's user prompt
        if not self._queue.has_capacity():
            raise self._queue_full()
        effective = (options or TuningOptionsIn()).effective()
        now = utcnow()
        run = TuningRun(
            id=new_id(TUNING_PREFIX),
            job_id=job_id,
            status=JobStatus.QUEUED,
            created_at=now,
            options=effective,
            progress=TuningProgress(message="Waiting in queue", updated_at=now),
            steps=default_steps(),
        )
        if not await asyncio.to_thread(self._store.save, run):
            raise NotFoundError("Job not found", details={"job_id": job_id})
        try:
            self._queue.put(run.id)
        except AppError as exc:  # filled up / closed while the run was being persisted
            await asyncio.to_thread(self._store.delete, run.id)
            if isinstance(exc, QueueFullError):
                raise self._queue_full() from exc
            raise BlenderUnavailableAppError("The server is shutting down") from exc
        logger.info("Tuning %s queued for job %s (preset=%s)", run.id, job_id, effective["preset"])
        self._events.publish(run)
        return run

    def _queue_full(self) -> QueueFullError:
        return QueueFullError(
            f"The Auto Mesh Tuner queue is full ({self._queue.max_size} pending runs)",
            details={"max_size": self._queue.max_size},
        )

    # ------------------------------------------------------------------ cancel / delete
    async def cancel_run(self, tuning_id: str, *, reason: str | None = None) -> TuningRun:
        run = self.get_run(tuning_id)
        if run.is_terminal:
            raise ConflictError(f"Tuning run is already {run.status}", details={"status": str(run.status)})
        if run.status is JobStatus.QUEUED and self._queue.remove(tuning_id):
            now = utcnow()
            run.status = JobStatus.CANCELLED
            run.finished_at = now
            run.cancel_requested = True
            run.progress.message = "Cancelled"
            run.progress.updated_at = now
            await asyncio.to_thread(self._store.save, run)
            logger.info("Queued tuning %s cancelled", tuning_id)
            self._events.publish(run)
            return run
        running = self._worker.cancel_running(tuning_id, reason or USER_CANCEL_REASON)
        if running is not None:
            return running
        current = self.get_run(tuning_id)  # changed state concurrently
        if current.is_terminal:
            raise ConflictError(f"Tuning run is already {current.status}", details={"status": str(current.status)})
        return current

    async def delete_run(self, tuning_id: str) -> None:
        run = self.get_run(tuning_id)
        if run.is_active:
            raise ConflictError(
                "Cannot delete a queued or running tuning run; cancel it first", details={"status": str(run.status)}
            )
        await asyncio.to_thread(self._store.delete, tuning_id)
        logger.info("Tuning %s deleted", tuning_id)

    async def on_job_deleting(self, job_id: str) -> None:
        """Job-delete hook: cancel the job's active runs (killing Blender) before its folder is removed."""
        for run in self._store.list_active(job_id):
            try:
                await self.cancel_run(run.id, reason=JOB_DELETED_REASON)
            except AppError:
                pass  # finished concurrently
            if not await self._worker.wait_finished(run.id, JOB_DELETE_WAIT_S):
                logger.warning("Tuning %s did not stop within %.0f s before deleting job %s", run.id,
                               JOB_DELETE_WAIT_S, job_id)
        forgotten = self._store.forget_job(job_id)
        if forgotten:
            logger.info("Job %s deleted with %d tuning run(s)", job_id, len(forgotten))

    # ------------------------------------------------------------------ files
    def _run_dir(self, run: TuningRun) -> Path:
        return self._store.run_dir(run.job_id, run.id)

    def _completed(self, tuning_id: str) -> TuningRun:
        run = self.get_run(tuning_id)
        if run.status is not JobStatus.COMPLETED or run.result is None:
            raise ConflictError(f"Tuning run is not completed (status: {run.status})",
                                details={"status": str(run.status)})
        return run

    def model_file(self, tuning_id: str, *, attachment: bool = False) -> StoredFile:
        run = self._completed(tuning_id)
        path = self._run_dir(run) / MODEL_FILE
        if not path.is_file():
            raise NotFoundError("Tuned model file not found", details={"tuning_id": tuning_id})
        filename = tuned_export_filename(run.job_id, run.id) if attachment else MODEL_FILE
        return StoredFile(path=path, media_type=GLB_MEDIA_TYPE, filename=filename)

    async def report(self, tuning_id: str) -> Any:
        run = self.get_run(tuning_id)
        path = self._run_dir(run) / REPORT_FILE
        if not path.is_file():
            raise NotFoundError("Tuning report not available", details={"tuning_id": tuning_id})
        try:
            return to_jsonable(await asyncio.to_thread(read_json, path))  # NaN/Infinity -> null
        except FileNotFoundError as exc:
            raise NotFoundError("Tuning report not available", details={"tuning_id": tuning_id}) from exc
        except StorageError as exc:
            raise StorageError("The tuning report is corrupt", details={"tuning_id": tuning_id}) from exc

    def preview_file(self, tuning_id: str, name: str) -> StoredFile:
        if not PREVIEW_NAME_RE.match(name or ""):
            raise NotFoundError("Preview not found", details={"name": name})
        run = self.get_run(tuning_id)
        path = self._run_dir(run) / PREVIEWS_DIR / name
        if not path.is_file():
            raise NotFoundError("Preview not found", details={"tuning_id": tuning_id, "name": name})
        return StoredFile(path=path, media_type="image/png")

    async def log_tail(self, tuning_id: str, *, max_bytes: int = LOG_TAIL_BYTES) -> str:
        run = self.get_run(tuning_id)
        path = self._worker.live_log_path(tuning_id) or (self._run_dir(run) / LOG_FILE)

        def _read() -> str | None:
            try:
                with open(path, "rb") as fh:
                    fh.seek(0, 2)
                    size = fh.tell()
                    fh.seek(max(0, size - max_bytes))
                    data = fh.read(max_bytes)
            except FileNotFoundError:
                return None
            text = data.decode("utf-8", "replace")
            if size > max_bytes:  # drop the partial first line
                text = text.split("\n", 1)[-1]
            return text

        text = await asyncio.to_thread(_read)
        if text is None:
            raise NotFoundError("Blender log not available", details={"tuning_id": tuning_id})
        return text
