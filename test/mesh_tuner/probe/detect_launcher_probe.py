"""blender-launcher.exe vs blender.exe when driven from a pipe (output, exit code, process tree).

Run:  backend\\python\\python.exe -s test\\mesh_tuner\\probe\\detect_launcher_probe.py
"""
import json
import subprocess
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / "outputs" / "mesh_tuner" / "detect"
DIR = Path(r"C:\Program Files\Blender Foundation\Blender 5.2")
CREATE_NO_WINDOW = 0x08000000


def children_of(pid):
    ps = ("Get-CimInstance Win32_Process -Filter \"ParentProcessId=%d\" | "
          "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress") % pid
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                       creationflags=CREATE_NO_WINDOW, timeout=30)
    return r.stdout.strip()


res = {}
for exe in ("blender.exe", "blender-launcher.exe"):
    r = {}
    for name, args in {
        "version": ["--factory-startup", "--version"],
        "bg_expr_exit4": ["--background", "--factory-startup", "--python-expr",
                          "import sys; print('HELLO_FROM_PY', flush=True); sys.exit(4)"],
    }.items():
        t0 = time.perf_counter()
        p = subprocess.run([str(DIR / exe), *args], capture_output=True, timeout=120, stdin=subprocess.DEVNULL,
                           creationflags=CREATE_NO_WINDOW)
        r[name] = {"exit": p.returncode, "secs": round(time.perf_counter() - t0, 3),
                   "stdout_lines": p.stdout.decode("utf-8", "replace").splitlines()[:3],
                   "stderr_lines": p.stderr.decode("utf-8", "replace").splitlines()[:3]}
    # process tree while a background sleep runs
    p = subprocess.Popen([str(DIR / exe), "--background", "--factory-startup", "--python-expr",
                          "import time; time.sleep(6)"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    time.sleep(3)
    r["tree_while_running"] = {"root_pid": p.pid, "root_alive": p.poll() is None, "children": children_of(p.pid)}
    p.wait(60)
    r["tree_while_running"]["root_exit"] = p.returncode
    res[exe] = r
(OUT / "launcher_probe.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
print(json.dumps(res, indent=1))
