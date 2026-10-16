"""Blender helper (test only): synthetic checks of smoothing / symmetry / face / topology.

blender --background --factory-startup --python-exit-code 86 --python synthetic_refine.py -- \
    --scripts <backend/blender_scripts> --out <dir>
Prints one "@@SYNTH@@{json}" line with a result dict per check (the pytest module asserts on it) and writes
<out>/bust_dent.glb + <out>/coarse_sphere.glb for CLI tests.
"""

import argparse
import json
import math
import os
import sys
import time
import traceback

argv = sys.argv[sys.argv.index("--") + 1:]
ap = argparse.ArgumentParser()
ap.add_argument("--scripts", required=True)
ap.add_argument("--out", required=True)
a = ap.parse_args(argv)
sys.path.insert(0, a.scripts)
os.makedirs(a.out, exist_ok=True)

import bmesh  # noqa: E402
import bpy  # noqa: E402
import numpy as np  # noqa: E402

from img3d_amt import face, meshdata, smoothing, symmetry, topology  # noqa: E402
from img3d_amt.config import OutputSpec, TunerConfig, TuningOptions  # noqa: E402
from img3d_amt.context import TunerContext  # noqa: E402

RESULTS: dict = {}
RNG = np.random.default_rng(7)


def check(name):
    def deco(fn):
        t = time.perf_counter()
        try:
            RESULTS[name] = fn()
        except Exception as exc:  # report, the test asserts
            RESULTS[name] = {"error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
        RESULTS[name]["_s"] = round(time.perf_counter() - t, 3)
        return fn
    return deco


def bm_arrays(bm):
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bm.verts.index_update()
    co = np.array([v.co[:] for v in bm.verts], dtype=np.float64)
    tris = np.array([[v.index for v in f.verts] for f in bm.faces], dtype=np.int64)
    return co, tris


def volume(co, tris):
    p0, p1, p2 = co[tris[:, 0]], co[tris[:, 1]], co[tris[:, 2]]
    return float(np.einsum("ij,ij->i", p0, np.cross(p1, p2)).sum() / 6.0)


# ------------------------------------------------------------------------------------------------ smoothing
@check("noisy_sphere")
def _():
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=6, radius=0.4)
    co, tris = bm_arrays(bm)
    bm.free()
    topo = smoothing.build_topology(tris, len(co))
    edge = float(np.median(smoothing.edge_lengths(co, topo.edges)))
    nrm = co / np.linalg.norm(co, axis=1, keepdims=True)
    noisy = co + nrm * RNG.normal(0.0, 0.2 * edge, len(co))[:, None]
    out, info = smoothing.denoise(noisy, topo, normal_iterations=5, vertex_iterations=12, sigma_r=0.35)
    rms_before = float(np.sqrt(((np.linalg.norm(noisy, axis=1) - 0.4) ** 2).mean()))
    rms_after = float(np.sqrt(((np.linalg.norm(out, axis=1) - 0.4) ** 2).mean()))
    v0, v1, vt = volume(noisy, tris), volume(out, tris), 4.0 / 3.0 * math.pi * 0.4 ** 3
    taubin = smoothing.taubin(noisy, topo, iterations=10)
    lap = smoothing.laplacian_smooth(noisy, topo, iterations=20, lam=0.5)
    return {"rms_before": rms_before, "rms_after": rms_after, "volume_change": abs(v1 - v0) / v0,
            "volume_vs_true": abs(v1 - vt) / vt, "taubin_volume_change": abs(volume(taubin, tris) - v0) / v0,
            "laplacian_volume_change": abs(volume(lap, tris) - v0) / v0,
            "taubin_rms": float(np.sqrt(((np.linalg.norm(taubin, axis=1) - 0.4) ** 2).mean())),
            "vertices": len(co), "info": {k: v for k, v in info.items() if not isinstance(v, dict)}}


