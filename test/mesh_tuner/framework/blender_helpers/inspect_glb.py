"""Blender helper (test only): import a model and print what Blender sees.

Run:
  blender --background --factory-startup --python-exit-code 86 --python inspect_glb.py -- <model> [<model> ...]
Prints one line per model: "@@INSPECT@@{json}" with mesh/uv/material/image/armature counts.
"""

import json
import os
import sys

import bpy


def inspect(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path, disable_bone_shape=True)
    else:
        raise SystemExit(f"unsupported {path}")
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    images = [im for im in bpy.data.images if im.type == "IMAGE"]
    return {
        "path": path,
        "objects": len(bpy.data.objects),
        "mesh_objects": len(meshes),
        "faces": sum(len(o.data.polygons) for o in meshes),
        "vertices": sum(len(o.data.vertices) for o in meshes),
        "uv_layers": [len(o.data.uv_layers) for o in meshes],
        "materials": len([m for m in bpy.data.materials if m.users]),
        "material_backface_culling": sorted({m.use_backface_culling for m in bpy.data.materials if m.users}),
        "images": len(images),
        "image_sizes": sorted(list(im.size) for im in images),
        "image_formats": sorted(im.file_format for im in images),
        "armatures": len([o for o in bpy.data.objects if o.type == "ARMATURE"]),
        "bones": sum(len(o.data.bones) for o in bpy.data.objects if o.type == "ARMATURE"),
        "skinned_meshes": len([o for o in meshes if any(m.type == "ARMATURE" for m in o.modifiers)]),
        "vertex_groups": [len(o.vertex_groups) for o in meshes],
        "attributes": sorted({a.name for o in meshes for a in o.data.attributes if not a.name.startswith(".")}),
    }


for p in sys.argv[sys.argv.index("--") + 1:]:
    print("@@INSPECT@@" + json.dumps(inspect(p)), flush=True)
