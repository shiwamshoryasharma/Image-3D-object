"""Unit tests of the progress reporter (percent formula, throttling, persistence, thread-safety)."""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest

from models.enums import JobStage, JobStatus
from models.job import Job
from pipelines.progress import JobProgressReporter, compute_percent
from utils.timeutil import utcnow


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_job() -> Job:
    return Job(
        id="job_0123456789ab",
        status=JobStatus.RUNNING,
        engine="fake",
        created_at=utcnow(),
        reference_set_id="rs_0123456789ab",
        primary_image_id="img_0123456789ab",
        seed=1,
        settings={},
    )


@pytest.fixture
def setup():
    clock = FakeClock()
    published: list[tuple[Job, bool]] = []
    persisted: list[Job] = []
    scheduled: list[tuple[float, Callable[[], None]]] = []
    reporter = JobProgressReporter(
        make_job(),
        publish=lambda job, droppable: published.append((job, droppable)),
        persist=persisted.append,
        schedule_later=lambda delay, cb: scheduled.append((delay, cb)),
        clock=clock,
    )
    return reporter, clock, published, persisted, scheduled


def test_compute_percent_matches_contract():
    assert compute_percent(JobStage.QUEUED, None, None) == 0
    assert compute_percent(JobStage.WAITING_FOR_MODEL, None, None) == 0
    assert compute_percent(JobStage.PREPROCESSING, None, None) == 0
    assert compute_percent(JobStage.CONDITIONING, None, None) == 5
    assert compute_percent(JobStage.SPARSE_STRUCTURE, 6, 12) == 17.5
    assert compute_percent(JobStage.SHAPE, 12, 24) == 40
    assert compute_percent(JobStage.TEXTURE, 0, 12) == 55
    assert compute_percent(JobStage.DECODING, None, None) == 75
    assert compute_percent(JobStage.EXPORTING, None, None) == 83
    assert compute_percent(JobStage.FINALIZING, None, None) == 98
    assert compute_percent(JobStage.DONE, None, None) == 100


def test_stage_and_step_update_progress(setup):
    reporter, clock, published, _, _ = setup
    reporter.stage(JobStage.SHAPE, total_steps=24)
    clock.advance(1)
    reporter.step(7)
    snap = reporter.snapshot()
    progress = snap.progress
    assert progress.stage is JobStage.SHAPE
    assert progress.stage_label == "Generating geometry"
    assert progress.stage_index == 4 and progress.stage_count == 9
    assert (progress.step, progress.total_steps) == (7, 24)
    assert progress.percent == pytest.approx(33.8, abs=0.05)
    assert progress.message == "Generating geometry - step 7/24"
    assert progress.eta_seconds is not None and progress.eta_seconds > 0
    assert progress.updated_at is not None


def test_publishing_is_throttled_with_trailing_flush(setup):
    reporter, clock, published, _, scheduled = setup
    reporter.stage(JobStage.SHAPE, total_steps=100)
    assert len(published) == 1 and published[0][1] is True  # progress events are droppable
    for step in range(1, 20):
        clock.advance(0.01)
        reporter.step(step)
    assert len(published) == 1  # throttled
    assert len(scheduled) == 1  # exactly one trailing flush pending
    delay, flush = scheduled[0]
    assert 0 < delay <= 0.2
    clock.advance(delay)
    flush()
    assert len(published) == 2
    assert published[-1][0].progress.step == 19  # latest state delivered


def test_mutate_is_unthrottled_and_not_droppable(setup):
    reporter, _, published, persisted, _ = setup
    reporter.stage(JobStage.SHAPE, total_steps=10)
    snap = reporter.mutate(lambda j: setattr(j, "status", JobStatus.COMPLETED), publish=True, persist=True)
    assert snap.status is JobStatus.COMPLETED
    assert published[-1][1] is False
    assert persisted[-1].status is JobStatus.COMPLETED


def test_persistence_at_most_every_two_seconds(setup):
    reporter, clock, _, persisted, _ = setup
    reporter.stage(JobStage.SHAPE, total_steps=1000)
    for step in range(1, 101):
        clock.advance(0.25)
        reporter.step(step)
    # 25 s of progress -> about 12 persists, never more often than every 2 s.
    assert 10 <= len(persisted) <= 13


def test_close_cancels_pending_trailing_flush(setup):
    reporter, clock, published, _, scheduled = setup
    reporter.stage(JobStage.SHAPE, total_steps=10)
    clock.advance(0.01)
    reporter.step(1)
    reporter.close()
    _, flush = scheduled[0]
    clock.advance(1)
    flush()
    assert len(published) == 1
    reporter.step(2)  # ignored after close
    assert len(published) == 1


def test_percent_never_decreases(setup):
    reporter, clock, _, _, _ = setup
    reporter.stage(JobStage.SHAPE, total_steps=10)
    reporter.step(9)
    reporter.step(2)  # e.g. a cascade restarting its step counter
    assert reporter.snapshot().progress.percent >= compute_percent(JobStage.SHAPE, 9, 10)


def test_stage_timings(setup):
    reporter, clock, _, _, _ = setup
    reporter.stage(JobStage.PREPROCESSING)
    clock.advance(1.5)
    reporter.stage(JobStage.CONDITIONING)
    clock.advance(0.5)
    timings = reporter.stage_timings()
    assert timings["preprocessing"] == pytest.approx(1.5)
    assert timings["conditioning"] == pytest.approx(0.5)


def test_reporter_is_thread_safe():
    published: list[Job] = []
    reporter = JobProgressReporter(
        make_job(), publish=lambda job, _d: published.append(job), persist=lambda _j: None, min_publish_interval=0
    )
    reporter.stage(JobStage.SHAPE, total_steps=1000)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for step in range(1, 300):
                reporter.step(step)
                reporter.snapshot()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert published
