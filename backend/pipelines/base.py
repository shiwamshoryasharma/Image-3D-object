"""Pipeline interface executed by the GPU worker for each job."""

from __future__ import annotations

from abc import ABC, abstractmethod

from inference.base import CancellationToken, ProgressReporter
from models.job import Job, JobResultData


class GenerationPipeline(ABC):
    """Turns a queued job (with its job-local inputs) into a :class:`JobResultData`.

    ``run`` is blocking and is always called from the GPU worker thread. It raises ``JobCancelledError`` on
    cancellation and :class:`~core.exceptions.AppError` subclasses for expected failures.
    """

    @abstractmethod
    def run(self, job: Job, reporter: ProgressReporter, cancel: CancellationToken) -> JobResultData: ...
