"""Camera helpers of the raster label lifter (part-aware DESIGN.md sections 3.7, 4.6.4 and 4.6.5; slice S7).

Pure numpy: the matrices are built here in float64 and handed to :mod:`inference.parts.raster_lifter`, which moves
them to the device. ``utils3d`` is not installed, so ``extrinsics_look_at`` and ``intrinsics_from_fov_xy`` are
re-implemented (``trellis2_internals.md`` section 3.2).

Conventions
-----------
* **World = asset frame** (glTF): +Y up, +Z front, units normalized (``[-0.5, 0.5]^3``).
* **Camera pose** (:class:`~inference.parts.base.CameraSpec`): the camera sits at
  ``distance·(sin yaw·cos pitch, sin pitch, cos yaw·cos pitch)`` and looks at the origin with +Y up, so yaw 0 is the
  front view and image-right is +X.
* **Extrinsics** are OpenCV world→camera (x right, y down, z forward), row-major 4x4 with column vectors.
* **Intrinsics** are normalized to the image: ``u = fx·x/z + cx`` and ``v = fy·y/z + cy`` with ``u, v`` in 0..1,
  ``v`` top-down. ``fov_deg`` spans the full square frame (``fx = fy = 0.5/tan(fov/2)``, ``cx = cy = 0.5``).
* **Crops** are normalized regions ``(x0, y0, x1, y1)`` of that square frame (values may leave 0..1). Rendering a
  crop at ``size = (width, height)`` maps it onto the whole output image; the crop's aspect equals ``width/height``,
  so pixels stay square. ``crop=None`` is the full frame; for a non-square ``size`` the frame is extended along the
  longer side so the field of view still covers the shorter side.
* **Clip space** (nvdiffrast): ``ndc_x = 2u - 1`` and ``ndc_y = 2v - 1``. nvdiffrast writes the row with
  ``ndc_y = -1`` first, so output rows are top-down like the conditioning image.
* **Camera distance** per FOV: ``1.05·r / sin(fov/2)`` (section 4.6.5 step 3), so the bounding sphere fills the frame
  at every FOV instead of clipping at 10° or shrinking at 50°.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Final

import numpy as np

from inference.parts.base import CameraGrid, CameraSpec

Crop = tuple[float, float, float, float]
"""Normalized ``(x0, y0, x1, y1)`` region of the square full frame."""
BBox = tuple[float, float, float, float]
"""``(x0, y0, x1, y1)`` with pixel-edge coordinates (``x1``/``y1`` exclusive)."""

DISTANCE_MARGIN: Final = 1.05
"""Bounding-sphere margin of the camera distance rule."""
FRONT_CAMERA: Final = (0.0, 0.0, 30.0)
"""``(yaw, pitch, fov)`` used when a silhouette fit is rejected (section 4.6.5 step 3)."""
EXTRA_VIEW_PITCHES: Final = (0.0, 30.0)
"""Pitch of the odd / even extra views (section 4.6.5 step 4)."""
MAX_ABS_PITCH_DEG: Final = 89.0
"""Pitches are clamped to keep the +Y up vector well defined."""
_TRI_ID_PLAIN_MAX: Final = 16_777_216
_TRI_ID_FLOAT_BIAS: Final = 0x4A800000


# --------------------------------------------------------------------------------------------------
# Search grid
# --------------------------------------------------------------------------------------------------


def default_camera_grid() -> CameraGrid:
    """The section 4.6.5 grid: yaw 0..350° step 10 x pitch -20..40° step 10 x FOV {10, 30, 50} = 756 candidates,
    top 5 refined over ±5° in 1° steps, accepted at IoU >= 0.6."""
    return CameraGrid(
        yaws=tuple(float(y) for y in range(0, 360, 10)),
        pitches=tuple(float(p) for p in range(-20, 41, 10)),
        fovs=(10.0, 30.0, 50.0),
    )


def wrap_yaw(yaw_deg: float) -> float:
    """Yaw in ``[0, 360)``."""
    wrapped = math.fmod(float(yaw_deg), 360.0)
    if wrapped < 0.0:
        wrapped += 360.0
    return 0.0 if wrapped >= 360.0 else wrapped


def clamp_pitch(pitch_deg: float) -> float:
    return max(-MAX_ABS_PITCH_DEG, min(MAX_ABS_PITCH_DEG, float(pitch_deg)))


def yaw_difference(a_deg: float, b_deg: float) -> float:
    """Absolute circular yaw difference in degrees (0..180)."""
    diff = abs(wrap_yaw(a_deg) - wrap_yaw(b_deg))
    return min(diff, 360.0 - diff)


def view_direction(yaw_deg: float, pitch_deg: float) -> np.ndarray:
    """Unit vector from the origin towards the camera."""
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    return np.array(
        [math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch)], dtype=np.float64
    )


def angle_from_front(yaw_deg: float, pitch_deg: float) -> float:
    """Great-circle angle (degrees) between this viewing direction and the front view (yaw 0, pitch 0)."""
    cosine = float(np.clip(view_direction(yaw_deg, pitch_deg)[2], -1.0, 1.0))
    return math.degrees(math.acos(cosine))


def coarse_candidates(grid: CameraGrid) -> list[tuple[float, float, float]]:
    """Every ``(yaw, pitch, fov)`` of the grid, in grid order."""
    return [
        (wrap_yaw(yaw), clamp_pitch(pitch), float(fov))
        for fov in grid.fovs
        for pitch in grid.pitches
        for yaw in grid.yaws
    ]


def refine_candidates(
    yaw_deg: float, pitch_deg: float, fov_deg: float, grid: CameraGrid
) -> list[tuple[float, float, float]]:
    """The ``±refine_range_deg`` neighbourhood of one candidate in ``refine_step_deg`` steps (same FOV)."""
    step = float(grid.refine_step_deg)
    if step <= 0.0 or grid.refine_range_deg <= 0.0:
        return [(wrap_yaw(yaw_deg), clamp_pitch(pitch_deg), float(fov_deg))]
    count = int(math.floor(grid.refine_range_deg / step + 1e-9))
    offsets = [i * step for i in range(-count, count + 1)]
    return [
        (wrap_yaw(yaw_deg + dy), clamp_pitch(pitch_deg + dp), float(fov_deg)) for dp in offsets for dy in offsets
    ]


def unique_candidates(candidates: Iterable[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    """Drop duplicates (rounded to 1e-6°), keeping the first occurrence."""
    seen: set[tuple[float, float, float]] = set()
    out: list[tuple[float, float, float]] = []
    for yaw, pitch, fov in candidates:
        key = (round(wrap_yaw(yaw), 6) % 360.0, round(pitch, 6), round(fov, 6))
        if key not in seen:
            seen.add(key)
            out.append((yaw, pitch, fov))
    return out


def extra_view_cameras(fit: CameraSpec, views: int) -> list[CameraSpec]:
    """Extra lifting views of section 4.6.5 step 4: ``yaw_i = fit_yaw + 360·i/(views+1)`` for ``i = 1..views``,
    pitch 0 for odd ``i`` and 30° for even ``i``; FOV and distance of the fitted camera."""
    cameras: list[CameraSpec] = []
    for i in range(1, int(views) + 1):
        pitch = EXTRA_VIEW_PITCHES[0] if i % 2 == 1 else EXTRA_VIEW_PITCHES[1]
        yaw = wrap_yaw(fit.yaw_deg + 360.0 * i / (views + 1))
        cameras.append(CameraSpec(yaw_deg=yaw, pitch_deg=pitch, fov_deg=fit.fov_deg, distance=fit.distance))
    return cameras


# --------------------------------------------------------------------------------------------------
# Distance, pose and projection
# --------------------------------------------------------------------------------------------------


def bounding_radius(positions: np.ndarray) -> float:
    """Radius of the bounding sphere centred at the AABB centre, grown by the centre's distance from the origin.

    Cameras look at the origin, so the sphere that matters is the one about the origin; TRELLIS.2 meshes are centred
    within a few 1e-3, which makes this the section 4.6.5 ``r_bs`` up to that offset.
    """
    pts = np.asarray(positions, dtype=np.float64).reshape(-1, 3)
    if pts.size == 0:
        raise ValueError("the mesh has no vertices")
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    centre = (lo + hi) * 0.5
    radius = float(np.sqrt(((pts - centre) ** 2).sum(axis=1).max()))
    radius += float(np.linalg.norm(centre))
    if not math.isfinite(radius) or radius <= 0.0:
        raise ValueError("the mesh has a degenerate bounding sphere")
    return radius


def fit_distance(fov_deg: float, radius: float) -> float:
    """``1.05·r / sin(fov/2)``: the bounding sphere stays inside the frame at every FOV."""
    half = math.radians(float(fov_deg)) * 0.5
    if not 0.0 < half < math.pi / 2:
        raise ValueError(f"fov_deg must be in (0, 180), got {fov_deg}")
    return DISTANCE_MARGIN * float(radius) / math.sin(half)


def resolve_distance(camera: CameraSpec, radius: float) -> float:
    """The camera's distance, or the fit rule when ``camera.distance <= 0`` (auto distance)."""
    return float(camera.distance) if camera.distance > 0.0 else fit_distance(camera.fov_deg, radius)


