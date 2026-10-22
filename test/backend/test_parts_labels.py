"""Face votes and label assignment (part-aware DESIGN.md section 4.6.5 steps 4-5; slice S3b)."""

from __future__ import annotations

import logging
import time

import numpy as np
import pytest
from parts_fixtures import SyntheticMesh, combine, uv_sphere

from parts.components import MeshTopology, build_topology
from parts.export import cut_partner_consistency
from parts.labels import (
    VOTE_MIN_TOTAL,
    LabelAssignment,
    VoteTable,
    assign_labels,
    min_island_faces,
    unlabelled_assignment,
)
from parts.policy import effective_min_part_faces, plan_parts
from parts.seams import build_seams
from parts.shells import NO_PARTNER, ShellReport, classify_shells, global_shell_eps, pair_opposite_faces
from parts.taxonomy import DEFAULT_SEPARATE
from parts.types import FACE_FLAG_COMPONENT_FILL, FACE_FLAG_PARTNER, FACE_FLAG_PROPAGATED

logger = logging.getLogger(__name__)

BODY, HAIR, EYE_L, EYE_R, BROW_L, LASH_L, HAT, HELD = 1, 30, 20, 21, 22, 24, 50, 60
RESOLUTION = 64


def _prepare(mesh: SyntheticMesh, resolution: int = RESOLUTION) -> tuple[MeshTopology, ShellReport, np.ndarray]:
    topology = build_topology(mesh.positions, mesh.faces)
    shells = classify_shells(topology, resolution)
    partners = pair_opposite_faces(topology.geometry.centroids, topology.geometry.normals,
                                   global_shell_eps(topology, resolution))
    return topology, shells, partners


def _assign(
    mesh: SyntheticMesh,
    labels: np.ndarray,
    *,
    weights: np.ndarray | None = None,
    face_lab: np.ndarray | None = None,
    resolution: int = RESOLUTION,
) -> tuple[LabelAssignment, MeshTopology, ShellReport, np.ndarray]:
    topology, shells, partners = _prepare(mesh, resolution)
    table = VoteTable(mesh.face_count)
    faces = np.flatnonzero(labels > 0)
    table.add(faces, labels[faces], np.ones(faces.size) if weights is None else weights[faces])
    assignment = assign_labels(table.summary(), topology, partners=partners, shells=shells,
                               effective_resolution=resolution, face_lab=face_lab)
    return assignment, topology, shells, partners


def _centroids(mesh: SyntheticMesh) -> np.ndarray:
    return mesh.positions[mesh.faces].astype(np.float64).mean(axis=1)


def _blocking_lab(face_count: int) -> np.ndarray:
    """Per-face colours 100 Lab units apart from every neighbour: propagation cost > 64 on every edge."""
    lab = np.zeros((face_count, 3), dtype=np.float32)
    lab[:, 0] = np.arange(face_count, dtype=np.float32) * 100.0
    return lab


# --------------------------------------------------------------------------------------------------
# Votes
# --------------------------------------------------------------------------------------------------


def test_vote_table_sums_sparse_votes_and_breaks_ties_low() -> None:
    table = VoteTable(4)
    table.add(np.array([0, 0, 1, 1, 2, 3, 7, -1]), np.array([5, 6, 6, 5, 0, 9, 5, 5]),
              np.array([0.5, 0.5, 0.2, 0.1, 1.0, -1.0, 1.0, 1.0]))
    table.add_view(np.array([[0, -1], [1, 2]]), np.array([[0.5, 1.0], [-1.0, 0.0]]),
                   np.array([[6, 6], [6, 6]]), weight=0.5, confidence=np.array([[1.0, 1.0], [0.5, 1.0]]))
    summary = table.summary()
    # face 0: label 5 -> 0.5, label 6 -> 0.5 + 0.25 = 0.75; face 1: 6 -> 0.2 + 0.25, 5 -> 0.1; face 2: cos 0.
    assert summary.winner.tolist() == [6, 6, 0, 0]
    assert summary.total.tolist() == pytest.approx([1.25, 0.55, 0.0, 0.0])
    assert summary.winner_weight.tolist() == pytest.approx([0.75, 0.45, 0.0, 0.0])
    tie = VoteTable(1)
    tie.add(np.array([0, 0]), np.array([9, 4]), np.array([1.0, 1.0]))
    assert tie.summary().winner.tolist() == [4]


