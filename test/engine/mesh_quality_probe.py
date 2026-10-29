"""CPU-only geometric diagnostics for generated test GLBs.

UV chart boundaries are welded by exact float32 position before edge analysis.
Open boundaries are measured, not silently classified as defects: legitimate
open surfaces, garment openings and disconnected semantic parts may be valid.
Watertightness alone cannot prove that geometry matches its reference image.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
TEST_ROOT = REPO / "test"
if str(REPO / "backend") not in sys.path:
    sys.path.insert(0, str(REPO / "backend"))

import numpy as np
from scipy import sparse
from scipy.sparse import csgraph

from parts.components import build_topology
from parts.weld import build_edge_table


def _boundary_summary(positions: np.ndarray, edges: np.ndarray, diagonal: float) -> dict[str, Any]:
    if not len(edges):
        return {"components": 0, "closed_loops": 0, "branched_components": 0,
                "total_length": 0.0, "total_length_over_diagonal": 0.0, "largest": []}
    vertices, inverse = np.unique(edges, return_inverse=True)
    pairs = inverse.reshape(-1, 2)
    graph = sparse.coo_array(
        (np.ones(len(pairs) * 2, dtype=np.int8),
         (np.concatenate((pairs[:, 0], pairs[:, 1])),
          np.concatenate((pairs[:, 1], pairs[:, 0])))),
        shape=(len(vertices), len(vertices)),
    ).tocsr()
    count, labels = csgraph.connected_components(graph, directed=False)
    degree = np.bincount(pairs.reshape(-1), minlength=len(vertices))
    edge_labels = labels[pairs[:, 0]]
    lengths = np.linalg.norm(
        positions[edges[:, 1]].astype(np.float64) - positions[edges[:, 0]], axis=1
    )
    order = np.argsort(edge_labels, kind="stable")
    sizes = np.bincount(edge_labels, minlength=count)
    offsets = np.concatenate(([0], np.cumsum(sizes)))
    groups = []
    for index in range(count):
        selected = order[offsets[index]:offsets[index + 1]]
        group_vertices = np.unique(pairs[selected])
        points = positions[vertices[group_vertices]]
        degrees = degree[group_vertices]
        length = float(lengths[selected].sum())
        span = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
        groups.append({"edges": int(len(selected)), "vertices": int(len(group_vertices)),
                       "length": length, "length_over_diagonal": length / diagonal,
                       "span_over_diagonal": span / diagonal,
                       "closed_loop": bool(np.all(degrees == 2)),
                       "branched": bool(np.any(degrees > 2))})
    groups.sort(key=lambda item: item["length"], reverse=True)
    total = float(lengths.sum())
    return {"components": int(count),
            "closed_loops": sum(group["closed_loop"] for group in groups),
            "branched_components": sum(group["branched"] for group in groups),
            "total_length": total, "total_length_over_diagonal": total / diagonal,
            "largest": groups[:20]}


def triangle_shape_metrics(positions: np.ndarray, faces: np.ndarray, *,
                           voxel_size: float | None = None) -> dict[str, Any]:
    """Measure needle/sliver geometry without assuming an intended topology.

    Aspect = longest edge / altitude to that edge = L**2 / (2*area).
    The long-edge filter prevents tiny numerical triangles from dominating
    the needle count. These thresholds are diagnostics, not automatic repairs.
    """
    points = np.asarray(positions)
    triangles = np.asarray(faces)
    if points.ndim != 2 or points.shape[1] != 3 or triangles.ndim != 2 or triangles.shape[1] != 3:
        raise ValueError("positions and triangular faces must have shapes (V,3)/(F,3)")
    if not len(triangles):
        raise ValueError("mesh must contain faces")
    if voxel_size is not None and voxel_size <= 0:
        raise ValueError("voxel_size must be positive")
    diagonal = float(np.linalg.norm(points.max(axis=0).astype(np.float64) - points.min(axis=0)))
    normalizer = diagonal if diagonal > 0 else 1.0
    count = len(triangles)
    longest = np.empty(count, dtype=np.float64)
    twice_area = np.empty(count, dtype=np.float64)
    for start in range(0, count, 1 << 17):
        stop = min(start + (1 << 17), count)
        corners = points[triangles[start:stop]].astype(np.float64)
        edge_a = corners[:, 1] - corners[:, 0]
        edge_b = corners[:, 2] - corners[:, 0]
        edge_c = corners[:, 2] - corners[:, 1]
        longest[start:stop] = np.sqrt(np.maximum.reduce((
            np.einsum("ij,ij->i", edge_a, edge_a),
            np.einsum("ij,ij->i", edge_b, edge_b),
            np.einsum("ij,ij->i", edge_c, edge_c),
        )))
        twice_area[start:stop] = np.linalg.norm(np.cross(edge_a, edge_b), axis=1)
    finite_area = twice_area > np.finfo(np.float64).eps * normalizer * normalizer
    altitude = np.divide(twice_area, longest, out=np.zeros_like(longest), where=longest > 0)
    aspect = np.divide(longest * longest, twice_area, out=np.full_like(longest, np.inf), where=finite_area)
    long = longest >= 0.01 * normalizer
    needles = long & (aspect >= 100) & finite_area
    surface_area = float(twice_area.sum())
    aspect_values = aspect[finite_area]
    def quantiles(values: np.ndarray, percentiles=(50, 95, 99, 99.9, 100)):
        if not len(values):
            return {}
        measured = np.percentile(values, percentiles)
        return {f"p{p:g}": float(v) for p, v in zip(percentiles, measured)}
    top = np.flatnonzero(needles)
    if len(top):
        top = top[np.argsort(aspect[top])[-20:][::-1]]
    result: dict[str, Any] = {
        "faces": count, "diagonal": diagonal,
        "collapsed_or_numerically_zero_area_faces": int(np.count_nonzero(~finite_area)),
        "longest_edge": quantiles(longest),
        "longest_edge_over_diagonal": quantiles(longest / normalizer),
        "altitude_over_diagonal": quantiles(altitude / normalizer, (0, 0.1, 1, 50)),
        "aspect_longest_edge_over_altitude": quantiles(aspect_values),
        "aspect_counts": {str(threshold): int(np.count_nonzero(finite_area & (aspect >= threshold)))
                          for threshold in (10, 30, 100, 300, 1000, 10000)},
        "long_sliver_counts": {
            str(threshold): int(np.count_nonzero(long & finite_area & (aspect >= threshold)))
            for threshold in (10, 30, 100, 300, 1000, 10000)
        },
        "long_sliver_filter": "longest_edge >= 1% of object diagonal",
        "needle_faces_aspect_100": int(np.count_nonzero(needles)),
        "needle_area_fraction": float(twice_area[needles].sum() / surface_area) if surface_area else 0.0,
        "worst_needles": [
            {"face": int(index), "aspect": float(aspect[index]),
             "longest_edge": float(longest[index]), "altitude": float(altitude[index]),
             "centroid": points[triangles[index]].mean(axis=0).astype(float).tolist()}
            for index in top
        ],
    }
    if voxel_size is not None:
        result["voxel_size"] = voxel_size
        result["longest_edge_in_voxels"] = quantiles(longest / voxel_size)
        result["voxel_sliver_counts"] = {
            f"edge_{edge_voxels}_voxels_aspect_{threshold}":
                int(np.count_nonzero(finite_area & (longest >= edge_voxels * voxel_size) & (aspect >= threshold)))
            for edge_voxels in (4, 8, 16) for threshold in (30, 100, 1000)
        }
    return result




def analyze_mesh_arrays(positions: np.ndarray, faces: np.ndarray) -> dict[str, Any]:
    """Measure mesh topology without changing positions/faces or UV/material data."""
    positions = np.asarray(positions, dtype=np.float32).copy()
    faces = np.asarray(faces)
    if positions.ndim != 2 or positions.shape[1] != 3 or not np.isfinite(positions).all():
        raise ValueError("positions must be finite (V, 3)")
    if faces.ndim != 2 or faces.shape[1] != 3 or faces.dtype.kind not in "iu":
        raise ValueError("faces must be integer (F, 3)")
    if len(faces) == 0 or len(positions) == 0:
        raise ValueError("mesh must have vertices and triangular faces")
    if faces.max() >= len(positions) or faces.min() < 0:
        raise ValueError("face vertex index is outside positions")
    faces = faces.astype(np.uint32, copy=False)
    # +0 and -0 are the same geometric position, despite distinct float bits.
    positions[positions == 0] = 0
    extent = positions.max(axis=0) - positions.min(axis=0)
    diagonal = float(np.linalg.norm(extent.astype(np.float64)))
    normalizer = diagonal if diagonal > 0 else 1.0

    raw_table = build_edge_table(faces, len(positions))
    raw_boundaries = int(np.count_nonzero(raw_table.face_counts == 1))
    del raw_table

    topology = build_topology(positions, faces)
    welded = topology.weld.weld_map[faces]
    table = topology.edges
    counts = table.face_counts
    boundary_mask = counts == 1
    nonmanifold = int(np.count_nonzero(counts > 2))
    boundary = _boundary_summary(topology.weld.welded_positions, table.edges[boundary_mask], normalizer)

    repeated = ((welded[:, 0] == welded[:, 1]) | (welded[:, 1] == welded[:, 2])
                | (welded[:, 0] == welded[:, 2]))
    area_floor = np.finfo(np.float64).eps * normalizer * normalizer
    degenerate = repeated | (topology.geometry.areas <= area_floor)
    canonical = np.sort(welded, axis=1)
    unique_faces = np.unique(canonical, axis=0)
    duplicate_faces = int(len(faces) - len(unique_faces))

    edge_ids = table.face_edges.reshape(-1)
    directed = np.where(welded < np.roll(welded, -1, axis=1), 1, -1).reshape(-1)
    valid = edge_ids >= 0
    signed_counts = np.bincount(edge_ids[valid], weights=directed[valid], minlength=len(counts))
    inconsistent_winding = int(np.count_nonzero((counts == 2) & (np.abs(signed_counts) == 2)))

    components = topology.components
    total_area = float(topology.geometry.areas.sum())
    used_vertices = int(len(np.unique(welded)))
    chi = used_vertices - table.edge_count + len(faces)
    closed_manifold = not (np.any(boundary_mask) or nonmanifold or np.any(degenerate)
                           or duplicate_faces or inconsistent_winding)
    handles = (2 * components.count - chi) / 2 if closed_manifold else None
    return {
        "vertices": int(len(positions)), "welded_vertices": topology.weld.welded_count,
        "faces": int(len(faces)), "diagonal": diagonal, "surface_area": total_area,
        "raw_boundary_edges": raw_boundaries,
        "boundary_edges": int(np.count_nonzero(boundary_mask)),
        "nonmanifold_edges": nonmanifold,
        "degenerate_faces": int(np.count_nonzero(degenerate)),
        "duplicate_faces": duplicate_faces, "inconsistent_winding_edges": inconsistent_winding,
        "connected_components": components.count,
        "largest_component_face_count": int(components.face_counts[0]) if components.count else 0,
        "largest_component_area_fraction": (
            float(components.areas[0] / total_area) if total_area > 0 else 0.0),
        "component_face_counts": components.face_counts[:20].astype(int).tolist(),
        "euler_characteristic": int(chi), "closed_manifold": closed_manifold,
        "closed_manifold_handle_count": handles, "boundary": boundary,
        "caveat": "Open boundaries and handles may be intentional. A closed manifold can still have missing visible surfaces or internal duplicate walls.",
    }


def analyze_file(path: Path) -> dict[str, Any]:
    import trimesh

    resolved = path.resolve()
    if not resolved.is_relative_to(TEST_ROOT.resolve()):
        raise ValueError(f"test mesh must be under {TEST_ROOT}")
    started = time.perf_counter()
    loaded = trimesh.load_scene(str(resolved), process=False)
    mesh = loaded.to_mesh()
    result = analyze_mesh_arrays(np.asarray(mesh.vertices), np.asarray(mesh.faces))
    result.update({"path": str(resolved), "file_size_bytes": resolved.stat().st_size,
                   "analysis_seconds": round(time.perf_counter() - started, 3)})
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("meshes", nargs="+", type=Path)
    parser.add_argument("--output", type=Path,
                        default=TEST_ROOT / "outputs/generation_quality/mesh_quality.json")
    parser.add_argument("--expect-closed", action="store_true",
                        help="fail on open/nonmanifold/degenerate/duplicate/winding defects; only use for known closed fixtures")
    options = parser.parse_args(argv)
    output = options.output.resolve()
    if not output.is_relative_to(TEST_ROOT.resolve()):
        parser.error("report output must stay under test/")
    reports = [analyze_file(path) for path in options.meshes]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"meshes": reports}, indent=2) + "\n", encoding="utf-8")
    for report in reports:
        print(json.dumps({key: report[key] for key in (
            "path", "faces", "raw_boundary_edges", "boundary_edges",
            "nonmanifold_edges", "degenerate_faces", "duplicate_faces",
            "inconsistent_winding_edges", "connected_components",
            "closed_manifold", "closed_manifold_handle_count", "analysis_seconds"
        )}), flush=True)
    print(f"Report: {output}", flush=True)
    return int(options.expect_closed and any(not report["closed_manifold"] for report in reports))


if __name__ == "__main__":
    raise SystemExit(main())
