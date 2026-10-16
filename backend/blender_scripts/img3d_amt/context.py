"""Shared run state for all steps: ``TunerContext``, ``StepOutcome``, ``StepError`` and the fixed step table.

Step contract (DESIGN section 5)::

    def run(ctx: TunerContext) -> StepOutcome:
        obj = ctx.require_primary()            # the mesh being tuned (bpy.types.Object, type MESH)
        ctx.progress(0.5, "Filling holes")     # fraction WITHIN the step, mapped to overall progress
        ...
        if broken_beyond_repair:
            raise StepError("hole_fill_failed", "Could not fill 3 holes")   # fails the step
        return StepOutcome("done", "Filled 12 holes", {"holes_filled": 12})

* ``ctx.original`` is a hidden, untouched copy of the loaded (welded) mesh - deviation reference + "before" previews.
  Never modify it; ``ctx.original_bvh()`` gives a cached BVH of its surface.
* Cross-step data: ``ctx.store`` (plain dict) and mesh attributes prefixed ``amt_`` (removed before export).
  Keys written by the framework: ``source_frame`` (dict), ``analysis`` ({"before"|"after": {"metrics", "details"}}),
  ``load`` (load step details). Keys read by validation: ``original_visible_face_mask`` (bool (F_original,)
  array over ``ctx.original`` faces, set by the repair step) and ``pose_checks`` (list of check dicts, rig step).
* ``ctx.set_metric(key, before=..., after=...)`` contributes report metrics the analysis does not compute
  (e.g. ``hidden_faces``, ``inner_shell_faces``, ``pose_max_stretch``).
* Use ``logging.getLogger(__name__)`` or ``ctx.log/ctx.warn``; both become protocol ``log`` events.
* Orientation: +Z up, front = -Y, character's left = +X, TRELLIS models fit [-0.5, 0.5]^3
  (see ``ctx.store["source_frame"]``).
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import bpy
import numpy as np

from . import protocol
from .config import TunerConfig, TuningOptions

log = logging.getLogger(__name__)

STEP_OUTCOME_STATUSES = ("done", "warning", "skipped")


@dataclass
class StepOutcome:
    """What a step's ``run(ctx)`` returns.

    Attributes:
        status: ``"done"`` (success), ``"warning"`` (success with caveats - ``message`` becomes a run warning) or
            ``"skipped"`` (nothing to do / disabled by options).
        message: short human-readable summary shown in the UI step list.
        details: JSON-serializable dict (numbers, strings, lists, dicts; numpy scalars/arrays are converted).
    """

    status: str = "done"
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in STEP_OUTCOME_STATUSES:
            raise ValueError(f"StepOutcome.status must be one of {STEP_OUTCOME_STATUSES}, got {self.status!r}")
        if self.details is None:
            self.details = {}


class StepError(Exception):
    """Raise from a step to fail it with a machine-readable ``code`` (snake_case) and a user ``message``.

    Required steps (load, geometry_repair, export) abort the run (exit 4); optional steps are rolled back and
    the run continues with a warning.
    """

    exit_code = 4

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class InputError(StepError):
    """Input file missing/unreadable/unsupported or without mesh geometry (exit 3)."""

    exit_code = 3


@dataclass(frozen=True)
class StepSpec:
    """One of the 8 fixed pipeline steps (DESIGN section 3)."""

    id: str
    label: str
    weight: float
    required: bool
    """Failure aborts the run (otherwise: rollback + warning + continue)."""
    module: str | None
    """``img3d_amt.<module>`` exposing ``run(ctx)``; ``None`` for framework-internal steps."""
    rollback: bool = False
    """Snapshot the primary mesh before the step and restore it when the step fails."""


STEP_SPECS: tuple[StepSpec, ...] = (
    StepSpec("load", "Load mesh", 0.05, True, None),
    StepSpec("analyze", "Mesh analysis", 0.07, False, None),
    StepSpec("geometry_repair", "Geometry repair", 0.30, True, "repair"),
    StepSpec("face_refinement", "Face refinement", 0.15, False, "face", rollback=True),
    StepSpec("topology_tuning", "Topology tuning", 0.20, False, "topology", rollback=True),
    StepSpec("rig_optimization", "Rig optimization", 0.05, False, "rig", rollback=True),
    StepSpec("validation", "Validation", 0.13, False, "validation"),
    StepSpec("export", "Save results", 0.05, True, None),
)
STEP_IDS: tuple[str, ...] = tuple(s.id for s in STEP_SPECS)


# ----------------------------------------------------------------------------------------------------------------
# environment probes: memory + GPU
# ----------------------------------------------------------------------------------------------------------------


def memory_mb() -> dict[str, int]:
    """Process memory in MB: ``ws`` / ``peak_ws`` (working set) and ``private`` / ``peak_private`` (commit).

    Windows only (``K32GetProcessMemoryInfo``); elsewhere ``peak_ws`` from ``resource``; ``{}`` when unavailable.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class _PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
            k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
            pmc = _PMC()
            pmc.cb = ctypes.sizeof(_PMC)
            if not k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
                return {}
            mb = 1024 * 1024
            return {"ws": round(pmc.WorkingSetSize / mb), "peak_ws": round(pmc.PeakWorkingSetSize / mb),
                    "private": round(pmc.PagefileUsage / mb), "peak_private": round(pmc.PeakPagefileUsage / mb)}
        except (OSError, AttributeError, ValueError):
            return {}
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
        return {"peak_ws": round(peak / divisor)}
    except (ImportError, OSError, ValueError):
        return {}


