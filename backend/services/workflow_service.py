"""Workflows (``wf_``): the server-side chain of runs and a job (part-aware DESIGN.md sections 4.7 and 6.5; S11).

A workflow is a linear, fail-fast chain driven on the event loop (it never touches the GPU itself)::

    text_to_image (ig_) → import_image (candidate 0, auto_first) → segmentation (sg_) → reconstruction (job_)
    → part_extraction (pa_, segmentation_id = outputs.segmentation_id) → export (records the URLs)

The rows of :data:`WORKFLOW_STEPS` are the section 4.7.3 table (``WorkflowStepDef(id, node, weight, create_child)``;
``create_child`` returning ``None`` = the step is skipped). A future approved node adds one row here (extension rule,
section 4.7.2).

* **Creation is all-or-nothing:** the source must exist (404; an empty set or a foreign primary image is 422; a job
  source must be completed, 409), the feature flags and capabilities of every requested step are checked (503), the
  reconstruction settings are validated against the engine (422 ``unsupported_by_engine``) and both the run queue and
  the job queue must have room (429). Then the record is saved and the first child is created; if that child is
  refused, the record is removed again and the refusal is returned unchanged.
* **advance(wf_id) is idempotent:** under the per-workflow lock it reads each child's state from its service, maps it
  onto the steps (progress, ``warning_count``, artifacts, outputs), creates the next child once its predecessors are
  done and persists + publishes ``workflow.updated``. A child failure (or a refused child creation) fails the workflow
  with ``step_failed`` (``queue_full`` for a 429) and keeps the outputs already produced.
* **Triggers:** an ``EventBus`` subscription to the four child event types (filtered on the store's active-children
  index), a reconciliation sweep every ``workflow_sweep_interval_s`` over active workflows (dropped events), and once
  at startup. Startup recovery (``interrupted``) is done by the store before :meth:`WorkflowService.start`.
* **Cancel** cancels the active child through its service; pending steps become ``skipped``. **Delete** removes only
  the workflow record (409 while active); children remain as history, ``steps[].resource.exists`` is computed at read
  time. Deleting an ``ig_`` an active workflow uses is refused (delete guard registered on the image generation
  service).

Torch-free.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Final

from config.settings import Settings
from core.events import Event, EventBus, Subscription
from core.exceptions import (
    AppError,
    ConflictError,
    EngineUnavailableError,
    NotFoundError,
    QueueFullError,
    ValidationAppError,
)
from core.feature_errors import CapabilityUnavailableError, InvalidWorkflowError, require_feature
from models.enums import JobStatus
from models.job import JobError
from models.workflow import (
    FINISHED_WORKFLOW_STEP_STATUSES,
    QUEUE_FULL_ERROR_CODE,
    STEP_FAILED_ERROR_CODE,
    WORKFLOW_PREFIX,
    WorkflowArtifact,
    WorkflowChildKind,
    WorkflowRecord,
    WorkflowResource,
    WorkflowStep,
    WorkflowStepId,
)
from pipelines.reference_strategy import get_strategy
from schemas.events import EventType
from schemas.image_generation import UseCandidateRequest, image_generation_url
from schemas.jobs import CreateJobRequest
from schemas.parts import PartAnalysisOptions
from schemas.segmentation import JobSegmentationSource, ReferenceImageSegmentationSource, SegmentationRequest
from schemas.workflows import (
    CatalogOut,
    GraphIssueOut,
    ValidationReport,
    WorkflowGraph,
    WorkflowGraphOut,
    WorkflowOut,
    WorkflowRequest,
)
from services.feature_service import FeatureService
from services.generation_settings import validate_against_capabilities
from services.image_generation_service import ImageGenerationService
from services.job_service import JobService
from services.part_analysis_service import PartAnalysisService
from services.reference_service import ReferenceService
from services.run_events import RunEventPublisher
from services.segmentation_service import SegmentationService
from services.workflow_catalog import build_catalog
from services.workflow_compiler import CompileResult, WorkflowCompiler
from storage.run_layout import RunLayout
from storage.workflow_store import WorkflowStore
from utils.ids import is_valid_id, new_id
from utils.timeutil import utcnow
from workers.queue import JobQueue
from workers.run_worker import RunWorker

logger = logging.getLogger(__name__)

CHILD_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        str(EventType.IMAGE_GENERATION_UPDATED),
        str(EventType.SEGMENTATION_UPDATED),
        str(EventType.JOB_UPDATED),
        str(EventType.PART_ANALYSIS_UPDATED),
    }
)
STEP_LABELS: Final[Mapping[str, str]] = {
    "text_to_image": "Generating the image",
    "import_image": "Importing the image",
    "segmentation": "Segmenting the image",
    "reconstruction": "Generating the 3D model",
    "part_extraction": "Separating parts",
    "export": "Exporting",
}
STEP_CHILD_KIND: Final[Mapping[str, WorkflowChildKind]] = {
    "text_to_image": "image_generation",
    "import_image": "image_generation",
    "segmentation": "segmentation",
    "reconstruction": "job",
    "part_extraction": "part_analysis",
    "export": "part_analysis",
}
DELETE_IN_USE_REASON: Final = "An active workflow uses this image generation; cancel or finish the workflow first"


# --------------------------------------------------------------------------------------------------
# Step table
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChildRef:
    """What ``create_child`` produced: the step's resource and whether the step is done at once (import, export)."""

    resource: WorkflowResource | None
    completed: bool = False