def test_argmax_threshold() -> None:
    mesh = uv_sphere(2000, radius=0.4)
    labels = np.full(mesh.face_count, BODY, dtype=np.uint16)
    weights = np.ones(mesh.face_count)
    low, high = 10, 20
    labels[[low, high]] = HAIR
    weights[low] = VOTE_MIN_TOTAL / 2
    weights[high] = VOTE_MIN_TOTAL * 1.5
    assignment, *_ = _assign(mesh, labels, weights=weights)
    assert assignment.flags[high] == 0 and assignment.votes[high] == pytest.approx(0.3)
    # The weak face is not voted: it is labelled later (propagation), never by its own sub-threshold vote.
    assert assignment.votes[low] == 0.0
    assert assignment.flags[low] & FACE_FLAG_PROPAGATED
    assert assignment.labels[low] == BODY
    assert assignment.stats["voted"] == mesh.face_count - 1


# --------------------------------------------------------------------------------------------------
# Partner fill (rim-joined double wall, like TRELLIS.2 output)
# --------------------------------------------------------------------------------------------------


def test_partner_fill_takes_both_walls_of_the_cut() -> None:
    mesh = uv_sphere(20000, double_walled=True, rim_joined=True, open_hemisphere=True)
    centroid = _centroids(mesh)
    outer, inner = mesh.group_mask("outer"), mesh.group_mask("inner")
    labels = np.zeros(mesh.face_count, dtype=np.uint16)
    labels[outer] = BODY
    labels[outer & (centroid[:, 1] > 0.3)] = HAIR  # votes only on the outer wall
    assignment, topology, shells, partners = _assign(mesh, labels, resolution=1536)
    assert topology.components.count == 1
    inner_faces = np.flatnonzero(inner)
    partnered = inner_faces[partners[inner_faces] != NO_PARTNER]
    assert partnered.size >= 0.95 * inner_faces.size
    filled = partnered[(assignment.flags[partnered] & FACE_FLAG_PARTNER) != 0]
    assert filled.size >= 0.95 * partnered.size
    assert np.array_equal(assignment.labels[filled], assignment.labels[partners[filled]])
    assert np.array_equal(assignment.donor[filled], partners[filled])
    assert assignment.confidence[filled] == pytest.approx(0.9 * assignment.confidence[partners[filled]])
    assert np.all(assignment.flags[outer] & FACE_FLAG_PARTNER == 0)
    assert assignment.partner_filled_ratio == pytest.approx(
        float(((assignment.flags & FACE_FLAG_PARTNER) != 0).mean()))

    plan = plan_parts(topology, shells, assignment.labels, strategy="semantic_parts", separate=DEFAULT_SEPARATE,
                      min_part_faces=effective_min_part_faces(None, mesh.face_count), max_parts=64,
                      face_flags=assignment.flags, donor=assignment.donor)
    hair_index = plan.mesh_part_ids.index("hair")
    hair_faces = np.flatnonzero(plan.face_owner == hair_index)
    assert np.count_nonzero(inner[hair_faces]) >= 0.4 * hair_faces.size  # the cut holds BOTH walls
    assert np.count_nonzero(outer[hair_faces]) >= 0.4 * hair_faces.size
    seams = build_seams(topology, plan.face_owner, plan.mesh_part_ids)
    consistency = cut_partner_consistency(plan, seams, partners)
    logger.info("rim-joined hemisphere: cut_partner_consistency=%.4f partner_filled=%.4f",
                consistency, assignment.partner_filled_ratio)
    assert consistency is not None and consistency >= 0.9
    # Both sheets contribute loops to the same seam record.
    hair_seams = [seam for seam in seams.seams if "hair" in (seam.a, seam.b)]
    assert len(hair_seams) == 1 and len(hair_seams[0].loops) >= 2


