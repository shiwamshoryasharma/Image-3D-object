"""Blender helper (test only): build synthetic defect meshes and export them.

Run:
  blender --background --factory-startup --python-exit-code 86 --python make_synthetic.py -- --out-dir <dir>

Writes into <dir>:
  synthetic.glb        single mesh: icosphere (20,480 tris, UVs, textured material) with 3 holes, a flipped patch,
                       a split patch (exact duplicate vertices), 3 floating 20-face islands, 3 zero-area triangles
  synthetic_multi.glb  same defects, but the islands are separate objects (exercises joining)
  synthetic_rigged.glb icosphere skinned to a 2-bone armature (exercises skins / rig metrics)
  synthetic.obj (+.mtl, texture png), synthetic.stl
  manifest.json        what was built (expected defect counts)
Prints "@@SYNTH@@<manifest path>".
"""

import argparse
import json
import os
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Vector


def args_():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", required=True)
    return p.parse_args(argv)


def make_texture(out_dir):
    size = 64
    img = bpy.data.images.new("synth_tex", size, size, alpha=False)
    yy, xx = np.mgrid[0:size, 0:size]
    px = np.zeros((size, size, 4), np.float32)
    px[..., 0] = xx / (size - 1)
    px[..., 1] = yy / (size - 1)
    px[..., 2] = ((xx // 8 + yy // 8) % 2) * 0.8
    px[..., 3] = 1.0
    img.pixels.foreach_set(px.ravel())
    path = os.path.join(out_dir, "synth_tex.png")
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    img.pack()
    return img


def make_material(img):
    mat = bpy.data.materials.new("SynthMat")
    if mat.node_tree is None:  # Blender < 5 creates materials without nodes
        mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    mat.use_backface_culling = True  # glTF doubleSided = false
    return mat


def faces_near(bm, point, radius):
    p = Vector(point)
    return [f for f in bm.faces if (f.calc_center_median() - p).length < radius]


def build_defect_bmesh(include_islands=True):
    bm = bmesh.new()
    bm.loops.layers.uv.new("UVMap")
    bmesh.ops.create_icosphere(bm, subdivisions=6, radius=0.4, calc_uvs=True)
    info = {"main_faces": len(bm.faces)}
    # 3 holes
    hole_faces = 0
    for d in ((0.4, 0, 0), (0, 0, 0.4), (0, -0.4, 0.05)):
        fs = faces_near(bm, d, 0.035)
        hole_faces += len(fs)
        bmesh.ops.delete(bm, geom=fs, context="FACES")
    info["holes"] = 3
    info["hole_faces_deleted"] = hole_faces
    # flipped patch
    flip = faces_near(bm, (0, 0.4, 0), 0.05)
    bmesh.ops.reverse_faces(bm, faces=flip)
    info["flipped_faces"] = len(flip)
    # split patch -> exact duplicate vertices along its border
    split = faces_near(bm, (-0.4, 0, 0), 0.05)
    nverts = len(bm.verts)
    bmesh.ops.split(bm, geom=split, use_only_faces=False)
    info["split_duplicate_vertices"] = len(bm.verts) - nverts
    # 3 zero-area triangles at exact binary-fraction collinear positions
    uv = bm.loops.layers.uv.active
    for k in range(3):
        z = 0.5 + 0.0625 * k
        vs = [bm.verts.new((0.5, 0.25, z)), bm.verts.new((0.5625, 0.25, z)), bm.verts.new((0.625, 0.25, z))]
        f = bm.faces.new(vs)
        for loop in f.loops:
            loop[uv].uv = (0.5, 0.5)
    info["degenerate_faces"] = 3
    islands = []
    if include_islands:
        for c in ((0.6, 0.0, 0.0), (0.0, 0.0, 0.62), (0.45, 0.35, -0.3)):
            islands.append(c)
            ret = bmesh.ops.create_icosphere(bm, subdivisions=1, radius=0.03, calc_uvs=True)
            bmesh.ops.translate(bm, verts=ret["verts"], vec=Vector(c))
    info["islands"] = 3
    info["island_centers"] = [list(c) for c in islands]
    return bm, info


def new_object(name, bm, mat):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat)
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def select_only(objs):
    for o in bpy.data.objects:
        o.select_set(o in objs)
    bpy.context.view_layer.objects.active = objs[0]


def export_glb(path, objs, skins=False):
    select_only(objs)
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_image_format="AUTO",
                              export_materials="EXPORT", export_yup=True, export_skins=skins,
                              export_animations=False)


