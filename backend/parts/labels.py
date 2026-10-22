"""Face votes and label assignment (part-aware DESIGN.md section 4.6.5 steps 4 and 5; slice S3b).

* :class:`VoteTable` accumulates the face-ID votes of every lifting view as sparse ``(face, label, weight)`` entries:
  a pixel with label ``k``, confidence ``c`` and view cosine ``|cos|`` adds ``w_view * c * |cos|`` to ``votes[f, k]``.
* :func:`assign_labels` runs the assignment steps in order:

  1. argmax per face where the total vote is >= 0.2;
  2. partner fill: an unvoted face whose opposite-sheet partner is voted takes the partner's label (confidence x 0.9,
     ``face_flags`` bit 0); faces of detached inner-sheet components that still have no label take the label of the
     nearest voted face centroid within ``shell_eps`` (also bit 0). Single pass: only voted faces donate;
  3. multi-source Dijkstra over the welded adjacency from the labelled faces, edge cost ``1 + (dE_Lab / 10)^2``; a
     face is assigned when its cost is <= 64 (bit 1);
  4. mirror completion of sided pairs when the mirrored mesh error is <= ``2 / effective_resolution``;
  5. two passes of 1-ring majority smoothing;
  6. component fill of ``unknown`` faces in small welded components (< 25 % of F, never component 0) where >= 90 % of
     the labelled faces agree (bit 2);
  7. island merge of labelled islands under ``min_island_faces = max(8, round(2e-5 * F))`` faces into the
     surrounding majority; eyes, eyebrows, eyelashes and face regions are never merged;
  8. confidence = winning vote share x 0.5 when the camera fit was rejected.

Torch-free: numpy and scipy only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

import numpy as np
from scipy import sparse
from scipy.sparse import csgraph
from scipy.spatial import cKDTree

from parts.components import MeshTopology
from parts.shells import NO_PARTNER, ShellReport, global_shell_eps
from parts.taxonomy import BY_VALUE, CLASSES, UNKNOWN
from parts.types import FACE_FLAG_COMPONENT_FILL, FACE_FLAG_PARTNER, FACE_FLAG_PROPAGATED

VOTE_MIN_TOTAL: Final = 0.2
PARTNER_EDGE_COST: Final = 1e-6
"""Propagation cost between opposite-sheet partners (step 5.3): the two walls of a thin sheet act as one surface."""
PARTNER_AGREEMENT_PASSES: Final = 3
PARTNER_CONFIDENCE: Final = 0.9
PROPAGATION_MAX_COST: Final = 64.0
PROPAGATION_LAB_SCALE: Final = 10.0
MIRROR_EPS_VOXELS: Final = 2.0
MIRROR_MIN_NORMAL_DOT: Final = 0.5
MIRROR_TRIGGER_RATIO: Final = 0.5
"""A sided class is mirrored when its counterpart has fewer than half its faces."""
SMOOTHING_PASSES: Final = 2
COMPONENT_FILL_MAX_FRACTION: Final = 0.25
COMPONENT_FILL_AGREEMENT: Final = 0.90
ISLAND_MIN_FACES_FLOOR: Final = 8
ISLAND_FACE_FRACTION: Final = 2e-5
PROTECTED_ISLAND_GROUPS: Final = frozenset({"eyes", "eyebrows", "eyelashes", "face"})
REJECTED_FIT_FACTOR: Final = 0.5
GEOMETRY_CONFIDENCE: Final = 255
"""``face_confidence`` of a geometry-only analysis (the ``components`` strategy)."""
_KEY_SHIFT: Final = 0x10000
_COMPACT_ENTRIES: Final = 1 << 23


def min_island_faces(face_count: int) -> int:
    """``max(8, round(2e-5 * F))``: 40 at 2 M faces, 10 at 500 k."""
    return max(ISLAND_MIN_FACES_FLOOR, int(round(ISLAND_FACE_FRACTION * face_count)))


# --------------------------------------------------------------------------------------------------
# Votes (step 4)
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, eq=False)
class VoteSummary:
    """Per-face vote totals: ``winner`` uint16 (0 = no vote), ``winner_weight`` and ``total`` float32 ``[F]``."""

    winner: np.ndarray
    winner_weight: np.ndarray
    total: np.ndarray

    @property
    def face_count(self) -> int:
        return int(self.winner.shape[0])

    @classmethod
    def empty(cls, face_count: int) -> VoteSummary:
        return cls(
            winner=np.zeros(face_count, dtype=np.uint16),
            winner_weight=np.zeros(face_count, dtype=np.float32),
            total=np.zeros(face_count, dtype=np.float32),
        )


class VoteTable:
    """Sparse ``(face, label)`` vote accumulator over every lifting view."""

    def __init__(self, face_count: int) -> None:
        if face_count < 0:
            raise ValueError("face_count must be >= 0")
        self.face_count = int(face_count)
        self._keys: list[np.ndarray] = []
        self._weights: list[np.ndarray] = []
        self._pending = 0
        self.pixels = 0

    def add(self, faces: np.ndarray, labels: np.ndarray, weights: np.ndarray) -> None:
        """Add one vote per entry. Entries with a face outside ``[0, F)``, label 0 or weight <= 0 are ignored."""
        faces = np.asarray(faces, dtype=np.int64).reshape(-1)
        labels = np.asarray(labels, dtype=np.int64).reshape(-1)
        weights = np.asarray(weights, dtype=np.float64).reshape(-1)
        if not faces.shape == labels.shape == weights.shape:
            raise ValueError("faces, labels and weights must have the same length")
        keep = (faces >= 0) & (faces < self.face_count) & (labels > 0) & (labels < _KEY_SHIFT) & (weights > 0.0)
        if not keep.any():
            return
        self._keys.append(faces[keep] * _KEY_SHIFT + labels[keep])
        self._weights.append(weights[keep])
        self._pending += int(keep.sum())
        self.pixels += int(keep.sum())
        if self._pending > _COMPACT_ENTRIES:
            self._compact()

    def add_view(
        self,
        face_id: np.ndarray,
        cos: np.ndarray,
        labels: np.ndarray,
        *,
        weight: float,
        confidence: np.ndarray | float = 1.0,
    ) -> None:
        """Votes of one rendered view: ``face_id`` int ``[H, W]`` (-1 = background), ``cos`` the view cosine,
        ``labels`` the taxonomy value per pixel (0 = no class), ``confidence`` per pixel or scalar, ``weight`` the
        view weight (1.0 for the input view, 0.5 for extra views)."""
        face_id = np.asarray(face_id)
        if face_id.shape != np.shape(labels) or face_id.shape != np.shape(cos):
            raise ValueError("face_id, cos and labels must have the same shape")
        votes = float(weight) * np.abs(np.asarray(cos, dtype=np.float64))
        if np.ndim(confidence):
            if np.shape(confidence) != face_id.shape:
                raise ValueError("confidence must be a scalar or have the shape of face_id")
            votes = votes * np.asarray(confidence, dtype=np.float64)
        else:
            votes = votes * float(confidence)
        self.add(face_id, labels, votes)

    def _compact(self) -> None:
        if not self._keys:
            return
        keys = np.concatenate(self._keys)
        weights = np.concatenate(self._weights)
        unique, inverse = np.unique(keys, return_inverse=True)
        self._keys = [unique]
        self._weights = [np.bincount(inverse.reshape(-1), weights=weights)]
        self._pending = 0

    def summary(self) -> VoteSummary:
        """Winner label per face (ties: the lower value), its weight and the face's total vote."""
        self._compact()
        result = VoteSummary.empty(self.face_count)
        if not self._keys or self._keys[0].size == 0:
            return result
        keys, weights = self._keys[0], self._weights[0]
        faces = keys // _KEY_SHIFT
        labels = keys % _KEY_SHIFT
        total = np.bincount(faces, weights=weights, minlength=self.face_count)
        order = np.lexsort((labels, -weights, faces))
        first = np.ones(order.size, dtype=bool)
        first[1:] = faces[order][1:] != faces[order][:-1]
        chosen = order[first]
        result.winner[faces[chosen]] = labels[chosen].astype(np.uint16)
        result.winner_weight[faces[chosen]] = weights[chosen].astype(np.float32)
        result.total[:] = total.astype(np.float32)
        return result


