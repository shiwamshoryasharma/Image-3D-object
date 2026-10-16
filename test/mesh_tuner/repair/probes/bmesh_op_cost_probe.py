"""Probe (repair agent): per-call cost of bmesh operators vs direct element removal on a ~1.2M-face BMesh."""
import time

import bmesh

bm = bmesh.new()
bmesh.ops.create_grid(bm, x_segments=780, y_segments=780, size=1.0)
bmesh.ops.triangulate(bm, faces=list(bm.faces))
bm.faces.ensure_lookup_table()
print("faces", len(bm.faces))
faces = list(bm.faces)
t0 = time.perf_counter()
for i in range(10):
    bmesh.ops.delete(bm, geom=[faces[i]], context="FACES")
print("ops.delete per call %.4f s" % ((time.perf_counter() - t0) / 10))
t0 = time.perf_counter()
for i in range(10, 1010):
    bm.faces.remove(faces[i])
print("faces.remove per call %.7f s" % ((time.perf_counter() - t0) / 1000))
t0 = time.perf_counter()
for i in range(3):
    bmesh.ops.reverse_faces(bm, faces=[faces[2000 + i]])
print("ops.reverse_faces per call %.4f s" % ((time.perf_counter() - t0) / 3))
bm.free()
