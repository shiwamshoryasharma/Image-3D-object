"""Part analysis runs (``pa_``) through the API with test doubles (part-aware DESIGN.md sections 4.6, 6.4, 10.2).

CPU only: :class:`fakes.FakeEngine` jobs, the fake segmentation registry (``fakes_segmentation``) and the numpy
:class:`fakes_lifting.FakeLabelLifter`. The semantic cases replace the fake job's ``model.glb``/``preview.png`` with a
textured two-shell mesh and its own front render, so the fake detector finds the hair and dress colours on it.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import threading
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import trimesh
from fakes import FakeEngine
from fakes_lifting import FakeLabelLifter
from fakes_segmentation import COLORS, FakeSegmentationRegistry, character_image, png_of
from helpers import assert_error, create_job, create_set_with_image, wait_for, wait_for_job, ws_collect_until, ws_receive
from PIL import Image
from starlette.testclient import TestClient

from core.part_aware_container import PartProviderOverrides
from inference.parts import cameras
from inference.parts.base import CameraSpec, MeshArrays
from parts.gltf_io import base_color_texture, read_base_glb
from parts.taxonomy import TAXONOMY

API = "/api/v1/part-analyses"
TERMINAL = {"completed", "failed", "cancelled"}
NO_PREVIEWS = {"export": {"previews": False}}
BASELINE = {"segmentation": {"profile": "baseline"}}


@pytest.fixture
def pa_app(make_settings: Callable[..., Any]) -> Iterator[Callable[..., SimpleNamespace]]:
    from main import create_app

    stack = contextlib.ExitStack()

    def factory(registry: Any = None, engine: Any = None, *, lifter: Any = True, **overrides: Any) -> SimpleNamespace:
        settings = make_settings(**overrides)
        registry = registry if registry is not None else FakeSegmentationRegistry()
        if lifter is True:
            lifter = FakeLabelLifter()
        if lifter:
            registry.providers["raster_lifter"] = lifter
        engine = engine if engine is not None else FakeEngine()
        app = create_app(settings, engine=engine, part_providers=PartProviderOverrides(registry=registry))
        client = stack.enter_context(TestClient(app))
        return SimpleNamespace(app=app, client=client, registry=registry, engine=engine, settings=settings,
                               lifter=lifter or None)

    with stack:
        yield factory


def _job(client: TestClient, *, rgba: bool = False) -> tuple[dict, dict]:
    ref_set, image = create_set_with_image(client, data=png_of(character_image(rgba=rgba)))
    job = create_job(client, ref_set["id"]).json()
    wait_for_job(client, job["id"], {"completed"})
    return ref_set, client.get(f"/api/v1/jobs/{job['id']}").json()


def _post(client: TestClient, job_id: str, body: dict | None = None):
    return client.post(f"/api/v1/jobs/{job_id}/part-analyses", json=body)


def _wait(client: TestClient, pa_id: str, statuses: set[str] | None = None, timeout: float = 60.0) -> dict:
    wanted = statuses or TERMINAL

    def poll() -> dict | None:
        body = client.get(f"{API}/{pa_id}").json()
        return body if body.get("status") in wanted else None

    return wait_for(poll, timeout=timeout, interval=0.05, message=f"part analysis {pa_id} did not reach {wanted}")


def _run(client: TestClient, job_id: str, body: dict | None = None, *, status: str = "completed") -> dict:
    created = _post(client, job_id, body)
    assert created.status_code == 202, created.text
    run = _wait(client, created.json()["id"])
    assert run["status"] == status, run
    return run


def _job_dir(app: SimpleNamespace, job_id: str) -> Path:
    return Path(app.settings.runtime_dir) / "jobs" / job_id


def _two_shell_mesh(job_dir: Path) -> None:
    """Replace the fake job's mesh with a textured dress, head and hair boxes and its preview with a front render."""
    texture = Image.new("RGB", (96, 32), COLORS["dress"])
    texture.paste(COLORS["hair"], (0, 0, 32, 32))
    texture.paste(COLORS["skin"], (32, 0, 64, 32))
    shells = []
    for extents, center, u, subdivisions in (((0.7, 0.9, 0.45), (0.0, -0.35, 0.0), 0.83, 4),
                                             ((0.45, 0.35, 0.35), (0.0, 0.33, 0.0), 0.5, 3),
                                             ((0.55, 0.15, 0.4), (0.0, 0.62, 0.0), 0.17, 3)):
        box = trimesh.creation.box(extents=extents)
        for _ in range(subdivisions):
            box = box.subdivide()
        shells.append((np.asarray(box.vertices) + np.asarray(center), np.asarray(box.faces), u))
    vertices, faces, uv, offset = [], [], [], 0
    for verts, tris, u in shells:
        vertices.append(verts)
        faces.append(tris + offset)
        uv.append(np.tile([u, 0.5], (len(verts), 1)))
        offset += len(verts)
    material = trimesh.visual.material.PBRMaterial(baseColorTexture=texture, metallicFactor=0.0, roughnessFactor=0.8)
    mesh = trimesh.Trimesh(np.vstack(vertices), np.vstack(faces), process=False,
                           visual=trimesh.visual.TextureVisuals(uv=np.vstack(uv), material=material))
    (job_dir / "model.glb").write_bytes(trimesh.Scene(mesh).export(file_type="glb"))
    base = read_base_glb(job_dir / "model.glb")
    lifter = FakeLabelLifter()
    handle = lifter.upload(MeshArrays(base.positions, base.faces, base.uv, base_color_texture(base)))
    radius = cameras.bounding_radius(base.positions)
    front = CameraSpec(0.0, 0.0, 30.0, cameras.fit_distance(30.0, radius))
    Image.fromarray(lifter.render_textured(handle, front, (256, 256))).save(job_dir / "preview.png")


