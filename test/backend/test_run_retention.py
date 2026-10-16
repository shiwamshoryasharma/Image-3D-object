"""Run retention (part-aware DESIGN.md section 7.2; slice S8).

The workflow side is a :class:`FakeWorkflowStore` with the frozen section 7.2 ``WorkflowStore`` API
(``list_terminal`` / ``delete`` / ``active_child_ids``), so these tests do not depend on slice S11. The run stores,
the runtime layout and the export cache are real (under ``test/.runtime``).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from config.settings import Settings
from models.enums import JobStatus
from models.image_generation import ImageGenerationRun
from models.part_analysis import PartAnalysisRun
from models.segmentation import SegmentationRun, SegmentationSource
from models.workflow import WorkflowOutputs, WorkflowRecord, WorkflowResource, WorkflowStep
from services.run_retention_service import GIB, RunRetentionService, StoreCleanup
from storage.run_layout import RunLayout
from storage.run_store import RunStore
from utils.timeutil import utcnow

NOW = utcnow()
JOB_A = "job_aaaaaaaaaaaa"
JOB_B = "job_bbbbbbbbbbbb"


class FakeWorkflowStore:
    """Test double with the frozen section 7.2 API of ``storage.workflow_store.WorkflowStore``."""

    def __init__(self, records: Iterable[WorkflowRecord] = ()) -> None:
        self.records: dict[str, WorkflowRecord] = {record.id: record for record in records}
        self.delete_calls: list[str] = []
        self.fail_listing = False

    def list_terminal(self) -> list[WorkflowRecord]:
        if self.fail_listing:
            raise RuntimeError("workflow index unreadable")
        items = [r.model_copy(deep=True) for r in self.records.values() if r.is_terminal]
        items.sort(key=lambda r: (r.created_at, r.id))
        return items

    def delete(self, wf_id: str) -> bool:
        self.delete_calls.append(wf_id)
        record = self.records.get(wf_id)
        if record is None or record.is_active:
            return False
        del self.records[wf_id]
        return True

    def active_child_ids(self) -> frozenset[str]:
        ids: set[str] = set()
        for record in self.records.values():
            if record.is_active:
                ids.update(record.child_ids())
        return frozenset(ids)

    def finish(self, wf_id: str) -> None:
        record = self.records[wf_id]
        record.status = JobStatus.COMPLETED
        record.finished_at = utcnow()


@dataclass
class Env:
    settings: Settings
    layout: RunLayout
    ig: RunStore[ImageGenerationRun]
    sg: RunStore[SegmentationRun]
    pa: RunStore[PartAnalysisRun]
    workflows: FakeWorkflowStore
    service: RunRetentionService

    def pass_(self) -> Any:
        return self.service.run_pass(NOW)


@pytest.fixture
def build(make_settings: Callable[..., Settings]) -> Callable[..., Env]:
    def factory(workflows: Iterable[WorkflowRecord] = (), **overrides: Any) -> Env:
        settings = make_settings(**overrides)
        layout = RunLayout(settings.paths)
        layout.ensure()
        ig: RunStore[ImageGenerationRun] = RunStore(layout, kind="image_generation", record_type=ImageGenerationRun)
        sg: RunStore[SegmentationRun] = RunStore(layout, kind="segmentation", record_type=SegmentationRun)
        pa: RunStore[PartAnalysisRun] = RunStore(layout, kind="part_analysis", record_type=PartAnalysisRun)
        fake = FakeWorkflowStore(workflows)
        service = RunRetentionService(
            settings=settings,
            layout=layout,
            stores={"image_generation": ig, "segmentation": sg, "part_analysis": pa},
            workflow_store=fake,  # type: ignore[arg-type]
        )
        return Env(settings, layout, ig, sg, pa, fake, service)

    return factory


def _times(days_old: float, status: JobStatus) -> dict[str, Any]:
    finished = NOW - timedelta(days=days_old)
    terminal = status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)
    return {
        "status": status,
        "created_at": finished - timedelta(minutes=5),
        "finished_at": finished if terminal else None,
    }


def _ig(store: RunStore[ImageGenerationRun], n: int, days_old: float, status=JobStatus.COMPLETED) -> str:
    run = ImageGenerationRun(id=f"ig_{n:012d}", options={"prompt": "a cat"}, **_times(days_old, status))
    assert store.save(run)
    return run.id


def _sg(store: RunStore[SegmentationRun], n: int, days_old: float, status=JobStatus.COMPLETED) -> str:
    source = SegmentationSource(
        kind="reference_image", reference_set_id="set_aaaaaaaaaaaa", image_id="img_000000000001"
    )
    run = SegmentationRun(id=f"sg_{n:012d}", source=source, **_times(days_old, status))
    assert store.save(run)
    return run.id


def _job(settings: Settings, job_id: str) -> Path:
    job_dir = settings.paths.job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.json").write_text("{}", encoding="utf-8")
    return job_dir


def _pa(env: Env, n: int, job_id: str = JOB_A) -> str:
    _job(env.settings, job_id)
    run = PartAnalysisRun(id=f"pa_{n:012d}", job_id=job_id, **_times(1, JobStatus.COMPLETED))
    assert env.pa.save(run)
    return run.id


def _wf(
    n: int,
    days_old: float,
    status: JobStatus = JobStatus.COMPLETED,
    *,
    image_generation_id: str | None = None,
    segmentation_id: str | None = None,
) -> WorkflowRecord:
    steps = []
    if image_generation_id is not None:
        steps.append(
            WorkflowStep(
                id="text_to_image",
                node="prompt",
                status="done",
                resource=WorkflowResource(kind="image_generation", id=image_generation_id),
            )
        )
    return WorkflowRecord(
        id=f"wf_{n:012d}",
        steps=steps,
        outputs=WorkflowOutputs(segmentation_id=segmentation_id),
        **_times(days_old, status),
    )


def _export(env: Env, pa_id: str, name: str, size: int, *, used: float, modified: float | None = None) -> Path:
    path = env.layout.part_exports_dir(pa_id) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    os.utime(path, (used, modified if modified is not None else used))
    return path


# ---------------------------------------------------------------------------------------------- runs


def test_age_and_count_limits_apply_per_store(build):
    env = build(
        imagegen_retention_days=14,
        max_stored_image_generations=2,
        segmentation_retention_days=3,
        max_stored_segmentations=5,
    )
    ig_expired = [
        _ig(env.ig, 1, 20),
        _ig(env.ig, 2, 15, JobStatus.FAILED),
        _ig(env.ig, 3, 30, JobStatus.CANCELLED),
    ]
    ig_kept = [_ig(env.ig, 4, 1), _ig(env.ig, 5, 2)]
    ig_excess = _ig(env.ig, 6, 3)
    ig_active = [_ig(env.ig, 7, 40, JobStatus.QUEUED), _ig(env.ig, 8, 40, JobStatus.RUNNING)]
    sg_expired = _sg(env.sg, 1, 4)  # 4 days: past the 3-day segmentation limit, inside the 14-day imagegen one
    sg_kept = [_sg(env.sg, 2, 1), _sg(env.sg, 3, 2), _sg(env.sg, 4, 2.5), _sg(env.sg, 5, 0, JobStatus.RUNNING)]

    report = env.pass_()

    assert report.image_generations == StoreCleanup(expired=3, excess=1, skipped=0)
    assert report.segmentations == StoreCleanup(expired=1, excess=0, skipped=0)
    assert env.ig.ids() == set(ig_kept + ig_active)
    assert env.sg.ids() == set(sg_kept)
    for run_id in [*ig_expired, ig_excess]:
        assert not env.layout.imagegen_dir(run_id).exists()
    for run_id in ig_kept + ig_active:
        assert (env.layout.imagegen_dir(run_id) / "run.json").is_file()
    assert not env.layout.segmentation_dir(sg_expired).exists()

    # A second pass finds nothing left to do (the kept runs are inside both limits).
    again = env.pass_()
    assert again.image_generations == StoreCleanup() and again.segmentations == StoreCleanup()
    assert not again.changed


def test_zero_retention_days_disables_only_the_age_limit(build):
    env = build(imagegen_retention_days=0, max_stored_image_generations=2)
    ancient = _ig(env.ig, 1, 400)
    newer = [_ig(env.ig, 2, 300), _ig(env.ig, 3, 200)]

    report = env.pass_()

    assert report.image_generations == StoreCleanup(expired=0, excess=1)
    assert env.ig.ids() == set(newer) and ancient not in env.ig.ids()


def test_part_analyses_are_not_run_retention_candidates(build):
    env = build()
    pa_id = _pa(env, 1)
    with pytest.raises(ValueError):
        env.service.cleanup_runs("part_analysis", NOW)
    env.pass_()
    assert env.pa.get(pa_id) is not None


def test_runs_used_by_active_workflows_are_skipped_until_the_workflow_ends(build):
    ig_used, ig_free, sg_used, sg_newest = "ig_000000000001", "ig_000000000002", "sg_000000000001", "sg_000000000002"
    running = _wf(1, 0, JobStatus.RUNNING, image_generation_id=ig_used)
    queued = _wf(2, 0, JobStatus.QUEUED, segmentation_id=sg_used)
    finished = _wf(3, 1, image_generation_id=ig_free)
    env = build(
        workflows=[running, queued, finished],
        imagegen_retention_days=14,
        max_stored_segmentations=1,
    )
    _ig(env.ig, 1, 20)  # expired, but a running workflow uses it
    _ig(env.ig, 2, 20)  # expired; only a finished workflow references it
    _sg(env.sg, 1, 2)  # beyond the count limit, but a queued workflow uses it
    _sg(env.sg, 2, 1)

    first = env.pass_()

    assert first.image_generations == StoreCleanup(expired=1, excess=0, skipped=1)
    assert first.segmentations == StoreCleanup(expired=0, excess=0, skipped=1)
    assert env.ig.ids() == {ig_used}
    assert env.sg.ids() == {sg_used, sg_newest}

    env.workflows.finish(running.id)
    env.workflows.finish(queued.id)
    second = env.pass_()

    assert second.image_generations == StoreCleanup(expired=1)
    assert second.segmentations == StoreCleanup(excess=1)
    assert env.ig.ids() == set() and env.sg.ids() == {sg_newest}
    assert not env.layout.imagegen_dir(ig_used).exists()
    assert not env.layout.segmentation_dir(sg_used).exists()


def test_protection_is_re_read_before_each_delete(build):
    """A workflow that starts during a pass still protects the run it picked up."""
    env = build(imagegen_retention_days=14)
    first, second = _ig(env.ig, 1, 30), _ig(env.ig, 2, 20)
    late = _wf(9, 0, JobStatus.QUEUED, image_generation_id=first)
    original_delete = env.ig.delete

    def delete_then_start_workflow(run_id: str) -> bool:
        deleted = original_delete(run_id)
        env.workflows.records[late.id] = late  # created between two deletes of the same pass
        return deleted

    env.ig.delete = delete_then_start_workflow  # type: ignore[method-assign]
    report = env.pass_()

    assert report.image_generations == StoreCleanup(expired=1, skipped=1)
    assert env.ig.ids() == {first} and second not in env.ig.ids()


# ---------------------------------------------------------------------------------------------- workflows


def test_terminal_workflows_past_the_limits_are_deleted_through_delete(build):
    records = [
        _wf(1, 40),
        _wf(2, 31, JobStatus.FAILED),
        _wf(3, 1),
        _wf(4, 2, JobStatus.CANCELLED),
        _wf(5, 3),
        _wf(6, 60, JobStatus.RUNNING),
        _wf(7, 90, JobStatus.QUEUED),
    ]
    env = build(workflows=records, workflow_retention_days=30, max_stored_workflows=2)

    report = env.pass_()

    assert report.workflows == StoreCleanup(expired=2, excess=1)
    assert sorted(env.workflows.delete_calls) == ["wf_000000000001", "wf_000000000002", "wf_000000000005"]
    assert set(env.workflows.records) == {"wf_000000000003", "wf_000000000004", "wf_000000000006", "wf_000000000007"}


def test_a_refused_workflow_delete_is_not_counted(build):
    env = build(workflows=[_wf(1, 40)], workflow_retention_days=30)
    env.workflows.delete = lambda wf_id: False  # type: ignore[method-assign]
    assert env.pass_().workflows == StoreCleanup()


# ---------------------------------------------------------------------------------------------- export cache


def test_orphan_export_cache_entries_are_purged(build):
    env = build()
    live = _pa(env, 1, JOB_A)
    vanished_job = _pa(env, 2, JOB_B)
    never_indexed = "pa_000000000003"
    now = time.time()
    for pa_id in (live, vanished_job, never_indexed):
        _export(env, pa_id, "part_hair.glb", 100, used=now)
    shutil.rmtree(env.settings.paths.job_dir(JOB_B))  # job retention removed the job without running hooks
    foreign_file = env.layout.part_exports_root / "README.txt"
    foreign_file.write_text("not an export", encoding="utf-8")
    foreign_dir = env.layout.part_exports_root / "pa_not_an_id"
    foreign_dir.mkdir()

    report = env.pass_()

    assert report.exports.orphan_dirs == 2
    assert report.exports.evicted_files == 0
    assert report.exports.size_bytes == 100
    assert (env.layout.part_exports_dir(live) / "part_hair.glb").is_file()
    assert not env.layout.part_exports_dir(vanished_job).exists()
    assert not env.layout.part_exports_dir(never_indexed).exists()
    assert foreign_file.is_file() and foreign_dir.is_dir()


def test_export_cache_evicts_least_recently_used_files_down_to_the_cap(build):
    env = build(parts_export_cache_max_gb=2500 / GIB)
    first, second = _pa(env, 1), _pa(env, 2)
    now = time.time()
    oldest = _export(env, first, "part_a.glb", 1000, used=now - 400)
    older = _export(env, second, "part_c.glb", 1000, used=now - 300)
    recent = _export(env, first, "bundle.zip", 1000, used=now - 200)
    # Modified long ago but downloaded a minute ago: the later of access and modification counts.
    accessed = _export(env, first, "part_b.glb", 1000, used=now - 50, modified=now - 10_000)
    writing = _export(env, second, ".bundle.zip.123.456.tmp", 5000, used=now)
    leftover = _export(env, second, ".part_d.glb.123.789.tmp", 700, used=now - 7200)

    report = env.pass_()

    assert report.exports.orphan_dirs == 0
    assert report.exports.evicted_files == 3  # two LRU files + the stale temp file
    assert report.exports.evicted_bytes == 2700
    assert report.exports.size_bytes == 2000
    assert not oldest.exists() and not older.exists() and not leftover.exists()
    assert recent.is_file() and accessed.is_file()
    assert writing.is_file(), "a temp file of an export being written must be left alone"
    assert env.layout.part_exports_dir(second).is_dir(), "folders of existing analyses stay"


def test_export_cache_cap_of_zero_keeps_nothing_between_passes(build):
    env = build(parts_export_cache_max_gb=0)
    pa_id = _pa(env, 1)
    now = time.time()
    files = [_export(env, pa_id, f"part_{i}.glb", 10, used=now - i) for i in range(3)]

    report = env.pass_()

    assert report.exports.evicted_files == 3 and report.exports.size_bytes == 0
    assert not any(path.exists() for path in files)


def test_missing_export_cache_root_is_not_an_error(build):
    env = build()
    shutil.rmtree(env.layout.part_exports_root)
    assert env.pass_().exports.size_bytes == 0
    assert not env.layout.part_exports_root.exists(), "retention never creates the cache folder"


# ---------------------------------------------------------------------------------------------- orchestration


def test_run_once_isolates_a_failing_section(build):
    env = build(imagegen_retention_days=14)
    _ig(env.ig, 1, 30)
    env.workflows.fail_listing = True

    report = asyncio.run(asyncio.wait_for(env.service.run_once(), 30))

    assert report.workflows == StoreCleanup()
    assert report.image_generations == StoreCleanup(expired=1)
    assert env.ig.ids() == set()


def test_run_forever_keeps_running_after_a_failed_pass(build, monkeypatch):
    env = build()
    calls: list[datetime] = []

    def failing_pass(now: datetime) -> Any:
        calls.append(now)
        raise RuntimeError("disk unavailable")

    monkeypatch.setattr(env.service, "run_pass", failing_pass)

    async def scenario() -> None:
        task = asyncio.create_task(env.service.run_forever(0.01, initial_delay_s=0))
        while len(calls) < 2:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(asyncio.wait_for(scenario(), 30))
    assert len(calls) >= 2


def test_container_builds_and_starts_run_retention(make_app):
    harness = make_app()
    part_aware = harness.container.part_aware
    assert part_aware is not None
    assert isinstance(part_aware.retention, RunRetentionService)
    assert "run-retention" in {task.get_name() for task in set(part_aware.background_tasks)}
