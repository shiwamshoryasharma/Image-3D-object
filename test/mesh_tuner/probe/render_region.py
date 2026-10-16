"""Probe: Workbench matcap close-up renders of arbitrary regions of a GLB.

blender --background --factory-startup --python-exit-code 1 --python render_region.py -- --input <glb> --out <dir>
    --view name:cx,cy,cz:scale:dx,dy,dz [--view ...] [--flat] [--wire]
dx,dy,dz = direction from target towards the camera (e.g. 0,-1,0 = Blender front view).
"""
import argparse
import os
import sys

import bpy
from mathutils import Vector


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--view", action="append", required=True)
    p.add_argument("--res", type=int, default=1024)
    p.add_argument("--flat", action="store_true", help="also render with flat (face) shading to expose facets")
    a = p.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=a.input)
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.render.resolution_x = sc.render.resolution_y = a.res
    sc.view_settings.view_transform = "Standard"
    sc.world = bpy.data.worlds.new("W"); sc.world.color = (0.18, 0.18, 0.18)
    sh = sc.display.shading
    sh.light = "MATCAP"
    sh.studio_light = "clay_studio.exr"
    sh.color_type = "SINGLE"; sh.single_color = (0.8, 0.8, 0.8)
    sh.show_cavity = True; sh.cavity_type = "BOTH"
    cam = bpy.data.cameras.new("c"); cam.type = "ORTHO"
    co = bpy.data.objects.new("c", cam); sc.collection.objects.link(co); sc.camera = co
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    for spec in a.view:
        name, c, s, d = spec.split(":")
        c = Vector([float(x) for x in c.split(",")]); d = Vector([float(x) for x in d.split(",")]).normalized()
        cam.ortho_scale = float(s); cam.clip_start = 0.001; cam.clip_end = 20
        co.location = c + d * 3.0
        co.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
        variants = [("", False)] + ([("_flat", True)] if a.flat else [])
        for suf, flat in variants:
            for o in meshes:
                if flat:
                    if o.data.has_custom_normals:  # imported GLB NORMALs override shading -> clear first
                        bpy.context.view_layer.objects.active = o
                        bpy.ops.mesh.customdata_custom_splitnormals_clear()
                    o.data.shade_flat()  # Mesh.shade_flat() (4.1+)
            sc.render.filepath = os.path.join(a.out, f"{name}{suf}.png")
            bpy.ops.render.render(write_still=True)
            print("@@saved", sc.render.filepath)


main()
