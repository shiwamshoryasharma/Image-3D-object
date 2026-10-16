"""TEST-ONLY doubles for the part-aware run framework (never used by the application).

* :class:`FakeGpu` - a ``GpuTaskSubmitter`` with its own single ``gpu-worker`` thread and a settable
  ``running_job_id`` (the jobs-first gate input);
* :class:`FakeRegistry` - a ``RunProviderRegistry`` that records ``guard``/``ensure_vram``/``offload`` calls and the
  thread they ran on;
* :class:`ScriptedExecutor` - a ``RunExecutor`` whose phases run scripted actions inside ``ctx.step`` and record the
  thread of every call; :class:`BrokenExecutor` fails in its constructor (executor table ``error`` state);
* :class:`RecordingPublisher` - captures every published snapshot;
* :class:`WorkerRig` - a ``RunWorker`` wired to real ``RunStore`` instances under the test runtime directory;
* :class:`GpuProbe` / :class:`ProbedEngine` - record which callables ran on the GPU thread, in which order, and the
  highest number that ever ran at the same time (the ``submit_gpu_task`` seam tests).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fakes import FakeEngine

from config.settings import Settings
from core.events import EventBus
from core.exceptions import EngineUnavailableError
from inference.base import CancellationToken, GenerationOutput, GenerationRequest, ProgressReporter
from models.enums import JobStatus
from models.image_generation import ImageGenerationResult, ImageGenerationRun
from models.part_analysis import PartAnalysisRun
from models.runs import RUN_PREFIXES, RunKind, RunRecord, StepDef
from pipelines.runs.context import RunContext
from pipelines.runs.executor import RunPhase
from storage.run_layout import RunLayout
from storage.run_store import RunStore
from utils.ids import new_id
from utils.timeutil import utcnow
from workers.queue import JobQueue
from workers.run_worker import RunWorker

Action = Callable[[RunRecord, dict[str, Any], RunContext], None]


class FakeGpu:
    """Stands in for ``GenerationWorker``'s GPU seam (``submit_gpu_task`` on one ``gpu-worker`` thread)."""

    def __init__(self) -> None:
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpu-worker")
        self._inflight: set[concurrent.futures.Future[Any]] = set()
        self.running_job_id: str | None = None
        self.stopping = False
        self.submit_times: list[float] = []

    @property
    def accepting_gpu_tasks(self) -> bool:
        return not self.stopping

    @property
    def is_busy(self) -> bool:
        return any(not f.done() for f in self._inflight)

    def submit_gpu_task(self, fn: Callable[..., Any], /, *args: Any) -> asyncio.Future[Any]:
        if self.stopping:
            raise EngineUnavailableError("The server is shutting down")
        self.submit_times.append(time.monotonic())
        future = self._executor.submit(fn, *args)
        self._inflight.add(future)
        future.add_done_callback(self._inflight.discard)
        return asyncio.wrap_future(future)

    def occupy(self, release: threading.Event) -> concurrent.futures.Future[Any]:
        """Queue a raw blocker on the GPU thread (an image-to-3D job seen from the run worker's side)."""
        future = self._executor.submit(release.wait, 30.0)
        self._inflight.add(future)
        future.add_done_callback(self._inflight.discard)
        return future

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)


class FakeRegistry:
    """Records the registry calls of GPU phases: ``(event, thread_name, model_ids)``."""

    def __init__(self, *, vram_error: BaseException | None = None) -> None:
        self.events: list[tuple[str, str, tuple[str, ...]]] = []
        self.vram_error = vram_error
        self._lock = threading.Lock()

    def _record(self, event: str, model_ids: Sequence[str] = ()) -> None:
        with self._lock:
            self.events.append((event, threading.current_thread().name, tuple(model_ids)))

    @contextlib.contextmanager
    def guard(self) -> Iterator[None]:
        self._record("guard_enter")
        try:
            yield
        finally:
            self._record("guard_exit")

    def ensure_vram(self, model_ids: Sequence[str]) -> None:
        self._record("ensure_vram", model_ids)
        if self.vram_error is not None:
            raise self.vram_error

    def offload(self, model_ids: Sequence[str]) -> None:
        self._record("offload", model_ids)

    def specs(self, kind: Any = None) -> list[Any]:
        return [SimpleNamespace(id="fake_model", revision="catalog-rev", license=SimpleNamespace(id="apache-2.0"))]

    def status(self, model_id: str) -> Any:
        return SimpleNamespace(model_id=model_id, revision="installed-rev")

    def names(self) -> list[str]:
        with self._lock:
            return [event for event, _thread, _ids in self.events]


