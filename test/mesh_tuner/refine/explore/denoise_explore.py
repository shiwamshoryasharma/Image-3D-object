"""Exploration (test only): stats + timings + renders of the smoothing primitives on a real job.

blender --background --factory-startup --python denoise_explore.py -- <glb> <out_dir> [sigma_r n_it v_it]
"""
import json
import os
import sys
import time

import bpy
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "blender_helpers"))
sys.path.insert(0, r"E:\14-OCT-2026\Img3D\backend\blender_scripts")

from img3d_amt import meshdata, smoothing  # noqa: E402
import render_util as ru  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:]
glb, out = argv[0], argv[1]
sigma_r = float(argv[2]) if len(argv) > 2 else 0.35
n_it = int(argv[3]) if len(argv) > 3 else 5
v_it = int(argv[4]) if len(argv) > 4 else 10
os.makedirs(out, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb, merge_vertices=True)
obj = max((o for o in bpy.data.objects if o.type == "MESH"), key=lambda o: len(o.data.polygons))
me = obj.data
t = time.perf_counter()
co = meshdata.get_positions(me)
tris = meshdata.get_triangles(me)
edges = meshdata.get_edges(me)
ce = meshdata.get_loop_edges(me).reshape(-1, 3)
tr = time.perf_counter() - t
t = time.perf_counter()
topo = smoothing.build_topology(tris, len(co), edges=edges, corner_edges=ce)
tt = time.perf_counter() - t
diag = float(np.linalg.norm(co.max(0) - co.min(0)))
el = smoothing.edge_lengths(co, topo.edges)
fn, fa, _ = smoothing.face_geometry(co, tris)
dih = smoothing.dihedral_angles(fn, topo)
stats = {"read_s": tr, "topo_s": tt, "V": len(co), "T": len(tris), "diag": diag,
         "edge_pct": np.percentile(el, [5, 25, 50, 75, 90, 95, 99, 99.9]).round(6).tolist(),
         "edge_over_2med": float((el > 2 * np.median(el)).mean()),
         "dihedral": smoothing.dihedral_stats(dih), "locked": int(topo.locked.sum())}
t = time.perf_counter()
elen = smoothing.vertex_mean_edge_length(co, topo)
new, info = smoothing.denoise(co, topo, normal_iterations=n_it, vertex_iterations=v_it, sigma_r=sigma_r,
                              max_disp=0.5 * elen)
stats["denoise_s"] = time.perf_counter() - t
stats["denoise"] = info
stats["disp_pct_diag"] = {"mean": info["disp_mean"] / diag * 100, "p95": info["disp_p95"] / diag * 100,
                          "max": info["disp_max"] / diag * 100}
fn2, _, _ = smoothing.face_geometry(new, tris)
stats["dihedral_after"] = smoothing.dihedral_stats(smoothing.dihedral_angles(fn2, topo))
t = time.perf_counter()
tb = smoothing.taubin(co, topo, iterations=10)
stats["taubin10_s"] = time.perf_counter() - t
print("@@STATS@@" + json.dumps(stats))

after = obj.copy()
after.data = me.copy()
bpy.context.scene.collection.objects.link(after)
meshdata.set_positions(after.data, new)
for o in (obj, after):
    meshdata.clear_custom_normals(o.data)
    o.data.shade_flat()
ru.setup_scene(900)
views = {"face": ((0.0, -0.12, 0.40), 0.17, (0, -1, 0)), "face34": ((0.0, -0.12, 0.40), 0.17, (0.6, -0.75, 0.15)),
         "legs": ((0.0, -0.05, -0.22), 0.36, (0, -1, 0)), "back": ((0.0, 0.05, 0.1), 0.6, (0, 1, 0))}
if len(argv) > 5:
    views = json.loads(argv[5])
for name, (tgt, sc, d) in views.items():
    ru.render(os.path.join(out, f"{name}_before.png"), tgt, sc, d, [obj])
    ru.render(os.path.join(out, f"{name}_after.png"), tgt, sc, d, [after])