# --------------------------------------------------------------------------------------------------
# Assignment (step 5)
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, eq=False)
class LabelAssignment:
    """Final per-face labels.

    * ``labels`` uint16 ``[F]`` (0 = unknown), ``confidence`` float32 in ``[0, 1]``, ``flags`` uint8 (bits of
      ``face_flags``), ``votes`` float32 (winning vote weight of a voted face, 0 elsewhere).
    * ``donor`` int32: the face a partner-filled face (bit 0) took its label from, -1 elsewhere. The partner
      ownership rule of the partition uses it.
    """

    labels: np.ndarray
    confidence: np.ndarray
    flags: np.ndarray
    donor: np.ndarray
    votes: np.ndarray
    stats: dict[str, float] = field(default_factory=dict)

    @property
    def face_count(self) -> int:
        return int(self.labels.shape[0])

    def confidence_u8(self) -> np.ndarray:
        """``face_confidence`` buffer: confidence x 255, rounded."""
        return np.clip(np.rint(self.confidence.astype(np.float64) * 255.0), 0, 255).astype(np.uint8)

    @property
    def unlabelled_face_ratio(self) -> float:
        return float((self.labels == UNKNOWN.value).mean()) if self.face_count else 0.0

    @property
    def partner_filled_ratio(self) -> float:
        return float(((self.flags & FACE_FLAG_PARTNER) != 0).mean()) if self.face_count else 0.0


