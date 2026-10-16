"""Characterize a TRELLIS.2 GLB inside Blender (probe for the Auto Mesh Tuner design).

Run:
  "C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe" --background --factory-startup ^
      --python-exit-code 1 --python analyze_glb.py -- --input <model.glb> --out <dir> [options]

Writes <out>/analysis.json (+ renders, round-trip GLB exports) and prints one line "@@RESULT@@<path>".
Probe only: lives under test/mesh_tuner/probe, never imported by the app.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import struct
import sys
import time
import traceback

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector, bvhtree, kdtree

# ----------------------------------------------------------------------------------------------------------------
# infrastructure: args, timing, memory
# ----------------------------------------------------------------------------------------------------------------


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="analyze_glb.py")
    p.add_argument("--input", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--job-json", default=None, help="defaults to <glb dir>/job.json or summary.json")
    p.add_argument("--front", default="auto", help="auto | -Y | +Y | -X | +X  (direction the character faces)")
    p.add_argument("--res", type=int, default=1024)
    p.add_argument("--samples", type=int, default=20000)
    p.add_argument("--head-center", default=None, help="x,y,z Blender world coords override")
    p.add_argument("--head-scale", type=float, default=None, help="ortho scale of the face close-up (BU)")
    p.add_argument("--skip-render", action="store_true")
    p.add_argument("--skip-export", action="store_true")
    p.add_argument("--export-welded", action="store_true")
    p.add_argument("--timing-merge-import", action="store_true",
                   help="also time a second import with merge_vertices=True")
    return p.parse_args(argv)


class _PMC(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def mem():
    try:
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PMC), ctypes.c_ulong]
        pmc = _PMC()
        pmc.cb = ctypes.sizeof(_PMC)
        k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        mb = 1024 * 1024
        return {"ws_mb": round(pmc.WorkingSetSize / mb), "peak_ws_mb": round(pmc.PeakWorkingSetSize / mb),
                "private_mb": round(pmc.PagefileUsage / mb), "peak_private_mb": round(pmc.PeakPagefileUsage / mb)}
    except Exception as ex:  # non-Windows
        return {"err": str(ex)}


TIMINGS: dict = {}
ERRORS: list = []


class Stage:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.t0 = time.perf_counter()
        print(f"[analyze] >> {self.name}", flush=True)
        return self

    def __exit__(self, et, ev, tb):
        dt = time.perf_counter() - self.t0
        TIMINGS[self.name] = {"s": round(dt, 3), **mem()}
        if et is not None:
            ERRORS.append({"stage": self.name, "error": f"{et.__name__}: {ev}",
                           "trace": "".join(traceback.format_exception(et, ev, tb))[-2000:]})
            print(f"[analyze] !! {self.name} failed: {et.__name__}: {ev}", flush=True)
            return True  # keep going with the next stage
        print(f"[analyze] << {self.name} {dt:.2f}s {TIMINGS[self.name]}", flush=True)
        return False


def r(x, n=6):
    return None if x is None else round(float(x), n)


def pct(a, q):
    return float(np.percentile(a, q)) if len(a) else None


# ----------------------------------------------------------------------------------------------------------------
# numpy mesh helpers
# ----------------------------------------------------------------------------------------------------------------


def mesh_arrays(me):
    V, E, F, L = len(me.vertices), len(me.edges), len(me.polygons), len(me.loops)
    co = np.empty(V * 3, np.float32); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3).astype(np.float64)
    ev = np.empty(E * 2, np.int32); me.edges.foreach_get("vertices", ev); ev = ev.reshape(-1, 2).astype(np.int64)
    lt = np.empty(F, np.int32); me.polygons.foreach_get("loop_total", lt)
    ls = np.empty(F, np.int32); me.polygons.foreach_get("loop_start", ls)
    lv = np.empty(L, np.int32); me.loops.foreach_get("vertex_index", lv)
    le = np.empty(L, np.int32); me.loops.foreach_get("edge_index", le)
    area = np.empty(F, np.float32); me.polygons.foreach_get("area", area)
    pn = np.empty(F * 3, np.float32); me.polygons.foreach_get("normal", pn); pn = pn.reshape(-1, 3).astype(np.float64)
    pc = np.empty(F * 3, np.float32); me.polygons.foreach_get("center", pc); pc = pc.reshape(-1, 3).astype(np.float64)
    return dict(V=V, E=E, F=F, L=L, co=co, ev=ev, lt=lt, ls=ls.astype(np.int64), lv=lv.astype(np.int64),
                le=le.astype(np.int64), area=area.astype(np.float64), pn=pn, pc=pc)


def connected_components(n, a, b):
    """Union-find by hooking + full pointer jumping, vectorized. Returns compact labels (0..k-1)."""
    parent = np.arange(n, dtype=np.int64)
    a = a.astype(np.int64)
    b = b.astype(np.int64)
    it = 0
    while len(a):
        it += 1
        pa, pb = parent[a], parent[b]
        neq = pa != pb
        if not neq.any():
            break
        a, b, pa, pb = a[neq], b[neq], pa[neq], pb[neq]
        lo, hi = np.minimum(pa, pb), np.maximum(pa, pb)
        np.minimum.at(parent, hi, lo)
        while True:
            gp = parent[parent]
            if np.array_equal(gp, parent):
                break
            parent = gp
    _, lab = np.unique(parent, return_inverse=True)
    return lab, it


def size_hist(sizes, edges):
    out = {}
    lo = edges[0]
    for hi in edges[1:] + [None]:
        if hi is None:
            out[f">={lo}"] = int((sizes >= lo).sum())
        else:
            out[f"{lo}-{hi - 1}"] = int(((sizes >= lo) & (sizes < hi)).sum())
            lo = hi
    return out


def topology_report(A, diag, label):
    """Edge/face topology on mesh arrays A. Returns (report, extra arrays)."""
    co, ev, lv, le, lt, ls = A["co"], A["ev"], A["lv"], A["le"], A["lt"], A["ls"]
    V, E, F = A["V"], A["E"], A["F"]
    rep = {}
    rep["verts"], rep["edges"], rep["faces"], rep["loops"] = V, E, F, A["L"]
    rep["tris"] = int((lt == 3).sum())
    rep["quads"] = int((lt == 4).sum())
    rep["ngons"] = int((lt > 4).sum())
    fc = np.bincount(le, minlength=E)
    rep["boundary_edges"] = int((fc == 1).sum())
    rep["non_manifold_edges_gt2_faces"] = int((fc > 2).sum())
    rep["max_faces_per_edge"] = int(fc.max()) if E else 0
    rep["wire_edges"] = int((fc == 0).sum())
    used_by_edge = np.zeros(V, bool); used_by_edge[ev.ravel()] = True
    used_by_face = np.zeros(V, bool); used_by_face[lv] = True
    rep["loose_verts_no_edges"] = int((~used_by_edge).sum())
    rep["verts_without_faces"] = int((~used_by_face).sum())
    elen = np.linalg.norm(co[ev[:, 0]] - co[ev[:, 1]], axis=1)
    rep["edge_length"] = {"mean": r(elen.mean()), "median": r(np.median(elen)), "min": r(elen.min()),
                          "p1": r(pct(elen, 1)), "p99": r(pct(elen, 99)), "max": r(elen.max()),
                          "mean_pct_diag": r(100 * elen.mean() / diag, 4)}
    rep["zero_length_edges"] = int((elen <= 0).sum())
    area = A["area"]
    rep["face_area"] = {"min": float(area.min()), "median": float(np.median(area)), "max": float(area.max()),
                        "total": r(area.sum())}
    rep["degenerate_faces_area_eq_0"] = int((area <= 0).sum())
    rep["degenerate_faces_area_lt_1e-10_diag2"] = int((area < 1e-10 * diag * diag).sum())
    rep["degenerate_faces_area_lt_1e-8_diag2"] = int((area < 1e-8 * diag * diag).sum())
    # face slivers: min altitude/edge ratio via triangle quality (tri only)
    tri = lt == 3
    if tri.any():
        s = ls[tri]
        p0, p1, p2 = co[lv[s]], co[lv[s + 1]], co[lv[s + 2]]
        a_ = np.linalg.norm(p1 - p0, axis=1); b_ = np.linalg.norm(p2 - p1, axis=1); c_ = np.linalg.norm(p0 - p2, axis=1)
        ar = area[tri]
        lmax = np.maximum(np.maximum(a_, b_), c_)
        q = np.where(lmax > 0, 4 * math.sqrt(3) * ar / np.maximum(a_ * a_ + b_ * b_ + c_ * c_, 1e-30), 0)
        rep["tri_quality_(1=equilateral)"] = {"p1": r(pct(q, 1), 4), "p5": r(pct(q, 5), 4), "median": r(np.median(q), 4),
                                              "lt_0.1": int((q < 0.1).sum()), "lt_0.01": int((q < 0.01).sum())}
    return rep, dict(fc=fc, elen=elen)


def face_edge_pairs(A, fc):
    """For manifold edges (2 faces): returns (edge_idx, loop1, loop2)."""
    le = A["le"]
    order = np.argsort(le, kind="stable")
    starts = np.concatenate([[0], np.cumsum(fc)[:-1]])
    m = np.nonzero(fc == 2)[0]
    l1 = order[starts[m]]
    l2 = order[starts[m] + 1]
    return m, l1, l2


# ----------------------------------------------------------------------------------------------------------------
# scene / import helpers
# ----------------------------------------------------------------------------------------------------------------


def glb_json_chunk(path):
    with open(path, "rb") as f:
        magic, ver, length = struct.unpack("<4sII", f.read(12))
        clen, _ = struct.unpack("<II", f.read(8))
        js = json.loads(f.read(clen))
    return {"length": length, "generator": js.get("asset", {}).get("generator"),
            "extensionsUsed": js.get("extensionsUsed"), "extensionsRequired": js.get("extensionsRequired"),
            "nodes": js.get("nodes"), "primitive_attributes": [p["attributes"] for m in js.get("meshes", []) for p in m["primitives"]],
            "accessor_counts": [(a.get("type"), a.get("count")) for a in js.get("accessors", [])],
            "images": [{"mimeType": im.get("mimeType"), "bufferView": im.get("bufferView"), "uri": im.get("uri")} for im in js.get("images", [])],
            "materials": js.get("materials")}


def load_job_meta(args):
    cand = [args.job_json] if args.job_json else []
    d = os.path.dirname(os.path.abspath(args.input))
    cand += [os.path.join(d, "job.json"), os.path.join(d, "summary.json")]
    for c in cand:
        if c and os.path.isfile(c):
            with open(c, "r", encoding="utf-8") as f:
                j = json.load(f)
            meta = {"source": c}
            if "settings" in j:
                s = j["settings"]
                meta.update(job_id=j.get("id"), primary_image_id=j.get("primary_image_id"),
                            slots=[x["slot"] for x in j.get("references", [])],
                            resolution=s.get("resolution"), polygon_target=s.get("polygon_target"),
                            texture_size=s.get("texture_size"), remove_background=s.get("remove_background"),
                            seed=j.get("seed"))
                md = ((j.get("result") or {}).get("metadata") or {})
                meta["pipeline_type"] = md.get("pipeline_type")
                meta["preprocess"] = md.get("preprocess")
                meta["sampler_steps"] = {k: (v.get("steps"), v.get("guidance_strength")) for k, v in (md.get("sampler_params") or {}).items()}
                meta["engine_mesh_stats"] = md.get("engine_mesh_stats")
                img = os.path.join(d, "inputs", f"{j.get('primary_image_id')}.png")
                meta["input_image"] = img if os.path.isfile(img) else None
            else:
                md = j.get("metadata") or {}
                meta.update(job_id=j.get("job_id"), pipeline_type=md.get("pipeline_type"), preprocess=md.get("preprocess"),
                            mesh_stats=j.get("mesh_stats"))
                ins = os.path.join(d, "inputs")
                if os.path.isdir(ins):
                    pngs = [os.path.join(ins, x) for x in os.listdir(ins) if x.lower().endswith(".png")]
                    meta["input_image"] = pngs[0] if pngs else None
            pv = os.path.join(d, "preview.png")
            meta["preview_image"] = pv if os.path.isfile(pv) else None
            return meta
    return {"source": None}


def image_report():
    out = []
    for im in bpy.data.images:
        if im.type != "IMAGE":
            continue
        out.append({"name": im.name, "size": list(im.size), "file_format": im.file_format, "packed": im.packed_file is not None,
                    "packed_bytes": im.packed_file.size if im.packed_file else None, "source": im.source,
                    "filepath": im.filepath, "colorspace": im.colorspace_settings.name, "channels": im.channels,
                    "depth": im.depth, "is_float": im.is_float, "has_data": im.has_data, "alpha_mode": im.alpha_mode,
                    "users": im.users})
    return out


def material_report():
    out = []
    for m in bpy.data.materials:
        d = {"name": m.name, "users": m.users, "blend_method": getattr(m, "blend_method", None),
             "surface_render_method": getattr(m, "surface_render_method", None),
             "use_backface_culling": m.use_backface_culling, "links": []}
        if m.node_tree:
            d["nodes"] = [n.bl_idname for n in m.node_tree.nodes]
            for lk in m.node_tree.links:
                fn = lk.from_node
                src = fn.image.name if getattr(fn, "image", None) else fn.bl_idname
                d["links"].append(f"{src}.{lk.from_socket.name} -> {lk.to_node.bl_idname}.{lk.to_socket.name}")
        out.append(d)
    return out


def object_report():
    out = []
    for o in bpy.data.objects:
        loc, rot, sca = o.matrix_world.decompose()
        out.append({"name": o.name, "type": o.type, "data": o.data.name if o.data else None,
                    "parent": o.parent.name if o.parent else None, "collections": [c.name for c in o.users_collection],
                    "location": [r(x) for x in loc], "rotation_quat": [r(x) for x in rot], "scale": [r(x) for x in sca],
                    "matrix_is_identity": o.matrix_world == Matrix.Identity(4),
                    "modifiers": [m.type for m in getattr(o, "modifiers", [])],
                    "vertex_groups": len(o.vertex_groups) if o.type == "MESH" else None})
    return out


# ----------------------------------------------------------------------------------------------------------------
# rendering helpers
# ----------------------------------------------------------------------------------------------------------------


def look_cam(scene, name, target, direction, ortho_scale, dist):
    cam = bpy.data.cameras.get(name) or bpy.data.cameras.new(name)
    cam.type = "ORTHO"
    cam.ortho_scale = ortho_scale
    cam.clip_start = dist * 0.001
    cam.clip_end = dist * 10
    obj = bpy.data.objects.get(name)
    if obj is None:
        obj = bpy.data.objects.new(name, cam)
        scene.collection.objects.link(obj)
    d = Vector(direction).normalized()
    obj.location = Vector(target) + d * dist
    obj.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
    scene.camera = obj
    return obj


def ortho_extent(co, direction, up=(0, 0, 1), pad=1.06):
    d = np.array(direction, float); d /= np.linalg.norm(d)
    u = np.array(up, float); u = u - d * (u @ d); u /= np.linalg.norm(u)
    rgt = np.cross(d, u)
    pu, pr = co @ u, co @ rgt
    ext = max(pu.max() - pu.min(), pr.max() - pr.min()) * pad
    # centre in the camera plane
    cu, cr = (pu.max() + pu.min()) / 2, (pr.max() + pr.min()) / 2
    centre = u * cu + rgt * cr + d * float((co @ d).mean())
    return float(ext), centre


def setup_world(scene, color=(0.18, 0.18, 0.18)):
    w = scene.world or bpy.data.worlds.new("World")
    scene.world = w
    w.color = color
    return w


def setup_workbench(scene, light="MATCAP", cavity=True, color_type="SINGLE", backface=False):
    scene.render.engine = "BLENDER_WORKBENCH"
    sh = scene.display.shading
    sh.light = light
    if light == "MATCAP":
        names = [l.name for l in bpy.context.preferences.studio_lights if l.type == "MATCAP"]
        for pref in ("basic_1.exr", "clay_studio.exr", "basic_grey.exr"):
            if pref in names:
                sh.studio_light = pref
                break
    elif light == "STUDIO":
        names = [l.name for l in bpy.context.preferences.studio_lights if l.type == "STUDIO"]
        for pref in ("Default", "studio.sl", "basic.sl"):
            if pref in names:
                sh.studio_light = pref
                break
    sh.color_type = color_type
    sh.single_color = (0.8, 0.8, 0.8)
    sh.show_cavity = cavity
    if cavity:
        sh.cavity_type = "BOTH"
        sh.cavity_ridge_factor = 1.0
        sh.cavity_valley_factor = 1.0
        sh.curvature_ridge_factor = 1.0
        sh.curvature_valley_factor = 1.0
    sh.show_backface_culling = backface
    sh.show_xray = False
    sh.show_shadows = False
    sh.show_specular_highlight = True
    scene.display.render_aa = "8"


def setup_eevee_textured(scene, samples=32):
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = samples
    w = setup_world(scene)
    hdri_dir = bpy.utils.system_resource("DATAFILES", path="studiolights/world")
    hdri = None
    for nm in ("forest.exr", "studio.exr", "city.exr"):
        pth = os.path.join(hdri_dir, nm)
        if os.path.isfile(pth):
            hdri = pth
            break
    try:
        w.use_nodes = True  # deprecated/no-op in 5.x, required in 4.x
    except Exception:
        pass
    nt = w.node_tree
    if nt is not None and hdri:
        for n in list(nt.nodes):
            nt.nodes.remove(n)
        env = nt.nodes.new("ShaderNodeTexEnvironment")
        env.image = bpy.data.images.load(hdri, check_existing=True)
        bg = nt.nodes.new("ShaderNodeBackground"); bg.inputs["Strength"].default_value = 1.0
        out = nt.nodes.new("ShaderNodeOutputWorld")
        nt.links.new(env.outputs["Color"], bg.inputs["Color"])
        nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    return hdri


def render_to(scene, path):
    scene.render.filepath = path
    t = time.perf_counter()
    bpy.ops.render.render(write_still=True)
    return round(time.perf_counter() - t, 2)


def load_rgba(path):
    im = bpy.data.images.load(path, check_existing=False)
    w, h = im.size
    px = np.empty(w * h * 4, np.float32)
    im.pixels.foreach_get(px)
    bpy.data.images.remove(im)
    return px.reshape(h, w, 4)[::-1]  # top-down


def norm_crop(rgba, mask, n=96):
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return np.zeros((n, n, 3)), np.zeros((n, n), bool)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    side = max(y1 - y0, x1 - x0)
    cy, cx = (y0 + y1) / 2, (x0 + x1) / 2
    yy = (cy - side / 2 + (np.arange(n) + 0.5) * side / n).astype(int)
    xx = (cx - side / 2 + (np.arange(n) + 0.5) * side / n).astype(int)
    H, W = mask.shape
    vy = (yy >= 0) & (yy < H); vx = (xx >= 0) & (xx < W)
    out = np.zeros((n, n, 3)); om = np.zeros((n, n), bool)
    sub = rgba[np.clip(yy, 0, H - 1)][:, np.clip(xx, 0, W - 1)]
    sm = mask[np.clip(yy, 0, H - 1)][:, np.clip(xx, 0, W - 1)] & vy[:, None] & vx[None, :]
    out[sm] = sub[..., :3][sm]
    om[:] = sm
    return out, om


def slice_segments(co, tv, axis, value):
    """Intersect triangles (tv: T x 3 vertex ids) with plane co[:,axis]==value -> (N,2,3) segments."""
    d = co[:, axis] - value
    dt = d[tv]
    cross = (dt.min(1) < 0) & (dt.max(1) > 0)
    T, D = tv[cross], dt[cross]
    P = co[T]  # N,3,3
    pts = []
    for a, b in ((0, 1), (1, 2), (2, 0)):
        da, db = D[:, a], D[:, b]
        m = (da * db) < 0
        t = np.where(m, da / np.where(m, da - db, 1), 0)
        pts.append((P[:, a] + (P[:, b] - P[:, a]) * t[:, None], m))
    out = np.zeros((len(T), 2, 3))
    cnt = np.zeros(len(T), int)
    for p, m in pts:
        for slot in (0, 1):
            sel = m & (cnt == slot)
            out[sel, slot] = p[sel]
            cnt[sel] += 1
            m = m & ~sel
    return out[cnt == 2]


def draw_slice(seg, ua, va, win, size, path, flip_u=False):
    umin, umax, vmin, vmax = win
    half = max(umax - umin, vmax - vmin) / 2  # square window -> no aspect distortion
    cu, cv = (umin + umax) / 2, (vmin + vmax) / 2
    umin, umax, vmin, vmax = cu - half, cu + half, cv - half, cv + half
    img = np.ones((size, size, 4), np.float32)
    img[..., :3] = 0.95
    if len(seg):
        t = np.linspace(0, 1, 10)
        p = seg[:, 0, None, :] + (seg[:, 1, None, :] - seg[:, 0, None, :]) * t[None, :, None]
        pu, pv = p[..., ua].ravel(), p[..., va].ravel()
        x = ((pu - umin) / (umax - umin) * size).astype(np.int64)
        if flip_u:
            x = size - 1 - x
        y = ((vmax - pv) / (vmax - vmin) * size).astype(np.int64)
        ok = (x >= 0) & (x < size) & (y >= 0) & (y < size)
        img[y[ok], x[ok], :3] = 0.0
    im = bpy.data.images.new(os.path.basename(path), size, size, alpha=True)
    im.pixels.foreach_set(img[::-1].ravel())
    im.filepath_raw = path
    im.file_format = "PNG"
    im.save()
    bpy.data.images.remove(im)


# ----------------------------------------------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------------------------------------------


def main():
    args = parse_args()
    os.makedirs(args.out, exist_ok=True)
    R: dict = {"input": os.path.abspath(args.input), "input_size_bytes": os.path.getsize(args.input),
               "blender": bpy.app.version_string, "argv": sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []}
    t_all = time.perf_counter()
    R["job"] = load_job_meta(args)
    R["glb_json"] = glb_json_chunk(args.input)
    R["mem_start"] = mem()

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    with Stage("import_gltf"):
        ret = bpy.ops.import_scene.gltf(filepath=args.input, merge_vertices=False, import_shading="NORMALS")
        R["import_return"] = list(ret)
    R["objects"] = object_report()
    R["collections"] = [{"name": c.name, "objects": [o.name for o in c.objects]} for c in bpy.data.collections]
    R["materials"] = material_report()
    R["images"] = image_report()

    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if not meshes:
        raise RuntimeError("no mesh objects imported")
    obj = max(meshes, key=lambda o: len(o.data.polygons))
    me = obj.data
    if obj.matrix_world != Matrix.Identity(4):
        me.transform(obj.matrix_world)
        obj.matrix_world = Matrix.Identity(4)
        R["note_transform_applied"] = True

    R["mesh_name"] = me.name
    R["uv_layers"] = [{"name": u.name, "active": u.active, "active_render": u.active_render} for u in me.uv_layers]
    R["color_attributes"] = [{"name": c.name, "domain": c.domain, "data_type": c.data_type} for c in me.color_attributes]
    R["attributes"] = [(a.name, a.domain, a.data_type) for a in me.attributes]
    R["has_custom_normals"] = me.has_custom_normals
    R["normals_domain"] = me.normals_domain

    # ---------------- raw mesh stats ----------------
    with Stage("raw_stats"):
        A = mesh_arrays(me)
        co = A["co"]
        bmin, bmax = co.min(0), co.max(0)
        dims = bmax - bmin
        diag = float(np.linalg.norm(dims))
        R["bbox"] = {"min": [r(x) for x in bmin], "max": [r(x) for x in bmax], "dimensions": [r(x) for x in dims],
                     "diag": r(diag), "center": [r(x) for x in (bmin + bmax) / 2],
                     "centroid_verts": [r(x) for x in co.mean(0)]}
        R["up_axis_guess"] = "XYZ"[int(np.argmax(dims))] + " (largest extent; glTF importer converts glTF +Y up -> Blender +Z up)"
        raw_rep, raw_x = topology_report(A, diag, "raw")
        R["raw"] = raw_rep
        uniq = np.unique(co.astype(np.float32), axis=0)
        R["raw"]["exact_duplicate_positions"] = int(len(co) - len(uniq))
        # GLB triangles vs Blender faces (importer drops index-degenerate / duplicate triangles)
        try:
            glb_tris = [c for t, c in R["glb_json"]["accessor_counts"] if t == "SCALAR"][0] // 3
            R["raw"]["glb_index_triangles"] = int(glb_tris)
            R["raw"]["faces_dropped_by_importer"] = int(glb_tris - A["F"])
        except Exception:
            pass
        # duplicate faces by POSITION (seam-split copies welded): same / opposite winding
        pos_id = np.unique(co.astype(np.float32), axis=0, return_inverse=True)[1].ravel()
        if (A["lt"] == 3).all():
            tri = pos_id[A["lv"]].reshape(-1, 3).astype(np.int64)
            s = np.sort(tri, axis=1)
            nb = int(pos_id.max()) + 1
            key = (s[:, 0] * nb + s[:, 1]) * nb + s[:, 2] if nb < 2 ** 21 else None
            if key is not None:
                _, inv_, cnt_ = np.unique(key, return_inverse=True, return_counts=True)
                dupf = cnt_[inv_] > 1
                k0 = np.argmin(tri, axis=1)
                rot = np.take_along_axis(tri, (k0[:, None] + np.arange(3)[None, :]) % 3, 1)
                orient = (rot[:, 1] < rot[:, 2]).astype(np.int64)
                o_sum = np.bincount(inv_, weights=orient, minlength=len(cnt_))
                grp = cnt_ > 1
                mixed = grp & (o_sum > 0) & (o_sum < cnt_)
                R["raw"]["duplicate_faces_by_position"] = {"faces_in_dup_groups": int(dupf.sum()), "groups": int(grp.sum()),
                                                           "groups_opposite_winding": int(mixed.sum()),
                                                           "groups_same_winding": int((grp & ~mixed).sum())}
            tri_idx_deg = (tri[:, 0] == tri[:, 1]) | (tri[:, 1] == tri[:, 2]) | (tri[:, 0] == tri[:, 2])
            R["raw"]["faces_with_coincident_corner_positions"] = int(tri_idx_deg.sum())
        del pos_id
        # UVs
        if me.uv_layers:
            uv = np.empty(A["L"] * 2, np.float32); me.uv_layers.active.data.foreach_get("uv", uv); uv = uv.reshape(-1, 2)
            R["uv_stats"] = {"min": [r(x) for x in uv.min(0)], "max": [r(x) for x in uv.max(0)],
                             "outside_0_1": int(((uv < 0) | (uv > 1)).any(1).sum())}
        # winding vs GLB-provided normals (custom split normals)
        try:
            cn = np.empty(A["L"] * 3, np.float32); me.corner_normals.foreach_get("vector", cn); cn = cn.reshape(-1, 3)
            fid = np.repeat(np.arange(A["F"]), A["lt"])
            fsum = np.zeros((A["F"], 3))
            for k in range(3):
                fsum[:, k] = np.bincount(fid, weights=cn[:, k], minlength=A["F"])
            dots = (fsum * A["pn"]).sum(1)
            R["raw"]["faces_winding_opposes_glb_normals"] = int((dots < 0).sum())
            R["raw"]["faces_winding_vs_glb_normals_dot_lt_0.5"] = int((dots / np.maximum(np.linalg.norm(fsum, axis=1), 1e-12) < 0.5).sum())
        except Exception as ex:
            ERRORS.append({"stage": "corner_normals", "error": str(ex)})
        del uniq

    # ---------------- export round-trip (raw, as imported) ----------------
    if not args.skip_export:
        with Stage("export_glb_roundtrip_raw"):
            for o in bpy.data.objects:
                o.select_set(o == obj)
            bpy.context.view_layer.objects.active = obj
            outp = os.path.join(args.out, "roundtrip_raw.glb")
            bpy.ops.export_scene.gltf(filepath=outp, export_format="GLB", use_selection=True,
                                      export_image_format="AUTO", export_materials="EXPORT", export_yup=True,
                                      export_apply=False, export_normals=True, export_texcoords=True)
            R["export_raw"] = {"path": outp, "size_bytes": os.path.getsize(outp),
                               "size_ratio_vs_input": r(os.path.getsize(outp) / R["input_size_bytes"], 3),
                               "glb_json": glb_json_chunk(outp)}

    # ---------------- bmesh: duplicates + weld ----------------
    welded = None
    with Stage("bmesh_from_mesh"):
        bm = bmesh.new()
        bm.from_mesh(me)
    eps1, eps2 = 1e-6 * diag, 1e-5 * diag
    R["dup_eps"] = {"eps_1e-6_diag": eps1, "eps_1e-5_diag": eps2}
    with Stage("find_doubles_raw"):
        R["raw"]["duplicate_verts_eps_1e-6_diag"] = len(bmesh.ops.find_doubles(bm, verts=bm.verts, dist=eps1)["targetmap"])
        R["raw"]["duplicate_verts_eps_1e-5_diag"] = len(bmesh.ops.find_doubles(bm, verts=bm.verts, dist=eps2)["targetmap"])
    with Stage("weld_remove_doubles_1e-6"):
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=eps1)
    with Stage("welded_bmesh_checks"):
        R["welded_near_duplicates_eps_1e-5_diag"] = len(bmesh.ops.find_doubles(bm, verts=bm.verts, dist=eps2)["targetmap"])
        bm.verts.ensure_lookup_table()
        R["welded_non_manifold_verts"] = sum(1 for v in bm.verts if not v.is_manifold)
        R["welded_boundary_verts"] = sum(1 for v in bm.verts if v.is_boundary)
    with Stage("bmesh_to_mesh"):
        me_w = bpy.data.meshes.new("welded")
        bm.to_mesh(me_w)
        bm.free()
        welded = bpy.data.objects.new("welded", me_w)
        scene.collection.objects.link(welded)
        welded.hide_render = True
        if obj.data.materials:
            pass  # materials carried by bmesh material_index; mesh has no slots -> keep for export only

    # ---------------- welded topology ----------------
    with Stage("welded_topology"):
        W = mesh_arrays(me_w)
        rep, x = topology_report(W, diag, "welded")
        R["welded"] = rep
        fc, elen = x["fc"], x["elen"]
        coW, ev = W["co"], W["ev"]
        mean_e = float(elen.mean())
        # manifold edge pairs -> winding consistency + dihedral
        m, l1, l2 = face_edge_pairs(W, fc)
        lf = np.repeat(np.arange(W["F"]), W["lt"])
        f1, f2 = lf[l1], lf[l2]
        inconsistent = W["lv"][l1] == W["lv"][l2]
        R["welded"]["winding_inconsistent_manifold_edges"] = int(inconsistent.sum())
        bad_faces = np.unique(np.concatenate([f1[inconsistent], f2[inconsistent]]))
        R["welded"]["faces_touching_inconsistent_edges"] = int(len(bad_faces))
        dots = np.clip((W["pn"][f1] * W["pn"][f2]).sum(1), -1, 1)
        ang = np.degrees(np.arccos(dots))
        ang_c = np.where(inconsistent, 180 - ang, ang)  # dihedral corrected for flipped neighbor
        R["welded"]["dihedral_deg"] = {"mean": r(ang_c.mean(), 3), "p50": r(pct(ang_c, 50), 3), "p95": r(pct(ang_c, 95), 3),
                                       "p99": r(pct(ang_c, 99), 3), "gt30": int((ang_c > 30).sum()),
                                       "gt60": int((ang_c > 60).sum()), "gt90": int((ang_c > 90).sum()),
                                       "gt150_foldover": int((ang_c > 150).sum())}
        # axis-aligned normals (voxel stair-stepping signature), area-weighted
        pn, area = W["pn"], W["area"]
        amax = np.abs(pn).max(1)
        R["welded"]["area_frac_normals_within_5deg_of_axis"] = r((area * (amax > math.cos(math.radians(5)))).sum() / area.sum(), 4)
        R["welded"]["area_frac_normals_within_2deg_of_axis"] = r((area * (amax > math.cos(math.radians(2)))).sum() / area.sum(), 4)
        R["welded"]["expected_frac_5deg_for_isotropic_surface"] = r(1 - math.cos(math.radians(5)), 4)
        # laplacian spikes
        a_, b_ = ev[:, 0], ev[:, 1]
        deg = np.bincount(a_, minlength=W["V"]) + np.bincount(b_, minlength=W["V"])
        nsum = np.zeros((W["V"], 3))
        for k in range(3):
            nsum[:, k] = np.bincount(a_, weights=coW[b_, k], minlength=W["V"]) + np.bincount(b_, weights=coW[a_, k], minlength=W["V"])
        ok = deg > 0
        lap = np.zeros(W["V"]); lap[ok] = np.linalg.norm(coW[ok] - nsum[ok] / deg[ok, None], axis=1) / mean_e
        R["welded"]["laplacian_over_mean_edge"] = {"p50": r(pct(lap[ok], 50), 4), "p99": r(pct(lap[ok], 99), 4),
                                                    "p999": r(pct(lap[ok], 99.9), 4), "max": r(lap.max(), 4),
                                                    "spikes_gt1": int((lap > 1).sum()), "spikes_gt2": int((lap > 2).sum()),
                                                    "spikes_gt4": int((lap > 4).sum())}
        R["welded"]["vertex_valence"] = {"min": int(deg[ok].min()), "max": int(deg.max()),
                                         "hist": {str(k): int((deg == k).sum()) for k in range(3, 10)},
                                         "gt9": int((deg > 9).sum())}

    with Stage("components"):
        lab, iters = connected_components(W["V"], ev[:, 0], ev[:, 1])
        flab = lab[W["lv"][W["ls"]]]
        ncomp_all = int(lab.max() + 1)
        fcount = np.bincount(flab, minlength=ncomp_all)
        comps = np.nonzero(fcount)[0]
        sizes = fcount[comps]
        order = np.argsort(-sizes)
        comps, sizes = comps[order], sizes[order]
        F = W["F"]
        tiny = sizes < 0.001 * F
        carea = np.bincount(flab, weights=W["area"], minlength=ncomp_all)[comps]
        R["components"] = {"count": int(len(comps)), "uf_iterations": iters,
                           "largest10_faces": sizes[:10].tolist(),
                           "largest10_frac": [r(s / F, 5) for s in sizes[:10]],
                           "tiny_lt_0.1pct_faces": int(tiny.sum()),
                           "faces_in_tiny": int(sizes[tiny].sum()),
                           "area_frac_in_tiny": r(carea[tiny].sum() / max(carea.sum(), 1e-30), 6),
                           "size_hist_faces": size_hist(sizes, [1, 2, 5, 10, 50, 200, 1000, 10000, 100000])}
        # signed volume per comp (outward orientation check) via loop triangles
        me_w.calc_loop_triangles()
        T = len(me_w.loop_triangles)
        tv = np.empty(T * 3, np.int32); me_w.loop_triangles.foreach_get("vertices", tv); tv = tv.reshape(-1, 3)
        v0, v1, v2 = coW[tv[:, 0]], coW[tv[:, 1]], coW[tv[:, 2]]
        sv = (v0 * np.cross(v1, v2)).sum(1) / 6
        tl = lab[tv[:, 0]]
        vol = np.bincount(tl, weights=sv, minlength=ncomp_all)
        R["components"]["largest10_signed_volume"] = [r(vol[c], 6) for c in comps[:10]]
        R["components"]["negative_volume_components"] = int((vol[comps] < 0).sum())
        # bbox of top comps (normalized)
        info = []
        for c in comps[:10]:
            vv = coW[lab == c]
            info.append({"faces": int(fcount[c]), "bbox_min": [r(x, 4) for x in vv.min(0)], "bbox_max": [r(x, 4) for x in vv.max(0)]})
        R["components"]["largest10_bbox"] = info
        # tiny comps: distance of centroid from main comp bbox
        main = comps[0]
        mv = coW[lab == main]
        mmin, mmax = mv.min(0), mv.max(0)
        vcount = np.bincount(lab, minlength=ncomp_all).astype(float)
        cen = np.zeros((ncomp_all, 3))
        for k in range(3):
            cen[:, k] = np.bincount(lab, weights=coW[:, k], minlength=ncomp_all) / np.maximum(vcount, 1)
        small = comps[1:]
        if len(small):
            outside = np.maximum(np.maximum(mmin - cen[small], cen[small] - mmax), 0)
            dist = np.linalg.norm(outside, axis=1) / diag
            R["components"]["non_main_centroid_outside_main_bbox"] = int((dist > 0).sum())
            R["components"]["non_main_centroid_dist_gt_1pct_diag"] = int((dist > 0.01).sum())
        R["_tmp_comp"] = None
        COMP = dict(lab=lab, comps=comps, cen=cen)

    with Stage("boundary_loops"):
        be_mask = fc == 1
        be = ev[be_mask]
        bl = elen[be_mask]
        if len(be):
            uv_, inv = np.unique(be.ravel(), return_inverse=True)
            inv = inv.reshape(-1, 2)
            blab, _ = connected_components(len(uv_), inv[:, 0], inv[:, 1])
            el = blab[inv[:, 0]]
            nloop = int(blab.max() + 1)
            ecount = np.bincount(el, minlength=nloop)
            perim = np.bincount(el, weights=bl, minlength=nloop)
            o = np.argsort(-ecount)
            # which face component each loop belongs to
            loop_comp = np.zeros(nloop, np.int64); loop_comp[el] = COMP["lab"][uv_[inv[:, 0]]]
            main = COMP["comps"][0]
            # loop centroid normalized in bbox
            lc = np.zeros((nloop, 3))
            for k in range(3):
                lc[:, k] = np.bincount(el, weights=(coW[be[:, 0], k] + coW[be[:, 1], k]) / 2, minlength=nloop) / np.maximum(ecount, 1)
            nb = (lc - bmin) / np.maximum(dims, 1e-12)
            R["holes"] = {"boundary_loops": nloop,
                          "loops_on_main_component": int((loop_comp == main).sum()),
                          "edges_per_loop_hist": size_hist(ecount, [1, 3, 5, 11, 51, 201, 1001]),
                          "largest20": [{"edges": int(ecount[i]), "perimeter": r(perim[i], 5),
                                         "perimeter_pct_diag": r(100 * perim[i] / diag, 3),
                                         "on_main": bool(loop_comp[i] == main),
                                         "centroid_bbox_norm": [r(x, 3) for x in nb[i]]} for i in o[:20]],
                          "total_boundary_edges": int(len(be)),
                          "loops_perimeter_gt_3pct_diag": int((perim > 0.03 * diag).sum()),
                          "loops_perimeter_lt_3pct_diag": int((perim <= 0.03 * diag).sum())}
        else:
            R["holes"] = {"boundary_loops": 0}

    # ---------------- orientation via textured flat renders vs input preview ----------------
    front = args.front
    lat_axis = 0
    if not args.skip_render:
        with Stage("orientation_match"):
            setup_world(scene, (0, 0, 0))
            scene.view_settings.view_transform = "Standard"
            scene.render.resolution_x = scene.render.resolution_y = 256
            scene.render.film_transparent = True
            scene.render.image_settings.file_format = "PNG"
            scene.render.image_settings.color_mode = "RGBA"
            setup_workbench(scene, light="FLAT", cavity=False, color_type="TEXTURE")
            ref = R["job"].get("preview_image") or R["job"].get("input_image")
            res = {}
            if ref:
                ri = load_rgba(ref)
                rmask = (ri[..., :3].max(-1) > 0.03) & (ri[..., 3] > 0.5)
                rimg, rm = norm_crop(ri, rmask)
                rimg_m, rm_m = rimg[:, ::-1], rm[:, ::-1]
                for nm, dvec in {"-Y": (0, -1, 0), "+Y": (0, 1, 0), "-X": (-1, 0, 0), "+X": (1, 0, 0)}.items():
                    ext, c = ortho_extent(co, dvec)
                    look_cam(scene, "cam_orient", c, dvec, ext, diag * 2)
                    pth = os.path.join(args.out, f"_orient_{nm.replace('+', 'p').replace('-', 'n')}.png")
                    render_to(scene, pth)
                    rr = load_rgba(pth)
                    img, m_ = norm_crop(rr, rr[..., 3] > 0.5)
                    u = m_ | rm
                    um = m_ | rm_m
                    res[nm] = {"rgb_mad": r(np.abs(img - rimg)[u].mean(), 4), "mask_iou": r((m_ & rm).sum() / max(u.sum(), 1), 4),
                               "rgb_mad_mirrored_ref": r(np.abs(img - rimg_m)[um].mean(), 4),
                               "mask_iou_mirrored_ref": r((m_ & rm_m).sum() / max(um.sum(), 1), 4)}
                best = min(res, key=lambda k: res[k]["rgb_mad"])
                R["orientation_match"] = {"reference": ref, "camera_at": res, "best_camera_side": best,
                                          "note": "camera placed on that side of the model looking at it; best match => model faces that direction"}
                if front == "auto":
                    front = best
            scene.render.film_transparent = False
    if front == "auto":
        front = "-Y"
    R["front_used"] = front
    fvec = {"-Y": (0, -1, 0), "+Y": (0, 1, 0), "-X": (-1, 0, 0), "+X": (1, 0, 0)}[front]
    fv = np.array(fvec, float)
    up = np.array((0, 0, 1.0))
    char_right = np.cross(fv, up)  # facing f with up z => character's right = f x up
    char_left = -char_right
    lat_axis = 0 if abs(fv[1]) > 0.5 else 1

    # ---------------- symmetry ----------------
    with Stage("symmetry"):
        rng = np.random.default_rng(0)
        kd = kdtree.KDTree(len(coW))
        for i, p in enumerate(coW):
            kd.insert(p, i)
        kd.balance()
        sidx = rng.choice(len(coW), size=min(args.samples, len(coW)), replace=False)
        S = coW[sidx]
        lat = np.zeros(3); lat[lat_axis] = 1.0

        def mirror_err(P, normal, offset):
            Pm = P - 2 * ((P @ normal) - offset)[:, None] * normal[None, :]
            return np.array([kd.find(p)[2] for p in Pm])

        e0 = mirror_err(S, lat, 0.0)
        best = (e0[:2000].mean(), 0.0, 0.0)
        lat_c = float(np.median(coW[:, lat_axis]))
        for off in np.linspace(lat_c - 0.03 * diag, lat_c + 0.03 * diag, 13):
            for yaw in np.radians(np.arange(-8, 9, 2)):
                n = np.array([math.cos(yaw), math.sin(yaw), 0.0]) if lat_axis == 0 else np.array([-math.sin(yaw), math.cos(yaw), 0.0])
                e = mirror_err(S[:2000], n, off).mean()
                if e < best[0]:
                    best = (e, off, yaw)
        _, boff, byaw = best
        for off in np.linspace(boff - 0.0025 * diag, boff + 0.0025 * diag, 5):
            for yaw in np.radians(np.degrees(byaw) + np.array([-1, -0.5, 0, 0.5, 1])):
                n = np.array([math.cos(yaw), math.sin(yaw), 0.0]) if lat_axis == 0 else np.array([-math.sin(yaw), math.cos(yaw), 0.0])
                e = mirror_err(S[:2000], n, off).mean()
                if e < best[0]:
                    best = (e, off, yaw)
        _, boff, byaw = best
        nb_ = np.array([math.cos(byaw), math.sin(byaw), 0.0]) if lat_axis == 0 else np.array([-math.sin(byaw), math.cos(byaw), 0.0])
        e1 = mirror_err(S, nb_, boff)
        zmax, zmin = coW[:, 2].max(), coW[:, 2].min()
        H = zmax - zmin
        head = S[:, 2] > zmax - 0.2 * H
        R["symmetry"] = {"lateral_axis": "XY"[lat_axis], "samples": int(len(S)),
                         "plane_world_origin": {"mean_pct_diag": r(100 * e0.mean() / diag, 4), "p95_pct_diag": r(100 * pct(e0, 95) / diag, 4)},
                         "plane_best_fit": {"offset": r(boff, 5), "offset_pct_diag": r(100 * boff / diag, 4), "yaw_deg": r(math.degrees(byaw), 3),
                                            "mean_pct_diag": r(100 * e1.mean() / diag, 4), "p95_pct_diag": r(100 * pct(e1, 95) / diag, 4),
                                            "p99_pct_diag": r(100 * pct(e1, 99) / diag, 4), "max_pct_diag": r(100 * e1.max() / diag, 4),
                                            "frac_gt_0.5pct_diag": r((e1 > 0.005 * diag).mean(), 4),
                                            "frac_gt_1pct_diag": r((e1 > 0.01 * diag).mean(), 4)},
                         "head_region_top20pct": {"n": int(head.sum()),
                                                  "best_fit_mean_pct_diag": r(100 * e1[head].mean() / diag, 4) if head.any() else None,
                                                  "best_fit_p95_pct_diag": r(100 * pct(e1[head], 95) / diag, 4) if head.any() else None},
                         "mean_edge_pct_diag": r(100 * mean_e / diag, 4)}

    # ---------------- ray parity (interior / inverted faces) + thickness ----------------
    with Stage("raycast_interior"):
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        bvh = bvhtree.BVHTree.FromObject(welded, dg)
        rng = np.random.default_rng(1)
        Fn = W["F"]
        nsamp = min(args.samples, Fn)
        fidx = rng.choice(Fn, size=nsamp, replace=False)
        epsr = 1e-5 * diag
        odd = 0; inward_thin = 0; same_dir_close = 0; thick = []
        far = diag * 4
        for fi in fidx:
            n = Vector(W["pn"][fi]); c = Vector(W["pc"][fi])
            if n.length < 0.5:
                continue
            o = c + n * epsr
            cnt = 0
            first = None
            for _ in range(24):
                loc, nrm, idx, dist = bvh.ray_cast(o, n, far)
                if loc is None:
                    break
                if first is None:
                    first = (dist, nrm, idx)
                cnt += 1
                o = loc + n * epsr
            if cnt % 2 == 1:
                odd += 1
            if first is not None and first[0] < 0.002 * diag and first[1].dot(n) > 0.5:
                same_dir_close += 1
            loc, nrm, idx, dist = bvh.ray_cast(c - n * epsr, -n, far)
            if loc is not None:
                thick.append(dist)
                if dist < 0.002 * diag:
                    inward_thin += 1
        thick = np.array(thick)
        res_ = (R["job"].get("resolution") or {"512": 512, "1024_cascade": 1024, "1536_cascade": 1536}.get(str(R["job"].get("pipeline_type")), 1024))
        cell = 1.0 / float(res_)
        R["raycast"] = {"samples": int(nsamp),
                        "odd_parity_frac_(interior_or_inverted)": r(odd / nsamp, 5), "odd_parity_count": odd,
                        "outward_hit_same_facing_within_0.2pct_diag_(stacked_shell)": same_dir_close,
                        "inward_thickness_lt_0.2pct_diag": inward_thin,
                        "inward_thickness_pct_diag": {f"p{q}": (r(100 * pct(thick, q) / diag, 4) if len(thick) else None) for q in (5, 10, 25, 50, 75, 90, 95)},
                        "voxel_cell_BU": r(cell, 7),
                        "inward_thickness_in_voxels": {f"p{q}": (r(pct(thick, q) / cell, 3) if len(thick) else None) for q in (5, 25, 50, 75, 95)},
                        "frac_thickness_lt_3_voxels": r((thick < 3 * cell).mean(), 4) if len(thick) else None,
                        "inward_no_hit_(open_surface_behind)": int(nsamp - len(thick))}
        # tiny components inside the main volume? (parity along +Z from centroid)
        comps, cen = COMP["comps"], COMP["cen"]
        small = comps[1:2001]
        inside = 0
        for c in small:
            o = Vector(cen[c]); cnt = 0
            for _ in range(24):
                loc, nrm, idx, dist = bvh.ray_cast(o, Vector((0, 0, 1)), far)
                if loc is None:
                    break
                cnt += 1
                o = loc + Vector((0, 0, 1)) * epsr
            # the ray also crosses the component's own surface once if the centroid is inside it -> ambiguous;
            # count >= 3 crossings with odd parity as "enclosed by something else"
            if cnt >= 3 and cnt % 2 == 1:
                inside += 1
        R["raycast"]["non_main_components_tested"] = int(len(small))
        R["raycast"]["non_main_components_enclosed_guess"] = inside

    # ---------------- renders ----------------
    if not args.skip_render:
        renders = {}
        scene.render.resolution_x = scene.render.resolution_y = args.res
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGB"
        scene.render.film_transparent = False
        scene.view_settings.view_transform = "Standard"
        welded.hide_render = True
        obj.hide_render = False
        with Stage("render_workbench_views"):
            setup_world(scene, (0.18, 0.18, 0.18))
            setup_workbench(scene, light="MATCAP", cavity=True, color_type="SINGLE")
            R["workbench_matcap"] = scene.display.shading.studio_light
            three_q = fv + char_left
            three_q = three_q / np.linalg.norm(three_q) + np.array((0, 0, 0.25))
            views = {"front": fv, "left": char_left, "three_quarter_front_left": three_q, "back": -fv}
            for nm, dvec in views.items():
                ext, c = ortho_extent(co, dvec)
                look_cam(scene, "cam", c, dvec, ext, diag * 2)
                pth = os.path.join(args.out, f"wb_{nm}.png")
                renders[pth] = {"engine": "WORKBENCH matcap+cavity", "camera_dir": [r(x, 3) for x in dvec], "s": render_to(scene, pth)}
            # backface-culled front: inverted / interior faces show as holes
            setup_workbench(scene, light="MATCAP", cavity=True, color_type="SINGLE", backface=True)
            ext, c = ortho_extent(co, fv)
            look_cam(scene, "cam", c, fv, ext, diag * 2)
            pth = os.path.join(args.out, "wb_front_backface_culled.png")
            renders[pth] = {"engine": "WORKBENCH matcap backface-culling", "camera_dir": [r(x, 3) for x in fv], "s": render_to(scene, pth)}
        # head / face close-up
        with Stage("render_face_closeups"):
            zmax, zmin = co[:, 2].max(), co[:, 2].min()
            H = zmax - zmin
            if args.head_center:
                hc = np.array([float(x) for x in args.head_center.split(",")])
            else:
                slab = (co[:, 2] > zmax - 0.2 * H) & (co[:, 2] < zmax - 0.02 * H)
                lat_c = float(np.median(co[slab, lat_axis]))
                hz = zmax - 0.10 * H
                hc = np.zeros(3); hc[lat_axis] = lat_c; hc[2] = hz
                depth_axis = 1 - lat_axis
                near = slab & (np.abs(co[:, lat_axis] - lat_c) < 0.05 * H)
                hc[depth_axis] = float(np.median(co[near, depth_axis])) if near.any() else float(co[:, depth_axis].mean())
            hs = args.head_scale or 0.2 * H
            R["face_closeup"] = {"head_center": [r(x, 4) for x in hc], "ortho_scale": r(hs, 4),
                                 "heuristic": "z = zmax-0.10H, lateral = median of top-20% slab, depth = median near lateral centre"}
            look_cam(scene, "cam", Vector(hc), fv, hs, diag * 2)
            setup_workbench(scene, light="MATCAP", cavity=True, color_type="SINGLE")
            pth = os.path.join(args.out, "face_closeup_matcap.png")
            renders[pth] = {"engine": "WORKBENCH matcap+cavity", "s": render_to(scene, pth)}
            setup_workbench(scene, light="STUDIO", cavity=True, color_type="SINGLE")
            pth = os.path.join(args.out, "face_closeup_studio.png")
            renders[pth] = {"engine": "WORKBENCH studio+cavity", "s": render_to(scene, pth)}
            # 3/4 face close-up (dents in eyes read better at an angle)
            d34 = fv + 0.6 * char_left; d34 = d34 / np.linalg.norm(d34)
            look_cam(scene, "cam", Vector(hc), d34, hs, diag * 2)
            setup_workbench(scene, light="MATCAP", cavity=True, color_type="SINGLE")
            pth = os.path.join(args.out, "face_closeup_matcap_34.png")
            renders[pth] = {"engine": "WORKBENCH matcap+cavity", "s": render_to(scene, pth)}
        with Stage("render_eevee_textured"):
            hdri = setup_eevee_textured(scene, samples=32)
            R["eevee_hdri"] = hdri
            look_cam(scene, "cam", Vector(hc), fv, hs, diag * 2)
            pth = os.path.join(args.out, "face_closeup_textured.png")
            renders[pth] = {"engine": "EEVEE material-preview-like (HDRI)", "s": render_to(scene, pth)}
            ext, c = ortho_extent(co, fv)
            look_cam(scene, "cam", c, fv, ext, diag * 2)
            pth = os.path.join(args.out, "front_textured.png")
            renders[pth] = {"engine": "EEVEE material-preview-like (HDRI)", "s": render_to(scene, pth)}
        with Stage("slice_plots"):
            slices = {}
            depth_axis = 1 - lat_axis
            zmaxW, zminW = coW[:, 2].max(), coW[:, 2].min()
            HW = zmaxW - zminW
            pad = 0.02 * diag
            jobs = [
                ("slice_transverse_head", 2, float(hc[2]), (lat_axis, depth_axis),
                 (hc[lat_axis] - 0.6 * hs, hc[lat_axis] + 0.6 * hs, hc[depth_axis] - 0.6 * hs, hc[depth_axis] + 0.6 * hs)),
                ("slice_transverse_mid", 2, float(zminW + 0.6 * HW), (lat_axis, depth_axis),
                 (bmin[lat_axis] - pad, bmax[lat_axis] + pad, bmin[depth_axis] - pad, bmax[depth_axis] + pad)),
                ("slice_sagittal_center", lat_axis, float(np.median(coW[:, lat_axis])), (depth_axis, 2),
                 (bmin[depth_axis] - pad, bmax[depth_axis] + pad, bmin[2] - pad, bmax[2] + pad)),
            ]
            for nm, ax, val, (ua, va), win in jobs:
                seg = slice_segments(coW, tv, ax, val)
                pth = os.path.join(args.out, f"{nm}.png")
                draw_slice(seg, ua, va, win, 1024, pth, flip_u=(fv[depth_axis] > 0 and ua == depth_axis))
                info = {"axis": "XYZ"[ax], "value": r(val, 4), "segments": int(len(seg)), "window": [r(w, 4) for w in win], "path": pth}
                if len(seg) and nm == "slice_transverse_head":
                    # zoom on the front-most point of the head slice (face surface)
                    mid = seg.mean(1)
                    inwin = (mid[:, ua] > win[0]) & (mid[:, ua] < win[1]) & (mid[:, va] > win[2]) & (mid[:, va] < win[3]) \
                        & (np.abs(mid[:, lat_axis] - hc[lat_axis]) < 0.1 * hs)
                    score = np.where(inwin, mid @ fv, -1e9)
                    k = int(np.argmax(score))
                    cu, cv = mid[k, ua], mid[k, va]
                    w = 0.012
                    zp = os.path.join(args.out, f"{nm}_zoom_front.png")
                    draw_slice(seg, ua, va, (cu - w, cu + w, cv - w, cv + w), 1024, zp)
                    info["zoom_front"] = {"path": zp, "center": [r(cu, 5), r(cv, 5)], "half_width_BU": w}
                slices[nm] = info
            R["slices"] = slices
        R["renders"] = renders

    # ---------------- optional welded export ----------------
    if args.export_welded and not args.skip_export:
        with Stage("export_glb_welded"):
            for mat in me.materials:
                me_w.materials.append(mat)
            for o in bpy.data.objects:
                o.select_set(o == welded)
            bpy.context.view_layer.objects.active = welded
            outp = os.path.join(args.out, "roundtrip_welded.glb")
            bpy.ops.export_scene.gltf(filepath=outp, export_format="GLB", use_selection=True,
                                      export_image_format="AUTO", export_materials="EXPORT", export_yup=True)
            R["export_welded"] = {"path": outp, "size_bytes": os.path.getsize(outp), "glb_json": glb_json_chunk(outp)}

    if args.timing_merge_import:
        with Stage("import_gltf_merge_vertices"):
            bpy.ops.wm.read_factory_settings(use_empty=True)
            bpy.ops.import_scene.gltf(filepath=args.input, merge_vertices=True, import_shading="NORMALS")
            mm = max((o for o in bpy.data.objects if o.type == "MESH"), key=lambda o: len(o.data.polygons)).data
            R["import_merge_vertices"] = {"verts": len(mm.vertices), "edges": len(mm.edges), "faces": len(mm.polygons)}

    R.pop("_tmp_comp", None)
    R["timings"] = TIMINGS
    R["total_seconds"] = round(time.perf_counter() - t_all, 2)
    R["mem_end"] = mem()
    R["errors"] = ERRORS
    outp = os.path.join(args.out, "analysis.json")
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(R, f, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    print("@@RESULT@@" + outp, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
