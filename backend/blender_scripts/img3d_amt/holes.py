"""Hole filling for the geometry-repair step: boundary cycles -> triangulated, refined, faired, UV-mapped patches.

Pipeline (per hole, see :func:`fill_holes`):

1. :func:`boundary_cycles` (numpy) walks the boundary half-edges in the direction OPPOSITE to their single face, so a
   patch triangle ``(c[i-1], c[i], c[i+1])`` winds like its neighbours (normal on the same side). Vertices with several
   outgoing boundary half-edges (two holes touching) are split into simple cycles; dangling chains (the 1-2-edge
   slits next to non-manifold junctions) are not holes and are only reported.
2. :func:`plan_holes` keeps cycles with ``3 <= edges <= max_edges`` and ``perimeter <= max_perimeter`` (25 % of the
   bbox diagonal by default) and picks, per hole, ONE UV island (the island of most rim faces) and a UV per rim vertex
   from that island (nearest rim vertex along the cycle when the vertex has no corner in the island) - patches
   never stretch across UV islands.
3. :func:`triangulate_cycle`: advancing-front ear clipping in 3D, smallest interior angle first (measured around the
   rim faces' mean normal).
4. In bmesh (:func:`fill_holes`): faces are created with the rim UVs, long interior edges are split
   (``subdivide_edges``) until they match the mean rim edge length, re-triangulated and edge-flipped
   (``beautify_fill``) with a tangential relaxation between rounds; finally the new interior vertices are faired
   with the rim and its outer 1-ring fixed (least-squares bi-Laplacian / thin-plate energy solved with CG), so the
   patch continues the surrounding curvature. New vertices get the POINT float attribute ``amt_filled = 1``; their
   UVs are interpolated by bmesh inside the chosen island.

Rejected patches (folded/flipped triangles after fairing) are removed again and reported as skipped.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

FILLED_ATTRIBUTE = "amt_filled"
MAX_PERIMETER_REL = 0.25
"""Holes whose perimeter exceeds this fraction of the bbox diagonal are not filled."""
REFINE_FACTOR = 4.0 / 3.0
"""Interior patch edges longer than ``REFINE_FACTOR x`` the mean rim edge length are split."""
REFINE_ROUNDS = 10
RELAX_ITERATIONS = 3
FAIR_MAX_ITER = 2000
FAIR_TOL = 1e-10
FLIPPED_PATCH_MAX = 0.2
"""A patch is rejected when more than this fraction of its area faces against the rim normal."""
UV_EPS = 1e-5


# ================================================================================================================
# boundary cycles (numpy)
# ================================================================================================================


@dataclass
class BoundaryCycle:
    """A closed boundary walk ordered for filling (patch triangles wind like the rim faces)."""

    verts: np.ndarray
    """(n,) int64 vertex ids; edge i runs verts[i] -> verts[(i + 1) % n]."""
    faces: np.ndarray
    """(n,) int64 rim face of edge i."""
    loops: np.ndarray
    """(n,) int64 the rim face's loop (corner) on edge i (the corner of the edge's FACE-direction start vertex)."""
    perimeter: float = 0.0

    @property
    def edges(self) -> int:
        return int(len(self.verts))


def loop_next(loop_start: np.ndarray, loop_total: np.ndarray) -> np.ndarray:
    """Next loop (corner) of the same face for every loop, (L,) int64."""
    n_loops = int(loop_total.sum())
    nxt = np.arange(1, n_loops + 1, dtype=np.int64)
    last = loop_start + loop_total - 1
    nxt[last] = loop_start
    return nxt


def boundary_cycles(co: np.ndarray, loop_verts: np.ndarray, loop_face: np.ndarray, nxt: np.ndarray,
                    loop_edges: np.ndarray, edge_face_count: np.ndarray, flipped: np.ndarray | None = None
                    ) -> tuple[list[BoundaryCycle], dict[str, Any]]:
    """Closed boundary cycles + statistics about open chains (see module doc).

    ``flipped`` (F,) bool: faces that will be reversed before filling (their boundary direction is swapped).
    Boundary edges whose directed walk does not close (rim faces of mixed orientation meet at a vertex) are
    re-assembled as undirected simple loops and oriented like the majority (by length) of their rim faces.
    """
    bl = np.flatnonzero(edge_face_count[loop_edges] == 1)
    stats: dict[str, Any] = {"boundary_edges": int(len(bl)), "open_chains": 0, "open_chain_edges": 0,
                             "split_cycles": 0, "undirected_loops": 0, "mixed_orientation_edges": 0}
    if len(bl) == 0:
        return [], stats
    a = loop_verts[bl]
    b = loop_verts[nxt[bl]]
    if flipped is not None:
        rev = flipped[loop_face[bl]]
        a, b = np.where(rev, b, a), np.where(rev, a, b)
    # hole half-edge runs b -> a (opposite to the rim face)
    start, end = b, a
    succ = _pair_successors(start, end)
    visited = np.zeros(len(bl), dtype=bool)
    cycles: list[BoundaryCycle] = []
    succ_l = succ.tolist()
    start_l = start.tolist()
    leftover: list[int] = []

    def emit(idx: np.ndarray, verts: np.ndarray) -> None:
        p = co[verts]
        perim = float(np.linalg.norm(p - np.roll(p, -1, axis=0), axis=1).sum())
        cycles.append(BoundaryCycle(verts=verts, faces=loop_face[bl[idx]], loops=bl[idx], perimeter=perim))

    for h0 in range(len(bl)):
        if visited[h0]:
            continue
        walk = []
        h = h0
        while h >= 0 and not visited[h]:
            visited[h] = True
            walk.append(h)
            h = succ_l[h]
        if h != h0:
            if h < 0 or h not in walk:
                leftover.extend(walk)
                continue
            k = walk.index(h)
            leftover.extend(walk[:k])
            walk = walk[k:]
        for part in _split_simple(walk, start_l):
            if len(part) < 3:
                leftover.extend(part)
                continue
            if len(part) != len(walk):
                stats["split_cycles"] += 1
            idx = np.asarray(part, dtype=np.int64)
            emit(idx, start[idx])
    if leftover:
        for idx, verts, mixed in _undirected_loops(np.asarray(leftover, np.int64), start, end, co):
            if idx is None:
                stats["open_chains"] += 1
                stats["open_chain_edges"] += int(len(verts))
                continue
            stats["undirected_loops"] += 1
            stats["mixed_orientation_edges"] += mixed
            emit(idx, verts)
    return cycles, stats


def _undirected_loops(hes: np.ndarray, start: np.ndarray, end: np.ndarray, co: np.ndarray):
    """Yield ``(half_edge_ids, ordered_verts, n_against)`` for every simple undirected loop among the half-edges
    ``hes`` (oriented with the length-weighted majority of their directions), or ``(None, half_edge_ids, 0)`` for
    pieces that are not simple loops (branching / open chains)."""
    from . import meshdata

    s, e = start[hes], end[hes]
    verts, inv = np.unique(np.concatenate([s, e]), return_inverse=True)
    inv = inv.reshape(2, -1)
    lab = meshdata.connected_components(len(verts), inv[0], inv[1])
    elab = lab[inv[0]]
    order = np.argsort(elab, kind="stable")
    bounds = np.flatnonzero(np.r_[True, elab[order][1:] != elab[order][:-1], True])
    for k in range(len(bounds) - 1):
        sel = order[bounds[k]:bounds[k + 1]]
        comp_he = hes[sel]
        pairs = np.stack([start[comp_he], end[comp_he]], axis=1)
        cyc = meshdata.order_loop_vertices(pairs)
        if cyc is None or len(cyc) < 3:
            yield None, comp_he, 0
            continue
        lookup = {}
        for h, (u, v) in zip(comp_he.tolist(), pairs.tolist()):
            lookup[(u, v)] = (h, True)
            lookup[(v, u)] = (h, False)
        n = len(cyc)
        ids = np.empty(n, np.int64)
        agree = np.empty(n, dtype=bool)
        cl = cyc.tolist()
        for i in range(n):
            h, fwd = lookup[(cl[i], cl[(i + 1) % n])]
            ids[i] = h
            agree[i] = fwd
        p = co[cyc]
        seg = np.linalg.norm(p - np.roll(p, -1, axis=0), axis=1)
        if seg[agree].sum() < seg[~agree].sum():
            cyc = cyc[::-1]
            # edge i of the reversed cycle is the old edge (n - 2 - i) mod n
            ids = ids[(np.arange(n)[::-1] - 1) % n]
            agree = ~agree[(np.arange(n)[::-1] - 1) % n]
        yield ids, cyc, int((~agree).sum())


def _pair_successors(start: np.ndarray, end: np.ndarray) -> np.ndarray:
    """Successor half-edge of every half-edge: the k-th half-edge ENDING at a vertex continues with the k-th one
    STARTING there (each half-edge has at most one predecessor, so walks decompose into cycles and chains); -1 when
    the vertex has fewer outgoing than incoming half-edges."""
    n = len(start)
    out_order = np.argsort(start, kind="stable")
    out_sorted = start[out_order]
    in_order = np.argsort(end, kind="stable")
    in_sorted = end[in_order]
    first_in = np.searchsorted(in_sorted, in_sorted, side="left")
    rank = np.empty(n, np.int64)
    rank[in_order] = np.arange(n) - first_in
    v = end
    lo = np.searchsorted(out_sorted, v, side="left")
    hi = np.searchsorted(out_sorted, v, side="right")
    ok = rank < (hi - lo)
    succ = np.full(n, -1, np.int64)
    succ[ok] = out_order[lo[ok] + rank[ok]]
    return succ


def _split_simple(walk: list[int], start: list[int]) -> list[list[int]]:
    """Split a closed half-edge walk that revisits vertices into simple cycles."""
    out: list[list[int]] = []
    stack: list[int] = []
    seen: dict[int, int] = {}
    for h in walk:
        v = start[h]
        if v in seen:
            k = seen[v]
            cyc = stack[k:]
            out.append(cyc)
            for hh in cyc:
                seen.pop(start[hh], None)
            del stack[k:]
        seen[v] = len(stack)
        stack.append(h)
    if stack:
        out.append(stack)
    return out


# ================================================================================================================
# UV islands + hole planning
# ================================================================================================================


def uv_islands(uv: np.ndarray | None, loop_verts: np.ndarray, loop_face: np.ndarray, nxt: np.ndarray,
               loop_edges: np.ndarray, edge_face_count: np.ndarray, n_faces: int) -> np.ndarray:
    """UV island label per face (faces joined across manifold edges whose two endpoint UVs match), (F,) int64."""
    from . import meshdata

    if uv is None:
        return np.zeros(n_faces, np.int64)
    manifold_loops = np.flatnonzero(edge_face_count[loop_edges] == 2)
    order = np.argsort(loop_edges[manifold_loops], kind="stable")
    ml = manifold_loops[order].reshape(-1, 2)
    l1, l2 = ml[:, 0], ml[:, 1]
    va1, vb1 = loop_verts[l1], loop_verts[nxt[l1]]
    uva1, uvb1 = uv[l1], uv[nxt[l1]]
    same_dir = loop_verts[l2] == va1
    uva2 = np.where(same_dir[:, None], uv[l2], uv[nxt[l2]])
    uvb2 = np.where(same_dir[:, None], uv[nxt[l2]], uv[l2])
    del vb1
    ok = (np.abs(uva1 - uva2).max(1) < UV_EPS) & (np.abs(uvb1 - uvb2).max(1) < UV_EPS)
    f1, f2 = loop_face[l1[ok]], loop_face[l2[ok]]
    return meshdata.connected_components(n_faces, f1, f2)


@dataclass
class HolePlan:
    cycle: BoundaryCycle
    fill: bool
    reason: str = ""
    uvs: np.ndarray | None = None
    """(n, 2) float32 UV per rim vertex (from one island)."""
    island: int = -1
    material: int = 0
    normal: np.ndarray = field(default_factory=lambda: np.zeros(3))
    target_edge: float = 0.0


def plan_holes(cycles: list[BoundaryCycle], *, co: np.ndarray, face_normals: np.ndarray, flipped: np.ndarray | None,
               uv: np.ndarray | None, islands: np.ndarray, loop_verts: np.ndarray, nxt: np.ndarray,
               materials: np.ndarray | None, max_edges: int, max_perimeter: float) -> list[HolePlan]:
    """Decide which cycles to fill and prepare their UVs / material / reference normal."""
    plans: list[HolePlan] = []
    for cyc in cycles:
        n = cyc.edges
        if n < 3:
            plans.append(HolePlan(cyc, False, "too small"))
            continue
        if n > max_edges:
            plans.append(HolePlan(cyc, False, f"more than {max_edges} edges"))
            continue
        if cyc.perimeter > max_perimeter:
            plans.append(HolePlan(cyc, False, "perimeter too large"))
            continue
        p = co[cyc.verts]
        seg = np.linalg.norm(p - np.roll(p, -1, axis=0), axis=1)
        normal = _cycle_normal(p, face_normals[cyc.faces], None if flipped is None else flipped[cyc.faces], seg)
        plan = HolePlan(cyc, True, normal=normal, target_edge=float(seg.mean()))
        if materials is not None and len(materials):
            plan.material = int(np.bincount(materials[cyc.faces]).argmax())
        if uv is not None:
            plan.island, plan.uvs = _rim_uvs(cyc, uv, islands, loop_verts, nxt, seg)
        plans.append(plan)
    return plans


def _newell(p: np.ndarray) -> np.ndarray:
    """Newell vector of a closed polygon (twice its projected area vector; follows the cycle orientation)."""
    c = p - p.mean(0)
    return np.cross(c, np.roll(c, -1, axis=0)).sum(0)


def _cycle_normal(p: np.ndarray, rim_normals: np.ndarray, rim_flipped: np.ndarray | None, seg: np.ndarray
                  ) -> np.ndarray:
    """Reference normal of a hole: the cycle's own Newell normal (it follows the winding the patch must have, also
    for holes wrapping around a thin edge where rim faces on both sides cancel); the length-weighted rim-face
    normal only when the cycle spans (almost) no area."""
    nw = _newell(p)
    ln = float(np.linalg.norm(nw))
    perim = float(seg.sum())
    if ln > 1e-6 * perim * perim:
        return nw / ln
    fn = rim_normals.copy()
    if rim_flipped is not None:
        fn[rim_flipped] *= -1.0
    avg = (fn * seg[:, None]).sum(0)
    la = float(np.linalg.norm(avg))
    return avg / la if la > 0 else np.array([0.0, 0.0, 1.0])


def _rim_uvs(cyc: BoundaryCycle, uv: np.ndarray, islands: np.ndarray, loop_verts: np.ndarray, nxt: np.ndarray,
             seg: np.ndarray) -> tuple[int, np.ndarray]:
    """Dominant UV island of the rim faces and one UV per rim vertex taken from that island."""
    isl = islands[cyc.faces]
    weights = np.bincount(isl, weights=seg)
    island = int(weights.argmax())
    n = cyc.edges
    out = np.zeros((n, 2), np.float32)
    have = np.zeros(n, dtype=bool)
    # edge i = verts[i] -> verts[i+1]; its rim face holds both vertices as corners `loops[i]` and `nxt[loops[i]]`
    for i in range(n):
        if isl[i] != island:
            continue
        lp = int(cyc.loops[i])
        for corner in (lp, int(nxt[lp])):
            v = int(loop_verts[corner])
            for k in (i, (i + 1) % n):
                if cyc.verts[k] == v and not have[k]:
                    out[k] = uv[corner]
                    have[k] = True
    if not have.any():
        return island, out
    idx = np.flatnonzero(have)
    for k in np.flatnonzero(~have):
        d = np.minimum(np.abs(idx - k), n - np.abs(idx - k))  # nearest along the cycle
        out[k] = out[idx[int(np.argmin(d))]]
    return island, out


# ================================================================================================================
# triangulation
# ================================================================================================================


def triangulate_cycle(p: np.ndarray, normal: np.ndarray) -> np.ndarray:
    """Advancing-front ear clipping of a closed 3D polygon (n, 3): (n - 2, 3) int64 indices into ``p``.

    The vertex with the smallest interior angle (around ``normal``; the cycle is counter-clockwise seen from the
    normal's side) is clipped first; ears that would contain another rim vertex (in the projection onto the plane
    perpendicular to ``normal``) are postponed while alternatives exist.
    """
    n = len(p)
    if n < 3:
        return np.zeros((0, 3), np.int64)
    if n == 3:
        return np.array([[0, 1, 2]], np.int64)
    nrm = np.asarray(normal, dtype=np.float64)
    u = np.cross(nrm, [1.0, 0.0, 0.0] if abs(nrm[0]) < 0.9 else [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    w = np.cross(nrm, u)
    q = np.stack([p @ u, p @ w], axis=1)
    ring = list(range(n))
    tris: list[tuple[int, int, int]] = []

    def angle(i_prev: int, i: int, i_next: int) -> float:
        e1 = p[i_prev] - p[i]
        e2 = p[i_next] - p[i]
        c = float(np.dot(np.cross(e2, e1), nrm))
        d = float(np.dot(e2, e1))
        a = math.atan2(c, d)
        return a + 2.0 * math.pi if a < 0 else a

    def contains_other(i_prev: int, i: int, i_next: int) -> bool:
        a, b, c = q[i_prev], q[i], q[i_next]
        area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if area <= 0:
            return True
        for j in ring:
            if j in (i_prev, i, i_next):
                continue
            pt = q[j]
            d1 = (b[0] - a[0]) * (pt[1] - a[1]) - (b[1] - a[1]) * (pt[0] - a[0])
            d2 = (c[0] - b[0]) * (pt[1] - b[1]) - (c[1] - b[1]) * (pt[0] - b[0])
            d3 = (a[0] - c[0]) * (pt[1] - c[1]) - (a[1] - c[1]) * (pt[0] - c[0])
            if d1 > 0 and d2 > 0 and d3 > 0:
                return True
        return False

    angles = {i: angle(ring[k - 1], i, ring[(k + 1) % len(ring)]) for k, i in enumerate(ring)}
    while len(ring) > 3:
        m = len(ring)
        order = sorted(range(m), key=lambda k: angles[ring[k]])
        chosen = order[0]
        if m <= 64:  # containment test is O(n) per candidate: only for small fronts
            for k in order[: min(m, 8)]:
                if angles[ring[k]] < math.pi and not contains_other(ring[k - 1], ring[k], ring[(k + 1) % m]):
                    chosen = k
                    break
        i_prev, i, i_next = ring[chosen - 1], ring[chosen], ring[(chosen + 1) % m]
        tris.append((i_prev, i, i_next))
        ring.pop(chosen)
        m -= 1
        kp = (chosen - 1) % m
        kn = chosen % m
        angles[ring[kp]] = angle(ring[kp - 1], ring[kp], ring[(kp + 1) % m])
        angles[ring[kn]] = angle(ring[kn - 1], ring[kn], ring[(kn + 1) % m])
    tris.append((ring[0], ring[1], ring[2]))
    return np.asarray(tris, dtype=np.int64)


# ================================================================================================================
# fairing (least-squares bi-Laplacian)
# ================================================================================================================


def fair_positions(co: np.ndarray, nbrs: list[np.ndarray], free: np.ndarray, rows: np.ndarray,
                   max_iter: int = FAIR_MAX_ITER, tol: float = FAIR_TOL) -> np.ndarray:
    """Minimise sum_{i in rows} |U(x)_i|^2 (uniform umbrella operator) over the ``free`` vertices.

    ``co`` (m, 3) local positions; ``nbrs[i]`` local neighbour ids of every local vertex in ``rows`` (rows must
    have their FULL 1-ring present locally); ``free`` (m,) bool unknowns. Returns new positions (m, 3).
    Solved with conjugate gradients on the normal equations (CGLS), all three coordinates at once.
    """
    m = len(co)
    free_idx = np.flatnonzero(free)
    if len(free_idx) == 0:
        return co.copy()
    col_of = np.full(m, -1, np.int64)
    col_of[free_idx] = np.arange(len(free_idx))
    r_list, c_list, v_list = [], [], []
    for r, i in enumerate(rows.tolist()):
        nb = nbrs[i]
        deg = len(nb)
        if deg == 0:
            continue
        r_list.append(np.full(deg + 1, r, np.int64))
        c_list.append(np.concatenate([[i], nb]))
        v_list.append(np.concatenate([[-1.0], np.full(deg, 1.0 / deg)]))
    rr = np.concatenate(r_list)
    cc = np.concatenate(c_list)
    vv = np.concatenate(v_list)
    n_rows = len(rows)
    is_free = col_of[cc] >= 0
    # constant part from fixed vertices
    c_fixed = np.zeros((n_rows, 3))
    fx = ~is_free
    for k in range(3):
        c_fixed[:, k] = np.bincount(rr[fx], weights=vv[fx] * co[cc[fx], k], minlength=n_rows)
    fr, fc, fv = rr[is_free], col_of[cc[is_free]], vv[is_free]
    n_free = len(free_idx)

    def mat(x: np.ndarray) -> np.ndarray:  # M x : (n_free, 3) -> (n_rows, 3)
        out = np.empty((n_rows, 3))
        for k in range(3):
            out[:, k] = np.bincount(fr, weights=fv * x[fc, k], minlength=n_rows)
        return out

    def mat_t(y: np.ndarray) -> np.ndarray:  # M^T y : (n_rows, 3) -> (n_free, 3)
        out = np.empty((n_free, 3))
        for k in range(3):
            out[:, k] = np.bincount(fc, weights=fv * y[fr, k], minlength=n_free)
        return out

    x = co[free_idx].astype(np.float64).copy()
    r = -c_fixed - mat(x)
    s = mat_t(r)
    p = s.copy()
    gamma = (s * s).sum(0)
    gamma0 = gamma.copy()
    for _ in range(max_iter):
        if np.all(gamma <= tol * np.maximum(gamma0, 1e-30)):
            break
        q = mat(p)
        qq = (q * q).sum(0)
        alpha = np.where(qq > 0, gamma / np.maximum(qq, 1e-300), 0.0)
        x += p * alpha
        r -= q * alpha
        s = mat_t(r)
        gamma_new = (s * s).sum(0)
        beta = np.where(gamma > 0, gamma_new / np.maximum(gamma, 1e-300), 0.0)
        p = s + p * beta
        gamma = gamma_new
    out = co.astype(np.float64).copy()
    out[free_idx] = x
    return out


# ================================================================================================================
# bmesh patch creation
# ================================================================================================================


@dataclass
class FillReport:
    filled: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    new_vertices: int = 0
    new_faces: int = 0
    timings: dict[str, float] = field(default_factory=dict)

    def summary(self, limit: int = 20) -> dict[str, Any]:
        reasons: dict[str, int] = {}
        for s in self.skipped:
            reasons[s["reason"]] = reasons.get(s["reason"], 0) + 1
        largest = sorted(self.filled, key=lambda h: -h["edges"])[:limit]
        return {"filled": len(self.filled), "skipped": len(self.skipped), "skipped_by_reason": reasons,
                "new_vertices": self.new_vertices, "new_faces": self.new_faces,
                "largest_filled": largest,
                "largest_skipped": sorted(self.skipped, key=lambda h: -h["edges"])[:limit],
                "timings_s": {k: round(v, 3) for k, v in self.timings.items()}}


def fill_holes(bm, plans: list[HolePlan], *, diag: float, uv_layer_name: str | None = None) -> FillReport:
    """Create the patches of ``plans`` (``fill=True``) in ``bm`` (BMesh whose vertex order matches the vertex ids
    used by the plans). Adds/uses the float vertex layer ``amt_filled``.

    Patches are built as small numpy meshes (:func:`build_patch`) and written with ``verts.new``/``faces.new`` only:
    bmesh operators such as ``subdivide_edges`` invalidate every Python element wrapper in Blender 5.x.
    """
    report = FillReport()
    t0 = time.perf_counter()
    uv_layer = bm.loops.layers.uv.get(uv_layer_name) if uv_layer_name else bm.loops.layers.uv.active
    filled_layer = bm.verts.layers.float.get(FILLED_ATTRIBUTE) or bm.verts.layers.float.new(FILLED_ATTRIBUTE)
    bm.verts.ensure_lookup_table()
    # resolve all rims first: adding vertices makes the index lookup table stale (wrappers stay valid)
    rims = [[bm.verts[int(i)] for i in plan.cycle.verts] if plan.fill else None for plan in plans]
    for plan, rim in zip(plans, rims):
        cyc = plan.cycle
        info = {"edges": cyc.edges, "perimeter_pct_diag": round(100.0 * cyc.perimeter / diag, 4) if diag else None}
        if not plan.fill or rim is None:
            report.skipped.append({**info, "reason": plan.reason})
            continue
        try:
            result = _fill_one(bm, plan, rim, uv_layer, filled_layer)
        except (ValueError, RuntimeError, ReferenceError, IndexError) as exc:  # leave the hole
            report.skipped.append({**info, "reason": f"fill failed: {exc}"})
            continue
        if isinstance(result, str):
            report.skipped.append({**info, "reason": result})
            continue
        new_verts, new_faces, center = result
        report.new_vertices += new_verts
        report.new_faces += new_faces
        report.filled.append({**info, "new_faces": new_faces, "new_vertices": new_verts, "center": center})
    report.timings["fill"] = time.perf_counter() - t0
    return report


def _fill_one(bm, plan: HolePlan, rim: list, uv_layer, filled_layer) -> tuple[int, int, list[float]] | str:
    """Build the patch of one hole and write it into ``bm``; returns ``(new_vertices, new_faces, rim centre)`` or a
    skip reason."""
    if len(set(rim)) != len(rim):
        return "rim visits a vertex twice"
    rim, rim_uv = _remove_teeth(bm, list(rim), plan.uvs)
    if len(rim) < 3:
        return "hole closed by removing rim teeth"
    rim_set = set(rim)
    rim_co = np.array([v.co[:] for v in rim], dtype=np.float64)
    outer = []
    for v in rim:
        ring = [e.other_vert(v).co[:] for e in v.link_edges if e.other_vert(v) not in rim_set]
        outer.append(np.array(ring, dtype=np.float64).reshape(-1, 3))
    patch = build_patch(rim_co, rim_uv, plan.normal, plan.target_edge, outer)
    if patch is None:
        return "patch folded (non-planar rim)"
    pos, uvs, tris = patch
    n = len(rim)
    created = []
    faces = []
    try:
        for p in pos[n:].tolist():
            v = bm.verts.new(p)
            v[filled_layer] = 1.0
            created.append(v)
        verts = rim + created
        for a, b, c in tris.tolist():
            f = bm.faces.new((verts[a], verts[b], verts[c]))
            f.material_index = plan.material
            f.smooth = True
            faces.append(f)
            if uv_layer is not None and uvs is not None:
                for loop, k in zip(f.loops, (a, b, c)):
                    loop[uv_layer].uv = (float(uvs[k, 0]), float(uvs[k, 1]))
    except ValueError:
        for f in faces:
            if f.is_valid:
                bm.faces.remove(f)
        for v in created:
            if v.is_valid:
                bm.verts.remove(v)
        raise
    return len(created), len(faces), [round(float(x), 5) for x in rim_co.mean(0)]


def _remove_teeth(bm, rim: list, uvs: np.ndarray | None) -> tuple[list, np.ndarray | None]:
    """Delete rim triangles made of three consecutive rim vertices ("teeth" with two boundary edges; the ear that
    fills them would duplicate the face) and drop their middle vertex from the cycle."""
    import bmesh

    uv = None if uvs is None else np.array(uvs, copy=True)
    changed = True
    while changed and len(rim) > 3:
        changed = False
        n = len(rim)
        for i in range(n):
            v, a, c = rim[i], rim[i - 1], rim[(i + 1) % n]
            tooth = next((f for f in v.link_faces if len(f.verts) == 3 and a in f.verts and c in f.verts), None)
            if tooth is None:
                continue
            bmesh.ops.delete(bm, geom=[tooth], context="FACES")
            rim.pop(i)
            if uv is not None:
                uv = np.delete(uv, i, axis=0)
            changed = True
            break
    return rim, uv


# ================================================================================================================
# patch mesher (numpy, per hole)
# ================================================================================================================


def build_patch(rim: np.ndarray, rim_uv: np.ndarray | None, normal: np.ndarray, target: float,
                outer: list[np.ndarray] | None = None
                ) -> tuple[np.ndarray, np.ndarray | None, np.ndarray] | None:
    """Triangulate, refine and fair the patch of one boundary cycle.

    Args:
        rim: (n, 3) cycle positions (counter-clockwise seen from ``normal``); rim_uv: (n, 2) or ``None``.
        target: wanted edge length (mean rim edge length).
        outer: per rim vertex, the positions of its neighbours OUTSIDE the patch (fixed ring for the fairing).
    Returns:
        ``(positions (V, 3), uvs (V, 2) | None, triangles (T, 3))`` with the rim as the first ``n`` vertices, or
        ``None`` when the patch folds against the rim normal (rejected).
    """
    n = len(rim)
    nrm = np.asarray(normal, dtype=np.float64)
    tris = triangulate_cycle(rim, nrm)
    pos = np.array(rim, dtype=np.float64)
    uvs = None if rim_uv is None else np.array(rim_uv, dtype=np.float64)
    nearest_uv = uvs is not None and not _uv_compact(rim, uvs)
    target = max(float(target), 1e-12)
    for _ in range(REFINE_ROUNDS):
        pos, uvs, tris, split = _split_long_edges(pos, uvs, tris, REFINE_FACTOR * target)
        tris = _flip_edges(pos, tris)
        if pos.shape[0] > n:
            pos = _relax_patch(pos, tris, n, nrm, RELAX_ITERATIONS)
        if not split:
            break
    if pos.shape[0] > n:
        pos = _fair_patch(pos, tris, n, outer)
        tris = _flip_edges(pos, tris)
    if uvs is not None and nearest_uv and pos.shape[0] > n:
        d = ((pos[n:, None, :] - pos[None, :n, :]) ** 2).sum(-1)
        uvs[n:] = uvs[np.argmin(d, axis=1)]
    # quality gate: area facing against the rim normal
    fn = np.cross(pos[tris[:, 1]] - pos[tris[:, 0]], pos[tris[:, 2]] - pos[tris[:, 0]])
    area = 0.5 * np.linalg.norm(fn, axis=1)
    bad = area[(fn @ nrm) < 0].sum()
    if area.sum() <= 0 or bad > FLIPPED_PATCH_MAX * area.sum():
        return None
    return pos, (None if uvs is None else uvs.astype(np.float32)), tris


def _uv_compact(rim: np.ndarray, uv: np.ndarray) -> bool:
    """True when the rim's UV polygon has a sane scale relative to its 3D size (no wrap across the atlas)."""
    seg3 = np.linalg.norm(rim - np.roll(rim, -1, axis=0), axis=1)
    seg2 = np.linalg.norm(uv - np.roll(uv, -1, axis=0), axis=1)
    ok = seg3 > 0
    if not ok.any():
        return False
    ratio = float(np.median(seg2[ok] / seg3[ok]))
    if ratio <= 0:
        return False
    span3 = float(np.linalg.norm(rim.max(0) - rim.min(0)))
    span2 = float(np.linalg.norm(uv.max(0) - uv.min(0)))
    return span2 <= 3.0 * ratio * span3 + 1e-6