def unlabelled_assignment(face_count: int) -> LabelAssignment:
    """Assignment of the geometry-only ``components`` strategy: every face ``unknown`` with geometric certainty."""
    return LabelAssignment(
        labels=np.zeros(face_count, dtype=np.uint16),
        confidence=np.ones(face_count, dtype=np.float32),
        flags=np.zeros(face_count, dtype=np.uint8),
        donor=np.full(face_count, -1, dtype=np.int32),
        votes=np.zeros(face_count, dtype=np.float32),
        stats={"voted": 0.0},
    )


def assign_labels(
    votes: VoteSummary,
    topology: MeshTopology,
    *,
    partners: np.ndarray,
    shells: ShellReport,
    effective_resolution: int,
    fit_accepted: bool = True,
    face_lab: np.ndarray | None = None,
    shell_eps: float | None = None,
) -> LabelAssignment:
    """Run steps 5.1-5.8. ``partners`` comes from ``pair_opposite_faces``; ``face_lab`` (float ``[F, 3]`` CIE Lab
    base colour per face) weights the propagation and may be ``None`` (uniform cost)."""
    face_count = topology.face_count
    if votes.face_count != face_count:
        raise ValueError("votes and topology have different face counts")
    partners = np.asarray(partners, dtype=np.int64)
    if partners.shape != (face_count,):
        raise ValueError(f"partners must have shape ({face_count},)")
    if effective_resolution <= 0:
        raise ValueError("effective_resolution must be positive")
    eps = global_shell_eps(topology, effective_resolution) if shell_eps is None else float(shell_eps)
    fit_factor = 1.0 if fit_accepted else REJECTED_FIT_FACTOR
    stats: dict[str, float] = {}

    # 5.1 argmax where the total vote is >= 0.2; confidence = winning share (x 0.5 when the fit was rejected).
    voted = (votes.total >= VOTE_MIN_TOTAL) & (votes.winner > 0)
    labels = np.where(voted, votes.winner, 0).astype(np.uint16)
    share = np.divide(votes.winner_weight, votes.total, out=np.zeros(face_count, dtype=np.float32),
                      where=votes.total > 0)
    confidence = np.where(voted, share * fit_factor, 0.0).astype(np.float32)
    flags = np.zeros(face_count, dtype=np.uint8)
    donor = np.full(face_count, -1, dtype=np.int32)
    stats["voted"] = float(voted.sum())

    # 5.2 partner fill: only voted faces donate (single pass).
    has_partner = partners != NO_PARTNER
    safe_partner = np.where(has_partner, partners, 0)
    # Both walls voted with different classes: the two walls of one thin sheet cannot be two classes, so the wall
    # with the weaker winning vote takes its partner's label (partner-filled, donor = the partner).
    weight = np.asarray(votes.winner_weight, dtype=np.float64)
    other = safe_partner
    conflict = voted & has_partner & voted[other] & (labels != labels[other])
    weaker = conflict & ((weight < weight[other]) | ((weight == weight[other]) & (np.arange(face_count) > other)))
    original = labels.copy()
    labels[weaker] = original[other[weaker]]
    confidence[weaker] = confidence[other[weaker]] * PARTNER_CONFIDENCE
    flags[weaker] |= FACE_FLAG_PARTNER
    donor[weaker] = other[weaker]
    stats["partner_conflicts"] = float(weaker.sum())
    fill = ~voted & has_partner & voted[safe_partner]
    labels[fill] = labels[safe_partner[fill]]
    confidence[fill] = confidence[safe_partner[fill]] * PARTNER_CONFIDENCE
    flags[fill] |= FACE_FLAG_PARTNER
    donor[fill] = safe_partner[fill]
    stats["partner_filled"] = float(fill.sum())
    if shells.root.size:
        inner = shells.inner_sheet_face_mask(topology.components.face_component) & (labels == 0)
        if inner.any() and voted.any():
            voted_faces = np.flatnonzero(voted)
            tree = cKDTree(topology.geometry.centroids[voted_faces])
            distance, nearest = tree.query(topology.geometry.centroids[inner], k=1, distance_upper_bound=eps,
                                           workers=-1)
            found = np.isfinite(distance)
            targets = np.flatnonzero(inner)[found]
            sources = voted_faces[nearest[found]]
            labels[targets] = labels[sources]
            confidence[targets] = confidence[sources] * PARTNER_CONFIDENCE
            flags[targets] |= FACE_FLAG_PARTNER
            donor[targets] = sources
            stats["inner_sheet_filled"] = float(targets.size)

    # 5.3 colour-affinity propagation (multi-source Dijkstra, cost limit 64).
    propagated = _propagate(topology, labels, confidence, face_lab, partners)
    flags[propagated] |= FACE_FLAG_PROPAGATED
    stats["propagated"] = float(propagated.sum())

    # 5.4 mirror completion of sided pairs.
    mirrored = _mirror_complete(topology, labels, confidence, flags, voted, effective_resolution)
    flags[mirrored] |= FACE_FLAG_PROPAGATED
    stats["mirrored"] = float(mirrored.sum())

    # 5.5 two passes of 1-ring majority smoothing.
    for _ in range(SMOOTHING_PASSES):
        stats["smoothed"] = stats.get("smoothed", 0.0) + float(_smooth_once(topology.adjacency, labels))

    # 5.6 component fill (never component 0, never over a label).
    filled = _component_fill(topology, labels, confidence, fit_factor)
    flags[filled] |= FACE_FLAG_COMPONENT_FILL
    stats["component_filled"] = float(filled.sum())

    # 5.7 island merge.
    stats["island_merged"] = float(_merge_islands(topology.adjacency, labels, min_island_faces(face_count)))

    # Partner agreement: an unvoted wall follows its opposite-sheet partner (either direction of the pairing),
    # voted partners first, then the more confident one, so both walls of a thin sheet end with one label.
    stats["partner_agreed"] = float(_partner_agreement(labels, confidence, flags, donor, partners, voted))

    confidence[labels == 0] = 0.0
    return LabelAssignment(
        labels=labels,
        confidence=np.clip(confidence, 0.0, 1.0).astype(np.float32),
        flags=flags,
        donor=donor,
        votes=np.where(voted, votes.winner_weight, 0.0).astype(np.float32),
        stats=stats,
    )


