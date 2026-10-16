"""Exploration (test only): bilateral denoise + facet fairing on a real job, timings + smooth-shaded renders.

blender --background --factory-startup --python facet_explore.py -- <glb> <out_dir> '<json params>' ['<json views>']
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
prm = {"n_it": 4, "v_it": 8, "sigma_r": 0.3, "disp": 0.6, "f_it": 35, "f_str": 1.0, "f_tol": 0.35, "f_ang": 28.0}
if len(argv) > 2:
    prm.update(json.loads(argv[2]))
os.makedirs(out, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb, merge_vertices=True)
obj = max((o for o in bpy.data.objects if o.type == "MESH"), key=lambda o: len(o.data.polygons))
me = obj.data
co = meshdata.get_positions(me)
tris = meshdata.get_triangles(me)
topo = smoothing.build_topology(tris, len(co), edges=meshdata.get_edges(me),
                                corner_edges=meshdata.get_loop_edges(me).reshape(-1, 3))
diag = float(np.linalg.norm(co.max(0) - co.min(0)))
elen = smoothing.vertex_mean_edge_length(co, topo)
stats = {}
t = time.perf_counter()
x1, info = smoothing.denoise(co, topo, normal_iterations=prm["n_it"], vertex_iterations=prm["v_it"],
                             sigma_r=prm["sigma_r"], max_disp=prm["disp"] * elen)
stats["denoise_s"] = round(time.perf_counter() - t, 3)
stats["denoise"] = info
t = time.perf_counter()
x2, finfo = smoothing.fair_facets(x1, topo, iterations=prm["f_it"], strength=prm["f_str"], tolerance=prm["f_tol"],
                                  feature_angle=prm["f_ang"], edge_len=elen)
stats["facets_s"] = round(time.perf_counter() - t, 3)
stats["facets"] = finfo
d = np.linalg.norm(x2 - co, axis=1) / diag * 100
stats["total_disp_pct"] = {"mean": float(d.mean()), "p95": float(np.percentile(d, 95)), "max": float(d.max())}
fn2, _, _ = smoothing.face_geometry(x2, tris)
fn0, _, _ = smoothing.face_geometry(co, tris)
stats["dihedral_before"] = smoothing.dihedral_stats(smoothing.dihedral_angles(fn0, topo))
stats["dihedral_after"] = smoothing.dihedral_stats(smoothing.dihedral_angles(fn2, topo))
print("@@STATS@@" + json.dumps(stats))

after = obj.copy()
after.data = me.copy()
bpy.context.scene.collection.objects.link(after)
meshdata.set_positions(after.data, x2)
for o in (obj, after):
    meshdata.clear_custom_normals(o.data)
    o.data.shade_smooth()
ru.setup_scene(800)
views = {"face": [[0.0, -0.12, 0.40], 0.17, [0, -1, 0]], "legs": [[0.0, -0.05, -0.22], 0.36, [0, -1, 0]],
         "back": [[0.0, 0.05, 0.1], 0.6, [0, 1, 0]], "skirt34": [[0.0, -0.05, 0.05], 0.45, [0.6, -0.75, 0.15]]}
if len(argv) > 3:
    views = json.loads(argv[3])
for name, (tgt, sc, dd) in views.items():
    ru.render(os.path.join(out, f"{name}_before.png"), tgt, sc, dd, [obj])
    ru.render(os.path.join(out, f"{name}_after.png"), tgt, sc, dd, [after])
