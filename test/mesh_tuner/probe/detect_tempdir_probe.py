"""Where does Blender put its session temp dir, does TEMP override it, and does a job-kill leak it?

Run:  backend\\python\\python.exe -s test\\mesh_tuner\\probe\\detect_tempdir_probe.py
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # embedded python is isolated: script dir is not on sys.path
from jobobject_probe import KillOnCloseJob, popen_in_job, start_reader, wait_for_line  # noqa: E402

OUT = HERE.parents[1] / "outputs" / "mesh_tuner" / "detect" / "tempdir"
OUT.mkdir(parents=True, exist_ok=True)
BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
EXPR = ("import bpy, os, time, sys; print('TEMPDIR', bpy.app.tempdir, os.environ.get('TEMP'), flush=True); "
        "time.sleep(float(sys.argv[-1]))")
res = {}

# 1) normal exit with TEMP overridden -> is the session dir created there and removed at exit?
env = dict(os.environ, TEMP=str(OUT), TMP=str(OUT))
p = subprocess.run([BLENDER, "-b", "--factory-startup", "--python-expr", EXPR, "--", "0"], capture_output=True,
                   env=env, creationflags=0x08000000)
line = next((ln for ln in p.stdout.decode().splitlines() if ln.startswith("TEMPDIR")), None)
res["normal_exit"] = {"exit": p.returncode, "line": line, "leftover": sorted(os.listdir(OUT))}

# 2) killed via job -> leftover?
job = KillOnCloseJob()
p = popen_in_job([BLENDER, "-b", "--factory-startup", "--python-expr", EXPR, "--", "60"], job,
                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env)
sink = []
start_reader(p, sink)
line = wait_for_line(sink, "TEMPDIR")
job.terminate(1)
p.wait(10)
job.close()
time.sleep(0.3)
res["job_killed"] = {"pid": p.pid, "line": line, "leftover": sorted(os.listdir(OUT))}

# 3) default TEMP (no override) -> location
p = subprocess.run([BLENDER, "-b", "--factory-startup", "--python-expr", EXPR, "--", "0"], capture_output=True,
                   creationflags=0x08000000)
res["default_temp"] = next((ln for ln in p.stdout.decode().splitlines() if ln.startswith("TEMPDIR")), None)
print(json.dumps(res, indent=1))