def _partner_agreement(
    labels: np.ndarray,
    confidence: np.ndarray,
    flags: np.ndarray,
    donor: np.ndarray,
    partners: np.ndarray,
    voted: np.ndarray,
) -> int:
    """In place: every unvoted face linked to a partner with another label takes the best linked label.

    Links are the partner pairs in both directions. A voted partner outranks an unvoted one; among equals the higher
    confidence wins (ties: the lower face index). Up to :data:`PARTNER_AGREEMENT_PASSES` passes; returns the number
    of faces changed (partner-filled, donor = the partner).
    """
    source = np.flatnonzero(partners != NO_PARTNER)
    if source.size == 0:
        return 0
    target = partners[source].astype(np.int64)
    a = np.concatenate([source, target])
    b = np.concatenate([target, source])
    keep = ~voted[a]
    a, b = a[keep], b[keep]
    changed = np.zeros(labels.shape[0], dtype=bool)
    for _ in range(PARTNER_AGREEMENT_PASSES):
        priority_b = voted[b] * 2.0 + confidence[b].astype(np.float64)
        priority_a = confidence[a].astype(np.float64) * (labels[a] > 0)
        better = (priority_b > priority_a) | ((priority_b == priority_a) & (b < a))
        candidate = (labels[b] > 0) & (labels[b] != labels[a]) & better
        if not candidate.any():
            break
        ca, cb, cp = a[candidate], b[candidate], priority_b[candidate]
        order = np.lexsort((cb, -cp, ca))
        first = np.ones(order.size, dtype=bool)
        first[1:] = ca[order][1:] != ca[order][:-1]
        faces, sources = ca[order][first], cb[order][first]
        labels[faces] = labels[sources]
        confidence[faces] = confidence[sources] * PARTNER_CONFIDENCE
        flags[faces] |= FACE_FLAG_PARTNER
        donor[faces] = sources
        changed[faces] = True
    return int(changed.sum())


