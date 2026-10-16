"""Per-triangle visibility from outside the model: GPU face-ID renders with a CPU ray-grid fallback.

The mesh is seen orthographically from ``N`` directions (Fibonacci sphere + the 6 axes), each view fitted to the
mesh bbox. Every view is rendered twice into an offscreen RGBA8 buffer with depth test, every triangle drawn with a
unique flat colour (``id + 1`` packed into RGB8, 16.7M ids), and read back exactly:

* pass A - no face culling: the first surface along every pixel ray;
* pass B - front faces culled: the first BACK-facing surface along every pixel ray.

Per triangle counters (``uint16``, number of views):

* ``front``  - first hit and facing the camera (seen from outside on its front side);
* ``back``   - first hit but facing away (its back side is seen: flipped faces, open sheets);
* ``behind`` - first back-facing surface lying within ``t_max`` (perpendicular) behind a front-facing first hit,
  i.e. the opposite-oriented wall directly under a visible wall. For a TRELLIS.2 hollow shell this is the inner wall
  (~2 voxels behind the outer surface); for a thin sheet it is the sheet's far side (which is also ``front``-visible
  from the other side - that is what keeps both walls of real sheets).

``t_max`` is estimated from the data (median perpendicular gap between a face-on front surface and the first
back-facing surface behind it, measured on 6 low-resolution axis views; capped at ``6 x mean edge length``).

GPU path: ``gpu.init()`` (Blender >= 5.2 in ``--background``) + ``GPUOffScreen`` + builtin ``FLAT_COLOR`` shader;
the un-indexed triangle batch is built once and reused for all views; ~0.1 s per view at 3072^2 for 1.9M triangles.

CPU fallback (no ``gpu.init``, GPU failure, or ``use_gpu=False``): the same views emulated with ``BVHTree.ray_cast``
on a coarse parallel-ray grid (budget ``CPU_RAY_BUDGET`` primary rays, ~8 us each), then the counts are dilated over
``CPU_DILATE_RINGS`` rings of edge-adjacent triangles to make up for the sparse sampling. Accuracy: triangles smaller
than the grid spacing are only classified through their neighbours, thin features narrower than ~2 grid cells can be
missed (reported as hidden), and counts are lower than the GPU ones (thresholds use ratios, so classification stays
comparable). Expect ~10-15 s for a 2M-triangle mesh.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

DEFAULT_VIEWS = 96
"""Fibonacci-sphere directions (the 6 axis directions are added on top)."""
CPU_VIEWS = 26
CPU_RAY_BUDGET = 1_200_000
CPU_DILATE_RINGS = 3
COS_MIN_BEHIND = 0.5
"""A front-facing first hit must face the camera at least this much (<= 60 deg) to vouch for the wall behind it."""
ANTI_PARALLEL_DOT = -0.7
"""The wall behind must be (nearly) anti-parallel to the front face (offset surface, not the far side of a bump)."""
PAIR_STRIDE = 2
"""The wall-pair analysis samples every 2nd pixel in x and y (faces cover ~9 px at the default resolution)."""
THICKNESS_COS_MIN = 0.5
THICKNESS_MAX_REL = 0.02
"""Gaps larger than this fraction of the bbox diagonal are other body parts, not the shell wall."""
T_MAX_FACTOR = 3.0
T_MAX_EDGE_CAP = 6.0
SHELL_MODE_MIN_FRACTION = 0.35
"""Fraction of face-on samples with a thin (<= 3 x median) gap needed to call the mesh a double-walled shell."""

ProgressFn = Callable[[float, str], None]


# ================================================================================================================
# results / directions / framing
# ================================================================================================================


@dataclass
class VisibilityResult:
    """Per-triangle view counters and run statistics (see module doc)."""

    front: np.ndarray
    back: np.ndarray
    behind: np.ndarray
    views: int
    method: str
    resolution: int
    t_max: float
    thickness: float | None
    shell_fraction: float
    """Fraction of face-on samples whose first back-facing surface is a thin wall behind them (0..1)."""
    timings: dict[str, float] = field(default_factory=dict)
    info: dict[str, Any] = field(default_factory=dict)
    partner: np.ndarray | None = None
    """(T,) int64 opposite wall of each triangle seen in a wall pair (front hit <-> wall behind it), -1 = none."""

    @property
    def any(self) -> np.ndarray:
        """Views in which the triangle is the first hit (either side), (T,) int64."""
        return self.front.astype(np.int64) + self.back.astype(np.int64)

    @property
    def is_shell(self) -> bool:
        """True when the data shows a hollow double-walled shell (TRELLIS remesh band)."""
        return self.thickness is not None and self.shell_fraction >= SHELL_MODE_MIN_FRACTION

    def summary(self) -> dict[str, Any]:
        n = len(self.front)
        any_vis = self.any
        return {
            "method": self.method, "views": self.views, "resolution": self.resolution, "triangles": n,
            "visible_front": int((self.front > 0).sum()), "visible_back_only": int(((self.front == 0) & (self.back > 0)).sum()),
            "hidden": int((any_vis == 0).sum()), "behind_marked": int((self.behind > 0).sum()),
            "thickness": None if self.thickness is None else round(float(self.thickness), 7),
            "t_max": round(float(self.t_max), 7), "shell_fraction": round(float(self.shell_fraction), 4),
            "timings_s": {k: round(v, 3) for k, v in self.timings.items()}, **self.info,
        }


def view_directions(count: int = DEFAULT_VIEWS, axes: bool = True) -> np.ndarray:
    """Unit directions FROM the model TO the camera: ``count`` Fibonacci-sphere points (+ the 6 axes), (N, 3)."""
    dirs = []
    if axes:
        dirs.extend([(1.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0),
                     (0.0, 0.0, -1.0)])
    if count > 0:
        k = np.arange(count, dtype=np.float64) + 0.5
        z = 1.0 - 2.0 * k / count
        rad = np.sqrt(np.maximum(0.0, 1.0 - z * z))
        phi = k * math.pi * (3.0 - math.sqrt(5.0))
        fib = np.stack([rad * np.cos(phi), rad * np.sin(phi), z], axis=1)
        dirs.extend(fib.tolist())
    out = np.asarray(dirs, dtype=np.float64).reshape(-1, 3)
    return out / np.linalg.norm(out, axis=1, keepdims=True)


def _basis(d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Right/up vectors of a camera looking along ``-d`` (eye space x=r, y=u, z=d, right-handed)."""
    up = np.array([0.0, 0.0, 1.0])
    if abs(float(d @ up)) > 0.95:
        up = np.array([0.0, 1.0, 0.0])
    r = np.cross(up, d)
    r /= np.linalg.norm(r)
    u = np.cross(d, r)
    return r, u


