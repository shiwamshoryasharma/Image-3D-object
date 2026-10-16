"""Thread-safe live state of a running run (part-aware DESIGN.md section 4.3; the ``JobProgressReporter`` pattern).

The run worker and the executor's phase callables (on the ``parts-cpu`` and ``gpu-worker`` threads) mutate the run
only through this object:

* progress updates (step fractions, model use) are published at most every ``min_publish_interval`` seconds (5/s)
  with a trailing flush; they are **droppable**;
* status, step transitions, warnings and explicit messages are published immediately and are **never droppable**;
* ``run.json`` is persisted at most every ``persist_interval`` seconds (2 s) while running, with a trailing persist so
  the file catches up; status changes are persisted immediately (``mutate(..., persist=True)``).

Percent (section 4.3): ``100 * (sum w(done|warning|skipped) + w(current) * progress(current)) / sum w(all)``, clamped
to 0..100, never decreasing, held below 100 until the run is ``completed`` (then exactly 100).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Generic, Literal, TypeVar

from models.enums import JobStatus
from models.runs import (
    COMPLETED_STEP_STATUSES,
    FINISHED_STEP_STATUSES,
    ModelUse,
    RunRecord,
    RunStep,
    RunWarning,
    StepDef,
    StepStatus,
    steps_from_plan,
)
from utils.timeutil import utcnow

logger = logging.getLogger(__name__)

R = TypeVar("R", bound=RunRecord)

PublishFn = Callable[[Any, bool], None]
"""``publish(run_snapshot, droppable)``."""
PersistFn = Callable[[Any], object]
ScheduleFn = Callable[[float, Callable[[], None]], None]

DEFAULT_PUBLISH_INTERVAL_S = 0.2
DEFAULT_PERSIST_INTERVAL_S = 2.0
RUNNING_PERCENT_CAP = 99.9

_Publish = Literal["now", "throttled", "none"]
_Persist = Literal["force", "throttled", "none"]


def compute_run_percent(weights: Mapping[str, float], steps: Iterable[RunStep]) -> float:
    """Section 4.3 formula over a plan's step weights (steps missing from ``weights`` weigh 0)."""
    total = sum(max(w, 0.0) for w in weights.values())
    if total <= 0:
        return 0.0
    done = 0.0
    for step in steps:
        weight = max(weights.get(step.id, 0.0), 0.0)
        if step.status in COMPLETED_STEP_STATUSES:
            done += weight
        elif step.status == "running":
            done += weight * min(max(step.progress or 0.0, 0.0), 1.0)
    return round(min(max(100.0 * done / total, 0.0), 100.0), 1)