@dataclass
class PhaseScript:
    name: str
    device: str
    steps: tuple[str, ...]
    action: Action | None = None
    models: tuple[str, ...] = ()


@dataclass
class ScriptedExecutor:
    """A run executor driven by :class:`PhaseScript` entries (one weight-1 step per listed step id)."""

    kind: RunKind
    scripts: list[PhaseScript]
    skip: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    work_dirs: list[Path] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.prefix = RUN_PREFIXES[self.kind]
        self._lock = threading.Lock()

    def _call(self, name: str) -> None:
        with self._lock:
            self.calls.append((name, threading.current_thread().name))

    def call_names(self) -> list[str]:
        with self._lock:
            return [name for name, _thread in self.calls]

    def thread_of(self, name: str) -> str:
        with self._lock:
            return next(thread for call, thread in self.calls if call == name)

    def plan(self, run: RunRecord) -> tuple[StepDef, ...]:
        return tuple(
            StepDef(step, step.replace("_", " ").title(), 1.0, script.device, self.skip.get(step))  # type: ignore[arg-type]
            for script in self.scripts
            for step in script.steps
        )

    def phases(self, run: RunRecord) -> tuple[RunPhase[Any, Any], ...]:
        return tuple(
            RunPhase(script.name, script.device, script.steps, self._phase_fn(script), script.models)  # type: ignore[arg-type]
            for script in self.scripts
        )

    def _phase_fn(self, script: PhaseScript) -> Action:
        def run_phase(run: RunRecord, state: dict[str, Any], ctx: RunContext) -> None:
            self._call(script.name)
            for step in script.steps:
                if step in self.skip:
                    continue
                with ctx.step(step):
                    if script.action is not None:
                        script.action(run, state, ctx)

        return run_phase

    def prepare(self, run: RunRecord, work_dir: Path) -> dict[str, Any]:
        self._call("prepare")
        self.work_dirs.append(work_dir)
        (work_dir / "input.txt").write_text(run.id, encoding="utf-8")
        return {"work_dir": work_dir}

    def finalize(self, run: RunRecord, state: dict[str, Any], ctx: RunContext) -> RunRecord:
        self._call("finalize")
        if isinstance(run, ImageGenerationRun):
            return run.model_copy(update={"result": ImageGenerationResult(candidates=[], timings={"total": 0.0})})
        return run


class BrokenExecutor:
    """An executor class whose constructor fails (the executor table must report ``error``, never crash)."""

    def __init__(self, deps: Any) -> None:
        raise RuntimeError("broken executor for tests")


class RecordingPublisher:
    """Captures ``(snapshot, droppable)`` for every publish (thread-safe)."""

    def __init__(self) -> None:
        self.events: list[tuple[RunRecord, bool]] = []
        self._lock = threading.Lock()

    def publish(self, run: RunRecord, *, droppable: bool = False) -> None:
        with self._lock:
            self.events.append((run.model_copy(deep=True), droppable))

    def for_run(self, run_id: str) -> list[tuple[RunRecord, bool]]:
        with self._lock:
            return [(run, droppable) for run, droppable in self.events if run.id == run_id]


