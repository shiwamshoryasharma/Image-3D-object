"""Backend-integration probe for the Auto Mesh Tuner (read-only w.r.t. project sources).

Checks:
  1. RetentionService / purge_temp behaviour on runtime/blender/, runtime/jobs/<id>/tuning/, orphan tuning dirs.
  2. asyncio subprocess support under the event loop uvicorn uses on Windows (Proactor) vs Selector.
  3. Launching blender.exe from the embedded (isolated) interpreter with CREATE_NO_WINDOW + Blender's Python
     isolation (PYTHONPATH / user site ignored?).

Run:  backend\\python\\python.exe -s test\\mesh_tuner\\probe\\integration_probe.py
Output: test\\outputs\\mesh_tuner\\integration\\integration_probe.json
"""

from __future__ import annotations

import asyncio
import json
import os
import site
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

_user_site = os.path.normcase(os.path.abspath(site.getusersitepackages()))
sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p or ".")) != _user_site]
HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
BACKEND = PROJECT / "backend"
sys.path.insert(0, str(BACKEND))
OUT = PROJECT / "test" / "outputs" / "mesh_tuner" / "integration"
OUT.mkdir(parents=True, exist_ok=True)

BLENDERS = [
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
]
results: dict = {"python": sys.version, "isolated": sys.flags.isolated}


# ----------------------------------------------------------------------------------------- 1. retention
def probe_retention() -> dict:
    from config.settings import Settings
    from models.enums import JobStage, JobStatus
    from models.job import Job, JobProgress, JobResultData, MeshStats
    from services.reference_service import ReferenceService
    from services.retention_service import RetentionService
    from storage.job_store import JobStore
    from storage.reference_store import ReferenceStore
    from utils.files import safe_rmtree
    from utils.timeutil import utcnow

    rt = OUT / "retention_rt"
    safe_rmtree(rt)
    settings = Settings(_env_file=None, runtime_dir=rt, log_dir=OUT / "logs", job_retention_days=14)
    paths = settings.paths
    paths.ensure()

    def make_job(job_id: str, finished_days_ago: float) -> None:
        now = utcnow()
        fin = now - timedelta(days=finished_days_ago)
        job = Job(
            id=job_id, status=JobStatus.COMPLETED, engine="probe", created_at=fin, started_at=fin, finished_at=fin,
            reference_set_id="rs_000000000000", primary_image_id="img_000000000000", seed=1,
            progress=JobProgress.for_stage(JobStage.DONE, percent=100.0),
            result=JobResultData(stats=MeshStats(), seed=1, resolution=512, effective_resolution=512,
                                 texture_size=1024, polygon_target=5000),
        )
        store.save(job)
        (paths.job_dir(job_id) / "tuning" / "tn_aaaaaaaaaaaa").mkdir(parents=True, exist_ok=True)
        (paths.job_dir(job_id) / "tuning" / "tn_aaaaaaaaaaaa" / "model_tuned.glb").write_bytes(b"x")

    store = JobStore(paths)
    make_job("job_000000000001", 30)  # expired
    make_job("job_000000000002", 1)  # fresh
    # orphan: job dir recreated by a late tuning write after the job was deleted (no job.json)
    (paths.job_dir("job_000000000003") / "tuning").mkdir(parents=True, exist_ok=True)
    # non-id dir directly under jobs/
    (paths.jobs / "tuning_shared").mkdir(parents=True, exist_ok=True)
    # blender runtime + cache + temp
    (rt / "blender" / "5.2.2").mkdir(parents=True, exist_ok=True)
    (rt / "blender" / "5.2.2" / "blender.exe").write_bytes(b"x")
    (paths.cache / "blender").mkdir(parents=True, exist_ok=True)
    (paths.temp / "blender-download.zip.part").write_bytes(b"x")
    store.load()

    ref_store = ReferenceStore(paths)
    ref_store.load()
    refs = ReferenceService(settings, ref_store)
    retention = RetentionService(settings, store, ref_store, refs, orphan_min_age_s=0)
    report = asyncio.run(retention.run_once())
    purged_temp = paths.purge_temp()

    def exists(rel: str) -> bool:
        return (rt / rel).exists()

    return {
        "report": report.__dict__ if hasattr(report, "__dict__") else str(report),
        "report_fields": {k: getattr(report, k) for k in ("expired_jobs", "excess_jobs", "expired_sets", "orphan_dirs")},
        "purge_temp_removed": purged_temp,
        "exists_after": {
            "jobs/job_000000000001 (expired, with tuning/)": exists("jobs/job_000000000001"),
            "jobs/job_000000000002/tuning (fresh)": exists("jobs/job_000000000002/tuning/tn_aaaaaaaaaaaa/model_tuned.glb"),
            "jobs/job_000000000003 (orphan, tuning only, age>=min)": exists("jobs/job_000000000003"),
            "jobs/tuning_shared (non-id name)": exists("jobs/tuning_shared"),
            "blender/5.2.2/blender.exe": exists("blender/5.2.2/blender.exe"),
            "cache/blender": exists("cache/blender"),
            "temp/blender-download.zip.part": exists("temp/blender-download.zip.part"),
        },
    }