@check("noisy_cube")
def _():
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=0.6)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=19, use_grid_fill=True)
    co, tris = bm_arrays(bm)
    bm.free()
    topo = smoothing.build_topology(tris, len(co))
    edge = float(np.median(smoothing.edge_lengths(co, topo.edges)))
    fn0, _, _ = smoothing.face_geometry(co, tris)
    crease = smoothing.dihedral_angles(fn0, topo) > 60.0  # the 12 cube edges
    # noise along the vertex normal (keeps the cube shape on average)
    vn = smoothing.vertex_normals(co, tris)
    noisy = co + vn * RNG.normal(0.0, 0.12 * edge, len(co))[:, None]
    out, _ = smoothing.denoise(noisy, topo, normal_iterations=5, vertex_iterations=12, sigma_r=0.35)
    lap = smoothing.laplacian_smooth(noisy, topo, iterations=15, lam=0.5)

    def flat_err(x):  # distance of face-interior vertices to their cube plane (0.3)
        inner = np.sum(np.abs(np.abs(co) - 0.3) < 1e-6, axis=1) == 1
        return float(np.abs(np.abs(x[inner]).max(axis=1) - 0.3).mean())

    def crease_angle(x):
        fn, _, _ = smoothing.face_geometry(x, tris)
        return float(smoothing.dihedral_angles(fn, topo)[crease].mean())

    def corner_err(x):  # vertices on the cube edges: distance to the ideal edge line position
        on_edge = np.sum(np.abs(np.abs(co) - 0.3) < 1e-6, axis=1) >= 2
        return float(np.linalg.norm(x[on_edge] - co[on_edge], axis=1).mean())

    return {"flat_err_before": flat_err(noisy), "flat_err_after": flat_err(out),
            "crease_deg_clean": crease_angle(co), "crease_deg_noisy": crease_angle(noisy),
            "crease_deg_after": crease_angle(out), "crease_deg_laplacian": crease_angle(lap),
            "edge_err_before": corner_err(noisy), "edge_err_after": corner_err(out),
            "edge_err_laplacian": corner_err(lap), "edge_len": edge, "crease_edges": int(crease.sum())}


@check("fair_pit")
def _():
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=40, y_segments=40, size=0.5)
    co, tris = bm_arrays(bm)
    bm.free()
    topo = smoothing.build_topology(tris, len(co))
    r = np.linalg.norm(co[:, :2], axis=1)
    pitted = co.copy()
    pitted[:, 2] -= 0.02 * np.exp(-(r / 0.02) ** 2)  # narrow pit in a flat sheet
    free = r < 0.06
    faired = smoothing.fair(pitted, topo, free, order=2)
    faired1 = smoothing.fair(pitted, topo, free, order=1)
    return {"pit_depth_before": float(-pitted[:, 2].min()), "pit_depth_after": float(-faired[:, 2].min()),
            "membrane_depth_after": float(-faired1[:, 2].min()), "fixed_moved": float(
                np.abs(faired[~free] - pitted[~free]).max())}


# ------------------------------------------------------------------------------------------------ busts
def make_bust(dent: bool = False, asym: bool = False, name: str = "bust") -> bpy.types.Object:
    """Upright symmetric bust in the TRELLIS frame (+Z up, faces -Y): head ellipsoid, neck, shoulders/torso."""
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap")
    parts = [((0.0, 0.0, 0.30), (0.13, 0.12, 0.16), 5),      # head
             ((0.0, 0.0, 0.10), (0.045, 0.045, 0.09), 4),    # neck
             ((0.0, 0.0, -0.25), (0.36, 0.16, 0.25), 5)]     # shoulders / torso
    for centre, radii, sub in parts:
        geom = bmesh.ops.create_icosphere(bm, subdivisions=sub, radius=1.0, calc_uvs=True)
        vs = geom["verts"]
        for v in vs:
            v.co.x = v.co.x * radii[0] + centre[0]
            v.co.y = v.co.y * radii[1] + centre[1]
            v.co.z = v.co.z * radii[2] + centre[2]
    if asym:  # a big one-sided lump on the torso + shifted head: clearly not mirror-symmetric
        for v in bm.verts:
            d = (v.co - type(v.co)((0.25, -0.1, -0.15))).length
            if d < 0.15:
                v.co += v.normal * 0.06 * (1 - d / 0.15)
            if v.co.z > 0.12:
                v.co.x += 0.03
    if dent:  # smooth dent on the character's left cheek (+X, front)
        bm.verts.ensure_lookup_table()
        bm.normal_update()
        centre = type(bm.verts[0].co)((0.07, -0.085, 0.30))
        for v in bm.verts:
            d = (v.co - centre).length
            if d < 0.035 and v.co.z > 0.15:
                v.co -= v.normal * 0.008 * (0.5 + 0.5 * math.cos(math.pi * d / 0.035))
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def run_face_step(obj, options):
    cfg = TunerConfig(input_path=os.path.join(a.out, "dummy.glb"), output_dir=a.out,
                      options=TuningOptions.from_dict(options), outputs=OutputSpec())
    ctx = TunerContext(cfg)
    ctx.mesh_objects = [obj]
    ctx.set_primary(obj)
    ctx.update_bounds()
    return face.run(ctx), ctx


