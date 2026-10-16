"""nvdiffrast raster label lifter (part-aware DESIGN.md sections 4.6.4, 4.6.5 and 5.2; slice S7).

Catalog entry ``raster_lifter`` (provider key ``nvdiffrast_raster``): no weights, ``approval_required=False``. It
rasterises the base mesh to lift 2D labels onto faces:

* :meth:`RasterLifterProvider.fit_camera` - silhouette grid search of section 4.6.5 step 3: 256² silhouettes over the
  :class:`~inference.parts.base.CameraGrid` (756 candidates by default), each framed like the conditioning image
  (:func:`inference.parts.cameras.framing_crop`) and scored by IoU against the conditioning foreground; the top
  candidates are refined in 1° steps; the winner is re-measured at the conditioning size. A fit is accepted at
  ``iou >= grid.accept_iou``; otherwise the front camera (yaw 0, pitch 0, FOV 30) is returned with
  ``accepted=False`` and the best IoU found.
* :meth:`RasterLifterProvider.raster` - face-ID (2^24 decode rule), camera-space depth and ``|cos θ|`` buffers.
* :meth:`RasterLifterProvider.render_textured` / :meth:`RasterLifterProvider.render_labels` - unlit baseColor and
  per-face colour renders on black.

Mirror ambiguity: :meth:`fit_camera` sees only a silhouette, and a silhouette seen from behind is almost the mirror
image of the front one. The candidate set therefore always refines the best candidate of the opposite hemisphere too,
and when that candidate scores within :data:`MIRROR_TIE_IOU` of the winner, the one closer to the front view wins
(TRELLIS.2 reconstructs frontal input art in its canonical front pose).

Provider rules (section 5.6): torch and nvdiffrast are imported only inside functions; :meth:`load` runs on a CPU
thread and only imports them (no CUDA call); every other method runs on the single ``gpu-worker`` thread inside a
``submit_gpu_task`` callable and takes :attr:`lock`. No file is read or written; arrays in and out are numpy.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Final

import numpy as np

from core.feature_errors import ModelUnavailableError
from inference.base import CancellationToken
from inference.parts import cameras
from inference.parts.base import CameraFit, CameraGrid, CameraSpec, LabelLifter, MeshArrays, MeshHandle, RasterOut
from inference.providers.base import LoadedDevice, ModelSpec, ModelUiState, ProviderStatus, ReasonCode
from utils.timeutil import utcnow

if TYPE_CHECKING:  # pragma: no cover
    from config.settings import Settings

logger = logging.getLogger("img3d.parts.lifter")

FIT_METHOD: Final = "silhouette_grid_refine"
FIT_RESOLUTION: Final = 256
"""Silhouette size of the grid search (section 4.6.5 step 3)."""
FINAL_FIT_RESOLUTION: Final = 1024
"""Full-frame silhouette size used to frame the winning camera precisely."""
MIRROR_TIE_IOU: Final = 0.01
"""Opposite-hemisphere candidates within this IoU of the winner are a tie; the one closer to the front view wins."""
MAX_DECODABLE_FACES: Final = 889_192_446
"""nvdiffrast's float32 triangle ids are unique up to 889 192 447 (= faces + 1)."""
MAX_FACES_PER_DRAW: Final = 1 << 23
"""nvdiffrast's CUDA rasterizer holds at most 2^24 subtriangles per draw (``CR_MAXSUBTRIS_LOG2``); larger meshes are
drawn in range-mode chunks of this many faces and composited by depth. Chunked triangle ids are absolute, so ids above
2^24 come back bit-encoded and are decoded by the 2^24 rule (offsets are never added to the float ids)."""
_CLIP_BATCH_FLOATS: Final = 1 << 25
"""Upper bound of float32 values of the batched clip-space positions (128 MB)."""
_MAX_BATCH: Final = 16
"""Images per instanced draw (nvdiffrast sizes its per-image subtriangle buffers by the largest image, ~50 B/face)."""


@dataclass(eq=False, slots=True)
class _DeviceMesh:
    """Device copy of one uploaded mesh (torch tensors on the lifter's device)."""

    handle: MeshHandle
    radius: float
    positions: Any
    """float32 ``[V, 3]``."""
    positions_h: Any
    """float32 ``[V, 4]`` homogeneous positions."""
    faces: Any
    """int32 ``[F, 3]``."""
    normals: Any
    """float32 ``[F, 3]`` unit geometric normals (zero for degenerate faces)."""
    uv: Any | None = None
    texture: Any | None = None
    """float32 ``[1, H, W, 3]`` baseColor atlas in 0..1."""
    texture_mip: Any | None = None


