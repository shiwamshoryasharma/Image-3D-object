"""The Auto Mesh Tuner worker (DESIGN.md sections 3 and 6).

One asyncio task consumes the tuning queue; every blocking piece of a run (input copy, the Blender process,
moving outputs, mesh statistics) runs on ONE dedicated thread (``ThreadPoolExecutor(max_workers=1,
thread_name_prefix="blender-worker")``) - never on the event loop and never on the GPU worker thread.

Per run:

1. resolve the selected Blender runtime (503 ``blender_unavailable`` message if there is none),
2. copy the job's ``model.glb`` to ``runtime/temp/tuning/<tn>/input.glb`` and write ``cfg.json``,
3. run ``blender --background ... --python blender_scripts/auto_mesh_tuner.py -- --config cfg.json`` through the
   launcher's :class:`~launcher.BlenderProcessRunner` (kill-on-close Job Object, per-run TEMP, isolated
   ``BLENDER_USER_RESOURCES``, raw log file, cancel event, timeout),
4. map ``@@AMT@@`` protocol events to ``steps``/``progress`` (progress publishes throttled to 5/s and droppable;
   step/status changes are never dropped),
5. on success re-check that the job still exists, move the outputs into ``runtime/jobs/<job>/tuning/<tn>/`` and
   compute the tuned model's statistics.

Cancel kills the Blender process tree; shutdown cancels the running run (marked failed ``interrupted``).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import re
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol

from config.settings import Settings
from core.events import Event, EventBus
from core.exceptions import AppError
from core.logging import bind_job_id
from launcher import BlenderInstallation, BlenderRunRequest, BlenderRunResult, PathTooLongError
from models.enums import JobStatus
from models.job import JobError
from models.tuning import (
    BLEND_FILE,
    CONFIG_FILE,
    FINISHED_STEP_STATUSES,
    INTERRUPTED_ERROR_CODE,
    LOG_FILE,
    MODEL_FILE,
    PREVIEWS_DIR,
    REPORT_FILE,
    STEP_STATUSES,
    STEP_TABLE,
    TuningBlender,
    TuningPreview,
    TuningProgress,
    TuningResultData,
    TuningRun,
    TuningStep,
)
from processing.mesh_stats import compute_glb_stats
from schemas.events import EventType
from schemas.mesh_tuner import TuningRunOut
from storage.paths import RuntimePaths
from storage.tuning_store import TuningStore
from utils.files import ensure_dir, replace_file, safe_rmtree
from utils.jsonsafe import to_jsonable
from utils.timeutil import utcnow
from workers.queue import JobQueue

logger = logging.getLogger(__name__)

BLENDER_THREAD_NAME: Final = "blender-worker"
TUNER_SCRIPT_NAME: Final = "auto_mesh_tuner.py"
SHUTDOWN_REASON: Final = "Server is shutting down"
USER_CANCEL_REASON: Final = "Cancelled by user"
JOB_DELETED_REASON: Final = "The job was deleted"
DEFAULT_PUBLISH_INTERVAL_S: Final = 0.2
CONFIG_SCHEMA: Final = 1

PREVIEW_NAME_RE: Final = re.compile(r"^[a-z0-9_]+\.png$")
_PLAIN_FILE_RE: Final = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_MAX_MESSAGE: Final = 2000
_MAX_ERROR_LINES: Final = 6
_INPUT_FILE: Final = "input.glb"
_CFG_FILE: Final = "cfg.json"
_OUT_DIR: Final = "out"


class BlenderRunner(Protocol):
    """What the worker needs from :class:`launcher.BlenderProcessRunner` (test doubles implement this)."""

    def run(self, req: BlenderRunRequest) -> BlenderRunResult: ...


class RuntimeProvider(Protocol):
    """What the worker needs from :class:`services.blender_runtime_service.BlenderRuntimeService`."""

    async def require_runtime(self) -> BlenderInstallation: ...


# --------------------------------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------------------------------


class TuningEventPublisher:
    """Builds and publishes ``tuning.updated`` events (thread-safe)."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    def publish(self, run: TuningRun, *, droppable: bool = False) -> None:
        data = TuningRunOut.from_domain(run).model_dump(mode="json")
        self._bus.publish_threadsafe(
            Event(type=str(EventType.TUNING_UPDATED), data=data, job_id=run.job_id, droppable=droppable)
        )


