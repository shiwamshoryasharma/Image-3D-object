"""Atomic publish of a staged part layer (part-aware DESIGN.md section 3.14 and invariant I12; slice S3b).

``publish(staging_dir, target_dir, job_exists)`` moves ``runtime/temp/parts/<pa>/out/`` to
``jobs/<job>/parts/<pa>/asset/`` with ONE directory rename:

1. an existing ``target_dir`` is refused (``publish_conflict``): a published layer is immutable, a new analysis
   creates a new ``pa_``;
2. ``job_exists()`` is re-checked (``job_deleted``);
3. ``utils.files.retry_os(lambda: os.replace(staging_dir, target_dir))``: on Windows a directory rename fails with
   ``PermissionError`` while Defender or the indexer holds a file inside, so it is retried. Both directories are under
   ``runtime_dir`` (one volume), so the rename is atomic. ``target_dir.parent`` (``jobs/<job>/parts/<pa>/``) exists
   already (created with ``run.json``); publish never creates it, so a deleted job is never resurrected.

:func:`validate_and_publish` runs :func:`parts.validate.validate_staging` (which re-hashes the base ``model.glb``)
immediately before the rename.

Torch-free.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

from core.exceptions import AppError
from core.feature_errors import JobDeletedError
from parts.manifest import PartsManifest
from parts.validate import validate_staging
from utils.files import retry_os

PUBLISH_CONFLICT: Final = "publish_conflict"
EXPORT_FAILED: Final = "export_failed"
_MAX_DETAIL_PROBLEMS: Final = 50


class PublishConflictError(AppError):
    """The target asset directory already exists (published layers are immutable)."""

    status_code = 409
    code = PUBLISH_CONFLICT

    def __init__(self, target_dir: Path) -> None:
        super().__init__(
            f"The part layer {target_dir.parent.name} is already published",
            details={"analysis_id": target_dir.parent.name},
        )


class StagingInvalidError(AppError):
    """The staged layer failed validation and was not published (run error ``export_failed``)."""

    status_code = 500
    code = EXPORT_FAILED

    def __init__(self, problems: Sequence[str]) -> None:
        listed = list(problems)
        super().__init__(
            f"The part layer failed validation: {listed[0] if listed else 'unknown problem'}"
            + (f" (+{len(listed) - 1} more)" if len(listed) > 1 else ""),
            details={"problems": listed[:_MAX_DETAIL_PROBLEMS]},
        )
        self.problems = tuple(listed)


class PublishError(AppError):
    """The rename failed for another reason than a conflict or a deleted job."""

    status_code = 500
    code = EXPORT_FAILED


def _job_id(target_dir: Path) -> str:
    """``jobs/<job>/parts/<pa>/asset`` -> ``<job>``."""
    parents = target_dir.parents
    return parents[2].name if len(parents) > 2 else ""


def publish(staging_dir: Path, target_dir: Path, job_exists: Callable[[], bool]) -> Path:
    """Publish ``staging_dir`` as ``target_dir`` with one atomic rename; returns ``target_dir``."""
    staging = Path(staging_dir)
    target = Path(target_dir)
    if not staging.is_dir():
        raise PublishError(f"The staged part layer {staging} does not exist")
    if target.exists():
        raise PublishConflictError(target)
    if not job_exists():
        raise JobDeletedError(job_id=_job_id(target))
    if not target.parent.is_dir():
        # The analysis folder is created with run.json when the run is queued; never recreate it here.
        raise JobDeletedError(job_id=_job_id(target))
    try:
        retry_os(lambda: os.replace(staging, target))
    except OSError as exc:
        if target.exists():
            raise PublishConflictError(target) from exc
        if not job_exists() or not target.parent.is_dir():
            raise JobDeletedError(job_id=_job_id(target)) from exc
        raise PublishError(f"Publishing the part layer failed: {type(exc).__name__}: {exc}") from exc
    return target


def validate_and_publish(
    staging_dir: Path,
    target_dir: Path,
    manifest: PartsManifest,
    base_path: Path,
    job_exists: Callable[[], bool],
) -> Path:
    """:func:`validate_staging` (base re-hashed now), then :func:`publish`. Raises :class:`StagingInvalidError`."""
    target = Path(target_dir)
    if target.exists():
        raise PublishConflictError(target)
    problems = validate_staging(Path(staging_dir), manifest, Path(base_path))
    if problems:
        raise StagingInvalidError(problems)
    return publish(staging_dir, target, job_exists)


__all__ = [
    "EXPORT_FAILED",
    "PUBLISH_CONFLICT",
    "PublishConflictError",
    "PublishError",
    "StagingInvalidError",
    "publish",
    "validate_and_publish",
]
