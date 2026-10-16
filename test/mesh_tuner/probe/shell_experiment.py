"""Probe: is the TRELLIS.2 remeshed GLB a hollow double-walled shell, and does Blender voxel remesh close it?

Run:
  blender --background --factory-startup --python-exit-code 1 --python shell_experiment.py -- --input <glb> --out <dir>
     [--voxel 0.002] [--samples 4000]
Writes <out>/shell_experiment.json. Probe only.
"""
import argparse
import json
import math
import os
import sys
import time

import bpy
import numpy as np
from mathutils import Vector, bvhtree


def args_():
    argv = sys.argv[sys.argv.index("--") + 1:]
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--voxel", type=float, nargs="+", default=[0.002, 0.004])
    p.add_argument("--samples", type=int, default=4000)
    p.add_argument("--rays", type=int, default=24)
    return p.parse_args(argv)


def tri_arrays(me):
    me.calc_loop_triangles()
    T = len(me.loop_triangles)
    tv = np.empty(T * 3, np.int32); me.loop_triangles.foreach_get("vertices", tv)
    co = np.empty(len(me.vertices) * 3, np.float32); me.vertices.foreach_get("co", co)
    return co.reshape(-1, 3).astype(np.float64), tv.reshape(-1, 3)


def signed_volume(co, tv):
    v0, v1, v2 = co[tv[:, 0]], co[tv[:, 1]], co[tv[:, 2]]
    return float((v0 * np.cross(v1, v2)).sum() / 6)


def area(co, tv):
    v0, v1, v2 = co[tv[:, 0]], co[tv[:, 1]], co[tv[:, 2]]
    return float(np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1).sum() / 2)


def visibility(obj, n_samples, n_rays, diag, seed=0):
    """Fraction of sampled faces from which at least one ray in the normal hemisphere escapes to infinity."""
    dg = bpy.context.evaluated_depsgraph_get()
    bvh = bvhtree.BVHTree.FromObject(obj, dg)
    me = obj.data
    F = len(me.polygons)
    pn = np.empty(F * 3, np.float32); me.polygons.foreach_get("normal", pn); pn = pn.reshape(-1, 3)
    pc = np.empty(F * 3, np.float32); me.polygons.foreach_get("center", pc); pc = pc.reshape(-1, 3)
    rng = np.random.default_rng(seed)
    idx = rng.choice(F, size=min(n_samples, F), replace=False)
    dirs = rng.normal(size=(n_rays, 3)); dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    eps = 1e-5 * diag
    vis = 0
    vis_back = 0
    thick = []
    for fi in idx:
        n = Vector(pn[fi]); c = Vector(pc[fi])
        seen = False
        for d in dirs:
            dv = Vector(d)
            if dv.dot(n) < 0:
                dv = -dv
            loc, _, _, _ = bvh.ray_cast(c + n * eps, dv, diag * 4)
            if loc is None:
                seen = True
                break
        vis += seen
        # back side visible? (sheet geometry: both sides see the outside)
        seenb = False
        for d in dirs:
            dv = Vector(d)
            if dv.dot(n) > 0:
                dv = -dv
            loc, _, _, _ = bvh.ray_cast(c - n * eps, dv, diag * 4)
            if loc is None:
                seenb = True
                break
        vis_back += seenb
        loc, _, _, dist = bvh.ray_cast(c - n * eps, -n, diag * 4)
        if loc is not None:
            thick.append(dist)
    thick = np.array(thick)
    return {"samples": int(len(idx)), "rays_per_face": n_rays,
            "front_side_visible_from_outside_frac": round(vis / len(idx), 4),
            "back_side_visible_from_outside_frac": round(vis_back / len(idx), 4),
            "thickness_p50_BU": float(np.percentile(thick, 50)) if len(thick) else None,
            "thickness_p10_BU": float(np.percentile(thick, 10)) if len(thick) else None,
            "thickness_p90_BU": float(np.percentile(thick, 90)) if len(thick) else None}


def main():
    a = args_()
    os.makedirs(a.out, exist_ok=True)
    R = {"input": a.input}
    bpy.ops.wm.read_factory_settings(use_empty=True)
    t = time.perf_counter()
    bpy.ops.import_scene.gltf(filepath=a.input, merge_vertices=True)
    R["import_merge_s"] = round(time.perf_counter() - t, 2)
    obj = max((o for o in bpy.data.objects if o.type == "MESH"), key=lambda o: len(o.data.polygons))
    co, tv = tri_arrays(obj.data)
    diag = float(np.linalg.norm(co.max(0) - co.min(0)))
    R["original"] = {"faces": len(obj.data.polygons), "verts": len(obj.data.vertices), "signed_volume": signed_volume(co, tv),
                     "area": area(co, tv)}
    R["original"]["volume_over_half_area_BU_(=wall_thickness_if_hollow_shell)"] = R["original"]["signed_volume"] / (R["original"]["area"] / 2)
    t = time.perf_counter()
    R["original"]["visibility"] = visibility(obj, a.samples, a.rays, diag)
    R["original"]["visibility_s"] = round(time.perf_counter() - t, 2)
    src = obj
    for vs in a.voxel:
        bpy.ops.object.select_all(action="DESELECT")
        dup = src.copy(); dup.data = src.data.copy(); bpy.context.scene.collection.objects.link(dup)
        bpy.context.view_layer.objects.active = dup; dup.select_set(True)
        dup.data.remesh_voxel_size = vs
        dup.data.remesh_voxel_adaptivity = 0.0
        t = time.perf_counter()
        ret = bpy.ops.object.voxel_remesh()
        dt = round(time.perf_counter() - t, 2)
        co2, tv2 = tri_arrays(dup.data)
        res = {"voxel_size": vs, "voxel_size_in_trellis_cells_1536": round(vs * 1536, 2), "op": list(ret), "seconds": dt,
               "faces": len(dup.data.polygons), "verts": len(dup.data.vertices), "signed_volume": signed_volume(co2, tv2),
               "area": area(co2, tv2), "uv_layers_after": [u.name for u in dup.data.uv_layers],
               "materials_after": [m.name for m in dup.data.materials if m]}
        bpy.context.view_layer.update()
        res["visibility"] = visibility(dup, min(a.samples, 2000), a.rays, diag)
        R[f"voxel_remesh_{vs}"] = res
        bpy.data.objects.remove(dup)
    with open(os.path.join(a.out, "shell_experiment.json"), "w") as f:
        json.dump(R, f, indent=1)
    print("@@RESULT@@" + json.dumps(R))


main()
