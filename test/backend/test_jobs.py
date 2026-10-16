"""Job lifecycle end-to-end with the fake engine: create, progress over WebSocket, results, cancel, queue, errors."""

from __future__ import annotations

import json
from io import BytesIO

import numpy as np
import pytest
from fakes import FakeEngine
from helpers import (
    assert_error,
    create_job,
    create_set,
    create_set_with_image,
    image_bytes,
    png_bytes,
    upload,
    wait_for,
    wait_for_job,
    ws_collect_until,
    ws_receive,
)
from PIL import Image

from core.exceptions import EngineError
from inference.base import EngineCapabilities, PolygonTargetRange
from inference.unavailable import UnavailableEngine

TERMINAL = {"completed", "failed", "cancelled"}
STAGE_ORDER = [
    "preprocessing",
    "conditioning",
    "sparse_structure",
    "shape",
    "texture",
    "decoding",
    "exporting",
    "finalizing",
    "done",
]


def test_job_lifecycle_end_to_end(harness):
    client = harness.client
    ref_set = create_set(client)
    front = upload(client, ref_set["id"], png_bytes((256, 256), mode="RGBA"), slot="front").json()["image"]
    back = upload(client, ref_set["id"], image_bytes("JPEG", (200, 300)), slot="back").json()["image"]

    with client.websocket_connect("/api/v1/ws") as ws:
        assert ws_receive(ws)["type"] == "hello"
        response = create_job(client, ref_set["id"], {"resolution": 512, "seed": 42, "randomize_seed": False})
        assert response.status_code == 202, response.text
        job = response.json()
        assert job["id"].startswith("job_")
        assert job["status"] == "queued"
        assert job["engine"] == "fake-engine"
        assert job["seed"] == 42
        assert job["primary_image_id"] == front["id"]
        assert job["references"] == [
            {"image_id": front["id"], "slot": "front", "is_primary": True},
            {"image_id": back["id"], "slot": "back", "is_primary": False},
        ]
        assert job["settings"]["resolution"] == 512
        assert job["settings"]["reference_mode"] == "primary_only"
        assert job["settings"]["texture_size"] == 2048
        assert job["progress"]["stage"] == "queued"
        assert job["result"] is None and job["error"] is None

        messages = ws_collect_until(
            ws, lambda m: m["type"] == "job.updated" and m["data"]["id"] == job["id"] and m["data"]["status"] in TERMINAL
        )
        if not any(m["type"] == "system.updated" for m in messages):  # debounced; may trail a fast job
            messages += ws_collect_until(ws, lambda m: m["type"] == "system.updated")

    updates = [m["data"] for m in messages if m["type"] == "job.updated" and m["data"]["id"] == job["id"]]
    assert all(m["ts"].endswith("Z") for m in messages)
    statuses = [u["status"] for u in updates]
    assert statuses[-1] == "completed"
    assert "running" in statuses
    # Progress is throttled (<= 5/s), so a fast engine's intermediate stages may be coalesced; the stage index
    # must still only move forward and the final state must be "done".
    indexes = [u["progress"]["stage_index"] for u in updates]
    assert indexes == sorted(indexes)
    assert updates[-1]["progress"]["stage"] == "done"
    assert all(u["progress"]["stage"] in STAGE_ORDER + ["queued", "waiting_for_model"] for u in updates)
    percents = [u["progress"]["percent"] for u in updates]
    assert percents == sorted(percents)
    assert percents[-1] == 100.0
    assert any(m["type"] == "system.updated" for m in messages)

    final = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert final["status"] == "completed"
    assert final["started_at"] and final["finished_at"]
    assert final["queue_position"] is None
    assert final["progress"]["stage"] == "done" and final["progress"]["stage_index"] == 9
    result = final["result"]
    assert result["model_url"] == f"/api/v1/jobs/{job['id']}/model.glb"
    assert result["download_url"] == f"/api/v1/jobs/{job['id']}/export/glb"
    assert result["preview_url"] == f"/api/v1/jobs/{job['id']}/preview"
    assert result["seed"] == 42
    assert result["resolution"] == 512 and result["effective_resolution"] == 512
    assert result["texture_size"] == 2048 and result["polygon_target"] == 500000
    stats = result["stats"]
    assert stats["vertices"] == 8 and stats["faces"] == 12
    assert stats["materials"] == 1 and stats["textures"] == 1
    assert stats["has_uv"] is True
    assert stats["bbox_min"] == [-0.5, -0.5, -0.5] and stats["bbox_max"] == [0.5, 0.5, 0.5]
    assert stats["dimensions"] == [1.0, 1.0, 1.0]
    assert stats["file_size_bytes"] > 0
    assert stats["texture_size"] == 64
    assert {"preprocessing", "shape", "finalizing", "total"} <= set(result["timings"])

    assert client.get(f"/api/v1/jobs/{job['id']}/result").json() == result

    glb = client.get(result["model_url"])
    assert glb.status_code == 200
    assert glb.headers["content-type"] == "model/gltf-binary"
    assert glb.headers["content-disposition"].startswith("inline")
    assert glb.content[:4] == b"glTF"
    assert len(glb.content) == stats["file_size_bytes"]

    export = client.get(result["download_url"])
    assert export.headers["content-type"] == "model/gltf-binary"
    assert export.headers["content-disposition"] == f'attachment; filename="img3d_{job["id"]}.glb"'

    preview = client.get(result["preview_url"])
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"

    # The engine received normalized job-local PNG inputs, primary first, on the GPU worker thread.
    request = harness.engine.requests[0]
    assert [img.image_id for img in request.images] == [front["id"]]
    assert {img.image_id for img in request.all_references} == {front["id"], back["id"]}
    assert request.params.seed == 42 and request.params.resolution == 512
    assert all(thread.startswith("gpu-worker") for thread in harness.engine.generate_threads)
    inputs = harness.settings.runtime_dir / "jobs" / job["id"] / "inputs"
    assert sorted(p.name for p in inputs.iterdir()) == sorted([f"{front['id']}.png", f"{back['id']}.png"])
    with Image.open(inputs / f"{front['id']}.png") as img:
        assert img.format == "PNG" and img.mode == "RGBA"
    with Image.open(inputs / f"{back['id']}.png") as img:
        assert img.format == "PNG" and img.mode == "RGB" and img.size == (200, 300)

    persisted = json.loads((harness.settings.runtime_dir / "jobs" / job["id"] / "job.json").read_text("utf-8"))
    assert persisted["status"] == "completed"