def _edges_of(tris: np.ndarray, n_verts: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(edge vertex pairs (E, 2), edge id per triangle corner (T, 3), faces per edge (E,))``; corner k is the
    edge tris[k] -> tris[k + 1]."""
    a = tris.reshape(-1)
    b = np.roll(tris, -1, axis=1).reshape(-1)
    key = np.minimum(a, b) * n_verts + np.maximum(a, b)
    uniq, inv = np.unique(key, return_inverse=True)
    inv = inv.reshape(-1)
    edges = np.stack([uniq // n_verts, uniq % n_verts], axis=1)
    count = np.bincount(inv, minlength=len(uniq))
    return edges, inv.reshape(-1, 3), count


def _split_long_edges(pos: np.ndarray, uvs: np.ndarray | None, tris: np.ndarray, max_len: float):
    """Red-green refinement: split every interior edge longer than ``max_len`` at its midpoint (UVs interpolated).

    Returns ``(pos, uvs, tris, split_any)``.
    """
    nv = len(pos)
    edges, tri_edges, count = _edges_of(tris, nv)
    length = np.linalg.norm(pos[edges[:, 0]] - pos[edges[:, 1]], axis=1)
    split = (count == 2) & (length > max_len)
    if not split.any():
        return pos, uvs, tris, False
    sid = np.flatnonzero(split)
    mid = np.full(len(edges), -1, np.int64)
    mid[sid] = nv + np.arange(len(sid))
    pos = np.concatenate([pos, 0.5 * (pos[edges[sid, 0]] + pos[edges[sid, 1]])])
    if uvs is not None:
        uvs = np.concatenate([uvs, 0.5 * (uvs[edges[sid, 0]] + uvs[edges[sid, 1]])])
    out: list[tuple[int, int, int]] = []
    for (a, b, c), (mab, mbc, mca) in zip(tris.tolist(), mid[tri_edges].tolist()):
        k = (mab >= 0) + (mbc >= 0) + (mca >= 0)
        if k == 0:
            out.append((a, b, c))
        elif k == 3:
            out += [(a, mab, mca), (mab, b, mbc), (mca, mbc, c), (mab, mbc, mca)]
        elif k == 1:
            if mab >= 0:
                out += [(a, mab, c), (mab, b, c)]
            elif mbc >= 0:
                out += [(b, mbc, a), (mbc, c, a)]
            else:
                out += [(c, mca, b), (mca, a, b)]
        else:  # two split edges: rotate so that the unsplit edge is (z, x)
            if mca < 0:
                x, y, z, m1, m2 = a, b, c, mab, mbc
            elif mab < 0:
                x, y, z, m1, m2 = b, c, a, mbc, mca
            else:
                x, y, z, m1, m2 = c, a, b, mca, mab
            out.append((m1, y, m2))
            # remaining quad x, m1, m2, z: split along the shorter diagonal
            if float(np.sum((pos[x] - pos[m2]) ** 2)) <= float(np.sum((pos[m1] - pos[z]) ** 2)):
                out += [(x, m1, m2), (x, m2, z)]
            else:
                out += [(x, m1, z), (m1, m2, z)]
    return pos, uvs, np.asarray(out, dtype=np.int64), True


def _flip_edges(pos: np.ndarray, tris: np.ndarray, passes: int = 4) -> np.ndarray:
    """Delaunay-style edge flips (opposite angles sum > pi) that do not fold the surface; rim edges never flip.

    Vectorised per pass: all candidate edges are evaluated at once, then a greedy independent set (no shared
    triangle, largest angle excess first) is flipped.
    """
    tris = np.array(tris, dtype=np.int64, copy=True)
    nv = len(pos)
    for _ in range(passes):
        if len(tris) < 2:
            break
        edges, tri_edges, count = _edges_of(tris, nv)
        inc_edge = tri_edges.reshape(-1)
        order = np.argsort(inc_edge, kind="stable")
        inc_sorted = inc_edge[order]
        interior = count[inc_sorted] == 2
        pairs = order[interior].reshape(-1, 2)  # corner ids (3 * face + k) of the two faces of each edge
        if len(pairs) == 0:
            break
        fa, ka = pairs[:, 0] // 3, pairs[:, 0] % 3
        fb, kb = pairs[:, 1] // 3, pairs[:, 1] % 3
        x = tris[fa, ka]
        y = tris[fa, (ka + 1) % 3]
        c = tris[fa, (ka + 2) % 3]
        d = tris[fb, (kb + 2) % 3]
        px, py, pc, pd = pos[x], pos[y], pos[c], pos[d]
        excess = _angles(px - pc, py - pc) + _angles(px - pd, py - pd) - math.pi
        cand = (excess > 1e-9) & (c != d)
        if not cand.any():
            break
        n_old = np.cross(py - px, pc - px) + np.cross(px - py, pd - py)
        n1 = np.cross(px - pc, pd - pc)
        n2 = np.cross(pd - pc, py - pc)
        cand &= ((n1 * n_old).sum(1) > 0) & ((n2 * n_old).sum(1) > 0)
        new_key = np.minimum(c, d) * nv + np.maximum(c, d)
        old_keys = edges[:, 0] * nv + edges[:, 1]
        cand &= ~np.isin(new_key, old_keys)
        idx = np.flatnonzero(cand)
        if len(idx) == 0:
            break
        idx = idx[np.argsort(-excess[idx], kind="stable")]
        used = np.zeros(len(tris), dtype=bool)
        chosen = []
        for i, a_face, b_face in zip(idx.tolist(), fa[idx].tolist(), fb[idx].tolist()):
            if used[a_face] or used[b_face]:
                continue
            used[a_face] = used[b_face] = True
            chosen.append(i)
        sel = np.asarray(chosen, dtype=np.int64)
        tris[fa[sel]] = np.stack([c[sel], x[sel], d[sel]], axis=1)
        tris[fb[sel]] = np.stack([c[sel], d[sel], y[sel]], axis=1)
    return tris


def _angles(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Angles between row vectors (radians), 0 for degenerate rows."""
    nu = np.linalg.norm(u, axis=1)
    nv = np.linalg.norm(v, axis=1)
    den = nu * nv
    cos = np.where(den > 0, (u * v).sum(1) / np.where(den > 0, den, 1.0), 1.0)
    return np.arccos(np.clip(cos, -1.0, 1.0))


def _relax_patch(pos: np.ndarray, tris: np.ndarray, n_rim: int, normal: np.ndarray, iterations: int) -> np.ndarray:
    """Tangential uniform-Laplacian relaxation of the interior patch vertices (rim fixed)."""
    edges, _, _ = _edges_of(tris, len(pos))
    rows = np.concatenate([edges[:, 0], edges[:, 1]])
    cols = np.concatenate([edges[:, 1], edges[:, 0]])
    nv = len(pos)
    deg = np.bincount(rows, minlength=nv).astype(np.float64)
    has = deg > 0
    free = (np.arange(nv) >= n_rim) & has
    pos = pos.copy()
    for _ in range(iterations):
        mean = np.zeros_like(pos)
        for k in range(3):
            mean[:, k] = np.bincount(rows, weights=pos[cols, k], minlength=nv)
        mean[has] /= deg[has, None]
        d = mean - pos
        d -= np.outer(d @ normal, normal)
        pos[free] += 0.5 * d[free]
    return pos


def _fair_patch(pos: np.ndarray, tris: np.ndarray, n_rim: int, outer: list[np.ndarray] | None) -> np.ndarray:
    """Bi-Laplacian fairing of the interior vertices; rim + its outer ring (``outer``) fixed."""
    nv = len(pos)
    edges, _, _ = _edges_of(tris, nv)
    nbr_lists: list[list[int]] = [[] for _ in range(nv)]
    for a, b in edges.tolist():
        nbr_lists[a].append(b)
        nbr_lists[b].append(a)
    extra: list[np.ndarray] = []
    if outer is not None:
        for i, ring in enumerate(outer[:n_rim]):
            for p in ring:
                nbr_lists[i].append(nv + len(extra))
                extra.append(p)
    co = np.concatenate([pos, np.asarray(extra, dtype=np.float64).reshape(-1, 3)]) if extra else pos
    nbrs = [np.asarray(lst, dtype=np.int64) for lst in nbr_lists] + [np.zeros(0, np.int64)] * len(extra)
    free = np.zeros(len(co), dtype=bool)
    free[n_rim:nv] = True
    new = fair_positions(co, nbrs, free, np.arange(nv, dtype=np.int64))
    return new[:nv]
