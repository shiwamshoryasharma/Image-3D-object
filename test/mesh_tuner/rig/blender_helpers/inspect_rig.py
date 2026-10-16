"""Inspect the skin of a model (runs INSIDE Blender 5.2) - independent of img3d_amt (no package imports).

blender --background --factory-startup --python-exit-code 86 --python inspect_rig.py -- --input <glb|fbx|blend>
    [--probe x,y,z ...] [--pose-bone NAME --pose-angle 45] [--render-dir DIR --render-tag TAG --render-pose b:X:45]

Prints one line ``@@RIGINFO@@{json}``:
  armatures / bones (name, deform, head, tail, parent) / pose (per bone rotation_mode + rotation values + location +
  scale) / meshes: vertices, faces, groups {name: [count, weight_sum]}, deform stats (max_influences,
  unnormalized (|sum-1| > 1e-3), tiny (< 1e-3), unweighted, min_weight), weights at the probe points (nearest
  vertex) / pose test (rotate --pose-bone by +-angle about local X and Z: max edge stretch ratio, collapsed tris).
With --render-dir: Workbench matcap renders of the mesh in rest pose (all channels identity) and for every
--render-pose, plus weight-colour renders (each deform bone a distinct colour; unweighted vertices magenta).
"""

import argparse
import colorsys
import json
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Matrix, Vector


def args_():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--probe", action="append", default=[])
    p.add_argument("--pose-bone", default=None)
    p.add_argument("--pose-angle", type=float, default=45.0)
    p.add_argument("--render-dir", default=None)
    p.add_argument("--render-pose", action="append", default=[], help="bone:axis:angle_deg (repeatable)")
    p.add_argument("--render-tag", default="model")
    p.add_argument("--color-bones", default=None, help="comma-separated bone order for stable weight colours")
    p.add_argument("--res", type=int, default=768)
    return p.parse_args(argv)


def load(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".blend":
        bpy.ops.wm.open_mainfile(filepath=path)
        return
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path, disable_bone_shape=True)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    else:
        raise SystemExit(f"unsupported {ext}")


def armature_of(obj):
    for m in obj.modifiers:
        if m.type == "ARMATURE" and m.object is not None:
            return m.object
    if obj.parent is not None and obj.parent.type == "ARMATURE":
        return obj.parent
    return None


def weights_of(obj):
    entries = []
    for v in obj.data.vertices:
        for g in v.groups:
            entries.append((v.index, g.group, g.weight))
    return entries


def positions(me):
    buf = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", buf)
    return buf.reshape(-1, 3).astype(np.float64)


