"""Part analysis runs (``pa_``): decomposition after reconstruction (part-aware DESIGN.md sections 4.6 and 6.4).

S1 stub with the **frozen constructor**; slice S10 implements creation (409 for an incomplete job or a missing
``model.glb``, ``segmentation_id`` reuse rules, capability checks), reads, ``latest``, cancel/delete and the file
routes. Runs are executed by :class:`workers.run_worker.RunWorker` with the
``pipelines.part_analysis:PartAnalysisExecutor`` executor and stored inside their job
(``jobs/<job>/parts/<pa>/run.json``). Torch-free.

:meth:`PartAnalysisService.on_job_deleting` is already real: the container registers it as a job-delete hook, so
deleting a job cancels its active analyses (waiting at most 30 s), forgets them and purges their export cache before
the job folder (and ``parts/`` with it) is removed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Final

from config.settings import Settings
from core.feature_errors import ServiceNotImplementedError
from models.part_analysis import PartAnalysisRun
from models.segmentation import SegmentationRun
from schemas.parts import PartAnalysisOptions, PartAnalysisOut
from services.feature_service import FeatureService
from services.job_service import JobService
from services.part_files import PartFiles
from services.result_service import ResultService
from services.run_events import RunEventPublisher
from storage.run_layout import RunLayout
from storage.run_store import RunStore
from workers.run_worker import RunWorker

if TYPE_CHECKING:  # pragma: no cover
    from inference.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

JOB_DELETE_WAIT_S: Final = 30.0
JOB_DELETED_REASON: Final = "The job was deleted"
_CAPABILITY = "part_analysis"


class PartAnalysisService:
    def __init__(
        self,
        *,
        settings: Settings,
        store: RunStore[PartAnalysisRun],
        runs: RunWorker,
        registry: ProviderRegistry | None,
        jobs: JobService,
        results: ResultService,
        segmentations: RunStore[SegmentationRun],
        events: RunEventPublisher[PartAnalysisRun],
        layout: RunLayout,
        files: PartFiles,
        features: FeatureService,
    ) -> None:
        self._settings = settings
        self._store = store
        self._runs = runs
        self._registry = registry
        self._jobs = jobs
        self._results = results
        self._segmentations = segmentations
        self._events = events
        self._layout = layout
        self._files = files
        self._features = features

    def to_out(self, run: PartAnalysisRun) -> PartAnalysisOut:
        return PartAnalysisOut.from_run(run)

    async def create(
        self, job_id: str, options: PartAnalysisOptions | None = None, *, workflow_id: str | None = None
    ) -> PartAnalysisRun:
        raise ServiceNotImplementedError(_CAPABILITY, operation="Part analysis")

    def get(self, pa_id: str) -> PartAnalysisRun:
        raise ServiceNotImplementedError(_CAPABILITY, operation="Reading part analyses")

    def list_for_job(self, job_id: str) -> list[PartAnalysisRun]:
        raise ServiceNotImplementedError(_CAPABILITY, operation="Listing part analyses")

    def latest(self, job_id: str) -> PartAnalysisRun:
        raise ServiceNotImplementedError(_CAPABILITY, operation="Reading the latest part analysis")

    async def cancel(self, pa_id: str) -> PartAnalysisRun:
        raise ServiceNotImplementedError(_CAPABILITY, operation="Cancelling part analyses")

    async def delete(self, pa_id: str) -> None:
        raise ServiceNotImplementedError(_CAPABILITY, operation="Deleting part analyses")

    def asset_file(self, pa_id: str, relative: str) -> Path:
        """A whitelisted file of a published asset (manifest, ``model_parts.glb``, masks, thumbs, ...)."""
        raise ServiceNotImplementedError(_CAPABILITY, operation="Serving part-analysis files")

    # ------------------------------------------------------------------ job-delete hook
    async def on_job_deleting(self, job_id: str) -> None:
        """Cancel the job's active analyses (wait <= 30 s), forget them and purge their export cache."""
        for run in self._store.list_active(job_id):
            if self._runs.cancel_queued(run.id, JOB_DELETED_REASON) is not None:
                continue
            if self._runs.cancel_running(run.id, JOB_DELETED_REASON) is None:
                continue
            if not await self._runs.wait_finished(run.id, JOB_DELETE_WAIT_S):
                logger.warning(
                    "Part analysis %s did not stop within %.0f s before deleting job %s",
                    run.id,
                    JOB_DELETE_WAIT_S,
                    job_id,
                )
        forgotten = self._store.forget_job(job_id)
        for pa_id in forgotten:
            self._files.purge(pa_id)
        if forgotten:
            suffix = "is" if len(forgotten) == 1 else "es"
            logger.info("Job %s deleted with %d part analys%s", job_id, len(forgotten), suffix)