def _propagate(
    topology: MeshTopology,
    labels: np.ndarray,
    confidence: np.ndarray,
    face_lab: np.ndarray | None,
    partners: np.ndarray | None = None,
) -> np.ndarray:
    """Step 5.3 in place; returns the mask of propagated faces. Confidence decays to half at the cost limit.

    The two walls of a thin sheet (``partners``) are linked at :data:`PARTNER_EDGE_COST`, so an unvoted wall pair
    is reached from the same source and keeps one label (a cut then takes both walls).
    """
    face_count = topology.face_count
    sources = np.flatnonzero(labels > 0)
    if sources.size == 0 or sources.size == face_count:
        return np.zeros(face_count, dtype=bool)
    adjacency = topology.adjacency.tocoo()
    row = adjacency.row.astype(np.int64)
    col = adjacency.col.astype(np.int64)
    if face_lab is not None:
        lab = np.asarray(face_lab, dtype=np.float32)
        if lab.shape != (face_count, 3):
            raise ValueError(f"face_lab must have shape ({face_count}, 3)")
        delta = np.linalg.norm(lab[row] - lab[col], axis=1).astype(np.float64)
        cost = 1.0 + (delta / PROPAGATION_LAB_SCALE) ** 2
    else:
        cost = np.ones(row.size, dtype=np.float64)
    if partners is not None:
        partner = np.asarray(partners, dtype=np.int64)
        linked = np.flatnonzero(partner >= 0)
        row = np.concatenate([row, linked, partner[linked]])
        col = np.concatenate([col, partner[linked], linked])
        cost = np.concatenate([cost, np.full(2 * linked.size, PARTNER_EDGE_COST)])
    graph = sparse.csr_array((cost, (row, col)), shape=(face_count, face_count))
    distance, _predecessors, origin = csgraph.dijkstra(
        graph, directed=True, indices=sources, min_only=True, limit=PROPAGATION_MAX_COST, return_predecessors=True
    )
    reached = (labels == 0) & np.isfinite(distance) & (distance <= PROPAGATION_MAX_COST) & (origin >= 0)
    donors = origin[reached].astype(np.int64)
    labels[reached] = labels[donors]
    decay = 1.0 - 0.5 * (distance[reached] / PROPAGATION_MAX_COST)
    confidence[reached] = (confidence[donors] * decay).astype(np.float32)
    return reached


