"""Scale test of the rig step on a REAL TRELLIS mesh (runs INSIDE Blender 5.2).

blender --background --factory-startup --python-exit-code 86 --python perf_rig.py -- --input <job model.glb>
    --out-dir <dir> [--render]

Imports the GLB with the framework loader (weld etc.), builds a 16-bone humanoid armature from the bbox (T-pose,
+Z up, front -Y, left +X), skins the mesh with inverse-distance weights to the 3 nearest bone segments, injects
defects (an unweighted box of vertices on the left forearm, 5 single-vertex spikes weighted to a foot bone, 1e-5
weights everywhere on the head bone), then runs ``img3d_amt.rig.run`` on a real ``TunerContext`` and prints
``@@PERF@@{json}`` (timings, fixes, pose summary, checks). With --render: Workbench matcap renders (left arm raised
+45 deg about X) before/after into --out-dir. The input file is only read.
"""

import argparse
import json
import math
import os
import sys
import time

import bpy
import numpy as np
from mathutils import Matrix

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", "backend", "blender_scripts"))
sys.path.insert(0, SCRIPTS)

from img3d_amt import io_mesh, rig  # noqa: E402
from img3d_amt.config import TunerConfig, TuningOptions  # noqa: E402
from img3d_amt.context import STEP_SPECS, TunerContext  # noqa: E402

BONES = [  # name, parent, head, tail (T-pose humanoid in the TRELLIS unit cube)
    ("hips", None, (0, 0, -0.08), (0, 0, 0.05)),
    ("spine", "hips", (0, 0, 0.05), (0, 0, 0.18)),
    ("chest", "spine", (0, 0, 0.18), (0, 0, 0.27)),
    ("neck", "chest", (0, 0, 0.27), (0, 0, 0.32)),
    ("head", "neck", (0, 0, 0.32), (0, 0, 0.5)),
    ("upper_arm.L", "chest", (0.06, 0, 0.24), (0.2, 0, 0.24)),
    ("forearm.L", "upper_arm.L", (0.2, 0, 0.24), (0.33, 0, 0.24)),
    ("hand.L", "forearm.L", (0.33, 0, 0.24), (0.45, 0, 0.24)),
    ("upper_arm.R", "chest", (-0.06, 0, 0.24), (-0.2, 0, 0.24)),
    ("forearm.R", "upper_arm.R", (-0.2, 0, 0.24), (-0.33, 0, 0.24)),
    ("hand.R", "forearm.R", (-0.33, 0, 0.24), (-0.45, 0, 0.24)),
    ("thigh.L", "hips", (0.07, 0, -0.08), (0.07, 0, -0.28)),
    ("shin.L", "thigh.L", (0.07, 0, -0.28), (0.07, 0, -0.46)),
    ("foot.L", "shin.L", (0.07, 0, -0.46), (0.07, -0.06, -0.5)),
    ("thigh.R", "hips", (-0.07, 0, -0.08), (-0.07, 0, -0.28)),
    ("shin.R", "thigh.R", (-0.07, 0, -0.28), (-0.07, 0, -0.46)),
]


def args_():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--render", action="store_true")
    p.add_argument("--no-stray", action="store_true", help="disable the stray-influence rule (A/B comparison)")
    return p.parse_args(argv)


def seg_dist(p, h, t):
    ht = t - h
    s = np.clip(((p - h) @ ht) / max(float(ht @ ht), 1e-12), 0, 1)
    return np.linalg.norm(p - (h + s[:, None] * ht), axis=1)