def test_job_snapshot_is_isolated_from_later_set_edits(harness):
    client = harness.client
    ref_set, image = create_set_with_image(client)
    job = create_job(client, ref_set["id"]).json()
    assert client.delete(f"/api/v1/reference-sets/{ref_set['id']}").status_code == 204
    final = wait_for_job(client, job["id"], TERMINAL)
    assert final["status"] == "completed"
    assert final["references"][0]["image_id"] == image["id"]


def test_inputs_are_normalized_png(harness):
    client = harness.client
    ref_set = create_set(client)
    set_id = ref_set["id"]

    cmyk = BytesIO()
    Image.new("CMYK", (100, 100), (0, 128, 128, 0)).save(cmyk, format="JPEG")
    gray16 = BytesIO()
    Image.fromarray(np.full((80, 120), 40000, dtype=np.uint16)).save(gray16, format="PNG")
    palette = BytesIO()
    pal_img = Image.new("P", (90, 90), 1)
    pal_img.info["transparency"] = 0
    pal_img.save(palette, format="PNG", transparency=0)
    rotated = image_bytes("JPEG", (160, 80), exif_orientation=6)

    uploads = {
        "front": (cmyk.getvalue(), "image/jpeg"),
        "back": (gray16.getvalue(), "image/png"),
        "left": (palette.getvalue(), "image/png"),
        "right": (rotated, "image/jpeg"),
    }
    ids = {}
    for slot, (data, mime) in uploads.items():
        response = upload(client, set_id, data, slot=slot, content_type=mime)
        assert response.status_code == 201, response.text
        ids[slot] = response.json()["image"]["id"]

    job = create_job(client, set_id).json()
    inputs = harness.settings.runtime_dir / "jobs" / job["id"] / "inputs"
    with Image.open(inputs / f"{ids['front']}.png") as img:
        assert img.mode == "RGB"
    with Image.open(inputs / f"{ids['back']}.png") as img:
        assert img.mode == "RGB"
        value = img.getpixel((0, 0))[0]
        assert 150 <= value <= 160  # 40000 / 257 ~= 156 (scaled, not clipped to 255)
    with Image.open(inputs / f"{ids['left']}.png") as img:
        assert img.mode == "RGBA"
    with Image.open(inputs / f"{ids['right']}.png") as img:
        assert img.size == (80, 160)  # EXIF orientation applied
    wait_for_job(client, job["id"], TERMINAL)