@dataclass
class ViewFrame:
    """Orthographic view fitted to a bbox: ``matrix`` maps world -> NDC (depth = nearest is smallest)."""

    direction: np.ndarray
    right: np.ndarray
    up: np.ndarray
    center: tuple[float, float, float]
    """(cx, cy, cz): projected centre along (right, up, direction)."""
    half_size: float
    """Half width of the square view in world units."""
    half_depth: float
    matrix: np.ndarray

    @property
    def depth_scale(self) -> float:
        """World distance along the view ray per unit of window depth (0..1)."""
        return 2.0 * self.half_depth

    def pixel_size(self, resolution: int) -> float:
        return 2.0 * self.half_size / resolution


def frame_view(bmin: np.ndarray, bmax: np.ndarray, direction: np.ndarray, pad: float = 1.02) -> ViewFrame:
    """Square orthographic frame of the bbox seen from ``direction`` (from model to camera)."""
    d = np.asarray(direction, dtype=np.float64)
    d = d / np.linalg.norm(d)
    r, u = _basis(d)
    corners = np.array([[x, y, z] for x in (bmin[0], bmax[0]) for y in (bmin[1], bmax[1]) for z in (bmin[2], bmax[2])])
    pr, pu, pd = corners @ r, corners @ u, corners @ d
    cx, cy, cz = 0.5 * (pr.max() + pr.min()), 0.5 * (pu.max() + pu.min()), 0.5 * (pd.max() + pd.min())
    half = 0.5 * max(pr.max() - pr.min(), pu.max() - pu.min()) * pad
    half = max(half, 1e-9)
    half_depth = max(0.5 * (pd.max() - pd.min()) * 1.05, 1e-9)
    m = np.eye(4)
    m[0, :3], m[0, 3] = r / half, -cx / half
    m[1, :3], m[1, 3] = u / half, -cy / half
    m[2, :3], m[2, 3] = -d / half_depth, cz / half_depth
    return ViewFrame(d, r, u, (float(cx), float(cy), float(cz)), float(half), float(half_depth), m)


def auto_resolution(n_triangles: int) -> int:
    """Render resolution: 2048 px up to 1M triangles, 3072 px above (keeps ~9 px per face-on triangle)."""
    return 3072 if n_triangles > 1_000_000 else 2048


# ================================================================================================================
# GPU renderer
# ================================================================================================================


