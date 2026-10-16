"""Blender helper (repair tests): topology/orientation statistics of GLB files (welded by position).

  blender --background --factory-startup --python-exit-code 86 --python inspect_repaired.py -- <glb> [<glb> ...]
Prints one ``@@INSPECT@@{json}`` line per file: faces, verts, boundary_edges, boundary_loops (sizes),
non_manifold_edges, inconsistent_edges, components [{faces, center, radius_mean, signed_volume, area}],
radius histogram helpers for sphere cases.
"""

import json
import sys

import bmesh
import bpy
import numpy as np

paths = sys.argv[sys.argv.index("--") + 1:]


def components(n, a, b):
    parent = np.arange(n)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for u, v in zip(a.tolist(), b.tolist()):
        ru, rv = find(u), find(v)
        if ru != rv:
            parent[max(ru, rv)] = min(ru, rv)
    return np.array([find(i) for i in range(n)])


for path in paths:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=path, merge_vertices=False)
    objs = [o for o in bpy.data.objects if o.type == "MESH"]
    bm = bmesh.new()
    for o in objs:
        tmp = o.data.copy()
        tmp.transform(o.matrix_world)
        bm.from_mesh(tmp)
    co_all = np.array([v.co[:] for v in bm.verts])
    diag = float(np.linalg.norm(co_all.max(0) - co_all.min(0))) if len(co_all) else 1.0
    bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-6 * diag)
    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    me = bpy.data.meshes.new("insp")
    bm.to_mesh(me)
    nv, nf = len(me.vertices), len(me.polygons)
    co = np.empty(nv * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3).astype(np.float64)
    me.calc_loop_triangles()
    tv = np.empty(len(me.loop_triangles) * 3, np.int32)
    me.loop_triangles.foreach_get("vertices", tv)
    tv = tv.reshape(-1, 3)
    tp = np.empty(len(me.loop_triangles), np.int32)
    me.loop_triangles.foreach_get("polygon_index", tp)
    edge_faces = np.array([len(e.link_faces) for e in bm.edges])
    boundary = [e for e in bm.edges if len(e.link_faces) == 1]
    # boundary loops (undirected connectivity)
    bverts = {}
    for e in boundary:
        for v in e.verts:
            bverts.setdefault(v.index, len(bverts))
    loops = []
    if boundary:
        a = np.array([bverts[e.verts[0].index] for e in boundary])
        b = np.array([bverts[e.verts[1].index] for e in boundary])
        lab = components(len(bverts), a, b)
        _, counts = np.unique(lab[a], return_counts=True)
        loops = sorted(counts.tolist(), reverse=True)
    inconsistent = 0
    for e in bm.edges:
        if len(e.link_faces) == 2:
            l1, l2 = e.link_loops[0], e.link_loops[1]
            if l1.vert == l2.vert:
                inconsistent += 1
    # face components via shared vertices
    p0 = co[tv[:, 0]]
    cr = np.cross(co[tv[:, 1]] - p0, co[tv[:, 2]] - p0)
    area = 0.5 * np.linalg.norm(cr, axis=1)
    vol = (p0 * np.cross(co[tv[:, 1]], co[tv[:, 2]])).sum(1) / 6.0
    flab_v = components(nv, np.concatenate([tv[:, 0], tv[:, 1]]), np.concatenate([tv[:, 1], tv[:, 2]]))
    tlab = flab_v[tv[:, 0]]
    comps = []
    for c in np.unique(tlab):
        m = tlab == c
        cen = (co[tv[m]].mean(axis=(0, 1)))
        rad = np.linalg.norm(co[tv[m]].mean(1) - cen, axis=1)
        comps.append({"faces": int(len(np.unique(tp[m]))), "center": [round(float(x), 4) for x in cen],
                      "radius_mean": round(float(rad.mean()), 4), "radius_min": round(float(rad.min()), 4),
                      "signed_volume": round(float(vol[m].sum()), 6), "area": round(float(area[m].sum()), 6)})
    comps.sort(key=lambda c: -c["faces"])
    out = {"path": path, "faces": nf, "verts": nv, "boundary_edges": len(boundary), "boundary_loops": loops[:20],
           "holes_ge5": sum(1 for n in loops if n >= 5), "non_manifold_edges": int((edge_faces > 2).sum()),
           "inconsistent_edges": inconsistent, "components": comps[:12], "n_components": len(comps),
           "triangle_centroids_radius": None}
    print("@@INSPECT@@" + json.dumps(out))
    bm.free()
