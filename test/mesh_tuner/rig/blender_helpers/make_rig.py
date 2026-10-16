"""Build the synthetic skinned "arm" with deliberately bad weights (runs INSIDE Blender 5.2).

blender --background --factory-startup --python-exit-code 86 --python make_rig.py -- --out-dir <dir>

The arm is a closed, subdivided cylinder along +X (x in [-0.5, 0.5], radius 0.06, 64 rings x 24 segments + cap fans)
skinned to a 4-bone chain ``upper_arm -> forearm -> hand -> finger`` plus two deform twist helpers
(``upper_twist``, ``fore_twist``) and one non-deform control bone (``ik_target``). Clean weights are smooth tent
blends (<= 2 influences); then these defects are injected:

* ``six_influence``: vertices around the elbow carry weights on all 6 deform bones;
* ``unnormalized``: weight sums scaled by 0.6 / 1.7 on a band of rings;
* ``tiny``: extra 1e-5 weights to a far bone;
* ``unweighted``: a 5-ring x 12-segment block with every deform weight removed;
* ``spike``: one upper-arm vertex weighted 1.0 to the tip bone ``finger`` (explodes when ``finger`` rotates);
* groups ``extra_empty`` (empty, not a bone), ``ik_target`` (empty, non-deform bone), ``displace_ref`` (empty, but
  referenced by a disabled Displace modifier -> must be kept), ``paint_mask`` (non-deform, has weights -> untouched).

Outputs in ``--out-dir``: ``rig_bad.glb`` (export_all_influences, skins), ``rig_bad.fbx``, ``rig_bad.blend`` (also has
a non-rest pose on ``hand`` to verify the pose is restored), the defect-free reference ``rig_clean.blend`` /
``rig_clean.glb`` and ``rig_manifest.json``.
"""

import argparse
import json
import math
import os
import sys

import bpy
import numpy as np

RINGS = 64
SEGS = 24
RADIUS = 0.06
X0, X1 = -0.5, 0.5
CHAIN = (("upper_arm", -0.5, -0.15), ("forearm", -0.15, 0.15), ("hand", 0.15, 0.38), ("finger", 0.38, 0.5))


def args_():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", required=True)
    return p.parse_args(argv)


def build_mesh():
    verts, faces, uvs_per_face = [], [], []
    for r in range(RINGS):
        x = X0 + (X1 - X0) * r / (RINGS - 1)
        for s in range(SEGS):
            a = 2.0 * math.pi * s / SEGS
            verts.append((x, RADIUS * math.cos(a), RADIUS * math.sin(a)))
    for r in range(RINGS - 1):
        for s in range(SEGS):
            a = r * SEGS + s
            b = r * SEGS + (s + 1) % SEGS
            faces.append((a, b, b + SEGS, a + SEGS))  # outward normals (caps below match)
    c0 = len(verts)
    verts.append((X0, 0.0, 0.0))
    c1 = len(verts)
    verts.append((X1, 0.0, 0.0))
    last = (RINGS - 1) * SEGS
    for s in range(SEGS):
        faces.append((c0, (s + 1) % SEGS, s))
        faces.append((c1, last + s, last + (s + 1) % SEGS))
    me = bpy.data.meshes.new("arm")
    me.from_pydata(verts, [], faces)
    me.update()
    me.shade_smooth()
    # simple cylindrical UVs (so the GLB carries TEXCOORD_0 like a real asset)
    uv = me.uv_layers.new(name="UVMap")
    co = np.array(verts)
    lv = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", lv)
    p = co[lv]
    u = (p[:, 0] - X0) / (X1 - X0)
    v = (np.arctan2(p[:, 2], p[:, 1]) / (2 * math.pi)) % 1.0
    uv.data.foreach_set("uv", np.stack([u, v], 1).astype(np.float32).reshape(-1))
    mat = bpy.data.materials.new("skin")
    mat.diffuse_color = (0.8, 0.6, 0.5, 1.0)
    me.materials.append(mat)
    return me