class GpuUnavailable(RuntimeError):
    """GPU offscreen rendering cannot be used (no ``gpu.init``, no context, shader/batch failure)."""


def gpu_init() -> str:
    """Initialise the GPU module for background mode (idempotent). Returns the renderer name.

    Raises:
        GpuUnavailable: ``gpu.init`` missing (Blender < 5.2 background) or initialisation failed.
    """
    try:
        import bpy
        import gpu
    except ImportError as exc:  # pragma: no cover - always present inside Blender
        raise GpuUnavailable(f"gpu module unavailable: {exc}") from exc
    try:
        if bpy.app.background:
            init = getattr(gpu, "init", None)
            if init is None:
                raise GpuUnavailable("gpu.init() is not available in this Blender build (background mode)")
            init()
        return str(gpu.platform.renderer_get())
    except GpuUnavailable:
        raise
    except Exception as exc:
        raise GpuUnavailable(f"{type(exc).__name__}: {exc}") from exc


def _id_colors(n: int) -> np.ndarray:
    """RGBA float32 colour per triangle encoding ``id + 1`` in RGB8 (exact through an RGBA8 target)."""
    ids = np.arange(1, n + 1, dtype=np.int64)
    rgba = np.empty((n, 4), np.float32)
    rgba[:, 0] = (ids & 255).astype(np.float32)
    rgba[:, 1] = ((ids >> 8) & 255).astype(np.float32)
    rgba[:, 2] = ((ids >> 16) & 255).astype(np.float32)
    rgba[:, 3] = 255.0
    rgba *= np.float32(1.0 / 255.0)
    return rgba


class GpuIdRenderer:
    """Face-ID renders of a triangle soup (reusable batches, one offscreen buffer). Use as a context manager.

    Pixel value ``0`` = background, otherwise ``triangle index + 1``. Depth is the normalised window depth.
    """

    MAX_IDS = (1 << 24) - 2

    def __init__(self, co: np.ndarray, tris: np.ndarray, resolution: int) -> None:
        if len(tris) > self.MAX_IDS:
            raise GpuUnavailable(f"too many triangles for 24-bit ids ({len(tris):,})")
        gpu_init()
        import gpu
        from gpu_extras.batch import batch_for_shader

        self._gpu = gpu
        self._batch_for_shader = batch_for_shader
        self.resolution = int(resolution)
        self.co = np.ascontiguousarray(co, dtype=np.float32)
        self.tris = np.asarray(tris, dtype=np.int64)
        self.offscreen = None
        self.batches: dict[str, Any] = {}
        try:
            self.shader = gpu.shader.from_builtin("FLAT_COLOR")
            self.offscreen = gpu.types.GPUOffScreen(self.resolution, self.resolution, format="RGBA8")
            self.add_batch("all", None)
        except GpuUnavailable:
            self.free()
            raise
        except Exception as exc:
            self.free()
            raise GpuUnavailable(f"GPU setup failed: {type(exc).__name__}: {exc}") from exc

    def add_batch(self, name: str, tri_mask: np.ndarray | None) -> None:
        """Build a batch of the triangles selected by ``tri_mask`` (ids stay the GLOBAL triangle indices)."""
        if tri_mask is None:
            sel = self.tris
            colors = _id_colors(len(self.tris))
        else:
            idx = np.flatnonzero(tri_mask)
            sel = self.tris[idx]
            colors = _id_colors(len(self.tris))[idx]
        pos = np.ascontiguousarray(self.co[sel.reshape(-1)])
        col = np.ascontiguousarray(np.repeat(colors, 3, axis=0))
        self.batches[name] = self._batch_for_shader(self.shader, "TRIS", {"pos": pos, "color": col})

    def render(self, frame: ViewFrame, *, cull: str = "NONE", batch: str = "all") -> tuple[np.ndarray, np.ndarray]:
        """Render one view: ``(ids uint32 (R*R,) with 0 = empty / id+1, depth float32 (R*R,))``.

        ``cull``: ``"NONE"`` (first surface), ``"FRONT"`` (first back-facing surface) or ``"BACK"``.
        """
        gpu = self._gpu
        from mathutils import Matrix

        res = self.resolution
        with self.offscreen.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=(0.0, 0.0, 0.0, 0.0), depth=1.0)
            gpu.state.depth_test_set("LESS")
            gpu.state.depth_mask_set(True)
            gpu.state.face_culling_set(cull)
            gpu.state.blend_set("NONE")
            try:
                with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                    gpu.matrix.load_matrix(Matrix(frame.matrix.tolist()))
                    gpu.matrix.load_projection_matrix(Matrix.Identity(4))
                    self.batches[batch].draw(self.shader)
                color = fb.read_color(0, 0, res, res, 4, 0, "UBYTE")
                depth = fb.read_depth(0, 0, res, res)
            finally:
                gpu.state.face_culling_set("NONE")
                gpu.state.depth_test_set("NONE")
        ids = np.frombuffer(color, dtype=np.uint32) & np.uint32(0xFFFFFF)
        return ids, np.frombuffer(depth, dtype=np.float32).copy()

    def free(self) -> None:
        """Release the offscreen buffer and the batches (GPU memory)."""
        if self.offscreen is not None:
            try:
                self.offscreen.free()
            except Exception:  # already freed / context gone
                pass
            self.offscreen = None
        self.batches.clear()

    def __enter__(self) -> GpuIdRenderer:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.free()


