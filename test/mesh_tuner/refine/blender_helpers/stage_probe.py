"""Blender helper (test only): run load -> analyze -> geometry_repair in-process and inspect the repaired mesh
around a point (boundary loops, duplicate positions, components) - what face_refinement actually receives.

blender --background --factory-startup --python-exit-code 86 --python stage_probe.py -- \
    --scripts <backend/blender_scripts> --input <glb> --point x,y,z --radius r [--save <blend>]
"""

import argparse
import json
import os
import sys

argv = sys.argv[sys.argv.index("--") + 1:]
ap = argparse.ArgumentParser()
ap.add_argument("--scripts", required=True)
ap.add_argument("--input", required=True)
ap.add_argument("--point", required=True)
ap.add_argument("--radius", type=float, default=0.01)
a = ap.parse_args(argv)
sys.path.insert(0, a.scripts)

import numpy as np  # noqa: E402

from img3d_amt import io_mesh, meshdata, repair, smoothing  # noqa: E402
from img3d_amt.config import OutputSpec, TunerConfig, TuningOptions  # noqa: E402
from img3d_amt.context import TunerContext  # noqa: E402

out_dir = os.path.join(os.environ.get("TEMP", "."), "stage_probe")
cfg = TunerConfig(input_path=a.input, output_dir=out_dir, options=TuningOptions.from_dict({}), outputs=OutputSpec())
ctx = TunerContext(cfg)
io_mesh.load_input(ctx)
outcome = repair.run(ctx)
print("@@STAGE repair", outcome.status, outcome.message)
me = ctx.require_primary().data
co = meshdata.get_positions(me)
tris = meshdata.get_triangles(me) if meshdata.is_all_triangles(me) else meshdata.get_loop_triangles(me)[0]
topo = smoothing.build_topology(tris, len(co), edges=meshdata.get_edges(me),
                                corner_edges=meshdata.get_loop_edges(me).reshape(-1, 3)
                                if meshdata.is_all_triangles(me) else None)
p = np.array([float(v) for v in a.point.split(",")])
near = np.linalg.norm(co - p, axis=1) < a.radius
bnd = smoothing.boundary_vertices(topo)
loops = meshdata.boundary_loops(topo.edges, topo.edge_face_count == 1, co)
stats = {"verts_near": int(near.sum()), "boundary_near": int((bnd & near).sum()),
         "dup_positions_total": meshdata.count_duplicate_positions(co),
         "nonmanifold_edges": int((topo.edge_face_count > 2).sum()),
         "attributes": [x.name for x in me.attributes]}
near_loops = []
for k in range(loops.count):
    ids = loops.loop_edge_ids(k)
    vs = np.unique(topo.edges[ids].ravel())
    if near[vs].any():
        cyc = meshdata.order_loop_vertices(topo.edges[ids])
        # coincident partner vertices (an unwelded seam rather than a hole)?
        sub = co[vs]
        d = np.linalg.norm(sub[:, None, :] - co[None, near, :], axis=2) if near.sum() < 5000 else None
        coincident = int(((d < 1e-7).sum(1) > 1).sum()) if d is not None else -1
        near_loops.append({"edges": int(len(ids)), "simple": cyc is not None, "all_near": bool(near[vs].all()),
                           "coincident_verts": coincident})
stats["loops"] = near_loops[:60]
print("@@STAGE " + json.dumps(stats))
