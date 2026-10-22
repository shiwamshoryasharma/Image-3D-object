"""The seams of the part-aware layer into working code (part-aware DESIGN.md section 1.2).

* S-a ``GenerationWorker.submit_gpu_task`` / ``accepting_gpu_tasks``: auxiliary callables run on the single
  ``gpu-worker`` thread, FIFO with image-to-3D jobs, never overlapping; refused once ``stop()`` began; shutdown with an
  in-flight auxiliary task completes within the timeout because the part-aware layer stops first.
* S-b container and lifecycle: the app starts with ``part_aware_enabled`` true and false, recovers interrupted runs,
  registers the job-delete hook.
* S-c API wiring: the new routers are mounted and empty (404), the five event types exist, ``part_analysis.updated``
  is job-scoped on the WebSocket, the section 6.8 errors use the existing envelope.
* S-d settings: the section 8.1 fields, their defaults and env parsing; ``safe_summary()`` is unchanged.
"""

from __future__ import annotations

import asyncio
import functools
import json
import threading
import time
from typing import Any

import pytest
from fakes import FakeEngine
from fakes_runs import FakeRegistry, PhaseScript, ProbedEngine, ScriptedExecutor
from helpers import assert_error, create_job, create_set_with_image, wait_for_job, ws_collect_until, ws_receive
from starlette.testclient import TestClient

from config.settings import BACKEND_DIR, Settings
from core.events import Event
from core.exceptions import EngineUnavailableError
from core.feature_errors import (
    CapabilityUnavailableError,
    FeatureDisabledError,
    InsufficientVramError,
    InvalidWorkflowError,
    JobDeletedError,
    ModelUnavailableError,
    ServiceNotImplementedError,
    feature_enabled,
    require_feature,
)
from core.part_aware_container import PartProviderOverrides
from main import create_app
from models.enums import JobStage, JobStatus
from models.image_generation import ImageGenerationRun
from models.job import Job, JobProgress
from models.part_analysis import PartAnalysisRun
from schemas.events import EventType
from utils.timeutil import utcnow
from workers.generation_worker import GPU_THREAD_NAME

TERMINAL = {"completed", "failed", "cancelled"}

# Routers of slices that have not started yet (S10 part analyses, S11 workflows). S2/S4/S5 routes are covered by
# their own API tests.
EMPTY_ROUTER_PATHS = [
    ("GET", "/api/v1/jobs/job_0123456789ab/part-analyses"),
    ("GET", "/api/v1/jobs/job_0123456789ab/part-analyses/latest"),
    ("GET", "/api/v1/part-analyses/pa_0123456789ab"),
    ("GET", "/api/v1/part-analyses/pa_0123456789ab/manifest"),
    # The /api/v1/workflows* routes left this list when S11 filled them (list, catalog and validate answer 200/422).
]


# ==================================================================================================
# S-a GPU seam
# ==================================================================================================


def test_submit_gpu_task_runs_on_the_gpu_worker_thread(harness):
    worker = harness.container.worker
    assert worker.accepting_gpu_tasks is True

    async def scenario() -> tuple[str, int]:
        return await worker.submit_gpu_task(lambda a, b: (threading.current_thread().name, a + b), 2, 3)

    thread, total = harness.client.portal.call(scenario)
    assert thread.startswith(GPU_THREAD_NAME)
    assert total == 5

    def fail() -> None:
        raise ValueError("aux failure")

    async def failing() -> str:
        try:
            await worker.submit_gpu_task(fail)
        except ValueError as exc:
            return str(exc)
        return "no error"

    assert harness.client.portal.call(failing) == "aux failure"


