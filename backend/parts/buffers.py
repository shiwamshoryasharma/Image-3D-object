"""Raw per-face / per-vertex buffers of a part layer (part-aware DESIGN.md section 3.3; slice S3b).

Every buffer is a raw little-endian file without header in ``<asset>/buffers/``, described by one
``manifest.buffers[]`` record (``id, file, dtype, components, count, byte_length, sha256, indexes, description``). A
consumer reads it with ``np.fromfile(path, dtype)`` after checking ``byte_length`` and ``sha256`` (:func:`read_buffer`).

Torch-free: numpy and hashlib only.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

import numpy as np

from parts.manifest import BufferRecord
from parts.types import BUFFER_ITEM_SIZES, BUFFER_SPECS, BufferSpec, vmap_buffer_spec

_LITTLE_ENDIAN: Final = {"uint8": "<u1", "uint16": "<u2", "uint32": "<u4"}
_HASH_CHUNK: Final = 1 << 24


class BufferError(ValueError):
    """A buffer file does not match its manifest record (size, hash or dtype)."""


def _as_le(array: np.ndarray, spec: BufferSpec) -> np.ndarray:
    data = np.asarray(array)
    if not np.issubdtype(data.dtype, np.integer):
        raise BufferError(f"buffer {spec.id!r} must hold integers, got {data.dtype}")
    expected_ndim = 1 if spec.components == 1 else 2
    if data.ndim != expected_ndim or (spec.components > 1 and data.shape[1] != spec.components):
        raise BufferError(f"buffer {spec.id!r} must have {spec.components} component(s) per element")
    info = np.iinfo(np.dtype(_LITTLE_ENDIAN[spec.dtype]))
    if data.size and (int(data.min()) < info.min or int(data.max()) > info.max):
        raise BufferError(f"buffer {spec.id!r} holds values outside {spec.dtype}")
    return np.ascontiguousarray(data.astype(_LITTLE_ENDIAN[spec.dtype], copy=False))


def write_buffer(asset_dir: Path, spec: BufferSpec, array: np.ndarray) -> BufferRecord:
    """Write one buffer under ``asset_dir`` (temporary sibling + rename) and return its manifest record."""
    data = _as_le(array, spec)
    path = Path(asset_dir) / spec.file
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = memoryview(data).cast("B")
    digest = hashlib.sha256(raw).hexdigest()
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as handle:
        handle.write(raw)
    os.replace(temporary, path)
    return BufferRecord(
        id=spec.id,
        file=spec.file,
        dtype=spec.dtype,
        components=spec.components,
        count=int(data.shape[0]),
        byte_length=len(raw),
        sha256=digest,
        indexes=spec.indexes,
        description=spec.description,
    )


def write_asset_buffers(
    asset_dir: Path,
    arrays: Mapping[str, np.ndarray],
    vmaps: Sequence[tuple[str, np.ndarray]],
) -> list[BufferRecord]:
    """Write the fixed buffers (``arrays`` keyed by buffer id, every id of ``BUFFER_SPECS`` required) and one
    ``vmap_<part id>`` per mesh node, in manifest order."""
    missing = [spec.id for spec in BUFFER_SPECS if spec.id not in arrays]
    if missing:
        raise BufferError(f"missing buffers: {', '.join(missing)}")
    records = [write_buffer(asset_dir, spec, arrays[spec.id]) for spec in BUFFER_SPECS]
    records.extend(write_buffer(asset_dir, vmap_buffer_spec(part_id), vmap) for part_id, vmap in vmaps)
    return records


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def buffer_problem(asset_dir: Path, record: BufferRecord, *, check_hash: bool = True) -> str | None:
    """Why the file of ``record`` does not match it, or ``None``."""
    path = Path(asset_dir) / record.file
    if not path.is_file():
        return f"buffer {record.id!r}: file {record.file} is missing"
    expected = record.count * record.components * BUFFER_ITEM_SIZES[record.dtype]
    if record.byte_length != expected:
        return f"buffer {record.id!r}: byte_length {record.byte_length} != count x components x itemsize {expected}"
    size = path.stat().st_size
    if size != record.byte_length:
        return f"buffer {record.id!r}: file has {size} bytes, manifest says {record.byte_length}"
    if check_hash and file_sha256(path) != record.sha256:
        return f"buffer {record.id!r}: sha256 mismatch"
    return None


def read_buffer(asset_dir: Path, record: BufferRecord, *, check_hash: bool = True) -> np.ndarray:
    """Read a buffer after checking its size (and sha256). Shape ``[count]`` or ``[count, components]``."""
    problem = buffer_problem(asset_dir, record, check_hash=check_hash)
    if problem is not None:
        raise BufferError(problem)
    data = np.fromfile(Path(asset_dir) / record.file, dtype=_LITTLE_ENDIAN[record.dtype])
    data = data.astype(record.dtype, copy=False)
    return data if record.components == 1 else data.reshape(record.count, record.components)


__all__ = [
    "BufferError",
    "buffer_problem",
    "file_sha256",
    "read_buffer",
    "write_asset_buffers",
    "write_buffer",
]
