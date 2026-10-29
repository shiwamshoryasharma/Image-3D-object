"""CPU component shape/connectivity comparison for test-only export variants."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "backend"), str(Path(__file__).resolve().parent)]
import numpy as np
import trimesh
from scipy import sparse
from scipy.sparse import csgraph
from scipy.spatial import cKDTree
from parts.components import build_topology


def compare(path, reference, resolution):
    mesh = trimesh.load_scene(str(path), process=False).to_mesh()
    positions = np.asarray(mesh.vertices, dtype=np.float32)
    faces = np.asarray(mesh.faces, dtype=np.uint32)
    topo = build_topology(positions, faces)
    faces = topo.welded
    points = topo.weld.welded_positions.astype(np.float64)
    areas = topo.geometry.areas
    table = topo.edges
    results = {}
    counts = table.face_counts
    pair_ids = np.flatnonzero(counts == 2)
    starts = table.face_offsets[pair_ids]
    pairs = np.stack((table.edge_faces[starts], table.edge_faces[starts + 1]), axis=1)
    graph = sparse.coo_array((np.ones(len(pairs), dtype=bool), (pairs[:, 0], pairs[:, 1])),
                             shape=(len(faces), len(faces))).tocsr()
    manifold_count, manifold_labels = csgraph.connected_components(graph, directed=False)
    rows = faces.reshape(-1)
    cols = np.roll(faces, -1, axis=1).reshape(-1)
    vertex_graph = sparse.coo_array((np.ones(len(rows), dtype=bool), (rows, cols)),
                                    shape=(len(points), len(points))).tocsr()
    vertex_count, vertex_labels = csgraph.connected_components(vertex_graph, directed=False)
    whole_diag2 = float(np.sum((points.max(0) - points.min(0)) ** 2))
    for name, count, labels in (
        ("vertex", vertex_count, vertex_labels[faces[:, 0]]),
        ("all_edges", topo.components.count, topo.components.face_component),
        ("manifold_edges", manifold_count, manifold_labels),
    ):
        area = np.bincount(labels, weights=areas, minlength=count)
        face_count = np.bincount(labels, minlength=count)
        lower, upper = np.full((count, 3), np.inf), np.full((count, 3), -np.inf)
        for corner in range(3):
            np.minimum.at(lower, labels, points[faces[:, corner]])
            np.maximum.at(upper, labels, points[faces[:, corner]])
        span = upper - lower
        diag2 = np.sum(span ** 2, axis=1)
        compactness = np.divide(area, diag2, out=np.full(count, np.inf), where=diag2 > 0)
        order = np.argsort(labels, kind="stable")
        offsets = np.concatenate(([0], np.cumsum(face_count)))
        components = []
        for index in range(count):
            row = {"id": index, "faces": int(face_count[index]), "area": float(area[index]),
                   "bbox_min": lower[index].tolist(), "bbox_max": upper[index].tolist(),
                   "bbox_diagonal_voxels": float(np.sqrt(diag2[index]) * resolution),
                   "area_over_component_diag2": float(compactness[index]),
                   "area_over_mesh_diag2": float(area[index] / whole_diag2),
                   "bbox_spans": span[index].tolist(),
                   "centroid_bbox": (0.5 * (lower[index] + upper[index])).tolist()}
            if area[index] < 1e-5 and face_count[index] < 2000:
                member_ids = order[offsets[index]:offsets[index + 1]]
                member_faces = faces[member_ids]
                members = np.unique(member_faces)
                coords = points[members]
                edge_pairs = np.sort(np.stack((member_faces, np.roll(member_faces, -1, axis=1)), axis=-1).reshape(-1, 2), axis=1)
                _, edge_counts = np.unique(edge_pairs, axis=0, return_counts=True)
                canonical = np.sort(member_faces, axis=1)
                duplicate_count = len(canonical) - len(np.unique(canonical, axis=0))
                centered = points[member_faces] - 0.5 * (lower[index] + upper[index])
                volume = abs(float(np.einsum("ij,ij->i", centered[:, 0], np.cross(centered[:, 1], centered[:, 2])).sum() / 6))
                covariance = np.cov(coords, rowvar=False, bias=True) if len(coords) > 1 else np.zeros((3, 3))
                spectrum = np.linalg.eigvalsh(covariance)
                denom = area[index] * np.sqrt(diag2[index])
                row["shape"] = {
                    "boundary_edges": int(np.count_nonzero(edge_counts == 1)),
                    "nonmanifold_edges": int(np.count_nonzero(edge_counts > 2)),
                    "duplicate_faces": int(duplicate_count),
                    "absolute_signed_volume": volume,
                    "volume_over_area_diagonal": float(volume / denom) if denom > 0 else 0,
                    "pca_spectrum": spectrum.tolist(),
                    "closed_edge_manifold": bool(np.all(edge_counts == 2)),
                }

            candidate = area[index] < 1e-5 and diag2[index] > (4 / resolution) ** 2
            if candidate:
                chosen = order[offsets[index]:offsets[index + 1]]
                if len(chosen) > 40:
                    chosen = chosen[np.linspace(0, len(chosen) - 1, 40).astype(int)]
                query = points[faces[chosen]].reshape(-1, 3)
                distances = reference.query(query, workers=1)[0]
                row["distance_to_islands_vertices_voxels"] = {
                    "median": float(np.median(distances) * resolution),
                    "p95": float(np.percentile(distances, 95) * resolution),
                    "max": float(distances.max() * resolution)}
            components.append(row)
        small_long = [row for row in components
                      if row["area"] < 1e-5 and row["bbox_diagonal_voxels"] > 4]
        small_long.sort(key=lambda row: row["area_over_component_diag2"])
        results[name] = {"components": count,
                         "native_area_cutoff_candidates": int(np.count_nonzero(area < 1e-5)),
                         "small_long_candidates": small_long,
                         "components_by_area": sorted(components, key=lambda row: row["area"], reverse=True)}
    return {"path": str(path), "mesh_diagonal_squared": whole_diag2, "connectivity": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("meshes", nargs="+", type=Path)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--resolution", type=int, default=1024)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    for path in (*args.meshes, args.reference, args.output):
        if not path.resolve().is_relative_to((REPO / "test").resolve()):
            parser.error("all paths must be under test/")
    ref_mesh = trimesh.load_scene(str(args.reference), process=False).to_mesh()
    reference = cKDTree(np.asarray(ref_mesh.vertices))
    records = []
    for path in args.meshes:
        record = compare(path, reference, args.resolution)
        records.append(record)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"reference": str(args.reference), "meshes": records}, indent=2) + "\n", encoding="utf-8")
        for kind, values in record["connectivity"].items():
            print(json.dumps({"path": str(path), "connectivity": kind,
                              "components": values["components"],
                              "area_lt_1e-5": values["native_area_cutoff_candidates"],
                              "small_long": len(values["small_long_candidates"]),
                              "top_candidates": [{"id": row["id"], "area": row["area"], "diagonal_voxels": row["bbox_diagonal_voxels"], "compactness": row["area_over_component_diag2"], "shape": row.get("shape")} for row in values["small_long_candidates"][:3]]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
