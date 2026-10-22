"""Physical-versus-region policy and the partition plan (part-aware DESIGN.md section 2.4; slice S3b)."""

from __future__ import annotations

import numpy as np
import pytest
from parts_fixtures import SyntheticMesh, combine, floaters, uv_sphere

from parts.components import MeshTopology, build_topology
from parts.policy import (
    FLOATER_CANDIDATES,
    MAX_PARTS_EXCEEDED,
    TOO_SMALL_FOR_GEOMETRY,
    PartPlan,
    decide_kind,
    effective_min_part_faces,
    kinds_summary,
    plan_parts,
)
from parts.shells import ShellReport, classify_shells
from parts.taxonomy import DEFAULT_SEPARATE, TAXONOMY
from parts.types import FACE_PART_NONE

RESOLUTION = 64
ALL = frozenset(DEFAULT_SEPARATE)


def _prepare(mesh: SyntheticMesh) -> tuple[MeshTopology, ShellReport]:
    topology = build_topology(mesh.positions, mesh.faces)
    return topology, classify_shells(topology, RESOLUTION)


def _plan(mesh: SyntheticMesh, labels: np.ndarray, **options: object) -> PartPlan:
    topology, shells = _prepare(mesh)
    settings: dict[str, object] = {"strategy": "semantic_parts", "separate": DEFAULT_SEPARATE, "min_part_faces": 10,
                                   "max_parts": 64}
    settings.update(options)
    return plan_parts(topology, shells, labels, **settings)  # type: ignore[arg-type]


def _check_face_part(plan: PartPlan) -> None:
    for index, part in enumerate(plan.parts):
        faces = np.flatnonzero(plan.face_part == index)
        assert np.array_equal(faces, np.sort(part.faces)), part.part_id
        assert np.all(plan.face_labels[faces] == part.value)
        assert np.all(plan.face_owner[faces] == part.host_index)


# --------------------------------------------------------------------------------------------------
# decide_kind and min_part_faces
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("class_id", "separate", "faces", "expected"),
    [
        ("body", ALL, 10_000, ("base", None)),
        ("eye.left", ALL, 500, ("physical", None)),
        ("eye.right", ALL, 500, ("physical", None)),
        ("eyebrow.left", ALL, 500, ("physical_deferred", None)),
        ("eyelash.right", ALL, 500, ("physical_deferred", None)),
        ("body.head.face", ALL, 500, ("region", None)),
        ("body.head.eyelid.left", ALL, 500, ("region", None)),
        ("body.torso", ALL, 500, ("region", None)),
        ("unknown", ALL, 500, ("region", None)),
        ("hair", ALL - {"hair"}, 50_000, ("region", None)),
        ("clothing.dress", frozenset({"hair"}), 50_000, ("region", None)),
        ("hair", ALL, 199, ("physical_deferred", TOO_SMALL_FOR_GEOMETRY)),
        ("hair", ALL, 200, ("physical", None)),
        ("accessory.earwear", ALL, 12, ("physical_deferred", TOO_SMALL_FOR_GEOMETRY)),
    ],
)
def test_decide_kind_matrix(class_id: str, separate: frozenset[str], faces: int, expected: tuple[str, str | None]) -> None:
    assert decide_kind(TAXONOMY[class_id], separate, faces, 200) == expected


def test_decide_kind_with_zero_min_part_faces_keeps_single_faces() -> None:
    assert decide_kind(TAXONOMY["eye.left"], ALL, 1, 0) == ("physical", None)
    assert decide_kind(TAXONOMY["eye.left"], ALL, 0, 0) == ("physical_deferred", TOO_SMALL_FOR_GEOMETRY)


def test_effective_min_part_faces() -> None:
    assert effective_min_part_faces(None, 2_000_000) == 200
    assert effective_min_part_faces(None, 500_000) == 50
    assert effective_min_part_faces(None, 1_895_691) == 190
    assert effective_min_part_faces(None, 1000) == 50
    assert effective_min_part_faces(7, 2_000_000) == 7
    with pytest.raises(ValueError):
        effective_min_part_faces(-1, 10)


# --------------------------------------------------------------------------------------------------
# Separate shells, floaters, budget
# --------------------------------------------------------------------------------------------------


def test_a_body_labelled_shell_stays_on_the_base() -> None:
    body = uv_sphere(4000, radius=0.4)
    leg = uv_sphere(400, radius=0.08, center=(0.0, -0.7, 0.0))
    mesh = combine(body, leg, names=("body", "leg"))
    labels = np.full(mesh.face_count, 1, dtype=np.uint16)
    labels[mesh.group_mask("leg")] = TAXONOMY["body.leg.left"].value
    plan = _plan(mesh, labels)
    assert plan.mesh_part_ids == ("body",)
    assert np.all(plan.face_owner == 0)
    leg_record = plan.part("body.leg.left")
    assert leg_record.kind == "region" and leg_record.host_index == 0
    assert leg_record.faces.size == mesh.face_groups["leg"].size
    _check_face_part(plan)


