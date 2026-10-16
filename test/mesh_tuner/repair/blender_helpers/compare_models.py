"""Blender helper (repair tests): before/after comparison of an original GLB and a tuned GLB.

  blender --background --factory-startup --python-exit-code 86 --python compare_models.py -- \
      --scripts <backend/blender_scripts> --original <glb> --tuned <glb> --out <dir> [--res 900] [--no-renders]

Writes into <out>:
* ``cmp_<view>.png``   before | after Workbench matcap+cavity renders with BACK-FACE CULLING (what a glTF viewer
  shows for doubleSided=false materials): front, back, left, three_quarter, back_three_quarter, below_front;
  ``cmp_back_flat.png`` (flat shading), ``cmp_face_matcap.png`` / ``cmp_face_texture.png`` (humanoids).
* ``slice_<head|mid>[_zoom].png`` before (left) | after (right) cross-sections.
* ``depth_<view>.png`` heat map of |depth after - depth before| (front-facing surfaces, 6 axis views).
* ``compare.json``: wall-pair fractions, depth statistics (14 views), slice segment counts, timings.
Prints ``@@COMPARE@@<json>``.
"""

import argparse
import json
import os
import sys
import time

sys.stdout.reconfigure(line_buffering=True)
argv = sys.argv[sys.argv.index("--") + 1:]
ap = argparse.ArgumentParser()
ap.add_argument("--scripts", required=True)
ap.add_argument("--original", required=True)
ap.add_argument("--tuned", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--res", type=int, default=900)
ap.add_argument("--no-renders", action="store_true")
ap.add_argument("--holes", default=None, help="JSON file with a list of filled holes ({center, edges, ...})")
ap.add_argument("--max-holes", type=int, default=6)
args = ap.parse_args(argv)
sys.path.insert(0, args.scripts)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Vector  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402

import amt_render as R  # noqa: E402
from img3d_amt import visibility  # noqa: E402

os.makedirs(args.out, exist_ok=True)
T0 = time.perf_counter()
bpy.ops.wm.read_factory_settings(use_empty=True)


def load(path, name):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path, merge_vertices=False)
    objs = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    obj = max(objs, key=lambda o: len(o.data.polygons))
    obj.name = name
    for o in objs:
        if o is not obj:
            bpy.data.objects.remove(o)
    return obj


orig = load(args.original, "before")
tuned = load(args.tuned, "after")
co_o, tv_o = R.mesh_arrays(orig.data)
co_t, tv_t = R.mesh_arrays(tuned.data)
bmin = np.minimum(co_o.min(0), co_t.min(0))
bmax = np.maximum(co_o.max(0), co_t.max(0))
diag = float(np.linalg.norm(bmax - bmin))
result = {"original": args.original, "tuned": args.tuned, "diag": diag,
          "faces_before": len(orig.data.polygons), "faces_after": len(tuned.data.polygons)}
dims = bmax - bmin
humanoid = bool(dims[2] >= dims[1] and dims[1] <= 0.6 * dims[2] and dims[0] <= 1.15 * dims[2])
result["humanoid"] = humanoid


# ------------------------------------------------------------------------------------------------ wall pairs
def wall_pair_fraction(co, tv, samples=20000, t_max=0.004, seed=0):
    """Fraction of sampled faces with an anti-parallel wall within t_max behind them (double-wall indicator)."""
    p0 = co[tv[:, 0]]
    n = np.cross(co[tv[:, 1]] - p0, co[tv[:, 2]] - p0)
    area = 0.5 * np.linalg.norm(n, axis=1)
    ok = area > 0
    nrm = np.zeros_like(n)
    nrm[ok] = n[ok] / (2 * area[ok, None])
    rng = np.random.default_rng(seed)
    p = area / area.sum()
    idx = rng.choice(len(tv), size=min(samples, len(tv)), replace=True, p=p)
    bvh = BVHTree.FromPolygons(co.tolist(), tv.tolist(), all_triangles=True)
    cen = (co[tv[idx, 0]] + co[tv[idx, 1]] + co[tv[idx, 2]]) / 3
    eps = 1e-6 * diag
    hits = 0
    for c, nn in zip(cen.tolist(), nrm[idx].tolist()):
        o = Vector(c) - Vector(nn) * eps
        loc, hn, fi, d = bvh.ray_cast(o, -Vector(nn), t_max)
        if loc is not None and float(np.dot(nrm[fi], nn)) < -0.7:
            hits += 1
    return hits / len(idx)