def build_armature():
    arm_data = bpy.data.armatures.new("rig")
    arm = bpy.data.objects.new("rig", arm_data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    prev = None
    ebs = {}
    for name, a, b in CHAIN:
        eb = arm_data.edit_bones.new(name)
        eb.head, eb.tail = (a, 0.0, 0.0), (b, 0.0, 0.0)
        eb.roll = 0.0
        if prev is not None:
            eb.parent = prev
            eb.use_connect = True
        prev = eb
        ebs[name] = eb
    for name, parent, a, b in (("upper_twist", "upper_arm", -0.35, -0.2), ("fore_twist", "forearm", -0.05, 0.1)):
        eb = arm_data.edit_bones.new(name)
        eb.head, eb.tail = (a, 0.0, 0.012), (b, 0.0, 0.012)
        eb.parent = ebs[parent]
    eb = arm_data.edit_bones.new("ik_target")
    eb.head, eb.tail = (0.5, 0.0, 0.15), (0.5, 0.0, 0.25)
    eb.use_deform = False
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm


def clean_weights(x):
    """Smooth tent blend over the chain bones: (V, 4) weights, rows sum to 1 (<= 2 influences)."""
    centers = np.array([(a + b) / 2 for _, a, b in CHAIN])
    w = np.zeros((len(x), len(CHAIN)))
    for i, c in enumerate(centers):
        half = 0.5 * (CHAIN[i][2] - CHAIN[i][1])
        w[:, i] = np.clip(1.0 - np.abs(x - c) / (half * 2.0), 0.0, None)
    w[x <= centers[0], 0] = 1.0
    w[x >= centers[-1], -1] = 1.0
    # keep the 2 largest
    order = np.argsort(-w, axis=1)
    mask = np.zeros_like(w, dtype=bool)
    np.put_along_axis(mask, order[:, :2], True, axis=1)
    w = np.where(mask, w, 0.0)
    return w / w.sum(1, keepdims=True)


def tri_count(me):
    me.calc_loop_triangles()
    return len(me.loop_triangles)


def write_groups(groups, W):
    for gi, g in enumerate(groups):
        idx = np.nonzero(W[:, gi] > 0.0)[0]
        for vi, wi in zip(idx.tolist(), W[idx, gi].tolist()):
            g.add([vi], float(wi), "REPLACE")


def select_only(objs):
    for o in bpy.data.objects:
        o.select_set(o in objs)
    bpy.context.view_layer.objects.active = objs[0]


def main():
    a = args_()
    os.makedirs(a.out_dir, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    me = build_mesh()
    obj = bpy.data.objects.new("arm", me)
    bpy.context.scene.collection.objects.link(obj)
    arm = build_armature()

    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3).astype(np.float64)
    x = co[:, 0]
    n = len(co)
    names = [c[0] for c in CHAIN] + ["upper_twist", "fore_twist"]
    W = np.zeros((n, len(names)))
    W[:, :4] = clean_weights(x)

    # clean reference rig (same mesh/armature, good weights) -> rig_clean.blend / rig_clean.glb
    groups = [obj.vertex_groups.new(name=nm) for nm in names]
    write_groups(groups, W)
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    obj.parent = arm
    select_only([arm, obj])
    clean_blend = os.path.join(a.out_dir, "rig_clean.blend")
    bpy.ops.wm.save_as_mainfile(filepath=clean_blend, copy=True, check_existing=False)
    clean_glb = os.path.join(a.out_dir, "rig_clean.glb")
    bpy.ops.export_scene.gltf(filepath=clean_glb, export_format="GLB", use_selection=True, export_skins=True,
                              export_animations=False, export_materials="EXPORT", export_yup=True)
    for g in list(obj.vertex_groups):
        obj.vertex_groups.remove(g)
    ring = np.full(n, -1)
    ring[: RINGS * SEGS] = np.arange(RINGS * SEGS) // SEGS
    seg = np.full(n, -1)
    seg[: RINGS * SEGS] = np.arange(RINGS * SEGS) % SEGS

    # 6 influences around the elbow (rings 20..23)
    six = np.nonzero((ring >= 20) & (ring <= 23))[0]
    W[six] = 0.6 * W[six] + 0.4 * np.array([0.2, 0.3, 0.15, 0.1, 0.12, 0.13])
    # unnormalized sums on rings 30..33 (alternating x0.6 / x1.7)
    unnorm = np.nonzero((ring >= 30) & (ring <= 33))[0]
    W[unnorm] *= np.where(ring[unnorm] % 2 == 0, 0.6, 1.7)[:, None]
    # tiny 1e-5 weights to the far bone 'finger' on rings 2..5
    tiny = np.nonzero((ring >= 2) & (ring <= 5))[0]
    W[tiny, 3] = 1e-5
    # unweighted block: rings 40..44, segments 0..11
    unweighted = np.nonzero((ring >= 40) & (ring <= 44) & (seg >= 0) & (seg < 12))[0]
    W[unweighted] = 0.0
    # spike: ring 8, segment 6 -> 100 % 'finger'
    spike = int(np.nonzero((ring == 8) & (seg == 6))[0][0])
    W[spike] = 0.0
    W[spike, 3] = 1.0

    groups = [obj.vertex_groups.new(name=nm) for nm in names]
    write_groups(groups, W)
    obj.vertex_groups.new(name="extra_empty")
    obj.vertex_groups.new(name="ik_target")
    obj.vertex_groups.new(name="displace_ref")
    paint = obj.vertex_groups.new(name="paint_mask")
    paint_idx = np.nonzero(co[:, 2] > 0.03)[0]
    for vi in paint_idx.tolist():
        paint.add([vi], 0.25 + 0.5 * float(co[vi, 2] > 0.05), "REPLACE")

    manifest = {
        "vertices": n, "faces": len(me.polygons), "triangles": tri_count(me), "deform_bones": names,
        "non_deform_bones": ["ik_target"],
        "six_influence_verts": len(six), "unnormalized_verts": len(unnorm), "tiny_weight_verts": len(tiny),
        "unweighted_verts": len(unweighted), "spike_vertex": spike, "spike_co": co[spike].tolist(),
        "unweighted_co": co[unweighted].tolist(), "paint_mask_count": len(paint_idx),
        "paint_mask_weights": sorted({0.25, 0.75}),
    }

    manifest.update({"clean_blend": clean_blend, "clean_glb": clean_glb})
    select_only([arm, obj])
    glb = os.path.join(a.out_dir, "rig_bad.glb")
    bpy.ops.export_scene.gltf(filepath=glb, export_format="GLB", use_selection=True, export_skins=True,
                              export_all_influences=True, export_animations=False, export_materials="EXPORT",
                              export_yup=True)
    fbx = os.path.join(a.out_dir, "rig_bad.fbx")
    bpy.ops.export_scene.fbx(filepath=fbx, use_selection=True, object_types={"ARMATURE", "MESH"},
                             add_leaf_bones=False, bake_anim=False, use_armature_deform_only=False)
    # .blend: exact weights + a disabled modifier referencing an empty group + a non-rest pose
    disp = obj.modifiers.new("Displace", "DISPLACE")
    disp.vertex_group = "displace_ref"
    disp.strength = 0.0
    disp.show_viewport = False
    disp.show_render = False
    pb = arm.pose.bones["hand"]
    pb.rotation_mode = "XYZ"
    pb.rotation_euler = (0.0, 0.0, math.radians(20.0))
    manifest["blend_pose"] = {"bone": "hand", "rotation_mode": "XYZ", "rotation_euler": [0.0, 0.0, math.radians(20.0)]}
    blend = os.path.join(a.out_dir, "rig_bad.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend, copy=True, check_existing=False)
    manifest.update({"glb": glb, "fbx": fbx, "blend": blend})
    path = os.path.join(a.out_dir, "rig_manifest.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    print("@@RIG@@" + path, flush=True)


main()
