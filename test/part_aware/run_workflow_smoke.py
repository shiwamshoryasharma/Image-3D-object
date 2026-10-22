"""S11 real workflow smoke: prompt → Z-Image-Turbo → TRELLIS.2 → ``semantic_parts`` through ``POST /workflows``.

Run under the GPU lock from the repo root::

    backend\\python\\python.exe -s test\\part_aware\\gpu_lock.py --label s11 -- ^
        backend\\python\\python.exe -s test\\part_aware\\run_workflow_smoke.py [--parts-only JOB_ID]

Starts ``backend/main.py`` on a free localhost port (auth off) with ``IMG3D_RUNTIME_DIR = test/.runtime/s11`` and
``IMG3D_LOG_DIR = test/.runtime/s11/logs``; refuses (exit 2) unless the backend's ``Configuration:`` line names
them, a job folder lies outside that runtime or a job id is one of :data:`EXCLUDED_JOB_IDS`. Every output is read
back through the API (workflow, children, model/parts GLB, manifest, artifacts) and summarised in
``test/outputs/part_aware/s11_real/summary.json``.

``--parts-only JOB_ID`` re-runs only the part step: a ``{source: {kind: job}}`` workflow on a completed job of this
runtime (used after a part-step failure once the parts library is fixed). Exit code 0 when the workflow completed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
PYTHON = REPO / "backend" / "python" / "python.exe"
MAIN = REPO / "backend" / "main.py"
RUNTIME = REPO / "test" / ".runtime" / "s11"
LOG_DIR = RUNTIME / "logs"
OUT = REPO / "test" / "outputs" / "part_aware" / "s11_real"
EXCLUDED_JOB_IDS = frozenset({"job_269a864ac597", "job_fa0c4771ec03"})
WORKFLOW_TIMEOUT_S = 4 * 3600.0
PROMPT = "an anime girl with long orange hair, a blue dress, white gloves and brown boots"
PARTS = {"strategy": "semantic_parts", "segmentation": {"profile": "baseline"}}


class Refusal(SystemExit):
    def __init__(self, message: str) -> None:
        print(f"REFUSED: {message}", file=sys.stderr)
        super().__init__(2)


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


class Backend:
    def __init__(self) -> None:
        if not _under(RUNTIME, REPO / "test") or not _under(LOG_DIR, REPO / "test"):
            raise Refusal(f"runtime {RUNTIME} / log dir {LOG_DIR} not under test/")
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        env = {k: v for k, v in os.environ.items() if not k.startswith("IMG3D_")}
        env.update({
            "IMG3D_RUNTIME_DIR": str(RUNTIME),
            "IMG3D_LOG_DIR": str(LOG_DIR),
            "IMG3D_AUTH_ENABLED": "false",
            "IMG3D_PRELOAD_MODEL": "false",
            "IMG3D_BLENDER_DETECT_ON_STARTUP": "false",
            "IMG3D_FRONTEND_DIST_DIR": str(RUNTIME / "no-frontend"),
            "PYTHONUNBUFFERED": "1",
        })
        self.stdout = open(LOG_DIR / "backend_stdout.txt", "w", encoding="utf-8", errors="replace")  # noqa: SIM115
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            [str(PYTHON), "-s", str(MAIN), "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=str(REPO), env=env, stdout=self.stdout, stderr=subprocess.STDOUT, creationflags=flags,
        )
        self.log_path = LOG_DIR / "img3d.log"
        self.log_offset = self.log_path.stat().st_size if self.log_path.is_file() else 0

    def wait_ready(self, timeout: float = 240.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise SystemExit(f"backend exited with {self.proc.returncode}; see {LOG_DIR / 'backend_stdout.txt'}")
            try:
                if self.get("/api/v1/health")[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(1.0)
        else:
            raise SystemExit("backend did not become healthy")
        line = next((ln for ln in self.log_text().splitlines() if "Configuration:" in ln), None)
        flat = re.sub(r"\\+", "/", line or "")
        if RUNTIME.as_posix() not in flat or LOG_DIR.as_posix() not in flat:
            raise Refusal("the backend's Configuration line does not name the test runtime and log dir")

    def log_text(self) -> str:
        if not self.log_path.is_file():
            return ""
        with open(self.log_path, "rb") as fh:
            fh.seek(self.log_offset)
            return fh.read().decode("utf-8", errors="replace")

    def request(self, method: str, path: str, body: Any = None, timeout: float = 120.0) -> tuple[int, Any, int]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload, headers, status = resp.read(), dict(resp.headers), resp.status
        except urllib.error.HTTPError as exc:
            payload, headers, status = exc.read(), dict(exc.headers), exc.code
        ctype = headers.get("content-type", headers.get("Content-Type", ""))
        return status, (json.loads(payload) if "json" in ctype and payload else payload), len(payload)

    def get(self, path: str) -> tuple[int, Any, int]:
        return self.request("GET", path)

    def stop(self) -> None:
        if self.proc.poll() is None:
            try:
                if os.name == "nt":
                    self.proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    self.proc.terminate()
                self.proc.wait(timeout=90)
            except (subprocess.TimeoutExpired, OSError):
                self.proc.kill()
                self.proc.wait(timeout=30)
        self.stdout.close()


def check_job(job_id: str | None) -> None:
    if job_id is None:
        return
    if job_id in EXCLUDED_JOB_IDS:
        raise Refusal(f"{job_id} is excluded")
    if not (RUNTIME / "jobs" / job_id / "job.json").is_file():
        raise Refusal(f"job.json of {job_id} is not under the test runtime")


def follow(backend: Backend, wf_id: str) -> dict:
    deadline = time.monotonic() + WORKFLOW_TIMEOUT_S
    last = None
    while time.monotonic() < deadline:
        status, body, _ = backend.get(f"/api/v1/workflows/{wf_id}")
        if status != 200:
            raise SystemExit(f"GET workflow -> {status} {body}")
        key = (body["status"], body["current_step"])
        if key != last:
            last = key
            print(f"  {time.strftime('%H:%M:%S')} {body['status']} {body['current_step']} "
                  f"{body['progress']['percent']}% {body['progress']['message']}", flush=True)
        if body["status"] in ("completed", "failed", "cancelled"):
            return body
        time.sleep(3.0)
    raise SystemExit("workflow timed out")


def _duration(item: dict | None) -> float | None:
    if not item or not item.get("started_at") or not item.get("finished_at"):
        return None
    from datetime import datetime

    def parse(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    return round((parse(item["finished_at"]) - parse(item["started_at"])).total_seconds(), 1)


def inspect(backend: Backend, wf: dict) -> dict[str, Any]:
    """Read every output back through the API."""
    out = wf["outputs"]
    summary: dict[str, Any] = {"workflow_id": wf["id"], "status": wf["status"], "error": wf["error"],
                               "progress": wf["progress"], "outputs": out}
    steps = {s["id"]: s for s in wf["steps"]}
    summary["steps"] = {k: {"status": s["status"], "node_id": s["node_id"], "warning_count": s["warning_count"],
                            "resource": s["resource"], "artifacts": s["artifacts"]} for k, s in steps.items()}
    children: dict[str, Any] = {}
    ig = steps.get("text_to_image", {}).get("resource")
    if ig:
        _, body, _ = backend.get(f"/api/v1/image-generations/{ig['id']}")
        children["image_generation"] = {
            "id": ig["id"], "status": body["status"], "seconds": _duration(body),
            "warnings": [w["code"] for w in body["warnings"]],
            "steps": {s["id"]: s.get("duration_s") for s in body.get("steps", [])},
            "readiness": (body.get("result") or {}).get("candidates", [{}])[0].get("readiness"),
        }
    if out.get("job_id"):
        check_job(out["job_id"])
        _, job, _ = backend.get(f"/api/v1/jobs/{out['job_id']}")
        result = job.get("result") or {}
        children["job"] = {"id": job["id"], "status": job["status"], "seconds": _duration(job),
                           "faces": (result.get("stats") or {}).get("faces"), "timings": result.get("timings")}
    if out.get("segmentation_id"):
        _, sg, _ = backend.get(f"/api/v1/segmentations/{out['segmentation_id']}")
        children["segmentation"] = {"id": sg["id"], "status": sg["status"], "seconds": _duration(sg)}
    if out.get("part_analysis_id"):
        _, pa, _ = backend.get(f"/api/v1/part-analyses/{out['part_analysis_id']}")
        result = pa.get("result") or {}
        children["part_analysis"] = {
            "id": pa["id"], "status": pa["status"], "seconds": _duration(pa), "error": pa.get("error"),
            "warnings": [w["code"] for w in pa.get("warnings", [])],
            "options": {k: pa["options"].get(k) for k in ("strategy", "views", "segmentation_id",
                                                           "segmentation_source", "segmentation_rerun")},
            "steps": {s["id"]: s.get("duration_s") for s in pa.get("steps", [])},
            "stats": result.get("stats"), "part_count": len(result.get("parts") or []),
            "quality": {k: result.get(k) for k in ("part_count", "physical_count", "region_count", "camera_fit_iou",
                                                    "unlabelled_face_ratio", "cut_partner_consistency")},
        }
    summary["children"] = children
    fetched: dict[str, Any] = {}
    urls = {k: out.get(k) for k in ("model_url", "parts_model_url", "manifest_url")}
    urls.update({f"artifact:{s['id']}:{a['role']}": a["url"] for s in wf["steps"] for a in s["artifacts"]})
    for name, url in urls.items():
        if url:
            status, body, size = backend.get(url)
            entry: dict[str, Any] = {"url": url, "status": status, "bytes": size}
            if name == "manifest_url" and status == 200 and isinstance(body, dict):
                entry["schema"] = body.get("schema")
                entry["parts"] = len(body.get("parts") or [])
            fetched[name] = entry
    summary["fetched"] = fetched
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parts-only", metavar="JOB_ID", help="re-run only the part step on this job")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    backend = Backend()
    report: dict[str, Any] = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "runtime": str(RUNTIME)}
    try:
        backend.wait_ready()
        _, features, _ = backend.get("/api/v1/features")
        pipeline = features["pipeline"]
        report["features"] = {
            "prompt_to_3d": [pipeline["prompt_to_3d"]["available"], pipeline["prompt_to_3d"]["code"]],
            "part_extraction": {s["id"]: s["available"] for s in pipeline["part_extraction"]["strategies"]},
            "workflow": [features["workflow"]["available"], features["workflow"]["code"]],
        }
        print("features:", report["features"], flush=True)
        if args.parts_only:
            check_job(args.parts_only)
            body: dict[str, Any] = {"name": "S11 smoke: parts only", "source": {"kind": "job", "job_id":
                                                                                 args.parts_only}, "parts": PARTS}
        else:
            body = {"name": "S11 smoke", "source": {"kind": "prompt", "generation": {
                "prompt": PROMPT, "preset": "character", "style": "anime", "seed": 11, "randomize_seed": False}},
                "reconstruction": {"settings": {"resolution": 1024, "seed": 11, "randomize_seed": False}},
                "parts": PARTS}
        report["request"] = body
        status, created, _ = backend.request("POST", "/api/v1/workflows", body)
        if status != 202:
            raise SystemExit(f"POST /workflows -> {status} {created}")
        print(f"workflow {created['id']} created", flush=True)
        wf = follow(backend, created["id"])
        report.update(inspect(backend, wf))
        report["wall_s"] = round(time.monotonic() - started, 1)
        name = "summary_parts_only.json" if args.parts_only else "summary.json"
        (OUT / name).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({k: report[k] for k in ("workflow_id", "status", "error", "wall_s")}, indent=1))
        return 0 if wf["status"] == "completed" else 1
    finally:
        backend.stop()


if __name__ == "__main__":
    raise SystemExit(main())