# --------------------------------------------------------------------------------------------------
# Live run state
# --------------------------------------------------------------------------------------------------


class RunReporter:
    """Thread-safe live state of the running tuning (mutated by protocol reader threads and the loop).

    Status/step changes are published immediately (not droppable); progress updates are throttled to
    ``min_publish_interval`` with a trailing flush so the latest state is always delivered.
    """

    def __init__(
        self,
        run: TuningRun,
        *,
        publish: Callable[[TuningRun, bool], None],
        persist: Callable[[TuningRun], object],
        schedule_later: Callable[[float, Callable[[], None]], None],
        min_publish_interval: float = DEFAULT_PUBLISH_INTERVAL_S,
    ) -> None:
        self._run = run.model_copy(deep=True)
        self._publish = publish
        self._persist = persist
        self._schedule_later = schedule_later
        self._interval = max(0.0, min_publish_interval)
        self._lock = threading.RLock()
        self._last_publish = 0.0
        self._pending = False
        self._flush_scheduled = False
        self._closed = False

    def snapshot(self) -> TuningRun:
        with self._lock:
            return self._run.model_copy(deep=True)

    def mutate(
        self,
        fn: Callable[[TuningRun], None],
        *,
        publish: bool = False,
        progress: bool = False,
        persist: bool = False,
    ) -> TuningRun:
        """Apply ``fn``; ``publish`` = immediate status-change event, ``progress`` = throttled droppable event."""
        with self._lock:
            fn(self._run)
            self._run.progress.updated_at = utcnow()
            snapshot = self._run.model_copy(deep=True)
            closed = self._closed
        if persist:
            try:
                self._persist(snapshot)
            except Exception:  # noqa: BLE001 - live progress must not die on a disk hiccup
                logger.exception("Could not persist tuning run %s", snapshot.id)
        if closed:
            return snapshot
        if publish:
            with self._lock:
                self._last_publish = time.monotonic()
                self._pending = False
            self._publish(snapshot, False)
        elif progress:
            self._throttled_publish()
        return snapshot

    def _throttled_publish(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last_publish)
            if wait <= 0:
                self._last_publish = now
                self._pending = False
                snapshot = self._run.model_copy(deep=True)
            else:
                self._pending = True
                if self._flush_scheduled:
                    return
                self._flush_scheduled = True
                snapshot = None
        if snapshot is not None:
            self._publish(snapshot, True)
        else:
            self._schedule_later(wait, self._flush)

    def _flush(self) -> None:
        with self._lock:
            self._flush_scheduled = False
            if self._closed or not self._pending:
                return
            self._pending = False
            self._last_publish = time.monotonic()
            snapshot = self._run.model_copy(deep=True)
        self._publish(snapshot, True)

    def close(self) -> None:
        """Stop progress publishing (the final status is published by the worker)."""
        with self._lock:
            self._closed = True
            self._pending = False


@dataclass(eq=False)
class _ActiveRun:
    run_id: str
    job_id: str
    reporter: RunReporter
    cancel_event: threading.Event = field(default_factory=threading.Event)
    cancel_reason: str | None = None
    work_dir: Path | None = None
    log_path: Path | None = None
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    result_event: dict[str, Any] | None = None
    error_event: dict[str, Any] | None = None

    def cancel(self, reason: str) -> None:
        if self.cancel_reason is None:
            self.cancel_reason = reason
        self.cancel_event.set()


@dataclass(slots=True)
class _Prepared:
    work_dir: Path
    input_path: Path
    output_dir: Path
    config_path: Path
    log_path: Path
    temp_dir: Path


class _RunCancelled(Exception):
    """Internal: the run was cancelled before Blender started."""


