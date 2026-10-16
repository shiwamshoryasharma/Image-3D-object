"""Torch-optional GPU device queries.

Used by engines to answer ``/system`` ``device`` requests. Everything here is safe to call from any thread while
the GPU worker is busy: static properties (name, compute capability, total VRAM) are queried once and cached,
live memory comes from ``torch.cuda.mem_get_info`` (a cheap driver query that does not synchronize streams).

torch is imported lazily. If it cannot be imported the helpers return ``None`` (and remember the failure so the
import is not retried on every request).
"""

from __future__ import annotations

import importlib
import logging
import re
import threading
from dataclasses import dataclass
from types import ModuleType
from typing import Any

logger = logging.getLogger(__name__)

_MIB = 1024 * 1024

_torch_lock = threading.Lock()
_torch_module: ModuleType | None = None
_torch_failed: str | None = None


def import_torch() -> ModuleType | None:
    """Import torch once (thread-safe). Returns ``None`` if torch is not importable."""
    global _torch_module, _torch_failed
    if _torch_module is not None:
        return _torch_module
    with _torch_lock:
        if _torch_module is not None:
            return _torch_module
        if _torch_failed is not None:
            return None
        try:
            _torch_module = importlib.import_module("torch")
        except Exception as exc:  # noqa: BLE001 - DLL load failures raise OSError, broken installs anything
            _torch_failed = f"{type(exc).__name__}: {exc}"
            logger.warning("torch is not importable; device information is unavailable (%s)", _torch_failed)
            return None
        return _torch_module


def torch_import_error() -> str | None:
    """Reason of the last failed torch import (``None`` if torch imported fine or was never tried)."""
    return _torch_failed


def parse_cuda_index(device: str | None) -> int:
    """``"cuda"`` → 0, ``"cuda:1"`` → 1. Anything unparsable maps to 0."""
    if not device:
        return 0
    match = re.fullmatch(r"\s*cuda(?::(\d+))?\s*", device.lower())
    if match and match.group(1):
        return int(match.group(1))
    return 0


def is_cuda_device(device: str | None) -> bool:
    return bool(device) and device.strip().lower().startswith("cuda")


@dataclass(frozen=True, slots=True)
class CudaDeviceStatic:
    """Properties of a CUDA device that never change while the process runs."""

    index: int
    name: str
    compute_capability: str
    total_bytes: int


@dataclass(frozen=True, slots=True)
class CudaMemory:
    free_bytes: int
    total_bytes: int

    @property
    def used_bytes(self) -> int:
        return max(self.total_bytes - self.free_bytes, 0)


def query_static(torch: ModuleType, index: int = 0) -> CudaDeviceStatic:
    props = torch.cuda.get_device_properties(index)
    return CudaDeviceStatic(
        index=index,
        name=str(props.name),
        compute_capability=f"{props.major}.{props.minor}",
        total_bytes=int(props.total_memory),
    )


def query_memory(torch: ModuleType, index: int = 0) -> CudaMemory:
    """Device-wide free/total memory (includes other processes), via ``cudaMemGetInfo``."""
    free, total = torch.cuda.mem_get_info(index)
    return CudaMemory(free_bytes=int(free), total_bytes=int(total))


class DeviceInfoProvider:
    """Thread-safe provider of the ``/system`` ``device`` object.

    Output shape (contract section 7.2)::

        {"cuda_available": true, "name": "NVIDIA GeForce RTX 5090", "compute_capability": "12.0",
         "vram_total_mb": 32607, "vram_used_mb": 9000, "vram_free_mb": 23607,
         "torch_version": "2.14.1+cu130", "cuda_version": "13.0"}
    """

    def __init__(self, device: str = "cuda") -> None:
        self._device = device
        self._index = parse_cuda_index(device)
        self._lock = threading.Lock()
        self._static: CudaDeviceStatic | None = None

    @property
    def index(self) -> int:
        return self._index

    def static(self) -> CudaDeviceStatic | None:
        """Cached static properties (``None`` if torch/CUDA is unavailable)."""
        if self._static is not None:
            return self._static
        torch = import_torch()
        if torch is None or not is_cuda_device(self._device):
            return None
        with self._lock:
            if self._static is None:
                try:
                    if not torch.cuda.is_available():
                        return None
                    self._static = query_static(torch, self._index)
                except Exception as exc:  # noqa: BLE001 - never let a status query fail
                    logger.debug("CUDA device query failed: %s", exc)
                    return None
            return self._static

    def info(self) -> dict[str, Any] | None:
        """Device description, or ``None`` if torch is not importable."""
        torch = import_torch()
        if torch is None:
            return None
        info: dict[str, Any] = {
            "cuda_available": False,
            "name": None,
            "compute_capability": None,
            "vram_total_mb": None,
            "vram_used_mb": None,
            "vram_free_mb": None,
            "torch_version": str(getattr(torch, "__version__", "")) or None,
            "cuda_version": getattr(getattr(torch, "version", None), "cuda", None),
        }
        static = self.static()
        if static is None:
            return info
        info.update(
            cuda_available=True,
            name=static.name,
            compute_capability=static.compute_capability,
            vram_total_mb=static.total_bytes // _MIB,
        )
        try:
            memory = query_memory(torch, static.index)
        except Exception as exc:  # noqa: BLE001
            logger.debug("cudaMemGetInfo failed: %s", exc)
            return info
        info.update(
            vram_total_mb=memory.total_bytes // _MIB,
            vram_used_mb=memory.used_bytes // _MIB,
            vram_free_mb=memory.free_bytes // _MIB,
        )
        return info
