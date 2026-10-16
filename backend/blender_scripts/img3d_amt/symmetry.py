"""X-mirror symmetry for ``face_refinement``: global score, best-fit plane check, local defect repair, symmetrize.

* :func:`measure` - mean / p95 vertex-to-mirrored-vertex distance (% of the bbox diagonal) for the best of a few
  X planes (x = 0, bbox centre, median, refined offset) plus a small yaw check. TRELLIS characters score
  0.06-0.12 %, the fox 3 % and the castle 2.9 %.
* :func:`decide` - ``options.symmetry``: ``off`` skip · ``auto`` local repair only for a mirror-symmetric
  (mean < ``AUTO_MAX_ERROR_PCT``) humanoid · ``repair`` local repair regardless · ``symmetrize`` full mirror of the
  better side (never chosen automatically).
* :func:`local_repair` - localized asymmetric defects (dents / pits / blobs / hole patches): vertices farther than a
  noise-adaptive threshold from the mirrored surface are clustered; per cluster the side with the higher defect
  score (high-pass roughness, ``amt_filled`` hole-fill vertices always count as defective) is moved onto the mirror
  image of the other side's surface (BVH projection with normal check), blended over a few rings. Large or strong
  clusters (hair partings, one-sided ribbons) are left alone.
* :func:`symmetrize` - ``bmesh.ops.symmetrize`` in the plane frame, source side = lower defect score.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import bmesh
import bpy
import numpy as np
from mathutils import kdtree
from mathutils.bvhtree import BVHTree

from . import smoothing

log = logging.getLogger(__name__)

AUTO_MAX_ERROR_PCT = 0.3
"""auto mode repairs only when the global mean mirror error is below this (% of diag)."""
HUMANOID_MAX_ERROR_PCT = 0.6
SCORE_SAMPLES = 20_000
MEASURE_CAP_PCT = 5.0
"""Mirror distances are clipped at this (% of diag) - beyond it a model is simply asymmetric."""
YAW_STEPS_DEG = (-2.0, -1.0, -0.5, 0.5, 1.0, 2.0)
CLUSTER_MAX_EXTENT_PCT = 2.5
"""Defect clusters (pits / blobs / dents) larger than this (% of diag) are intentional details - left alone."""
CLUSTER_MAX_EXTENT_FILLED_PCT = 6.0
"""Hole-fill patches (``amt_filled``) may be larger."""
CLUSTER_MAX_ERROR_PCT = 1.0
CLUSTER_MAX_VERTS_FRAC = 0.005
CLUSTER_MIN_VERTS = 4
DEFECT_RATIO = 1.6
MIN_PROJECTED_FRAC = 0.85
HYSTERESIS = 0.4
BLEND_RINGS = 3


@dataclass
class MirrorPlane:
    """Mirror plane through ``point`` with unit ``normal`` (horizontal; yaw = rotation about +Z from +X)."""

    offset: float = 0.0
    yaw_deg: float = 0.0
    y_center: float = 0.0

    @property
    def normal(self) -> np.ndarray:
        a = math.radians(self.yaw_deg)
        return np.array([math.cos(a), math.sin(a), 0.0])

    @property
    def point(self) -> np.ndarray:
        return np.array([self.offset, self.y_center, 0.0])

    def signed_distance(self, co: np.ndarray) -> np.ndarray:
        return (co - self.point) @ self.normal

    def reflect(self, co: np.ndarray) -> np.ndarray:
        n = self.normal
        return co - 2.0 * ((co - self.point) @ n)[:, None] * n[None, :]

    def reflect_vectors(self, vec: np.ndarray) -> np.ndarray:
        n = self.normal
        return vec - 2.0 * (vec @ n)[:, None] * n[None, :]

    def to_dict(self) -> dict[str, float]:
        return {"offset": round(self.offset, 6), "yaw_deg": round(self.yaw_deg, 3)}


@dataclass
class SymmetryScore:
    mean_pct: float
    p95_pct: float
    max_pct: float
    plane: MirrorPlane
    candidates: dict[str, float] = field(default_factory=dict)
    yaw_check: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"mean_pct": round(self.mean_pct, 5), "p95_pct": round(self.p95_pct, 5),
                "max_pct": round(self.max_pct, 5), "plane": self.plane.to_dict(),
                "plane_candidates_mean_pct": {k: round(v, 5) for k, v in self.candidates.items()},
                "yaw_check": self.yaw_check}


# ================================================================================================================
# KD / BVH helpers
# ================================================================================================================


def build_kdtree(co: np.ndarray) -> kdtree.KDTree:
    """KD-tree over all vertices (~0.5 s for 0.9M)."""
    tree = kdtree.KDTree(len(co))
    for i, p in enumerate(co.tolist()):
        tree.insert(p, i)
    tree.balance()
    return tree


def nearest_vertices(tree: kdtree.KDTree, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``(distance (N,), vertex index (N,))`` of the nearest tree vertex for each point (~2.2 us/point)."""
    find = tree.find
    n = len(points)
    dist = np.empty(n, np.float64)
    idx = np.empty(n, np.int64)
    for i, p in enumerate(points.tolist()):
        _, j, d = find(p)
        idx[i] = j
        dist[i] = d
    return dist, idx


