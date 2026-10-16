"""Debug (repair agent): where does the shell classification create NEW boundaries? (uses vis.npz from plan_debug)

blender ... --python seam_debug.py -- <glb> <out_dir>
Colours kept faces touching a new boundary edge by the visibility of the deleted neighbour region.
"""
import os
import sys

sys.stdout.reconfigure(line_buffering=True)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", "backend", "blender_scripts")))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "blender_helpers")))

import numpy as np  # noqa: E402

import amt_render as R  # noqa: E402
from img3d_amt import io_mesh, meshdata, repair  # noqa: E402
from img3d_amt.config import load_config  # noqa: E402
from img3d_amt.context import TunerContext  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:]
glb, out = argv[0], argv[1]
cfg = load_config(["--input", glb, "--output-dir", out])
ctx = TunerContext(cfg)
io_mesh.load_input(ctx)
obj = ctx.primary
me = obj.data
z = np.load(os.path.join(out, "vis.npz"))
f, b, h, partner = z["front"], z["back"], z["behind"], z["partner"]
arr = repair.MeshArrays.read(me)
f1, f2 = arr.manifold_face_pairs()
plan = repair.classify_shell(f, b, h, partner, f1, f2, shell=True)
print("plan stats", plan.stats)
lab = plan.label
keep = lab > 0
vis = f.astype(int) + b
# new boundary: manifold edges (both faces) with one kept and one deleted face
cut = keep[f1] != keep[f2]
kf = np.where(keep[f1[cut]], f1[cut], f2[cut])
df = np.where(keep[f1[cut]], f2[cut], f1[cut])
print("cut edges", int(cut.sum()))
# classify cut edges: kept face visible? deleted face seed?
kvis = vis[kf] >= 3
dseed = plan.seed[df]
print("kept side visible>=3:", int(kvis.sum()), " kept side weak:", int((~kvis).sum()))
print("deleted side seed -1:", int((dseed < 0).sum()), " seed 0:", int((dseed == 0).sum()), " seed +1:", int((dseed > 0).sum()))
# deleted regions: components of deleted faces over manifold edges; size + cut length
dl = ~keep
m = dl[f1] & dl[f2]
comp = meshdata.connected_components(arr.n_faces, f1[m], f2[m])
comp_d = comp[dl]
uniq, inv, cnt = np.unique(comp_d, return_inverse=True, return_counts=True)
cut_per = np.bincount(np.searchsorted(uniq, comp[df]), minlength=len(uniq))
order = np.argsort(-cnt)
print("deleted regions", len(uniq), "largest sizes", cnt[order[:8]].tolist(), "their cuts", cut_per[order[:8]].tolist())
smallish = cnt < 2000
print("regions <2000 faces:", int(smallish.sum()), "faces", int(cnt[smallish].sum()), "cut edges", int(cut_per[smallish].sum()))
# kept regions
km = keep[f1] & keep[f2]
kcomp = meshdata.connected_components(arr.n_faces, f1[km], f2[km])
ku, kcnt = np.unique(kcomp[keep], return_counts=True)
print("kept regions", len(ku), "largest", np.sort(kcnt)[::-1][:8].tolist(), "regions < 50 faces", int((kcnt < 50).sum()))
col = np.full((arr.n_faces, 3), 0.75)
touch = np.zeros(arr.n_faces, bool)
touch[kf] = True
col[touch & (vis >= 3)] = (0.9, 0.1, 0.1)
col[touch & (vis < 3)] = (1.0, 0.6, 0.0)
R.set_face_colors(obj, col)
kept_obj = R.subset_object(obj, keep, "kept")
R.set_face_colors(kept_obj, col[keep])
R.setup_scene(900)
R.shading("color")
for v in ("front", "back", "left", "below_front", "back_three_quarter"):
    R.render_view(os.path.join(out, f"seam_{v}.png"), [kept_obj], R.VIEWS[v])
np.savez_compressed(os.path.join(out, "plan.npz"), label=lab, seed=plan.seed)
print("SEAM_DEBUG_DONE")