# ----------------------------------------------------------------------------------------- 2. asyncio subprocess
async def _async_version(exe: str) -> dict:
    started = time.perf_counter()
    proc = await asyncio.create_subprocess_exec(
        exe, "--factory-startup", "--version",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    return {"rc": proc.returncode, "first_line": out.decode("utf-8", "replace").splitlines()[0:1],
            "seconds": round(time.perf_counter() - started, 3)}


def probe_asyncio_subprocess() -> dict:
    exe = BLENDERS[0]
    res: dict = {}
    for name, factory in (("proactor", asyncio.ProactorEventLoop), ("selector", asyncio.SelectorEventLoop)):
        try:
            res[name] = asyncio.run(_async_version(exe), loop_factory=factory)
        except Exception as exc:  # noqa: BLE001
            res[name] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        import uvicorn.loops.asyncio as ula

        res["uvicorn_loop_factory_no_subprocess"] = ula.asyncio_loop_factory(use_subprocess=False).__name__
    except Exception as exc:  # noqa: BLE001
        res["uvicorn_loop_factory_no_subprocess"] = repr(exc)
    return res


# ----------------------------------------------------------------------------------------- 3. Popen + isolation
INSIDE = (
    "import sys, site, os, json, bpy;"
    "print('@@IMG3D ' + json.dumps({'type':'env','py':sys.version.split()[0],"
    "'ignore_env':sys.flags.ignore_environment,'isolated':sys.flags.isolated,"
    "'user_site_enabled':site.ENABLE_USER_SITE,'pythonpath_in_sys_path':any('IMG3D_PROBE_FAKE' in p for p in sys.path),"
    "'argv_after_dashdash':sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else None,"
    "'version':bpy.app.version_string,'version_cycle':getattr(bpy.app,'version_cycle',None),"
    "'gltf_import':hasattr(bpy.ops.import_scene,'gltf'),'gltf_export':hasattr(bpy.ops.export_scene,'gltf'),"
    "'binary_path':bpy.app.binary_path}), flush=True)"
)


def probe_popen() -> dict:
    res: dict = {}
    for exe in BLENDERS:
        if not Path(exe).is_file():
            res[exe] = {"missing": True}
            continue
        env = dict(os.environ)
        env["PYTHONPATH"] = r"C:\IMG3D_PROBE_FAKE"
        started = time.perf_counter()
        proc = subprocess.run(
            [exe, "--background", "--factory-startup", "--python-exit-code", "1",
             "--python-expr", INSIDE, "--", "--job", "job_x", "--opt", "a b"],
            capture_output=True, timeout=180, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        text = proc.stdout.decode("utf-8", "replace")
        payload = None
        for line in text.splitlines():
            if line.startswith("@@IMG3D "):
                payload = json.loads(line[len("@@IMG3D "):])
        res[exe] = {
            "rc": proc.returncode, "seconds": round(time.perf_counter() - started, 3), "payload": payload,
            "stdout_tail": text.splitlines()[-6:], "stderr_tail": proc.stderr.decode("utf-8", "replace").splitlines()[-6:],
        }
    return res


if __name__ == "__main__":
    for key, fn in (("retention", probe_retention), ("asyncio_subprocess", probe_asyncio_subprocess),
                    ("popen_isolation", probe_popen)):
        try:
            results[key] = fn()
        except Exception as exc:  # noqa: BLE001
            import traceback

            results[key] = {"error": traceback.format_exc()}
    (OUT / "integration_probe.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(json.dumps(results, indent=2, default=str))