def surface_bvh(co: np.ndarray, tris: np.ndarray) -> BVHTree:
    """BVH of the triangle surface (~0.9 s for 1.2M triangles)."""
    return BVHTree.FromPolygons(np.asarray(co, dtype=np.float64).tolist(), np.asarray(tris).tolist(),
                                all_triangles=True)


def mirror_distances(bvh: BVHTree, points: np.ndarray, plane: MirrorPlane, max_dist: float) -> np.ndarray:
    """Distance from each point's mirror image to the surface (clipped at ``max_dist``; ~5 us/point)."""
    find = bvh.find_nearest
    out = np.full(len(points), float(max_dist))
    for i, p in enumerate(plane.reflect(points).tolist()):
        hit = find(p, max_dist)
        if hit[0] is not None:
            out[i] = hit[3]
    return out


# ================================================================================================================
# global score
# ================================================================================================================


def measure(co: np.ndarray, tris: np.ndarray, diag: float, bvh: BVHTree | None = None, *,
            samples: int = SCORE_SAMPLES, seed: int = 0) -> tuple[SymmetryScore, BVHTree]:
    """Global X-mirror error for the best plane (see module doc): point -> mirrored SURFACE distances, so the score
    does not depend on the mesh resolution. Returns ``(score, surface BVH)`` (the BVH is reused by the repair)."""
    if bvh is None:
        bvh = surface_bvh(co, tris)
    rng = np.random.default_rng(seed)
    n = len(co)
    pts = co[rng.choice(n, size=min(samples, n), replace=False)] if n else co
    probe = pts[:3000]
    scale = 100.0 / diag if diag > 0 else 0.0
    cap = MEASURE_CAP_PCT / 100.0 * diag
    y_center = float(np.median(co[:, 1])) if n else 0.0

    def err(points: np.ndarray, plane: MirrorPlane) -> np.ndarray:
        return mirror_distances(bvh, points, plane, cap)

    xmin, xmax = (float(co[:, 0].min()), float(co[:, 0].max())) if n else (0.0, 0.0)
    cands = {"x0": 0.0, "bbox_center": 0.5 * (xmin + xmax), "median": float(np.median(co[:, 0])) if n else 0.0}
    cand_err = {k: float(err(probe, MirrorPlane(v, 0.0, y_center)).mean()) * scale for k, v in cands.items()}
    best_key = min(cand_err, key=cand_err.get)
    best = MirrorPlane(cands[best_key], 0.0, y_center)
    best_err = cand_err[best_key]
    # refine the offset (golden-section on +-0.5 % of the diagonal)
    lo, hi = best.offset - 0.005 * diag, best.offset + 0.005 * diag
    g = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = hi - g * (hi - lo), lo + g * (hi - lo)
    fa = float(err(probe, MirrorPlane(a, 0.0, y_center)).mean()) * scale
    fb = float(err(probe, MirrorPlane(b, 0.0, y_center)).mean()) * scale
    for _ in range(10):
        if fa < fb:
            hi, b, fb = b, a, fa
            a = hi - g * (hi - lo)
            fa = float(err(probe, MirrorPlane(a, 0.0, y_center)).mean()) * scale
        else:
            lo, a, fa = a, b, fb
            b = lo + g * (hi - lo)
            fb = float(err(probe, MirrorPlane(b, 0.0, y_center)).mean()) * scale
    refined = a if fa < fb else b
    if min(fa, fb) < best_err * 0.97:
        best = MirrorPlane(refined, 0.0, y_center)
        best_err = min(fa, fb)
        cand_err["refined"] = best_err
    # yaw check (tilted characters)
    yaw_err = {}
    for yaw in YAW_STEPS_DEG:
        yaw_err[yaw] = float(err(probe, MirrorPlane(best.offset, yaw, y_center)).mean()) * scale
    best_yaw = min(yaw_err, key=yaw_err.get)
    yaw_info = {"best_yaw_deg": 0.0, "improvement": 0.0}
    if yaw_err[best_yaw] < 0.75 * best_err:
        best = MirrorPlane(best.offset, best_yaw, y_center)
        yaw_info = {"best_yaw_deg": best_yaw, "improvement": round(1.0 - yaw_err[best_yaw] / best_err, 3)}
    dist = err(pts, best)
    score = SymmetryScore(mean_pct=float(dist.mean()) * scale if len(dist) else 0.0,
                          p95_pct=float(np.percentile(dist, 95)) * scale if len(dist) else 0.0,
                          max_pct=float(dist.max()) * scale if len(dist) else 0.0,
                          plane=best, candidates=cand_err, yaw_check=yaw_info)
    return score, bvh


