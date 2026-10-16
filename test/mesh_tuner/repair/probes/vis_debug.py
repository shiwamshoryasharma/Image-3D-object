"""Debug (repair agent): run the real load step + visibility on a GLB and dump statistics / arrays.

blender --background --factory-startup --python-exit-code 86 --python vis_debug.py -- <glb> <out_dir> [--cpu]
"""
import json
import os
import sys
import time

sys.stdout.reconfigure(line_buffering=True)
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", "backend", "blender_scripts"))
sys.path.insert(0, SCRIPTS)

import numpy as np  # noqa: E402

from img3d_amt import io_mesh, meshdata, visibility  # noqa: E402
from img3d_amt.config import load_config  # noqa: E402
from img3d_amt.context import TunerContext, probe_gpu  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:]
glb, out = argv[0], argv[1]
use_gpu = "--cpu" not in argv
os.makedirs(out, exist_ok=True)
probe_gpu()
cfg = load_config(["--input", glb, "--output-dir", out])
ctx = TunerContext(cfg)
t = time.perf_counter()
io_mesh.load_input(ctx)
print("load", time.perf_counter() - t)
me = ctx.mesh
co = meshdata.get_positions(me)
tris = meshdata.get_triangles(me)
nrm = meshdata.triangle_normals(co, tris)
t = time.perf_counter()
res = visibility.compute_visibility(co, tris, nrm, use_gpu=use_gpu)
print("visibility", time.perf_counter() - t)
s = res.summary()
print(json.dumps(s, indent=1))
np.savez_compressed(os.path.join(out, "vis.npz"), front=res.front, back=res.back, behind=res.behind)
f, b, h = res.front.astype(int), res.back.astype(int), res.behind.astype(int)
T = len(f)
print("front hist", np.bincount(np.minimum(f, 60))[:61].tolist())
print("behind hist", np.bincount(np.minimum(h, 60))[:61].tolist())
for ratio in (0.1, 0.2, 0.3, 0.5):
    cand = (h >= 2) & (f <= ratio * h)
    print(f"ratio {ratio}: candidates {cand.sum()} ({cand.mean():.3f}) of which front>0: {(cand & (f > 0)).sum()}")
print("hidden", (f + b == 0).sum(), "behind>0 & front==0", ((h > 0) & (f == 0)).sum(), "front>0&behind>0", ((f > 0) & (h > 0)).sum())
# joint table of f vs h buckets
fb = np.digitize(f, [1, 3, 6, 11, 21])
hb = np.digitize(h, [1, 3, 6, 11, 21])
tab = np.zeros((6, 6), int)
np.add.at(tab, (fb, hb), 1)
print("rows front buckets [0,1-2,3-5,6-10,11-20,21+], cols behind buckets")
print(tab)
