"""Extract tiny opposite duplicate membranes for CPU-only test comparisons."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
import numpy as np
import trimesh
from scipy import sparse
from scipy.sparse import csgraph
from parts.components import build_topology


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mesh", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--max-area", type=float, default=1e-5)
    args = parser.parse_args()
    test = (REPO / "test").resolve()
    if not args.mesh.resolve().is_relative_to(test) or not args.output_dir.resolve().is_relative_to(test):
        parser.error("all paths must stay under test/")
    mesh = trimesh.load_scene(str(args.mesh), process=False).to_mesh()
    topology = build_topology(np.asarray(mesh.vertices, dtype=np.float32),
                              np.asarray(mesh.faces, dtype=np.uint32))
    table = topology.edges
    edge_ids = np.flatnonzero(table.face_counts == 2)
    offsets = table.face_offsets[edge_ids]
    rows, columns = table.edge_faces[offsets], table.edge_faces[offsets + 1]
    graph = sparse.coo_array((np.ones(len(rows), dtype=bool), (rows, columns)),
                             shape=(len(mesh.faces), len(mesh.faces))).tocsr()
    count, labels = csgraph.connected_components(graph, directed=False)
    sizes = np.bincount(labels, minlength=count)
    areas = np.bincount(labels, weights=topology.geometry.areas, minlength=count)
    order = np.argsort(labels, kind="stable")
    offsets = np.concatenate(([0], np.cumsum(sizes)))
    remove = np.zeros(len(mesh.faces), dtype=bool)
    records = []
    for component in np.flatnonzero((sizes == 2) & (areas < args.max_area)):
        members = order[offsets[component]:offsets[component + 1]]
        welded = topology.welded[members]
        if not np.array_equal(np.sort(welded[0]), np.sort(welded[1])):
            continue
        normal_dot = float(np.dot(topology.geometry.normals[members[0]],
                                  topology.geometry.normals[members[1]]))
        if normal_dot > -0.99999:
            continue
        coords = topology.welded_positions[np.unique(welded)]
        diagonal = float(np.linalg.norm(coords.max(0) - coords.min(0)))
        remove[members] = True
        records.append({"component": int(component), "faces": members.astype(int).tolist(),
                        "area": float(areas[component]), "normal_dot": normal_dot,
                        "bbox_diagonal_voxels_1024": diagonal * 1024,
                        "centroid": coords.mean(0).astype(float).tolist()})
    args.output_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.output_dir / "selected_faces.npz", remove=remove)
    for keep, name in ((remove, "collapsed_membranes.glb"), (~remove, "without_collapsed_membranes.glb")):
        if not keep.any():
            continue
        copy = mesh.copy()
        copy.update_faces(keep)
        copy.remove_unreferenced_vertices()
        copy.export(str(args.output_dir / name), file_type="glb")
    report = {"input": str(args.mesh), "criterion": "manifold-edge component: exactly2 coincident opposite triangles; summed area<max_area",
              "max_area": args.max_area, "components": len(records),
              "faces": int(np.count_nonzero(remove)),
              "components_longer_4_voxels_1024": sum(row["bbox_diagonal_voxels_1024"] > 4 for row in records),
              "removed_surface_area_fraction": float(topology.geometry.areas[remove].sum() / topology.geometry.areas.sum()),
              "records": records}
    (args.output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key:value for key,value in report.items() if key != "records"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