# ================================================================================================================
# per-view accumulation (shared by GPU and CPU paths)
# ================================================================================================================


class _Accumulator:
    """Accumulates per-triangle front/back/behind view counts (+ wall-pair partners) from id images."""

    def __init__(self, normals: np.ndarray) -> None:
        n = len(normals)
        self.normals = np.ascontiguousarray(normals, dtype=np.float32)
        self.front = np.zeros(n, np.uint16)
        self.back = np.zeros(n, np.uint16)
        self.behind = np.zeros(n, np.uint16)
        self.partner = np.full(n, -1, np.int64)
        self._mark = np.zeros(n, dtype=bool)
        self.views = 0

    def _unique(self, ids: np.ndarray) -> np.ndarray:
        mark = self._mark
        mark[ids] = True
        nz = np.flatnonzero(mark)
        mark[nz] = False
        return nz

    def add_view(self, direction: np.ndarray, ids_a: np.ndarray, depth_a: np.ndarray | None,
                 ids_b: np.ndarray | None, depth_b: np.ndarray | None, depth_scale: float, t_max: float,
                 stride: int = 1, size: int | None = None) -> None:
        """Count one view. ``ids_*`` are id+1 images (0 = empty), flattened ``size x size``; depths in window
        units (scaled to world by ``depth_scale``). The wall-pair (``behind``) analysis uses every ``stride``-th
        pixel in both image directions."""
        self.views += 1
        fdot = self.normals @ np.asarray(direction, dtype=np.float32)
        hit = ids_a != 0
        seen = self._unique(ids_a[hit].astype(np.int64) - 1)
        facing = fdot[seen] > 0.0
        self.front[seen[facing]] += 1
        self.back[seen[~facing]] += 1
        if ids_b is None or depth_a is None or depth_b is None:
            return
        if stride > 1 and size:
            pick = (slice(None, None, stride), slice(None, None, stride))
            ids_a = ids_a.reshape(size, size)[pick].ravel()
            ids_b = ids_b.reshape(size, size)[pick].ravel()
            depth_a = depth_a.reshape(size, size)[pick].ravel()
            depth_b = depth_b.reshape(size, size)[pick].ravel()
        ia, ib = self._wall_pairs(fdot, ids_a, depth_a, ids_b, depth_b, depth_scale, t_max)
        self.behind[self._unique(ib)] += 1
        if len(ia):
            # one partner per triangle is enough (all candidates lie on the same opposite wall)
            self.partner[ib] = ia
            free = self.partner[ia] < 0
            self.partner[ia[free]] = ib[free]

    def _wall_pairs(self, fdot: np.ndarray, ids_a: np.ndarray, depth_a: np.ndarray, ids_b: np.ndarray,
                    depth_b: np.ndarray, depth_scale: float, t_max: float) -> tuple[np.ndarray, np.ndarray]:
        """``(front ids, wall ids)`` (with repeats): back-facing walls lying parallel and close behind a face-on
        front-facing first hit."""
        sel = (ids_a != 0) & (ids_b != 0) & (ids_b != ids_a)
        if not sel.any():
            empty = np.zeros(0, np.int64)
            return empty, empty
        ia = ids_a[sel].astype(np.int64) - 1
        ib = ids_b[sel].astype(np.int64) - 1
        cos_a = fdot[ia]
        gap = (depth_b[sel] - depth_a[sel]) * np.float32(depth_scale)
        ok = (cos_a > COS_MIN_BEHIND) & (gap * cos_a <= t_max) & (fdot[ib] < -0.5 * cos_a)
        ia, ib = ia[ok], ib[ok]
        # opposite wall = parallel offset surface: normals anti-parallel (rules out the exit facet of a bump)
        anti = np.einsum("ij,ij->i", self.normals[ia], self.normals[ib]) < ANTI_PARALLEL_DOT
        return ia[anti], ib[anti]


