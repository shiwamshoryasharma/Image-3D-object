"""Builds and publishes ``job.updated`` events (shared by the job service and the GPU worker)."""

from __future__ import annotations

from collections.abc import Iterable

from core.events import Event, EventBus
from models.enums import JobStatus
from models.job import Job
from schemas.events import EventType
from schemas.jobs import JobOut
from workers.queue import JobQueue


class JobEventPublisher:
    def __init__(self, bus: EventBus, queue: JobQueue) -> None:
        self._bus = bus
        self._queue = queue

    def to_out(self, job: Job) -> JobOut:
        """API representation. Queue positions are only computed for queued jobs (loop thread)."""
        position = self._queue.position(job.id) if job.status is JobStatus.QUEUED else None
        return JobOut.from_domain(job, queue_position=position)

    def publish(self, job: Job, *, droppable: bool = False) -> None:
        """Publish ``job.updated`` (thread-safe)."""
        data = self.to_out(job).model_dump(mode="json")
        self._bus.publish_threadsafe(
            Event(type=str(EventType.JOB_UPDATED), data=data, job_id=job.id, droppable=droppable)
        )

    def publish_many(self, jobs: Iterable[Job]) -> None:
        for job in jobs:
            self.publish(job)
