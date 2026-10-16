"""Publishers of the five part-aware WebSocket events (part-aware DESIGN.md section 6.6).

=============================  ===============================  ============================================
type                           ``Event.job_id``                 throttle / droppable
=============================  ===============================  ============================================
``features.updated``           -                                debounced 250 ms; never dropped
``image_generation.updated``   -                                progress <= 5/s droppable; status/steps never
``segmentation.updated``       the job id of a job source       same (not job-scoped: delivered to everyone)
``part_analysis.updated``      the analysed job (job-scoped)    same
``workflow.updated``           -                                progress <= 2/s droppable; steps never
=============================  ===============================  ============================================

Run progress is already throttled to 5/s by :class:`~pipelines.runs.reporter.RunReporter`, so run publishers pass
every event through; the workflow publisher throttles droppable events itself (``min_interval``) with a trailing
publish of the latest state. Every ``publish`` is thread-safe (events hop onto the loop through the bus).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Final, Generic, TypeVar

from core.events import Event, EventBus
from core.feature_errors import ServiceNotImplementedError
from schemas.events import EventType

logger = logging.getLogger(__name__)

T = TypeVar("T")

RUN_PROGRESS_INTERVAL_S: Final = 0.0
"""Run publishers do not throttle again (the RunReporter emits progress at most 5/s)."""
WORKFLOW_PROGRESS_INTERVAL_S: Final = 0.5
"""``workflow.updated`` progress at most 2/s."""
FEATURES_DEBOUNCE_S: Final = 0.25


def default_job_id(record: Any) -> str | None:
    """``record.job_id`` (part analyses), else the job of a job-kind ``record.source`` (segmentations)."""
    job_id = getattr(record, "job_id", None)
    if isinstance(job_id, str) and job_id:
        return job_id
    source = getattr(record, "source", None)
    if source is not None and getattr(source, "kind", None) == "job":
        source_job = getattr(source, "job_id", None)
        if isinstance(source_job, str) and source_job:
            return source_job
    return None


def to_payload(value: Any) -> Any:
    """JSON-ready event data of a response model (or of an already plain value)."""
    dump = getattr(value, "model_dump", None)
    return dump(mode="json") if callable(dump) else value


class RunEventPublisher(Generic[T]):
    """Builds and publishes one ``<resource>.updated`` event type for records of type ``T``."""

    def __init__(
        self,
        bus: EventBus,
        *,
        event_type: EventType,
        serialize: Callable[[T], Any],
        job_id_of: Callable[[T], str | None] = default_job_id,
        min_interval: float = RUN_PROGRESS_INTERVAL_S,
    ) -> None:
        self._bus = bus
        self.event_type = event_type
        self._serialize = serialize
        self._job_id_of = job_id_of
        self._min_interval = max(0.0, min_interval)
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}
        self._pending: dict[str, T] = {}

    def to_out(self, record: T) -> Any:
        """API representation of ``record`` (the same body the resource's GET route returns)."""
        return self._serialize(record)

    def publish(self, record: T, *, droppable: bool = False) -> None:
        """Publish ``record`` (thread-safe). Droppable progress may be coalesced when ``min_interval`` > 0."""
        record_id = str(getattr(record, "id", ""))
        if self._min_interval > 0 and record_id:
            with self._lock:
                now = time.monotonic()
                if droppable:
                    wait = self._min_interval - (now - self._last.get(record_id, -1e9))
                    if wait > 0:
                        first = record_id not in self._pending
                        self._pending[record_id] = record
                        if first:
                            self._bus.call_later_threadsafe(wait, self._flush, record_id)
                        return
                else:
                    self._pending.pop(record_id, None)
                self._last[record_id] = now
        self._emit(record, droppable=droppable)

    def forget(self, record_id: str) -> None:
        """Drop the throttle state of a finished/deleted record."""
        with self._lock:
            self._last.pop(record_id, None)
            self._pending.pop(record_id, None)

    def _flush(self, record_id: str) -> None:
        with self._lock:
            record = self._pending.pop(record_id, None)
            if record is None:
                return
            self._last[record_id] = time.monotonic()
        self._emit(record, droppable=True)

    def _emit(self, record: T, *, droppable: bool) -> None:
        try:
            data = to_payload(self._serialize(record))
            job_id = self._job_id_of(record)
        except Exception:  # noqa: BLE001 - event delivery must never break the caller
            logger.exception("Could not serialize %s event for %s", self.event_type, getattr(record, "id", "?"))
            return
        self._bus.publish_threadsafe(Event(type=str(self.event_type), data=data, job_id=job_id, droppable=droppable))


class FeaturesEventPublisher:
    """Debounced ``features.updated`` (250 ms; never droppable; identical payloads are not re-sent).

    ``notify()`` may be called from any thread (registry state changes, run queue changes, engine changes). The
    payload is built on the event loop by the function given to :meth:`bind` (``FeatureService.features``).
    """

    def __init__(self, bus: EventBus, *, debounce_s: float = FEATURES_DEBOUNCE_S) -> None:
        self._bus = bus
        self._debounce = max(0.0, debounce_s)
        self._build: Callable[[], Any] | None = None
        self._lock = threading.Lock()
        self._scheduled = False
        self._last_payload: str | None = None

    def bind(self, build: Callable[[], Any]) -> None:
        self._build = build

    def notify(self) -> None:
        with self._lock:
            # Before startup binds the bus there is no loop and no WebSocket client to tell: nothing is scheduled,
            # so the first notification after startup is not swallowed.
            if self._scheduled or self._build is None or self._bus.loop is None:
                return
            self._scheduled = True
        self._bus.call_later_threadsafe(self._debounce, self._flush)

    def _flush(self) -> None:
        with self._lock:
            self._scheduled = False
        build = self._build
        if build is None:
            return
        try:
            data = to_payload(build())
        except ServiceNotImplementedError:
            logger.debug("features.updated not published: feature discovery is not implemented in this build")
            return
        except Exception:  # noqa: BLE001
            logger.exception("Could not build the features payload")
            return
        try:
            canonical = json.dumps(data, sort_keys=True, default=str)
        except (TypeError, ValueError):
            canonical = None
        if canonical is not None and canonical == self._last_payload:
            return
        self._last_payload = canonical
        self._bus.publish(Event(type=str(EventType.FEATURES_UPDATED), data=data, droppable=False))