def test_seed_randomization_and_primary_override(harness):
    client = harness.client
    ref_set = create_set(client)
    front = upload(client, ref_set["id"], png_bytes(), slot="front").json()["image"]
    detail = upload(client, ref_set["id"], png_bytes(), slot="detail").json()["image"]

    randomized = create_job(client, ref_set["id"], {"seed": 5, "randomize_seed": True}).json()
    assert 0 <= randomized["seed"] < 2**31
    fixed = create_job(client, ref_set["id"], {"seed": 123, "randomize_seed": False}, primary_image_id=detail["id"])
    fixed = fixed.json()
    assert fixed["seed"] == 123
    assert fixed["primary_image_id"] == detail["id"]
    assert {r["image_id"]: r["is_primary"] for r in fixed["references"]} == {front["id"]: False, detail["id"]: True}

    done = wait_for_job(client, fixed["id"], TERMINAL)
    assert done["result"]["seed"] == 123
    assert harness.engine.requests[-1].images[0].image_id == detail["id"]
    wait_for_job(client, randomized["id"], TERMINAL)


def test_create_job_validation_errors(harness):
    client = harness.client
    assert_error(create_job(client, "rs_000000000000"), 404, "not_found")
    empty = create_set(client)
    assert_error(create_job(client, empty["id"]), 422, "validation_error")
    ref_set, _ = create_set_with_image(client)
    assert_error(
        create_job(client, ref_set["id"], primary_image_id="img_000000000000"), 422, "validation_error"
    )
    bad_settings = [
        {"resolution": 768},
        {"texture_size": 3000},
        {"polygon_target": 100},
        {"polygon_target": 3_000_000},
        {"reference_mode": "multi_view"},
        {"reference_mode": "nope"},
        {"seed": -1},
        {"seed": 2**31},
        {"unknown": 1},
        {"advanced": {"shape": {"steps": 0}}},
        {"advanced": {"shape": {"steps": 51}}},
        {"advanced": {"texture": {"guidance_strength": 11}}},
        {"advanced": {"sparse_structure": {"guidance_rescale": 1.5}}},
        {"advanced": {"sparse_structure": {"rescale_t": 0.5}}},
        {"advanced": {"bogus": {}}},
    ]
    for settings in bad_settings:
        response = create_job(client, ref_set["id"], settings)
        error = assert_error(response, 422, "validation_error")
        assert error["details"], settings
    assert_error(client.post("/api/v1/jobs", json={"reference_set_id": ref_set["id"], "x": 1}), 422, "validation_error")


def test_settings_validated_against_engine_capabilities(make_app):
    caps = EngineCapabilities(resolutions=(512,), texture_sizes=(1024,), polygon_target=PolygonTargetRange(1000, 5000, 2000))
    h = make_app(engine=FakeEngine(caps=caps))
    ref_set, _ = create_set_with_image(h.client)
    error = assert_error(create_job(h.client, ref_set["id"], {"resolution": 1024}), 422, "validation_error")
    fields = {item["loc"][-1] for item in error["details"]}
    assert {"resolution", "texture_size", "polygon_target"} <= fields
    ok = create_job(h.client, ref_set["id"], {"resolution": 512, "texture_size": 1024, "polygon_target": 2000})
    assert ok.status_code == 202, ok.text
    wait_for_job(h.client, ok.json()["id"], TERMINAL)