def _sample_gaps(normals: np.ndarray, fdot: np.ndarray, ids_a: np.ndarray, depth_a: np.ndarray, ids_b: np.ndarray,
                 depth_b: np.ndarray, depth_scale: float) -> tuple[np.ndarray, int]:
    """``(gaps, face_on)``: perpendicular gaps between face-on front-facing first hits and an anti-parallel
    back-facing wall behind them, and the number of face-on front-facing samples (with or without such a wall)."""
    hit = ids_a != 0
    ia_all = ids_a[hit].astype(np.int64) - 1
    face_on = int((fdot[ia_all] > THICKNESS_COS_MIN).sum())
    sel = hit & (ids_b != 0) & (ids_b != ids_a)
    ia = ids_a[sel].astype(np.int64) - 1
    ib = ids_b[sel].astype(np.int64) - 1
    cos_a = fdot[ia]
    gap = (depth_b[sel] - depth_a[sel]) * np.float32(depth_scale) * cos_a
    ok = cos_a > THICKNESS_COS_MIN
    ia, ib, gap = ia[ok], ib[ok], gap[ok]
    anti = np.einsum("ij,ij->i", normals[ia], normals[ib]) < ANTI_PARALLEL_DOT
    return gap[anti].astype(np.float64), face_on


def thickness_from_gaps(gaps: np.ndarray, face_on: int, diag: float, mean_edge: float
                        ) -> tuple[float | None, float, float]:
    """``(thickness or None, shell_fraction, t_max)`` from perpendicular wall-gap samples (see module doc).

    ``shell_fraction`` = share of the ``face_on`` front samples that have an anti-parallel wall within 3 x the
    median gap (TRELLIS shells: ~0.9; ordinary single-surface meshes: only their thin parts).
    """
    cap = T_MAX_EDGE_CAP * mean_edge if mean_edge > 0 else 0.01 * diag
    gaps = gaps[(gaps >= 0) & (gaps <= THICKNESS_MAX_REL * diag)]
    if len(gaps) < 100 or face_on <= 0:
        return None, 0.0, cap
    med = float(np.median(gaps))
    fraction = float((gaps <= 3.0 * med).sum()) / float(face_on) if med > 0 else 0.0
    t_max = min(max(T_MAX_FACTOR * med, 1e-7 * diag), cap)
    return med, min(fraction, 1.0), t_max


# ================================================================================================================
# main entry
# ================================================================================================================


def compute_visibility(co: np.ndarray, tris: np.ndarray, normals: np.ndarray, *, views: int = DEFAULT_VIEWS,
                       resolution: int | None = None, use_gpu: bool = True, t_max: float | None = None,
                       mean_edge: float | None = None, progress: ProgressFn | None = None,
                       cpu_ray_budget: int = CPU_RAY_BUDGET, renderer: GpuIdRenderer | None = None,
                       pairs: bool = True) -> VisibilityResult:
    """Per-triangle ``front``/``back``/``behind`` view counts of the triangle mesh ``(co, tris)``.

    Args:
        co: (V, 3) positions; tris: (T, 3) triangle vertex ids; normals: (T, 3) unit triangle normals (winding).
        views: Fibonacci directions (+6 axes); resolution: render size (default :func:`auto_resolution`).
        use_gpu: try the GPU path first (falls back to CPU on :class:`GpuUnavailable`).
        t_max: max perpendicular wall gap counted as ``behind`` (default: estimated from the data).
        mean_edge: mean edge length (caps ``t_max``); computed when omitted.
        progress: ``progress(fraction 0..1, message)``.
        renderer: an open :class:`GpuIdRenderer` of the same ``(co, tris)`` to reuse (not freed here).
        pairs: also run pass B (``behind`` + ``partner``); ``False`` = first-hit visibility only (~2x faster).
    """
    co = np.asarray(co, dtype=np.float64)
    tris = np.asarray(tris, dtype=np.int64)
    bmin, bmax = co.min(0), co.max(0)
    diag = float(np.linalg.norm(bmax - bmin))
    if mean_edge is None:
        e = co[tris[:, [1, 2, 0]]] - co[tris]
        mean_edge = float(np.linalg.norm(e.reshape(-1, 3), axis=1).mean()) if len(tris) else 0.0
    report = progress or (lambda f, m: None)
    if use_gpu or renderer is not None:
        try:
            if renderer is not None:
                return _gpu_visibility(renderer, normals, bmin, bmax, diag, mean_edge, views, t_max, report, pairs)
            with GpuIdRenderer(co, tris, resolution or auto_resolution(len(tris))) as own:
                return _gpu_visibility(own, normals, bmin, bmax, diag, mean_edge, views, t_max, report, pairs)
        except GpuUnavailable as exc:
            log.warning("GPU visibility unavailable (%s) - using the CPU ray-grid fallback", exc)
            result = _cpu_visibility(co, tris, normals, bmin, bmax, diag, mean_edge, t_max, report, cpu_ray_budget)
            result.info["gpu_error"] = str(exc)
            return result
    return _cpu_visibility(co, tris, normals, bmin, bmax, diag, mean_edge, t_max, report, cpu_ray_budget)


