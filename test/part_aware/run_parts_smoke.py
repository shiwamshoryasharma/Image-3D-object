"""S10 acceptance smoke: real part analyses on freshly generated TRELLIS.2 jobs (TEST-ONLY; real weights, real GPU).

Starts the backend itself (``backend/main.py`` on a free localhost port, auth off) with
``IMG3D_RUNTIME_DIR = test/.runtime/parts_smoke`` and ``IMG3D_LOG_DIR = test/.runtime/parts_smoke/logs`` and applies
the runtime refusal rule (exit 2): both paths must be under ``test/``, the backend's ``Configuration:`` log line
must name them, every ``job.json`` must be under the test runtime and no job id may be in :data:`EXCLUDED_JOB_IDS`.
Then, for ``test/images/ALICE/ALICE_full_front_view.png`` and ``test/images/ANIME_CHARACTER_01/Alice.png``:

1. a reference set + ``POST /jobs`` (default settings; the real TRELLIS.2 path, several minutes each);
2. part analyses ``components``, ``semantic_parts`` with ``views: 0`` and ``semantic_parts`` with ``views: 8``;
3. per analysis: manifest valid (``PartsManifest`` + ``AssetReader(verify="full")``: hashes and bit-exact
   reassembly), ``model.glb``/``preview.png``/``job.json`` hashes unchanged, ``unlabelled_face_ratio <= 0.10`` and
   ``cut_partner_consistency >= 0.90`` (semantic analyses; ``components`` is geometry-only: every face is
   ``unknown`` and nothing is cut, so both values are reported, not gated), timings, camera IoU and the fit search
   (front/back near-tie) recorded;
4. views 0 vs 8 compared (face-label agreement, per-class face IoU, counts, timings).

Outputs: ``test/outputs/part_aware/s10_smoke/report.json`` + ``report.md`` (+ previews/overlays copied for
inspection); every ``[torch_guard] restored ...`` record of ``<log_dir>/img3d.log`` goes into
``test/outputs/part_aware/torch_guard.json`` under key ``s10_parts``.

Usage (repository root, under the GPU lock)::

    backend\\python\\python.exe -s test\\part_aware\\gpu_lock.py --label s10 -- ^
        backend\\python\\python.exe -s test\\part_aware\\run_parts_smoke.py [--reuse-jobs] [--cases alice,anime]

``--reuse-jobs`` reuses completed jobs this script created earlier in the same test runtime (``jobs.json``) instead
of generating new ones. Exit codes: 0 all gates pass, 1 a gate failed, 2 refusal, 3 backend/job failure.
"""

from __future__ import annotations

import os
import site
import sys

_USER_SITE = os.path.normcase(os.path.abspath(site.getusersitepackages()))
sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p)) != _USER_SITE]

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import signal  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
import uuid  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

import numpy as np  # noqa: E402

from parts.manifest import PartsManifest  # noqa: E402
from parts.reader import AssetReader  # noqa: E402