def test_partner_ownership_rule_follows_the_donor_owner() -> None:
    mesh = uv_sphere(20000, double_walled=True, rim_joined=True, open_hemisphere=True)
    centroid = _centroids(mesh)
    outer = mesh.group_mask("outer")
    labels = np.zeros(mesh.face_count, dtype=np.uint16)
    labels[outer] = BODY
    labels[outer & (centroid[:, 1] > 0.3)] = HAIR
    assignment, topology, shells, _partners = _assign(mesh, labels, resolution=1536)
    relabelled = assignment.labels.copy()
    filled = np.flatnonzero((assignment.flags & FACE_FLAG_PARTNER) != 0)
    hair_filled = filled[assignment.labels[filled] == HAIR]
    relabelled[hair_filled[:50]] = BODY  # e.g. smoothing changed a partner-filled label
    plan = plan_parts(topology, shells, relabelled, strategy="semantic_parts", separate=DEFAULT_SEPARATE,
                      min_part_faces=50, max_parts=64, face_flags=assignment.flags, donor=assignment.donor)
    hair_index = plan.mesh_part_ids.index("hair")
    assert np.all(plan.face_owner[hair_filled[:50]] == hair_index)


# --------------------------------------------------------------------------------------------------
# Propagation, mirror, smoothing
# --------------------------------------------------------------------------------------------------


def test_colour_affinity_propagation_stops_at_a_colour_edge() -> None:
    mesh = uv_sphere(3000, radius=0.5)
    centroid = _centroids(mesh)
    labels = np.zeros(mesh.face_count, dtype=np.uint16)
    labels[centroid[:, 1] > 0.45] = HAIR
    lab = np.zeros((mesh.face_count, 3), dtype=np.float32)
    lab[:, 0] = np.where(centroid[:, 1] > 0.0, 50.0, 0.0)
    lab[centroid[:, 1] <= 0.0, 1] = 100.0  # dE = 112 across the equator: cost 1 + 11.2^2 > 64
    assignment, *_ = _assign(mesh, labels, face_lab=lab)
    upper = centroid[:, 1] > 0.02
    lower = centroid[:, 1] < -0.02
    assert np.all(assignment.labels[upper] == HAIR)
    assert np.all(assignment.flags[upper & (labels == 0)] & FACE_FLAG_PROPAGATED)
    assert np.all(assignment.labels[lower] == 0)
    # Without the colour edge the same votes reach the lower half (uniform cost).
    uniform, *_ = _assign(mesh, labels)
    assert np.all(uniform.labels[lower] == HAIR)
    assert float(uniform.confidence[lower].min()) < float(uniform.confidence[labels > 0].min())


@pytest.mark.parametrize("symmetric", [True, False])
def test_mirror_completion_within_tolerance(symmetric: bool) -> None:
    mesh = uv_sphere(6000, radius=0.5)
    if not symmetric:
        mesh.positions = mesh.positions.copy()
        mesh.positions[mesh.positions[:, 0] < 0.0, 2] += 0.08  # the right half is displaced by 2.5 tolerances
    centroid = _centroids(mesh)
    eye_left = (centroid[:, 2] > 0.3) & (centroid[:, 0] > 0.1) & (centroid[:, 0] < 0.25) & (np.abs(centroid[:, 1])
                                                                                             < 0.08)
    eye_right = (centroid[:, 2] > 0.3) & (centroid[:, 0] < -0.1 * (1.3 if not symmetric else 1.0)) \
        & (centroid[:, 0] > -0.25 * (1.3 if not symmetric else 1.0)) & (np.abs(centroid[:, 1]) < 0.08)
    labels = np.full(mesh.face_count, BODY, dtype=np.uint16)
    labels[eye_left] = EYE_L
    labels[eye_right] = 0  # no vote where the right eye should be
    assignment, *_ = _assign(mesh, labels)
    share = float((assignment.labels[eye_right] == EYE_R).mean())
    logger.info("mirror completion symmetric=%s: %.3f of the right-eye area mirrored", symmetric, share)
    if symmetric:
        assert share >= 0.95 and assignment.stats["mirrored"] > 0
        assert float((assignment.labels[eye_left] == EYE_L).mean()) >= 0.95  # smoothing may trim borders
    else:
        assert share == 0.0 and assignment.stats["mirrored"] == 0