def evaluated_positions(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    return positions(obj.evaluated_get(dg).data)


def pose_test(obj, arm, bone, angle_deg):
    me = obj.data
    edges = np.empty(len(me.edges) * 2, np.int32)
    me.edges.foreach_get("vertices", edges)
    edges = edges.reshape(-1, 2)
    me.calc_loop_triangles()
    tris = np.empty(len(me.loop_triangles) * 3, np.int32)
    me.loop_triangles.foreach_get("vertices", tris)
    tris = tris.reshape(-1, 3)
    p0 = evaluated_positions(obj)
    l0 = np.linalg.norm(p0[edges[:, 0]] - p0[edges[:, 1]], axis=1)
    a0 = 0.5 * np.linalg.norm(np.cross(p0[tris[:, 1]] - p0[tris[:, 0]], p0[tris[:, 2]] - p0[tris[:, 0]]), axis=1)
    pb = arm.pose.bones[bone]
    base = pb.matrix_basis.copy()
    out = []
    for axis in ("X", "Z"):
        for sign in (1, -1):
            pb.matrix_basis = base @ Matrix.Rotation(math.radians(sign * angle_deg), 4, axis)
            p1 = evaluated_positions(obj)
            l1 = np.linalg.norm(p1[edges[:, 0]] - p1[edges[:, 1]], axis=1)
            a1 = 0.5 * np.linalg.norm(np.cross(p1[tris[:, 1]] - p1[tris[:, 0]], p1[tris[:, 2]] - p1[tris[:, 0]]),
                                      axis=1)
            ok = l0 > 1e-12
            ratio = l1[ok] / l0[ok]
            okt = a0 > 1e-14
            out.append({"axis": axis, "sign": sign, "max_stretch": float(ratio.max()),
                        "min_ratio": float(ratio.min()),
                        "stretched_gt2": int((ratio > 2.0).sum()), "compressed_lt05": int((ratio < 0.5).sum()),
                        "collapsed": int((a1[okt] / a0[okt] < 0.05).sum())})
    pb.matrix_basis = base
    return out


def bone_colors(names):
    cols = {}
    for i, n in enumerate(names):
        r, g, b = colorsys.hsv_to_rgb((i * 0.618034) % 1.0, 0.75, 0.95)
        cols[n] = (r, g, b)
    return cols


def setup_render(scene, res):
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = scene.render.resolution_y = res
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "Standard"
    world = bpy.data.worlds.new("W")
    world.color = (0.18, 0.18, 0.18)
    scene.world = world
    cam = bpy.data.cameras.new("cam")
    cam.type = "ORTHO"
    cam_obj = bpy.data.objects.new("cam", cam)
    scene.collection.objects.link(cam_obj)
    scene.camera = cam_obj
    return cam_obj


def frame(cam_obj, pts, direction):
    d = Vector(direction).normalized()
    centre = Vector(((pts.max(0) + pts.min(0)) / 2).tolist())
    ext = float(np.linalg.norm(pts.max(0) - pts.min(0)))
    cam_obj.data.ortho_scale = ext * 1.1
    cam_obj.data.clip_start = 0.001
    cam_obj.data.clip_end = 100
    cam_obj.location = centre + d * (ext * 3)
    cam_obj.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()


def shading(scene, mode):
    sh = scene.display.shading
    if mode == "matcap":
        sh.light = "MATCAP"
        names = [s.name for s in bpy.context.preferences.studio_lights if s.type == "MATCAP"]
        sh.studio_light = "clay_studio.exr" if "clay_studio.exr" in names else names[0]
        sh.color_type = "SINGLE"
        sh.single_color = (0.8, 0.8, 0.8)
        sh.show_cavity = True
        sh.cavity_type = "BOTH"
    else:
        sh.light = "FLAT"
        sh.color_type = "VERTEX"
        sh.show_cavity = False
    sh.show_backface_culling = False


def add_weight_colors(obj, deform_names):
    me = obj.data
    cols = bone_colors(deform_names)
    gi_name = {g.index: g.name for g in obj.vertex_groups}
    vc = np.zeros((len(me.vertices), 4), np.float32)
    vc[:, 3] = 1.0
    for v in me.vertices:
        acc = np.zeros(3)
        tot = 0.0
        for g in v.groups:
            nm = gi_name.get(g.group)
            if nm in cols and g.weight > 0:
                acc += np.array(cols[nm]) * g.weight
                tot += g.weight
        vc[v.index, :3] = acc / tot if tot > 0 else (1.0, 0.0, 1.0)
    attr = me.color_attributes.new("weights_vis", "FLOAT_COLOR", "POINT")
    attr.data.foreach_set("color", vc.reshape(-1))
    me.color_attributes.active_color = attr
    return cols


def render(a, meshes, deform):
    """Rest + one render per --render-pose (all other pose channels at identity); matcap and weight colours."""
    os.makedirs(a.render_dir, exist_ok=True)
    scene = bpy.context.scene
    cam = setup_render(scene, a.res)
    obj = max(meshes, key=lambda o: len(o.data.vertices))
    arm = armature_of(obj)
    for o in bpy.data.objects:
        if o.type == "MESH" and o is not obj:
            o.hide_render = True
    if arm is not None:
        arm.hide_render = True
        for pb in arm.pose.bones:
            pb.matrix_basis = Matrix.Identity(4)
    rest = evaluated_positions(obj)
    mw = np.array(obj.matrix_world)
    to_world = lambda p: p @ mw[:3, :3].T + mw[:3, 3]  # noqa: E731
    view = (0.0, -1.0, 0.35)
    renders = []
    shots = [("rest", None, None, 0.0)]
    for spec in a.render_pose:
        bone, axis, angle = spec.split(":")
        if arm is not None and bone in arm.pose.bones:
            shots.append((f"{bone}_{axis}{int(float(angle))}", bone, axis, float(angle)))
    add_weight_colors(obj, a.color_bones.split(",") if a.color_bones else sorted(deform))
    for label, bone, axis, angle in shots:
        if bone:
            arm.pose.bones[bone].matrix_basis = Matrix.Rotation(math.radians(angle), 4, axis)
        posed = evaluated_positions(obj)
        frame(cam, np.concatenate([to_world(rest), to_world(posed)]), view)
        for mode in ("matcap", "weights"):
            shading(scene, mode)
            path = os.path.join(a.render_dir, f"{a.render_tag}_{label}_{mode}.png")
            scene.render.filepath = path
            bpy.ops.render.render(write_still=True)
            renders.append(path)
        if bone:
            arm.pose.bones[bone].matrix_basis = Matrix.Identity(4)
    return renders


def main():
    a = args_()
    load(a.input)
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    meshes = [o for o in bpy.data.objects if o.type == "MESH" and len(o.data.polygons) and not o.hide_viewport
              and o.name != "AMT_original"]
    info = {"armatures": len(arms), "bones": [], "pose": {}, "meshes": []}
    deform = set()
    for arm in arms:
        for b in arm.data.bones:
            info["bones"].append({"name": b.name, "deform": b.use_deform, "head": list(b.head_local),
                                  "tail": list(b.tail_local), "parent": b.parent.name if b.parent else None})
            if b.use_deform:
                deform.add(b.name)
        info["pose_position"] = arm.data.pose_position
        for pb in arm.pose.bones:
            info["pose"][pb.name] = {"rotation_mode": pb.rotation_mode, "location": list(pb.location),
                                     "rotation_quaternion": list(pb.rotation_quaternion),
                                     "rotation_euler": list(pb.rotation_euler), "scale": list(pb.scale)}
    for obj in meshes:
        arm = armature_of(obj)
        entries = weights_of(obj)
        names = {g.index: g.name for g in obj.vertex_groups}
        groups = {g.name: [0, 0.0] for g in obj.vertex_groups}
        for _, gi, w in entries:
            groups[names[gi]][0] += 1
            groups[names[gi]][1] += w
        n = len(obj.data.vertices)
        count = np.zeros(n, int)
        total = np.zeros(n)
        min_w = 1.0
        tiny = 0
        for vi, gi, w in entries:
            if names[gi] in deform and w > 0:
                count[vi] += 1
                total[vi] += w
                min_w = min(min_w, w)
                tiny += int(w < 1e-3)
        weighted = count > 0
        mesh_info = {
            "name": obj.name, "vertices": n, "faces": len(obj.data.polygons), "armature": arm.name if arm else None,
            "modifiers": [[m.type, m.name, getattr(m, "vertex_group", None)] for m in obj.modifiers],
            "groups": groups, "max_influences": int(count.max()) if n else 0,
            "unnormalized": int((weighted & (np.abs(total - 1.0) > 1e-3)).sum()),
            "unweighted": int((~weighted).sum()), "tiny": tiny, "min_weight": float(min_w),
            "max_sum_error": float(np.abs(total[weighted] - 1.0).max()) if weighted.any() else None,
        }
        co = positions(obj.data)
        probes = []
        for spec in a.probe:
            p = np.array([float(t) for t in spec.split(",")])
            vi = int(np.argmin(np.linalg.norm(co - p, axis=1)))
            probes.append({"probe": p.tolist(), "vertex": vi, "dist": float(np.linalg.norm(co[vi] - p)),
                           "weights": {names[g.group]: g.weight for g in obj.data.vertices[vi].groups}})
        mesh_info["probes"] = probes
        if a.pose_bone and arm is not None and a.pose_bone in arm.pose.bones:
            mesh_info["pose_test"] = pose_test(obj, arm, a.pose_bone, a.pose_angle)
        info["meshes"].append(mesh_info)

    if a.render_dir and meshes:
        info["renders"] = render(a, meshes, deform)
    print("@@RIGINFO@@" + json.dumps(info), flush=True)


main()
