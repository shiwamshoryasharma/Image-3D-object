"""Workflow schemas: ``/workflows``, ``/workflows/validate``, ``/workflows/catalog`` (part-aware DESIGN.md sections 4.7
and 6.5). Frozen in slice S1.

A :class:`WorkflowRequest` comes in one of two forms:

* the **simple form**: ``source`` (+ ``reconstruction``, optional ``parts`` and ``segmentation``);
* the **graph form**: a typed Node-UI ``graph`` (+ opaque ``layout``), compiled server-side into the simple form.

Rules enforced here (422 ``validation_error``; details type ``conflicting_fields`` unless noted):

* ``graph`` cannot be combined with ``source``, ``segmentation``, ``reconstruction`` or ``parts`` (present keys, even
  ``null``, count);
* without ``graph`` a ``source`` is required (type ``missing``);
* ``reconstruction`` is required for image and prompt sources (type ``missing``) and must be null for a job source;
* ``segmentation`` (a ``sg_`` before reconstruction) requires ``parts`` (``loc: ["body", "segmentation"]``);
* ``parts.segmentation_id`` must be null (``loc: ["body", "parts", "segmentation_id"]``): the workflow links its own
  ``sg_``;
* a prompt source generates exactly one candidate (``num_images`` ≤ 1, pydantic's ``less_than_equal``);
* there is no top-level ``export`` (``extra_forbidden``): export options live in ``parts.export``.

Graph limits (node/edge counts, serialized bytes), node types, ports and per-node params are checked by the compiler
and reported as ``GraphIssue``\\ s (``POST /workflows/validate``) or as ``invalid_workflow`` / ``graph_too_large``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Final, Literal, get_args

from pydantic import ConfigDict, Field, ValidationInfo, field_validator, model_validator
from pydantic_core import PydanticCustomError

from inference.providers.base import ReasonCode
from models.enums import JobStatus
from models.types import UtcDateTime
from models.workflow import (
    WorkflowChildKind,
    WorkflowRecord,
    WorkflowResource,
    WorkflowResourceKind,
    WorkflowStep,
    WorkflowStepId,
    WorkflowStepStatus,
)
from schemas.common import ApiModel, RequestModel
from schemas.image_generation import WorkflowImageGenerationRequest
from schemas.jobs import GenerationSettings
from schemas.parts import PartAnalysisOptions
from schemas.segmentation import SegmentationOptions

WORKFLOW_GRAPH_SCHEMA: Final = "img3d.workflow_graph"
WORKFLOW_GRAPH_VERSION: Final = 1
WORKFLOW_CATALOG_SCHEMA: Final = "img3d.workflow_catalog/1"
WORKFLOW_NAME_MAX_LENGTH: Final = 120

NODE_ID_PATTERN: Final = r"^[a-z][a-z0-9_]{0,31}$"
MAX_GRAPH_NODES: Final = 32
MAX_GRAPH_EDGES: Final = 64
DEFAULT_GRAPH_MAX_BYTES: Final = 65536
"""Default of ``settings.workflow_graph_max_bytes`` (the limit is enforced by the compiler/service)."""

PortType = Literal["image_set", "segmentation", "mesh", "part_set"]
NodeCategory = Literal["input", "analyze", "generate", "parts", "output", "refine"]
WorkflowMode = Literal["job", "workflow"]
"""``job``: the graph is exactly input_image → image_to_3d → export (no parts); the client posts its own
``CreateJobRequest`` to ``POST /jobs``. ``workflow``: post the request to ``POST /workflows``."""
IssueSeverity = Literal["error", "warning"]

GraphIssueCode = Literal[
    "graph_too_large",
    "unknown_node_type",
    "unknown_port",
    "type_mismatch",
    "port_already_connected",
    "missing_required_input",
    "no_source",
    "multiple_sources",
    "cycle",
    "too_many_instances",
    "node_locked",
    "unsupported_topology",
    "capability_unavailable",
    "feature_disabled",
    "invalid_param",
    "not_found",
    "no_sink",
]
GRAPH_ISSUE_CODES: Final[tuple[str, ...]] = get_args(GraphIssueCode)
"""Every compiler issue code (section 4.7.2); ``no_sink`` is the only warning."""

_SIMPLE_FORM_FIELDS: Final = ("source", "segmentation", "reconstruction", "parts")


# --------------------------------------------------------------------------------------------------
# Graph (Node UI)
# --------------------------------------------------------------------------------------------------


class GraphPortRef(RequestModel):
    node: str
    port: str


class GraphEdge(RequestModel):
    """A connection ``from`` an output port ``to`` an input port (``from`` is a Python keyword: field ``from_``)."""

    model_config = ConfigDict(extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True)

    from_: GraphPortRef = Field(alias="from")
    to: GraphPortRef


class GraphNode(RequestModel):
    id: str = Field(pattern=NODE_ID_PATTERN)
    type: str = Field(min_length=1, max_length=64)
    """A catalog node type; unknown types are a compiler issue (``unknown_node_type``), not a 422."""
    params: dict[str, Any] = Field(default_factory=dict)
    """Validated by the compiler with the node type's params model (``invalid_param``)."""