def _gpu_visibility(renderer: GpuIdRenderer, normals, bmin, bmax, diag, mean_edge, views, t_max, report,
                    pairs: bool = True) -> VisibilityResult:
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    dirs = view_directions(views)
    acc = _Accumulator(normals)
    resolution = renderer.resolution
    thickness, fraction = None, 0.0
    if t_max is None and pairs:
        thickness, fraction, t_max = _estimate_thickness_gpu(renderer, acc.normals, dirs[:6], bmin, bmax, diag,
                                                             mean_edge)
        timings["thickness"] = time.perf_counter() - t0
    if t_max is None:
        t_max = T_MAX_EDGE_CAP * mean_edge
    t1 = time.perf_counter()
    for k, d in enumerate(dirs):
        frame = frame_view(bmin, bmax, d)
        ids_a, depth_a = renderer.render(frame, cull="NONE")
        if pairs:
            ids_b, depth_b = renderer.render(frame, cull="FRONT")
            acc.add_view(d, ids_a, depth_a, ids_b, depth_b, frame.depth_scale, t_max, stride=PAIR_STRIDE,
                         size=resolution)
        else:
            acc.add_view(d, ids_a, None, None, None, frame.depth_scale, t_max)
        report((k + 1) / len(dirs), f"Visibility {k + 1}/{len(dirs)} views")
    timings["views"] = time.perf_counter() - t1
    timings["total"] = time.perf_counter() - t0
    pixel = frame_view(bmin, bmax, dirs[0]).pixel_size(resolution)
    return VisibilityResult(acc.front, acc.back, acc.behind, acc.views, "gpu", resolution, float(t_max), thickness,
                            fraction, timings, {"pixel_size": round(pixel, 7)}, partner=acc.partner if pairs else None)


def removal_check(renderer: GpuIdRenderer, keep_tri: np.ndarray, normals: np.ndarray, tolerance: float,
                  views: int = 8) -> dict[str, Any]:
    """Compare first-hit renders of ALL triangles vs the KEPT ones (6 axes + ``views`` Fibonacci directions).

    Returns pixel fractions (of the pixels covered before): ``removed_visible`` (the visible front-facing surface
    was deleted), ``silhouette_lost`` (no surface left at all) and ``receded`` (the visible surface moved back by
    more than ``tolerance`` world units), plus the worst per-view values.
    """
    co = renderer.co
    bmin, bmax = co.min(0).astype(np.float64), co.max(0).astype(np.float64)
    renderer.add_batch("kept", keep_tri)
    keep = np.asarray(keep_tri, dtype=bool)
    nrm = np.ascontiguousarray(normals, dtype=np.float32)
    covered = removed = lost = receded = 0
    worst = 0.0
    try:
        for d in view_directions(views):
            frame = frame_view(bmin, bmax, d)
            ids0, dep0 = renderer.render(frame, cull="NONE", batch="all")
            ids1, dep1 = renderer.render(frame, cull="NONE", batch="kept")
            cov0 = ids0 != 0
            n0 = int(cov0.sum())
            if n0 == 0:
                continue
            face0 = ids0[cov0].astype(np.int64) - 1
            gone = ~keep[face0] & ((nrm[face0] @ d.astype(np.float32)) > 0)
            cov1 = ids1[cov0] != 0
            moved = cov1 & ((dep1[cov0] - dep0[cov0]) * np.float32(frame.depth_scale) > tolerance)
            covered += n0
            removed += int(gone.sum())
            lost += int((~cov1).sum())
            receded += int(moved.sum())
            worst = max(worst, float(gone.sum()) / n0)
    finally:
        renderer.batches.pop("kept", None)
    denom = max(covered, 1)
    return {"pixels": covered, "removed_visible": removed / denom, "silhouette_lost": lost / denom,
            "receded": receded / denom, "worst_view_removed_visible": worst, "tolerance": tolerance}


