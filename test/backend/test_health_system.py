"""Health, system status and engine control."""

from __future__ import annotations

import threading
import time

from fakes import FakeEngine
from helpers import assert_error, create_job, create_set_with_image, wait_for, wait_for_job


def test_health_is_public_and_well_formed(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "Img3D"
    assert body["version"] == "1.0.0"
    assert isinstance(body["uptime_seconds"], float)
    assert body["time"].endswith("Z")
    assert response.headers["x-request-id"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_request_id_is_propagated(client):
    response = client.get("/api/v1/health", headers={"X-Request-ID": "my-req-123"})
    assert response.headers["x-request-id"] == "my-req-123"
    # Invalid ids are replaced.
    response = client.get("/api/v1/health", headers={"X-Request-ID": "bad id with spaces"})
    assert response.headers["x-request-id"] != "bad id with spaces"


def test_system_status_shape(client):
    response = client.get("/api/v1/system")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"app", "engine", "device", "queue", "limits", "defaults", "auth_required"}
    assert body["app"] == {"name": "Img3D", "version": "1.0.0", "environment": "development"}

    engine = body["engine"]
    assert engine["name"] == "fake-engine"
    assert engine["state"] == "ready"
    assert set(engine) >= {"name", "state", "message", "load_progress", "error", "loaded_at", "details", "capabilities"}
    caps = engine["capabilities"]
    assert caps["resolutions"] == [512, 1024, 1536]
    assert caps["texture_sizes"] == [512, 1024, 2048, 4096]
    assert caps["polygon_target"] == {"min": 5000, "max": 2000000, "default": 500000}
    assert caps["reference_modes"] == ["primary_only"]
    assert caps["max_reference_images"] == 1

    assert body["device"]["name"] == "Fake GPU"
    assert body["device"]["vram_total_mb"] == 32607
    assert body["queue"] == {"pending": 0, "running_job_id": None, "max_size": 16}
    assert body["limits"] == {
        "max_upload_mb": 25,
        "allowed_mime_types": ["image/png", "image/jpeg", "image/webp"],
        "max_detail_images": 8,
        "min_image_side": 64,
        "slots": ["front", "back", "left", "right", "face_front", "face_side", "detail"],
    }
    assert body["defaults"] == {
        "resolution": 1024,
        "seed": 0,
        "randomize_seed": True,
        "polygon_target": 500000,
        "texture_size": 2048,
        "remove_background": True,
        "reference_mode": "primary_only",
    }
    assert body["auth_required"] is False


def test_device_info_times_out_without_blocking(make_app):
    engine = FakeEngine(device_delay=2.0)
    h = make_app(engine=engine, device_info_timeout_s=0.2)
    started = time.monotonic()
    body = h.client.get("/api/v1/system").json()
    assert time.monotonic() - started < 1.5
    assert body["device"] is None  # nothing cached yet


def test_device_values_keep_integers_and_tolerate_odd_types(make_app):
    device = {"cuda_available": True, "name": "GPU", "compute_capability": (12, 0), "vram_total_mb": 32607}
    h = make_app(engine=FakeEngine(device=device))
    body = h.client.get("/api/v1/system").json()
    assert body["device"]["compute_capability"] == "12.0"  # (major, minor) -> contract format
    assert body["device"]["vram_total_mb"] == 32607 and isinstance(body["device"]["vram_total_mb"], int)


def test_device_null_when_engine_has_no_device(make_app):
    h = make_app(engine=FakeEngine(device=None))
    assert h.client.get("/api/v1/system").json()["device"] is None


def test_engine_load_and_unload(make_app):
    engine = FakeEngine(ready=False, load_delay=0.1)
    h = make_app(engine=engine)
    assert h.client.get("/api/v1/system").json()["engine"]["state"] == "not_loaded"

    response = h.client.post("/api/v1/system/engine/load")
    assert response.status_code == 202
    assert response.json()["engine"]["state"] in {"loading", "not_loaded", "ready"}
    wait_for(lambda: h.client.get("/api/v1/system").json()["engine"]["state"] == "ready")
    assert engine.load_calls == 1

    response = h.client.post("/api/v1/system/engine/unload")
    assert response.status_code == 200
    assert response.json()["engine"]["state"] == "not_loaded"
    assert engine.unload_calls >= 1


def test_preload_loads_engine_at_startup(make_app):
    engine = FakeEngine(ready=False)
    h = make_app(engine=engine, preload_model=True)
    wait_for(lambda: h.client.get("/api/v1/system").json()["engine"]["state"] == "ready")
    assert engine.load_calls == 1


def test_engine_load_error_is_reported(make_app):
    engine = FakeEngine(ready=False, load_error="CUDA exploded")
    h = make_app(engine=engine, preload_model=True)
    body = wait_for(
        lambda: (b := h.client.get("/api/v1/system").json())["engine"]["state"] == "error" and b
    )
    assert body["engine"]["error"] == "CUDA exploded"


def test_unavailable_engine_reports_reason(make_app):
    from inference.unavailable import UnavailableEngine

    h = make_app(engine=UnavailableEngine("trellis2", "ImportError: no torch"))
    body = h.client.get("/api/v1/system").json()
    assert body["engine"]["state"] == "unavailable"
    assert body["engine"]["error"] == "ImportError: no torch"
    assert body["device"] is None


def test_unload_conflict_while_job_running(make_app):
    from helpers import create_job, create_set_with_image, wait_for_job

    engine = FakeEngine()
    engine.hold()
    h = make_app(engine=engine)
    ref_set, _ = create_set_with_image(h.client)
    job = create_job(h.client, ref_set["id"]).json()
    wait_for_job(h.client, job["id"], {"running"})
    assert_error(h.client.post("/api/v1/system/engine/unload"), 409, "conflict")
    engine.release()
    wait_for_job(h.client, job["id"], {"completed"})


class SlowUnloadEngine(FakeEngine):
    """Fake engine whose ``unload()`` blocks until released (still reporting READY meanwhile, like a real engine
    that is freeing GPU memory)."""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.unload_started = threading.Event()
        self.unload_gate = threading.Event()

    def unload(self) -> None:
        self.unload_started.set()
        self.unload_gate.wait(10)
        super().unload()


def _unload_in_background(client) -> tuple[threading.Thread, dict]:
    result: dict = {}
    thread = threading.Thread(target=lambda: result.update(response=client.post("/api/v1/system/engine/unload")))
    thread.start()
    return thread, result


def test_job_created_during_unload_waits_for_the_model(make_app):
    """A job picked up while an unload is in flight must wait and reload the model, not fail."""
    engine = SlowUnloadEngine()
    h = make_app(engine=engine)
    ref_set, _ = create_set_with_image(h.client)
    thread, result = _unload_in_background(h.client)
    try:
        assert engine.unload_started.wait(5)
        job = create_job(h.client, ref_set["id"]).json()
        running = wait_for_job(h.client, job["id"], {"running"})
        assert running["progress"]["stage"] == "waiting_for_model"
    finally:
        engine.unload_gate.set()
        thread.join(15)
    assert result["response"].status_code == 200
    final = wait_for_job(h.client, job["id"], {"completed", "failed", "cancelled"})
    assert final["status"] == "completed", final["error"]
    assert engine.load_calls == 1
    assert h.client.get("/api/v1/system").json()["engine"]["state"] == "ready"


def test_load_requested_during_unload_is_honoured(make_app):
    engine = SlowUnloadEngine()
    h = make_app(engine=engine)
    thread, result = _unload_in_background(h.client)
    try:
        assert engine.unload_started.wait(5)
        assert h.client.post("/api/v1/system/engine/load").status_code == 202
    finally:
        engine.unload_gate.set()
        thread.join(15)
    assert result["response"].status_code == 200
    wait_for(lambda: h.client.get("/api/v1/system").json()["engine"]["state"] == "ready")
    assert engine.unload_calls == 1 and engine.load_calls == 1
