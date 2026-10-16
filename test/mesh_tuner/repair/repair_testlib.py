"""Helpers for the geometry-repair tests (real Blender 5.2 subprocesses; artifacts in test/outputs/mesh_tuner/repair).

* :func:`run_repair` - the real tuner CLI (``auto_mesh_tuner.py`` via ``blender_helpers/run_repair_only.py``) with
  only framework steps + ``geometry_repair`` active (or the whole pipeline with ``full=True``).
* :func:`compare` - ``blender_helpers/compare_models.py``: before/after renders, slices, depth maps, wall pairs.
* :func:`repair_details` - the ``geometry_repair`` step event (details) of a run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPAIR_TEST_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = REPAIR_TEST_DIR.parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "backend" / "blender_scripts"
HELPERS_DIR = REPAIR_TEST_DIR / "blender_helpers"
OUTPUT_ROOT = PROJECT_ROOT / "test" / "outputs" / "mesh_tuner" / "repair"
JOBS_DIR = PROJECT_ROOT / "backend" / "runtime" / "jobs"
SMOKE_GLB = PROJECT_ROOT / "test" / "outputs" / "engine_mechanics" / "job_smoke145706" / "model.glb"
BLENDER_52 = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
PYTHON_311 = Path(r"C:\Program Files\Blender Foundation\Blender 4.2\4.2\python\bin\python.exe")
SENTINEL = "@@AMT@@"
EXCLUDED_JOBS = frozenset({"job_269a864ac597"})  # excluded input (local policy): never open/process/render
REAL_JOBS = ["job_04535ae38930", "job_2496cdfeecc8", "job_a68b9057a882", "job_046e19f0ca62", "job_dd0e629ac377",
             "job_57425383ed74", "job_5b72e3768e6e"]
CHARACTER_JOBS = {"job_04535ae38930", "job_2496cdfeecc8", "job_a68b9057a882", "job_046e19f0ca62",
                  "job_dd0e629ac377"}


@dataclass
class BlenderRun:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    events: list[dict[str, Any]] = field(default_factory=list)

    def of(self, event: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("event") == event]

    def step(self, step_id: str) -> dict[str, Any] | None:
        done = [e for e in self.of("step") if e.get("id") == step_id and e.get("status") != "running"]
        return done[-1] if done else None

    def tail(self, n: int = 40) -> str:
        return "\n".join((self.stdout + "\n" + self.stderr).splitlines()[-n:])


def job_glb(job: str) -> Path:
    if job in EXCLUDED_JOBS:
        raise ValueError(f"{job} must never be processed")
    if job == "smoke":
        return SMOKE_GLB
    return JOBS_DIR / job / "model.glb"


def _env(tag: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith("PYTHON") and not k.upper().startswith("BLENDER_")}
    tmp = OUTPUT_ROOT / "tmp" / tag
    tmp.mkdir(parents=True, exist_ok=True)
    user = OUTPUT_ROOT / "blender_user"
    user.mkdir(parents=True, exist_ok=True)
    env.update(TEMP=str(tmp), TMP=str(tmp), BLENDER_USER_RESOURCES=str(user))
    return env


def run_blender(script: Path, args: list[str], *, tag: str, timeout: float = 1800.0,
                extra_env: dict[str, str] | None = None) -> BlenderRun:
    cmd = [str(BLENDER_52), "--background", "--factory-startup", "--disable-autoexec", "--offline-mode",
           "--python-exit-code", "86", "--python", str(script), "--", *[str(a) for a in args]]
    env = _env(tag)
    if extra_env:
        env.update(extra_env)
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL,
                          creationflags=flags, cwd=str(OUTPUT_ROOT))
    out = proc.stdout.decode("utf-8", errors="replace").replace("\r", "")
    err = proc.stderr.decode("utf-8", errors="replace").replace("\r", "")
    events = []
    for line in out.splitlines():
        i = line.find(SENTINEL)
        if i >= 0:
            try:
                events.append(json.loads(line[i + len(SENTINEL):]))
            except json.JSONDecodeError:
                pass
    log_dir = OUTPUT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{tag}.log").write_text(f"$ {' '.join(cmd)}\n# rc={proc.returncode}\n--- stdout\n{out}\n--- stderr\n{err}",
                                        encoding="utf-8")
    return BlenderRun(cmd, proc.returncode, out, err, time.perf_counter() - t0, events)


def run_repair(input_path: Path, out_dir: Path, *, options: dict[str, Any] | None = None, full: bool = False,
               tag: str, fresh: bool = True, extra_env: dict[str, str] | None = None) -> BlenderRun:
    """Run the real CLI (framework + repair only unless ``full``) on ``input_path`` into ``out_dir``."""
    if fresh and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = {"render_previews": False}
    opts.update(options or {})
    own = ["--scripts", str(SCRIPTS_DIR)] + (["--full"] if full else [])
    args = own + ["--", "--input", str(input_path), "--output-dir", str(out_dir), "--options", json.dumps(opts)]
    run = run_blender(HELPERS_DIR / "run_repair_only.py", args, tag=tag, extra_env=extra_env)
    (out_dir / "events.json").write_text(json.dumps(run.events, indent=1), encoding="utf-8")
    return run


def compare(original: Path, tuned: Path, out_dir: Path, *, tag: str, holes: list[dict[str, Any]] | None = None,
            renders: bool = True) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    args = ["--scripts", str(SCRIPTS_DIR), "--original", str(original), "--tuned", str(tuned), "--out", str(out_dir)]
    if holes:
        hp = out_dir / "filled_holes.json"
        hp.write_text(json.dumps(holes), encoding="utf-8")
        args += ["--holes", str(hp)]
    if not renders:
        args.append("--no-renders")
    run = run_blender(HELPERS_DIR / "compare_models.py", args, tag=tag)
    if run.returncode != 0:
        raise RuntimeError(f"compare_models failed (rc={run.returncode}):\n{run.tail()}")
    return json.loads((out_dir / "compare.json").read_text(encoding="utf-8"))


def repair_details(run: BlenderRun) -> dict[str, Any]:
    step = run.step("geometry_repair")
    return (step or {}).get("details") or {}


def report_metrics(out_dir: Path) -> dict[str, dict[str, Any]]:
    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    return {m["key"]: m for m in report["metrics"]}