def test_smoothing_removes_single_face_islands() -> None:
    mesh = uv_sphere(2000, radius=0.4)
    labels = np.full(mesh.face_count, BODY, dtype=np.uint16)
    labels[777] = HAIR
    assignment, *_ = _assign(mesh, labels)
    assert assignment.labels[777] == BODY
    assert assignment.stats["smoothed"] >= 1 and assignment.stats["island_merged"] == 0


# --------------------------------------------------------------------------------------------------
# Component fill and island merge
# --------------------------------------------------------------------------------------------------


def test_component_fill_rules() -> None:
    main = uv_sphere(6000, radius=0.5)
    big = uv_sphere(4000, radius=0.35, center=(1.5, 0.0, 0.0))
    small = uv_sphere(1200, radius=0.2, center=(-1.5, 0.0, 0.0))
    mesh = combine(main, big, small, names=("main", "big", "small"))
    centroid = _centroids(mesh)
    labels = np.zeros(mesh.face_count, dtype=np.uint16)

    def nearest(group: str, point: tuple[float, float, float], count: int) -> np.ndarray:
        faces = mesh.face_groups[group]
        order = np.argsort(np.linalg.norm(centroid[faces] - np.asarray(point), axis=1), kind="stable")
        return faces[order[:count]]

    labels[nearest("small", (-1.5, 0.2, 0.0), 90)] = HAT
    minority = nearest("small", (-1.5, -0.2, 0.0), 10)
    labels[minority] = HELD
    labels[nearest("main", (0.0, 0.5, 0.0), 50)] = HAT
    labels[nearest("big", (1.5, 0.35, 0.0), 50)] = HAT
    assignment, topology, *_ = _assign(mesh, labels, face_lab=_blocking_lab(mesh.face_count))
    assert assignment.stats["propagated"] == 0
    components = topology.components.face_component
    small_c = int(components[mesh.face_groups["small"][0]])
    main_c = int(components[mesh.face_groups["main"][0]])
    big_c = int(components[mesh.face_groups["big"][0]])
    assert main_c == 0 and topology.components.face_counts[big_c] >= 0.25 * mesh.face_count
    small_faces = np.flatnonzero(components == small_c)
    unvoted = small_faces[labels[small_faces] == 0]
    assert np.all(assignment.labels[unvoted] == HAT)
    assert np.all(assignment.flags[unvoted] & FACE_FLAG_COMPONENT_FILL)
    assert np.all(assignment.labels[minority] == HELD)  # a voted label is never overwritten
    assert assignment.confidence[unvoted] == pytest.approx(0.9)
    for component in (main_c, big_c):
        faces = np.flatnonzero(components == component)
        assert np.all(assignment.labels[faces][labels[faces] == 0] == 0)
        assert not np.any(assignment.flags[faces] & FACE_FLAG_COMPONENT_FILL)


def test_island_merge_spares_protected_groups() -> None:
    mesh = uv_sphere(6000, radius=0.5)
    topology = build_topology(mesh.positions, mesh.faces)
    assert min_island_faces(mesh.face_count) == 8
    centroid = _centroids(mesh)
    fans = []
    for point in ((0.0, 0.5, 0.0), (0.0, -0.5, 0.0), (0.5, 0.0, 0.0), (0.0, 0.0, 0.5)):
        nearest_face = int(np.argmin(np.linalg.norm(centroid - np.asarray(point), axis=1)))
        vertex_counts = np.bincount(topology.welded.reshape(-1).astype(np.int64))
        corners = topology.welded[nearest_face].astype(np.int64)
        vertex = int(corners[np.argmax(vertex_counts[corners] == 6)])
        assert vertex_counts[vertex] == 6
        fans.append(np.flatnonzero(np.any(topology.welded == vertex, axis=1)))
    labels = np.full(mesh.face_count, BODY, dtype=np.uint16)
    for fan, value in zip(fans, (HAIR, EYE_L, BROW_L, 3), strict=True):  # 3 = body.head.face (a face region)
        labels[fan] = value
    assignment, *_ = _assign(mesh, labels)
    assert np.all(assignment.labels[fans[0]] == BODY)
    assert assignment.stats["island_merged"] >= 6
    for fan, value in zip(fans[1:], (EYE_L, BROW_L, 3), strict=True):
        assert np.all(assignment.labels[fan] == value)