def test_multi_view_requires_capable_engine(make_app):
    from models.enums import ReferenceMode

    caps = EngineCapabilities(max_reference_images=4, reference_modes=(ReferenceMode.PRIMARY_ONLY, ReferenceMode.MULTI_VIEW))
    h = make_app(engine=FakeEngine(caps=caps))
    ref_set = create_set(h.client)
    upload(h.client, ref_set["id"], png_bytes(), slot="back")
    upload(h.client, ref_set["id"], png_bytes(), slot="front", make_primary=True)
    job = create_job(h.client, ref_set["id"], {"reference_mode": "multi_view"}).json()
    wait_for_job(h.client, job["id"], {"completed"})
    request = h.engine.requests[0]
    assert [img.slot for img in request.images] == ["front", "back"]


def test_cancel_queued_job(harness):
    client, engine = harness.client, harness.engine
    engine.hold()
    ref_set, _ = create_set_with_image(client)
    first = create_job(client, ref_set["id"]).json()
    wait_for_job(client, first["id"], {"running"})
    second = create_job(client, ref_set["id"]).json()
    assert second["status"] == "queued" and second["queue_position"] == 1
    system = client.get("/api/v1/system").json()
    assert system["queue"] == {"pending": 1, "running_job_id": first["id"], "max_size": 16}

    cancelled = client.post(f"/api/v1/jobs/{second['id']}/cancel")
    assert cancelled.status_code == 200
    body = cancelled.json()
    assert body["status"] == "cancelled" and body["finished_at"] and body["queue_position"] is None
    assert_error(client.post(f"/api/v1/jobs/{second['id']}/cancel"), 409, "conflict")

    engine.release()
    assert wait_for_job(client, first["id"], TERMINAL)["status"] == "completed"
    assert len(engine.requests) == 1  # the cancelled job never reached the engine


