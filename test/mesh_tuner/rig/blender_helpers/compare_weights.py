"""Compare the skin weights of two models by vertex position (runs INSIDE Blender 5.2; no img3d_amt imports).

blender --background --factory-startup --python-exit-code 86 --python compare_weights.py -- --a <ref> --b <test>

For every vertex of model B the nearest vertex of model A is found (KD-tree); prints ``@@CMPW@@{json}`` with the
L1 distance between their weight vectors (by bone name): mean / p95 / max, the worst vertices (position, both
weight dicts) and the fraction of matched vertices farther than 1e-5 apart.
"""

import argparse
import json
import os
import sys

import bpy
import numpy as np
from mathutils import kdtree


def args_():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    return p.parse_args(argv)


def load_weights(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".blend":
        bpy.ops.wm.open_mainfile(filepath=path)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if ext == ".fbx":
            bpy.ops.import_scene.fbx(filepath=path)
        else:
            bpy.ops.import_scene.gltf(filepath=path, disable_bone_shape=True)
    meshes = [o for o in bpy.data.objects if o.type == "MESH" and o.vertex_groups and o.name != "AMT_original"]
    obj = max(meshes, key=lambda o: len(o.data.vertices))
    deform = set()
    for o in bpy.data.objects:
        if o.type == "ARMATURE":
            deform |= {b.name for b in o.data.bones if b.use_deform}
    names = {g.index: g.name for g in obj.vertex_groups}
    co = np.array([tuple(obj.matrix_world @ v.co) for v in obj.data.vertices])
    weights = []
    for v in obj.data.vertices:
        d = {names[g.group]: g.weight for g in v.groups if names[g.group] in deform and g.weight > 0}
        s = sum(d.values()) or 1.0
        weights.append({k: w / s for k, w in d.items()})
    return co, weights


def main():
    a = args_()
    co_a, w_a = load_weights(a.a)
    co_b, w_b = load_weights(a.b)
    tree = kdtree.KDTree(len(co_a))
    for i, p in enumerate(co_a.tolist()):
        tree.insert(p, i)
    tree.balance()
    l1 = np.zeros(len(co_b))
    far = 0
    match = np.zeros(len(co_b), int)
    for i, p in enumerate(co_b.tolist()):
        _, j, d = tree.find(p)
        match[i] = j
        far += d > 1e-5
        keys = set(w_a[j]) | set(w_b[i])
        l1[i] = sum(abs(w_a[j].get(k, 0.0) - w_b[i].get(k, 0.0)) for k in keys)
    worst = np.argsort(-l1)[:8]
    out = {"vertices_b": len(co_b), "far_matches": int(far), "l1_mean": float(l1.mean()),
           "l1_p95": float(np.percentile(l1, 95)), "l1_max": float(l1.max()),
           "l1_gt_0_2": int((l1 > 0.2).sum()),
           "worst": [{"co": co_b[i].round(4).tolist(), "l1": round(float(l1[i]), 4),
                      "a": {k: round(v, 3) for k, v in w_a[match[i]].items()},
                      "b": {k: round(v, 3) for k, v in w_b[i].items()}} for i in worst]}
    print("@@CMPW@@" + json.dumps(out), flush=True)


main()
