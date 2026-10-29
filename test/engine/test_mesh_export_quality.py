"""Known geometric fixtures verify that the quality probe detects real gaps, not UV seams."""
from __future__ import annotations

import numpy as np
import trimesh

from mesh_quality_probe import analyze_mesh_arrays


def box_arrays():
    box = trimesh.creation.box()
    return np.asarray(box.vertices, dtype=np.float32), np.asarray(box.faces, dtype=np.uint32)


def test_uv_chart_vertex_splits_do_not_count_as_holes():
    positions, faces = box_arrays()
    split_positions = positions[faces].reshape(-1, 3)
    split_faces = np.arange(len(split_positions), dtype=np.uint32).reshape(-1, 3)
    report = analyze_mesh_arrays(split_positions, split_faces)
    assert report["raw_boundary_edges"] == 36
    assert report["welded_vertices"] == 8
    assert report["boundary_edges"] == 0
    assert report["connected_components"] == 1
    assert report["closed_manifold"]
    assert report["closed_manifold_handle_count"] == 0


def test_missing_surface_is_detected_as_one_triangular_boundary_loop():
    positions, faces = box_arrays()
    report = analyze_mesh_arrays(positions, faces[1:])
    assert report["boundary_edges"] == 3
    assert report["boundary"]["closed_loops"] == 1
    assert report["boundary"]["total_length_over_diagonal"] > 0
    assert not report["closed_manifold"]


def test_intentional_open_sheet_is_reported_without_modifying_geometry():
    positions = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=np.float32)
    faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.uint32)
    before = positions.copy(), faces.copy()
    report = analyze_mesh_arrays(positions, faces)
    assert report["boundary_edges"] == 4
    assert report["boundary"]["closed_loops"] == 1
    assert not report["closed_manifold"]
    np.testing.assert_array_equal(positions, before[0])
    np.testing.assert_array_equal(faces, before[1])


def test_duplicate_face_creates_nonmanifold_edges_and_is_reported():
    positions, faces = box_arrays()
    report = analyze_mesh_arrays(positions, np.concatenate((faces, faces[:1])))
    assert report["duplicate_faces"] == 1
    assert report["nonmanifold_edges"] == 3
    assert not report["closed_manifold"]


def test_collapsed_face_and_reversed_winding_are_detected_separately():
    positions, faces = box_arrays()
    degenerate = analyze_mesh_arrays(positions, np.concatenate((faces, np.array([[0, 0, 0]], dtype=np.uint32))))
    assert degenerate["degenerate_faces"] == 1
    reversed_faces = faces.copy()
    reversed_faces[0] = reversed_faces[0, ::-1]
    winding = analyze_mesh_arrays(positions, reversed_faces)
    assert winding["boundary_edges"] == 0
    assert winding["inconsistent_winding_edges"] == 3
    assert not winding["closed_manifold"]



def test_equilateral_triangle_aspect_and_normalized_lengths_are_scale_invariant():
    import pytest
    from mesh_quality_probe import triangle_shape_metrics

    positions = np.array([[0, 0, 0], [1, 0, 0], [0.5, np.sqrt(3) / 2, 0]], dtype=np.float64)
    faces = np.array([[0, 1, 2]])
    original = triangle_shape_metrics(positions, faces, voxel_size=0.01)
    scaled = triangle_shape_metrics(positions * 5, faces, voxel_size=0.05)
    assert original["aspect_longest_edge_over_altitude"]["p100"] == pytest.approx(2 / np.sqrt(3))
    assert scaled["aspect_longest_edge_over_altitude"] == pytest.approx(original["aspect_longest_edge_over_altitude"])
    assert scaled["longest_edge_over_diagonal"] == pytest.approx(original["longest_edge_over_diagonal"])
    assert scaled["longest_edge_in_voxels"] == pytest.approx(original["longest_edge_in_voxels"])
    assert original["needle_faces_aspect_100"] == 0


def test_long_needle_has_expected_edge_to_altitude_ratio_and_voxel_length():
    import pytest
    from mesh_quality_probe import triangle_shape_metrics

    positions = np.array([[0, 0, 0], [1, 0, 0], [1, 1e-4, 0]], dtype=np.float64)
    report = triangle_shape_metrics(positions, np.array([[0, 1, 2]]), voxel_size=1 / 1024)
    assert report["needle_faces_aspect_100"] == 1
    assert report["needle_area_fraction"] == pytest.approx(1)
    assert report["aspect_longest_edge_over_altitude"]["p100"] == pytest.approx(10000.0001)
    assert report["longest_edge_in_voxels"]["p100"] == pytest.approx(1024, rel=1e-7)
    assert report["worst_needles"][0]["altitude"] == pytest.approx(1e-4)


def test_tiny_slivers_do_not_count_as_long_needles_and_collapsed_faces_are_separate():
    from mesh_quality_probe import triangle_shape_metrics

    positions = np.array([[0, 0, 0], [0.001, 0, 0], [0.001, 1e-7, 0],
                          [100, 0, 0], [101, 0, 0], [100.5, 0.866, 0]], dtype=np.float64)
    faces = np.array([[0, 1, 2], [3, 4, 5], [0, 0, 0]])
    report = triangle_shape_metrics(positions, faces)
    assert report["aspect_counts"]["1000"] == 1
    assert report["needle_faces_aspect_100"] == 0
    assert report["collapsed_or_numerically_zero_area_faces"] == 1
