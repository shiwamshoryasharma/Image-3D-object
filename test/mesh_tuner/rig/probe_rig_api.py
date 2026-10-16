"""Probe (run inside Blender 5.2): vertex-group read/write speed, attribute exposure, pose evaluation speed.

blender --background --factory-startup --python-exit-code 86 --python probe_rig_api.py -- [--rings 400 --segs 256]
"""
import math
import sys
import time

import bmesh
import bpy
import numpy as np
from mathutils import Matrix

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
rings = int(argv[argv.index("--rings") + 1]) if "--rings" in argv else 400
segs = int(argv[argv.index("--segs") + 1]) if "--segs" in argv else 256

bpy.ops.wm.read_factory_settings(use_empty=True)
bm = bmesh.new()
bmesh.ops.create_cone(bm, cap_ends=True, segments=segs, radius1=0.05, radius2=0.05, depth=1.0)
bm.to_mesh(bpy.data.meshes.new("tmp"))
bm.free()
# cylinder with many rings: build manually
verts = []
for r in range(rings):
    z = -0.5 + r / (rings - 1)
    for s in range(segs):
        a = 2 * math.pi * s / segs
        verts.append((0.05 * math.cos(a), 0.05 * math.sin(a), z))
faces = []
for r in range(rings - 1):
    for s in range(segs):
        a = r * segs + s
        b = r * segs + (s + 1) % segs
        faces.append((a, b, b + segs, a + segs))
me = bpy.data.meshes.new("arm")
me.from_pydata(verts, [], faces)
me.update()
obj = bpy.data.objects.new("arm", me)
bpy.context.scene.collection.objects.link(obj)
V = len(me.vertices)
print("verts", V, "faces", len(me.polygons))

arm_data = bpy.data.armatures.new("rig")
arm = bpy.data.objects.new("rig", arm_data)
bpy.context.scene.collection.objects.link(arm)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode="EDIT")
prev = None
names = []
for i in range(4):
    eb = arm_data.edit_bones.new(f"b{i}")
    eb.head = (0, 0, -0.5 + i * 0.25)
    eb.tail = (0, 0, -0.5 + (i + 1) * 0.25)
    if prev is not None:
        eb.parent = prev
        eb.use_connect = True
    prev = eb
    names.append(eb.name)
bpy.ops.object.mode_set(mode="OBJECT")

co = np.empty(V * 3, np.float32)
me.vertices.foreach_get("co", co)
z = co.reshape(-1, 3)[:, 2]
groups = [obj.vertex_groups.new(name=n) for n in names]
t = time.perf_counter()
for i, g in enumerate(groups):
    c = -0.5 + (i + 0.5) * 0.25
    w = np.clip(1.0 - np.abs(z - c) / 0.25, 0.0, 1.0)
    idx = np.nonzero(w > 0)[0]
    for vi, wi in zip(idx.tolist(), w[idx].tolist()):
        g.add([vi], wi, "REPLACE")
print("write per-vertex add: %.3fs" % (time.perf_counter() - t))
mod = obj.modifiers.new("Armature", "ARMATURE")
mod.object = arm
obj.parent = arm

print("attributes:", [a.name for a in me.attributes])
print("has vertex_group attr API:", [n for n in dir(bpy.types.Mesh) if "group" in n.lower() or "deform" in n.lower()])
print("VertexGroup API:", [n for n in dir(bpy.types.VertexGroup) if not n.startswith("_")])

t = time.perf_counter()
vi_l, gi_l, w_l = [], [], []
for v in me.vertices:
    for g in v.groups:
        vi_l.append(v.index)
        gi_l.append(g.group)
        w_l.append(g.weight)
print("read loop: %.3fs entries %d" % (time.perf_counter() - t, len(w_l)))

t = time.perf_counter()
counts = np.empty(V, np.int32)
try:
    # unlikely to work: per-vertex group collections
    me.vertices.foreach_get("groups", counts)
    print("foreach groups ok")
except Exception as exc:
    print("foreach groups:", type(exc).__name__, exc)

# read through bmesh deform layer
t = time.perf_counter()
bm = bmesh.new()
bm.from_mesh(me)
dl = bm.verts.layers.deform.active
ent = 0
vi2, gi2, w2 = [], [], []
for v in bm.verts:
    d = v[dl]
    for k, w in d.items():
        vi2.append(v.index)
        gi2.append(k)
        w2.append(w)
bm.free()
print("bmesh read: %.3fs entries %d" % (time.perf_counter() - t, len(w2)))

# pose evaluation timing
dg = bpy.context.evaluated_depsgraph_get()
pb = arm.pose.bones["b1"]
for mode in ("data", "to_mesh"):
    t = time.perf_counter()
    for k in range(8):
        pb.matrix_basis = Matrix.Rotation(math.radians(45 if k % 2 else -45), 4, "X")
        dg.update()
        ev = obj.evaluated_get(dg)
        if mode == "data":
            m2 = ev.data
            buf = np.empty(len(m2.vertices) * 3, np.float32)
            m2.vertices.foreach_get("co", buf)
        else:
            m2 = ev.to_mesh()
            buf = np.empty(len(m2.vertices) * 3, np.float32)
            m2.vertices.foreach_get("co", buf)
            ev.to_mesh_clear()
    dt = (time.perf_counter() - t) / 8
    moved = np.abs(buf - co).max()
    print("eval %s: %.4fs per eval, n=%d moved=%.4f" % (mode, dt, len(buf) // 3, moved))
pb.matrix_basis = Matrix.Identity(4)
print("rotation_mode", pb.rotation_mode, tuple(pb.rotation_quaternion))
print("PROBE_DONE")
