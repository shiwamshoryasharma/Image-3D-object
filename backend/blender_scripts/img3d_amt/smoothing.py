"""Pure-numpy smoothing / fairing / curvature primitives for triangle meshes (no bpy; Python 3.11, numpy >= 1.24).

Shared by ``face_refinement`` (face.py, symmetry.py) and ``topology_tuning`` (topology.py). Everything works on plain
arrays: positions ``co`` (V, 3) float64, triangles ``tris`` (T, 3) int64 and a :class:`MeshTopology` built once per
mesh state (rebuild it after the connectivity changes; moving vertices does not invalidate it).

Algorithms
* Bilateral normal filtering (Zheng et al. 2011, "Bilateral Normal Filtering for Mesh Denoising"): face normals are
  averaged over edge-adjacent faces with spatial (centroid distance) x range (normal difference) Gaussian weights;
  the vertices are then moved to agree with the filtered normals (Sun et al. 2007 iterative update,
  :func:`vertices_from_normals`). Creases survive because normals across a crease have a large range distance.
* Taubin lambda|mu smoothing (low-pass without shrinkage) with per-vertex weights in [0, 1] and an optional
  ``active`` vertex subset (cost proportional to the subset - used for eyes / creases / new vertices).
* Constrained fairing (:func:`fair`): membrane (order 1, sum of squared edge vectors) or thin-plate (order 2, sum of
  squared umbrella Laplacians) energy minimised over the free vertices with all others fixed, solved with conjugate
  gradients on the normal equations (CGLS). Fills pits / removes blobs while matching the surrounding surface.
* Curvature / roughness: umbrella operator, signed convexity, dihedral angles + per-vertex stats, high-pass residual.

Conventions: ``adj`` = CSR vertex adjacency ``(indptr (V+1,), indices)`` int64; ``weights`` (V,) in [0, 1] scale
every displacement (0 = vertex fixed); ``locked`` (V,) bool vertices never move. All functions return new arrays and
never modify their inputs.

Performance (Blender 5.2 numpy, 1.9M triangles / 0.9M vertices): :func:`build_topology` ~1.5 s, one bilateral
normal pass ~0.3 s, one vertex-update pass ~0.4 s, one Taubin pass (lambda + mu) ~0.25 s.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)

# ================================================================================================================
# topology
# ================================================================================================================


@dataclass
class MeshTopology:
    """Connectivity of a triangle mesh (immutable while only vertex positions change).

    Attributes:
        n_verts: vertex count ``V``.
        tris: (T, 3) int64 triangles.
        edges: (E, 2) int64 unique edges (``lo < hi`` when built here; Blender order when passed in).
        corner_edges: (T, 3) int64 edge id of corner ``k`` (edge ``tris[:, k] -> tris[:, (k + 1) % 3]``).
        edge_face_count: (E,) int64 faces per edge (1 boundary, 2 manifold, 3+ non-manifold, 0 wire).
        adj_indptr / adj_indices: CSR vertex adjacency (both directions of every edge).
        adj_rows: (2E,) int64 row (source vertex) of each ``adj_indices`` entry.
        face_pairs: (M, 2) int64 the two faces of every manifold edge.
        pair_edges: (M,) int64 edge id of each face pair.
        locked: (V,) bool vertices on boundary / non-manifold / wire edges or without faces (keep them fixed).
    """

    n_verts: int
    tris: np.ndarray
    edges: np.ndarray
    corner_edges: np.ndarray
    edge_face_count: np.ndarray
    adj_indptr: np.ndarray
    adj_indices: np.ndarray
    adj_rows: np.ndarray
    face_pairs: np.ndarray
    pair_edges: np.ndarray
    locked: np.ndarray
    _degree: np.ndarray | None = field(default=None, repr=False)

    @property
    def adj(self) -> tuple[np.ndarray, np.ndarray]:
        """``(indptr, indices)`` vertex adjacency."""
        return self.adj_indptr, self.adj_indices

    @property
    def degree(self) -> np.ndarray:
        """(V,) int64 number of neighbours."""
        if self._degree is None:
            self._degree = np.diff(self.adj_indptr)
        return self._degree

    @property
    def n_faces(self) -> int:
        return int(len(self.tris))

    def neighbors(self, v: int) -> np.ndarray:
        return self.adj_indices[self.adj_indptr[v]:self.adj_indptr[v + 1]]


def boundary_vertices(topo: MeshTopology) -> np.ndarray:
    """(V,) bool vertices on open (single-face) or wire edges - real mesh borders (holes), unlike non-manifold
    junctions which may move with their surface."""
    out = np.zeros(topo.n_verts, dtype=bool)
    border = topo.edge_face_count <= 1
    if border.any():
        out[topo.edges[border].reshape(-1)] = True
    return out


def build_topology(tris: np.ndarray, n_verts: int, *, edges: np.ndarray | None = None,
                   corner_edges: np.ndarray | None = None) -> MeshTopology:
    """Build :class:`MeshTopology` from triangles (T, 3).

    ``edges`` (E, 2) + ``corner_edges`` (T, 3) may be passed from Blender (``mesh.edges`` / loop ``edge_index`` of an
    all-triangle mesh) to skip the ``np.unique`` edge extraction (~0.7 s at 1.9M triangles).
    """
    tris = np.ascontiguousarray(tris, dtype=np.int64).reshape(-1, 3)
    n_verts = int(n_verts)
    if edges is None or corner_edges is None:
        a = tris.reshape(-1)
        b = tris[:, [1, 2, 0]].reshape(-1)
        lo = np.minimum(a, b)
        hi = np.maximum(a, b)
        key = lo * np.int64(max(n_verts, 1)) + hi
        uniq, inverse = np.unique(key, return_inverse=True)
        edges = np.stack([uniq // max(n_verts, 1), uniq % max(n_verts, 1)], axis=1).astype(np.int64)
        corner_edges = inverse.reshape(-1, 3).astype(np.int64)
    else:
        edges = np.ascontiguousarray(edges, dtype=np.int64).reshape(-1, 2)
        corner_edges = np.ascontiguousarray(corner_edges, dtype=np.int64).reshape(-1, 3)
    n_edges = len(edges)
    flat_e = corner_edges.reshape(-1)
    count = np.bincount(flat_e, minlength=n_edges).astype(np.int64)
    # manifold face pairs
    manifold = np.nonzero(count == 2)[0]
    if len(manifold):
        order = np.argsort(flat_e, kind="stable")
        indptr = np.zeros(n_edges + 1, np.int64)
        np.cumsum(count, out=indptr[1:])
        start = indptr[manifold]
        face_of = order // 3
        face_pairs = np.stack([face_of[start], face_of[start + 1]], axis=1)
        del order, face_of
    else:
        face_pairs = np.zeros((0, 2), np.int64)
    # vertices that must not move freely: boundary / non-manifold / wire edges, or unused
    locked = np.zeros(n_verts, dtype=bool)
    bad = count != 2
    if bad.any():
        locked[edges[bad].reshape(-1)] = True
    used = np.zeros(n_verts, dtype=bool)
    used[tris.reshape(-1)] = True
    locked |= ~used
    # vertex adjacency (CSR)
    src = np.concatenate([edges[:, 0], edges[:, 1]])
    dst = np.concatenate([edges[:, 1], edges[:, 0]])
    order = np.argsort(src, kind="stable")
    adj_indptr = np.zeros(n_verts + 1, np.int64)
    np.cumsum(np.bincount(src, minlength=n_verts), out=adj_indptr[1:])
    return MeshTopology(n_verts=n_verts, tris=tris, edges=edges, corner_edges=corner_edges, edge_face_count=count,
                        adj_indptr=adj_indptr, adj_indices=dst[order], adj_rows=src[order],
                        face_pairs=face_pairs, pair_edges=manifold.astype(np.int64), locked=locked)


# ================================================================================================================
# basic geometry
# ================================================================================================================


def face_geometry(co: np.ndarray, tris: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(unit normals (T, 3), areas (T,), centroids (T, 3))``; degenerate triangles get a zero normal."""
    p0 = co[tris[:, 0]]
    p1 = co[tris[:, 1]]
    p2 = co[tris[:, 2]]
    n = np.cross(p1 - p0, p2 - p0)
    length = np.linalg.norm(n, axis=1)
    ok = length > 0
    n[ok] /= length[ok, None]
    n[~ok] = 0.0
    return n, 0.5 * length, (p0 + p1 + p2) / 3.0