def test_gpu_tasks_interleave_fifo_with_jobs_and_never_overlap(make_app):
    engine = ProbedEngine()
    h = make_app(engine=engine)
    worker = h.container.worker
    probe = engine.probe
    futures: list[Any] = []

    def submit(label: str) -> None:  # runs on the event loop (submit_gpu_task wraps the future for that loop)
        futures.append(worker.submit_gpu_task(probe.task(label)))

    ref_set, _ = create_set_with_image(h.client)
    engine.hold()
    first = create_job(h.client, ref_set["id"]).json()
    assert engine.generation_started.wait(5)
    h.client.portal.call(submit, "aux:A")  # queued behind the running job on the single GPU thread
    assert worker.is_busy
    second = create_job(h.client, ref_set["id"]).json()
    h.client.portal.call(submit, "aux:B")
    engine.release()

    for job in (first, second):
        assert wait_for_job(h.client, job["id"], TERMINAL)["status"] == "completed"
    aux_threads = h.client.portal.call(functools.partial(_gather, futures))
    assert all(name.startswith(GPU_THREAD_NAME) for name in aux_threads)
    assert probe.snapshot() == [f"job:{first['id']}", "aux:A", "aux:B", f"job:{second['id']}"]
    assert probe.max_active == 1, "auxiliary tasks and jobs never overlap on the GPU"
    assert all(name.startswith(GPU_THREAD_NAME) for name in probe.threads)


async def _gather(futures: list[Any]) -> list[Any]:
    return list(await asyncio.gather(*futures))


def test_submit_gpu_task_is_refused_once_stop_began(harness):
    worker = harness.container.worker
    harness.client.portal.call(functools.partial(worker.stop, timeout=1.0))
    assert worker.accepting_gpu_tasks is False
    with pytest.raises(EngineUnavailableError) as exc_info:
        worker.submit_gpu_task(lambda: None)
    assert exc_info.value.code == "engine_unavailable"
    assert exc_info.value.status_code == 503
    assert "shutting down" in exc_info.value.message


def test_shutdown_with_an_inflight_aux_task_stops_the_part_aware_layer_first(make_settings):
    started = threading.Event()
    marks: dict[str, float] = {}

    def gpu_work(_run: Any, _state: Any, ctx: Any) -> None:
        started.set()
        while not ctx.cancel.is_cancelled:
            time.sleep(0.01)
        time.sleep(0.2)  # e.g. offloading weights after noticing the cancel
        marks["aux_returned"] = time.monotonic()
        ctx.cancel.raise_if_cancelled()

    executor = ScriptedExecutor("image_generation", [PhaseScript("gen", "gpu", ("gen",), gpu_work)])
    settings = make_settings(shutdown_timeout_s=5)
    engine = FakeEngine()
    app = create_app(
        settings,
        engine=engine,
        part_providers=PartProviderOverrides(registry=FakeRegistry(), executors={"image_generation": executor}),
    )
    run = ImageGenerationRun(id="ig_0123456789ab", created_at=utcnow(), options={"prompt": "test"})
    with TestClient(app) as client:
        container = app.state.container
        part_aware = container.part_aware
        original_stop = container.worker.stop

        async def recording_stop(timeout: float) -> None:
            marks["worker_stop"] = time.monotonic()
            await original_stop(timeout)

        container.worker.stop = recording_stop  # type: ignore[method-assign]
        client.portal.call(part_aware.run_worker.enqueue, run)
        assert started.wait(5)
        t0 = time.monotonic()
    elapsed = time.monotonic() - t0

    assert elapsed < settings.shutdown_timeout_s
    assert marks["aux_returned"] <= marks["worker_stop"], "the auxiliary GPU task returned before worker.stop()"
    assert engine.unload_calls == 1, "the GPU thread was free, so the engine was unloaded at shutdown"
    final = part_aware.stores["image_generation"].get(run.id)
    assert final.status is JobStatus.FAILED and final.error.code == "interrupted"


# ==================================================================================================
# S-b container and lifecycle
# ==================================================================================================


@pytest.mark.parametrize("enabled", [True, False], ids=["part-aware-on", "part-aware-off"])
def test_app_starts_with_part_aware_enabled_and_disabled(make_app, runtime_dir, enabled):
    h = make_app(part_aware_enabled=enabled)
    part_aware = h.container.part_aware
    assert part_aware is not None and part_aware.started
    assert part_aware.enabled is enabled
    assert part_aware.run_worker.started is enabled
    assert (runtime_dir / "imagegen").is_dir() is enabled, "a disabled layer writes nothing"
    assert h.client.get("/api/v1/health").status_code == 200
    assert h.client.get("/api/v1/system").status_code == 200
    assert h.client.get("/api/v1/jobs").status_code == 200

    for method, path in EMPTY_ROUTER_PATHS:
        response = h.client.request(method, path, json={} if method == "POST" else None)
        assert_error(response, 404, "not_found")


