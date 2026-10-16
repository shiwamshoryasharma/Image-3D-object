"""Periodic cleanup enforcing job_retention_days, max_stored_jobs and reference_set_retention_days.

* Jobs: only terminal jobs are ever deleted (expired first, then the oldest beyond ``max_stored_jobs``).
* Reference sets: deleted through :meth:`ReferenceService.delete_set_if_stale`, i.e. under the per-set lock with
  the staleness re-checked, so retention never races an upload or a job snapshotting the set.
* Orphans: job/set directories that are not indexed and have no metadata file (e.g. left behind when Windows
  refused to delete a file that was still open by a download) are removed once they are old enough that they
  cannot belong to a job or set being created right now.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from config.settings import Settings
from services.reference_service import ReferenceService
from storage.job_store import JobStore
from storage.paths import JOB_METADATA_FILE, SET_METADATA_FILE
from storage.reference_store import ReferenceStore
from utils.files import safe_rmtree
from utils.ids import JOB_PREFIX, REFERENCE_SET_PREFIX, is_valid_id
from utils.timeutil import utcnow

logger = logging.getLogger(__name__)

ORPHAN_MIN_AGE_S = 3600.0
"""Unindexed directories younger than this are left alone (a job's inputs are written before its job.json)."""


@dataclass(frozen=True, slots=True)
class RetentionReport:
    expired_jobs: int = 0
    excess_jobs: int = 0
    expired_sets: int = 0
    orphan_dirs: int = 0


class RetentionService:
    def __init__(
        self,
        settings: Settings,
        job_store: JobStore,
        reference_store: ReferenceStore,
        references: ReferenceService,
        *,
        orphan_min_age_s: float = ORPHAN_MIN_AGE_S,
    ) -> None:
        self._settings = settings
        self._jobs = job_store
        self._sets = reference_store
        self._references = references
        self._orphan_min_age_s = orphan_min_age_s

    # ------------------------------------------------------------------ jobs (blocking; run in a thread)
    def cleanup_jobs(self, now: datetime) -> tuple[int, int]:
        """Delete expired and excess terminal jobs. Returns ``(expired, excess)``."""
        terminal = [job for job in self._jobs.list() if job.is_terminal]  # newest first

        expired = 0
        if self._settings.job_retention_days > 0:
            cutoff = now - timedelta(days=self._settings.job_retention_days)
            for job in list(terminal):
                if (job.finished_at or job.created_at) < cutoff:
                    self._jobs.delete(job.id)
                    terminal.remove(job)
                    expired += 1

        excess = 0
        for job in terminal[self._settings.max_stored_jobs :]:
            self._jobs.delete(job.id)
            excess += 1
        return expired, excess

    # ------------------------------------------------------------------ reference sets (event loop)
    async def cleanup_reference_sets(self, now: datetime) -> int:
        """Delete reference sets not updated for ``reference_set_retention_days`` (lock-aware)."""
        if self._settings.reference_set_retention_days <= 0:
            return 0
        cutoff = now - timedelta(days=self._settings.reference_set_retention_days)
        deleted = 0
        for ref_set in self._references.list_sets():
            if ref_set.updated_at < cutoff and await self._references.delete_set_if_stale(ref_set.id, cutoff):
                deleted += 1
        return deleted

    # ------------------------------------------------------------------ orphans (blocking; run in a thread)
    def purge_orphans(self) -> int:
        """Remove old, unindexed job/set directories that have no metadata file. Returns the number removed."""
        removed = 0
        removed += self._purge_orphans_in(self._jobs.paths.jobs, JOB_PREFIX, JOB_METADATA_FILE, self._jobs.ids())
        removed += self._purge_orphans_in(
            self._sets.paths.uploads, REFERENCE_SET_PREFIX, SET_METADATA_FILE, self._sets.ids()
        )
        return removed

    def _purge_orphans_in(self, root: Path, prefix: str, metadata_name: str, indexed: set[str]) -> int:
        if not root.is_dir():
            return 0
        removed = 0
        now = time.time()
        for directory in root.iterdir():
            name = directory.name
            if name in indexed or not is_valid_id(name, prefix) or not directory.is_dir():
                continue
            # A directory with a (possibly corrupt) metadata file is kept for inspection; startup logs it.
            if (directory / metadata_name).exists():
                continue
            try:
                age = now - directory.stat().st_mtime
            except OSError:
                continue
            # Anything indexed after ``indexed`` was snapshotted is brand new and therefore skipped by age.
            if age < self._orphan_min_age_s:
                continue
            if safe_rmtree(directory):
                removed += 1
                logger.info("Removed orphaned directory %s", directory)
        return removed

    # ------------------------------------------------------------------ orchestration
    async def run_once(self) -> RetentionReport:
        """One cleanup pass (call on the event loop; blocking parts run in worker threads)."""
        now = utcnow()
        expired_jobs, excess_jobs = await asyncio.to_thread(self.cleanup_jobs, now)
        expired_sets = await self.cleanup_reference_sets(now)
        orphan_dirs = await asyncio.to_thread(self.purge_orphans)

        report = RetentionReport(expired_jobs, excess_jobs, expired_sets, orphan_dirs)
        if expired_jobs or excess_jobs or expired_sets or orphan_dirs:
            logger.info(
                "Retention cleanup removed %d expired job(s), %d excess job(s), %d reference set(s), "
                "%d orphaned director%s",
                expired_jobs,
                excess_jobs,
                expired_sets,
                orphan_dirs,
                "y" if orphan_dirs == 1 else "ies",
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
                logger.exception("Retention cleanup failed")
            await asyncio.sleep(interval_s)
