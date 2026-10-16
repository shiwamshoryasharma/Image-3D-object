"""Debug (repair agent): visibility + wall pairing + shell classification; class renders + kept renders + slices.

blender ... --python plan_debug.py -- <glb> <out_dir> [keep_min]
"""
import os
import sys
import time

sys.stdout.reconfigure(line_buffering=True)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", "backend", "blender_scripts")))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "blender_helpers")))

import numpy as np  # noqa: E402

import amt_render as R  # noqa: E402
from img3d_amt import io_mesh, meshdata, repair, visibility  # noqa: E402
from img3d_amt.config import load_config  # noqa: E402
from img3d_amt.context import TunerContext, probe_gpu  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:]
glb, out = argv[0], argv[1]
keep_min = int(argv[2]) if len(argv) > 2 else 3
os.makedirs(out, exist_ok=True)
probe_gpu()
cfg = load_config(["--input", glb, "--output-dir", out])
ctx = TunerContext(cfg)
io_mesh.load_input(ctx)
obj = ctx.primary
me = obj.data
co = meshdata.get_positions(me)
tris = meshdata.get_triangles(me)
nrm = meshdata.triangle_normals(co, tris)
t = time.perf_counter()
vis = visibility.compute_visibility(co, tris, nrm)
print("visibility", round(time.perf_counter() - t, 2), vis.summary())
f, b, h = vis.front, vis.back, vis.behind
partner = vis.partner.copy()
seed = repair.shell_seeds(f, b, h, keep_min=keep_min, shell=vis.is_shell)
need = np.flatnonzero((seed == 0) & (partner < 0))
t = time.perf_counter()
p2, d2 = visibility.pair_by_rays(co, tris, nrm, need, vis.t_max * 1.5)
partner[need] = np.where(p2 >= 0, p2, partner[need])
print("pair_by_rays", len(need), "found", int((p2 >= 0).sum()), round(time.perf_counter() - t, 2))
np.savez_compressed(os.path.join(out, "vis.npz"), front=f, back=b, behind=h, partner=partner)
t = time.perf_counter()
ef = meshdata.edge_faces(me)
_, f1, f2, _, _ = ef.manifold_pairs()
plan = repair.classify_shell(f, b, h, partner, f1, f2, shell=vis.is_shell, keep_min=keep_min)
print("classify", round(time.perf_counter() - t, 2), plan.stats)
lab = plan.label
col = np.full((len(lab), 3), 0.75)
col[lab < 0] = (0.9, 0.15, 0.1)
col[(lab < 0) & ((f.astype(int) + b) > 0)] = (1.0, 0.0, 1.0)  # visible but deleted (magenta)
col[lab == 0] = (0.95, 0.85, 0.1)
R.set_face_colors(obj, col)
R.setup_scene(900)
R.shading("color")
for v in ("front", "back", "three_quarter", "below_front"):
    R.render_view(os.path.join(out, f"plan_cls_{v}.png"), [obj], R.VIEWS[v])
kept = R.subset_object(obj, lab > 0, "kept")
R.shading("matcap", backface=True)
for v in ("front", "back", "back_three_quarter", "below_front"):
    R.render_view(os.path.join(out, f"plan_kept_{v}_culled.png"), [kept], R.VIEWS[v])
R.shading("matcap", backface=False)
R.render_view(os.path.join(out, "plan_orig_back.png"), [obj], R.VIEWS["back"])
co = meshdata.get_positions(me)
tris = meshdata.get_triangles(me)
ck, tk = R.mesh_arrays(kept.data)
zmin, zmax = co[:, 2].min(), co[:, 2].max()
hc, hs = R.head_center(co)
for name, zf, win in (("mid", 0.6, (-0.09, 0.09, -0.09, 0.09)), ("chest", 0.68, (-0.09, 0.09, -0.09, 0.09)),
                      ("head", (hc[2] - zmin) / (zmax - zmin), (hc[0] - 0.6 * hs, hc[0] + 0.6 * hs, hc[1] - 0.6 * hs, hc[1] + 0.6 * hs))):
    zval = zmin + zf * (zmax - zmin)
    layers = [(R.slice_segments(co, tris[lab < 0], 2, zval), (0.9, 0.1, 0.1)),
              (R.slice_segments(co, tris[lab == 0], 2, zval), (0.95, 0.6, 0.0)),
              (R.slice_segments(ck, tk, 2, zval), (0.0, 0.0, 0.0))]
    R.draw_slices(layers, 0, 1, win, 1024, os.path.join(out, f"plan_slice_{name}.png"))
print("PLAN_DEBUG_DONE")
