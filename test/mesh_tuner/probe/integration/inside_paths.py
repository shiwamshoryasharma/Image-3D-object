"""Runs INSIDE Blender (probe only): reports __file__, sys.path[0:4], whether the script dir is on sys.path,
then optionally raises to test exit codes. Usage: blender -b --factory-startup [--python-exit-code 1]
--python inside_paths.py -- [--raise]"""
import json
import os
import sys

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
here = os.path.dirname(os.path.abspath(__file__))
print("@@IMG3D " + json.dumps({
    "file": __file__,
    "name": __name__,
    "script_dir_on_sys_path": any(os.path.normcase(os.path.abspath(p or ".")) == os.path.normcase(here) for p in sys.path),
    "sys_path_head": sys.path[:4],
    "cwd": os.getcwd(),
    "argv": argv,
}), flush=True)
if "--raise" in argv:
    raise RuntimeError("probe failure")
if "--exit3" in argv:
    sys.exit(3)