@dataclass(frozen=True, slots=True)
class WorkflowContext:
    service: WorkflowService
    request: WorkflowRequest


CreateChild = Callable[[WorkflowRecord, WorkflowContext], Awaitable[ChildRef | None]]


@dataclass(frozen=True, slots=True)
class WorkflowStepDef:
    id: WorkflowStepId
    node: str
    weight: float
    create_child: CreateChild


async def _create_text_to_image(record: WorkflowRecord, ctx: WorkflowContext) -> ChildRef | None:
    return await ctx.service._create_image_generation(record, ctx.request)


async def _create_import_image(record: WorkflowRecord, ctx: WorkflowContext) -> ChildRef | None:
    return await ctx.service._import_candidate(record)


async def _create_segmentation(record: WorkflowRecord, ctx: WorkflowContext) -> ChildRef | None:
    return await ctx.service._create_segmentation(record, ctx.request)


async def _create_reconstruction(record: WorkflowRecord, ctx: WorkflowContext) -> ChildRef | None:
    return await ctx.service._create_job(record, ctx.request)


async def _create_part_extraction(record: WorkflowRecord, ctx: WorkflowContext) -> ChildRef | None:
    return await ctx.service._create_part_analysis(record, ctx.request)


async def _create_export(record: WorkflowRecord, ctx: WorkflowContext) -> ChildRef | None:
    return ChildRef(resource=None, completed=True)


WORKFLOW_STEPS: Final[tuple[WorkflowStepDef, ...]] = (
    WorkflowStepDef("text_to_image", "prompt", 6, _create_text_to_image),
    WorkflowStepDef("import_image", "input_image", 1, _create_import_image),
    WorkflowStepDef("segmentation", "segmentation", 4, _create_segmentation),
    WorkflowStepDef("reconstruction", "image_to_3d", 70, _create_reconstruction),
    WorkflowStepDef("part_extraction", "part_extraction", 17, _create_part_extraction),
    WorkflowStepDef("export", "export", 2, _create_export),
)
"""Section 4.7.3 in execution order."""
STEPS_BY_ID: Final[Mapping[str, WorkflowStepDef]] = {step.id: step for step in WORKFLOW_STEPS}


def step_applies(step_id: str, request: WorkflowRequest) -> bool:
    """Whether the request asks for ``step_id`` (steps that do not apply count neither for nor against percent)."""
    source = request.source
    kind = source.kind if source is not None else None
    return {
        "text_to_image": kind == "prompt",
        "import_image": kind in ("image", "prompt"),
        "segmentation": request.segmentation is not None,
        "reconstruction": kind != "job",
        "part_extraction": request.parts is not None,
        "export": True,
    }[step_id]


@dataclass(frozen=True, slots=True)
class _ChildState:
    status: JobStatus
    percent: float
    message: str | None
    warnings: int
    error: dict[str, Any] | None
    obj: Any


