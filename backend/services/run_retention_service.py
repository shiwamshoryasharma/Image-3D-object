"""Periodic cleanup of the part-aware runs and of the on-demand part export cache (part-aware DESIGN.md section 7.2).

Started by :mod:`core.part_aware_container` next to the job retention (:mod:`services.retention_service`) and run
every ``retention_interval_s``:

* **Image generations** (``runtime/imagegen/``) and **segmentations** (``runtime/segmentations/``): only terminal runs
  are ever deleted, those older than ``imagegen_retention_days`` / ``segmentation_retention_days`` first (0 disables
  the age limit), then the oldest beyond ``max_stored_image_generations`` / ``max_stored_segmentations``. Runs whose
  ids are in :meth:`WorkflowStore.active_child_ids` (children of queued or running workflows) are skipped; that set
  is re-read right before every delete, so a workflow that starts during a pass still protects its inputs.
* **Workflows** (``runtime/workflows/``): terminal workflows older than ``workflow_retention_days`` or beyond
  ``max_stored_workflows`` are removed through :meth:`WorkflowStore.delete`, which refuses active ones.
* **Part analyses** live inside their job folder and go with the job (the existing job retention); nothing here
  deletes them.
* **Export cache** (``runtime/cache/part_exports/<pa>/``): folders whose ``pa_`` no longer exists (deleted, or its job
  removed by the job retention, which runs no delete hooks) are purged. Then files are evicted least recently used
  first, by last access or modification time, whichever is later, until the cache fits ``parts_export_cache_max_gb``
  (0 keeps nothing between passes). Exports are rebuilt on demand, so eviction never loses data. ``*.tmp`` files
  younger than :data:`STALE_TEMP_MIN_AGE_S` belong to an export being written and are left alone; older ones are
  leftovers of an interrupted export and are removed.

The blocking work of a pass runs in one worker thread (the run and workflow stores are thread-safe). A failure in one
section is logged and does not stop the others.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final, Protocol, TypeVar

from config.settings import Settings
from models.runs import PART_ANALYSIS_PREFIX, RunKind
from storage.run_layout import RunLayout
from storage.run_store import RunStore
from storage.workflow_store import WorkflowStore
from utils.files import safe_rmtree, safe_unlink
from utils.ids import is_valid_id
from utils.timeutil import utcnow

logger = logging.getLogger(__name__)

GIB: Final = 1024**3
STALE_TEMP_MIN_AGE_S: Final = 3600.0
"""``*.tmp`` files in the export cache younger than this belong to an export being written right now."""
TEMP_SUFFIX: Final = ".tmp"


class _Retainable(Protocol):
    """What the age and count limits need from a run or workflow record."""

    @property
    def id(self) -> str: ...

    @property
    def created_at(self) -> datetime: ...

    @property
    def finished_at(self) -> datetime | None: ...


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class StoreCleanup:
    """Outcome of the age and count limits on one store."""

    expired: int = 0
    excess: int = 0
    skipped: int = 0
    """Candidates kept because a queued or running workflow uses them."""

    @property
    def removed(self) -> int:
        return self.expired + self.excess


@dataclass(frozen=True, slots=True)
class ExportCacheCleanup:
    orphan_dirs: int = 0
    evicted_files: int = 0
    evicted_bytes: int = 0
    size_bytes: int = 0
    """Size of the cache after the pass (files of exports being written are not counted)."""


@dataclass(frozen=True, slots=True)
class RunRetentionReport:
    image_generations: StoreCleanup = field(default_factory=StoreCleanup)
    segmentations: StoreCleanup = field(default_factory=StoreCleanup)
    workflows: StoreCleanup = field(default_factory=StoreCleanup)
    exports: ExportCacheCleanup = field(default_factory=ExportCacheCleanup)

    @property
    def changed(self) -> bool:
        return bool(
            self.image_generations.removed
            or self.segmentations.removed
            or self.workflows.removed
            or self.exports.orphan_dirs
            or self.exports.evicted_files
        )


@dataclass(frozen=True, slots=True)
class _CacheFile:
    path: Path
    size: int
    last_used: float


class RunRetentionService:
    """Enforces the section 7.2 limits (constructor frozen by ``core.part_aware_container``)."""

    def __init__(
        self,
        *,
        settings: Settings,
        layout: RunLayout,
        stores: Mapping[RunKind, RunStore[Any]],
        workflow_store: WorkflowStore,
        stale_temp_min_age_s: float = STALE_TEMP_MIN_AGE_S,
    ) -> None:
        self._settings = settings
        self._layout = layout
        self._stores = stores
        self._workflows = workflow_store
        self._stale_temp_min_age_s = stale_temp_min_age_s

    # ------------------------------------------------------------------ runs (blocking; run in a thread)
    def cleanup_runs(self, kind: RunKind, now: datetime) -> StoreCleanup:
        """Apply the age and count limits of ``kind`` (image generations or segmentations)."""
        if kind == "image_generation":
            retention_days = self._settings.imagegen_retention_days
            max_stored = self._settings.max_stored_image_generations
        elif kind == "segmentation":
            retention_days = self._settings.segmentation_retention_days
            max_stored = self._settings.max_stored_segmentations
        else:
            raise ValueError(f"{kind} runs are removed with their job, not by run retention")
        store = self._stores.get(kind)
        if store is None:
            return StoreCleanup()
        newest_first = list(reversed(store.list_terminal()))
        return _apply_limits(
            newest_first,
            now,
            retention_days=retention_days,
            max_stored=max_stored,
            protected=self._used_by_active_workflow,
            delete=store.delete,
            label=kind.replace("_", " "),
        )

    def _used_by_active_workflow(self, run_id: str) -> bool:
        return run_id in self._workflows.active_child_ids()

    # ------------------------------------------------------------------ workflows (blocking; run in a thread)
    def cleanup_workflows(self, now: datetime) -> StoreCleanup:
        """Apply the age and count limits to terminal workflows (``WorkflowStore.delete`` refuses active ones)."""
        newest_first = list(reversed(self._workflows.list_terminal()))
        return _apply_limits(
            newest_first,
            now,
            retention_days=self._settings.workflow_retention_days,
            max_stored=self._settings.max_stored_workflows,
            protected=lambda _wf_id: False,
            delete=self._workflows.delete,
            label="workflow",
        )

    # ------------------------------------------------------------------ export cache (blocking; run in a thread)
    def cleanup_export_cache(self) -> ExportCacheCleanup:
        """Purge exports of vanished analyses, then evict least recently used files down to the size cap."""
        root = self._layout.part_exports_root
        if not root.is_dir():
            return ExportCacheCleanup()
        orphan_dirs = self._purge_orphan_exports(root)

        cap_bytes = max(0, int(self._settings.parts_export_cache_max_gb * GIB))
        files, stale_files, stale_bytes = self._scan_cache(root)
        total = sum(entry.size for entry in files)
        evicted_files, evicted_bytes = stale_files, stale_bytes
        if total > cap_bytes:
            for entry in sorted(files, key=lambda item: (item.last_used, str(item.path))):
                if total <= cap_bytes:
                    break
                # A file that is being downloaded cannot be deleted on Windows; it stays until a later pass.
                if safe_unlink(entry.path):
                    total -= entry.size
                    evicted_files += 1
                    evicted_bytes += entry.size
            logger.debug("Part export cache trimmed to %d byte(s) (cap %d)", total, cap_bytes)
        return ExportCacheCleanup(
            orphan_dirs=orphan_dirs, evicted_files=evicted_files, evicted_bytes=evicted_bytes, size_bytes=total
        )

    def _purge_orphan_exports(self, root: Path) -> int:
        store = self._stores.get("part_analysis")
        if store is None:  # without the store nobody can tell which analyses still exist
            return 0
        removed = 0
        for directory in _export_dirs(root):
            # ``get`` hides analyses whose job folder is gone (job retention runs no delete hooks).
            if store.get(directory.name) is not None:
                continue
            if safe_rmtree(directory):
                removed += 1
                logger.info("Removed cached exports of vanished part analysis %s", directory.name)
        return removed

    def _scan_cache(self, root: Path) -> tuple[list[_CacheFile], int, int]:
        """Cache files that count against the cap, plus the number and size of stale temp files removed."""
        now = time.time()
        files: list[_CacheFile] = []
        stale_files = stale_bytes = 0
        for directory in _export_dirs(root):
            for path in _walk_files(directory):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if path.name.endswith(TEMP_SUFFIX):
                    if now - stat.st_mtime < self._stale_temp_min_age_s:
                        continue  # an export is being written
                    if safe_unlink(path):
                        stale_files += 1
                        stale_bytes += stat.st_size
                    continue
                files.append(_CacheFile(path, stat.st_size, max(stat.st_atime, stat.st_mtime)))
        return files, stale_files, stale_bytes

    # ------------------------------------------------------------------ orchestration
    def run_pass(self, now: datetime) -> RunRetentionReport:
        """One blocking cleanup pass (call from a worker thread)."""
        return RunRetentionReport(
            image_generations=_guarded(
                "image generation retention", lambda: self.cleanup_runs("image_generation", now), StoreCleanup()
            ),
            segmentations=_guarded(
                "segmentation retention", lambda: self.cleanup_runs("segmentation", now), StoreCleanup()
            ),
            workflows=_guarded("workflow retention", lambda: self.cleanup_workflows(now), StoreCleanup()),
            exports=_guarded("part export cache cleanup", self.cleanup_export_cache, ExportCacheCleanup()),
        )

    async def run_once(self) -> RunRetentionReport:
        """One cleanup pass (call on the event loop; the blocking work runs in a worker thread)."""
        report = await asyncio.to_thread(self.run_pass, utcnow())
        if report.changed:
            ig, sg, wf, exports = report.image_generations, report.segmentations, report.workflows, report.exports
            logger.info(
                "Run retention removed %d image generation(s), %d segmentation(s) and %d workflow(s) "
                "(%d expired, %d beyond the count limits); %d cached export folder(s) of vanished analyses and "
                "%d cached export file(s) (%.1f MB) removed; cache now %.1f MB",
                ig.removed,
                sg.removed,
                wf.removed,
                ig.expired + sg.expired + wf.expired,
                ig.excess + sg.excess + wf.excess,
                exports.orphan_dirs,
                exports.evicted_files,
                exports.evicted_bytes / 1e6,
                exports.size_bytes / 1e6,
            )
        if report.image_generations.skipped or report.segmentations.skipped:
            logger.info(
                "Run retention kept %d run(s) past their limits because active workflows use them",
                report.image_generations.skipped + report.segmentations.skipped,
            )
        return report

    async def run_forever(self, interval_s: float, *, initial_delay_s: float = 5.0) -> None:
        await asyncio.sleep(initial_delay_s)
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Run retention cleanup failed")
            await asyncio.sleep(interval_s)


def _apply_limits(
    newest_first: Sequence[_Retainable],
    now: datetime,
    *,
    retention_days: int,
    max_stored: int,
    protected: Callable[[str], bool],
    delete: Callable[[str], bool],
    label: str,
) -> StoreCleanup:
    """Delete expired items, then the oldest beyond ``max_stored``; protected items are kept and still counted."""
    expired = excess = 0
    skipped: set[str] = set()
    remaining: list[_Retainable] = []
    cutoff = now - timedelta(days=retention_days) if retention_days > 0 else None
    for item in newest_first:
        if cutoff is None or (item.finished_at or item.created_at) >= cutoff:
            remaining.append(item)
        elif protected(item.id):
            skipped.add(item.id)
            remaining.append(item)
        elif delete(item.id):
            expired += 1
            logger.debug("Deleted expired %s %s", label, item.id)
    for item in remaining[max_stored:]:
        if protected(item.id):
            skipped.add(item.id)
        elif delete(item.id):
            excess += 1
            logger.debug("Deleted %s %s (beyond the count limit of %d)", label, item.id, max_stored)
    return StoreCleanup(expired=expired, excess=excess, skipped=len(skipped))


def _guarded(section: str, func: Callable[[], T], fallback: T) -> T:
    try:
        return func()
    except Exception:  # noqa: BLE001 - one broken section must not stop the others
        logger.exception("Run retention: %s failed", section)
        return fallback


def _export_dirs(root: Path) -> Iterator[Path]:
    """``cache/part_exports/<pa>/`` folders (anything else in the cache root is not ours and is left alone)."""
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if is_valid_id(entry.name, PART_ANALYSIS_PREFIX) and entry.is_dir():
            yield entry


def _walk_files(directory: Path) -> Iterator[Path]:
    for dirpath, _dirnames, filenames in os.walk(directory):
        base = Path(dirpath)
        for name in filenames:
            yield base / name