@pytest.mark.parametrize("enabled", [True, False], ids=["part-aware-on", "part-aware-off"])
def test_startup_marks_interrupted_runs_failed(make_app, runtime_dir, enabled):
    ig = ImageGenerationRun(id="ig_00000000000a", created_at=utcnow(), status=JobStatus.RUNNING, started_at=utcnow())
    ig_dir = runtime_dir / "imagegen" / ig.id
    ig_dir.mkdir(parents=True)
    (ig_dir / "run.json").write_text(json.dumps(ig.model_dump(mode="json")), encoding="utf-8")

    job = Job(
        id="job_00000000000a",
        status=JobStatus.COMPLETED,
        engine="fake-engine",
        created_at=utcnow(),
        finished_at=utcnow(),
        reference_set_id="rs_00000000000a",
        primary_image_id="img_00000000000a",
        seed=1,
        settings={"resolution": 1024},
        progress=JobProgress.for_stage(JobStage.DONE),
    )
    job_dir = runtime_dir / "jobs" / job.id
    job_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps(job.model_dump(mode="json")), encoding="utf-8")
    pa = PartAnalysisRun(id="pa_00000000000a", job_id=job.id, created_at=utcnow())
    pa_dir = job_dir / "parts" / pa.id
    pa_dir.mkdir(parents=True)
    (pa_dir / "run.json").write_text(json.dumps(pa.model_dump(mode="json")), encoding="utf-8")

    h = make_app(part_aware_enabled=enabled)
    stores = h.container.part_aware.stores
    for store, run_id, path in (
        (stores["image_generation"], ig.id, ig_dir / "run.json"),
        (stores["part_analysis"], pa.id, pa_dir / "run.json"),
    ):
        recovered = store.get(run_id)
        assert recovered.status is JobStatus.FAILED and recovered.error.code == "interrupted"
        on_disk = json.loads(path.read_text("utf-8"))
        assert on_disk["status"] == "failed" and on_disk["error"]["code"] == "interrupted"
    assert h.client.get(f"/api/v1/jobs/{job.id}").status_code == 200


def test_deleting_a_job_forgets_its_part_analyses_and_purges_their_export_cache(harness, runtime_dir):
    client = harness.client
    ref_set, _ = create_set_with_image(client)
    job = create_job(client, ref_set["id"]).json()
    wait_for_job(client, job["id"], {"completed"})
    part_aware = harness.container.part_aware
    store = part_aware.stores["part_analysis"]
    run = PartAnalysisRun(id="pa_00000000000b", job_id=job["id"], created_at=utcnow(), status=JobStatus.COMPLETED)
    assert store.save(run)
    cache = part_aware.layout.part_exports_dir(run.id)
    cache.mkdir(parents=True)
    (cache / "part_hair.glb").write_bytes(b"glb")

    assert client.delete(f"/api/v1/jobs/{job['id']}").status_code == 204
    assert store.get(run.id) is None and run.id not in store.ids()
    assert not cache.exists()
    assert not (runtime_dir / "jobs" / job["id"]).exists()


# ==================================================================================================
# S-c API wiring
# ==================================================================================================


def test_event_types_of_the_part_aware_layer():
    assert str(EventType.FEATURES_UPDATED) == "features.updated"
    assert str(EventType.IMAGE_GENERATION_UPDATED) == "image_generation.updated"
    assert str(EventType.SEGMENTATION_UPDATED) == "segmentation.updated"
    assert str(EventType.PART_ANALYSIS_UPDATED) == "part_analysis.updated"
    assert str(EventType.WORKFLOW_UPDATED) == "workflow.updated"
    existing = {"job.updated", "system.updated", "blender.updated", "tuning.updated"}
    assert existing <= {str(member) for member in EventType}, "existing event types are unchanged"


