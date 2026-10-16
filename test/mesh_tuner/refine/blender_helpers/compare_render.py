"""Blender helper (test only): render the ORIGINAL job GLB and a TUNED GLB with identical cameras.

blender --background --factory-startup --python-exit-code 86 --python compare_render.py -- \
    --original <glb> --tuned <glb> --out <dir> --views '<json {name: [[tx,ty,tz], scale, [dx,dy,dz], mode]}>' [--res 800]
mode: matcap | texture | flat_matcap. Writes <name>_before.png / <name>_after.png.
"""

import argparse
import json
import os
import sys

argv = sys.argv[sys.argv.index("--") + 1:]
ap = argparse.ArgumentParser()
ap.add_argument("--original", required=True)
ap.add_argument("--tuned", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--views", required=True)
ap.add_argument("--res", type=int, default=800)
a = ap.parse_args(argv)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy  # noqa: E402

import render_util as ru  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)


def load(path, name):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    objs = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    for o in objs:
        o.name = name
    return objs


orig = load(a.original, "ORIG")
tuned = load(a.tuned, "TUNED")
for o in orig + tuned:
    # identical shading conditions: no back-face culling surprises, smooth shading as exported
    for slot in o.material_slots:
        if slot.material is not None:
            slot.material.use_backface_culling = False
views = json.loads(a.views)
ru.setup_scene(a.res)
os.makedirs(a.out, exist_ok=True)
for name, spec in views.items():
    tgt, scale, d = spec[0], spec[1], spec[2]
    mode = spec[3] if len(spec) > 3 else "matcap"
    ru.render(os.path.join(a.out, f"{name}_before.png"), tgt, scale, d, orig, mode=mode)
    ru.render(os.path.join(a.out, f"{name}_after.png"), tgt, scale, d, tuned, mode=mode)
print("@@COMPARE@@done")
