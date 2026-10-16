"""Shells: detached inner sheets, separate shells, floaters and opposite-sheet partners (DESIGN.md section 4.6.5
step 2 and section 2.4 rule 2; slice S3a).

TRELLIS.2 output is double-walled: thin sheets (hair, skirts, sleeves) have an outer and an inner wall.

* **Detached inner sheets.** A welded component C (not the largest) whose vertices lie, for at least 95 %, within
  ``shell_eps = max(3 / effective_resolution, 1.5 * median_edge_length(K))`` of a larger component K is K's inner
  sheet: it merges into K's owner and is tagged ``inner_sheet_merged``. Merges chain (an inner sheet of an inner sheet
  joins the same root).
* **Separate shells.** The remaining roots under 25 % of the faces (their merged inner sheets included) are separate
  shells; larger roots stay on the base. A separate shell without a label and under
  ``floater_max_faces = max(min_part_faces, round(0.0005 * F))`` faces is a floater: no part record, listed in
  ``parts[body].geometry.floaters``, never deleted.
* **Opposite-sheet partners.** Rim-joined walls are ONE welded component, so a component rule cannot see them;
  :func:`pair_opposite_faces` pairs every face with the nearest face behind it whose normal is opposite.

Torch-free: numpy and scipy only.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Literal

import numpy as np
from scipy.spatial import cKDTree

from parts.components import MeshTopology
from parts.manifest import Floater
from parts.weld import edge_lengths

INNER_SHEET_COVERAGE: Final = 0.95
"""Share of a component's vertices that must lie within ``shell_eps`` of a larger component."""
SEPARATE_SHELL_MAX_FRACTION: Final = 0.25
"""Roots holding at least this share of the faces stay on the base (section 2.4 rule 2)."""
FLOATER_FACE_FRACTION: Final = 0.0005
"""``floater_max_faces = max(min_part_faces, round(0.0005 * F))``."""
SHELL_EPS_VOXELS: Final = 3.0
SHELL_EPS_EDGE_FACTOR: Final = 1.5
PARTNER_NEIGHBOURS: Final = 16
PARTNER_MAX_NORMAL_DOT: Final = -0.5
NO_PARTNER: Final = -1
_PARTNER_CHUNK: Final = 1 << 18

ShellRole = Literal["base", "major", "inner_sheet", "shell"]
"""``base``: component 0. ``major``: a root with >= 25 % of the faces (stays on the base). ``inner_sheet``: merged into
its ``host``. ``shell``: a separate shell (a part, or a floater when unlabelled and tiny)."""


def shell_eps(effective_resolution: int, median_edge_length: float) -> float:
    """``max(3 / effective_resolution, 1.5 * median_edge_length)`` (section 4.6.5 step 2)."""
    if effective_resolution <= 0:
        raise ValueError("effective_resolution must be positive")
    return max(SHELL_EPS_VOXELS / effective_resolution, SHELL_EPS_EDGE_FACTOR * max(float(median_edge_length), 0.0))


def median_edge_length(topology: MeshTopology) -> float:
    """Median length of every welded edge of the mesh (0.0 without edges)."""
    lengths = edge_lengths(topology.edges, topology.welded_positions)
    return float(np.median(lengths)) if lengths.size else 0.0


def global_shell_eps(topology: MeshTopology, effective_resolution: int) -> float:
    """The global ``shell_eps`` used by the partner search and the partner fill (section 4.6.5 steps 2 and 5.2)."""
    return shell_eps(effective_resolution, median_edge_length(topology))


@dataclass(frozen=True, slots=True)
class ComponentShell:
    """Role of one welded component. ``host`` is the root component an inner sheet merged into; ``coverage`` the
    share of its vertices within ``eps`` (the host's ``shell_eps``) of the component it was tested against."""

    component: int
    faces: int
    area: float
    role: ShellRole
    host: int | None = None
    coverage: float | None = None
    eps: float | None = None