def _sided_pairs() -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    for cls in CLASSES:
        if cls.sided and cls.side == "left" and cls.symmetric_with:
            right = next(other for other in CLASSES if other.id == cls.symmetric_with)
            pairs.append((cls.value, right.value))
    return pairs


def _mirror_complete(
    topology: MeshTopology,
    labels: np.ndarray,
    confidence: np.ndarray,
    flags: np.ndarray,
    voted: np.ndarray,
    effective_resolution: int,
) -> np.ndarray:
    """Step 5.4 in place: when one side of a sided pair is (mostly) missing, the faces mirrored across x = 0 from the
    present side take the counterpart label, provided the mesh is symmetric there (median mirrored-centroid error
    <= 2 / effective_resolution). Only unknown or propagated faces are overwritten."""
    face_count = topology.face_count
    result = np.zeros(face_count, dtype=bool)
    threshold = MIRROR_EPS_VOXELS / effective_resolution
    eligible = ~voted & ((flags & FACE_FLAG_PARTNER) == 0)
    eligible &= (labels == 0) | ((flags & FACE_FLAG_PROPAGATED) != 0)
    tree: cKDTree | None = None
    centroids = topology.geometry.centroids
    normals = topology.geometry.normals
    for left, right in _sided_pairs():
        counts = {left: int((labels == left).sum()), right: int((labels == right).sum())}
        for source, target in ((left, right), (right, left)):
            if counts[source] == 0 or counts[target] >= MIRROR_TRIGGER_RATIO * counts[source]:
                continue
            if tree is None:
                tree = cKDTree(centroids)
            faces = np.flatnonzero(labels == source)
            mirrored = centroids[faces].astype(np.float64) * np.array([-1.0, 1.0, 1.0])
            distance, nearest = tree.query(mirrored, k=1, workers=-1)
            if float(np.median(distance)) > threshold:
                continue
            # Every eligible face within the tolerance of a mirrored source centroid takes the counterpart label
            # (queried from the target side, so a target is covered even when no source maps onto it exactly).
            candidates = np.flatnonzero(eligible)
            if candidates.size == 0:
                continue
            gap, donor_index = cKDTree(mirrored).query(centroids[candidates], k=1, distance_upper_bound=threshold,
                                                        workers=-1)
            found = np.isfinite(gap)
            targets = candidates[found]
            donors = faces[donor_index[found]]
            mirrored_normals = normals[donors] * np.array([-1.0, 1.0, 1.0], dtype=np.float32)
            agree = np.einsum("ij,ij->i", mirrored_normals, normals[targets]) > MIRROR_MIN_NORMAL_DOT
            targets, donors = targets[agree], donors[agree]
            labels[targets] = target
            confidence[targets] = confidence[donors] * PARTNER_CONFIDENCE
            eligible[targets] = False
            result[targets] = True
    return result


