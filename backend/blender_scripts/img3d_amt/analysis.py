"""Mesh analysis: the DESIGN section 3 metric keys, fully vectorized (~5-8 s for 1.9M faces / 0.9M vertices).

``analyze(ctx, obj)`` returns ``{metric_key: value}`` for: vertices, faces, triangles, quads, ngons,
non_manifold_edges, boundary_edges, holes, largest_hole_edges, components, small_components, degenerate_faces,
duplicate_vertices, inconsistent_normal_edges, symmetry_error_pct, surface_area, mean_edge_length, uv_layers,
materials, textures, armatures, max_bone_influences, unnormalized_weight_verts.
Extra details (hole list, component sizes, symmetry plane, timings) go to ``ctx.store["analysis"][label]``.

Definitions
* non-manifold edge: used by 3+ faces; boundary edge: used by 1 face.
* hole: connected loop of boundary edges with >= ``HOLE_MIN_EDGES`` (5) edges.
* component: faces connected through shared vertices; small: fewer than ``island_min_ratio`` x total faces.
* degenerate face: area <= ``DEGENERATE_REL_AREA`` x diag^2.
* duplicate vertices: vertices at exactly (float32) the same position as another vertex.
* inconsistent normal edges: manifold edges whose two faces are wound in opposite directions (one face flipped).
* symmetry error: mean distance (% of diag) from sampled vertices mirrored across the best of a few X planes
  (x = 0, bbox centre, median) to the nearest vertex (KD-tree) - TRELLIS characters score ~0.06-0.12 %.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import bpy
import numpy as np
from mathutils import kdtree

from . import meshdata
from .context import TunerContext

log = logging.getLogger(__name__)

HOLE_MIN_EDGES = 5
DEGENERATE_REL_AREA = 1e-10
SYMMETRY_SAMPLES = 20000
WEIGHT_TOLERANCE = 1e-3

METRIC_KEYS = ("vertices", "faces", "triangles", "quads", "ngons", "non_manifold_edges", "boundary_edges", "holes",
               "largest_hole_edges", "components", "small_components", "degenerate_faces", "duplicate_vertices",
               "inconsistent_normal_edges", "symmetry_error_pct", "surface_area", "mean_edge_length", "uv_layers",
               "materials", "textures", "armatures", "max_bone_influences", "unnormalized_weight_verts")


def analyze(ctx: TunerContext, obj: bpy.types.Object, *, label: str = "before", symmetry: bool = True,
            progress: tuple[float, float] = (0.0, 1.0)) -> dict[str, Any]:
    """Compute the metrics of ``obj`` (a MESH object) and store details in ``ctx.store["analysis"][label]``.

    ``progress`` = (start, end) fraction range of the current step used for ``ctx.progress`` reports.
    """
    p0, p1 = progress

    def report(frac: float, msg: str) -> None:
        ctx.progress(p0 + (p1 - p0) * frac, msg)

    timings: dict[str, float] = {}
    t_all = time.perf_counter()
    me = obj.data
    m: dict[str, Any] = {}
    details: dict[str, Any] = {}

    t = time.perf_counter()
    co = meshdata.get_positions(me)
    n_verts, n_faces = len(me.vertices), len(me.polygons)
    start, total = meshdata.get_polygon_loops(me)
    m["vertices"], m["faces"] = n_verts, n_faces
    m["triangles"] = int((total == 3).sum())
    m["quads"] = int((total == 4).sum())
    m["ngons"] = int((total > 4).sum())
    diag = float(np.linalg.norm(co.max(0) - co.min(0))) if n_verts else 0.0
    details["diag"] = diag
    timings["read"] = time.perf_counter() - t
    report(0.05, "Reading mesh")

    if n_faces == 0:
        m.update({k: 0 for k in ("non_manifold_edges", "boundary_edges", "holes", "largest_hole_edges", "components",
                                 "small_components", "degenerate_faces", "inconsistent_normal_edges")})
        m["duplicate_vertices"] = meshdata.count_duplicate_positions(co)
        m["surface_area"] = 0.0
        m["mean_edge_length"] = None
        m["symmetry_error_pct"] = None
    else:
        t = time.perf_counter()
        ef = meshdata.edge_faces(me)
        lv = meshdata.get_loop_vertices(me)
        m["non_manifold_edges"] = int(ef.non_manifold.sum())
        m["boundary_edges"] = int(ef.boundary.sum())
        details["wire_edges"] = int(ef.wire.sum())
        details["max_faces_per_edge"] = int(ef.face_count.max()) if len(ef.face_count) else 0
        m["inconsistent_normal_edges"] = int(len(meshdata.inconsistent_winding_edges(ef, lv)))
        elen = meshdata.edge_lengths(co, ef.edges)
        m["mean_edge_length"] = float(elen.mean()) if len(elen) else None
        details["mean_edge_length_pct_diag"] = 100.0 * float(elen.mean()) / diag if len(elen) and diag else None
        timings["edges"] = time.perf_counter() - t
        report(0.3, "Checking edges")

        t = time.perf_counter()
        loops = meshdata.boundary_loops(ef.edges, ef.boundary, co)
        holes = loops.holes(HOLE_MIN_EDGES)
        m["holes"] = int(len(holes))
        m["largest_hole_edges"] = int(loops.edge_count[0]) if len(holes) else 0
        details["boundary_loops"] = loops.count
        details["largest_holes"] = [
            {"edges": int(loops.edge_count[k]),
             "perimeter_pct_diag": round(100.0 * float(loops.perimeter[k]) / diag, 4)}
            for k in holes[:10]]
        timings["holes"] = time.perf_counter() - t
        report(0.4, "Finding holes")

        t = time.perf_counter()
        areas = meshdata.get_face_areas(me)
        m["surface_area"] = float(areas.sum())
        m["degenerate_faces"] = int((areas <= DEGENERATE_REL_AREA * diag * diag).sum())
        flab, sizes = meshdata.face_components(n_verts, ef.edges, lv[start])
        m["components"] = int(len(sizes))
        ratio = float(ctx.options.island_min_ratio)
        small = sizes < ratio * n_faces
        m["small_components"] = int(small.sum())
        details["largest_components_faces"] = sizes[:10].tolist()
        details["faces_in_small_components"] = int(sizes[small].sum())
        details["island_min_ratio"] = ratio
        timings["components"] = time.perf_counter() - t
        report(0.6, "Counting parts")

        t = time.perf_counter()
        m["duplicate_vertices"] = meshdata.count_duplicate_positions(co)
        timings["duplicates"] = time.perf_counter() - t
        del ef, lv, flab, elen

        if symmetry and n_verts >= 8:
            t = time.perf_counter()
            report(0.7, "Measuring symmetry")
            sym = symmetry_error(co, diag)
            m["symmetry_error_pct"] = sym["mean_pct"]
            details["symmetry"] = sym
            timings["symmetry"] = time.perf_counter() - t
        else:
            m["symmetry_error_pct"] = None

    t = time.perf_counter()
    m["uv_layers"] = len(me.uv_layers)
    materials = [s.material for s in obj.material_slots if s.material is not None]
    m["materials"] = len(set(materials))
    m["textures"] = len(meshdata.unique_images(materials))
    m["armatures"] = sum(1 for a in ctx.armatures if a is not None and a.name in bpy.data.objects)
    rig = rig_weight_stats(obj)
    m["max_bone_influences"] = rig["max_bone_influences"]
    m["unnormalized_weight_verts"] = rig["unnormalized_weight_verts"]
    if rig["deform_groups"]:
        details["rig"] = rig
    timings["attributes"] = time.perf_counter() - t
    report(1.0, "Analysis complete")

    timings["total"] = time.perf_counter() - t_all
    details["timings_s"] = {k: round(v, 3) for k, v in timings.items()}
    ctx.store.setdefault("analysis", {})[label] = {"metrics": dict(m), "details": details}
    log.debug("analysis[%s] %.2fs: %s", label, timings["total"], m)
    return m


def symmetry_error(co: np.ndarray, diag: float, samples: int = SYMMETRY_SAMPLES, seed: int = 0) -> dict[str, Any]:
    """X-mirror error of a point set (see module doc). Returns mean/p95 in % of diag and the chosen plane x."""
    n = len(co)
    tree = kdtree.KDTree(n)
    for i, p in enumerate(co.tolist()):
        tree.insert(p, i)
    tree.balance()
    rng = np.random.default_rng(seed)
    idx = rng.choice(n, size=min(samples, n), replace=False)
    pts = co[idx]
    xmin, xmax = float(co[:, 0].min()), float(co[:, 0].max())
    candidates = sorted({0.0 if xmin < 0.0 < xmax else 0.5 * (xmin + xmax), 0.5 * (xmin + xmax),
                         float(np.median(co[:, 0]))})
    find = tree.find

    def mirror_dist(points: np.ndarray, plane_x: float) -> np.ndarray:
        mirrored = points.copy()
        mirrored[:, 0] = 2.0 * plane_x - mirrored[:, 0]
        return np.fromiter((find(p)[2] for p in mirrored.tolist()), dtype=np.float64, count=len(mirrored))

    probe = pts[:2000]
    best_x = min(candidates, key=lambda x: float(mirror_dist(probe, x).mean()))
    dist = mirror_dist(pts, best_x)
    scale = 100.0 / diag if diag > 0 else 0.0
    return {"plane_x": round(best_x, 6), "samples": int(len(pts)), "mean_pct": round(float(dist.mean()) * scale, 5),
            "p95_pct": round(float(np.percentile(dist, 95)) * scale, 5),
            "max_pct": round(float(dist.max()) * scale, 5)}


def rig_weight_stats(obj: bpy.types.Object) -> dict[str, Any]:
    """Deform-weight statistics for a skinned mesh (no armature -> zeros).

    ``max_bone_influences``: max number of deform bones with weight > 0 on one vertex;
    ``unnormalized_weight_verts``: weighted vertices whose deform weights do not sum to 1 (+-1e-3).
    """
    stats = {"deform_groups": 0, "max_bone_influences": 0, "unnormalized_weight_verts": 0, "unweighted_verts": 0}
    armature = None
    for mod in obj.modifiers:
        if mod.type == "ARMATURE" and mod.object is not None:
            armature = mod.object
            break
    if armature is None and obj.parent is not None and obj.parent.type == "ARMATURE":
        armature = obj.parent
    if armature is None or not obj.vertex_groups:
        return stats
    bones = {b.name for b in armature.data.bones if b.use_deform}
    deform = np.zeros(max(g.index for g in obj.vertex_groups) + 1, dtype=bool)
    for g in obj.vertex_groups:
        deform[g.index] = g.name in bones
    stats["deform_groups"] = int(deform.sum())
    if not deform.any():
        return stats
    max_inf = 0
    unnormalized = 0
    unweighted = 0
    n_groups = len(deform)
    for v in obj.data.vertices:
        count = 0
        total = 0.0
        for g in v.groups:
            if g.group < n_groups and deform[g.group] and g.weight > 0.0:
                count += 1
                total += g.weight
        if count == 0:
            unweighted += 1
            continue
        if count > max_inf:
            max_inf = count
        if abs(total - 1.0) > WEIGHT_TOLERANCE:
            unnormalized += 1
    stats.update(max_bone_influences=max_inf, unnormalized_weight_verts=unnormalized, unweighted_verts=unweighted)
    return stats