@dataclass(frozen=True, slots=True, eq=False)
class ShellReport:
    """Shell classification of every welded component.

    * ``root`` int64 ``[C]``: the component that owns each component after inner-sheet merges (itself for roots).
    * ``group_faces`` int64 ``[C]``: faces of each root including its merged inner sheets (0 for merged components).
    """

    shells: tuple[ComponentShell, ...]
    face_count: int
    root: np.ndarray
    group_faces: np.ndarray

    @property
    def inner_sheets(self) -> dict[int, int]:
        """Merged component -> root host."""
        return {s.component: int(s.host) for s in self.shells if s.role == "inner_sheet" and s.host is not None}

    @property
    def separate_shells(self) -> tuple[int, ...]:
        """Root components that are separate shells (ascending, i.e. largest first)."""
        return tuple(s.component for s in self.shells if s.role == "shell")

    def roots_with_inner_sheets(self) -> tuple[int, ...]:
        """Roots that absorbed at least one inner sheet (their part gets the ``inner_sheet_merged`` tag)."""
        return tuple(sorted({int(host) for host in self.inner_sheets.values()}))

    def face_root(self, face_component: np.ndarray) -> np.ndarray:
        """Root component of every face (int64 ``[F]``)."""
        return self.root[np.asarray(face_component, dtype=np.int64)]

    def inner_sheet_face_mask(self, face_component: np.ndarray) -> np.ndarray:
        """``True`` for faces of merged (detached) inner-sheet components."""
        merged = self.root != np.arange(self.root.shape[0])
        return merged[np.asarray(face_component, dtype=np.int64)]

    def members(self, root: int) -> tuple[int, ...]:
        """The components owned by ``root`` (itself first, then its merged inner sheets ascending)."""
        others = np.flatnonzero((self.root == root) & (np.arange(self.root.shape[0]) != root))
        return (int(root), *(int(c) for c in others))

    def floater_max_faces(self, min_part_faces: int) -> int:
        """``max(min_part_faces, round(0.0005 * F))`` (section 2.4 rule 2)."""
        return max(int(min_part_faces), int(round(FLOATER_FACE_FRACTION * self.face_count)))

    def split_floaters(
        self,
        topology: MeshTopology,
        min_part_faces: int,
        *,
        candidates: Iterable[int] | None = None,
    ) -> tuple[tuple[int, ...], tuple[Floater, ...]]:
        """Split unlabelled separate shells into part shells and floaters (section 2.4 rule 2).

        ``candidates`` are the unlabelled shell roots (default: every separate shell, as in the ``components``
        strategy). A floater stays on the base; its record lists the root component, its face count (merged inner
        sheets included) and its area-weighted centroid.
        """
        limit = self.floater_max_faces(min_part_faces)
        roots = self.separate_shells if candidates is None else tuple(sorted(int(c) for c in candidates))
        kept: list[int] = []
        floaters: list[Floater] = []
        for root in roots:
            if self.shells[root].role != "shell":
                raise ValueError(f"component {root} is not a separate shell")
            faces = int(self.group_faces[root])
            if faces < limit:
                floaters.append(Floater(component=root, faces=faces, centroid=list(_group_centroid(
                    topology, self.members(root)))))
            else:
                kept.append(root)
        return tuple(kept), tuple(floaters)


def _group_centroid(topology: MeshTopology, components: tuple[int, ...]) -> tuple[float, float, float]:
    faces = np.concatenate([topology.components.faces_of(c) for c in components])
    weights = topology.geometry.areas[faces]
    centroids = topology.geometry.centroids[faces].astype(np.float64)
    total = float(weights.sum())
    point = (weights[:, None] * centroids).sum(axis=0) / total if total > 0.0 else centroids.mean(axis=0)
    return float(point[0]), float(point[1]), float(point[2])


def classify_shells(
    topology: MeshTopology,
    effective_resolution: int,
    *,
    coverage: float = INNER_SHEET_COVERAGE,
    max_shell_fraction: float = SEPARATE_SHELL_MAX_FRACTION,
) -> ShellReport:
    """Find detached inner sheets and classify every welded component (section 4.6.5 step 2).

    For each component C > 0 (largest first) every larger component K whose ``shell_eps``-expanded bounding box can
    hold ``coverage`` of C's welded vertices is tested with a ``cKDTree`` over K's welded vertices; the K with the
    highest coverage (>= ``coverage``; ties: the larger K) is the host and C joins the host's root.
    """
    components = topology.components
    count = components.count
    face_count = topology.face_count
    if count == 0:
        return ShellReport((), 0, np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64))
    points = topology.welded_positions.astype(np.float64)
    welded_count = max(topology.weld.welded_count, 1)
    face_component = components.face_component.astype(np.int64)

    keys = np.unique(np.repeat(face_component, 3) * welded_count + topology.welded.reshape(-1).astype(np.int64))
    vertex_component = keys // welded_count
    vertices = keys % welded_count
    vertex_offsets = np.searchsorted(vertex_component, np.arange(count + 1))
    component_points = points[vertices]
    low = np.minimum.reduceat(component_points, vertex_offsets[:-1], axis=0)
    high = np.maximum.reduceat(component_points, vertex_offsets[:-1], axis=0)
    eps = np.array([shell_eps(effective_resolution, m) for m in _component_median_edges(topology, count)])

    root = np.arange(count, dtype=np.int64)
    host_of: dict[int, tuple[int, float, float]] = {}
    trees: dict[int, cKDTree] = {}
    for component in range(1, count):
        own = component_points[vertex_offsets[component]:vertex_offsets[component + 1]]
        needed = math.ceil(coverage * own.shape[0])
        overlap = np.all(low[:component] - eps[:component, None] <= high[component], axis=1) & np.all(
            high[:component] + eps[:component, None] >= low[component], axis=1
        )
        best: tuple[int, float, float] | None = None
        for candidate in np.flatnonzero(overlap).tolist():
            margin = eps[candidate]
            inside = np.all((own >= low[candidate] - margin) & (own <= high[candidate] + margin), axis=1)
            if int(inside.sum()) < needed:
                continue
            tree = trees.get(candidate)
            if tree is None:
                tree = cKDTree(component_points[vertex_offsets[candidate]:vertex_offsets[candidate + 1]])
                trees[candidate] = tree
            distances, _ = tree.query(own, k=1, distance_upper_bound=margin, workers=-1)
            share = float(np.isfinite(distances).mean())
            if share >= coverage and (best is None or share > best[1]):
                best = (candidate, share, float(margin))
        if best is not None:
            root[component] = root[best[0]]
            host_of[component] = best

    group_faces = np.bincount(root, weights=components.face_counts, minlength=count).astype(np.int64)
    shells: list[ComponentShell] = []
    for component in range(count):
        faces = int(components.face_counts[component])
        area = float(components.areas[component])
        if component == 0:
            shells.append(ComponentShell(component, faces, area, "base"))
        elif component in host_of:
            tested, share, margin = host_of[component]
            shells.append(ComponentShell(component, faces, area, "inner_sheet", int(root[tested]), share, margin))
        elif group_faces[component] >= max_shell_fraction * face_count:
            shells.append(ComponentShell(component, faces, area, "major"))
        else:
            shells.append(ComponentShell(component, faces, area, "shell"))
    return ShellReport(shells=tuple(shells), face_count=face_count, root=root, group_faces=group_faces)


