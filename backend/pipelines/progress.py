"""Thread-safe job progress reporter.

Implements :class:`inference.base.ProgressReporter`. The engine calls ``stage()``/``step()`` from the GPU
worker thread; the reporter updates the job's progress (percent/ETA per the contract stage table) and:

* publishes ``job.updated`` snapshots at most every ``min_publish_interval`` seconds (5/s), with a trailing
  flush so the latest state is always delivered;
* persists ``job.json`` at most every ``persist_interval`` seconds while running.

Status changes are not published here: the worker publishes them unthrottled (via :meth:`mutate` with
``force=True``), so they are never dropped.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable

from models.enums import JobStage
from models.job import STAGE_TABLE, Job
from utils.timeutil import utcnow

logger = logging.getLogger(__name__)

PublishFn = Callable[[Job, bool], None]
"""``publish(job_snapshot, droppable)`` - progress snapshots are droppable, status changes are not."""
PersistFn = Callable[[Job], None]
ScheduleFn = Callable[[float, Callable[[], None]], None]

DEFAULT_PUBLISH_INTERVAL_S = 0.2
DEFAULT_PERSIST_INTERVAL_S = 2.0

_WORK_STAGES = tuple(stage for stage, info in STAGE_TABLE.items() if info.index > 0)


def compute_percent(stage: JobStage, step: int | None, total_steps: int | None) -> float:
    """Contract formula: sum(weights of completed stages) + weight(current) * step / total_steps."""
    if stage is JobStage.DONE:
        return 100.0
    current = STAGE_TABLE[stage]
    if current.index == 0:
        return 0.0
    completed = sum(STAGE_TABLE[s].weight for s in _WORK_STAGES if STAGE_TABLE[s].index < current.index)
    fraction = 0.0
    if total_steps and total_steps > 0 and step is not None:
        fraction = min(max(step / total_steps, 0.0), 1.0)
    return round(min(completed + current.weight * fraction, 99.9), 1)


class JobProgressReporter:
    """Owns the live copy of a running job. All mutations of that job go through this object."""

    def __init__(
        self,
        job: Job,
        *,
        publish: PublishFn,
        persist: PersistFn,
        schedule_later: ScheduleFn | None = None,
        min_publish_interval: float = DEFAULT_PUBLISH_INTERVAL_S,
        persist_interval: float = DEFAULT_PERSIST_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._job = job.model_copy(deep=True)
        self._publish = publish
        self._persist = persist
        self._schedule_later = schedule_later
        self._min_interval = min_publish_interval
        self._persist_interval = persist_interval
        self._clock = clock
        self._lock = threading.RLock()

        now = clock()
        self._created = now
        self._generation_started: float | None = None
        self._stage_started = now
        self._last_publish = -math.inf
        self._last_persist = now
        self._trailing_scheduled = False
        self._closed = False
        self._timings: dict[str, float] = {}

    # ------------------------------------------------------------------ ProgressReporter protocol
    def stage(self, stage: JobStage, message: str | None = None, total_steps: int | None = None) -> None:
        stage = JobStage(stage)
        with self._lock:
            if self._closed:
                return
            now = self._clock()
            previous = self._job.progress.stage
            if previous != stage and STAGE_TABLE[previous].index > 0:
                self._timings[str(previous)] = round(
                    self._timings.get(str(previous), 0.0) + (now - self._stage_started), 3
                )
            if previous != stage:
                self._stage_started = now
            if STAGE_TABLE[stage].index > 0 and self._generation_started is None:
                self._generation_started = now
            info = STAGE_TABLE[stage]
            progress = self._job.progress
            progress.stage = stage
            progress.stage_label = info.label
            progress.stage_index = info.index
            progress.total_steps = total_steps if total_steps and total_steps > 0 else None
            progress.step = 0 if progress.total_steps else None
            progress.message = message or info.label
            progress.percent = max(progress.percent, compute_percent(stage, progress.step, progress.total_steps))
        self._emit()

    def step(self, step: int, total_steps: int | None = None, message: str | None = None) -> None:
        with self._lock:
            if self._closed:
                return
            progress = self._job.progress
            if total_steps and total_steps > 0:
                progress.total_steps = total_steps
            total = progress.total_steps
            progress.step = max(0, min(int(step), total)) if total else max(0, int(step))
            if message:
                progress.message = message
            elif total:
                progress.message = f"{progress.stage_label} - step {progress.step}/{total}"
            progress.percent = max(progress.percent, compute_percent(progress.stage, progress.step, total))
        self._emit()

    # ------------------------------------------------------------------ worker API
    def mutate(self, fn: Callable[[Job], None], *, publish: bool = True, persist: bool = False) -> Job:
        """Apply ``fn`` to the live job under the lock; optionally publish (unthrottled) and persist.

        Publishing and persisting happen while holding the lock, so snapshots reach clients and ``job.json`` in
        the order they were taken (a progress snapshot can never overwrite a newer status change).
        """
        with self._lock:
            fn(self._job)
            snapshot = self._snapshot_locked()
            if publish:
                self._last_publish = self._clock()
                self._safe_publish(snapshot, droppable=False)
            if persist:
                self._last_persist = self._clock()
                self._persist(snapshot)
        return snapshot

    def snapshot(self) -> Job:
        with self._lock:
            return self._snapshot_locked()

    def stage_timings(self) -> dict[str, float]:
        """Seconds spent per stage so far (the current stage is included up to now)."""
        with self._lock:
            timings = dict(self._timings)
            current = self._job.progress.stage
            if STAGE_TABLE[current].index > 0:
                timings[str(current)] = round(
                    timings.get(str(current), 0.0) + (self._clock() - self._stage_started), 3
                )
            return timings

    def close(self) -> None:
        """Stop publishing (pending trailing flushes become no-ops)."""
        with self._lock:
            self._closed = True

    # ------------------------------------------------------------------ internals
    def _snapshot_locked(self) -> Job:
        now = self._clock()
        progress = self._job.progress
        progress.updated_at = utcnow()
        progress.elapsed_seconds = round(now - self._created, 2)
        progress.eta_seconds = self._eta_locked(now)
        return self._job.model_copy(deep=True)

    def _eta_locked(self, now: float) -> float | None:
        percent = self._job.progress.percent
        if self._job.is_terminal or self._generation_started is None or percent < 1.0 or percent >= 100.0:
            return None
        elapsed = now - self._generation_started
        return round(elapsed * (100.0 - percent) / percent, 1)

    def _emit(self) -> None:
        delay: float | None = None
        with self._lock:
            if self._closed:
                return
            now = self._clock()
            since = now - self._last_publish
            if since >= self._min_interval:
                self._last_publish = now
                snapshot = self._snapshot_locked()
                self._safe_publish(snapshot, droppable=True)
                if now - self._last_persist >= self._persist_interval:
                    self._last_persist = now
                    self._safe_persist(snapshot)
            elif not self._trailing_scheduled and self._schedule_later is not None:
                self._trailing_scheduled = True
                delay = max(self._min_interval - since, 0.0)
        if delay is not None and self._schedule_later is not None:
            self._schedule_later(delay, self._trailing_flush)

    def _trailing_flush(self) -> None:
        with self._lock:
            self._trailing_scheduled = False
            if self._closed:
                return
        self._emit()

    def _safe_publish(self, job: Job, *, droppable: bool) -> None:
        try:
            self._publish(job, droppable)
        except Exception:  # noqa: BLE001 - progress delivery must never break generation
            logger.exception("Failed to publish progress for job %s", job.id)

    def _safe_persist(self, job: Job) -> None:
        try:
            self._persist(job)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to persist progress for job %s", job.id)
