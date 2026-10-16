"""Executor protocol of the run framework (part-aware DESIGN.md section 4.3).

An executor describes one run kind as an ordered tuple of *phases*. The run worker drives it:

1. ``state = prepare(run, work_dir)`` on a worker thread (snapshot the inputs into ``work_dir``);
2. every phase in order: a ``cpu`` phase runs ``phase.fn(run, state, ctx)`` on the ``parts-cpu`` thread, a ``gpu``
   phase runs it on the single ``gpu-worker`` thread through ``GenerationWorker.submit_gpu_task`` inside
   ``registry.guard()`` with ``ensure_vram(phase.models)`` before and ``offload(phase.models)`` after it;
3. ``run = finalize(run, state, ctx)`` on a worker thread (validate, publish, build ``result``).

``run`` is a read-only snapshot: progress goes through ``ctx``; ``finalize`` returns the record whose kind-specific
fields (``result``, ...) the worker copies onto the live run. The GPU is free between phases, so a queued
image-to-3D job may run in between.

Executor classes are resolved lazily by dotted path (``core/part_aware_container.py``) and constructed as
``Executor(deps)`` with one :class:`ExecutorDeps`, so a slice adds its executor module without touching the
container. A module that is not merged yet makes its run kind report ``not_implemented``.
"""

from __future__ import annotations

import importlib
import logging
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, Literal, Protocol, TypeVar

from models.runs import RunKind, RunRecord, StepDef, StepDevice

if TYPE_CHECKING:  # pragma: no cover
    from config.settings import Settings
    from pipelines.runs.context import RunContext, RunProviderRegistry
    from storage.job_store import JobStore
    from storage.reference_store import ReferenceStore
    from storage.run_layout import RunLayout
    from storage.run_store import RunStore

logger = logging.getLogger(__name__)

R = TypeVar("R", bound=RunRecord)
P = TypeVar("P")

AvailabilityCode = Literal["ok", "not_implemented", "error"]
"""Subset of the ``ReasonCode`` vocabulary (section 5.1) an executor can report."""


@dataclass(frozen=True, slots=True)
class RunPhase(Generic[R, P]):
    name: str
    device: StepDevice
    steps: tuple[str, ...]
    """Step ids this phase may run."""
    fn: Callable[[R, P, RunContext], None]
    """Mutates ``P`` (the phase state) and files in ``ctx.work_dir``."""
    models: tuple[str, ...] = ()
    """Registry ids needed (live VRAM check before a gpu phase, offload after it)."""


class RunExecutor(Protocol[R, P]):
    kind: RunKind
    prefix: str
    """``"ig"`` | ``"sg"`` | ``"pa"``."""

    def plan(self, run: R) -> tuple[StepDef, ...]:
        """Every step, in order; steps that do not apply carry a ``skip_reason`` (pre-marked ``skipped``)."""
        ...

    def phases(self, run: R) -> tuple[RunPhase[R, P], ...]:
        """Ordered phases."""
        ...

    def prepare(self, run: R, work_dir: Path) -> P:
        """Worker thread: snapshot the inputs into ``work_dir`` and return the phase state."""
        ...

    def finalize(self, run: R, state: P, ctx: RunContext) -> R:
        """Worker thread: validate, publish and return the run with its ``result``."""
        ...


@dataclass(frozen=True, slots=True)
class ExecutorDeps:
    """Everything an executor may need, passed as the single constructor argument (``Executor(deps)``)."""

    settings: Settings
    layout: RunLayout
    registry: RunProviderRegistry | None
    """``None`` when the model registry is not available in this build (GPU phases then fail honestly)."""
    job_store: JobStore
    reference_store: ReferenceStore
    stores: Mapping[RunKind, RunStore[Any]]
    """The run stores, e.g. a part analysis reads the segmentation it reuses."""


# --------------------------------------------------------------------------------------------------
# Lazy resolution by dotted path
# --------------------------------------------------------------------------------------------------


