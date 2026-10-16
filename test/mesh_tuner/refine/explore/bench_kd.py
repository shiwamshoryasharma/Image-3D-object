"""Exploration (test only): KD-tree / BVH build + query timings at TRELLIS scale."""
import sys
import time

import bpy
import numpy as np
from mathutils import kdtree
from mathutils.bvhtree import BVHTree

sys.path.insert(0, r"E:\14-OCT-2026\Img3D\backend\blender_scripts")
from img3d_amt import meshdata  # noqa: E402

glb = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb, merge_vertices=True)
obj = max((o for o in bpy.data.objects if o.type == "MESH"), key=lambda o: len(o.data.polygons))
me = obj.data
co = meshdata.get_positions(me)
tris = meshdata.get_triangles(me)
t = time.perf_counter()
kd = kdtree.KDTree(len(co))
for i, p in enumerate(co.tolist()):
    kd.insert(p, i)
kd.balance()
print("kd build", time.perf_counter() - t)
m = co.copy()
m[:, 0] *= -1
pts = m[::3].tolist()
t = time.perf_counter()
find = kd.find
d = np.fromiter((find(p)[2] for p in pts), np.float64, len(pts))
print("kd query", len(pts), time.perf_counter() - t)
t = time.perf_counter()
mt = tris[:, [0, 2, 1]]
bvh = BVHTree.FromPolygons(m.tolist(), mt.tolist(), all_triangles=True)
print("bvh build from lists", time.perf_counter() - t)
t = time.perf_counter()
fn = bvh.find_nearest
q = co[::30].tolist()
r = [fn(p, 0.01) for p in q]
print("bvh nearest", len(q), time.perf_counter() - t)
t = time.perf_counter()
dup = obj.copy()
dup.data = me.copy()
bpy.context.scene.collection.objects.link(dup)
meshdata.set_positions(dup.data, m)
dg = bpy.context.evaluated_depsgraph_get()
bvh2 = BVHTree.FromObject(dup, dg)
print("bvh FromObject", time.perf_counter() - t)
t = time.perf_counter()
import bmesh  # noqa: E402
bm = bmesh.new()
bm.from_mesh(dup.data)
bvh3 = BVHTree.FromBMesh(bm)
print("bvh FromBMesh (incl from_mesh)", time.perf_counter() - t)
t = time.perf_counter()
rc = bvh.ray_cast
dirs = np.tile([0.0, -1.0, 0.0], (len(q), 1)).tolist()
r = [rc(p, dd, 0.01) for p, dd in zip(q, dirs)]
print("bvh raycast", len(q), time.perf_counter() - t)
