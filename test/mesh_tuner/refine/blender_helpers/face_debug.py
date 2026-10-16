"""Blender helper (test only): run the face_refinement detection + corrections stage by stage on one mesh and
render diagnostic images (mask overlays, offsets, before/after matcap + texture close-ups).

blender --background --factory-startup --python-exit-code 86 --python face_debug.py -- \
    --scripts <backend/blender_scripts> --input <glb> --out <dir> [--res 800]
Prints "@@FACEDEBUG@@{json}".
"""

import argparse
import json
import os
import sys
import time

argv = sys.argv[sys.argv.index("--") + 1:]
ap = argparse.ArgumentParser()
ap.add_argument("--scripts", required=True)
ap.add_argument("--input", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--res", type=int, default=800)
ap.add_argument("--no-render", action="store_true")
a = ap.parse_args(argv)
sys.path.insert(0, a.scripts)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy  # noqa: E402
import numpy as np  # noqa: E402

from img3d_amt import face, io_mesh, meshdata, smoothing, symmetry  # noqa: E402
from img3d_amt.topology import MeshArrays  # noqa: E402
import render_util as ru  # noqa: E402

os.makedirs(a.out, exist_ok=True)
t0 = time.perf_counter()
io_mesh.reset_scene()
objs = io_mesh.import_file(a.input, os.path.splitext(a.input)[1].lstrip(".").lower())
obj = max((o for o in objs if o.type == "MESH"), key=lambda o: len(o.data.polygons))
me = obj.data
co0 = meshdata.get_positions(me)
diag = float(np.linalg.norm(co0.max(0) - co0.min(0)))
meshdata.weld_by_distance(me, 1e-6 * diag)
out = {"timings": {}}
T = out["timings"]
t = time.perf_counter()
arrays = MeshArrays(me)
T["arrays"] = time.perf_counter() - t
t = time.perf_counter()
score, _bvh = symmetry.measure(arrays.co, arrays.tris, arrays.diag)
T["measure"] = time.perf_counter() - t
out["symmetry"] = score.to_dict()
bmin, bmax = arrays.co.min(0), arrays.co.max(0)
t = time.perf_counter()
head = face.detect_head(arrays.co, bmin, bmax, center_x=score.plane.offset, edge=float(np.median(arrays.edge_len())))
T["head"] = time.perf_counter() - t
out["head"] = head.to_dict()
hum, why = face.humanoid_gate(bmin, bmax, score, head)
out["humanoid"] = [hum, why]
print("@@FACEDEBUG@@" + json.dumps(out))
if not hum:
    sys.exit(0)
co = arrays.co.copy()
t = time.perf_counter()
region = face.build_face_region(co, arrays, head)
T["region"] = time.perf_counter() - t
t = time.perf_counter()
colors = face.analyze_colors(obj, me, co, region, head, arrays.topo)
T["colors"] = time.perf_counter() - t
out["colors"] = face._color_details(colors)
t = time.perf_counter()
eyes = face.detect_eyes(co, arrays, region, head, colors, score.plane)
T["eyes"] = time.perf_counter() - t
out["eyes"] = {"found": eyes.found, "reason": eyes.reason, "method": eyes.method, "eyes": eyes.eyes,
               "candidates": eyes.candidates}
co1 = co
if eyes.found:
    t = time.perf_counter()
    co1, einfo = face.correct_eyes(co, arrays, region, eyes)
    T["eye_fix"] = time.perf_counter() - t
    out["eye_correction"] = einfo
t = time.perf_counter()
co2, cinfo = face.soften_midline_crease(co1, arrays, region, head, colors, eyes, score.plane)
T["crease"] = time.perf_counter() - t
out["crease"] = cinfo
t = time.perf_counter()
co3, dinfo = face.facial_denoise(co2, arrays, region, colors, eyes)
T["denoise"] = time.perf_counter() - t
out["denoise"] = dinfo
out["total_s"] = time.perf_counter() - t0
print("@@FACEDEBUG@@" + json.dumps(out))
if a.no_render:
    sys.exit(0)

# ------------------------------------------------------------------ renders
h = head.height
target = [head.center_x, head.center_y - 0.3 * h, head.z_neck + 0.5 * h]
scale = max(1.1 * h, 2.4 * head.half_width)
eye_scale = 0.55 * scale
meshdata.clear_custom_normals(me)
me.shade_smooth()
after = obj.copy()
after.data = me.copy()
bpy.context.scene.collection.objects.link(after)
meshdata.set_positions(after.data, co3)
mask_obj = obj.copy()
mask_obj.data = me.copy()
bpy.context.scene.collection.objects.link(mask_obj)
rgb = np.full((len(co), 3), 0.55)
if colors.available:
    c = np.where(np.isnan(colors.colors), 0.55, colors.colors)
    rgb = 0.35 * c + 0.65 * 0.55
rgb[region.mask] = rgb[region.mask] * 0.7 + np.array([0.2, 0.3, 0.6]) * 0.3
if colors.available:
    rgb[colors.skin] = np.array([0.55, 0.75, 0.55])
    rgb[colors.eye_like & region.mask] = np.array([0.9, 0.3, 0.3])
    rgb[colors.hair_like & region.mask] = np.array([0.45, 0.35, 0.6])
if eyes.found:
    rgb[eyes.mask] = np.array([1.0, 0.85, 0.1])
ru.set_vertex_colors(mask_obj.data, rgb)
loc = region.local / float(np.median(arrays.edge_len()))
heat = np.full((len(co), 3), 0.5)
heat[:, 0] = np.clip(0.5 + loc * 0.5, 0, 1)
heat[:, 2] = np.clip(0.5 - loc * 0.5, 0, 1)
heat[:, 1] = 0.5 - np.abs(np.clip(loc * 0.5, -0.5, 0.5))
heat_obj = obj.copy()
heat_obj.data = me.copy()
bpy.context.scene.collection.objects.link(heat_obj)
ru.set_vertex_colors(heat_obj.data, heat)
ru.setup_scene(a.res)
front = (0, -1, 0)
tq = (0.55, -0.8, 0.12)
ru.render(os.path.join(a.out, "mask_front.png"), target, scale, front, [mask_obj], mode="attribute")
ru.render(os.path.join(a.out, "local_offset_front.png"), target, scale, front, [heat_obj], mode="attribute")
for nm, d in (("front", front), ("34", tq)):
    ru.render(os.path.join(a.out, f"face_{nm}_before.png"), target, scale, d, [obj], mode="matcap")
    ru.render(os.path.join(a.out, f"face_{nm}_after.png"), target, scale, d, [after], mode="matcap")
ru.render(os.path.join(a.out, "face_tex_before.png"), target, scale, front, [obj], mode="texture")
ru.render(os.path.join(a.out, "face_tex_after.png"), target, scale, front, [after], mode="texture")
if eyes.found:
    for i, e in enumerate(eyes.eyes):
        tgt = [e["center"][0], e["center"][1] - 0.05, e["center"][2]]
        ru.render(os.path.join(a.out, f"eye{i}_before.png"), tgt, 0.3 * scale, front, [obj], mode="matcap")
        ru.render(os.path.join(a.out, f"eye{i}_after.png"), tgt, 0.3 * scale, front, [after], mode="matcap")
