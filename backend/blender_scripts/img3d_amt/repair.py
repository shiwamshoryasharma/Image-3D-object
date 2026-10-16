"""The ``geometry_repair`` step: fixes the hollow double-walled TRELLIS.2 shell, holes, junk parts and normals.

Sub-steps (timed individually in ``details["timings_s"]``):

1. **cleanup** - weld near-duplicate vertices (``1e-6 x diag``, only when any exist; the load step already welds),
   find loose vertices/edges, degenerate faces (area <= ``1e-10 x diag^2``, dissolved with
   ``bmesh.ops.dissolve_degenerate``) and duplicate faces (same vertex set: the copy seen from outside is kept,
   all copies go when none is visible).
2. **visibility** (``visibility.py``) - per-face counts from 102 orthographic directions on the GPU (face-ID
   renders; CPU ray-grid fallback): ``front`` / ``back`` (first hit on either side) and ``behind`` (back-facing,
   anti-parallel wall within ``t_max`` behind a face-on visible face) + one wall partner per face.
3. **wall pairing** - faces without decisive evidence (hidden, not under a visible wall) get their opposite wall by a
   BVH ray along ``-n`` (``t_max`` estimated from the data: median wall gap, capped at 6 x mean edge).
4. **inner-shell classification** - seeds: seen from outside in >= 3 views -> outer; never seen but under a
   visible anti-parallel wall -> inner. Labels then spread over the surface (adjacent faces: same label) and across
   wall pairs (opposite label) by a multi-source BFS + relaxation, so hidden double-walled regions (e.g. the body
   under a skirt) and faces seen only through holes are decided by their wall, not by a local threshold. Regions
   without any seed are fully hidden interior junk. Thin sheets keep both walls wherever both are seen.
   Safety: abort (keep the shell, warning) when > 70 % of the faces would go or when re-rendering the kept faces
   shows removed visible surface / lost silhouette / receded depth (GPU only).
   ``ctx.store["original_visible_face_mask"]`` = faces of ``ctx.original`` seen from outside that the repair meant
   to keep.
5. **islands** - parts (vertex-connected) smaller than ``island_min_ratio`` of the faces that are hidden (< 5 % of
   their area seen) or tiny (bbox < 0.5 % of the diagonal or < 16 faces) are removed; big separate parts (e.g. legs)
   are never removed.
6. **non-manifold** - edges with > 2 faces: small manifold patches hanging on them (fins, <= 64 faces, beyond the
   two largest) are removed; the remaining count is reported.
7. **normals** - ``recalc_face_normals`` (consistent winding per manifold region), then each region is flipped when
   its visibility says it is seen from the back; custom normals are cleared, smooth shading + sharp edges above
   60 degrees (``Mesh.set_sharp_from_angle``).
8. **holes** (``holes.py``) - boundary cycles with <= ``max_hole_edges`` edges and perimeter <= 25 % diag are
   triangulated, refined, faired and UV-mapped from one UV island; new vertices get ``amt_filled = 1``.

Metrics: ``inner_shell_faces`` (before = faces classified as inner wall, after = left in the mesh) and
``hidden_faces`` (before = never seen from outside, after = hidden faces of the result, GPU only).
Performance (1.9M faces, RTX 5090, Blender 5.2): ~30-40 s, peak ~6 GB.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import bmesh
import bpy
import numpy as np

from . import holes, meshdata, visibility
from .context import StepError, StepOutcome, TunerContext

log = logging.getLogger(__name__)

WELD_REL = 1e-6
DEGENERATE_REL_AREA = 1e-10
DISSOLVE_REL_DIST = 1e-5
KEEP_MIN_VIEWS = 3
"""Faces seen from outside in at least this many views are always kept (never classified as inner wall)."""
PAIR_VOTE_WEIGHT = 2.0
DEL_SPECK_FACES = 64
"""Inner-labelled regions up to this size are kept (avoids punching tiny holes into hidden walls)."""
KEEP_SPECK_FACES = 48
"""Barely visible kept regions up to this size, surrounded by inner faces, are removed (inner-wall specks)."""
MAX_DELETE_FRACTION = 0.70
SAFETY_REMOVED_VISIBLE = 0.02
SAFETY_SILHOUETTE_LOST = 0.003
SAFETY_RECEDED = 0.02
SAFETY_TOLERANCE_TMAX = 4.0
ISLAND_HIDDEN_AREA = 0.05
ISLAND_TINY_REL = 0.005
ISLAND_TINY_FACES = 16
FIN_MAX_FACES = 64
FIN_ROUNDS = 2
SHARP_ANGLE_DEG = 60.0
PAIR_RAY_BUDGET_S = 20.0
FINAL_VIEWS = 40
SRC_ATTRIBUTE = "amt_src_face"

ProgressRange = tuple[float, float]


# ================================================================================================================
# inner-shell classification (pure numpy)
# ================================================================================================================


@dataclass
class ShellPlan:
    """Inner-shell / hidden-interior classification of the faces (all arrays (F,))."""

    label: np.ndarray
    """int8: +1 keep (outer surface), -1 inner wall, 0 unreached (fully hidden, no evidence in its region)."""
    seed: np.ndarray
    """int8 label from the face's own visibility evidence (0 = no evidence, decided by propagation)."""
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def inner(self) -> np.ndarray:
        return self.label < 0

    @property
    def unreached(self) -> np.ndarray:
        return self.label == 0


def shell_seeds(front: np.ndarray, back: np.ndarray, behind: np.ndarray, *, keep_min: int = KEEP_MIN_VIEWS,
                shell: bool = True) -> np.ndarray:
    """Per-face evidence label.

    * +1 outer: seen from outside (either side) in >= ``keep_min`` views;
    * -1 inner: never seen from outside but lying directly under a visible, anti-parallel wall (``behind`` >= 1)
      - the inner wall of a hollow shell or the hidden far side of a thin sheet (both invisible);
    * 0: no decisive evidence (hidden and not under a visible wall, or seen in only 1-2 views) - decided by
      propagation.
    """
    vis = front.astype(np.int64) + back.astype(np.int64)
    seed = np.zeros(len(vis), np.int8)
    seed[vis >= keep_min] = 1
    if shell:
        seed[(vis == 0) & (behind > 0)] = -1
    return seed


