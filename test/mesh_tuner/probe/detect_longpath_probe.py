"""Does Blender (not longPathAware) handle >260-char paths, plain vs \\\\?\\ prefixed?

Run:  backend\\python\\python.exe -s test\\mesh_tuner\\probe\\detect_longpath_probe.py
"""
import json
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / "outputs" / "mesh_tuner" / "detect"
base = str(OUT / "longpath")
d = os.path.join(base, *[f"segment_{i:02d}_abcdefghijklmnopqrstuvwxyz" for i in range(8)])
os.makedirs("\\\\?\\" + d, exist_ok=True)
f = os.path.join(d, "tri.obj")
with open("\\\\?\\" + f, "w") as fh:
    fh.write("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
print("path len", len(f), "| embedded py314 os.path.exists(plain) =", os.path.exists(f))
for exe in [r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe"]:
    p = subprocess.run([exe, "--background", "--factory-startup", "--python-exit-code", "1", "--python",
                        str(HERE / "blender_side" / "longpath_check.py"), "--", f],
                       capture_output=True, creationflags=0x08000000)
    out = p.stdout.decode("utf-8", "replace")
    line = [ln[ln.index("LP_JSON"):] for ln in out.splitlines() if "LP_JSON " in ln]
    print("==", exe, "exit", p.returncode)
    print(json.dumps(json.loads(line[0][8:]), indent=1) if line else
          out[-1500:] + p.stderr.decode("utf-8", "replace")[-1500:])
    errs = [ln for ln in (out + p.stderr.decode("utf-8", "replace")).splitlines()
            if "rror" in ln or "annot" in ln]
    print("error lines:", errs[:8])
