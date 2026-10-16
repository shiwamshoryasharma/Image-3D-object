"""Exploration (test only): z-profiles for head/neck detection on real TRELLIS meshes.

blender --background --factory-startup --python profile_head.py -- <glb> [<glb> ...]
"""
import sys

import bpy
import numpy as np


def load(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=path, merge_vertices=True)
    obj = max((o for o in bpy.data.objects if o.type == "MESH"), key=lambda o: len(o.data.polygons))
    me = obj.data
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    return co.reshape(-1, 3).astype(np.float64)


def central_run(xs, center, binw):
    if len(xs) == 0:
        return 0.0
    lo = xs.min()
    idx = np.floor((xs - lo) / binw).astype(int)
    occ = np.zeros(idx.max() + 2, bool)
    occ[idx] = True
    c = int(np.floor((center - lo) / binw))
    if c < 0 or c >= len(occ) or not occ[c]:
        # nearest occupied
        occ_idx = np.nonzero(occ)[0]
        c = occ_idx[np.argmin(np.abs(occ_idx - c))]
    a = c
    while a > 0 and occ[a - 1]:
        a -= 1
    b = c
    while b + 1 < len(occ) and occ[b + 1]:
        b += 1
    return (b - a + 1) * binw


for path in sys.argv[sys.argv.index("--") + 1:]:
    co = load(path)
    bmin, bmax = co.min(0), co.max(0)
    H = bmax[2] - bmin[2]
    print("==", path, "bbox", bmin.round(3), bmax.round(3), "H", round(H, 3))
    print(" z%   allW  runW  frontRunW  frontY(mid)  backY  n")
    for k in range(0, 60):
        z1 = bmax[2] - k * 0.01 * H
        z0 = z1 - 0.01 * H
        s = co[(co[:, 2] >= z0) & (co[:, 2] < z1)]
        if len(s) < 5:
            print(f"{k:3d}  empty")
            continue
        allw = np.percentile(s[:, 0], 99) - np.percentile(s[:, 0], 1)
        runw = central_run(s[:, 0], 0.0, 0.0025 * H)
        ymid = np.median(s[:, 1])
        fr = s[s[:, 1] < ymid]
        frw = central_run(fr[:, 0], 0.0, 0.0025 * H)
        mid = s[np.abs(s[:, 0]) < 0.01 * H]
        fy = mid[:, 1].min() if len(mid) else np.nan
        by = mid[:, 1].max() if len(mid) else np.nan
        print(f"{k:3d} {allw:6.3f} {runw:6.3f} {frw:6.3f} {fy:8.4f} {by:8.4f} {len(s)}")