def test_unlabelled_assignment_is_geometric() -> None:
    assignment = unlabelled_assignment(5)
    assert assignment.labels.tolist() == [0] * 5 and assignment.confidence_u8().tolist() == [255] * 5
    assert assignment.unlabelled_face_ratio == 1.0 and assignment.partner_filled_ratio == 0.0


# --------------------------------------------------------------------------------------------------
# 500 k faces: small brow island and lash band keep their records
# --------------------------------------------------------------------------------------------------


@pytest.mark.slow
def test_small_brow_and_lash_records_survive_at_500k_faces() -> None:
    mesh = uv_sphere(n_faces=500_000, radius=0.5)
    centroid = _centroids(mesh)
    front = centroid[:, 2] > 0.4
    labels = np.full(mesh.face_count, BODY, dtype=np.uint16)
    brow_distance = np.linalg.norm(centroid[:, :2] - np.array([0.1, 0.1]), axis=1)
    brow = np.flatnonzero(front)[np.argsort(brow_distance[front], kind="stable")[:180]]
    edge = float(np.sqrt(np.median(np.linalg.norm(np.cross(
        mesh.positions[mesh.faces[:, 1]] - mesh.positions[mesh.faces[:, 0]],
        mesh.positions[mesh.faces[:, 2]] - mesh.positions[mesh.faces[:, 0]]), axis=1))))
    band = front & (np.abs(centroid[:, 1] - 0.02) < edge) & (np.abs(centroid[:, 0] - 0.1) < 0.06)
    lash = np.flatnonzero(band)
    labels[brow] = BROW_L
    labels[lash] = LASH_L
    logger.info("500k: brow island %d faces, lash band %d faces", brow.size, lash.size)
    assert 150 <= brow.size <= 200 and 80 <= lash.size <= 200
    started = time.perf_counter()
    assignment, topology, shells, _partners = _assign(mesh, labels, resolution=1536)
    plan = plan_parts(topology, shells, assignment.labels, strategy="semantic_parts", separate=DEFAULT_SEPARATE,
                      min_part_faces=effective_min_part_faces(None, mesh.face_count), max_parts=64,
                      face_flags=assignment.flags, donor=assignment.donor)
    logger.info("500k: assign + plan %.2f s", time.perf_counter() - started)
    kinds = {part.part_id: (part.kind, part.faces.size) for part in plan.parts}
    assert kinds["eyebrow.left"][0] == "physical_deferred" and kinds["eyebrow.left"][1] >= 0.8 * brow.size
    assert kinds["eyelash.left"][0] == "physical_deferred" and kinds["eyelash.left"][1] >= 0.5 * lash.size
    assert min_island_faces(mesh.face_count) == 10


# --------------------------------------------------------------------------------------------------
# Thin-sheet walls keep one label (S10 regressions from the real ALICE/anime smoke)
# --------------------------------------------------------------------------------------------------


def test_unvoted_wall_pairs_propagate_through_the_partner_link() -> None:
    """Both walls unvoted: the inner wall cannot propagate along itself (colour edges), only via its partner."""
    mesh = uv_sphere(20000, double_walled=True, rim_joined=True, open_hemisphere=True)
    centroid = _centroids(mesh)
    outer, inner = mesh.group_mask("outer"), mesh.group_mask("inner")
    labels = np.zeros(mesh.face_count, dtype=np.uint16)
    labels[outer & (centroid[:, 1] > 0.35)] = HAIR
    labels[outer & (centroid[:, 1] < 0.15)] = BODY
    lab = _blocking_lab(mesh.face_count)
    lab[outer] = 0.0
    assignment, _topology, _shells, partners = _assign(mesh, labels, face_lab=lab, resolution=1536)
    middle = np.flatnonzero(inner & (centroid[:, 1] >= 0.18) & (centroid[:, 1] <= 0.32))
    middle = middle[partners[middle] != NO_PARTNER]
    assert middle.size > 100
    assert np.all(assignment.labels[middle] > 0)
    agree = assignment.labels[middle] == assignment.labels[partners[middle]]
    assert agree.mean() >= 0.99
    assert np.mean((assignment.flags[middle] & FACE_FLAG_PROPAGATED) != 0) >= 0.9


