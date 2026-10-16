"""Debug (repair agent): render ONLY the faces near label cut edges (smooth cuts red, fold cuts blue) + a ghost."""
import os
import sys

sys.stdout.reconfigure(line_buffering=True)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", "backend", "blender_scripts")))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "blender_helpers")))
import numpy as np  # noqa: E402
import amt_render as R  # noqa: E402
from img3d_amt import io_mesh, repair  # noqa: E402
from img3d_amt.config import load_config  # noqa: E402
from img3d_amt.context import TunerContext  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:]
glb, out = argv[0], argv[1]
ctx = TunerContext(load_config(["--input", glb, "--output-dir", out]))
io_mesh.load_input(ctx)
obj = ctx.primary
arr = repair.MeshArrays.read(obj.data)
z = np.load(os.path.join(out, "vis.npz"))
vis = z["front"].astype(int) + z["back"]
lab = np.load(os.path.join(out, "plan.npz"))["label"]
seed = np.load(os.path.join(out, "plan.npz"))["seed"]
keep = lab > 0
f1, f2 = arr.manifold_face_pairs()
cut = keep[f1] != keep[f2]
dots = (arr.face_normals[f1] * arr.face_normals[f2]).sum(1)
mark = np.zeros(arr.n_faces, np.int8)
sm = cut & (dots > 0.5)
mark[f1[sm]] = 1
mark[f2[sm]] = 1
fo = cut & (dots <= 0.5)
mark[f1[fo]] = 2
mark[f2[fo]] = 2
# dilate 2 rings for visibility
for _ in range(2):
    m1 = mark[f1].copy()
    m2 = mark[f2].copy()
    upd1 = (mark[f2] == 0) & (m1 > 0)
    mark[f2[upd1]] = m1[upd1]
    upd2 = (mark[f1] == 0) & (m2 > 0)
    mark[f1[upd2]] = m2[upd2]
sel = mark > 0
col = np.zeros((arr.n_faces, 3))
col[mark == 1] = (0.95, 0.1, 0.1)
col[mark == 2] = (0.1, 0.3, 1.0)
sub = R.subset_object(obj, sel, "cuts")
R.set_face_colors(sub, col[sel])
R.setup_scene(1000)
R.shading("color")
for v in ("front", "back", "left"):
    R.render_view(os.path.join(out, f"cutfaces_{v}.png"), [sub], R.VIEWS[v])
# statistics of the two sides of smooth cuts
kf = np.where(keep[f1[sm]], f1[sm], f2[sm])
dfc = np.where(keep[f1[sm]], f2[sm], f1[sm])
print("smooth cuts", int(sm.sum()), "kept side vis>=3", int((vis[kf] >= 3).sum()), "kept seed", np.bincount(seed[kf] + 1, minlength=3).tolist(),
      "deleted side seed", np.bincount(seed[dfc] + 1, minlength=3).tolist(), "deleted side vis", np.bincount(np.minimum(vis[dfc], 3), minlength=4).tolist())
print("CUTFACES_DONE")
