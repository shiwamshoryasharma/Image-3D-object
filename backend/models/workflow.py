"""Workflow (``wf_``) domain record (part-aware DESIGN.md section 4.7). Frozen in slice S1.

A workflow is a server-side, linear, fail-fast chain of child resources (``ig_`` → reference image → ``sg_`` →
``job_`` → ``pa_`` → export URLs). It runs on the event loop only and never touches the GPU itself. Persisted as
``runtime/workflows/<wf>/workflow.json`` (plus ``graph.json`` / ``layout.json`` when a Node-UI graph was posted).
Children are normal resources: deleting a workflow removes only this record.

Statuses reuse :class:`~models.enums.JobStatus`. ``WorkflowStep.progress`` is 0..100 (the child's percent), unlike
``RunStep.progress`` (0..1).
"""

from __future__ import annotations

from typing import Any, Final, Literal

from pydantic import Field

from models.enums import TERMINAL_STATUSES, JobStatus
from models.job import JobError
from models.types import DomainModel, UtcDateTime

WORKFLOW_PREFIX: Final = "wf"

WorkflowStepId = Literal["text_to_image", "import_image", "segmentation", "reconstruction", "part_extraction", "export"]
WorkflowStepStatus = Literal["pending", "running", "done", "failed", "skipped", "cancelled"]
WorkflowResourceKind = Literal["image_generation", "reference_image", "segmentation", "job", "part_analysis"]
WorkflowChildKind = Literal["image_generation", "segmentation", "job", "part_analysis"]
"""Kinds of child resource a step can create (``reference_image`` is an import, not a child run)."""

WORKFLOW_STEP_IDS: Final[tuple[WorkflowStepId, ...]] = (
    "text_to_image",
    "import_image",
    "segmentation",
    "reconstruction",
    "part_extraction",
    "export",
)
"""Step ids in execution order (section 4.7.3)."""
WORKFLOW_CHILD_KINDS: Final[frozenset[str]] = frozenset({"image_generation", "segmentation", "job", "part_analysis"})
FINISHED_WORKFLOW_STEP_STATUSES: Final[frozenset[str]] = frozenset({"done", "failed", "skipped", "cancelled"})

STEP_FAILED_ERROR_CODE: Final = "step_failed"
"""``error.code`` of a workflow whose child failed or could not be created (``error.details`` = StepFailedDetails)."""
QUEUE_FULL_ERROR_CODE: Final = "queue_full"
INTERRUPTED_ERROR_CODE: Final = "interrupted"
SELECTION_AUTO_FIRST: Final = "auto_first"
"""The only candidate selection mode of v1: the workflow imports candidate 0."""


class WorkflowResource(DomainModel):
    """The resource a step created or used. ``exists`` is computed at read time, never stored."""

    kind: WorkflowResourceKind
    id: str
    reference_set_id: str | None = None
    """Set for ``reference_image`` resources."""


class WorkflowArtifact(DomainModel):
    """A URL a finished step produced (node thumbnails in the Node UI): ``candidate``, ``overlay``, ``preview``…"""

    role: str
    url: str


class WorkflowStep(DomainModel):
    id: WorkflowStepId
    node: str
    """Catalog node type that owns the step (``prompt``, ``input_image``, ``segmentation``, ``image_to_3d``…)."""
    node_id: str | None = None
    """Graph node id; ``None`` for steps implicit in a simple-form request."""
    status: WorkflowStepStatus = "pending"
    message: str | None = None
    progress: float = 0.0
    """0..100: the child's ``progress.percent`` (100 when done or skipped)."""
    warning_count: int = 0
    """``len(child.warnings)`` of the step's ``ig_``/``sg_``/``pa_`` child; 0 for the other steps."""
    resource: WorkflowResource | None = None
    artifacts: list[WorkflowArtifact] = Field(default_factory=list)
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None


class WorkflowProgress(DomainModel):
    percent: float = 0.0
    """Σ(step weight × child percent) / Σ weights of the present steps."""
    message: str | None = None
    updated_at: UtcDateTime | None = None


class WorkflowOutputs(DomainModel):
    """What the chain produced so far (outputs already produced stay set when a later step fails)."""

    reference_set_id: str | None = None
    image_id: str | None = None
    segmentation_id: str | None = None
    job_id: str | None = None
    part_analysis_id: str | None = None
    model_url: str | None = None
    parts_model_url: str | None = None
    manifest_url: str | None = None


class WorkflowRecord(DomainModel):
    id: str
    name: str | None = None
    status: JobStatus = JobStatus.QUEUED
    created_at: UtcDateTime
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None
    request: dict[str, Any] = Field(default_factory=dict)
    """The normalized simple-form ``WorkflowRequest`` (without ``graph``/``layout``)."""
    graph_stored: bool = False
    """``graph.json`` (and ``layout.json``) were stored next to the record."""
    current_step: WorkflowStepId | None = None
    progress: WorkflowProgress = Field(default_factory=WorkflowProgress)
    steps: list[WorkflowStep] = Field(default_factory=list)
    outputs: WorkflowOutputs = Field(default_factory=WorkflowOutputs)
    error: JobError | None = None
    cancel_requested: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def is_active(self) -> bool:
        return self.status in (JobStatus.QUEUED, JobStatus.RUNNING)

    def step(self, step_id: str) -> WorkflowStep | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def child_ids(self) -> frozenset[str]:
        """Ids of every child run or job (``ig_``/``sg_``/``job_``/``pa_``) this workflow created or used."""
        ids = {s.resource.id for s in self.steps if s.resource is not None and s.resource.kind in WORKFLOW_CHILD_KINDS}
        ids.update(
            value
            for value in (self.outputs.segmentation_id, self.outputs.job_id, self.outputs.part_analysis_id)
            if value is not None
        )
        return frozenset(ids)
