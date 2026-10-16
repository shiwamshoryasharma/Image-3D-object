"""The ``rig_optimization`` step: skin-weight cleanup + pose deformation checks (``ctx.store["pose_checks"]``).

Runs only for meshes skinned to an armature; TRELLIS output is unrigged -> ``skipped``.

Deform groups are the vertex groups named after a deform bone of the mesh's armature(s); every other vertex group
("non-deform": masks, paint layers, groups of non-deform bones) is never modified. Per skinned mesh:

1. **prune** non-finite / non-positive / tiny deform weights (< ``min_weight``, default 0.001). Weights on the glTF
   exporter's ``neutral_bone`` (a parentless, childless placeholder joint Blender's exporter adds for unweighted
   vertices) count as "no weight".
2. **outliers**: an influence (>= 0.05) that no 1-ring neighbour shares, on a bone much farther from the vertex than
   the bones its neighbours use (``d_bone > 2 d_ref + 0.25 len_bone``), is removed. A vertex that the "before" pose
   checks show popping away from ALL its neighbours (> 4 local edge lengths) and whose weights differ from its
   neighbours' mean (L1 > 0.5) is re-derived from its neighbours. Influences carrying more than half of a vertex's
   weight clear the whole vertex (it is re-filled in step 4). Weight bleeding is dropped: small influences (< 0.1)
   on bones far from the vertex compared with its dominant bone (``d_bone > 2 d_dominant + 0.5 len_bone``) that sit
   in a detached, weak (peak < 0.25) island of that bone's influence region.
3. **limit** to the ``max_influences`` (4, glTF) strongest influences, **normalize** to 1, re-prune.
4. **fill** unweighted vertices from their nearest weighted neighbours (multi-source graph propagation over mesh
   edges, inverse-edge-length averaging); parts without any weighted vertex are attached rigidly to the nearest
   weighted vertex (KD-tree).
5. light Laplacian **smoothing** of the weights of filled/repaired vertices only (all other vertices fixed).
6. **empty groups** that are not deform-bone groups and not referenced by a modifier / shape key / particle system /
   constraint are removed - last, after everything else succeeded (keeps the pipeline rollback consistent).

Pose checks (rest vs posed, depsgraph-evaluated mesh): every pose channel is set to identity (bone constraints
muted, non-armature modifiers hidden), then each major deform bone (<= 40, by weighted-vertex count, capped by a
vertex budget for huge meshes) is rotated +-45 deg about its local X and Z. Per rotation, over the deformed region:
edge length ratios (max stretch, edges > 2x / < 0.5x; rest lengths floored at 0.25 x the median edge so slivers do
not dominate), collapsed triangles (area ratio < 0.05; triangles below 5 % of the median area ignored) and a
per-vertex spike score. Run with the original weights ("before") and
the cleaned weights ("after", same bones); the pose (channels, constraint mutes, pose_position, modifier visibility)
is restored exactly afterwards and verified by re-evaluating the mesh. Results: ``ctx.store["pose_checks"]``
(``rig_weights``, ``pose_stretch``, ``pose_collapse``) and the ``pose_max_stretch`` metric (worst edge elongation
in %, 2x = 100 %).

Settings: :class:`RigSettings` (module defaults; optional ``TuningOptions`` fields ``rig_min_weight`` /
``rig_max_influences`` / ``rig_smooth_iterations`` / ``rig_pose_bones`` / ``rig_pose_checks`` override them).
Python 3.11 syntax, numpy >= 1.24, bpy/mathutils only.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import bpy
import numpy as np
from mathutils import Matrix, kdtree

from . import meshdata
from .context import StepError, StepOutcome, TunerContext

log = logging.getLogger(__name__)

SKIP_NO_RIG = "No rig present (TRELLIS output is unrigged)"
NEUTRAL_BONE_NAMES = frozenset({"neutral_bone"})
"""Joint names Blender's glTF exporter creates for vertices without weights (rest-pose placeholder)."""
WEIGHT_TOLERANCE = 1e-3
"""|sum - 1| above this = unnormalized (same as ``analysis.WEIGHT_TOLERANCE``)."""
WRITE_TOLERANCE = 1e-6
OUTLIER_MIN_WEIGHT = 0.05
OUTLIER_DISTANCE_FACTOR = 2.0
OUTLIER_BONE_LENGTH_FACTOR = 0.25
OUTLIER_CLEAR_FRACTION = 0.5
STRAY_MAX_WEIGHT = 0.1
STRAY_DISTANCE_FACTOR = 2.0
STRAY_BONE_LENGTH_FACTOR = 0.5
STRAY_ISLAND_MAX = 0.25
SPIKE_EDGE_LENGTHS = 4.0
SPIKE_WEIGHT_L1 = 0.5
STRETCH_RATIO = 2.0
COMPRESS_RATIO = 0.5
COLLAPSE_AREA_RATIO = 0.05
POSE_STRETCH_WARN = 2.5
POSE_STRETCH_FAIL = 5.0
POSE_BAD_EDGE_PCT_WARN = 1.0
POSE_COLLAPSE_PCT_FAIL = 1.0
POSE_AXES = ("X", "Z")
POSE_VERTEX_BUDGET = 40_000_000
"""Max (vertices x evaluations) per pose pass; caps the bone count on very dense meshes."""
POSE_MIN_BONES = 4
ADJ_CHUNK_VERTS = 200_000


# ================================================================================================================
# settings
# ================================================================================================================


@dataclass
class RigSettings:
    """Tunables of the rig step (defaults = DESIGN behaviour)."""

    max_influences: int = 4
    min_weight: float = 1e-3
    fix_outliers: bool = True
    smooth_iterations: int = 3
    smooth_factor: float = 0.5
    pose_checks: bool = True
    pose_angle_deg: float = 45.0
    pose_max_bones: int = 40

    @classmethod
    def from_options(cls, options: Any) -> RigSettings:
        """Defaults overridden by optional ``TuningOptions`` fields ``rig_*`` (ignored when absent/None)."""
        s = cls()
        mapping = {"rig_max_influences": ("max_influences", int, 1, 8),
                   "rig_min_weight": ("min_weight", float, 0.0, 0.1),
                   "rig_smooth_iterations": ("smooth_iterations", int, 0, 20),
                   "rig_pose_bones": ("pose_max_bones", int, 0, 200)}
        for opt, (attr, kind, lo, hi) in mapping.items():
            value = getattr(options, opt, None)
            if value is None or isinstance(value, bool):
                continue
            try:
                setattr(s, attr, min(max(kind(value), kind(lo)), kind(hi)))
            except (TypeError, ValueError):
                log.warning("Ignoring invalid option %s=%r", opt, value)
        pose = getattr(options, "rig_pose_checks", None)
        if isinstance(pose, bool):
            s.pose_checks = pose
        return s


# ================================================================================================================
# padded per-vertex weights
# ================================================================================================================


def _rank_within(sorted_ids: np.ndarray) -> np.ndarray:
    """Position of each element inside its run of equal ids (``sorted_ids`` grouped/sorted)."""
    n = len(sorted_ids)
    if n == 0:
        return np.zeros(0, np.int64)
    start = np.empty(n, dtype=bool)
    start[0] = True
    np.not_equal(sorted_ids[1:], sorted_ids[:-1], out=start[1:])
    idx = np.arange(n, dtype=np.int64)
    return idx - np.maximum.accumulate(np.where(start, idx, 0))