def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def main():
    a = args_()
    os.makedirs(a.out_dir, exist_ok=True)
    manifest = {}

    # 1) single object
    clear_scene()
    mat = make_material(make_texture(a.out_dir))
    bm, info = build_defect_bmesh(include_islands=True)
    obj = new_object("synthetic", bm, mat)
    info["faces_total"] = len(obj.data.polygons)
    info["vertices_total"] = len(obj.data.vertices)
    glb = os.path.join(a.out_dir, "synthetic.glb")
    export_glb(glb, [obj])
    manifest["synthetic"] = dict(info, path=glb)
    # OBJ + STL of the same object
    select_only([obj])
    objp = os.path.join(a.out_dir, "synthetic.obj")
    bpy.ops.wm.obj_export(filepath=objp, export_selected_objects=True, export_materials=True,
                          path_mode="RELATIVE")
    stlp = os.path.join(a.out_dir, "synthetic.stl")
    bpy.ops.wm.stl_export(filepath=stlp, export_selected_objects=True)
    manifest["obj"] = {"path": objp}
    manifest["stl"] = {"path": stlp}

    # 2) multi object: islands separate
    clear_scene()
    mat = make_material(make_texture(a.out_dir))
    bm, info = build_defect_bmesh(include_islands=False)
    main_obj = new_object("main", bm, mat)
    objs = [main_obj]
    for i, c in enumerate(((0.6, 0.0, 0.0), (0.0, 0.0, 0.62), (0.45, 0.35, -0.3))):
        b = bmesh.new()
        b.loops.layers.uv.new("UVMap")
        bmesh.ops.create_icosphere(b, subdivisions=1, radius=0.03, calc_uvs=True)
        o = new_object(f"island_{i}", b, mat)
        o.location = c  # object transform (applied by the tuner)
        objs.append(o)
    glb = os.path.join(a.out_dir, "synthetic_multi.glb")
    export_glb(glb, objs)
    manifest["multi"] = {"path": glb, "objects": len(objs)}

    # 3) rigged
    clear_scene()
    mat = make_material(make_texture(a.out_dir))
    b = bmesh.new()
    b.loops.layers.uv.new("UVMap")
    bmesh.ops.create_icosphere(b, subdivisions=4, radius=0.3, calc_uvs=True)
    body = new_object("body", b, mat)
    arm_data = bpy.data.armatures.new("rig")
    arm = bpy.data.objects.new("rig", arm_data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    b1 = arm_data.edit_bones.new("lower")
    b1.head, b1.tail = (0, 0, -0.3), (0, 0, 0)
    b2 = arm_data.edit_bones.new("upper")
    b2.head, b2.tail = (0, 0, 0), (0, 0, 0.3)
    b2.parent = b1
    bpy.ops.object.mode_set(mode="OBJECT")
    g1 = body.vertex_groups.new(name="lower")
    g2 = body.vertex_groups.new(name="upper")
    for v in body.data.vertices:
        t = min(max((v.co.z + 0.3) / 0.6, 0.0), 1.0)
        g1.add([v.index], 1.0 - t, "REPLACE")
        g2.add([v.index], t, "REPLACE")
    mod = body.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    body.parent = arm
    glb = os.path.join(a.out_dir, "synthetic_rigged.glb")
    export_glb(glb, [arm, body], skins=True)
    manifest["rigged"] = {"path": glb, "bones": 2, "faces": len(body.data.polygons)}

    mpath = os.path.join(a.out_dir, "manifest.json")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    print("@@SYNTH@@" + mpath, flush=True)


main()
