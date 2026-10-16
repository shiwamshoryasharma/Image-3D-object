"""Helpers for the rig-step tests (real Blender subprocesses; artifacts in test/outputs/mesh_tuner/rig/).

Blender runs exactly like the backend runner (``--background --factory-startup --disable-autoexec --offline-mode
--python-exit-code 86``) with an isolated ``BLENDER_USER_RESOURCES`` and a per-run TEMP.
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

RIG_TEST_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = RIG_TEST_DIR.parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "backend" / "blender_scripts"
ENTRY_SCRIPT = SCRIPTS_DIR / "auto_mesh_tuner.py"
RIG_MODULE = SCRIPTS_DIR / "img3d_amt" / "rig.py"
HELPERS_DIR = RIG_TEST_DIR / "blender_helpers"
OUTPUT_ROOT = PROJECT_ROOT / "test" / "outputs" / "mesh_tuner" / "rig"
JOBS_DIR = PROJECT_ROOT / "backend" / "runtime" / "jobs"
BLENDER_52 = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
PYTHON_311 = Path(r"C:\Program Files\Blender Foundation\Blender 4.2\4.2\python\bin\python.exe")
SENTINEL = "@@AMT@@"
EXCLUDED_JOBS = frozenset({"job_269a864ac597"})  # excluded input (local policy): never open/process/render
TRELLIS_JOB = "job_046e19f0ca62"

# every other step reduced to (near) no-ops so the rig assertions do not depend on the geometry steps
MINIMAL_OPTIONS: dict[str, Any] = {
    "remove_inner_shell": False, "fill_holes": False, "remove_islands": False, "smoothing": 0,
    "refine_face": False, "eye_correction": False, "symmetry": "off", "refine_density": False,
    "subdivision": "off", "render_previews": False,
}
COLOR_BONES = "upper_arm,forearm,hand,finger,upper_twist,fore_twist"


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

    def step(self, step_id: str) -> dict[str, Any]:
        """Final (non-running) ``step`` event of ``step_id``."""
        finals = [e for e in self.of("step") if e["id"] == step_id and e["status"] != "running"]
        assert finals, f"no final step event for {step_id}"
        return finals[-1]

    @property
    def result(self) -> dict[str, Any] | None:
        res = self.of("result")
        return res[-1] if res else None

    def tagged(self, tag: str) -> list[dict[str, Any]]:
        """JSON payloads of ``@@<tag>@@{json}`` lines printed by a helper script."""
        marker = f"@@{tag}@@"
        rows = []
        for line in self.stdout.splitlines():
            i = line.find(marker)
            if i >= 0:
                rows.append(json.loads(line[i + len(marker):]))
        return rows

    def tail(self, n: int = 40) -> str:
        return "\n".join((self.stdout + "\n" + self.stderr).splitlines()[-n:])


def parse_events(stdout: str) -> list[dict[str, Any]]:
    events = []
    for line in stdout.splitlines():
        i = line.find(SENTINEL)
        if i >= 0:
            events.append(json.loads(line[i + len(SENTINEL):]))
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
    """``exe --background ... --python script -- args``; full log in outputs/mesh_tuner/rig/logs/<tag>.log."""
    cmd = [str(exe), "--background", "--factory-startup", "--disable-autoexec", "--offline-mode",
           "--python-exit-code", "86", "--python", str(script), "--", *[str(a) for a in args]]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env=blender_env(OUTPUT_ROOT / "tmp" / tag),
                          stdin=subprocess.DEVNULL, creationflags=flags, cwd=str(OUTPUT_ROOT))
    duration = time.perf_counter() - t0
    out = proc.stdout.decode("utf-8", errors="replace").replace("\r", "")
    err = proc.stderr.decode("utf-8", errors="replace").replace("\r", "")
    log_dir = OUTPUT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{tag}.log"
    log_path.write_text(f"$ {' '.join(cmd)}\n# rc={proc.returncode} {duration:.2f}s\n--- stdout\n{out}\n"
                        f"--- stderr\n{err}", encoding="utf-8")
    return BlenderRun(cmd, proc.returncode, out, err, duration, parse_events(out), log_path)


def fresh_dir(name: str) -> Path:
    """An empty run folder under outputs/mesh_tuner/rig/runs/ (previous contents removed)."""
    path = OUTPUT_ROOT / "runs" / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def run_tuner(input_path: Path, out_dir: Path, options: dict[str, Any], *, tag: str,
              timeout: float = 900.0) -> BlenderRun:
    """Full CLI pipeline via the real entry script (``--input/--output-dir/--options``)."""
    return run_blender(ENTRY_SCRIPT, ["--input", input_path, "--output-dir", out_dir, "--options",
                                      json.dumps(options)], tag=tag, timeout=timeout)


def inspect_rig(path: Path, *, tag: str, extra: list[Any] | None = None) -> dict[str, Any]:
    """``inspect_rig.py`` summary of a model (armatures, bones, pose, per-mesh weight stats, pose test)."""
    run = run_blender(HELPERS_DIR / "inspect_rig.py", ["--input", path, *(extra or [])], tag=tag, timeout=600)
    assert run.returncode == 0, run.tail()
    rows = run.tagged("RIGINFO")
    assert len(rows) == 1, run.tail()
    return rows[0]


def metric_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["key"]: m for m in report["metrics"]}


def check_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in report["validation"]["checks"]}


def rig_step(report: dict[str, Any]) -> dict[str, Any]:
    return next(s for s in report["steps"] if s["id"] == "rig_optimization")


def load_report(out_dir: Path) -> dict[str, Any]:
    return json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