def _neighbour_majority(adjacency: sparse.csr_array, labels: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per face: the most frequent neighbour label (ties: the lower value), its count and the face degree."""
    face_count = labels.shape[0]
    degree = np.diff(adjacency.indptr)
    rows = np.repeat(np.arange(face_count, dtype=np.int64), degree)
    neighbour = labels[adjacency.indices].astype(np.int64)
    keys = rows * _KEY_SHIFT + neighbour
    unique, counts = np.unique(keys, return_counts=True)
    unique_rows = unique // _KEY_SHIFT
    unique_labels = unique % _KEY_SHIFT
    order = np.lexsort((unique_labels, -counts, unique_rows))
    first = np.ones(order.size, dtype=bool)
    first[1:] = unique_rows[order][1:] != unique_rows[order][:-1]
    chosen = order[first]
    best = np.zeros(face_count, dtype=np.int64)
    best_count = np.zeros(face_count, dtype=np.int64)
    best[unique_rows[chosen]] = unique_labels[chosen]
    best_count[unique_rows[chosen]] = counts[chosen]
    return best, best_count, degree


def _smooth_once(adjacency: sparse.csr_array, labels: np.ndarray) -> int:
    """Step 5.5 (one pass, in place): a labelled face takes the label held by a strict majority of its neighbours."""
    if labels.size == 0:
        return 0
    best, best_count, degree = _neighbour_majority(adjacency, labels)
    change = (labels > 0) & (best > 0) & (best != labels) & (2 * best_count > degree) & (best_count >= 2)
    labels[change] = best[change].astype(np.uint16)
    return int(change.sum())


def _component_fill(
    topology: MeshTopology, labels: np.ndarray, confidence: np.ndarray, fit_factor: float
) -> np.ndarray:
    """Step 5.6 in place; returns the mask of filled faces."""
    face_count = topology.face_count
    result = np.zeros(face_count, dtype=bool)
    components = topology.components
    limit = COMPONENT_FILL_MAX_FRACTION * face_count
    component = components.face_component.astype(np.int64)
    labelled = labels > 0
    if not labelled.any():
        return result
    keys = component[labelled] * _KEY_SHIFT + labels[labelled].astype(np.int64)
    unique, counts = np.unique(keys, return_counts=True)
    owner = unique // _KEY_SHIFT
    value = unique % _KEY_SHIFT
    labelled_per_component = np.bincount(owner, weights=counts, minlength=components.count)
    order = np.lexsort((value, -counts, owner))
    first = np.ones(order.size, dtype=bool)
    first[1:] = owner[order][1:] != owner[order][:-1]
    for index in order[first].tolist():
        c = int(owner[index])
        if c == 0 or components.face_counts[c] >= limit:
            continue
        agreement = counts[index] / labelled_per_component[c]
        if agreement < COMPONENT_FILL_AGREEMENT:
            continue
        faces = components.faces_of(c).astype(np.int64)
        faces = faces[labels[faces] == 0]
        labels[faces] = value[index]
        confidence[faces] = np.float32(agreement * fit_factor)
        result[faces] = True
    return result


def _merge_islands(adjacency: sparse.csr_array, labels: np.ndarray, min_faces: int) -> int:
    """Step 5.7 (single pass, in place): small labelled islands take the majority label around them."""
    face_count = labels.shape[0]
    if face_count == 0:
        return 0
    coo = adjacency.tocoo()
    row = coo.row.astype(np.int64)
    col = coo.col.astype(np.int64)
    same = (labels[row] == labels[col]) & (labels[row] > 0)
    graph = sparse.coo_array((np.ones(int(same.sum()), dtype=np.int8), (row[same], col[same])),
                             shape=(face_count, face_count)).tocsr()
    _count, island = csgraph.connected_components(graph, directed=False)
    sizes = np.bincount(island)
    protected = np.zeros(_KEY_SHIFT, dtype=bool)
    for value, cls in BY_VALUE.items():
        protected[value] = cls.group in PROTECTED_ISLAND_GROUPS
    small = (labels > 0) & (sizes[island] < min_faces) & ~protected[labels]
    if not small.any():
        return 0
    border = small[row] & (labels[col] != labels[row]) & (labels[col] > 0)
    if not border.any():
        return 0
    keys = island[row[border]].astype(np.int64) * _KEY_SHIFT + labels[col[border]].astype(np.int64)
    unique, counts = np.unique(keys, return_counts=True)
    owner = unique // _KEY_SHIFT
    value = unique % _KEY_SHIFT
    order = np.lexsort((value, -counts, owner))
    first = np.ones(order.size, dtype=bool)
    first[1:] = owner[order][1:] != owner[order][:-1]
    new_label = np.zeros(int(island.max()) + 1, dtype=np.int64)
    new_label[owner[order[first]]] = value[order[first]]
    change = small & (new_label[island] > 0)
    labels[change] = new_label[island[change]].astype(np.uint16)
    return int(change.sum())


__all__ = [
    "COMPONENT_FILL_AGREEMENT",
    "COMPONENT_FILL_MAX_FRACTION",
    "GEOMETRY_CONFIDENCE",
    "PROPAGATION_MAX_COST",
    "VOTE_MIN_TOTAL",
    "LabelAssignment",
    "VoteSummary",
    "VoteTable",
    "assign_labels",
    "min_island_faces",
    "unlabelled_assignment",
]
