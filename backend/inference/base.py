"""Inference engine abstraction.

This module is the seam between the application (API, services, workers, pipelines) and any concrete 3D
generation backend. Nothing outside ``inference/<engine>/`` may import model-specific code (torch, trellis2,
o_voxel, ...). Engines are created through :func:`inference.registry.create_engine`.

Threading model
---------------
``load()`` and ``generate()`` are blocking and are always invoked from the single GPU worker thread owned by
:class:`workers.generation_worker.GenerationWorker`. ``status()`` and ``capabilities()`` must be cheap,
non-blocking and thread-safe because the API reads them from the event loop at any time.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from core.exceptions import JobCancelledError
from models.enums import EngineState, JobStage, ReferenceMode, ReferenceSlot


# --------------------------------------------------------------------------------------------------
# Capabilities & status
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PolygonTargetRange:
    min: int = 5_000
    max: int = 2_000_000
    default: int = 500_000


@dataclass(frozen=True, slots=True)
class EngineCapabilities:
    """Static description of what an engine supports. Used for request validation and by the UI."""

    resolutions: tuple[int, ...] = (512, 1024, 1536)
    texture_sizes: tuple[int, ...] = (512, 1024, 2048, 4096)
    polygon_target: PolygonTargetRange = field(default_factory=PolygonTargetRange)
    max_reference_images: int = 1
    reference_modes: tuple[ReferenceMode, ...] = (ReferenceMode.PRIMARY_ONLY,)
    supports_cancel: bool = True
    supports_texture: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolutions": list(self.resolutions),
            "texture_sizes": list(self.texture_sizes),
            "polygon_target": asdict(self.polygon_target),
            "max_reference_images": self.max_reference_images,
            "reference_modes": [str(m) for m in self.reference_modes],
            "supports_cancel": self.supports_cancel,
            "supports_texture": self.supports_texture,
        }


@dataclass(frozen=True, slots=True)
class LoadProgress:
    loaded: int
    total: int
    current: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"loaded": self.loaded, "total": self.total, "current": self.current}


@dataclass(frozen=True, slots=True)
class EngineStatus:
    """Snapshot of an engine's lifecycle state. Immutable; engines replace it on every transition."""

    state: EngineState
    message: str | None = None
    load_progress: LoadProgress | None = None
    error: str | None = None
    loaded_at: datetime | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        return self.state is EngineState.READY

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": str(self.state),
            "message": self.message,
            "load_progress": self.load_progress.to_dict() if self.load_progress else None,
            "error": self.error,
            "loaded_at": _iso(self.loaded_at),
            "details": dict(self.details),
        }


# --------------------------------------------------------------------------------------------------
# Requests & outputs
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SamplerOverrides:
    """Optional per-sampler overrides. ``None`` means "use the engine/model default"."""

    steps: int | None = None
    guidance_strength: float | None = None
    guidance_rescale: float | None = None
    rescale_t: float | None = None

    def as_kwargs(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass(frozen=True, slots=True)
class GenerationParams:
    resolution: int = 1024
    seed: int = 0
    polygon_target: int = 500_000
    texture_size: int = 2048
    remove_background: bool = True
    sparse_structure: SamplerOverrides = field(default_factory=SamplerOverrides)
    shape: SamplerOverrides = field(default_factory=SamplerOverrides)
    texture: SamplerOverrides = field(default_factory=SamplerOverrides)
    glb_webp_textures: bool = True


@dataclass(frozen=True, slots=True)
class ConditioningImage:
    """A reference image handed to the engine. ``path`` points to the job-local copy of the file."""

    image_id: str
    slot: ReferenceSlot
    path: Path
    is_primary: bool = False


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    job_id: str
    images: Sequence[ConditioningImage]
    """Images selected by the reference strategy, primary first. Single-image engines use ``images[0]``."""

    all_references: Sequence[ConditioningImage]
    """Every reference of the job (all slots), for engines that support multi-reference conditioning."""

    params: GenerationParams
    output_dir: Path
    reference_mode: ReferenceMode = ReferenceMode.PRIMARY_ONLY

    @property
    def primary(self) -> ConditioningImage:
        if not self.images:
            raise ValueError("GenerationRequest has no conditioning images")
        return self.images[0]


@dataclass(slots=True)
class GenerationOutput:
    glb_path: Path
    preview_path: Path | None
    effective_resolution: int
    timings: dict[str, float] = field(default_factory=dict)
    """Seconds per stage (keys are ``JobStage`` values) plus ``"total"``."""

    mesh_stats: dict[str, Any] = field(default_factory=dict)
    """Engine-side statistics (e.g. raw vertex/face counts before export). Optional."""

    metadata: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------------------------------
# Progress & cancellation
# --------------------------------------------------------------------------------------------------


@runtime_checkable
class ProgressReporter(Protocol):
    """Receives progress from the engine. Implementations must be thread-safe and cheap."""

    def stage(self, stage: JobStage, message: str | None = None, total_steps: int | None = None) -> None:
        """Enter a new stage. ``total_steps`` enables fine-grained percent inside the stage."""

    def step(self, step: int, total_steps: int | None = None, message: str | None = None) -> None:
        """Report progress inside the current stage (``step`` is 1-based, ``step <= total_steps``)."""


class NullProgressReporter:
    """Reporter that ignores everything (useful for tests and warm-up runs)."""

    def stage(self, stage: JobStage, message: str | None = None, total_steps: int | None = None) -> None:
        return None

    def step(self, step: int, total_steps: int | None = None, message: str | None = None) -> None:
        return None


class CancellationToken:
    """Thread-safe cooperative cancellation flag."""

    __slots__ = ("_event", "_reason")

    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason: str | None = None

    def cancel(self, reason: str | None = None) -> None:
        self._reason = reason
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise JobCancelledError(self._reason or "Job was cancelled")


# --------------------------------------------------------------------------------------------------
# Engine interface
# --------------------------------------------------------------------------------------------------

StatusCallback = Callable[[EngineStatus], None]


class InferenceEngine(ABC):
    """Abstract 3D generation engine."""

    name: str = "engine"

    @abstractmethod
    def status(self) -> EngineStatus:
        """Current lifecycle status (non-blocking, thread-safe)."""

    @abstractmethod
    def capabilities(self) -> EngineCapabilities:
        """Static capabilities (non-blocking)."""

    @abstractmethod
    def load(self, on_status: StatusCallback | None = None) -> None:
        """Load model weights (blocking, idempotent). Call ``on_status`` on every status transition.

        Must leave the engine in ``READY`` on success, ``ERROR`` (with ``error`` set) on failure and
        ``UNAVAILABLE`` if the runtime (torch / CUDA / native extensions / weights) is missing. Must not raise
        for expected failures; the status carries the error.
        """

    @abstractmethod
    def unload(self) -> None:
        """Release models and GPU memory (blocking, idempotent)."""

    @abstractmethod
    def generate(
        self,
        request: GenerationRequest,
        reporter: ProgressReporter,
        cancel: CancellationToken,
    ) -> GenerationOutput:
        """Run generation (blocking). Raises ``JobCancelledError`` on cancellation, ``EngineUnavailableError``
        if not ready, ``EngineError`` for failures. Must release transient GPU memory before returning/raising."""

    def device_info(self) -> dict[str, Any] | None:
        """Optional device description for ``/system`` (non-blocking). ``None`` if unknown."""
        return None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    text = value.isoformat()
    return text.replace("+00:00", "Z")
