"""TEST-ONLY doubles for the raster label lifter (never used by the application).

* :func:`lifter_spec` - the ``raster_lifter`` catalog entry of DESIGN.md section 5.4 as a :class:`ModelSpec`;
* :class:`FakeLabelLifter` - a CPU :class:`~inference.parts.base.LabelLifter` for small meshes. It rasterises with
  numpy using the same camera conventions as the real lifter (:mod:`inference.parts.cameras`: asset frame, look-at
  origin, OpenCV extrinsics, normalized intrinsics, framing crops, rows top-down), so face IDs, depth, ``|cos θ|``,
  textured and label renders are real values a part-analysis test can vote with. ``fit_camera`` does not search: it
  returns the scripted ``fit`` or scores the front camera (yaw 0, pitch 0, FOV 30) against the foreground. Every call
  records ``(method, thread name)`` in :attr:`FakeLabelLifter.calls` so tests can assert the GPU-thread contract;
  ``fail`` maps a method name to an exception raised when it is called.

Keep meshes to a few thousand faces and renders to a few hundred pixels: the rasteriser loops over triangles.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

import numpy as np

from inference.base import CancellationToken
from inference.parts import cameras
from inference.parts.base import CameraFit, CameraGrid, CameraSpec, LabelLifter, MeshArrays, MeshHandle, RasterOut
from inference.providers.base import DependencySpec, LicenseInfo, ModelSpec, ProviderStatus

FAKE_FIT_METHOD: Final = "fake_front"


def lifter_spec() -> ModelSpec:
    """The ``raster_lifter`` catalog row (section 5.4, mirrored from ``inference/providers/catalog.py``): no weights,
    exempt from approval."""
    return ModelSpec(
        id="raster_lifter",
        kind="part_extraction",
        roles=("lifter",),
        display_name="Raster label lifter",
        provider="nvdiffrast_raster",
        repo_id=None,
        revision=None,
        local_subdir=None,
        required_files=(),
        size_bytes=None,
        vram_peak_gb=4.0,
        ram_gb=0.5,
        cold_load_s=None,
        license=LicenseInfo(
            id="nvidia-source-code-license",
            name="NVIDIA Source Code License (nvdiffrast)",
            url="https://github.com/NVlabs/nvdiffrast/blob/main/LICENSE.txt",
            commercial="non_commercial",
            attribution="nvdiffrast (NVIDIA), already required by the TRELLIS.2 export",
        ),
        dependencies=(DependencySpec("nvdiffrast", "==0.4.0"), DependencySpec("cumesh", "==0.0.1")),
        approval_required=False,
        capabilities={"max_views": 24},
    )


@dataclass(slots=True)
class _HostMesh:
    positions: np.ndarray
    faces: np.ndarray
    uv: np.ndarray | None
    atlas: np.ndarray | None
    normals: np.ndarray
    radius: float


class FakeLabelLifter(LabelLifter):
    """CPU numpy lifter with the real camera conventions (see the module docstring)."""

    def __init__(
        self,
        spec: ModelSpec | None = None,
        *,
        fit: CameraFit | None = None,
        fail: Mapping[str, BaseException] | None = None,
    ) -> None:
        super().__init__(spec or lifter_spec())
        self.fit = fit
        self.fail = dict(fail or {})
        self.calls: list[tuple[str, str]] = []
        self.loaded = False
        self._meshes: dict[str, _HostMesh] = {}
        self._next = 0

    # ------------------------------------------------------------------ lifecycle

    def status(self) -> ProviderStatus:
        state = "loaded" if self.loaded else "installed"
        return ProviderStatus(
            model_id=self.model_id,
            state=state,
            reason_code="ok",
            reason=None,
            loaded_device="cpu" if self.loaded else None,
        )

    def load(self, cancel: CancellationToken) -> None:
        self._record("load")
        cancel.raise_if_cancelled()
        self.loaded = True

    def offload(self) -> None:
        self._record("offload")
        self._meshes.clear()

    def unload(self) -> None:
        self._record("unload")
        self._meshes.clear()
        self.loaded = False

    @property
    def uploaded(self) -> int:
        """Meshes currently held (lets tests assert that every upload is released)."""
        return len(self._meshes)

    # ------------------------------------------------------------------ meshes

    def upload(self, mesh: MeshArrays) -> MeshHandle:
        self._record("upload")
        positions = np.asarray(mesh.positions, dtype=np.float64).reshape(-1, 3)
        faces = np.asarray(mesh.faces, dtype=np.int64).reshape(-1, 3)
        v0, v1, v2 = positions[faces[:, 0]], positions[faces[:, 1]], positions[faces[:, 2]]
        normals = np.cross(v1 - v0, v2 - v0)
        lengths = np.linalg.norm(normals, axis=1, keepdims=True)
        normals = np.divide(normals, lengths, out=np.zeros_like(normals), where=lengths > 0)
        self._next += 1
        handle = MeshHandle(handle_id=f"fake_mesh_{self._next}", vertex_count=len(positions), face_count=len(faces))
        self._meshes[handle.handle_id] = _HostMesh(
            positions=positions,
            faces=faces,
            uv=None if mesh.uv is None else np.asarray(mesh.uv, dtype=np.float64),
            atlas=None if mesh.atlas_rgb is None else np.asarray(mesh.atlas_rgb, dtype=np.uint8),
            normals=normals,
            radius=cameras.bounding_radius(positions),
        )
        return handle

    def release(self, handle: MeshHandle) -> None:
        self._record("release")
        self._meshes.pop(handle.handle_id, None)

    # ------------------------------------------------------------------ role methods

    def fit_camera(
        self, handle: MeshHandle, foreground: np.ndarray, grid: CameraGrid, cancel: CancellationToken
    ) -> CameraFit:
        self._record("fit_camera")
        cancel.raise_if_cancelled()
        mesh = self._mesh(handle)
        if self.fit is not None:
            return self.fit
        fg = np.asarray(foreground, dtype=bool)
        fg_bbox = cameras.mask_bbox(fg)
        if fg_bbox is None:
            raise ValueError("the conditioning foreground is empty")
        yaw, pitch, fov = cameras.FRONT_CAMERA
        camera = CameraSpec(yaw, pitch, fov, cameras.fit_distance(fov, mesh.radius))
        full = _raster(mesh, camera, (128, 128), None)[0] >= 0
        sil_bbox = cameras.mask_bbox(full)
        if sil_bbox is None:
            crop, iou = cameras.full_frame_crop((fg.shape[1], fg.shape[0])), 0.0
        else:
            sil_norm = (sil_bbox[0] / 128.0, sil_bbox[1] / 128.0, sil_bbox[2] / 128.0, sil_bbox[3] / 128.0)
            crop = cameras.framing_crop(sil_norm, fg_bbox, (fg.shape[1], fg.shape[0]))
            framed = _raster(mesh, camera, (fg.shape[1], fg.shape[0]), crop)[0] >= 0
            iou = cameras.silhouette_iou(framed, fg)
        return CameraFit(
            yaw_deg=yaw,
            pitch_deg=pitch,
            fov_deg=fov,
            distance=camera.distance,
            crop=crop,
            iou=iou,
            accepted=iou >= grid.accept_iou,
            method=FAKE_FIT_METHOD,
            candidates_tested=1,
        )

    def raster(
        self,
        handle: MeshHandle,
        camera: CameraSpec,
        size: tuple[int, int],
        crop: tuple[float, float, float, float] | None,
    ) -> RasterOut:
        self._record("raster")
        face, depth, cos, _ = _raster(self._mesh(handle), camera, size, crop)
        return RasterOut(face_id=face, depth=depth, cos=cos)

    def render_textured(self, handle: MeshHandle, camera: CameraSpec, size: tuple[int, int]) -> np.ndarray:
        self._record("render_textured")
        mesh = self._mesh(handle)
        if mesh.uv is None or mesh.atlas is None:
            raise ValueError("the mesh has no UVs or baseColor atlas; a textured render is impossible")
        face, _, _, bary = _raster(mesh, camera, size, None)
        out = np.zeros((*face.shape, 3), dtype=np.uint8)
        rows, cols = np.nonzero(face >= 0)
        if rows.size:
            tri = mesh.faces[face[rows, cols]]
            uv = (bary[rows, cols, :, None] * mesh.uv[tri]).sum(axis=1)
            height, width = mesh.atlas.shape[:2]
            px = np.clip((uv[:, 0] * width).astype(np.int64), 0, width - 1)
            py = np.clip((uv[:, 1] * height).astype(np.int64), 0, height - 1)
            out[rows, cols] = mesh.atlas[py, px]
        return out

    def render_labels(
        self, handle: MeshHandle, camera: CameraSpec, size: tuple[int, int], face_colors: np.ndarray
    ) -> np.ndarray:
        self._record("render_labels")
        mesh = self._mesh(handle)
        colors = np.asarray(face_colors, dtype=np.uint8)
        if colors.shape != (len(mesh.faces), 3):
            raise ValueError(f"face_colors must have shape ({len(mesh.faces)}, 3), got {colors.shape}")
        face = _raster(mesh, camera, size, None)[0]
        out = np.zeros((*face.shape, 3), dtype=np.uint8)
        covered = face >= 0
        out[covered] = colors[face[covered]]
        return out

    # ------------------------------------------------------------------ internals

    def _record(self, method: str) -> None:
        self.calls.append((method, threading.current_thread().name))
        error = self.fail.get(method)
        if error is not None:
            raise error

    def _mesh(self, handle: MeshHandle) -> _HostMesh:
        mesh = self._meshes.get(handle.handle_id)
        if mesh is None:
            raise ValueError(f"unknown or released mesh handle {handle.handle_id!r}")
        return mesh


def _raster(
    mesh: _HostMesh, camera: CameraSpec, size: tuple[int, int], crop: tuple[float, float, float, float] | None
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Numpy z-buffer rasteriser: ``(face_id, depth, |cos|, perspective-correct barycentrics [H, W, 3])``."""
    width, height = int(size[0]), int(size[1])
    clip, extrinsics = cameras.view_clip_matrix(camera, mesh.radius, (width, height), crop)
    homo = np.concatenate([mesh.positions, np.ones((len(mesh.positions), 1))], axis=1)
    clip_pos = homo @ clip.T
    w = clip_pos[:, 3]
    safe_w = np.where(w > 1e-9, w, 1.0)
    px = (clip_pos[:, 0] / safe_w + 1.0) * 0.5 * width
    py = (clip_pos[:, 1] / safe_w + 1.0) * 0.5 * height
    eye = -(extrinsics[:3, :3].T @ extrinsics[:3, 3])

    face_id = np.full((height, width), -1, dtype=np.int32)
    zbuf = np.full((height, width), np.inf)
    bary = np.zeros((height, width, 3))
    for f, (a, b, c) in enumerate(mesh.faces):
        if w[a] <= 1e-9 or w[b] <= 1e-9 or w[c] <= 1e-9:
            continue
        xs, ys = px[[a, b, c]], py[[a, b, c]]
        x_lo, x_hi = max(int(np.floor(xs.min() - 0.5)), 0), min(int(np.ceil(xs.max() - 0.5)), width - 1)
        y_lo, y_hi = max(int(np.floor(ys.min() - 0.5)), 0), min(int(np.ceil(ys.max() - 0.5)), height - 1)
        if x_lo > x_hi or y_lo > y_hi:
            continue
        area = (xs[1] - xs[0]) * (ys[2] - ys[0]) - (xs[2] - xs[0]) * (ys[1] - ys[0])
        if abs(area) < 1e-12:
            continue
        gx, gy = np.meshgrid(np.arange(x_lo, x_hi + 1) + 0.5, np.arange(y_lo, y_hi + 1) + 0.5)
        l0 = ((xs[1] - gx) * (ys[2] - gy) - (xs[2] - gx) * (ys[1] - gy)) / area
        l1 = ((xs[2] - gx) * (ys[0] - gy) - (xs[0] - gx) * (ys[2] - gy)) / area
        l2 = 1.0 - l0 - l1
        inside = (l0 >= 0) & (l1 >= 0) & (l2 >= 0)
        if not inside.any():
            continue
        inv = l0 / w[a] + l1 / w[b] + l2 / w[c]
        depth = 1.0 / inv
        region = (slice(y_lo, y_hi + 1), slice(x_lo, x_hi + 1))
        closer = inside & (depth < zbuf[region])
        if not closer.any():
            continue
        zbuf[region][closer] = depth[closer]
        face_id[region][closer] = f
        persp = np.stack([l0 / w[a], l1 / w[b], l2 / w[c]], axis=-1) / inv[..., None]
        bary[region][closer] = persp[closer]

    covered = face_id >= 0
    depth_out = np.where(covered, zbuf, 0.0).astype(np.float32)
    cos = np.zeros((height, width), dtype=np.float32)
    rows, cols = np.nonzero(covered)
    if rows.size:
        tri = mesh.faces[face_id[rows, cols]]
        world = (bary[rows, cols, :, None] * mesh.positions[tri]).sum(axis=1)
        view = eye - world
        view /= np.maximum(np.linalg.norm(view, axis=1, keepdims=True), 1e-12)
        cos[rows, cols] = np.abs((mesh.normals[face_id[rows, cols]] * view).sum(axis=1))
    return face_id, depth_out, cos, bary
