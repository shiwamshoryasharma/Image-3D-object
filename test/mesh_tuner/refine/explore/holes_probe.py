"""Exploration (test only): boundary loops near a point of a GLB and what bmesh hole filling does with them.

blender --background --factory-startup --python holes_probe.py -- <glb> x,y,z radius
"""
import sys

import bmesh
import bpy
import numpy as np

sys.path.insert(0, r"E:\14-OCT-2026\Img3D\backend\blender_scripts")
from img3d_amt import meshdata, smoothing  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=argv[0], merge_vertices=True)
obj = max((o for o in bpy.data.objects if o.type == "MESH"), key=lambda o: len(o.data.polygons))
me = obj.data
p = np.array([float(v) for v in argv[1].split(",")])
rad = float(argv[2])
co = meshdata.get_positions(me)
edges = meshdata.get_edges(me)
ef = meshdata.edge_faces(me)
loops = meshdata.boundary_loops(edges, ef.boundary, co)
near = np.linalg.norm(co - p, axis=1) < rad
sel = []
for k in range(loops.count):
    ids = loops.loop_edge_ids(k)
    vs = np.unique(edges[ids].ravel())
    if near[vs].all():
        cyc = meshdata.order_loop_vertices(edges[ids])
        sel.append((k, len(ids), cyc is not None, ids))
print("@@HOLES loops near:", len(sel), [(n, simple) for _, n, simple, _ in sel])
bm = bmesh.new()
bm.from_mesh(me)
bm.edges.ensure_lookup_table()
for k, n, simple, ids in sel[:12]:
    es = [bm.edges[i] for i in ids.tolist()]
    nonman_verts = sum(1 for e in es for v in e.verts if sum(1 for x in v.link_edges if x.is_boundary) > 2)
    r = bmesh.ops.holes_fill(bm, edges=es, sides=0)
    print("@@HOLES loop", k, "edges", n, "simple", simple, "bowtie_verts", nonman_verts, "holes_fill faces",
          len(r["faces"]))

# second pass: edgeloop_fill on a fresh bmesh, check orientation against the neighbouring faces
bm2 = bmesh.new()
bm2.from_mesh(me)
bm2.edges.ensure_lookup_table()
for k, n, simple, ids in sel[:12]:
    es = [bm2.edges[i] for i in ids.tolist()]
    try:
        r = bmesh.ops.edgeloop_fill(bm2, edges=es)
    except Exception as exc:  # report and continue
        print("@@HOLES edgeloop_fill error", k, exc)
        continue
    faces = r["faces"]
    agree = []
    for f in faces:
        f.normal_update()
        for e in f.edges:
            for g in e.link_faces:
                if g is not f:
                    # consistent winding: the shared edge is traversed in opposite directions
                    lf = [l for l in f.loops if l.edge == e][0]
                    lg = [l for l in g.loops if l.edge == e][0]
                    agree.append(lf.vert != lg.vert)
    print("@@HOLES edgeloop_fill loop", k, "faces", len(faces), "consistent", sum(agree), "/", len(agree))