t = time.perf_counter()
result["double_wall_fraction_before"] = round(wall_pair_fraction(co_o, tv_o), 4)
result["double_wall_fraction_after"] = round(wall_pair_fraction(co_t, tv_t), 4)
result["wall_pairs_s"] = round(time.perf_counter() - t, 2)


# ------------------------------------------------------------------------------------------------ depth maps
def depth_stats():
    out = {"views": []}
    res = 1536
    dirs = visibility.view_directions(8)
    heat_views = {"x+": 0, "x-": 1, "y+": 2, "y-": 3, "z+": 4, "z-": 5}
    agg = {"both": 0, "before_only": 0, "after_only": 0, "abs": [], "closer": 0, "farther": 0}
    with visibility.GpuIdRenderer(co_o, tv_o, res) as r_o, visibility.GpuIdRenderer(co_t, tv_t, res) as r_t:
        for k, d in enumerate(dirs):
            frame = visibility.frame_view(bmin, bmax, d)
            ids_o, dep_o = r_o.render(frame, cull="BACK")
            ids_t, dep_t = r_t.render(frame, cull="BACK")
            co_ = ids_o != 0
            ct_ = ids_t != 0
            both = co_ & ct_
            delta = (dep_t - dep_o) * frame.depth_scale  # >0: after surface farther away (receded)
            dv = delta[both]
            tol = 0.005 * diag
            view = {"dir": [round(float(x), 3) for x in d], "covered_before": int(co_.sum()),
                    "both": int(both.sum()), "before_only": int((co_ & ~ct_).sum()),
                    "after_only": int((ct_ & ~co_).sum()),
                    "mean_abs_pct": round(100 * float(np.abs(dv).mean()) / diag, 5) if len(dv) else None,
                    "p99_abs_pct": round(100 * float(np.percentile(np.abs(dv), 99)) / diag, 5) if len(dv) else None,
                    "max_abs_pct": round(100 * float(np.abs(dv).max()) / diag, 4) if len(dv) else None,
                    "receded_gt_0p5pct": int((dv > tol).sum()), "closer_gt_0p5pct": int((dv < -tol).sum())}
            out["views"].append(view)
            agg["both"] += view["both"]
            agg["before_only"] += view["before_only"]
            agg["after_only"] += view["after_only"]
            agg["closer"] += view["closer_gt_0p5pct"]
            agg["farther"] += view["receded_gt_0p5pct"]
            if len(dv):
                agg["abs"].append(np.abs(dv)[:: max(1, len(dv) // 200000)])
            if k < 6 and not args.no_renders:
                name = list(heat_views)[k]
                img = np.full((res, res, 3), 0.15, np.float32)
                cov = co_.reshape(res, res)
                img[cov] = 0.35
                mag = np.zeros(res * res, np.float32)
                mag[both] = np.clip(np.abs(delta[both]) / (0.01 * diag), 0, 1)
                mag = mag.reshape(res, res)
                hot = both.reshape(res, res) & (mag > 0.02)
                img[hot, 0] = 0.35 + 0.65 * mag[hot]
                img[hot, 1] = 0.35 * (1 - mag[hot])
                img[hot, 2] = 0.35 * (1 - mag[hot])
                lost = (co_ & ~ct_).reshape(res, res)
                img[lost] = (0.1, 0.4, 1.0)
                gain = (ct_ & ~co_).reshape(res, res)
                img[gain] = (0.1, 1.0, 0.3)
                im = bpy.data.images.new(f"depth_{name}", res, res, alpha=False)
                rgba = np.ones((res, res, 4), np.float32)
                rgba[..., :3] = img
                im.pixels.foreach_set(rgba.ravel())
                im.filepath_raw = os.path.join(args.out, f"depth_{name}.png")
                im.file_format = "PNG"
                im.save()
                bpy.data.images.remove(im)
    allabs = np.concatenate(agg["abs"]) if agg["abs"] else np.zeros(1)
    tot = max(agg["both"] + agg["before_only"], 1)
    out["summary"] = {"views": len(dirs), "resolution": res,
                      "mean_abs_pct": round(100 * float(allabs.mean()) / diag, 5),
                      "p99_abs_pct": round(100 * float(np.percentile(allabs, 99)) / diag, 5),
                      "max_abs_pct": round(max(v["max_abs_pct"] or 0 for v in out["views"]), 4),
                      "receded_frac": round(agg["farther"] / tot, 6), "closer_frac": round(agg["closer"] / tot, 6),
                      "silhouette_lost_frac": round(agg["before_only"] / tot, 6),
                      "silhouette_gained_frac": round(agg["after_only"] / tot, 6)}
    return out


t = time.perf_counter()
result["depth"] = depth_stats()
result["depth_s"] = round(time.perf_counter() - t, 2)


# ------------------------------------------------------------------------------------------------ slices
def hstack_save(paths, out_path):
    imgs = []
    for p in paths:
        im = bpy.data.images.load(p, check_existing=False)
        w, h = im.size
        px = np.empty(w * h * 4, np.float32)
        im.pixels.foreach_get(px)
        imgs.append(px.reshape(h, w, 4))
        bpy.data.images.remove(im)
    sep = np.ones((imgs[0].shape[0], 6, 4), np.float32)
    sep[..., :3] = 1.0
    parts = []
    for i, a in enumerate(imgs):
        if i:
            parts.append(sep)
        parts.append(a)
    out = np.concatenate(parts, axis=1)
    h, w = out.shape[:2]
    im = bpy.data.images.new(os.path.basename(out_path), w, h, alpha=True)
    im.pixels.foreach_set(out.ravel())
    im.filepath_raw = out_path
    im.file_format = "PNG"
    im.save()
    bpy.data.images.remove(im)
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


slices = {}
if humanoid:
    hc, hs = R.head_center(co_o)
    zmin, zmax = co_o[:, 2].min(), co_o[:, 2].max()
    torso_z = zmin + 0.62 * (zmax - zmin)
    mid_slab = co_o[np.abs(co_o[:, 2] - torso_z) < 0.01]
    cx = float(np.median(mid_slab[:, 0])) if len(mid_slab) else 0.0
    cy = float(np.median(mid_slab[:, 1])) if len(mid_slab) else 0.0
    specs = [("head", float(hc[2]), (hc[0] - 0.6 * hs, hc[0] + 0.6 * hs, hc[1] - 0.6 * hs, hc[1] + 0.6 * hs)),
             ("mid", float(torso_z), (cx - 0.1, cx + 0.1, cy - 0.1, cy + 0.1))]
else:
    zmin, zmax = co_o[:, 2].min(), co_o[:, 2].max()
    c = 0.5 * (bmin + bmax)
    half = 0.55 * max(dims[0], dims[1])
    specs = [("upper", float(zmin + 0.75 * (zmax - zmin)), (c[0] - half, c[0] + half, c[1] - half, c[1] + half)),
             ("mid", float(zmin + 0.5 * (zmax - zmin)), (c[0] - half, c[0] + half, c[1] - half, c[1] + half))]
for name, zval, win in specs:
    s_o = R.slice_segments(co_o, tv_o, 2, zval)
    s_t = R.slice_segments(co_t, tv_t, 2, zval)
    slices[name] = {"z": round(zval, 4), "segments_before": int(len(s_o)), "segments_after": int(len(s_t))}
    if args.no_renders:
        continue
    pa = R.draw_slices([(s_o, (0, 0, 0))], 0, 1, win, 900, os.path.join(args.out, f"_s_{name}_b.png"))
    pb = R.draw_slices([(s_t, (0.8, 0, 0))], 0, 1, win, 900, os.path.join(args.out, f"_s_{name}_a.png"))
    hstack_save([pa, pb], os.path.join(args.out, f"slice_{name}.png"))
    # zoom on the front-most (min y) after-segment near the window centre
    if len(s_t):
        mid = s_t.mean(1)
        cxw = 0.5 * (win[0] + win[1])
        near = np.abs(mid[:, 0] - cxw) < 0.25 * (win[1] - win[0])
        k = int(np.argmin(np.where(near, mid[:, 1], np.inf)))
        zu, zv, w = mid[k, 0], mid[k, 1], 0.012
        zw = (zu - w, zu + w, zv - w, zv + w)
        pa = R.draw_slices([(s_o, (0, 0, 0))], 0, 1, zw, 900, os.path.join(args.out, f"_s_{name}_zb.png"))
        pb = R.draw_slices([(s_t, (0.8, 0, 0))], 0, 1, zw, 900, os.path.join(args.out, f"_s_{name}_za.png"))
        hstack_save([pa, pb], os.path.join(args.out, f"slice_{name}_zoom.png"))
result["slices"] = slices

# ------------------------------------------------------------------------------------------------ renders
if not args.no_renders:
    t = time.perf_counter()
    R.setup_scene(args.res)
    views = ["front", "back", "left", "three_quarter", "back_three_quarter", "below_front"]
    pts = np.concatenate([co_o[:: max(1, len(co_o) // 100000)], co_t[:: max(1, len(co_t) // 100000)]])

    def frame_for(direction):
        dd = np.array(direction, float)
        dd /= np.linalg.norm(dd)
        up = np.array([0.0, 0.0, 1.0]) if abs(dd[2]) < 0.99 else np.array([0.0, 1.0, 0.0])
        u = up - dd * (up @ dd)
        u /= np.linalg.norm(u)
        r = np.cross(dd, u)
        pu, pr = pts @ u, pts @ r
        target = u * 0.5 * (pu.max() + pu.min()) + r * 0.5 * (pr.max() + pr.min()) + dd * float((pts @ dd).mean())
        return target.tolist(), float(max(pu.max() - pu.min(), pr.max() - pr.min()) * 1.06)

    for view in views:
        target, scale = frame_for(R.VIEWS[view])
        R.shading("matcap", backface=True)
        pb = R.render_view(os.path.join(args.out, f"_b_{view}.png"), [orig], R.VIEWS[view], target, scale)
        pa = R.render_view(os.path.join(args.out, f"_a_{view}.png"), [tuned], R.VIEWS[view], target, scale)
        hstack_save([pb, pa], os.path.join(args.out, f"cmp_{view}.png"))
    # flat shading (exposes facets / stair steps)
    target, scale = frame_for(R.VIEWS["back"])
    for o in (orig, tuned):
        R.shade_flat(o)
    R.shading("studio", backface=True)
    pb = R.render_view(os.path.join(args.out, "_b_flat.png"), [orig], R.VIEWS["back"], target, scale)
    pa = R.render_view(os.path.join(args.out, "_a_flat.png"), [tuned], R.VIEWS["back"], target, scale)
    hstack_save([pb, pa], os.path.join(args.out, "cmp_back_flat.png"))
    if humanoid:
        for o in (orig, tuned):
            o.data.shade_smooth()
        hc, hs = R.head_center(co_o)
        target = (float(hc[0]), float(hc[1]), float(hc[2]))
        for mode in ("matcap", "texture"):
            R.shading(mode, backface=True)
            pb = R.render_view(os.path.join(args.out, f"_b_face_{mode}.png"), [orig], R.VIEWS["front"], target, hs * 1.3)
            pa = R.render_view(os.path.join(args.out, f"_a_face_{mode}.png"), [tuned], R.VIEWS["front"], target, hs * 1.3)
            hstack_save([pb, pa], os.path.join(args.out, f"cmp_face_{mode}.png"))
    if args.holes and os.path.isfile(args.holes):
        with open(args.holes, encoding="utf-8") as fh:
            filled = [h for h in json.load(fh) if h.get("center")]
        filled.sort(key=lambda h: -h["edges"])
        for o in (orig, tuned):
            o.data.shade_smooth()
        bvh_o = BVHTree.FromPolygons(co_o.tolist(), tv_o.tolist(), all_triangles=True)
        centre = 0.5 * (bmin + bmax)
        shots = []
        for k, h in enumerate(filled[: args.max_holes]):
            c = np.array(h["center"], float)
            # look from outside: pick the axis-ish direction with the most free space in front of the hole
            best = None
            for d in visibility.view_directions(26):
                hit = bvh_o.ray_cast(Vector((c + d * 0.002).tolist()), Vector(d.tolist()), diag)
                free = diag if hit[0] is None else hit[3]
                score = free + 0.05 * float(d @ (c - centre))
                if best is None or score > best[0]:
                    best = (score, d)
            d = best[1]
            scale = max(0.03, 0.6 * h.get("perimeter_pct_diag", 5) / 100.0 * diag)
            R.shading("matcap", backface=True)
            pb = R.render_view(os.path.join(args.out, f"_b_hole{k}.png"), [orig], tuple(d), c.tolist(), scale)
            pa = R.render_view(os.path.join(args.out, f"_a_hole{k}.png"), [tuned], tuple(d), c.tolist(), scale)
            hstack_save([pb, pa], os.path.join(args.out, f"cmp_hole{k}.png"))
            shots.append({"index": k, "edges": h["edges"], "center": h["center"], "dir": [round(float(x), 3) for x in d]})
        result["hole_closeups"] = shots
    result["renders_s"] = round(time.perf_counter() - t, 2)

result["total_s"] = round(time.perf_counter() - T0, 2)
with open(os.path.join(args.out, "compare.json"), "w", encoding="utf-8") as fh:
    json.dump(result, fh, indent=1)
print("@@COMPARE@@" + json.dumps(result))