def test_part_analysis_updated_is_job_scoped_on_the_websocket(harness):
    client = harness.client
    part_aware = harness.container.part_aware
    bus = harness.container.bus
    mine, other = "job_00000000000c", "job_00000000000d"
    with client.websocket_connect(f"/api/v1/ws?job_id={mine}") as ws:
        ws_receive(ws)  # hello
        publisher = part_aware.publishers["part_analysis"]
        publisher.publish(PartAnalysisRun(id="pa_0000000000d1", job_id=other, created_at=utcnow()))
        bus.publish_threadsafe(Event(type="segmentation.updated", data={"id": "sg_0000000000d1"}, job_id=other))
        publisher.publish(PartAnalysisRun(id="pa_0000000000c1", job_id=mine, created_at=utcnow()))
        messages = ws_collect_until(ws, lambda m: m["type"] == "part_analysis.updated", timeout=5)

    by_type = [(m["type"], m["data"].get("id")) for m in messages if m["type"] != "ping"]
    assert ("segmentation.updated", "sg_0000000000d1") in by_type, "segmentation events are not job-scoped"
    assert ("part_analysis.updated", "pa_0000000000d1") not in by_type
    final = messages[-1]
    assert final["data"]["id"] == "pa_0000000000c1" and final["data"]["job_id"] == mine
    assert final["data"]["kind"] == "part_analysis" and final["data"]["status"] == "queued"


def test_part_aware_errors_use_the_existing_error_envelope(harness):
    errors: dict[str, Exception] = {
        "disabled": FeatureDisabledError(
            "Auto Mesh Tuner is paused until approved", feature="mesh_tuning", flag="IMG3D_MESH_TUNING_ENABLED"
        ),
        "capability": CapabilityUnavailableError(
            "No usable segmentation model",
            capability="segmentation",
            reasons=[
                {
                    "model_id": "grounding_dino_base",
                    "state": "unavailable",
                    "reason_code": "not_approved",
                    "reason": "Not approved",
                }
            ],
        ),
        "model": ModelUnavailableError(
            "Model not usable", model_id="z_image_turbo", state="not_installed", reason_code="not_installed"
        ),
        "workflow": InvalidWorkflowError("Invalid graph", issues=[{"code": "cycle", "node_id": "n1"}]),
        "stub": ServiceNotImplementedError("part_analysis", operation="Part analysis"),
        "vram": InsufficientVramError("Not enough VRAM", model_ids=["z_image_turbo"], free_mb=1000, required_mb=26112),
        "job": JobDeletedError(job_id="job_0123456789ab"),
    }

    async def raise_error(name: str) -> None:
        raise errors[name]

    app = harness.app
    app.add_api_route("/__s1_feature_errors/{name}", raise_error, methods=["GET"])
    app.router.routes.insert(0, app.router.routes.pop())  # ahead of any catch-all mount

    def body(name: str, status: int, code: str) -> dict[str, Any]:
        response = harness.client.get(f"/__s1_feature_errors/{name}")
        payload = assert_error(response, status, code)
        assert set(response.json()["error"]) == {"code", "message", "details", "request_id"}
        return payload

    assert body("disabled", 503, "feature_disabled")["details"] == {
        "feature": "mesh_tuning",
        "flag": "IMG3D_MESH_TUNING_ENABLED",
    }
    capability = body("capability", 503, "capability_unavailable")["details"]
    assert capability["capability"] == "segmentation" and capability["reasons"][0]["reason_code"] == "not_approved"
    assert body("model", 503, "model_unavailable")["details"] == {
        "model_id": "z_image_turbo",
        "state": "not_installed",
        "reason_code": "not_installed",
    }
    assert body("workflow", 422, "invalid_workflow")["details"] == {"issues": [{"code": "cycle", "node_id": "n1"}]}
    stub = body("stub", 503, "capability_unavailable")["details"]
    assert stub["capability"] == "part_analysis" and stub["reasons"][0]["reason_code"] == "not_implemented"
    assert body("vram", 503, "insufficient_vram")["details"]["model_ids"] == ["z_image_turbo"]
    assert body("job", 409, "job_deleted")["details"] == {"job_id": "job_0123456789ab"}


def test_require_feature_names_the_flag_that_is_off(make_settings):
    settings = make_settings(mesh_tuning_enabled=False)
    for feature in ("part_aware", "image_generation", "segmentation", "part_analysis", "workflows"):
        assert feature_enabled(settings, feature)
        require_feature(settings, feature)
    assert not feature_enabled(settings, "mesh_tuning")
    with pytest.raises(FeatureDisabledError) as exc_info:
        require_feature(settings, "mesh_tuning")
    assert exc_info.value.details == {"feature": "mesh_tuning", "flag": "IMG3D_MESH_TUNING_ENABLED"}

    master_off = make_settings(part_aware_enabled=False)
    assert not feature_enabled(master_off, "segmentation")
    with pytest.raises(FeatureDisabledError) as exc_info:
        require_feature(master_off, "segmentation")
    assert exc_info.value.details == {"feature": "part_aware", "flag": "IMG3D_PART_AWARE_ENABLED"}

    segmentation_off = make_settings(segmentation_enabled=False)
    with pytest.raises(FeatureDisabledError) as exc_info:
        require_feature(segmentation_off, "segmentation")
    assert exc_info.value.details == {"feature": "segmentation", "flag": "IMG3D_SEGMENTATION_ENABLED"}
    with pytest.raises(KeyError):
        require_feature(settings, "tuning")  # type: ignore[arg-type]