def build_rig(obj):
    arm_data = bpy.data.armatures.new("rig")
    arm = bpy.data.objects.new("rig", arm_data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    ebs = {}
    for name, parent, h, t in BONES:
        eb = arm_data.edit_bones.new(name)
        eb.head, eb.tail = h, t
        if parent:
            eb.parent = ebs[parent]
        ebs[name] = eb
    bpy.ops.object.mode_set(mode="OBJECT")
    co = np.empty(len(obj.data.vertices) * 3, np.float32)
    obj.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3).astype(np.float64)
    d = np.stack([seg_dist(co, np.array(h, float), np.array(t, float)) for _, _, h, t in BONES], 1)
    near = np.argsort(d, 1)[:, :3]
    dn = np.take_along_axis(d, near, 1)
    w = 1.0 / np.maximum(dn, 1e-4) ** 4
    w /= w.sum(1, keepdims=True)
    W = np.zeros_like(d)
    np.put_along_axis(W, near, w, 1)
    # defects
    box = (co[:, 0] > 0.25) & (co[:, 0] < 0.3) & (co[:, 2] > 0.22)  # unweighted patch, left forearm top
    W[box] = 0.0
    rng = np.random.default_rng(1)
    torso = np.nonzero((np.abs(co[:, 0]) < 0.05) & (co[:, 2] > 0.0) & (co[:, 2] < 0.15))[0]
    spikes = rng.choice(torso, 5, replace=False)
    W[spikes] = 0.0
    W[spikes, [n for n, *_ in BONES].index("foot.L")] = 1.0
    t0 = time.perf_counter()
    groups = [obj.vertex_groups.new(name=n) for n, *_ in BONES]
    head_i = [n for n, *_ in BONES].index("head")
    for gi, g in enumerate(groups):
        idx = np.nonzero(W[:, gi] > 1e-6)[0]
        for vi, wi in zip(idx.tolist(), W[idx, gi].tolist()):
            g.add([vi], wi, "REPLACE")
    tiny = np.nonzero(W[:, head_i] == 0)[0][::7]
    for vi in tiny.tolist():
        groups[head_i].add([vi], 1e-5, "REPLACE")
    write_s = time.perf_counter() - t0
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    obj.parent = arm
    return arm, {"box_verts": int(box.sum()), "spikes": spikes.tolist(), "tiny": int(len(tiny)),
                 "build_write_s": round(write_s, 3)}


def render(obj, arm, path):
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = scene.render.resolution_y = 768
    scene.view_settings.view_transform = "Standard"
    world = bpy.data.worlds.get("W") or bpy.data.worlds.new("W")
    world.color = (0.18, 0.18, 0.18)
    scene.world = world
    sh = scene.display.shading
    sh.light = "MATCAP"
    sh.studio_light = "clay_studio.exr"
    sh.color_type = "SINGLE"
    sh.show_cavity = True
    sh.cavity_type = "BOTH"
    cam = bpy.data.objects.get("cam")
    if cam is None:
        cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
        scene.collection.objects.link(cam)
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 1.15
    cam.location = (0.0, -3.0, 0.0)
    cam.rotation_euler = (math.radians(90), 0, 0)
    scene.camera = cam
    for o in bpy.data.objects:
        if o.type == "MESH" and o is not obj:
            o.hide_render = True
    pb = arm.pose.bones["upper_arm.L"]
    pb.matrix_basis = Matrix.Rotation(math.radians(45), 4, "X")
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    pb.matrix_basis = Matrix.Identity(4)


def main():
    a = args_()
    os.makedirs(a.out_dir, exist_ok=True)
    if a.no_stray:
        rig.STRAY_MAX_WEIGHT = 0.0
    cfg = TunerConfig(input_path=os.path.abspath(a.input), output_dir=os.path.abspath(a.out_dir),
                      options=TuningOptions.from_dict({"render_previews": False}))
    ctx = TunerContext(cfg)
    t0 = time.perf_counter()
    io_mesh.load_input(ctx)
    load_s = time.perf_counter() - t0
    obj = ctx.require_primary()
    arm, info = build_rig(obj)
    ctx.armatures = [arm]
    if a.render:
        render(obj, arm, os.path.join(a.out_dir, "trellis_rig_before_armL45.png"))
    spec = next(s for s in STEP_SPECS if s.id == "rig_optimization")
    ctx.begin_step(spec, 0.5)
    t0 = time.perf_counter()
    outcome = rig.run(ctx)
    step_s = time.perf_counter() - t0
    ctx.end_step()
    if a.render:
        render(obj, arm, os.path.join(a.out_dir, "trellis_rig_after_armL45.png"))
    mesh = outcome.details["meshes"][0]
    out = {"vertices": len(obj.data.vertices), "load_s": round(load_s, 2), "step_s": round(step_s, 2),
           "status": outcome.status, "message": outcome.message, "inject": info,
           "timings": outcome.details["timings_s"], "fixes": mesh["fixes"], "before": mesh["before"],
           "after": mesh["after"], "pose_before": {k: v for k, v in (mesh["pose_before"] or {}).items()
                                                   if k != "per_bone"},
           "pose_after": {k: v for k, v in (mesh["pose_after"] or {}).items() if k != "per_bone"},
           "per_bone_after": (mesh["pose_after"] or {}).get("per_bone"),
           "checks": ctx.store.get("pose_checks"), "metrics": ctx.metric_overrides,
           "memory_mb": ctx.memory()}
    ctx.cleanup()
    print("@@PERF@@" + json.dumps(out, default=str), flush=True)


main()