def _semantic_job(app: SimpleNamespace) -> dict:
    _ref, job = _job(app.client)
    _two_shell_mesh(_job_dir(app, job["id"]))
    return job


def _manifest(client: TestClient, pa_id: str) -> dict:
    response = client.get(f"{API}/{pa_id}/manifest")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-cache"
    return response.json()


# ==================================================================================================
# components (CPU geometry path) and the file routes
# ==================================================================================================


def test_components_end_to_end_serves_files_and_caches_on_demand_exports(pa_app):
    app = pa_app()
    client = app.client
    _ref, job = _job(client)
    job_id = job["id"]
    run = _run(client, job_id, {"strategy": "components"})
    result = run["result"]
    pa_id = run["id"]
    assert run["job_id"] == job_id and run["kind"] == "part_analysis"
    assert result["assembled_url"] == f"{API}/{pa_id}/model.glb"
    assert result["download_url"] == f"{API}/{pa_id}/export/glb"
    assert result["overlay_url"] is None and result["camera_fit_iou"] is None
    assert result["region_attribute"] == "_FEATURE_ID_0"
    assert set(result["preview_urls"]) == {"front", "back", "left", "right"}
    assert {s["id"]: s["status"] for s in run["steps"]}["camera_fit"] == "skipped"
    assert set(result["timings"]) >= {"prepare", "partition", "export", "previews", "total"}

    manifest = _manifest(client, pa_id)
    assert manifest["schema"] == "img3d.parts/1" and manifest["analysis_id"] == pa_id
    reassembly = manifest["reassembly"]
    assert all(reassembly[k] for k in ("verified", "positions_exact", "uvs_exact", "normals_exact", "faces_exact"))
    assert manifest["strategy"]["segmentation"]["source"] == "none"
    assert len(manifest["artifacts"]["previews"]) == 4

    glb = client.get(result["assembled_url"])
    assert glb.status_code == 200 and glb.headers["content-type"] == "model/gltf-binary"
    assert glb.content[:4] == b"glTF"
    download = client.get(result["download_url"])
    assert f"img3d_{job_id}_parts_{pa_id}.glb" in download.headers["content-disposition"]
    labels = client.get(result["face_labels_url"])
    assert labels.status_code == 200 and labels.headers["x-img3d-dtype"] == "uint16"
    assert int(labels.headers["x-img3d-count"]) == manifest["source"]["faces"]
    assert client.get(result["preview_urls"]["front"]).headers["content-type"] == "image/png"

    base_part = next(p for p in result["parts"] if p["kind"] == "base")
    part_glb = client.get(base_part["model_url"])
    assert part_glb.status_code == 200 and part_glb.content[:4] == b"glTF"
    cache = Path(app.settings.runtime_dir) / "cache" / "part_exports" / pa_id
    assert (cache / "parts" / f"{base_part['id']}.glb").is_file()
    exported = client.get(base_part["download_url"])
    assert f"img3d_{job_id}_{base_part['id']}.glb" in exported.headers["content-disposition"]
    assert_error(client.get(f"{API}/{pa_id}/parts/nope/model.glb"), 404, "not_found")
    assert_error(client.get(f"{API}/{pa_id}/segmentation/overlay.png"), 404, "not_found")
    assert_error(client.get(f"{API}/{pa_id}/buffers/nope"), 404, "not_found")
    assert_error(client.get(f"{API}/{pa_id}/masks/30"), 404, "not_found")

    bundle = client.get(f"{API}/{pa_id}/export/zip", params={"include_base": "true"})
    assert bundle.status_code == 200 and bundle.headers["content-type"] == "application/zip"
    names = set(zipfile.ZipFile(io.BytesIO(bundle.content)).namelist())
    assert {"manifest.json", "model_parts.glb", "model.glb", "buffers/face_labels.u16"} <= names
    assert f"parts/{base_part['id']}.glb" in names
    assert (cache / "bundle_with_base.zip").is_file()

    assert client.get(f"/api/v1/jobs/{job_id}/part-analyses/latest").json()["id"] == pa_id
    assert [i["id"] for i in client.get(f"/api/v1/jobs/{job_id}/part-analyses").json()["items"]] == [pa_id]
    assert client.delete(f"{API}/{pa_id}").status_code == 204
    assert not cache.exists() and not (_job_dir(app, job_id) / "parts" / pa_id).exists()
    assert_error(client.get(f"{API}/{pa_id}"), 404, "not_found")


