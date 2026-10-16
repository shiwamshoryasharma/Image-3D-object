"""Blender helper (repair tests): build synthetic TRELLIS-like defect meshes and export them as GLB.

  blender --background --factory-startup --python-exit-code 86 --python make_repair_synthetic.py -- --out-dir <dir>

Cases (unit-ish scale, +Z up; every mesh has UVs and one material):
* ``shell_hole``     closed double-walled sphere shell (outer r=1 outward, inner r=0.98 inward) with a hole through
                     both walls (cap around +X removed, rims bridged by a lip) - like a TRELLIS shell with a hole.
* ``thin_sheet``     open cloth sheet (wavy rectangle) thickened into a closed 0.02 sausage - both walls visible.
* ``interior_junk``  closed double-walled sphere shell + a small closed double-walled blob INSIDE it.
* ``separate_part``  double-walled sphere shell + a separate smaller double-walled sphere next to it (visible).
* ``flipped``        single-wall closed sphere with all faces wound inwards + a second sphere with a flipped patch.
Writes ``manifest.json`` ({case: {path, faces, notes}}).
"""

import argparse
import json
import math
import os
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

argv = sys.argv[sys.argv.index("--") + 1:]
ap = argparse.ArgumentParser()
ap.add_argument("--out-dir", required=True)
args = ap.parse_args(argv)
os.makedirs(args.out_dir, exist_ok=True)


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def sphere(bm, radius, center=(0, 0, 0), segments=96, rings=48, inward=False):
    """UV sphere into ``bm``; returns its faces."""
    before = set(bm.faces)
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=rings, radius=radius,
                              matrix=Matrix.Translation(Vector(center)), calc_uvs=True)
    faces = [f for f in bm.faces if f not in before]
    if inward:
        bmesh.ops.reverse_faces(bm, faces=faces)
    return faces


def triangulate(bm):
    bmesh.ops.triangulate(bm, faces=list(bm.faces), quad_method="BEAUTY", ngon_method="BEAUTY")


def shell(bm, r_out, r_in, center=(0, 0, 0), segments=96, rings=48, hole_axis=None, hole_cos=0.9):
    """Double-walled sphere shell (outer outward, inner inward); optional hole through both walls, bridged."""
    outer = sphere(bm, r_out, center, segments, rings)
    inner = sphere(bm, r_in, center, segments, rings, inward=True)
    if hole_axis is None:
        return
    ax = Vector(hole_axis).normalized()
    c = Vector(center)
    kill = [f for f in outer + inner if (f.calc_center_median() - c).normalized().dot(ax) > hole_cos]
    bmesh.ops.delete(bm, geom=kill, context="FACES")
    rim = [e for e in bm.edges if e.is_boundary]
    bmesh.ops.bridge_loops(bm, edges=rim)


def material(obj, name, color):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    obj.data.materials.append(mat)


def finish(bm, name, color=(0.8, 0.6, 0.5)):
    triangulate(bm)
    bm.normal_update()
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    if not me.uv_layers:
        me.uv_layers.new(name="UVMap")
    material(obj, name + "_mat", color)
    return obj


def export(name):
    path = os.path.join(args.out_dir, f"{name}.glb")
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=False, export_apply=True)
    return path


manifest = {}

# --- shell_hole
reset()
bm = bmesh.new()
shell(bm, 1.0, 0.98, hole_axis=(1, 0, 0), hole_cos=0.93)
obj = finish(bm, "shell_hole")
manifest["shell_hole"] = {"path": export("shell_hole"), "faces": len(obj.data.polygons),
                          "notes": "outer r=1, inner r=0.98, hole around +X bridged by a lip"}

# --- thin_sheet: wavy open sheet, thickened into a closed sausage (solidify-like)
reset()
bm = bmesh.new()
bmesh.ops.create_grid(bm, x_segments=80, y_segments=60, size=0.5)
for v in bm.verts:
    x, y = v.co.x, v.co.y
    v.co = Vector((x * 2.0, y * 1.5, 0.15 * math.sin(3.0 * x) * math.cos(2.0 * y)))
bm.normal_update()
grid = list(bm.faces)
res = bmesh.ops.solidify(bm, geom=grid, thickness=0.02)
obj = finish(bm, "thin_sheet", (0.4, 0.5, 0.8))
manifest["thin_sheet"] = {"path": export("thin_sheet"), "faces": len(obj.data.polygons),
                          "notes": "open sheet thickened by 0.02 (closed): both sides seen from outside"}

# --- interior_junk
reset()
bm = bmesh.new()
shell(bm, 1.0, 0.98)
junk_before = len(bm.faces)
shell(bm, 0.25, 0.24, center=(0.2, 0.1, -0.1), segments=24, rings=12)
obj = finish(bm, "interior_junk")
manifest["interior_junk"] = {"path": export("interior_junk"), "faces": len(obj.data.polygons),
                             "notes": "closed shell + hidden double-walled blob inside (r=0.25 at (0.2,0.1,-0.1))"}

# --- separate_part
reset()
bm = bmesh.new()
shell(bm, 1.0, 0.98)
shell(bm, 0.35, 0.34, center=(1.7, 0.0, 0.0), segments=48, rings=24)
obj = finish(bm, "separate_part")
manifest["separate_part"] = {"path": export("separate_part"), "faces": len(obj.data.polygons),
                             "notes": "main shell + separate visible shell r=0.35 at (1.7,0,0)"}

# --- flipped: inward-wound sphere + sphere with a flipped patch (single walls)
reset()
bm = bmesh.new()
sphere(bm, 1.0, inward=True)
second = sphere(bm, 0.6, center=(2.0, 0.0, 0.0), segments=64, rings=32)
patch = [f for f in second if (f.calc_center_median() - Vector((2.0, 0.0, 0.0))).normalized().z > 0.7]
bmesh.ops.reverse_faces(bm, faces=patch)
obj = finish(bm, "flipped")
manifest["flipped"] = {"path": export("flipped"), "faces": len(obj.data.polygons),
                       "notes": "sphere r=1 wound inwards + sphere r=0.6 at (2,0,0) with its top cap flipped"}

with open(os.path.join(args.out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump(manifest, fh, indent=1)
print("@@SYNTH@@" + json.dumps(manifest))