class _RunFailure(Exception):
    """Internal: fail the run with ``code``/``message``."""

    def __init__(self, code: str, message: str, details: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


# --------------------------------------------------------------------------------------------------
# Worker
# --------------------------------------------------------------------------------------------------


class TuningWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        paths: RuntimePaths,
        tuning_store: TuningStore,
        queue: JobQueue,
        runtime: RuntimeProvider,
        runner: BlenderRunner | Callable[[], BlenderRunner],
        events: TuningEventPublisher,
        bus: EventBus,
        job_exists: Callable[[str], bool],
        job_model_path: Callable[[str], Path],
        user_resources_dir: Path | None = None,
        publish_interval: float = DEFAULT_PUBLISH_INTERVAL_S,
    ) -> None:
        self._settings = settings
        self._paths = paths
        self._store = tuning_store
        self._queue = queue
        self._runtime = runtime
        self._runner = runner
        self._events = events
        self._bus = bus
        self._job_exists = job_exists
        self._job_model_path = job_model_path
        self._user_resources_dir = user_resources_dir
        self._publish_interval = publish_interval
        self.script_path: Path = settings.blender_scripts_dir / TUNER_SCRIPT_NAME
        """Blender-side entry script (tests point it at a protocol-conformant fake)."""
        self.timeout_s: float = settings.blender_tune_timeout_s

        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix=BLENDER_THREAD_NAME)
        self._inflight: set[concurrent.futures.Future[Any]] = set()
        self._task: asyncio.Task[None] | None = None
        self._current: _ActiveRun | None = None
        self._stopping = False

    # ------------------------------------------------------------------ introspection
    @property
    def running_id(self) -> str | None:
        return self._current.run_id if self._current else None

    @property
    def is_busy(self) -> bool:
        return any(not f.done() for f in self._inflight)

    @property
    def stopping(self) -> bool:
        return self._stopping

    def live_snapshot(self, tuning_id: str) -> TuningRun | None:
        active = self._current
        if active is not None and active.run_id == tuning_id:
            return active.reporter.snapshot()
        return None

    def live_log_path(self, tuning_id: str) -> Path | None:
        """Raw Blender log of the running tuning (still in its temp work dir)."""
        active = self._current
        if active is not None and active.run_id == tuning_id:
            return active.log_path
        return None

    def _get_runner(self) -> BlenderRunner:
        runner = self._runner
        return runner if hasattr(runner, "run") else runner()  # type: ignore[operator,return-value]

    def _submit(self, fn: Callable[..., Any], *args: Any) -> asyncio.Future[Any]:
        future = self._executor.submit(fn, *args)
        self._inflight.add(future)
        future.add_done_callback(self._inflight.discard)
        return asyncio.wrap_future(future)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="tuning-worker")

    async def stop(self, timeout: float) -> None:
        """Stop accepting runs, fail queued ones, kill the running Blender and wait (at most ``timeout`` s)."""
        if self._stopping:
            return
        self._stopping = True
        for tuning_id in self._queue.close():
            self._fail_unstarted(tuning_id)
        active = self._current
        if active is not None:
            logger.info("Cancelling tuning run %s for shutdown", active.run_id)
            active.cancel(SHUTDOWN_REASON)
        task = self._task
        if task is not None and not task.done():
            done, _ = await asyncio.wait({task}, timeout=max(timeout, 0.1))
            if not done:
                logger.warning("Tuning worker did not stop within %.0f s; abandoning it", timeout)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _fail_unstarted(self, tuning_id: str) -> None:
        run = self._store.get(tuning_id)
        if run is None or run.status is not JobStatus.QUEUED:
            return
        now = utcnow()
        run.status = JobStatus.FAILED
        run.finished_at = now
        run.error = JobError(code=INTERRUPTED_ERROR_CODE, message="The server stopped before this tuning run started")
        run.progress.message = "Interrupted"
        run.progress.updated_at = now
        try:
            self._store.save(run)
        except Exception:  # noqa: BLE001
            logger.exception("Could not persist interrupted tuning run %s", tuning_id)
        self._events.publish(run)

    # ------------------------------------------------------------------ cancellation
    def cancel_running(self, tuning_id: str, reason: str = USER_CANCEL_REASON) -> TuningRun | None:
        """Kill the running tuning's Blender. Returns its snapshot, or ``None`` if it is not the running one."""
        active = self._current
        if active is None or active.run_id != tuning_id:
            return None
        active.cancel(reason)

        def _mark(run: TuningRun) -> None:
            run.cancel_requested = True
            run.progress.message = "Cancelling..."

        logger.info("Cancellation requested for running tuning %s (%s)", tuning_id, reason)
        return active.reporter.mutate(_mark, publish=True, persist=True)

    async def wait_finished(self, tuning_id: str, timeout: float) -> bool:
        """Wait until ``tuning_id`` is no longer the running tuning. Returns ``False`` on timeout."""
        active = self._current
        if active is None or active.run_id != tuning_id:
            return True
        try:
            await asyncio.wait_for(active.finished.wait(), timeout=timeout)
        except TimeoutError:
            return False
        return True

    # ------------------------------------------------------------------ main loop
    async def _loop(self) -> None:
        logger.info("Tuning worker started")
        while True:
            tuning_id = await self._queue.get()
            if tuning_id is None:
                break
            run = self._store.get(tuning_id)
            if run is None or run.status is not JobStatus.QUEUED:
                continue
            try:
                await self._process(run)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the worker must survive any single run
                logger.exception("Unexpected tuning worker failure while processing %s", tuning_id)
        logger.info("Tuning worker stopped")

    async def _process(self, run: TuningRun) -> None:
        reporter = RunReporter(
            run,
            publish=lambda snapshot, droppable: self._events.publish(snapshot, droppable=droppable),
            persist=self._store.save,
            schedule_later=lambda delay, cb: self._bus.call_later_threadsafe(delay, cb),
            min_publish_interval=self._publish_interval,
        )
        active = _ActiveRun(run_id=run.id, job_id=run.job_id, reporter=reporter)
        self._current = active
        prepared: _Prepared | None = None
        started = time.monotonic()
        with bind_job_id(run.job_id):
            try:
                try:
                    install = await self._runtime.require_runtime()
                except AppError as exc:
                    raise _RunFailure(exc.code, exc.message, to_jsonable(exc.details)) from exc

                def _start(r: TuningRun) -> None:
                    r.status = JobStatus.RUNNING
                    r.started_at = utcnow()
                    r.blender = TuningBlender(
                        executable=str(install.executable),
                        version=install.version.label if install.version else None,
                        kind=install.kind,
                    )
                    r.progress = TuningProgress(message="Starting Blender")

                reporter.mutate(_start, publish=True, persist=True)
                logger.info(
                    "Tuning %s started (Blender %s, %s)",
                    run.id,
                    install.version.label if install.version else "?",
                    install.executable,
                )
                if active.cancel_event.is_set():
                    raise _RunCancelled
                prepared = await self._submit(self._prepare, run)
                active.work_dir = prepared.work_dir
                active.log_path = prepared.log_path
                if active.cancel_event.is_set():
                    raise _RunCancelled
                request = BlenderRunRequest(
                    executable=install.executable,
                    script=self.script_path,
                    script_args=["--config", str(prepared.config_path)],
                    timeout_s=self.timeout_s,
                    temp_dir=prepared.temp_dir,
                    user_resources_dir=self._user_resources_dir,
                    log_path=prepared.log_path,
                    cancel_event=active.cancel_event,
                    on_event=lambda event: self._on_protocol_event(active, event),
                )
                result: BlenderRunResult = await self._submit(self._run_blender, request)
                await self._complete(active, prepared, result, time.monotonic() - started)
            except asyncio.CancelledError:  # the worker task itself is abandoned at shutdown
                active.cancel(SHUTDOWN_REASON)
                self._finish(active, JobStatus.FAILED, error=self._interrupted_error())
                raise
            except _RunCancelled:
                self._finish_cancelled(active)
            except _RunFailure as failure:
                if active.cancel_event.is_set():
                    self._finish_cancelled(active)
                else:
                    logger.warning("Tuning %s failed: %s (%s)", run.id, failure.message, failure.code)
                    self._finish(
                        active,
                        JobStatus.FAILED,
                        error=JobError(code=failure.code, message=failure.message, details=failure.details),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Tuning %s failed unexpectedly", run.id)
                self._finish(
                    active,
                    JobStatus.FAILED,
                    error=JobError(
                        code="internal_error",
                        message=f"Auto Mesh Tuner failed unexpectedly: {type(exc).__name__}: {exc}"[:_MAX_MESSAGE],
                    ),
                )
            finally:
                self._current = None
                active.finished.set()
                await self._cleanup(run.id)

    # ------------------------------------------------------------------ blocking parts (blender-worker thread)
    def _work_dir(self, tuning_id: str) -> Path:
        return self._paths.temp / "tuning" / tuning_id

    def _run_temp_dir(self, tuning_id: str) -> Path:
        return self._settings.blender_temp_dir / tuning_id

    def _prepare(self, run: TuningRun) -> _Prepared:
        """Copy the job model to the work dir and write ``cfg.json`` (DESIGN section 3)."""
        source = self._job_model_path(run.job_id)
        if not self._job_exists(run.job_id):
            raise _RunFailure("job_not_found", "The job was deleted before tuning started")
        if not source.is_file():
            raise _RunFailure("input_missing", "The job's model.glb is missing", {"job_id": run.job_id})
        work = self._work_dir(run.id)
        safe_rmtree(work)
        out = ensure_dir(work / _OUT_DIR)
        input_path = work / _INPUT_FILE
        try:
            shutil.copyfile(source, input_path)
        except OSError as exc:
            raise _RunFailure("input_copy_failed", f"Could not copy the job model: {exc}") from exc
        options = dict(run.options)
        config = {
            "schema": CONFIG_SCHEMA,
            "input": str(input_path),
            "output_dir": str(out),
            "options": options,
            "outputs": {
                "model": MODEL_FILE,
                "report": REPORT_FILE,
                "blend": BLEND_FILE if options.get("save_blend") else None,
                "previews_dir": PREVIEWS_DIR,
            },
            "context": {"job_id": run.job_id, "tuning_id": run.id, "source": "trellis2"},
        }
        config_path = work / _CFG_FILE
        config_path.write_text(json.dumps(config, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        temp_dir = self._run_temp_dir(run.id)
        safe_rmtree(temp_dir)
        return _Prepared(
            work_dir=work,
            input_path=input_path,
            output_dir=out,
            config_path=config_path,
            log_path=work / LOG_FILE,
            temp_dir=temp_dir,
        )

    def _run_blender(self, request: BlenderRunRequest) -> BlenderRunResult:
        try:
            return self._get_runner().run(request)
        except PathTooLongError as exc:
            raise _RunFailure(exc.code, exc.message, exc.details) from exc

    def _finalize(self, run_id: str, job_id: str, prepared: _Prepared, result_event: dict[str, Any]) -> TuningResultData:
        """Move the outputs into the job's tuning folder and compute the tuned model's stats."""
        out = prepared.output_dir
        model_name = _plain_name(result_event.get("model")) or MODEL_FILE
        model_src = out / model_name
        if not model_src.is_file() or model_src.stat().st_size == 0:
            raise _RunFailure("tuning_output_missing", f"Blender finished but did not write {model_name}")
        if not self._job_exists(job_id):
            raise _RunFailure("job_not_found", "The job was deleted while it was being tuned")
        final = ensure_dir(self._store.run_dir(job_id, run_id))
        replace_file(model_src, final / MODEL_FILE)

        report_name = _plain_name(result_event.get("report")) or REPORT_FILE
        report_file: str | None = None
        if (out / report_name).is_file():
            replace_file(out / report_name, final / REPORT_FILE)
            report_file = REPORT_FILE
        blend_name = _plain_name(result_event.get("blend"))
        blend_file: str | None = None
        if blend_name and (out / blend_name).is_file():
            replace_file(out / blend_name, final / BLEND_FILE)
            blend_file = BLEND_FILE
        previews = self._move_previews(out / PREVIEWS_DIR, final / PREVIEWS_DIR, result_event.get("previews"))
        self._move_aux_files(prepared, final)

        stats = None
        try:
            stats = compute_glb_stats(final / MODEL_FILE)
        except Exception as exc:  # noqa: BLE001 - stats are informative; the tuned model is still usable
            logger.warning("Could not compute statistics of tuned model %s: %s", run_id, exc)
        return TuningResultData(
            model_file=MODEL_FILE,
            report_file=report_file,
            blend_file=blend_file,
            previews=previews,
            stats=stats,
        )

    @staticmethod
    def _move_previews(src: Path, dst: Path, listed: Any) -> list[TuningPreview]:
        found = {p.name: p for p in src.glob("*.png")} if src.is_dir() else {}
        previews: list[TuningPreview] = []
        seen: set[str] = set()
        entries = listed if isinstance(listed, list) else []
        for entry in [*entries, *({"name": name} for name in sorted(found))]:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or name in seen or not PREVIEW_NAME_RE.match(name) or name not in found:
                continue
            seen.add(name)
            ensure_dir(dst)
            replace_file(found[name], dst / name)
            previews.append(
                TuningPreview(
                    name=name,
                    view=entry.get("view") if isinstance(entry.get("view"), str) else None,
                    variant=entry.get("variant") if isinstance(entry.get("variant"), str) else None,
                )
            )
        return previews

    def _move_aux_files(self, prepared: _Prepared, final: Path) -> None:
        """Keep ``blender.log`` and ``config.json`` next to the run (also for failed runs)."""
        for src, name in ((prepared.log_path, LOG_FILE), (prepared.config_path, CONFIG_FILE)):
            if src.is_file():
                try:
                    replace_file(src, final / name)
                except OSError as exc:
                    logger.warning("Could not keep %s for tuning run: %s", name, exc)

    def _keep_failure_files(self, run_id: str, job_id: str, prepared: _Prepared) -> None:
        if not self._job_exists(job_id):
            return
        final = ensure_dir(self._store.run_dir(job_id, run_id))
        report = prepared.output_dir / REPORT_FILE
        if report.is_file():
            try:
                replace_file(report, final / REPORT_FILE)
            except OSError as exc:
                logger.warning("Could not keep the report of failed tuning %s: %s", run_id, exc)
        self._move_aux_files(prepared, final)

    async def _cleanup(self, tuning_id: str) -> None:
        def _remove() -> None:
            safe_rmtree(self._work_dir(tuning_id))
            safe_rmtree(self._run_temp_dir(tuning_id))

        try:
            await asyncio.to_thread(_remove)
        except Exception:  # noqa: BLE001
            logger.exception("Could not clean the work directory of tuning %s", tuning_id)

    # ------------------------------------------------------------------ result mapping
    async def _complete(self, active: _ActiveRun, prepared: _Prepared, result: BlenderRunResult, elapsed: float) -> None:
        run_id, job_id = active.run_id, active.job_id
        if result.status == "succeeded" and not result.cancelled:
            result_event = result.result or active.result_event or {}
            if not result_event:
                await self._submit(self._keep_failure_files, run_id, job_id, prepared)
                raise _RunFailure(
                    "tuning_output_missing", "Blender finished without reporting a result", self._details(result)
                )

            def _validating(r: TuningRun) -> None:
                r.progress.message = "Saving results"

            active.reporter.mutate(_validating, publish=True)
            try:
                data: TuningResultData = await self._submit(self._finalize, run_id, job_id, prepared, result_event)
            except _RunFailure:
                await self._submit(self._keep_failure_files, run_id, job_id, prepared)
                raise
            data.duration_s = round(elapsed, 3)
            reported = result_event.get("warnings")
            warnings = [str(w) for w in reported if w] if isinstance(reported, list) else None
            metrics = [to_jsonable(m) for m in result_event.get("metrics") or [] if isinstance(m, dict)]
            validation = result_event.get("validation")

            def _apply(r: TuningRun) -> None:
                if metrics:
                    r.metrics = metrics
                if warnings is not None:  # the result's list is authoritative (live step warnings are previews)
                    r.warnings = list(dict.fromkeys(warnings))
                if isinstance(validation, dict):
                    r.validation = to_jsonable(validation)
                if result.blender_version and r.blender is not None:
                    r.blender.version = result.blender_version

            active.reporter.mutate(_apply)
            self._finish(active, JobStatus.COMPLETED, result=data)
            return

        await self._submit(self._keep_failure_files, run_id, job_id, prepared)
        if result.cancelled or active.cancel_event.is_set():
            self._finish_cancelled(active)
            return
        raise _RunFailure(*self._failure(result, active))

    def _failure(self, result: BlenderRunResult, active: _ActiveRun) -> tuple[str, str, dict[str, Any]]:
        details = self._details(result)
        status = result.status
        if status == "timed_out":
            return "blender_timeout", f"Blender did not finish within {self.timeout_s:.0f} s and was stopped", details
        if status == "launch_failed":
            return "blender_launch_failed", _first(result.errors) or "Could not start Blender", details
        if status == "crashed":
            code = "unknown" if result.exit_code is None else f"0x{result.exit_code & 0xFFFFFFFF:08X}"
            return "blender_crashed", f"Blender exited abnormally (exit code {code})", details
        if status == "script_error":
            return "blender_script_error", _script_error_message(result.errors), details
        error_event = active.error_event or next(
            (e for e in reversed(result.events) if e.get("event") == "error"), None
        )
        if error_event is not None:
            code = str(error_event.get("code") or "tuning_failed")
            message = str(error_event.get("message") or "Auto Mesh Tuner failed")[:_MAX_MESSAGE]
            if error_event.get("step"):
                details["step"] = error_event.get("step")
            return code, message, details
        return "tuning_failed", _first(result.errors) or f"Blender exited with code {result.exit_code}", details

    @staticmethod
    def _details(result: BlenderRunResult) -> dict[str, Any]:
        return {
            "run_status": result.status,
            "exit_code": result.exit_code,
            "duration_s": result.duration_s,
            "errors": [e[-_MAX_MESSAGE:] for e in result.errors[-_MAX_ERROR_LINES:]],
        }

    @staticmethod
    def _interrupted_error() -> JobError:
        return JobError(code=INTERRUPTED_ERROR_CODE, message=SHUTDOWN_REASON)

    def _finish_cancelled(self, active: _ActiveRun) -> None:
        if active.cancel_reason == SHUTDOWN_REASON:
            self._finish(active, JobStatus.FAILED, error=self._interrupted_error())
        else:
            logger.info("Tuning %s cancelled", active.run_id)
            self._finish(active, JobStatus.CANCELLED)

    def _finish(
        self,
        active: _ActiveRun,
        status: JobStatus,
        *,
        error: JobError | None = None,
        result: TuningResultData | None = None,
    ) -> None:
        reporter = active.reporter
        reporter.close()

        def _apply(run: TuningRun) -> None:
            now = utcnow()
            run.status = status
            run.finished_at = now
            run.error = error
            run.result = result
            if status is JobStatus.COMPLETED:
                run.progress.percent = 100.0
                run.progress.message = (
                    f"Completed with {len(run.warnings)} warning(s)" if run.warnings else "Completed"
                )
            elif status is JobStatus.CANCELLED:
                run.progress.message = "Cancelled"
            else:
                run.progress.message = error.message if error else "Failed"
            for step in run.steps:
                if step.status == "running":
                    step.status = "failed" if status is JobStatus.FAILED else "skipped"
                    if status is JobStatus.CANCELLED:
                        step.message = "Cancelled"

        snapshot = reporter.mutate(_apply)
        try:
            saved = self._store.save(snapshot)
        except Exception:  # noqa: BLE001 - persistence failure must still notify clients
            logger.exception("Could not persist final state of tuning %s", active.run_id)
            saved = True
        if saved:
            self._events.publish(snapshot)
        if status is JobStatus.COMPLETED and result is not None:
            stats = result.stats
            logger.info(
                "Tuning %s completed in %.1f s (%s vertices, %s faces, %d warning(s))",
                active.run_id,
                result.duration_s or 0.0,
                stats.vertices if stats else "?",
                stats.faces if stats else "?",
                len(snapshot.warnings),
            )
        elif status is JobStatus.FAILED:
            logger.info("Tuning %s finished with status failed (%s)", active.run_id, error.code if error else "?")

    # ------------------------------------------------------------------ protocol events (reader threads)
    def _on_protocol_event(self, active: _ActiveRun, event: dict[str, Any]) -> None:
        kind = event.get("event")
        if kind == "step":
            self._on_step(active, event)
        elif kind == "progress":
            self._on_progress(active, event)
        elif kind == "plan":
            self._on_plan(active, event)
        elif kind == "hello":
            version = event.get("blender_version")
            if isinstance(version, str) and version:

                def _hello(run: TuningRun) -> None:
                    if run.blender is not None:
                        run.blender.version = version
                    run.progress.message = "Blender started"

                active.reporter.mutate(_hello, progress=True)
        elif kind == "result":
            active.result_event = event
        elif kind == "error":
            active.error_event = event
            self._on_error(active, event)
        elif kind == "log":
            level = str(event.get("level") or "info").lower()
            message = str(event.get("message") or "")[:_MAX_MESSAGE]
            log_level = logging.WARNING if level in ("warning", "error") else logging.DEBUG
            logger.log(log_level, "Blender[%s] %s: %s", active.run_id, level, message)

    def _on_plan(self, active: _ActiveRun, event: dict[str, Any]) -> None:
        planned = event.get("steps")
        if not isinstance(planned, list):
            return

        def _plan(run: TuningRun) -> None:
            existing = {s.id: s for s in run.steps}
            steps: list[TuningStep] = []
            for item in planned:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    continue
                step_id = item["id"]
                label = item.get("label") if isinstance(item.get("label"), str) else None
                current = existing.get(step_id) or TuningStep(id=step_id, label=label or step_id)
                if label:
                    current.label = label
                steps.append(current)
            if steps:
                run.steps = steps
                run.progress.step_count = len(steps)

        active.reporter.mutate(_plan, publish=True)

    def _on_step(self, active: _ActiveRun, event: dict[str, Any]) -> None:
        step_id = event.get("id")
        status = event.get("status")
        if not isinstance(step_id, str) or status not in STEP_STATUSES:
            return
        message = _text(event.get("message"))
        duration = _number(event.get("duration_s"))
        overall = _number(event.get("progress"))

        def _step(run: TuningRun) -> None:
            step = run.step(step_id)
            if step is None:
                step = TuningStep(id=step_id, label=step_id)
                run.steps.append(step)
                run.progress.step_count = len(run.steps)
            step.status = status  # type: ignore[assignment]
            if message is not None:
                step.message = message
            if duration is not None:
                step.duration_s = round(duration, 3)
            details = event.get("details")
            if isinstance(details, dict):
                step.details = to_jsonable(details)
            index = next(i for i, s in enumerate(run.steps) if s.id == step_id)
            if status == "running":
                run.progress.step_id = step_id
                run.progress.step_index = index + 1
                run.progress.message = message or step.label
            elif status in FINISHED_STEP_STATUSES and run.progress.step_id == step_id and message:
                run.progress.message = message
            if status == "warning" and message:
                run.warnings = list(dict.fromkeys([*run.warnings, f"{step.label}: {message}"]))
            percent = overall * 100.0 if overall is not None else _weighted_percent(run.steps, step_id, status, 0.0)
            run.progress.percent = _bounded(max(run.progress.percent, percent))

        active.reporter.mutate(_step, publish=True, persist=True)

    def _on_progress(self, active: _ActiveRun, event: dict[str, Any]) -> None:
        step_id = event.get("id") if isinstance(event.get("id"), str) else None
        overall = _number(event.get("progress"))
        fraction = _number(event.get("fraction"))
        message = _text(event.get("message"))

        def _progress(run: TuningRun) -> None:
            if overall is not None:
                percent = overall * 100.0
            elif step_id is not None:
                percent = _weighted_percent(run.steps, step_id, "running", fraction or 0.0)
            else:
                percent = run.progress.percent
            run.progress.percent = _bounded(max(run.progress.percent, percent))
            if message is not None:
                run.progress.message = message

        active.reporter.mutate(_progress, progress=True)

    def _on_error(self, active: _ActiveRun, event: dict[str, Any]) -> None:
        step_id = event.get("step") if isinstance(event.get("step"), str) else None
        message = _text(event.get("message")) or "Auto Mesh Tuner reported an error"

        def _error(run: TuningRun) -> None:
            if step_id is not None:
                step = run.step(step_id)
                if step is not None:
                    step.status = "failed"
                    step.message = message
            run.progress.message = message

        active.reporter.mutate(_error, publish=True)


# --------------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------------

_WEIGHTS: Final[dict[str, float]] = {step.id: step.weight for step in STEP_TABLE}


def _weighted_percent(steps: list[TuningStep], step_id: str, status: str, fraction: float) -> float:
    """Overall percent from the DESIGN step weights (fallback when an event carries no ``progress``)."""
    total = sum(_WEIGHTS.get(s.id, 0.0) for s in steps) or 1.0
    done = 0.0
    for step in steps:
        if step.id == step_id:
            weight = _WEIGHTS.get(step.id, 0.0)
            done += weight if status in FINISHED_STEP_STATUSES else weight * min(max(fraction, 0.0), 1.0)
            break
        done += _WEIGHTS.get(step.id, 0.0)
    return 100.0 * done / total


def _bounded(percent: float) -> float:
    return round(min(max(percent, 0.0), 99.9), 1)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number == number and abs(number) != float("inf") else None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:_MAX_MESSAGE] if text else None


def _plain_name(value: Any) -> str | None:
    """A bare output file name reported by the script (no folders, no traversal), else ``None``."""
    if not isinstance(value, str):
        return None
    name = value.strip()
    return name if _PLAIN_FILE_RE.match(name) and ".." not in name else None


def _first(errors: list[str]) -> str | None:
    for error in errors:
        text = error.strip()
        if text:
            return text.splitlines()[-1][:_MAX_MESSAGE] if text.startswith("Traceback") else text[:_MAX_MESSAGE]
    return None


def _script_error_message(errors: list[str]) -> str:
    for error in errors:
        if error.startswith("Traceback"):
            lines = [line for line in error.splitlines() if line.strip()]
            return f"Blender script error: {lines[-1].strip()}"[:_MAX_MESSAGE] if lines else "Blender script error"
    return (_first(errors) or "The Blender script raised an uncaught exception")[:_MAX_MESSAGE]
