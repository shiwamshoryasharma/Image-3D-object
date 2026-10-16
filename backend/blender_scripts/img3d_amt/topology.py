"""Topology tuning (``topology_tuning``): denoise, adaptive density refinement, quads, subdivision rule, shading.

Sub-steps (each reported in ``details`` with numbers):

1. **denoise** (``options.smoothing`` > 0): feature-preserving bilateral normal filtering + vertex update
   (:func:`smoothing.denoise`). Strength scales iterations / range sigma / displacement cap. "Rough" meshes (fur,
   hard-surface props: dihedral p50 > 12 deg or X-mirror error > 1 % of the diagonal) get a gentler setting.
   Boundary / non-manifold vertices never move; the face region processed by ``face_refinement``
   (``amt_face_region``) is only lightly re-smoothed.
2. **refine_density** (``options.refine_density``): (a) facet fairing - robust low-pass that rounds the flat-facet
   stair-stepping TRELLIS leaves on legs / skirts / hair while rejecting large displacements (real details);
   (b) adaptive split of long edges (> 2 x median length, smooth regions only) with ``bmesh.ops.subdivide_edges``
   (UVs, attributes and weights are interpolated), the new vertices are moved onto the curved PN-triangle edge
   midpoints and relaxed. Growth is capped at +30 % faces and 3.5M faces in total.
3. **quads** (``options.quads``): ``bmesh.ops.join_triangles`` respecting UVs, materials, seams and sharp edges.
4. **subdivision** (``options.subdivision``): ``auto`` adds a Subdivision Surface modifier (viewport 1, render 2)
   only when the result has <= 300k faces, ``modifier`` always adds it, ``off`` never. It is never applied to the
   GLB (the exporter disables it); it is kept in the optional ``.blend``.
5. **shading**: when the geometry changed, imported custom normals are dropped, faces set smooth and edges sharper
   than ``SHARP_ANGLE_DEG`` marked sharp.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

import bmesh
import bpy
import numpy as np

from . import meshdata, smoothing
from .context import StepOutcome, TunerContext

log = logging.getLogger(__name__)

ROUGH_DIHEDRAL_P50_DEG = 12.0
ROUGH_SYMMETRY_PCT = 1.0
SPLIT_LENGTH_FACTOR = 2.0
SPLIT_MAX_DIHEDRAL_DEG = 30.0
MAX_GROWTH = 0.30
MAX_FACES = 3_500_000
SUBDIVISION_AUTO_MAX_FACES = 300_000
SHARP_ANGLE_DEG = 60.0
FACE_REGION_DENOISE_WEIGHT = 0.3
SUBSURF_NAME = "AMT_Subdivision"


# ================================================================================================================
# shared mesh I/O helpers (also used by face.py / symmetry.py)
# ================================================================================================================


class MeshArrays:
    """Positions + triangles + topology of a Blender mesh (triangulated view for non-triangle meshes)."""

    def __init__(self, mesh: bpy.types.Mesh) -> None:
        self.mesh = mesh
        self.co = meshdata.get_positions(mesh)
        self.all_tris = meshdata.is_all_triangles(mesh)
        if self.all_tris:
            self.tris = meshdata.get_triangles(mesh)
            self.topo = smoothing.build_topology(self.tris, len(self.co), edges=meshdata.get_edges(mesh),
                                                 corner_edges=meshdata.get_loop_edges(mesh).reshape(-1, 3))
            self.tri_poly = None
        else:
            self.tris, self.tri_poly, _ = meshdata.get_loop_triangles(mesh)
            self.topo = smoothing.build_topology(self.tris, len(self.co))
        self.diag = float(np.linalg.norm(self.co.max(0) - self.co.min(0))) if len(self.co) else 0.0
        self._edge_len: np.ndarray | None = None

    @property
    def n_verts(self) -> int:
        return len(self.co)

    def edge_len(self) -> np.ndarray:
        """(V,) local mean edge length of the positions this snapshot was read with (cached)."""
        if self._edge_len is None:
            self._edge_len = smoothing.vertex_mean_edge_length(self.co, self.topo)
        return self._edge_len


def apply_positions(mesh: bpy.types.Mesh, co: np.ndarray) -> None:
    """Write positions; shape keys (if any) receive the same per-vertex delta so the basis stays consistent."""
    keys = mesh.shape_keys
    if keys is not None and len(keys.key_blocks):
        old = meshdata.get_positions(mesh)
        delta = np.asarray(co, dtype=np.float64) - old
        for block in keys.key_blocks:
            buf = np.empty(len(mesh.vertices) * 3, np.float32)
            block.data.foreach_get("co", buf)
            buf = (buf.reshape(-1, 3) + delta).astype(np.float32).reshape(-1)
            block.data.foreach_set("co", buf)
    meshdata.set_positions(mesh, co)


def face_region_weights(mesh: bpy.types.Mesh) -> np.ndarray | None:
    """``amt_face_region`` (POINT, 0..1) written by face_refinement, or ``None``."""
    values = meshdata.get_attribute(mesh, "amt_face_region")
    if values is None or values.ndim != 1 or len(values) != len(mesh.vertices):
        return None
    return np.clip(values.astype(np.float64), 0.0, 1.0)


def pct(value: float, diag: float) -> float:
    """``value`` in % of ``diag`` (rounded for reports)."""
    return round(100.0 * float(value) / diag, 5) if diag > 0 else 0.0


# ================================================================================================================
# step
# ================================================================================================================


def run(ctx: TunerContext) -> StepOutcome:
    """Run the topology tuning sub-steps on the primary mesh (see module doc)."""
    opts = ctx.options
    obj = ctx.require_primary()
    me = obj.data
    t_step = time.perf_counter()
    timings: dict[str, float] = {}
    details: dict[str, Any] = {}
    done: list[str] = []
    if len(me.polygons) == 0:
        return StepOutcome("skipped", "The mesh has no faces", {})

    faces_before = len(me.polygons)
    verts_before = len(me.vertices)
    geometry_changed = False
    arrays: MeshArrays | None = None
    profile: dict[str, Any] = {"rough": False}
    if opts.smoothing > 0 or opts.refine_density:
        ctx.progress(0.02, "Reading mesh")
        t = time.perf_counter()
        arrays = MeshArrays(me)
        timings["read_topology"] = time.perf_counter() - t
        t = time.perf_counter()
        profile = classify_surface(ctx, arrays)
        timings["classify"] = time.perf_counter() - t
        details["surface"] = profile
    face_w = face_region_weights(me)
    co = arrays.co.copy() if arrays is not None else None

    # ---------------------------------------------------------------- 1. denoise
    if arrays is not None and opts.smoothing > 0:
        ctx.progress(0.08, "Denoising surface")
        t = time.perf_counter()
        co, info = denoise_surface(arrays, co, float(opts.smoothing), rough=bool(profile["rough"]), face_w=face_w)
        timings["denoise"] = time.perf_counter() - t
        details["denoise"] = info
        done.append(f"denoised ({info['mode']}, p95 shift {info['disp_p95_pct']:.3f}% of size)")
        geometry_changed = True
    else:
        details["denoise"] = {"skipped": "smoothing = 0"}

    # ---------------------------------------------------------------- 2a. facet fairing
    if arrays is not None and opts.refine_density:
        ctx.progress(0.3, "Smoothing flat facets")
        t = time.perf_counter()
        co, finfo = fair_facets(arrays, co, float(opts.smoothing), rough=bool(profile["rough"]), face_w=face_w)
        timings["facet_fairing"] = time.perf_counter() - t
        details["facet_fairing"] = finfo
        if finfo.get("free_vertices"):
            geometry_changed = True
            done.append(f"faired flat facets ({finfo['free_vertices']:,} vertices)")
    if arrays is not None and geometry_changed:
        apply_positions(me, co)

    # ---------------------------------------------------------------- 2b. adaptive split
    if arrays is not None and opts.refine_density:
        ctx.progress(0.5, "Refining long edges")
        t = time.perf_counter()
        split = refine_long_edges(ctx, obj, arrays, co)
        timings["split"] = time.perf_counter() - t
        details["refine_density"] = split
        if split.get("split_edges"):
            geometry_changed = True
            done.append(f"split {split['split_edges']:,} long edges (+{split['growth_pct']:.1f}% faces)")
    else:
        details["refine_density"] = {"skipped": "refine_density disabled"}
    arrays = None  # release the big arrays before the bmesh passes

    # ---------------------------------------------------------------- 3. quads
    if opts.quads:
        ctx.progress(0.78, "Converting triangles to quads")
        t = time.perf_counter()
        qinfo = convert_to_quads(me)
        timings["quads"] = time.perf_counter() - t
        details["quads"] = qinfo
        done.append(f"{qinfo['quads']:,} quads")
    else:
        details["quads"] = {"skipped": "quads disabled"}

    # ---------------------------------------------------------------- 4. subdivision rule
    ctx.progress(0.86, "Subdivision rule")
    details["subdivision"] = apply_subdivision_rule(obj, opts.subdivision)
    if details["subdivision"].get("added"):
        done.append("Subdivision Surface modifier added (kept out of the GLB)")

    # ---------------------------------------------------------------- 5. final shading
    ctx.progress(0.9, "Final shading")
    t = time.perf_counter()
    details["shading"] = final_shading(ctx, obj, geometry_changed=geometry_changed)
    timings["shading"] = time.perf_counter() - t
    if details["shading"].get("applied"):
        done.append(f"smooth shading with {details['shading']['sharp_edges']:,} sharp edges "
                    f"(> {SHARP_ANGLE_DEG:.0f} deg)")

    details["faces_before"] = faces_before
    details["faces_after"] = len(me.polygons)
    details["vertices_before"] = verts_before
    details["vertices_after"] = len(me.vertices)
    timings["total"] = time.perf_counter() - t_step
    details["timings_s"] = {k: round(v, 3) for k, v in timings.items()}
    ctx.store["topology"] = {"geometry_changed": geometry_changed, "faces_after": len(me.polygons)}
    ctx.progress(1.0, "Topology tuned")
    if not done:
        return StepOutcome("skipped", "Nothing to do with the current options", details)
    message = "; ".join(done)
    return StepOutcome("done", message[:1].upper() + message[1:], details)


# ================================================================================================================
# 0. classification
# ================================================================================================================


def classify_surface(ctx: TunerContext, arrays: MeshArrays) -> dict[str, Any]:
    """Rough (fur / hard-surface / asymmetric prop) vs smooth character surface (see module doc)."""
    fn, _, _ = smoothing.face_geometry(arrays.co, arrays.tris)
    stats = smoothing.dihedral_stats(smoothing.dihedral_angles(fn, arrays.topo))
    sym = _symmetry_pct(ctx, arrays)
    reasons = []
    if stats["p50"] > ROUGH_DIHEDRAL_P50_DEG:
        reasons.append(f"dihedral p50 {stats['p50']:.1f} deg > {ROUGH_DIHEDRAL_P50_DEG:.0f}")
    if sym is not None and sym > ROUGH_SYMMETRY_PCT:
        reasons.append(f"mirror error {sym:.2f}% > {ROUGH_SYMMETRY_PCT:.0f}%")
    return {"rough": bool(reasons), "reasons": reasons, "dihedral": stats,
            "symmetry_error_pct": None if sym is None else round(sym, 5)}


def _symmetry_pct(ctx: TunerContext, arrays: MeshArrays) -> float | None:
    sym = ctx.store.get("symmetry")
    if isinstance(sym, dict) and sym.get("mean_pct") is not None:
        return float(sym["mean_pct"])
    before = (ctx.store.get("analysis") or {}).get("before") or {}
    value = ((before.get("details") or {}).get("symmetry") or {}).get("mean_pct")
    if value is not None:
        return float(value)
    try:
        from . import analysis

        return float(analysis.symmetry_error(arrays.co, arrays.diag, samples=8000)["mean_pct"])
    except Exception as exc:  # informational only
        log.debug("symmetry estimate failed: %s", exc)
        return None


# ================================================================================================================
# 1. denoise
# ================================================================================================================


def denoise_params(strength: float, rough: bool) -> dict[str, Any]:
    """Bilateral denoise parameters for ``strength`` in (0, 1] (``rough`` -> gentler, see module doc)."""
    s = min(max(float(strength), 0.0), 1.0)
    if rough:
        s *= 0.4
    return {"mode": "gentle" if rough else "normal", "strength": round(s, 3),
            "normal_iterations": max(1, int(round(2 + 4 * s))),
            "vertex_iterations": max(2, int(round(3 + 10 * s))),
            "sigma_r": round((0.22 if rough else 0.25) + 0.15 * s, 3),
            "max_disp_edges": round(0.3 + 0.6 * s, 3)}


def denoise_surface(arrays: MeshArrays, co: np.ndarray, strength: float, *, rough: bool,
                    face_w: np.ndarray | None) -> tuple[np.ndarray, dict[str, Any]]:
    """Bilateral denoise of ``co`` with step-level parameters; returns ``(positions, report dict)``."""
    prm = denoise_params(strength, rough)
    weights = None
    if face_w is not None:
        weights = 1.0 - (1.0 - FACE_REGION_DENOISE_WEIGHT) * face_w
    new, info = smoothing.denoise(co, arrays.topo, normal_iterations=prm["normal_iterations"],
                                  vertex_iterations=prm["vertex_iterations"], sigma_r=prm["sigma_r"],
                                  weights=weights, max_disp=prm["max_disp_edges"] * arrays.edge_len())
    fn0, _, _ = smoothing.face_geometry(co, arrays.tris)
    fn1, _, _ = smoothing.face_geometry(new, arrays.tris)
    out = dict(prm)
    out["normal_change_deg_mean"] = round(info.get("normal_change_deg_mean", 0.0), 3)
    out["normal_change_deg_p95"] = round(info.get("normal_change_deg_p95", 0.0), 3)
    out["disp_mean_pct"] = pct(info["disp_mean"], arrays.diag)
    out["disp_p95_pct"] = pct(info["disp_p95"], arrays.diag)
    out["disp_max_pct"] = pct(info["disp_max"], arrays.diag)
    out["locked_vertices"] = int(arrays.topo.locked.sum())
    out["dihedral_before"] = smoothing.dihedral_stats(smoothing.dihedral_angles(fn0, arrays.topo))
    out["dihedral_after"] = smoothing.dihedral_stats(smoothing.dihedral_angles(fn1, arrays.topo))
    return new, out


# ================================================================================================================
# 2. density refinement
# ================================================================================================================


def fair_facets(arrays: MeshArrays, co: np.ndarray, strength: float, *, rough: bool,
                face_w: np.ndarray | None) -> tuple[np.ndarray, dict[str, Any]]:
    """Facet fairing pass (see :func:`smoothing.fair_facets`) with step-level parameters."""
    s = min(max(float(strength), 0.0), 1.0)
    iterations = 12 if rough else 30
    gain = (0.3 if rough else 0.7) + 0.3 * s
    weights = None
    if face_w is not None:  # the face is handled by face_refinement (nose / eyes must not be rounded off)
        weights = 1.0 - face_w
    new, info = smoothing.fair_facets(co, arrays.topo, iterations=iterations, strength=gain, weights=weights,
                                      edge_len=arrays.edge_len())
    out = {"iterations": iterations, "strength": round(gain, 3),
           "feature_vertices": info.get("feature_vertices", 0), "free_vertices": info.get("free_vertices", 0),
           "attenuated_frac": round(info.get("attenuated_frac", 0.0), 5),
           "disp_mean_pct": pct(info.get("disp_mean", 0.0), arrays.diag),
           "disp_p95_pct": pct(info.get("disp_p95", 0.0), arrays.diag),
           "disp_max_pct": pct(info.get("disp_max", 0.0), arrays.diag)}
    return new, out


def _blender_edge_ids(mesh: bpy.types.Mesh, pairs: np.ndarray) -> np.ndarray:
    """Blender edge index of each vertex pair (-1 when the pair is not a mesh edge)."""
    edges = meshdata.get_edges(mesh)
    n = max(len(mesh.vertices), 1)
    key_m = np.minimum(edges[:, 0], edges[:, 1]) * n + np.maximum(edges[:, 0], edges[:, 1])
    order = np.argsort(key_m)
    sorted_keys = key_m[order]
    key_p = np.minimum(pairs[:, 0], pairs[:, 1]) * n + np.maximum(pairs[:, 0], pairs[:, 1])
    pos = np.clip(np.searchsorted(sorted_keys, key_p), 0, len(sorted_keys) - 1)
    found = sorted_keys[pos] == key_p
    return np.where(found, order[pos], -1)


def select_split_edges(arrays: MeshArrays, co: np.ndarray, max_edges: int) -> tuple[np.ndarray, dict[str, Any]]:
    """Long manifold edges in smooth regions, longest first (ids into ``arrays.topo.edges``)."""
    topo = arrays.topo
    lengths = smoothing.edge_lengths(co, topo.edges)
    median = float(np.median(lengths)) if len(lengths) else 0.0
    info: dict[str, Any] = {"median_edge_pct": pct(median, arrays.diag),
                            "threshold_pct": pct(SPLIT_LENGTH_FACTOR * median, arrays.diag)}
    if median <= 0 or len(topo.pair_edges) == 0 or max_edges <= 0:
        return np.zeros(0, np.int64), info
    fn, _, _ = smoothing.face_geometry(co, arrays.tris)
    dih = smoothing.dihedral_angles(fn, topo)
    vmax = smoothing.vertex_max_dihedral(dih, topo)
    e = topo.edges[topo.pair_edges]
    long_ = lengths[topo.pair_edges] > SPLIT_LENGTH_FACTOR * median
    smooth = (dih < SPLIT_MAX_DIHEDRAL_DEG) & (vmax[e[:, 0]] < 2 * SPLIT_MAX_DIHEDRAL_DEG) \
        & (vmax[e[:, 1]] < 2 * SPLIT_MAX_DIHEDRAL_DEG)
    free = ~(topo.locked[e[:, 0]] | topo.locked[e[:, 1]])
    cand = topo.pair_edges[long_ & smooth & free]
    info["long_edges"] = int(long_.sum())
    info["candidates"] = int(len(cand))
    if len(cand) > max_edges:
        cand = cand[np.argsort(-lengths[cand], kind="stable")[:max_edges]]
        info["capped_to"] = int(max_edges)
    return cand.astype(np.int64), info


def refine_long_edges(ctx: TunerContext, obj: bpy.types.Object, arrays: MeshArrays,
                      co: np.ndarray) -> dict[str, Any]:
    """Split long smooth-region edges with bmesh, curve the new vertices (PN midpoints), relax them."""
    me = obj.data
    n_faces = len(me.polygons)
    budget_faces = min(int(MAX_GROWTH * n_faces), MAX_FACES - n_faces)
    if budget_faces <= 0:
        return {"skipped": f"face budget exhausted ({n_faces:,} faces)", "split_edges": 0}
    cand, info = select_split_edges(arrays, co, budget_faces // 2)
    if len(cand) == 0:
        info["split_edges"] = 0
        return info
    pairs = arrays.topo.edges[cand]
    blender_ids = cand if arrays.all_tris else _blender_edge_ids(me, pairs)
    ok = blender_ids >= 0
    pairs, blender_ids = pairs[ok], blender_ids[ok]
    # curved targets: PN-triangle midpoints from smoothed vertex normals of the current surface
    vn = smoothing.vertex_normals(co, arrays.tris)
    vn = smoothing.neighbor_mean(vn, arrays.topo.adj, arrays.topo.adj_rows)
    vn /= np.maximum(np.linalg.norm(vn, axis=1), 1e-12)[:, None]
    p0, p1 = co[pairs[:, 0]], co[pairs[:, 1]]
    targets = smoothing.pn_midpoints(p0, p1, vn[pairs[:, 0]], vn[pairs[:, 1]])
    linear = 0.5 * (p0 + p1)
    offset = targets - linear
    olen = np.linalg.norm(offset, axis=1)
    cap = 0.25 * np.linalg.norm(p1 - p0, axis=1)
    over = olen > cap
    offset[over] *= (cap[over] / olen[over])[:, None]
    targets = linear + offset

    n_verts_old = len(me.vertices)
    faces_old = len(me.polygons)
    ctx.progress(0.55, f"Splitting {len(blender_ids):,} edges")
    bm = bmesh.new()
    try:
        bm.from_mesh(me)
        bm.edges.ensure_lookup_table()
        edges = [bm.edges[i] for i in blender_ids.tolist()]
        result = bmesh.ops.subdivide_edges(bm, edges=edges, cuts=1, use_grid_fill=False, use_single_edge=True,
                                           use_only_quads=False, smooth=0.0)
        if arrays.all_tris:
            touched: set = set()
            for elem in result["geom_inner"]:
                if isinstance(elem, bmesh.types.BMVert):
                    touched.update(elem.link_faces)
            ngons = [f for f in touched if len(f.verts) > 3]
            if ngons:
                bmesh.ops.triangulate(bm, faces=ngons, quad_method="BEAUTY", ngon_method="BEAUTY")
        ctx.progress(0.68, "Writing refined mesh")
        bm.to_mesh(me)
    finally:
        bm.free()
    me.update()

    new_co = meshdata.get_positions(me)
    n_new = len(new_co) - n_verts_old
    info["split_edges"] = int(len(blender_ids))
    info["new_vertices"] = int(n_new)
    info["faces_before"] = faces_old
    info["faces_after"] = len(me.polygons)
    info["growth_pct"] = round(100.0 * (len(me.polygons) - faces_old) / max(faces_old, 1), 3)
    if n_new <= 0:
        return info
    if not np.allclose(new_co[:n_verts_old], co, atol=1e-6 * max(arrays.diag, 1e-9)):
        log.warning("Vertex order changed during the edge split; curved placement skipped")
        info["curved"] = False
        return info
    ctx.progress(0.72, "Curving new vertices")
    match, good = _match_points(new_co[n_verts_old:], linear, 1e-4 * max(arrays.diag, 1e-9))
    new_ids = np.arange(n_verts_old, len(new_co), dtype=np.int64)[good]
    placed = new_co.copy()
    placed[new_ids] = targets[match[good]]
    # relax only the new vertices along their normals (removes PN overshoot on noisy normals)
    tris = meshdata.get_triangles(me) if meshdata.is_all_triangles(me) else meshdata.get_loop_triangles(me)[0]
    topo = smoothing.build_topology(tris, len(placed))
    nrm = smoothing.vertex_normals(placed, tris)
    relaxed = smoothing.taubin(placed, topo, iterations=2, lam=0.4, mu=-0.42, active=new_ids, normal_only=nrm)
    apply_positions(me, relaxed)
    shift = np.linalg.norm(relaxed[new_ids] - new_co[new_ids], axis=1)
    info["curved"] = True
    info["curved_vertices"] = int(len(new_ids))
    info["curve_offset_mean_pct"] = pct(float(shift.mean()) if len(shift) else 0.0, arrays.diag)
    info["curve_offset_max_pct"] = pct(float(shift.max()) if len(shift) else 0.0, arrays.diag)
    return info


def _match_points(points: np.ndarray, reference: np.ndarray, tol: float) -> tuple[np.ndarray, np.ndarray]:
    """Index of the nearest ``reference`` point for every point (KD-tree) and whether it is within ``tol``."""
    from mathutils import kdtree

    tree = kdtree.KDTree(len(reference))
    for i, p in enumerate(reference.tolist()):
        tree.insert(p, i)
    tree.balance()
    match = np.zeros(len(points), np.int64)
    dist = np.full(len(points), np.inf)
    find = tree.find
    for j, p in enumerate(points.tolist()):
        _, idx, d = find(p)
        if idx is not None:
            match[j] = idx
            dist[j] = d
    return match, dist <= tol


# ================================================================================================================
# 3. quads
# ================================================================================================================


def convert_to_quads(mesh: bpy.types.Mesh) -> dict[str, Any]:
    """Join triangle pairs into quads (UV / material / seam / sharp aware)."""
    before = len(mesh.polygons)
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        faces = [f for f in bm.faces if len(f.verts) == 3]
        bmesh.ops.join_triangles(bm, faces=faces, cmp_seam=True, cmp_sharp=True, cmp_uvs=True, cmp_vcols=True,
                                 cmp_materials=True, angle_face_threshold=math.radians(40.0),
                                 angle_shape_threshold=math.radians(40.0))
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update()
    _, total = meshdata.get_polygon_loops(mesh)
    return {"faces_before": before, "faces_after": len(mesh.polygons), "quads": int((total == 4).sum()),
            "triangles": int((total == 3).sum())}


# ================================================================================================================
# 4. subdivision
# ================================================================================================================


def apply_subdivision_rule(obj: bpy.types.Object, mode: str) -> dict[str, Any]:
    """DESIGN section 4 subdivision rule; the modifier is never applied to the exported GLB."""
    faces = len(obj.data.polygons)
    existing = [m for m in obj.modifiers if m.type in ("SUBSURF", "MULTIRES")]
    if mode == "off":
        return {"mode": mode, "added": False, "reason": "subdivision off"}
    if existing:
        return {"mode": mode, "added": False, "reason": f"object already has '{existing[0].name}'"}
    if mode == "auto" and faces > SUBDIVISION_AUTO_MAX_FACES:
        return {"mode": mode, "added": False,
                "reason": f"{faces:,} faces > {SUBDIVISION_AUTO_MAX_FACES:,} (auto keeps dense meshes as they are)"}
    mod = obj.modifiers.new(SUBSURF_NAME, "SUBSURF")
    mod.levels = 1
    mod.render_levels = 2
    try:
        mod.uv_smooth = "PRESERVE_BOUNDARIES"
    except (AttributeError, TypeError):
        pass
    return {"mode": mode, "added": True, "modifier": mod.name, "levels": 1, "render_levels": 2,
            "applied_to_glb": False, "faces": faces}


# ================================================================================================================
# 5. shading
# ================================================================================================================


def _geometry_differs(ctx: TunerContext, mesh: bpy.types.Mesh) -> bool:
    original = ctx.original
    if original is None:
        return True
    om = original.data
    if len(om.vertices) != len(mesh.vertices) or len(om.polygons) != len(mesh.polygons):
        return True
    return not np.array_equal(meshdata.get_positions(om, np.float32), meshdata.get_positions(mesh, np.float32))


def final_shading(ctx: TunerContext, obj: bpy.types.Object, *, geometry_changed: bool) -> dict[str, Any]:
    """Smooth shading + sharp-by-angle when the geometry changed (imported custom normals would be stale)."""
    me = obj.data
    changed = geometry_changed or _geometry_differs(ctx, me)
    had_custom = bool(getattr(me, "has_custom_normals", False))
    if not changed and had_custom:
        return {"applied": False, "reason": "geometry unchanged - imported normals kept"}
    cleared = meshdata.clear_custom_normals(me) if had_custom else False
    me.shade_smooth()
    angle = math.radians(SHARP_ANGLE_DEG)
    setter = getattr(me, "set_sharp_from_angle", None)
    if callable(setter):
        setter(angle=angle)
        sharp = 0
        attr = me.attributes.get("sharp_edge")
        if attr is not None:
            buf = np.zeros(len(me.edges), dtype=bool)
            attr.data.foreach_get("value", buf)
            sharp = int(buf.sum())
    else:  # pragma: no cover - pre-4.1 API
        sharp = _mark_sharp_numpy(me, angle)
    me.update()
    return {"applied": True, "custom_normals_cleared": bool(cleared), "sharp_angle_deg": SHARP_ANGLE_DEG,
            "sharp_edges": sharp}


def _mark_sharp_numpy(me: bpy.types.Mesh, angle: float) -> int:
    ef = meshdata.edge_faces(me)
    fn = meshdata.get_face_normals(me)
    m, f1, f2, _, _ = ef.manifold_pairs()
    dots = np.einsum("ij,ij->i", fn[f1], fn[f2])
    sharp = np.zeros(len(me.edges), dtype=bool)
    sharp[m[dots < math.cos(angle)]] = True
    sharp[ef.non_manifold] = True
    meshdata.set_attribute(me, "sharp_edge", sharp, domain="EDGE", data_type="BOOLEAN")
    return int(sharp.sum())
