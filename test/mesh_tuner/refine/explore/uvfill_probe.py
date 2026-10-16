"""Exploration (test only): do BMLoop.copy_from / layer assignment carry UVs onto edgeloop_fill faces?"""
import bmesh
import bpy

bm = bmesh.new()
uv = bm.loops.layers.uv.new("UVMap")
bmesh.ops.create_grid(bm, x_segments=4, y_segments=4, size=1.0, calc_uvs=True)
bm.faces.ensure_lookup_table()
victim = bm.faces[5]
edges = list(victim.edges)
bm.faces.remove(victim)
res = bmesh.ops.edgeloop_fill(bm, edges=edges)
f = res["faces"][0]
print("@@UV before copy", [tuple(round(c, 3) for c in l[uv].uv) for l in f.loops])
for loop in f.loops:
    for other in loop.vert.link_loops:
        if other.face is not f:
            loop.copy_from(other)
            break
print("@@UV after copy_from", [tuple(round(c, 3) for c in l[uv].uv) for l in f.loops])
for loop in f.loops:
    for other in loop.vert.link_loops:
        if other.face is not f:
            loop[uv].uv = other[uv].uv
            break
print("@@UV after layer assign", [tuple(round(c, 3) for c in l[uv].uv) for l in f.loops])