def test_unknown_small_shell_is_a_floater_without_record() -> None:
    body = uv_sphere(4000, radius=0.4)
    loose = uv_sphere(300, radius=0.05, center=(0.8, 0.0, 0.0))
    mesh = combine(body, loose, floaters(2), names=("body", "loose", "floaters"))
    labels = np.zeros(mesh.face_count, dtype=np.uint16)
    labels[mesh.group_mask("body")] = 1
    plan = _plan(mesh, labels, min_part_faces=10)
    # floater_max_faces = max(10, round(0.0005 * F)) = 10 > 4: both tetrahedra are floaters, the 432-face shell a part.
    assert len(plan.floaters) == 2 and all(f.faces == 4 for f in plan.floaters)
    assert [w.code for w in plan.warnings] == [FLOATER_CANDIDATES]
    assert plan.warnings[0].part_id == "body"
    floater_faces = mesh.face_groups["floaters"]
    assert np.all(plan.face_owner[floater_faces] == 0)
    assert np.all(plan.face_labels[floater_faces] == 0)
    assert np.all(plan.face_part[floater_faces] == FACE_PART_NONE)
    component = plan.part("component_001")
    assert component.kind == "physical" and component.source == "component" and component.value == 0
    assert component.seamless and component.faces.size == mesh.face_groups["loose"].size
    assert {part.part_id for part in plan.parts} == {"body", "component_001"}
    # With a larger min_part_faces the 432-face shell is a floater too (still no deletion).
    bigger = _plan(mesh, labels, min_part_faces=500)
    assert len(bigger.floaters) == 3 and bigger.mesh_part_ids == ("body",)


def test_labelled_shell_of_a_selected_group_follows_decide_kind_whatever_its_size() -> None:
    body = uv_sphere(4000, radius=0.4)
    earring = uv_sphere(24, radius=0.02, center=(0.45, 0.0, 0.0))
    mesh = combine(body, earring, names=("body", "earring"))
    labels = np.full(mesh.face_count, 1, dtype=np.uint16)
    labels[mesh.group_mask("earring")] = TAXONOMY["accessory.earwear"].value
    plan = _plan(mesh, labels, min_part_faces=50)
    record = plan.part("accessory.earwear")
    assert record.kind == "physical_deferred" and record.reason == TOO_SMALL_FOR_GEOMETRY
    assert any(w.code == TOO_SMALL_FOR_GEOMETRY and w.part_id == "accessory.earwear" for w in plan.warnings)
    assert plan.floaters == ()
    physical = _plan(mesh, labels, min_part_faces=10)
    assert physical.part("accessory.earwear").kind == "physical"
    assert physical.part("accessory.earwear").seamless


def test_part_budget_keeps_class_values_on_overflow_regions() -> None:
    body = uv_sphere(8000, radius=0.4)
    beads = [uv_sphere(n, radius=0.04, center=(0.6 + 0.15 * i, 0.0, 0.0)) for i, n in enumerate((192, 432, 768, 1200))]
    mesh = combine(body, *beads)
    labels = np.full(mesh.face_count, 1, dtype=np.uint16)
    jewelry = TAXONOMY["accessory.jewelry"].value
    labels[body.face_count:] = jewelry
    plan = _plan(mesh, labels, max_parts=2)
    physical = [p for p in plan.parts if p.kind == "physical"]
    overflow = [p for p in plan.parts if p.reason == MAX_PARTS_EXCEEDED]
    assert [p.part_id for p in physical] == ["accessory.jewelry", "accessory.jewelry_2"]
    assert physical[0].faces.size > physical[1].faces.size  # numbered by face count, largest first
    assert len(overflow) == 2 and all(p.kind == "region" and p.value == jewelry for p in overflow)
    assert all(p.host_index == 0 for p in overflow)
    assert [w.code for w in plan.warnings].count(MAX_PARTS_EXCEEDED) == 1
    assert plan.node_count == 3
    _check_face_part(plan)
    assert kinds_summary(plan.parts) == {"physical": 2, "physical_deferred": 0, "region": 2}


def test_semantic_regions_never_cuts() -> None:
    body = uv_sphere(4000, radius=0.4)
    hat = uv_sphere(300, radius=0.08, center=(0.0, 0.6, 0.0))
    mesh = combine(body, hat, names=("body", "hat"))
    centroid = mesh.positions[mesh.faces].astype(np.float64).mean(axis=1)
    labels = np.full(mesh.face_count, 1, dtype=np.uint16)
    labels[mesh.group_mask("body") & (centroid[:, 1] > 0.25)] = TAXONOMY["hair"].value
    labels[mesh.group_mask("hat")] = TAXONOMY["accessory.headwear"].value
    plan = _plan(mesh, labels, strategy="semantic_regions")
    assert plan.mesh_part_ids == ("body",) and np.all(plan.face_owner == 0)
    assert {p.kind for p in plan.parts[1:]} == {"region"}
    cut = _plan(mesh, labels)
    assert set(cut.mesh_part_ids) == {"body", "hair", "accessory.headwear"}
    assert cut.part("hair").seamless is False and cut.part("accessory.headwear").seamless
    _check_face_part(cut)


