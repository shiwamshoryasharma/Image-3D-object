"""Run framework (part-aware DESIGN.md sections 4.3, 4.8, 4.11, 6.6, 7.1): stores, reporter, worker, publishers.

Covers the S1 row of section 10.2: ``RunStore`` atomic save/load/``recover_interrupted`` and the refusal to recreate
a deleted job folder; reporter throttling and monotonic percent; CPU/GPU phases on the right threads inside
``registry.guard()``; cancel while queued / in a GPU phase / in a CPU phase; shutdown; error classification; the
active-time watchdog; ``work_dir`` kept while a GPU callable that ignores its token still runs; the jobs-first gate.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fakes_runs import (
    FakeRegistry,
    PhaseScript,
    ScriptedExecutor,
    WorkerRig,
    blocking_action,
)

from core.events import Event, EventBus
from core.exceptions import AppError, EngineUnavailableError
from core.feature_errors import InsufficientVramError, JobDeletedError, ServiceNotImplementedError
from models.enums import JobStatus
from models.image_generation import ImageGenerationRun
from models.part_analysis import PartAnalysisRun
from models.runs import (
    GPU_DEFER_TIMEOUT_WARNING,
    RUN_FILE,
    RunStep,
    StepDef,
    run_kind_for_id,
    steps_from_plan,
)
from pipelines.runs.executor import DottedPathMissing, ExecutorDeps, ExecutorTable, import_dotted
from pipelines.runs.reporter import RUNNING_PERCENT_CAP, RunReporter, compute_run_percent
from schemas.events import EventType
from services.run_events import FeaturesEventPublisher, RunEventPublisher, default_job_id
from storage.run_layout import RunLayout
from storage.run_store import RunStore
from utils.ids import new_id
from utils.timeutil import utcnow
from workers.run_worker import CPU_THREAD_NAME, MESSAGE_GPU_QUEUED

TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}


def run_async(scenario: Callable[[], Coroutine[Any, Any, Any]], timeout: float = 60.0) -> Any:
    return asyncio.run(asyncio.wait_for(scenario(), timeout))


def _executor(kind: str = "image_generation", *scripts: PhaseScript, **kwargs: Any) -> ScriptedExecutor:
    return ScriptedExecutor(kind, list(scripts) or [PhaseScript("work", "cpu", ("work",))], **kwargs)  # type: ignore[arg-type]


async def _shutdown(rig: WorkerRig, timeout: float = 5.0) -> None:
    await rig.stop(timeout)
    await asyncio.to_thread(rig.gpu.shutdown, wait=True)


# ==================================================================================================
# RunStore
# ==================================================================================================


def test_run_store_atomic_save_and_load_roundtrip(make_settings):
    settings = make_settings()
    layout = RunLayout(settings.paths)
    store: RunStore[ImageGenerationRun] = RunStore(layout, kind="image_generation", record_type=ImageGenerationRun)
    run = ImageGenerationRun(id=new_id("ig"), created_at=utcnow(), options={"prompt": "a cat"})
    assert store.save(run) is True

    run_dir = layout.imagegen_dir(run.id)
    assert (run_dir / RUN_FILE).is_file()
    assert [p.name for p in run_dir.iterdir()] == [RUN_FILE]  # no temp file left behind
    on_disk = json.loads((run_dir / RUN_FILE).read_text("utf-8"))
    assert on_disk["id"] == run.id and on_disk["kind"] == "image_generation" and on_disk["status"] == "queued"

    copy = store.get(run.id)
    assert copy == run
    copy.options["prompt"] = "mutated"
    assert store.get(run.id).options["prompt"] == "a cat"  # hands out deep copies

    # Unreadable and mismatched folders are skipped; foreign names are ignored.
    (layout.imagegen_root / "ig_000000000bad").mkdir()
    (layout.imagegen_root / "ig_000000000bad" / RUN_FILE).write_text("{not json", encoding="utf-8")
    other = ImageGenerationRun(id=new_id("ig"), created_at=utcnow())
    (layout.imagegen_root / "ig_00000000beef").mkdir()
    (layout.imagegen_root / "ig_00000000beef" / RUN_FILE).write_text(
        json.dumps(other.model_dump(mode="json")), encoding="utf-8"
    )
    (layout.imagegen_root / "not-a-run").mkdir()

    fresh: RunStore[ImageGenerationRun] = RunStore(layout, kind="image_generation", record_type=ImageGenerationRun)
    assert fresh.load() == 1
    assert fresh.get(run.id) == run
    assert fresh.get("ig_000000000bad") is None
    assert fresh.get("../etc") is None and fresh.get("job_aaaaaaaaaaaa") is None


def test_run_store_lists_newest_first_and_terminal_oldest_first(make_settings):
    layout = RunLayout(make_settings().paths)
    store: RunStore[ImageGenerationRun] = RunStore(layout, kind="image_generation", record_type=ImageGenerationRun)
    runs = []
    for index, status in enumerate((JobStatus.COMPLETED, JobStatus.QUEUED, JobStatus.FAILED)):
        run = ImageGenerationRun(id=f"ig_00000000000{index}", created_at=utcnow(), status=status)
        store.save(run)
        runs.append(run)
        time.sleep(0.002)
    assert [r.id for r in store.list_runs()] == [r.id for r in reversed(runs)]
    assert [r.id for r in store.list_runs(limit=1)] == [runs[-1].id]
    assert [r.id for r in store.list_active()] == [runs[1].id]
    assert [r.id for r in store.list_terminal()] == [runs[0].id, runs[2].id]
    assert [r.id for r in store.list_runs(status=JobStatus.FAILED)] == [runs[2].id]


def test_run_store_recover_interrupted(make_settings):
    layout = RunLayout(make_settings().paths)
    store: RunStore[ImageGenerationRun] = RunStore(layout, kind="image_generation", record_type=ImageGenerationRun)
    queued = ImageGenerationRun(id="ig_000000000001", created_at=utcnow())
    running = ImageGenerationRun(
        id="ig_000000000002",
        created_at=utcnow(),
        status=JobStatus.RUNNING,
        started_at=utcnow(),
        steps=[RunStep(id="load", label="Load", status="done"), RunStep(id="generate", label="Gen", status="running")],
    )
    done = ImageGenerationRun(id="ig_000000000003", created_at=utcnow(), status=JobStatus.COMPLETED)
    for run in (queued, running, done):
        store.save(run)

    fresh: RunStore[ImageGenerationRun] = RunStore(layout, kind="image_generation", record_type=ImageGenerationRun)
    fresh.load()
    recovered = fresh.recover_interrupted()
    assert {r.id for r in recovered} == {queued.id, running.id}
    for run_id in (queued.id, running.id):
        run = fresh.get(run_id)
        assert run.status is JobStatus.FAILED and run.finished_at is not None
        assert run.error.code == "interrupted"
        on_disk = json.loads((layout.imagegen_dir(run_id) / RUN_FILE).read_text("utf-8"))
        assert on_disk["status"] == "failed" and on_disk["error"]["code"] == "interrupted"
    assert [s.status for s in fresh.get(running.id).steps] == ["done", "failed"]
    assert fresh.get(done.id).status is JobStatus.COMPLETED


@pytest.mark.parametrize("index_says_exists", [False, True], ids=["pre-check", "mkdir-parents-false"])
def test_run_store_never_recreates_a_deleted_job_folder(make_settings, index_says_exists):
    settings = make_settings()
    layout = RunLayout(settings.paths)
    job_id = "job_aaaaaaaaaaaa"
    job_dir = settings.paths.job_dir(job_id)
    job_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text("{}", encoding="utf-8")
    # With index_says_exists the store's job check is stale (True): only mkdir(parents=False) can catch the delete.
    job_exists = (lambda _job_id: True) if index_says_exists else None
    store: RunStore[PartAnalysisRun] = RunStore(
        layout, kind="part_analysis", record_type=PartAnalysisRun, job_exists=job_exists
    )
    run = PartAnalysisRun(id="pa_000000000001", job_id=job_id, created_at=utcnow())
    assert store.save(run)
    assert (layout.part_analysis_dir(job_id, run.id) / RUN_FILE).is_file()
    assert store.list_for_job(job_id)[0].id == run.id

    shutil.rmtree(job_dir)  # retention / delete removed the job in between
    run.status = JobStatus.RUNNING
    with pytest.raises(JobDeletedError) as exc_info:
        store.save(run)
    assert exc_info.value.code == "job_deleted"
    assert exc_info.value.details == {"job_id": job_id}
    assert not job_dir.exists(), "RunStore must never recreate jobs/<job>/"


def test_run_store_delete_tombstones_the_run_and_hides_runs_of_deleted_jobs(make_settings):
    settings = make_settings()
    layout = RunLayout(settings.paths)
    rig_job = settings.paths.job_dir("job_aaaaaaaaaaaa")
    rig_job.mkdir(parents=True)
    (rig_job / "job.json").write_text("{}", encoding="utf-8")
    store: RunStore[PartAnalysisRun] = RunStore(layout, kind="part_analysis", record_type=PartAnalysisRun)
    run = PartAnalysisRun(id="pa_000000000001", job_id="job_aaaaaaaaaaaa", created_at=utcnow())
    store.save(run)
    assert store.delete(run.id) is True
    assert not layout.part_analysis_dir("job_aaaaaaaaaaaa", run.id).exists()
    assert store.save(run) is False  # a deleted run is never written again
    assert not layout.part_analysis_dir("job_aaaaaaaaaaaa", run.id).exists()

    other = PartAnalysisRun(id="pa_000000000002", job_id="job_aaaaaaaaaaaa", created_at=utcnow())
    store.save(other)
    (rig_job / "job.json").unlink()  # the job vanished without hooks
    assert store.get(other.id) is None and store.list_runs() == []


def test_run_ids_and_initial_steps():
    assert run_kind_for_id("ig_0123456789ab") == "image_generation"
    assert run_kind_for_id("sg_0123456789ab") == "segmentation"
    assert run_kind_for_id("pa_0123456789ab") == "part_analysis"
    assert run_kind_for_id("job_0123456789ab") is None and run_kind_for_id("wf_0123456789ab") is None
    assert run_kind_for_id("pa_../../x") is None
    steps = steps_from_plan([StepDef("a", "A", 1, "cpu"), StepDef("b", "B", 1, "gpu", "Not needed")])
    assert [(s.id, s.status, s.message, s.progress) for s in steps] == [
        ("a", "pending", None, None),
        ("b", "skipped", "Not needed", 1.0),
    ]


# ==================================================================================================
# RunReporter
# ==================================================================================================


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _reporter(clock: _Clock) -> tuple[RunReporter[Any], list[tuple[Any, bool]], list[Any], list[tuple[float, Any]]]:
    published: list[tuple[Any, bool]] = []
    persisted: list[Any] = []
    scheduled: list[tuple[float, Any]] = []
    run = ImageGenerationRun(id="ig_000000000001", created_at=utcnow())
    reporter: RunReporter[Any] = RunReporter(
        run,
        publish=lambda snapshot, droppable: published.append((snapshot, droppable)),
        persist=persisted.append,
        schedule_later=lambda delay, callback: scheduled.append((delay, callback)),
        clock=clock,
    )
    return reporter, published, persisted, scheduled


def test_reporter_throttles_progress_and_never_drops_transitions():
    clock = _Clock()
    reporter, published, _persisted, scheduled = _reporter(clock)
    reporter.set_plan((StepDef("load", "Load", 1.0, "cpu"), StepDef("generate", "Generate", 3.0, "gpu")))
    reporter.start_step("load")
    count_after_start = len(published)
    for index in range(50):  # 50 updates within the same 0.2 s window
        reporter.step_progress("load", index / 50)
    assert len(published) == count_after_start, "progress inside the 0.2 s window must be held back"
    flushes = [(delay, cb) for delay, cb in scheduled if getattr(cb, "__name__", "") == "_trailing_flush"]
    assert len(flushes) == 1, "exactly one trailing flush is scheduled"

    clock.now = 0.25
    delay, flush = flushes[0]
    assert 0 < delay <= 0.2
    flush()
    snapshot, droppable = published[-1]
    assert droppable is True and snapshot.step("load").progress == pytest.approx(49 / 50)

    flush()  # nothing new: a second flush publishes nothing
    assert len(published) == count_after_start + 1

    reporter.finish_step("load", "done")
    reporter.warn("t2i_touches_border", "touches", "generate")
    reporter.start_step("generate")
    transitions = [(s, d) for s, d in published[count_after_start + 1 :]]
    assert all(droppable is False for _s, droppable in transitions)
    assert [s.step("load").status for s, _d in transitions][0] == "done"
    assert transitions[-1][0].progress.step_id == "generate"
    reporter.finish_step("generate", "warning")
    assert published[-1][0].step("generate").status == "warning"


def test_reporter_percent_is_monotonic_capped_and_exactly_100_on_completion():
    clock = _Clock()
    reporter, published, _persisted, _scheduled = _reporter(clock)
    reporter.set_plan((StepDef("a", "A", 1.0, "cpu"), StepDef("b", "B", 3.0, "gpu"), StepDef("c", "C", 0, "cpu", "n/a")))
    reporter.start_step("a")
    clock.now = 1.0
    reporter.step_progress("a", 0.8)
    high = reporter.snapshot().progress.percent
    assert high == pytest.approx(20.0)
    clock.now = 2.0
    reporter.step_progress("a", 0.2)  # a step fraction going down never lowers the run percent
    assert reporter.snapshot().progress.percent == high
    reporter.finish_step("a", "done")
    reporter.start_step("b")
    reporter.finish_step("b", "done")
    assert reporter.snapshot().progress.percent == RUNNING_PERCENT_CAP  # held below 100 until completed

    def complete(run: Any) -> None:
        run.status = JobStatus.COMPLETED

    final = reporter.mutate(complete, publish=True, persist=True)
    assert final.progress.percent == 100.0
    percents = [snapshot.progress.percent for snapshot, _ in published]
    assert percents == sorted(percents)


def test_reporter_persists_at_most_every_two_seconds():
    clock = _Clock()
    reporter, _published, persisted, scheduled = _reporter(clock)
    reporter.set_plan((StepDef("a", "A", 1.0, "cpu"),))
    reporter.start_step("a")
    for index in range(20):
        clock.now = index * 0.05  # 1 s of updates
        reporter.step_progress("a", index / 20)
    assert persisted == [], "no write inside the first 2 s"
    trailing = [cb for _delay, cb in scheduled if getattr(cb, "__name__", "") == "_trailing_persist"]
    assert len(trailing) == 1

    clock.now = 2.1
    trailing[0]()
    assert len(persisted) == 1 and persisted[0].step("a").progress == pytest.approx(19 / 20)

    def mark(run: Any) -> None:
        run.status = JobStatus.RUNNING

    reporter.mutate(mark, persist=True)  # status changes are written immediately
    assert len(persisted) == 2 and persisted[-1].status is JobStatus.RUNNING


def test_compute_run_percent_formula():
    weights = {"a": 1.0, "b": 2.0, "c": 1.0}
    steps = [
        RunStep(id="a", label="A", status="skipped"),
        RunStep(id="b", label="B", status="running", progress=0.5),
        RunStep(id="c", label="C", status="pending"),
    ]
    assert compute_run_percent(weights, steps) == pytest.approx(50.0)
    assert compute_run_percent({}, steps) == 0.0


# ==================================================================================================
# RunWorker
# ==================================================================================================


def test_phases_alternate_cpu_and_gpu_on_the_right_threads(make_settings):
    def use_model(_run: Any, _state: Any, ctx: Any) -> None:
        ctx.use_model("fake_model")

    executor = _executor(
        "image_generation",
        PhaseScript("detect", "gpu", ("detect",), use_model, models=("fake_model",)),
        PhaseScript("assign", "cpu", ("assign",)),
        PhaseScript("render", "gpu", ("render",), models=("fake_model", "other")),
    )
    rig = WorkerRig(make_settings(), {"image_generation": executor})

    async def scenario() -> Any:
        await rig.start()
        run = rig.worker.enqueue(rig.new_ig())
        final = await rig.wait_status(run, TERMINAL)
        await _shutdown(rig)
        return final

    final = run_async(scenario)
    assert final.status is JobStatus.COMPLETED, final.error
    assert final.progress.percent == 100.0 and final.progress.message == "Done"
    assert [s.status for s in final.steps] == ["done", "done", "done"]
    assert final.result is not None and final.result.timings == {"total": 0.0}
    assert [(m.id, m.revision, m.license_id) for m in final.models] == [("fake_model", "installed-rev", "apache-2.0")]

    assert executor.call_names() == ["prepare", "detect", "assign", "render", "finalize"]
    assert executor.thread_of("detect").startswith("gpu-worker")
    assert executor.thread_of("render").startswith("gpu-worker")
    assert executor.thread_of("assign").startswith(CPU_THREAD_NAME)
    for name in ("prepare", "finalize"):  # worker threads, never the GPU or parts-cpu thread
        assert not executor.thread_of(name).startswith(("gpu-worker", CPU_THREAD_NAME))

    events = rig.registry.events
    assert [e for e, _t, _ids in events] == ["guard_enter", "ensure_vram", "offload", "guard_exit"] * 2
    assert all(thread.startswith("gpu-worker") for _e, thread, _ids in events)
    assert [ids for e, _t, ids in events if e == "ensure_vram"] == [("fake_model",), ("fake_model", "other")]
    assert [ids for e, _t, ids in events if e == "offload"] == [("fake_model",), ("fake_model", "other")]

    assert not executor.work_dirs[0].exists(), "work_dir is removed after the run"
    on_disk = json.loads((rig.layout.imagegen_dir(final.id) / RUN_FILE).read_text("utf-8"))
    assert on_disk["status"] == "completed" and on_disk["progress"]["percent"] == 100.0
    published = rig.publisher.for_run(final.id)
    assert published[0][0].status is JobStatus.QUEUED and published[-1][0].status is JobStatus.COMPLETED
    assert published[-1][1] is False


def test_plan_steps_that_do_not_apply_are_pre_marked_skipped(make_settings):
    executor = _executor(
        "image_generation",
        PhaseScript("load", "cpu", ("load_model",)),
        PhaseScript("generate", "cpu", ("generate",)),
        skip={"load_model": "Model already in memory"},
    )
    rig = WorkerRig(make_settings(), {"image_generation": executor})

    async def scenario() -> Any:
        await rig.start()
        run = rig.worker.enqueue(rig.new_ig())
        final = await rig.wait_status(run, TERMINAL)
        await _shutdown(rig)
        return final

    final = run_async(scenario)
    assert final.status is JobStatus.COMPLETED
    load = final.step("load_model")
    assert (load.status, load.message, load.progress, load.duration_s) == (
        "skipped",
        "Model already in memory",
        1.0,
        0.0,
    )


def test_cancel_while_queued(make_settings):
    started, release = threading.Event(), threading.Event()
    executor = _executor("image_generation", PhaseScript("work", "cpu", ("work",), blocking_action(started, release)))
    rig = WorkerRig(make_settings(), {"image_generation": executor})

    async def scenario() -> tuple[Any, Any, Any]:
        await rig.start()
        first = rig.worker.enqueue(rig.new_ig())
        second = rig.worker.enqueue(rig.new_ig())
        await asyncio.to_thread(started.wait, 5)
        cancelled = rig.worker.cancel_queued(second.id)
        assert rig.worker.cancel_queued(second.id) is None  # no longer queued
        release.set()
        first_final = await rig.wait_status(first, TERMINAL)
        await _shutdown(rig)
        return cancelled, first_final, rig.stores["image_generation"].get(second.id)

    cancelled, first_final, second_final = run_async(scenario)
    assert cancelled.status is JobStatus.CANCELLED and cancelled.cancel_requested
    assert second_final.status is JobStatus.CANCELLED and second_final.finished_at is not None
    assert first_final.status is JobStatus.COMPLETED
    assert executor.call_names().count("prepare") == 1, "the cancelled run never started"


@pytest.mark.parametrize("device", ["gpu", "cpu"])
def test_cancel_during_a_phase(make_settings, device):
    started, release = threading.Event(), threading.Event()
    executor = _executor(
        "image_generation",
        PhaseScript("work", device, ("work",), blocking_action(started, release), models=("fake_model",)),
        PhaseScript("after", "cpu", ("after",)),
    )
    rig = WorkerRig(make_settings(), {"image_generation": executor})

    async def scenario() -> Any:
        await rig.start()
        run = rig.worker.enqueue(rig.new_ig())
        await asyncio.to_thread(started.wait, 5)
        snapshot = rig.worker.cancel_running(run.id)
        assert snapshot is not None and snapshot.cancel_requested
        assert rig.worker.cancel_running("ig_000000000000") is None
        final = await rig.wait_status(run, TERMINAL)
        await _shutdown(rig)
        return final

    final = run_async(scenario)
    release.set()
    assert final.status is JobStatus.CANCELLED and final.error is None
    assert final.step("work").status == "cancelled"
    assert final.step("after").status == "pending"
    assert "after" not in executor.call_names()
    assert not executor.work_dirs[0].exists()
    if device == "gpu":
        assert rig.registry.names() == ["guard_enter", "ensure_vram", "offload", "guard_exit"]


def test_shutdown_interrupts_the_running_run_and_fails_queued_runs(make_settings):
    started, release = threading.Event(), threading.Event()
    executor = _executor("image_generation", PhaseScript("work", "gpu", ("work",), blocking_action(started, release)))
    rig = WorkerRig(make_settings(), {"image_generation": executor})

    async def scenario() -> tuple[Any, Any]:
        await rig.start()
        running = rig.worker.enqueue(rig.new_ig())
        queued = rig.worker.enqueue(rig.new_ig())
        await asyncio.to_thread(started.wait, 5)
        await rig.stop(timeout=5)
        with pytest.raises(EngineUnavailableError):
            rig.worker.enqueue(rig.new_ig())
        await asyncio.to_thread(rig.gpu.shutdown, wait=True)
        store = rig.stores["image_generation"]
        return store.get(running.id), store.get(queued.id)

    running, queued = run_async(scenario)
    release.set()
    for run in (running, queued):
        assert run.status is JobStatus.FAILED and run.error.code == "interrupted" and run.finished_at is not None
    assert "before this run started" in queued.error.message
    assert running.step("work").status == "cancelled"  # the step saw the shutdown cancel; the run is interrupted


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB"), "out_of_memory"),
        (ValueError("boom"), "run_error"),
        (AppError("segmentation exploded", code="segmentation_failed", status_code=500), "segmentation_failed"),
    ],
    ids=["oom", "unexpected", "app-error"],
)
def test_errors_map_to_run_error_codes(make_settings, error, code):
    def fail(_run: Any, _state: Any, _ctx: Any) -> None:
        raise error

    executor = _executor("image_generation", PhaseScript("work", "gpu", ("work",), fail, models=("fake_model",)))
    rig = WorkerRig(make_settings(), {"image_generation": executor})

    async def scenario() -> Any:
        await rig.start()
        run = rig.worker.enqueue(rig.new_ig())
        final = await rig.wait_status(run, TERMINAL)
        await _shutdown(rig)
        return final

    final = run_async(scenario)
    assert final.status is JobStatus.FAILED and final.error.code == code
    if code == "run_error":
        assert final.error.message == "ValueError: boom"
    assert final.step("work").status == "failed"
    assert rig.registry.names() == ["guard_enter", "ensure_vram", "offload", "guard_exit"]  # offload in finally


def test_insufficient_vram_fails_the_run_before_the_phase_runs(make_settings):
    ran = threading.Event()
    registry = FakeRegistry(vram_error=InsufficientVramError("No VRAM", model_ids=["m"], free_mb=1, required_mb=2))
    executor = _executor(
        "image_generation", PhaseScript("work", "gpu", ("work",), lambda *_: ran.set(), models=("m",))
    )
    rig = WorkerRig(make_settings(), {"image_generation": executor}, registry=registry)

    async def scenario() -> Any:
        await rig.start()
        run = rig.worker.enqueue(rig.new_ig())
        final = await rig.wait_status(run, TERMINAL)
        await _shutdown(rig)
        return final

    final = run_async(scenario)
    assert final.status is JobStatus.FAILED and final.error.code == "insufficient_vram"
    assert final.error.details == {"model_ids": ["m"], "free_mb": 1.0, "required_mb": 2.0}
    assert not ran.is_set()
    assert registry.names() == ["guard_enter", "ensure_vram", "offload", "guard_exit"]


def test_missing_executor_or_registry_fails_honestly(make_settings):
    gpu_executor = _executor("image_generation", PhaseScript("work", "gpu", ("work",)))
    no_executor = WorkerRig(make_settings(), {})
    no_registry = WorkerRig(make_settings(), {"image_generation": gpu_executor}, no_registry=True)

    async def scenario() -> tuple[Any, Any]:
        results = []
        for rig in (no_executor, no_registry):
            await rig.start()
            run = rig.worker.enqueue(rig.new_ig())
            results.append(await rig.wait_status(run, TERMINAL))
            await _shutdown(rig)
        return results[0], results[1]

    missing_executor, missing_registry = run_async(scenario)
    assert missing_executor.status is JobStatus.FAILED and missing_executor.error.code == "run_error"
    assert "not available in this build" in missing_executor.error.message
    assert missing_registry.status is JobStatus.FAILED and missing_registry.error.code == "run_error"
    assert "registry" in missing_registry.error.message


def test_failed_part_analysis_keeps_its_log_next_to_run_json(make_settings):
    def fail(_run: Any, _state: Any, ctx: Any) -> None:
        ctx.log_path.write_text("partition exploded\n", encoding="utf-8")
        raise ValueError("partition exploded")

    executor = _executor("part_analysis", PhaseScript("assign", "cpu", ("assign",), fail))
    rig = WorkerRig(make_settings(), {"part_analysis": executor})

    async def scenario() -> Any:
        await rig.start()
        job_id = rig.make_job()
        run = rig.worker.enqueue(rig.new_pa(job_id))
        final = await rig.wait_status(run, TERMINAL)
        await _shutdown(rig)
        return final

    final = run_async(scenario)
    assert final.status is JobStatus.FAILED and final.error.code == "run_error"
    run_dir = rig.layout.part_analysis_dir(final.job_id, final.id)
    assert (run_dir / "log.txt").read_text("utf-8") == "partition exploded\n"
    assert (run_dir / RUN_FILE).is_file()
    assert not executor.work_dirs[0].exists()


def test_run_progress_events_are_throttled_and_transitions_are_never_dropped(make_settings):
    def chatty(_run: Any, _state: Any, ctx: Any) -> None:
        deadline = time.monotonic() + 0.6
        index = 0
        while time.monotonic() < deadline:
            index += 1
            ctx.progress("work", min(index / 600, 1.0), f"tick {index}")
            time.sleep(0.001)

    executor = _executor("image_generation", PhaseScript("work", "cpu", ("work",), chatty))
    rig = WorkerRig(make_settings(), {"image_generation": executor})

    async def scenario() -> Any:
        await rig.start()
        run = rig.worker.enqueue(rig.new_ig())
        final = await rig.wait_status(run, TERMINAL)
        await asyncio.sleep(0.3)  # let a trailing flush (if any) land
        await _shutdown(rig)
        return final

    final = run_async(scenario)
    events = rig.publisher.for_run(final.id)
    droppable = [snapshot for snapshot, is_droppable in events if is_droppable]
    assert 1 <= len(droppable) <= 6, f"{len(droppable)} progress events in 0.6 s (limit 5/s)"
    statuses = [snapshot.step("work").status if snapshot.steps else None for snapshot, d in events if not d]
    assert "running" in statuses and statuses[-1] == "done"
    assert events[-1][0].status is JobStatus.COMPLETED and events[-1][1] is False


# --------------------------------------------------------------------------------------------------
# Watchdog and work_dir safety
# --------------------------------------------------------------------------------------------------


def test_watchdog_does_not_count_time_in_the_jobs_first_gate(make_settings):
    executor = _executor("part_analysis", PhaseScript("lift", "gpu", ("lift",)))
    rig = WorkerRig(
        make_settings(part_analysis_timeout_s=2.0, run_max_defer_s=30), {"part_analysis": executor}
    )

    async def scenario() -> Any:
        await rig.start()
        rig.gpu.running_job_id = "job_bbbbbbbbbbbb"  # an image-to-3D job holds the GPU
        run = rig.worker.enqueue(rig.new_pa(rig.make_job()))
        await asyncio.sleep(3.0)
        assert rig.gpu.submit_times == [], "the GPU phase waits behind the running job"
        rig.gpu.running_job_id = None
        final = await rig.wait_status(run, TERMINAL)
        await _shutdown(rig)
        return final

    final = run_async(scenario)
    assert final.status is JobStatus.COMPLETED, final.error
    assert all(w.code != GPU_DEFER_TIMEOUT_WARNING for w in final.warnings)


def test_watchdog_does_not_count_time_queued_behind_the_gpu_thread(make_settings):
    executor = _executor("part_analysis", PhaseScript("lift", "gpu", ("lift",)))
    rig = WorkerRig(make_settings(part_analysis_timeout_s=0.5), {"part_analysis": executor})
    release = threading.Event()

    async def scenario() -> Any:
        await rig.start()
        rig.gpu.occupy(release)  # e.g. an engine load already running on the GPU thread
        run = rig.worker.enqueue(rig.new_pa(rig.make_job()))
        await rig.wait_until(lambda: rig.gpu.submit_times, message="GPU phase submitted")
        await asyncio.sleep(1.2)
        release.set()
        final = await rig.wait_status(run, TERMINAL)
        await _shutdown(rig)
        return final

    final = run_async(scenario)
    assert final.status is JobStatus.COMPLETED, final.error


def test_watchdog_times_out_and_keeps_work_dir_until_the_gpu_callable_returns(make_settings):
    observed: dict[str, Any] = {}
    returned = threading.Event()

    def stubborn(_run: Any, _state: Any, ctx: Any) -> None:
        time.sleep(2.0)  # ignores the token after the timeout
        observed["work_dir_exists"] = ctx.work_dir.is_dir()
        observed["cancelled"] = ctx.cancel.is_cancelled
        returned.set()

    executor = _executor("part_analysis", PhaseScript("lift", "gpu", ("lift",), stubborn, models=("fake_model",)))
    rig = WorkerRig(make_settings(part_analysis_timeout_s=0.5), {"part_analysis": executor})

    async def scenario() -> tuple[Any, bool]:
        await rig.start()
        run = rig.worker.enqueue(rig.new_pa(rig.make_job()))
        final = await rig.wait_status(run, TERMINAL, timeout=15)
        returned_when_final = returned.is_set()
        await _shutdown(rig)
        return final, returned_when_final

    final, returned_when_final = run_async(scenario)
    assert final.status is JobStatus.FAILED and final.error.code == "run_timeout"
    assert final.error.details == {"timeout_s": 0.5}
    assert returned_when_final, "the final status is set only after the GPU callable returned"
    assert observed == {"work_dir_exists": True, "cancelled": True}
    assert not executor.work_dirs[0].exists()
    assert rig.registry.names()[-2:] == ["offload", "guard_exit"]


def test_stop_waits_for_a_stubborn_gpu_callable_before_removing_work_dir(make_settings):
    observed: dict[str, Any] = {}
    started = threading.Event()

    def stubborn(_run: Any, _state: Any, ctx: Any) -> None:
        started.set()
        time.sleep(2.0)
        observed["work_dir_exists"] = ctx.work_dir.is_dir()

    executor = _executor("image_generation", PhaseScript("gen", "gpu", ("gen",), stubborn))
    rig = WorkerRig(make_settings(), {"image_generation": executor})

    async def scenario() -> tuple[Any, float]:
        await rig.start()
        run = rig.worker.enqueue(rig.new_ig())
        await asyncio.to_thread(started.wait, 5)
        t0 = time.monotonic()
        await rig.stop(timeout=10)
        elapsed = time.monotonic() - t0
        await asyncio.to_thread(rig.gpu.shutdown, wait=True)
        return rig.stores["image_generation"].get(run.id), elapsed

    final, elapsed = run_async(scenario)
    assert 1.0 < elapsed < 10
    assert final.status is JobStatus.FAILED and final.error.code == "interrupted"
    assert observed == {"work_dir_exists": True}
    assert not executor.work_dirs[0].exists()


def test_stop_timeout_abandons_the_run_but_leaves_work_dir_to_the_startup_purge(make_settings):
    started, finished = threading.Event(), threading.Event()
    observed: dict[str, Any] = {}

    def stubborn(_run: Any, _state: Any, ctx: Any) -> None:
        started.set()
        time.sleep(1.5)
        observed["work_dir_exists"] = ctx.work_dir.is_dir()
        finished.set()

    executor = _executor("image_generation", PhaseScript("gen", "gpu", ("gen",), stubborn))
    rig = WorkerRig(make_settings(), {"image_generation": executor})

    async def scenario() -> Any:
        await rig.start()
        run = rig.worker.enqueue(rig.new_ig())
        await asyncio.to_thread(started.wait, 5)
        await rig.stop(timeout=0.3)
        snapshot = rig.stores["image_generation"].get(run.id)
        assert not finished.is_set(), "stop() returned before the callable (abandoned)"
        await asyncio.to_thread(finished.wait, 5)
        await asyncio.to_thread(rig.gpu.shutdown, wait=True)
        return snapshot

    final = run_async(scenario)
    assert final.status is JobStatus.FAILED and final.error.code == "interrupted"
    assert observed == {"work_dir_exists": True}
    assert executor.work_dirs[0].exists(), "an abandoned work_dir is left for purge_temp"


# --------------------------------------------------------------------------------------------------
# Jobs-first gate
# --------------------------------------------------------------------------------------------------


def test_jobs_first_gate_holds_gpu_phases_while_a_job_is_pending(make_settings):
    executor = _executor(
        "image_generation", PhaseScript("prep", "cpu", ("prep",)), PhaseScript("gen", "gpu", ("gen",))
    )
    rig = WorkerRig(make_settings(run_max_defer_s=30), {"image_generation": executor})

    async def scenario() -> tuple[Any, Any]:
        await rig.start()
        rig.job_queue.put("job_cccccccccccc")  # job_queue.pending == 1
        run = rig.worker.enqueue(rig.new_ig())
        waiting = await rig.wait_until(
            lambda: (live := rig.worker.live_snapshot(run.id)) is not None
            and live.progress.message == MESSAGE_GPU_QUEUED
            and live,
            message="gate message",
        )
        await asyncio.sleep(0.4)
        assert rig.gpu.submit_times == [], "no GPU submission while a job is pending"
        assert "prep" in executor.call_names(), "CPU phases are not gated"
        rig.job_queue.remove("job_cccccccccccc")
        final = await rig.wait_status(run, TERMINAL)
        await _shutdown(rig)
        return waiting, final

    waiting, final = run_async(scenario)
    assert waiting.status is JobStatus.RUNNING
    assert final.status is JobStatus.COMPLETED and final.warnings == []
    assert len(rig.gpu.submit_times) == 1


def test_jobs_first_gate_gives_up_after_run_max_defer_s(make_settings):
    executor = _executor("image_generation", PhaseScript("gen", "gpu", ("gen",)))
    rig = WorkerRig(make_settings(run_max_defer_s=1), {"image_generation": executor})

    async def scenario() -> tuple[Any, float]:
        await rig.start()
        rig.gpu.running_job_id = "job_dddddddddddd"
        t0 = time.monotonic()
        run = rig.worker.enqueue(rig.new_ig())
        final = await rig.wait_status(run, TERMINAL)
        waited = rig.gpu.submit_times[0] - t0
        await _shutdown(rig)
        return final, waited

    final, waited = run_async(scenario)
    assert final.status is JobStatus.COMPLETED
    assert 0.9 <= waited < 5
    assert [w.code for w in final.warnings] == [GPU_DEFER_TIMEOUT_WARNING]
    assert final.step("gen").status == "done"


def test_cancel_during_the_jobs_first_gate(make_settings):
    executor = _executor("image_generation", PhaseScript("gen", "gpu", ("gen",)))
    rig = WorkerRig(make_settings(run_max_defer_s=30), {"image_generation": executor})

    async def scenario() -> Any:
        await rig.start()
        rig.gpu.running_job_id = "job_eeeeeeeeeeee"
        run = rig.worker.enqueue(rig.new_ig())
        await rig.wait_until(
            lambda: (live := rig.worker.live_snapshot(run.id)) is not None
            and live.progress.message == MESSAGE_GPU_QUEUED,
            message="gate message",
        )
        rig.worker.cancel_running(run.id)
        final = await rig.wait_status(run, TERMINAL, timeout=5)
        await _shutdown(rig)
        return final

    final = run_async(scenario)
    assert final.status is JobStatus.CANCELLED
    assert rig.gpu.submit_times == []


# ==================================================================================================
# Executor table (lazy dotted paths)
# ==================================================================================================


def _deps() -> ExecutorDeps:
    return ExecutorDeps(
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        layout=SimpleNamespace(),  # type: ignore[arg-type]
        registry=None,
        job_store=SimpleNamespace(),  # type: ignore[arg-type]
        reference_store=SimpleNamespace(),  # type: ignore[arg-type]
        stores={},
    )


def test_executor_table_resolves_lazily_and_degrades_honestly():
    override = object()
    table = ExecutorTable(
        {
            "image_generation": "pipelines.s1_module_that_is_not_merged:Executor",
            "segmentation": "fakes_runs:BrokenExecutor",
            "part_analysis": "fakes_runs:ScriptedExecutor",
        },
        _deps(),
        overrides={"part_analysis": override},
    )
    missing = table.availability("image_generation")
    assert (missing.code, missing.available) == ("not_implemented", False)
    assert "not part of this build" in missing.reason
    broken = table.availability("segmentation")
    assert broken.code == "error" and "broken executor for tests" in broken.reason
    assert table.availability("part_analysis").available and table["part_analysis"] is override
    assert list(table) == ["part_analysis"] and len(table) == 1
    assert table.get("image_generation") is None
    with pytest.raises(KeyError):
        table["segmentation"]


def test_import_dotted():
    assert import_dotted("pipelines.runs.executor:ExecutorTable") is ExecutorTable
    with pytest.raises(DottedPathMissing):
        import_dotted("pipelines.s1_missing_module:Thing")
    with pytest.raises(ImportError):
        import_dotted("pipelines.runs.executor:NoSuchAttribute")
    with pytest.raises(ValueError):
        import_dotted("pipelines.runs.executor")


# ==================================================================================================
# Event publishers (section 6.6)
# ==================================================================================================


def _collect(bus: EventBus) -> Any:
    return bus.subscribe()


async def _drain(subscription: Any, wait: float) -> list[Event]:
    await asyncio.sleep(wait)
    events = []
    while len(subscription):
        events.append(await subscription.get())
    return events


def test_run_event_publisher_scopes_part_analyses_and_coalesces_workflow_progress():
    async def scenario() -> tuple[list[Event], list[Event]]:
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        sub = _collect(bus)
        runs = RunEventPublisher(bus, event_type=EventType.PART_ANALYSIS_UPDATED, serialize=lambda r: {"id": r.id})
        pa = PartAnalysisRun(id="pa_000000000001", job_id="job_aaaaaaaaaaaa", created_at=utcnow())
        runs.publish(pa)
        runs.publish(pa, droppable=True)
        first = await _drain(sub, 0.05)

        workflows = RunEventPublisher(
            bus,
            event_type=EventType.WORKFLOW_UPDATED,
            serialize=lambda r: {"id": r.id, "n": r.n},
            job_id_of=lambda _r: None,
            min_interval=0.5,
        )
        for n in range(10):
            workflows.publish(SimpleNamespace(id="wf_000000000001", n=n), droppable=True)
        workflows.publish(SimpleNamespace(id="wf_000000000001", n=99))  # a step transition: immediate
        second = await _drain(sub, 0.7)
        return first, second

    first, second = run_async(scenario)
    assert [(e.type, e.job_id, e.droppable) for e in first] == [
        ("part_analysis.updated", "job_aaaaaaaaaaaa", False),
        ("part_analysis.updated", "job_aaaaaaaaaaaa", True),
    ]
    assert [(e.data["n"], e.droppable) for e in second] == [(0, True), (99, False)]
    assert all(e.job_id is None for e in second)


def test_default_job_id_of_segmentation_sources():
    job_source = SimpleNamespace(id="sg_000000000001", source=SimpleNamespace(kind="job", job_id="job_aaaaaaaaaaaa"))
    image_source = SimpleNamespace(id="sg_000000000002", source=SimpleNamespace(kind="reference_image"))
    assert default_job_id(job_source) == "job_aaaaaaaaaaaa"
    assert default_job_id(image_source) is None
    assert default_job_id(SimpleNamespace(id="ig_000000000001")) is None


def test_features_event_publisher_debounces_and_skips_identical_payloads():
    async def scenario() -> tuple[list[Event], list[Event], list[Event]]:
        bus = EventBus()
        publisher = FeaturesEventPublisher(bus, debounce_s=0.05)
        state = {"value": 1, "calls": 0}

        def build() -> dict[str, int]:
            state["calls"] += 1
            return {"value": state["value"]}

        publisher.bind(build)
        publisher.notify()  # before startup binds the loop: ignored, and it must not wedge later notifications
        bus.bind_loop(asyncio.get_running_loop())
        sub = _collect(bus)
        for _ in range(5):
            publisher.notify()
        burst = await _drain(sub, 0.15)
        publisher.notify()
        same = await _drain(sub, 0.15)
        state["value"] = 2
        publisher.notify()
        changed = await _drain(sub, 0.15)
        return burst, same, changed

    burst, same, changed = run_async(scenario)
    assert [(e.type, e.data, e.droppable) for e in burst] == [("features.updated", {"value": 1}, False)]
    assert same == []
    assert [e.data for e in changed] == [{"value": 2}]


def test_features_event_publisher_is_silent_while_discovery_is_not_implemented():
    async def scenario() -> list[Event]:
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        sub = _collect(bus)
        publisher = FeaturesEventPublisher(bus, debounce_s=0.01)

        def build() -> Any:
            raise ServiceNotImplementedError("features", operation="Feature discovery")

        publisher.bind(build)
        publisher.notify()
        return await _drain(sub, 0.1)

    assert run_async(scenario) == []


def test_layout_paths(make_settings):
    settings = make_settings()
    layout = RunLayout(settings.paths)
    root = settings.paths.root
    assert layout.imagegen_dir("ig_000000000001") == root / "imagegen" / "ig_000000000001"
    assert layout.candidate_path("ig_000000000001", 2) == root / "imagegen" / "ig_000000000001" / "candidates" / "2.png"
    assert layout.part_analysis_dir("job_aaaaaaaaaaaa", "pa_000000000001") == (
        root / "jobs" / "job_aaaaaaaaaaaa" / "parts" / "pa_000000000001"
    )
    assert layout.work_dir("part_analysis", "pa_000000000001") == root / "temp" / "parts" / "pa_000000000001"
    assert layout.part_exports_dir("pa_000000000001") == root / "cache" / "part_exports" / "pa_000000000001"
    assert layout.workflow_file("wf_000000000001") == root / "workflows" / "wf_000000000001" / "workflow.json"
    every = [
        layout.imagegen_root,
        layout.segmentations_root,
        layout.workflows_root,
        layout.part_exports_root,
        layout.temp_root("image_generation"),
        layout.temp_root("segmentation"),
        layout.temp_root("part_analysis"),
    ]
    assert all("aux" not in Path(p).relative_to(root).parts for p in every)  # reserved Windows device name