@dataclass(slots=True)
class _Candidate:
    yaw: float
    pitch: float
    fov: float
    iou: float


@dataclass(slots=True)
class FitDiagnostics:
    """What the last :meth:`RasterLifterProvider.fit_camera` did (logged by smoke tests and the part-analysis run)."""

    coarse_count: int = 0
    refine_count: int = 0
    best: tuple[float, float, float, float] | None = None
    """``(yaw, pitch, fov, iou)`` of the winner of the search (before the final re-measure)."""
    mirror: tuple[float, float, float, float] | None = None
    """Best candidate of the opposite hemisphere."""
    mirror_tie_break: bool = False
    final_iou: float = 0.0
    timings: dict[str, float] = field(default_factory=dict)


class RasterLifterProvider(LabelLifter):
    """nvdiffrast implementation of :class:`~inference.parts.base.LabelLifter`.

    Constructed by the provider registry as ``RasterLifterProvider(spec, settings=settings, model_dir=None)``;
    ``settings.device`` selects the CUDA device (``device=`` overrides it). The lifter has no weights, so
    ``model_dir`` is accepted for the registry's uniform constructor and ignored.
    """

    def __init__(
        self,
        spec: ModelSpec,
        settings: Settings | None = None,
        *,
        model_dir: Path | None = None,
        device: str | None = None,
    ) -> None:
        super().__init__(spec)
        del model_dir  # no weights
        self._device_name = device or (settings.device if settings is not None else "cuda")
        self._status_lock = threading.Lock()
        self._state: ModelUiState = "installed"
        self._reason_code: ReasonCode = "ok"
        self._reason: str | None = None
        self._loaded_device: LoadedDevice | None = None
        self._last_used_at: datetime | None = None
        self._torch: ModuleType | None = None
        self._dr: ModuleType | None = None
        self._glctx: Any | None = None
        self._meshes: dict[str, _DeviceMesh] = {}
        self.last_fit: FitDiagnostics | None = None

    # ------------------------------------------------------------------ lifecycle

    def status(self) -> ProviderStatus:
        with self._status_lock:
            return ProviderStatus(
                model_id=self.model_id,
                state=self._state,
                reason_code=self._reason_code,
                reason=self._reason,
                revision=self.spec.revision,
                loaded_device=self._loaded_device,
                last_used_at=self._last_used_at,
            )

    def load(self, cancel: CancellationToken) -> None:
        """Import torch and nvdiffrast on a CPU thread (no weights; never touches CUDA)."""
        with self.lock:
            if self._torch is not None and self._dr is not None:
                return
            cancel.raise_if_cancelled()
            self._set_state("loading", "ok", None, None)
            try:
                import nvdiffrast.torch as dr
                import torch
            except Exception as exc:  # noqa: BLE001 - a broken CUDA DLL raises OSError
                reason = f"{type(exc).__name__}: {exc}"
                self._set_state("error", "error", reason, None)
                raise ModelUnavailableError(
                    f"The raster label lifter cannot import nvdiffrast ({reason}).",
                    model_id=self.model_id,
                    state="error",
                    reason_code="error",
                ) from exc
            self._torch, self._dr = torch, dr
            self._set_state("loaded", "ok", None, "cpu")

    def warm(self, cancel: CancellationToken) -> None:
        """Create the CUDA rasterizer context and run one tiny raster (GPU thread)."""
        with self.lock:
            self._ensure_context(cancel)
            torch, dr = self._modules()
            with torch.inference_mode():
                pos = torch.tensor(
                    [[[-0.5, -0.5, 0.0, 1.0], [0.5, -0.5, 0.0, 1.0], [0.0, 0.5, 0.0, 1.0]]],
                    dtype=torch.float32,
                    device=self._device(),
                )
                tri = torch.tensor([[0, 1, 2]], dtype=torch.int32, device=self._device())
                dr.rasterize(self._glctx, pos, tri, resolution=(8, 8), grad_db=False)
                torch.cuda.synchronize(self._device())

    def offload(self) -> None:
        """Drop every uploaded mesh and the rasterizer context, then release cached VRAM."""
        with self.lock:
            self._meshes.clear()
            self._glctx = None
            if self._torch is not None:
                try:
                    if self._torch.cuda.is_available():
                        self._torch.cuda.empty_cache()
                except Exception:  # noqa: BLE001 - releasing VRAM must never fail an offload
                    logger.debug("empty_cache failed during lifter offload", exc_info=True)
            with self._status_lock:
                if self._state in ("loaded", "loading"):
                    if self._torch is not None:
                        self._state, self._loaded_device = "loaded", "cpu"
                    else:
                        self._state, self._loaded_device = "installed", None

    def unload(self) -> None:
        with self.lock:
            self.offload()
            self._torch = None
            self._dr = None
            self._set_state("installed", "ok", None, None)

    # ------------------------------------------------------------------ meshes

    def upload(self, mesh: MeshArrays) -> MeshHandle:
        positions, faces, uv, atlas = _validate_mesh(mesh)
        radius = cameras.bounding_radius(positions)
        with self.lock:
            torch, _ = self._modules_or_load(CancellationToken())
            device = self._device()
            with torch.inference_mode():
                pos_t = torch.from_numpy(positions).to(device)
                tri_t = torch.from_numpy(faces).to(device)
                ones = torch.ones((pos_t.shape[0], 1), dtype=torch.float32, device=device)
                pos_h = torch.cat([pos_t, ones], dim=1).contiguous()
                v0, v1, v2 = (pos_t[tri_t[:, k].long()] for k in range(3))
                normals = torch.linalg.cross(v1 - v0, v2 - v0)
                lengths = normals.norm(dim=1, keepdim=True)
                normals = torch.where(lengths > 0, normals / lengths.clamp_min(1e-30), torch.zeros_like(normals))
                uv_t = torch.from_numpy(uv).to(device).contiguous() if uv is not None else None
                tex_t = None
                if atlas is not None:
                    tex_t = (torch.from_numpy(atlas).to(device).to(torch.float32) / 255.0).unsqueeze(0).contiguous()
            handle = MeshHandle(
                handle_id=f"mesh_{uuid.uuid4().hex[:12]}",
                vertex_count=int(positions.shape[0]),
                face_count=int(faces.shape[0]),
            )
            self._meshes[handle.handle_id] = _DeviceMesh(
                handle=handle,
                radius=radius,
                positions=pos_t,
                positions_h=pos_h,
                faces=tri_t,
                normals=normals.contiguous(),
                uv=uv_t,
                texture=tex_t,
            )
            self._touch()
            logger.info(
                "lifter: uploaded %s (%d vertices, %d faces, radius %.4f, atlas %s)",
                handle.handle_id,
                handle.vertex_count,
                handle.face_count,
                radius,
                None if atlas is None else f"{atlas.shape[1]}x{atlas.shape[0]}",
            )
            return handle

    def release(self, handle: MeshHandle) -> None:
        with self.lock:
            self._meshes.pop(handle.handle_id, None)

    # ------------------------------------------------------------------ camera fit

    def fit_camera(
        self, handle: MeshHandle, foreground: np.ndarray, grid: CameraGrid, cancel: CancellationToken
    ) -> CameraFit:
        fg = np.asarray(foreground, dtype=bool)
        if fg.ndim != 2:
            raise ValueError(f"foreground must be a 2D boolean mask, got shape {fg.shape}")
        fg_bbox = cameras.mask_bbox(fg)
        if fg_bbox is None:
            raise ValueError("the conditioning foreground is empty")
        if not grid.yaws or not grid.pitches or not grid.fovs:
            raise ValueError("the camera grid is empty")
        height, width = fg.shape
        with self.lock:
            torch, _ = self._modules_or_load(cancel)
            mesh = self._mesh(handle)
            diag = FitDiagnostics()
            started = time.perf_counter()
            with torch.inference_mode():
                fg_small, small_bbox = self._comparison_mask(fg)
                coarse = cameras.coarse_candidates(grid)
                coarse_ious = self._score(mesh, coarse, fg_small, small_bbox, cancel)
                scored = [_Candidate(y, p, f, float(i)) for (y, p, f), i in zip(coarse, coarse_ious, strict=True)]
                diag.coarse_count = len(coarse)
                diag.timings["coarse"] = time.perf_counter() - started

                seeds = _refine_seeds(scored, grid.refine_top_k)
                seen = {_key(c.yaw, c.pitch, c.fov) for c in scored}
                refine: list[tuple[float, float, float]] = []
                for seed in seeds:
                    for cand in cameras.refine_candidates(seed.yaw, seed.pitch, seed.fov, grid):
                        key = _key(*cand)
                        if key not in seen:
                            seen.add(key)
                            refine.append(cand)
                t_refine = time.perf_counter()
                if refine:
                    refine_ious = self._score(mesh, refine, fg_small, small_bbox, cancel)
                    scored.extend(
                        _Candidate(y, p, f, float(i)) for (y, p, f), i in zip(refine, refine_ious, strict=True)
                    )
                diag.refine_count = len(refine)
                diag.timings["refine"] = time.perf_counter() - t_refine

                best, mirror, tie_break = _select(scored)
                diag.best = (best.yaw, best.pitch, best.fov, best.iou)
                diag.mirror = None if mirror is None else (mirror.yaw, mirror.pitch, mirror.fov, mirror.iou)
                diag.mirror_tie_break = tie_break
                if tie_break and mirror is not None:
                    best = mirror

                cancel.raise_if_cancelled()
                t_final = time.perf_counter()
                camera = CameraSpec(best.yaw, best.pitch, best.fov, cameras.fit_distance(best.fov, mesh.radius))
                crop, iou = self._frame(mesh, camera, fg, fg_bbox)
                accepted = iou >= grid.accept_iou
                if not accepted:
                    yaw, pitch, fov = cameras.FRONT_CAMERA
                    camera = CameraSpec(yaw, pitch, fov, cameras.fit_distance(fov, mesh.radius))
                    crop, _ = self._frame(mesh, camera, fg, fg_bbox)
                diag.final_iou = iou
                diag.timings["final"] = time.perf_counter() - t_final
                diag.timings["total"] = time.perf_counter() - started
            self.last_fit = diag
            self._touch()
            logger.info(
                "lifter: camera fit yaw=%.1f pitch=%.1f fov=%.0f iou=%.4f accepted=%s (%d+%d candidates, %.2f s, "
                "mirror %s, tie-break %s, foreground %dx%d)",
                camera.yaw_deg, camera.pitch_deg, camera.fov_deg, iou, accepted, diag.coarse_count,
                diag.refine_count, diag.timings["total"], diag.mirror, tie_break, width, height,
            )
            return CameraFit(
                yaw_deg=camera.yaw_deg,
                pitch_deg=camera.pitch_deg,
                fov_deg=camera.fov_deg,
                distance=camera.distance,
                crop=tuple(float(v) for v in crop),  # type: ignore[arg-type]
                iou=float(min(1.0, max(0.0, iou))),
                accepted=bool(accepted),
                method=FIT_METHOD,
                candidates_tested=diag.coarse_count + diag.refine_count,
            )

    # ------------------------------------------------------------------ renders

    def raster(
        self,
        handle: MeshHandle,
        camera: CameraSpec,
        size: tuple[int, int],
        crop: tuple[float, float, float, float] | None,
    ) -> RasterOut:
        width, height = _size(size)
        with self.lock:
            torch, dr = self._modules_or_load(CancellationToken())
            mesh = self._mesh(handle)
            with torch.inference_mode():
                clip, extrinsics = cameras.view_clip_matrix(camera, mesh.radius, (width, height), crop)
                rast, _ = self._rasterize(mesh, [clip], (height, width))
                face = _decode_ids(torch, rast)[0]
                valid = face >= 0
                world, _ = dr.interpolate(mesh.positions.unsqueeze(0), rast, mesh.faces)
                world = world[0]
                rot = torch.as_tensor(extrinsics[:3, :3], dtype=torch.float32, device=world.device)
                trans = torch.as_tensor(extrinsics[:3, 3], dtype=torch.float32, device=world.device)
                depth = world @ rot[2] + trans[2]
                eye = -(rot.T @ trans)
                view = eye - world
                view = view / view.norm(dim=-1, keepdim=True).clamp_min(1e-12)
                normals = mesh.normals[face.clamp_min(0).long()]
                cos = (normals * view).sum(dim=-1).abs()
                depth = torch.where(valid, depth, torch.zeros_like(depth))
                cos = torch.where(valid, cos, torch.zeros_like(cos))
                out = RasterOut(
                    face_id=face.to(torch.int32).cpu().numpy(),
                    depth=depth.to(torch.float32).cpu().numpy(),
                    cos=cos.to(torch.float32).cpu().numpy(),
                )
            self._touch()
            return out

    def render_textured(self, handle: MeshHandle, camera: CameraSpec, size: tuple[int, int]) -> np.ndarray:
        width, height = _size(size)
        with self.lock:
            torch, dr = self._modules_or_load(CancellationToken())
            mesh = self._mesh(handle)
            if mesh.uv is None or mesh.texture is None:
                raise ValueError("the mesh has no UVs or baseColor atlas; a textured render is impossible")
            with torch.inference_mode():
                clip, _ = cameras.view_clip_matrix(camera, mesh.radius, (width, height), None)
                rast, rast_db, pos_clip = self._rasterize(mesh, [clip], (height, width), with_db=True)
                uv, uv_da = dr.interpolate(mesh.uv.unsqueeze(0), rast, mesh.faces, rast_db=rast_db, diff_attrs="all")
                if mesh.texture_mip is None:
                    mesh.texture_mip = dr.texture_construct_mip(mesh.texture)
                color = dr.texture(
                    mesh.texture,
                    uv,
                    uv_da,
                    mip=mesh.texture_mip,
                    filter_mode="linear-mipmap-linear",
                    boundary_mode="clamp",
                )
                covered = rast[..., 3:4] > 0
                color = torch.where(covered, color, torch.zeros_like(color)).contiguous()
                color = dr.antialias(color, rast, pos_clip, mesh.faces)
                image = (color[0].clamp(0.0, 1.0) * 255.0 + 0.5).to(torch.uint8).cpu().numpy()
            self._touch()
            return image

    def render_labels(
        self, handle: MeshHandle, camera: CameraSpec, size: tuple[int, int], face_colors: np.ndarray
    ) -> np.ndarray:
        width, height = _size(size)
        colors = np.asarray(face_colors)
        with self.lock:
            torch, _ = self._modules_or_load(CancellationToken())
            mesh = self._mesh(handle)
            if colors.shape != (mesh.handle.face_count, 3):
                raise ValueError(f"face_colors must have shape ({mesh.handle.face_count}, 3), got {colors.shape}")
            with torch.inference_mode():
                clip, _ = cameras.view_clip_matrix(camera, mesh.radius, (width, height), None)
                rast, _ = self._rasterize(mesh, [clip], (height, width))
                face = _decode_ids(torch, rast)[0]
                palette = torch.from_numpy(np.ascontiguousarray(colors, dtype=np.uint8)).to(face.device)
                image = palette[face.clamp_min(0).long()]
                image = torch.where((face >= 0).unsqueeze(-1), image, torch.zeros_like(image))
                out = image.cpu().numpy()
            self._touch()
            return out

    # ------------------------------------------------------------------ internals

    def _set_state(
        self, state: ModelUiState, reason_code: ReasonCode, reason: str | None, device: LoadedDevice | None
    ) -> None:
        with self._status_lock:
            self._state, self._reason_code, self._reason, self._loaded_device = state, reason_code, reason, device

    def _touch(self) -> None:
        with self._status_lock:
            self._last_used_at = utcnow()

    def _modules(self) -> tuple[ModuleType, ModuleType]:
        if self._torch is None or self._dr is None:
            raise RuntimeError("the raster label lifter is not loaded")
        return self._torch, self._dr

    def _modules_or_load(self, cancel: CancellationToken) -> tuple[ModuleType, ModuleType]:
        self._ensure_context(cancel)
        return self._modules()

    def _device(self) -> Any:
        torch, _ = self._modules()
        return torch.device(self._device_name)

    def _ensure_context(self, cancel: CancellationToken) -> None:
        if self._torch is None or self._dr is None:
            self.load(cancel)
        if self._glctx is not None:
            return
        torch, dr = self._modules()
        device = torch.device(self._device_name)
        if device.type != "cuda" or not torch.cuda.is_available():
            reason = f"nvdiffrast needs a CUDA device (configured device: {self._device_name!r})"
            self._set_state("error", "error", reason, None)
            raise ModelUnavailableError(
                f"The raster label lifter is unavailable: {reason}.",
                model_id=self.model_id,
                state="error",
                reason_code="error",
            )
        try:
            self._glctx = dr.RasterizeCudaContext(device=device)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            self._set_state("error", "error", reason, None)
            raise ModelUnavailableError(
                f"The raster label lifter could not create its CUDA context ({reason}).",
                model_id=self.model_id,
                state="error",
                reason_code="error",
            ) from exc
        self._set_state("loaded", "ok", None, "cuda")

    def _mesh(self, handle: MeshHandle) -> _DeviceMesh:
        mesh = self._meshes.get(handle.handle_id)
        if mesh is None:
            raise ValueError(f"unknown or released mesh handle {handle.handle_id!r}")
        return mesh

    def _rasterize(
        self, mesh: _DeviceMesh, clip_matrices: list[np.ndarray], resolution: tuple[int, int], *, with_db: bool = False
    ) -> tuple[Any, ...]:
        """Rasterize the mesh for a batch of world→clip matrices. ``resolution`` is (H, W).

        Meshes up to :data:`MAX_FACES_PER_DRAW` faces use one instanced draw per batch; larger meshes are drawn per
        camera in range mode (one layer per chunk) and the layers are composited by depth.
        """
        torch, dr = self._modules()
        mats = torch.as_tensor(np.stack(clip_matrices), dtype=torch.float32, device=mesh.positions_h.device)
        pos_clip = torch.matmul(mesh.positions_h.unsqueeze(0), mats.transpose(1, 2)).contiguous()
        face_count = mesh.handle.face_count
        if face_count <= MAX_FACES_PER_DRAW:
            rast, rast_db = dr.rasterize(self._glctx, pos_clip, mesh.faces, resolution=resolution, grad_db=False)
        else:
            starts = range(0, face_count, MAX_FACES_PER_DRAW)
            ranges = torch.tensor(
                [[start, min(MAX_FACES_PER_DRAW, face_count - start)] for start in starts], dtype=torch.int32
            )
            rasts, dbs = [], []
            for b in range(pos_clip.shape[0]):
                layers, layers_db = dr.rasterize(
                    self._glctx,
                    pos_clip[b].contiguous(),
                    mesh.faces,
                    resolution=resolution,
                    ranges=ranges,
                    grad_db=False,
                )
                merged, merged_db = _composite_layers(torch, layers, layers_db)
                rasts.append(merged)
                dbs.append(merged_db)
            rast, rast_db = torch.cat(rasts), torch.cat(dbs)
        if with_db:
            return rast, rast_db, pos_clip
        return rast, pos_clip

    def _comparison_mask(self, fg: np.ndarray) -> tuple[Any, tuple[float, float, float, float]]:
        """The foreground at the grid-search resolution (longer side :data:`FIT_RESOLUTION`) and its bbox."""
        torch, _ = self._modules()
        height, width = fg.shape
        scale = FIT_RESOLUTION / max(height, width)
        out_h, out_w = max(1, round(height * scale)), max(1, round(width * scale))
        fg_t = torch.from_numpy(fg.astype(np.float32)).to(self._device())[None, None]
        small = torch.nn.functional.interpolate(fg_t, size=(out_h, out_w), mode="area")[0, 0] >= 0.5
        bbox = cameras.mask_bbox(small.cpu().numpy())
        if bbox is None:  # a foreground thinner than one comparison pixel: keep every touched pixel
            small = torch.nn.functional.interpolate(fg_t, size=(out_h, out_w), mode="area")[0, 0] > 0.0
            bbox = cameras.mask_bbox(small.cpu().numpy())
        assert bbox is not None
        return small, bbox

    def _batch_size(self, vertex_count: int) -> int:
        return max(1, min(_MAX_BATCH, _CLIP_BATCH_FLOATS // max(1, 4 * vertex_count)))

    def _score(
        self,
        mesh: _DeviceMesh,
        candidates: list[tuple[float, float, float]],
        fg_small: Any,
        fg_bbox: tuple[float, float, float, float],
        cancel: CancellationToken,
    ) -> np.ndarray:
        """IoU of every candidate's framed 256² silhouette against the comparison foreground."""
        torch, _ = self._modules()
        functional = torch.nn.functional
        res = FIT_RESOLUTION
        out_h, out_w = fg_small.shape
        device = fg_small.device
        fx0, fy0, fx1, fy1 = fg_bbox
        f_side = max(fx1 - fx0, fy1 - fy0)
        fcx, fcy = (fx0 + fx1) * 0.5, (fy0 + fy1) * 0.5
        xs = torch.arange(out_w, dtype=torch.float32, device=device) + 0.5
        ys = torch.arange(out_h, dtype=torch.float32, device=device) + 0.5
        fg_b = fg_small.unsqueeze(0)
        batch = self._batch_size(mesh.handle.vertex_count)
        ious: list[Any] = []
        for start in range(0, len(candidates), batch):
            cancel.raise_if_cancelled()
            chunk = candidates[start : start + batch]
            mats = [
                cameras.view_clip_matrix(
                    CameraSpec(yaw, pitch, fov, cameras.fit_distance(fov, mesh.radius)), mesh.radius, (res, res)
                )[0]
                for yaw, pitch, fov in chunk
            ]
            rast, _ = self._rasterize(mesh, mats, (res, res))
            sil = rast[..., 3] > 0
            rows, cols = sil.any(dim=2), sil.any(dim=1)
            has = rows.any(dim=1)
            y0 = rows.to(torch.int32).argmax(dim=1).to(torch.float32)
            y1 = res - rows.flip(1).to(torch.int32).argmax(dim=1).to(torch.float32)
            x0 = cols.to(torch.int32).argmax(dim=1).to(torch.float32)
            x1 = res - cols.flip(1).to(torch.int32).argmax(dim=1).to(torch.float32)
            s_side = torch.maximum(x1 - x0, y1 - y0).clamp_min(1.0)
            k = f_side / s_side  # comparison pixels per render pixel
            xr = ((x0 + x1) * 0.5)[:, None] + (xs[None, :] - fcx) / k[:, None]
            yr = ((y0 + y1) * 0.5)[:, None] + (ys[None, :] - fcy) / k[:, None]
            n = len(chunk)
            sample_grid = torch.stack(
                [
                    (2.0 * xr / res - 1.0)[:, None, :].expand(n, out_h, out_w),
                    (2.0 * yr / res - 1.0)[:, :, None].expand(n, out_h, out_w),
                ],
                dim=-1,
            )
            framed = functional.grid_sample(
                sil.to(torch.float32).unsqueeze(1),
                sample_grid,
                mode="bilinear",
                padding_mode="zeros",
                align_corners=False,
            )[:, 0] >= 0.5
            inter = (framed & fg_b).sum(dim=(1, 2)).to(torch.float32)
            union = (framed | fg_b).sum(dim=(1, 2)).to(torch.float32).clamp_min(1.0)
            ious.append(torch.where(has, inter / union, torch.zeros_like(inter)))
            del rast, sil, sample_grid, framed
        return torch.cat(ious).cpu().numpy().astype(np.float64)

    def _silhouette(
        self, mesh: _DeviceMesh, camera: CameraSpec, size: tuple[int, int], crop: cameras.Crop | None
    ) -> np.ndarray:
        width, height = size
        clip, _ = cameras.view_clip_matrix(camera, mesh.radius, (width, height), crop)
        rast, _ = self._rasterize(mesh, [clip], (height, width))
        return (rast[0, ..., 3] > 0).cpu().numpy()

    def _frame(
        self,
        mesh: _DeviceMesh,
        camera: CameraSpec,
        fg: np.ndarray,
        fg_bbox: tuple[float, float, float, float],
    ) -> tuple[cameras.Crop, float]:
        """Crop that frames ``camera``'s silhouette like the conditioning image, and the IoU at full size."""
        height, width = fg.shape
        full = self._silhouette(mesh, camera, (FINAL_FIT_RESOLUTION, FINAL_FIT_RESOLUTION), None)
        bbox = cameras.mask_bbox(full)
        if bbox is None:
            return cameras.full_frame_crop((width, height)), 0.0
        norm_bbox = tuple(v / FINAL_FIT_RESOLUTION for v in bbox)
        crop = cameras.framing_crop(norm_bbox, fg_bbox, (width, height))  # type: ignore[arg-type]
        framed = self._silhouette(mesh, camera, (width, height), crop)
        return crop, cameras.silhouette_iou(framed, fg)


# --------------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------------


def _decode_ids(torch: ModuleType, rast: Any) -> Any:
    """Face index (int32, -1 = background) from the rasterizer's ``triangle_id + 1`` channel (2^24 rule)."""
    values = rast[..., 3].contiguous()
    plain = values.to(torch.int32)
    biased = values.view(torch.int32) - 0x4A800000
    return torch.where(values <= 16777216.0, plain, biased) - 1


def _composite_layers(torch: ModuleType, layers: Any, layers_db: Any) -> tuple[Any, Any]:
    """Merge range-mode layers ``[n, H, W, 4]`` into one ``[1, H, W, 4]`` raster: the nearest covered layer wins."""
    covered = layers[..., 3] > 0
    depth = torch.where(covered, layers[..., 2], torch.full_like(layers[..., 2], float("inf")))
    nearest = depth.argmin(dim=0)  # [H, W]
    index = nearest[None, :, :, None].expand(1, *nearest.shape, layers.shape[-1])
    return torch.gather(layers, 0, index), torch.gather(layers_db, 0, index)


def _key(yaw: float, pitch: float, fov: float) -> tuple[float, float, float]:
    return (round(cameras.wrap_yaw(yaw), 6) % 360.0, round(pitch, 6), round(fov, 6))


def _refine_seeds(scored: list[_Candidate], top_k: int) -> list[_Candidate]:
    """The ``top_k`` best candidates, always including the best one of the opposite hemisphere of the winner."""
    if top_k <= 0 or not scored:
        return []
    ranked = sorted(scored, key=lambda c: -c.iou)
    best = ranked[0]
    mirror = next((c for c in ranked if cameras.yaw_difference(c.yaw, best.yaw) > 90.0), None)
    seeds = ranked[:top_k]
    if top_k >= 2 and mirror is not None and mirror not in seeds:
        seeds = ranked[: top_k - 1] + [mirror]
    return seeds


def _select(scored: list[_Candidate]) -> tuple[_Candidate, _Candidate | None, bool]:
    """``(winner, best opposite-hemisphere candidate, tie-break applies)``."""
    best = max(scored, key=lambda c: c.iou)
    opposite = [c for c in scored if cameras.yaw_difference(c.yaw, best.yaw) > 90.0]
    mirror = max(opposite, key=lambda c: c.iou) if opposite else None
    tie_break = bool(
        mirror is not None
        and mirror.iou >= best.iou - MIRROR_TIE_IOU
        and cameras.angle_from_front(mirror.yaw, mirror.pitch) < cameras.angle_from_front(best.yaw, best.pitch)
    )
    return best, mirror, tie_break


def _size(size: tuple[int, int]) -> tuple[int, int]:
    width, height = int(size[0]), int(size[1])
    if width <= 0 or height <= 0 or width > 16384 or height > 16384:
        raise ValueError(f"invalid render size {size}")
    return width, height


def _validate_mesh(
    mesh: MeshArrays,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, np.ndarray | None]:
    positions = np.ascontiguousarray(mesh.positions, dtype=np.float32)
    if positions.ndim != 2 or positions.shape[1] != 3 or positions.shape[0] == 0:
        raise ValueError(f"positions must be float32 [V, 3], got {positions.shape}")
    if not np.isfinite(positions).all():
        raise ValueError("positions contain non-finite values")
    faces_in = np.asarray(mesh.faces)
    if faces_in.ndim != 2 or faces_in.shape[1] != 3 or faces_in.shape[0] == 0:
        raise ValueError(f"faces must be [F, 3], got {faces_in.shape}")
    if faces_in.shape[0] > MAX_DECODABLE_FACES:
        raise ValueError(f"{faces_in.shape[0]} faces exceed nvdiffrast's decodable triangle id range")
    if int(faces_in.min()) < 0 or int(faces_in.max()) >= positions.shape[0]:
        raise ValueError("faces reference vertices out of range")
    faces = np.ascontiguousarray(faces_in, dtype=np.int32)
    uv = None
    if mesh.uv is not None:
        uv = np.ascontiguousarray(mesh.uv, dtype=np.float32)
        if uv.shape != (positions.shape[0], 2):
            raise ValueError(f"uv must be float32 [V, 2], got {uv.shape}")
    atlas = None
    if mesh.atlas_rgb is not None:
        atlas = np.ascontiguousarray(mesh.atlas_rgb)
        if atlas.dtype != np.uint8 or atlas.ndim != 3 or atlas.shape[2] != 3:
            raise ValueError(f"atlas_rgb must be uint8 [H, W, 3], got {atlas.dtype} {atlas.shape}")
    # torch.from_numpy needs writable buffers (decoded images and GLB accessor views are often read-only)
    positions, faces, uv, atlas = (
        None if arr is None else (arr if arr.flags.writeable else arr.copy()) for arr in (positions, faces, uv, atlas)
    )
    return positions, faces, uv, atlas  # type: ignore[return-value]


__all__ = ["FIT_METHOD", "FitDiagnostics", "RasterLifterProvider"]
