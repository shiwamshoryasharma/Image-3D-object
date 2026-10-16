"""Debug (repair agent): cut-away renders (back half seen from the front) coloured by label + cut edges."""
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
y0 = float(argv[2]) if len(argv) > 2 else 0.0
zlo = float(argv[3]) if len(argv) > 3 else -1.0
zhi = float(argv[4]) if len(argv) > 4 else 1.0
tag = argv[5] if len(argv) > 5 else "a"
ctx = TunerContext(load_config(["--input", glb, "--output-dir", out]))
io_mesh.load_input(ctx)
obj = ctx.primary
arr = repair.MeshArrays.read(obj.data)
lab = np.load(os.path.join(out, "plan.npz"))["label"]
keep = lab > 0
f1, f2 = arr.manifold_face_pairs()
cut = keep[f1] != keep[f2]
touch = np.zeros(arr.n_faces, bool)
touch[f1[cut]] = True
touch[f2[cut]] = True
cen = arr.co[arr.tris].mean(1)
sel = (cen[:, 1] > y0) & (cen[:, 2] > zlo) & (cen[:, 2] < zhi)
col = np.full((arr.n_faces, 3), 0.7)
col[~keep] = (0.85, 0.3, 0.3)
col[touch & keep] = (0.1, 0.2, 1.0)
col[touch & ~keep] = (1.0, 0.9, 0.0)
sub = R.subset_object(obj, sel, "cut")
R.set_face_colors(sub, col[sel])
R.setup_scene(1000)
R.shading("color")
R.render_view(os.path.join(out, f"cutaway_{tag}.png"), [sub], R.VIEWS["front"])
print("CUTAWAY_DONE")
R.shading("color", backface=True)
R.render_view(os.path.join(out, f"cutaway_{tag}_culled.png"), [sub], R.VIEWS["front"])
kept_sel = sel & keep
sub2 = R.subset_object(obj, kept_sel, "cutkept")
R.set_face_colors(sub2, col[kept_sel])
R.shading("color", backface=True)
R.render_view(os.path.join(out, f"cutaway_{tag}_kept_culled.png"), [sub2], R.VIEWS["front"])
R.render_view(os.path.join(out, f"cutaway_{tag}_kept_culled_back.png"), [sub2], R.VIEWS["back"])
print("CUTAWAY2_DONE")
