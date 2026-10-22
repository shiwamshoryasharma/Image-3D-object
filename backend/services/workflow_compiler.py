"""Workflow graph compiler: ``WorkflowGraph`` (Node UI) → simple-form ``WorkflowRequest`` + ``GraphIssue``\\ s
(part-aware DESIGN.md section 4.7.2; slice S11). Torch-free; the only I/O is existence lookups.

The checks run in phases; a phase with an error ends the compile, so the client sees the root cause and not its
consequences (a dangling edge does not also report the input it failed to connect):

1. limits: node count, edge count and serialized bytes (``graph_too_large``);
2. structure: ``unknown_node_type``, ``too_many_instances``, ``node_locked``, ``unknown_port``, ``type_mismatch``,
   ``port_already_connected`` and ``cycle`` (Kahn leftover);
3. sources: ``no_source`` / ``multiple_sources``;
4. wiring: ``missing_required_input`` and ``unsupported_topology`` (``job_result`` + ``image_to_3d``; an image source
   without Image-to-3D; an existing result without Part Extraction; a Segmentation node whose ``masks`` does not feed
   an active ``part_extraction.masks``, reported on the Segmentation node);
5. per node: availability (``feature_disabled`` / ``capability_unavailable`` with the registry reasons), params
   (``invalid_param``, pydantic errors relocated to ``nodes[i].params.<field>``) and existence (``not_found``; an
   empty reference set or an unfinished job is ``invalid_param``), then the workflows flag for workflow-mode graphs.

``no_sink`` (no Export node) is the only warning: an implicit export is added. ``mode == "job"`` when the graph is
exactly input_image → image_to_3d (→ export): the client then posts its own ``CreateJobRequest`` to ``POST /jobs``.
:meth:`WorkflowCompiler.graph_from_request` builds the graph of a simple-form request, so the simple form and the
graph form are checked by the same code (simple form ≡ graph form).
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from pydantic import ValidationError

from core.exceptions import AppError, NotFoundError
from models.enums import JobStatus
from schemas.features import FeaturesOut
from schemas.workflows import (
    MAX_GRAPH_EDGES,
    MAX_GRAPH_NODES,
    GraphEdge,
    GraphIssueOut,
    GraphNode,
    GraphPortRef,
    WorkflowGraph,
    WorkflowMode,
    WorkflowRequest,
)
from services.feature_service import FeatureService
from services.job_service import JobService
from services.reference_service import ReferenceService
from services.workflow_catalog import NodeState, NodeTypeDef, node_type, resolve_node_states

logger = logging.getLogger(__name__)

IssueSeverity = Literal["error", "warning"]

PIPELINE_KEYS = ("image_to_3d", "prompt_to_3d", "segmentation", "part_extraction")
JOB_MODE_TYPES = frozenset({"input_image", "image_to_3d", "export"})
"""A graph made only of these node types (with an Input Image source) runs as a plain job (UI BR1)."""
SIMPLE_FORM_NODE_IDS: Mapping[str, str] = MappingProxyType(
    {
        "input_image": "n_input",
        "prompt": "n_prompt",
        "job_result": "n_job",
        "segmentation": "n_seg",
        "image_to_3d": "n_recon",
        "part_extraction": "n_parts",
        "export": "n_export",
    }
)
"""Node ids of the graph :meth:`WorkflowCompiler.graph_from_request` builds (the template ids)."""


@dataclass(frozen=True, slots=True)
class GraphIssue:
    severity: IssueSeverity
    code: str
    message: str
    node_id: str | None = None
    port: str | None = None
    edge_index: int | None = None
    details: Mapping[str, Any] | None = None

    def to_out(self) -> GraphIssueOut:
        return GraphIssueOut(
            severity=self.severity,
            code=self.code,  # type: ignore[arg-type]
            message=self.message,
            node_id=self.node_id,
            port=self.port,
            edge_index=self.edge_index,
            details=dict(self.details) if self.details is not None else None,
        )


@dataclass(frozen=True, slots=True)
class CompileResult:
    valid: bool
    issues: tuple[GraphIssue, ...]
    request: WorkflowRequest | None
    """Normalized simple form (``None`` when invalid); the graph is kept alongside by the caller."""
    mode: WorkflowMode | None
    node_ids: Mapping[str, str] = field(default_factory=dict)
    """Graph node id per node type (``WorkflowStep.node_id``)."""

    @property
    def errors(self) -> tuple[GraphIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")


def _error(code: str, message: str, **kwargs: Any) -> GraphIssue:
    return GraphIssue("error", code, message, **kwargs)


class WorkflowCompiler:
    def __init__(self, *, features: FeatureService, jobs: JobService, references: ReferenceService) -> None:
        self._features = features
        self._jobs = jobs
        self._references = references

    # ------------------------------------------------------------------ API
    def node_states(self, features: FeaturesOut | None = None) -> dict[str, NodeState]:
        doc = features or self._features.features()
        return resolve_node_states({key: getattr(doc.pipeline, key) for key in PIPELINE_KEYS})

    def compile(self, graph: WorkflowGraph) -> CompileResult:
        """Check ``graph`` and compile it to the simple form (pure apart from existence lookups)."""
        features = self._features.features()
        issues = list(self._limits(graph, features.limits.workflow_graph_max_bytes))
        if issues:
            return CompileResult(False, tuple(issues), None, None)
        nodes = {node.id: node for node in graph.nodes}
        specs = {node.id: spec for node in graph.nodes if (spec := node_type(node.type)) is not None}

        issues = self._structure(graph, specs)
        if issues:
            return CompileResult(False, tuple(issues), None, None)
        edges = list(graph.edges)

        sources = [nid for nid, spec in specs.items() if spec.is_source]
        if not sources:
            issue = _error("no_source", "Add an input: Input Image, Prompt or Existing Result")
            return CompileResult(False, (issue,), None, None)
        if len(sources) > 1:
            issue = _error(
                "multiple_sources",
                "Use exactly one input (Input Image, Prompt or Existing Result)",
                details={"nodes": sources},
            )
            return CompileResult(False, (issue,), None, None)
        source = nodes[sources[0]]

        by_type = {spec.type: nodes[nid] for nid, spec in specs.items()}
        issues = self._wiring(graph, specs, by_type, source, edges)
        if issues:
            return CompileResult(False, tuple(issues), None, None)

        mode: WorkflowMode = (
            "job" if source.type == "input_image" and set(by_type) <= JOB_MODE_TYPES else "workflow"
        )
        issues = self._nodes(graph, specs, features)
        if mode == "workflow" and not features.workflow.available:
            code = "feature_disabled" if features.workflow.code == "disabled" else "capability_unavailable"
            issues.append(
                _error(
                    code,
                    features.workflow.reason or "Workflows are not available",
                    details={"feature": "workflows", "code": features.workflow.code},
                )
            )
        warnings: list[GraphIssue] = []
        if "export" not in by_type:
            warnings.append(GraphIssue("warning", "no_sink", "No Export node: the result is exported by default"))
        if issues:
            return CompileResult(False, tuple(issues + warnings), None, None)

        request, problem = self._build_request(by_type)
        if problem is not None:
            return CompileResult(False, (problem, *warnings), None, None)
        node_ids = {node_type_: node.id for node_type_, node in by_type.items()}
        return CompileResult(True, tuple(warnings), request, mode, MappingProxyType(node_ids))

    def graph_from_request(self, request: WorkflowRequest) -> WorkflowGraph:
        """The graph a simple-form request stands for (template node ids; params only where the request sets them)."""
        source = request.source
        if source is None:
            raise ValueError("A simple-form request needs a source")
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []

        def add(type_: str, params: dict[str, Any]) -> str:
            node_id = SIMPLE_FORM_NODE_IDS[type_]
            nodes.append(GraphNode(id=node_id, type=type_, params=params))
            return node_id

        def link(a: str, a_port: str, b: str, b_port: str) -> None:
            edges.append(GraphEdge(from_=GraphPortRef(node=a, port=a_port), to=GraphPortRef(node=b, port=b_port)))

        if source.kind == "image":
            src = add("input_image", source.model_dump(mode="json", exclude={"kind"}, exclude_unset=True))
        elif source.kind == "prompt":
            src = add("prompt", source.generation.model_dump(mode="json", exclude_unset=True))
        else:
            src = add("job_result", {"job_id": source.job_id})
        mesh_from = (src, "mesh")
        if request.reconstruction is not None:
            recon = add("image_to_3d", request.reconstruction.settings.model_dump(mode="json", exclude_unset=True))
            link(src, "images", recon, "images")
            mesh_from = (recon, "mesh")
        asset_from = mesh_from
        if request.parts is not None:
            params = request.parts.model_dump(mode="json", exclude={"segmentation_id", "export"}, exclude_unset=True)
            parts = add("part_extraction", params)
            link(*mesh_from, parts, "mesh")
            asset_from = (parts, "parts")
            if request.segmentation is not None:
                seg = add("segmentation", request.segmentation.model_dump(mode="json", exclude_unset=True))
                link(src, "images", seg, "images")
                link(seg, "masks", parts, "masks")
        export_params = request.parts.export.model_dump(mode="json") if request.parts is not None else {}
        export = add("export", export_params)
        link(*asset_from, export, "asset")
        return WorkflowGraph(nodes=nodes, edges=edges)

    # ------------------------------------------------------------------ phase 1: limits
    @staticmethod
    def _limits(graph: WorkflowGraph, max_bytes: int) -> list[GraphIssue]:
        size = len(json.dumps(graph.model_dump(mode="json", by_alias=True), separators=(",", ":")).encode("utf-8"))
        details = {
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "bytes": size,
            "max_nodes": MAX_GRAPH_NODES,
            "max_edges": MAX_GRAPH_EDGES,
            "max_bytes": max_bytes,
        }
        if len(graph.nodes) > MAX_GRAPH_NODES or len(graph.edges) > MAX_GRAPH_EDGES or size > max_bytes:
            return [
                _error(
                    "graph_too_large",
                    f"The graph exceeds the limits ({MAX_GRAPH_NODES} nodes, {MAX_GRAPH_EDGES} edges, "
                    f"{max_bytes} bytes)",
                    details=details,
                )
            ]
        return []

    # ------------------------------------------------------------------ phase 2: structure
    @staticmethod
    def _structure(graph: WorkflowGraph, specs: Mapping[str, NodeTypeDef]) -> list[GraphIssue]:
        issues: list[GraphIssue] = []
        seen_ids: set[str] = set()
        per_type: Counter[str] = Counter()
        for node in graph.nodes:
            if node.id in seen_ids:
                issues.append(
                    _error("too_many_instances", f"Node id {node.id} is used twice", node_id=node.id,
                           details={"reason": "duplicate_node_id"})
                )
                continue
            seen_ids.add(node.id)
            spec = specs.get(node.id)
            if spec is None:
                issues.append(_error("unknown_node_type", f"Unknown node type {node.type!r}", node_id=node.id))
                continue
            per_type[spec.type] += 1
            if per_type[spec.type] > spec.max_instances:
                issues.append(
                    _error(
                        "too_many_instances",
                        f"Only {spec.max_instances} {spec.title} node is allowed",
                        node_id=node.id,
                        details={"type": spec.type, "max_instances": spec.max_instances},
                    )
                )
            if spec.is_locked and spec.static_state is not None:
                issues.append(
                    _error(
                        "node_locked",
                        f"{spec.title}: {spec.static_state.reason}",
                        node_id=node.id,
                        details={"code": spec.static_state.code, "reason": spec.static_state.reason},
                    )
                )

        connected: Counter[tuple[str, str]] = Counter()
        valid_edges: list[tuple[str, str]] = []
        for index, edge in enumerate(graph.edges):
            src, dst = edge.from_, edge.to
            src_spec, dst_spec = specs.get(src.node), specs.get(dst.node)
            problem = False
            for ref, spec, direction in ((src, src_spec, "output"), (dst, dst_spec, "input")):
                if spec is None:
                    if ref.node not in seen_ids:
                        issues.append(
                            _error("unknown_port", f"Edge {index} refers to an unknown node {ref.node!r}",
                                   node_id=ref.node, port=ref.port, edge_index=index,
                                   details={"reason": "unknown_node"})
                        )
                    problem = True
                    continue
                ports = spec.outputs if direction == "output" else spec.inputs
                if all(port.id != ref.port for port in ports):
                    issues.append(
                        _error("unknown_port", f"{spec.title} has no {direction} {ref.port!r}", node_id=ref.node,
                               port=ref.port, edge_index=index, details={"direction": direction})
                    )
                    problem = True
            if problem or src_spec is None or dst_spec is None:
                continue
            out_port = next(p for p in src_spec.outputs if p.id == src.port)
            in_port = next(p for p in dst_spec.inputs if p.id == dst.port)
            if out_port.type not in in_port.accepts:
                issues.append(
                    _error(
                        "type_mismatch",
                        f"{dst_spec.title}.{in_port.label} does not accept {out_port.label}",
                        node_id=dst.node,
                        port=dst.port,
                        edge_index=index,
                        details={"from_type": out_port.type, "accepts": list(in_port.accepts)},
                    )
                )
                continue
            connected[(dst.node, dst.port)] += 1
            if connected[(dst.node, dst.port)] > 1 and not in_port.multiple:
                issues.append(
                    _error("port_already_connected", f"{dst_spec.title}.{in_port.label} is already connected",
                           node_id=dst.node, port=dst.port, edge_index=index)
                )
                continue
            valid_edges.append((src.node, dst.node))

        leftover = _kahn_leftover([nid for nid in specs], valid_edges)
        if leftover:
            issues.append(_error("cycle", "The graph contains a cycle", details={"nodes": leftover}))
        return issues

    # ------------------------------------------------------------------ phase 4: wiring
    @staticmethod
    def _wiring(
        graph: WorkflowGraph,
        specs: Mapping[str, NodeTypeDef],
        by_type: Mapping[str, GraphNode],
        source: GraphNode,
        edges: Sequence[GraphEdge],
    ) -> list[GraphIssue]:
        issues: list[GraphIssue] = []
        inputs = {(edge.to.node, edge.to.port) for edge in edges}
        for node in graph.nodes:
            spec = specs[node.id]
            for port in spec.inputs:
                if port.required and (node.id, port.id) not in inputs:
                    issues.append(
                        _error("missing_required_input", f"{spec.title} needs its {port.label} input",
                               node_id=node.id, port=port.id)
                    )
        recon, parts, seg = by_type.get("image_to_3d"), by_type.get("part_extraction"), by_type.get("segmentation")
        if source.type == "job_result" and recon is not None:
            issues.append(
                _error("unsupported_topology", "An existing result is already 3D: remove Image-to-3D",
                       node_id=recon.id, details={"nodes": [source.id, recon.id]})
            )
        elif source.type == "job_result" and parts is None:
            issues.append(
                _error("unsupported_topology", "An existing result can only be rebuilt into parts: add Part Extraction",
                       node_id=source.id)
            )
        if source.type != "job_result" and recon is None:
            issues.append(
                _error("unsupported_topology", "Connect the image to Image-to-3D", node_id=source.id)
            )
        if seg is not None:
            feeds_parts = parts is not None and any(
                edge.from_.node == seg.id and edge.from_.port == "masks" and edge.to.node == parts.id
                and edge.to.port == "masks"
                for edge in edges
            )
            if not feeds_parts:
                issues.append(
                    _error("unsupported_topology",
                           "Segmentation is skipped: connect its Masks to an active Part Extraction",
                           node_id=seg.id)
                )
        return issues

    # ------------------------------------------------------------------ phase 5: nodes
    def _nodes(
        self, graph: WorkflowGraph, specs: Mapping[str, NodeTypeDef], features: FeaturesOut
    ) -> list[GraphIssue]:
        issues: list[GraphIssue] = []
        states = self.node_states(features)
        for index, node in enumerate(graph.nodes):
            spec = specs[node.id]
            state = spec.static_state or states.get(spec.type)
            if state is not None and not state.available:
                issues.append(self._unavailable(node, spec, state, features))
                continue
            if spec.params_model is None:
                continue
            try:
                params = spec.params_model.model_validate(node.params)
            except ValidationError as exc:
                issues.extend(_param_issues(node, index, exc))
                continue
            issues.extend(self._node_checks(node, index, spec, params, features))
        return issues

    @staticmethod
    def _unavailable(node: GraphNode, spec: NodeTypeDef, state: NodeState, features: FeaturesOut) -> GraphIssue:
        reason = state.reason or f"{spec.title} is not available"
        if state.code == "disabled":
            return _error("feature_disabled", f"{spec.title}: {reason}", node_id=node.id,
                          details={"code": state.code, "reason": state.reason})
        reasons: list[dict[str, Any]] = [{"model_id": None, "state": None, "reason_code": state.code,
                                          "reason": state.reason}]
        if spec.type == "prompt":
            prompt = features.pipeline.prompt_to_3d
            reasons = [{"model_id": prompt.default_model_id, "state": prompt.default_model_state,
                        "reason_code": state.code, "reason": state.reason}]
        return _error("capability_unavailable", f"{spec.title}: {reason}", node_id=node.id,
                      details={"capability": spec.requires, "reasons": reasons})

    def _node_checks(
        self, node: GraphNode, index: int, spec: NodeTypeDef, params: Any, features: FeaturesOut
    ) -> list[GraphIssue]:
        loc = ["nodes", index, "params"]
        if spec.type == "prompt":
            limit = features.limits.prompt_max_chars
            if len(params.prompt) > limit:
                return [
                    _error("invalid_param", f"The prompt is longer than {limit} characters", node_id=node.id,
                           details={"loc": [*loc, "prompt"], "type": "string_too_long", "max_length": limit})
                ]
            return []
        if spec.type == "segmentation" and params.profile != "auto":
            profile = next((p for p in features.pipeline.segmentation.profiles if p.id == params.profile), None)
            if profile is not None and not profile.available:
                return [self._option_unavailable(node, "segmentation", f"profile {params.profile}", profile)]
            return []
        if spec.type == "part_extraction" and params.strategy is not None:
            strategy = next((s for s in features.pipeline.part_extraction.strategies if s.id == params.strategy), None)
            if strategy is not None and not strategy.available:
                return [self._option_unavailable(node, "part_extraction", f"strategy {params.strategy}", strategy)]
            return []
        if spec.type == "input_image":
            return self._check_reference_set(node, loc, params.reference_set_id, params.primary_image_id)
        if spec.type == "job_result":
            return self._check_job(node, loc, params.job_id)
        return []

    @staticmethod
    def _option_unavailable(node: GraphNode, capability: str, label: str, entry: Any) -> GraphIssue:
        return _error(
            "capability_unavailable",
            f"The {label} is not available: {entry.reason or entry.code}",
            node_id=node.id,
            details={"capability": capability,
                     "reasons": [{"model_id": None, "state": None, "reason_code": entry.code, "reason": entry.reason}]},
        )

    def _check_reference_set(
        self, node: GraphNode, loc: list[Any], set_id: str, primary_image_id: str | None
    ) -> list[GraphIssue]:
        try:
            ref_set = self._references.get_set(set_id)
        except NotFoundError:
            return [_error("not_found", "The reference set does not exist", node_id=node.id,
                           details={"reference_set_id": set_id, "loc": [*loc, "reference_set_id"]})]
        if not ref_set.images:
            return [_error("invalid_param", "The reference set has no images", node_id=node.id,
                           details={"reference_set_id": set_id, "loc": [*loc, "reference_set_id"],
                                    "type": "no_images"})]
        if primary_image_id is not None and ref_set.get_image(primary_image_id) is None:
            return [_error("not_found", "The primary image is not in the reference set", node_id=node.id,
                           details={"image_id": primary_image_id, "loc": [*loc, "primary_image_id"]})]
        return []

    def _check_job(self, node: GraphNode, loc: list[Any], job_id: str) -> list[GraphIssue]:
        try:
            job = self._jobs.get_job(job_id)
        except AppError:
            return [_error("not_found", "The job does not exist", node_id=node.id,
                           details={"job_id": job_id, "loc": [*loc, "job_id"]})]
        if job.status is not JobStatus.COMPLETED:
            return [_error("invalid_param", f"The job is {job.status}; open a finished model first", node_id=node.id,
                           details={"job_id": job_id, "loc": [*loc, "job_id"], "type": "job_not_completed",
                                    "status": str(job.status)})]
        return []

    # ------------------------------------------------------------------ request
    @staticmethod
    def _build_request(by_type: Mapping[str, GraphNode]) -> tuple[WorkflowRequest | None, GraphIssue | None]:
        body: dict[str, Any] = {}
        if (node := by_type.get("input_image")) is not None:
            body["source"] = {"kind": "image", **node.params}
        elif (node := by_type.get("prompt")) is not None:
            body["source"] = {"kind": "prompt", "generation": dict(node.params)}
        elif (node := by_type.get("job_result")) is not None:
            body["source"] = {"kind": "job", **node.params}
        body["reconstruction"] = (
            {"settings": dict(by_type["image_to_3d"].params)} if "image_to_3d" in by_type else None
        )
        if (parts := by_type.get("part_extraction")) is not None:
            body["parts"] = dict(parts.params)
            if (export := by_type.get("export")) is not None:
                body["parts"]["export"] = dict(export.params)
        if (seg := by_type.get("segmentation")) is not None:
            body["segmentation"] = dict(seg.params)
        try:
            return WorkflowRequest.model_validate(body), None
        except ValidationError as exc:  # every per-node check passed, so this is a rule across nodes
            logger.warning("Compiled workflow request is invalid: %s", exc)
            first = exc.errors()[0]
            return None, _error("invalid_param", str(first.get("msg")),
                                details={"loc": list(first.get("loc", ())), "type": first.get("type")})


def _param_issues(node: GraphNode, index: int, exc: ValidationError) -> list[GraphIssue]:
    issues: list[GraphIssue] = []
    for err in exc.errors():
        field_loc = [str(part) if not isinstance(part, int) else part for part in err.get("loc", ())]
        dotted = ".".join(str(part) for part in field_loc) or "params"
        issues.append(
            _error(
                "invalid_param",
                f"{dotted}: {err.get('msg')}",
                node_id=node.id,
                details={"loc": ["nodes", index, "params", *field_loc], "type": err.get("type"),
                         "field": dotted},
            )
        )
    return issues


def _kahn_leftover(node_ids: Sequence[str], edges: Sequence[tuple[str, str]]) -> list[str]:
    """Nodes left after Kahn's topological sort (non-empty ⇔ the graph has a cycle)."""
    indegree = dict.fromkeys(node_ids, 0)
    out: defaultdict[str, list[str]] = defaultdict(list)
    for src, dst in edges:
        if src in indegree and dst in indegree:
            out[src].append(dst)
            indegree[dst] += 1
    ready = deque(nid for nid, degree in indegree.items() if degree == 0)
    while ready:
        current = ready.popleft()
        for nxt in out[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
        indegree.pop(current, None)
    return sorted(indegree)
