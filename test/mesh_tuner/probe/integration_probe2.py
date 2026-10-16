"""Probe: Blender --python script semantics relevant to backend\\blender_scripts (sys.path, __file__, exit codes)
and the full ``--version`` text. Run: backend\\python\\python.exe -s test\\mesh_tuner\\probe\\integration_probe2.py
Output: test\\outputs\\mesh_tuner\\integration\\integration_probe2.json"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / "outputs" / "mesh_tuner" / "integration"
OUT.mkdir(parents=True, exist_ok=True)
EXE = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
SCRIPT = HERE / "integration" / "inside_paths.py"


def run(args: list[str]) -> dict:
    started = time.perf_counter()
    proc = subprocess.run([EXE, *args], capture_output=True, timeout=180, creationflags=subprocess.CREATE_NO_WINDOW,
                          cwd=str(OUT))
    out = proc.stdout.decode("utf-8", "replace").splitlines()
    payload = next((json.loads(l[8:]) for l in out if l.startswith("@@IMG3D ")), None)
    return {"rc": proc.returncode, "seconds": round(time.perf_counter() - started, 3), "payload": payload,
            "stdout_tail": out[-8:], "stderr_tail": proc.stderr.decode("utf-8", "replace").splitlines()[-8:]}


base = ["--background", "--factory-startup"]
results = {
    "version_full": run(["--version"]),
    "ok": run([*base, "--python-exit-code", "1", "--python", str(SCRIPT), "--", "--a", "1"]),
    "raise_with_exit_code_flag": run([*base, "--python-exit-code", "1", "--python", str(SCRIPT), "--", "--raise"]),
    "raise_without_flag": run([*base, "--python", str(SCRIPT), "--", "--raise"]),
    "sys_exit_3": run([*base, "--python-exit-code", "1", "--python", str(SCRIPT), "--", "--exit3"]),
}
(OUT / "integration_probe2.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(json.dumps(results, indent=2))