class RunReporter(Generic[R]):
    """Owns the live copy of one running run."""

    def __init__(
        self,
        run: R,
        *,
        publish: PublishFn,
        persist: PersistFn,
        schedule_later: ScheduleFn | None = None,
        min_publish_interval: float = DEFAULT_PUBLISH_INTERVAL_S,
        persist_interval: float = DEFAULT_PERSIST_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._run: R = run.model_copy(deep=True)
        self._publish = publish
        self._persist = persist
        self._schedule_later = schedule_later
        self._min_interval = max(0.0, min_publish_interval)
        self._persist_interval = max(0.0, persist_interval)
        self._clock = clock
        self._lock = threading.RLock()

        self._weights: dict[str, float] = {}
        self._step_started: dict[str, float] = {}
        self._warned_steps: set[str] = set()
        self._last_publish = -math.inf
        self._last_persist = clock()
        self._trailing_scheduled = False
        self._persist_scheduled = False
        self._unpublished = False
        """A throttled update was held back; the trailing flush publishes it."""
        self._dirty = False
        """The live run changed since the last ``run.json`` write."""
        self._closed = False

    # ------------------------------------------------------------------ plan
    def set_plan(self, plan: Sequence[StepDef]) -> R:
        """Install the executor's plan. Existing step states are kept; ``skip_reason`` steps become ``skipped``."""

        def _install(run: RunRecord) -> None:
            existing = {s.id: s for s in run.steps}
            steps: list[RunStep] = []
            for definition in plan:
                fresh = steps_from_plan([definition])[0]
                step = existing.get(definition.id)
                if step is None:
                    step = fresh
                else:
                    step.label = definition.label
                    if step.status == "pending" and fresh.status == "skipped":
                        step = fresh
                steps.append(step)
            run.steps = steps
            run.progress.step_count = len(steps)

        with self._lock:
            self._weights = {d.id: float(d.weight) for d in plan}
        return self._apply(_install, publish="now", persist="throttled")

    # ------------------------------------------------------------------ step API (any thread)
    def start_step(self, step_id: str, message: str | None = None) -> R:
        def _start(run: RunRecord) -> None:
            index, step = self._find(run, step_id)
            step.status = "running"
            step.progress = 0.0
            step.duration_s = None
            if message:
                step.message = message
            run.progress.step_id = step_id
            run.progress.step_index = index + 1
            run.progress.message = message or step.label
            self._step_started[step_id] = self._clock()

        return self._apply(_start, publish="now", persist="throttled")

    def step_progress(self, step_id: str, fraction: float, message: str | None = None) -> R:
        def _progress(run: RunRecord) -> None:
            _index, step = self._find(run, step_id)
            if step.status != "running":
                return
            step.progress = min(max(float(fraction), 0.0), 1.0)
            if message:
                step.message = message
                run.progress.message = message

        return self._apply(_progress, publish="throttled", persist="throttled")

    def finish_step(
        self,
        step_id: str,
        status: StepStatus,
        message: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> R:
        if status not in FINISHED_STEP_STATUSES:
            raise ValueError(f"{status!r} is not a final step status")

        def _finish(run: RunRecord) -> None:
            _index, step = self._find(run, step_id)
            step.status = status
            if status in COMPLETED_STEP_STATUSES:
                step.progress = 1.0
            started = self._step_started.pop(step_id, None)
            if started is not None:
                step.duration_s = round(self._clock() - started, 3)
            if message is not None:
                step.message = message
            if details:
                step.details.update(dict(details))

        return self._apply(_finish, publish="now", persist="throttled")

    def skip_step(self, step_id: str, reason: str) -> R:
        """Mark a pending step ``skipped`` (no-op for a step that already started)."""

        def _skip(run: RunRecord) -> None:
            _index, step = self._find(run, step_id)
            if step.status != "pending":
                return
            step.status = "skipped"
            step.message = reason
            step.progress = 1.0
            step.duration_s = 0.0

        return self._apply(_skip, publish="now", persist="throttled")

    def has_warning(self, step_id: str) -> bool:
        with self._lock:
            return step_id in self._warned_steps

    def warn(self, code: str, message: str, step_id: str | None = None) -> R:
        def _warn(run: RunRecord) -> None:
            run.warnings.append(RunWarning(code=code, message=message, step_id=step_id))
            if step_id is not None:
                self._warned_steps.add(step_id)

        return self._apply(_warn, publish="now", persist="throttled")

    def use_model(self, use: ModelUse) -> R:
        def _use(run: RunRecord) -> None:
            if all(existing.id != use.id for existing in run.models):
                run.models.append(use)

        return self._apply(_use, publish="throttled", persist="throttled")

    def set_message(self, message: str) -> R:
        def _message(run: RunRecord) -> None:
            run.progress.message = message

        return self._apply(_message, publish="now", persist="throttled")

    # ------------------------------------------------------------------ worker API
    def mutate(self, fn: Callable[[R], None], *, publish: bool = True, persist: bool = False) -> R:
        """Apply ``fn`` to the live run; ``publish`` = immediate non-droppable event, ``persist`` = write now.

        Publishing and persisting happen under the lock, so snapshots reach clients and ``run.json`` in the order they
        were taken. A forced persist re-raises storage errors (e.g. ``JobDeletedError``) to the caller.
        """
        return self._apply(fn, publish="now" if publish else "none", persist="force" if persist else "throttled")

    def snapshot(self) -> R:
        with self._lock:
            return self._snapshot_locked()

    def close(self) -> None:
        """Stop throttled publishing/persisting (pending trailing flushes become no-ops)."""
        with self._lock:
            self._closed = True

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _find(run: RunRecord, step_id: str) -> tuple[int, RunStep]:
        for index, step in enumerate(run.steps):
            if step.id == step_id:
                return index, step
        raise ValueError(f"Unknown step {step_id!r} for run {run.id}")

    def _apply(self, fn: Callable[[Any], None] | None, *, publish: _Publish, persist: _Persist) -> R:
        flush_delay: float | None = None
        persist_delay: float | None = None
        with self._lock:
            if fn is not None:
                fn(self._run)
                self._refresh_percent_locked()
                self._dirty = True
            now = self._clock()
            snapshot: R | None = None
            if publish == "now":
                snapshot = self._snapshot_locked()
                self._last_publish = now
                self._unpublished = False
                self._safe_publish(snapshot, droppable=False)
            elif publish == "throttled" and not self._closed and (fn is not None or self._unpublished):
                since = now - self._last_publish
                if since >= self._min_interval:
                    snapshot = self._snapshot_locked()
                    self._last_publish = now
                    self._unpublished = False
                    self._safe_publish(snapshot, droppable=True)
                else:
                    self._unpublished = True
                    if not self._trailing_scheduled and self._schedule_later is not None:
                        self._trailing_scheduled = True
                        flush_delay = max(self._min_interval - since, 0.0)
            if persist == "force":
                snapshot = snapshot or self._snapshot_locked()
                self._last_persist = now
                self._dirty = False
                self._persist(snapshot)
            elif persist == "throttled" and self._dirty and not self._closed:
                since_persist = now - self._last_persist
                if since_persist >= self._persist_interval:
                    snapshot = snapshot or self._snapshot_locked()
                    self._last_persist = now
                    self._dirty = False
                    self._safe_persist(snapshot)
                elif not self._persist_scheduled and self._schedule_later is not None:
                    self._persist_scheduled = True
                    persist_delay = max(self._persist_interval - since_persist, 0.0)
            result = snapshot or self._snapshot_locked()
        if flush_delay is not None and self._schedule_later is not None:
            self._schedule_later(flush_delay, self._trailing_flush)
        if persist_delay is not None and self._schedule_later is not None:
            self._schedule_later(persist_delay, self._trailing_persist)
        return result

    def _refresh_percent_locked(self) -> None:
        progress = self._run.progress
        if self._run.status is JobStatus.COMPLETED:
            progress.percent = 100.0
            return
        computed = min(compute_run_percent(self._weights, self._run.steps), RUNNING_PERCENT_CAP)
        progress.percent = max(progress.percent, computed)

    def _snapshot_locked(self) -> R:
        self._run.progress.updated_at = utcnow()
        return self._run.model_copy(deep=True)

    def _trailing_flush(self) -> None:
        with self._lock:
            self._trailing_scheduled = False
            if self._closed:
                return
        self._apply(None, publish="throttled", persist="none")

    def _trailing_persist(self) -> None:
        with self._lock:
            self._persist_scheduled = False
            if self._closed or not self._dirty:
                return
        self._apply(None, publish="none", persist="throttled")

    def _safe_publish(self, run: R, *, droppable: bool) -> None:
        try:
            self._publish(run, droppable)
        except Exception:  # noqa: BLE001 - progress delivery must never break a run
            logger.exception("Failed to publish progress for run %s", run.id)

    def _safe_persist(self, run: R) -> None:
        try:
            self._persist(run)
        except Exception:  # noqa: BLE001 - a throttled write is retried by the next forced one
            logger.exception("Failed to persist progress for run %s", run.id)