class WorkflowGraph(RequestModel):
    """``img3d.workflow_graph`` v1."""

    model_config = ConfigDict(extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True)

    schema_id: Literal["img3d.workflow_graph"] = Field(default=WORKFLOW_GRAPH_SCHEMA, alias="schema")
    version: Literal[1] = WORKFLOW_GRAPH_VERSION
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)

    def node(self, node_id: str) -> GraphNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)


# --------------------------------------------------------------------------------------------------
# Node params models (the ones not shared with other routes)
# --------------------------------------------------------------------------------------------------


class InputImageParams(RequestModel):
    """Params of the ``input_image`` node: a reference set (and optionally its primary image)."""

    reference_set_id: str
    primary_image_id: str | None = None


class JobResultParams(RequestModel):
    """Params of the ``job_result`` node: rebuild the parts of a completed job."""

    job_id: str


# --------------------------------------------------------------------------------------------------
# Simple form
# --------------------------------------------------------------------------------------------------


class ImageWorkflowSource(RequestModel):
    """Reconstruct an uploaded reference set (the ``input_image`` node)."""

    kind: Literal["image"]
    reference_set_id: str
    primary_image_id: str | None = None


class PromptWorkflowSource(RequestModel):
    """Generate the image first (the ``prompt`` node); candidate 0 is imported automatically (``auto_first``)."""

    kind: Literal["prompt"]
    generation: WorkflowImageGenerationRequest


class JobWorkflowSource(RequestModel):
    """Rebuild the parts of a completed job (the ``job_result`` node); no reconstruction."""

    kind: Literal["job"]
    job_id: str


WorkflowSource = Annotated[
    ImageWorkflowSource | PromptWorkflowSource | JobWorkflowSource, Field(discriminator="kind")
]


class ReconstructionOptions(RequestModel):
    """The image-to-3D step: the existing ``GenerationSettings``, unchanged."""

    settings: GenerationSettings = Field(default_factory=GenerationSettings)