# --------------------------------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------------------------------


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
        self._compiler = WorkflowCompiler(features=features, jobs=jobs, references=references)
        self._locks: dict[str, asyncio.Lock] = {}
        self._requests: dict[str, WorkflowRequest] = {}
        """Original requests of the workflows created by this process (keeps ``model_fields_set``, so service-side
        defaults such as ``parts_default_views`` still apply to unset fields)."""
        self._subscription: Subscription | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._kicks: set[str] = set()
        self._started = False
        image_generations.add_delete_guard(self._guard_image_generation_delete)

    @property
    def compiler(self) -> WorkflowCompiler:
        return self._compiler

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        """Child-event subscription, reconciliation sweep and one advance of every active workflow."""
        if self._started:
            return
        self._started = True
        self._subscription = self._bus.subscribe(self._is_child_event)
        self._spawn(self._consume(self._subscription), "workflow-events")
        self._spawn(self._sweep_forever(), "workflow-sweep")
        for record in self._store.list_active():
            self._kick(record.id)

    async def stop(self, timeout: float) -> None:
        """Stop the subscription, the sweep and pending advances (active workflows are marked ``interrupted`` by the
        store at the next start, like runs)."""
        if self._subscription is not None:
            self._subscription.close()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            with contextlib.suppress(Exception):
                await asyncio.wait(tasks, timeout=max(0.1, timeout))
        self._started = False

    def _spawn(self, coro: Awaitable[Any], name: str) -> None:
        task = asyncio.ensure_future(coro)
        task.set_name(name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _is_child_event(self, event: Event) -> bool:
        if event.type not in CHILD_EVENT_TYPES or not isinstance(event.data, Mapping):
            return False
        child_id = event.data.get("id")
        return isinstance(child_id, str) and self._store.workflow_for_child(child_id) is not None

    async def _consume(self, subscription: Subscription) -> None:
        while True:
            event = await subscription.get()
            if event is None:
                logger.info("Workflow event subscription closed; the reconciliation sweep keeps workflows moving")
                return
            child_id = event.data.get("id") if isinstance(event.data, Mapping) else None
            wf_id = self._store.workflow_for_child(child_id) if isinstance(child_id, str) else None
            if wf_id is not None:
                self._kick(wf_id)

    async def _sweep_forever(self) -> None:
        interval = float(self._settings.workflow_sweep_interval_s)
        while True:
            await asyncio.sleep(interval)
            for record in self._store.list_active():
                try:
                    await self.advance(record.id)
                except Exception:  # noqa: BLE001 - one broken workflow must not stop the sweep
                    logger.exception("Workflow sweep failed for %s", record.id)

    def _kick(self, wf_id: str) -> None:
        """Schedule one ``advance`` (bursts of child events for one workflow collapse into one)."""
        if wf_id in self._kicks:
            return
        self._kicks.add(wf_id)

        async def run() -> None:
            await asyncio.sleep(0)
            self._kicks.discard(wf_id)
            try:
                await self.advance(wf_id)
            except Exception:  # noqa: BLE001
                logger.exception("Advancing workflow %s failed", wf_id)

        self._spawn(run(), f"workflow-advance-{wf_id}")

    def _guard_image_generation_delete(self, run: Any) -> str | None:
        return DELETE_IN_USE_REASON if run.id in self._store.active_child_ids() else None

    # ------------------------------------------------------------------ read
    def to_out(self, record: WorkflowRecord) -> WorkflowOut:
        return WorkflowOut.from_domain(record, resource_exists=self._resource_exists)

    def catalog(self) -> tuple[CatalogOut, str]:
        """Node catalog and its ETag (node availability from the current ``/features`` capabilities)."""
        catalog = build_catalog(
            self._compiler.node_states(), graph_max_bytes=self._settings.workflow_graph_max_bytes
        )
        return catalog, catalog.etag

    def get(self, wf_id: str) -> WorkflowRecord:
        record = self._store.get(wf_id) if is_valid_id(wf_id, WORKFLOW_PREFIX) else None
        if record is None:
            raise NotFoundError("Workflow not found", details={"workflow_id": wf_id})
        return record

    def list_runs(
        self,
        *,
        limit: int = 20,
        status: JobStatus | None = None,
        job_id: str | None = None,
        image_generation_id: str | None = None,
    ) -> list[WorkflowRecord]:
        def matches(record: WorkflowRecord) -> bool:
            if job_id is not None and record.outputs.job_id != job_id:
                return False
            if image_generation_id is not None:
                step = record.step("text_to_image")
                return step is not None and step.resource is not None and step.resource.id == image_generation_id
            return True

        return self._store.list_runs(status=status, predicate=matches, limit=limit)

    def graph(self, wf_id: str) -> dict[str, Any]:
        """``{graph, layout}`` stored with a Node-UI workflow (404 when none was stored)."""
        record = self.get(wf_id)
        graph, layout = self._store.read_graph(wf_id) if record.graph_stored else (None, None)
        if graph is None:
            raise NotFoundError("This workflow has no stored graph", details={"workflow_id": wf_id})
        out = WorkflowGraphOut(graph=WorkflowGraph.model_validate(graph), layout=layout)
        return out.model_dump(mode="json", by_alias=True)

    # ------------------------------------------------------------------ validate
    def validate(self, request: WorkflowRequest) -> ValidationReport:
        """``POST /workflows/validate``: the compiler's report for a graph or a simple form (never creates)."""
        if request.graph is not None:
            result = self._compiler.compile(request.graph)
            issues = [issue.to_out() for issue in result.issues]
            layout_issue = self._layout_issue(request)
            if layout_issue is not None:
                issues.insert(0, layout_issue)
            valid = result.valid and layout_issue is None
            return ValidationReport(
                valid=valid, issues=issues, mode=result.mode if valid else None,
                request=result.request if valid else None,
            )
        if request.source is not None and request.source.kind == "job" and request.parts is None:
            issue = GraphIssueOut(severity="error", code="unsupported_topology",
                                  message="A job source rebuilds parts: 'parts' is required")
            return ValidationReport(valid=False, issues=[issue], mode=None, request=None)
        result = self._compiler.compile(self._compiler.graph_from_request(request))
        # The synthesized graph's node ids mean nothing to a simple-form client.
        issues = [replace(issue, node_id=None).to_out() for issue in result.issues]
        layout_issue = self._layout_issue(request)
        if layout_issue is not None:
            issues.insert(0, layout_issue)
        valid = result.valid and layout_issue is None
        return ValidationReport(
            valid=valid, issues=issues, mode=result.mode if valid else None, request=request if valid else None
        )

    def _layout_issue(self, request: WorkflowRequest) -> GraphIssueOut | None:
        if request.layout is None:
            return None
        size = len(json.dumps(request.layout, separators=(",", ":")).encode("utf-8"))
        limit = self._settings.workflow_graph_max_bytes
        if size <= limit:
            return None
        return GraphIssueOut(severity="error", code="graph_too_large",
                             message=f"The layout is larger than {limit} bytes",
                             details={"bytes": size, "max_bytes": limit, "field": "layout"})

    # ------------------------------------------------------------------ create
    async def create(self, request: WorkflowRequest) -> WorkflowRecord:
        require_feature(self._settings, "workflows")
        if self._runs.stopping:
            raise EngineUnavailableError("The server is shutting down")
        layout_issue = self._layout_issue(request)
        if layout_issue is not None:
            raise ValidationAppError(
                layout_issue.message,
                details=[{"loc": ["body", "layout"], "msg": layout_issue.message, "type": "graph_too_large"}],
            )
        graph_json: dict[str, Any] | None = None
        node_ids: Mapping[str, str] = {}
        if request.graph is not None:
            compiled = self._compile_for_create(request.graph)
            assert compiled.request is not None
            graph_json = request.graph.model_dump(mode="json", by_alias=True)
            node_ids = compiled.node_ids
            simple = compiled.request.model_copy(update={"name": request.name})
        else:
            simple = request
        context = self._precheck(simple)

        now = utcnow()
        record = WorkflowRecord(
            id=new_id(WORKFLOW_PREFIX),
            name=simple.name,
            created_at=now,
            request=simple.simple_form(),
            graph_stored=graph_json is not None,
            steps=self._initial_steps(simple, node_ids, context),
        )
        self._apply_initial_outputs(record, simple, context)
        lock = self._lock(record.id)
        async with lock:
            if graph_json is not None:
                self._store.write_graph(record.id, graph_json, request.layout)
            self._store.save(record)
            self._requests[record.id] = simple
            try:
                await self._advance_locked(record, simple, initial=True)
            except BaseException:
                self._store.discard(record.id)
                self._requests.pop(record.id, None)
                self._locks.pop(record.id, None)
                raise
        logger.info(
            "Workflow %s created (source=%s, segmentation=%s, parts=%s, graph=%s)",
            record.id,
            simple.source.kind if simple.source is not None else None,
            simple.segmentation is not None,
            simple.parts is not None,
            graph_json is not None,
        )
        return self.get(record.id)

    def _compile_for_create(self, graph: WorkflowGraph) -> CompileResult:
        compiled = self._compiler.compile(graph)
        if compiled.valid:
            return compiled
        errors = compiled.errors
        too_large = next((issue for issue in errors if issue.code == "graph_too_large"), None)
        if too_large is not None:
            raise ValidationAppError(
                too_large.message,
                details=[{"loc": ["body", "graph"], "msg": too_large.message, "type": "graph_too_large",
                          "ctx": dict(too_large.details or {})}],
            )
        raise InvalidWorkflowError(
            "The workflow graph is not valid",
            issues=[issue.to_out().model_dump(mode="json") for issue in compiled.issues],
        )

    def _precheck(self, request: WorkflowRequest) -> dict[str, Any]:
        """Every creation check that does not create anything (section 4.7.4 "At creation")."""
        source = request.source
        assert source is not None  # WorkflowRequest guarantees a source without a graph
        context: dict[str, Any] = {}
        if source.kind == "image":
            ref_set = self._references.get_set(source.reference_set_id)  # 404
            if not ref_set.images:
                raise ValidationAppError("The reference set has no images", details={"reference_set_id": ref_set.id})
            primary = source.primary_image_id or ref_set.primary_image_id
            if source.primary_image_id is not None and ref_set.get_image(source.primary_image_id) is None:
                raise ValidationAppError(
                    "primary_image_id does not belong to the reference set",
                    details={"primary_image_id": source.primary_image_id, "reference_set_id": ref_set.id},
                )
            context["reference_set_id"], context["image_id"] = ref_set.id, primary
        elif source.kind == "job":
            job = self._jobs.get_job(source.job_id)  # 404
            if job.status is not JobStatus.COMPLETED:
                raise ConflictError(
                    f"The job is {job.status}; only completed jobs can be rebuilt",
                    details={"job_id": job.id, "status": str(job.status), "reason": "job_not_completed"},
                )
            if request.parts is None:
                raise ValidationAppError(
                    "A job source rebuilds the parts of the job: 'parts' is required",
                    details=[{"loc": ["body", "parts"], "msg": "required for a job source",
                              "type": "conflicting_fields"}],
                )
            context["job"] = job
        else:
            require_feature(self._settings, "image_generation")
        if request.segmentation is not None:
            require_feature(self._settings, "segmentation")
        if request.parts is not None:
            require_feature(self._settings, "part_analysis")

        features = self._features.features()
        pipeline = features.pipeline
        if source.kind == "prompt" and not pipeline.prompt_to_3d.available:
            prompt = pipeline.prompt_to_3d
            raise CapabilityUnavailableError(
                prompt.reason or "Prompt-to-3D is not available",
                capability="prompt_to_3d",
                reasons=[{"model_id": prompt.default_model_id, "state": prompt.default_model_state,
                          "reason_code": prompt.code, "reason": prompt.reason}],
            )
        if request.reconstruction is not None:
            self._check_capability("image_to_3d", pipeline.image_to_3d)
            self._check_engine_settings(request)
        if request.segmentation is not None:
            self._check_capability("segmentation", pipeline.segmentation)
            profile = request.segmentation.profile
            entry = next((p for p in pipeline.segmentation.profiles if p.id == profile), None)
            if profile != "auto" and entry is not None:
                self._check_capability("segmentation", entry, label=f"segmentation profile {profile}")
        if request.parts is not None:
            self._check_capability("part_extraction", pipeline.part_extraction)
            strategy = request.parts.strategy
            entry = next((s for s in pipeline.part_extraction.strategies if s.id == strategy), None)
            if strategy is not None and entry is not None:
                self._check_capability("part_extraction", entry, label=f"part strategy {strategy}")

        needs_runs = source.kind == "prompt" or request.segmentation is not None or request.parts is not None
        if needs_runs:
            queue = self._runs.queue_status()
            if queue.pending >= queue.max_size:
                raise QueueFullError(
                    f"The run queue is full ({queue.max_size} pending runs)",
                    details={"max_size": queue.max_size, "queue": "runs"},
                )
        if request.reconstruction is not None:
            self._job_queue.ensure_capacity()
        return context

    @staticmethod
    def _check_capability(capability: str, entry: Any, *, label: str | None = None) -> None:
        if entry.available:
            return
        raise CapabilityUnavailableError(
            entry.reason or f"{(label or capability).replace('_', ' ').capitalize()} is not available",
            capability=capability,
            reasons=[{"model_id": None, "state": None, "reason_code": entry.code, "reason": entry.reason}],
        )

    def _check_engine_settings(self, request: WorkflowRequest) -> None:
        """``validate_against_capabilities`` (read-only) on the reconstruction settings: 422 before any child."""
        assert request.reconstruction is not None
        engine = getattr(self._jobs, "_engine", None)  # JobService has no public engine accessor (S1 interface)
        if engine is None:
            return
        settings = request.reconstruction.settings
        reference_mode = settings.reference_mode or self._settings.reference_mode
        capabilities = engine.capabilities()
        validate_against_capabilities(settings, reference_mode, capabilities)
        get_strategy(reference_mode).validate(capabilities)

    def _initial_steps(
        self, request: WorkflowRequest, node_ids: Mapping[str, str], context: Mapping[str, Any]
    ) -> list[WorkflowStep]:
        source = request.source
        assert source is not None
        now = utcnow()
        steps: list[WorkflowStep] = []
        for definition in WORKFLOW_STEPS:
            if definition.id == "text_to_image" and source.kind != "prompt":
                continue
            step = WorkflowStep(id=definition.id, node=definition.node, node_id=node_ids.get(definition.node))
            if definition.id == "import_image":
                step.node_id = node_ids.get("input_image") if source.kind == "image" else None
                if source.kind == "image":
                    step.status, step.progress, step.started_at, step.finished_at = "done", 100.0, now, now
                    step.resource = WorkflowResource(
                        kind="reference_image", id=context["image_id"], reference_set_id=context["reference_set_id"]
                    )
                elif source.kind == "job":
                    step.status, step.progress = "skipped", 100.0
            elif definition.id == "reconstruction" and source.kind == "job":
                job = context["job"]
                step.node_id = node_ids.get("job_result")
                step.status, step.progress, step.started_at, step.finished_at = "skipped", 100.0, now, now
                step.resource = WorkflowResource(kind="job", id=job.id)
                step.artifacts = self._job_artifacts(job)
            elif not step_applies(definition.id, request):
                step.status, step.progress = "skipped", 100.0
            steps.append(step)
        return steps

    def _apply_initial_outputs(
        self, record: WorkflowRecord, request: WorkflowRequest, context: Mapping[str, Any]
    ) -> None:
        outputs = record.outputs
        if "reference_set_id" in context:
            outputs.reference_set_id, outputs.image_id = context["reference_set_id"], context["image_id"]
        job = context.get("job")
        if job is not None:
            outputs.job_id = job.id
            outputs.reference_set_id, outputs.image_id = job.reference_set_id, job.primary_image_id
            result = self._jobs.to_out(job).result
            outputs.model_url = result.model_url if result is not None else None

    # ------------------------------------------------------------------ advance
    def _lock(self, wf_id: str) -> asyncio.Lock:
        lock = self._locks.get(wf_id)
        if lock is None:
            lock = self._locks[wf_id] = asyncio.Lock()
        return lock

    def _request_of(self, record: WorkflowRecord) -> WorkflowRequest:
        request = self._requests.get(record.id)
        if request is None:
            request = WorkflowRequest.model_validate(record.request)
            self._requests[record.id] = request
        return request

    async def advance(self, wf_id: str) -> None:
        """Idempotent: map child states onto steps, create the next child, persist and publish (section 4.7.4)."""
        async with self._lock(wf_id):
            record = self._store.get(wf_id)
            if record is None or not record.is_active:
                return
            await self._advance_locked(record, self._request_of(record))

    async def _advance_locked(self, record: WorkflowRecord, request: WorkflowRequest, *, initial: bool = False) -> None:
        before = _structure(record)
        ctx = WorkflowContext(service=self, request=request)
        index = 0
        while index < len(record.steps) and record.is_active:
            step = record.steps[index]
            if step.status in FINISHED_WORKFLOW_STEP_STATUSES:
                index += 1
                continue
            if step.status == "running":
                if not self._sync_running_step(record, step):
                    break  # still running, or the workflow failed
                index += 1
                continue
            definition = STEPS_BY_ID[step.id]
            try:
                ref = await definition.create_child(record, ctx)
            except Exception as exc:
                if initial:
                    raise
                self._fail_creation(record, step, exc)
                break
            now = utcnow()
            if record.status is JobStatus.QUEUED:
                record.status, record.started_at = JobStatus.RUNNING, now
            if ref is None:
                step.status, step.progress = "skipped", 100.0
                continue
            step.resource, step.started_at = ref.resource, now
            if ref.completed:
                step.status, step.progress, step.finished_at = "done", 100.0, now
                if step.id == "export":
                    self._finish_export(record)
                continue
            step.status, step.progress, step.message = "running", 0.0, None
            initial = False  # the first child exists: later refusals fail the workflow instead of the request
            self._persist(record, before)  # index the child id before anything else awaits
            before = _structure(record)
        if record.is_active and all(s.status in ("done", "skipped") for s in record.steps):
            now = utcnow()
            record.status, record.finished_at, record.current_step = JobStatus.COMPLETED, now, None
        self._refresh_progress(record, request)
        self._persist(record, before)

    def _sync_running_step(self, record: WorkflowRecord, step: WorkflowStep) -> bool:
        """Read the step's child; ``True`` when it completed (the step is done now)."""
        assert step.resource is not None
        state = self._child_state(step.resource)
        if state is None:
            self._fail(
                record, step,
                child_error={"code": "not_found", "message": f"The {step.resource.kind.replace('_', ' ')} "
                             "was deleted", "details": None},
            )
            return False
        step.progress = max(0.0, min(100.0, float(state.percent)))
        step.warning_count = state.warnings
        step.message = state.message
        if state.status in (JobStatus.QUEUED, JobStatus.RUNNING):
            return False
        if state.status is JobStatus.COMPLETED:
            now = utcnow()
            step.status, step.progress, step.finished_at = "done", 100.0, now
            self._finish_step(record, step, state.obj)
            return True
        child_error = state.error or {
            "code": "cancelled" if state.status is JobStatus.CANCELLED else "run_error",
            "message": f"The {step.resource.kind.replace('_', ' ')} was {state.status}",
            "details": None,
        }
        self._fail(record, step, child_error=child_error)
        return False

    def _finish_step(self, record: WorkflowRecord, step: WorkflowStep, child: Any) -> None:
        outputs = record.outputs
        if step.id == "text_to_image":
            if child.result is not None and child.candidate(0) is not None:
                url = f"{image_generation_url(child.id)}/candidates/0/thumbnail"
                step.artifacts = [WorkflowArtifact(role="candidate", url=url)]
        elif step.id == "segmentation":
            result = self._segmentations.to_out(child).result
            if result is not None:
                step.artifacts = [WorkflowArtifact(role="overlay", url=result.overlay_url)]
        elif step.id == "reconstruction":
            step.artifacts = self._job_artifacts(child)
            result = self._jobs.to_out(child).result
            outputs.model_url = result.model_url if result is not None else None
        elif step.id == "part_extraction":
            result = self._part_analyses.to_out(child).result
            if result is not None:
                front = result.preview_urls.get("front")
                step.artifacts = [WorkflowArtifact(role="preview", url=front)] if front else []
                outputs.parts_model_url, outputs.manifest_url = result.assembled_url, result.manifest_url

    def _finish_export(self, record: WorkflowRecord) -> None:
        outputs = record.outputs
        if outputs.model_url is None and outputs.job_id is not None:
            with contextlib.suppress(AppError):
                result = self._jobs.to_out(self._jobs.get_job(outputs.job_id)).result
                outputs.model_url = result.model_url if result is not None else None

    def _job_artifacts(self, job: Any) -> list[WorkflowArtifact]:
        result = self._jobs.to_out(job).result
        if result is None or result.preview_url is None:
            return []
        return [WorkflowArtifact(role="preview", url=result.preview_url)]

    def _fail(self, record: WorkflowRecord, step: WorkflowStep, *, child_error: Mapping[str, Any],
              code: str = STEP_FAILED_ERROR_CODE) -> None:
        now = utcnow()
        step.status = "cancelled" if child_error.get("code") == "cancelled" else "failed"
        step.finished_at = now
        for other in record.steps:
            if other.status == "pending":
                other.status = "skipped"
        label = STEP_LABELS[step.id]
        details = {
            "step": step.id,
            "child_kind": STEP_CHILD_KIND[step.id],
            "child_id": step.resource.id if step.resource is not None and step.resource.kind != "reference_image"
            else None,
            "child_error": {
                "code": str(child_error.get("code") or "run_error"),
                "message": str(child_error.get("message") or f"{label} failed"),
                "details": child_error.get("details"),
            },
        }
        if step.id == "import_image":
            ig_step = record.step("text_to_image")
            details["child_id"] = ig_step.resource.id if ig_step is not None and ig_step.resource else None
        record.status, record.finished_at, record.current_step = JobStatus.FAILED, now, step.id
        record.error = JobError(code=code, message=f"{label} failed: {details['child_error']['message']}",
                                details=details)
        record.progress.message = f"{label} failed"
        logger.warning("Workflow %s failed at %s (%s): %s", record.id, step.id, details["child_error"]["code"],
                       details["child_error"]["message"])

    def _fail_creation(self, record: WorkflowRecord, step: WorkflowStep, exc: BaseException) -> None:
        if isinstance(exc, AppError):
            child_error = {"code": exc.code, "message": exc.message, "details": exc.details}
        else:
            logger.exception("Creating the %s child of workflow %s crashed", step.id, record.id)
            child_error = {"code": "internal_error", "message": f"{type(exc).__name__}: {exc}", "details": None}
        step.resource = None if step.id != "import_image" else step.resource
        code = QUEUE_FULL_ERROR_CODE if isinstance(exc, QueueFullError) else STEP_FAILED_ERROR_CODE
        self._fail(record, step, child_error=child_error, code=code)

    def _refresh_progress(self, record: WorkflowRecord, request: WorkflowRequest) -> None:
        total = weighted = 0.0
        for step in record.steps:
            if not step_applies(step.id, request):
                continue
            weight = STEPS_BY_ID[step.id].weight
            total += weight
            if step.status in ("done", "skipped"):
                weighted += weight * 100.0
            elif step.status != "pending":
                weighted += weight * step.progress
        percent = round(weighted / total, 1) if total else 0.0
        progress = record.progress
        progress.percent = 100.0 if record.status is JobStatus.COMPLETED else min(percent, 100.0)
        progress.updated_at = utcnow()
        if record.status is JobStatus.COMPLETED:
            progress.message = "Completed"
        elif record.is_active:
            current = next((s for s in record.steps if s.status in ("running", "pending")), None)
            record.current_step = current.id if current is not None else None
            if current is not None:
                progress.message = current.message or STEP_LABELS[current.id]

    def _persist(self, record: WorkflowRecord, before: tuple[Any, ...]) -> None:
        structural = _structure(record) != before
        saved = self._store.save(record, durable=structural or record.is_terminal)
        if saved:
            self._events.publish(record, droppable=not structural)
        if record.is_terminal:
            self._requests.pop(record.id, None)

    # ------------------------------------------------------------------ children
    def _child_state(self, resource: WorkflowResource) -> _ChildState | None:
        try:
            if resource.kind == "job":
                job = self._jobs.get_job(resource.id)
                return _ChildState(job.status, job.progress.percent, job.progress.message or job.progress.stage_label,
                                   0, job.error.model_dump(mode="json") if job.error else None, job)
            getters: dict[str, Callable[[str], Any]] = {
                "image_generation": self._image_generations.get,
                "segmentation": self._segmentations.get,
                "part_analysis": self._part_analyses.get,
            }
            getter = getters.get(resource.kind)
            if getter is None:
                return None
            run = getter(resource.id)
        except NotFoundError:
            return None
        return _ChildState(run.status, run.progress.percent, run.progress.message, len(run.warnings),
                           run.error.model_dump(mode="json") if run.error else None, run)

    def _resource_exists(self, resource: WorkflowResource) -> bool:
        if resource.kind == "reference_image":
            if resource.reference_set_id is None:
                return False
            try:
                return self._references.get_set(resource.reference_set_id).get_image(resource.id) is not None
            except AppError:
                return False
        try:
            return self._child_state(resource) is not None
        except AppError:
            return False

    async def _create_image_generation(self, record: WorkflowRecord, request: WorkflowRequest) -> ChildRef | None:
        source = request.source
        if source is None or source.kind != "prompt":
            return None
        run = await self._image_generations.create(source.generation, workflow_id=record.id)
        return ChildRef(WorkflowResource(kind="image_generation", id=run.id))

    async def _import_candidate(self, record: WorkflowRecord) -> ChildRef | None:
        ig_step = record.step("text_to_image")
        if ig_step is None or ig_step.resource is None:
            return None
        response = await self._image_generations.use_candidate(ig_step.resource.id, 0, UseCandidateRequest())
        record.outputs.reference_set_id, record.outputs.image_id = response.set.id, response.image.id
        resource = WorkflowResource(kind="reference_image", id=response.image.id, reference_set_id=response.set.id)
        return ChildRef(resource, completed=True)

    async def _create_segmentation(self, record: WorkflowRecord, request: WorkflowRequest) -> ChildRef | None:
        if request.segmentation is None or request.source is None:
            return None
        outputs = record.outputs
        source: JobSegmentationSource | ReferenceImageSegmentationSource
        if request.source.kind == "job":
            source = JobSegmentationSource(kind="job", job_id=request.source.job_id)
        else:
            if outputs.reference_set_id is None or outputs.image_id is None:
                raise NotFoundError("The workflow has no imported image to segment")
            source = ReferenceImageSegmentationSource(
                kind="reference_image", reference_set_id=outputs.reference_set_id, image_id=outputs.image_id
            )
        run = await self._segmentations.create(
            SegmentationRequest(source=source, options=request.segmentation), workflow_id=record.id
        )
        outputs.segmentation_id = run.id
        return ChildRef(WorkflowResource(kind="segmentation", id=run.id))

    async def _create_job(self, record: WorkflowRecord, request: WorkflowRequest) -> ChildRef | None:
        if request.reconstruction is None or request.source is None or request.source.kind == "job":
            return None
        outputs = record.outputs
        if outputs.reference_set_id is None:
            raise NotFoundError("The workflow has no reference set to reconstruct")
        job = await self._jobs.create_job(
            CreateJobRequest(
                reference_set_id=outputs.reference_set_id,
                primary_image_id=outputs.image_id,
                settings=request.reconstruction.settings,
            )
        )
        outputs.job_id = job.id
        return ChildRef(WorkflowResource(kind="job", id=job.id))

    async def _create_part_analysis(self, record: WorkflowRecord, request: WorkflowRequest) -> ChildRef | None:
        if request.parts is None:
            return None
        outputs = record.outputs
        if outputs.job_id is None:
            raise NotFoundError("The workflow has no job to analyse")
        body = request.parts.model_dump(mode="json", exclude_unset=True, exclude={"segmentation_id"})
        if outputs.segmentation_id is not None:
            body["segmentation_id"] = outputs.segmentation_id
        options = PartAnalysisOptions.model_validate(body)
        # S10's create() refuses a segmentation_id together with workflow_id (422); the workflow's own sg_ is the
        # documented exception (section 4.7.1), so the link is made through segmentation_id alone in that case.
        workflow_id = record.id if outputs.segmentation_id is None else None
        run = await self._part_analyses.create(outputs.job_id, options, workflow_id=workflow_id)
        outputs.part_analysis_id = run.id
        return ChildRef(WorkflowResource(kind="part_analysis", id=run.id))

    # ------------------------------------------------------------------ cancel / delete
    async def cancel(self, wf_id: str) -> WorkflowRecord:
        require_feature(self._settings, "workflows")
        self.get(wf_id)  # 404
        async with self._lock(wf_id):
            record = self.get(wf_id)
            if record.is_terminal:
                raise ConflictError(
                    f"The workflow has already finished ({record.status})",
                    details={"workflow_id": wf_id, "status": str(record.status), "reason": "terminal"},
                )
            before = _structure(record)
            record.cancel_requested = True
            now = utcnow()
            for step in record.steps:
                if step.status == "running":
                    if step.resource is not None:
                        await self._cancel_child(step.resource)
                    step.status, step.finished_at, step.message = "cancelled", now, "Cancelled"
                elif step.status == "pending":
                    step.status = "skipped"
            record.status, record.finished_at = JobStatus.CANCELLED, now
            record.progress.message = "Cancelled"
            record.progress.updated_at = now
            self._persist(record, before)
        logger.info("Workflow %s cancelled", wf_id)
        return record

    async def _cancel_child(self, resource: WorkflowResource) -> None:
        cancellers: dict[str, Callable[[str], Awaitable[Any]]] = {
            "image_generation": self._image_generations.cancel,
            "segmentation": self._segmentations.cancel,
            "job": self._jobs.cancel_job,
            "part_analysis": self._part_analyses.cancel,
        }
        cancel = cancellers.get(resource.kind)
        if cancel is None:
            return
        try:
            await cancel(resource.id)
        except AppError as exc:  # already finished, deleted or the feature was switched off meanwhile
            logger.info("Cancelling %s %s for a workflow: %s", resource.kind, resource.id, exc.message)

    async def delete(self, wf_id: str) -> None:
        require_feature(self._settings, "workflows")
        record = self.get(wf_id)
        if record.is_active:
            raise ConflictError(
                "The workflow is still active; cancel it first",
                details={"workflow_id": wf_id, "status": str(record.status), "reason": "active"},
            )
        async with self._lock(wf_id):
            deleted = await asyncio.to_thread(self._store.delete, wf_id)
        if not deleted:
            raise ConflictError("The workflow could not be deleted", details={"workflow_id": wf_id})
        self._events.forget(wf_id)
        self._locks.pop(wf_id, None)
        self._requests.pop(wf_id, None)
        logger.info("Workflow %s deleted (its children are kept)", wf_id)


def _structure(record: WorkflowRecord) -> tuple[Any, ...]:
    """The parts of a record whose change must be persisted (not progress-only updates)."""
    return (
        record.status,
        record.current_step,
        tuple((s.status, s.resource.id if s.resource else None, len(s.artifacts), s.warning_count)
              for s in record.steps),
        record.outputs.model_dump_json(),
        record.error.code if record.error else None,
    )


__all__ = [
    "WORKFLOW_STEPS",
    "ChildRef",
    "WorkflowContext",
    "WorkflowService",
    "WorkflowStepDef",
    "step_applies",
]