def decide(mode: str, score: SymmetryScore, humanoid: bool) -> tuple[str, str]:
    """``(action, reason)`` with action in ``skip`` / ``repair`` / ``symmetrize`` (see module doc)."""
    if mode == "off":
        return "skip", "symmetry disabled (off)"
    if mode == "symmetrize":
        return "symmetrize", "full symmetrize requested"
    if mode == "repair":
        return "repair", "local repair requested"
    if not humanoid:
        return "skip", "not an upright mirror-symmetric character - symmetry repair skipped"
    if score.mean_pct >= AUTO_MAX_ERROR_PCT:
        return "skip", (f"model is not mirror-symmetric (mean error {score.mean_pct:.2f}% >= "
                        f"{AUTO_MAX_ERROR_PCT}%) - symmetry repair skipped")
    return "repair", f"mirror-symmetric character (mean error {score.mean_pct:.3f}%)"


# ================================================================================================================
# local repair
# ================================================================================================================


def defect_score(co: np.ndarray, topo: smoothing.MeshTopology, normals: np.ndarray, edge_len: np.ndarray,
                 region: np.ndarray) -> np.ndarray:
    """(V,) high-pass roughness (|normal . (x - lowpass(x))| / edge length) computed only on ``region``."""
    active = smoothing.dilate(region, topo, 4)
    low = smoothing.taubin(co, topo, iterations=15, lam=0.6, mu=-0.63, active=active, locked=topo.locked)
    hp = np.abs(np.einsum("ij,ij->i", co - low, normals)) / np.maximum(edge_len, 1e-12)
    return np.where(region, hp, 0.0)