def _csr(n: int, src: np.ndarray, dst: np.ndarray, val: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    order = np.argsort(src, kind="stable")
    indptr = np.zeros(n + 1, np.int64)
    np.cumsum(np.bincount(src, minlength=n), out=indptr[1:])
    return indptr, dst[order], val[order]


def _gather(indptr: np.ndarray, rows: np.ndarray) -> np.ndarray:
    """Positions of all CSR entries of ``rows``."""
    start = indptr[rows]
    count = indptr[rows + 1] - start
    total = int(count.sum())
    if total == 0:
        return np.zeros(0, np.int64)
    offs = np.repeat(start - np.cumsum(count) + count, count)
    return offs + np.arange(total, dtype=np.int64)


def label_graph(n: int, f1: np.ndarray, f2: np.ndarray, partner: np.ndarray | None
                ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Directed vote graph in CSR form ``(indptr, dst, kind)``: ``kind`` 0 = adjacent faces (vote the SAME label),
    1 = wall pair (vote the OPPOSITE label, see :func:`propagate_labels`)."""
    src = [f1, f2]
    dst = [f2, f1]
    kind = [np.zeros(len(f1), np.int8), np.zeros(len(f1), np.int8)]
    if partner is not None:
        g = np.flatnonzero(partner >= 0)
        p = partner[g]
        src += [g, p]
        dst += [p, g]
        kind += [np.ones(len(g), np.int8), np.ones(len(g), np.int8)]
    return _csr(n, np.concatenate(src), np.concatenate(dst), np.concatenate(kind))


def _votes(lab_src: np.ndarray, kind: np.ndarray, hidden_tgt: np.ndarray, pair_weight: float) -> np.ndarray:
    """Vote of a labelled source face for a target face.

    Adjacent faces vote their own label (+-1). A wall partner that is an INNER wall votes "keep" (+w): the face
    opposite an inner wall is the outer wall. A KEPT partner votes "inner" (-w) only when the target is never seen
    from outside: thin sheets have two kept walls, so a visible target must not be pushed inwards by its partner.
    """
    adj = kind == 0
    out = np.where(adj, lab_src.astype(np.float64), 0.0)
    pair = ~adj
    out[pair & (lab_src < 0)] = pair_weight
    out[pair & (lab_src > 0) & hidden_tgt] = -pair_weight
    return out


def propagate_labels(seed: np.ndarray, graph: tuple[np.ndarray, np.ndarray, np.ndarray], hidden: np.ndarray,
                     pair_weight: float = PAIR_VOTE_WEIGHT, relax_iterations: int = 2) -> np.ndarray:
    """Multi-source BFS of +1/-1 labels into 0-faces over the vote graph, then a few relaxation passes.

    BFS: a face reached for the first time takes the sign of the summed votes (:func:`_votes`) of its labelled
    neighbours (ties -> +1, conservative). Faces in regions without any seed stay 0. Relaxation: non-seed faces are
    re-voted by ALL their labelled neighbours (removes BFS-order artefacts where fronts met).
    """
    indptr, dst, kind = graph
    lab = seed.astype(np.int8).copy()
    frontier = np.flatnonzero(lab != 0)
    rows_of = np.repeat(np.arange(len(lab), dtype=np.int64), np.diff(indptr))
    while len(frontier):
        pos = _gather(indptr, frontier)
        tgt = dst[pos]
        free = lab[tgt] == 0
        if not free.any():
            break
        pos, tgt = pos[free], tgt[free]
        vote = _votes(lab[rows_of[pos]], kind[pos], hidden[tgt], pair_weight)
        uniq, inv = np.unique(tgt, return_inverse=True)
        total = np.bincount(inv.reshape(-1), weights=vote, minlength=len(uniq))
        lab[uniq] = np.where(total >= 0, 1, -1).astype(np.int8)
        frontier = uniq
    free_faces = seed == 0
    for _ in range(max(0, relax_iterations)):
        vote = _votes(lab[rows_of], kind, hidden[dst], pair_weight)
        total = np.bincount(dst, weights=vote, minlength=len(lab))
        new = np.where(total > 0, 1, np.where(total < 0, -1, lab)).astype(np.int8)
        update = free_faces & (lab != 0)
        lab[update] = new[update]
    return lab


def regularize_labels(label: np.ndarray, vis: np.ndarray, partner: np.ndarray | None, f1: np.ndarray,
                      f2: np.ndarray, *, del_speck: int = DEL_SPECK_FACES, keep_speck: int = KEEP_SPECK_FACES,
                      rounds: int = 2) -> tuple[np.ndarray, dict[str, int]]:
    """Remove label specks that would punch tiny holes into hidden walls (all decisions are on hidden or barely
    visible faces, so the visible surface is unaffected).

    * inner regions (manifold-connected faces labelled -1) with <= ``del_speck`` faces are kept instead (a tiny
      hidden double-wall patch is harmless; a tiny hole is not);
    * kept regions with <= ``keep_speck`` faces that are completely surrounded by inner faces are dropped when they
      are barely visible (< ``KEEP_MIN_VIEWS`` views on every face) - inner-wall specks seen through holes.
    """
    lab = label.copy()
    n = len(lab)
    stats = {"reverted_inner_regions": 0, "reverted_inner_faces": 0, "dropped_keep_regions": 0,
             "dropped_keep_faces": 0}
    for _ in range(rounds):
        changed = False
        for target, limit in ((-1, del_speck), (1, keep_speck)):
            mine = lab == target
            other = lab == -target
            m = mine[f1] & mine[f2]
            comp = meshdata.connected_components(n, f1[m], f2[m])
            k = int(comp.max()) + 1 if n else 0
            size = np.bincount(comp[mine], minlength=k)
            # border: manifold edges to the opposite label (regions touching nothing labelled are separate parts)
            cut1 = mine[f1] & other[f2]
            cut2 = mine[f2] & other[f1]
            border = np.bincount(np.concatenate([comp[f1[cut1]], comp[f2[cut2]]]), minlength=k)
            small = (size > 0) & (size <= limit) & (border > 0)
            if target > 0:
                # every face of the region must be barely visible, and the region must not touch unlabelled faces
                strong = np.bincount(comp[mine & (vis >= KEEP_MIN_VIEWS)], minlength=k) > 0
                free1 = mine[f1] & (lab[f2] == 0)
                free2 = mine[f2] & (lab[f1] == 0)
                loose = np.bincount(np.concatenate([comp[f1[free1]], comp[f2[free2]]]), minlength=k) > 0
                small &= ~strong & ~loose
            flip = mine & small[comp]
            if flip.any():
                changed = True
                lab[flip] = -target
                key = "reverted_inner" if target < 0 else "dropped_keep"
                stats[key + "_regions"] += int(small.sum())
                stats[key + "_faces"] += int(flip.sum())
        if not changed:
            break
    return lab, stats


def classify_shell(front: np.ndarray, back: np.ndarray, behind: np.ndarray, partner: np.ndarray | None,
                   f1: np.ndarray, f2: np.ndarray, *, shell: bool, keep_min: int = KEEP_MIN_VIEWS,
                   seed: np.ndarray | None = None, regularize: bool = True) -> ShellPlan:
    """Label every face outer / inner / unreached from visibility evidence + propagation over the surface
    (``f1``-``f2`` manifold face adjacency) and across wall pairs (``partner``), then remove label specks.
    ``seed`` overrides :func:`shell_seeds`."""
    if seed is None:
        seed = shell_seeds(front, back, behind, keep_min=keep_min, shell=shell)
    vis = front.astype(np.int64) + back
    graph = label_graph(len(seed), f1, f2, partner if shell else None)
    label = propagate_labels(seed, graph, vis == 0)
    stats: dict[str, Any] = {}
    if regularize and shell:
        label, stats = regularize_labels(label, vis, partner, f1, f2)
    stats.update({"seed_keep": int((seed > 0).sum()), "seed_inner": int((seed < 0).sum()),
                  "seed_none": int((seed == 0).sum()), "keep": int((label > 0).sum()),
                  "inner": int((label < 0).sum()), "unreached": int((label == 0).sum()),
                  "visible_inner": int(((label < 0) & (vis > 0)).sum()),
                  "paired": int((partner >= 0).sum()) if partner is not None else 0})
    return ShellPlan(label, seed, stats)


# ================================================================================================================
# mesh arrays
# ================================================================================================================


@dataclass
class MeshArrays:
    """Numpy snapshot of a mesh's topology/geometry (see meshdata conventions)."""

    co: np.ndarray
    loop_start: np.ndarray
    loop_total: np.ndarray
    loop_verts: np.ndarray
    loop_edges: np.ndarray
    loop_face: np.ndarray
    nxt: np.ndarray
    edges: np.ndarray
    edge_face_count: np.ndarray
    face_normals: np.ndarray
    areas: np.ndarray
    tris: np.ndarray
    tri_poly: np.ndarray | None
    """Polygon of every render triangle (``None`` when the mesh is all triangles: triangle == polygon)."""

    @classmethod
    def read(cls, me: bpy.types.Mesh) -> MeshArrays:
        start, total = meshdata.get_polygon_loops(me)
        lv = meshdata.get_loop_vertices(me)
        le = meshdata.get_loop_edges(me)
        edges = meshdata.get_edges(me)
        all_tri = bool(total.size == 0 or (total == 3).all())
        if all_tri:
            tris = meshdata.get_triangles(me)
            tri_poly = None
        else:
            tris, tri_poly, _ = meshdata.get_loop_triangles(me)
        return cls(co=meshdata.get_positions(me), loop_start=start, loop_total=total, loop_verts=lv, loop_edges=le,
                   loop_face=meshdata.loop_faces(total), nxt=holes.loop_next(start, total), edges=edges,
                   edge_face_count=np.bincount(le, minlength=len(edges)).astype(np.int64),
                   face_normals=meshdata.get_face_normals(me), areas=meshdata.get_face_areas(me), tris=tris,
                   tri_poly=tri_poly)

    @property
    def n_faces(self) -> int:
        return int(len(self.loop_start))

    def tri_normals(self) -> np.ndarray:
        if self.tri_poly is None:
            return self.face_normals
        return meshdata.triangle_normals(self.co, self.tris)

    def to_faces(self, per_tri: np.ndarray, reduce: str = "max") -> np.ndarray:
        """Aggregate a per-render-triangle array to polygons (identity for all-triangle meshes)."""
        if self.tri_poly is None:
            return per_tri
        out = np.zeros(self.n_faces, dtype=per_tri.dtype)
        if reduce == "max":
            np.maximum.at(out, self.tri_poly, per_tri)
        else:
            np.add.at(out, self.tri_poly, per_tri)
        return out

    def manifold_face_pairs(self, face_mask: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
        """``(f1, f2)`` faces sharing an edge used by exactly two (masked) faces."""
        loops = np.arange(len(self.loop_edges)) if face_mask is None else np.flatnonzero(face_mask[self.loop_face])
        le = self.loop_edges[loops]
        counts = np.bincount(le, minlength=len(self.edges))
        sel = loops[counts[le] == 2]
        order = np.argsort(self.loop_edges[sel], kind="stable")
        pairs = self.loop_face[sel[order]].reshape(-1, 2)
        return pairs[:, 0], pairs[:, 1]


# ================================================================================================================
# step
# ================================================================================================================


class _Run:
    """State of one repair run (timings, progress ranges, details)."""

    def __init__(self, ctx: TunerContext) -> None:
        self.ctx = ctx
        self.timings: dict[str, float] = {}
        self.details: dict[str, Any] = {}
        self.warnings: list[str] = []

    @contextmanager
    def stage(self, name: str, p0: float, p1: float, message: str) -> Iterator[ProgressRange]:
        self.ctx.progress(p0, message)
        t0 = time.perf_counter()
        try:
            yield (p0, p1)
        finally:
            self.timings[name] = round(self.timings.get(name, 0.0) + time.perf_counter() - t0, 3)
            self.ctx.memory()

    def sub(self, rng: ProgressRange):
        p0, p1 = rng
        return lambda f, m: self.ctx.progress(p0 + (p1 - p0) * f, m)

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        self.ctx.warn(message)


def run(ctx: TunerContext) -> StepOutcome:
    """Repair the primary mesh (see module doc)."""
    obj = ctx.require_primary()
    me = obj.data
    if len(me.polygons) == 0:
        return StepOutcome("skipped", "The mesh has no faces", {})
    opts = ctx.options
    r = _Run(ctx)
    t_start = time.perf_counter()
    diag = ctx.update_bounds()
    if diag <= 0:
        raise StepError("degenerate_mesh", "The mesh has zero size")
    faces_before = len(me.polygons)
    verts_before = len(me.vertices)

    # ---------------------------------------------------------------- 1. weld + arrays
    with r.stage("weld", 0.0, 0.03, "Welding duplicate vertices"):
        merged = _weld_if_needed(me, WELD_REL * diag)
        if merged:
            ctx.update_bounds()
        meshdata.clear_custom_normals(me)
    with r.stage("read", 0.03, 0.05, "Reading mesh"):
        arr = MeshArrays.read(me)
    same_as_original = _matches_original(ctx, arr)
    r.details["cleanup"] = {"welded_vertices": merged}

    # ---------------------------------------------------------------- 2. visibility
    tri_normals = arr.tri_normals()
    mean_edge = float(meshdata.edge_lengths(arr.co, arr.edges).mean()) if len(arr.edges) else 0.0
    use_gpu = ctx.gpu_available
    renderer = None
    try:
        if use_gpu:
            try:
                renderer = visibility.GpuIdRenderer(arr.co, arr.tris, visibility.auto_resolution(len(arr.tris)))
            except visibility.GpuUnavailable as exc:
                r.warn(f"GPU visibility unavailable ({exc}); using the slower CPU fallback")
                renderer = None
        with r.stage("visibility", 0.05, 0.35, "Computing visibility") as rng:
            vis = visibility.compute_visibility(arr.co, arr.tris, tri_normals, use_gpu=renderer is not None,
                                                renderer=renderer, mean_edge=mean_edge, progress=r.sub(rng))
        r.details["visibility"] = vis.summary()
        front = arr.to_faces(vis.front)
        back = arr.to_faces(vis.back)
        behind = arr.to_faces(vis.behind)
        partner = _faces_partner(arr, vis.partner)
        visible = (front.astype(np.int64) + back) > 0

        # ------------------------------------------------------------ 3-4. cleanup marks + inner shell
        delete = np.zeros(arr.n_faces, dtype=bool)
        with r.stage("cleanup", 0.35, 0.37, "Finding degenerate and duplicate faces"):
            degenerate = arr.areas <= DEGENERATE_REL_AREA * diag * diag
            dup_delete = _duplicate_faces(arr, front, back)
            delete |= dup_delete
            loose_verts = np.flatnonzero(np.bincount(arr.loop_verts, minlength=len(arr.co)) == 0)
            loose_edges = np.flatnonzero(arr.edge_face_count == 0)
            r.details["cleanup"].update({"degenerate_faces": int(degenerate.sum()),
                                         "duplicate_faces_removed": int(dup_delete.sum()),
                                         "loose_vertices": int(len(loose_verts)), "loose_edges": int(len(loose_edges))})
        inner = np.zeros(arr.n_faces, dtype=bool)
        junk = np.zeros(arr.n_faces, dtype=bool)
        shell_info: dict[str, Any] = {"enabled": bool(opts.remove_inner_shell), "detected": vis.is_shell,
                                      "thickness": vis.thickness, "t_max": vis.t_max}
        if opts.remove_inner_shell:
            with r.stage("inner_shell", 0.37, 0.50, "Classifying the inner shell") as rng:
                inner, junk, info = _classify(arr, vis, front, back, behind, partner, tri_normals, ~delete,
                                              r.sub(rng), shell=vis.is_shell)
                shell_info.update(info)
            if inner.any() or junk.any():
                with r.stage("safety", 0.50, 0.53, "Checking the visible surface"):
                    ok, check = _safety_check(arr, inner | junk | delete, renderer, tri_normals, vis.t_max)
                    shell_info["safety"] = check
                if not ok:
                    r.warn("Inner-shell removal was skipped because it would change the visible surface "
                           f"({check.get('reason')})")
                    shell_info["aborted"] = check.get("reason")
                    inner[:] = False
                    junk[:] = False
        r.details["inner_shell"] = shell_info
        delete |= inner | junk
        hidden_before = int((~visible).sum())

        # ------------------------------------------------------------ 5-6. islands + fins (on the kept faces)
        with r.stage("islands", 0.53, 0.57, "Removing floating parts"):
            island_del, island_info = (_islands(arr, ~delete, front, back, diag, opts.island_min_ratio)
                                       if opts.remove_islands else (np.zeros(arr.n_faces, bool), {"enabled": False}))
            delete |= island_del
            r.details["islands"] = island_info
        with r.stage("non_manifold", 0.57, 0.60, "Resolving non-manifold edges"):
            fin_del, fin_info = _fins(arr, ~delete)
            delete |= fin_del
            r.details["non_manifold"] = fin_info

        # mask over the ORIGINAL faces: seen from outside and meant to be kept (validation)
        keep_visible = visible & ~inner & ~junk & ~island_del
        if same_as_original:
            ctx.store["original_visible_face_mask"] = keep_visible.copy()
        elif ctx.original is not None and renderer is not None:
            ctx.store["original_visible_face_mask"] = _original_visible_mask(ctx)
    finally:
        if renderer is not None:
            renderer.free()

    if delete.mean() > MAX_DELETE_FRACTION:
        raise StepError("repair_unsafe", f"Repair would delete {100 * delete.mean():.0f}% of the faces - aborted")

    # ---------------------------------------------------------------- apply deletions (bmesh session 1)
    with r.stage("apply", 0.60, 0.70, "Removing faces"):
        _set_src_attribute(me)
        apply_info = _apply_deletions(me, delete, degenerate & ~delete, loose_verts, loose_edges, diag)
        r.details["apply"] = apply_info

    # ---------------------------------------------------------------- 7. orientation + 8. holes
    with r.stage("orientation", 0.70, 0.74, "Orienting normals"):
        arr2 = MeshArrays.read(me)
        src = meshdata.get_attribute(me, SRC_ATTRIBUTE)
        src = (np.asarray(src, np.int64) - 1) if src is not None else np.full(arr2.n_faces, -1, np.int64)
        flip, orient_info = _orientation(arr2, src, arr.face_normals, front, back)
        r.details["normals"] = orient_info
    with r.stage("holes", 0.74, 0.92, "Filling holes"):
        hole_info = _holes(me, arr2, flip, diag, opts)
        r.details["holes"] = hole_info
    with r.stage("shading", 0.92, 0.95, "Updating normals and shading"):
        meshdata.remove_attributes(me, SRC_ATTRIBUTE)
        meshdata.clear_custom_normals(me)
        _smooth_shading(me)

    # ---------------------------------------------------------------- final checks + metrics
    hidden_after = None
    if use_gpu and len(me.polygons):
        with r.stage("final_visibility", 0.95, 1.0, "Checking the result") as rng:
            hidden_after = _hidden_after(me, r.sub(rng))
    ctx.update_bounds()
    detected = int(shell_info.get("inner_faces", 0))
    removed = 0 if shell_info.get("aborted") or not opts.remove_inner_shell else detected
    ctx.set_metric("inner_shell_faces", before=detected, after=detected - removed)
    ctx.set_metric("hidden_faces", before=hidden_before, after=hidden_after)
    ctx.store["repair"] = {"inner_shell_faces_removed": removed, "hidden_faces_before": hidden_before,
                           "hidden_faces_after": hidden_after, "faces_before": faces_before,
                           "faces_after": len(me.polygons)}

    total_s = time.perf_counter() - t_start
    r.timings["total"] = round(total_s, 3)
    r.details.update({"faces_before": faces_before, "faces_after": len(me.polygons), "vertices_before": verts_before,
                      "vertices_after": len(me.vertices), "deleted_faces": int(delete.sum()),
                      "timings_s": r.timings, "memory_mb": ctx.peak_memory,
                      "visibility_method": vis.method})
    msg = _summary(r.details)
    status = "warning" if r.warnings else "done"
    ctx.progress(1.0, "Geometry repaired")
    return StepOutcome(status, msg, r.details)


# ================================================================================================================
# sub-steps
# ================================================================================================================


def _weld_if_needed(me: bpy.types.Mesh, dist: float) -> int:
    """Weld vertices closer than ``dist`` when a quantized-grid check finds any (cheap no-op otherwise)."""
    co = meshdata.get_positions(me)
    if len(co) < 2 or dist <= 0:
        return 0
    span = co.max(0) - co.min(0)
    k = np.maximum(np.ceil(span / dist).astype(np.int64) + 2, 1)
    if float(np.prod(k.astype(np.float64))) > 9e18:
        return meshdata.weld_by_distance(me, dist)
    found = False
    for offset in (0.0, 0.5):
        q = np.floor((co - co.min(0)) / dist + offset).astype(np.int64)
        key = (q[:, 0] * k[1] + q[:, 1]) * k[2] + q[:, 2]
        if len(np.unique(key)) < len(key):
            found = True
            break
    return meshdata.weld_by_distance(me, dist) if found else 0


def _matches_original(ctx: TunerContext, arr: MeshArrays) -> bool:
    """True when the mesh is still exactly the loaded original (face masks can be shared 1:1)."""
    if ctx.original is None:
        return False
    om = ctx.original.data
    if len(om.polygons) != arr.n_faces or len(om.vertices) != len(arr.co) or len(om.loops) != len(arr.loop_verts):
        return False
    return bool(np.array_equal(meshdata.get_loop_vertices(om), arr.loop_verts))


def _faces_partner(arr: MeshArrays, tri_partner: np.ndarray | None) -> np.ndarray:
    if tri_partner is None:
        return np.full(arr.n_faces, -1, np.int64)
    if arr.tri_poly is None:
        return tri_partner.astype(np.int64)
    out = np.full(arr.n_faces, -1, np.int64)
    has = tri_partner >= 0
    out[arr.tri_poly[has]] = arr.tri_poly[tri_partner[has]]
    out[out == np.arange(arr.n_faces)] = -1
    return out


def _duplicate_faces(arr: MeshArrays, front: np.ndarray, back: np.ndarray) -> np.ndarray:
    """Faces sharing their exact vertex set with another face: keep the one seen most from outside (front first);
    delete every copy when none of them is seen."""
    out = np.zeros(arr.n_faces, dtype=bool)
    n_verts = max(len(arr.co), 1)
    for size in (3, 4):
        faces = np.flatnonzero(arr.loop_total == size)
        if len(faces) < 2:
            continue
        idx = arr.loop_start[faces][:, None] + np.arange(size)[None, :]
        vs = np.sort(arr.loop_verts[idx], axis=1)
        if size == 3 and n_verts < 2 ** 21:
            key = (vs[:, 0] * n_verts + vs[:, 1]) * n_verts + vs[:, 2]
            _, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
        else:
            _, inv, cnt = np.unique(vs, axis=0, return_inverse=True, return_counts=True)
        inv = inv.reshape(-1)
        dup = cnt[inv] > 1
        if not dup.any():
            continue
        fd = faces[dup]
        gd = inv[dup]
        score = front[fd].astype(np.float64) * 2.0 + back[fd] * 0.5
        order = np.lexsort((-score, gd))
        fd, gd, score = fd[order], gd[order], score[order]
        first = np.ones(len(fd), dtype=bool)
        first[1:] = gd[1:] != gd[:-1]
        best_score = np.maximum.reduceat(score, np.flatnonzero(first))
        group_best = np.repeat(best_score, np.diff(np.append(np.flatnonzero(first), len(fd))))
        out[fd[~first]] = True
        out[fd[first & (group_best <= 0)]] = True
    return out


def _classify(arr: MeshArrays, vis: visibility.VisibilityResult, front, back, behind, partner, tri_normals,
              alive: np.ndarray, progress, *, shell: bool) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Inner-shell + hidden-junk masks (see module doc, step 4)."""
    info: dict[str, Any] = {}
    seed = shell_seeds(front, back, behind, shell=shell)
    seed[~alive] = 0
    partner = partner.copy()
    if shell:
        need = np.flatnonzero((seed == 0) & (partner < 0) & alive)
        t0 = time.perf_counter()
        progress(0.05, f"Pairing {len(need):,} hidden walls")
        if arr.tri_poly is None:
            p2, _ = visibility.pair_by_rays(arr.co, arr.tris, tri_normals, need, vis.t_max * 1.5,
                                            budget_s=PAIR_RAY_BUDGET_S)
        else:
            tri_of = _first_tri_of_face(arr)
            p2t, _ = visibility.pair_by_rays(arr.co, arr.tris, tri_normals, tri_of[need], vis.t_max * 1.5,
                                             budget_s=PAIR_RAY_BUDGET_S)
            p2 = np.where(p2t >= 0, arr.tri_poly[np.maximum(p2t, 0)], -1)
        partner[need] = np.where(p2 >= 0, p2, partner[need])
        info["ray_paired"] = int((p2 >= 0).sum())
        info["ray_pairing_s"] = round(time.perf_counter() - t0, 3)
    progress(0.6, "Propagating wall labels")
    f1, f2 = arr.manifold_face_pairs(alive)
    partner[~alive] = -1
    partner[(partner >= 0) & ~alive[np.maximum(partner, 0)]] = -1
    plan = classify_shell(front, back, behind, partner, f1, f2, shell=shell, seed=seed)
    label = plan.label.copy()
    label[~alive] = 1
    inner = (label < 0) & alive
    junk = (label == 0) & alive if shell else np.zeros(arr.n_faces, dtype=bool)
    info.update(plan.stats)
    info["inner_faces"] = int(inner.sum())
    info["hidden_junk_faces"] = int(junk.sum())
    if not shell:
        info["note"] = "no double-walled shell detected: only hidden junk parts are handled by the island rule"
    progress(1.0, "Inner shell classified")
    return inner, junk, info


def _first_tri_of_face(arr: MeshArrays) -> np.ndarray:
    out = np.zeros(arr.n_faces, np.int64)
    out[arr.tri_poly[::-1]] = np.arange(len(arr.tri_poly) - 1, -1, -1)
    return out


def _safety_check(arr: MeshArrays, delete: np.ndarray, renderer, tri_normals: np.ndarray,
                  t_max: float) -> tuple[bool, dict[str, Any]]:
    """Deletion-fraction band + (GPU) re-render of the kept faces vs all faces."""
    frac = float(delete.mean())
    check: dict[str, Any] = {"delete_fraction": round(frac, 4)}
    if frac > MAX_DELETE_FRACTION:
        check["reason"] = f"{100 * frac:.0f}% of the faces would be removed"
        return False, check
    if renderer is None:
        check["render_check"] = "skipped (no GPU)"
        return True, check
    keep_tri = ~delete if arr.tri_poly is None else ~delete[arr.tri_poly]
    tol = SAFETY_TOLERANCE_TMAX * t_max
    res = visibility.removal_check(renderer, keep_tri, tri_normals, tol)
    check.update({k: (round(v, 6) if isinstance(v, float) else v) for k, v in res.items()})
    if res["removed_visible"] > SAFETY_REMOVED_VISIBLE:
        check["reason"] = f"{100 * res['removed_visible']:.1f}% of the visible surface would be removed"
    elif res["silhouette_lost"] > SAFETY_SILHOUETTE_LOST:
        check["reason"] = f"{100 * res['silhouette_lost']:.2f}% of the silhouette would be lost"
    elif res["receded"] > SAFETY_RECEDED:
        check["reason"] = f"{100 * res['receded']:.1f}% of the visible surface would move back"
    return "reason" not in check, check


def _islands(arr: MeshArrays, alive: np.ndarray, front, back, diag: float, ratio: float
             ) -> tuple[np.ndarray, dict[str, Any]]:
    """Small parts that are hidden or tiny (never "keep the largest only")."""
    out = np.zeros(arr.n_faces, dtype=bool)
    faces = np.flatnonzero(alive)
    if len(faces) == 0:
        return out, {"components": 0}
    loops = np.flatnonzero(alive[arr.loop_face])
    used_edges = np.unique(arr.loop_edges[loops])
    ev = arr.edges[used_edges]
    vlab = meshdata.connected_components(len(arr.co), ev[:, 0], ev[:, 1])
    flab = vlab[arr.loop_verts[arr.loop_start[faces]]]
    uniq, comp = np.unique(flab, return_inverse=True)
    comp = comp.reshape(-1)
    k = len(uniq)
    sizes = np.bincount(comp, minlength=k)
    area = arr.areas[faces]
    seen = ((front[faces].astype(np.int64) + back[faces]) >= 1).astype(np.float64)
    comp_area = np.bincount(comp, weights=area, minlength=k)
    seen_area = np.bincount(comp, weights=area * seen, minlength=k)
    hidden = seen_area <= ISLAND_HIDDEN_AREA * np.maximum(comp_area, 1e-300)
    # bbox diagonal per component
    vco = arr.co[arr.loop_verts[arr.loop_start[faces]]]
    mins = np.full((k, 3), np.inf)
    maxs = np.full((k, 3), -np.inf)
    for c in range(3):
        np.minimum.at(mins[:, c], comp, vco[:, c])
        np.maximum.at(maxs[:, c], comp, vco[:, c])
    extent = np.linalg.norm(maxs - mins, axis=1)
    small = sizes < ratio * len(faces)
    tiny = (extent < ISLAND_TINY_REL * diag) | (sizes < ISLAND_TINY_FACES)
    remove = small & (hidden | tiny)
    out[faces[remove[comp]]] = True
    largest = np.sort(sizes)[::-1]
    return out, {"components": int(k), "small_components": int(small.sum()), "removed_components": int(remove.sum()),
                 "removed_hidden": int((remove & hidden).sum()), "removed_tiny": int((remove & tiny & ~hidden).sum()),
                 "removed_faces": int(out.sum()), "kept_small_visible": int((small & ~remove).sum()),
                 "largest_components_faces": largest[:5].tolist(), "min_faces": int(math.ceil(ratio * len(faces)))}


def _fins(arr: MeshArrays, alive: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """Remove small manifold patches hanging on edges with more than two faces (beyond the two largest)."""
    out = np.zeros(arr.n_faces, dtype=bool)
    info: dict[str, Any] = {}
    cur = alive.copy()
    for rnd in range(FIN_ROUNDS):
        loops = np.flatnonzero(cur[arr.loop_face])
        le = arr.loop_edges[loops]
        cnt = np.bincount(le, minlength=len(arr.edges))
        nm_edges = np.flatnonzero(cnt > 2)
        if rnd == 0:
            info["non_manifold_edges_before"] = int(len(nm_edges))
        if len(nm_edges) == 0:
            break
        f1, f2 = arr.manifold_face_pairs(cur)
        patch = meshdata.connected_components(arr.n_faces, f1, f2)
        psize = np.bincount(patch, minlength=int(patch.max()) + 1)
        # incident faces of non-manifold edges
        sel = loops[cnt[le] > 2]
        order = np.argsort(arr.loop_edges[sel], kind="stable")
        sel = sel[order]
        e_of = arr.loop_edges[sel]
        f_of = arr.loop_face[sel]
        size_of = psize[patch[f_of]]
        # rank incident faces per edge by their patch size (descending); ranks >= 2 are fin candidates
        o2 = np.lexsort((-size_of, e_of))
        e_s, f_s, s_s = e_of[o2], f_of[o2], size_of[o2]
        first = np.r_[True, e_s[1:] != e_s[:-1]]
        start_idx = np.maximum.accumulate(np.where(first, np.arange(len(e_s)), 0))
        rank = np.arange(len(e_s)) - start_idx
        cand = (rank >= 2) & (s_s <= FIN_MAX_FACES)
        if not cand.any():
            break
        kill_patches = np.unique(patch[f_s[cand]])
        kill = np.isin(patch, kill_patches) & cur
        out |= kill
        cur &= ~kill
    loops = np.flatnonzero(cur[arr.loop_face])
    cnt = np.bincount(arr.loop_edges[loops], minlength=len(arr.edges))
    info["removed_faces"] = int(out.sum())
    info["non_manifold_edges_after"] = int((cnt > 2).sum())
    return out, info


def _set_src_attribute(me: bpy.types.Mesh) -> None:
    meshdata.set_attribute(me, SRC_ATTRIBUTE, np.arange(1, len(me.polygons) + 1, dtype=np.int32), domain="FACE",
                           data_type="INT")


def _apply_deletions(me: bpy.types.Mesh, delete: np.ndarray, dissolve: np.ndarray, loose_verts: np.ndarray,
                     loose_edges: np.ndarray, diag: float) -> dict[str, Any]:
    """bmesh session: delete faces (+ their now-unused edges/verts), loose geometry, dissolve degenerate faces,
    make the winding consistent per manifold region (``recalc_face_normals``)."""
    info: dict[str, Any] = {}
    bm = bmesh.new()
    try:
        t0 = time.perf_counter()
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        info["from_mesh_s"] = round(time.perf_counter() - t0, 3)
        t0 = time.perf_counter()
        dissolve_edges = {e for i in np.flatnonzero(dissolve).tolist() for e in bm.faces[i].edges}
        lv = [bm.verts[i] for i in loose_verts.tolist()]
        lE = [bm.edges[i] for i in loose_edges.tolist()]
        kill = [bm.faces[i] for i in np.flatnonzero(delete).tolist()]
        if kill:
            bmesh.ops.delete(bm, geom=kill, context="FACES")
        lE = [e for e in lE if e.is_valid]
        if lE:
            bmesh.ops.delete(bm, geom=lE, context="EDGES")
        lv = [v for v in lv if v.is_valid]
        if lv:
            bmesh.ops.delete(bm, geom=lv, context="VERTS")
        info["delete_s"] = round(time.perf_counter() - t0, 3)
        dissolve_edges = [e for e in dissolve_edges if e.is_valid]
        if dissolve_edges:
            t0 = time.perf_counter()
            before = len(bm.faces)
            bmesh.ops.dissolve_degenerate(bm, dist=DISSOLVE_REL_DIST * diag, edges=dissolve_edges)
            info["dissolve_degenerate_removed_faces"] = before - len(bm.faces)
            info["dissolve_s"] = round(time.perf_counter() - t0, 3)
        t0 = time.perf_counter()
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        info["recalc_normals_s"] = round(time.perf_counter() - t0, 3)
        t0 = time.perf_counter()
        bm.to_mesh(me)
        info["to_mesh_s"] = round(time.perf_counter() - t0, 3)
    finally:
        bm.free()
    me.update()
    return info


def _orientation(arr: MeshArrays, src: np.ndarray, normals_orig: np.ndarray, front: np.ndarray, back: np.ndarray
                 ) -> tuple[np.ndarray, dict[str, Any]]:
    """Faces to reverse so that every manifold region faces the side it is seen from."""
    n = arr.n_faces
    flip = np.zeros(n, dtype=bool)
    known = src >= 0
    s = np.where(known, src, 0)
    turned = known & ((arr.face_normals * normals_orig[s]).sum(1) < 0)
    f_eff = np.where(turned, back[s], front[s]).astype(np.float64)
    k_eff = np.where(turned, front[s], back[s]).astype(np.float64)
    f_eff[~known] = 0
    k_eff[~known] = 0
    f1, f2 = arr.manifold_face_pairs()
    region = meshdata.connected_components(n, f1, f2)
    k = int(region.max()) + 1 if n else 0
    score = np.bincount(region, weights=arr.areas * (f_eff - k_eff), minlength=k)
    seen = np.bincount(region, weights=arr.areas * (f_eff + k_eff), minlength=k)
    flip_region = (score < 0) & (np.abs(score) > 0.1 * seen)
    flip = flip_region[region]
    info = {"regions": int(k), "recalc_turned_faces": int(turned.sum()), "flipped_regions": int(flip_region.sum()),
            "flipped_faces": int(flip.sum())}
    return flip, info


def _holes(me: bpy.types.Mesh, arr: MeshArrays, flip: np.ndarray, diag: float, opts) -> dict[str, Any]:
    """Plan + fill holes and apply region flips (bmesh session 2)."""
    t0 = time.perf_counter()
    cycles, cstats = holes.boundary_cycles(arr.co, arr.loop_verts, arr.loop_face, arr.nxt, arr.loop_edges,
                                           arr.edge_face_count, flip)
    uv = meshdata.get_uvs(me)
    islands = holes.uv_islands(uv, arr.loop_verts, arr.loop_face, arr.nxt, arr.loop_edges, arr.edge_face_count,
                               arr.n_faces)
    max_edges = int(opts.max_hole_edges) if opts.fill_holes else 0
    plans = holes.plan_holes(cycles, co=arr.co, face_normals=arr.face_normals, flipped=flip, uv=uv, islands=islands,
                             loop_verts=arr.loop_verts, nxt=arr.nxt, materials=meshdata.get_material_indices(me),
                             max_edges=max_edges, max_perimeter=holes.MAX_PERIMETER_REL * diag)
    if not opts.fill_holes:
        for p in plans:
            p.fill = False
            p.reason = "hole filling disabled"
    plan_s = time.perf_counter() - t0
    info: dict[str, Any] = {"cycles": len(cycles), **cstats, "plan_s": round(plan_s, 3)}
    if not flip.any() and not any(p.fill for p in plans):
        report = holes.FillReport()
        for p in plans:
            report.skipped.append({"edges": p.cycle.edges, "reason": p.reason,
                                   "perimeter_pct_diag": round(100.0 * p.cycle.perimeter / diag, 4)})
        info.update(report.summary())
        return info
    bm = bmesh.new()
    try:
        t1 = time.perf_counter()
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
        if flip.any():
            bmesh.ops.reverse_faces(bm, faces=[bm.faces[i] for i in np.flatnonzero(flip).tolist()])
        uv_name = me.uv_layers.active.name if me.uv_layers.active is not None else None
        report = holes.fill_holes(bm, plans, diag=diag, uv_layer_name=uv_name)
        bm.to_mesh(me)
        info["bmesh_s"] = round(time.perf_counter() - t1, 3)
    finally:
        bm.free()
    me.update()
    info.update(report.summary())
    return info


def _smooth_shading(me: bpy.types.Mesh) -> None:
    """Smooth shading with sharp edges above ``SHARP_ANGLE_DEG`` (Blender 4.1+ API, best effort)."""
    try:
        me.shade_smooth()
    except (AttributeError, RuntimeError):
        try:
            me.polygons.foreach_set("use_smooth", np.ones(len(me.polygons), dtype=bool))
        except (AttributeError, RuntimeError, TypeError) as exc:
            log.debug("could not set smooth shading: %s", exc)
    fn = getattr(me, "set_sharp_from_angle", None)
    if fn is not None:
        try:
            fn(angle=math.radians(SHARP_ANGLE_DEG))
        except (TypeError, RuntimeError) as exc:
            log.debug("set_sharp_from_angle failed: %s", exc)
    me.update()


def _gpu_seen(me: bpy.types.Mesh, progress=None) -> np.ndarray | None:
    """Per-face "seen from outside" (first-hit GPU renders, ``FINAL_VIEWS`` directions); ``None`` without GPU."""
    arr = MeshArrays.read(me)
    if arr.n_faces == 0:
        return np.zeros(0, dtype=bool)
    try:
        with visibility.GpuIdRenderer(arr.co, arr.tris, visibility.auto_resolution(len(arr.tris))) as renderer:
            res = visibility.compute_visibility(arr.co, arr.tris, arr.tri_normals(), views=FINAL_VIEWS,
                                                renderer=renderer, pairs=False, progress=progress)
    except visibility.GpuUnavailable as exc:
        log.debug("GPU visibility unavailable: %s", exc)
        return None
    return (arr.to_faces(res.front) + arr.to_faces(res.back)) > 0


def _hidden_after(me: bpy.types.Mesh, progress) -> int | None:
    """Faces of the result never seen from outside (metric; ``None`` without GPU)."""
    seen = _gpu_seen(me, progress)
    return None if seen is None else int((~seen).sum())


def _original_visible_mask(ctx: TunerContext) -> np.ndarray | None:
    """Faces of ``ctx.original`` seen from outside (used when the mesh no longer matches it at repair time)."""
    return _gpu_seen(ctx.original.data)


def _summary(d: dict[str, Any]) -> str:
    parts = []
    shell = d.get("inner_shell", {})
    if shell.get("inner_faces") and not shell.get("aborted"):
        parts.append(f"removed {shell['inner_faces']:,} inner-shell faces")
    junk = shell.get("hidden_junk_faces", 0) + d.get("islands", {}).get("removed_faces", 0)
    if junk:
        parts.append(f"{junk:,} hidden/floating faces")
    h = d.get("holes", {})
    if h.get("filled"):
        parts.append(f"filled {h['filled']} holes")
    n = d.get("normals", {})
    if n.get("flipped_regions"):
        parts.append(f"flipped {n['flipped_regions']} regions")
    parts.append(f"{d['faces_before']:,} -> {d['faces_after']:,} faces")
    return "; ".join(parts)
