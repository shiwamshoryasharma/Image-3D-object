"""Restart recovery, graceful shutdown and retention cleanup."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from datetime import timedelta

from fakes import FakeEngine
from helpers import create_job, create_set_with_image, wait_for_job
from starlette.testclient import TestClient

from main import create_app
from models.enums import JobStage, JobStatus
from models.job import Job, JobProgress
from storage.job_store import JobStore
from utils.timeutil import utcnow


def _write_job(runtime_dir, status: JobStatus, *, job_id: str = "job_aaaaaaaaaaaa", created=None, finished=None) -> Job:
    job = Job(
        id=job_id,
        status=status,
        engine="fake-engine",
        created_at=created or utcnow(),
        started_at=utcnow() if status is JobStatus.RUNNING else None,
        finished_at=finished,
        reference_set_id="rs_aaaaaaaaaaaa",
        primary_image_id="img_aaaaaaaaaaaa",
        seed=7,
        settings={"resolution": 1024},
        progress=JobProgress.for_stage(JobStage.SHAPE if status is JobStatus.RUNNING else JobStage.QUEUED),
    )
    job_dir = runtime_dir / "jobs" / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.json").write_text(json.dumps(job.model_dump(mode="json")), encoding="utf-8")
    return job


def test_jobs_left_running_are_marked_interrupted(make_app, runtime_dir):
    running = _write_job(runtime_dir, JobStatus.RUNNING, job_id="job_aaaaaaaaaaaa")
    queued = _write_job(runtime_dir, JobStatus.QUEUED, job_id="job_bbbbbbbbbbbb")
    h = make_app()
    for job_id in (running.id, queued.id):
        job = h.client.get(f"/api/v1/jobs/{job_id}").json()
        assert job["status"] == "failed"
        assert job["error"]["code"] == "interrupted"
        assert job["finished_at"]
    on_disk = json.loads((runtime_dir / "jobs" / running.id / "job.json").read_text("utf-8"))
    assert on_disk["status"] == "failed"
    assert on_disk["error"]["code"] == "interrupted"


def test_corrupt_job_files_are_skipped(make_app, runtime_dir):
    bad = runtime_dir / "jobs" / "job_cccccccccccc"
    bad.mkdir(parents=True)
    (bad / "job.json").write_text("{not json", encoding="utf-8")
    h = make_app()
    assert h.client.get("/api/v1/jobs").json()["items"] == []


def test_graceful_shutdown_interrupts_running_job(make_settings):
    settings = make_settings(shutdown_timeout_s=5)
    engine = FakeEngine()
    engine.hold()
    app = create_app(settings, engine=engine)
    with TestClient(app) as client:
        ref_set, _ = create_set_with_image(client)
        running = create_job(client, ref_set["id"]).json()
        wait_for_job(client, running["id"], {"running"})
        queued = create_job(client, ref_set["id"]).json()
    # Lifespan shutdown cancelled the running job and failed the queued one.
    store = JobStore(settings.paths)
    store.load()
    for job_id in (running["id"], queued["id"]):
        job = store.get(job_id)
        assert job is not None
        assert job.status is JobStatus.FAILED, job
        assert job.error is not None and job.error.code == "interrupted"
    assert engine.unload_calls == 1  # engine unloaded at shutdown


def test_retention_cleanup(make_app, runtime_dir):
    old = utcnow() - timedelta(days=30)
    _write_job(runtime_dir, JobStatus.COMPLETED, job_id="job_dddddddddddd", created=old, finished=old)
    for index in range(3):
        _write_job(runtime_dir, JobStatus.FAILED, job_id=f"job_eeeeeeeeeee{index}", finished=utcnow())
    h = make_app(job_retention_days=14, max_stored_jobs=2, reference_set_retention_days=30)
    ref_set, _ = create_set_with_image(h.client)
    # Age the set on disk + in the store.
    store = h.container.reference_store
    stale = store.get(ref_set["id"])
    stale.updated_at = utcnow() - timedelta(days=31)
    store.save(stale)

    report = h.client.portal.call(h.container.retention_service.run_once)
    assert report.expired_jobs == 1
    assert report.excess_jobs == 1
    assert report.expired_sets == 1
    remaining = sorted(j["id"] for j in h.client.get("/api/v1/jobs").json()["items"])
    assert len(remaining) == 2
    assert not (runtime_dir / "jobs" / "job_dddddddddddd").exists()
    assert h.client.get(f"/api/v1/reference-sets/{ref_set['id']}").status_code == 404


def test_temp_dir_is_purged_at_startup(make_app, runtime_dir):
    temp = runtime_dir / "temp"
    temp.mkdir(parents=True)
    (temp / "leftover.bin").write_bytes(b"x")
    os.makedirs(temp / "nested" / "dir")
    make_app()
    assert list(temp.iterdir()) == []


def test_retention_never_deletes_a_set_while_it_is_in_use(make_app):
    """Set expiry runs under the per-set lock and re-checks staleness (no race with uploads/job snapshots)."""
    h = make_app(reference_set_retention_days=30)
    ref_set, _ = create_set_with_image(h.client)
    store = h.container.reference_store
    stale = store.get(ref_set["id"])
    stale.updated_at = utcnow() - timedelta(days=31)
    store.save(stale)
    references = h.container.reference_service
    retention = h.container.retention_service

    async def scenario():
        async with references.locked_set(ref_set["id"]):  # e.g. an upload or a job copying the inputs
            task = asyncio.create_task(retention.run_once())
            await asyncio.sleep(0.5)
            assert not task.done(), "retention must wait for the set lock"
            fresh = store.get(ref_set["id"])
            fresh.updated_at = utcnow()  # the in-flight operation refreshes the set
            store.save(fresh)
        return await asyncio.wait_for(task, timeout=10)

    report = h.client.portal.call(scenario)
    assert report.expired_sets == 0
    assert h.client.get(f"/api/v1/reference-sets/{ref_set['id']}").status_code == 200


def test_retention_removes_old_orphaned_directories_only(make_app, runtime_dir):
    h = make_app()
    ref_set, _ = create_set_with_image(h.client)
    job = create_job(h.client, ref_set["id"]).json()
    wait_for_job(h.client, job["id"], {"completed"})

    jobs_dir = runtime_dir / "jobs"
    uploads_dir = runtime_dir / "uploads"
    orphan_job = jobs_dir / "job_0123456789ab"  # e.g. job.json deleted but model.glb was locked by a download
    (orphan_job / "inputs").mkdir(parents=True)
    (orphan_job / "model.glb").write_bytes(b"glTF")
    orphan_set = uploads_dir / "rs_0123456789ab"
    orphan_set.mkdir(parents=True)
    (orphan_set / "img_0123456789ab.png").write_bytes(b"x")
    fresh_orphan = jobs_dir / "job_ba9876543210"  # a job being created right now (inputs before job.json)
    (fresh_orphan / "inputs").mkdir(parents=True)
    corrupt = jobs_dir / "job_cccccccccccc"  # unreadable metadata is kept for inspection
    corrupt.mkdir()
    (corrupt / "job.json").write_text("{not json", encoding="utf-8")
    not_an_id = jobs_dir / "keep-me"
    not_an_id.mkdir()
    old = time.time() - 2 * 3600
    for directory in (orphan_job, orphan_set, corrupt, not_an_id, jobs_dir / job["id"], uploads_dir / ref_set["id"]):
        os.utime(directory, (old, old))

    report = h.client.portal.call(h.container.retention_service.run_once)
    assert report.orphan_dirs == 2
    assert not orphan_job.exists() and not orphan_set.exists()
    for kept in (fresh_orphan, corrupt, not_an_id, jobs_dir / job["id"], uploads_dir / ref_set["id"]):
        assert kept.exists(), kept
    assert h.client.get(f"/api/v1/jobs/{job['id']}/model.glb").status_code == 200


class UninterruptibleEngine(FakeEngine):
    """Generation blocks in a non-interruptible call (like a CUDA kernel) until ``gate`` opens, then honours cancel."""

    def generate(self, request, reporter, cancel):
        self.generation_started.set()
        self.gate.wait(10)
        cancel.raise_if_cancelled()
        return super().generate(request, reporter, cancel)


def test_user_cancel_is_kept_when_shutdown_follows(make_settings):
    """A job the user cancelled must end 'cancelled', not 'failed/interrupted', if the server stops meanwhile."""
    settings = make_settings(shutdown_timeout_s=10)
    engine = UninterruptibleEngine()
    engine.hold()
    app = create_app(settings, engine=engine)
    with TestClient(app) as client:
        ref_set, _ = create_set_with_image(client)
        job = create_job(client, ref_set["id"]).json()
        assert engine.generation_started.wait(5)
        assert client.post(f"/api/v1/jobs/{job['id']}/cancel").json()["status"] == "running"
        threading.Timer(0.5, engine.release).start()  # the blocking call returns during shutdown
    store = JobStore(settings.paths)
    store.load()
    final = store.get(job["id"])
    assert final is not None and final.status is JobStatus.CANCELLED, final


def test_worker_failure_outside_the_job_handler_fails_the_job(harness):
    """An unexpected error in the worker itself must not leave the job queued/running forever."""
    worker = harness.container.worker
    original = worker._publish_queue_positions

    def boom() -> None:
        raise RuntimeError("worker bug")

    worker._publish_queue_positions = boom
    ref_set, _ = create_set_with_image(harness.client)
    job = create_job(harness.client, ref_set["id"]).json()
    failed = wait_for_job(harness.client, job["id"], {"completed", "failed", "cancelled"})
    assert failed["status"] == "failed" and failed["error"]["code"] == "internal_error"
    assert failed["finished_at"]
    worker._publish_queue_positions = original
    nxt = create_job(harness.client, ref_set["id"]).json()
    assert wait_for_job(harness.client, nxt["id"], {"completed", "failed", "cancelled"})["status"] == "completed"
