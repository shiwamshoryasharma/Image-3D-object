"""Probe (repair agent): API details in this Blender (gpu state funcs, bmesh op docs, Mesh helpers)."""
import sys

sys.stdout.reconfigure(line_buffering=True)
import bmesh
import bpy

print("version", bpy.app.version_string)
import gpu

print("gpu has init", hasattr(gpu, "init"))
print("gpu.state", [n for n in dir(gpu.state) if not n.startswith("_")])
print("GPUOffScreen doc", gpu.types.GPUOffScreen.__doc__[:600])
print("FB read_depth", gpu.types.GPUFrameBuffer.read_depth.__doc__)
for op in ("recalc_face_normals", "reverse_faces", "dissolve_degenerate", "holes_fill", "triangulate", "beautify_fill",
           "subdivide_edges", "delete", "remove_doubles", "split_edges", "edgeloop_fill"):
    print("==", op, getattr(bmesh.ops, op).__doc__[:900])
me = bpy.data.meshes.new("t")
print("Mesh set_sharp_from_angle", hasattr(me, "set_sharp_from_angle"), getattr(me.__class__, "set_sharp_from_angle", None).__doc__ if hasattr(me, "set_sharp_from_angle") else None)
print("Mesh shade_smooth", hasattr(me, "shade_smooth"))
me.vertices.add(3)
me.loops.add(3)
me.polygons.add(1)
me.vertices.foreach_set("co", [0, 0, 0, 1, 0, 0, 0, 1, 0])
me.loops.foreach_set("vertex_index", [0, 1, 2])
me.polygons.foreach_set("loop_start", [0])
try:
    me.polygons.foreach_set("loop_total", [3])
    print("loop_total writable")
except Exception as e:
    print("loop_total not writable", e)
me.update(calc_edges=True)
print("edges", len(me.edges), "valid", not me.validate(verbose=True))
print("attrs", [(a.name, a.domain, a.data_type) for a in me.attributes])
