"""Vectorized numpy helpers over ``bpy.types.Mesh`` (foreach_get/foreach_set) - the toolbox of every step.

Conventions
* ``V``/``E``/``F``/``L``/``T`` = vertex / edge / face (polygon) / loop (corner) / triangle counts.
* Index arrays are ``int64``; positions/normals are ``float64`` (Blender stores float32 - written back as float32);
  UVs and image pixels are ``float32``.
* Blender stores the loops of each polygon contiguously and in polygon order (``loop_start`` is monotonic), so
  ``loop_face = np.repeat(np.arange(F), loop_total)`` maps loops to faces, and for an all-triangle mesh
  ``loop_vertices.reshape(-1, 3)`` is the triangle array.
* A loop ``l`` of a face runs from ``loop_vertices[l]`` to the next loop's vertex, along ``loop_edges[l]``.
  Two faces sharing a manifold edge are consistently wound iff their loops on that edge start at DIFFERENT
  vertices.
* Coordinates are object space. TRELLIS imports have identity transforms (object space == world space).
* Performance reference (1.9M faces / 0.9M verts, Blender 5.2): positions 0.01 s, loop vertices 0.12 s,
  ``edge_faces`` ~1 s, ``connected_components`` ~0.7 s, BVH build 0.1-0.3 s, ``find_nearest`` ~9 us/point with
  a distance limit (~22 us without), bmesh weld ~2 s.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import bmesh
import bpy
import numpy as np
from mathutils.bvhtree import BVHTree

log = logging.getLogger(__name__)

# ================================================================================================================
# raw reads / writes
# ================================================================================================================


def get_positions(mesh: bpy.types.Mesh, dtype=np.float64) -> np.ndarray:
    """Vertex positions, shape (V, 3), ``dtype`` (default float64)."""
    buf = np.empty(len(mesh.vertices) * 3, np.float32)
    mesh.vertices.foreach_get("co", buf)
    return buf.reshape(-1, 3).astype(dtype, copy=False)


def set_positions(mesh: bpy.types.Mesh, co: np.ndarray) -> None:
    """Write vertex positions (V, 3) (any float dtype; stored as float32) and tag the mesh for update.

    Note: imported custom normals (``custom_normal`` attribute) do NOT follow moved vertices - call
    :func:`clear_custom_normals` after substantial deformation.
    """
    co = np.ascontiguousarray(co, dtype=np.float32).reshape(-1)
    if co.size != len(mesh.vertices) * 3:
        raise ValueError(f"set_positions: expected {len(mesh.vertices)} x 3 values, got {co.size}")
    mesh.vertices.foreach_set("co", co)
    mesh.update()


def get_edges(mesh: bpy.types.Mesh) -> np.ndarray:
    """Edge vertex pairs, shape (E, 2) int64 (Blender edge order)."""
    buf = np.empty(len(mesh.edges) * 2, np.int32)
    mesh.edges.foreach_get("vertices", buf)
    return buf.reshape(-1, 2).astype(np.int64)


def get_loop_vertices(mesh: bpy.types.Mesh) -> np.ndarray:
    """Vertex index of every loop (corner), shape (L,) int64."""
    buf = np.empty(len(mesh.loops), np.int32)
    mesh.loops.foreach_get("vertex_index", buf)
    return buf.astype(np.int64)


def get_loop_edges(mesh: bpy.types.Mesh) -> np.ndarray:
    """Edge index of every loop (edge from this loop's vertex to the next one of the face), shape (L,) int64."""
    buf = np.empty(len(mesh.loops), np.int32)
    mesh.loops.foreach_get("edge_index", buf)
    return buf.astype(np.int64)


def get_polygon_loops(mesh: bpy.types.Mesh) -> tuple[np.ndarray, np.ndarray]:
    """``(loop_start, loop_total)``, each shape (F,) int64."""
    n = len(mesh.polygons)
    start = np.empty(n, np.int32)
    total = np.empty(n, np.int32)
    mesh.polygons.foreach_get("loop_start", start)
    mesh.polygons.foreach_get("loop_total", total)
    return start.astype(np.int64), total.astype(np.int64)


def loop_faces(loop_total: np.ndarray) -> np.ndarray:
    """Face index of every loop, shape (L,) int64, from ``loop_total`` (F,)."""
    return np.repeat(np.arange(len(loop_total), dtype=np.int64), loop_total)


def is_all_triangles(mesh: bpy.types.Mesh) -> bool:
    """True when every polygon has exactly 3 corners."""
    _, total = get_polygon_loops(mesh)
    return bool(total.size == 0 or (total == 3).all())


def get_triangles(mesh: bpy.types.Mesh) -> np.ndarray:
    """Triangle vertex indices of an ALL-TRIANGLE mesh, shape (F, 3) int64 (row f == polygon f).

    Raises:
        ValueError: the mesh contains quads/n-gons (use :func:`get_loop_triangles`).
    """
    start, total = get_polygon_loops(mesh)
    if total.size and not (total == 3).all():
        raise ValueError("mesh is not all triangles; use get_loop_triangles()")
    lv = get_loop_vertices(mesh)
    if total.size and not np.array_equal(start, np.arange(len(start), dtype=np.int64) * 3):
        return lv[start[:, None] + np.arange(3)[None, :]]
    return lv.reshape(-1, 3)


def get_loop_triangles(mesh: bpy.types.Mesh) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Triangulation of any mesh: ``(tri_verts (T,3), tri_polygon (T,), tri_loops (T,3))``, all int64."""
    mesh.calc_loop_triangles()
    n = len(mesh.loop_triangles)
    verts = np.empty(n * 3, np.int32)
    loops = np.empty(n * 3, np.int32)
    poly = np.empty(n, np.int32)
    mesh.loop_triangles.foreach_get("vertices", verts)
    mesh.loop_triangles.foreach_get("loops", loops)
    mesh.loop_triangles.foreach_get("polygon_index", poly)
    return verts.reshape(-1, 3).astype(np.int64), poly.astype(np.int64), loops.reshape(-1, 3).astype(np.int64)


def get_face_areas(mesh: bpy.types.Mesh) -> np.ndarray:
    """Polygon areas, shape (F,) float64 (computed by Blender)."""
    buf = np.empty(len(mesh.polygons), np.float32)
    mesh.polygons.foreach_get("area", buf)
    return buf.astype(np.float64)


def get_face_normals(mesh: bpy.types.Mesh) -> np.ndarray:
    """Unit polygon normals (from winding), shape (F, 3) float64."""
    buf = np.empty(len(mesh.polygons) * 3, np.float32)
    normals = getattr(mesh, "polygon_normals", None)
    if normals is not None:
        normals.foreach_get("vector", buf)
    else:  # pragma: no cover - pre-4.1 API
        mesh.polygons.foreach_get("normal", buf)
    return buf.reshape(-1, 3).astype(np.float64)


def get_face_centers(mesh: bpy.types.Mesh) -> np.ndarray:
    """Polygon centres (corner average), shape (F, 3) float64."""
    buf = np.empty(len(mesh.polygons) * 3, np.float32)
    mesh.polygons.foreach_get("center", buf)
    return buf.reshape(-1, 3).astype(np.float64)


def get_vertex_normals(mesh: bpy.types.Mesh) -> np.ndarray:
    """Vertex normals computed by Blender from the geometry (ignores custom normals), shape (V, 3) float64."""
    buf = np.empty(len(mesh.vertices) * 3, np.float32)
    normals = getattr(mesh, "vertex_normals", None)
    if normals is not None:
        normals.foreach_get("vector", buf)
    else:  # pragma: no cover - pre-4.1 API
        mesh.vertices.foreach_get("normal", buf)
    return buf.reshape(-1, 3).astype(np.float64)


def get_material_indices(mesh: bpy.types.Mesh) -> np.ndarray:
    """Material slot index per polygon, shape (F,) int64."""
    buf = np.empty(len(mesh.polygons), np.int32)
    mesh.polygons.foreach_get("material_index", buf)
    return buf.astype(np.int64)


def get_uvs(mesh: bpy.types.Mesh, layer: str | None = None) -> np.ndarray | None:
    """Per-loop UVs of ``layer`` (default: active layer), shape (L, 2) float32; ``None`` if the mesh has no UVs."""
    uv_layer = mesh.uv_layers.get(layer) if layer else mesh.uv_layers.active
    if uv_layer is None:
        return None
    buf = np.empty(len(mesh.loops) * 2, np.float32)
    uv_layer.data.foreach_get("uv", buf)
    return buf.reshape(-1, 2)


def set_uvs(mesh: bpy.types.Mesh, uv: np.ndarray, layer: str | None = None) -> None:
    """Write per-loop UVs (L, 2) into ``layer`` (created when missing; default: active layer or ``UVMap``)."""
    uv_layer = mesh.uv_layers.get(layer) if layer else mesh.uv_layers.active
    if uv_layer is None:
        uv_layer = mesh.uv_layers.new(name=layer or "UVMap")
    data = np.ascontiguousarray(uv, dtype=np.float32).reshape(-1)
    if data.size != len(mesh.loops) * 2:
        raise ValueError(f"set_uvs: expected {len(mesh.loops)} x 2 values, got {data.size}")
    uv_layer.data.foreach_set("uv", data)


# ================================================================================================================
# numpy geometry (no bpy)
# ================================================================================================================


def triangle_normals(co: np.ndarray, tris: np.ndarray, normalize: bool = True) -> np.ndarray:
    """Triangle normals (T, 3) float64 from positions (V, 3) and triangles (T, 3).

    ``normalize=False`` returns the raw cross product (length = 2 x area). Degenerate triangles give (0, 0, 0).
    """
    p0 = co[tris[:, 0]]
    n = np.cross(co[tris[:, 1]] - p0, co[tris[:, 2]] - p0)
    if not normalize:
        return n
    length = np.linalg.norm(n, axis=1)
    out = np.zeros_like(n)
    ok = length > 0
    out[ok] = n[ok] / length[ok, None]
    return out


def triangle_areas(co: np.ndarray, tris: np.ndarray) -> np.ndarray:
    """Triangle areas (T,) float64."""
    return 0.5 * np.linalg.norm(triangle_normals(co, tris, normalize=False), axis=1)


def triangle_centroids(co: np.ndarray, tris: np.ndarray) -> np.ndarray:
    """Triangle centroids (T, 3) float64."""
    return (co[tris[:, 0]] + co[tris[:, 1]] + co[tris[:, 2]]) / 3.0


def vertex_normals_from_triangles(co: np.ndarray, tris: np.ndarray) -> np.ndarray:
    """Area-weighted unit vertex normals (V, 3) float64; vertices without faces get (0, 0, 0)."""
    n = triangle_normals(co, tris, normalize=False)
    out = np.zeros((len(co), 3), np.float64)
    flat = tris.reshape(-1)
    for k in range(3):
        out[:, k] = np.bincount(flat, weights=np.repeat(n[:, k], 3), minlength=len(co))
    length = np.linalg.norm(out, axis=1)
    ok = length > 0
    out[ok] /= length[ok, None]
    return out


def edge_lengths(co: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """Edge lengths (E,) float64 for vertex pairs (E, 2)."""
    return np.linalg.norm(co[edges[:, 0]] - co[edges[:, 1]], axis=1)


def sample_surface(co: np.ndarray, tris: np.ndarray, count: int, *, seed: int = 0,
                   face_mask: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Area-weighted random points on triangles: ``(points (N, 3) float64, triangle_index (N,) int64)``.

    ``face_mask`` (T,) bool restricts sampling to those triangles. Returns empty arrays when nothing has area.
    """
    areas = triangle_areas(co, tris)
    if face_mask is not None:
        areas = np.where(face_mask, areas, 0.0)
    total = float(areas.sum())
    if count <= 0 or total <= 0:
        return np.zeros((0, 3)), np.zeros(0, np.int64)
    rng = np.random.default_rng(seed)
    cdf = np.cumsum(areas)
    idx = np.searchsorted(cdf, rng.random(count) * total, side="right")
    idx = np.minimum(idx, len(tris) - 1).astype(np.int64)
    r1 = np.sqrt(rng.random(count))
    r2 = rng.random(count)
    a, b, c = co[tris[idx, 0]], co[tris[idx, 1]], co[tris[idx, 2]]
    pts = (1 - r1)[:, None] * a + (r1 * (1 - r2))[:, None] * b + (r1 * r2)[:, None] * c
    return pts, idx


def count_duplicate_positions(co: np.ndarray) -> int:
    """Number of vertices whose position (float32-exact) equals an earlier vertex's position."""
    if len(co) < 2:
        return 0
    c = np.ascontiguousarray(co, dtype=np.float32)
    order = np.lexsort((c[:, 2], c[:, 1], c[:, 0]))
    s = c[order]
    return int(np.all(s[1:] == s[:-1], axis=1).sum())


# ================================================================================================================
# topology
# ================================================================================================================


@dataclass
class EdgeFaces:
    """Edge -> faces incidence (CSR) for a mesh or a triangle array.

    Attributes:
        edges: (E, 2) int64 vertex ids of each edge.
        face_count: (E,) int64 number of faces using the edge (0 wire, 1 boundary, 2 manifold, >2 non-manifold).
        indptr: (E + 1,) int64 CSR offsets: faces of edge ``e`` are ``faces[indptr[e]:indptr[e + 1]]``.
        faces: (sum(face_count),) int64 face ids grouped by edge (ascending loop order within an edge).
        loops: same shape; the loop/corner id of each incidence (for a triangle array: ``3 * t + k``, the corner
            running from ``tris[t, k]`` to ``tris[t, (k + 1) % 3]``).
        loop_edges: (L,) int64 edge id of every loop/corner.
    """

    edges: np.ndarray
    face_count: np.ndarray
    indptr: np.ndarray
    faces: np.ndarray
    loops: np.ndarray
    loop_edges: np.ndarray

    @property
    def boundary(self) -> np.ndarray:
        """(E,) bool - edges used by exactly one face."""
        return self.face_count == 1

    @property
    def manifold(self) -> np.ndarray:
        """(E,) bool - edges used by exactly two faces."""
        return self.face_count == 2

    @property
    def non_manifold(self) -> np.ndarray:
        """(E,) bool - edges used by three or more faces."""
        return self.face_count > 2

    @property
    def wire(self) -> np.ndarray:
        """(E,) bool - edges without faces."""
        return self.face_count == 0

    def manifold_pairs(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """For every manifold edge: ``(edge_ids (M,), face1, face2, loop1, loop2)`` (all int64)."""
        m = np.nonzero(self.face_count == 2)[0]
        start = self.indptr[m]
        return m, self.faces[start], self.faces[start + 1], self.loops[start], self.loops[start + 1]

    def faces_of(self, edge: int) -> np.ndarray:
        """Face ids using ``edge``."""
        return self.faces[self.indptr[edge]:self.indptr[edge + 1]]


def _edge_faces_from_loops(edges: np.ndarray, loop_edges: np.ndarray, loop_face: np.ndarray) -> EdgeFaces:
    n_edges = len(edges)
    counts = np.bincount(loop_edges, minlength=n_edges).astype(np.int64)
    order = np.argsort(loop_edges, kind="stable")
    indptr = np.zeros(n_edges + 1, np.int64)
    np.cumsum(counts, out=indptr[1:])
    return EdgeFaces(edges=edges, face_count=counts, indptr=indptr, faces=loop_face[order],
                     loops=order.astype(np.int64), loop_edges=loop_edges)


def edge_faces(mesh: bpy.types.Mesh) -> EdgeFaces:
    """Edge -> faces incidence of a Blender mesh (uses the mesh's own edges)."""
    _, total = get_polygon_loops(mesh)
    return _edge_faces_from_loops(get_edges(mesh), get_loop_edges(mesh), loop_faces(total))


def edge_faces_from_triangles(tris: np.ndarray, n_verts: int | None = None) -> EdgeFaces:
    """Edge -> faces incidence of a triangle array (T, 3) (pure numpy; edges are built from the triangles).

    ``edges`` are unique sorted vertex pairs; ``loops`` are corner ids ``3 * t + k``.
    """
    tris = np.asarray(tris, dtype=np.int64)
    if n_verts is None:
        n_verts = int(tris.max()) + 1 if tris.size else 0
    a = tris.reshape(-1)
    b = np.roll(tris, -1, axis=1).reshape(-1)
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    key = lo * np.int64(max(n_verts, 1)) + hi
    uniq, inverse = np.unique(key, return_inverse=True)
    edges = np.stack([uniq // max(n_verts, 1), uniq % max(n_verts, 1)], axis=1).astype(np.int64)
    loop_face = np.repeat(np.arange(len(tris), dtype=np.int64), 3)
    return _edge_faces_from_loops(edges, inverse.reshape(-1).astype(np.int64), loop_face)


def inconsistent_winding_edges(ef: EdgeFaces, loop_vertices: np.ndarray) -> np.ndarray:
    """Manifold edge ids whose two faces traverse the edge in the SAME direction (flipped neighbour), int64.

    ``loop_vertices``: (L,) start vertex of every loop (``get_loop_vertices`` or ``tris.reshape(-1)``).
    """
    m, _, _, l1, l2 = ef.manifold_pairs()
    return m[loop_vertices[l1] == loop_vertices[l2]]


def csr_from_pairs(n: int, pairs: np.ndarray, symmetric: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """CSR adjacency ``(indptr (n+1,), indices)`` int64 from index pairs (P, 2); rows sorted by source."""
    pairs = np.asarray(pairs, dtype=np.int64).reshape(-1, 2)
    if symmetric:
        src = np.concatenate([pairs[:, 0], pairs[:, 1]])
        dst = np.concatenate([pairs[:, 1], pairs[:, 0]])
    else:
        src, dst = pairs[:, 0], pairs[:, 1]
    order = np.argsort(src, kind="stable")
    indptr = np.zeros(n + 1, np.int64)
    np.cumsum(np.bincount(src, minlength=n), out=indptr[1:])
    return indptr, dst[order]


def vertex_adjacency(n_verts: int, edges: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Symmetric vertex adjacency (CSR) from edges (E, 2): neighbours of ``v`` = ``indices[indptr[v]:indptr[v+1]]``."""
    return csr_from_pairs(n_verts, edges, symmetric=True)


def face_adjacency(ef: EdgeFaces, n_faces: int, *, include_non_manifold: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Face adjacency (CSR) through shared edges.

    Manifold edges connect their two faces. With ``include_non_manifold`` the faces of an edge with 3+ faces are
    connected to the first face of that edge (star), which preserves connectivity without a quadratic blow-up.
    """
    _, f1, f2, _, _ = ef.manifold_pairs()
    pairs = [np.stack([f1, f2], axis=1)]
    if include_non_manifold:
        nm = np.nonzero(ef.face_count > 2)[0]
        if len(nm):
            counts = ef.face_count[nm]
            first = np.repeat(ef.faces[ef.indptr[nm]], counts - 1)
            starts = np.repeat(ef.indptr[nm], counts - 1)
            offs = np.arange(int((counts - 1).sum())) - np.repeat(np.cumsum(counts - 1) - (counts - 1), counts - 1)
            pairs.append(np.stack([first, ef.faces[starts + 1 + offs]], axis=1))
    return csr_from_pairs(n_faces, np.concatenate(pairs, axis=0))


def vertex_faces(n_verts: int, loop_vertices: np.ndarray, loop_face: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Vertex -> incident faces (CSR ``indptr (V+1,), faces``) from loop arrays (or ``tris.reshape(-1)`` +
    ``np.repeat(arange(T), 3)``)."""
    order = np.argsort(loop_vertices, kind="stable")
    indptr = np.zeros(n_verts + 1, np.int64)
    np.cumsum(np.bincount(loop_vertices, minlength=n_verts), out=indptr[1:])
    return indptr, loop_face[order]


def connected_components(n: int, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Connected-component labels (n,) int64 (compact, 0..K-1) of an undirected graph with edges ``a[i]-b[i]``.

    Vectorized union-find (min-label hooking + pointer jumping); ~0.7 s for 0.9M nodes / 2.8M edges.
    """
    parent = np.arange(n, dtype=np.int64)
    a = np.asarray(a, dtype=np.int64)
    b = np.asarray(b, dtype=np.int64)
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
    _, labels = np.unique(parent, return_inverse=True)
    return labels.reshape(-1).astype(np.int64)


def face_components(n_verts: int, edges: np.ndarray, face_first_vertex: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Face components through shared vertices: ``(face_label (F,) int64 compact 0..K-1, face_count (K,) int64)``.

    ``edges``: (E, 2) mesh edges (or triangle edges); ``face_first_vertex``: (F,) any vertex of each face
    (``loop_vertices[loop_start]`` or ``tris[:, 0]``). Labels are ordered by descending component size.
    """
    vlab = connected_components(n_verts, edges[:, 0], edges[:, 1])
    flab = vlab[face_first_vertex]
    uniq, flab = np.unique(flab, return_inverse=True)
    sizes = np.bincount(flab, minlength=len(uniq)).astype(np.int64)
    order = np.argsort(-sizes, kind="stable")
    remap = np.empty_like(order)
    remap[order] = np.arange(len(order))
    return remap[flab].astype(np.int64), sizes[order]


@dataclass
class BoundaryLoops:
    """Boundary edges grouped into connected loops (labels ordered by descending edge count).

    Attributes:
        edge_ids: (B,) int64 boundary edge ids (into the edges array the loops were built from).
        loop_of_edge: (B,) int64 loop label of each boundary edge.
        edge_count: (K,) int64 edges per loop.
        perimeter: (K,) float64 summed edge length per loop (zeros when positions were not given).
    """

    edge_ids: np.ndarray
    loop_of_edge: np.ndarray
    edge_count: np.ndarray
    perimeter: np.ndarray

    @property
    def count(self) -> int:
        return int(len(self.edge_count))

    def holes(self, min_edges: int = 5) -> np.ndarray:
        """Loop labels with at least ``min_edges`` edges (DESIGN: a real hole has >= 5 edges)."""
        return np.nonzero(self.edge_count >= min_edges)[0]

    def loop_edge_ids(self, loop: int) -> np.ndarray:
        """Edge ids of loop ``loop``."""
        return self.edge_ids[self.loop_of_edge == loop]


def boundary_loops(edges: np.ndarray, boundary_mask: np.ndarray, co: np.ndarray | None = None) -> BoundaryLoops:
    """Group boundary edges (``boundary_mask`` (E,) bool) into connected loops (see :class:`BoundaryLoops`)."""
    ids = np.nonzero(boundary_mask)[0].astype(np.int64)
    if len(ids) == 0:
        z = np.zeros(0, np.int64)
        return BoundaryLoops(z, z, z, np.zeros(0))
    be = edges[ids]
    verts, inv = np.unique(be.reshape(-1), return_inverse=True)
    inv = inv.reshape(-1, 2)
    vlab = connected_components(len(verts), inv[:, 0], inv[:, 1])
    lab = vlab[inv[:, 0]]
    n = int(lab.max()) + 1
    counts = np.bincount(lab, minlength=n)
    order = np.argsort(-counts, kind="stable")
    remap = np.empty_like(order)
    remap[order] = np.arange(n)
    lab = remap[lab]
    counts = counts[order].astype(np.int64)
    perim = np.zeros(n)
    if co is not None:
        perim = np.bincount(lab, weights=edge_lengths(co, be), minlength=n)
    return BoundaryLoops(edge_ids=ids, loop_of_edge=lab.astype(np.int64), edge_count=counts, perimeter=perim)


def order_loop_vertices(loop_edges: np.ndarray) -> np.ndarray | None:
    """Walk a closed edge loop (n, 2) into an ordered vertex cycle (n,) int64; ``None`` if not a simple cycle."""
    loop_edges = np.asarray(loop_edges, dtype=np.int64)
    if len(loop_edges) < 3:
        return None
    nbrs: dict[int, list[int]] = {}
    for a, b in loop_edges.tolist():
        nbrs.setdefault(a, []).append(b)
        nbrs.setdefault(b, []).append(a)
    if any(len(v) != 2 for v in nbrs.values()) or len(nbrs) != len(loop_edges):
        return None
    start = int(loop_edges[0, 0])
    order = [start]
    prev, cur = start, nbrs[start][0]
    while cur != start:
        order.append(cur)
        a, b = nbrs[cur]
        prev, cur = cur, (b if a == prev else a)
        if len(order) > len(nbrs):
            return None
    return np.asarray(order, dtype=np.int64) if len(order) == len(nbrs) else None


# ================================================================================================================
# bmesh / datablock operations
# ================================================================================================================


def weld_by_distance(mesh: bpy.types.Mesh, dist: float) -> int:
    """Merge vertices closer than ``dist`` (``bmesh.ops.remove_doubles``); keeps UVs/custom normals/weights.

    Returns the number of merged (removed) vertices. ~2 s for 1.7M vertices.
    """
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        before = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=float(dist))
        merged = before - len(bm.verts)
        if merged:
            bm.to_mesh(mesh)
    finally:
        bm.free()
    if merged:
        mesh.update()
    return merged


def snapshot_mesh(obj: bpy.types.Object, name: str | None = None) -> bpy.types.Mesh:
    """Copy ``obj.data`` into a new (fake-user) mesh datablock for rollback. Cheap (implicit sharing)."""
    snap = obj.data.copy()
    snap.name = name or f"AMT_snapshot_{obj.name}"
    snap.use_fake_user = True
    return snap


def restore_mesh(obj: bpy.types.Object, snapshot: bpy.types.Mesh, *, keep_snapshot: bool = False) -> None:
    """Replace ``obj.data`` with ``snapshot`` (or a copy of it with ``keep_snapshot``) and free the old mesh."""
    old = obj.data
    name = old.name
    new = snapshot.copy() if keep_snapshot else snapshot
    new.use_fake_user = False
    obj.data = new
    if old.users == 0:
        bpy.data.meshes.remove(old)
    new.name = name


def discard_mesh(mesh: bpy.types.Mesh | None) -> None:
    """Remove a snapshot/helper mesh datablock (no-op when ``None`` or still used by an object)."""
    if mesh is None:
        return
    try:
        mesh.use_fake_user = False
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    except ReferenceError:
        pass


def clear_custom_normals(mesh: bpy.types.Mesh) -> bool:
    """Drop imported custom split normals so shading follows the (modified) geometry. Returns True if removed."""
    if not getattr(mesh, "has_custom_normals", False):
        return False
    attr = mesh.attributes.get("custom_normal")
    if attr is not None:
        try:
            mesh.attributes.remove(attr)
            mesh.update()
            return not mesh.has_custom_normals
        except (RuntimeError, TypeError):
            pass
    # fallback: operator on a temporary object context
    obj = next((o for o in bpy.data.objects if o.type == "MESH" and o.data == mesh), None)
    if obj is None:
        return False
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
        bpy.ops.mesh.customdata_custom_splitnormals_clear()
    return not mesh.has_custom_normals


# ================================================================================================================
# BVH / nearest-surface queries
# ================================================================================================================


def bvh_from_arrays(co: np.ndarray, polys: np.ndarray | Sequence[Sequence[int]], epsilon: float = 0.0) -> BVHTree:
    """BVH over polygons: positions (V, 3) + triangles (T, 3) int array or a list of index lists."""
    all_tris = isinstance(polys, np.ndarray) and polys.ndim == 2 and polys.shape[1] == 3
    plist = polys.tolist() if isinstance(polys, np.ndarray) else [list(p) for p in polys]
    return BVHTree.FromPolygons(np.asarray(co, dtype=np.float64).tolist(), plist, all_triangles=all_tris,
                                epsilon=epsilon)


def bvh_from_mesh(mesh: bpy.types.Mesh, matrix=None) -> BVHTree:
    """BVH of a mesh's loop triangles in object space (or transformed by the 4x4 ``matrix``).

    Face indices returned by queries are TRIANGLE indices of ``get_loop_triangles(mesh)``; for an all-triangle
    mesh they equal polygon indices.
    """
    co = get_positions(mesh)
    if matrix is not None:
        m = np.array(matrix, dtype=np.float64)
        co = co @ m[:3, :3].T + m[:3, 3]
    if is_all_triangles(mesh):
        tris = get_triangles(mesh)
    else:
        tris, _, _ = get_loop_triangles(mesh)
    return bvh_from_arrays(co, tris)


def nearest_on_surface(bvh: BVHTree, points: np.ndarray, max_dist: float | None = None
                       ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Closest surface points: ``(distance (N,), location (N, 3), face_index (N,) int64)``.

    Queries first with ``max_dist`` (fast, ~9 us/point) and re-queries misses without a limit (~22 us/point).
    Points with no result (empty tree) get distance ``inf`` and face ``-1``.
    """
    pts = np.asarray(points, dtype=np.float64).tolist()
    n = len(pts)
    dist = np.full(n, np.inf)
    loc = np.zeros((n, 3))
    face = np.full(n, -1, np.int64)
    find = bvh.find_nearest
    for i, p in enumerate(pts):
        hit = find(p, max_dist) if max_dist else find(p)
        if hit[0] is None and max_dist:
            hit = find(p)
        if hit[0] is not None:
            loc[i] = hit[0]
            face[i] = hit[2]
            dist[i] = hit[3]
    return dist, loc, face


# ================================================================================================================
# smoothing primitives
# ================================================================================================================


def neighbor_mean(co: np.ndarray, adj: tuple[np.ndarray, np.ndarray]) -> np.ndarray:
    """Uniform neighbour average (V, 3) for CSR adjacency ``adj``; isolated vertices return themselves."""
    indptr, indices = adj
    n = len(co)
    deg = np.diff(indptr)
    rows = np.repeat(np.arange(n, dtype=np.int64), deg)
    out = np.empty((n, 3), np.float64)
    for k in range(3):
        out[:, k] = np.bincount(rows, weights=co[indices, k], minlength=n)
    ok = deg > 0
    out[ok] /= deg[ok, None]
    out[~ok] = co[~ok]
    return out


def laplacian_fair(co: np.ndarray, adj: tuple[np.ndarray, np.ndarray], fixed_mask: np.ndarray | None = None,
                   iterations: int = 10, lam: float = 0.5) -> np.ndarray:
    """Uniform (umbrella) Laplacian smoothing: ``p += lam * (mean(neighbours) - p)`` per iteration.

    Args:
        co: (V, 3) positions (not modified).
        adj: CSR vertex adjacency from :func:`vertex_adjacency`.
        fixed_mask: (V,) bool, True = vertex never moves (e.g. hole rims, features). ``None`` = all free.
        iterations: number of passes; ``lam`` in (0, 1].
    Returns: new (V, 3) float64 positions. Shrinks volume - use :func:`taubin` to preserve it.
    """
    p = np.array(co, dtype=np.float64, copy=True)
    free = None if fixed_mask is None else ~np.asarray(fixed_mask, dtype=bool)
    for _ in range(max(0, int(iterations))):
        delta = neighbor_mean(p, adj) - p
        if free is None:
            p += lam * delta
        else:
            p[free] += lam * delta[free]
    return p


def taubin(co: np.ndarray, adj: tuple[np.ndarray, np.ndarray], mask: np.ndarray | None = None,
           iterations: int = 10, lam: float = 0.5, mu: float = -0.53) -> np.ndarray:
    """Taubin lambda|mu smoothing (low-pass, ~volume preserving).

    Args:
        mask: (V,) bool, True = vertex may move (``None`` = all). Note: opposite meaning of ``fixed_mask``.
        iterations: number of (lambda, mu) pass pairs. ``mu`` must be negative with ``|mu| > lam``.
    Returns: new (V, 3) float64 positions.
    """
    p = np.array(co, dtype=np.float64, copy=True)
    sel = None if mask is None else np.asarray(mask, dtype=bool)
    for _ in range(max(0, int(iterations))):
        for factor in (lam, mu):
            delta = neighbor_mean(p, adj) - p
            if sel is None:
                p += factor * delta
            else:
                p[sel] += factor * delta[sel]
    return p


# ================================================================================================================
# attributes
# ================================================================================================================

_ATTR_LAYOUT = {  # data_type -> (foreach property, numpy dtype, components)
    "FLOAT": ("value", np.float32, 1),
    "INT": ("value", np.int32, 1),
    "INT8": ("value", np.int8, 1),
    "BOOLEAN": ("value", np.bool_, 1),
    "FLOAT_VECTOR": ("vector", np.float32, 3),
    "FLOAT2": ("vector", np.float32, 2),
    "FLOAT_COLOR": ("color", np.float32, 4),
    "INT32_2D": ("value", np.int32, 2),
}


def domain_size(mesh: bpy.types.Mesh, domain: str) -> int:
    """Element count of an attribute domain (POINT/EDGE/FACE/CORNER)."""
    return {"POINT": len(mesh.vertices), "EDGE": len(mesh.edges), "FACE": len(mesh.polygons),
            "CORNER": len(mesh.loops)}[domain]


def get_attribute(mesh: bpy.types.Mesh, name: str) -> np.ndarray | None:
    """Read a generic attribute: (N,) for scalar types, (N, k) for vectors/colors; ``None`` if missing/unsupported."""
    attr = mesh.attributes.get(name)
    if attr is None or attr.data_type not in _ATTR_LAYOUT:
        return None
    prop, dtype, comps = _ATTR_LAYOUT[attr.data_type]
    n = domain_size(mesh, attr.domain)
    buf = np.empty(n * comps, dtype)
    attr.data.foreach_get(prop, buf)
    return buf if comps == 1 else buf.reshape(n, comps)


def set_attribute(mesh: bpy.types.Mesh, name: str, values: np.ndarray, domain: str = "POINT",
                  data_type: str | None = None):
    """Create/overwrite a generic attribute (e.g. ``amt_filled`` POINT FLOAT) and return it.

    ``data_type`` is inferred when omitted: bool -> BOOLEAN, integer -> INT, float (N,) -> FLOAT,
    (N, 2) -> FLOAT2, (N, 3) -> FLOAT_VECTOR, (N, 4) -> FLOAT_COLOR. An existing attribute with another domain or
    type is replaced. Prefix framework-internal attributes with ``amt_`` (stripped before export).
    """
    values = np.asarray(values)
    if data_type is None:
        if values.dtype == np.bool_:
            data_type = "BOOLEAN"
        elif np.issubdtype(values.dtype, np.integer):
            data_type = "INT"
        else:
            data_type = {1: "FLOAT", 2: "FLOAT2", 3: "FLOAT_VECTOR", 4: "FLOAT_COLOR"}[
                1 if values.ndim == 1 else values.shape[1]]
    prop, dtype, comps = _ATTR_LAYOUT[data_type]
    n = domain_size(mesh, domain)
    data = np.ascontiguousarray(values, dtype=dtype).reshape(-1)
    if data.size != n * comps:
        raise ValueError(f"set_attribute({name!r}): expected {n} x {comps} values for {domain}, got {data.size}")
    attr = mesh.attributes.get(name)
    if attr is not None and (attr.domain != domain or attr.data_type != data_type):
        mesh.attributes.remove(attr)
        attr = None
    if attr is None:
        attr = mesh.attributes.new(name=name, type=data_type, domain=domain)
    attr.data.foreach_set(prop, data)
    return attr


def remove_attributes(mesh: bpy.types.Mesh, prefix: str = "amt_") -> list[str]:
    """Remove every attribute whose name starts with ``prefix``; returns the removed names."""
    names = [a.name for a in mesh.attributes if a.name.startswith(prefix)]
    for name in names:
        attr = mesh.attributes.get(name)
        if attr is not None:
            mesh.attributes.remove(attr)
    return names


# ================================================================================================================
# images / textures
# ================================================================================================================


def material_images(material: bpy.types.Material | None) -> list[bpy.types.Image]:
    """Images referenced by Image Texture nodes of a material (node groups included), unique, in node order."""
    found: list[bpy.types.Image] = []
    seen_trees: set[int] = set()

    def walk(tree) -> None:
        if tree is None or id(tree) in seen_trees:
            return
        seen_trees.add(id(tree))
        for node in tree.nodes:
            image = getattr(node, "image", None) if node.type == "TEX_IMAGE" else None
            if image is not None and image not in found:
                found.append(image)
            if node.type == "GROUP":
                walk(getattr(node, "node_tree", None))

    if material is not None:
        walk(material.node_tree)
    return found


def find_base_color_image(material: bpy.types.Material | None) -> bpy.types.Image | None:
    """The image feeding the Principled BSDF "Base Color" (through mix/reroute/separate nodes), else the first
    sRGB image of the material, else ``None``."""
    if material is None or material.node_tree is None:
        return None
    tree = material.node_tree
    for node in tree.nodes:
        if node.type != "BSDF_PRINCIPLED":
            continue
        socket = node.inputs.get("Base Color")
        stack = [socket] if socket is not None else []
        visited: set[int] = set()
        while stack:
            sock = stack.pop()
            for link in getattr(sock, "links", ()):
                src = link.from_node
                if id(src) in visited:
                    continue
                visited.add(id(src))
                if src.type == "TEX_IMAGE" and src.image is not None:
                    return src.image
                stack.extend(i for i in src.inputs if i.is_linked)
    for image in material_images(material):
        if image.colorspace_settings.name.lower() in ("srgb", "srgb - texture"):
            return image
    images = material_images(material)
    return images[0] if images else None


def image_pixels(image: bpy.types.Image) -> np.ndarray:
    """Image pixels as (H, W, 4) float32 RGBA in 0..1 (row 0 = bottom = v 0, Blender convention).

    A 4096^2 image needs 268 MB - keep at most one or two alive, ``del`` when done.
    """
    w, h = image.size
    if w == 0 or h == 0:
        raise ValueError(f"image {image.name!r} has no pixel data")
    buf = np.empty(w * h * image.channels, np.float32)
    image.pixels.foreach_get(buf)
    px = buf.reshape(h, w, image.channels)
    if image.channels == 4:
        return px
    out = np.ones((h, w, 4), np.float32)
    out[..., :min(3, image.channels)] = px[..., :3]
    if image.channels == 1:
        out[..., 1] = out[..., 2] = px[..., 0]
    return out


def sample_image(pixels: np.ndarray, uv: np.ndarray, *, bilinear: bool = True, wrap: bool = True) -> np.ndarray:
    """Sample (H, W, 4) pixels at UVs (N, 2) -> (N, 4) float32 RGBA (repeat wrap or clamp)."""
    h, w = pixels.shape[:2]
    uv = np.asarray(uv, dtype=np.float64)
    x = uv[:, 0] * w - 0.5
    y = uv[:, 1] * h - 0.5
    if not bilinear:
        xi = np.floor(x + 0.5).astype(np.int64)
        yi = np.floor(y + 0.5).astype(np.int64)
        xi = np.mod(xi, w) if wrap else np.clip(xi, 0, w - 1)
        yi = np.mod(yi, h) if wrap else np.clip(yi, 0, h - 1)
        return pixels[yi, xi].astype(np.float32)
    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    fx = (x - x0)[:, None]
    fy = (y - y0)[:, None]

    def idx(i: np.ndarray, n: int) -> np.ndarray:
        return np.mod(i, n) if wrap else np.clip(i, 0, n - 1)

    xa, xb = idx(x0, w), idx(x0 + 1, w)
    ya, yb = idx(y0, h), idx(y0 + 1, h)
    top = pixels[ya, xa] * (1 - fx) + pixels[ya, xb] * fx
    bot = pixels[yb, xa] * (1 - fx) + pixels[yb, xb] * fx
    return (top * (1 - fy) + bot * fy).astype(np.float32)


def object_base_color_images(obj: bpy.types.Object) -> list[bpy.types.Image | None]:
    """Base-colour image per material slot of ``obj`` (``None`` for slots without one)."""
    return [find_base_color_image(slot.material) for slot in obj.material_slots]


def unique_images(materials: Iterable[bpy.types.Material | None]) -> list[bpy.types.Image]:
    """Unique images used by the given materials."""
    out: list[bpy.types.Image] = []
    for mat in materials:
        for image in material_images(mat):
            if image not in out:
                out.append(image)
    return out
