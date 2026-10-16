"""Physical-versus-region policy and the face partition plan (part-aware DESIGN.md sections 2.4 and 4.6.5 step 6;
slice S3b).

* :func:`decide_kind` is the per-class rule of section 2.4: the kind comes from the policy, never from the segmenter.
* :func:`effective_min_part_faces` resolves ``min_part_faces = null`` to ``max(50, round(1e-4 * F))`` (section 4.6.2).
* :func:`plan_parts` turns the final per-face labels into the partition plan: which faces every physical part owns
  (``face_owner``), which records exist (base, physical, physical_deferred, region), which separate shells are free
  physical parts or floaters (rule 2), the part budget (rule 3), instance splitting (section 2.3) and the partner
  ownership rule (a face whose label came from its opposite-sheet partner always follows the partner's owner, so a cut
  takes both walls of a double-walled sheet). ``face_part`` (section 3.3) is filled from the records.

Nothing here touches geometry: no hole filling, remeshing or smoothing (rule 5). Torch-free: numpy and scipy only.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np
from scipy import sparse
from scipy.sparse import csgraph

from parts.components import MeshTopology
from parts.manifest import Floater, ManifestWarning
from parts.shells import SEPARATE_SHELL_MAX_FRACTION, ShellReport
from parts.taxonomy import (
    BODY,
    BY_VALUE,
    SEPARABLE_GROUP_IDS,
    UNKNOWN,
    PartClass,
    component_part_id,
    instance_part_id,
)
from parts.types import (
    BASE_MESH_INDEX,
    FACE_FLAG_PARTNER,
    FACE_PART_NONE,
    GeometrySource,
    Kind,
    StrategyId,
)

MIN_PART_FACES_FLOOR: Final = 50
MIN_PART_FACES_FRACTION: Final = 1e-4
REGION_GROUPS: Final = frozenset({"body", "face", "other"})
"""Groups that never become mesh nodes: continuous skin, face and body stay one mesh with regions (section 2.1)."""
TOO_SMALL_FOR_GEOMETRY: Final = "too_small_for_geometry"
MAX_PARTS_EXCEEDED: Final = "max_parts_exceeded"
FLOATER_CANDIDATES: Final = "floater_candidates"
CUTTING_STRATEGIES: Final = frozenset({"components", "semantic_parts"})
"""``semantic_regions`` labels every face but never cuts (one node, regions only; section 4.6.1)."""


def effective_min_part_faces(requested: int | None, face_count: int) -> int:
    """The request value, or ``max(50, round(1e-4 * F))`` when it is ``None`` (200 at 2 M faces, 50 at 500 k)."""
    if requested is not None:
        if requested < 0:
            raise ValueError("min_part_faces must be >= 0")
        return int(requested)
    return max(MIN_PART_FACES_FLOOR, int(round(MIN_PART_FACES_FRACTION * face_count)))


def decide_kind(
    cls: PartClass, separate: frozenset[str], faces: int, min_part_faces: int
) -> tuple[Kind, str | None]:
    """Kind of one class instance and the reason when it is downgraded (section 2.4).

    ``min_part_faces`` is the EFFECTIVE value. A physical class that is too small becomes ``physical_deferred`` with
    ``too_small_for_geometry``; it is never silently dropped.
    """
    if cls.id == BODY.id:
        return "base", None
    if cls.group in REGION_GROUPS:
        return "region", None
    if cls.group not in separate:
        return "region", None
    if cls.default_kind == "physical_deferred":
        return "physical_deferred", None
    if faces < max(min_part_faces, 1):
        return "physical_deferred", TOO_SMALL_FOR_GEOMETRY
    return "physical", None


# --------------------------------------------------------------------------------------------------
# Plan records
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, eq=False)
class PlannedPart:
    """One part record of the plan.

    * ``faces`` (int64, ascending): the faces the record covers. For a mesh-owning record (base, physical) these are
      the faces of its node that carry its class value; for a region or deferred record, its faces on its host.
    * ``mesh_index``: own node (base and physical) or ``None``; ``host_index``: the node that owns ``faces``.
    * ``value``: the region value (the class value; ``0`` for ``component_###`` shells).
    """

    part_id: str
    cls: PartClass
    kind: Kind
    value: int
    faces: np.ndarray
    mesh_index: int | None
    host_index: int
    instance: int
    source: GeometrySource
    components: int
    reason: str | None = None
    shell_roots: tuple[int, ...] = ()

    @property
    def owns_mesh(self) -> bool:
        return self.kind in ("base", "physical")

    @property
    def is_component(self) -> bool:
        """A geometry-only ``component_###`` shell (unknown class)."""
        return self.cls.id == UNKNOWN.id

    @property
    def seamless(self) -> bool:
        """A physical part made only of whole separate shells (no cut, so no seam)."""
        return self.kind == "physical" and bool(self.shell_roots)


@dataclass(frozen=True, slots=True, eq=False)
class PartPlan:
    """Partition plan in base face space.

    ``face_owner``/``face_labels``/``face_part`` are uint16 ``[F]``. ``parts`` is in manifest order (base, physical
    by mesh index, deferred, regions); ``mesh_part_ids[k]`` is the part id of mesh index ``k``. ``face_labels`` is the
    input labelling with floater shells reset to ``unknown`` (they stay on the base, owner 0, label 0).
    """

    face_owner: np.ndarray
    face_labels: np.ndarray
    face_part: np.ndarray
    parts: tuple[PlannedPart, ...]
    mesh_part_ids: tuple[str, ...]
    floaters: tuple[Floater, ...]
    warnings: tuple[ManifestWarning, ...]
    min_part_faces: int
    strategy: StrategyId

    def part(self, part_id: str) -> PlannedPart:
        for part in self.parts:
            if part.part_id == part_id:
                return part
        raise KeyError(part_id)

    def index_of(self, part_id: str) -> int:
        for index, part in enumerate(self.parts):
            if part.part_id == part_id:
                return index
        raise KeyError(part_id)

    @property
    def node_count(self) -> int:
        return len(self.mesh_part_ids)


@dataclass(slots=True)
class _Candidate:
    cls: PartClass
    value: int
    instance: int
    faces: np.ndarray
    components: int
    source: GeometrySource
    shell_roots: tuple[int, ...]
    part_id: str = ""
    kind: Kind = "physical"
    reason: str | None = None


# --------------------------------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------------------------------


def plan_parts(
    topology: MeshTopology,
    shells: ShellReport,
    face_labels: np.ndarray,
    *,
    strategy: StrategyId,
    separate: Iterable[str],
    min_part_faces: int,
    max_parts: int,
    split_shells: bool = True,
    face_flags: np.ndarray | None = None,
    donor: np.ndarray | None = None,
) -> PartPlan:
    """Build the partition plan (section 2.4 rules 1-5 and section 4.6.5 step 6).

    ``min_part_faces`` is the effective value (:func:`effective_min_part_faces`). ``face_flags``/``donor`` come from
    the label assignment: a face with ``face_flags`` bit 0 took its label from face ``donor[f]`` and follows that
    face's owner when both lie in the same welded shell.
    """
    if strategy == "per_part_regeneration":
        raise ValueError("per_part_regeneration is reserved and cannot be planned")
    face_count = topology.face_count
    labels = np.asarray(face_labels)
    if labels.shape != (face_count,):
        raise ValueError(f"face_labels must have shape ({face_count},)")
    labels = labels.astype(np.uint16, copy=True)
    unknown_values = sorted(set(np.unique(labels).tolist()) - set(BY_VALUE))
    if unknown_values:
        raise ValueError(f"face_labels holds values outside taxonomy v1: {unknown_values[:5]}")
    selected = frozenset(separate)
    bad_groups = sorted(selected - set(SEPARABLE_GROUP_IDS))
    if bad_groups:
        raise ValueError(f"separate holds groups that cannot be separated: {', '.join(bad_groups)}")
    if max_parts < 1:
        raise ValueError("max_parts must be >= 1")
    cuts = strategy in CUTTING_STRATEGIES
    warnings: list[ManifestWarning] = []

    face_component = topology.components.face_component.astype(np.int64)
    face_root = shells.face_root(face_component) if shells.root.size else np.zeros(face_count, dtype=np.int64)
    shell_roots = shells.separate_shells if split_shells else ()
    shell_mask = np.zeros(shells.root.shape[0], dtype=bool)
    shell_mask[list(shell_roots)] = True
    in_shell = shell_mask[face_root] if face_count else np.zeros(0, dtype=bool)

    # Rule 2: separate shells are decided as wholes by their majority lifted label.
    shell_class: dict[int, int] = {}
    component_roots: tuple[int, ...] = ()
    floaters: tuple[Floater, ...] = ()
    if shell_roots:
        majority = _shell_majorities(face_root[in_shell], labels[in_shell])
        unlabelled = [root for root in shell_roots if majority[root] == 0]
        for root in shell_roots:
            value = majority[root]
            if value and cuts and BY_VALUE[value].group in selected:
                shell_class[root] = value
        if unlabelled:
            component_roots, floaters = shells.split_floaters(topology, min_part_faces, candidates=unlabelled)
            floater_roots = np.zeros(shells.root.shape[0], dtype=bool)
            floater_roots[[floater.component for floater in floaters]] = True
            labels[floater_roots[face_root]] = UNKNOWN.value
            if not cuts:
                component_roots = ()
    if floaters:
        warnings.append(
            ManifestWarning(
                code=FLOATER_CANDIDATES,
                message=f"{len(floaters)} small unlabelled shell(s) stay on the base.",
                part_id=BODY.id,
            )
        )

    # Physical candidates: class instances (cut from the welded surface or taken as whole shells) and components.
    candidates: list[_Candidate] = []
    deferred: list[_Candidate] = []
    consumed: set[int] = set()
    present = [value for value in np.unique(labels).tolist() if value not in (UNKNOWN.value, BODY.value)]
    for value in present:
        cls = BY_VALUE[value]
        # Group-level rule first (the size check is applied per instance below).
        kind, _reason = decide_kind(cls, selected, max(face_count, min_part_faces, 1), min_part_faces)
        if kind == "region" or not cuts:
            continue
        roots = tuple(sorted(root for root, shell_value in shell_class.items() if shell_value == value))
        faces = np.flatnonzero((labels == value) & ~in_shell)
        if roots:
            root_mask = np.zeros(shells.root.shape[0], dtype=bool)
            root_mask[list(roots)] = True
            faces = np.union1d(faces, np.flatnonzero(root_mask[face_root]))
        if faces.size == 0:
            continue
        consumed.add(value)
        pieces = _instances(topology, faces, face_root, roots, cls, min_part_faces)
        for instance, (piece_faces, piece_count, piece_roots) in enumerate(pieces, start=1):
            candidate = _Candidate(cls, value, instance, piece_faces, piece_count, "lifted", piece_roots)
            candidate.kind, candidate.reason = decide_kind(cls, selected, int(piece_faces.size), min_part_faces)
            (candidates if candidate.kind == "physical" else deferred).append(candidate)
    for number, root in enumerate(component_roots, start=1):
        members = shells.members(root)
        faces = np.flatnonzero(np.isin(face_component, members))
        candidates.append(_Candidate(UNKNOWN, UNKNOWN.value, number, faces, len(members), "component", (root,)))

    # Instance ids: first instance = class id, then _2, _3 ... (largest first), across physical and deferred.
    for candidate in (*candidates, *deferred):
        if candidate.cls.id == UNKNOWN.id:
            candidate.part_id = component_part_id(candidate.instance)
        else:
            candidate.part_id = instance_part_id(candidate.cls.id, candidate.instance)
    for candidate in deferred:
        if candidate.reason == TOO_SMALL_FOR_GEOMETRY:
            warnings.append(
                ManifestWarning(
                    code=TOO_SMALL_FOR_GEOMETRY,
                    message=f"{candidate.part_id} has {candidate.faces.size} faces (< {max(min_part_faces, 1)}); "
                    "it is kept as a deferred part record.",
                    part_id=candidate.part_id,
                )
            )

    # Rule 3: the part budget. The smallest extra parts stay on the base as region records keeping their value.
    overflow: list[_Candidate] = []
    if len(candidates) > max_parts:
        ranked = sorted(candidates, key=lambda c: (-int(c.faces.size), c.value, c.instance))
        overflow = ranked[max_parts:]
        for candidate in overflow:
            candidate.kind, candidate.reason = "region", MAX_PARTS_EXCEEDED
        candidates = ranked[:max_parts]
        warnings.append(
            ManifestWarning(
                code=MAX_PARTS_EXCEEDED,
                message=f"{len(overflow)} physical part(s) beyond max_parts={max_parts} stay on the base as regions.",
                part_id=None,
            )
        )
    candidates.sort(key=lambda c: (c.value, c.instance))

    # face_owner, then the partner ownership rule (both walls of a cut sheet follow the voted wall).
    owner = np.zeros(face_count, dtype=np.int64)
    for mesh_index, candidate in enumerate(candidates, start=1):
        owner[candidate.faces] = mesh_index
    if face_flags is not None and donor is not None:
        owner = _apply_partner_ownership(owner, np.asarray(face_flags), np.asarray(donor), face_root, in_shell)
    counts = np.bincount(owner, minlength=len(candidates) + 1)
    kept_indices = [index for index in range(1, len(candidates) + 1) if counts[index] > 0]
    if len(kept_indices) != len(candidates):
        # A node the partner rule emptied disappears; its labelled faces become region faces of their new owner.
        remap = np.zeros(len(candidates) + 1, dtype=np.int64)
        remap[kept_indices] = np.arange(1, len(kept_indices) + 1)
        owner = remap[owner]
        candidates = [candidates[index - 1] for index in kept_indices]

    face_owner = owner.astype(np.uint16)
    parts: list[PlannedPart] = []
    base_faces = np.flatnonzero((owner == BASE_MESH_INDEX) & (labels == BODY.value))
    parts.append(
        PlannedPart(
            part_id=BODY.id,
            cls=BODY,
            kind="base",
            value=BODY.value,
            faces=base_faces,
            mesh_index=BASE_MESH_INDEX,
            host_index=BASE_MESH_INDEX,
            instance=1,
            source="component",
            components=_component_count(face_component, np.flatnonzero(owner == BASE_MESH_INDEX)),
        )
    )
    for mesh_index, candidate in enumerate(candidates, start=1):
        node_faces = np.flatnonzero(owner == mesh_index)
        parts.append(
            PlannedPart(
                part_id=candidate.part_id,
                cls=candidate.cls,
                kind="physical",
                value=candidate.value,
                faces=node_faces[labels[node_faces] == candidate.value],
                mesh_index=mesh_index,
                host_index=mesh_index,
                instance=candidate.instance,
                source=candidate.source,
                components=_component_count(face_component, node_faces),
                shell_roots=candidate.shell_roots,
            )
        )

    # Deferred records, overflow regions and plain regions live on the node that owns most of their faces.
    for candidate in sorted(deferred, key=lambda c: (c.value, c.instance)):
        parts.append(_hosted(candidate, candidate.faces, owner, labels, face_component, cuts))
    regions: list[PlannedPart] = []
    for candidate in overflow:
        regions.append(_hosted(candidate, candidate.faces, owner, labels, face_component, cuts))
    for value in present:
        if value in consumed:
            continue
        cls = BY_VALUE[value]
        faces = np.flatnonzero(labels == value)
        candidate = _Candidate(cls, value, 1, faces, 0, "lifted", (), part_id=cls.id, kind="region")
        regions.append(_hosted(candidate, faces, owner, labels, face_component, cuts))
    regions.sort(key=lambda p: (p.value, p.instance, p.part_id))
    parts.extend(regions)

    face_part = np.full(face_count, FACE_PART_NONE, dtype=np.uint16)
    for index, part in enumerate(parts):
        if part.owns_mesh:
            face_part[part.faces] = index
    for index, part in enumerate(parts):
        if not part.owns_mesh:
            face_part[part.faces] = index
    if len(parts) >= FACE_PART_NONE:
        raise ValueError(f"too many part records ({len(parts)}) for the uint16 face_part buffer")

    return PartPlan(
        face_owner=face_owner,
        face_labels=labels,
        face_part=face_part,
        parts=tuple(parts),
        mesh_part_ids=(BODY.id, *(candidate.part_id for candidate in candidates)),
        floaters=floaters,
        warnings=tuple(warnings),
        min_part_faces=int(min_part_faces),
        strategy=strategy,
    )


def _shell_majorities(roots: np.ndarray, labels: np.ndarray) -> dict[int, int]:
    """Majority label (ties: the lower value) of every separate shell root (its merged inner sheets included)."""
    if roots.size == 0:
        return {}
    keys = roots.astype(np.int64) * 0x10000 + labels.astype(np.int64)
    unique, counts = np.unique(keys, return_counts=True)
    unique_roots = unique // 0x10000
    unique_labels = unique % 0x10000
    order = np.lexsort((unique_labels, -counts, unique_roots))
    first = np.ones(order.shape[0], dtype=bool)
    first[1:] = unique_roots[order][1:] != unique_roots[order][:-1]
    chosen = order[first]
    return {int(root): int(label) for root, label in zip(unique_roots[chosen], unique_labels[chosen], strict=True)}


def _instances(
    topology: MeshTopology,
    faces: np.ndarray,
    face_root: np.ndarray,
    roots: tuple[int, ...],
    cls: PartClass,
    min_part_faces: int,
) -> list[tuple[np.ndarray, int, tuple[int, ...]]]:
    """Split the faces of one class into instances (section 2.3).

    Connected pieces are taken over the welded adjacency restricted to ``faces``; a whole separate shell (with its
    detached inner sheets) counts as one piece. ``instance_split`` classes make every piece with at least
    ``min_part_faces`` faces its own instance (largest first) and join the smaller pieces to the largest one; every
    other class is one instance. Returns ``(faces, welded component count, shell roots)`` per instance.
    """
    face_count = topology.face_count
    local = np.full(face_count, -1, dtype=np.int64)
    local[faces] = np.arange(faces.size, dtype=np.int64)
    adjacency = topology.adjacency.tocoo()
    keep = (local[adjacency.row] >= 0) & (local[adjacency.col] >= 0)
    rows = [local[adjacency.row[keep]]]
    cols = [local[adjacency.col[keep]]]
    piece_roots = face_root[faces]
    root_set = np.zeros(int(face_root.max(initial=0)) + 1, dtype=bool)
    root_set[list(roots)] = True
    whole = root_set[piece_roots] if roots else np.zeros(faces.size, dtype=bool)
    if whole.any():
        members = np.flatnonzero(whole)
        anchor_of_root: dict[int, int] = {}
        anchors = np.empty(members.size, dtype=np.int64)
        for position, member in enumerate(members.tolist()):
            anchors[position] = anchor_of_root.setdefault(int(piece_roots[member]), member)
        rows.append(members)
        cols.append(anchors)
    row = np.concatenate(rows)
    col = np.concatenate(cols)
    graph = sparse.coo_array((np.ones(row.size, dtype=np.int8), (row, col)), shape=(faces.size, faces.size)).tocsr()
    count, piece = csgraph.connected_components(graph, directed=False)
    sizes = np.bincount(piece, minlength=count)
    first_face = np.full(count, faces.size, dtype=np.int64)
    np.minimum.at(first_face, piece, np.arange(faces.size, dtype=np.int64))
    order = np.lexsort((first_face, -sizes))
    component_of = topology.components.face_component.astype(np.int64)[faces]

    def describe(selection: np.ndarray) -> tuple[np.ndarray, int, tuple[int, ...]]:
        mask = np.isin(piece, selection)
        chosen = faces[mask]
        shell = tuple(sorted({int(r) for r in piece_roots[mask & whole].tolist()}))
        return chosen, int(np.unique(component_of[mask]).size), shell

    if not cls.instance_split:
        return [describe(order)]
    large = [p for p in order.tolist() if sizes[p] >= max(min_part_faces, 1)]
    if not large:
        return [describe(order)]
    small = np.asarray([p for p in order.tolist() if sizes[p] < max(min_part_faces, 1)], dtype=np.int64)
    result = [describe(np.concatenate([np.asarray([large[0]]), small]))]
    result.extend(describe(np.asarray([p])) for p in large[1:])
    return result


def _apply_partner_ownership(
    owner: np.ndarray, flags: np.ndarray, donor: np.ndarray, face_root: np.ndarray, in_shell: np.ndarray
) -> np.ndarray:
    """A partner-filled face (``face_flags`` bit 0) takes its donor's owner when both lie in the same welded shell.

    Separate shells are decided as wholes (rule 2), so faces inside them keep their owner.
    """
    face_count = owner.shape[0]
    if flags.shape != (face_count,) or donor.shape != (face_count,):
        raise ValueError("face_flags and donor must be per-face arrays")
    filled = np.flatnonzero(((flags & FACE_FLAG_PARTNER) != 0) & (donor >= 0) & ~in_shell)
    if filled.size == 0:
        return owner
    source = donor[filled].astype(np.int64)
    same_shell = face_root[filled] == face_root[source]
    result = owner.copy()
    result[filled[same_shell]] = owner[source[same_shell]]
    return result


def _component_count(face_component: np.ndarray, faces: np.ndarray) -> int:
    return int(np.unique(face_component[faces]).size) if faces.size else 0


def _hosted(
    candidate: _Candidate,
    faces: np.ndarray,
    owner: np.ndarray,
    labels: np.ndarray,
    face_component: np.ndarray,
    cuts: bool,
) -> PlannedPart:
    """A region or deferred record on the node owning most of its faces (ties: the lower mesh index)."""
    faces = np.asarray(faces, dtype=np.int64)
    host = int(np.bincount(owner[faces]).argmax()) if faces.size else BASE_MESH_INDEX
    covered = faces[(owner[faces] == host) & (labels[faces] == candidate.value)]
    kind: Kind = candidate.kind if cuts else "region"
    if kind == "physical":
        kind = "region"
    return PlannedPart(
        part_id=candidate.part_id,
        cls=candidate.cls,
        kind=kind,
        value=candidate.value,
        faces=covered,
        mesh_index=None,
        host_index=host,
        instance=candidate.instance,
        source=candidate.source,
        components=_component_count(face_component, covered),
        reason=candidate.reason,
        shell_roots=(),
    )


def kinds_summary(parts: Sequence[PlannedPart]) -> dict[str, int]:
    """``{physical, physical_deferred, region}`` record counts (the base is not counted)."""
    result = {"physical": 0, "physical_deferred": 0, "region": 0}
    for part in parts:
        if part.kind in result:
            result[part.kind] += 1
    return result


__all__ = [
    "CUTTING_STRATEGIES",
    "FLOATER_CANDIDATES",
    "MAX_PARTS_EXCEEDED",
    "MIN_PART_FACES_FLOOR",
    "MIN_PART_FACES_FRACTION",
    "REGION_GROUPS",
    "SEPARATE_SHELL_MAX_FRACTION",
    "TOO_SMALL_FOR_GEOMETRY",
    "PartPlan",
    "PlannedPart",
    "decide_kind",
    "effective_min_part_faces",
    "kinds_summary",
    "plan_parts",
]
