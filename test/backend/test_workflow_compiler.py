"""Workflow graph compiler (part-aware DESIGN.md sections 4.7.2 and 10.2; slice S11).

Pure unit tests: the compiler gets duck-typed doubles for ``/features`` (capability states, limits), the job service
and the reference service. Covers every template, every issue code, ``mode == "job"``, the Export params, simple form
≡ graph form, locked/unavailable nodes, the catalog schemas and the UI-owned fixtures in
``test/frontend/fixtures/graphs`` (their ``server`` blocks: graph, valid, mode and issues).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from core.exceptions import NotFoundError
from models.enums import JobStatus
from schemas.workflows import GRAPH_ISSUE_CODES, WorkflowGraph, WorkflowRequest
from services.workflow_catalog import NODE_TYPES, REFINE_NODE_TYPES, TEMPLATES, build_catalog
from services.workflow_compiler import WorkflowCompiler

FIXTURES = Path(__file__).resolve().parents[1] / "frontend" / "fixtures" / "graphs"
SET_ID, IMAGE_ID, EMPTY_SET = "rs_0123456789ab", "img_0123456789ab", "rs_00000000empt"
JOB_DONE, JOB_RUNNING = "job_0123456789ab", "job_00000000runn"
RECON = {"resolution": 1024, "texture_size": 2048}


# --------------------------------------------------------------------------------------------------
# Doubles
# --------------------------------------------------------------------------------------------------


def _cap(available: bool = True, code: str = "ok", reason: str | None = None, **extra: Any) -> SimpleNamespace:
    return SimpleNamespace(available=available, code=code, reason=None if available else (reason or code), **extra)


class FakeFeatures:
    def __init__(self, **flags: Any) -> None:
        self.flags = {"prompt": True, "segmentation": True, "parts": True, "workflows": True, **flags}
        self.max_bytes = 65536
        self.prompt_max = 2000

    def features(self) -> SimpleNamespace:
        f = self.flags

        def flag(on: Any) -> SimpleNamespace:
            if on is True:
                return _cap()
            return _cap(False, on or "disabled")

        profiles = [SimpleNamespace(id="baseline", **vars(_cap())),
                    SimpleNamespace(id="anime", **vars(_cap(False, "vendored_source_missing")))]
        strategies = [SimpleNamespace(id="components", **vars(_cap())),
                      SimpleNamespace(id="semantic_parts", **vars(_cap(False, "not_installed")))]
        pipeline = SimpleNamespace(
            image_to_3d=_cap(),
            prompt_to_3d=SimpleNamespace(**vars(flag(f["prompt"])), default_model_id="z_image_turbo",
                                         default_model_state="not_installed"),
            segmentation=SimpleNamespace(**vars(flag(f["segmentation"])), profiles=profiles),
            part_extraction=SimpleNamespace(**vars(flag(f["parts"])), strategies=strategies),
        )
        return SimpleNamespace(
            pipeline=pipeline,
            workflow=flag(f["workflows"]),
            limits=SimpleNamespace(workflow_graph_max_bytes=self.max_bytes, prompt_max_chars=self.prompt_max),
        )


class FakeReferences:
    def get_set(self, set_id: str) -> SimpleNamespace:
        if set_id == SET_ID:
            images = [SimpleNamespace(id=IMAGE_ID)]
        elif set_id == EMPTY_SET:
            images = []
        else:
            raise NotFoundError("Reference set not found")
        return SimpleNamespace(
            id=set_id, images=images, get_image=lambda image_id: next((i for i in images if i.id == image_id), None)
        )


class FakeJobs:
    def get_job(self, job_id: str) -> SimpleNamespace:
        status = {JOB_DONE: JobStatus.COMPLETED, JOB_RUNNING: JobStatus.RUNNING}.get(job_id)
        if status is None:
            raise NotFoundError("Job not found")
        return SimpleNamespace(id=job_id, status=status)


@pytest.fixture
def features() -> FakeFeatures:
    return FakeFeatures()


@pytest.fixture
def compiler(features: FakeFeatures) -> WorkflowCompiler:
    return WorkflowCompiler(features=features, jobs=FakeJobs(), references=FakeReferences())  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------------
# Graph helpers
# --------------------------------------------------------------------------------------------------

SOURCE_PARAMS = {
    "input_image": {"reference_set_id": SET_ID, "primary_image_id": IMAGE_ID},
    "prompt": {"prompt": "a knight with a red cape"},
    "job_result": {"job_id": JOB_DONE},
    "image_to_3d": RECON,
}


def graph(nodes: list[tuple[str, str] | tuple[str, str, dict]], edges: list[str]) -> WorkflowGraph:
    """Nodes ``(id, type[, params])`` (source/recon params default from :data:`SOURCE_PARAMS`); edges ``"a.p>b.q"``."""
    body_nodes = []
    for entry in nodes:
        node_id, type_ = entry[0], entry[1]
        params = entry[2] if len(entry) > 2 else SOURCE_PARAMS.get(type_, {})
        body_nodes.append({"id": node_id, "type": type_, "params": params})
    body_edges = []
    for edge in edges:
        left, right = edge.split(">")
        (a, a_port), (b, b_port) = left.split("."), right.split(".")
        body_edges.append({"from": {"node": a, "port": a_port}, "to": {"node": b, "port": b_port}})
    return WorkflowGraph.model_validate({"schema": "img3d.workflow_graph", "version": 1, "nodes": body_nodes,
                                         "edges": body_edges})


IMAGE_PARTS = (
    [("n_input", "input_image"), ("n_recon", "image_to_3d"), ("n_parts", "part_extraction"), ("n_export", "export")],
    ["n_input.images>n_recon.images", "n_recon.mesh>n_parts.mesh", "n_parts.parts>n_export.asset"],
)


def codes(result: Any) -> list[tuple[str, str | None, str | None]]:
    return sorted((i.code, i.node_id, i.port) for i in result.issues)


def filled(template_graph: WorkflowGraph) -> WorkflowGraph:
    body = template_graph.model_dump(mode="json", by_alias=True)
    for node in body["nodes"]:
        node["params"] = SOURCE_PARAMS.get(node["type"], node["params"])
    return WorkflowGraph.model_validate(body)


# --------------------------------------------------------------------------------------------------
# Templates, mode and params
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.id)
def test_every_template_compiles(compiler, template):
    result = compiler.compile(filled(template.graph))
    assert result.valid, result.issues
    assert result.issues == ()
    assert result.mode == ("job" if template.id == "image_to_3d" else "workflow")
    request = result.request
    assert request is not None and request.graph is None
    assert request.source.kind == {"image_to_3d": "image", "image_to_3d_parts": "image", "prompt_to_3d": "prompt",
                                   "prompt_to_3d_parts": "prompt", "rebuild_parts": "job"}[template.id]
    assert (request.parts is not None) == template.id.endswith("parts")
    assert dict(result.node_ids) == {node.type: node.id for node in template.graph.nodes}


def test_trivial_graph_is_job_mode_with_the_node_settings(compiler):
    settings = {"resolution": 1536, "texture_size": 4096, "seed": 7, "randomize_seed": False}
    result = compiler.compile(graph([("a", "input_image"), ("b", "image_to_3d", settings), ("c", "export")],
                                    ["a.images>b.images", "b.mesh>c.asset"]))
    assert result.valid and result.mode == "job"
    assert result.request.reconstruction.settings.model_dump(exclude_unset=True) == settings
    assert result.request.parts is None and result.request.segmentation is None
    assert not hasattr(result, "job_request")


def test_export_node_params_compile_to_parts_export(compiler):
    nodes, edges = IMAGE_PARTS
    nodes = [*nodes[:3], ("n_export", "export", {"per_part_glbs": True, "previews": False})]
    result = compiler.compile(graph(nodes, edges))
    assert result.valid
    assert result.request.parts.export.per_part_glbs is True and result.request.parts.export.previews is False


def test_no_sink_adds_an_implicit_export(compiler):
    result = compiler.compile(graph([("a", "input_image"), ("b", "image_to_3d")], ["a.images>b.images"]))
    assert result.valid and result.mode == "job"
    assert [(i.severity, i.code) for i in result.issues] == [("warning", "no_sink")]


# --------------------------------------------------------------------------------------------------
# Every issue code
# --------------------------------------------------------------------------------------------------

BASIC = ([("a", "input_image"), ("b", "image_to_3d"), ("c", "export")], ["a.images>b.images", "b.mesh>c.asset"])
CASES: dict[str, tuple[Any, ...]] = {
    "unknown_node_type": ([*BASIC[0], ("x", "mystery")], BASIC[1], [("unknown_node_type", "x", None)]),
    "unknown_port": (BASIC[0], ["a.images>b.picture", "b.mesh>c.asset"], [("unknown_port", "b", "picture")]),
    "type_mismatch": (BASIC[0], ["a.images>b.images", "a.images>c.asset"], [("type_mismatch", "c", "asset")]),
    "port_already_connected": (
        [*BASIC[0], ("p", "prompt")], [*BASIC[1], "p.images>b.images"], [("port_already_connected", "b", "images")]
    ),
    "missing_required_input": (
        [("a", "input_image"), ("s", "segmentation"), ("b", "image_to_3d"), ("p", "part_extraction"), ("c", "export")],
        ["a.images>s.images", "a.images>b.images", "s.masks>p.masks", "p.parts>c.asset"],
        [("missing_required_input", "p", "mesh")],
    ),
    "no_source": ([("b", "image_to_3d"), ("c", "export")], ["b.mesh>c.asset"], [("no_source", None, None)]),
    "multiple_sources": (
        [*BASIC[0], ("p", "prompt")], BASIC[1], [("multiple_sources", None, None)]
    ),
    "too_many_instances": ([*BASIC[0], ("a2", "input_image")], BASIC[1], [("too_many_instances", "a2", None)]),
    "node_locked": (
        [*BASIC[0][:2], ("p", "part_extraction"), ("r", "part_refine"), ("c", "export")],
        ["a.images>b.images", "b.mesh>p.mesh", "p.parts>r.parts", "r.parts>c.asset"],
        [("node_locked", "r", None)],
    ),
    "unsupported_topology": (
        [("j", "job_result"), ("b", "image_to_3d"), ("c", "export")], ["j.images>b.images", "b.mesh>c.asset"],
        [("unsupported_topology", "b", None)],
    ),
    "capability_unavailable": (
        [("a", "input_image"), ("b", "image_to_3d"), ("p", "part_extraction", {"strategy": "semantic_parts"}),
         ("c", "export")],
        ["a.images>b.images", "b.mesh>p.mesh", "p.parts>c.asset"],
        [("capability_unavailable", "p", None)],
    ),
    "invalid_param": (
        [("a", "input_image"), ("b", "image_to_3d", {"resolution": "huge"}), ("c", "export")], BASIC[1],
        [("invalid_param", "b", None)],
    ),
    "not_found": (
        [("a", "input_image", {"reference_set_id": "rs_000000000404"}), *BASIC[0][1:]], BASIC[1],
        [("not_found", "a", None)],
    ),
}


@pytest.mark.parametrize("code", list(CASES))
def test_issue_codes_on_crafted_graphs(compiler, code):
    nodes, edges, expected = CASES[code]
    result = compiler.compile(graph(nodes, edges))
    assert not result.valid and result.request is None and result.mode is None
    assert codes(result) == sorted(expected)


def test_cycle_is_reported_once_without_a_node(compiler):
    nodes = [("a", "input_image"), ("b", "image_to_3d"), ("p", "part_extraction"), ("r", "part_refine"),
             ("m", "mesh_tune"), ("c", "export")]
    edges = ["a.images>b.images", "b.mesh>p.mesh", "p.parts>c.asset", "m.parts>r.parts", "r.parts>m.parts"]
    result = compiler.compile(graph(nodes, edges))
    assert codes(result) == [("cycle", None, None), ("node_locked", "m", None), ("node_locked", "r", None)]
    cycle = next(i for i in result.issues if i.code == "cycle")
    assert cycle.details["nodes"] == ["m", "r"]


def test_graph_too_large_for_nodes_edges_and_bytes(compiler, features):
    many = [(f"n{i}", "export") for i in range(33)]
    assert codes(compiler.compile(graph(many, []))) == [("graph_too_large", None, None)]
    edges = ["a.images>b.images"] * 65
    assert codes(compiler.compile(graph(BASIC[0], edges))) == [("graph_too_large", None, None)]
    features.max_bytes = 200
    result = compiler.compile(graph(*BASIC))
    assert codes(result) == [("graph_too_large", None, None)] and result.issues[0].details["bytes"] > 200


def test_feature_disabled_for_a_node_and_for_workflows(features, compiler):
    features.flags["parts"] = "disabled"
    assert codes(compiler.compile(graph(*IMAGE_PARTS))) == [("feature_disabled", "n_parts", None)]
    features.flags.update(parts=True, workflows="disabled")
    assert codes(compiler.compile(graph(*IMAGE_PARTS))) == [("feature_disabled", None, None)]
    assert compiler.compile(graph(*BASIC)).valid, "a job-mode graph does not need the workflows flag"


def test_segmentation_must_feed_part_extraction_masks(compiler):
    nodes = [("a", "input_image"), ("s", "segmentation"), ("b", "image_to_3d"), ("p", "part_extraction"),
             ("c", "export")]
    edges = ["a.images>s.images", "a.images>b.images", "b.mesh>p.mesh", "p.parts>c.asset"]
    assert codes(compiler.compile(graph(nodes, edges))) == [("unsupported_topology", "s", None)]
    result = compiler.compile(graph(nodes, [*edges, "s.masks>p.masks"]))
    assert result.valid and result.request.segmentation is not None


@pytest.mark.parametrize("refine", REFINE_NODE_TYPES)
def test_refine_nodes_are_locked(compiler, refine):
    nodes = [*IMAGE_PARTS[0][:3], ("r", refine), ("n_export", "export")]
    edges = [*IMAGE_PARTS[1][:2], "n_parts.parts>r.parts", "r.parts>n_export.asset"]
    result = compiler.compile(graph(nodes, edges))
    assert codes(result) == [("node_locked", "r", None)]
    assert result.issues[0].details == {"code": "not_approved", "reason": "Paused until approved"}


def test_unavailable_nodes_give_capability_unavailable(features, compiler):
    features.flags["prompt"] = "not_installed"
    result = compiler.compile(graph([("p", "prompt"), ("b", "image_to_3d"), ("c", "export")],
                                    ["p.images>b.images", "b.mesh>c.asset"]))
    assert codes(result) == [("capability_unavailable", "p", None)]
    assert result.issues[0].details["reasons"][0]["model_id"] == "z_image_turbo"
    nodes = [*BASIC[0][:2], ("s3", "segmentation_3d"), ("c", "export")]
    result = compiler.compile(graph(nodes, ["a.images>b.images", "b.mesh>s3.mesh", "s3.parts>c.asset"]))
    assert codes(result) == [("capability_unavailable", "s3", None)]


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"prompt": "   "}, "string_too_short"),
        ({"prompt": "x" * 2001}, "string_too_long"),
        ({"prompt": "a knight", "num_images": 2}, "less_than_equal"),
    ],
    ids=["empty", "too_long", "two_images"],
)
def test_prompt_params(compiler, params, expected):
    result = compiler.compile(graph([("p", "prompt", params), ("b", "image_to_3d"), ("c", "export")],
                                    ["p.images>b.images", "b.mesh>c.asset"]))
    assert codes(result) == [("invalid_param", "p", None)]
    assert result.issues[0].details["type"] == expected
    assert result.issues[0].details["loc"][:3] == ["nodes", 0, "params"]


def test_source_existence_and_state(compiler):
    def first(source: tuple) -> tuple[str, str | None, Any]:
        result = compiler.compile(graph([source, *BASIC[0][1:]], BASIC[1]))
        issue = result.issues[0]
        return issue.code, issue.node_id, (issue.details or {}).get("type")

    assert first(("a", "input_image", {"reference_set_id": EMPTY_SET})) == ("invalid_param", "a", "no_images")
    assert first(("a", "input_image", {"reference_set_id": SET_ID, "primary_image_id": "img_000000000404"}))[0] == (
        "not_found"
    )
    rebuild = lambda job_id: compiler.compile(graph(  # noqa: E731
        [("j", "job_result", {"job_id": job_id}), ("p", "part_extraction"), ("c", "export")],
        ["j.mesh>p.mesh", "p.parts>c.asset"]))
    assert codes(rebuild("job_000000000404")) == [("not_found", "j", None)]
    result = rebuild(JOB_RUNNING)
    assert codes(result) == [("invalid_param", "j", None)] and result.issues[0].details["type"] == "job_not_completed"
    assert rebuild(JOB_DONE).valid


def test_every_issue_code_is_covered():
    covered = set(CASES) | {"cycle", "graph_too_large", "feature_disabled",
                                                               "no_sink"}
    assert covered == set(GRAPH_ISSUE_CODES)


# --------------------------------------------------------------------------------------------------
# Simple form ≡ graph form
# --------------------------------------------------------------------------------------------------

COMBOS = [(seg, parts, ppg) for seg in (False, True) for parts in (False, True) for ppg in (False, True)]


@pytest.mark.parametrize(("seg", "parts", "ppg"), COMBOS, ids=lambda v: str(v))
def test_simple_form_equals_graph_form(compiler, seg, parts, ppg):
    source = {"kind": "image", "reference_set_id": SET_ID, "primary_image_id": IMAGE_ID}
    simple_body: dict[str, Any] = {"source": source, "reconstruction": {"settings": RECON}}
    if parts:
        simple_body["parts"] = {"separate": ["hair", "clothes"], "export": {"per_part_glbs": ppg}}
    if seg:
        simple_body["segmentation"] = {"profile": "baseline"}
    if seg and not parts:
        with pytest.raises(ValidationError) as caught:
            WorkflowRequest.model_validate(simple_body)
        error = caught.value.errors()[0]
        assert error["type"] == "conflicting_fields" and error["loc"] == ("segmentation",)
        return
    simple = WorkflowRequest.model_validate(simple_body)

    nodes: list[Any] = [("n_input", "input_image"), ("n_recon", "image_to_3d"),
                        ("n_export", "export", {"per_part_glbs": ppg})]
    edges = ["n_input.images>n_recon.images", "n_recon.mesh>n_export.asset"]
    if parts:
        nodes.append(("n_parts", "part_extraction", {"separate": ["hair", "clothes"]}))
        edges = ["n_input.images>n_recon.images", "n_recon.mesh>n_parts.mesh", "n_parts.parts>n_export.asset"]
    if seg:
        nodes.append(("n_seg", "segmentation", {"profile": "baseline"}))
        edges += ["n_input.images>n_seg.images", "n_seg.masks>n_parts.masks"]
    result = compiler.compile(graph(nodes, edges))
    assert result.valid, result.issues
    assert result.request.model_dump(mode="json") == simple.model_dump(mode="json")
    assert result.mode == ("workflow" if parts else "job")
    roundtrip = compiler.compile(compiler.graph_from_request(simple))
    assert roundtrip.valid and roundtrip.request.model_dump(mode="json") == simple.model_dump(mode="json")


# --------------------------------------------------------------------------------------------------
# Catalog + UI fixtures
# --------------------------------------------------------------------------------------------------


def test_every_catalog_node_type_has_a_params_schema_with_properties(compiler):
    catalog = build_catalog(compiler.node_states())
    assert [n.type for n in catalog.node_types] == [n.type for n in NODE_TYPES]
    for node in catalog.node_types:
        assert isinstance(node.params_schema.get("properties"), dict), node.type


def _server_fixtures() -> list[Path]:
    if not FIXTURES.is_dir():
        return []
    return [p for p in sorted(FIXTURES.glob("*.json")) if json.loads(p.read_text(encoding="utf-8")).get("server")]


@pytest.mark.skipif(not _server_fixtures(), reason="no UI graph fixtures with a server block")
@pytest.mark.parametrize("path", _server_fixtures(), ids=lambda p: p.stem)
def test_ui_fixtures_compile_to_their_expected_issues(compiler, path):
    server = json.loads(path.read_text(encoding="utf-8"))["server"]
    result = compiler.compile(WorkflowGraph.model_validate(server["graph"]))
    expected = sorted((i["severity"], i["code"], i.get("node_id"), i.get("port")) for i in server["issues"])
    assert sorted((i.severity, i.code, i.node_id, i.port) for i in result.issues) == expected
    assert result.valid is server["valid"] and result.mode == server["mode"]
