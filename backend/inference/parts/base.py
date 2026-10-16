"""3D part role ABCs (part-aware DESIGN.md section 5.2). Torch-free; frozen in slice S1.

The :class:`LabelLifter` (nvdiffrast; no weights, ``approval_required=False``) rasterises the base mesh to lift 2D
labels onto faces: it fits the input-view camera to the conditioning silhouette, renders face-ID/depth/cosine buffers
for voting and renders textured and label-coloured views. Arrays in and out are plain numpy; the GPU copy of a mesh
stays inside the provider behind an opaque :class:`MeshHandle`.

Cameras use the asset frame (glTF, +Y up, +Z front): a camera with yaw/pitch sits at
``distance·(sin yaw·cos pitch, sin pitch, cos yaw·cos pitch)`` and looks at the origin, so yaw 0 is the front view.

:class:`MeshSegmenter3D`, :class:`AmodalCompleter` and :class:`PartGenerator` are future seams: interfaces only, zero
implementations; their catalog entries report ``unsupported``.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from inference.base import CancellationToken
from inference.providers.base import ModelProvider
from inference.segmentation.base import Mask2D

if TYPE_CHECKING:
    import numpy as np

    from parts.reader import PartGeometry


@dataclass(frozen=True, slots=True)
class MeshArrays:
    """The base mesh in the asset frame: positions float32 ``[V, 3]``, faces uint32 ``[F, 3]`` (base face order),
    optional UVs float32 ``[V, 2]`` and the decoded baseColor atlas uint8 ``[H, W, 3]``."""

    positions: np.ndarray
    faces: np.ndarray
    uv: np.ndarray | None
    atlas_rgb: np.ndarray | None


@dataclass(frozen=True, slots=True)
class MeshHandle:
    """Opaque reference to a mesh uploaded to a lifter (the GPU buffers stay inside the provider)."""

    handle_id: str
    vertex_count: int
    face_count: int


@dataclass(frozen=True, slots=True)
class CameraSpec:
    yaw_deg: float
    pitch_deg: float
    fov_deg: float
    distance: float


@dataclass(frozen=True, slots=True)
class CameraFit(CameraSpec):
    """The fitted input-view camera."""

    crop: tuple[float, float, float, float]
    """Normalized square crop that makes the render match the conditioning framing."""
    iou: float
    accepted: bool
    method: str
    candidates_tested: int


@dataclass(frozen=True, slots=True)
class CameraGrid:
    """Silhouette search grid: every (yaw, pitch, fov) is scored, then the best ``refine_top_k`` are refined over
    ``±refine_range_deg`` in ``refine_step_deg`` steps; a fit is accepted at ``iou >= accept_iou``."""

    yaws: tuple[float, ...]
    pitches: tuple[float, ...]
    fovs: tuple[float, ...]
    refine_top_k: int = 5
    refine_range_deg: float = 5.0
    refine_step_deg: float = 1.0
    accept_iou: float = 0.6


@dataclass(slots=True)
class RasterOut:
    """Per-pixel buffers of one render: ``face_id`` int32 (-1 = background), ``depth`` float32, ``cos`` float32 (the
    absolute cosine between the view direction and the face normal)."""

    face_id: np.ndarray
    depth: np.ndarray
    cos: np.ndarray


class LabelLifter(ModelProvider):
    """Rasteriser that lifts 2D labels onto mesh faces (nvdiffrast in catalog v1)."""

    @abstractmethod
    def upload(self, mesh: MeshArrays) -> MeshHandle:
        """Copy the mesh to the device once per analysis."""

    @abstractmethod
    def release(self, handle: MeshHandle) -> None:
        """Free the device copy (idempotent)."""

    @abstractmethod
    def fit_camera(
        self, handle: MeshHandle, foreground: np.ndarray, grid: CameraGrid, cancel: CancellationToken
    ) -> CameraFit:
        """Fit the input-view camera to the boolean conditioning ``foreground`` mask by silhouette IoU."""

    @abstractmethod
    def raster(
        self,
        handle: MeshHandle,
        camera: CameraSpec,
        size: tuple[int, int],
        crop: tuple[float, float, float, float] | None,
    ) -> RasterOut:
        """Face-ID, depth and cosine buffers at ``size`` (width, height), optionally restricted to a normalized crop."""

    @abstractmethod
    def render_textured(self, handle: MeshHandle, camera: CameraSpec, size: tuple[int, int]) -> np.ndarray:
        """Unlit baseColor render, uint8 ``[H, W, 3]`` (extra views are segmented from it)."""

    @abstractmethod
    def render_labels(
        self, handle: MeshHandle, camera: CameraSpec, size: tuple[int, int], face_colors: np.ndarray
    ) -> np.ndarray:
        """Render with one uint8 RGB colour per face (``face_colors`` ``[F, 3]``), uint8 ``[H, W, 3]``."""


# --------------------------------------------------------------------------------------------------
# Future seams: interfaces only, zero implementations (catalog entries report "unsupported")
# --------------------------------------------------------------------------------------------------


class MeshSegmenter3D(ModelProvider):
    """Native 3D part segmentation (e.g. P3-SAM, PartField)."""

    @abstractmethod
    def segment_mesh(self, mesh: MeshArrays, cancel: CancellationToken) -> np.ndarray:
        """Per-face labels, int32 ``[F]``."""


class AmodalCompleter(ModelProvider):
    """Completion of occluded part geometry (e.g. HoloPart)."""

    @abstractmethod
    def complete(self, part: PartGeometry, cancel: CancellationToken) -> Path:
        """Path of the completed part mesh."""


class PartGenerator(ModelProvider):
    """Per-part generation from image masks (e.g. X-Part)."""

    @abstractmethod
    def generate_parts(self, image: Path, masks: Sequence[Mask2D], cancel: CancellationToken) -> list[Path]:
        """Paths of the generated part meshes."""
