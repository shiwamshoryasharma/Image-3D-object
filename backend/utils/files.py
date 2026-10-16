"""Filesystem helpers that behave well on Windows (retries for transient sharing violations)."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Windows raises PermissionError when a file is briefly held open by another handle (antivirus, indexer,
# a concurrent reader). These operations are retried a few times before giving up.
_RETRY_ATTEMPTS = 8
_RETRY_DELAY_S = 0.05


def retry_os(func: Callable[[], T], *, attempts: int = _RETRY_ATTEMPTS, delay: float = _RETRY_DELAY_S) -> T:
    """Run ``func`` retrying on ``PermissionError`` (Windows sharing violations)."""
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except PermissionError:
            if attempt == attempts:
                raise
            time.sleep(delay * attempt)
    raise AssertionError("unreachable")  # pragma: no cover


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (temp file in the same directory + ``os.replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        retry_os(lambda: os.replace(tmp, path))
    finally:
        if tmp.exists():
            safe_unlink(tmp)


def replace_file(src: Path, dst: Path) -> None:
    """Move ``src`` over ``dst`` (atomic on the same volume), retrying transient sharing violations."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        retry_os(lambda: os.replace(src, dst))
    except OSError:
        # Different volumes: fall back to copy + delete.
        shutil.copy2(src, dst)
        safe_unlink(src)


def safe_unlink(path: Path) -> bool:
    """Delete a file if it exists. Returns ``True`` if a file was removed. Never raises."""
    try:
        retry_os(lambda: path.unlink(missing_ok=False))
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("Could not delete file %s: %s", path, exc)
        return False


def safe_rmtree(path: Path) -> bool:
    """Recursively delete a directory if it exists. Returns ``True`` on success. Never raises."""
    if not path.exists():
        return False

    def _onexc(func: Callable[..., object], target: str, exc: BaseException) -> None:
        # Clear read-only bits and retry once (common on Windows).
        try:
            os.chmod(target, 0o700)
            func(target)
        except OSError:
            pass

    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        shutil.rmtree(path, onexc=_onexc)
        if not path.exists():
            return True
        time.sleep(_RETRY_DELAY_S * attempt)
    logger.warning("Could not fully delete directory %s", path)
    return False


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_size(path: Path) -> int:
    return path.stat().st_size
