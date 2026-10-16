"""Helpers for the refine (face_refinement / topology_tuning) tests and validation runs.

Runs real Blender subprocesses exactly like the backend runner (``--background --factory-startup
--disable-autoexec --offline-mode --python-exit-code 86``) with an isolated ``BLENDER_USER_RESOURCES`` and per-run
TEMP under ``test/outputs/mesh_tuner/refine/``.
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

REFINE_TEST_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = REFINE_TEST_DIR.parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "backend" / "blender_scripts"
ENTRY_SCRIPT = SCRIPTS_DIR / "auto_mesh_tuner.py"
HELPERS_DIR = REFINE_TEST_DIR / "blender_helpers"
OUTPUT_ROOT = PROJECT_ROOT / "test" / "outputs" / "mesh_tuner" / "refine"
JOBS_DIR = PROJECT_ROOT / "backend" / "runtime" / "jobs"
BLENDER_52 = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
PYTHON_311 = Path(r"C:\Program Files\Blender Foundation\Blender 4.2\4.2\python\bin\python.exe")
SENTINEL = "@@AMT@@"
EXCLUDED_JOBS = frozenset({"job_269a864ac597"})  # excluded input (local policy): never open/process/render
VALIDATION_JOBS = ("job_04535ae38930", "job_2496cdfeecc8", "job_a68b9057a882", "job_046e19f0ca62",
                   "job_dd0e629ac377", "job_57425383ed74", "job_5b72e3768e6e")


@dataclass
class BlenderRun:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    events: list[dict[str, Any]] = field(default_factory=list)
    log_path: Path | None = None

    def of(self, event: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("event") == event]

    @property
    def result(self) -> dict[str, Any] | None:
        res = self.of("result")
        return res[-1] if res else None

    def step(self, step_id: str) -> dict[str, Any] | None:
        final = [e for e in self.of("step") if e.get("id") == step_id and e.get("status") != "running"]
        return final[-1] if final else None

    def tail(self, n: int = 40) -> str:
        return "\n".join((self.stdout + "\n" + self.stderr).splitlines()[-n:])


def parse_events(stdout: str) -> list[dict[str, Any]]:
    events = []
    for line in stdout.splitlines():
        i = line.find(SENTINEL)
        if i >= 0:
            try:
                events.append(json.loads(line[i + len(SENTINEL):]))
            except json.JSONDecodeError:
                pass
    return events


def blender_env(temp_dir: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith("PYTHON") and not k.upper().startswith("BLENDER_")}
    temp_dir.mkdir(parents=True, exist_ok=True)
    user = OUTPUT_ROOT / "blender_user"
    user.mkdir(parents=True, exist_ok=True)
    env.update(TEMP=str(temp_dir), TMP=str(temp_dir), BLENDER_USER_RESOURCES=str(user))
    return env


def run_blender(script: Path, args: list[Any], *, tag: str, timeout: float = 900.0,
                exe: Path = BLENDER_52) -> BlenderRun:
    """Run ``exe --background ... --python script -- args``; full log in ``outputs/.../refine/logs/<tag>.log``."""
    cmd = [str(exe), "--background", "--factory-startup", "--disable-autoexec", "--offline-mode",
           "--python-exit-code", "86", "--python", str(script), "--", *[str(a) for a in args]]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    tmp = OUTPUT_ROOT / "tmp" / tag
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env=blender_env(tmp), stdin=subprocess.DEVNULL,
                          creationflags=flags, cwd=str(OUTPUT_ROOT))
    duration = time.perf_counter() - t0
    shutil.rmtree(tmp, ignore_errors=True)
    out = proc.stdout.decode("utf-8", errors="replace").replace("\r", "")
    err = proc.stderr.decode("utf-8", errors="replace").replace("\r", "")
    log_dir = OUTPUT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{tag}.log"
    log_path.write_text(f"$ {' '.join(cmd)}\n# rc={proc.returncode} {duration:.2f}s\n--- stdout\n{out}\n"
                        f"--- stderr\n{err}", encoding="utf-8")
    return BlenderRun(cmd, proc.returncode, out, err, duration, parse_events(out), log_path)


def run_tuner(input_path: Path, out_dir: Path, options: dict[str, Any], *, tag: str, timeout: float = 1800.0,
              context: dict[str, Any] | None = None) -> BlenderRun:
    """Run the real entry script with a cfg.json (``out_dir`` is recreated)."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    cfg = {"schema": 1, "input": str(input_path), "output_dir": str(out_dir), "options": options,
           "outputs": {"model": "tuned.glb", "report": "report.json", "blend": None, "previews_dir": "previews"},
           "context": context or {"job_id": tag, "tuning_id": "tn_refine", "source": "trellis2"}}
    cfg_path = out_dir / "config.json"
    cfg_path.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    return run_blender(ENTRY_SCRIPT, ["--config", cfg_path], tag=tag, timeout=timeout)


def job_glb(job_id: str) -> Path:
    if job_id in EXCLUDED_JOBS:
        raise ValueError(f"{job_id} must never be processed")
    return JOBS_DIR / job_id / "model.glb"
