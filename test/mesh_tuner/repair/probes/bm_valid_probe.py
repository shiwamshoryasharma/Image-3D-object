"""Probe: which bmesh operations invalidate existing BMVert python wrappers (is_valid)?"""
import sys

sys.stdout.reconfigure(line_buffering=True)
import bmesh


def fresh():
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=6, y_segments=6, size=1.0)
    bm.verts.ensure_lookup_table()
    return bm, [bm.verts[i] for i in range(10)]


bm, vs = fresh()
bm.verts.layers.float.new("x")
print("layer new:", all(v.is_valid for v in vs))
bm, vs = fresh()
bm.loops.layers.uv.new("uv")
print("loop uv layer new:", all(v.is_valid for v in vs))
bm, vs = fresh()
bmesh.ops.delete(bm, geom=list(bm.faces)[:3], context="FACES_ONLY")
print("delete faces only:", all(v.is_valid for v in vs))
bm, vs = fresh()
e = [e for e in bm.edges if not e.is_boundary][:4]
bmesh.ops.subdivide_edges(bm, edges=e, cuts=1)
print("subdivide:", all(v.is_valid for v in vs))
bm, vs = fresh()
bm.faces.new((bm.verts.new((5, 5, 0)), bm.verts.new((6, 5, 0)), bm.verts.new((5, 6, 0))))
print("faces.new:", all(v.is_valid for v in vs))
bm, vs = fresh()
bmesh.ops.triangulate(bm, faces=list(bm.faces)[:5])
print("triangulate:", all(v.is_valid for v in vs))
bm, vs = fresh()
bmesh.ops.triangulate(bm, faces=list(bm.faces))
bmesh.ops.beautify_fill(bm, faces=list(bm.faces), edges=list(bm.edges))
print("beautify:", all(v.is_valid for v in vs))
bm, vs = fresh()
lay = bm.verts.layers.float.new("x")
vs = [bm.verts[i] for i in range(10)]
print("after layer then re-fetch (stale lookup?):", all(v.is_valid for v in vs))
bm.verts.ensure_lookup_table()
vs = [bm.verts[i] for i in range(10)]
print("after layer + ensure_lookup_table + fetch:", all(v.is_valid for v in vs))
v = bm.verts.new((9, 9, 9))
v[lay] = 1.0
print("new vert layer write ok", v[lay])
