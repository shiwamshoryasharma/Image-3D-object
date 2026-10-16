"""Test-only Blender-side script for the launcher process-runner tests (runs INSIDE Blender's Python).

Usage: blender -b --python amt_probe.py -- <mode>
Modes: ok | exit3 | raise | error_event | spawn
"""

import json
import os
import subprocess
import sys
import tempfile
import time

import bpy

sys.stdout.reconfigure(line_buffering=True)
SENTINEL = "@@AMT@@"
ARGS = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
MODE = ARGS[0] if ARGS else "ok"


def emit(obj):
    sys.stdout.write(SENTINEL + json.dumps(obj, ensure_ascii=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()


emit({
    "event": "hello",
    "protocol": 1,
    "script_version": "test",
    "blender_version": bpy.app.version_string,
    "python": sys.version.split()[0],
    "gpu": None,
    "pid": os.getpid(),
})

if MODE == "ok":
    sys.stdout.write("noise-without-newline ")  # the next protocol line gets glued onto this text
    emit({"event": "step", "id": "load", "status": "running", "message": "Loading ✓ é 日本",
          "progress": 0.0})
    print("plain non-ascii stdout: é ✓ 日本 Ω")
    print("stderr non-ascii: é ✓ 日本 Ω", file=sys.stderr, flush=True)
    blend = os.path.join(tempfile.gettempdir(), "amt_probe.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend)  # C-level "Info: Saved ..." (block-buffered, interleaves)
    env = {k: v for k, v in os.environ.items() if k.upper().startswith(("PYTHON", "BLENDER_"))}
    emit({
        "event": "log",
        "level": "info",
        "message": "env",
        "tempdir": tempfile.gettempdir(),
        "env_temp": os.environ.get("TEMP"),
        "env_tmp": os.environ.get("TMP"),
        "bpy_tempdir": bpy.app.tempdir,
        "user_resources": bpy.utils.resource_path("USER"),
        "filtered_env": env,
        "argv": ARGS,
        "cwd": os.getcwd(),
    })
    emit({"event": "step", "id": "load", "status": "done", "message": "ok", "duration_s": 0.0, "progress": 1.0,
          "details": {}})
    emit({"event": "result", "status": "completed", "model": "tuned.glb", "report": "report.json", "blend": None,
          "previews": [], "metrics": [], "warnings": [], "validation": {"passed": True, "checks": []}})
elif MODE == "exit3":
    sys.exit(3)
elif MODE == "raise":
    raise RuntimeError("boom ✓ é")
elif MODE == "error_event":
    emit({"event": "error", "code": "input_unreadable", "message": "Could not read input", "step": "load",
          "traceback": None})
elif MODE == "spawn":
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    emit({"event": "log", "level": "info", "message": "spawned", "child_pid": child.pid, "pid": os.getpid()})
    time.sleep(120)