def test_components_strategy_plans_shells_only() -> None:
    body = uv_sphere(4000, radius=0.4)
    shells = [uv_sphere(300, radius=0.05, center=(0.8, 0.3 * i, 0.0)) for i in range(2)]
    mesh = combine(body, *shells, floaters(1))
    plan = _plan(mesh, np.zeros(mesh.face_count, dtype=np.uint16), strategy="components")
    assert plan.mesh_part_ids == ("body", "component_001", "component_002")
    assert len(plan.floaters) == 1
    assert plan.part("body").faces.size == 0  # nothing is labelled body: base faces have no record
    assert np.all(plan.face_part[plan.face_owner == 0] == FACE_PART_NONE)


def test_plan_rejects_bad_input() -> None:
    mesh = uv_sphere(500, radius=0.4)
    labels = np.ones(mesh.face_count, dtype=np.uint16)
    with pytest.raises(ValueError):
        _plan(mesh, labels, strategy="per_part_regeneration")
    with pytest.raises(ValueError):
        _plan(mesh, labels, separate=("face",))
    with pytest.raises(ValueError):
        _plan(mesh, labels, max_parts=0)
    bad = labels.copy()
    bad[0] = 19  # not a taxonomy v1 value
    with pytest.raises(ValueError):
        _plan(mesh, bad)


# --------------------------------------------------------------------------------------------------
# S10 regressions from the real ALICE/anime smoke
# --------------------------------------------------------------------------------------------------


def test_both_walls_of_a_thin_sheet_stay_one_instance() -> None:
    from parts.export import cut_partner_consistency
    from parts.seams import build_seams
    from parts.shells import global_shell_eps, pair_opposite_faces

    mesh = uv_sphere(20000, double_walled=True, rim_joined=True, open_hemisphere=True)
    topology = build_topology(mesh.positions, mesh.faces)
    shells = classify_shells(topology, 1536)
    partners = pair_opposite_faces(topology.geometry.centroids, topology.geometry.normals,
                                   global_shell_eps(topology, 1536))
    foot = TAXONOMY["clothing.footwear"]
    assert foot.instance_split
    centroid = mesh.positions[mesh.faces].astype(np.float64).mean(axis=1)
    labels = np.ones(mesh.face_count, dtype=np.uint16)
    labels[centroid[:, 1] > 0.3] = foot.value  # both walls; they only meet at the BODY-labelled rim
    options = {"strategy": "semantic_parts", "separate": DEFAULT_SEPARATE, "min_part_faces": 10, "max_parts": 64}
    split = plan_parts(topology, shells, labels, **options)  # type: ignore[arg-type]
    assert "clothing.footwear_2" in split.mesh_part_ids  # one instance per wall without the partner links
    joined = plan_parts(topology, shells, labels, partners=partners, **options)  # type: ignore[arg-type]
    assert [p for p in joined.mesh_part_ids if p.startswith("clothing.footwear")] == ["clothing.footwear"]
    seams = build_seams(topology, joined.face_owner, joined.mesh_part_ids)
    assert cut_partner_consistency(joined, seams, partners) >= 0.9
    _check_face_part(joined)


def test_overflow_region_never_counts_the_faces_of_a_same_class_part() -> None:
    mesh = uv_sphere(3000, radius=0.5)
    centroid = mesh.positions[mesh.faces].astype(np.float64).mean(axis=1)
    foot = TAXONOMY["clothing.footwear"].value
    labels = np.ones(mesh.face_count, dtype=np.uint16)
    big = np.flatnonzero(centroid[:, 1] > 0.25)
    small = np.flatnonzero(centroid[:, 1] < -0.35)
    labels[big] = foot
    labels[small] = foot
    flags = np.zeros(mesh.face_count, dtype=np.uint8)
    donor = np.full(mesh.face_count, -1, dtype=np.int32)
    flags[small] = 1  # partner-filled from a face of the big instance: they follow its owner
    donor[small] = big[0]
    plan = _plan(mesh, labels, max_parts=1, face_flags=flags, donor=donor)
    assert plan.mesh_part_ids == ("body", "clothing.footwear")
    assert MAX_PARTS_EXCEEDED in {w.code for w in plan.warnings}
    _check_face_part(plan)  # region.face_count == face_part count for every record


def test_a_fully_cut_mesh_keeps_its_largest_part_on_the_base() -> None:
    mesh = uv_sphere(3000, radius=0.5)
    centroid = mesh.positions[mesh.faces].astype(np.float64).mean(axis=1)
    labels = np.where(centroid[:, 1] > -0.1, TAXONOMY["hair"].value, TAXONOMY["clothing.dress"].value)
    plan = _plan(mesh, labels.astype(np.uint16))
    assert np.any(plan.face_owner == 0)
    assert plan.mesh_part_ids == ("body", "clothing.dress")
    assert plan.part("hair").kind == "region" and plan.part("hair").host_index == 0
    _check_face_part(plan)