class DottedPathMissing(LookupError):
    """The module named by a dotted path does not exist (its slice has not merged yet)."""


def import_dotted(path: str) -> Any:
    """Return ``attr`` of ``"package.module:attr"``.

    Raises :class:`DottedPathMissing` when the module itself is absent; any other import failure (a missing
    dependency, a broken CUDA DLL raising ``OSError``, a missing attribute) propagates unchanged.
    """
    module_name, sep, attr = path.partition(":")
    if not sep or not module_name or not attr:
        raise ValueError(f"Not a 'module:attribute' path: {path!r}")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name is not None and (module_name == exc.name or module_name.startswith(exc.name + ".")):
            raise DottedPathMissing(module_name) from exc
        raise
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise ImportError(f"{module_name} has no attribute {attr!r}") from exc


@dataclass(frozen=True, slots=True)
class ExecutorAvailability:
    code: AvailabilityCode
    reason: str | None = None

    @property
    def available(self) -> bool:
        return self.code == "ok"


class ExecutorTable(Mapping[RunKind, "RunExecutor[Any, Any]"]):
    """Executors resolved lazily from ``"package.module:ClassName"`` paths and built as ``ClassName(deps)``.

    A module that does not exist yet reports ``not_implemented``; one that fails to import or construct reports
    ``error`` with the exception text. ``overrides`` (test doubles) take precedence over the paths. Resolution
    happens once per kind (thread-safe) and only imports torch-free executor modules.
    """

    def __init__(
        self,
        paths: Mapping[RunKind, str],
        deps: ExecutorDeps,
        *,
        overrides: Mapping[RunKind, Any] | None = None,
    ) -> None:
        self._paths = dict(paths)
        self._deps = deps
        self._overrides = dict(overrides or {})
        self._resolved: dict[RunKind, tuple[Any | None, ExecutorAvailability]] = {}
        self._lock = threading.Lock()

    def _resolve(self, kind: RunKind) -> tuple[Any | None, ExecutorAvailability]:
        with self._lock:
            cached = self._resolved.get(kind)
            if cached is not None:
                return cached
            result = self._build(kind)
            self._resolved[kind] = result
            return result

    def _build(self, kind: RunKind) -> tuple[Any | None, ExecutorAvailability]:
        if kind in self._overrides:
            return self._overrides[kind], ExecutorAvailability("ok")
        path = self._paths.get(kind)
        if path is None:
            return None, ExecutorAvailability("not_implemented", f"No executor is registered for {kind}")
        try:
            executor_cls = import_dotted(path)
        except DottedPathMissing:
            return None, ExecutorAvailability("not_implemented", f"{path.partition(':')[0]} is not part of this build")
        except Exception as exc:  # noqa: BLE001 - a broken module must not break startup
            logger.warning("Run executor %s is not importable: %s: %s", path, type(exc).__name__, exc)
            return None, ExecutorAvailability("error", f"{type(exc).__name__}: {exc}")
        try:
            executor = executor_cls(self._deps)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Run executor %s could not be constructed", path)
            return None, ExecutorAvailability("error", f"{type(exc).__name__}: {exc}")
        logger.info("Run executor for %s: %s", kind, path)
        return executor, ExecutorAvailability("ok")

    def availability(self, kind: RunKind) -> ExecutorAvailability:
        return self._resolve(kind)[1]

    def __getitem__(self, kind: RunKind) -> RunExecutor[Any, Any]:
        executor, _availability = self._resolve(kind)
        if executor is None:
            raise KeyError(kind)
        return executor

    def __iter__(self) -> Iterator[RunKind]:
        kinds = list(dict.fromkeys([*self._paths, *self._overrides]))
        return iter([kind for kind in kinds if self._resolve(kind)[0] is not None])

    def __len__(self) -> int:
        return sum(1 for _ in self)