_GPU_INFO: dict[str, Any] | None = None


def probe_gpu() -> dict[str, Any]:
    """Feature-detect GPU offscreen rendering (``import gpu``; ``gpu.init()`` in background mode).

    Returns (cached) ``{"available": bool, "renderer": str|None, "backend": str|None, "error": str|None}``.
    Blender 5.2 supports ``gpu.init()`` in ``--background``; older builds / headless machines report
    ``available=False`` and steps must fall back to CPU code paths.
    """
    global _GPU_INFO
    if _GPU_INFO is not None:
        return _GPU_INFO
    info: dict[str, Any] = {"available": False, "renderer": None, "backend": None, "error": None}
    try:
        import gpu

        if bpy.app.background:
            init = getattr(gpu, "init", None)
            if init is None:
                raise RuntimeError("gpu.init() is not available in this Blender build (background mode)")
            init()
        offscreen = gpu.types.GPUOffScreen(8, 8, format="RGBA8")
        offscreen.free()
        info["available"] = True
        try:
            info["renderer"] = gpu.platform.renderer_get()
            info["backend"] = gpu.platform.backend_type_get()
        except Exception:  # informational only
            pass
    except Exception as exc:  # any failure = no GPU path
        info["error"] = f"{type(exc).__name__}: {exc}"
    _GPU_INFO = info
    return info


# ----------------------------------------------------------------------------------------------------------------
# TunerContext
# ----------------------------------------------------------------------------------------------------------------

_UNSET = object()


