"""Torch-free provider seam (part-aware DESIGN.md section 5.1). Frozen in slice S1.

Threading model
---------------
``ModelProvider.load()`` runs on a CPU thread: it fully materialises the weights in CPU RAM and never touches CUDA.
``warm()``, ``offload()``, ``unload()`` and the role methods (``generate``, ``detect``, ``refine``, ``raster``, ...) run
only on the single ``gpu-worker`` thread, inside a callable submitted through ``GenerationWorker.submit_gpu_task`` and
wrapped in ``torch_globals_guard()``. One re-entrant lock per provider (:attr:`ModelProvider.lock`) serialises all of
them, so a ``POST /models/{id}/load`` warm-up and a run never load the same model twice. ``status()`` must be cheap,
thread-safe and never block on the GPU.

Only files and plain dataclasses cross this seam, never tensors.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal, Protocol, get_args

from inference.base import CancellationToken

ModelKind = Literal[
    "text_to_image", "segmentation", "part_extraction", "segmentation_3d", "completion", "part_generation"
]
"""The last three are future seams: catalog entries only, reported ``unsupported`` in this milestone."""

CommercialUse = Literal[
    "allowed", "allowed_with_conditions", "revenue_capped", "non_commercial", "research_only", "unknown"
]

ModelUiState = Literal["not_installed", "installed", "loading", "loaded", "error", "unavailable"]

ReasonCode = Literal[
    "ok",
    "unsupported",
    "disabled",
    "not_approved",
    "license_blocked",
    "vendored_source_missing",
    "missing_dependency",
    "not_installed",
    "incompatible",
    "insufficient_vram",
    "error",
    "not_implemented",
    "engine_unavailable",
]
"""One vocabulary for every ``code`` in ``/features`` (pipeline, profiles, strategies, nodes) and
``ModelOut.reason_code``. ``not_implemented``: a reserved strategy or an executor module that is not merged yet;
``engine_unavailable``: image-to-3D gating."""

ProviderRole = Literal["generator", "detector", "grounder", "refiner", "parser", "lifter", "segmenter", "completer"]
"""Roles of catalog v1 plus the future 3D roles (``segmenter``, ``completer``)."""

LoadedDevice = Literal["cpu", "cuda"]

MODEL_KINDS: Final[tuple[str, ...]] = get_args(ModelKind)
MODEL_UI_STATES: Final[tuple[str, ...]] = get_args(ModelUiState)
REASON_CODES: Final[tuple[str, ...]] = get_args(ReasonCode)
COMMERCIAL_USES: Final[tuple[str, ...]] = get_args(CommercialUse)
USABLE_STATES: Final[frozenset[str]] = frozenset({"installed", "loading", "loaded"})
"""``registry.usable(id)`` is ``status(id).state in USABLE_STATES``."""
RESTRICTED_COMMERCIAL_USES: Final[frozenset[str]] = frozenset({"non_commercial", "research_only", "unknown"})
"""Licences that need ``allow_restricted_licenses`` when the model requires approval (registry rule 4)."""
MODEL_ID_PATTERN: Final = r"^[a-z0-9_]{2,40}$"


@dataclass(frozen=True, slots=True)
class LicenseInfo:
    """Licence metadata shown by ``/models`` and recorded in manifests (``id`` is an SPDX-like key)."""

    id: str
    name: str
    url: str
    commercial: CommercialUse
    gated: bool = False
    requires_acceptance: bool = False
    attribution: str | None = None
    conditions: tuple[str, ...] = ()
    """Use conditions the UI must show (e.g. the Sapiens2 licence restrictions)."""


@dataclass(frozen=True, slots=True)
class DependencySpec:
    """An installed distribution a provider needs. Checked with ``importlib.metadata`` + ``packaging.specifiers``;
    the package itself is never imported by the check."""

    distribution: str
    """``importlib.metadata`` name, e.g. ``diffusers``."""
    requirement: str
    """Version specifier, e.g. ``==0.39.0``."""


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Static catalog entry of one model (``inference/providers/catalog.py``)."""

    id: str
    """``^[a-z0-9_]{2,40}$``."""
    kind: ModelKind
    roles: tuple[str, ...]
    """``generator | detector | grounder | refiner | parser | lifter`` or a future role."""
    display_name: str
    provider: str | None
    """Implementation key, resolved lazily by the registry; ``None`` = unsupported in this build."""
    repo_id: str | None
    revision: str | None
    """Pinned revision; segmentation revisions are read from ``backend/.model/seg/download_manifest.json``."""
    local_subdir: str | None
    """Relative to ``settings.aux_model_root`` (``backend/.model``)."""
    required_files: tuple[str, ...]
    size_bytes: int | None
    vram_peak_gb: float
    ram_gb: float
    cold_load_s: float | None
    """Measured where known."""
    license: LicenseInfo
    dependencies: tuple[DependencySpec, ...] = ()
    vendored_source: str | None = None
    """e.g. ``third_party/see-through`` (relative to ``backend/``)."""
    approval_required: bool = True
    default_approved: bool = False
    """``True`` for the six ``DECISIONS.md`` models (Q1)."""
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    unsupported_reason: str | None = None
    hidden_by_default: bool = False
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    """Resolved state of one model (registry rules of section 5.3)."""

    model_id: str
    state: ModelUiState
    reason_code: ReasonCode
    reason: str | None
    revision: str | None = None
    loaded_device: LoadedDevice | None = None
    last_used_at: datetime | None = None

    @property
    def usable(self) -> bool:
        return self.state in USABLE_STATES


class ModelProvider(ABC):
    """One loadable model behind a role ABC (``inference/{imagegen,segmentation,parts}/base.py``).

    ``load()`` runs on a CPU thread and never touches CUDA; ``warm``/``offload``/``unload`` and the role methods run
    only on the GPU thread. Implementations take :attr:`lock` around every one of them.
    """

    spec: ModelSpec

    def __init__(self, spec: ModelSpec) -> None:
        self.spec = spec
        # Serialises load/warm/offload/unload/run of this provider (re-entrant: a run may call load() itself).
        self.lock = threading.RLock()

    @property
    def model_id(self) -> str:
        return self.spec.id

    @abstractmethod
    def status(self) -> ProviderStatus:
        """Live state: cheap, thread-safe, never blocks on the GPU."""

    @abstractmethod
    def load(self, cancel: CancellationToken) -> None:
        """Fully materialise the weights in CPU RAM (idempotent; never touches CUDA)."""

    def warm(self, cancel: CancellationToken) -> None:  # noqa: B027 - optional hook, default no-op
        """Optional warm-up on the GPU thread (one ``to(cuda)``/``to(cpu)`` cycle). Default: nothing."""
        return None

    @abstractmethod
    def offload(self) -> None:
        """Move the weights back to CPU and release cached VRAM (``torch.cuda.empty_cache()``)."""

    @abstractmethod
    def unload(self) -> None:
        """Drop the weights entirely (``gc.collect()``, ``empty_cache()``)."""


class StepProgress(Protocol):
    """Progress callback a provider receives for its step: ``fraction`` in 0..1 within the step."""

    def __call__(self, fraction: float, message: str | None = None) -> None: ...