PYTHON = REPO / "backend" / "python" / "python.exe"
MAIN = REPO / "backend" / "main.py"
RUNTIME = REPO / "test" / ".runtime" / "parts_smoke"
LOG_DIR = RUNTIME / "logs"
OUT = REPO / "test" / "outputs" / "part_aware" / "s10_smoke"
TORCH_GUARD = REPO / "test" / "outputs" / "part_aware" / "torch_guard.json"
EXCLUDED_JOB_IDS = frozenset({"job_269a864ac597", "job_fa0c4771ec03"})
CASES = {
    "alice": REPO / "test" / "images" / "ALICE" / "ALICE_full_front_view.png",
    "anime": REPO / "test" / "images" / "ANIME_CHARACTER_01" / "Alice.png",
}
ANALYSES = (
    ("components", {"strategy": "components"}),
    ("semantic_v0", {"strategy": "semantic_parts", "views": 0}),
    ("semantic_v8", {"strategy": "semantic_parts", "views": 8}),
)
MAX_UNLABELLED = 0.10
MIN_CUT_CONSISTENCY = 0.90
JOB_TIMEOUT_S = 3600.0
ANALYSIS_TIMEOUT_S = 2400.0
PROTECTED = ("model.glb", "preview.png", "job.json")


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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------------------------------
# Backend process + HTTP
# --------------------------------------------------------------------------------------------------


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

    def wait_ready(self, timeout: float = 180.0) -> None:
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
        self._check_configuration()

    def _check_configuration(self) -> None:
        text = self.log_text()
        line = next((ln for ln in text.splitlines() if "Configuration:" in ln), None)
        if line is None:
            raise Refusal("no 'Configuration:' line in the backend log")
        flat = re.sub(r"\\+", "/", line)  # repr/JSON-escaped Windows paths
        if RUNTIME.as_posix() not in flat or LOG_DIR.as_posix() not in flat:
            raise Refusal("the backend's Configuration line does not name the test runtime and log dir")

    def log_text(self) -> str:
        if not self.log_path.is_file():
            return ""
        with open(self.log_path, "rb") as fh:
            fh.seek(self.log_offset)
            return fh.read().decode("utf-8", errors="replace")

    def request(self, method: str, path: str, body: Any = None, *, raw: bytes | None = None,
                content_type: str | None = None, timeout: float = 120.0) -> tuple[int, Any, dict[str, str]]:
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if content_type or body is not None:
            req.add_header("Content-Type", content_type or "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read()
                headers = dict(resp.headers)
                status = resp.status
        except urllib.error.HTTPError as exc:
            payload, headers, status = exc.read(), dict(exc.headers), exc.code
        ctype = headers.get("content-type", headers.get("Content-Type", ""))
        return status, (json.loads(payload) if "json" in ctype and payload else payload), headers

    def get(self, path: str, **kw: Any) -> tuple[int, Any, dict[str, str]]:
        return self.request("GET", path, **kw)

    def post(self, path: str, body: Any = None) -> tuple[int, Any, dict[str, str]]:
        return self.request("POST", path, body if body is not None else {})

    def upload(self, set_id: str, image: Path) -> dict:
        boundary = uuid.uuid4().hex
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="slot"\r\n\r\nfront\r\n'.encode(),
            (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{image.name}"\r\n'
             "Content-Type: image/png\r\n\r\n").encode() + image.read_bytes() + b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
        status, body, _ = self.request("POST", f"/api/v1/reference-sets/{set_id}/images", raw=b"".join(parts),
                                       content_type=f"multipart/form-data; boundary={boundary}")
        if status not in (200, 201):
            raise SystemExit(f"upload failed: {status} {body}")
        return body

    def stop(self) -> None:
        if self.proc.poll() is None:
            try:
                if os.name == "nt":
                    self.proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    self.proc.terminate()
                self.proc.wait(timeout=60)
            except (subprocess.TimeoutExpired, OSError):
                self.proc.kill()
                self.proc.wait(timeout=30)
        self.stdout.close()


def poll(backend: Backend, path: str, timeout: float, label: str) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        status, body, _ = backend.get(path)
        if status != 200:
            raise SystemExit(f"{label}: GET {path} -> {status} {body}")
        if body["status"] != last:
            last = body["status"]
            print(f"  {label}: {last} {body.get('progress', {}).get('message') or ''}", flush=True)
        if body["status"] in ("completed", "failed", "cancelled"):
            return body
        time.sleep(2.0)
    raise SystemExit(f"{label}: timed out after {timeout:.0f} s")


# --------------------------------------------------------------------------------------------------
# Cases
# --------------------------------------------------------------------------------------------------


def job_dir(job_id: str) -> Path:
    if job_id in EXCLUDED_JOB_IDS:
        raise Refusal(f"job {job_id} is excluded")
    path = RUNTIME / "jobs" / job_id
    if not _under(path / "job.json", RUNTIME) or not (path / "job.json").is_file():
        raise Refusal(f"job.json of {job_id} is not under the test runtime")
    return path


def create_job(backend: Backend, case: str, image: Path) -> dict:
    status, ref_set, _ = backend.post("/api/v1/reference-sets", {"name": f"s10 smoke {case}"})
    if status != 201:
        raise SystemExit(f"reference set: {status} {ref_set}")
    backend.upload(ref_set["id"], image)
    status, job, _ = backend.post("/api/v1/jobs", {"reference_set_id": ref_set["id"], "settings": {}})
    if status not in (200, 201, 202):
        raise SystemExit(f"job: {status} {job}")
    job_dir_path = RUNTIME / "jobs" / job["id"]
    if job["id"] in EXCLUDED_JOB_IDS:
        raise Refusal(f"job {job['id']} is excluded")
    started = time.perf_counter()
    final = poll(backend, f"/api/v1/jobs/{job['id']}", JOB_TIMEOUT_S, f"{case} job {job['id']}")
    if final["status"] != "completed":
        raise SystemExit(f"{case} job {job['id']} {final['status']}: {final.get('error')}")
    job_dir(job["id"])
    final["_wall_s"] = round(time.perf_counter() - started, 1)
    assert job_dir_path.is_dir()
    return final


def analyse(backend: Backend, case: str, job: dict, name: str, body: dict, hashes: dict[str, str]) -> dict:
    status, created, _ = backend.post(f"/api/v1/jobs/{job['id']}/part-analyses", body)
    if status != 202:
        return {"name": name, "error": f"create -> {status} {created}", "gates": {"created": False}}
    started = time.perf_counter()
    run = poll(backend, f"/api/v1/part-analyses/{created['id']}", ANALYSIS_TIMEOUT_S, f"{case} {name}")
    wall = round(time.perf_counter() - started, 1)
    directory = job_dir(job["id"])
    after = {n: sha256(directory / n) for n in PROTECTED}
    entry: dict[str, Any] = {
        "name": name, "id": run["id"], "request": body, "status": run["status"], "wall_s": wall,
        "error": run.get("error"), "warnings": sorted({w["code"] for w in run["warnings"]}),
        "warning_messages": [w["message"] for w in run["warnings"]][:20],
        "models": run["models"],
        "steps": {s["id"]: {"status": s["status"], "duration_s": s["duration_s"], "message": s["message"]}
                  for s in run["steps"]},
    }
    gates: dict[str, Any] = {"completed": run["status"] == "completed", "job_files_unchanged": after == hashes}
    if run["status"] != "completed":
        log_status, log, _ = backend.get(f"/api/v1/part-analyses/{run['id']}/log")
        entry["log_tail"] = log[-4000:].decode("utf-8", "replace") if log_status == 200 and isinstance(log, bytes) else None
        entry["gates"] = gates
        return entry
    result = run["result"]
    steps = {s["id"]: s for s in run["steps"]}
    asset = directory / "parts" / run["id"] / "asset"
    t = time.perf_counter()
    try:
        manifest = PartsManifest.model_validate_json((asset / "manifest.json").read_bytes())
        reader = AssetReader(asset, verify="full")
        manifest_ok, reassembly_ok = True, bool(reader.manifest.reassembly.all_exact)
    except Exception as exc:  # noqa: BLE001 - recorded as a failed gate
        manifest_ok, reassembly_ok, manifest = False, False, None
        entry["manifest_error"] = f"{type(exc).__name__}: {exc}"
    entry["verify_full_s"] = round(time.perf_counter() - t, 2)
    _status, served, _ = backend.get(f"/api/v1/part-analyses/{run['id']}/manifest")
    gates.update({"manifest_valid": manifest_ok and isinstance(served, dict) and served.get("analysis_id") == run["id"],
                  "reassembly_exact": reassembly_ok})
    semantic = body["strategy"] != "components"
    if semantic:
        gates["unlabelled_face_ratio<=0.10"] = result["unlabelled_face_ratio"] <= MAX_UNLABELLED
        consistency = result["cut_partner_consistency"]
        gates["cut_partner_consistency>=0.90"] = consistency is not None and consistency >= MIN_CUT_CONSISTENCY
    entry.update({
        "result_status": result["status"],
        "summary": {k: result[k] for k in ("part_count", "physical_count", "deferred_count", "region_count",
                                           "camera_fit_iou", "unlabelled_face_ratio", "partner_filled_ratio",
                                           "cut_partner_consistency")},
        "timings": result["timings"],
        "parts": [{k: p[k] for k in ("id", "kind", "category", "faces", "confidence", "side")} for p in result["parts"]],
        "camera_fit": steps.get("camera_fit", {}).get("details"),
        "views": steps.get("label_lift", {}).get("details", {}).get("views"),
        "prepare": steps.get("prepare", {}).get("details"),
        "manifest_warnings": sorted({w.code for w in manifest.warnings}) if manifest else None,
        "faces": manifest.source.faces if manifest else None,
        "stats": result["stats"],
        "gates": gates,
    })
    copy_dir = OUT / case / name
    copy_dir.mkdir(parents=True, exist_ok=True)
    for rel in ("segmentation/overlay.png", "previews/front.png", "previews/back.png", "previews/left.png",
                "previews/right.png"):
        if (asset / rel).is_file():
            shutil.copyfile(asset / rel, copy_dir / rel.replace("/", "_"))
    return entry


def compare_views(directory: Path, v0: dict, v8: dict) -> dict:
    if v0.get("status") != "completed" or v8.get("status") != "completed":
        return {"available": False}
    a = np.fromfile(directory / "parts" / v0["id"] / "asset" / "buffers" / "face_labels.u16", dtype=np.uint16)
    b = np.fromfile(directory / "parts" / v8["id"] / "asset" / "buffers" / "face_labels.u16", dtype=np.uint16)
    classes = sorted(set(np.unique(a).tolist()) | set(np.unique(b).tolist()))
    from parts.taxonomy import BY_VALUE  # noqa: PLC0415

    per_class = {}
    for value in classes:
        x, y = a == value, b == value
        union = int((x | y).sum())
        if union:
            per_class[BY_VALUE[value].id if value in BY_VALUE else str(value)] = {
                "faces_v0": int(x.sum()), "faces_v8": int(y.sum()), "face_iou": round(int((x & y).sum()) / union, 4)}
    return {
        "available": True,
        "face_label_agreement": round(float((a == b).mean()), 4),
        "per_class": per_class,
        "unlabelled": [v0["summary"]["unlabelled_face_ratio"], v8["summary"]["unlabelled_face_ratio"]],
        "partner_filled": [v0["summary"]["partner_filled_ratio"], v8["summary"]["partner_filled_ratio"]],
        "cut_partner_consistency": [v0["summary"]["cut_partner_consistency"], v8["summary"]["cut_partner_consistency"]],
        "parts": [v0["summary"]["part_count"], v8["summary"]["part_count"]],
        "physical": [v0["summary"]["physical_count"], v8["summary"]["physical_count"]],
        "total_s": [v0["timings"].get("total"), v8["timings"].get("total")],
        "label_lift_s": [v0["timings"].get("label_lift"), v8["timings"].get("label_lift")],
    }


def torch_guard_records(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if "[torch_guard] restored" in line]


def write_torch_guard(records: list[str]) -> None:
    data: dict[str, Any] = {}
    if TORCH_GUARD.is_file():
        try:
            data = json.loads(TORCH_GUARD.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
    data["s10_parts"] = {"count": len(records), "records": records}
    TORCH_GUARD.write_text(json.dumps(data, indent=2), encoding="utf-8")


def markdown(report: dict) -> str:
    lines = ["# S10 part-analysis smoke", "", f"Run {report['started']} ({report['wall_s']} s total).", ""]
    for case, data in report["cases"].items():
        job = data["job"]
        lines += [f"## {case}: {job['id']} ({job.get('faces')} faces, generation {job.get('generation_s')} s)", "",
                  "| analysis | status | parts (phys/def/reg) | IoU | unlabelled | partner fill | cut consistency | total s | gates |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for entry in data["analyses"]:
            s = entry.get("summary", {})
            failed = [k for k, v in entry.get("gates", {}).items() if not v]
            lines.append(
                f"| {entry['name']} | {entry.get('result_status', entry['status'])} | {s.get('part_count')} "
                f"({s.get('physical_count')}/{s.get('deferred_count')}/{s.get('region_count')}) | "
                f"{s.get('camera_fit_iou')} | {s.get('unlabelled_face_ratio')} | {s.get('partner_filled_ratio')} | "
                f"{s.get('cut_partner_consistency')} | {entry.get('timings', {}).get('total')} | "
                f"{'ok' if not failed else 'FAIL ' + ', '.join(failed)} |")
        cmp_ = data.get("views_0_vs_8", {})
        if cmp_.get("available"):
            lines += ["", f"views 0 vs 8: face-label agreement {cmp_['face_label_agreement']}, label_lift "
                          f"{cmp_['label_lift_s']} s, total {cmp_['total_s']} s; per-class face IoU: "
                          + ", ".join(f"{k} {v['face_iou']}" for k, v in cmp_["per_class"].items())]
        fit = next((e.get("camera_fit") for e in data["analyses"] if e.get("camera_fit")), None)
        if fit:
            lines += ["", f"camera fit: {json.dumps({k: fit.get(k) for k in ('yaw', 'pitch', 'fov', 'iou', 'accepted')})}"
                          f"; search {json.dumps(fit.get('search'))}"]
        lines.append("")
    lines += [f"Gates: {'ALL PASS' if report['passed'] else 'FAILED'}", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--reuse-jobs", action="store_true", help="reuse completed jobs of an earlier run (jobs.json)")
    parser.add_argument("--cases", default="alice,anime")
    args = parser.parse_args()
    cases = [c.strip() for c in args.cases.split(",") if c.strip()]
    for case in cases:
        if case not in CASES or not CASES[case].is_file():
            raise SystemExit(f"unknown case or missing image: {case}")
    OUT.mkdir(parents=True, exist_ok=True)
    jobs_file = OUT / "jobs.json"
    known = json.loads(jobs_file.read_text(encoding="utf-8")) if args.reuse_jobs and jobs_file.is_file() else {}
    started = time.perf_counter()
    report: dict[str, Any] = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "runtime": str(RUNTIME), "cases": {}}
    backend = Backend()
    try:
        backend.wait_ready()
        print(f"backend ready on {backend.base}", flush=True)
        for case in cases:
            job = None
            if case in known:
                status, body, _ = backend.get(f"/api/v1/jobs/{known[case]}")
                job = body if status == 200 and body.get("status") == "completed" else None
            if job is None:
                job = create_job(backend, case, CASES[case])
                known[case] = job["id"]
                jobs_file.write_text(json.dumps(known, indent=2), encoding="utf-8")
            directory = job_dir(job["id"])
            hashes = {n: sha256(directory / n) for n in PROTECTED}
            result = job.get("result") or {}
            case_report: dict[str, Any] = {"job": {
                "id": job["id"], "faces": (result.get("stats") or {}).get("faces"),
                "generation_s": (result.get("timings") or {}).get("total"), "settings": job.get("settings"),
                "model_sha256": hashes["model.glb"]}, "analyses": []}
            for name, body in ANALYSES:
                entry = analyse(backend, case, job, name, body, hashes)
                case_report["analyses"].append(entry)
                print(f"  {case} {name}: {entry.get('result_status', entry['status'])} gates={entry.get('gates')}",
                      flush=True)
            by_name = {e["name"]: e for e in case_report["analyses"]}
            case_report["views_0_vs_8"] = compare_views(directory, by_name["semantic_v0"], by_name["semantic_v8"])
            report["cases"][case] = case_report
    finally:
        records = torch_guard_records(backend.log_text())
        backend.stop()
    write_torch_guard(records)
    report["torch_guard_records"] = len(records)
    report["wall_s"] = round(time.perf_counter() - started, 1)
    report["passed"] = all(all(e.get("gates", {}).values()) and e.get("gates")
                           for c in report["cases"].values() for e in c["analyses"])
    (OUT / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (OUT / "report.md").write_text(markdown(report), encoding="utf-8")
    print(markdown(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Refusal:
        raise
