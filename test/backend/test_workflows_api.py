"""Workflows (``wf_``) through the API with test doubles (part-aware DESIGN.md sections 4.7, 6.5 and 10.2; S11).

CPU only: :class:`fakes.FakeEngine` jobs, the fake text-to-image provider (``fakes_imagegen``), the fake segmentation
stack (``fakes_segmentation``) and the numpy :class:`fakes_lifting.FakeLabelLifter`, behind one registry double that
also answers the ``/features`` catalog questions with the real catalog specs.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fakes import FakeEngine
from fakes_imagegen import T2I_MODEL_ID, FakeImageGenRegistry, FakeTextToImageProvider
from fakes_lifting import FakeLabelLifter
from fakes_segmentation import FakeSegmentationRegistry, character_image, png_of
from helpers import assert_error, create_job, create_set_with_image, wait_for, wait_for_job
from starlette.testclient import TestClient

from core.part_aware_container import PartProviderOverrides
from inference.providers.catalog import CATALOG
from models.enums import JobStatus
from models.workflow import WorkflowRecord, WorkflowStep
from services.workflow_catalog import TEMPLATES_BY_ID
from utils.timeutil import utcnow

API = "/api/v1/workflows"
TERMINAL = {"completed", "failed", "cancelled"}
RECON = {"settings": {"resolution": 1024}}
PARTS = {"strategy": "components"}


class WorkflowRegistry(FakeSegmentationRegistry):
    """Segmentation fakes + the fake text-to-image provider + the fake lifter, with real catalog specs."""

    def __init__(self, *, t2i: FakeTextToImageProvider | None = None, lifter: Any = True, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.imagegen = FakeImageGenRegistry(t2i)
        if lifter:
            self.providers["raster_lifter"] = FakeLabelLifter() if lifter is True else lifter

    def _is_t2i(self, model_id: str) -> bool:
        return model_id == T2I_MODEL_ID

    def specs(self, kind: Any = None) -> list[Any]:
        specs = [spec for spec in CATALOG if spec.id in self.providers] + [self.imagegen.t2i.spec]
        return [spec for spec in specs if kind is None or spec.kind == kind]

    def spec(self, model_id: str) -> Any:
        return next(spec for spec in self.specs() if spec.id == model_id)

    def status(self, model_id: str) -> Any:
        return self.imagegen.status(model_id) if self._is_t2i(model_id) else super().status(model_id)

    def statuses(self, kind: Any = None) -> list[Any]:
        return [self.status(spec.id) for spec in self.specs(kind)]

    def default_model(self, kind: Any) -> str | None:
        return self.imagegen.default_model(kind) if kind == "text_to_image" else None

    def provider(self, model_id: str) -> Any:
        return self.imagegen.provider(model_id) if self._is_t2i(model_id) else super().provider(model_id)

    def check_vram_static(self, model_id: str) -> None:
        self.imagegen.check_vram_static(model_id)

    def offload(self, model_ids: Any) -> None:
        super().offload(model_ids)
        self.imagegen.offload(model_ids)

    def model_dir(self, model_id: str) -> Path:
        return Path(__file__).resolve().parents[2] / "backend" / ".model" / "test-only-missing" / model_id

    def is_approved(self, model_id: str) -> bool:
        return True

    def preferred_model_ids(self) -> tuple[str, ...]:
        return tuple(spec.id for spec in self.specs())


@pytest.fixture
def wf_app(make_settings: Callable[..., Any]) -> Iterator[Callable[..., SimpleNamespace]]:
    from main import create_app

    stack = contextlib.ExitStack()

    def factory(registry: Any = None, engine: Any = None, **overrides: Any) -> SimpleNamespace:
        settings = make_settings(**{"workflow_sweep_interval_s": 0.2, **overrides})
        registry = registry if registry is not None else WorkflowRegistry()
        engine = engine if engine is not None else FakeEngine()
        app = create_app(settings, engine=engine, part_providers=PartProviderOverrides(registry=registry))
        client = stack.enter_context(TestClient(app))
        part_aware = app.state.container.part_aware
        return SimpleNamespace(app=app, client=client, registry=registry, engine=engine, settings=settings,
                               service=part_aware.workflow_service, part_aware=part_aware)

    with stack:
        yield factory


def _image_set(client: TestClient) -> dict:
    ref_set, _image = create_set_with_image(client, data=png_of(character_image()))
    return ref_set


def _image_body(ref_set: dict, **extra: Any) -> dict:
    return {"source": {"kind": "image", "reference_set_id": ref_set["id"]}, "reconstruction": RECON, **extra}


def _wait(client: TestClient, wf_id: str, statuses: set[str] | None = None, timeout: float = 60.0) -> dict:
    wanted = statuses or TERMINAL

    def poll() -> dict | None:
        body = client.get(f"{API}/{wf_id}").json()
        return body if body.get("status") in wanted else None

    return wait_for(poll, timeout=timeout, interval=0.05, message=f"workflow {wf_id} did not reach {wanted}")


def _run(client: TestClient, body: dict, *, status: str = "completed") -> dict:
    created = client.post(API, json=body)
    assert created.status_code == 202, created.text
    done = _wait(client, created.json()["id"])
    assert done["status"] == status, json.dumps(done, indent=1)
    return done


def _steps(wf: dict) -> dict[str, dict]:
    return {step["id"]: step for step in wf["steps"]}


# --------------------------------------------------------------------------------------------------
# Chains
# --------------------------------------------------------------------------------------------------


def test_image_and_parts_chain_completes_with_outputs_artifacts_and_children(wf_app):
    h = wf_app()
    ref_set = _image_set(h.client)
    wf = _run(h.client, _image_body(ref_set, parts=PARTS))
    steps = _steps(wf)
    assert list(steps) == ["import_image", "segmentation", "reconstruction", "part_extraction", "export"]
    assert {s["status"] for s in steps.values()} == {"done", "skipped"} and steps["segmentation"]["status"] == "skipped"
    assert all(s["node_id"] is None for s in wf["steps"]), "simple-form steps have no graph node"
    assert steps["import_image"]["resource"] == {"kind": "reference_image", "id": ref_set["primary_image_id"],
                                                 "reference_set_id": ref_set["id"], "exists": True}
    out = wf["outputs"]
    job_id, pa_id = out["job_id"], out["part_analysis_id"]
    assert steps["reconstruction"]["resource"] == {"kind": "job", "id": job_id, "reference_set_id": None,
                                                   "exists": True}
    assert steps["part_extraction"]["resource"]["id"] == pa_id
    pa = h.client.get(f"/api/v1/part-analyses/{pa_id}").json()
    assert pa["status"] == "completed" and pa["workflow_id"] == wf["id"]
    assert out["model_url"] == f"/api/v1/jobs/{job_id}/model.glb"
    assert out["parts_model_url"] == pa["result"]["assembled_url"]
    assert out["manifest_url"] == pa["result"]["manifest_url"]
    assert h.client.get(out["parts_model_url"]).status_code == 200
    job = h.client.get(f"/api/v1/jobs/{job_id}").json()
    if job["result"]["preview_url"]:
        assert steps["reconstruction"]["artifacts"] == [{"role": "preview", "url": job["result"]["preview_url"]}]
    front = pa["result"]["preview_urls"].get("front")
    assert steps["part_extraction"]["artifacts"] == ([{"role": "preview", "url": front}] if front else [])
    assert wf["progress"]["percent"] == 100.0 and wf["current_step"] is None and wf["error"] is None
    assert steps["reconstruction"]["warning_count"] == 0 and steps["export"]["warning_count"] == 0
    assert wf["request"]["parts"]["strategy"] == "components" and "graph" not in wf["request"]
    listed = h.client.get(API, params={"job_id": job_id}).json()["items"]
    assert [w["id"] for w in listed] == [wf["id"]]


def test_prompt_one_shot_counts_child_warnings_and_guards_the_ig_delete(wf_app):
    engine = FakeEngine()
    h = wf_app(registry=WorkflowRegistry(t2i=FakeTextToImageProvider(layout="edge")), engine=engine)
    engine.hold()
    body = {"source": {"kind": "prompt", "generation": {"prompt": "a knight with a red cape"}},
            "reconstruction": RECON, "parts": PARTS}
    created = h.client.post(API, json=body)
    assert created.status_code == 202, created.text
    wf_id = created.json()["id"]
    def recon_running() -> dict | None:
        body = h.client.get(f"{API}/{wf_id}").json()
        return body if _steps(body)["reconstruction"]["status"] == "running" else None

    running = wait_for(recon_running, timeout=30, message="reconstruction never started")
    ig_id = running["steps"][0]["resource"]["id"]
    assert_error(h.client.delete(f"/api/v1/image-generations/{ig_id}"), 409, "conflict")
    engine.release()
    wf = _wait(h.client, wf_id)
    assert wf["status"] == "completed", wf
    steps = _steps(wf)
    ig = h.client.get(f"/api/v1/image-generations/{ig_id}").json()
    assert ig["workflow_id"] == wf_id and "t2i_touches_border" in {w["code"] for w in ig["warnings"]}
    assert steps["text_to_image"]["warning_count"] == len(ig["warnings"]) >= 1
    assert steps["reconstruction"]["warning_count"] == 0 and steps["export"]["warning_count"] == 0
    assert steps["text_to_image"]["artifacts"] == [
        {"role": "candidate", "url": f"/api/v1/image-generations/{ig_id}/candidates/0/thumbnail"}
    ]
    imported = steps["import_image"]["resource"]
    assert imported["kind"] == "reference_image" and imported["exists"] is True
    assert wf["outputs"]["image_id"] == imported["id"] and wf["outputs"]["reference_set_id"] == imported[
        "reference_set_id"]
    assert ig["result"]["candidates"][0]["used_as"][0]["image_id"] == imported["id"]
    assert [w["id"] for w in h.client.get(API, params={"image_generation_id": ig_id}).json()["items"]] == [wf_id]
    assert h.client.delete(f"/api/v1/image-generations/{ig_id}").status_code == 204, "allowed once finished"


def test_job_source_rebuilds_parts(wf_app):
    h = wf_app()
    ref_set = _image_set(h.client)
    job = create_job(h.client, ref_set["id"]).json()
    wait_for_job(h.client, job["id"], {"completed"})
    wf = _run(h.client, {"source": {"kind": "job", "job_id": job["id"]}, "parts": PARTS})
    steps = _steps(wf)
    assert steps["import_image"]["status"] == "skipped"
    assert steps["reconstruction"]["status"] == "skipped"
    assert steps["reconstruction"]["resource"]["id"] == job["id"]
    assert steps["part_extraction"]["status"] == "done" and wf["outputs"]["job_id"] == job["id"]
    assert_error(h.client.post(API, json={"source": {"kind": "job", "job_id": job["id"]}}), 422, "validation_error")
    assert_error(h.client.post(API, json={"source": {"kind": "job", "job_id": "job_000000000404"}, "parts": PARTS}),
                 404, "not_found")


def test_segmentation_runs_first_and_its_id_feeds_the_part_analysis(wf_app):
    h = wf_app()
    ref_set = _image_set(h.client)
    wf = _run(h.client, _image_body(ref_set, segmentation={"profile": "baseline"}, parts=PARTS))
    steps = _steps(wf)
    sg_id = wf["outputs"]["segmentation_id"]
    assert steps["segmentation"]["status"] == "done" and steps["segmentation"]["resource"]["id"] == sg_id
    assert steps["segmentation"]["artifacts"] == [{"role": "overlay", "url": f"/api/v1/segmentations/{sg_id}/overlay"}]
    sg = h.client.get(f"/api/v1/segmentations/{sg_id}").json()
    job = h.client.get(f"/api/v1/jobs/{wf['outputs']['job_id']}").json()
    assert sg["workflow_id"] == wf["id"] and sg["finished_at"] <= job["created_at"], "sg_ runs before the job"
    pa = h.client.get(f"/api/v1/part-analyses/{wf['outputs']['part_analysis_id']}").json()
    assert pa["options"]["segmentation_id"] == sg_id


# --------------------------------------------------------------------------------------------------
# Request validation
# --------------------------------------------------------------------------------------------------


def test_request_rules_and_graph_limits(wf_app):
    h = wf_app(workflow_graph_max_bytes=1024)
    ref_set = _image_set(h.client)

    def detail(body: dict) -> dict:
        error = assert_error(h.client.post(API, json=body), 422, "validation_error")
        return error["details"][0] if isinstance(error["details"], list) else error["details"]

    seg_only = detail(_image_body(ref_set, segmentation={}))
    assert seg_only["type"] == "conflicting_fields" and seg_only["loc"] == ["body", "segmentation"]
    seg_id = detail(_image_body(ref_set, parts={"segmentation_id": "sg_0123456789ab"}))
    assert seg_id["type"] == "conflicting_fields" and seg_id["loc"] == ["body", "parts", "segmentation_id"]
    assert detail(_image_body(ref_set, export={"per_part_glbs": True}))["type"] == "extra_forbidden"
    graph = TEMPLATES_BY_ID["image_to_3d_parts"].graph.model_dump(mode="json", by_alias=True)
    assert detail({"graph": graph, "source": {"kind": "job", "job_id": "job_0123456789ab"}})["type"] == (
        "conflicting_fields"
    )
    graph["nodes"][0]["params"] = {"reference_set_id": "rs_" + "x" * 2000}
    assert detail({"graph": graph})["type"] == "graph_too_large"
    assert h.client.get(API).json()["items"] == []


def test_validate_reports_issues_and_the_catalog_supports_etags(wf_app):
    h = wf_app()
    ref_set = _image_set(h.client)
    graph = TEMPLATES_BY_ID["image_to_3d"].graph.model_dump(mode="json", by_alias=True)
    graph["nodes"][0]["params"] = {"reference_set_id": ref_set["id"]}
    report = h.client.post(f"{API}/validate", json={"graph": graph}).json()
    assert report["valid"] is True and report["mode"] == "job" and report["issues"] == []
    assert report["request"]["reconstruction"]["settings"]["resolution"] == 1024
    graph["nodes"].append({"id": "n_refine", "type": "part_refine", "params": {}})
    report = h.client.post(f"{API}/validate", json={"graph": graph}).json()
    assert report["valid"] is False and report["request"] is None
    assert [(i["code"], i["node_id"]) for i in report["issues"]] == [("node_locked", "n_refine")]
    simple = h.client.post(f"{API}/validate", json=_image_body(ref_set, parts=PARTS)).json()
    assert simple["valid"] is True and simple["mode"] == "workflow"
    missing = h.client.post(f"{API}/validate", json=_image_body({"id": "rs_000000000404"}, parts=PARTS)).json()
    assert [(i["code"], i["node_id"]) for i in missing["issues"]] == [("not_found", None)]
    assert h.client.get(API).json()["items"] == [], "validate never creates anything"

    catalog = h.client.get(f"{API}/catalog")
    assert catalog.status_code == 200 and catalog.headers["ETag"] == catalog.json()["etag"]
    body = catalog.json()
    assert body["schema"] == "img3d.workflow_catalog/1" and set(body) >= {"templates", "limits", "legacy_job_node_map"}
    assert body["etag"] == h.client.get("/api/v1/features").json()["workflow"]["catalog_etag"]
    cached = h.client.get(f"{API}/catalog", headers={"If-None-Match": body["etag"]})
    assert cached.status_code == 304 and cached.headers["ETag"] == body["etag"] and cached.content == b""


def test_graph_form_records_node_ids_and_serves_the_graph(wf_app):
    h = wf_app()
    ref_set = _image_set(h.client)
    graph = TEMPLATES_BY_ID["image_to_3d_parts"].graph.model_dump(mode="json", by_alias=True)
    params = {"n_input": {"reference_set_id": ref_set["id"]}, "n_recon": {"resolution": 1024},
              "n_parts": PARTS, "n_export": {"per_part_glbs": True}}
    for node in graph["nodes"]:
        node["params"] = params[node["id"]]
    layout = {"positions": {"n_input": {"x": 0, "y": 0}}}
    wf = _run(h.client, {"graph": graph, "layout": layout, "name": "From the node editor"})
    assert wf["graph_stored"] is True and wf["name"] == "From the node editor"
    assert {s["id"]: s["node_id"] for s in wf["steps"]} == {
        "import_image": "n_input", "segmentation": None, "reconstruction": "n_recon", "part_extraction": "n_parts",
        "export": "n_export"}
    assert wf["request"]["parts"]["export"]["per_part_glbs"] is True
    stored = h.client.get(f"{API}/{wf['id']}/graph").json()
    assert stored == {"graph": graph, "layout": layout}
    invalid = dict(graph, nodes=[*graph["nodes"], {"id": "n_tune", "type": "mesh_tune", "params": {}}])
    error = assert_error(h.client.post(API, json={"graph": invalid}), 422, "invalid_workflow")
    assert [i["code"] for i in error["details"]["issues"]] == ["node_locked"]
    simple = _run(h.client, _image_body(ref_set))
    assert simple["graph_stored"] is False
    assert_error(h.client.get(f"{API}/{simple['id']}/graph"), 404, "not_found")


# --------------------------------------------------------------------------------------------------
# Failures, cancel, queues
# --------------------------------------------------------------------------------------------------


def test_a_failed_child_fails_the_workflow_with_its_error_and_keeps_outputs(wf_app):
    engine = FakeEngine(fail_with=RuntimeError("shape decoder exploded"))
    h = wf_app(engine=engine)
    ref_set = _image_set(h.client)
    wf = _run(h.client, _image_body(ref_set, parts=PARTS), status="failed")
    job = h.client.get(f"/api/v1/jobs/{wf['outputs']['job_id']}").json()
    assert wf["error"]["code"] == "step_failed"
    assert wf["error"]["details"] == {"step": "reconstruction", "child_kind": "job", "child_id": job["id"],
                                      "child_error": job["error"]}
    assert wf["outputs"]["reference_set_id"] == ref_set["id"] and wf["outputs"]["model_url"] is None
    steps = _steps(wf)
    assert steps["reconstruction"]["status"] == "failed" and wf["current_step"] == "reconstruction"
    assert steps["part_extraction"]["status"] == "skipped" and steps["export"]["status"] == "skipped"


def test_a_failed_part_analysis_copies_its_error(wf_app):
    lifter = FakeLabelLifter(fail={"fit_camera": RuntimeError("rasteriser exploded")})
    h = wf_app(registry=WorkflowRegistry(lifter=lifter))
    ref_set = _image_set(h.client)
    wf = _run(h.client, _image_body(ref_set, parts={"strategy": "semantic_parts", "segmentation": {"profile":
                                                                                                    "baseline"}}),
              status="failed")
    pa = h.client.get(f"/api/v1/part-analyses/{wf['outputs']['part_analysis_id']}").json()
    assert pa["status"] == "failed"
    details = wf["error"]["details"]
    assert details["step"] == "part_extraction" and details["child_kind"] == "part_analysis"
    assert details["child_error"] == {"code": pa["error"]["code"], "message": pa["error"]["message"],
                                      "details": pa["error"]["details"]}
    assert wf["outputs"]["job_id"] and wf["outputs"]["model_url"], "outputs already produced stay set"


def test_cancel_mid_reconstruction_cancels_the_job(wf_app):
    engine = FakeEngine()
    h = wf_app(engine=engine)
    engine.hold()
    ref_set = _image_set(h.client)
    wf_id = h.client.post(API, json=_image_body(ref_set, parts=PARTS)).json()["id"]
    job_id = h.client.get(f"{API}/{wf_id}").json()["outputs"]["job_id"]
    wait_for_job(h.client, job_id, {"running"})
    assert_error(h.client.delete(f"{API}/{wf_id}"), 409, "conflict")
    cancelled = h.client.post(f"{API}/{wf_id}/cancel")
    assert cancelled.status_code == 202 and cancelled.json()["status"] == "cancelled"
    engine.release()
    assert wait_for_job(h.client, job_id, {"cancelled", "completed", "failed"})["status"] == "cancelled"
    wf = h.client.get(f"{API}/{wf_id}").json()
    steps = _steps(wf)
    assert steps["reconstruction"]["status"] == "cancelled" and steps["part_extraction"]["status"] == "skipped"
    assert_error(h.client.post(f"{API}/{wf_id}/cancel"), 409, "conflict")
    assert wf["outputs"]["part_analysis_id"] is None


def test_queue_full_at_creation_is_all_or_nothing(wf_app):
    engine = FakeEngine()
    h = wf_app(engine=engine, max_queue_size=1)
    engine.hold()
    ref_set = _image_set(h.client)
    first = create_job(h.client, ref_set["id"]).json()
    wait_for_job(h.client, first["id"], {"running"})
    assert create_job(h.client, ref_set["id"]).status_code == 202, "the job queue is now full"
    assert_error(h.client.post(API, json=_image_body(ref_set, parts=PARTS)), 429, "queue_full")
    assert h.client.get(API).json()["items"] == [] and len(h.client.get("/api/v1/jobs").json()["items"]) == 2
    engine.release()


def test_a_refused_child_creation_later_fails_with_queue_full(wf_app):
    engine = FakeEngine()
    t2i = FakeTextToImageProvider()
    t2i.block_generate = threading.Event()
    h = wf_app(registry=WorkflowRegistry(t2i=t2i), engine=engine, run_queue_max_size=1)
    engine.hold()
    ref_set = _image_set(h.client)
    wf_id = h.client.post(API, json=_image_body(ref_set, parts=PARTS)).json()["id"]
    job_id = h.client.get(f"{API}/{wf_id}").json()["outputs"]["job_id"]
    wait_for_job(h.client, job_id, {"running"})
    ig1 = h.client.post("/api/v1/image-generations", json={"prompt": "a lamp"}).json()
    wait_for(lambda: h.client.get(f"/api/v1/image-generations/{ig1['id']}").json()["status"] == "running" or None,
             message="ig1 never started")
    assert h.client.post("/api/v1/image-generations", json={"prompt": "a chair"}).status_code == 202
    assert_error(h.client.post(API, json=_image_body(ref_set, parts=PARTS)), 429, "queue_full")
    engine.release()
    wf = _wait(h.client, wf_id)
    t2i.block_generate.set()
    assert wf["status"] == "failed" and wf["error"]["code"] == "queue_full"
    details = wf["error"]["details"]
    assert details["step"] == "part_extraction" and details["child_id"] is None
    assert details["child_error"]["code"] == "queue_full"
    assert wf["outputs"]["job_id"] == job_id and wf["outputs"]["model_url"]
    assert wf["outputs"]["part_analysis_id"] is None


# --------------------------------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------------------------------


def test_restart_marks_active_workflows_interrupted(wf_app, runtime_dir):
    record = WorkflowRecord(
        id="wf_00000000000a", created_at=utcnow(), status=JobStatus.RUNNING, started_at=utcnow(),
        current_step="reconstruction",
        steps=[WorkflowStep(id="reconstruction", node="image_to_3d", status="running"),
               WorkflowStep(id="export", node="export")],
    )
    wf_dir = runtime_dir / "workflows" / record.id
    wf_dir.mkdir(parents=True)
    (wf_dir / "workflow.json").write_text(json.dumps(record.model_dump(mode="json")), encoding="utf-8")
    h = wf_app()
    wf = h.client.get(f"{API}/{record.id}").json()
    assert wf["status"] == "failed" and wf["error"]["code"] == "interrupted"
    assert [s["status"] for s in wf["steps"]] == ["failed", "skipped"]
    assert h.client.delete(f"{API}/{record.id}").status_code == 204


def test_children_survive_a_workflow_delete(wf_app):
    h = wf_app()
    wf = _run(h.client, _image_body(_image_set(h.client), parts=PARTS))
    assert h.client.delete(f"{API}/{wf['id']}").status_code == 204
    assert_error(h.client.get(f"{API}/{wf['id']}"), 404, "not_found")
    assert h.client.get(f"/api/v1/jobs/{wf['outputs']['job_id']}").status_code == 200
    assert h.client.get(f"/api/v1/part-analyses/{wf['outputs']['part_analysis_id']}").status_code == 200
    assert_error(h.client.delete(f"{API}/{wf['id']}"), 404, "not_found")


def test_the_sweep_recovers_dropped_events(wf_app):
    h = wf_app(workflow_sweep_interval_s=0.2)
    subscription = h.service._subscription
    assert subscription is not None
    subscription.close()
    started = time.monotonic()
    wf = _run(h.client, _image_body(_image_set(h.client), parts=PARTS))
    assert wf["status"] == "completed" and time.monotonic() - started < 30
    retention_view = h.part_aware.workflow_store
    assert retention_view.active_child_ids() == frozenset()
    assert [r.id for r in retention_view.list_terminal()] == [wf["id"]]


def test_the_store_lists_active_children_for_retention(wf_app):
    engine = FakeEngine()
    h = wf_app(engine=engine)
    engine.hold()
    created = h.client.post(API, json=_image_body(_image_set(h.client), parts=PARTS)).json()
    store = h.part_aware.workflow_store
    assert created["outputs"]["job_id"] in store.active_child_ids()
    assert store.delete(created["id"]) is False, "active workflows are never deleted"
    engine.release()
    _wait(h.client, created["id"])
    assert store.active_child_ids() == frozenset()
