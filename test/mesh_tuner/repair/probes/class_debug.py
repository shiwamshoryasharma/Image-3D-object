"""Debug (repair agent): colour faces by visibility class and render; slice plots of kept/removed walls.

blender ... --python class_debug.py -- <glb> <out_dir> [ratio]
Needs <out_dir>/vis.npz from vis_debug.py.
"""
import os
import sys

sys.stdout.reconfigure(line_buffering=True)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", "backend", "blender_scripts")))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "blender_helpers")))

import numpy as np  # noqa: E402

import amt_render as R  # noqa: E402
from img3d_amt import io_mesh  # noqa: E402
from img3d_amt.config import load_config  # noqa: E402
from img3d_amt.context import TunerContext  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:]
glb, out = argv[0], argv[1]
ratio = float(argv[2]) if len(argv) > 2 else 0.2
cfg = load_config(["--input", glb, "--output-dir", out])
ctx = TunerContext(cfg)
io_mesh.load_input(ctx)
obj = ctx.primary
z = np.load(os.path.join(out, "vis.npz"))
f, b, h = z["front"].astype(int), z["back"].astype(int), z["behind"].astype(int)
cand = (h >= 2) & (f <= ratio * h)
both = (f > 0) & (h > 0) & ~cand
hidden = (f + b == 0) & ~cand
col = np.full((len(f), 3), 0.8)
col[cand] = (0.9, 0.15, 0.1)
col[both] = (0.2, 0.4, 0.95)
col[hidden] = (0.95, 0.85, 0.1)
R.set_face_colors(obj, col)
R.setup_scene(900)
R.shading("color")
for v in ("front", "back", "left", "below_front"):
    R.render_view(os.path.join(out, f"cls_{v}.png"), [obj], R.VIEWS[v])
# kept-only and removed-only renders
kept = R.subset_object(obj, ~cand, "kept")
rem = R.subset_object(obj, cand, "removed")
R.shading("matcap", backface=True)
for v in ("front", "back", "below_front"):
    R.render_view(os.path.join(out, f"kept_{v}_culled.png"), [kept], R.VIEWS[v])
R.shading("matcap")
for v in ("front", "back"):
    R.render_view(os.path.join(out, f"removed_{v}.png"), [rem], R.VIEWS[v])
    R.render_view(os.path.join(out, f"orig_{v}.png"), [obj], R.VIEWS[v])
# slices: head + mid torso, original (grey) vs kept (black) vs removed (red)
co, tv = R.mesh_arrays(obj.data)
hc, hs = R.head_center(co)
zmin, zmax = co[:, 2].min(), co[:, 2].max()
for name, zval, win in (("head", hc[2], (hc[0] - 0.6 * hs, hc[0] + 0.6 * hs, hc[1] - 0.6 * hs, hc[1] + 0.6 * hs)),
                        ("mid", zmin + 0.6 * (zmax - zmin), (co[:, 0].min(), co[:, 0].max(), co[:, 1].min(), co[:, 1].max()))):
    ck, tk = R.mesh_arrays(kept.data)
    cr, tr = R.mesh_arrays(rem.data)
    R.draw_slices([(R.slice_segments(cr, tr, 2, zval), (0.9, 0.1, 0.1)), (R.slice_segments(ck, tk, 2, zval), (0.0, 0.0, 0.0))],
                  0, 1, win, 1024, os.path.join(out, f"slice_{name}.png"))
    # zoomed window on the front-most point
    seg = R.slice_segments(ck, tk, 2, zval)
    if len(seg):
        mid = seg.mean(1)
        near = np.abs(mid[:, 0] - hc[0]) < 0.1 * hs
        k = int(np.argmin(np.where(near, mid[:, 1], 9)))
        cu, cv = mid[k, 0], mid[k, 1]
        w = 0.012
        R.draw_slices([(R.slice_segments(cr, tr, 2, zval), (0.9, 0.1, 0.1)), (seg, (0.0, 0.0, 0.0))], 0, 1,
                      (cu - w, cu + w, cv - w, cv + w), 1024, os.path.join(out, f"slice_{name}_zoom.png"))
print("CLASS_DEBUG_DONE", int(cand.sum()), int(both.sum()), int(hidden.sum()))
