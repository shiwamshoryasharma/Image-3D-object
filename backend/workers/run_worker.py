"""The run worker: single consumer of the shared run queue (part-aware DESIGN.md sections 4.3, 4.8 and 4.11).

Image generations, segmentations and part analyses share one :class:`~workers.queue.JobQueue` of run ids (FIFO, one
run at a time). For each run the worker drives its executor (:mod:`pipelines.runs.executor`):

1. mark the run ``running`` (message "Waiting for the GPU (generation in progress)" while the GPU thread is busy);
2. ``prepare`` on a worker thread (``asyncio.to_thread``), with the scratch dir ``runtime/temp/<kind>/<id>/``;
3. each phase in order: **cpu** phases on the dedicated ``parts-cpu`` thread; **gpu** phases on the single
   ``gpu-worker`` thread through ``GenerationWorker.submit_gpu_task`` - after the **jobs-first gate** (wait while an
   image-to-3D job is pending or running, at most ``run_max_defer_s``, then warning ``gpu_defer_timeout``) and inside
   ``registry.guard()`` with ``ensure_vram(phase.models)`` before and ``offload(phase.models)`` after the callable;
4. ``finalize`` on a worker thread, then ``completed``.

Errors: ``JobCancelledError`` -> ``cancelled``; ``AppError`` -> ``failed`` with its code; anything else is classified
by :func:`workers.generation_worker.classify_exception` -> ``out_of_memory`` or ``run_error``. Part analyses have a
watchdog (``part_analysis_timeout_s`` -> ``run_timeout``) that counts **active** time only (inside ``prepare``, a
phase callable or ``finalize``; never the jobs-first gate or time queued behind TRELLIS jobs on the GPU thread).

Cancellation, timeout and shutdown never race a running callable: the token is cancelled and the worker keeps
awaiting the submitted future (which a ``concurrent.futures`` task cannot be cancelled out of), so the final status
is set and ``work_dir`` removed only after the callable returned. ``stop(timeout)`` waits at most ``timeout``; an
abandoned run is marked ``interrupted`` and its ``work_dir`` is left to the startup ``purge_temp``. Queued runs are
failed as ``interrupted`` at shutdown.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol, TypeVar

from config.settings import Settings
from core.events import EventBus
from core.exceptions import AppError, JobCancelledError
from core.feature_errors import JobDeletedError
from core.logging import bind_job_id
from inference.base import CancellationToken
from models.enums import JobStatus
from models.job import JobError
from models.runs import (
    GPU_DEFER_TIMEOUT_WARNING,
    INTERRUPTED_ERROR_CODE,
    OUT_OF_MEMORY_ERROR_CODE,
    RUN_ERROR_CODE,
    RUN_TIMEOUT_ERROR_CODE,
    RunKind,
    RunRecord,
    run_kind_for_id,
)
from pipelines.runs.context import RunContext, RunProviderRegistry
from pipelines.runs.executor import RunExecutor, RunPhase
from pipelines.runs.reporter import DEFAULT_PERSIST_INTERVAL_S, DEFAULT_PUBLISH_INTERVAL_S, RunReporter
from services.run_events import RunEventPublisher
from storage.paths import RuntimePaths
from storage.run_layout import RUN_LOG_FILE, RunLayout
from storage.run_store import RunStore
from utils.files import ensure_dir, retry_os, safe_rmtree
from utils.jsonsafe import to_jsonable
from utils.timeutil import utcnow
from workers.generation_worker import classify_exception
from workers.queue import JobQueue

logger = logging.getLogger(__name__)

T = TypeVar("T")

CPU_THREAD_NAME: Final = "parts-cpu"
SHUTDOWN_REASON: Final = "Server is shutting down"
USER_CANCEL_REASON: Final = "Cancelled by user"
TIMEOUT_REASON: Final = "The run exceeded its time limit"
MESSAGE_GPU_BUSY: Final = "Waiting for the GPU (generation in progress)"
MESSAGE_GPU_QUEUED: Final = "Waiting for the GPU (generation queued)"
GATE_POLL_INTERVAL_S: Final = 0.5
WATCHDOG_TICK_S: Final = 0.25
_MAX_ERROR_MESSAGE: Final = 2000

_KIND_LABELS: Final[dict[str, str]] = {
    "image_generation": "Image generation",
    "segmentation": "Segmentation",
    "part_analysis": "Part analysis",
}


class GpuTaskSubmitter(Protocol):
    """What the run worker needs from :class:`workers.generation_worker.GenerationWorker` (section 1.2 S-a)."""

    def submit_gpu_task(self, fn: Callable[..., T], /, *args: Any) -> asyncio.Future[T]: ...

    @property
    def accepting_gpu_tasks(self) -> bool: ...

    @property
    def is_busy(self) -> bool: ...

    @property
    def running_job_id(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class RunQueueStatus:
    """State of the shared run queue (``/features`` ``queues.runs``)."""

    pending: int
    running_id: str | None
    running_kind: RunKind | None
    max_size: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "pending": self.pending,
            "running_id": self.running_id,
            "running_kind": self.running_kind,
            "max_size": self.max_size,
        }


def jobs_pending(gpu: GpuTaskSubmitter, job_queue: JobQueue) -> bool:
    """``True`` while an image-to-3D job is queued or running."""
    return job_queue.pending > 0 or gpu.running_job_id is not None


async def jobs_first_gate(
    gpu: GpuTaskSubmitter,
    job_queue: JobQueue,
    *,
    max_defer_s: float,
    cancel: CancellationToken,
    on_wait: Callable[[], None] | None = None,
    wakeup: asyncio.Event | None = None,
    poll_interval: float = GATE_POLL_INTERVAL_S,
) -> bool:
    """Hold an auxiliary GPU task back while image-to-3D jobs are pending or running (section 4.3).

    Polls every ``poll_interval`` seconds on the event loop (``wakeup`` interrupts the wait, e.g. on cancel) and
    raises ``JobCancelledError`` when ``cancel`` is set. Returns ``True`` if it gave up after ``max_defer_s``
    (the caller then submits anyway and records ``gpu_defer_timeout``); ``max_defer_s <= 0`` disables the gate.
    """
    if max_defer_s <= 0 or not jobs_pending(gpu, job_queue):
        return False
    if on_wait is not None:
        on_wait()
    deadline = time.monotonic() + max_defer_s
    while True:
        cancel.raise_if_cancelled()
        if not jobs_pending(gpu, job_queue):
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        timeout = min(poll_interval, remaining)
        if wakeup is not None:
            try:
                await asyncio.wait_for(wakeup.wait(), timeout=timeout)
            except TimeoutError:
                pass
        else:
            await asyncio.sleep(timeout)


@dataclass(eq=False)
class _ActiveRun:
    run_id: str
    kind: RunKind
    reporter: RunReporter[Any]
    token: CancellationToken = field(default_factory=CancellationToken)
    wakeup: asyncio.Event = field(default_factory=asyncio.Event)
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    work_dir: Path | None = None
    inflight: asyncio.Future[Any] | None = None
    """The awaited prepare/phase/finalize future (``None`` once it completed)."""
    timed_out: bool = False
    _active_s: float = 0.0
    _call_started: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def cancel(self, reason: str) -> None:
        """Cancel the token once (the first reason wins) and wake the jobs-first gate (loop thread)."""
        if not self.token.is_cancelled:
            self.token.cancel(reason)
        self.wakeup.set()

    # Active-time accounting for the watchdog (called from the worker threads).
    def begin_call(self) -> None:
        with self._lock:
            self._call_started = time.monotonic()

    def end_call(self) -> None:
        with self._lock:
            if self._call_started is not None:
                self._active_s += time.monotonic() - self._call_started
                self._call_started = None

    def active_elapsed(self) -> float:
        with self._lock:
            running = time.monotonic() - self._call_started if self._call_started is not None else 0.0
            return self._active_s + running


class RunWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        gpu: GpuTaskSubmitter,
        queue: JobQueue,
        job_queue: JobQueue,
        executors: Mapping[RunKind, RunExecutor[Any, Any]],
        stores: Mapping[RunKind, RunStore[Any]],
        publishers: Mapping[RunKind, RunEventPublisher[Any]],
        registry: RunProviderRegistry | None,
        bus: EventBus,
        paths: RuntimePaths,
        on_queue_changed: Callable[[], None] | None = None,
        publish_interval: float = DEFAULT_PUBLISH_INTERVAL_S,
        persist_interval: float = DEFAULT_PERSIST_INTERVAL_S,
        gate_poll_interval: float = GATE_POLL_INTERVAL_S,
    ) -> None:
        self._settings = settings
        self._gpu = gpu
        self._queue = queue
        self._job_queue = job_queue
        self._executors = executors
        self._stores = stores
        self._publishers = publishers
        self._registry = registry
        self._bus = bus
        self._layout = RunLayout(paths)
        self._on_queue_changed = on_queue_changed
        self._publish_interval = publish_interval
        self._persist_interval = persist_interval
        self._gate_poll_interval = gate_poll_interval

        self._cpu_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix=CPU_THREAD_NAME)
        self._task: asyncio.Task[None] | None = None
        self._current: _ActiveRun | None = None
        self._stopping = False

    # ------------------------------------------------------------------ introspection
    @property
    def running_id(self) -> str | None:
        return self._current.run_id if self._current else None

    @property
    def running_kind(self) -> RunKind | None:
        return self._current.kind if self._current else None

    @property
    def stopping(self) -> bool:
        return self._stopping

    @property
    def started(self) -> bool:
        return self._task is not None

    def queue_status(self) -> RunQueueStatus:
        return RunQueueStatus(
            pending=self._queue.pending,
            running_id=self.running_id,
            running_kind=self.running_kind,
            max_size=self._queue.max_size,
        )

    def live_snapshot(self, run_id: str) -> RunRecord | None:
        """Fresh in-memory state of the running run (``run.json`` is only persisted every few seconds)."""
        active = self._current
        if active is not None and active.run_id == run_id:
            return active.reporter.snapshot()
        return None

    # ------------------------------------------------------------------ submission (services)
    def enqueue(self, run: RunRecord) -> RunRecord:
        """Persist a new ``queued`` run, append it to the run queue and publish it.

        Raises ``QueueFullError`` (429) when the queue is full and ``EngineUnavailableError`` (503) while stopping.
        """
        store = self._store_for(run.kind)
        if run.status is not JobStatus.QUEUED:
            raise ValueError(f"Only queued runs can be enqueued ({run.id} is {run.status})")
        self._queue.ensure_capacity()
        store.save(run)
        self._queue.put(run.id)
        self._publish(run)
        self._notify_queue_changed()
        return run

    def cancel_queued(self, run_id: str, reason: str = USER_CANCEL_REASON) -> RunRecord | None:
        """Cancel a run that has not started yet. Returns it, or ``None`` if it is not waiting in the queue."""
        kind = run_kind_for_id(run_id)
        if kind is None or kind not in self._stores or not self._queue.remove(run_id):
            return None
        store = self._stores[kind]
        run = store.get(run_id)
        if run is None or run.status is not JobStatus.QUEUED:
            return None
        now = utcnow()
        run.status = JobStatus.CANCELLED
        run.finished_at = now
        run.cancel_requested = True
        run.progress.message = "Cancelled"
        run.progress.updated_at = now
        try:
            store.save(run)
        except JobDeletedError:
            logger.info("Run %s cancelled while its job was being deleted", run_id)
        logger.info("Queued run %s cancelled (%s)", run_id, reason)
        self._publish(run)
        self._notify_queue_changed()
        return run

    def cancel_running(self, run_id: str, reason: str = USER_CANCEL_REASON) -> RunRecord | None:
        """Request cancellation of the running run. Returns its snapshot, or ``None`` if it is not running."""
        active = self._current
        if active is None or active.run_id != run_id:
            return None
        active.cancel(reason)

        def _mark(run: RunRecord) -> None:
            run.cancel_requested = True
            run.progress.message = "Cancelling..."

        logger.info("Cancellation requested for running run %s (%s)", run_id, reason)
        try:
            return active.reporter.mutate(_mark, publish=True, persist=True)
        except JobDeletedError:
            return active.reporter.snapshot()

    async def wait_finished(self, run_id: str, timeout: float) -> bool:
        """Wait until ``run_id`` is no longer the running run. Returns ``False`` on timeout."""
        active = self._current
        if active is None or active.run_id != run_id:
            return True
        try:
            await asyncio.wait_for(active.finished.wait(), timeout=timeout)
        except TimeoutError:
            return False
        return True

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="run-worker")

    async def stop(self, timeout: float) -> None:
        """Fail queued runs, cancel the running one and wait (at most ``timeout`` s) for its callable to return.

        Must run before the generation worker stops, so an auxiliary GPU task is never orphaned on its executor.
        """
        if self._stopping:
            return
        self._stopping = True
        for run_id in self._queue.close():
            self._fail_unstarted(run_id)
        active = self._current
        if active is not None:
            logger.info("Cancelling run %s for shutdown", active.run_id)
            active.cancel(SHUTDOWN_REASON)
        task = self._task
        if task is not None and not task.done():
            done, _ = await asyncio.wait({task}, timeout=max(timeout, 0.1))
            if not done:
                logger.warning("Run worker did not stop within %.0f s; abandoning the running run", timeout)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self._cpu_executor.shutdown(wait=False, cancel_futures=True)

    def _fail_unstarted(self, run_id: str) -> None:
        kind = run_kind_for_id(run_id)
        store = self._stores.get(kind) if kind is not None else None
        run = store.get(run_id) if store is not None else None
        if store is None or run is None or run.status is not JobStatus.QUEUED:
            return
        now = utcnow()
        run.status = JobStatus.FAILED
        run.finished_at = now
        run.error = JobError(code=INTERRUPTED_ERROR_CODE, message="The server stopped before this run started")
        run.progress.message = "Interrupted"
        run.progress.updated_at = now
        try:
            store.save(run)
        except Exception:  # noqa: BLE001
            logger.exception("Could not persist interrupted run %s", run_id)
        self._publish(run)

    # ------------------------------------------------------------------ main loop
    async def _loop(self) -> None:
        logger.info("Run worker started")
        while True:
            run_id = await self._queue.get()
            if run_id is None:
                break
            kind = run_kind_for_id(run_id)
            store = self._stores.get(kind) if kind is not None else None
            run = store.get(run_id) if store is not None else None
            if kind is None or store is None or run is None or run.status is not JobStatus.QUEUED:
                continue
            try:
                await self._process(kind, store, run)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the worker must survive any single run
                logger.exception("Unexpected run worker failure while processing %s", run_id)
        logger.info("Run worker stopped")

    async def _process(self, kind: RunKind, store: RunStore[Any], run: RunRecord) -> None:
        publisher = self._publishers[kind]
        reporter: RunReporter[Any] = RunReporter(
            run,
            publish=lambda snapshot, droppable: publisher.publish(snapshot, droppable=droppable),
            persist=store.save,
            schedule_later=lambda delay, cb: self._bus.call_later_threadsafe(delay, cb),
            min_publish_interval=self._publish_interval,
            persist_interval=self._persist_interval,
        )
        active = _ActiveRun(run_id=run.id, kind=kind, reporter=reporter)
        self._current = active
        self._notify_queue_changed()
        abandoned = False
        with bind_job_id(run.id):
            try:
                await self._execute(active, store)
            except asyncio.CancelledError:  # stop() timed out: the callable may still be running
                abandoned = True
                self._finish(active, JobStatus.FAILED, error=_interrupted_error())
                raise
            except JobCancelledError:
                self._finish_cancelled(active)
            except AppError as exc:
                if active.token.is_cancelled:
                    self._finish_cancelled(active)
                else:
                    log = logger.error if exc.status_code >= 500 else logger.warning
                    log("Run failed: %s (%s)", exc.message, exc.code)
                    self._finish(
                        active,
                        JobStatus.FAILED,
                        error=JobError(code=exc.code, message=exc.message, details=to_jsonable(exc.details)),
                    )
            except Exception as exc:  # noqa: BLE001
                if active.token.is_cancelled:
                    self._finish_cancelled(active)
                else:
                    logger.error("Run failed with an unexpected error", exc_info=exc)
                    self._finish(active, JobStatus.FAILED, error=_classify(exc))
            finally:
                self._current = None
                active.finished.set()
                if abandoned:
                    self._cleanup(active, store)
                else:
                    await asyncio.to_thread(self._cleanup, active, store)
                self._notify_queue_changed()

    async def _execute(self, active: _ActiveRun, store: RunStore[Any]) -> None:
        reporter = active.reporter
        executor = self._executors.get(active.kind)
        if executor is None:
            raise AppError(
                f"{_KIND_LABELS[active.kind]} is not available in this build", code=RUN_ERROR_CODE, status_code=503
            )
        reporter.set_plan(tuple(executor.plan(reporter.snapshot())))

        def _start(run: RunRecord) -> None:
            run.status = JobStatus.RUNNING
            run.started_at = utcnow()
            run.progress.message = MESSAGE_GPU_BUSY if self._gpu.is_busy else "Starting"

        reporter.mutate(_start, publish=True, persist=True)
        logger.info("Run started (%s)", active.kind)
        active.token.raise_if_cancelled()

        work_dir = self._layout.work_dir(active.kind, active.run_id)
        active.work_dir = work_dir
        snapshot = reporter.snapshot()
        ctx = RunContext(
            run_id=active.run_id,
            kind=active.kind,
            cancel=active.token,
            work_dir=work_dir,
            device=self._settings.device,
            registry=self._registry,
            reporter=reporter,
        )
        state = await self._await_call(
            active, self._thread_call(active, self._prepare_blocking, executor, snapshot, work_dir)
        )
        loop = asyncio.get_running_loop()
        for phase in executor.phases(snapshot):
            active.token.raise_if_cancelled()
            future: asyncio.Future[Any]
            if phase.device == "gpu":
                await self._gpu_gate(active)
                active.token.raise_if_cancelled()
                if self._gpu.is_busy:
                    reporter.set_message(MESSAGE_GPU_BUSY)
                future = self._gpu.submit_gpu_task(self._gpu_phase, active, phase, snapshot, state, ctx)
            else:
                future = loop.run_in_executor(self._cpu_executor, self._cpu_phase, active, phase, snapshot, state, ctx)
            await self._await_call(active, future)
            active.token.raise_if_cancelled()
        final = await self._await_call(
            active, self._thread_call(active, self._finalize_blocking, executor, snapshot, state, ctx)
        )
        self._finish(active, JobStatus.COMPLETED, final=final)
        logger.info("Run completed (%s)", active.kind)

    # ------------------------------------------------------------------ jobs-first gate
    async def _gpu_gate(self, active: _ActiveRun) -> None:
        max_defer = self._settings.run_max_defer_s
        gave_up = await jobs_first_gate(
            self._gpu,
            self._job_queue,
            max_defer_s=max_defer,
            cancel=active.token,
            on_wait=lambda: active.reporter.set_message(MESSAGE_GPU_QUEUED),
            wakeup=active.wakeup,
            poll_interval=self._gate_poll_interval,
        )
        if gave_up:
            logger.warning("Jobs-first gate gave up after %.0f s; submitting the GPU phase anyway", max_defer)
            active.reporter.warn(
                GPU_DEFER_TIMEOUT_WARNING,
                f"Waited {max_defer:g} s for image-to-3D jobs to finish; continuing on the shared GPU",
            )

    # ------------------------------------------------------------------ awaiting callables (watchdog)
    async def _await_call(self, active: _ActiveRun, future: asyncio.Future[T]) -> T:
        """Await a submitted callable without ever cancelling it; the pa_ watchdog cancels only the token."""
        active.inflight = future
        timeout = self._settings.part_analysis_timeout_s if active.kind == "part_analysis" else None
        while True:
            wait: float | None = None
            if timeout is not None and not active.timed_out:
                remaining = timeout - active.active_elapsed()
                if remaining <= 0:
                    active.timed_out = True
                    logger.warning("Run %s exceeded %.0f s of active time; cancelling it", active.run_id, timeout)
                    active.cancel(TIMEOUT_REASON)
                else:
                    wait = min(WATCHDOG_TICK_S, remaining)
            done, _ = await asyncio.wait({future}, timeout=wait)
            if done:
                break
        active.inflight = None
        if future.cancelled():  # dropped by an executor shutdown before it started
            raise JobCancelledError(active.token.reason or "The task was cancelled before it started")
        return future.result()

    def _thread_call(self, active: _ActiveRun, fn: Callable[..., T], *args: Any) -> asyncio.Future[T]:
        return asyncio.ensure_future(asyncio.to_thread(self._timed, active, fn, *args))

    def _timed(self, active: _ActiveRun, fn: Callable[..., T], *args: Any) -> T:
        """Run ``fn`` counting its wall time as active run time (any worker thread)."""
        active.begin_call()
        try:
            with bind_job_id(active.run_id):
                return fn(*args)
        finally:
            active.end_call()

    # ------------------------------------------------------------------ blocking parts
    @staticmethod
    def _prepare_blocking(executor: RunExecutor[Any, Any], run: RunRecord, work_dir: Path) -> Any:
        return executor.prepare(run, ensure_dir(work_dir))

    @staticmethod
    def _finalize_blocking(executor: RunExecutor[Any, Any], run: RunRecord, state: Any, ctx: RunContext) -> Any:
        return executor.finalize(run, state, ctx)

    def _cpu_phase(
        self, active: _ActiveRun, phase: RunPhase[Any, Any], run: RunRecord, state: Any, ctx: RunContext
    ) -> None:
        """Runs on the ``parts-cpu`` thread."""
        self._timed(active, phase.fn, run, state, ctx)

    def _gpu_phase(
        self, active: _ActiveRun, phase: RunPhase[Any, Any], run: RunRecord, state: Any, ctx: RunContext
    ) -> None:
        """Runs on the ``gpu-worker`` thread (submitted through ``submit_gpu_task``)."""
        self._timed(active, self._gpu_phase_body, phase, run, state, ctx)

    def _gpu_phase_body(self, phase: RunPhase[Any, Any], run: RunRecord, state: Any, ctx: RunContext) -> None:
        ctx.cancel.raise_if_cancelled()
        registry = self._registry
        if registry is None:
            raise AppError(
                "The model registry is not available in this build; GPU steps cannot run",
                code=RUN_ERROR_CODE,
                status_code=503,
            )
        with registry.guard():
            failed = True
            try:
                registry.ensure_vram(phase.models)
                phase.fn(run, state, ctx)
                failed = False
            finally:
                try:
                    registry.offload(phase.models)
                except Exception:
                    if not failed:
                        raise
                    logger.exception("Offloading %s failed after a phase error", ", ".join(phase.models) or "models")

    # ------------------------------------------------------------------ completion
    def _finish_cancelled(self, active: _ActiveRun) -> None:
        reason = active.token.reason
        if active.timed_out:
            timeout = self._settings.part_analysis_timeout_s
            self._finish(
                active,
                JobStatus.FAILED,
                error=JobError(
                    code=RUN_TIMEOUT_ERROR_CODE,
                    message=f"The run exceeded {timeout:g} s of processing time",
                    details={"timeout_s": timeout},
                ),
            )
        elif reason == SHUTDOWN_REASON:
            self._finish(active, JobStatus.FAILED, error=_interrupted_error())
        else:
            logger.info("Run cancelled")
            self._finish(active, JobStatus.CANCELLED)

    def _finish(
        self,
        active: _ActiveRun,
        status: JobStatus,
        *,
        error: JobError | None = None,
        final: RunRecord | None = None,
    ) -> None:
        reporter = active.reporter
        reporter.close()

        def _apply(run: RunRecord) -> None:
            run.status = status
            run.finished_at = utcnow()
            run.error = error
            if final is not None:
                _copy_result_fields(final, run)
            leftover = "cancelled" if status is JobStatus.CANCELLED else "failed"
            for step in run.steps:
                if step.status == "running":
                    step.status = "done" if status is JobStatus.COMPLETED else leftover  # type: ignore[assignment]
                elif step.status == "pending" and status is JobStatus.COMPLETED:
                    step.status = "skipped"
                    step.message = step.message or "Not run"
                    step.progress = 1.0
            if status is JobStatus.COMPLETED:
                run.progress.message = "Done"
            elif status is JobStatus.CANCELLED:
                run.progress.message = "Cancelled"
            else:
                run.progress.message = error.message if error else "Failed"

        try:
            reporter.mutate(_apply, publish=True, persist=True)
        except JobDeletedError:
            logger.info("Run %s finished after its job was deleted (%s)", active.run_id, status)
        except Exception:  # noqa: BLE001 - persistence failure must still notify clients (already published)
            logger.exception("Could not persist the final state of run %s", active.run_id)
        if status is JobStatus.FAILED:
            logger.info("Run finished with status failed (%s)", error.code if error else "unknown")

    def _cleanup(self, active: _ActiveRun, store: RunStore[Any]) -> None:
        """Remove the work dir, but only once no callable can still be using it (section 4.3 step 7)."""
        work_dir = active.work_dir
        if work_dir is None:
            return
        if active.inflight is not None and not active.inflight.done():
            logger.warning("Leaving %s for the startup purge: a run task is still executing", work_dir)
            return
        final = active.reporter.snapshot()
        if store.job_scoped and final.status is JobStatus.FAILED:
            self._keep_log(store, final, work_dir)
        safe_rmtree(work_dir)

    @staticmethod
    def _keep_log(store: RunStore[Any], run: RunRecord, work_dir: Path) -> None:
        """Move ``work_dir/log.txt`` of a failed part analysis next to its ``run.json`` (job must still exist)."""
        source = work_dir / RUN_LOG_FILE
        job_id = getattr(run, "job_id", None)
        if not source.is_file() or not isinstance(job_id, str) or not store.job_exists(job_id):
            return
        target_dir = store.run_dir(run)
        if not target_dir.is_dir():
            return
        try:
            retry_os(lambda: os.replace(source, target_dir / RUN_LOG_FILE))
        except OSError as exc:
            logger.warning("Could not keep the log of failed run %s: %s", run.id, exc)

    # ------------------------------------------------------------------ helpers
    def _store_for(self, kind: RunKind) -> RunStore[Any]:
        store = self._stores.get(kind)
        if store is None:
            raise AppError(f"{_KIND_LABELS[kind]} is not available in this build", code=RUN_ERROR_CODE)
        return store

    def _publish(self, run: RunRecord) -> None:
        publisher = self._publishers.get(run.kind)
        if publisher is not None:
            publisher.publish(run)

    def _notify_queue_changed(self) -> None:
        if self._on_queue_changed is None:
            return
        try:
            self._on_queue_changed()
        except Exception:  # noqa: BLE001
            logger.exception("Run queue change notification failed")


def _interrupted_error() -> JobError:
    return JobError(code=INTERRUPTED_ERROR_CODE, message=SHUTDOWN_REASON)


def _classify(exc: BaseException) -> JobError:
    """``classify_exception`` (read-only): OOM keeps ``out_of_memory``; anything else becomes ``run_error``."""
    classified = classify_exception(exc)
    if classified.code == OUT_OF_MEMORY_ERROR_CODE:
        return classified
    return JobError(code=RUN_ERROR_CODE, message=classified.message[:_MAX_ERROR_MESSAGE], details=classified.details)


_BASE_FIELDS: Final = frozenset(RunRecord.model_fields)


def _copy_result_fields(source: RunRecord, target: RunRecord) -> None:
    """Copy the kind-specific fields (``result``, ...) that ``finalize`` set onto the live run."""
    for name in type(source).model_fields:
        if name not in _BASE_FIELDS and name in type(target).model_fields:
            value = getattr(source, name)
            setattr(target, name, value.model_copy(deep=True) if hasattr(value, "model_copy") else value)