class PaddedWeights:
    """Per-vertex deform weights as ``group (V, K) int64`` (-1 = empty) + ``weight (V, K) float64`` (0 = empty).

    Invariant: ``group >= 0`` iff ``weight > 0``; rows are sorted by descending weight (empty slots last).
    """

    __slots__ = ("group", "weight")

    def __init__(self, group: np.ndarray, weight: np.ndarray) -> None:
        self.group = group
        self.weight = weight

    @classmethod
    def from_entries(cls, n_verts: int, vert: np.ndarray, group: np.ndarray, weight: np.ndarray,
                     width: int | None = None) -> PaddedWeights:
        """Build from ``(vertex, group, weight)`` entries; non-finite / non-positive weights are dropped."""
        vert = np.asarray(vert, np.int64)
        group = np.asarray(group, np.int64)
        weight = np.asarray(weight, np.float64)
        ok = np.isfinite(weight) & (weight > 0.0)
        vert, group, weight = vert[ok], group[ok], weight[ok]
        order = np.lexsort((-weight, vert))
        vert, group, weight = vert[order], group[order], weight[order]
        if width is None:
            width = int(np.bincount(vert, minlength=n_verts).max()) if len(vert) else 0
        width = max(int(width), 1)
        rank = _rank_within(vert)
        keep = rank < width
        g = np.full((n_verts, width), -1, np.int64)
        w = np.zeros((n_verts, width), np.float64)
        g[vert[keep], rank[keep]] = group[keep]
        w[vert[keep], rank[keep]] = weight[keep]
        return cls(g, w)

    def copy(self) -> PaddedWeights:
        return PaddedWeights(self.group.copy(), self.weight.copy())

    @property
    def n_verts(self) -> int:
        return self.group.shape[0]

    @property
    def width(self) -> int:
        return self.group.shape[1]

    def entries(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """``(vertex, group, weight)`` of every non-empty slot (vertex-major order)."""
        rows, cols = np.nonzero(self.group >= 0)
        return rows.astype(np.int64), self.group[rows, cols], self.weight[rows, cols]

    def counts(self) -> np.ndarray:
        return (self.group >= 0).sum(1)

    def sums(self) -> np.ndarray:
        return self.weight.sum(1)

    def sort_rows(self, rows: np.ndarray | None = None) -> None:
        """Re-establish the descending-weight row order (all rows or ``rows``)."""
        if rows is None:
            order = np.argsort(-self.weight, axis=1, kind="stable")
            self.weight = np.take_along_axis(self.weight, order, 1)
            self.group = np.take_along_axis(self.group, order, 1)
            return
        order = np.argsort(-self.weight[rows], axis=1, kind="stable")
        self.weight[rows] = np.take_along_axis(self.weight[rows], order, 1)
        self.group[rows] = np.take_along_axis(self.group[rows], order, 1)

    def prune(self, min_weight: float, rows: np.ndarray | None = None) -> int:
        """Empty every slot with a non-finite weight or a weight below ``min_weight``; returns the count."""
        if rows is None:
            g, w = self.group, self.weight
        else:
            g, w = self.group[rows], self.weight[rows]
        bad = (g >= 0) & ~(np.isfinite(w) & (w >= min_weight) & (w > 0.0))
        removed = int(bad.sum())
        if removed:
            g[bad] = -1
            w[bad] = 0.0
            if rows is not None:
                self.group[rows], self.weight[rows] = g, w
            self.sort_rows(rows)
        return removed

    def limit(self, k: int) -> np.ndarray:
        """Keep the ``k`` strongest influences per vertex (width becomes ``k``); returns the mask of vertices
        that had more."""
        self.sort_rows()
        over = self.counts() > k
        if self.width > k:
            self.group = np.ascontiguousarray(self.group[:, :k])
            self.weight = np.ascontiguousarray(self.weight[:, :k])
        elif self.width < k:
            pad = k - self.width
            self.group = np.pad(self.group, ((0, 0), (0, pad)), constant_values=-1)
            self.weight = np.pad(self.weight, ((0, 0), (0, pad)), constant_values=0.0)
        return over

    def normalize(self, rows: np.ndarray | None = None) -> np.ndarray:
        """Scale rows to sum 1 (empty rows unchanged); returns the mask (over ``rows``) of rows that changed."""
        w = self.weight if rows is None else self.weight[rows]
        s = w.sum(1)
        changed = (s > 0) & (np.abs(s - 1.0) > 1e-12)
        if changed.any():
            w[changed] /= s[changed, None]
            if rows is not None:
                self.weight[rows] = w
        return changed

    def clear_rows(self, rows: np.ndarray) -> None:
        self.group[rows] = -1
        self.weight[rows] = 0.0

    def set_rows(self, rows: np.ndarray, group: np.ndarray, weight: np.ndarray) -> None:
        """Replace whole rows (``group``/``weight`` padded or truncated to the current width)."""
        k = self.width
        if group.shape[1] < k:
            pad = k - group.shape[1]
            group = np.pad(group, ((0, 0), (0, pad)), constant_values=-1)
            weight = np.pad(weight, ((0, 0), (0, pad)), constant_values=0.0)
        self.group[rows] = group[:, :k]
        self.weight[rows] = weight[:, :k]


def accumulate_rows(rows: np.ndarray, groups: np.ndarray, weights: np.ndarray, n_groups: int,
                    width: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sum weighted contributions per (row, group) and keep the ``width`` strongest per row, normalized.

    ``rows`` (P,), ``groups``/``weights`` (P, K'): row ``rows[p]`` receives ``weights[p, j]`` on ``groups[p, j]``.
    Returns ``(unique_rows (U,), group (U, width), weight (U, width))``.
    """
    kk = groups.shape[1]
    r = np.repeat(np.asarray(rows, np.int64), kk)
    g = groups.reshape(-1)
    w = weights.reshape(-1)
    ok = (g >= 0) & (w > 0.0) & np.isfinite(w)
    if not ok.any():
        return np.zeros(0, np.int64), np.full((0, width), -1, np.int64), np.zeros((0, width))
    key = r[ok] * n_groups + g[ok]
    uniq, inv = np.unique(key, return_inverse=True)
    tot = np.bincount(inv.reshape(-1), weights=w[ok], minlength=len(uniq))
    urow = uniq // n_groups
    ugrp = uniq % n_groups
    order = np.lexsort((-tot, urow))
    urow, ugrp, tot = urow[order], ugrp[order], tot[order]
    rank = _rank_within(urow)
    keep = rank < width
    out_rows = np.unique(urow)
    pos = np.searchsorted(out_rows, urow[keep])
    og = np.full((len(out_rows), width), -1, np.int64)
    ow = np.zeros((len(out_rows), width))
    og[pos, rank[keep]] = ugrp[keep]
    ow[pos, rank[keep]] = tot[keep]
    s = ow.sum(1)
    ow[s > 0] /= s[s > 0, None]
    return out_rows, og, ow


def weight_stats(pw: PaddedWeights, max_influences: int, min_weight: float) -> dict[str, Any]:
    """Influence statistics of deform weights (``unnormalized`` uses ``WEIGHT_TOLERANCE`` like the analysis)."""
    counts = pw.counts()
    sums = pw.sums()
    weighted = counts > 0
    filled = pw.group >= 0
    return {"max_influences": int(counts.max()) if len(counts) else 0,
            "over_limit_verts": int((counts > max_influences).sum()),
            "unnormalized_verts": int((weighted & (np.abs(sums - 1.0) > WEIGHT_TOLERANCE)).sum()),
            "unweighted_verts": int((~weighted).sum()),
            "tiny_weights": int((filled & (pw.weight < min_weight)).sum()),
            "min_weight": round(float(pw.weight[filled].min()), 8) if filled.any() else None,
            "max_sum_error": round(float(np.abs(sums[weighted] - 1.0).max()), 6) if weighted.any() else None}


# ================================================================================================================
# graph-based repairs
# ================================================================================================================


@dataclass
class MeshGraph:
    """Vertex adjacency of a mesh (CSR, both directions) with rest edge lengths."""

    n_verts: int
    edges: np.ndarray       # (E, 2) without self-loops
    indptr: np.ndarray      # (V+1,)
    src: np.ndarray         # (2E,) row of each CSR entry (sorted)
    dst: np.ndarray         # (2E,) neighbour
    length: np.ndarray      # (2E,) rest length of the entry's edge

    @classmethod
    def build(cls, co: np.ndarray, edges: np.ndarray) -> MeshGraph:
        n = len(co)
        edges = edges.reshape(-1, 2)
        edges = edges[edges[:, 0] != edges[:, 1]]
        indptr, dst = meshdata.vertex_adjacency(n, edges)
        src = np.repeat(np.arange(n, dtype=np.int64), np.diff(indptr))
        length = np.linalg.norm(co[src] - co[dst], axis=1) if len(src) else np.zeros(0)
        return cls(n, edges, indptr, src, dst, length)

    def inverse_length(self) -> np.ndarray:
        pos = self.length[self.length > 0]
        eps = float(pos.mean()) * 1e-3 if len(pos) else 1e-9
        return 1.0 / np.maximum(self.length, eps)


def _segment_distance(p: np.ndarray, head: np.ndarray, tail: np.ndarray) -> np.ndarray:
    """Distance from points ``p`` (..., 3) to segments ``head``-``tail`` (..., 3)."""
    ht = tail - head
    l2 = np.maximum((ht * ht).sum(-1), 1e-30)
    s = np.clip(((p - head) * ht).sum(-1) / l2, 0.0, 1.0)
    return np.linalg.norm(p - (head + s[..., None] * ht), axis=-1)


def isolated_far_influences(pw: PaddedWeights, graph: MeshGraph, co: np.ndarray, heads: np.ndarray,
                            tails: np.ndarray, bone_ok: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Influences no 1-ring neighbour shares, on a bone far from the vertex (see module doc).

    Returns ``(vertex, slot)`` index arrays into ``pw``.
    """
    n, k = pw.group.shape
    supported = np.zeros((n, k), dtype=bool)
    has_weighted_nbr = np.zeros(n, dtype=bool)
    weighted = pw.group[:, 0] >= 0
    for a in range(0, n, ADJ_CHUNK_VERTS):
        b = min(n, a + ADJ_CHUNK_VERTS)
        p0, p1 = int(graph.indptr[a]), int(graph.indptr[b])
        if p1 == p0:
            continue
        u = graph.src[p0:p1]
        nb = graph.dst[p0:p1]
        gu = pw.group[u]
        gn = pw.group[nb]
        match = np.zeros(gu.shape, dtype=bool)
        for j in range(k):
            col = gn[:, j:j + 1]
            match |= (gu == col) & (col >= 0)
        starts = graph.indptr[a:b] - p0
        nonempty = np.diff(graph.indptr[a:b + 1]) > 0
        rows = np.arange(a, b)[nonempty]
        supported[rows] = np.add.reduceat(match.astype(np.int32), starts[nonempty], axis=0) > 0
        has_weighted_nbr[rows] = np.add.reduceat(weighted[nb].astype(np.int32), starts[nonempty]) > 0
    cand = (pw.group >= 0) & (pw.weight >= OUTLIER_MIN_WEIGHT) & ~supported & has_weighted_nbr[:, None]
    cv, cs = np.nonzero(cand)
    cv, cs = cv.astype(np.int64), cs.astype(np.int64)
    if len(cv) == 0:
        return cv, cs
    cg = pw.group[cv, cs]
    valid = bone_ok[cg]
    cv, cs, cg = cv[valid], cs[valid], cg[valid]
    if len(cv) == 0:
        return cv, cs
    d_bone = _segment_distance(co[cv], heads[cg], tails[cg])
    blen = np.linalg.norm(tails[cg] - heads[cg], axis=1)
    # reference distance: the closest bone used by any 1-ring neighbour
    cnt = graph.indptr[cv + 1] - graph.indptr[cv]
    pair_c = np.repeat(np.arange(len(cv)), cnt)
    offs = np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt)
    nbrs = graph.dst[np.repeat(graph.indptr[cv], cnt) + offs]
    ng = pw.group[nbrs]
    safe = np.maximum(ng, 0)
    ok = (ng >= 0) & bone_ok[safe]
    d = _segment_distance(co[cv][pair_c][:, None, :], heads[safe], tails[safe])
    d = np.where(ok, d, np.inf).min(1)
    d_ref = np.full(len(cv), np.inf)
    np.minimum.at(d_ref, pair_c, d)
    flag = np.isfinite(d_ref) & (d_bone > OUTLIER_DISTANCE_FACTOR * d_ref + OUTLIER_BONE_LENGTH_FACTOR * blen)
    return cv[flag], cs[flag]


def stray_far_influences(pw: PaddedWeights, graph: MeshGraph, co: np.ndarray, heads: np.ndarray,
                         tails: np.ndarray, bone_ok: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Weight bleeding: small secondary influences that dent the surface when a far bone moves.

    An influence of bone B on vertex v is stray when (1) it is small (< ``STRAY_MAX_WEIGHT``), (2) B is far from v
    compared with v's dominant bone (``d_B > 2 d_dominant + 0.5 len_B``) and (3) v lies in a detached island of B's
    influence region (edge-connected vertices weighted to B) whose largest weight on B is < ``STRAY_ISLAND_MAX``;
    the island holding most of B's total weight is B's main region and is never touched. (3) keeps smooth
    gradients that fade out across a whole part. Returns ``(vertex, slot)`` index arrays into ``pw``.
    """
    empty = np.zeros(0, np.int64)
    if pw.width < 2:
        return empty, empty
    dom = pw.group[:, 0]
    rows = np.nonzero((dom >= 0) & bone_ok[np.maximum(dom, 0)])[0]
    if len(rows) == 0:
        return empty, empty
    g = pw.group[rows, 1:]
    cand = (g >= 0) & (pw.weight[rows, 1:] < STRAY_MAX_WEIGHT) & bone_ok[np.maximum(g, 0)]
    ri, ci = np.nonzero(cand)
    if len(ri) == 0:
        return empty, empty
    verts = rows[ri]
    groups = g[ri, ci]
    slots = ci + 1
    d_dom = _segment_distance(co[verts], heads[dom[verts]], tails[dom[verts]])
    d_bone = _segment_distance(co[verts], heads[groups], tails[groups])
    blen = np.linalg.norm(tails[groups] - heads[groups], axis=1)
    far = d_bone > STRAY_DISTANCE_FACTOR * d_dom + STRAY_BONE_LENGTH_FACTOR * blen
    verts, groups, slots = verts[far], groups[far], slots[far]
    if len(verts) == 0:
        return empty, empty
    keep = np.zeros(len(verts), dtype=bool)
    e = graph.edges
    n = pw.n_verts
    for bone in np.unique(groups).tolist():
        hit = pw.group == bone
        member = hit.any(1)
        w_b = np.where(hit, pw.weight, 0.0).sum(1)
        idx = np.nonzero(member)[0]
        local = np.full(n, -1, np.int64)
        local[idx] = np.arange(len(idx))
        inner = member[e[:, 0]] & member[e[:, 1]] if len(e) else np.zeros(0, dtype=bool)
        labels = meshdata.connected_components(len(idx), local[e[inner, 0]], local[e[inner, 1]])
        total = np.bincount(labels, weights=w_b[idx])
        peak = np.zeros(len(total))
        np.maximum.at(peak, labels, w_b[idx])
        stray_island = np.zeros(n, dtype=bool)
        stray_island[idx] = (labels != int(np.argmax(total))) & (peak[labels] < STRAY_ISLAND_MAX)
        sel = groups == bone
        keep[sel] = stray_island[verts[sel]]
    return verts[keep].astype(np.int64), slots[keep].astype(np.int64)


def weight_l1_to_neighbours(pw: PaddedWeights, graph: MeshGraph, verts: np.ndarray) -> np.ndarray:
    """L1 distance between each vertex's weight vector and the mean of its weighted neighbours' (inf: none)."""
    out = np.full(len(verts), np.inf)
    for i, v in enumerate(verts.tolist()):
        nbrs = graph.dst[graph.indptr[v]:graph.indptr[v + 1]]
        nbrs = nbrs[pw.group[nbrs, 0] >= 0]
        if len(nbrs) == 0:
            continue
        mean: dict[int, float] = {}
        for g, w in zip(pw.group[nbrs].reshape(-1).tolist(), pw.weight[nbrs].reshape(-1).tolist()):
            if g >= 0:
                mean[g] = mean.get(g, 0.0) + w / len(nbrs)
        own = {g: w for g, w in zip(pw.group[v].tolist(), pw.weight[v].tolist()) if g >= 0}
        total = sum(own.values()) or 1.0
        out[i] = sum(abs(own.get(g, 0.0) / total - mean.get(g, 0.0)) for g in set(mean) | set(own))
    return out


def fill_unweighted(pw: PaddedWeights, graph: MeshGraph, co: np.ndarray, n_groups: int,
                    min_weight: float) -> dict[str, Any]:
    """Give every unweighted vertex weights (graph propagation, then rigid KD-tree attachment). In place.

    Returns counters plus ``filled_mask`` (V,) and ``rigid_mask`` (V,) bool arrays.
    """
    assigned = pw.group[:, 0] >= 0
    info: dict[str, Any] = {"filled_verts": 0, "rigid_verts": 0, "rigid_parts": 0, "iterations": 0,
                            "unfillable_verts": 0}
    start = assigned.copy()
    info["rigid_mask"] = np.zeros_like(assigned)
    if assigned.all() or not assigned.any():
        info["unfillable_verts"] = int((~assigned).sum())
        info["filled_mask"] = np.zeros_like(assigned)
        return info
    alpha = graph.inverse_length()
    width = pw.width
    while True:
        m = ~assigned[graph.src] & assigned[graph.dst]
        if not m.any():
            break
        u, nb, a = graph.src[m], graph.dst[m], alpha[m]
        rows, g, w = accumulate_rows(u, pw.group[nb], pw.weight[nb] * a[:, None], n_groups, width)
        pw.set_rows(rows, g, w)
        pw.prune(min_weight, rows)
        pw.normalize(rows)
        assigned[rows] = True
        info["iterations"] += 1
    info["filled_verts"] = int((assigned & ~start).sum())
    rest = ~assigned
    if rest.any():
        # parts without any weighted vertex: attach each part rigidly to its closest weighted vertex
        rest_idx = np.nonzero(rest)[0]
        e = graph.edges
        inner = rest[e[:, 0]] & rest[e[:, 1]]
        local = np.full(len(co), -1, np.int64)
        local[rest_idx] = np.arange(len(rest_idx))
        labels = meshdata.connected_components(len(rest_idx), local[e[inner, 0]], local[e[inner, 1]])
        src_idx = np.nonzero(assigned)[0]
        tree = kdtree.KDTree(len(src_idx))
        for i, p in zip(src_idx.tolist(), co[src_idx].tolist()):
            tree.insert(p, i)
        tree.balance()
        n_lab = int(labels.max()) + 1
        best_d = np.full(n_lab, np.inf)
        best_v = np.full(n_lab, -1, np.int64)
        for lab, p in zip(labels.tolist(), co[rest_idx].tolist()):
            _, idx, dist = tree.find(p)
            if idx is not None and dist < best_d[lab]:
                best_d[lab] = dist
                best_v[lab] = idx
        ok = best_v[labels] >= 0
        rows = rest_idx[ok]
        src_rows = best_v[labels[ok]]
        pw.set_rows(rows, pw.group[src_rows].copy(), pw.weight[src_rows].copy())
        assigned[rows] = True
        info["rigid_mask"][rows] = True
        info["rigid_verts"] = int(len(rows))
        info["rigid_parts"] = int(len(np.unique(labels[ok])))
        info["unfillable_verts"] = int((~ok).sum())
    info["filled_mask"] = assigned & ~start
    return info


def smooth_weights(pw: PaddedWeights, graph: MeshGraph, mask: np.ndarray, n_groups: int, iterations: int,
                   factor: float, min_weight: float) -> int:
    """Laplacian smoothing (Jacobi, inverse-edge-length weights) of the ``mask`` vertices' weights; all other
    vertices stay fixed. Returns the number of smoothed vertices."""
    if iterations <= 0 or not mask.any():
        return 0
    sel = mask[graph.src] & (pw.group[graph.dst, 0] >= 0)
    u, nb = graph.src[sel], graph.dst[sel]
    a = graph.inverse_length()[sel]
    asum = np.bincount(u, weights=a, minlength=pw.n_verts)
    rows_self = np.nonzero(mask & (asum > 0))[0]
    if len(rows_self) == 0:
        return 0
    coef = factor * a / asum[u]
    for _ in range(iterations):
        rows = np.concatenate([u, rows_self])
        groups = np.concatenate([pw.group[nb], pw.group[rows_self]])
        weights = np.concatenate([pw.weight[nb] * coef[:, None], pw.weight[rows_self] * (1.0 - factor)])
        out_rows, g, w = accumulate_rows(rows, groups, weights, n_groups, pw.width)
        pw.set_rows(out_rows, g, w)
        pw.prune(min_weight, out_rows)
        pw.normalize(out_rows)
    return int(len(rows_self))


# ================================================================================================================
# skin bindings + bpy I/O
# ================================================================================================================


@dataclass
class SkinBinding:
    """A mesh object skinned to one or more armatures."""

    obj: bpy.types.Object
    armatures: list[bpy.types.Object]
    group_names: list[str]
    deform: np.ndarray            # (G,) bool: group of a deform bone
    neutral: np.ndarray           # (G,) bool: group of a glTF ``neutral_bone`` placeholder
    heads: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))   # (G, 3) bone head, mesh space
    tails: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    bone_ok: np.ndarray = field(default_factory=lambda: np.zeros(0, bool))
    raw: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None
    """All vertex-group entries ``(vertex, group, weight)`` as read before any change."""

    @property
    def n_groups(self) -> int:
        return len(self.group_names)

    def bone_of(self, group: int) -> tuple[bpy.types.Object, str] | None:
        name = self.group_names[group]
        for arm in self.armatures:
            bone = arm.data.bones.get(name)
            if bone is not None and bone.use_deform:
                return arm, name
        return None


def _alive(obj: Any) -> bool:
    try:
        return obj is not None and bpy.data.objects.get(obj.name) == obj
    except ReferenceError:
        return False


def armatures_of(obj: bpy.types.Object) -> list[bpy.types.Object]:
    """Armatures deforming ``obj`` through vertex groups (Armature modifiers, or a legacy ARMATURE parent)."""
    out: list[bpy.types.Object] = []
    for mod in obj.modifiers:
        if mod.type == "ARMATURE" and mod.object is not None and mod.object.type == "ARMATURE" \
                and getattr(mod, "use_vertex_groups", True) and mod.object not in out:
            out.append(mod.object)
    if not out and obj.parent is not None and obj.parent.type == "ARMATURE" and obj.parent_type == "ARMATURE":
        out.append(obj.parent)
    return out


def find_bindings(ctx: TunerContext) -> tuple[list[SkinBinding], int]:
    """Skinned meshes among the result objects, and the number of armatures."""
    armature_count = sum(1 for a in ctx.armatures if _alive(a))
    bindings: list[SkinBinding] = []
    for obj in ctx.mesh_objects:
        if not _alive(obj) or obj.type != "MESH" or not obj.vertex_groups:
            continue
        arms = armatures_of(obj)
        if not arms:
            continue
        names = [g.name for g in obj.vertex_groups]
        deform = np.zeros(len(names), dtype=bool)
        neutral = np.zeros(len(names), dtype=bool)
        for i, name in enumerate(names):
            for arm in arms:
                bone = arm.data.bones.get(name)
                if bone is not None and bone.use_deform:
                    deform[i] = True
                    neutral[i] = name in NEUTRAL_BONE_NAMES and bone.parent is None and not bone.children
                    break
        if not deform.any():
            continue
        binding = SkinBinding(obj, arms, names, deform, neutral)
        _bone_segments(binding)
        bindings.append(binding)
        armature_count = max(armature_count, len(arms))
    return bindings, armature_count


def _bone_segments(b: SkinBinding) -> None:
    """Rest-pose bone segments of the deform groups in the mesh's object space."""
    g = b.n_groups
    b.heads = np.zeros((g, 3))
    b.tails = np.zeros((g, 3))
    b.bone_ok = np.zeros(g, dtype=bool)
    inv = b.obj.matrix_world.inverted_safe()
    for i in range(g):
        if not b.deform[i] or b.neutral[i]:
            continue
        found = b.bone_of(i)
        if found is None:
            continue
        arm, name = found
        bone = arm.data.bones[name]
        m = inv @ arm.matrix_world
        b.heads[i] = tuple(m @ bone.head_local)
        b.tails[i] = tuple(m @ bone.tail_local)
        b.bone_ok[i] = True


def read_weights(obj: bpy.types.Object) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """All vertex-group entries ``(vertex, group, weight)`` (~1 us per vertex, no bulk API exists)."""
    vi: list[int] = []
    gi: list[int] = []
    wi: list[float] = []
    for v in obj.data.vertices:
        idx = v.index
        for g in v.groups:
            vi.append(idx)
            gi.append(g.group)
            wi.append(g.weight)
    return np.asarray(vi, np.int64), np.asarray(gi, np.int64), np.asarray(wi, np.float64)


def write_weights(obj: bpy.types.Object, old: tuple[np.ndarray, np.ndarray, np.ndarray],
                  new: PaddedWeights, groups: np.ndarray) -> dict[str, int]:
    """Apply ``new`` weights to the vertex groups ``groups`` (indices); only changed entries are written."""
    n_groups = len(obj.vertex_groups)
    ov, og, ow = old
    sel = np.isin(og, groups)
    ov, og, ow = ov[sel], og[sel], ow[sel]
    nv, ng, nw = new.entries()
    old_key = ov * n_groups + og
    new_key = nv * n_groups + ng
    order = np.argsort(old_key)
    sk, sw = old_key[order], ow[order]
    if len(sk):
        pos = np.clip(np.searchsorted(sk, new_key), 0, len(sk) - 1)
        present = sk[pos] == new_key
        old_w = np.where(present, sw[pos], -1.0)
    else:
        present = np.zeros(len(new_key), dtype=bool)
        old_w = np.full(len(new_key), -1.0)
    write = ~present | (np.abs(old_w - nw) > WRITE_TOLERANCE)
    removed = ~np.isin(old_key, new_key)
    vgs = obj.vertex_groups
    n_removed = 0
    for g in np.unique(og[removed]).tolist():
        verts = ov[removed & (og == g)].tolist()
        vgs[g].remove(verts)
        n_removed += len(verts)
    wv, wg, ww = nv[write], ng[write], nw[write]
    for g in np.unique(wg).tolist():
        vg = vgs[g]
        m = wg == g
        for v, w in zip(wv[m].tolist(), ww[m].tolist()):
            vg.add([v], w, "REPLACE")
    obj.data.update()
    return {"entries_removed": n_removed, "entries_written": int(write.sum())}


def referenced_group_names(obj: bpy.types.Object) -> set[str]:
    """Vertex-group names used by modifiers, shape keys, particle systems or constraints targeting ``obj``."""
    names: set[str] = set()
    for mod in obj.modifiers:
        for prop in mod.bl_rna.properties:
            if prop.type == "STRING" and "vertex_group" in prop.identifier:
                value = getattr(mod, prop.identifier, "")
                if value:
                    names.add(value)
        if mod.type == "NODES":
            try:
                for key in mod.keys():
                    value = mod[key]
                    if key.endswith("_attribute_name") and isinstance(value, str) and value:
                        names.add(value)
            except (TypeError, AttributeError, KeyError):
                pass
    keys = getattr(obj.data, "shape_keys", None)
    if keys is not None:
        for kb in keys.key_blocks:
            if kb.vertex_group:
                names.add(kb.vertex_group)
    for ps in getattr(obj, "particle_systems", []):
        for prop in ps.bl_rna.properties:
            if prop.type == "STRING" and prop.identifier.startswith("vertex_group"):
                value = getattr(ps, prop.identifier, "")
                if value:
                    names.add(value)
    for other in bpy.data.objects:
        owners = [other.constraints]
        if other.type == "ARMATURE" and other.pose is not None:
            owners += [pb.constraints for pb in other.pose.bones]
        for cons in owners:
            for con in cons:
                if getattr(con, "target", None) == obj and getattr(con, "subtarget", ""):
                    names.add(con.subtarget)
    return names


def remove_empty_groups(obj: bpy.types.Object, keep_names: set[str], counts: np.ndarray | None = None) -> list[str]:
    """Delete vertex groups without any weight that are not in ``keep_names`` (deform-bone groups) and not
    referenced elsewhere. ``counts`` = entries per group index (read from the mesh when omitted). Returns the
    removed names."""
    if counts is None or len(counts) != len(obj.vertex_groups):
        _, g, w = read_weights(obj)
        counts = np.bincount(g[w > 0.0], minlength=len(obj.vertex_groups)) if len(g) else \
            np.zeros(len(obj.vertex_groups), np.int64)
    refs = referenced_group_names(obj)
    doomed = [vg.name for vg in obj.vertex_groups
              if counts[vg.index] == 0 and vg.name not in keep_names and vg.name not in refs]
    for name in doomed:
        vg = obj.vertex_groups.get(name)
        if vg is not None:
            obj.vertex_groups.remove(vg)
    return doomed


# ================================================================================================================
# pose checks
# ================================================================================================================


_CHANNELS = ("rotation_mode", "location", "rotation_quaternion", "rotation_euler", "rotation_axis_angle", "scale")


def _evaluated_positions(obj: bpy.types.Object) -> np.ndarray:
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    return meshdata.get_positions(obj.evaluated_get(dg).data)


def _reset_channels(pb: bpy.types.PoseBone) -> None:
    pb.location = (0.0, 0.0, 0.0)
    pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
    pb.rotation_euler = (0.0, 0.0, 0.0)
    pb.rotation_axis_angle = (0.0, 0.0, 1.0, 0.0)
    pb.scale = (1.0, 1.0, 1.0)


class PoseEvaluator:
    """Context manager: clean test pose for one skinned mesh, depsgraph evaluation, exact restore on exit.

    On enter: every pose channel of the mesh's armature(s) is set to identity, bone constraints are muted,
    ``pose_position`` is ``POSE``, non-armature modifiers of the mesh are hidden and its armature modifiers shown.
    ``rest`` holds the evaluated rest positions. On exit everything is restored and ``restored`` tells whether
    the re-evaluated mesh matches the evaluation taken before entering.
    """

    def __init__(self, obj: bpy.types.Object, armatures: list[bpy.types.Object]) -> None:
        self.obj = obj
        self.armatures = armatures
        self.n_verts = len(obj.data.vertices)
        self.rest: np.ndarray | None = None
        self.initial: np.ndarray | None = None
        self.restored: bool | None = None
        self.restore_error: float | None = None
        self._channels: list[tuple[Any, dict[str, Any]]] = []
        self._mutes: list[tuple[Any, bool]] = []
        self._pose_positions: list[tuple[Any, str]] = []
        self._modifiers: list[tuple[Any, bool]] = []
        self._unhidden = False

    def evaluate(self) -> np.ndarray:
        """Evaluated (deformed) vertex positions (V, 3) in the mesh's object space."""
        co = _evaluated_positions(self.obj)
        if len(co) != self.n_verts:
            raise StepError("pose_eval_mismatch", f"Evaluated mesh of '{self.obj.name}' has {len(co)} vertices, "
                                                  f"expected {self.n_verts}")
        return co

    def __enter__(self) -> PoseEvaluator:
        self.initial = _evaluated_positions(self.obj)
        try:
            for arm in self.armatures:
                self._pose_positions.append((arm.data, arm.data.pose_position))
                arm.data.pose_position = "POSE"
                for pb in arm.pose.bones:
                    saved = {}
                    for name in _CHANNELS:
                        value = getattr(pb, name)
                        saved[name] = value if isinstance(value, str) else tuple(value)
                    self._channels.append((pb, saved))
                    for con in pb.constraints:
                        self._mutes.append((con, con.mute))
                        con.mute = True
                    _reset_channels(pb)
            for mod in self.obj.modifiers:
                self._modifiers.append((mod, mod.show_viewport))
                mod.show_viewport = mod.type == "ARMATURE" and mod.object in self.armatures
            if self.obj.hide_viewport:
                self._unhidden = True
                self.obj.hide_viewport = False
            self.rest = self.evaluate()
        except BaseException:
            self._restore()
            raise
        return self

    def __exit__(self, *exc: Any) -> None:
        self._restore()

    def _restore(self) -> None:
        for pb, saved in self._channels:
            try:
                pb.rotation_mode = saved["rotation_mode"]
                for name in _CHANNELS[1:]:
                    setattr(pb, name, saved[name])
            except (ReferenceError, AttributeError, TypeError) as exc:
                log.warning("Could not restore a pose channel: %s", exc)
        for con, mute in self._mutes:
            try:
                con.mute = mute
            except ReferenceError:
                pass
        for data, value in self._pose_positions:
            try:
                data.pose_position = value
            except ReferenceError:
                pass
        for mod, show in self._modifiers:
            try:
                mod.show_viewport = show
            except ReferenceError:
                pass
        if self._unhidden:
            self.obj.hide_viewport = True
        self._channels, self._mutes, self._pose_positions, self._modifiers = [], [], [], []
        self._unhidden = False
        if self.initial is not None:
            after = _evaluated_positions(self.obj)
            if after.shape != self.initial.shape:
                self.restored = False
                return
            scale = max(float(np.abs(self.initial).max()) if len(after) else 0.0, 1e-9)
            self.restore_error = float(np.abs(after - self.initial).max()) / scale if len(after) else 0.0
            self.restored = self.restore_error <= 1e-6

    @staticmethod
    def set_rotation(arm: bpy.types.Object, bone: str, axis: str, angle: float) -> None:
        arm.pose.bones[bone].matrix_basis = Matrix.Rotation(angle, 4, axis)

    @staticmethod
    def clear_rotation(arm: bpy.types.Object, bone: str) -> None:
        _reset_channels(arm.pose.bones[bone])


@dataclass
class PoseTopology:
    """Rest-pose measurement base of one mesh for the pose tests.

    Degenerate geometry must not dominate the ratios (TRELLIS meshes have near-zero slivers): edge ratios use the
    rest length floored at ``EDGE_FLOOR`` x the median edge length, only triangles with a rest area of at least
    ``TRI_FLOOR`` x the median area are checked for collapse, and the local edge length of the spike score is
    floored the same way.
    """

    edges: np.ndarray       # (E, 2) non-degenerate edges
    tris: np.ndarray        # (T, 3) well-formed triangles
    l0: np.ndarray          # (E,) floored rest lengths
    a0: np.ndarray          # (T,) rest areas
    degree: np.ndarray      # (V,) number of edges per vertex
    local_len: np.ndarray   # (V,) floored mean rest length of the incident edges
    move_eps: float

    EDGE_FLOOR = 0.25
    TRI_FLOOR = 0.05

    @classmethod
    def build(cls, mesh: bpy.types.Mesh, rest: np.ndarray) -> PoseTopology:
        n = len(rest)
        diag = max(float(np.linalg.norm(rest.max(0) - rest.min(0))) if n else 0.0, 1e-9)
        edges = meshdata.get_edges(mesh)
        l0 = np.linalg.norm(rest[edges[:, 0]] - rest[edges[:, 1]], axis=1) if len(edges) else np.zeros(0)
        ok = l0 > 1e-9 * diag
        edges, l0 = edges[ok], l0[ok]
        median = float(np.median(l0)) if len(l0) else 0.0
        floor = cls.EDGE_FLOOR * median
        deg = np.bincount(edges[:, 0], minlength=n) + np.bincount(edges[:, 1], minlength=n)
        tot = np.bincount(edges[:, 0], l0, minlength=n) + np.bincount(edges[:, 1], l0, minlength=n)
        local = np.maximum(np.where(deg > 0, tot / np.maximum(deg, 1), 0.0), floor)
        tris = meshdata.get_loop_triangles(mesh)[0] if len(mesh.polygons) else np.zeros((0, 3), np.int64)
        a0 = meshdata.triangle_areas(rest, tris) if len(tris) else np.zeros(0)
        if len(a0):
            keep = a0 >= max(cls.TRI_FLOOR * float(np.median(a0)), 1e-14 * diag * diag)
            tris, a0 = tris[keep], a0[keep]
        return cls(edges, tris, np.maximum(l0, floor), a0, deg, local, 1e-6 * diag)


def measure_pose(topo: PoseTopology, rest: np.ndarray, posed: np.ndarray,
                 spike: np.ndarray | None = None) -> dict[str, Any]:
    """Edge/triangle deformation of ``posed`` vs ``rest`` over the deformed region (edges/triangles with a moved
    vertex). ``spike`` (V,) is updated in place with each vertex's minimum displacement relative to its neighbours,
    in local edge lengths (only vertices whose every edge was affected can score)."""
    disp = posed - rest
    moved = np.abs(disp).max(1) > topo.move_eps
    e = topo.edges
    aff = np.nonzero(moved[e[:, 0]] | moved[e[:, 1]])[0] if len(e) else np.zeros(0, np.int64)
    ea = e[aff]
    r = np.linalg.norm(posed[ea[:, 0]] - posed[ea[:, 1]], axis=1) / topo.l0[aff] if len(aff) else np.zeros(0)
    t = topo.tris
    aff_t = np.nonzero(moved[t[:, 0]] | moved[t[:, 1]] | moved[t[:, 2]])[0] if len(t) else np.zeros(0, np.int64)
    if len(aff_t):
        a1 = meshdata.triangle_areas(posed, t[aff_t])
        collapsed = int((a1 < COLLAPSE_AREA_RATIO * topo.a0[aff_t]).sum())
    else:
        collapsed = 0
    stretched = int((r > STRETCH_RATIO).sum())
    compressed = int((r < COMPRESS_RATIO).sum())
    if spike is not None and len(aff):
        diff = np.linalg.norm(disp[ea[:, 0]] - disp[ea[:, 1]], axis=1)
        ends = np.concatenate([ea[:, 0], ea[:, 1]])
        vals = np.concatenate([diff, diff])
        full = np.bincount(ends, minlength=len(rest)) == topo.degree
        sel = full[ends]
        ends, vals = ends[sel], vals[sel]
        if len(ends):
            order = np.argsort(ends, kind="stable")
            ends, vals = ends[order], vals[order]
            starts = np.concatenate([[0], np.nonzero(ends[1:] != ends[:-1])[0] + 1])
            verts = ends[starts]
            score = np.minimum.reduceat(vals, starts) / np.maximum(topo.local_len[verts], 1e-30)
            spike[verts] = np.maximum(spike[verts], score)
    n_aff, n_aff_t = int(len(aff)), int(len(aff_t))
    return {"max_stretch": float(r.max()) if len(r) else 1.0, "min_ratio": float(r.min()) if len(r) else 1.0,
            "stretched_edges": stretched, "compressed_edges": compressed, "collapsed_tris": collapsed,
            "affected_edges": n_aff, "affected_tris": n_aff_t, "moved_verts": int(moved.sum()),
            "bad_edge_pct": 100.0 * (stretched + compressed) / n_aff if n_aff else 0.0,
            "collapsed_pct": 100.0 * collapsed / n_aff_t if n_aff_t else 0.0}


def select_pose_bones(b: SkinBinding, pw: PaddedWeights, max_bones: int,
                      n_verts: int) -> list[tuple[bpy.types.Object, str, int]]:
    """Major deform bones ``(armature, bone, weighted_vertex_count)``, most weighted vertices first.

    At most ``max_bones``, fewer on dense meshes (``POSE_VERTEX_BUDGET`` vertex evaluations per pass).
    """
    filled = pw.group >= 0
    counts = np.bincount(pw.group[filled], minlength=b.n_groups) if filled.any() else np.zeros(b.n_groups, int)
    budget_bones = max(POSE_MIN_BONES, POSE_VERTEX_BUDGET // max(1, n_verts * 2 * len(POSE_AXES)))
    limit = min(max_bones, budget_bones)
    out: list[tuple[bpy.types.Object, str, int]] = []
    for g in np.argsort(-counts, kind="stable").tolist():
        if counts[g] == 0 or len(out) >= limit:
            break
        if not b.deform[g] or b.neutral[g]:
            continue
        found = b.bone_of(g)
        if found is not None:
            out.append((found[0], found[1], int(counts[g])))
    return out


def run_pose_tests(b: SkinBinding, bones: list[tuple[bpy.types.Object, str, int]], angle_deg: float,
                   collect_spikes: bool = False,
                   progress: Callable[[float], None] | None = None) -> dict[str, Any]:
    """Rotate each bone +-``angle_deg`` about its local X and Z; aggregate :func:`measure_pose` per bone."""
    angle = math.radians(angle_deg)
    per_bone: list[dict[str, Any]] = []
    spike = None
    total = max(1, len(bones) * 2 * len(POSE_AXES))
    done = 0
    with PoseEvaluator(b.obj, b.armatures) as ev:
        rest = ev.rest
        assert rest is not None
        topo = PoseTopology.build(b.obj.data, rest)
        if collect_spikes:
            spike = np.zeros(len(rest))
        for arm, bone, count in bones:
            agg: dict[str, Any] = {"bone": bone, "weighted_verts": count, "max_stretch": 1.0, "min_ratio": 1.0,
                                   "stretched_edges": 0, "compressed_edges": 0, "collapsed_tris": 0,
                                   "bad_edge_pct": 0.0, "collapsed_pct": 0.0, "moved_verts": 0}
            try:
                for axis in POSE_AXES:
                    for sign in (1.0, -1.0):
                        ev.set_rotation(arm, bone, axis, sign * angle)
                        m = measure_pose(topo, rest, ev.evaluate(), spike)
                        agg["max_stretch"] = max(agg["max_stretch"], m["max_stretch"])
                        agg["min_ratio"] = min(agg["min_ratio"], m["min_ratio"])
                        for key in ("stretched_edges", "compressed_edges", "collapsed_tris", "moved_verts",
                                    "bad_edge_pct", "collapsed_pct"):
                            agg[key] = max(agg[key], m[key])
                        done += 1
                        if progress is not None:
                            progress(done / total)
            finally:
                ev.clear_rotation(arm, bone)
            per_bone.append(agg)
    summary = summarize_pose(per_bone, len(POSE_AXES) * 2)
    summary["restored"] = bool(ev.restored)
    summary["restore_error"] = ev.restore_error
    summary["edges"] = int(len(topo.edges))
    summary["triangles"] = int(len(topo.tris))
    if spike is not None:
        summary["spike_score"] = spike
    return summary


def summarize_pose(per_bone: list[dict[str, Any]], rotations: int) -> dict[str, Any]:
    """Worst-case aggregate over the tested bones (+ per-bone rows and the 5 worst bones)."""
    if not per_bone:
        return {"bones_tested": 0, "rotations_per_bone": rotations, "max_stretch": None, "min_ratio": None,
                "stretched_edges": 0, "compressed_edges": 0, "collapsed_tris": 0, "bad_edge_pct": 0.0,
                "collapsed_pct": 0.0, "per_bone": [], "worst_bones": []}
    worst = sorted(per_bone, key=lambda r: (-r["max_stretch"], -r["bad_edge_pct"], r["min_ratio"]))
    return {"bones_tested": len(per_bone), "rotations_per_bone": rotations,
            "max_stretch": max(r["max_stretch"] for r in per_bone),
            "min_ratio": min(r["min_ratio"] for r in per_bone),
            "stretched_edges": max(r["stretched_edges"] for r in per_bone),
            "compressed_edges": max(r["compressed_edges"] for r in per_bone),
            "collapsed_tris": max(r["collapsed_tris"] for r in per_bone),
            "bad_edge_pct": max(r["bad_edge_pct"] for r in per_bone),
            "collapsed_pct": max(r["collapsed_pct"] for r in per_bone),
            "per_bone": [_round_dict(r) for r in per_bone],
            "worst_bones": [{"bone": r["bone"], "max_stretch": round(r["max_stretch"], 3),
                             "bad_edge_pct": round(r["bad_edge_pct"], 3), "collapsed_tris": r["collapsed_tris"]}
                            for r in worst[:5]]}


def _round_dict(d: dict[str, Any]) -> dict[str, Any]:
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()}


def merge_pose(summaries: list[dict[str, Any] | None]) -> dict[str, Any] | None:
    """Worst-case merge of per-mesh pose summaries (``None`` when nothing was tested)."""
    tested = [s for s in summaries if s and s.get("bones_tested")]
    if not tested:
        return None
    merged: dict[str, Any] = {"bones_tested": sum(s["bones_tested"] for s in tested),
                              "rotations_per_bone": tested[0]["rotations_per_bone"],
                              "max_stretch": max(s["max_stretch"] for s in tested),
                              "min_ratio": min(s["min_ratio"] for s in tested),
                              "restored": all(s.get("restored", True) for s in tested)}
    for key in ("stretched_edges", "compressed_edges", "collapsed_tris", "bad_edge_pct", "collapsed_pct"):
        merged[key] = max(s[key] for s in tested)
    worst = [w for s in tested for w in s["worst_bones"]]
    merged["worst_bones"] = sorted(worst, key=lambda r: -r["max_stretch"])[:5]
    return merged


def stretch_pct(ratio: float | None) -> float | None:
    """``pose_max_stretch`` metric value: elongation of the worst edge in percent (2x -> 100 %)."""
    return None if ratio is None else round(max(0.0, ratio - 1.0) * 100.0, 2)


def build_pose_checks(before: dict[str, Any] | None, after: dict[str, Any] | None, weights_after: dict[str, Any],
                      settings: RigSettings) -> list[dict[str, Any]]:
    """Validation check dicts ``{id, label, status, value, threshold, message}`` for ``ctx.store["pose_checks"]``.

    * ``rig_weights``: fail with unweighted vertices left, warn when over the influence limit or unnormalized.
    * ``pose_stretch``: fail when an edge stretches > 5x (spike), warn > 2.5x or > 1 % of the deformed edges
      outside [0.5x, 2x] in the worst pose.
    * ``pose_collapse``: warn on any triangle collapsing below 5 % of its area, fail above 1 % of the deformed ones.
    """
    checks: list[dict[str, Any]] = []
    unweighted = int(weights_after.get("unweighted_verts", 0))
    over = int(weights_after.get("over_limit_verts", 0))
    unnorm = int(weights_after.get("unnormalized_verts", 0))
    status = "fail" if unweighted else ("warn" if over or unnorm else "pass")
    checks.append({"id": "rig_weights", "label": "Skin weights", "status": status,
                   "value": int(weights_after.get("max_influences", 0)), "threshold": settings.max_influences,
                   "message": (f"Max {weights_after.get('max_influences', 0)} influences per vertex (limit "
                               f"{settings.max_influences}), {unnorm} unnormalized, {unweighted} unweighted "
                               "vertices")})
    if after is None:
        return checks
    ratio = float(after["max_stretch"])
    bad = float(after["bad_edge_pct"])
    if ratio > POSE_STRETCH_FAIL:
        status = "fail"
    elif ratio > POSE_STRETCH_WARN or bad > POSE_BAD_EDGE_PCT_WARN:
        status = "warn"
    else:
        status = "pass"
    msg = (f"Max edge stretch {ratio:.2f}x over {after['bones_tested']} bones x {after['rotations_per_bone']} "
           f"rotations (+-{settings.pose_angle_deg:g} deg about local X/Z); worst pose: "
           f"{after['stretched_edges']} edges > {STRETCH_RATIO:g}x, {after['compressed_edges']} < "
           f"{COMPRESS_RATIO:g}x")
    worst = ", ".join(f"{w['bone']} {w['max_stretch']:.2f}x" for w in after["worst_bones"][:3])
    if worst:
        msg += f"; worst bones: {worst}"
    if before is not None and before.get("max_stretch") is not None:
        msg += f" (before cleanup: {before['max_stretch']:.2f}x)"
    checks.append({"id": "pose_stretch", "label": "Pose deformation", "status": status, "value": round(ratio, 4),
                   "threshold": POSE_STRETCH_WARN, "message": msg})
    collapsed = int(after["collapsed_tris"])
    pct = float(after["collapsed_pct"])
    status = "pass" if collapsed == 0 else ("fail" if pct > POSE_COLLAPSE_PCT_FAIL else "warn")
    msg = (f"{collapsed} triangles collapse below {COLLAPSE_AREA_RATIO:g}x of their area in the worst pose "
           f"({pct:.3f}% of the deformed triangles)")
    if before is not None:
        msg += f" (before cleanup: {before['collapsed_tris']})"
    checks.append({"id": "pose_collapse", "label": "Collapsed triangles in poses", "status": status,
                   "value": collapsed, "threshold": 0, "message": msg})
    return checks


# ================================================================================================================
# per-mesh optimization
# ================================================================================================================


def optimize_binding(b: SkinBinding, settings: RigSettings,
                     spike_score: np.ndarray | None = None) -> tuple[PaddedWeights, dict[str, Any]]:
    """Compute the cleaned deform weights of one binding (pure computation - the mesh is not modified).

    Returns the cleaned weights (width ``settings.max_influences``) and the fix counters.
    """
    if b.raw is None:
        b.raw = read_weights(b.obj)
    mesh = b.obj.data
    n = len(mesh.vertices)
    co = meshdata.get_positions(mesh)
    graph = MeshGraph.build(co, meshdata.get_edges(mesh))
    v, g, w = b.raw
    is_def = b.deform[g] & ~b.neutral[g] if len(g) else np.zeros(0, dtype=bool)
    pw = PaddedWeights.from_entries(n, v[is_def], g[is_def], w[is_def])
    neutral_rows = np.unique(v[b.neutral[g] & (w > 0)]) if len(g) else np.zeros(0, np.int64)
    fixes: dict[str, Any] = {
        "neutral_bone_verts": int((pw.group[neutral_rows, 0] < 0).sum()) if len(neutral_rows) else 0,
        "tiny_weights_removed": pw.prune(settings.min_weight), "outlier_influences": 0, "spike_vertices": 0,
        "stray_influences": 0}
    repaired = np.zeros(n, dtype=bool)

    if settings.fix_outliers:
        ov, os_ = isolated_far_influences(pw, graph, co, b.heads, b.tails, b.bone_ok)
        if len(ov):
            row_sum = pw.weight.sum(1)
            removed = np.zeros(n)
            np.add.at(removed, ov, pw.weight[ov, os_])
            clear = np.unique(ov[removed[ov] > OUTLIER_CLEAR_FRACTION * row_sum[ov]])
            pw.group[ov, os_] = -1
            pw.weight[ov, os_] = 0.0
            pw.clear_rows(clear)
            pw.sort_rows(np.unique(ov))
            repaired[ov] = True
            fixes["outlier_influences"] = int(len(ov))
            fixes["outlier_vertices"] = [int(x) for x in np.unique(ov)[:20]]
        if spike_score is not None and len(spike_score) == n:
            cand = np.nonzero((spike_score > SPIKE_EDGE_LENGTHS) & (pw.group[:, 0] >= 0) & ~repaired)[0]
            if len(cand):
                l1 = weight_l1_to_neighbours(pw, graph, cand)
                hit = cand[np.isfinite(l1) & (l1 > SPIKE_WEIGHT_L1)]
                if len(hit):
                    pw.clear_rows(hit)
                    repaired[hit] = True
                    fixes["spike_vertices"] = int(len(hit))
                    fixes["spike_vertex_ids"] = [int(x) for x in hit[:20]]
        sv, ss = stray_far_influences(pw, graph, co, b.heads, b.tails, b.bone_ok)
        if len(sv):
            pw.group[sv, ss] = -1
            pw.weight[sv, ss] = 0.0
            pw.sort_rows(np.unique(sv))
            repaired[sv] = True
        fixes["stray_influences"] = int(len(sv))

    fixes["over_limit_verts"] = int(pw.limit(settings.max_influences).sum())
    sums = pw.sums()
    fixes["normalized_verts"] = int(((sums > 0) & (np.abs(sums - 1.0) > WEIGHT_TOLERANCE)).sum())
    pw.normalize()
    for _ in range(4):  # normalization can push small weights below the threshold again
        if not pw.prune(settings.min_weight):
            break
        pw.normalize()

    fill = fill_unweighted(pw, graph, co, b.n_groups, settings.min_weight)
    filled = fill.pop("filled_mask")
    rigid = fill.pop("rigid_mask")
    fixes.update({"unweighted_filled": int(fill["filled_verts"]), "rigid_attached": int(fill["rigid_verts"]),
                  "rigid_parts": int(fill["rigid_parts"]), "unfillable_verts": int(fill["unfillable_verts"]),
                  "fill_iterations": int(fill["iterations"])})
    smooth_mask = (filled | repaired) & ~rigid & (pw.group[:, 0] >= 0)
    fixes["smoothed_verts"] = smooth_weights(pw, graph, smooth_mask, b.n_groups, settings.smooth_iterations,
                                             settings.smooth_factor, settings.min_weight)
    # final invariants: <= max_influences, >= min_weight, normalized
    pw.limit(settings.max_influences)
    pw.prune(settings.min_weight)
    pw.normalize()
    return pw, fixes


# ================================================================================================================
# step
# ================================================================================================================


def run(ctx: TunerContext) -> StepOutcome:
    """Clean the skin weights of every skinned mesh and run the pose checks (see module doc)."""
    settings = RigSettings.from_options(ctx.options)
    if not getattr(ctx.options, "optimize_rig", True):
        return StepOutcome("skipped", "Rig optimization is disabled", {"settings": asdict(settings)})
    bindings, n_armatures = find_bindings(ctx)
    if not bindings:
        if n_armatures == 0:
            return StepOutcome("skipped", SKIP_NO_RIG, {"armatures": 0})
        return StepOutcome("skipped", "No rig present (the armature has no skinned mesh with deform weights)",
                           {"armatures": n_armatures})

    t_all = time.perf_counter()
    timings: dict[str, float] = {}
    warnings: list[str] = []
    n_b = len(bindings)

    def prog(lo: float, hi: float, i: int) -> Callable[[float], None]:
        span = (hi - lo) / n_b
        return lambda f: ctx.progress(lo + span * (i + f), "Testing poses")

    # 1) read the weights, "before" statistics and pose tests (original weights)
    ctx.progress(0.01, "Reading skin weights")
    t = time.perf_counter()
    for b in bindings:
        b.raw = read_weights(b.obj)
    timings["read"] = time.perf_counter() - t
    before_stats: list[dict[str, Any]] = []
    before_pose: list[dict[str, Any] | None] = []
    bone_lists: list[list[tuple[bpy.types.Object, str, int]]] = []
    spikes: list[np.ndarray | None] = []
    t = time.perf_counter()
    for i, b in enumerate(bindings):
        v, g, w = b.raw
        n = len(b.obj.data.vertices)
        is_def = b.deform[g] if len(g) else np.zeros(0, dtype=bool)
        raw_pw = PaddedWeights.from_entries(n, v[is_def], g[is_def], w[is_def])
        before_stats.append(weight_stats(raw_pw, settings.max_influences, settings.min_weight))
        bones: list[tuple[bpy.types.Object, str, int]] = []
        summary = None
        spike = None
        if settings.pose_checks and settings.pose_max_bones > 0:
            raw_pw.prune(settings.min_weight)
            bones = select_pose_bones(b, raw_pw, settings.pose_max_bones, n)
            if bones:
                try:
                    summary = run_pose_tests(b, bones, settings.pose_angle_deg, collect_spikes=True,
                                             progress=prog(0.02, 0.40, i))
                    spike = summary.pop("spike_score", None)
                except StepError as exc:
                    warnings.append(f"pose checks skipped for '{b.obj.name}': {exc.message}")
                    bones = []
        bone_lists.append(bones)
        before_pose.append(summary)
        spikes.append(spike)
    timings["pose_before"] = time.perf_counter() - t

    # 2) cleaned weights for every mesh (nothing is modified yet)
    ctx.progress(0.42, "Cleaning skin weights")
    t = time.perf_counter()
    results = [optimize_binding(b, settings, spikes[i]) for i, b in enumerate(bindings)]
    timings["clean"] = time.perf_counter() - t

    # 3) write them
    ctx.progress(0.55, "Writing skin weights")
    t = time.perf_counter()
    write_info = [write_weights(b.obj, b.raw, pw, np.nonzero(b.deform)[0]) for b, (pw, _) in zip(bindings, results)]
    timings["write"] = time.perf_counter() - t

    # 4) "after" pose tests with the same bones
    t = time.perf_counter()
    after_pose: list[dict[str, Any] | None] = []
    for i, b in enumerate(bindings):
        summary = None
        unchanged = write_info[i]["entries_written"] == 0 and write_info[i]["entries_removed"] == 0
        if bone_lists[i] and unchanged and before_pose[i] is not None:
            summary = dict(before_pose[i])  # same weights -> same deformation, no second pass needed
        elif bone_lists[i]:
            try:
                summary = run_pose_tests(b, bone_lists[i], settings.pose_angle_deg, progress=prog(0.6, 0.95, i))
            except StepError as exc:
                warnings.append(f"pose checks after cleanup failed for '{b.obj.name}': {exc.message}")
        after_pose.append(summary)
    timings["pose_after"] = time.perf_counter() - t

    # 5) checks + metrics, then drop empty non-deform groups (last: nothing after it can fail half-way)
    after_stats = [weight_stats(pw, settings.max_influences, settings.min_weight) for pw, _ in results]
    pose_b = merge_pose(before_pose)
    pose_a = merge_pose(after_pose)
    totals_before = _merge_stats(before_stats)
    totals_after = _merge_stats(after_stats)
    ctx.store["pose_checks"] = build_pose_checks(pose_b, pose_a, totals_after, settings)
    ctx.set_metric("max_bone_influences", before=totals_before["max_influences"],
                   after=totals_after["max_influences"])
    ctx.set_metric("unnormalized_weight_verts", before=totals_before["unnormalized_verts"],
                   after=totals_after["unnormalized_verts"])
    if pose_b is not None or pose_a is not None:
        ctx.set_metric("pose_max_stretch", before=stretch_pct(pose_b["max_stretch"]) if pose_b else None,
                       after=stretch_pct(pose_a["max_stretch"]) if pose_a else None)
    if any(p is not None and not p.get("restored", True) for p in (pose_b, pose_a)):
        warnings.append("the armature pose could not be restored exactly after the pose checks")

    removed_groups = []
    for b, (pw, _) in zip(bindings, results):
        keep = {name for name, d in zip(b.group_names, b.deform.tolist()) if d}
        _, g, w = b.raw
        counts = np.bincount(g[(w > 0.0) & ~b.deform[g]], minlength=b.n_groups) if len(g) else \
            np.zeros(b.n_groups, np.int64)
        counts = counts + np.bincount(pw.entries()[1], minlength=b.n_groups)
        removed_groups.append(remove_empty_groups(b.obj, keep, counts))

    meshes: list[dict[str, Any]] = []
    for i, b in enumerate(bindings):
        pw, fixes = results[i]
        fixes["groups_removed"] = removed_groups[i]
        meshes.append({"name": b.obj.name, "armatures": [a.name for a in b.armatures], "vertices": pw.n_verts,
                       "vertex_groups": b.n_groups, "deform_groups": int(b.deform.sum()),
                       "before": before_stats[i], "after": after_stats[i], "fixes": fixes, "write": write_info[i],
                       "pose_before": _strip_pose(before_pose[i]), "pose_after": _strip_pose(after_pose[i])})
        if fixes["unfillable_verts"]:
            warnings.append(f"{fixes['unfillable_verts']} vertices of '{b.obj.name}' have no weights and no "
                            "weighted vertex to copy from")
        b.raw = None
    timings["total"] = time.perf_counter() - t_all
    details: dict[str, Any] = {"armatures": n_armatures, "settings": asdict(settings), "meshes": meshes,
                               "pose": {"before": pose_b, "after": pose_a},
                               "timings_s": {k: round(val, 3) for k, val in timings.items()}}
    ctx.progress(1.0, "Rig optimized")
    message = _summary_message(meshes, pose_b, pose_a, settings.max_influences)
    if warnings:
        details["warnings"] = warnings
        for text in warnings:
            ctx.warn(f"Rig optimization: {text}")
        return StepOutcome("warning", message, details)
    return StepOutcome("done", message, details)


def _strip_pose(summary: dict[str, Any] | None) -> dict[str, Any] | None:
    if summary is None:
        return None
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in summary.items() if k != "spike_score"}


def _merge_stats(stats: list[dict[str, Any]]) -> dict[str, Any]:
    return {"max_influences": max((s["max_influences"] for s in stats), default=0),
            "over_limit_verts": sum(s["over_limit_verts"] for s in stats),
            "unnormalized_verts": sum(s["unnormalized_verts"] for s in stats),
            "unweighted_verts": sum(s["unweighted_verts"] for s in stats),
            "tiny_weights": sum(s["tiny_weights"] for s in stats)}


def _summary_message(meshes: list[dict[str, Any]], pose_b: dict[str, Any] | None,
                     pose_a: dict[str, Any] | None, max_influences: int) -> str:
    def total(key: str) -> int:
        return sum(int(m["fixes"].get(key) or 0) for m in meshes)

    parts = [f"{len(meshes)} skinned mesh" + ("es" if len(meshes) != 1 else "")]
    for key, label in (("over_limit_verts", f"limited to {max_influences} influences"),
                       ("normalized_verts", "normalized"),
                       ("tiny_weights_removed", "tiny weights removed"), ("unweighted_filled", "unweighted filled"),
                       ("rigid_attached", "attached rigidly"), ("outlier_influences", "outlier influences removed"),
                       ("spike_vertices", "spikes fixed"), ("stray_influences", "stray influences removed")):
        value = total(key)
        if value:
            parts.append(f"{value:,} {label}")
    groups = sum(len(m["fixes"].get("groups_removed") or []) for m in meshes)
    if groups:
        parts.append(f"{groups} empty group{'s' if groups != 1 else ''} removed")
    if len(parts) == 1:
        parts.append("weights already clean")
    if pose_a is not None:
        parts.append(f"pose stretch {pose_b['max_stretch']:.2f}x -> {pose_a['max_stretch']:.2f}x"
                     if pose_b is not None else f"pose stretch {pose_a['max_stretch']:.2f}x")
    return ", ".join(parts)
