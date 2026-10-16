"""The single GPU worker.

One asyncio task consumes :class:`~workers.queue.JobQueue`. All blocking GPU work - ``engine.load()``,
``engine.generate()`` (through the pipeline) and ``engine.unload()`` - runs on ONE dedicated thread
(``ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpu-worker")``), so loading and generation never
overlap and the event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from core.events import EventBus
from core.exceptions import AppError, ConflictError, EngineUnavailableError, JobCancelledError
from core.logging import bind_job_id
from inference.base import CancellationToken, EngineStatus, InferenceEngine
from models.enums import EngineState, JobStage, JobStatus
from models.job import INTERRUPTED_ERROR_CODE, Job, JobError, JobProgress, JobResultData
from pipelines.base import GenerationPipeline
from pipelines.progress import DEFAULT_PERSIST_INTERVAL_S, DEFAULT_PUBLISH_INTERVAL_S, JobProgressReporter
from services.job_events import JobEventPublisher
from storage.job_store import JobStore
from utils.jsonsafe import to_jsonable
from utils.timeutil import utcnow
from workers.queue import JobQueue

logger = logging.getLogger(__name__)

T = TypeVar("T")

GPU_THREAD_NAME = "gpu-worker"
SHUTDOWN_REASON = "Server is shutting down"
_MAX_ERROR_MESSAGE = 2000


@dataclass(slots=True)
class _ActiveJob:
    job_id: str
    reporter: JobProgressReporter
    token: CancellationToken


def classify_exception(exc: BaseException) -> JobError:
    """Map an unexpected exception to a job error. OOM-style failures keep their message for the user."""
    name = type(exc).__name__
    text = str(exc).strip() or name
    if "outofmemory" in name.lower() or "out of memory" in text.lower():
        return JobError(
            code="out_of_memory",
            message=f"GPU ran out of memory: {text}"[:_MAX_ERROR_MESSAGE],
            details={"exception": name},
        )
    return JobError(code="engine_error", message=f"{name}: {text}"[:_MAX_ERROR_MESSAGE], details={"exception": name})


class GenerationWorker:
    def __init__(
        self,
        *,
        engine: InferenceEngine,
        pipeline: GenerationPipeline,
        job_store: JobStore,
        queue: JobQueue,
        events: JobEventPublisher,
        bus: EventBus,
        on_system_changed: Callable[[], None],
        publish_interval: float = DEFAULT_PUBLISH_INTERVAL_S,
        persist_interval: float = DEFAULT_PERSIST_INTERVAL_S,
        engine_poll_interval: float = 0.5,
    ) -> None:
        self._engine = engine
        self._pipeline = pipeline
        self._jobs = job_store
        self._queue = queue
        self._events = events
        self._bus = bus
        self._on_system_changed = on_system_changed
        self._publish_interval = publish_interval
        self._persist_interval = persist_interval
        self._engine_poll_interval = engine_poll_interval

        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix=GPU_THREAD_NAME)
        self._inflight: set[concurrent.futures.Future[Any]] = set()
        self._task: asyncio.Task[None] | None = None
        self._current: _ActiveJob | None = None
        self._load_future: asyncio.Future[None] | None = None
        self._unload_future: asyncio.Future[None] | None = None
        self._engine_changed = asyncio.Event()
        self._stopping = False

    # ------------------------------------------------------------------ introspection
    @property
    def running_job_id(self) -> str | None:
        return self._current.job_id if self._current else None

    @property
    def is_busy(self) -> bool:
        """``True`` while the GPU thread is executing something (load, generation or unload)."""
        return any(not f.done() for f in self._inflight)

    @property
    def is_loading(self) -> bool:
        return self._load_future is not None and not self._load_future.done()

    @property
    def is_unloading(self) -> bool:
        """``True`` while an unload is pending/running on the GPU thread (the engine may still report READY)."""
        return self._unload_future is not None and not self._unload_future.done()

    def _engine_usable(self) -> bool:
        return self._engine.status().is_ready and not self.is_unloading

    def live_snapshot(self, job_id: str) -> Job | None:
        """Fresh in-memory state of the running job (job.json is only persisted every few seconds)."""
        active = self._current
        if active is not None and active.job_id == job_id:
            return active.reporter.snapshot()
        return None

    # ------------------------------------------------------------------ executor
    def _submit(self, fn: Callable[..., Any], *args: Any) -> concurrent.futures.Future[Any]:
        future = self._executor.submit(fn, *args)
        self._inflight.add(future)
        future.add_done_callback(self._inflight.discard)
        return future

    @property
    def accepting_gpu_tasks(self) -> bool:
        """False once stop() has begun (no new auxiliary GPU work may be queued)."""
        return not self._stopping

    def submit_gpu_task(self, fn: Callable[..., T], /, *args: Any) -> asyncio.Future[T]:
        """Run a blocking callable on the single gpu-worker thread, FIFO with model load/generation/unload.
        The callable must honour its CancellationToken and leave no weights on the GPU when it returns.
        Raises EngineUnavailableError("The server is shutting down") while stopping."""
        if self._stopping:
            raise EngineUnavailableError("The server is shutting down")
        return asyncio.wrap_future(self._submit(fn, *args))

    # ------------------------------------------------------------------ engine lifecycle
    def request_engine_load(self) -> bool:
        """Start loading the model on the GPU thread unless it is loaded/loading. Returns ``True`` if started."""
        if self._stopping or self.is_loading:
            return False
        state = self._engine.status().state
        # During an unload the engine may still report READY; the load is queued behind it on the GPU thread.
        if state in (EngineState.READY, EngineState.LOADING) and not self.is_unloading:
            return False
        logger.info("Loading inference engine '%s' (state was %s)", self._engine.name, state)
        self._load_future = asyncio.wrap_future(self._submit(self._load_engine_blocking))
        self._notify_system()
        return True

    def _load_engine_blocking(self) -> None:
        try:
            self._engine.load(on_status=self._on_engine_status)
        except Exception:  # noqa: BLE001 - engines should not raise, but never kill the GPU thread
            logger.exception("Engine load raised an exception")
        finally:
            status = self._engine.status()
            if status.state is EngineState.READY:
                logger.info("Inference engine ready")
            else:
                logger.warning("Inference engine not ready after load: state=%s error=%s", status.state, status.error)
            self._on_engine_status(status)

    def _on_engine_status(self, status: EngineStatus | None = None) -> None:
        """Engine status callback (any thread)."""
        self._bus.call_soon_threadsafe(self._engine_status_changed)

    def _engine_status_changed(self) -> None:
        self._engine_changed.set()
        self._notify_system()

    async def unload_engine(self) -> None:
        """Unload the model (409 while a job is running or the model is loading)."""
        if self._current is not None:
            raise ConflictError("Cannot unload the model while a job is running")
        if self.is_loading:
            raise ConflictError("Cannot unload the model while it is loading")
        if not self.is_unloading:
            future = asyncio.wrap_future(self._submit(self._engine.unload))
            future.add_done_callback(self._on_unload_done)
            self._unload_future = future
            self._notify_system()
        assert self._unload_future is not None
        # Shielded: a client disconnecting mid-request must not leave the GPU thread state ambiguous.
        await asyncio.shield(self._unload_future)
        logger.info("Inference engine unloaded")

    def _on_unload_done(self, future: asyncio.Future[None]) -> None:
        if not future.cancelled() and future.exception() is not None:
            logger.error("Engine unload failed", exc_info=future.exception())
        self._engine_status_changed()  # wake jobs waiting for the model + broadcast system.updated

    # ------------------------------------------------------------------ worker lifecycle
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="generation-worker")

    async def stop(self, timeout: float) -> None:
        """Stop accepting jobs, cancel the running job and wait (at most ``timeout`` s) for the worker."""
        if self._stopping:
            return
        self._stopping = True
        for job_id in self._queue.close():
            self._fail_unstarted(job_id)
        active = self._current
        if active is not None and not active.token.is_cancelled:  # a user cancel keeps its reason ("cancelled")
            logger.info("Cancelling running job %s for shutdown", active.job_id)
            active.token.cancel(SHUTDOWN_REASON)
        task = self._task
        if task is not None and not task.done():
            done, _ = await asyncio.wait({task}, timeout=timeout)
            if not done:
                logger.warning("Generation worker did not stop within %.0f s; abandoning it", timeout)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def shutdown_engine(self, timeout: float) -> None:
        """Unload the engine on the GPU thread (skipped if that thread is still busy) and shut the executor."""
        try:
            if self.is_busy:
                logger.warning("GPU thread still busy; skipping engine unload")
                return
            try:
                await asyncio.wait_for(asyncio.wrap_future(self._submit(self._engine.unload)), timeout=timeout)
            except TimeoutError:
                logger.warning("Engine unload did not finish within %.0f s", timeout)
            except Exception:  # noqa: BLE001
                logger.exception("Engine unload failed")
        finally:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def _fail_unstarted(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None or job.status is not JobStatus.QUEUED:
            return
        now = utcnow()
        job.status = JobStatus.FAILED
        job.finished_at = now
        job.error = JobError(code=INTERRUPTED_ERROR_CODE, message="The server stopped before this job started")
        job.progress.message = "Interrupted"
        job.progress.updated_at = now
        try:
            self._jobs.save(job)
        except Exception:  # noqa: BLE001
            logger.exception("Could not persist interrupted job %s", job_id)
        self._events.publish(job)

    # ------------------------------------------------------------------ cancellation
    def cancel_running(self, job_id: str) -> Job | None:
        """Request cancellation of the running job. Returns its snapshot, or ``None`` if it is not running."""
        active = self._current
        if active is None or active.job_id != job_id:
            return None
        active.token.cancel("Cancelled by user")

        def _mark(job: Job) -> None:
            job.cancel_requested = True
            job.progress.message = "Cancelling..."

        logger.info("Cancellation requested for running job %s", job_id)
        return active.reporter.mutate(_mark, publish=True, persist=True)

    # ------------------------------------------------------------------ main loop
    async def _run(self) -> None:
        logger.info("Generation worker started")
        while True:
            job_id = await self._queue.get()
            if job_id is None:
                break
            job = self._jobs.get(job_id)
            if job is None or job.status is not JobStatus.QUEUED:
                continue
            try:
                await self._process(job)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the worker must survive any single job
                logger.exception("Unexpected worker failure while processing job %s", job_id)
                self._fail_after_crash(job_id)
            finally:
                self._current = None
                self._notify_system()
        logger.info("Generation worker stopped")

    def _fail_after_crash(self, job_id: str) -> None:
        """Best effort: never leave a job queued/running after the worker failed outside the job's own handling."""
        active = self._current
        job = active.reporter.snapshot() if active is not None and active.job_id == job_id else self._jobs.get(job_id)
        if job is None or job.is_terminal:
            return
        now = utcnow()
        job.status = JobStatus.FAILED
        job.finished_at = now
        job.error = JobError(code="internal_error", message="The generation worker failed unexpectedly")
        job.progress.message = job.error.message
        job.progress.eta_seconds = None
        job.progress.updated_at = now
        if active is not None:
            active.reporter.close()
        try:
            self._jobs.save(job)
        except Exception:  # noqa: BLE001
            logger.exception("Could not persist the failed state of job %s", job_id)
        self._events.publish(job)

    async def _process(self, job: Job) -> None:
        token = CancellationToken()
        reporter = JobProgressReporter(
            job,
            publish=lambda snapshot, droppable: self._events.publish(snapshot, droppable=droppable),
            persist=self._jobs.save,
            schedule_later=lambda delay, cb: self._bus.call_later_threadsafe(delay, cb),
            min_publish_interval=self._publish_interval,
            persist_interval=self._persist_interval,
        )
        active = _ActiveJob(job_id=job.id, reporter=reporter, token=token)
        self._current = active

        with bind_job_id(job.id):
            engine_ready = self._engine_usable()
            first_stage = JobStage.PREPROCESSING if engine_ready else JobStage.WAITING_FOR_MODEL

            def _start(j: Job) -> None:
                j.status = JobStatus.RUNNING
                j.started_at = utcnow()
                j.progress = JobProgress.for_stage(
                    first_stage, message="Starting generation" if engine_ready else "Waiting for model"
                )

            reporter.mutate(_start, publish=True, persist=True)
            logger.info("Job started (engine %s)", "ready" if engine_ready else "not ready")
            self._publish_queue_positions()
            self._notify_system()

            try:
                if not engine_ready:
                    await self._wait_for_engine(active)

                    def _ready(j: Job) -> None:
                        j.progress = JobProgress.for_stage(JobStage.PREPROCESSING, message="Starting generation")

                    reporter.mutate(_ready, publish=True)
                token.raise_if_cancelled()
                result: JobResultData = await asyncio.wrap_future(
                    self._submit(self._execute_pipeline, reporter.snapshot(), reporter, token)
                )
            except asyncio.CancelledError:  # worker task abandoned at shutdown
                self._finish(
                    active, JobStatus.FAILED, error=JobError(code=INTERRUPTED_ERROR_CODE, message=SHUTDOWN_REASON)
                )
                raise
            except JobCancelledError:
                self._finish_cancelled(active)
            except AppError as exc:
                if token.is_cancelled:
                    self._finish_cancelled(active)
                    return
                if exc.status_code >= 500:
                    logger.error("Job failed: %s (%s)", exc.message, exc.code, exc_info=exc)
                else:
                    logger.warning("Job failed: %s (%s)", exc.message, exc.code)
                self._finish(
                    active,
                    JobStatus.FAILED,
                    error=JobError(code=exc.code, message=exc.message, details=to_jsonable(exc.details)),
                )
            except Exception as exc:  # noqa: BLE001
                if token.is_cancelled:
                    self._finish_cancelled(active)
                    return
                logger.error("Job failed with an unexpected engine error", exc_info=exc)
                self._finish(active, JobStatus.FAILED, error=classify_exception(exc))
            else:
                self._finish(active, JobStatus.COMPLETED, result=result)

    def _execute_pipeline(self, job: Job, reporter: JobProgressReporter, token: CancellationToken) -> JobResultData:
        """Runs on the GPU thread."""
        with bind_job_id(job.id):
            return self._pipeline.run(job, reporter, token)

    async def _wait_for_engine(self, active: _ActiveJob) -> None:
        load_attempted = False
        last_message: str | None = None
        while True:
            active.token.raise_if_cancelled()
            status = self._engine.status()
            unloading = self.is_unloading
            if status.state is EngineState.READY and not unloading:
                return
            if status.state in (EngineState.ERROR, EngineState.UNAVAILABLE):
                raise EngineUnavailableError(
                    f"The model is not available: {status.error or status.message or status.state}",
                    details={"engine_state": str(status.state), "error": status.error},
                )
            if unloading:
                pass  # an unload is in flight on the GPU thread; load again once it has finished
            elif not self.is_loading:
                if status.state is EngineState.NOT_LOADED and not load_attempted:
                    self.request_engine_load()
                    load_attempted = True
                elif load_attempted or status.state is EngineState.LOADING:
                    # A load finished (or crashed) without making the engine ready.
                    raise EngineUnavailableError(
                        "The model failed to load", details={"engine_state": str(status.state)}
                    )
            message = "Waiting for the model to unload" if unloading else (status.message or "Waiting for model")
            if message != last_message:
                last_message = message
                active.reporter.mutate(lambda j, m=message: setattr(j.progress, "message", m), publish=True)
            self._engine_changed.clear()
            try:
                await asyncio.wait_for(self._engine_changed.wait(), timeout=self._engine_poll_interval)
            except TimeoutError:
                pass

    # ------------------------------------------------------------------ completion
    def _finish_cancelled(self, active: _ActiveJob) -> None:
        if self._stopping and active.token.reason == SHUTDOWN_REASON:
            self._finish(
                active, JobStatus.FAILED, error=JobError(code=INTERRUPTED_ERROR_CODE, message=SHUTDOWN_REASON)
            )
        else:
            logger.info("Job cancelled")
            self._finish(active, JobStatus.CANCELLED)

    def _finish(
        self,
        active: _ActiveJob,
        status: JobStatus,
        *,
        error: JobError | None = None,
        result: JobResultData | None = None,
    ) -> None:
        reporter = active.reporter
        reporter.close()

        def _apply(job: Job) -> None:
            job.status = status
            job.finished_at = utcnow()
            job.error = error
            job.result = result
            if status is JobStatus.COMPLETED:
                job.progress = JobProgress.for_stage(JobStage.DONE, message="Completed", percent=100.0)
            elif status is JobStatus.CANCELLED:
                job.progress.message = "Cancelled"
            else:
                job.progress.message = error.message if error else "Failed"

        try:
            reporter.mutate(_apply, publish=True, persist=True)
        except Exception:  # noqa: BLE001 - persistence failure must still notify clients
            logger.exception("Could not persist final state of job %s", active.job_id)
            self._events.publish(reporter.snapshot())
        if status is JobStatus.COMPLETED and result is not None:
            logger.info(
                "Job completed in %.1f s (%d vertices, %d faces)",
                result.timings.get("total", 0.0),
                result.stats.vertices,
                result.stats.faces,
            )
        elif status is JobStatus.FAILED:
            logger.info("Job finished with status failed (%s)", error.code if error else "unknown")

    # ------------------------------------------------------------------ notifications
    def _notify_system(self) -> None:
        try:
            self._on_system_changed()
        except Exception:  # noqa: BLE001
            logger.exception("System change notification failed")

    def _publish_queue_positions(self) -> None:
        for job_id in self._queue.snapshot():
            job = self._jobs.get(job_id)
            if job is not None:
                self._events.publish(job)
