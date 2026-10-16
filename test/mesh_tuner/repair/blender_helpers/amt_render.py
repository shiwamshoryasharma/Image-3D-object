"""Test-side render helpers (run INSIDE Blender): Workbench views, face-category colours, slice plots, depth maps.

Used by the repair agent's debug probes and the real-job validation harness. Never imported by the backend.
Orientation: +Z up, front = -Y, character's left = +X.
"""
from __future__ import annotations

import os

import bmesh
import bpy
import numpy as np
from mathutils import Vector

VIEWS = {
    "front": (0.0, -1.0, 0.0), "back": (0.0, 1.0, 0.0), "left": (1.0, 0.0, 0.0), "right": (-1.0, 0.0, 0.0),
    "top": (0.0, 0.0, 1.0), "bottom": (0.0, 0.0, -1.0), "three_quarter": (0.6857, -0.6857, 0.2443),
    "back_three_quarter": (-0.6857, 0.6857, 0.2443), "below_front": (0.0, -0.6, -0.8),
}


def mesh_arrays(me):
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    me.calc_loop_triangles()
    tv = np.empty(len(me.loop_triangles) * 3, np.int32)
    me.loop_triangles.foreach_get("vertices", tv)
    return co.reshape(-1, 3).astype(np.float64), tv.reshape(-1, 3).astype(np.int64)


def subset_object(src, face_mask, name):
    """New object with only the faces in ``face_mask`` (polygon mask) of ``src`` (bmesh delete; UVs kept)."""
    me = src.data.copy()
    me.name = name
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    kill = [bm.faces[i] for i in np.flatnonzero(~np.asarray(face_mask, bool)).tolist()]
    bmesh.ops.delete(bm, geom=kill, context="FACES")
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    for slot_src, slot in zip(src.material_slots, obj.material_slots):
        slot.material = slot_src.material
    return obj


def set_face_colors(obj, face_rgb, name="amt_dbg_color"):
    """Corner-domain byte colour attribute from per-polygon RGB (0..1) and make it the active colour."""
    me = obj.data
    lt = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get("loop_total", lt)
    col = np.ones((len(me.loops), 4), np.float32)
    col[:, :3] = np.repeat(np.asarray(face_rgb, np.float32), lt, axis=0)
    attr = me.color_attributes.get(name) or me.color_attributes.new(name, "BYTE_COLOR", "CORNER")
    attr.data.foreach_set("color", col.reshape(-1))
    me.color_attributes.active_color = attr
    try:
        me.attributes.active_color = attr
    except (AttributeError, TypeError):
        pass


def setup_scene(res=1024):
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.render.resolution_x = sc.render.resolution_y = res
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = False
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGB"
    sc.view_settings.view_transform = "Standard"
    sc.display.render_aa = "8"
    if sc.world is None:
        sc.world = bpy.data.worlds.new("W")
    sc.world.color = (0.18, 0.18, 0.18)
    cam = bpy.data.cameras.get("amt_cam") or bpy.data.cameras.new("amt_cam")
    cam.type = "ORTHO"
    co = bpy.data.objects.get("amt_cam") or bpy.data.objects.new("amt_cam", cam)
    if co.name not in sc.collection.objects:
        sc.collection.objects.link(co)
    sc.camera = co
    return sc, co


def shading(mode, *, backface=False):
    """mode: matcap | flat | texture | color (face colour attribute) | studio."""
    sh = bpy.context.scene.display.shading
    if mode in ("matcap", "flat_matcap"):
        sh.light = "MATCAP"
        names = [s.name for s in bpy.context.preferences.studio_lights if s.type == "MATCAP"]
        sh.studio_light = "clay_studio.exr" if "clay_studio.exr" in names else names[0]
        sh.color_type = "SINGLE"
        sh.single_color = (0.8, 0.8, 0.8)
        sh.show_cavity = True
        sh.cavity_type = "BOTH"
    elif mode == "texture":
        sh.light = "STUDIO"
        sh.color_type = "TEXTURE"
        sh.show_cavity = False
    elif mode == "color":
        sh.light = "STUDIO"
        sh.color_type = "VERTEX"
        sh.show_cavity = False
    else:
        sh.light = "STUDIO"
        sh.color_type = "SINGLE"
        sh.single_color = (0.8, 0.8, 0.8)
        sh.show_cavity = True
        sh.cavity_type = "BOTH"
    sh.show_backface_culling = backface
    sh.show_xray = False
    sh.show_shadows = False