def local_repair(co: np.ndarray, tris: np.ndarray, topo: smoothing.MeshTopology, plane: MirrorPlane,
                 tree: kdtree.KDTree, bvh: BVHTree, diag: float, *, filled: np.ndarray | None = None,
                 edge_len: np.ndarray | None = None, progress=None) -> tuple[np.ndarray, dict[str, Any]]:
    """Repair localized asymmetric defects from the better side (see module doc). Returns ``(positions, details)``.

    ``tree`` = KD-tree of the vertices (screening), ``bvh`` = surface BVH of ``co`` (projection: the mirror image of
    a defective vertex is projected onto the surface and the hit is mirrored back).
    """
    n = len(co)
    report = progress or (lambda f, m: None)
    if edge_len is None:
        edge_len = smoothing.vertex_mean_edge_length(co, topo)
    l_med = float(np.median(edge_len)) if n else 0.0
    side = plane.signed_distance(co)
    away = np.abs(side) > 2.0 * l_med
    filled = np.zeros(n, bool) if filled is None else np.asarray(filled, dtype=bool).copy()
    # 1. screening: vertex -> nearest vertex of the mirrored point set
    report(0.05, "Comparing both sides")
    err = np.zeros(n, np.float64)
    ids = np.nonzero(away)[0]
    d, _ = nearest_vertices(tree, plane.reflect(co[ids]))
    err[ids] = d
    noise = float(np.median(d)) if len(d) else 0.0
    tau = max(2.5 * noise, 1.2 * l_med, 0.0015 * diag)
    flagged = (err > tau) | filled
    flagged &= away
    details: dict[str, Any] = {"noise_floor_pct": round(100.0 * noise / diag, 5), "threshold_pct":
                               round(100.0 * tau / diag, 5), "flagged_vertices": int(flagged.sum())}
    if not flagged.any():
        details.update(clusters=0, repaired_clusters=0, moved_vertices=0)
        return co.copy(), details
    # hysteresis: grow each seed region over the connected vertices above 40 % of the threshold (a blob's base,
    # the shallow rim of a dent) so the whole defect moves, not just its strongest part
    weak = ((err > HYSTERESIS * tau) | filled) & away
    labels, count = smoothing.mask_components(weak, topo)
    if count:
        seeded = np.zeros(count, bool)
        seeded[np.unique(labels[flagged & weak])] = True
        flagged = weak & seeded[np.maximum(labels, 0)]
    flagged = smoothing.dilate(flagged, topo, 1) & away
    labels, count = smoothing.mask_components(flagged, topo)
    details["clusters"] = int(count)
    report(0.3, f"Scoring {count} asymmetric regions")
    # 2. per-vertex defect score on the flagged regions and their mirrored counterparts
    normals = smoothing.vertex_normals(co, tris)
    _, partner = nearest_vertices(tree, plane.reflect(co[flagged]))
    partner_of = np.full(n, -1, np.int64)
    partner_of[flagged] = partner
    region = flagged.copy()
    region[partner] = True
    score = defect_score(co, topo, normals, edge_len, region)
    # 3. decide per cluster (size / strength limits, better side)
    sizes = np.bincount(labels[flagged], minlength=count)
    max_verts = max(2000, int(CLUSTER_MAX_VERTS_FRAC * n))
    reasons: dict[str, int] = {}
    accepted: list[dict[str, Any]] = []
    order = np.argsort(-sizes)
    members_all = np.argsort(labels[flagged], kind="stable")
    flagged_ids = np.nonzero(flagged)[0][members_all]
    starts = np.zeros(count + 1, np.int64)
    np.cumsum(sizes, out=starts[1:])

    def skip(reason: str) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1

    for c in order.tolist():
        members = flagged_ids[starts[c]:starts[c + 1]]
        filled_frac = float(filled[members].mean())
        if len(members) < CLUSTER_MIN_VERTS and filled_frac == 0.0:
            skip("tiny")
            continue
        pts = co[members]
        extent = float(np.linalg.norm(pts.max(0) - pts.min(0)))
        emax = float(err[members].max())
        partners = partner_of[members]
        partners = partners[partners >= 0]
        partner_filled = float(filled[partners].mean()) if len(partners) else 0.0
        patch = filled_frac >= 0.3 and partner_filled < 0.1  # hole-fill patch: always adopt the real side
        max_extent = (CLUSTER_MAX_EXTENT_FILLED_PCT if patch else CLUSTER_MAX_EXTENT_PCT) / 100.0 * diag
        if len(members) > max_verts or extent > max_extent:
            skip("too_large")
            continue
        if emax > CLUSTER_MAX_ERROR_PCT / 100.0 * diag and not patch:
            skip("too_strong")
            continue
        s_self = float(score[members].mean())
        s_mirror = float(score[partners].mean()) if len(partners) else 0.0
        if patch:
            verdict = True
        elif partner_filled >= 0.3 and filled_frac < 0.1:
            verdict = False
        else:
            verdict = s_self > DEFECT_RATIO * s_mirror + 0.02
        if not verdict:
            skip("better_side")
            continue
        accepted.append({"members": members, "extent": extent, "emax": emax, "score": s_self,
                         "mirror_score": s_mirror, "filled_frac": filled_frac, "patch": patch})
    if not accepted:
        details.update(skipped_clusters=reasons, repaired_clusters=0, repaired=[], moved_vertices=0)
        return co.copy(), details
    # 4. targets: project the cluster vertices onto the mirrored mesh (normal-compatible hit)
    report(0.6, "Projecting onto the mirrored side")
    # normals of the underlying surface (diffused over ~6 rings): a blob's own flanks face sideways, the mirrored
    # surface it must be projected onto faces like the surrounding skin
    smooth_n = normals.copy()
    for _ in range(6):
        smooth_n = smoothing.neighbor_mean(smooth_n, topo.adj, topo.adj_rows)
    smooth_n /= np.maximum(np.linalg.norm(smooth_n, axis=1), 1e-12)[:, None]
    find, ray = bvh.find_nearest, bvh.ray_cast

    def project(p: list, nv: list, max_dist: float) -> list | None:
        hit = find(p, max_dist)
        if hit[0] is not None and (hit[1][0] * nv[0] + hit[1][1] * nv[1] + hit[1][2] * nv[2]) > 0.3:
            return hit[0]
        best = None
        for sign in (1.0, -1.0):
            h = ray(p, (sign * nv[0], sign * nv[1], sign * nv[2]), max_dist)
            if h[0] is not None and (h[1][0] * nv[0] + h[1][1] * nv[1] + h[1][2] * nv[2]) > 0.3:
                if best is None or h[3] < best[1]:
                    best = (h[0], h[3])
        return None if best is None else best[0]

    movable = ~smoothing.boundary_vertices(topo)
    delta = np.zeros((n, 3), np.float64)
    core = np.zeros(n, bool)
    repaired: list[dict[str, Any]] = []
    n_projected = 0
    n_unprojected = 0
    err_ids: list[np.ndarray] = []
    for cl in accepted:
        members = cl["members"][movable[cl["members"]]]
        if len(members) == 0:
            skip("border_only")
            continue
        max_dist = max(4.0 * tau, 1.5 * cl["emax"])
        targets = np.zeros((len(members), 3))
        ok = np.zeros(len(members), bool)
        # project the MIRROR image of each vertex onto the surface, then mirror the hit back
        rp = plane.reflect(co[members]).tolist()
        rn = plane.reflect_vectors(smooth_n[members]).tolist()
        for k, (p, nv) in enumerate(zip(rp, rn)):
            t = project(p, nv, max_dist)
            if t is not None:
                targets[k] = t
                ok[k] = True
        targets = plane.reflect(targets)
        if ok.mean() < MIN_PROJECTED_FRAC:  # a partly projected cluster would be crumpled - leave it alone
            skip("unprojectable")
            continue
        delta[members[ok]] = targets[ok] - co[members[ok]]
        core[members[ok]] = True
        n_projected += int(ok.sum())
        n_unprojected += int((~ok).sum())
        err_ids.append(members[ok])
        if len(repaired) < 25:
            repaired.append({"vertices": int(len(cl["members"])),
                             "centroid": [round(float(v), 4) for v in co[cl["members"]].mean(0)],
                             "extent_pct": round(100.0 * cl["extent"] / diag, 4),
                             "max_error_pct": round(100.0 * cl["emax"] / diag, 4),
                             "score": round(cl["score"], 4), "mirror_score": round(cl["mirror_score"], 4),
                             "filled_frac": round(cl["filled_frac"], 3), "hole_patch": cl["patch"]})
    details["skipped_clusters"] = reasons
    details["repaired_clusters"] = len(err_ids)
    details["repaired"] = repaired
    if not core.any():
        details["moved_vertices"] = 0
        return co.copy(), details
    # 5. smooth the displacement inside the core, then blend it to zero over a few rings outside
    delta = smoothing.taubin(delta, topo, iterations=2, lam=0.5, mu=0.0, active=core, locked=~movable)
    ring = smoothing.dilate(core, topo, BLEND_RINGS) & ~core & movable
    if ring.any():
        delta = smoothing.fair(delta, topo, ring, order=1, iterations=200)
    delta[~movable] = 0.0
    new = co + delta
    moved = np.linalg.norm(delta, axis=1)
    mv = moved > 0
    ids_ok = np.concatenate(err_ids)
    after, _ = nearest_vertices(tree, plane.reflect(new[ids_ok]))
    details.update({"moved_vertices": int(mv.sum()), "projected_vertices": n_projected,
                    "unprojected_vertices": n_unprojected,
                    "move_mean_pct": round(100.0 * float(moved[mv].mean()) / diag, 5) if mv.any() else 0.0,
                    "move_max_pct": round(100.0 * float(moved.max()) / diag, 5),
                    "error_before_mean_pct": round(100.0 * float(err[ids_ok].mean()) / diag, 5),
                    "error_after_mean_pct": round(100.0 * float(after.mean()) / diag, 5)})
    return new, details