class WorkerRig:
    """A ``RunWorker`` over real stores (image generations + part analyses) and the doubles above.

    ``registry=None`` (the default) installs a :class:`FakeRegistry`; pass ``no_registry=True`` for a build without
    the model registry.
    """

    def __init__(
        self,
        settings: Settings,
        executors: dict[RunKind, Any],
        *,
        registry: Any = None,
        no_registry: bool = False,
        gpu: FakeGpu | None = None,
        publish_interval: float | None = None,
    ) -> None:
        self.settings = settings
        self.layout = RunLayout(settings.paths)
        self.bus = EventBus()
        self.run_queue = JobQueue(settings.run_queue_max_size)
        self.job_queue = JobQueue(16)
        self.gpu = gpu or FakeGpu()
        self.registry = None if no_registry else (registry if registry is not None else FakeRegistry())
        self.stores: dict[RunKind, RunStore[Any]] = {
            "image_generation": RunStore(self.layout, kind="image_generation", record_type=ImageGenerationRun),
            "part_analysis": RunStore(self.layout, kind="part_analysis", record_type=PartAnalysisRun),
        }
        self.publisher = RecordingPublisher()
        self.queue_changes = 0
        extra: dict[str, Any] = {}
        if publish_interval is not None:
            extra["publish_interval"] = publish_interval
        self.worker = RunWorker(
            settings=settings,
            gpu=self.gpu,
            queue=self.run_queue,
            job_queue=self.job_queue,
            executors=executors,
            stores=self.stores,
            publishers={kind: self.publisher for kind in self.stores},  # type: ignore[misc]
            registry=self.registry,
            bus=self.bus,
            paths=settings.paths,
            on_queue_changed=self._queue_changed,
            gate_poll_interval=0.05,
            **extra,
        )

    def _queue_changed(self) -> None:
        self.queue_changes += 1

    async def start(self) -> None:
        self.bus.bind_loop(asyncio.get_running_loop())
        self.settings.paths.ensure()
        self.worker.start()

    async def stop(self, timeout: float = 5.0) -> None:
        await self.worker.stop(timeout)

    def make_job(self, job_id: str = "job_aaaaaaaaaaaa") -> str:
        job_dir = self.settings.paths.job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "job.json").write_text("{}", encoding="utf-8")
        return job_id

    @staticmethod
    def new_ig() -> ImageGenerationRun:
        return ImageGenerationRun(id=new_id("ig"), created_at=utcnow(), options={"prompt": "test"})

    @staticmethod
    def new_pa(job_id: str) -> PartAnalysisRun:
        return PartAnalysisRun(id=new_id("pa"), job_id=job_id, created_at=utcnow(), options={})

    def current(self, run: RunRecord) -> RunRecord | None:
        live = self.worker.live_snapshot(run.id)
        return live if live is not None else self.stores[run.kind].get(run.id)

    async def wait_status(self, run: RunRecord, statuses: set[JobStatus], timeout: float = 10.0) -> RunRecord:
        deadline = time.monotonic() + timeout
        while True:
            current = self.current(run)
            if current is not None and current.status in statuses and self.worker.running_id != run.id:
                return current
            if time.monotonic() > deadline:
                raise AssertionError(f"Timed out waiting for {run.id} in {statuses}; last={current!r}")
            await asyncio.sleep(0.02)

    async def wait_until(self, predicate: Callable[[], Any], timeout: float = 10.0, message: str = "") -> Any:
        deadline = time.monotonic() + timeout
        while True:
            value = predicate()
            if value:
                return value
            if time.monotonic() > deadline:
                raise AssertionError(f"Timed out waiting for {message or predicate}")
            await asyncio.sleep(0.02)


def blocking_action(started: threading.Event, release: threading.Event, *, honour_token: bool = True) -> Action:
    """An action that signals ``started`` and blocks until ``release`` (or the token, when honoured)."""

    def action(run: RunRecord, state: dict[str, Any], ctx: RunContext) -> None:
        started.set()
        while not release.wait(0.01):
            if honour_token:
                ctx.cancel.raise_if_cancelled()

    return action


# --------------------------------------------------------------------------------------------------
# GPU seam probes
# --------------------------------------------------------------------------------------------------


class GpuProbe:
    """Lock-guarded counters: the order of GPU callables and the highest concurrency ever observed."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = 0
        self.max_active = 0
        self.order: list[str] = []
        self.threads: list[str] = []

    @contextlib.contextmanager
    def enter(self, label: str) -> Iterator[None]:
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
            self.order.append(label)
            self.threads.append(threading.current_thread().name)
        try:
            time.sleep(0.02)  # widen the window an overlap would need
            yield
        finally:
            with self._lock:
                self._active -= 1

    def task(self, label: str, *, hold: float = 0.05) -> Callable[[], str]:
        """A GPU callable that records itself and returns its thread name."""

        def run() -> str:
            with self.enter(label):
                time.sleep(hold)
            return threading.current_thread().name

        return run

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self.order)


@dataclass(eq=False)
class ProbedEngine(FakeEngine):
    """A :class:`FakeEngine` whose generations are recorded by a :class:`GpuProbe` as ``job:<job_id>``."""

    probe: GpuProbe = field(default_factory=GpuProbe)

    def generate(
        self, request: GenerationRequest, reporter: ProgressReporter, cancel: CancellationToken
    ) -> GenerationOutput:
        with self.probe.enter(f"job:{request.job_id}"):
            return super().generate(request, reporter, cancel)