@check("bust_dent_auto")
def _():
    clean = make_bust(name="clean")
    dented = make_bust(dent=True, name="dented")
    co_clean = meshdata.get_positions(clean.data)
    co_dent = meshdata.get_positions(dented.data)
    outcome, ctx = run_face_step(dented, {"symmetry": "auto", "refine_face": False})
    co_after = meshdata.get_positions(dented.data)
    dent_mask = np.linalg.norm(co_dent - co_clean, axis=1) > 1e-6
    good_side = (co_clean[:, 0] < -0.02) & (co_clean[:, 2] > 0.15)
    sym = (outcome.details.get("symmetry") or {})
    return {"status": outcome.status, "message": outcome.message, "action": sym.get("action"),
            "humanoid": outcome.details.get("humanoid"), "head": outcome.details.get("head"),
            "dent_vertices": int(dent_mask.sum()),
            "dent_err_before": float(np.linalg.norm(co_dent[dent_mask] - co_clean[dent_mask], axis=1).max()),
            "dent_err_after": float(np.linalg.norm(co_after[dent_mask] - co_clean[dent_mask], axis=1).max()),
            "dent_err_after_mean": float(np.linalg.norm(co_after[dent_mask] - co_clean[dent_mask], axis=1).mean()),
            "good_side_moved_max": float(np.linalg.norm(co_after[good_side] - co_dent[good_side], axis=1).max()),
            "repair": {k: v for k, v in (sym.get("repair") or {}).items() if k != "repaired"}}


@check("bust_symmetry_off")
def _():
    dented = make_bust(dent=True, name="dented_off")
    co0 = meshdata.get_positions(dented.data)
    outcome, _ = run_face_step(dented, {"symmetry": "off", "refine_face": False})
    return {"status": outcome.status, "message": outcome.message,
            "moved": float(np.abs(meshdata.get_positions(dented.data) - co0).max())}


@check("asymmetric_auto_skipped")
def _():
    obj = make_bust(asym=True, name="asym")
    co0 = meshdata.get_positions(obj.data)
    outcome, ctx = run_face_step(obj, {"symmetry": "auto", "refine_face": True})
    sym = outcome.details.get("symmetry") or {}
    return {"status": outcome.status, "message": outcome.message, "action": sym.get("action"),
            "reason": sym.get("reason"), "humanoid": outcome.details.get("humanoid"),
            "mean_pct": (sym.get("score") or {}).get("mean_pct"),
            "moved": float(np.abs(meshdata.get_positions(obj.data) - co0).max()),
            "face_attr": "amt_face_region" in obj.data.attributes}


@check("symmetrize_mode")
def _():
    obj = make_bust(dent=True, name="symz")
    v0 = len(obj.data.vertices)
    outcome, _ = run_face_step(obj, {"symmetry": "symmetrize", "refine_face": False})
    co = meshdata.get_positions(obj.data)
    tris = meshdata.get_loop_triangles(obj.data)[0]
    score, _ = symmetry.measure(co, tris, float(np.linalg.norm(co.max(0) - co.min(0))))
    sinfo = (outcome.details.get("symmetry") or {}).get("symmetrize") or {}
    return {"status": outcome.status, "message": outcome.message, "source": sinfo.get("source_side"),
            "vertices_before": v0, "vertices_after": len(obj.data.vertices), "mean_pct_after": score.mean_pct}


# ------------------------------------------------------------------------------------------------ topology
@check("pn_midpoints")
def _():
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=2, radius=0.4)
    co, tris = bm_arrays(bm)
    bm.free()
    topo = smoothing.build_topology(tris, len(co))
    e = topo.edges
    n = co / np.linalg.norm(co, axis=1, keepdims=True)
    lin = 0.5 * (co[e[:, 0]] + co[e[:, 1]])
    pn = smoothing.pn_midpoints(co[e[:, 0]], co[e[:, 1]], n[e[:, 0]], n[e[:, 1]])
    return {"linear_err": float(np.abs(np.linalg.norm(lin, axis=1) - 0.4).mean()),
            "pn_err": float(np.abs(np.linalg.norm(pn, axis=1) - 0.4).mean())}


def export_glb(obj, path):
    for o in bpy.context.scene.objects:
        o.select_set(o == obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True)


@check("export_inputs")
def _():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    dented = make_bust(dent=True, name="bust_dent")
    export_glb(dented, os.path.join(a.out, "bust_dent.glb"))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bm = bmesh.new()
    bm.loops.layers.uv.new("UVMap")
    bmesh.ops.create_icosphere(bm, subdivisions=3, radius=0.4, calc_uvs=True)  # coarse, faceted, long edges
    for v in bm.verts:
        v.co.z *= 1.6
    me = bpy.data.meshes.new("coarse")
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new("coarse", me)
    bpy.context.scene.collection.objects.link(obj)
    export_glb(obj, os.path.join(a.out, "coarse_sphere.glb"))
    return {"bust": os.path.join(a.out, "bust_dent.glb"), "sphere": os.path.join(a.out, "coarse_sphere.glb")}


print("@@SYNTH@@" + json.dumps(RESULTS, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)))