def _component_median_edges(topology: MeshTopology, count: int) -> np.ndarray:
    """Median welded edge length per component (0.0 for a component without edges)."""
    table = topology.edges
    if table.edge_count == 0:
        return np.zeros(count)
    lengths = edge_lengths(table, topology.welded_positions)
    edge_component = topology.components.face_component[table.first_face()].astype(np.int64)
    order = np.lexsort((lengths, edge_component))
    sorted_lengths = lengths[order]
    offsets = np.searchsorted(edge_component[order], np.arange(count + 1))
    sizes = np.diff(offsets)
    medians = np.zeros(count)
    has = sizes > 0
    upper = offsets[:-1] + sizes // 2
    lower = offsets[:-1] + (sizes - 1) // 2
    medians[has] = 0.5 * (sorted_lengths[lower[has]] + sorted_lengths[upper[has]])
    return medians


def pair_opposite_faces(
    centroids: np.ndarray,
    normals: np.ndarray,
    eps: float,
    *,
    k: int = PARTNER_NEIGHBOURS,
    chunk_size: int = _PARTNER_CHUNK,
) -> np.ndarray:
    """Opposite-sheet partner of every face, int32 ``[F]`` (:data:`NO_PARTNER` = none; section 4.6.5 step 2).

    Among the ``k`` nearest face centroids within ``eps`` (``cKDTree.query(k, distance_upper_bound=eps)``) the partner
    of f is the nearest g with ``dot(n_f, n_g) < -0.5`` and ``dot(c_g - c_f, n_f) < 0`` (g lies behind f). The rule is
    the same for outer and inner faces. ``normals`` are unit face normals; a degenerate (zero) normal gets no partner.
    """
    points = np.ascontiguousarray(centroids, dtype=np.float32)
    unit = np.ascontiguousarray(normals, dtype=np.float32)
    face_count = int(points.shape[0])
    if points.shape != (face_count, 3) or unit.shape != (face_count, 3):
        raise ValueError("centroids and normals must both have shape (F, 3)")
    partners = np.full(face_count, NO_PARTNER, dtype=np.int32)
    if face_count < 2 or eps <= 0.0:
        return partners
    neighbours = min(int(k), face_count)
    tree = cKDTree(points)
    for start in range(0, face_count, chunk_size):
        stop = min(start + chunk_size, face_count)
        _distances, index = tree.query(points[start:stop], k=neighbours, distance_upper_bound=float(eps), workers=-1)
        index = index.reshape(stop - start, neighbours)
        valid = index < face_count
        safe = np.where(valid, index, 0)
        own_normal = unit[start:stop]
        facing = np.einsum("mj,mkj->mk", own_normal, unit[safe])
        behind = np.einsum("mkj,mj->mk", points[safe] - points[start:stop, None, :], own_normal)
        accepted = valid & (facing < PARTNER_MAX_NORMAL_DOT) & (behind < 0.0)
        found = accepted.any(axis=1)
        first = accepted.argmax(axis=1)
        chunk = partners[start:stop]
        chunk[found] = index[found, first[found]]
    return partners


def partner_coverage(partners: np.ndarray, mask: np.ndarray | None = None) -> float:
    """Share of faces (of ``mask``, or of all faces) that have an opposite-sheet partner."""
    selected = np.asarray(partners) if mask is None else np.asarray(partners)[np.asarray(mask)]
    return float((selected != NO_PARTNER).mean()) if selected.size else 0.0
