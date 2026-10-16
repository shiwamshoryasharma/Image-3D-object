"""Static workflow node catalog (part-aware DESIGN.md sections 4.7.2 and 6.5). Frozen in slice S1.

The single description of every Node-UI node type: ports, params model (and its JSON schema and defaults), what it
compiles to in the simple-form ``WorkflowRequest``, the capability that gates it, the locked future-tuning nodes and
the templates. ``/features.nodes`` (feature service), ``GET /workflows/catalog`` and the compiler all read it, so the
four UI modes and the server agree on one catalog.

Availability is not decided here. Dynamic nodes name the ``/features`` pipeline capability they need
(``NodeTypeDef.requires``); :func:`resolve_node_states` maps the current capability states onto nodes. Locked and
unsupported nodes carry a static state: the four refine nodes are ``locked`` with ``code: "not_approved"`` and
``hidden_by_default: true`` (tuning stays paused, Q3) and no executor exists for them.

Extension rule (for a future, approved node type; nothing of it is implemented now): one :data:`NODE_TYPES` entry with
its params model and ``compiles_to``, one optional ``WorkflowRequest`` field and one ``WORKFLOW_STEPS`` row in
``services/workflow_service.py``; the compiler core is not edited.

Torch-free; no I/O.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Literal, Protocol

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from inference.providers.base import ReasonCode
from models.enums import JobStage
from schemas.features import FeatureNodeOut
from schemas.image_generation import WorkflowImageGenerationRequest
from schemas.jobs import GenerationSettings
from schemas.parts import PartExportOptions, PartExtractionOptions
from schemas.segmentation import SegmentationOptions
from schemas.workflows import (
    DEFAULT_GRAPH_MAX_BYTES,
    MAX_GRAPH_EDGES,
    MAX_GRAPH_NODES,
    WORKFLOW_CATALOG_SCHEMA,
    CatalogInputPortOut,
    CatalogLimitsOut,
    CatalogNodeTypeOut,
    CatalogOut,
    CatalogOutputPortOut,
    CatalogTemplateOut,
    GraphEdge,
    GraphNode,
    GraphPortRef,
    InputImageParams,
    JobResultParams,
    NodeCategory,
    PortType,
    PortTypeOut,
    WorkflowGraph,
)

CapabilityKey = Literal["image_to_3d", "prompt_to_3d", "segmentation", "part_extraction"]
"""``/features.pipeline`` entries a node can depend on."""
SourceKind = Literal["image", "prompt", "job"]

LOCKED_CODE: Final[ReasonCode] = "not_approved"
LOCKED_REASON: Final = "Paused until approved"
EMPTY_PARAMS_SCHEMA: Final[Mapping[str, Any]] = MappingProxyType({"type": "object", "properties": {}})


# --------------------------------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PortTypeDef:
    id: PortType
    label: str


@dataclass(frozen=True, slots=True)
class InputPortDef:
    id: str
    label: str
    accepts: tuple[PortType, ...]
    required: bool
    multiple: bool = False


@dataclass(frozen=True, slots=True)
class OutputPortDef:
    id: str
    label: str
    type: PortType


@dataclass(frozen=True, slots=True)
class NodeState:
    """Availability of one node type right now (same fields as every ``/features`` capability entry)."""

    available: bool
    locked: bool
    code: ReasonCode
    reason: str | None


NODE_AVAILABLE: Final = NodeState(available=True, locked=False, code="ok", reason=None)


class CapabilityState(Protocol):
    """Anything shaped like a ``/features`` capability entry (e.g. ``schemas.features.CapabilityOut``)."""

    @property
    def available(self) -> bool: ...

    @property
    def code(self) -> ReasonCode: ...

    @property
    def reason(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class NodeTypeDef:
    """One catalog node type."""

    type: str
    title: str
    category: NodeCategory
    inputs: tuple[InputPortDef, ...]
    outputs: tuple[OutputPortDef, ...]
    params_model: type[BaseModel] | None
    """Pydantic model the compiler validates ``params`` with (``None`` = paramless)."""
    compiles_to: str | None
    """Simple-form field the node compiles to: ``source``, ``segmentation``, ``reconstruction``, ``parts`` or
    ``parts.export``; ``None`` for nodes without an executor."""
    requires: CapabilityKey | None = None
    """``/features.pipeline`` capability that gates the node (``None`` = always available unless static)."""
    source_kind: SourceKind | None = None
    """``source.kind`` of a source node."""
    static_state: NodeState | None = None
    """Fixed state of locked and unsupported nodes."""
    hidden_by_default: bool = False
    version: int = 1
    max_instances: int = 1

    @property
    def is_source(self) -> bool:
        return self.source_kind is not None

    @property
    def is_locked(self) -> bool:
        return self.static_state is not None and self.static_state.locked

    def params_schema(self) -> dict[str, Any]:
        """``model_json_schema()`` of the params model; ``{"type": "object", "properties": {}}`` when paramless."""
        if self.params_model is None:
            return {"type": "object", "properties": {}}
        schema = self.params_model.model_json_schema()
        schema.setdefault("properties", {})
        return schema

    def defaults(self) -> dict[str, Any]:
        """JSON values of every params field that has a default (required fields are omitted)."""
        if self.params_model is None:
            return {}
        values = {
            name: info.get_default(call_default_factory=True)
            for name, info in self.params_model.model_fields.items()
            if not info.is_required()
        }
        return to_jsonable_python(values)


# --------------------------------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------------------------------

PORT_TYPES: Final[tuple[PortTypeDef, ...]] = (
    PortTypeDef("image_set", "Image"),
    PortTypeDef("segmentation", "Masks"),
    PortTypeDef("mesh", "Mesh"),
    PortTypeDef("part_set", "Parts"),
)

_IMAGES_IN: Final = InputPortDef("images", "Image", ("image_set",), required=True)
_IMAGES_OUT: Final = OutputPortDef("images", "Image", "image_set")
_MESH_OUT: Final = OutputPortDef("mesh", "Mesh", "mesh")
_PARTS_IN: Final = InputPortDef("parts", "Parts", ("part_set",), required=True)
_PARTS_OUT: Final = OutputPortDef("parts", "Parts", "part_set")


def _locked(type_: str, title: str) -> NodeTypeDef:
    return NodeTypeDef(
        type=type_,
        title=title,
        category="refine",
        inputs=(_PARTS_IN,),
        outputs=(_PARTS_OUT,),
        params_model=None,
        compiles_to=None,
        static_state=NodeState(available=False, locked=True, code=LOCKED_CODE, reason=LOCKED_REASON),
        hidden_by_default=True,
    )


def _unsupported(type_: str, title: str, inputs: tuple[InputPortDef, ...], reason: str) -> NodeTypeDef:
    return NodeTypeDef(
        type=type_,
        title=title,
        category="parts",
        inputs=inputs,
        outputs=(_PARTS_OUT,),
        params_model=None,
        compiles_to=None,
        static_state=NodeState(available=False, locked=False, code="unsupported", reason=reason),
        hidden_by_default=True,
    )


NODE_TYPES: Final[tuple[NodeTypeDef, ...]] = (
    NodeTypeDef(
        type="input_image",
        title="Input Image",
        category="input",
        inputs=(),
        outputs=(_IMAGES_OUT,),
        params_model=InputImageParams,
        compiles_to="source",
        source_kind="image",
    ),
    NodeTypeDef(
        type="prompt",
        title="Prompt",
        category="input",
        inputs=(),
        outputs=(_IMAGES_OUT,),
        params_model=WorkflowImageGenerationRequest,
        compiles_to="source",
        requires="prompt_to_3d",
        source_kind="prompt",
    ),
    NodeTypeDef(
        type="job_result",
        title="Existing Result",
        category="input",
        inputs=(),
        outputs=(_IMAGES_OUT, _MESH_OUT),
        params_model=JobResultParams,
        compiles_to="source",
        source_kind="job",
    ),
    NodeTypeDef(
        type="segmentation",
        title="Segmentation",
        category="analyze",
        inputs=(_IMAGES_IN,),
        outputs=(OutputPortDef("masks", "Masks", "segmentation"),),
        params_model=SegmentationOptions,
        compiles_to="segmentation",
        requires="segmentation",
    ),
    NodeTypeDef(
        type="image_to_3d",
        title="Image-to-3D",
        category="generate",
        inputs=(_IMAGES_IN,),
        outputs=(_MESH_OUT,),
        params_model=GenerationSettings,
        compiles_to="reconstruction",
        requires="image_to_3d",
    ),
    NodeTypeDef(
        type="part_extraction",
        title="Part Extraction",
        category="parts",
        inputs=(
            InputPortDef("mesh", "Mesh", ("mesh",), required=True),
            InputPortDef("masks", "Masks", ("segmentation",), required=False),
        ),
        outputs=(_PARTS_OUT,),
        params_model=PartExtractionOptions,
        compiles_to="parts",
        requires="part_extraction",
    ),
    NodeTypeDef(
        type="export",
        title="Export",
        category="output",
        inputs=(InputPortDef("asset", "Asset", ("mesh", "part_set"), required=True),),
        outputs=(),
        params_model=PartExportOptions,
        compiles_to="parts.export",
    ),
    _locked("part_refine", "Part Refine"),
    _locked("mesh_tune", "Mesh Tune"),
    _locked("eye_correction", "Eye Correction"),
    _locked("rig_correction", "Rig Correction"),
    _unsupported(
        "segmentation_3d",
        "3D Segmentation",
        (InputPortDef("mesh", "Mesh", ("mesh",), required=True),),
        "Needs spconv/torch_scatter (no Python 3.14 / CUDA 13 wheels)",
    ),
    _unsupported(
        "part_completion",
        "Part Completion",
        (_PARTS_IN,),
        "Needs diso/torch_cluster (no Python 3.14 / CUDA 13 wheels)",
    ),
    _unsupported(
        "part_generation",
        "Part Generation",
        (InputPortDef("mesh", "Mesh", ("mesh",), required=True),),
        "Needs the Sonata/spconv stack (Linux sidecar only)",
    ),
)
"""Every node type in catalog (and ``/features.nodes``) order."""

NODE_TYPES_BY_TYPE: Final[Mapping[str, NodeTypeDef]] = MappingProxyType({node.type: node for node in NODE_TYPES})
SOURCE_NODE_TYPES: Final[frozenset[str]] = frozenset(node.type for node in NODE_TYPES if node.is_source)
REFINE_NODE_TYPES: Final[tuple[str, ...]] = ("part_refine", "mesh_tune", "eye_correction", "rig_correction")
UNSUPPORTED_NODE_TYPES: Final[tuple[str, ...]] = ("segmentation_3d", "part_completion", "part_generation")

LEGACY_JOB_NODE_MAP: Final[Mapping[str, str]] = MappingProxyType(
    {
        JobStage.QUEUED.value: "image_to_3d",
        JobStage.WAITING_FOR_MODEL.value: "image_to_3d",
        JobStage.PREPROCESSING.value: "input_image",
        JobStage.CONDITIONING.value: "image_to_3d",
        JobStage.SPARSE_STRUCTURE.value: "image_to_3d",
        JobStage.SHAPE.value: "image_to_3d",
        JobStage.TEXTURE.value: "image_to_3d",
        JobStage.DECODING.value: "image_to_3d",
        JobStage.EXPORTING.value: "export",
        JobStage.FINALIZING.value: "export",
        JobStage.DONE.value: "export",
    }
)
"""``JobStage`` → node type: lights graph nodes for plain ``POST /jobs`` jobs (every stage is mapped)."""


# --------------------------------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorkflowTemplateDef:
    id: str
    title: str
    graph: WorkflowGraph


def _graph(nodes: tuple[tuple[str, str], ...], edges: tuple[tuple[str, str, str, str], ...]) -> WorkflowGraph:
    """A template graph: nodes ``(id, type)`` with empty params (source nodes are filled in by the user) and edges
    ``(from node, from port, to node, to port)``."""
    return WorkflowGraph(
        nodes=[GraphNode(id=node_id, type=node_type) for node_id, node_type in nodes],
        edges=[
            GraphEdge(from_=GraphPortRef(node=a, port=a_port), to=GraphPortRef(node=b, port=b_port))
            for a, a_port, b, b_port in edges
        ],
    )


TEMPLATES: Final[tuple[WorkflowTemplateDef, ...]] = (
    WorkflowTemplateDef(
        "image_to_3d",
        "Image to 3D",
        _graph(
            (("n_input", "input_image"), ("n_recon", "image_to_3d"), ("n_export", "export")),
            (("n_input", "images", "n_recon", "images"), ("n_recon", "mesh", "n_export", "asset")),
        ),
    ),
    WorkflowTemplateDef(
        "image_to_3d_parts",
        "Image to 3D with parts",
        _graph(
            (
                ("n_input", "input_image"),
                ("n_recon", "image_to_3d"),
                ("n_parts", "part_extraction"),
                ("n_export", "export"),
            ),
            (
                ("n_input", "images", "n_recon", "images"),
                ("n_recon", "mesh", "n_parts", "mesh"),
                ("n_parts", "parts", "n_export", "asset"),
            ),
        ),
    ),
    WorkflowTemplateDef(
        "prompt_to_3d",
        "Prompt to 3D",
        _graph(
            (("n_prompt", "prompt"), ("n_recon", "image_to_3d"), ("n_export", "export")),
            (("n_prompt", "images", "n_recon", "images"), ("n_recon", "mesh", "n_export", "asset")),
        ),
    ),
    WorkflowTemplateDef(
        "prompt_to_3d_parts",
        "Prompt to 3D with parts",
        _graph(
            (
                ("n_prompt", "prompt"),
                ("n_recon", "image_to_3d"),
                ("n_parts", "part_extraction"),
                ("n_export", "export"),
            ),
            (
                ("n_prompt", "images", "n_recon", "images"),
                ("n_recon", "mesh", "n_parts", "mesh"),
                ("n_parts", "parts", "n_export", "asset"),
            ),
        ),
    ),
    WorkflowTemplateDef(
        "rebuild_parts",
        "Rebuild parts of a result",
        _graph(
            (("n_job", "job_result"), ("n_parts", "part_extraction"), ("n_export", "export")),
            (("n_job", "mesh", "n_parts", "mesh"), ("n_parts", "parts", "n_export", "asset")),
        ),
    ),
)
TEMPLATES_BY_ID: Final[Mapping[str, WorkflowTemplateDef]] = MappingProxyType({t.id: t for t in TEMPLATES})


# --------------------------------------------------------------------------------------------------
# Derived views
# --------------------------------------------------------------------------------------------------


def node_type(type_: str) -> NodeTypeDef | None:
    return NODE_TYPES_BY_TYPE.get(type_)


def resolve_node_states(capabilities: Mapping[str, CapabilityState]) -> dict[str, NodeState]:
    """State of every node type from the ``/features.pipeline`` capability states (keyed by :data:`CapabilityKey`).

    Static nodes keep their fixed state; ungated nodes are available; a gated node mirrors its capability. A missing
    capability key makes the node unavailable with ``not_implemented``.
    """
    states: dict[str, NodeState] = {}
    for node in NODE_TYPES:
        if node.static_state is not None:
            states[node.type] = node.static_state
        elif node.requires is None:
            states[node.type] = NODE_AVAILABLE
        else:
            capability = capabilities.get(node.requires)
            if capability is None:
                states[node.type] = NodeState(
                    available=False,
                    locked=False,
                    code="not_implemented",
                    reason=f"The {node.requires.replace('_', ' ')} capability is not reported by this server",
                )
            else:
                states[node.type] = NodeState(
                    available=capability.available,
                    locked=False,
                    code=capability.code,
                    reason=None if capability.available else capability.reason,
                )
    return states


def _state_for(node: NodeTypeDef, states: Mapping[str, NodeState]) -> NodeState:
    return node.static_state or states.get(node.type) or NODE_AVAILABLE


def feature_nodes(states: Mapping[str, NodeState]) -> list[FeatureNodeOut]:
    """``/features.nodes``: every node type with its current state."""
    nodes: list[FeatureNodeOut] = []
    for node in NODE_TYPES:
        state = _state_for(node, states)
        nodes.append(
            FeatureNodeOut(
                type=node.type,
                title=node.title,
                category=node.category,
                available=state.available,
                locked=state.locked,
                code=state.code,
                reason=state.reason,
                hidden_by_default=node.hidden_by_default,
            )
        )
    return nodes


def catalog_etag(payload: Mapping[str, Any]) -> str:
    """Quoted strong ETag: ``"sha256-<hex>"`` of the canonical JSON (sorted keys, compact separators)."""
    canonical = json.dumps(to_jsonable_python(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f'"sha256-{hashlib.sha256(canonical.encode("utf-8")).hexdigest()}"'


def build_catalog(states: Mapping[str, NodeState], *, graph_max_bytes: int = DEFAULT_GRAPH_MAX_BYTES) -> CatalogOut:
    """``GET /workflows/catalog`` for the given node states; the ETag changes whenever availability changes."""
    node_types = []
    for node in NODE_TYPES:
        state = _state_for(node, states)
        node_types.append(
            CatalogNodeTypeOut(
                type=node.type,
                version=node.version,
                category=node.category,
                title=node.title,
                inputs=[
                    CatalogInputPortOut(
                        id=port.id,
                        label=port.label,
                        accepts=list(port.accepts),
                        required=port.required,
                        multiple=port.multiple,
                    )
                    for port in node.inputs
                ],
                outputs=[CatalogOutputPortOut(id=port.id, label=port.label, type=port.type) for port in node.outputs],
                params_schema=node.params_schema(),
                defaults=node.defaults(),
                compiles_to=node.compiles_to,
                max_instances=node.max_instances,
                available=state.available,
                locked=state.locked,
                code=state.code,
                reason=state.reason,
                hidden_by_default=node.hidden_by_default,
            )
        )
    body = {
        "schema": WORKFLOW_CATALOG_SCHEMA,
        "port_types": [PortTypeOut(id=port.id, label=port.label) for port in PORT_TYPES],
        "node_types": node_types,
        "templates": [CatalogTemplateOut(id=t.id, title=t.title, graph=t.graph) for t in TEMPLATES],
        "legacy_job_node_map": dict(LEGACY_JOB_NODE_MAP),
        "limits": CatalogLimitsOut(
            max_nodes=MAX_GRAPH_NODES, max_edges=MAX_GRAPH_EDGES, graph_max_bytes=graph_max_bytes
        ),
    }
    unsigned = {key: to_jsonable_python(value, by_alias=True) for key, value in body.items()}
    return CatalogOut.model_validate({**unsigned, "etag": catalog_etag(unsigned)})
