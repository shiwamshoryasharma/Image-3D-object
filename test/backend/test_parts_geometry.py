"""Geometry core of the part asset layer (part-aware DESIGN.md sections 3.2-3.5, 4.6.5 and 10.2, slice S3a).

Weld (UV duplicates only, numpy 2.4 inverse-shape guard), welded edges and adjacency, components ordered by area,
shells (a double-walled sphere is ONE part; floaters get no record and are never deleted), opposite-sheet partners on
the rim-joined double-walled hemisphere, the vmap rule of section 3.4, bit-exact reassembly from the written files
(A1, A4), seams (closed loops on a clean cut, open chains at a non-manifold junction, both sheets in one record), a
2 M-face synthetic partition + write under 60 s, and the real ``alice_front`` fixture read in place.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from pathlib import Path

import numpy as np
import pytest
from parts_fixtures import (
    band_labels,
    capped_cylinder,
    combine,
    floaters,
    cube_sphere_size,
    t_junction,
    touching_spheres,
    uv_sphere,
    write_base_glb,
)

from parts.components import build_topology, connected_components, face_geometry
from parts.gltf_io import NodeSpec, read_base_glb, write_parts_glb
from parts.manifest import SeamRecord
from parts.partition import build_mesh_nodes, face_owner_from_nodes, gather_node_attributes
from parts.reassembly import ReassemblyError, verify_mesh_nodes, verify_reassembly
from parts.seams import build_seams, open_boundary_loops
from parts.shells import (
    NO_PARTNER,
    classify_shells,
    global_shell_eps,
    pair_opposite_faces,
    partner_coverage,
    shell_eps,
)
from parts.types import vmap_buffer_spec
from parts.weld import build_edge_table, face_adjacency, weld_vertices, welded_faces

LOG = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[2]
EFFECTIVE_RESOLUTION = 1536


def _min_part_faces(face_count: int) -> int:
    return max(50, round(1e-4 * face_count))


def _base(tmp_runtime: Path, mesh, name: str = "model.glb"):
    return read_base_glb(write_base_glb(tmp_runtime / name, mesh))


def _write_nodes(path: Path, base, nodes) -> None:
    specs = [NodeSpec.from_mesh_node(base, node, {"kind": "base" if node.index == 0 else "physical"})
             for node in nodes]
    write_parts_glb(path, base, specs, scene_extras={"schema": "img3d.parts/1"})


# --------------------------------------------------------------------------------------------------
# Weld, edges, adjacency, components
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("mesh_factory", [lambda: capped_cylinder(), lambda: uv_sphere(2000)])
def test_weld_groups_bit_identical_positions_only(mesh_factory):
    mesh = mesh_factory()
    weld = weld_vertices(mesh.positions)
    assert weld.weld_map.shape == (mesh.vertex_count,) and weld.weld_map.dtype == np.uint32
    assert weld.welded_count == np.unique(mesh.positions.view(np.dtype((np.void, 12))).ravel()).shape[0]
    assert weld.welded_count < mesh.vertex_count
    regathered = weld.welded_positions[weld.weld_map]
    assert np.array_equal(regathered.view(np.uint32), mesh.positions.view(np.uint32))
    assert np.unique(weld.welded_positions, axis=0).shape[0] == weld.welded_count
    groups = np.split(np.argsort(weld.weld_map, kind="stable"), np.cumsum(np.bincount(weld.weld_map))[:-1])
    merged = [group for group in groups if group.size > 1]
    assert merged and all(np.unique(mesh.uv[group], axis=0).shape[0] > 1 for group in merged)
    faces = welded_faces(mesh.faces, weld.weld_map)
    assert faces.shape == (mesh.face_count, 3) and faces.dtype == np.uint32


def test_weld_never_merges_near_but_different_positions():
    positions = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [np.nextafter(np.float32(0), np.float32(1)), 0, 0],
                          [-0.0, 0.0, 0.0]], dtype=np.float32)
    weld = weld_vertices(positions)
    assert weld.weld_map[0] == weld.weld_map[1]
    assert len({int(weld.weld_map[0]), int(weld.weld_map[2]), int(weld.weld_map[3])}) == 3
    with pytest.raises(ValueError):
        weld_vertices(positions.astype(np.float64))


def test_edge_table_adjacency_and_non_manifold_edges():
    sphere = uv_sphere(2000)
    weld = weld_vertices(sphere.positions)
    faces = welded_faces(sphere.faces, weld.weld_map)
    table = build_edge_table(faces, weld.welded_count)
    assert table.edge_count * 2 == 3 * sphere.face_count
    assert np.all(table.face_counts == 2) and table.boundary_edges().size == 0
    assert np.all(table.edges[:, 0] < table.edges[:, 1])
    corner_edges = table.edges[table.face_edges]
    expected = np.sort(np.stack([faces, np.roll(faces, -1, axis=1)], axis=-1), axis=-1)
    assert np.array_equal(corner_edges, expected)
    adjacency = face_adjacency(table, sphere.face_count)
    assert adjacency.nnz == 3 * sphere.face_count and (adjacency != adjacency.T).nnz == 0
    assert adjacency.diagonal().sum() == 0

    junction = t_junction(n=8)
    topology = build_topology(junction.positions, junction.faces)
    nonmanifold = topology.edges.nonmanifold_edges()
    assert nonmanifold.size == 8 and np.all(topology.edges.face_counts[nonmanifold] == 3)
    fin_face = int(junction.face_groups["fin"][0])
    neighbours = topology.adjacency[[fin_face], :].indices
    assert neighbours.size >= 2
    assert topology.components.count == 1


def test_components_are_ordered_by_area_and_vertex_contact_does_not_join():
    big = uv_sphere(4000, radius=0.5)
    small = uv_sphere(4000, radius=0.1, center=(2.0, 0.0, 0.0))
    mesh = combine(small, big, names=("small", "big"))
    topology = build_topology(mesh.positions, mesh.faces)
    components = topology.components
    assert components.count == 2
    assert np.all(components.face_component[mesh.face_groups["big"]] == 0)
    assert np.all(components.face_component[mesh.face_groups["small"]] == 1)
    assert components.areas[0] > components.areas[1] and components.face_component.dtype == np.uint32
    assert np.array_equal(components.faces_of(0), mesh.face_groups["big"])
    assert math.isclose(components.areas[0], 4 * math.pi * 0.25, rel_tol=0.01)

    touching = touching_spheres()
    contact = build_topology(touching.positions, touching.faces)
    assert contact.weld.welded_count == np.unique(touching.positions, axis=0).shape[0]
    assert contact.components.count == 2


def test_face_geometry_and_degenerate_faces():
    positions = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [2, 0, 0]], dtype=np.float32)
    faces = np.array([[0, 1, 2], [0, 1, 3]], dtype=np.uint32)
    geometry = face_geometry(positions, faces)
    assert np.allclose(geometry.areas, [0.5, 0.0])
    assert np.allclose(geometry.normals[0], [0, 0, 1]) and np.all(geometry.normals[1] == 0)
    assert np.allclose(geometry.centroids[0], [1 / 3, 1 / 3, 0])
    weld = weld_vertices(positions)
    table = build_edge_table(welded_faces(faces, weld.weld_map), weld.welded_count)
    components = connected_components(face_adjacency(table, 2), geometry.areas)
    assert components.count == 1


# --------------------------------------------------------------------------------------------------
# Shells and partners
# --------------------------------------------------------------------------------------------------


def test_double_walled_sphere_is_one_part_and_floaters_are_kept_on_the_base(tmp_runtime: Path):
    wall = uv_sphere(20000, double_walled=True)
    topology = build_topology(wall.positions, wall.faces)
    report = classify_shells(topology, EFFECTIVE_RESOLUTION)
    assert topology.components.count == 2
    assert [shell.role for shell in report.shells] == ["base", "inner_sheet"]
    assert report.inner_sheets == {1: 0} and report.shells[1].coverage == 1.0
    assert report.separate_shells == () and report.roots_with_inner_sheets() == (0,)
    assert report.inner_sheet_face_mask(topology.components.face_component).sum() == wall.face_groups["inner"].size

    shell = uv_sphere(2000, radius=0.1, center=(1.5, 0.0, 0.0))
    mesh = combine(wall, shell, floaters(3), names=("wall", "shell", "dust"))
    topology = build_topology(mesh.positions, mesh.faces)
    report = classify_shells(topology, EFFECTIVE_RESOLUTION)
    face_count = mesh.face_count
    min_part_faces = _min_part_faces(face_count)
    assert report.floater_max_faces(min_part_faces) == max(min_part_faces, round(0.0005 * face_count))
    kept, dust = report.split_floaters(topology, min_part_faces)
    shell_component = int(topology.components.face_component[mesh.face_groups["shell"][0]])
    assert kept == (shell_component,)
    assert len(dust) == 3 and all(floater.faces == 4 for floater in dust)
    for floater in dust:
        faces = topology.components.faces_of(floater.component)
        assert np.allclose(floater.centroid, topology.geometry.centroids[faces].mean(axis=0), atol=1e-3)

    owner = np.zeros(face_count, dtype=np.uint16)
    owner[report.face_root(topology.components.face_component) == shell_component] = 1
    base = _base(tmp_runtime, mesh)
    nodes = build_mesh_nodes(base, owner, np.zeros(face_count, dtype=np.uint16), part_ids=["body", "component_001"])
    assert sum(node.face_count for node in nodes) == face_count
    assert np.all(np.isin(mesh.face_groups["dust"], nodes[0].faces_base))
    assert np.array_equal(nodes[1].faces_base, mesh.face_groups["shell"])


def test_large_separate_component_stays_on_the_base():
    mesh = combine(uv_sphere(4000, radius=0.5), uv_sphere(4000, radius=0.45, center=(3.0, 0.0, 0.0)))
    report = classify_shells(build_topology(mesh.positions, mesh.faces), EFFECTIVE_RESOLUTION)
    assert [shell.role for shell in report.shells] == ["base", "major"]
    assert report.separate_shells == ()


def test_pair_opposite_faces_pairs_the_rim_joined_hemisphere_walls():
    mesh = uv_sphere(40000, double_walled=True, rim_joined=True, open_hemisphere=True)
    topology = build_topology(mesh.positions, mesh.faces)
    assert topology.components.count == 1
    report = classify_shells(topology, EFFECTIVE_RESOLUTION)
    assert report.inner_sheets == {}
    eps = global_shell_eps(topology, EFFECTIVE_RESOLUTION)
    partners = pair_opposite_faces(topology.geometry.centroids, topology.geometry.normals, eps)
    assert partners.dtype == np.int32 and partners.shape == (mesh.face_count,)
    inner, outer = mesh.group_mask("inner"), mesh.group_mask("outer")
    inner_partners = partners[inner]
    paired = inner_partners != NO_PARTNER
    assert paired.mean() >= 0.95
    assert np.all(outer[inner_partners[paired]])
    outer_partners = partners[outer]
    assert np.all(inner[outer_partners[outer_partners != NO_PARTNER]])
    assert partner_coverage(partners, inner) == paired.mean()
    LOG.info("rim-joined hemisphere: %d faces, inner-wall partner coverage %.4f", mesh.face_count, paired.mean())

    single = uv_sphere(4000)
    single_topology = build_topology(single.positions, single.faces)
    alone = pair_opposite_faces(single_topology.geometry.centroids, single_topology.geometry.normals,
                                global_shell_eps(single_topology, EFFECTIVE_RESOLUTION))
    assert np.all(alone == NO_PARTNER)


def test_shell_eps_formula():
    assert shell_eps(1536, 0.0) == pytest.approx(3 / 1536)
    assert shell_eps(1536, 0.01) == pytest.approx(0.015)
    with pytest.raises(ValueError):
        shell_eps(0, 0.01)


# --------------------------------------------------------------------------------------------------
# Partition (section 3.4)
# --------------------------------------------------------------------------------------------------


def _reference_node(base_faces: np.ndarray, owner: np.ndarray, labels: np.ndarray, k: int):
    """Literal transcription of the section 3.4 docstring (Python loops; small meshes only)."""
    faces_k = np.flatnonzero(owner == k)
    unique = np.unique(base_faces[faces_k])
    local_of = {int(v): i for i, v in enumerate(unique)}
    incident: dict[int, set[int]] = {}
    for f in faces_k:
        for v in base_faces[f]:
            incident.setdefault(int(v), set()).add(int(labels[f]))
    duplicates: list[tuple[int, int]] = []
    for v in sorted(incident):
        for label in sorted(incident[v])[1:]:
            duplicates.append((v, label))
    dup_local = {pair: len(unique) + i for i, pair in enumerate(duplicates)}
    local_faces = []
    for f in faces_k:
        label = int(labels[f])
        row = []
        for v in base_faces[f]:
            v = int(v)
            row.append(local_of[v] if label == min(incident[v]) else dup_local[(v, label)])
        local_faces.append(row)
    vmap = np.concatenate([unique, np.asarray([v for v, _ in duplicates], dtype=np.int64)]).astype(np.uint32)
    feature = [min(incident[int(v)]) for v in unique] + [label for _, label in duplicates]
    return faces_k, vmap, np.asarray(local_faces, dtype=np.uint32), np.asarray(feature, dtype=np.uint16)


def test_build_mesh_nodes_follows_the_vmap_rule(tmp_runtime: Path):
    mesh = uv_sphere(1500)
    base = _base(tmp_runtime, mesh)
    labels = band_labels(mesh, (30, 3, 7, 3))
    owner = np.zeros(mesh.face_count, dtype=np.uint16)
    centroid = mesh.positions[mesh.faces].mean(axis=1)
    owner[centroid[:, 0] > 0.25] = 1
    owner[centroid[:, 1] < -0.3] = 2
    nodes = build_mesh_nodes(base, owner, labels, part_ids=["body", "hair", "clothing.footwear"])
    assert [node.index for node in nodes] == [0, 1, 2] and nodes[2].node_name == "part:clothing.footwear"
    assert sum(node.face_count for node in nodes) == mesh.face_count
    assert np.array_equal(face_owner_from_nodes(nodes, mesh.face_count), owner)
    for node in nodes:
        faces_k, vmap, local_faces, feature = _reference_node(mesh.faces, owner, labels, node.index)
        assert np.array_equal(node.faces_base, faces_k) and node.faces_base.dtype == np.uint32
        assert np.array_equal(node.vmap, vmap) and node.vmap.dtype == np.uint32
        assert np.array_equal(node.local_faces, local_faces) and node.local_faces.dtype == np.uint32
        assert np.array_equal(node.feature_ids, feature) and node.feature_ids.dtype == np.uint16
        head = node.vmap[: node.unique_count]
        assert np.all(np.diff(head.astype(np.int64)) > 0)
        assert np.array_equal(node.vmap[node.local_faces], mesh.faces[node.faces_base])
        assert np.all(node.feature_ids[node.local_faces] == labels[node.faces_base][:, None])
        positions, normals, uv = gather_node_attributes(base, node)
        assert np.array_equal(positions, mesh.positions[node.vmap]) and normals.shape == positions.shape
        assert uv.shape == (node.vertex_count, 2)
    assert nodes[0].vertex_count > nodes[0].unique_count


def test_build_mesh_nodes_rejects_bad_partitions(tmp_runtime: Path):
    mesh = uv_sphere(500)
    base = _base(tmp_runtime, mesh)
    labels = np.zeros(mesh.face_count, dtype=np.uint16)
    owner = np.zeros(mesh.face_count, dtype=np.uint16)
    with pytest.raises(ValueError, match="owns no faces"):
        build_mesh_nodes(base, owner, labels, part_ids=["body", "hair"])
    with pytest.raises(ValueError):
        build_mesh_nodes(base, owner + 1, labels, part_ids=["body"])
    with pytest.raises(ValueError):
        build_mesh_nodes(base, owner[:-1], labels, part_ids=["body"])
    with pytest.raises(ValueError):
        build_mesh_nodes(base, owner.astype(np.float32), labels, part_ids=["body"])
    with pytest.raises(ValueError):
        build_mesh_nodes(base, owner, labels.astype(np.int64) - 1, part_ids=["body"])
    owner[:10] = 1
    with pytest.raises(ValueError):
        build_mesh_nodes(base, owner, labels, part_ids=["body", "body"])


# --------------------------------------------------------------------------------------------------
# Reassembly (A1, A4)
# --------------------------------------------------------------------------------------------------


def test_reassembly_is_bit_exact_from_the_written_files(tmp_runtime: Path):
    mesh = uv_sphere(3000)
    base = _base(tmp_runtime, mesh)
    labels = band_labels(mesh, (30, 3, 7))
    owner = (mesh.positions[mesh.faces].mean(axis=1)[:, 1] > 0.2).astype(np.uint16)
    nodes = build_mesh_nodes(base, owner, labels, part_ids=["body", "hair"])
    glb = tmp_runtime / "model_parts.glb"
    _write_nodes(glb, base, nodes)

    owner_file = tmp_runtime / "face_owner.u16"
    owner.astype("<u2").tofile(owner_file)
    vmaps = []
    for node in nodes:
        path = tmp_runtime / Path(vmap_buffer_spec(node.part_id).file).name
        node.vmap.astype("<u4").tofile(path)
        vmaps.append(np.fromfile(path, dtype="<u4"))
    report = verify_reassembly(base, glb, part_ids=["body", "hair"], vmaps=vmaps,
                               face_owner=np.fromfile(owner_file, dtype="<u2"))
    assert report.all_exact and report.nodes_checked == 2 and report.problems == ()
    assert report.to_manifest().all_exact
    report.raise_if_inexact()
    assert verify_mesh_nodes(base, glb, nodes).all_exact


def test_reassembly_detects_every_kind_of_difference(tmp_runtime: Path):
    mesh = uv_sphere(3000)
    base = _base(tmp_runtime, mesh)
    labels = np.zeros(mesh.face_count, dtype=np.uint16)
    owner = (mesh.positions[mesh.faces].mean(axis=1)[:, 1] > 0.2).astype(np.uint16)
    nodes = build_mesh_nodes(base, owner, labels, part_ids=["body", "hair"])
    specs = [NodeSpec.from_mesh_node(base, node, {"kind": "physical"}) for node in nodes]

    shifted = specs[1].positions.copy()
    shifted[0, 0] = np.nextafter(shifted[0, 0], np.float32(np.inf))
    tampered = tmp_runtime / "tampered.glb"
    write_parts_glb(tampered, base, [specs[0], NodeSpec(specs[1].part_id, shifted, specs[1].faces,
                                                         specs[1].feature_ids, {"kind": "physical"},
                                                         specs[1].normals, specs[1].uv)], scene_extras={})
    report = verify_mesh_nodes(base, tampered, nodes)
    assert not report.positions_exact and report.normals_exact and report.uvs_exact and report.faces_exact
    with pytest.raises(ReassemblyError) as info:
        report.raise_if_inexact()
    assert info.value.code == "partition_failed" and "part:hair" in str(info.value)

    flipped_uv = specs[0].uv.copy()
    flipped_uv[3, 1] = -0.0 if flipped_uv[3, 1] == 0.0 else -flipped_uv[3, 1]
    uv_glb = tmp_runtime / "uv.glb"
    write_parts_glb(uv_glb, base, [NodeSpec(specs[0].part_id, specs[0].positions, specs[0].faces,
                                            specs[0].feature_ids, {"kind": "base"}, specs[0].normals, flipped_uv),
                                   specs[1]], scene_extras={})
    assert not verify_mesh_nodes(base, uv_glb, nodes).uvs_exact

    only_base = tmp_runtime / "only_base.glb"
    write_parts_glb(only_base, base, specs[:1], scene_extras={})
    missing = verify_mesh_nodes(base, only_base, nodes)
    assert not missing.verified and not missing.faces_exact and missing.nodes_checked == 1

    good = tmp_runtime / "good.glb"
    write_parts_glb(good, base, specs, scene_extras={})
    wrong_owner = owner.copy()
    wrong_owner[int(np.flatnonzero(owner == 1)[0])] = 0
    swapped = verify_reassembly(base, good, part_ids=["body", "hair"], vmaps=[n.vmap for n in nodes],
                                face_owner=wrong_owner)
    assert not swapped.faces_exact and swapped.positions_exact
    unreadable = verify_mesh_nodes(base, tmp_runtime / "missing.glb", nodes)
    assert not unreadable.verified and not unreadable.to_manifest().all_exact


# --------------------------------------------------------------------------------------------------
# Seams (section 3.5)
# --------------------------------------------------------------------------------------------------


def test_clean_cut_gives_one_closed_loop_per_sheet():
    size = cube_sphere_size(4000)
    mesh = uv_sphere(4000)
    topology = build_topology(mesh.positions, mesh.faces)
    owner = mesh.group_mask("+y").astype(np.uint16)
    seams = build_seams(topology, owner, ["body", "hair"])
    assert len(seams.seams) == 1
    seam = seams.seams[0]
    assert (seam.id, seam.a, seam.b, seam.host_opening) == ("seam_0", "hair", "body", True)
    assert seam.boundary_edges == 4 * size and len(seam.loops) == 1
    loop = seam.loops[0]
    assert loop.closed and loop.count == 4 * size and loop.start == 0
    vertices = seams.loops_of("seam_0")[0]
    assert np.unique(vertices).size == 4 * size and vertices[0] == vertices.min()
    known = {tuple(edge) for edge in topology.edges.edges.tolist()}
    ring = np.append(vertices, vertices[0]).astype(np.int64)
    assert all((min(u, v), max(u, v)) in known for u, v in zip(ring[:-1].tolist(), ring[1:].tolist(), strict=True))
    assert seam.length == pytest.approx(4 * 0.5 * math.acos(1 / 3), rel=2e-3)
    hair_face = int(mesh.face_groups["+y"][0])
    first_edge = (int(vertices[0]), int(vertices[1]))
    corners = topology.welded[owner == 1]
    directed = {(int(a), int(b)) for row in corners.tolist() for a, b in zip(row, row[1:] + row[:1], strict=True)}
    assert first_edge in directed and owner[hair_face] == 1
    record = seams.manifest_records()[0]
    assert isinstance(record, SeamRecord) and record.loops[0].count == 4 * size
    assert seams.seam_ids_for("hair") == ["seam_0"] == seams.seam_ids_for("body")
    assert seams.loop_vertices.dtype == np.uint32
    assert np.all(open_boundary_loops(topology, owner, 2) == 0)


def test_double_walled_cut_puts_both_sheet_loops_in_one_record():
    mesh = uv_sphere(8000, double_walled=True)
    size = cube_sphere_size(4000)
    topology = build_topology(mesh.positions, mesh.faces)
    owner = mesh.group_mask("+y").astype(np.uint16)
    assert owner.sum() == 2 * 2 * size * size
    seams = build_seams(topology, owner, ["body", "hair"])
    assert len(seams.seams) == 1
    loops = seams.seams[0].loops
    assert len(loops) == 2 and all(loop.closed and loop.count == 4 * size for loop in loops)
    assert seams.seams[0].boundary_edges == 8 * size


def test_non_manifold_junction_gives_open_chains():
    n = 8
    mesh = t_junction(n=n)
    topology = build_topology(mesh.positions, mesh.faces)
    owner = np.zeros(mesh.face_count, dtype=np.uint16)
    owner[mesh.face_groups["fin"]] = 1
    seams = build_seams(topology, owner, ["body", "accessory.wing"])
    assert len(seams.seams) == 1
    (loop,) = seams.seams[0].loops
    assert not loop.closed and loop.count == n + 1 and seams.seams[0].boundary_edges == n
    assert seams.seams[0].length == pytest.approx(0.8, rel=1e-5)

    owner[mesh.face_groups["sheet_front"]] = 2
    seams = build_seams(topology, owner, ["body", "accessory.wing", "clothing.dress"])
    pairs = [(seam.id, seam.a, seam.b, seam.host_opening) for seam in seams.seams]
    assert pairs == [("seam_0", "accessory.wing", "body", True), ("seam_1", "clothing.dress", "body", True),
                     ("seam_2", "clothing.dress", "accessory.wing", False)]
    for seam in seams.seams:
        assert seam.boundary_edges == n and [lp.closed for lp in seam.loops] == [False]


def test_separate_shells_need_no_seam_and_open_boundaries_are_counted():
    mesh = combine(uv_sphere(2000), uv_sphere(1000, radius=0.1, center=(2.0, 0.0, 0.0)), names=("body", "shell"))
    topology = build_topology(mesh.positions, mesh.faces)
    owner = np.zeros(mesh.face_count, dtype=np.uint16)
    owner[mesh.face_groups["shell"]] = 1
    seams = build_seams(topology, owner, ["body", "component_001"])
    assert seams.seams == () and seams.loop_vertices.size == 0

    hemisphere = uv_sphere(2000, open_hemisphere=True)
    topology = build_topology(hemisphere.positions, hemisphere.faces)
    assert np.array_equal(open_boundary_loops(topology, np.zeros(hemisphere.face_count, dtype=np.uint16), 1), [1])


# --------------------------------------------------------------------------------------------------
# Scale: 2 M faces, and the real alice_front fixture
# --------------------------------------------------------------------------------------------------


@pytest.mark.slow
def test_two_million_face_partition_and_write_under_60_seconds(tmp_runtime: Path):
    mesh = uv_sphere(2_000_000)
    base = _base(tmp_runtime, mesh)
    labels = band_labels(mesh, (30, 3, 7, 40, 41))
    owner = np.zeros(mesh.face_count, dtype=np.uint16)
    owner[mesh.face_groups["+y"]] = 1
    owner[mesh.face_groups["-y"]] = 2
    part_ids = ["body", "hair", "clothing.dress"]

    started = time.perf_counter()
    topology = build_topology(base.positions, base.faces)
    topology_s = time.perf_counter() - started
    nodes = build_mesh_nodes(base, owner, labels, part_ids=part_ids)
    seams = build_seams(topology, owner, part_ids)
    glb = tmp_runtime / "model_parts.glb"
    _write_nodes(glb, base, nodes)
    report = verify_mesh_nodes(base, glb, nodes)
    total_s = time.perf_counter() - started
    LOG.info("2M synthetic: %d faces, topology %.2f s, partition+seams+write+verify total %.2f s",
             base.face_count, topology_s, total_s)
    assert base.face_count >= 1_900_000
    assert report.all_exact
    assert [(seam.a, seam.b) for seam in seams.seams] == [("hair", "body"), ("clothing.dress", "body")]
    assert all(loop.closed for seam in seams.seams for loop in seam.loops)
    assert total_s < 60.0


def _fixture_dir(name: str) -> Path | None:
    text = (REPO_ROOT / "test" / "part_aware" / "fixtures.json").read_text(encoding="utf-8")
    # The catalog's "regenerate" command holds unescaped Windows backslashes; escape them before parsing.
    catalog = json.loads(re.sub(r'\\(?![\\/"bfnrtu])', r"\\\\", text))
    entry = catalog["fixtures"].get(name)
    if entry is None:
        return None
    path = REPO_ROOT / entry["dir"]
    return path if (path / "model.glb").is_file() else None


def _find_key(value, key: str):
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for item in value.values():
            found = _find_key(item, key)
            if found is not None:
                return found
    return None


@pytest.mark.slow
def test_real_alice_front_fixture_read_in_place():
    fixture = _fixture_dir("alice_front")
    if fixture is None:
        pytest.skip("the alice_front fixture (test/outputs/engine_smoke) is not present on this machine")
    model = fixture / "model.glb"
    listing = sorted(p.name for p in fixture.iterdir())
    before = hashlib.sha256(model.read_bytes()).hexdigest()
    summary_path = fixture / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    effective_resolution = int(_find_key(summary, "effective_resolution") or EFFECTIVE_RESOLUTION)

    base = read_base_glb(model, expected_sha256=before)
    started = time.perf_counter()
    topology = build_topology(base.positions, base.faces)
    topology_s = time.perf_counter() - started
    report = classify_shells(topology, effective_resolution)
    shells_s = time.perf_counter() - started - topology_s
    eps = global_shell_eps(topology, effective_resolution)
    partners = pair_opposite_faces(topology.geometry.centroids, topology.geometry.normals, eps)
    total_s = time.perf_counter() - started
    component0 = topology.components.face_component == 0
    coverage = partner_coverage(partners, component0)
    inner = report.inner_sheets
    LOG.info(
        "alice_front: F=%d V=%d W=%d components=%d c0=%.4f of F; inner sheets=%d (%d faces); separate shells=%d; "
        "eps=%.6f; weld+components %.2f s, shells %.2f s, total %.2f s; component-0 partner coverage %.4f",
        base.face_count, base.vertex_count, topology.weld.welded_count, topology.components.count,
        component0.mean(), len(inner), int(sum(topology.components.face_counts[c] for c in inner)),
        len(report.separate_shells), eps, topology_s, shells_s, total_s, coverage,
    )
    assert total_s < 60.0
    assert coverage >= 0.70
    assert hashlib.sha256(model.read_bytes()).hexdigest() == before
    assert sorted(p.name for p in fixture.iterdir()) == listing