def test_options_defaults_export_options_and_validation(pa_app):
    app = pa_app()
    client = app.client
    _ref, job = _job(client)
    job_id = job["id"]
    for export in ({}, {"per_part_glbs": False}):
        run = _run(client, job_id, {"strategy": "components", "min_part_faces": None, "export": export})
        assert run["result"]["assembled_url"] and run["result"]["download_url"]
        faces = _manifest(client, run["id"])["source"]["faces"]
        assert _manifest(client, run["id"])["strategy"]["min_part_faces"] == max(50, round(1e-4 * faces))
    assert run["options"]["views"] == app.settings.parts_default_views

    written = _run(client, job_id, {"strategy": "components", "export": {"per_part_glbs": True, "previews": False}})
    manifest = _manifest(client, written["id"])
    base = next(p for p in manifest["parts"] if p["kind"] == "base")
    assert base["files"]["glb"] == f"parts/{base['id']}.glb"
    assert client.get(f"{API}/{written['id']}/parts/{base['id']}/model.glb").status_code == 200
    assert written["result"]["preview_urls"] == {}

    error = assert_error(_post(client, job_id, {"export": {"assembled_glb": False}}), 422, "validation_error")
    assert "extra_forbidden" in str(error)
    assert_error(_post(client, job_id, {"separate": ["wings"]}), 422, "validation_error")
    assert_error(_post(client, job_id, {"views": 25}), 422, "validation_error")
    assert_error(_post(client, job_id, {"strategy": "per_part_regeneration"}), 503, "capability_unavailable")