def test_voted_walls_with_different_classes_take_the_stronger_label() -> None:
    mesh = uv_sphere(20000, double_walled=True, rim_joined=True, open_hemisphere=True)
    topology, shells, partners = _prepare(mesh, 1536)
    centroid = _centroids(mesh)
    outer, inner = mesh.group_mask("outer"), mesh.group_mask("inner")
    table = VoteTable(mesh.face_count)
    outer_faces = np.flatnonzero(outer)
    table.add(outer_faces, np.where(centroid[outer_faces, 1] > 0.3, HAIR, BODY), np.ones(outer_faces.size))
    wrong = np.flatnonzero(inner & (centroid[:, 1] > 0.33) & (partners != NO_PARTNER))
    wrong = wrong[outer[partners[wrong]] & (centroid[partners[wrong], 1] > 0.3)]
    table.add(wrong, np.full(wrong.size, HAT), np.full(wrong.size, 0.5))  # a weaker, conflicting vote
    assignment = assign_labels(table.summary(), topology, partners=partners, shells=shells, effective_resolution=1536)
    assert wrong.size > 100
    assert np.mean(assignment.labels[wrong] == HAIR) >= 0.99
    assert np.mean(assignment.donor[wrong] == partners[wrong]) >= 0.99
    assert np.all((assignment.flags[wrong] & FACE_FLAG_PARTNER) != 0)
    assert assignment.stats["partner_conflicts"] >= wrong.size
    plan = plan_parts(topology, shells, assignment.labels, strategy="semantic_parts", separate=DEFAULT_SEPARATE,
                      min_part_faces=50, max_parts=64, face_flags=assignment.flags, donor=assignment.donor,
                      partners=partners)
    seams = build_seams(topology, plan.face_owner, plan.mesh_part_ids)
    assert "accessory.headwear" not in plan.mesh_part_ids
    assert cut_partner_consistency(plan, seams, partners) >= 0.9


def test_cut_partner_consistency_is_face_weighted_not_the_worst_fragment() -> None:
    """User decision 2026-10-22: one large consistent cut part (1000 paired faces) plus a 40-face fragment whose walls
    disagree on 10 faces scores ~0.99, not the fragment's 0.75; no cut → None; cut parts without partners → 1.0."""
    from types import SimpleNamespace

    big, small = np.arange(0, 1000), np.arange(1000, 1040)
    owner = np.zeros(1100, dtype=np.int64)
    owner[big] = 1
    owner[small] = 2
    partner = np.full(1100, NO_PARTNER, dtype=np.int64)
    partner[big] = big[::-1]  # every big-part face is paired inside the big part
    partner[small] = small[::-1]
    partner[small[:10]] = 1050  # 10 fragment faces pair with a face owned by the base (0)
    parts = [SimpleNamespace(kind="physical", mesh_index=1), SimpleNamespace(kind="physical", mesh_index=2)]
    plan = SimpleNamespace(face_owner=owner, parts=parts)
    seams = SimpleNamespace(seams=[SimpleNamespace(a_index=0, b_index=1), SimpleNamespace(a_index=0, b_index=2)])

    assert cut_partner_consistency(plan, seams, partner) == pytest.approx(1030 / 1040, abs=1e-4)
    assert cut_partner_consistency(plan, SimpleNamespace(seams=[]), partner) is None
    assert cut_partner_consistency(plan, seams, np.full(1100, NO_PARTNER, dtype=np.int64)) == 1.0
    assert cut_partner_consistency(plan, seams, None) is None