# ==================================================================================================
# S-d settings
# ==================================================================================================


def test_part_aware_settings_defaults(make_settings, monkeypatch):
    monkeypatch.delenv("IMG3D_MESH_TUNING_ENABLED", raising=False)
    settings = make_settings()
    expected: dict[str, Any] = {
        "part_aware_enabled": True,
        "image_generation_enabled": True,
        "segmentation_enabled": True,
        "part_analysis_enabled": True,
        "workflows_enabled": True,
        "mesh_tuning_enabled": False,
        "part_refinement_enabled": False,
        "approved_models": None,
        "model_paths": {},
        "allow_restricted_licenses": False,
        "t2i_default_model": None,
        "segmentation_default_profile": "auto",
        "run_queue_max_size": 8,
        "aux_keep_models_in_ram": True,
        "aux_model_idle_unload_s": 0,
        "aux_vram_budget_gb": 28.0,
        "engine_resident_vram_gb": 17.0,
        "aux_vram_margin_gb": 1.5,
        "prompt_max_chars": 2000,
        "parts_default_views": 8,
        "parts_min_part_faces": None,
        "parts_max_parts": 64,
        "run_max_defer_s": 600,
        "parts_export_cache_max_gb": 10,
        "part_analysis_timeout_s": 1800,
        "workflow_graph_max_bytes": 65536,
        "workflow_sweep_interval_s": 5.0,
        "imagegen_retention_days": 14,
        "segmentation_retention_days": 14,
        "workflow_retention_days": 30,
        "max_stored_image_generations": 200,
        "max_stored_segmentations": 200,
        "max_stored_workflows": 500,
        "persist_generation_intermediates": False,
    }
    assert {name: getattr(settings, name) for name in expected} == expected
    assert not set(expected) & set(settings.safe_summary()), "safe_summary() is unchanged"
    summary = settings.part_aware_summary()
    assert summary["part_aware_enabled"] is True and summary["approved_models"] is None

    inert = make_settings(persist_generation_intermediates=True)
    assert inert.part_aware_summary()["persist_generation_intermediates"] is False


@pytest.mark.parametrize(
    ("raw", "parsed"),
    [
        ("z_image_turbo,grounding_dino_base", ["z_image_turbo", "grounding_dino_base"]),
        ("a; b", ["a", "b"]),
        ('["sam2_1_hiera_large"]', ["sam2_1_hiera_large"]),
        ("[]", []),
        ("", None),
        ("   ", None),
    ],
)
def test_approved_models_env_parsing(monkeypatch, make_settings, raw, parsed):
    monkeypatch.setenv("IMG3D_APPROVED_MODELS", raw)
    assert make_settings().approved_models == parsed


def test_model_paths_env_is_a_json_map_resolved_against_the_backend(monkeypatch, make_settings):
    monkeypatch.setenv("IMG3D_MODEL_PATHS", '{"z_image_turbo": ".model/custom/zimage"}')
    settings = make_settings()
    assert settings.model_paths == {"z_image_turbo": (BACKEND_DIR / ".model" / "custom" / "zimage").resolve()}
    monkeypatch.setenv("IMG3D_MODEL_PATHS", "")
    assert make_settings().model_paths == {}


def test_settings_reject_out_of_range_part_aware_values(make_settings):
    out_of_range = (
        ("run_queue_max_size", 0),
        ("run_max_defer_s", 3601),
        ("parts_default_views", 25),
        ("prompt_max_chars", 8001),
    )
    for field, value in out_of_range:
        with pytest.raises(ValueError):
            make_settings(**{field: value})
    assert isinstance(make_settings(run_max_defer_s=0), Settings)  # 0 disables the jobs-first gate