def test_incomplete_job_unknown_ids_and_files_before_completion(pa_app):
    gate = threading.Event()
    app = pa_app(registry=FakeSegmentationRegistry(gate=gate))
    client = app.client
    ref_set, job = _job(client)
    app.engine.hold()
    try:
        running = create_job(client, ref_set["id"]).json()
        error = assert_error(_post(client, running["id"]), 409, "conflict")
        assert error["details"]["reason"] == "job_not_completed"
    finally:
        app.engine.release()
    assert_error(_post(client, "job_000000000000"), 404, "not_found")
    assert_error(client.get(f"{API}/pa_000000000000"), 404, "not_found")
    assert_error(client.get("/api/v1/jobs/job_000000000000/part-analyses"), 404, "not_found")
    none = assert_error(client.get(f"/api/v1/jobs/{job['id']}/part-analyses/latest"), 404, "not_found")
    assert none["details"]["reason"] == "none"

    _two_shell_mesh(_job_dir(app, job["id"]))
    created = _post(client, job["id"], {"strategy": "semantic_parts", **NO_PREVIEWS})
    assert created.status_code == 202, created.text
    pa_id = created.json()["id"]
    assert app.registry.detector.started.wait(10)
    conflict = assert_error(client.get(f"{API}/{pa_id}/manifest"), 409, "conflict")
    assert conflict["details"]["reason"] == "not_completed"
    assert_error(client.delete(f"{API}/{pa_id}"), 409, "conflict")
    assert client.post(f"{API}/{pa_id}/cancel").status_code == 202
    assert _wait(client, pa_id)["status"] == "cancelled"
    assert_error(client.post(f"{API}/{pa_id}/cancel"), 409, "conflict")
    assert_error(client.get(f"/api/v1/jobs/{job['id']}/part-analyses/latest"), 404, "not_found")
    gate.set()


# ==================================================================================================
# Semantic strategies with the fake segmenter and lifter
# ==================================================================================================


def test_semantic_parts_cuts_the_hair_and_serves_masks_overlay_and_part_glbs(pa_app):
    app = pa_app()
    client = app.client
    job = _semantic_job(app)
    run = _run(client, job["id"], {"strategy": "semantic_parts", "views": 0, **BASELINE})
    result = run["result"]
    pa_id = run["id"]
    assert result["status"] == "completed", run["warnings"]
    assert result["camera_fit_iou"] >= 0.6
    assert result["unlabelled_face_ratio"] <= 0.10
    hair = next(p for p in result["parts"] if p["category"] == "hair")
    assert hair["kind"] == "physical" and hair["model_url"] and hair["node_name"] == "part:hair"
    hair_glb = client.get(hair["model_url"])
    assert hair_glb.status_code == 200 and hair_glb.content[:4] == b"glTF"
    value = TAXONOMY["hair"].value
    assert hair["mask_url"] == f"{API}/{pa_id}/masks/{value}"
    assert client.get(hair["mask_url"]).headers["content-type"] == "image/png"
    if hair["thumbnail_url"]:
        assert client.get(hair["thumbnail_url"]).headers["content-type"] == "image/webp"
    assert client.get(result["overlay_url"]).status_code == 200
    for name in ("source.png", "labels.png"):
        assert client.get(f"{API}/{pa_id}/segmentation/{name}").status_code == 200
    assert_error(client.get(f"{API}/{pa_id}/segmentation/frame.json"), 404, "not_found")
    manifest = _manifest(client, pa_id)
    assert manifest["strategy"]["segmentation"]["source"] == "internal"
    assert manifest["strategy"]["segmentation"]["profile"] == "baseline"
    assert [v["id"] for v in manifest["views"]] == ["input"]
    assert {m["id"] for m in run["models"]} >= {"raster_lifter", "grounding_dino_base", "sam2_1_hiera_large"}
    regions = [p for p in result["parts"] if p["kind"] in ("region", "physical_deferred")]
    for part in regions:
        assert part["model_url"] is None
        assert_error(client.get(f"{API}/{pa_id}/parts/{part['id']}/model.glb"), 409, "conflict")
    lifter_threads = {thread for method, thread in app.lifter.calls if method in ("fit_camera", "raster")}
    assert lifter_threads and all(t.startswith("gpu-worker") for t in lifter_threads)