class TunerContext:
    """Mutable state shared by the pipeline and all steps of one run."""

    PROGRESS_INTERVAL_S = 0.25

    def __init__(self, config: TunerConfig, *, temp_dir: str | None = None) -> None:
        self.config: TunerConfig = config
        self.options: TuningOptions = config.options
        self.store: dict[str, Any] = {}
        self.warnings: list[str] = []
        self.timings: dict[str, float] = {}
        self.metrics_before: dict[str, Any] = {}
        """Analysis metrics of the loaded mesh (metric key -> value), filled by the ``analyze`` step."""
        self.metrics_after: dict[str, Any] = {}
        """Analysis metrics of the tuned mesh, filled by the ``validation`` step."""
        self.metric_overrides: dict[str, dict[str, Any]] = {}
        self.mesh_objects: list[bpy.types.Object] = []
        """Result mesh objects (exported). ``primary`` is the one the steps tune."""
        self.armatures: list[bpy.types.Object] = []
        self.original: bpy.types.Object | None = None
        """Hidden snapshot object of the loaded mesh (never exported, never modified)."""
        self.input_info: dict[str, Any] = {}
        """``{"path", "format", "size_bytes"}`` (report ``input``)."""
        self.source_gltf: dict[str, Any] | None = None
        """Parsed glTF JSON of a .glb/.gltf input (export fidelity check)."""
        self.validation: dict[str, Any] | None = None
        """``{"passed": bool, "checks": [...]}`` set by the validation step."""
        self.bbox_min: np.ndarray = np.zeros(3)
        self.bbox_max: np.ndarray = np.zeros(3)
        self.started = time.perf_counter()
        self._primary: bpy.types.Object | None = None
        self._original_bvh = None
        self._original_geometry: tuple[np.ndarray, np.ndarray] | None = None
        self._step: StepSpec | None = None
        self._step_base = 0.0
        self._last_progress_t = 0.0
        self._last_progress_msg = ""
        self._peak_memory: dict[str, int] = {}
        self._owns_temp = temp_dir is None
        self.temp_dir = temp_dir or tempfile.mkdtemp(prefix="img3d_amt_")
        self.gpu: dict[str, Any] = dict(_GPU_INFO) if _GPU_INFO is not None else {}

    # ------------------------------------------------------------------ scene / objects

    @property
    def scene(self) -> bpy.types.Scene:
        return bpy.context.scene

    @property
    def primary(self) -> bpy.types.Object | None:
        """The mesh object being tuned (largest mesh by face count unless set explicitly)."""
        if self._primary is not None and _is_alive(self._primary):
            return self._primary
        alive = [o for o in self.mesh_objects if _is_alive(o)]
        self._primary = max(alive, key=lambda o: len(o.data.polygons)) if alive else None
        return self._primary

    def set_primary(self, obj: bpy.types.Object) -> None:
        """Make ``obj`` the tuned object (adds it to ``mesh_objects`` if needed)."""
        if obj not in self.mesh_objects:
            self.mesh_objects.append(obj)
        self._primary = obj

    def require_primary(self) -> bpy.types.Object:
        """Return the primary mesh object or raise ``StepError("no_mesh")``."""
        obj = self.primary
        if obj is None or obj.type != "MESH":
            raise StepError("no_mesh", "There is no mesh to process")
        return obj

    @property
    def mesh(self) -> bpy.types.Mesh:
        """``primary.data`` (the mesh datablock steps modify)."""
        return self.require_primary().data

    @property
    def has_armature(self) -> bool:
        return any(_is_alive(a) for a in self.armatures)

    # ------------------------------------------------------------------ bounds

    def update_bounds(self) -> float:
        """Recompute the primary's bbox from its vertex positions (object space == world for unrigged inputs).

        Returns the bbox diagonal. Call after a step changed the geometry substantially (the pipeline does it
        after every step).
        """
        obj = self.primary
        if obj is None or len(obj.data.vertices) == 0:
            self.bbox_min = np.zeros(3)
            self.bbox_max = np.zeros(3)
            return 0.0
        co = np.empty(len(obj.data.vertices) * 3, np.float32)
        obj.data.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        self.bbox_min = co.min(0).astype(np.float64)
        self.bbox_max = co.max(0).astype(np.float64)
        return self.diag

    @property
    def diag(self) -> float:
        """Bounding-box diagonal of the primary mesh (scale reference for all tolerances)."""
        return float(np.linalg.norm(self.bbox_max - self.bbox_min))

    @property
    def dimensions(self) -> np.ndarray:
        """(3,) float64 bbox size."""
        return self.bbox_max - self.bbox_min

    @property
    def center(self) -> np.ndarray:
        """(3,) float64 bbox centre."""
        return (self.bbox_min + self.bbox_max) * 0.5

    def original_geometry(self) -> tuple[np.ndarray, np.ndarray]:
        """Cached ``(positions (V, 3) float64, triangles (T, 3) int64)`` of the ORIGINAL (loaded, welded) mesh.

        For an all-triangle original (TRELLIS) triangle ``t`` == polygon ``t``, so per-face masks over the original
        (e.g. ``original_visible_face_mask``) index these rows directly.
        """
        if self._original_geometry is None:
            if self.original is None:
                raise StepError("no_original", "The original mesh snapshot is not available")
            from . import meshdata

            me = self.original.data
            tris = meshdata.get_triangles(me) if meshdata.is_all_triangles(me) else meshdata.get_loop_triangles(me)[0]
            self._original_geometry = (meshdata.get_positions(me), tris)
        return self._original_geometry

    def original_bvh(self):
        """Cached ``mathutils.bvhtree.BVHTree`` of the ORIGINAL surface (object space; face index = row of
        ``original_geometry()[1]``)."""
        if self._original_bvh is None:
            from . import meshdata

            co, tris = self.original_geometry()
            self._original_bvh = meshdata.bvh_from_arrays(co, tris)
        return self._original_bvh

    # ------------------------------------------------------------------ progress / logging

    def progress(self, fraction: float, message: str = "") -> None:
        """Report progress WITHIN the current step (0..1), e.g. ``ctx.progress(0.4, "Filling holes")``.

        Cheap to call often: throttled to ~4 events/s (a changed message may pass after 50 ms; 1.0 always passes).
        """
        if self._step is None:
            return
        fraction = min(max(float(fraction), 0.0), 1.0)
        now = time.perf_counter()
        elapsed = now - self._last_progress_t
        changed = message != self._last_progress_msg
        if fraction < 1.0 and elapsed < self.PROGRESS_INTERVAL_S and not (changed and elapsed >= 0.05):
            return
        self._last_progress_t = now
        self._last_progress_msg = message
        protocol.progress(self._step.id, fraction, self._step_base + self._step.weight * fraction, message)

    @property
    def overall_progress(self) -> float:
        return self._step_base

    def log(self, level: str, message: str) -> None:
        """Emit a protocol ``log`` event (``debug`` is dropped unless the run log level is debug)."""
        if level == "debug" and self.config.log_level != "debug":
            return
        protocol.log(level, message)

    def info(self, message: str) -> None:
        self.log("info", message)

    def debug(self, message: str) -> None:
        self.log("debug", message)

    def warn(self, message: str) -> None:
        """Record a run warning (-> ``completed_with_warnings``) and emit it as a ``log`` warning event."""
        if message not in self.warnings:
            self.warnings.append(message)
        protocol.log("warning", message)

    @contextlib.contextmanager
    def timer(self, name: str) -> Iterator[None]:
        """``with ctx.timer("weld"): ...`` records ``ctx.timings[name]`` (seconds, accumulated)."""
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.timings[name] = round(self.timings.get(name, 0.0) + time.perf_counter() - t0, 4)

    def memory(self) -> dict[str, int]:
        """Current process memory in MB (see :func:`memory_mb`); also tracks the run's peak."""
        mem = memory_mb()
        for key, value in mem.items():
            if value > self._peak_memory.get(key, -1):
                self._peak_memory[key] = value
        return mem

    @property
    def peak_memory(self) -> dict[str, int]:
        return dict(self._peak_memory)

    # ------------------------------------------------------------------ metrics

    def set_metric(self, key: str, *, before: Any = _UNSET, after: Any = _UNSET) -> None:
        """Contribute a report metric value (``before`` and/or ``after``) not produced by the analysis."""
        entry = self.metric_overrides.setdefault(key, {})
        if before is not _UNSET:
            entry["before"] = before
        if after is not _UNSET:
            entry["after"] = after

    # ------------------------------------------------------------------ GPU

    @property
    def gpu_available(self) -> bool:
        """True when GPU offscreen rendering works (probe runs once per process)."""
        if not self.gpu:
            self.gpu = dict(probe_gpu())
        return bool(self.gpu.get("available"))

    # ------------------------------------------------------------------ pipeline internals

    def begin_step(self, spec: StepSpec, base_progress: float) -> None:
        """(pipeline) mark ``spec`` as the running step; ``base_progress`` = overall progress at its start."""
        self._step = spec
        self._step_base = base_progress
        self._last_progress_t = 0.0
        self._last_progress_msg = ""

    def end_step(self) -> None:
        """(pipeline) leave the current step."""
        self._step = None

    @property
    def current_step(self) -> StepSpec | None:
        return self._step

    def invalidate_caches(self) -> None:
        """Forget the cached primary object (call after replacing/removing objects) and recompute the bounds."""
        self._primary = None
        if self.primary is not None:
            self.update_bounds()

    def cleanup(self) -> None:
        """Remove the run's temp dir (only when the context created it)."""
        if self._owns_temp and self.temp_dir and os.path.isdir(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)


def _is_alive(obj: Any) -> bool:
    """False when the Blender object behind ``obj`` was removed (StructRNA invalid)."""
    try:
        return obj is not None and bpy.data.objects.get(obj.name) == obj
    except ReferenceError:
        return False
