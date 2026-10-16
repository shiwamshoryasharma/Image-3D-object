"""Debug (repair agent): smooth vs fold cut edges per deleted region (uses vis.npz + plan.npz)."""
import os
import sys

sys.stdout.reconfigure(line_buffering=True)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", "backend", "blender_scripts")))
import numpy as np  # noqa: E402
from img3d_amt import io_mesh, meshdata, repair  # noqa: E402
from img3d_amt.config import load_config  # noqa: E402
from img3d_amt.context import TunerContext  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:]
glb, out = argv[0], argv[1]
ctx = TunerContext(load_config(["--input", glb, "--output-dir", out]))
io_mesh.load_input(ctx)
arr = repair.MeshArrays.read(ctx.mesh)
p = np.load(os.path.join(out, "plan.npz"))
lab = p["label"]
keep = lab > 0
f1, f2 = arr.manifold_face_pairs()
cut = keep[f1] != keep[f2]
dots = (arr.face_normals[f1[cut]] * arr.face_normals[f2[cut]]).sum(1)
print("cut edges", int(cut.sum()), "dot hist [-1,-.5,0,.5,.8,1]:", np.histogram(dots, [-1.01, -0.5, 0, 0.5, 0.8, 1.01])[0].tolist())
d = ~keep
m = d[f1] & d[f2]
comp = meshdata.connected_components(arr.n_faces, f1[m], f2[m])
df = np.where(d[f1[cut]], f1[cut], f2[cut])
n = int(comp.max()) + 1
size = np.bincount(comp[d], minlength=n)
cutn = np.bincount(comp[df], minlength=n)
smooth = np.bincount(comp[df], weights=(dots > 0.5).astype(float), minlength=n)
reg = np.flatnonzero(size > 0)
frac = smooth[reg] / np.maximum(cutn[reg], 1)
for lo, hi in ((1, 10), (10, 100), (100, 1000), (1000, 10000), (10000, 10**9)):
    s = (size[reg] >= lo) & (size[reg] < hi)
    print(f"del regions size [{lo},{hi}): n={int(s.sum())} faces={int(size[reg][s].sum())} cut={int(cutn[reg][s].sum())} "
          f"smooth_frac median={float(np.median(frac[s])) if s.any() else None:.3} >0.5: {int((frac[s] > 0.5).sum())}")
