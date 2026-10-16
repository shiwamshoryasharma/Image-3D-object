"""VRAM helpers and the torch-globals guard of auxiliary GPU work (part-aware DESIGN.md sections 4.8 and 4.9).

torch is imported only inside functions, through :func:`inference.device.import_torch` (cached; ``None`` when torch
is not importable), so this module stays importable from torch-free code.

:func:`torch_globals_guard` wraps **every** auxiliary GPU callable (``RunWorker._gpu_phase`` through
``ProviderRegistry.guard()``, and the model warm-up). TRELLIS reseeds with ``torch.manual_seed`` at the start of each
generation, but it draws its noise with global-default-dtype CPU ``torch.randn`` on the shared ``gpu-worker`` thread,
and globals that a diffusers/transformers provider leaves changed survive the reseed. The guard snapshots the globals
listed in :data:`GUARDED_GLOBALS`, restores the changed ones on exit and logs
``[torch_guard] restored [<names>]`` on the ``inference.providers.vram`` logger when at least one was changed (nothing
is logged otherwise). Adding a global is one :data:`GUARDED_GLOBALS` entry plus a ``test_aux_isolation.py`` case.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Final

from inference.device import import_torch, is_cuda_device, parse_cuda_index, query_memory

logger = logging.getLogger(__name__)

_MIB: Final = 1024 * 1024
RESTORED_LOG_PREFIX: Final = "[torch_guard] restored"


class _Unreadable:
    """Snapshot value of a global whose getter raised (e.g. a mixed legacy/new TF32 API state)."""

    __slots__ = ("error",)

    def __init__(self, error: str) -> None:
        self.error = error

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Unreadable) and other.error == self.error

    def __hash__(self) -> int:
        return hash(self.error)

    def __repr__(self) -> str:
        return f"<unreadable: {self.error}>"


@dataclass(frozen=True, slots=True)
class GuardedGlobal:
    name: str
    """Dotted name used in the log record and in ``torch_guard.json``."""
    read: Callable[[ModuleType], Any]
    write: Callable[[ModuleType, Any], None]


def _set_deterministic(torch: ModuleType, value: tuple[bool, bool]) -> None:
    enabled, warn_only = value
    torch.use_deterministic_algorithms(enabled, warn_only=warn_only)


def _attr(path: str) -> tuple[Callable[[ModuleType], Any], Callable[[ModuleType, Any], None]]:
    """Reader/writer of a plain module attribute below ``torch`` (e.g. ``backends.cudnn.benchmark``)."""
    *parents, leaf = path.split(".")

    def owner(torch: ModuleType) -> Any:
        obj: Any = torch
        for part in parents:
            obj = getattr(obj, part)
        return obj

    return (lambda torch: getattr(owner(torch), leaf)), (lambda torch, value: setattr(owner(torch), leaf, value))


# Restore order matters for the coupled TF32 settings: ``set_float32_matmul_precision`` normalises a mixed
# legacy/new-API state and flips ``matmul.allow_tf32`` as a side effect, so it is restored first.
GUARDED_GLOBALS: Final[tuple[GuardedGlobal, ...]] = (
    GuardedGlobal(
        "torch.get_default_dtype",
        lambda torch: torch.get_default_dtype(),
        lambda torch, value: torch.set_default_dtype(value),
    ),
    GuardedGlobal(
        "torch.get_float32_matmul_precision",
        lambda torch: torch.get_float32_matmul_precision(),
        lambda torch, value: torch.set_float32_matmul_precision(value),
    ),
    GuardedGlobal("torch.backends.cuda.matmul.allow_tf32", *_attr("backends.cuda.matmul.allow_tf32")),
    GuardedGlobal(
        "torch.backends.cuda.matmul.allow_fp16_reduced_precision_reduction",
        *_attr("backends.cuda.matmul.allow_fp16_reduced_precision_reduction"),
    ),
    GuardedGlobal(
        "torch.backends.cuda.matmul.allow_bf16_reduced_precision_reduction",
        *_attr("backends.cuda.matmul.allow_bf16_reduced_precision_reduction"),
    ),
    GuardedGlobal("torch.backends.cudnn.benchmark", *_attr("backends.cudnn.benchmark")),
    GuardedGlobal("torch.backends.cudnn.deterministic", *_attr("backends.cudnn.deterministic")),
    GuardedGlobal("torch.backends.cudnn.allow_tf32", *_attr("backends.cudnn.allow_tf32")),
    GuardedGlobal(
        "torch.get_num_threads",
        lambda torch: torch.get_num_threads(),
        lambda torch, value: torch.set_num_threads(value),
    ),
    GuardedGlobal(
        "torch.backends.cuda.flash_sdp_enabled",
        lambda torch: torch.backends.cuda.flash_sdp_enabled(),
        lambda torch, value: torch.backends.cuda.enable_flash_sdp(value),
    ),
    GuardedGlobal(
        "torch.backends.cuda.mem_efficient_sdp_enabled",
        lambda torch: torch.backends.cuda.mem_efficient_sdp_enabled(),
        lambda torch, value: torch.backends.cuda.enable_mem_efficient_sdp(value),
    ),
    GuardedGlobal(
        "torch.backends.cuda.math_sdp_enabled",
        lambda torch: torch.backends.cuda.math_sdp_enabled(),
        lambda torch, value: torch.backends.cuda.enable_math_sdp(value),
    ),
    GuardedGlobal(
        "torch.are_deterministic_algorithms_enabled",
        lambda torch: (
            bool(torch.are_deterministic_algorithms_enabled()),
            bool(torch.is_deterministic_algorithms_warn_only_enabled()),
        ),
        _set_deterministic,
    ),
    GuardedGlobal(
        "torch.is_grad_enabled",
        lambda torch: torch.is_grad_enabled(),
        lambda torch, value: torch.set_grad_enabled(value),
    ),
)
GUARDED_GLOBAL_NAMES: Final[tuple[str, ...]] = tuple(item.name for item in GUARDED_GLOBALS)


def _snapshot(torch: ModuleType) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for item in GUARDED_GLOBALS:
        try:
            values[item.name] = item.read(torch)
        except Exception as exc:  # noqa: BLE001 - e.g. RuntimeError for a mixed TF32 API state
            values[item.name] = _Unreadable(f"{type(exc).__name__}: {exc}")
    return values


def snapshot_torch_globals() -> dict[str, Any]:
    """Current values of :data:`GUARDED_GLOBALS` (empty when torch is not importable). Used by tests and smokes."""
    torch = import_torch()
    return _snapshot(torch) if torch is not None else {}


def _restore(torch: ModuleType, before: dict[str, Any]) -> list[str]:
    """Write back every global that differs from ``before``; returns the names that could not be restored."""
    failed: list[str] = []
    for _attempt in range(2):  # a second pass settles globals coupled to one restored earlier
        current = _snapshot(torch)
        pending = [item for item in GUARDED_GLOBALS if current[item.name] != before[item.name]]
        if not pending:
            return []
        failed = []
        for item in pending:
            value = before[item.name]
            if isinstance(value, _Unreadable):
                failed.append(item.name)
                continue
            try:
                item.write(torch, value)
            except Exception:  # noqa: BLE001 - restore the others regardless
                logger.exception("[torch_guard] could not restore %s", item.name)
                failed.append(item.name)
    current = _snapshot(torch)
    return sorted({item.name for item in GUARDED_GLOBALS if current[item.name] != before[item.name]} | set(failed))


@contextlib.contextmanager
def torch_globals_guard() -> Iterator[None]:
    """Snapshot the torch globals, run the block, restore whatever it changed (GPU thread; section 4.9)."""
    torch = import_torch()
    if torch is None:
        yield
        return
    before = _snapshot(torch)
    try:
        yield
    finally:
        after = _snapshot(torch)
        changed = sorted(name for name in GUARDED_GLOBAL_NAMES if after[name] != before[name])
        if changed:
            unrestored = _restore(torch, before)
            logger.info("%s %s", RESTORED_LOG_PREFIX, changed)
            if unrestored:
                logger.warning("[torch_guard] could not restore %s", unrestored)


def free_vram_mb(device: str) -> float | None:
    """Device-wide free VRAM of ``device`` in MiB, or ``None`` when it is not a usable CUDA device (GPU thread)."""
    if not is_cuda_device(device):
        return None
    torch = import_torch()
    if torch is None:
        return None
    try:
        if not torch.cuda.is_available():
            return None
        memory = query_memory(torch, parse_cuda_index(device))
    except Exception as exc:  # noqa: BLE001 - a broken driver must not crash the caller; the phase fails on its own
        logger.warning("Free VRAM query failed for %s: %s: %s", device, type(exc).__name__, exc)
        return None
    return memory.free_bytes / _MIB


def empty_cuda_cache() -> None:
    """``torch.cuda.empty_cache()`` when CUDA is initialised (no-op otherwise; never imports torch itself)."""
    torch = sys.modules.get("torch")
    if torch is None:
        return
    try:
        if torch.cuda.is_available() and torch.cuda.is_initialized():
            torch.cuda.empty_cache()
    except Exception as exc:  # noqa: BLE001
        logger.warning("torch.cuda.empty_cache() failed: %s: %s", type(exc).__name__, exc)