def test_extra_views_vote_and_auto_profile_degradation_is_partial(pa_app, monkeypatch):
    import pipelines.part_analysis as pa_module

    monkeypatch.setattr(pa_module, "EXTRA_VIEW_SIZE", 192)
    app = pa_app()
    client = app.client
    job = _semantic_job(app)
    run = _run(client, job["id"], {"strategy": "semantic_regions", "views": 2, **NO_PREVIEWS})
    manifest = _manifest(client, run["id"])
    assert [v["id"] for v in manifest["views"]] == ["input", "view_1", "view_2"]
    lift = next(s for s in run["steps"] if s["id"] == "label_lift")
    assert [v["id"] for v in lift["details"]["views"]] == ["input", "view_1", "view_2"]
    # The fake caption says "anime"; no anime parser is usable here, so the profile degrades -> partial.
    assert run["result"]["status"] == "partial"
    assert "profile_degraded" in {w["code"] for w in run["warnings"]}
    assert run["result"]["physical_count"] == 0  # semantic_regions never cuts


def test_segmentation_reuse_rules(pa_app):
    app = pa_app()
    client = app.client
    ref_set, image = create_set_with_image(client, data=png_of(character_image()))
    rgba_set, rgba_image = create_set_with_image(client, data=png_of(character_image(rgba=True)))
    jobs = {}
    for key, set_id in (("opaque", ref_set["id"]), ("rgba", rgba_set["id"])):
        job = create_job(client, set_id).json()
        jobs[key] = wait_for_job(client, job["id"], {"completed"})

    def segmentation(set_id: str, image_id: str) -> str:
        source = {"kind": "reference_image", "reference_set_id": set_id, "image_id": image_id}
        sg_id = client.post("/api/v1/segmentations", json={"source": source}).json()["id"]
        wait_for(lambda: client.get(f"/api/v1/segmentations/{sg_id}").json()["status"] == "completed" or None,
                 timeout=30)
        return sg_id

    opaque_sg = segmentation(ref_set["id"], image["id"])
    run = _run(client, jobs["opaque"]["id"], {"strategy": "semantic_regions", "views": 0,
                                               "segmentation_id": opaque_sg, **NO_PREVIEWS}, status="completed")
    strategy = _manifest(client, run["id"])["strategy"]["segmentation"]
    assert strategy == {**strategy, "source": "internal", "segmentation_id": opaque_sg}
    assert "segmentation_rerun_for_alignment" in {w["code"] for w in run["warnings"]}

    rgba_sg = segmentation(rgba_set["id"], rgba_image["id"])
    reused = _run(client, jobs["rgba"]["id"], {"strategy": "semantic_regions", "views": 0,
                                                "segmentation_id": rgba_sg, **NO_PREVIEWS})
    assert _manifest(client, reused["id"])["strategy"]["segmentation"]["source"] == "reused"
    assert {s["id"]: s["status"] for s in reused["steps"]}["segmentation_2d"] == "skipped"
    assert "segmentation_rerun_for_alignment" not in {w["code"] for w in reused["warnings"]}

    assert_error(_post(client, jobs["rgba"]["id"], {"segmentation_id": "sg_000000000000"}), 404, "not_found")


# ==================================================================================================
# Capability, flags, failures
# ==================================================================================================


