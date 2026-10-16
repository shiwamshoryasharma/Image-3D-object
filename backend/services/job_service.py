"""Job use-cases: create (validate, snapshot + normalize inputs, enqueue), read, list, cancel, delete."""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable

from config.settings import Settings
from core.exceptions import ConflictError, NotFoundError, StorageError, ValidationAppError
from inference.base import InferenceEngine
from models.enums import JobStage, JobStatus
from models.job import Job, JobProgress, JobReference
from models.reference import ReferenceSet
from pipelines.reference_strategy import get_strategy
from processing.image_ops import normalize_to_png
from schemas.jobs import MAX_SEED, CreateJobRequest, JobOut
from services.generation_settings import validate_against_capabilities
from services.job_events import JobEventPublisher
from services.reference_service import ReferenceService
from storage.job_store import JobStore
from utils.ids import new_job_id
from utils.timeutil import utcnow
from workers.generation_worker import GenerationWorker
from workers.queue import JobQueue

logger = logging.getLogger(__name__)

DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 200

JobDeleteHook = Callable[[str], Awaitable[None]]
"""Awaited with the job id before a job's folder is deleted (e.g. cancel its active tuning runs)."""


class JobService:
    def __init__(
        self,
        *,
        settings: Settings,
        engine: InferenceEngine,
        job_store: JobStore,
        references: ReferenceService,
        queue: JobQueue,
        worker: GenerationWorker,
        events: JobEventPublisher,
        on_system_changed: Callable[[], None],
    ) -> None:
        self._settings = settings
        self._engine = engine
        self._jobs = job_store
        self._references = references
        self._queue = queue
        self._worker = worker
        self._events = events
        self._on_system_changed = on_system_changed
        self._delete_hooks: list[JobDeleteHook] = []

    def add_delete_hook(self, hook: JobDeleteHook) -> None:
        """Register ``hook(job_id)``, awaited before a job is deleted (failures are logged, never block it)."""
        self._delete_hooks.append(hook)

    # ------------------------------------------------------------------ helpers
    def to_out(self, job: Job) -> JobOut:
        return self._events.to_out(job)

    def get_job(self, job_id: str) -> Job:
        """Current state of a job (live in-memory state for the running job)."""
        live = self._worker.live_snapshot(job_id)
        if live is not None:
            return live
        job = self._jobs.get(job_id)
        if job is None:
            raise NotFoundError("Job not found", details={"job_id": job_id})
        return job

    def list_jobs(self, *, limit: int = DEFAULT_LIST_LIMIT, status: JobStatus | None = None) -> list[Job]:
        jobs = self._jobs.list(status=status, limit=limit)
        return [self._worker.live_snapshot(j.id) or j for j in jobs]

    # ------------------------------------------------------------------ create
    async def create_job(self, request: CreateJobRequest) -> Job:
        settings = request.settings
        reference_mode = settings.reference_mode or self._settings.reference_mode
        capabilities = self._engine.capabilities()

        async with self._references.locked_set(request.reference_set_id) as ref_set:
            if not ref_set.images:
                raise ValidationAppError(
                    "The reference set has no images", details={"reference_set_id": ref_set.id}
                )
            primary_id = request.primary_image_id or ref_set.primary_image_id
            if request.primary_image_id is not None and ref_set.get_image(request.primary_image_id) is None:
                raise ValidationAppError(
                    "primary_image_id does not belong to the reference set",
                    details={"primary_image_id": request.primary_image_id, "reference_set_id": ref_set.id},
                )
            if primary_id is None:  # pragma: no cover - ensure_primary keeps a primary in non-empty sets
                raise ValidationAppError("The reference set has no primary image")

            validate_against_capabilities(settings, reference_mode, capabilities)
            get_strategy(reference_mode).validate(capabilities)
            self._queue.ensure_capacity()

            seed = secrets.randbelow(MAX_SEED + 1) if settings.randomize_seed else settings.seed
            normalized = settings.model_copy(update={"reference_mode": reference_mode}).model_dump(mode="json")
            now = utcnow()
            job = Job(
                id=new_job_id(),
                status=JobStatus.QUEUED,
                engine=self._engine.name,
                created_at=now,
                reference_set_id=ref_set.id,
                primary_image_id=primary_id,
                references=[
                    JobReference(image_id=img.id, slot=img.slot, is_primary=img.id == primary_id)
                    for img in ref_set.ordered_images()
                ],
                settings=normalized,
                seed=seed,
                progress=JobProgress.for_stage(JobStage.QUEUED, message="Waiting in queue"),
            )
            job.progress.updated_at = now
            await self._prepare_inputs(job, ref_set)

        try:
            self._jobs.save(job)
            self._queue.put(job.id)
        except Exception:
            await asyncio.to_thread(self._jobs.delete, job.id)
            raise
        logger.info(
            "Job %s queued (set=%s, primary=%s, resolution=%s, seed=%d, position=%s)",
            job.id,
            job.reference_set_id,
            job.primary_image_id,
            normalized["resolution"],
            seed,
            self._queue.position(job.id),
        )
        self._events.publish(job)
        self._notify_system()
        return job

    async def _prepare_inputs(self, job: Job, ref_set: ReferenceSet) -> None:
        """Copy every reference into ``jobs/<id>/inputs/<image_id>.png`` as a normalized PNG.

        Images are processed one at a time (off the event loop): a set may hold 14 images of up to
        ``max_image_pixels`` each, and decoding them all at once could need several GB of RAM.
        """
        inputs_dir = self._jobs.inputs_dir(job.id)

        def _prepare_all() -> None:
            inputs_dir.mkdir(parents=True, exist_ok=True)
            for ref in job.references:
                image = ref_set.get_image(ref.image_id)
                if image is None:  # pragma: no cover - references were built from this very set
                    raise StorageError(f"Reference image {ref.image_id} disappeared from the set")
                normalize_to_png(self._references.original_path(image), inputs_dir / ref.input_file_name)

        try:
            await asyncio.to_thread(_prepare_all)
        except Exception as exc:
            await asyncio.to_thread(self._jobs.delete, job.id)
            logger.exception("Could not prepare inputs for job %s", job.id)
            raise StorageError("Could not prepare the job input images") from exc

    # ------------------------------------------------------------------ cancel / delete
    async def cancel_job(self, job_id: str) -> Job:
        job = self.get_job(job_id)
        if job.is_terminal:
            raise ConflictError(f"Job is already {job.status}", details={"status": str(job.status)})

        if job.status is JobStatus.QUEUED and self._queue.remove(job_id):
            now = utcnow()
            job.status = JobStatus.CANCELLED
            job.finished_at = now
            job.cancel_requested = True
            job.progress.message = "Cancelled"
            job.progress.updated_at = now
            self._jobs.save(job)
            logger.info("Queued job %s cancelled", job_id)
            self._events.publish(job)
            self._publish_queue_positions()
            self._notify_system()
            return job

        running = self._worker.cancel_running(job_id)
        if running is not None:
            return running
        # The job changed state concurrently; report its current state.
        current = self.get_job(job_id)
        if current.is_terminal:
            raise ConflictError(f"Job is already {current.status}", details={"status": str(current.status)})
        return current

    async def delete_job(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if job.is_active:
            raise ConflictError(
                "Cannot delete a queued or running job; cancel it first", details={"status": str(job.status)}
            )
        for hook in self._delete_hooks:
            try:
                await hook(job_id)
            except Exception:  # noqa: BLE001 - dependent cleanup must not prevent deleting the job
                logger.exception("Job delete hook failed for %s", job_id)
        await asyncio.to_thread(self._jobs.delete, job_id)
        logger.info("Job %s deleted", job_id)

    # ------------------------------------------------------------------ notifications
    def _publish_queue_positions(self) -> None:
        for queued_id in self._queue.snapshot():
            queued = self._jobs.get(queued_id)
            if queued is not None:
                self._events.publish(queued)

    def _notify_system(self) -> None:
        try:
            self._on_system_changed()
        except Exception:  # noqa: BLE001
            logger.exception("System change notification failed")
