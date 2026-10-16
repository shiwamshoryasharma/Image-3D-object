"""Frames, lifting views, part frames and sides (part-aware DESIGN.md section 3.7; slice S3b).

* ``frames.asset`` is glTF (+Y up, +Z front, normalized unit AABB); ``frames.trellis`` is TRELLIS' Blender frame with
  its ``to_asset`` matrix (row-major, column vectors: x' = x, y' = z, z' = -y); ``frames.conditioning`` describes the
  image the 2D labels live in (section 4.5.2).
* ``views[]`` hold the lifting cameras. A camera with yaw/pitch sits at ``d * (sin yaw cos pitch, sin pitch,
  cos yaw cos pitch)`` in the asset frame and looks at the origin (yaw 0 = front view). ``world_to_camera`` uses the
  OpenCV convention (x right, y down, z forward) and the intrinsics are normalized (``fx = fy = 0.5 / tan(fov / 2)``).
* A part frame is ``{pivot, axes, method}``: the pivot is the seam-loop centroid of cut parts, the bbox centre of
  seamless shells and the area-weighted centroid of the base, regions and deferred parts; the axes are the PCA of the
  part's welded vertices, made right-handed and sign-stabilised towards the asset axes (identity under 3 vertices).
* ``side`` is character-relative. With an accepted front-ish fit the character's left is image-right (+X); with a
  rejected fit the side comes from the centroid x and is marked ``assumed_front``.

Torch-free: numpy only.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np

from parts.manifest import (
    CameraIntrinsics,
    ConditioningFrameInfo,
    Frames,
    PartFrame,
    ViewCamera,
    ViewFit,
    ViewRecord,
)
from parts.taxonomy import PartClass
from parts.types import PartFrameMethod, Side

INPUT_VIEW_ID: Final = "input"
INPUT_VIEW_WEIGHT: Final = 1.0
EXTRA_VIEW_WEIGHT: Final = 0.5
EXTRA_VIEW_HIGH_PITCH_DEG: Final = 30.0
FRONT_FALLBACK: Final = (0.0, 0.0, 30.0)
"""(yaw, pitch, fov) of the front view used when the fit is rejected (section 4.6.5 step 3)."""
CENTER_TOLERANCE: Final = 0.1
"""A part whose centroid x lies within this share of its half-width of x = 0 is ``center``."""
CENTER_MIN_ABS: Final = 0.01
ASSUMED_FRONT_CONFIDENCE: Final = 0.5
_WORLD_UP: Final = np.array([0.0, 1.0, 0.0])


def build_frames(conditioning: ConditioningFrameInfo | None) -> Frames:
    """The ``frames`` object: fixed ``asset``/``trellis`` frames plus the conditioning frame (``None`` without 2D)."""
    return Frames(conditioning=conditioning)


# --------------------------------------------------------------------------------------------------
# Cameras
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ViewCameraSpec:
    """A lifting camera in the asset frame (degrees; distance in asset units)."""

    yaw_deg: float
    pitch_deg: float
    fov_deg: float
    distance: float


def camera_position(yaw_deg: float, pitch_deg: float, distance: float) -> np.ndarray:
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    return distance * np.array([math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch)])


def world_to_camera(yaw_deg: float, pitch_deg: float, distance: float) -> list[float]:
    """Row-major 4x4 OpenCV extrinsics of a camera at ``camera_position`` looking at the origin."""
    if distance <= 0.0:
        raise ValueError("camera distance must be positive")
    center = camera_position(yaw_deg, pitch_deg, distance)
    forward = -center / np.linalg.norm(center)
    right = np.cross(forward, _WORLD_UP)
    if np.linalg.norm(right) < 1e-9:  # looking straight up or down: keep +X as image right
        right = np.array([1.0, 0.0, 0.0])
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    rotation = np.stack([right, down, forward])
    translation = -rotation @ center
    matrix = np.eye(4)
    matrix[:3, :3] = rotation
    matrix[:3, 3] = translation
    matrix[np.abs(matrix) < 1e-12] = 0.0
    return [float(value) for value in matrix.reshape(-1)]


def intrinsics(fov_deg: float) -> CameraIntrinsics:
    """Normalized pinhole intrinsics of a square image: ``fx = fy = 0.5 / tan(fov / 2)``, principal point centred."""
    if not 0.0 < fov_deg < 180.0:
        raise ValueError("fov_deg must be in (0, 180)")
    focal = 0.5 / math.tan(math.radians(fov_deg) / 2.0)
    return CameraIntrinsics(fx=focal, fy=focal, cx=0.5, cy=0.5, normalized=True)


def view_camera(camera: ViewCameraSpec) -> ViewCamera:
    return ViewCamera(
        yaw_deg=float(camera.yaw_deg),
        pitch_deg=float(camera.pitch_deg),
        fov_deg=float(camera.fov_deg),
        distance=float(camera.distance),
        world_to_camera=world_to_camera(camera.yaw_deg, camera.pitch_deg, camera.distance),
        intrinsics=intrinsics(camera.fov_deg),
    )


def input_view(
    camera: ViewCameraSpec, *, method: str, iou: float, accepted: bool, candidates_tested: int
) -> ViewRecord:
    """The ``input`` view record with its silhouette fit."""
    return ViewRecord(
        id=INPUT_VIEW_ID,
        camera=view_camera(camera),
        fit=ViewFit(method=method, iou=float(min(max(iou, 0.0), 1.0)), accepted=bool(accepted),
                    candidates_tested=int(candidates_tested)),
        weight=INPUT_VIEW_WEIGHT,
    )


def extra_view_specs(fit: ViewCameraSpec, views: int) -> list[ViewCameraSpec]:
    """Extra lifting cameras (section 4.6.5 step 4): ``yaw_i = fit_yaw + 360 * i / (views + 1)``, pitch 0 for odd
    ``i`` and 30 degrees for even ``i``, same FOV and distance as the fit."""
    if views < 0:
        raise ValueError("views must be >= 0")
    specs: list[ViewCameraSpec] = []
    for i in range(1, views + 1):
        yaw = (fit.yaw_deg + 360.0 * i / (views + 1)) % 360.0
        pitch = 0.0 if i % 2 else EXTRA_VIEW_HIGH_PITCH_DEG
        specs.append(ViewCameraSpec(yaw_deg=yaw, pitch_deg=pitch, fov_deg=fit.fov_deg, distance=fit.distance))
    return specs


def extra_views(specs: Sequence[ViewCameraSpec]) -> list[ViewRecord]:
    """``view_<i>`` records (weight 0.5) for the extra cameras, numbered from 1."""
    return [
        ViewRecord(id=f"view_{i}", camera=view_camera(spec), fit=None, weight=EXTRA_VIEW_WEIGHT)
        for i, spec in enumerate(specs, start=1)
    ]


# --------------------------------------------------------------------------------------------------
# Part frames
# --------------------------------------------------------------------------------------------------


def principal_axes(points: np.ndarray) -> list[list[float]]:
    """Right-handed PCA axes (rows, descending variance), each sign-stabilised towards the nearest asset axis.

    The first two axes point to the positive side of the asset axis they are most aligned with; the third is their
    cross product. Fewer than 3 points (or a degenerate cloud) give the identity.
    """
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    identity = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    if pts.shape[0] < 3:
        return identity
    centered = pts - pts.mean(axis=0)
    covariance = centered.T @ centered / pts.shape[0]
    if not np.all(np.isfinite(covariance)) or float(np.trace(covariance)) <= 0.0:
        return identity
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    axes = eigenvectors[:, np.argsort(eigenvalues)[::-1]].T
    for row in range(2):
        dominant = int(np.argmax(np.abs(axes[row])))
        if axes[row, dominant] < 0.0:
            axes[row] = -axes[row]
    axes[2] = np.cross(axes[0], axes[1])
    axes[2] /= max(float(np.linalg.norm(axes[2])), 1e-12)
    return [[float(v) for v in row] for row in axes]


def part_frame(method: PartFrameMethod, pivot: Sequence[float], welded_points: np.ndarray) -> PartFrame:
    return PartFrame(pivot=[float(v) for v in pivot], axes=principal_axes(welded_points), method=method)


def bbox_center(points: np.ndarray) -> list[float]:
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if pts.shape[0] == 0:
        return [0.0, 0.0, 0.0]
    return [float(v) for v in (pts.min(axis=0) + pts.max(axis=0)) / 2.0]


# --------------------------------------------------------------------------------------------------
# Sides
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SideInfo:
    side: Side | None
    image_side: Side | None
    assumed_front: bool


def _mirror(side: Side | None) -> Side | None:
    if side == "left":
        return "right"
    if side == "right":
        return "left"
    return side


def _side_from_x(centroid_x: float, half_width: float) -> Side:
    if abs(centroid_x) <= max(CENTER_MIN_ABS, CENTER_TOLERANCE * max(half_width, 0.0)):
        return "center"
    return "left" if centroid_x > 0.0 else "right"


def resolve_side(
    cls: PartClass,
    centroid_x: float,
    half_width: float,
    *,
    fit_accepted: bool | None,
    fit_yaw_deg: float | None = None,
) -> SideInfo:
    """``side`` / ``image_side`` of one record (section 3.7).

    ``fit_accepted`` is ``None`` without a camera fit (``components``): the side then comes from the centroid and the
    image side is unknown. A sided class keeps the side named by its id. With an accepted fit, image-left and
    character-left agree only for back-ish views (``90 < yaw < 270``).
    """
    if fit_accepted is None:
        side = cls.side if cls.sided else _side_from_x(centroid_x, half_width)
        return SideInfo(side=side, image_side=None, assumed_front=False)
    if not fit_accepted:
        side = cls.side if cls.sided else _side_from_x(centroid_x, half_width)
        return SideInfo(side=side, image_side=_mirror(side), assumed_front=True)
    side = cls.side if cls.sided else _side_from_x(centroid_x, half_width)
    yaw = (fit_yaw_deg or 0.0) % 360.0
    back = 90.0 < yaw < 270.0
    return SideInfo(side=side, image_side=side if back else _mirror(side), assumed_front=False)


__all__ = [
    "ASSUMED_FRONT_CONFIDENCE",
    "EXTRA_VIEW_WEIGHT",
    "FRONT_FALLBACK",
    "INPUT_VIEW_ID",
    "INPUT_VIEW_WEIGHT",
    "SideInfo",
    "ViewCameraSpec",
    "bbox_center",
    "build_frames",
    "camera_position",
    "extra_view_specs",
    "extra_views",
    "input_view",
    "intrinsics",
    "part_frame",
    "principal_axes",
    "resolve_side",
    "view_camera",
    "world_to_camera",
]
