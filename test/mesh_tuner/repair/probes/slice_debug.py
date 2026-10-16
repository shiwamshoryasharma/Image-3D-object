"""Debug (repair agent): zoomed slices coloured by visibility class + render of hidden-not-behind faces.

blender ... --python slice_debug.py -- <glb> <out_dir> <z_frac> <cx> <cy> <half> [tag]
"""
import os
import sys

sys.stdout.reconfigure(line_buffering=True)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", "backend", "blender_scripts")))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "blender_helpers")))

import numpy as np  # noqa: E402

import amt_render as R  # noqa: E402
from img3d_amt import io_mesh, meshdata  # noqa: E402
from img3d_amt.config import load_config  # noqa: E402
from img3d_amt.context import TunerContext  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:]
glb, out = argv[0], argv[1]
zf, cx, cy, half = (float(a) for a in argv[2:6])
tag = argv[6] if len(argv) > 6 else "z"
cfg = load_config(["--input", glb, "--output-dir", out])
ctx = TunerContext(cfg)
io_mesh.load_input(ctx)
obj = ctx.primary
z = np.load(os.path.join(out, "vis.npz"))
f, b, h = z["front"].astype(int), z["back"].astype(int), z["behind"].astype(int)
co = meshdata.get_positions(obj.data)
tris = meshdata.get_triangles(obj.data)
zmin, zmax = co[:, 2].min(), co[:, 2].max()
zval = zmin + zf * (zmax - zmin)
classes = {
    "front_only": ((f > 0) & (h == 0), (0.0, 0.0, 0.0)),
    "front_and_behind": ((f > 0) & (h > 0), (0.1, 0.3, 1.0)),
    "behind_hidden": ((f == 0) & (h > 0), (0.9, 0.1, 0.1)),
    "hidden_not_behind": ((f + b == 0) & (h == 0), (0.95, 0.6, 0.0)),
    "back_only": ((f == 0) & (b > 0) & (h == 0), (0.0, 0.8, 0.0)),
}
layers = []
for name, (mask, rgb) in classes.items():
    seg = R.slice_segments(co, tris[mask], 2, zval)
    layers.append((seg, rgb))
    print(name, int(mask.sum()), "segments", len(seg))
R.draw_slices(layers, 0, 1, (cx - half, cx + half, cy - half, cy + half), 1024, os.path.join(out, f"slicecls_{tag}.png"))
mask = (f + b == 0) & (h == 0)
sub = R.subset_object(obj, mask, "hnb")
R.setup_scene(900)
R.shading("matcap")
for v in ("front", "left"):
    R.render_view(os.path.join(out, f"hidden_not_behind_{v}.png"), [sub], R.VIEWS[v])
print("SLICE_DEBUG_DONE")
