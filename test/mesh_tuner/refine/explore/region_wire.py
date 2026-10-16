"""Exploration (test only): edge stats + wireframe render of a box region of a real job.

blender --background --factory-startup --python region_wire.py -- <glb> <out_png> cx,cy,cz scale dx,dy,dz
"""
import os
import sys

import bmesh
import bpy
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "blender_helpers"))
sys.path.insert(0, r"E:\14-OCT-2026\Img3D\backend\blender_scripts")
from img3d_amt import meshdata  # noqa: E402
import render_util as ru  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:]
glb, out_png = argv[0], argv[1]
c = np.array([float(v) for v in argv[2].split(",")])
scale = float(argv[3])
d = np.array([float(v) for v in argv[4].split(",")])
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb, merge_vertices=True)
obj = max((o for o in bpy.data.objects if o.type == "MESH"), key=lambda o: len(o.data.polygons))
me = obj.data
co = meshdata.get_positions(me)
tris = meshdata.get_triangles(me)
cent = co[tris].mean(1)
half = scale / 2
inbox = np.all(np.abs(cent - c) < half, axis=1)
e = np.concatenate([tris[inbox][:, [0, 1]], tris[inbox][:, [1, 2]], tris[inbox][:, [2, 0]]])
el = np.linalg.norm(co[e[:, 0]] - co[e[:, 1]], axis=1)
allel = np.linalg.norm(co[tris[:, 0]] - co[tris[:, 1]], axis=1)
print("@@REGION faces", int(inbox.sum()), "edge pct", np.percentile(el, [10, 50, 90, 99]).round(5),
      "global median", round(float(np.median(allel)), 5))
# region copy with wireframe
bm = bmesh.new()
bm.from_mesh(me)
bm.faces.ensure_lookup_table()
keep = set(np.nonzero(inbox)[0].tolist())
bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.index not in keep], context="FACES")
reg = bpy.data.meshes.new("reg")
bm.to_mesh(reg)
bm.free()
ro = bpy.data.objects.new("reg", reg)
bpy.context.scene.collection.objects.link(ro)
mod = ro.modifiers.new("w", "WIREFRAME")
mod.thickness = float(np.median(el)) * 0.08
mod.use_replace = True
mod.use_even_offset = False
ro.location = (d / np.linalg.norm(d)) * 0.002
ru.setup_scene(1000)
for o in bpy.data.objects:
    if o.type == "MESH":
        meshdata.clear_custom_normals(o.data)
ru.render(out_png, c.tolist(), scale, d.tolist(), [obj, ro], mode="flat_matcap")