# ================================================================================================================
# full symmetrize
# ================================================================================================================


def side_defect_scores(co: np.ndarray, topo: smoothing.MeshTopology, normals: np.ndarray, edge_len: np.ndarray,
                       plane: MirrorPlane, filled: np.ndarray | None = None) -> dict[str, float]:
    """Defect score per side (+ = positive plane side): mean roughness + boundary/non-manifold + filled vertices."""
    side = plane.signed_distance(co)
    sample = np.zeros(len(co), bool)
    rng = np.random.default_rng(0)
    pick = rng.choice(len(co), size=min(len(co), 150_000), replace=False)
    sample[pick] = True
    rough = defect_score(co, topo, normals, edge_len, sample)
    out = {}
    for name, sel in (("positive", side > 0), ("negative", side < 0)):
        s = sel & sample
        out[name] = float(rough[s].mean()) if s.any() else 0.0
        out[name] += 0.5 * float(topo.locked[sel].mean()) if sel.any() else 0.0
        if filled is not None and sel.any():
            out[name] += 1.0 * float(np.asarray(filled, bool)[sel].mean())
    return out


def symmetrize(obj: bpy.types.Object, plane: MirrorPlane, source: str, weld_dist: float) -> dict[str, Any]:
    """Mirror the ``source`` side (``positive``/``negative`` side of ``plane``) onto the other (bmesh, in place)."""
    from . import meshdata

    me = obj.data
    before_v, before_f = len(me.vertices), len(me.polygons)
    a = math.radians(plane.yaw_deg)
    # plane frame: rotate by -yaw about Z through the plane point, translate the plane to x = 0
    c, s = math.cos(-a), math.sin(-a)
    rot = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    p = plane.point
    meshdata.set_positions(me, (meshdata.get_positions(me) - p) @ rot.T)
    direction = "X" if source == "positive" else "-X"
    bm = bmesh.new()
    try:
        bm.from_mesh(me)
        geom = list(bm.verts) + list(bm.edges) + list(bm.faces)
        bmesh.ops.symmetrize(bm, input=geom, direction=direction, dist=float(weld_dist), use_shapekey=False)
        bm.to_mesh(me)
    finally:
        bm.free()
    meshdata.set_positions(me, meshdata.get_positions(me) @ rot + p)
    me.update()
    return {"source_side": source, "direction": "X" if source == "positive" else "-X",
            "vertices_before": before_v, "vertices_after": len(me.vertices), "faces_before": before_f,
            "faces_after": len(me.polygons), "weld_dist": weld_dist}
