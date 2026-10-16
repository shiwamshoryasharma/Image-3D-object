"""Blender helper (test only): render a GLB close-up with its open (boundary) edges drawn as red tubes.

blender --background --factory-startup --python-exit-code 86 --python boundary_render.py -- \
    --input <glb> --out <png> --target x,y,z --scale s [--dir dx,dy,dz]
"""

import argparse
import os
import sys

argv = sys.argv[sys.argv.index("--") + 1:]
ap = argparse.ArgumentParser()
ap.add_argument("--input", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--target", required=True)
ap.add_argument("--scale", type=float, required=True)
ap.add_argument("--dir", default="0,-1,0")
a = ap.parse_args(argv)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r"E:\14-OCT-2026\Img3D\backend\blender_scripts")

import bpy  # noqa: E402
import numpy as np  # noqa: E402

from img3d_amt import meshdata  # noqa: E402
import render_util as ru  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=a.input, merge_vertices=True)
obj = max((o for o in bpy.data.objects if o.type == "MESH"), key=lambda o: len(o.data.polygons))
me = obj.data
co = meshdata.get_positions(me)
ef = meshdata.edge_faces(me)
tgt = np.array([float(v) for v in a.target.split(",")])
e = ef.edges[ef.boundary]
mid = 0.5 * (co[e[:, 0]] + co[e[:, 1]])
e = e[np.all(np.abs(mid - tgt) < a.scale, axis=1)]
print("@@BOUNDARY edges in view:", len(e))
if len(e):
    verts = co[np.unique(e.ravel())]
    remap = {int(v): i for i, v in enumerate(np.unique(e.ravel()))}
    bm_me = bpy.data.meshes.new("bnd")
    bm_me.from_pydata(verts.tolist(), [(remap[int(x)], remap[int(y)]) for x, y in e], [])
    bo = bpy.data.objects.new("bnd", bm_me)
    bpy.context.scene.collection.objects.link(bo)
    mod = bo.modifiers.new("skin", "WIREFRAME")
    # wire edges need faces for the wireframe modifier -> use a skin-like tube via a curve instead
    bo.modifiers.remove(mod)
    curve = bpy.data.curves.new("bndc", "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = a.scale * 0.004
    for x, y in e:
        sp = curve.splines.new("POLY")
        sp.points.add(1)
        sp.points[0].co = (*co[x], 1.0)
        sp.points[1].co = (*co[y], 1.0)
    co_obj = bpy.data.objects.new("bndc", curve)
    bpy.context.scene.collection.objects.link(co_obj)
    mat = bpy.data.materials.new("red")
    mat.diffuse_color = (1.0, 0.0, 0.0, 1.0)
    curve.materials.append(mat)
ru.setup_scene(900)
sh = bpy.context.scene.display.shading
for o in bpy.context.scene.objects:
    o.hide_render = False
d = [float(v) for v in a.dir.split(",")]
cam = bpy.context.scene.camera
ru.shading("matcap")
sh.color_type = "MATERIAL"
from mathutils import Vector  # noqa: E402

cam.data.ortho_scale = a.scale
cam.data.clip_start = 0.001
dv = Vector(d).normalized()
cam.location = Vector(tgt.tolist()) + dv * 3.0
cam.rotation_euler = (-dv).to_track_quat("-Z", "Y").to_euler()
bpy.context.scene.render.filepath = a.out
bpy.ops.render.render(write_still=True)