def _estimate_thickness_gpu(renderer: GpuIdRenderer, normals: np.ndarray, dirs: np.ndarray, bmin, bmax, diag: float,
                            mean_edge: float) -> tuple[float | None, float, float]:
    gaps = []
    face_on = 0
    res = renderer.resolution
    for d in dirs:
        frame = frame_view(bmin, bmax, d)
        fdot = normals @ np.asarray(d, dtype=np.float32)
        ids_a, depth_a = renderer.render(frame, cull="NONE")
        ids_b, depth_b = renderer.render(frame, cull="FRONT")
        pick = (slice(None, None, 4), slice(None, None, 4))  # 1/16 of the pixels is plenty for a median
        g, n = _sample_gaps(normals, fdot, ids_a.reshape(res, res)[pick].ravel(),
                            depth_a.reshape(res, res)[pick].ravel(), ids_b.reshape(res, res)[pick].ravel(),
                            depth_b.reshape(res, res)[pick].ravel(), frame.depth_scale)
        gaps.append(g)
        face_on += n
    return thickness_from_gaps(np.concatenate(gaps) if gaps else np.zeros(0), face_on, diag, mean_edge)


# ================================================================================================================
# CPU fallback (BVH ray grid)
# ================================================================================================================


def _cpu_cast_view(cast, frame: ViewFrame, grid: int, fdot: np.ndarray, eps: float
                   ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Emulate passes A and B of one view with parallel BVH rays: ``(ids_a, dist_a, ids_b, dist_b)``."""
    from mathutils import Vector

    ids_a = np.zeros(grid * grid, np.uint32)
    ids_b = np.zeros(grid * grid, np.uint32)
    dep_a = np.zeros(grid * grid, np.float32)
    dep_b = np.zeros(grid * grid, np.float32)
    d = frame.direction
    cx, cy, cz = frame.center
    r, u = frame.right, frame.up
    span = (np.arange(grid) + 0.5) / grid * 2.0 - 1.0
    ray_dir = Vector((-d).tolist())
    max_dist = 2.0 * frame.half_depth
    for j, y in enumerate(span.tolist()):
        base = r * cx + u * (cy + y * frame.half_size) + d * (cz + frame.half_depth)
        row = base[None, :] + r[None, :] * (span[:, None] * frame.half_size)
        for i, o in enumerate(row.tolist()):
            loc, _n, idx, dist = cast(Vector(o), ray_dir, max_dist)
            if loc is None:
                continue
            p = j * grid + i
            ids_a[p] = idx + 1
            dep_a[p] = dist
            if fdot[idx] <= 0.0:
                continue
            # first back-facing surface behind the front-facing hit (skip front-facing layers in between)
            cur, travelled = loc, dist
            for _ in range(4):
                loc2, _n2, idx2, dist2 = cast(cur + ray_dir * eps, ray_dir, max_dist - travelled)
                if loc2 is None:
                    break
                travelled += dist2 + eps
                if fdot[idx2] < 0.0:
                    ids_b[p] = idx2 + 1
                    dep_b[p] = travelled
                    break
                cur = loc2
    return ids_a, dep_a, ids_b, dep_b


def _cpu_visibility(co, tris, normals, bmin, bmax, diag, mean_edge, t_max, report, budget) -> VisibilityResult:
    from mathutils.bvhtree import BVHTree

    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    bvh = BVHTree.FromPolygons(co.tolist(), tris.tolist(), all_triangles=True)
    timings["bvh"] = time.perf_counter() - t0
    dirs = view_directions(CPU_VIEWS - 6)
    grid = max(48, int(math.sqrt(max(budget, 1) / len(dirs))))
    nrm32 = np.ascontiguousarray(normals, dtype=np.float32)
    acc = _Accumulator(nrm32)
    eps = 1e-6 * diag
    thickness, fraction = None, 0.0
    t1 = time.perf_counter()
    pending = []  # the 6 axis views wait for the thickness estimate
    gaps, face_on = [], 0
    t_use = t_max
    for k, d in enumerate(dirs):
        frame = frame_view(bmin, bmax, d)
        fdot = nrm32 @ d.astype(np.float32)
        ids_a, dep_a, ids_b, dep_b = _cpu_cast_view(bvh.ray_cast, frame, grid, fdot, eps)
        if t_use is None:
            g, n = _sample_gaps(nrm32, fdot, ids_a, dep_a, ids_b, dep_b, 1.0)
            gaps.append(g)
            face_on += n
            pending.append((d, ids_a, dep_a, ids_b, dep_b))
            if k == 5 or k == len(dirs) - 1:
                thickness, fraction, t_use = thickness_from_gaps(np.concatenate(gaps), face_on, diag, mean_edge)
                for item in pending:
                    acc.add_view(item[0], *item[1:], 1.0, t_use)
                pending.clear()
        else:
            acc.add_view(d, ids_a, dep_a, ids_b, dep_b, 1.0, t_use)
        report(0.9 * (k + 1) / len(dirs), f"Visibility (CPU) {k + 1}/{len(dirs)} views")
    timings["views"] = time.perf_counter() - t1
    t1 = time.perf_counter()
    front, back, behind = dilate_counts(tris, len(co), [acc.front, acc.back, acc.behind], CPU_DILATE_RINGS)
    timings["dilate"] = time.perf_counter() - t1
    timings["total"] = time.perf_counter() - t0
    report(1.0, "Visibility (CPU) done")
    return VisibilityResult(front, back, behind, acc.views, "cpu", grid, float(t_use), thickness, fraction, timings,
                            {"grid_spacing": round(2.0 * frame_view(bmin, bmax, dirs[0]).half_size / grid, 7),
                             "primary_rays": int(grid * grid * len(dirs))}, partner=acc.partner)


# ================================================================================================================
# wall pairing by rays (hidden faces)
# ================================================================================================================


def build_bvh(co: np.ndarray, tris: np.ndarray):
    """``BVHTree`` over triangles (face index == row of ``tris``); ~1 s for 1.9M triangles."""
    from mathutils.bvhtree import BVHTree

    return BVHTree.FromPolygons(np.asarray(co, np.float64).tolist(), np.asarray(tris).tolist(), all_triangles=True)


def pair_by_rays(co: np.ndarray, tris: np.ndarray, normals: np.ndarray, faces: np.ndarray, t_max: float, *,
                 bvh=None, budget_s: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Opposite wall of each triangle in ``faces``: ray from ``centroid - eps * n`` along ``-n`` up to ``t_max``;
    the hit counts only when its normal is anti-parallel (dot < ``ANTI_PARALLEL_DOT``).

    Returns ``(partner (n,) int64 -1 = none, distance (n,) float64 inf = none)``. ~7 us per ray (BVHTree.ray_cast);
    ``budget_s`` stops early (remaining faces stay unpaired).
    """
    from mathutils import Vector

    faces = np.asarray(faces, dtype=np.int64)
    partner = np.full(len(faces), -1, np.int64)
    dist = np.full(len(faces), np.inf)
    if len(faces) == 0:
        return partner, dist
    if bvh is None:
        bvh = build_bvh(co, tris)
    diag = float(np.linalg.norm(co.max(0) - co.min(0)))
    eps = 1e-6 * diag
    cen = (co[tris[faces, 0]] + co[tris[faces, 1]] + co[tris[faces, 2]]) / 3.0
    nrm = np.asarray(normals, dtype=np.float64)
    origins = (cen - nrm[faces] * eps).tolist()
    dirs = (-nrm[faces]).tolist()
    cast = bvh.ray_cast
    t0 = time.perf_counter()
    hit_idx = np.full(len(faces), -1, np.int64)
    for i, (o, d) in enumerate(zip(origins, dirs)):
        if budget_s is not None and (i & 4095) == 0 and time.perf_counter() - t0 > budget_s:
            log.warning("pair_by_rays: time budget reached after %d of %d rays", i, len(faces))
            break
        loc, _n, idx, hd = cast(Vector(o), Vector(d), t_max)
        if loc is not None and idx is not None:
            hit_idx[i] = idx
            dist[i] = hd
    hit = hit_idx >= 0
    anti = np.zeros(len(faces), dtype=bool)
    anti[hit] = np.einsum("ij,ij->i", nrm[faces[hit]], nrm[hit_idx[hit]]) < ANTI_PARALLEL_DOT
    partner[anti] = hit_idx[anti]
    dist[~anti] = np.inf
    return partner, dist


def dilate_counts(tris: np.ndarray, n_verts: int, counts: list[np.ndarray], rings: int) -> list[np.ndarray]:
    """Max-dilate per-triangle counters over ``rings`` rings of edge-adjacent triangles (manifold edges only)."""
    from . import meshdata

    ef = meshdata.edge_faces_from_triangles(tris, n_verts)
    _, f1, f2, _, _ = ef.manifold_pairs()
    out = []
    for c in counts:
        cur = c.copy()
        for _ in range(rings):
            nxt = cur.copy()
            np.maximum.at(nxt, f1, cur[f2])
            np.maximum.at(nxt, f2, cur[f1])
            cur = nxt
        out.append(cur)
    return out
