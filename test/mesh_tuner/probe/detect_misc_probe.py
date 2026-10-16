"""Misc runner facts: line-buffer fix inside Blender, BLENDER_USER_* env isolation, DETACHED_PROCESS vs CREATE_NO_WINDOW.

Run:  backend\\python\\python.exe -s test\\mesh_tuner\\probe\\detect_misc_probe.py
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from jobobject_probe import descendants  # noqa: E402

OUT = HERE.parents[1] / "outputs" / "mesh_tuner" / "detect" / "misc"
OUT.mkdir(parents=True, exist_ok=True)
BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
BASE = [BLENDER, "--background", "--factory-startup", "--disable-autoexec"]
res = {}

# 1) sys.stdout.reconfigure(line_buffering=True) at top of the Blender-side script -> prompt lines?
expr = ("import sys, time; sys.stdout.reconfigure(line_buffering=True)\n"
        "for i in range(4):\n    print('L', i); time.sleep(0.5)\n")
t0 = time.perf_counter()
p = subprocess.Popen(BASE + ["--python-expr", expr], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, creationflags=0x08000000, bufsize=0)
arr = [(round(time.perf_counter() - t0, 3), ln.decode().rstrip()) for ln in iter(p.stdout.readline, b"")]
p.wait()
res["reconfigure_line_buffering"] = arr

# 2) text-mode reading from the host: encoding + newline translation
p = subprocess.run(BASE + ["--python-expr", "print('é✓日本')"], capture_output=True, text=True, encoding="utf-8",
                   errors="replace", creationflags=0x08000000)
res["host_text_mode_line"] = [ln for ln in p.stdout.splitlines() if "日本" in ln]

# 3) user-resource isolation via env (not via flags)
ur = OUT / "user_resources"
env = dict(os.environ, BLENDER_USER_RESOURCES=str(ur))
p = subprocess.run(BASE + ["--python-expr",
                           "import bpy; print('USER=' + bpy.utils.resource_path('USER')); "
                           "print('CFG=' + str(bpy.utils.user_resource('CONFIG'))); "
                           "print('SCRIPTS=' + str(bpy.utils.user_resource('SCRIPTS')))"],
                   capture_output=True, text=True, encoding="utf-8", env=env, creationflags=0x08000000)
res["BLENDER_USER_RESOURCES"] = [ln for ln in p.stdout.splitlines() if ln.startswith(("USER=", "CFG=", "SCRIPTS="))]
res["BLENDER_USER_RESOURCES_dir_created"] = ur.exists()

# 4) DETACHED_PROCESS vs CREATE_NO_WINDOW: conhost child? output still captured?
for name, flags in (("CREATE_NO_WINDOW", 0x08000000), ("DETACHED_PROCESS", 0x00000008)):
    p = subprocess.Popen(BASE + ["--python-expr", "import time; print('UP', flush=True); time.sleep(3)"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         creationflags=flags)
    first = p.stdout.readline().decode().strip()
    time.sleep(0.5)
    kids = descendants(p.pid)
    p.communicate(timeout=30)
    res[name] = {"first_line": first, "children": kids, "exit": p.returncode}

print(json.dumps(res, indent=1, ensure_ascii=False))
(OUT / "misc_probe.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
