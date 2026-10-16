"""Helpers for the Blender-side Auto Mesh Tuner framework tests (imported by conftest.py and the test modules).

Real Blender subprocesses run ``backend/blender_scripts/auto_mesh_tuner.py`` exactly like the backend runner does
(``--background --factory-startup --disable-autoexec --offline-mode --python-exit-code 86``), with an isolated
``BLENDER_USER_RESOURCES`` and per-run TEMP under ``test/outputs/mesh_tuner/framework/``.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FRAMEWORK_TEST_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = FRAMEWORK_TEST_DIR.parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "backend" / "blender_scripts"
ENTRY_SCRIPT = SCRIPTS_DIR / "auto_mesh_tuner.py"
HELPERS_DIR = FRAMEWORK_TEST_DIR / "blender_helpers"
OUTPUT_ROOT = PROJECT_ROOT / "test" / "outputs" / "mesh_tuner" / "framework"
JOBS_DIR = PROJECT_ROOT / "backend" / "runtime" / "jobs"
BLENDER_52 = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
BLENDER_42 = Path(r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe")
PYTHON_311 = Path(r"C:\Program Files\Blender Foundation\Blender 4.2\4.2\python\bin\python.exe")
SENTINEL = "@@AMT@@"
EXCLUDED_JOBS = frozenset({"job_269a864ac597"})  # excluded input (local policy): never open/process/render


@dataclass
class BlenderRun:
    """Outcome of one Blender subprocess."""

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

    @property
    def errors(self) -> list[dict[str, Any]]:
        return self.of("error")

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


def run_blender(exe: Path, script: Path, args: list[str], *, tag: str, timeout: float = 900.0) -> BlenderRun:
    """Run ``exe --background ... --python script -- args`` and capture everything (log: outputs/logs/<tag>.log)."""
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
    log_path.write_text(f"$ {' '.join(cmd)}\n# rc={proc.returncode} {duration:.2f}s\n--- stdout\n{out}\n--- stderr\n{err}",
                        encoding="utf-8")
    return BlenderRun(cmd, proc.returncode, out, err, duration, parse_events(out), log_path)


def read_glb_json(path: Path) -> dict[str, Any]:
    with open(path, "rb") as fh:
        magic, _version, _length = struct.unpack("<4sII", fh.read(12))
        assert magic == b"glTF", f"{path} is not a GLB"
        clen, ctype = struct.unpack("<II", fh.read(8))
        assert ctype == 0x4E4F534A
        return json.loads(fh.read(clen))


def write_glb_with_json(src: Path, dst: Path, document: dict[str, Any]) -> Path:
    """Copy GLB ``src`` to ``dst`` with its JSON chunk replaced by ``document`` (BIN chunk kept)."""
    data = src.read_bytes()
    clen, _ctype = struct.unpack_from("<II", data, 12)
    rest = data[20 + clen:]
    payload = json.dumps(document, separators=(",", ":")).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    header = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(payload) + len(rest))
    dst.write_bytes(header + struct.pack("<II", len(payload), 0x4E4F534A) + payload + rest)
    return dst


def write_config(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return path


def fresh_dir(name: str) -> Path:
    """An empty run folder under the framework output root (previous contents removed)."""
    path = OUTPUT_ROOT / "runs" / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path
