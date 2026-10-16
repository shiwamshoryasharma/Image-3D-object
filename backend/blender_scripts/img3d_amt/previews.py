"""Before/after Workbench preview renders (matcap + cavity; texture colour for the face close-up).

Files (``<previews_dir>/<variant>_<view>.png``, 768 px square, names match ``^[a-z0-9_]+\\.png$``):
``before_front``, ``before_three_quarter``, ``before_side``, ``before_back``, ``before_face``, ``before_face_texture``
and the same with ``after_``. "before" renders the hidden original snapshot, "after" the tuned result; both use the
same cameras (framed on the union of both meshes). Face views are rendered only for upright humanoid-shaped models
(bbox heuristic, see :func:`head_frame`).

Orientation (TRELLIS/glTF after import): +Z up, front = -Y, character's left = +X. Camera directions below point
FROM the model TO the camera. The scene state (render settings, shading, camera, world, visibility) is restored
afterwards and every helper datablock is removed.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import bpy
import numpy as np
from mathutils import Vector

from . import meshdata
from .context import TunerContext

log = logging.getLogger(__name__)

RESOLUTION = 768
FRAME_PAD = 1.08
FACE_TOP_FRACTION = 0.18
VIEW_DIRECTIONS: dict[str, tuple[float, float, float]] = {
    "front": (0.0, -1.0, 0.0),
    "three_quarter": (0.6857, -0.6857, 0.2443),  # front-left (+X = character's left), slightly above
    "side": (1.0, 0.0, 0.0),  # character's left side
    "back": (0.0, 1.0, 0.0),
}
MATCAP_PREFERENCE = ("clay_studio.exr", "basic_grey.exr", "basic_1.exr", "clay_brown.exr")
STUDIO_PREFERENCE = ("Default", "studio.sl", "basic.sl")
_PREFIX = "AMT_preview"


# ================================================================================================================
# framing
# ================================================================================================================


def _camera_basis(direction: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    d = direction / np.linalg.norm(direction)
    up = np.array([0.0, 0.0, 1.0])
    if abs(d @ up) > 0.99:
        up = np.array([0.0, 1.0, 0.0])
    u = up - d * (up @ d)
    u /= np.linalg.norm(u)
    r = np.cross(d, u)
    return d, u, r


def frame_points(points: np.ndarray, direction: np.ndarray, pad: float = FRAME_PAD) -> tuple[np.ndarray, float]:
    """Ortho framing of ``points`` (N, 3) seen from ``direction``: ``(target_centre (3,), ortho_scale)``."""
    d, u, r = _camera_basis(np.asarray(direction, dtype=np.float64))
    pu, pr, pd = points @ u, points @ r, points @ d
    cu, cr = 0.5 * (pu.max() + pu.min()), 0.5 * (pr.max() + pr.min())
    extent = max(pu.max() - pu.min(), pr.max() - pr.min()) * pad
    centre = u * cu + r * cr + d * float(pd.mean())
    return centre, float(max(extent, 1e-6))


def is_upright_humanoid(bmin: np.ndarray, bmax: np.ndarray) -> bool:
    """Bbox heuristic: tallest along Z, shallow in Y (depth <= 0.6 x height), not wider than ~1.15 x height.

    TRELLIS characters (T/A-pose) pass (depth/height 0.32-0.41); the fox (0.73) and castle (0.71) do not.
    """
    dims = np.asarray(bmax) - np.asarray(bmin)
    height = float(dims[2])
    if height <= 0:
        return False
    return dims[1] <= 0.6 * height and dims[0] <= 1.15 * height and height >= dims[1]


def head_frame(co: np.ndarray, bmin: np.ndarray, bmax: np.ndarray) -> tuple[np.ndarray, float] | None:
    """Head close-up for an upright humanoid: ``(target (3,), ortho_scale)`` or ``None`` when no head is found.

    The head is the top ``FACE_TOP_FRACTION`` (18 %) of the height, centred laterally on the median X of that slab;
    the slab must be clearly narrower than the body (otherwise it is not a head).
    """
    if not is_upright_humanoid(bmin, bmax):
        return None
    height = float(bmax[2] - bmin[2])
    top = float(bmax[2])
    slab = co[co[:, 2] > top - FACE_TOP_FRACTION * height]
    if len(slab) < 50:
        return None
    cx = float(np.median(slab[:, 0]))
    near = slab[np.abs(slab[:, 0] - cx) < 0.15 * height]
    if len(near) < 50:
        return None
    width = float(np.percentile(near[:, 0], 98) - np.percentile(near[:, 0], 2))
    body_width = float(bmax[0] - bmin[0])
    if width > 0.75 * body_width and body_width > 0.4 * height:
        return None
    target = np.array([cx, float(np.median(near[:, 1])), top - 0.5 * FACE_TOP_FRACTION * height])
    scale = max(0.2 * height, 1.25 * width)
    return target, scale


# ================================================================================================================
# scene state
# ================================================================================================================

_SCENE_PROPS = {
    "render": ("engine", "resolution_x", "resolution_y", "resolution_percentage", "filepath", "film_transparent",
               "use_file_extension"),
    "render.image_settings": ("file_format", "color_mode", "color_depth", "compression"),
    "view_settings": ("view_transform", "look", "exposure", "gamma"),
    "display": ("render_aa",),
    "display.shading": ("light", "studio_light", "color_type", "single_color", "show_cavity", "cavity_type",
                        "cavity_ridge_factor", "cavity_valley_factor", "curvature_ridge_factor",
                        "curvature_valley_factor", "show_backface_culling", "show_xray", "show_shadows",
                        "show_specular_highlight", "show_object_outline"),
}


def _resolve(root: Any, path: str) -> Any:
    for part in path.split("."):
        root = getattr(root, part)
    return root


class _SceneState:
    """Snapshot of the render/shading settings, camera, world and object visibility; ``restore()`` puts it back."""

    def __init__(self, scene: bpy.types.Scene) -> None:
        self.scene = scene
        self.values: list[tuple[str, str, Any]] = []
        for path, names in _SCENE_PROPS.items():
            owner = _resolve(scene, path)
            for name in names:
                try:
                    value = getattr(owner, name)
                    if hasattr(value, "__len__") and not isinstance(value, str):
                        value = tuple(value)
                    self.values.append((path, name, value))
                except AttributeError:
                    continue
        self.camera = scene.camera
        self.world = scene.world
        self.visibility = {o.name: (o.hide_render, o.hide_viewport) for o in scene.objects}

    def restore(self) -> None:
        scene = self.scene
        # light first: studio_light must be valid for the light type
        for path, name, value in sorted(self.values, key=lambda v: v[1] != "light"):
            try:
                setattr(_resolve(scene, path), name, value)
            except (AttributeError, TypeError, ValueError):
                log.debug("could not restore %s.%s", path, name)
        scene.camera = self.camera
        scene.world = self.world
        for obj in scene.objects:
            if obj.name in self.visibility:
                obj.hide_render, obj.hide_viewport = self.visibility[obj.name]


def _pick_studio_light(kind: str, preference: tuple[str, ...]) -> str | None:
    names = [sl.name for sl in bpy.context.preferences.studio_lights if sl.type == kind]
    for name in preference:
        if name in names:
            return name
    return names[0] if names else None


def _setup_shading(scene: bpy.types.Scene, mode: str) -> None:
    shading = scene.display.shading
    if mode == "matcap":
        shading.light = "MATCAP"
        matcap = _pick_studio_light("MATCAP", MATCAP_PREFERENCE)
        if matcap:
            shading.studio_light = matcap
        shading.color_type = "SINGLE"
        shading.single_color = (0.8, 0.8, 0.8)
        shading.show_cavity = True
        shading.cavity_type = "BOTH"
        shading.cavity_ridge_factor = 1.0
        shading.cavity_valley_factor = 1.0
        shading.curvature_ridge_factor = 1.0
        shading.curvature_valley_factor = 1.0
    else:  # texture
        shading.light = "STUDIO"
        studio = _pick_studio_light("STUDIO", STUDIO_PREFERENCE)
        if studio:
            shading.studio_light = studio
        shading.color_type = "TEXTURE"
        shading.show_cavity = False
    shading.show_backface_culling = False
    shading.show_xray = False
    shading.show_shadows = False
    shading.show_specular_highlight = True
    try:
        shading.show_object_outline = False
    except AttributeError:
        pass


# ================================================================================================================
# rendering
# ================================================================================================================


def _sample_points(objs: list[bpy.types.Object], limit: int = 200_000, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    parts = []
    for obj in objs:
        co = meshdata.get_positions(obj.data)
        if len(co) == 0:
            continue
        mw = np.array(obj.matrix_world, dtype=np.float64)
        co = co @ mw[:3, :3].T + mw[:3, 3]
        if len(co) > limit:
            co = co[rng.choice(len(co), limit, replace=False)]
        parts.append(co)
    return np.concatenate(parts, axis=0) if parts else np.zeros((0, 3))


def render_previews(ctx: TunerContext, out_dir: str, *, resolution: int = RESOLUTION,
                    progress: tuple[float, float] = (0.0, 1.0)) -> list[dict[str, Any]]:
    """Render before/after previews into ``out_dir``; returns ``[{name, view, variant, shading, path}]``.

    ``path`` is relative to the run's output dir (``previews/<name>``). Raises on render failure (callers turn
    that into a warning); the scene state is always restored.
    """
    after_obj = ctx.require_primary()
    before_obj = ctx.original
    others = [o for o in ctx.mesh_objects if o is not after_obj and o.name in bpy.data.objects]
    variants: list[tuple[str, list[bpy.types.Object]]] = [("after", [after_obj, *others])]
    if before_obj is not None:
        variants.insert(0, ("before", [before_obj, *others]))
    os.makedirs(out_dir, exist_ok=True)
    scene = ctx.scene
    unique_objs: list[bpy.types.Object] = []
    for _, objs in variants:
        unique_objs += [o for o in objs if o not in unique_objs]
    pts = _sample_points(unique_objs)
    if len(pts) == 0:
        return []
    bmin, bmax = pts.min(0), pts.max(0)
    diag = float(np.linalg.norm(bmax - bmin))
    ref = meshdata.get_positions(before_obj.data) if before_obj is not None else meshdata.get_positions(after_obj.data)
    head = head_frame(ref, bmin, bmax)

    shots: list[tuple[str, str, np.ndarray, float, np.ndarray]] = []  # view, shading, target, scale, direction
    for view, direction in VIEW_DIRECTIONS.items():
        dvec = np.array(direction)
        target, scale = frame_points(pts, dvec)
        shots.append((view, "matcap", target, scale, dvec))
    if head is not None:
        target, scale = head
        dvec = np.array(VIEW_DIRECTIONS["front"])
        shots.append(("face", "matcap", target, scale, dvec))
        shots.append(("face_texture", "texture", target, scale, dvec))

    state = _SceneState(scene)
    cam_data = bpy.data.cameras.new(f"{_PREFIX}_camera")
    cam_obj = bpy.data.objects.new(f"{_PREFIX}_camera", cam_data)
    world = bpy.data.worlds.new(f"{_PREFIX}_world")
    results: list[dict[str, Any]] = []
    try:
        scene.collection.objects.link(cam_obj)
        world.color = (0.18, 0.18, 0.18)
        scene.world = world
        scene.camera = cam_obj
        render = scene.render
        render.engine = "BLENDER_WORKBENCH"
        render.resolution_x = render.resolution_y = int(resolution)
        render.resolution_percentage = 100
        render.film_transparent = False
        render.use_file_extension = True
        render.image_settings.file_format = "PNG"
        render.image_settings.color_mode = "RGB"
        render.image_settings.color_depth = "8"
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        scene.display.render_aa = "8"
        cam_data.type = "ORTHO"
        cam_data.clip_start = max(diag * 1e-3, 1e-5)
        cam_data.clip_end = diag * 10.0
        total = len(shots) * len(variants)
        done = 0
        p0, p1 = progress
        for variant, visible in variants:
            visible_names = {o.name for o in visible}
            for obj in scene.objects:
                if obj.type in ("MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "POINTCLOUD", "VOLUME"):
                    show = obj.name in visible_names
                    obj.hide_render = not show
                    if show:
                        obj.hide_viewport = False
            for view, shading, target, scale, dvec in shots:
                _setup_shading(scene, shading)
                cam_data.ortho_scale = scale
                cam_obj.location = Vector((target + dvec * (2.0 * diag)).tolist())
                cam_obj.rotation_euler = (-Vector(dvec.tolist())).to_track_quat("-Z", "Y").to_euler()
                name = f"{variant}_{view}.png"
                path = os.path.join(out_dir, name)
                render.filepath = path
                t0 = time.perf_counter()
                bpy.ops.render.render(write_still=True)
                if not os.path.isfile(path):
                    raise RuntimeError(f"Workbench render did not write {name}")
                results.append({"name": name, "view": view, "variant": variant, "shading": shading,
                                "path": f"{os.path.basename(out_dir)}/{name}",
                                "render_s": round(time.perf_counter() - t0, 3)})
                done += 1
                ctx.progress(p0 + (p1 - p0) * done / total, f"Rendering previews ({done}/{total})")
    finally:
        state.restore()
        bpy.data.objects.remove(cam_obj, do_unlink=True)
        bpy.data.cameras.remove(cam_data)
        bpy.data.worlds.remove(world)
    if head is None:
        log.info("Face close-up skipped (model is not an upright humanoid shape)")
    return results