def camera_position(yaw_deg: float, pitch_deg: float, distance: float) -> np.ndarray:
    return view_direction(yaw_deg, clamp_pitch(pitch_deg)) * float(distance)


def extrinsics_look_at(eye: Sequence[float], target: Sequence[float], up: Sequence[float]) -> np.ndarray:
    """OpenCV world→camera matrix (4x4, float64) of a camera at ``eye`` looking at ``target``."""
    eye_v = np.asarray(eye, dtype=np.float64)
    forward = np.asarray(target, dtype=np.float64) - eye_v
    norm = np.linalg.norm(forward)
    if norm <= 0.0:
        raise ValueError("eye and target coincide")
    z_axis = forward / norm
    x_axis = np.cross(z_axis, np.asarray(up, dtype=np.float64))
    x_norm = np.linalg.norm(x_axis)
    if x_norm < 1e-9:
        raise ValueError("the up vector is parallel to the viewing direction")
    x_axis /= x_norm
    y_axis = np.cross(z_axis, x_axis)
    matrix = np.eye(4, dtype=np.float64)
    matrix[0, :3], matrix[1, :3], matrix[2, :3] = x_axis, y_axis, z_axis
    matrix[:3, 3] = -matrix[:3, :3] @ eye_v
    return matrix


def world_to_camera(camera: CameraSpec, radius: float | None = None) -> np.ndarray:
    """Extrinsics of a :class:`CameraSpec` (looks at the origin, +Y up). ``radius`` resolves an auto distance."""
    distance = float(camera.distance)
    if distance <= 0.0:
        if radius is None:
            raise ValueError("an auto camera distance needs the bounding radius")
        distance = fit_distance(camera.fov_deg, radius)
    eye = camera_position(camera.yaw_deg, camera.pitch_deg, distance)
    return extrinsics_look_at(eye, (0.0, 0.0, 0.0), (0.0, 1.0, 0.0))


