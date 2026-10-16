"""Exploration (test only): connected-component size + boundary info around given points of a GLB.

blender --background --factory-startup --python component_probe.py -- <glb> x,y,z [x,y,z ...]
"""
import sys

import bpy
import numpy as np

sys.path.insert(0, r"E:\14-OCT-2026\Img3D\backend\blender_scripts")
from img3d_amt import meshdata, smoothing  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=argv[0], merge_vertices=True)
obj = max((o for o in bpy.data.objects if o.type == "MESH"), key=lambda o: len(o.data.polygons))
me = obj.data
co = meshdata.get_positions(me)
tris = meshdata.get_triangles(me)
topo = smoothing.build_topology(tris, len(co))
labels, count = smoothing.mask_components(np.ones(len(co), bool), topo)
sizes = np.bincount(labels)
bnd = smoothing.boundary_vertices(topo)
for spec in argv[1:]:
    p = np.array([float(v) for v in spec.split(",")])
    d = np.linalg.norm(co - p, axis=1)
    near = np.nonzero(d < 0.006)[0]
    labs, cnt = np.unique(labels[near], return_counts=True)
    print("@@PROBE", spec, "verts within 6mm:", len(near), "components:",
          [(int(sizes[l]), int(c)) for l, c in zip(labs, cnt)], "boundary:", int(bnd[near].sum()),
          "locked:", int(topo.locked[near].sum()))