def test_cancel_running_job(harness):
    client, engine = harness.client, harness.engine
    engine.hold()
    ref_set, _ = create_set_with_image(client)
    job = create_job(client, ref_set["id"]).json()
    wait_for(lambda: engine.generation_started.is_set())
    running = wait_for_job(client, job["id"], {"running"})
    assert running["started_at"]

    response = client.post(f"/api/v1/jobs/{job['id']}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] in {"running", "cancelled"}
    final = wait_for_job(client, job["id"], TERMINAL)
    assert final["status"] == "cancelled"
    assert final["error"] is None and final["result"] is None
    assert_error(client.get(f"/api/v1/jobs/{job['id']}/result"), 409, "conflict")
    # The worker is free again.
    engine.release()
    nxt = create_job(client, ref_set["id"]).json()
    assert wait_for_job(client, nxt["id"], TERMINAL)["status"] == "completed"


def test_queue_full_returns_429(make_app):
    h = make_app(max_queue_size=1)
    h.engine.hold()
    ref_set, _ = create_set_with_image(h.client)
    first = create_job(h.client, ref_set["id"]).json()
    wait_for_job(h.client, first["id"], {"running"})
    second = create_job(h.client, ref_set["id"])
    assert second.status_code == 202
    error = assert_error(create_job(h.client, ref_set["id"]), 429, "queue_full")
    assert error["details"] == {"max_size": 1}
    # Nothing is left behind for the rejected job.
    job_dirs = list((h.settings.runtime_dir / "jobs").iterdir())
    assert len(job_dirs) == 2
    h.engine.release()
    wait_for_job(h.client, second.json()["id"], TERMINAL)


def test_delete_job(harness):
    client, engine = harness.client, harness.engine
    engine.hold()
    ref_set, _ = create_set_with_image(client)
    job = create_job(client, ref_set["id"]).json()
    wait_for_job(client, job["id"], {"running"})
    assert_error(client.delete(f"/api/v1/jobs/{job['id']}"), 409, "conflict")
    engine.release()
    wait_for_job(client, job["id"], TERMINAL)
    job_dir = harness.settings.runtime_dir / "jobs" / job["id"]
    assert job_dir.exists()
    assert client.delete(f"/api/v1/jobs/{job['id']}").status_code == 204
    assert not job_dir.exists()
    assert_error(client.get(f"/api/v1/jobs/{job['id']}"), 404, "not_found")


def test_result_endpoints_before_completion(harness):
    client, engine = harness.client, harness.engine
    engine.hold()
    ref_set, _ = create_set_with_image(client)
    job = create_job(client, ref_set["id"]).json()
    assert_error(client.get(f"/api/v1/jobs/{job['id']}/result"), 409, "conflict")
    assert_error(client.get(f"/api/v1/jobs/{job['id']}/model.glb"), 409, "conflict")
    assert_error(client.get(f"/api/v1/jobs/{job['id']}/export/glb"), 409, "conflict")
    # The preview exists once the engine produced it (during preprocessing), even while running.
    wait_for(lambda: client.get(f"/api/v1/jobs/{job['id']}/preview").status_code == 200)
    engine.release()
    wait_for_job(client, job["id"], TERMINAL)


def test_preview_404_until_produced(harness):
    client, engine = harness.client, harness.engine
    engine.hold()
    ref_set, _ = create_set_with_image(client)
    first = create_job(client, ref_set["id"]).json()
    queued = create_job(client, ref_set["id"]).json()
    assert_error(client.get(f"/api/v1/jobs/{queued['id']}/preview"), 404, "not_found")
    engine.release()
    wait_for_job(client, queued["id"], TERMINAL)
    wait_for_job(client, first["id"], TERMINAL)


@pytest.mark.parametrize(
    ("exc", "code", "fragment"),
    [
        (RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB"), "out_of_memory", "out of memory"),
        (EngineError("sampler exploded", details={"stage": "shape"}), "engine_error", "sampler exploded"),
        (ValueError("bad tensor shape"), "engine_error", "ValueError: bad tensor shape"),
    ],
)
def test_engine_failures_surface_on_the_job(make_app, exc, code, fragment):
    h = make_app(engine=FakeEngine(fail_with=exc))
    ref_set, _ = create_set_with_image(h.client)
    job = create_job(h.client, ref_set["id"]).json()
    final = wait_for_job(h.client, job["id"], TERMINAL)
    assert final["status"] == "failed"
    assert set(final["error"]) == {"code", "message", "details"}
    assert final["error"]["code"] == code
    assert fragment in final["error"]["message"]
    assert final["result"] is None
    # The worker survives and processes the next job.
    h.engine.fail_with = None
    nxt = create_job(h.client, ref_set["id"]).json()
    assert wait_for_job(h.client, nxt["id"], TERMINAL)["status"] == "completed"


def test_job_waits_for_model_then_runs(make_app):
    engine = FakeEngine(ready=False, load_delay=0.3)
    h = make_app(engine=engine)
    ref_set, _ = create_set_with_image(h.client)
    with h.client.websocket_connect("/api/v1/ws") as ws:
        ws_receive(ws)
        job = create_job(h.client, ref_set["id"]).json()
        messages = ws_collect_until(
            ws, lambda m: m["type"] == "job.updated" and m["data"]["status"] in TERMINAL
        )
        stages = [m["data"]["progress"]["stage"] for m in messages if m["type"] == "job.updated"]
        assert "waiting_for_model" in stages
        assert messages[-1]["data"]["status"] == "completed"
        engine_states = [m["data"]["engine"]["state"] for m in messages if m["type"] == "system.updated"]
        assert "loading" in engine_states
        if "ready" not in engine_states:  # the debounced broadcast may trail the job events slightly
            ws_collect_until(ws, lambda m: m["type"] == "system.updated" and m["data"]["engine"]["state"] == "ready")
    assert engine.load_calls == 1
    assert h.client.get(f"/api/v1/jobs/{job['id']}").json()["status"] == "completed"


def test_cancel_while_waiting_for_model(make_app):
    engine = FakeEngine(ready=False, load_delay=3.0)
    h = make_app(engine=engine)
    ref_set, _ = create_set_with_image(h.client)
    job = create_job(h.client, ref_set["id"]).json()
    wait_for(lambda: h.client.get(f"/api/v1/jobs/{job['id']}").json()["progress"]["stage"] == "waiting_for_model")
    assert h.client.post(f"/api/v1/jobs/{job['id']}/cancel").status_code == 200
    final = wait_for_job(h.client, job["id"], TERMINAL, timeout=2.0)  # well before the 3 s load finishes
    assert final["status"] == "cancelled"
    assert engine.requests == []


def test_job_fails_when_model_cannot_load(make_app):
    h = make_app(engine=FakeEngine(ready=False, load_error="weights missing"))
    ref_set, _ = create_set_with_image(h.client)
    job = create_job(h.client, ref_set["id"]).json()
    final = wait_for_job(h.client, job["id"], TERMINAL)
    assert final["status"] == "failed"
    assert final["error"]["code"] == "engine_unavailable"
    assert "weights missing" in final["error"]["message"]


def test_job_fails_with_unavailable_engine(make_app):
    h = make_app(engine=UnavailableEngine("trellis2", "ImportError: DLL load failed"))
    ref_set, _ = create_set_with_image(h.client)
    job = create_job(h.client, ref_set["id"]).json()
    assert job["engine"] == "trellis2"
    final = wait_for_job(h.client, job["id"], TERMINAL)
    assert final["status"] == "failed"
    assert final["error"]["code"] == "engine_unavailable"
    assert "DLL load failed" in final["error"]["message"]


def test_list_jobs(harness):
    client = harness.client
    ref_set, _ = create_set_with_image(client)
    ids = [create_job(client, ref_set["id"]).json()["id"] for _ in range(3)]
    for job_id in ids:
        wait_for_job(client, job_id, TERMINAL)
    items = client.get("/api/v1/jobs").json()["items"]
    assert [j["id"] for j in items] == list(reversed(ids))
    assert [j["id"] for j in client.get("/api/v1/jobs?limit=2").json()["items"]] == list(reversed(ids))[:2]
    assert len(client.get("/api/v1/jobs?limit=20&status=").json()["items"]) == 3
    assert len(client.get("/api/v1/jobs?status=completed").json()["items"]) == 3
    assert client.get("/api/v1/jobs?status=failed").json()["items"] == []
    assert_error(client.get("/api/v1/jobs?status=bogus"), 422, "validation_error")
    assert_error(client.get("/api/v1/jobs?limit=0"), 422, "validation_error")


def test_progress_events_are_throttled(make_app):
    engine = FakeEngine(steps=40, step_delay=0.005)  # 120 steps in ~0.6 s+
    h = make_app(engine=engine)
    ref_set, _ = create_set_with_image(h.client)
    with h.client.websocket_connect("/api/v1/ws") as ws:
        ws_receive(ws)
        job = create_job(h.client, ref_set["id"]).json()
        messages = ws_collect_until(
            ws, lambda m: m["type"] == "job.updated" and m["data"]["status"] in TERMINAL
        )
    updates = [m for m in messages if m["type"] == "job.updated" and m["data"]["id"] == job["id"]]
    final = h.client.get(f"/api/v1/jobs/{job['id']}").json()
    elapsed = final["progress"]["elapsed_seconds"] or 1.0
    running = [m for m in updates if m["data"]["status"] == "running"]
    # <= 5 progress events per second (+ a few unthrottled status/stage transitions).
    assert len(running) <= elapsed * 5 + 6, (len(running), elapsed)
    assert updates[-1]["data"]["status"] == "completed"


class NonFiniteEngine(FakeEngine):
    """Engine double reporting NaN/Infinity in timings, metadata, status details and device info."""

    def generate(self, request, reporter, cancel):
        output = super().generate(request, reporter, cancel)
        output.timings["shape"] = float("nan")
        output.timings["texture"] = float("inf")
        output.metadata["score"] = float("nan")
        return output


def test_non_finite_engine_values_never_break_json_responses(make_app):
    """Starlette serializes with allow_nan=False: one NaN from an engine must not turn responses into 500s."""
    device = {"cuda_available": True, "name": "Fake GPU", "vram_total_mb": float("inf"), "vram_used_mb": float("nan")}
    h = make_app(engine=NonFiniteEngine(device=device))
    ref_set, _ = create_set_with_image(h.client)
    job = create_job(h.client, ref_set["id"]).json()
    final = wait_for_job(h.client, job["id"], TERMINAL)
    assert final["status"] == "completed", final["error"]
    timings = final["result"]["timings"]
    assert timings["shape"] == 0.0 and timings["texture"] == 0.0
    for url in (f"/api/v1/jobs/{job['id']}", f"/api/v1/jobs/{job['id']}/result", "/api/v1/jobs", "/api/v1/system"):
        assert h.client.get(url).status_code == 200, url
    system = h.client.get("/api/v1/system").json()
    assert system["device"]["vram_total_mb"] is None and system["device"]["vram_used_mb"] is None