def intrinsics_from_fov_xy(fov_x_deg: float, fov_y_deg: float) -> np.ndarray:
    """Normalized OpenCV intrinsics (3x3) of a principal-point-centred camera."""
    fx = 0.5 / math.tan(math.radians(float(fov_x_deg)) * 0.5)
    fy = 0.5 / math.tan(math.radians(float(fov_y_deg)) * 0.5)
    return np.array([[fx, 0.0, 0.5], [0.0, fy, 0.5], [0.0, 0.0, 1.0]], dtype=np.float64)


def full_frame_crop(size: tuple[int, int]) -> Crop:
    """The ``crop=None`` region for an output ``size`` (width, height)."""
    width, height = int(size[0]), int(size[1])
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid render size {size}")
    if width >= height:
        half = 0.5 * width / height
        return (0.5 - half, 0.0, 0.5 + half, 1.0)
    half = 0.5 * height / width
    return (0.0, 0.5 - half, 1.0, 0.5 + half)


def camera_intrinsics(fov_deg: float, crop: Crop | None, size: tuple[int, int]) -> tuple[float, float, float, float]:
    """Normalized ``(fx, fy, cx, cy)`` of the output image: the square-frame intrinsics remapped onto ``crop``."""
    base = intrinsics_from_fov_xy(fov_deg, fov_deg)
    x0, y0, x1, y1 = crop if crop is not None else full_frame_crop(size)
    width, height = x1 - x0, y1 - y0
    if width <= 0.0 or height <= 0.0:
        raise ValueError(f"invalid crop {crop}")
    return (
        float(base[0, 0] / width),
        float(base[1, 1] / height),
        float((base[0, 2] - x0) / width),
        float((base[1, 2] - y0) / height),
    )


def clip_planes(distance: float, radius: float) -> tuple[float, float]:
    """Near/far planes that enclose the bounding sphere with margin."""
    far = float(distance) + 1.5 * float(radius)
    near = max(float(distance) - 1.5 * float(radius), 1e-3 * far)
    return near, far


def perspective_from_intrinsics(
    intrinsics: tuple[float, float, float, float], near: float, far: float
) -> np.ndarray:
    """OpenCV camera → clip matrix (4x4) with ``ndc = 2·(u, v) - 1`` and depth -1 at ``near``, +1 at ``far``."""
    fx, fy, cx, cy = intrinsics
    proj = np.zeros((4, 4), dtype=np.float64)
    proj[0, 0], proj[0, 2] = 2.0 * fx, 2.0 * cx - 1.0
    proj[1, 1], proj[1, 2] = 2.0 * fy, 2.0 * cy - 1.0
    proj[2, 2] = (far + near) / (far - near)
    proj[2, 3] = -2.0 * near * far / (far - near)
    proj[3, 2] = 1.0
    return proj


