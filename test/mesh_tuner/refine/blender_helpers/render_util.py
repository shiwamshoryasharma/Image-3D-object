"""Test-only Workbench render helpers for the refine (face/topology) validation scripts (run inside Blender)."""

import os

import bpy
import numpy as np
from mathutils import Vector


def setup_scene(res=896):
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.render.resolution_x = sc.render.resolution_y = res
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGB"
    sc.view_settings.view_transform = "Standard"
    sc.display.render_aa = "8"
    if sc.world is None:
        sc.world = bpy.data.worlds.new("W")
    sc.world.color = (0.18, 0.18, 0.18)
    cam = bpy.data.cameras.get("RU_cam") or bpy.data.cameras.new("RU_cam")
    cam.type = "ORTHO"
    obj = bpy.data.objects.get("RU_cam")
    if obj is None:
        obj = bpy.data.objects.new("RU_cam", cam)
        sc.collection.objects.link(obj)
    sc.camera = obj
    return obj


def shading(mode):
    sh = bpy.context.scene.display.shading
    sh.show_backface_culling = False
    sh.show_xray = False
    sh.show_shadows = False
    if mode == "matcap":
        sh.light = "MATCAP"
        names = [s.name for s in bpy.context.preferences.studio_lights if s.type == "MATCAP"]
        sh.studio_light = "clay_studio.exr" if "clay_studio.exr" in names else names[0]
        sh.color_type = "SINGLE"
        sh.single_color = (0.8, 0.8, 0.8)
        sh.show_cavity = True
        sh.cavity_type = "BOTH"
        sh.show_specular_highlight = True
    elif mode == "texture":
        sh.light = "STUDIO"
        sh.color_type = "TEXTURE"
        sh.show_cavity = False
    elif mode == "attribute":
        sh.light = "FLAT"
        sh.color_type = "VERTEX"
        sh.show_cavity = False
    elif mode == "flat_matcap":
        shading("matcap")
        sh.show_cavity = False


def render(path, target, scale, direction, objs_visible, mode="matcap"):
    sc = bpy.context.scene
    cam_obj = sc.camera
    cam_obj.data.ortho_scale = scale
    cam_obj.data.clip_start = 0.001
    cam_obj.data.clip_end = 20.0
    d = Vector(direction).normalized()
    cam_obj.location = Vector(target) + d * 3.0
    cam_obj.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
    vis = {o.name for o in objs_visible}
    for o in sc.objects:
        if o.type == "MESH":
            o.hide_render = o.name not in vis
    shading(mode)
    sc.render.filepath = path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.ops.render.render(write_still=True)
    return path


def set_vertex_colors(mesh, rgb, name="ru_mask"):
    """Per-vertex RGB (V,3) colour attribute shown by shading('attribute')."""
    attr = mesh.color_attributes.get(name)
    if attr is None:
        attr = mesh.color_attributes.new(name=name, type="FLOAT_COLOR", domain="POINT")
    col = np.ones((len(mesh.vertices), 4), np.float32)
    col[:, :3] = rgb
    attr.data.foreach_set("color", col.ravel())
    mesh.color_attributes.active_color = attr
    try:
        mesh.attributes.active_color = attr
        mesh.color_attributes.render_color_index = mesh.color_attributes.find(name)
    except Exception:
        pass