def test_capability_and_feature_flag(pa_app):
    app = pa_app(lifter=False)
    client = app.client
    _ref, job = _job(client)
    assert_error(_post(client, job["id"], {"strategy": "semantic_parts"}), 503, "capability_unavailable")
    run = _run(client, job["id"])  # strategy null -> components without a lifter; previews skipped
    assert run["options"]["strategy"] == "components"
    assert run["result"]["preview_urls"] == {}
    assert {s["id"]: s["status"] for s in run["steps"]}["previews"] == "skipped"

    off = pa_app(part_analysis_enabled=False)
    _ref, other = _job(off.client)
    assert_error(_post(off.client, other["id"]), 503, "feature_disabled")
    assert off.client.get(f"/api/v1/jobs/{other['id']}/part-analyses").json() == {"items": []}


def test_failed_lifting_keeps_the_log_and_publishes_nothing(pa_app):
    app = pa_app(lifter=FakeLabelLifter(fail={"fit_camera": RuntimeError("rasteriser exploded")}))
    client = app.client
    job = _semantic_job(app)
    run = _run(client, job["id"], {"strategy": "semantic_parts", **NO_PREVIEWS}, status="failed")
    assert run["error"]["code"] == "camera_fit_failed"
    log = client.get(f"{API}/{run['id']}/log")
    assert log.status_code == 200 and "rasteriser exploded" in log.text
    assert not (_job_dir(app, job["id"]) / "parts" / run["id"] / "asset").exists()
    assert_error(client.get(f"{API}/{run['id']}/manifest"), 409, "conflict")


# ==================================================================================================
# Job deletion, retention, events
# ==================================================================================================


def test_job_delete_cancels_an_active_analysis_and_removes_parts(pa_app):
    gate = threading.Event()
    app = pa_app(registry=FakeSegmentationRegistry(gate=gate))
    client = app.client
    job = _semantic_job(app)
    pa_id = _post(client, job["id"], {"strategy": "semantic_parts", **NO_PREVIEWS}).json()["id"]
    assert app.registry.detector.started.wait(10)
    assert (_job_dir(app, job["id"]) / "parts" / pa_id / "run.json").is_file()
    assert client.delete(f"/api/v1/jobs/{job['id']}").status_code == 204
    assert not _job_dir(app, job["id"]).exists()
    assert_error(client.get(f"{API}/{pa_id}"), 404, "not_found")
    gate.set()


def test_retention_deleting_the_job_mid_run_fails_with_job_deleted_without_resurrection(pa_app):
    gate = threading.Event()
    app = pa_app(registry=FakeSegmentationRegistry(gate=gate))
    client = app.client
    job = _semantic_job(app)
    job_dir = _job_dir(app, job["id"])
    with client.websocket_connect(f"/api/v1/ws?job_id={job['id']}") as ws:
        ws_receive(ws)
        pa_id = _post(client, job["id"], {"strategy": "semantic_parts", **NO_PREVIEWS}).json()["id"]
        assert app.registry.detector.started.wait(10)
        shutil.rmtree(job_dir)  # retention without hooks
        gate.set()
        messages = ws_collect_until(
            ws, lambda m: m["type"] == "part_analysis.updated" and m["data"]["status"] in TERMINAL, timeout=30
        )
    final = messages[-1]["data"]
    assert final["id"] == pa_id and final["status"] == "failed" and final["error"]["code"] == "job_deleted"
    assert not job_dir.exists()


def test_part_analysis_events_honour_the_job_id_filter(pa_app):
    app = pa_app()
    client = app.client
    _ref, first = _job(client)
    _ref, second = _job(client)
    with client.websocket_connect(f"/api/v1/ws?job_id={second['id']}") as ws:
        ws_receive(ws)
        assert _post(client, first["id"], {"strategy": "components", **NO_PREVIEWS}).status_code == 202
        second_pa = _post(client, second["id"], {"strategy": "components", **NO_PREVIEWS}).json()["id"]
        messages = ws_collect_until(
            ws, lambda m: m["type"] == "part_analysis.updated" and m["data"]["status"] in TERMINAL, timeout=30
        )
    seen = {m["data"]["job_id"] for m in messages if m["type"] == "part_analysis.updated"}
    assert seen == {second["id"]} and messages[-1]["data"]["id"] == second_pa