def view_clip_matrix(
    camera: CameraSpec, radius: float, size: tuple[int, int], crop: Crop | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """``(world→clip, world→camera)`` for one render (the distance resolves with the fit rule when ``<= 0``)."""
    distance = resolve_distance(camera, radius)
    resolved = CameraSpec(camera.yaw_deg, camera.pitch_deg, camera.fov_deg, distance)
    extrinsics = world_to_camera(resolved)
    near, far = clip_planes(distance, radius)
    proj = perspective_from_intrinsics(camera_intrinsics(camera.fov_deg, crop, size), near, far)
    return proj @ extrinsics, extrinsics


# --------------------------------------------------------------------------------------------------
# Silhouette framing (section 4.6.5 step 3)
# --------------------------------------------------------------------------------------------------


def mask_bbox(mask: np.ndarray) -> BBox | None:
    """Tight bbox of a boolean ``[H, W]`` mask in pixel-edge coordinates, ``None`` when empty."""
    arr = np.asarray(mask, dtype=bool)
    rows = np.flatnonzero(arr.any(axis=1))
    if rows.size == 0:
        return None
    cols = np.flatnonzero(arr.any(axis=0))
    return (float(cols[0]), float(rows[0]), float(cols[-1] + 1), float(rows[-1] + 1))


def framing_crop(silhouette_bbox: BBox, foreground_bbox: BBox, foreground_size: tuple[int, int]) -> Crop:
    """Crop of the render that reproduces the conditioning framing.

    ``silhouette_bbox`` is the render silhouette's tight bbox in normalized full-frame units; ``foreground_bbox`` is
    the conditioning foreground's bbox in pixels of an image of ``foreground_size`` (width, height). The longer side
    of the silhouette bbox is scaled onto the longer side of the foreground bbox and the centres are aligned (a 2D
    similarity). For conditioning images made by the upstream tight-bbox square crop (``alpha_crop`` /
    ``background_removed``), the foreground spans the longer side and is centred, so this is exactly the square crop
    of section 4.5.2; for ``untouched`` jobs it is the scale + translation fit.
    """
    sx0, sy0, sx1, sy1 = silhouette_bbox
    fx0, fy0, fx1, fy1 = foreground_bbox
    width, height = float(foreground_size[0]), float(foreground_size[1])
    s_side = max(sx1 - sx0, sy1 - sy0)
    f_side = max(fx1 - fx0, fy1 - fy0)
    if s_side <= 0.0 or f_side <= 0.0:
        raise ValueError("empty silhouette or foreground")
    scale = f_side / s_side  # foreground pixels per normalized render unit
    scx, scy = (sx0 + sx1) * 0.5, (sy0 + sy1) * 0.5
    fcx, fcy = (fx0 + fx1) * 0.5, (fy0 + fy1) * 0.5
    return (scx - fcx / scale, scy - fcy / scale, scx + (width - fcx) / scale, scy + (height - fcy) / scale)


def silhouette_iou(a: np.ndarray, b: np.ndarray) -> float:
    """IoU of two boolean masks of equal shape (0 when both are empty)."""
    a_b, b_b = np.asarray(a, dtype=bool), np.asarray(b, dtype=bool)
    if a_b.shape != b_b.shape:
        raise ValueError(f"mask shapes differ: {a_b.shape} vs {b_b.shape}")
    union = int(np.logical_or(a_b, b_b).sum())
    return float(np.logical_and(a_b, b_b).sum()) / union if union else 0.0


# --------------------------------------------------------------------------------------------------
# nvdiffrast triangle ids
# --------------------------------------------------------------------------------------------------


def decode_triangle_ids(values: np.ndarray) -> np.ndarray:
    """Face index (int32, -1 = background) from nvdiffrast's float32 ``triangle_id + 1`` channel.

    IDs up to 2^24 are stored as plain floats; larger ones as ``__int_as_float(0x4a800000 + id)``
    (``nvdiffrast/csrc/common/common.h``). Never add offsets to the float values.
    """
    floats = np.ascontiguousarray(values, dtype=np.float32)
    plain_mask = floats <= np.float32(_TRI_ID_PLAIN_MAX)
    ids = floats.view(np.int32).astype(np.int64) - _TRI_ID_FLOAT_BIAS
    ids[plain_mask] = floats[plain_mask].astype(np.int64)
    return (ids - 1).astype(np.int32)


def encode_triangle_ids(face_index: np.ndarray) -> np.ndarray:
    """Inverse of :func:`decode_triangle_ids` (``face_index`` int, -1 = background); used to verify the decode."""
    ids = np.asarray(face_index, dtype=np.int64) + 1
    plain = ids.astype(np.float32)
    biased = (ids + _TRI_ID_FLOAT_BIAS).astype(np.int32).view(np.float32)
    return np.where(ids <= _TRI_ID_PLAIN_MAX, plain, biased).astype(np.float32)
