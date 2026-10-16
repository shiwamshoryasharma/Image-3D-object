"""Auto Mesh Tuner runs (DESIGN.md section 6): lifecycle, failures, cancel, limits, deletion, recovery, files, WS.

Most tests use :class:`fakes_blender.FakeTunerRunner` (protocol events + real output files, no Blender).
``test_real_blender_plumbing`` (marker ``blender``) runs ``blender_side/fake_tuner.py`` through the REAL launcher
runner + tuning worker with the installed Blender 5.2.2 and checks that cancel kills the Blender process.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fakes import write_test_glb
from fakes_blender import FakeTunerRunner, TunerHarness, start_tuner_app
from helpers import (
    assert_error,
    create_job,
    create_set_with_image,
    wait_for,
    wait_for_job,
    ws_collect_until,
    ws_receive,
)

from config.settings import Settings
from models.enums import JobStage, JobStatus
from models.job import Job, JobProgress, JobResultData, MeshStats
from models.tuning import TuningRun
from storage.tuning_store import TuningStore
from utils.timeutil import utcnow

TERMINAL = {"completed", "failed", "cancelled"}
BLENDER_52 = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
FAKE_TUNER = Path(__file__).resolve().parent / "blender_side" / "fake_tuner.py"
STEP_IDS = ["load", "analyze", "geometry_repair", "face_refinement", "topology_tuning", "rig_optimization",
            "validation", "export"]
NEEDS_DOWNLOAD_42 = (
    "Blender 4.2.17 is installed but Auto Mesh Tuner requires Blender 5.1 or newer. "
    "Download a supported portable Blender?"
)
RUN_KEYS = {"id", "job_id", "status", "created_at", "started_at", "finished_at", "options", "progress", "steps",
            "metrics", "warnings", "validation", "blender", "result", "error"}


@pytest.fixture
def make_tuner_app(make_settings: Callable[..., Settings], runtime_dir: Path) -> Iterator[Callable[..., TunerHarness]]:
    stack = contextlib.ExitStack()

    def factory(
        *, scenario: str = "ok", versions: dict[str, str] | None = None, runner: object = None, **overrides: object
    ) -> TunerHarness:
        runner = runner if runner is not None else FakeTunerRunner(scenario)
        return start_tuner_app(stack, make_settings(**overrides), runtime_dir, versions=versions, runner=runner)

    with stack:
        yield factory


# --------------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------------


def completed_job(h: TunerHarness) -> dict:
    ref_set, _ = create_set_with_image(h.client)
    job = create_job(h.client, ref_set["id"]).json()
    return wait_for_job(h.client, job["id"], {"completed"})


def start_tuning(h: TunerHarness, job_id: str, options: dict | None = None) -> dict:
    response = h.client.post(f"/api/v1/jobs/{job_id}/tunings", json=options or {})
    assert response.status_code == 202, response.text
    return response.json()


def get_tuning(h: TunerHarness, tuning_id: str) -> dict:
    response = h.client.get(f"/api/v1/tunings/{tuning_id}")
    assert response.status_code == 200, response.text
    return response.json()


def wait_tuning(h: TunerHarness, tuning_id: str, statuses: set[str], timeout: float = 15.0) -> dict:
    def check() -> dict | None:
        run = get_tuning(h, tuning_id)
        return run if run["status"] in statuses else None

    return wait_for(check, timeout=timeout, message=f"tuning {tuning_id} in {statuses}")


def run_dir(h: TunerHarness, job_id: str, tuning_id: str) -> Path:
    return h.settings.runtime_dir / "jobs" / job_id / "tuning" / tuning_id


# --------------------------------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------------------------------


def test_tuning_lifecycle_completed(make_tuner_app):
    h = make_tuner_app()
    runner: FakeTunerRunner = h.runner  # type: ignore[assignment]
    job = completed_job(h)
    queued = start_tuning(h, job["id"], {"preset": "strong", "smoothing": 0.3, "save_blend": True})
    tn = queued["id"]
    assert set(queued) == RUN_KEYS
    assert tn.startswith("tn_") and queued["job_id"] == job["id"]
    assert queued["status"] in {"queued", "running", "completed"}
    options = queued["options"]
    assert options["preset"] == "strong" and options["smoothing"] == 0.3  # explicit field wins over the preset
    assert options["max_hole_edges"] == 1000 and options["island_min_ratio"] == 0.003
    assert options["save_blend"] is True and options["remove_inner_shell"] is True and options["symmetry"] == "auto"
    assert [s["id"] for s in queued["steps"]] == STEP_IDS
    assert queued["progress"]["step_count"] == 8

    run = wait_tuning(h, tn, {"completed"})
    assert run["error"] is None and run["finished_at"] and run["started_at"]
    statuses = {s["id"]: s["status"] for s in run["steps"]}
    assert statuses == {**dict.fromkeys(STEP_IDS, "done"), "rig_optimization": "skipped"}
    assert all(s["duration_s"] == 0.01 for s in run["steps"])
    assert run["steps"][0]["label"] == "Load mesh" and run["steps"][-1]["label"] == "Save results"
    assert run["progress"]["percent"] == 100.0 and run["progress"]["message"] == "Completed"
    assert run["progress"]["step_id"] == "export" and run["progress"]["step_index"] == 8
    assert [m["key"] for m in run["metrics"]] == ["faces", "holes"]
    assert run["validation"]["passed"] is True and run["warnings"] == []
    assert run["blender"] == {"executable": str(h.env.exe("5.2")), "version": "5.2.2 LTS", "kind": "system"}

    base = f"/api/v1/tunings/{tn}"
    result = run["result"]
    assert result["model_url"] == f"{base}/model.glb" and result["download_url"] == f"{base}/export/glb"
    assert result["report_url"] == f"{base}/report" and result["log_url"] == f"{base}/log"
    assert [(p["name"], p["view"], p["variant"], p["url"]) for p in result["previews"]] == [
        ("before_front.png", "front", "before", f"{base}/previews/before_front.png"),
        ("after_front.png", "front", "after", f"{base}/previews/after_front.png"),
    ]
    assert result["stats"]["faces"] == 12 and result["stats"]["materials"] == 1 and result["stats"]["has_uv"]

    model = h.client.get(result["model_url"])
    assert model.status_code == 200 and model.headers["content-type"] == "model/gltf-binary"
    assert model.content[:4] == b"glTF" and "inline" in model.headers["content-disposition"]
    export = h.client.get(result["download_url"])
    assert export.status_code == 200
    assert f'filename="img3d_{job["id"]}_tuned_{tn}.glb"' in export.headers["content-disposition"]
    assert export.headers["content-disposition"].startswith("attachment")
    report = h.client.get(result["report_url"])
    assert report.status_code == 200 and report.json()["tool"] == "Img3D Auto Mesh Tuner"
    assert report.json()["options"]["preset"] == "strong"
    preview = h.client.get(result["previews"][1]["url"])
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"
    assert preview.content[:8] == b"\x89PNG\r\n\x1a\n"
    log = h.client.get(result["log_url"])
    assert log.status_code == 200 and log.headers["content-type"].startswith("text/plain")
    assert '@@AMT@@{"event":"hello"' in log.text

    listed = h.client.get(f"/api/v1/jobs/{job['id']}/tunings").json()["items"]
    assert [item["id"] for item in listed] == [tn]

    # files: final layout, run.json, temp cleaned
    final = run_dir(h, job["id"], tn)
    names = sorted(p.relative_to(final).as_posix() for p in final.rglob("*") if p.is_file())
    assert names == ["blender.log", "config.json", "previews/after_front.png", "previews/before_front.png",
                     "report.json", "run.json", "tuned.blend", "tuned.glb"]
    on_disk = json.loads((final / "run.json").read_text("utf-8"))
    assert on_disk["status"] == "completed" and on_disk["result"]["blend_file"] == "tuned.blend"
    assert not (h.settings.runtime_dir / "temp" / "tuning" / tn).exists()
    assert not (h.settings.blender_temp_dir / tn).exists()

    # what the worker handed to the runner (DESIGN section 3 contract)
    assert runner.threads == ["blender-worker_0"]
    req = runner.requests[0]
    assert req.script == h.settings.backend_dir / "blender_scripts" / "auto_mesh_tuner.py"
    assert req.timeout_s == 1800 and req.cancel_event is not None and req.on_event is not None
    assert req.temp_dir == h.settings.blender_temp_dir / tn
    assert req.user_resources_dir == h.settings.blender_runtime_dir / "user"
    assert req.log_path is not None and req.log_path.name == "blender.log"
    cfg = runner.configs[0]
    work = h.settings.runtime_dir / "temp" / "tuning" / tn
    assert cfg["schema"] == 1
    assert Path(cfg["input"]) == work / "input.glb" and Path(cfg["output_dir"]) == work / "out"
    assert cfg["outputs"] == {"model": "tuned.glb", "report": "report.json", "blend": "tuned.blend",
                              "previews_dir": "previews"}
    assert cfg["context"] == {"job_id": job["id"], "tuning_id": tn, "source": "trellis2"}
    assert cfg["options"] == options
    model_path = h.settings.runtime_dir / "jobs" / job["id"] / "model.glb"
    assert runner.input_sizes == [model_path.stat().st_size]


def test_completed_with_warnings(make_tuner_app):
    h = make_tuner_app(scenario="warnings")
    job = completed_job(h)
    run = wait_tuning(h, start_tuning(h, job["id"])["id"], {"completed"})
    assert run["warnings"] == ["Eye correction skipped"]  # the result event's list is authoritative
    assert run["progress"]["message"] == "Completed with 1 warning(s)"
    assert {s["id"]: s["status"] for s in run["steps"]}["face_refinement"] == "warning"
    assert run["options"]["preset"] == "balanced" and run["options"]["smoothing"] == 0.5


@pytest.mark.parametrize(
    ("scenario", "code", "message", "failed_step"),
    [
        ("error_event", "repair_failed", "Hole filling failed: boom", "geometry_repair"),
        ("script_error", "blender_script_error", "Blender script error: ZeroDivisionError: division by zero",
         "face_refinement"),
        ("crash", "blender_crashed", "Blender exited abnormally (exit code 0xC0000005)", "topology_tuning"),
        ("timeout", "blender_timeout", "Blender did not finish within 1800 s and was stopped", "validation"),
        ("no_result", "tuning_output_missing", "Blender finished without reporting a result", None),
    ],
)
def test_tuning_failure_mapping(make_tuner_app, scenario, code, message, failed_step):
    h = make_tuner_app(scenario=scenario)
    job = completed_job(h)
    tn = start_tuning(h, job["id"])["id"]
    run = wait_tuning(h, tn, {"failed"})
    assert run["error"]["code"] == code
    assert run["error"]["message"] == message
    assert run["error"]["details"]["run_status"] in {"failed", "script_error", "crashed", "timed_out", "succeeded"}
    assert run["result"] is None and run["progress"]["message"] == message
    steps = {s["id"]: s["status"] for s in run["steps"]}
    if failed_step:
        assert steps[failed_step] == "failed"
    assert "running" not in steps.values()
    # the raw log is kept for diagnosis; files of a failed run are not served as a model
    assert '"event":"hello"' in h.client.get(f"/api/v1/tunings/{tn}/log").text
    assert_error(h.client.get(f"/api/v1/tunings/{tn}/model.glb"), 409, "conflict")
    assert (run_dir(h, job["id"], tn) / "config.json").is_file()
    assert not (h.settings.runtime_dir / "temp" / "tuning" / tn).exists()


# --------------------------------------------------------------------------------------------------
# cancel / limits / errors
# --------------------------------------------------------------------------------------------------


def test_cancel_running_and_queued(make_tuner_app):
    h = make_tuner_app()
    runner: FakeTunerRunner = h.runner  # type: ignore[assignment]
    runner.hold()
    job = completed_job(h)
    first = start_tuning(h, job["id"])["id"]
    wait_for(runner.blocked.is_set, timeout=10)
    running = wait_tuning(h, first, {"running"})
    assert running["progress"]["step_id"] == "geometry_repair"
    second = start_tuning(h, job["id"])["id"]
    assert get_tuning(h, second)["status"] == "queued"

    response = h.client.post(f"/api/v1/tunings/{second}/cancel")
    assert response.status_code == 202 and response.json()["status"] == "cancelled"
    response = h.client.post(f"/api/v1/tunings/{first}/cancel")
    assert response.status_code == 202, response.text
    assert response.json()["progress"]["message"] == "Cancelling..."
    run = wait_tuning(h, first, {"cancelled"})
    assert run["error"] is None and run["progress"]["message"] == "Cancelled"
    assert {s["id"]: s["status"] for s in run["steps"]}["geometry_repair"] == "skipped"
    assert runner.cancelled_runs == [first]
    assert len(runner.requests) == 1  # the cancelled queued run never started
    assert_error(h.client.post(f"/api/v1/tunings/{first}/cancel"), 409, "conflict")
    listed = h.client.get(f"/api/v1/jobs/{job['id']}/tunings").json()["items"]
    assert [item["id"] for item in listed] == [second, first]  # newest first


def test_create_requires_completed_job_and_valid_options(make_tuner_app):
    h = make_tuner_app()
    ref_set, _ = create_set_with_image(h.client)
    h.engine.hold()
    job = create_job(h.client, ref_set["id"]).json()
    wait_for_job(h.client, job["id"], {"running"})
    error = assert_error(h.client.post(f"/api/v1/jobs/{job['id']}/tunings", json={}), 409, "conflict")
    assert "not completed" in error["message"]
    h.engine.release()
    wait_for_job(h.client, job["id"], {"completed"})
    assert_error(h.client.post("/api/v1/jobs/job_000000000000/tunings", json={}), 404, "not_found")
    assert_error(h.client.post("/api/v1/jobs/not-an-id/tunings", json={}), 404, "not_found")
    assert_error(h.client.get("/api/v1/jobs/job_000000000000/tunings"), 404, "not_found")
    for bad in ({"smoothing": 2}, {"bogus": 1}, {"preset": "extreme"}, {"max_hole_edges": 4}, {"symmetry": "x"}):
        assert_error(h.client.post(f"/api/v1/jobs/{job['id']}/tunings", json=bad), 422, "validation_error")
    # no body at all = all defaults
    response = h.client.post(f"/api/v1/jobs/{job['id']}/tunings")
    assert response.status_code == 202, response.text
    assert response.json()["options"]["preset"] == "balanced"


def test_blender_unavailable_carries_runtime_message(make_tuner_app):
    h = make_tuner_app(versions={"4.2.17 LTS": "system"})
    job = completed_job(h)
    error = assert_error(h.client.post(f"/api/v1/jobs/{job['id']}/tunings", json={}), 503, "blender_unavailable")
    assert error["message"] == NEEDS_DOWNLOAD_42
    assert h.client.get(f"/api/v1/jobs/{job['id']}/tunings").json()["items"] == []
    assert h.runner.requests == []  # type: ignore[union-attr]


def test_queue_full_returns_429(make_tuner_app):
    h = make_tuner_app(blender_max_queue_size=1)
    runner: FakeTunerRunner = h.runner  # type: ignore[assignment]
    runner.hold()
    job = completed_job(h)
    first = start_tuning(h, job["id"])["id"]
    wait_tuning(h, first, {"running"})
    wait_for(runner.blocked.is_set, timeout=10)
    second = start_tuning(h, job["id"])["id"]
    error = assert_error(h.client.post(f"/api/v1/jobs/{job['id']}/tunings", json={}), 429, "queue_full")
    assert error["details"] == {"max_size": 1}
    runner.release()
    wait_tuning(h, first, {"completed"})
    wait_tuning(h, second, {"completed"})
    assert runner.threads == ["blender-worker_0", "blender-worker_0"]


def test_delete_tuning(make_tuner_app):
    h = make_tuner_app()
    runner: FakeTunerRunner = h.runner  # type: ignore[assignment]
    job = completed_job(h)
    done = start_tuning(h, job["id"])["id"]
    wait_tuning(h, done, {"completed"})
    runner.hold()
    active = start_tuning(h, job["id"])["id"]
    wait_tuning(h, active, {"running"})
    wait_for(runner.blocked.is_set, timeout=10)
    assert_error(h.client.delete(f"/api/v1/tunings/{active}"), 409, "conflict")
    response = h.client.delete(f"/api/v1/tunings/{done}")
    assert response.status_code == 204 and response.content == b""
    assert_error(h.client.get(f"/api/v1/tunings/{done}"), 404, "not_found")
    assert not run_dir(h, job["id"], done).exists()
    assert_error(h.client.delete(f"/api/v1/tunings/{done}"), 404, "not_found")
    runner.release()
    wait_tuning(h, active, {"completed"})


def test_deleting_job_cancels_its_active_tunings(make_tuner_app):
    h = make_tuner_app()
    runner: FakeTunerRunner = h.runner  # type: ignore[assignment]
    runner.hold()
    job = completed_job(h)
    running = start_tuning(h, job["id"])["id"]
    wait_tuning(h, running, {"running"})
    wait_for(runner.blocked.is_set, timeout=10)
    queued = start_tuning(h, job["id"])["id"]
    response = h.client.delete(f"/api/v1/jobs/{job['id']}")
    assert response.status_code == 204, response.text
    assert runner.cancelled_runs == [running]
    for tn in (running, queued):
        assert_error(h.client.get(f"/api/v1/tunings/{tn}"), 404, "not_found")
    assert not (h.settings.runtime_dir / "jobs" / job["id"]).exists()
    assert len(runner.requests) == 1
    # the worker keeps serving other jobs
    runner.release()
    other = completed_job(h)
    wait_tuning(h, start_tuning(h, other["id"])["id"], {"completed"})


def _write_completed_job(runtime_dir: Path, job_id: str) -> Job:
    now = utcnow()
    job = Job(
        id=job_id, status=JobStatus.COMPLETED, engine="fake-engine", created_at=now, started_at=now,
        finished_at=now, reference_set_id="rs_aaaaaaaaaaaa", primary_image_id="img_aaaaaaaaaaaa", seed=1,
        settings={"resolution": 512}, progress=JobProgress.for_stage(JobStage.DONE, percent=100.0),
        result=JobResultData(stats=MeshStats(vertices=8, faces=12), seed=1, resolution=512,
                             effective_resolution=512, texture_size=512, polygon_target=5000),
    )
    job_dir = runtime_dir / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.json").write_text(json.dumps(job.model_dump(mode="json")), encoding="utf-8")
    write_test_glb(job_dir / "model.glb")
    return job


def _write_run(runtime_dir: Path, job_id: str, tuning_id: str, status: JobStatus) -> None:
    run = TuningRun(id=tuning_id, job_id=job_id, status=status, created_at=utcnow(),
                    started_at=utcnow() if status is JobStatus.RUNNING else None)
    if status is JobStatus.RUNNING:
        run.steps[2].status = "running"
        run.progress.step_id = "geometry_repair"
    path = runtime_dir / "jobs" / job_id / "tuning" / tuning_id / "run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(run.model_dump(mode="json")), encoding="utf-8")


def test_interrupted_runs_are_recovered_at_startup(make_tuner_app, runtime_dir):
    _write_completed_job(runtime_dir, "job_aaaaaaaaaaaa")
    _write_run(runtime_dir, "job_aaaaaaaaaaaa", "tn_aaaaaaaaaaa1", JobStatus.RUNNING)
    _write_run(runtime_dir, "job_aaaaaaaaaaaa", "tn_aaaaaaaaaaa2", JobStatus.QUEUED)
    _write_run(runtime_dir, "job_aaaaaaaaaaaa", "tn_aaaaaaaaaaa3", JobStatus.CANCELLED)
    _write_run(runtime_dir, "job_bbbbbbbbbbbb", "tn_bbbbbbbbbbbb", JobStatus.RUNNING)  # job is gone: ignored
    bad = runtime_dir / "jobs" / "job_aaaaaaaaaaaa" / "tuning" / "tn_cccccccccccc"
    bad.mkdir(parents=True)
    (bad / "run.json").write_text("{not json", encoding="utf-8")
    h = make_tuner_app()
    for tn in ("tn_aaaaaaaaaaa1", "tn_aaaaaaaaaaa2"):
        run = get_tuning(h, tn)
        assert run["status"] == "failed" and run["error"]["code"] == "interrupted"
        assert run["finished_at"] and "running" not in {s["status"] for s in run["steps"]}
    assert get_tuning(h, "tn_aaaaaaaaaaa3")["status"] == "cancelled"
    assert_error(h.client.get("/api/v1/tunings/tn_bbbbbbbbbbbb"), 404, "not_found")
    on_disk = json.loads((runtime_dir / "jobs" / "job_aaaaaaaaaaaa" / "tuning" / "tn_aaaaaaaaaaa1" / "run.json")
                         .read_text("utf-8"))
    assert on_disk["status"] == "failed" and on_disk["error"]["code"] == "interrupted"
    assert len(h.client.get("/api/v1/jobs/job_aaaaaaaaaaaa/tunings").json()["items"]) == 3
    # a recovered job can be tuned again
    wait_tuning(h, start_tuning(h, "job_aaaaaaaaaaaa")["id"], {"completed"})


def test_shutdown_interrupts_running_tuning(make_settings, runtime_dir):
    settings = make_settings(shutdown_timeout_s=5)
    runner = FakeTunerRunner()
    runner.hold()
    with contextlib.ExitStack() as stack:
        h = start_tuner_app(stack, settings, runtime_dir, runner=runner)
        job = completed_job(h)
        running = start_tuning(h, job["id"])["id"]
        wait_tuning(h, running, {"running"})
        wait_for(runner.blocked.is_set, timeout=10)
        queued = start_tuning(h, job["id"])["id"]
    store = TuningStore(settings.paths)
    store.load()
    for tn in (running, queued):
        run = store.get(tn)
        assert run is not None and run.status is JobStatus.FAILED, run
        assert run.error is not None and run.error.code == "interrupted"
    assert runner.cancelled_runs == [running]


# --------------------------------------------------------------------------------------------------
# files + WebSocket
# --------------------------------------------------------------------------------------------------


def test_file_routes_validate_ids_and_preview_names(make_tuner_app):
    h = make_tuner_app()
    runner: FakeTunerRunner = h.runner  # type: ignore[assignment]
    job = completed_job(h)
    tn = start_tuning(h, job["id"])["id"]
    wait_tuning(h, tn, {"completed"})
    base = f"/api/v1/tunings/{tn}"
    assert h.client.get(f"{base}/previews/before_front.png").status_code == 200
    for name in ("Not-Listed.PNG", "..%5C..%5Crun.json", "..%5Crun.json", "run.json", "escape.png",
                 "%2e%2e%2frun.json", "after_front.PNG", "a b.png"):
        response = h.client.get(f"{base}/previews/{name}")
        assert response.status_code == 404, (name, response.status_code)
    for path in ("/api/v1/tunings/tn_zzzzzzzzzzzz", "/api/v1/tunings/..%2Fjobs", "/api/v1/tunings/job_aaaaaaaaaaaa",
                 "/api/v1/tunings/tn_000000000000/model.glb", "/api/v1/tunings/tn_000000000000/report",
                 "/api/v1/tunings/TN_AAAAAAAAAAAA/log"):
        assert h.client.get(path).status_code == 404, path
    assert not (run_dir(h, job["id"], tn) / "previews" / "Not-Listed.PNG").exists()

    runner.hold()
    active = start_tuning(h, job["id"])["id"]
    wait_for(runner.blocked.is_set, timeout=10)
    assert get_tuning(h, active)["status"] == "running"
    assert_error(h.client.get(f"/api/v1/tunings/{active}/model.glb"), 409, "conflict")
    assert_error(h.client.get(f"/api/v1/tunings/{active}/export/glb"), 409, "conflict")
    live_log = h.client.get(f"/api/v1/tunings/{active}/log")
    assert live_log.status_code == 200 and '"event":"plan"' in live_log.text  # live tail from the temp work dir
    assert_error(h.client.get(f"/api/v1/tunings/{active}/report"), 404, "not_found")
    runner.release()
    wait_tuning(h, active, {"completed"})


def test_file_routes_accept_query_token_when_auth_enabled(make_tuner_app):
    h = make_tuner_app(auth_enabled=True, ui_username="admin", ui_password="secret-password-1")
    assert_error(h.client.get("/api/v1/blender/runtime"), 401, "unauthorized")
    token = h.client.post("/api/v1/auth/login", json={"username": "admin", "password": "secret-password-1"}).json()
    headers = {"Authorization": f"Bearer {token['token']}"}
    ref_set, _ = create_set_with_image(h.client, headers=headers)
    job = h.client.post("/api/v1/jobs", json={"reference_set_id": ref_set["id"], "settings": {}}, headers=headers)
    job_id = job.json()["id"]
    wait_for(lambda: h.client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()["status"] == "completed")
    tn = h.client.post(f"/api/v1/jobs/{job_id}/tunings", json={}, headers=headers).json()["id"]
    wait_for(lambda: h.client.get(f"/api/v1/tunings/{tn}", headers=headers).json()["status"] == "completed")
    assert_error(h.client.get(f"/api/v1/tunings/{tn}/model.glb"), 401, "unauthorized")
    assert h.client.get(f"/api/v1/tunings/{tn}/model.glb?token={token['token']}").status_code == 200
    assert h.client.get(f"/api/v1/tunings/{tn}/previews/after_front.png?token={token['token']}").status_code == 200
    assert_error(h.client.get(f"/api/v1/tunings/{tn}?token={token['token']}"), 401, "unauthorized")


def test_tuning_updated_websocket_filtered_by_job(make_tuner_app):
    h = make_tuner_app()
    job_a = completed_job(h)
    job_b = completed_job(h)
    with h.client.websocket_connect(f"/api/v1/ws?job_id={job_b['id']}") as ws:
        assert ws_receive(ws)["type"] == "hello"
        tn_a = start_tuning(h, job_a["id"])["id"]
        wait_tuning(h, tn_a, {"completed"})
        tn_b = start_tuning(h, job_b["id"])["id"]
        messages = ws_collect_until(
            ws, lambda m: m["type"] == "tuning.updated" and m["data"]["status"] in TERMINAL
        )
    tuning = [m for m in messages if m["type"] == "tuning.updated"]
    assert {m["data"]["id"] for m in tuning} == {tn_b}
    assert {m["data"]["job_id"] for m in tuning} == {job_b["id"]}
    assert all(set(m["data"]) == RUN_KEYS for m in tuning)
    statuses = [m["data"]["status"] for m in tuning]
    assert statuses[0] == "queued" and statuses[-1] == "completed" and "running" in statuses
    percents = [m["data"]["progress"]["percent"] for m in tuning if m["data"]["status"] == "running"]
    assert percents == sorted(percents) and 0 < max(percents) < 100
    # 25 raw progress events in geometry_repair are throttled to <= 5/s (plus the never-dropped step changes)
    progress_frames = [m for m in tuning if "Repairing" in (m["data"]["progress"]["message"] or "")]
    assert len(progress_frames) < 25
    assert tuning[-1]["data"]["result"]["model_url"].endswith(f"/tunings/{tn_b}/model.glb")


def test_unfiltered_websocket_receives_every_tuning(make_tuner_app):
    h = make_tuner_app()
    job = completed_job(h)
    with h.client.websocket_connect("/api/v1/ws") as ws:
        ws_receive(ws)
        tn = start_tuning(h, job["id"])["id"]
        messages = ws_collect_until(
            ws, lambda m: m["type"] == "tuning.updated" and m["data"]["status"] == "completed"
        )
    assert messages[-1]["data"]["id"] == tn


# --------------------------------------------------------------------------------------------------
# real Blender (plumbing only: fake protocol script, real launcher runner + worker + Job Object)
# --------------------------------------------------------------------------------------------------


@pytest.mark.blender
@pytest.mark.skipif(not BLENDER_52.is_file(), reason=f"Blender 5.2 not installed at {BLENDER_52}")
def test_real_blender_plumbing(make_settings, runtime_dir):
    import psutil

    out_dir = Path(__file__).resolve().parents[1] / "outputs" / "mesh_tuner" / "backend" / "real_blender"
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = make_settings(blender_executable=str(BLENDER_52), blender_tune_timeout_s=300, shutdown_timeout_s=10)
    with contextlib.ExitStack() as stack:
        h = start_tuner_app(stack, settings, runtime_dir, real_runtime=True)
        h.container.tuning_worker.script_path = FAKE_TUNER
        detected = h.client.post("/api/v1/blender/runtime/detect").json()
        assert detected["state"] == "ready", detected
        assert detected["selected"]["version"] == "5.2.2"
        assert detected["selection_reason"] == "Configured override"
        versions = {i["version"]: i["compatible"] for i in detected["installations"]}
        assert versions.get("5.2.2") is True
        if "4.2.17" in versions:
            assert versions["4.2.17"] is False

        job = completed_job(h)
        started = time.monotonic()
        tn = start_tuning(h, job["id"])["id"]
        run = wait_tuning(h, tn, {"completed", "failed"}, timeout=180)
        elapsed = time.monotonic() - started
        assert run["status"] == "completed", run["error"]
        assert run["blender"]["version"].startswith("5.2.2")
        assert [s["status"] for s in run["steps"]] == ["done"] * 8
        assert run["result"]["stats"]["faces"] > 0
        assert len(run["result"]["previews"]) == 2
        final = run_dir(h, job["id"], tn)
        assert (final / "tuned.glb").read_bytes()[:4] == b"glTF"
        assert h.client.get(run["result"]["previews"][0]["url"]).content[:4] == b"\x89PNG"
        log_text = h.client.get(run["result"]["log_url"]).text
        assert "--python-exit-code 86" in log_text and "--factory-startup" in log_text

        # cancel a slow run: the real Blender process (Job Object) must die
        slow = start_tuning(h, job["id"], {"symmetry": "symmetrize"})["id"]

        def blender_pid() -> int | None:
            text = h.client.get(f"/api/v1/tunings/{slow}/log").text if get_tuning(h, slow)["status"] == "running" \
                else ""
            for line in text.splitlines():
                if '"event":"hello"' in line:
                    return json.loads(line[line.index("@@AMT@@") + 7:])["pid"]
            return None

        pid = wait_for(blender_pid, timeout=60, interval=0.2)
        wait_for(lambda: "slow " in h.client.get(f"/api/v1/tunings/{slow}/log").text, timeout=60)
        assert psutil.pid_exists(pid)
        cancel_at = time.monotonic()
        assert h.client.post(f"/api/v1/tunings/{slow}/cancel").status_code == 202
        cancelled = wait_tuning(h, slow, {"cancelled", "failed", "completed"}, timeout=30)
        cancel_s = time.monotonic() - cancel_at
        assert cancelled["status"] == "cancelled", cancelled
        wait_for(lambda: not psutil.pid_exists(pid), timeout=10)
        (out_dir / "summary.json").write_text(json.dumps({
            "tuning_id": tn, "elapsed_s": round(elapsed, 2), "cancel_s": round(cancel_s, 2), "run": run,
        }, indent=2), encoding="utf-8")