def render_view(path, objs, direction, target=None, scale=None, pad=1.06):
    """Ortho render of ``objs`` (others hidden) from ``direction`` (model -> camera). Auto-frames when scale is None."""
    sc, cam = setup_scene(bpy.context.scene.render.resolution_x)
    d = Vector(direction).normalized()
    names = {o.name for o in objs}
    for o in sc.objects:
        if o.type == "MESH":
            o.hide_render = o.name not in names
    if scale is None or target is None:
        pts = np.concatenate([mesh_arrays(o.data)[0] for o in objs])
        dd = np.array(d)
        up = np.array([0.0, 0.0, 1.0]) if abs(dd[2]) < 0.99 else np.array([0.0, 1.0, 0.0])
        u = up - dd * (up @ dd)
        u /= np.linalg.norm(u)
        r = np.cross(dd, u)
        pu, pr = pts @ u, pts @ r
        target_np = u * 0.5 * (pu.max() + pu.min()) + r * 0.5 * (pr.max() + pr.min()) + dd * float((pts @ dd).mean())
        scale = max(pu.max() - pu.min(), pr.max() - pr.min()) * pad
        target = Vector(target_np.tolist())
    else:
        target = Vector(target)
    cam.data.ortho_scale = scale
    cam.data.clip_start = 0.001
    cam.data.clip_end = 100.0
    cam.location = target + d * 5.0
    cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    return path


def shade_flat(obj):
    me = obj.data
    if getattr(me, "has_custom_normals", False):
        a = me.attributes.get("custom_normal")
        if a is not None:
            me.attributes.remove(a)
    me.shade_flat()


def slice_segments(co, tv, axis, value):
    """Intersect triangles with the plane co[:, axis] == value -> (N, 2, 3) segments."""
    d = co[:, axis] - value
    dt = d[tv]
    cross = (dt.min(1) < 0) & (dt.max(1) > 0)
    T, D = tv[cross], dt[cross]
    P = co[T]
    out = np.zeros((len(T), 2, 3))
    cnt = np.zeros(len(T), int)
    for a, b in ((0, 1), (1, 2), (2, 0)):
        da, db = D[:, a], D[:, b]
        m = (da * db) < 0
        t = np.where(m, da / np.where(m, da - db, 1), 0)
        p = P[:, a] + (P[:, b] - P[:, a]) * t[:, None]
        for slot in (0, 1):
            sel = m & (cnt == slot)
            out[sel, slot] = p[sel]
            cnt[sel] += 1
            m = m & ~sel
    return out[cnt == 2]


def draw_slices(layers, ua, va, win, size, path):
    """Draw segment layers [(segments, rgb)] in window (umin, umax, vmin, vmax) -> PNG (square, no distortion)."""
    umin, umax, vmin, vmax = win
    half = max(umax - umin, vmax - vmin) / 2
    cu, cv = (umin + umax) / 2, (vmin + vmax) / 2
    umin, umax, vmin, vmax = cu - half, cu + half, cv - half, cv + half
    img = np.ones((size, size, 4), np.float32)
    img[..., :3] = 0.97
    for seg, rgb in layers:
        if not len(seg):
            continue
        L = np.linalg.norm(seg[:, 1] - seg[:, 0], axis=1).max()
        steps = int(min(64, max(4, L / (2 * half) * size * 2)))
        t = np.linspace(0, 1, steps)
        p = seg[:, 0, None, :] + (seg[:, 1, None, :] - seg[:, 0, None, :]) * t[None, :, None]
        pu, pv = p[..., ua].ravel(), p[..., va].ravel()
        x = ((pu - umin) / (umax - umin) * size).astype(np.int64)
        y = ((vmax - pv) / (vmax - vmin) * size).astype(np.int64)
        ok = (x >= 0) & (x < size) & (y >= 0) & (y < size)
        img[y[ok], x[ok], :3] = rgb
    im = bpy.data.images.new(os.path.basename(path), size, size, alpha=True)
    im.pixels.foreach_set(img[::-1].ravel())
    im.filepath_raw = path
    im.file_format = "PNG"
    im.save()
    bpy.data.images.remove(im)
    return path


def head_center(co):
    """Upright-humanoid head guess: top 12 % slab, median x/y."""
    zmax, zmin = co[:, 2].max(), co[:, 2].min()
    h = zmax - zmin
    slab = co[co[:, 2] > zmax - 0.12 * h]
    return np.array([np.median(slab[:, 0]), np.median(slab[:, 1]), zmax - 0.08 * h]), 0.16 * h