class WorkflowPartAnalysisOptions(PartAnalysisOptions):
    """``PartAnalysisOptions`` inside a workflow: ``segmentation_id`` must stay null (the workflow links its own
    ``sg_``).

    ``from_attributes`` lets the compiler and services pass a plain :class:`PartAnalysisOptions`; it is re-validated,
    so a non-null ``segmentation_id`` is still refused. JSON bodies are unaffected (unknown keys stay 422).
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    @field_validator("segmentation_id")
    @classmethod
    def _no_segmentation_id(cls, value: str | None) -> str | None:
        if value is not None:
            raise PydanticCustomError(
                "conflicting_fields",
                "parts.segmentation_id must be null inside a workflow; use the top-level 'segmentation' instead",
            )
        return value


class WorkflowRequest(RequestModel):
    """``POST /workflows`` and ``POST /workflows/validate`` body (see the module docstring for the rules).

    ``parts`` is declared before ``segmentation`` so that the ``segmentation`` check can see it; the wire format is
    a JSON object, so the declaration order has no other effect.
    """

    name: str | None = Field(default=None, max_length=WORKFLOW_NAME_MAX_LENGTH)
    source: WorkflowSource | None = None
    reconstruction: ReconstructionOptions | None = Field(default=None, validate_default=True)
    """Required for image and prompt sources; must be null for a job source."""
    parts: WorkflowPartAnalysisOptions | None = None
    """Non-null → a ``pa_`` after reconstruction; ``parts.export`` carries the export options."""
    segmentation: SegmentationOptions | None = None
    """Non-null → a ``sg_`` before reconstruction; requires ``parts``."""
    graph: WorkflowGraph | None = None
    layout: dict[str, Any] | None = None
    """Opaque Node-UI layout (≤ ``workflow_graph_max_bytes``), stored and returned, never interpreted."""

    @model_validator(mode="before")
    @classmethod
    def _graph_excludes_simple_form(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("graph") is not None:
            present = [key for key in _SIMPLE_FORM_FIELDS if key in data]
            if present:
                raise PydanticCustomError(
                    "conflicting_fields",
                    "'graph' cannot be combined with {fields}",
                    {"fields": ", ".join(present)},
                )
        return data

    @field_validator("reconstruction")
    @classmethod
    def _reconstruction_for_source(
        cls, value: ReconstructionOptions | None, info: ValidationInfo
    ) -> ReconstructionOptions | None:
        source = info.data.get("source")
        if source is None:
            return value
        if source.kind == "job" and value is not None:
            raise PydanticCustomError(
                "conflicting_fields", "reconstruction must be null for a job source (the job is already built)"
            )
        if source.kind != "job" and value is None:
            raise PydanticCustomError(
                "missing", "Field required: reconstruction is required for {kind} sources", {"kind": source.kind}
            )
        return value

    @field_validator("segmentation")
    @classmethod
    def _segmentation_needs_parts(
        cls, value: SegmentationOptions | None, info: ValidationInfo
    ) -> SegmentationOptions | None:
        if value is not None and "parts" in info.data and info.data["parts"] is None:
            raise PydanticCustomError(
                "conflicting_fields",
                "segmentation before reconstruction requires 'parts'; use POST /segmentations for a preview",
            )
        return value

    @model_validator(mode="after")
    def _source_or_graph(self) -> WorkflowRequest:
        if self.graph is None and self.source is None:
            raise PydanticCustomError("missing", "Field required: either 'source' or 'graph'")
        return self

    def simple_form(self) -> dict[str, Any]:
        """The normalized simple form stored as ``WorkflowRecord.request`` (JSON-ready, without graph/layout)."""
        return self.model_dump(mode="json", exclude={"graph", "layout"})


# --------------------------------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------------------------------


class WorkflowResourceOut(ApiModel):
    kind: WorkflowResourceKind
    id: str
    reference_set_id: str | None = None
    exists: bool
    """Computed at read time (children are normal history and may be deleted independently)."""


class WorkflowArtifactOut(ApiModel):
    role: str
    url: str


class WorkflowStepOut(ApiModel):
    id: WorkflowStepId
    node: str
    node_id: str | None
    status: WorkflowStepStatus
    message: str | None = None
    progress: float = 0.0
    """0..100 (the child's ``progress.percent``)."""
    warning_count: int = 0
    resource: WorkflowResourceOut | None
    artifacts: list[WorkflowArtifactOut]

    @classmethod
    def from_domain(cls, step: WorkflowStep, *, resource_exists: Callable[[WorkflowResource], bool]) -> WorkflowStepOut:
        resource = step.resource
        return cls(
            id=step.id,
            node=step.node,
            node_id=step.node_id,
            status=step.status,
            message=step.message,
            progress=step.progress,
            warning_count=step.warning_count,
            resource=WorkflowResourceOut(
                kind=resource.kind,
                id=resource.id,
                reference_set_id=resource.reference_set_id,
                exists=resource_exists(resource),
            )
            if resource is not None
            else None,
            artifacts=[WorkflowArtifactOut(role=a.role, url=a.url) for a in step.artifacts],
        )


class WorkflowProgressOut(ApiModel):
    percent: float
    message: str | None
    updated_at: UtcDateTime | None


class WorkflowOutputsOut(ApiModel):
    reference_set_id: str | None
    image_id: str | None
    segmentation_id: str | None
    job_id: str | None
    part_analysis_id: str | None
    model_url: str | None
    parts_model_url: str | None
    manifest_url: str | None


class ChildErrorOut(ApiModel):
    """The ``JobError`` shape of a failed child (or of the refused create call)."""

    code: str
    message: str
    details: Any | None = None


class StepFailedDetails(ApiModel):
    """``error.details`` of a workflow with ``error.code == "step_failed"`` (section 4.7.4)."""

    step: WorkflowStepId
    child_kind: WorkflowChildKind
    child_id: str | None
    """``None`` when the child could not be created."""
    child_error: ChildErrorOut


class WorkflowErrorOut(ApiModel):
    code: str
    message: str
    details: StepFailedDetails | Any | None = None
    """:class:`StepFailedDetails` for ``step_failed``; otherwise free-form (or ``None``)."""


class WorkflowOut(ApiModel):
    id: str
    name: str | None
    status: JobStatus
    created_at: UtcDateTime
    started_at: UtcDateTime | None
    finished_at: UtcDateTime | None
    request: dict[str, Any]
    """The normalized simple-form request (without ``graph``/``layout``)."""
    graph_stored: bool
    current_step: WorkflowStepId | None
    progress: WorkflowProgressOut
    steps: list[WorkflowStepOut]
    outputs: WorkflowOutputsOut
    error: WorkflowErrorOut | None

    @classmethod
    def from_domain(
        cls, record: WorkflowRecord, *, resource_exists: Callable[[WorkflowResource], bool]
    ) -> WorkflowOut:
        return cls(
            id=record.id,
            name=record.name,
            status=record.status,
            created_at=record.created_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
            request=dict(record.request),
            graph_stored=record.graph_stored,
            current_step=record.current_step,
            progress=WorkflowProgressOut.model_validate(record.progress.model_dump()),
            steps=[WorkflowStepOut.from_domain(s, resource_exists=resource_exists) for s in record.steps],
            outputs=WorkflowOutputsOut.model_validate(record.outputs.model_dump()),
            error=WorkflowErrorOut.model_validate(record.error.model_dump()) if record.error is not None else None,
        )


class WorkflowList(ApiModel):
    items: list[WorkflowOut]


class WorkflowGraphOut(ApiModel):
    """``GET /workflows/{wf_id}/graph``."""

    graph: WorkflowGraph | None
    layout: dict[str, Any] | None


class GraphIssueOut(ApiModel):
    severity: IssueSeverity
    code: GraphIssueCode
    message: str
    node_id: str | None = None
    port: str | None = None
    edge_index: int | None = None
    details: dict[str, Any] | None = None


class ValidationReport(ApiModel):
    """``POST /workflows/validate`` (never creates anything). For ``mode == "job"`` the client posts its own
    ``CreateJobRequest`` to ``POST /jobs``; no server copy is returned."""

    valid: bool
    issues: list[GraphIssueOut]
    mode: WorkflowMode | None
    request: WorkflowRequest | None
    """The normalized simple form the graph compiles to (``None`` when invalid)."""


# --------------------------------------------------------------------------------------------------
# Catalog (GET /workflows/catalog)
# --------------------------------------------------------------------------------------------------


class PortTypeOut(ApiModel):
    id: PortType
    label: str


class CatalogInputPortOut(ApiModel):
    id: str
    label: str
    accepts: list[PortType]
    required: bool
    multiple: bool


class CatalogOutputPortOut(ApiModel):
    id: str
    label: str
    type: PortType


class CatalogNodeTypeOut(ApiModel):
    type: str
    version: int
    category: NodeCategory
    title: str
    inputs: list[CatalogInputPortOut]
    outputs: list[CatalogOutputPortOut]
    params_schema: dict[str, Any]
    """``model_json_schema()`` of the node's params model; always has ``properties``."""
    defaults: dict[str, Any]
    compiles_to: str | None
    max_instances: int
    available: bool
    locked: bool
    code: ReasonCode
    reason: str | None
    hidden_by_default: bool


class CatalogTemplateOut(ApiModel):
    id: str
    title: str
    graph: WorkflowGraph


class CatalogLimitsOut(ApiModel):
    max_nodes: int
    max_edges: int
    graph_max_bytes: int


class CatalogOut(ApiModel):
    """``GET /workflows/catalog`` (``ETag`` = :attr:`etag`, the sha256 of the canonical JSON without it)."""

    model_config = ConfigDict(
        extra="ignore", from_attributes=True, validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    schema_id: Literal["img3d.workflow_catalog/1"] = Field(default=WORKFLOW_CATALOG_SCHEMA, alias="schema")
    etag: str
    port_types: list[PortTypeOut]
    node_types: list[CatalogNodeTypeOut]
    templates: list[CatalogTemplateOut]
    legacy_job_node_map: dict[str, str]
    limits: CatalogLimitsOut
