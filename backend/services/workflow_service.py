"""Workflows (``wf_``): the server-side chain of runs and a job (part-aware DESIGN.md sections 4.7 and 6.5).

S1 stub with the **frozen constructor**; slice S11 implements the compiler integration (``POST /workflows/validate``),
creation (all-or-nothing checks, 429 when the run or job queue is full), the idempotent ``advance`` driven by the
child events, the reconciliation sweep, cancel/delete and ``to_out`` (``steps[].resource.exists`` computed at read
time). Workflows run on the event loop only (no GPU). Torch-free.

:meth:`WorkflowService.start` / :meth:`WorkflowService.stop` are the lifecycle hooks the part-aware container calls
(subscription, sweep and startup recovery in S11). Until S11 merges no workflow can exist, so they have nothing to do.
"""

from __future__ import annotations

from typing import Any

from config.settings import Settings
from core.events import EventBus
from core.feature_errors import ServiceNotImplementedError
from models.enums import JobStatus
from models.workflow import WorkflowRecord
from schemas.workflows import CatalogOut, ValidationReport, WorkflowOut, WorkflowRequest
from services.feature_service import FeatureService
from services.image_generation_service import ImageGenerationService
from services.job_service import JobService
from services.part_analysis_service import PartAnalysisService
from services.reference_service import ReferenceService
from services.run_events import RunEventPublisher
from services.segmentation_service import SegmentationService
from storage.run_layout import RunLayout
from storage.workflow_store import WorkflowStore
from workers.queue import JobQueue
from workers.run_worker import RunWorker

_CAPABILITY = "workflows"


class WorkflowService:
    def __init__(
        self,
        *,
        settings: Settings,
        store: WorkflowStore,
        bus: EventBus,
        events: RunEventPublisher[WorkflowRecord],
        features: FeatureService,
        jobs: JobService,
        references: ReferenceService,
        image_generations: ImageGenerationService,
        segmentations: SegmentationService,
        part_analyses: PartAnalysisService,
        runs: RunWorker,
        job_queue: JobQueue,
        layout: RunLayout,
    ) -> None:
        self._settings = settings
        self._store = store
        self._bus = bus
        self._events = events
        self._features = features
        self._jobs = jobs
        self._references = references
        self._image_generations = image_generations
        self._segmentations = segmentations
        self._part_analyses = part_analyses
        self._runs = runs
        self._job_queue = job_queue
        self._layout = layout

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        """Startup recovery, child-event subscription and reconciliation sweep (S11)."""
        return None

    async def stop(self, timeout: float) -> None:
        """Stop the sweep and mark active workflows ``interrupted`` (S11)."""
        return None

    # ------------------------------------------------------------------ API
    def to_out(self, record: WorkflowRecord) -> WorkflowOut:
        raise ServiceNotImplementedError(_CAPABILITY, operation="Reading workflows")

    def catalog(self) -> tuple[CatalogOut, str]:
        """Node catalog and its ETag."""
        raise ServiceNotImplementedError(_CAPABILITY, operation="The workflow catalog")

    def validate(self, request: WorkflowRequest) -> ValidationReport:
        raise ServiceNotImplementedError(_CAPABILITY, operation="Workflow validation")

    async def create(self, request: WorkflowRequest) -> WorkflowRecord:
        raise ServiceNotImplementedError(_CAPABILITY, operation="Workflows")

    def get(self, wf_id: str) -> WorkflowRecord:
        raise ServiceNotImplementedError(_CAPABILITY, operation="Reading workflows")

    def list_runs(
        self,
        *,
        limit: int = 20,
        status: JobStatus | None = None,
        job_id: str | None = None,
        image_generation_id: str | None = None,
    ) -> list[WorkflowRecord]:
        raise ServiceNotImplementedError(_CAPABILITY, operation="Listing workflows")

    def graph(self, wf_id: str) -> dict[str, Any]:
        """``{graph, layout}`` stored with a Node-UI workflow."""
        raise ServiceNotImplementedError(_CAPABILITY, operation="Reading workflow graphs")

    async def cancel(self, wf_id: str) -> WorkflowRecord:
        raise ServiceNotImplementedError(_CAPABILITY, operation="Cancelling workflows")

    async def delete(self, wf_id: str) -> None:
        raise ServiceNotImplementedError(_CAPABILITY, operation="Deleting workflows")