def vertex_normals(co: np.ndarray, tris: np.ndarray, face_normals: np.ndarray | None = None,
                   face_areas: np.ndarray | None = None) -> np.ndarray:
    """Area-weighted unit vertex normals (V, 3); isolated vertices get (0, 0, 0)."""
    if face_normals is None or face_areas is None:
        face_normals, face_areas, _ = face_geometry(co, tris)
    out = np.zeros((len(co), 3), np.float64)
    flat = tris.reshape(-1)
    for k in range(3):
        out[:, k] = np.bincount(flat, weights=np.repeat(face_normals[:, k] * face_areas, 3), minlength=len(co))
    length = np.linalg.norm(out, axis=1)
    ok = length > 0
    out[ok] /= length[ok, None]
    return out


def edge_lengths(co: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """(E,) edge lengths."""
    return np.linalg.norm(co[edges[:, 0]] - co[edges[:, 1]], axis=1)


def vertex_mean_edge_length(co: np.ndarray, topo: MeshTopology) -> np.ndarray:
    """(V,) mean length of the edges around each vertex (global mean for isolated vertices)."""
    lengths = np.linalg.norm(co[topo.adj_rows] - co[topo.adj_indices], axis=1)
    deg = topo.degree
    total = np.bincount(topo.adj_rows, weights=lengths, minlength=topo.n_verts)
    out = np.full(topo.n_verts, float(lengths.mean()) if len(lengths) else 0.0)
    ok = deg > 0
    out[ok] = total[ok] / deg[ok]
    return out


# ================================================================================================================
# sparse helpers
# ================================================================================================================


def _region_rows(adj: tuple[np.ndarray, np.ndarray], ids: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """CSR rows of the vertices ``ids``: ``(row_local (K,), neighbour_global (K,), degree (len(ids),))``."""
    indptr, indices = adj
    ids = np.asarray(ids, dtype=np.int64)
    starts = indptr[ids]
    deg = indptr[ids + 1] - starts
    total = int(deg.sum())
    row_local = np.repeat(np.arange(len(ids), dtype=np.int64), deg)
    if total == 0:
        return row_local, np.zeros(0, np.int64), deg
    offs = np.arange(total, dtype=np.int64) - np.repeat(np.cumsum(deg) - deg, deg)
    return row_local, indices[np.repeat(starts, deg) + offs], deg


def neighbor_mean(x: np.ndarray, adj: tuple[np.ndarray, np.ndarray], rows: np.ndarray | None = None) -> np.ndarray:
    """Uniform neighbour average of ``x`` (V, k) or (V,); isolated vertices return themselves.

    ``rows`` = repeated row ids (``MeshTopology.adj_rows``) to skip recomputing them.
    """
    indptr, indices = adj
    n = len(x)
    deg = np.diff(indptr)
    if rows is None:
        rows = np.repeat(np.arange(n, dtype=np.int64), deg)
    scalar = x.ndim == 1
    xs = x[:, None] if scalar else x
    out = np.empty(xs.shape, dtype=np.float64)
    ok = deg > 0
    inv = np.where(ok, 1.0 / np.maximum(deg, 1), 0.0)
    for k in range(xs.shape[1]):
        col = np.ascontiguousarray(xs[:, k], dtype=np.float64)
        out[:, k] = np.where(ok, np.bincount(rows, weights=col[indices], minlength=n) * inv, col)
    return out[:, 0] if scalar else out


def umbrella(co: np.ndarray, topo: MeshTopology) -> np.ndarray:
    """Uniform Laplacian ``mean(neighbours) - x`` (V, 3)."""
    return neighbor_mean(co, topo.adj, topo.adj_rows) - co


def smooth_scalar(values: np.ndarray, topo: MeshTopology, iterations: int = 2, lam: float = 0.5,
                  locked: np.ndarray | None = None) -> np.ndarray:
    """Diffuse a per-vertex scalar field (e.g. a mask) over the mesh graph."""
    v = np.asarray(values, dtype=np.float64).copy()
    for _ in range(max(0, int(iterations))):
        delta = neighbor_mean(v, topo.adj, topo.adj_rows) - v
        if locked is not None:
            delta[locked] = 0.0
        v += lam * delta
    return v


def dilate(mask: np.ndarray, topo: MeshTopology, rings: int = 1) -> np.ndarray:
    """Grow a vertex mask by ``rings`` edge hops."""
    m = np.asarray(mask, dtype=bool).copy()
    for _ in range(max(0, int(rings))):
        hit = np.bincount(topo.adj_rows, weights=m[topo.adj_indices].astype(np.float64), minlength=topo.n_verts) > 0
        m |= hit
    return m


def erode(mask: np.ndarray, topo: MeshTopology, rings: int = 1) -> np.ndarray:
    """Shrink a vertex mask by ``rings`` edge hops (vertices with any outside neighbour are removed)."""
    m = np.asarray(mask, dtype=bool).copy()
    for _ in range(max(0, int(rings))):
        outside = np.bincount(topo.adj_rows, weights=(~m)[topo.adj_indices].astype(np.float64),
                              minlength=topo.n_verts) > 0
        m &= ~outside
    return m


def ring_distance(mask: np.ndarray, topo: MeshTopology, max_rings: int) -> np.ndarray:
    """Edge-hop distance (V,) int64 from the vertices in ``mask`` (``max_rings + 1`` = farther / unreachable)."""
    m = np.asarray(mask, dtype=bool)
    dist = np.full(topo.n_verts, max_rings + 1, np.int64)
    dist[m] = 0
    cur = m.copy()
    for r in range(1, max_rings + 1):
        nxt = dilate(cur, topo, 1)
        new = nxt & (dist > max_rings)
        if not new.any():
            break
        dist[new] = r
        cur = nxt
    return dist


def mask_components(mask: np.ndarray, topo: MeshTopology) -> tuple[np.ndarray, int]:
    """Connected components of the vertex subgraph ``mask``: ``(labels (V,) int64, -1 outside; count)``."""
    m = np.asarray(mask, dtype=bool)
    ids = np.nonzero(m)[0]
    labels = np.full(topo.n_verts, -1, np.int64)
    if len(ids) == 0:
        return labels, 0
    e = topo.edges
    keep = m[e[:, 0]] & m[e[:, 1]]
    local = np.full(topo.n_verts, -1, np.int64)
    local[ids] = np.arange(len(ids))
    a, b = local[e[keep, 0]], local[e[keep, 1]]
    parent = np.arange(len(ids), dtype=np.int64)
    while len(a):
        pa, pb = parent[a], parent[b]
        neq = pa != pb
        if not neq.any():
            break
        a, b, pa, pb = a[neq], b[neq], pa[neq], pb[neq]
        np.minimum.at(parent, np.maximum(pa, pb), np.minimum(pa, pb))
        while True:
            grand = parent[parent]
            if np.array_equal(grand, parent):
                break
            parent = grand
    _, lab = np.unique(parent, return_inverse=True)
    labels[ids] = lab.reshape(-1)
    return labels, int(lab.max()) + 1


def smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    """Hermite step: 0 below ``edge0``, 1 above ``edge1`` (works for ``edge0 > edge1`` too, reversed)."""
    if edge1 == edge0:
        return (np.asarray(x) >= edge1).astype(np.float64)
    t = np.clip((np.asarray(x, dtype=np.float64) - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def clamp_displacement(new: np.ndarray, old: np.ndarray, max_disp: np.ndarray | float) -> np.ndarray:
    """Limit ``|new - old|`` per vertex to ``max_disp`` (scalar or (V,))."""
    d = new - old
    length = np.linalg.norm(d, axis=1)
    limit = np.broadcast_to(np.asarray(max_disp, dtype=np.float64), length.shape)
    over = length > limit
    if over.any():
        d[over] *= (limit[over] / length[over])[:, None]
    return old + d


# ================================================================================================================
# smoothing
# ================================================================================================================


def taubin(co: np.ndarray, topo: MeshTopology, *, iterations: int = 10, lam: float = 0.5, mu: float = -0.53,
           weights: np.ndarray | None = None, locked: np.ndarray | None = None, active: np.ndarray | None = None,
           normal_only: np.ndarray | None = None) -> np.ndarray:
    """Taubin lambda|mu smoothing (no shrinkage).

    Args:
        iterations: number of (lambda, mu) pass pairs; ``mu`` negative with ``|mu| > lam``.
        weights: (V,) displacement scale in [0, 1] (``None`` = 1).
        locked: (V,) bool vertices that never move.
        active: vertex ids (or bool mask) to update - the cost is proportional to this subset (their neighbours are
            read, never written). ``None`` = all vertices.
        normal_only: (V, 3) unit normals - keep only the displacement along them (no tangential drift).
    Returns: new (V, 3) positions.
    """
    n = len(co)
    ids = _active_ids(active, n)
    w = _weights_for(ids, weights, locked, n)
    if (ids is not None and len(ids) == 0) or iterations <= 0 or not np.any(w > 0):
        return np.array(co, dtype=np.float64, copy=True)
    if ids is None:
        rows, nbrs, deg = topo.adj_rows, topo.adj_indices, topo.degree
        n_local = n
    else:
        rows, nbrs, deg = _region_rows(topo.adj, ids)
        n_local = len(ids)
    inv_deg = 1.0 / np.maximum(deg, 1).astype(np.float64)
    isolated = deg == 0
    # structure-of-arrays: contiguous 1-D gathers are ~4x faster than row gathers at this scale
    xs = [np.ascontiguousarray(co[:, k], dtype=np.float64) for k in range(3)]
    nrm = None
    if normal_only is not None:
        nn = normal_only if ids is None else normal_only[ids]
        nrm = [np.ascontiguousarray(nn[:, k], dtype=np.float64) for k in range(3)]
    factors = (lam,) if mu == 0.0 else (lam, mu)
    for _ in range(int(iterations)):
        for factor in factors:
            deltas = []
            for k in range(3):
                mean = np.bincount(rows, weights=xs[k][nbrs], minlength=n_local) * inv_deg
                cur = xs[k] if ids is None else xs[k][ids]
                d = mean - cur
                d[isolated] = 0.0
                deltas.append(d)
            if nrm is not None:
                dot = deltas[0] * nrm[0] + deltas[1] * nrm[1] + deltas[2] * nrm[2]
                deltas = [dot * nrm[k] for k in range(3)]
            fw = factor * w
            for k in range(3):
                if ids is None:
                    xs[k] += fw * deltas[k]
                else:
                    xs[k][ids] += fw * deltas[k]
    return np.stack(xs, axis=1)


def laplacian_smooth(co: np.ndarray, topo: MeshTopology, *, iterations: int = 5, lam: float = 0.5,
                     weights: np.ndarray | None = None, locked: np.ndarray | None = None,
                     active: np.ndarray | None = None) -> np.ndarray:
    """Plain umbrella smoothing (shrinks; use for relaxing small regions, :func:`taubin` otherwise)."""
    return taubin(co, topo, iterations=iterations, lam=lam, mu=0.0, weights=weights, locked=locked,
                  active=active)


def _active_ids(active: np.ndarray | None, n: int) -> np.ndarray | None:
    if active is None:
        return None
    a = np.asarray(active)
    if a.dtype == np.bool_:
        return np.nonzero(a)[0].astype(np.int64)
    return a.astype(np.int64)


def _weights_for(ids: np.ndarray | None, weights: np.ndarray | None, locked: np.ndarray | None, n: int) -> np.ndarray:
    w = np.ones(n, np.float64) if weights is None else np.clip(np.asarray(weights, dtype=np.float64), 0.0, 1.0).copy()
    if locked is not None:
        w = np.where(np.asarray(locked, dtype=bool), 0.0, w)
    return w if ids is None else w[ids]


# ================================================================================================================
# bilateral normal filtering + vertex update
# ================================================================================================================


def bilateral_normal_filter(co: np.ndarray, topo: MeshTopology, *, iterations: int = 5, sigma_r: float = 0.35,
                            sigma_s: float | None = None, face_weights: np.ndarray | None = None
                            ) -> tuple[np.ndarray, dict]:
    """Filter face normals with bilateral weights over edge-adjacent faces (see module doc).

    Args:
        iterations: filter passes (each widens the support by one face ring).
        sigma_r: range sigma on ``|n_i - n_j|`` (0.35 ~ creases above ~25-30 degrees are kept sharp).
        sigma_s: spatial sigma on centroid distance (default: mean adjacent-centroid distance).
        face_weights: (T,) in [0, 1] - 0 keeps a face's original normal (blend per face).
    Returns: ``(filtered unit normals (T, 3), info)``.
    """
    n0, areas, cent = face_geometry(co, topo.tris)
    if len(topo.face_pairs) == 0 or iterations <= 0:
        return n0, {"iterations": 0}
    f1 = topo.face_pairs[:, 0]
    f2 = topo.face_pairs[:, 1]
    dc2 = np.zeros(len(f1), np.float64)
    for k in range(3):
        c = np.ascontiguousarray(cent[:, k])
        dc2 += (c[f1] - c[f2]) ** 2
    if sigma_s is None:
        sigma_s = float(np.sqrt(dc2).mean()) or 1.0
    ws = np.exp(-dc2 / (2.0 * sigma_s * sigma_s))
    w12 = ws * areas[f2]  # weight of face f2's normal in f1's average
    w21 = ws * areas[f1]
    del dc2, ws
    ns = [np.ascontiguousarray(n0[:, k]) for k in range(3)]
    n_faces = len(n0)
    inv_2sr2 = 1.0 / (2.0 * sigma_r * sigma_r)
    for _ in range(int(iterations)):
        g1 = [ns[k][f1] for k in range(3)]
        g2 = [ns[k][f2] for k in range(3)]
        dn2 = (g1[0] - g2[0]) ** 2 + (g1[1] - g2[1]) ** 2 + (g1[2] - g2[2]) ** 2
        wr = np.exp(-dn2 * inv_2sr2)
        a12 = w12 * wr
        a21 = w21 * wr
        acc = []
        for k in range(3):
            s = ns[k] * areas
            s += np.bincount(f1, weights=a12 * g2[k], minlength=n_faces)
            s += np.bincount(f2, weights=a21 * g1[k], minlength=n_faces)
            acc.append(s)
        length = np.sqrt(acc[0] ** 2 + acc[1] ** 2 + acc[2] ** 2)
        ok = length > 1e-30
        inv = np.where(ok, 1.0 / np.where(ok, length, 1.0), 0.0)
        ns = [np.where(ok, acc[k] * inv, ns[k]) for k in range(3)]
    n = np.stack(ns, axis=1)
    if face_weights is not None:
        fw = np.clip(np.asarray(face_weights, dtype=np.float64), 0.0, 1.0)[:, None]
        n = n0 * (1.0 - fw) + n * fw
        length = np.linalg.norm(n, axis=1)
        ok = length > 1e-30
        n[ok] /= length[ok, None]
    change = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", n, n0), -1.0, 1.0)))
    valid = areas > 0
    info = {"iterations": int(iterations), "sigma_r": float(sigma_r), "sigma_s": float(sigma_s),
            "normal_change_deg_mean": float(change[valid].mean()) if valid.any() else 0.0,
            "normal_change_deg_p95": float(np.percentile(change[valid], 95)) if valid.any() else 0.0}
    return n, info


def vertices_from_normals(co: np.ndarray, topo: MeshTopology, normals: np.ndarray, *, iterations: int = 10,
                          weights: np.ndarray | None = None, locked: np.ndarray | None = None,
                          max_disp: np.ndarray | float | None = None) -> np.ndarray:
    """Move vertices so the faces match target ``normals`` (T, 3) (Sun et al. 2007, area-weighted).

    ``x_i += sum_f A_f n_f (n_f . (c_f - x_i)) / sum_f A_f`` over the faces of vertex ``i``, ``iterations`` times
    (centroids/areas recomputed every pass). ``max_disp`` caps the total displacement per vertex.
    """
    n_verts = len(co)
    w = _weights_for(None, weights, locked, n_verts)
    if not np.any(w > 0) or iterations <= 0:
        return np.array(co, dtype=np.float64, copy=True)
    tris = topo.tris
    flat = tris.reshape(-1)
    nrm = [np.ascontiguousarray(normals[:, k], dtype=np.float64) for k in range(3)]
    xs = [np.ascontiguousarray(co[:, k], dtype=np.float64) for k in range(3)]
    x0 = [x.copy() for x in xs] if max_disp is not None else None
    limit = None if max_disp is None else np.broadcast_to(np.asarray(max_disp, dtype=np.float64), (n_verts,))
    for _ in range(int(iterations)):
        p = [xs[k][tris] for k in range(3)]  # (T, 3) corner coordinates per axis
        cen = [p[k].mean(axis=1) for k in range(3)]
        e1 = [p[k][:, 1] - p[k][:, 0] for k in range(3)]
        e2 = [p[k][:, 2] - p[k][:, 0] for k in range(3)]
        cx = e1[1] * e2[2] - e1[2] * e2[1]
        cy = e1[2] * e2[0] - e1[0] * e2[2]
        cz = e1[0] * e2[1] - e1[1] * e2[0]
        areas = 0.5 * np.sqrt(cx * cx + cy * cy + cz * cz) + 1e-30
        del e1, e2, cx, cy, cz
        base = (nrm[0] * cen[0] + nrm[1] * cen[1] + nrm[2] * cen[2])[:, None]  # n . c per face
        # signed distance of each corner to its face plane (target normal through the centroid), area-weighted
        d = (base - (nrm[0][:, None] * p[0] + nrm[1][:, None] * p[1] + nrm[2][:, None] * p[2])) * areas[:, None]
        den = np.bincount(flat, weights=np.repeat(areas, 3), minlength=n_verts)
        inv = np.where(den > 0, 1.0 / np.where(den > 0, den, 1.0), 0.0) * w
        for k in range(3):
            num = np.bincount(flat, weights=(d * nrm[k][:, None]).reshape(-1), minlength=n_verts)
            xs[k] += num * inv
        if limit is not None:
            dx = [xs[k] - x0[k] for k in range(3)]
            length = np.sqrt(dx[0] ** 2 + dx[1] ** 2 + dx[2] ** 2)
            over = length > limit
            if over.any():
                scale = limit[over] / length[over]
                for k in range(3):
                    xs[k][over] = x0[k][over] + dx[k][over] * scale
    return np.stack(xs, axis=1)


def denoise(co: np.ndarray, topo: MeshTopology, *, normal_iterations: int = 5, vertex_iterations: int = 10,
            sigma_r: float = 0.35, weights: np.ndarray | None = None, locked: np.ndarray | None = None,
            max_disp: np.ndarray | float | None = None) -> tuple[np.ndarray, dict]:
    """Feature-preserving denoise = :func:`bilateral_normal_filter` + :func:`vertices_from_normals`.

    Returns ``(new positions (V, 3), info)`` where ``info`` has the filter stats and displacement stats.
    """
    face_w = None
    if weights is not None:  # a face is filtered as much as its most-movable vertex allows
        w = np.clip(np.asarray(weights, dtype=np.float64), 0.0, 1.0)
        face_w = w[topo.tris].max(axis=1)
    normals, info = bilateral_normal_filter(co, topo, iterations=normal_iterations, sigma_r=sigma_r,
                                            face_weights=face_w)
    lock = topo.locked if locked is None else (np.asarray(locked, dtype=bool) | topo.locked)
    x = vertices_from_normals(co, topo, normals, iterations=vertex_iterations, weights=weights, locked=lock,
                              max_disp=max_disp)
    disp = np.linalg.norm(x - co, axis=1)
    info.update({"vertex_iterations": int(vertex_iterations), "disp_mean": float(disp.mean()) if len(disp) else 0.0,
                 "disp_p95": float(np.percentile(disp, 95)) if len(disp) else 0.0,
                 "disp_max": float(disp.max()) if len(disp) else 0.0})
    return x, info


def fair_facets(co: np.ndarray, topo: MeshTopology, *, iterations: int = 30, strength: float = 1.0,
                feature_angle: float = 28.0, tolerance: float = 0.35, weights: np.ndarray | None = None,
                locked: np.ndarray | None = None, edge_len: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """Robust low-pass against flat-facet stair-stepping (piecewise-planar strips on smooth surfaces).

    A Taubin low-pass (``iterations`` passes ~ ``sqrt(iterations)`` edges of support) is computed with crease
    vertices (dihedral > ``feature_angle``, dilated by one ring) held fixed; each vertex then takes the low-pass
    displacement attenuated by ``1 / (1 + (|d| / (tolerance * edge_len))^2)``: the small displacements that round
    facet seams are applied fully, the large ones that would erode real details (nose, fingers, ornaments) are
    mostly rejected.

    Returns ``(positions (V, 3), info)``.
    """
    fn, _, _ = face_geometry(co, topo.tris)
    dih = dihedral_angles(fn, topo)
    feature = dilate(vertex_max_dihedral(dih, topo) > feature_angle, topo, 1)
    lock = topo.locked if locked is None else (np.asarray(locked, dtype=bool) | topo.locked)
    w = np.ones(len(co)) if weights is None else np.clip(np.asarray(weights, dtype=np.float64), 0.0, 1.0)
    w = np.where(feature | lock, 0.0, w)
    if not np.any(w > 0):
        return np.array(co, dtype=np.float64, copy=True), {"moved_vertices": 0}
    low = taubin(co, topo, iterations=iterations, lam=0.5, mu=-0.53, weights=w)
    delta = low - co
    if edge_len is None:
        edge_len = vertex_mean_edge_length(co, topo)
    r = np.linalg.norm(delta, axis=1) / np.maximum(tolerance * edge_len, 1e-12)
    atten = 1.0 / (1.0 + r * r)
    atten = smooth_scalar(atten, topo, iterations=2, lam=0.5)
    gain = np.clip(atten * float(strength), 0.0, 1.0) * (w > 0)
    x = co + delta * gain[:, None]
    disp = np.linalg.norm(x - co, axis=1)
    info = {"feature_vertices": int(feature.sum()), "free_vertices": int((w > 0).sum()),
            "disp_mean": float(disp.mean()) if len(disp) else 0.0,
            "disp_p95": float(np.percentile(disp, 95)) if len(disp) else 0.0,
            "disp_max": float(disp.max()) if len(disp) else 0.0,
            "attenuated_frac": float((gain[w > 0] < 0.5 * strength).mean()) if np.any(w > 0) else 0.0}
    return x, info


# ================================================================================================================
# constrained fairing (CGLS)
# ================================================================================================================


def fair(co: np.ndarray, topo: MeshTopology, free: np.ndarray, *, order: int = 2, iterations: int = 400,
         tol: float = 1e-6) -> np.ndarray:
    """Fair the ``free`` vertices (bool (V,) or ids) with every other vertex fixed.

    order 1 = membrane (minimise sum of squared edge vectors touching free vertices -> harmonic patch);
    order 2 = thin-plate (minimise sum over ``free`` + their neighbours of ``|umbrella(x)|^2`` -> smooth patch that
    continues the surrounding slopes). Solved with CGLS starting from the current positions (cost ~ region size).
    Free components that touch no fixed vertex keep their minimum-change solution.
    """
    x = np.array(co, dtype=np.float64, copy=True)
    free_ids = _active_ids(free, len(x))
    if free_ids is None or len(free_ids) == 0:
        return x
    n = len(x)
    is_free = np.zeros(n, dtype=bool)
    is_free[free_ids] = True
    col_of = np.full(n, -1, np.int64)
    col_of[free_ids] = np.arange(len(free_ids))
    if order <= 1:
        e = topo.edges
        sel = is_free[e[:, 0]] | is_free[e[:, 1]]
        es = e[sel]
        m = len(es)
        r_idx = np.concatenate([np.arange(m), np.arange(m)])
        c_glob = np.concatenate([es[:, 0], es[:, 1]])
        vals = np.concatenate([np.ones(m), -np.ones(m)])
        n_rows = m
    else:
        rows_ids = np.nonzero(_dilate_ids(is_free, topo))[0]
        r_local, nbr, deg = _region_rows(topo.adj, rows_ids)
        safe = np.maximum(deg, 1).astype(np.float64)
        r_idx = np.concatenate([r_local, np.arange(len(rows_ids))])
        c_glob = np.concatenate([nbr, rows_ids])
        vals = np.concatenate([1.0 / safe[r_local], -np.ones(len(rows_ids))])
        n_rows = len(rows_ids)
    fcol = is_free[c_glob]
    # constant part b = B x_fixed
    b = np.zeros((n_rows, 3), np.float64)
    fixed_r, fixed_c, fixed_v = r_idx[~fcol], c_glob[~fcol], vals[~fcol]
    for k in range(3):
        b[:, k] = np.bincount(fixed_r, weights=fixed_v * x[fixed_c, k], minlength=n_rows)
    ar, ac, av = r_idx[fcol], col_of[c_glob[fcol]], vals[fcol]
    nf = len(free_ids)

    def amul(p: np.ndarray) -> np.ndarray:
        out = np.empty((n_rows, 3), np.float64)
        for k in range(3):
            out[:, k] = np.bincount(ar, weights=av * p[ac, k], minlength=n_rows)
        return out

    def atmul(r: np.ndarray) -> np.ndarray:
        out = np.empty((nf, 3), np.float64)
        for k in range(3):
            out[:, k] = np.bincount(ac, weights=av * r[ar, k], minlength=nf)
        return out

    xf = x[free_ids].copy()
    r = -(amul(xf) + b)
    s = atmul(r)
    p = s.copy()
    gamma = np.einsum("ij,ij->j", s, s)
    gamma0 = gamma.copy()
    for _ in range(max(1, int(iterations))):
        if np.all(gamma <= (tol * tol) * np.maximum(gamma0, 1e-300)):
            break
        q = amul(p)
        qq = np.einsum("ij,ij->j", q, q)
        alpha = np.where(qq > 0, gamma / np.maximum(qq, 1e-300), 0.0)
        xf += p * alpha
        r -= q * alpha
        s = atmul(r)
        gamma_new = np.einsum("ij,ij->j", s, s)
        beta = np.where(gamma > 0, gamma_new / np.maximum(gamma, 1e-300), 0.0)
        p = s + p * beta
        gamma = gamma_new
    x[free_ids] = xf
    return x


def _dilate_ids(mask: np.ndarray, topo: MeshTopology) -> np.ndarray:
    """``mask`` plus its one-ring, computed only around the mask (cost ~ mask size)."""
    ids = np.nonzero(mask)[0]
    out = mask.copy()
    if len(ids):
        _, nbr, _ = _region_rows(topo.adj, ids)
        out[nbr] = True
    return out


# ================================================================================================================
# curvature / roughness
# ================================================================================================================


def convexity(co: np.ndarray, topo: MeshTopology, normals: np.ndarray, edge_len: np.ndarray | None = None
              ) -> np.ndarray:
    """Signed umbrella curvature along the normal, normalised by the local edge length (V,).

    Positive = convex (bump / ridge, the vertex sticks out of its neighbours), negative = concave (pit / groove).
    """
    u = umbrella(co, topo)
    if edge_len is None:
        edge_len = vertex_mean_edge_length(co, topo)
    return -np.einsum("ij,ij->i", u, normals) / np.maximum(edge_len, 1e-12)


def dihedral_angles(face_normals: np.ndarray, topo: MeshTopology) -> np.ndarray:
    """(M,) angle in degrees between the normals of each manifold face pair (0 = flat)."""
    if len(topo.face_pairs) == 0:
        return np.zeros(0)
    a = face_normals[topo.face_pairs[:, 0]]
    b = face_normals[topo.face_pairs[:, 1]]
    return np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", a, b), -1.0, 1.0)))


def vertex_max_dihedral(dihedral: np.ndarray, topo: MeshTopology) -> np.ndarray:
    """(V,) largest dihedral angle (degrees) of the manifold edges around each vertex."""
    out = np.zeros(topo.n_verts, np.float64)
    if len(dihedral):
        e = topo.edges[topo.pair_edges]
        np.maximum.at(out, e[:, 0], dihedral)
        np.maximum.at(out, e[:, 1], dihedral)
    return out


def dihedral_stats(dihedral: np.ndarray) -> dict:
    """``{p50, p90, p95, over_30, over_60, over_150}`` (degrees / fractions) of a dihedral array."""
    if len(dihedral) == 0:
        return {"p50": 0.0, "p90": 0.0, "p95": 0.0, "over_30": 0.0, "over_60": 0.0, "over_150": 0.0}
    p50, p90, p95 = np.percentile(dihedral, [50, 90, 95])
    return {"p50": round(float(p50), 3), "p90": round(float(p90), 3), "p95": round(float(p95), 3),
            "over_30": round(float((dihedral > 30).mean()), 5), "over_60": round(float((dihedral > 60).mean()), 5),
            "over_150": round(float((dihedral > 150).mean()), 6)}


def high_pass(co: np.ndarray, topo: MeshTopology, *, iterations: int = 10, active: np.ndarray | None = None,
              locked: np.ndarray | None = None) -> np.ndarray:
    """High-frequency residual ``co - taubin(co)`` (V, 3) - the detail a low-pass removes (noise, pits, grooves)."""
    return co - taubin(co, topo, iterations=iterations, lam=0.6, mu=-0.63, active=active, locked=locked)


def roughness(co: np.ndarray, topo: MeshTopology, normals: np.ndarray, *, iterations: int = 10,
              edge_len: np.ndarray | None = None, active: np.ndarray | None = None) -> np.ndarray:
    """(V,) ``|normal . high_pass|`` / local edge length - 0 on smooth surfaces, ~0.1-0.3 on noisy TRELLIS skin."""
    hp = high_pass(co, topo, iterations=iterations, active=active, locked=topo.locked)
    if edge_len is None:
        edge_len = vertex_mean_edge_length(co, topo)
    return np.abs(np.einsum("ij,ij->i", hp, normals)) / np.maximum(edge_len, 1e-12)


# ================================================================================================================
# curved edge midpoints (PN triangles)
# ================================================================================================================


def pn_midpoints(p0: np.ndarray, p1: np.ndarray, n0: np.ndarray, n1: np.ndarray) -> np.ndarray:
    """Midpoints (N, 3) of the cubic PN-triangle edge curves between ``p0``/``p1`` with unit normals ``n0``/``n1``.

    ``(p0 + p1) / 2 - ((p1 - p0) . n0 * n0 + (p0 - p1) . n1 * n1) / 8``: on a convex surface the midpoint bulges
    outwards, on a flat one it stays on the chord - exactly the curvature a flat facet lost.
    """
    d = p1 - p0
    w01 = np.einsum("ij,ij->i", d, n0)
    w10 = np.einsum("ij,ij->i", -d, n1)
    return 0.5 * (p0 + p1) - (w01[:, None] * n0 + w10[:, None] * n1) / 8.0
