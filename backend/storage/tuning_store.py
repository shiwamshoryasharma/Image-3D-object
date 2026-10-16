"""Tuning run persistence: ``runtime/jobs/<job_id>/tuning/<tn_id>/run.json`` (+ the run's output files).

Tuning runs live inside their job's folder, so deleting a job (API or retention) removes its runs too. Runs whose
job no longer exists are hidden and never written again (a late write must not resurrect a deleted job folder).
Thread-safe (the Blender worker thread and protocol reader threads persist progress); hands out deep copies.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from models.enums import JobStatus
from models.job import JobError
from models.tuning import INTERRUPTED_ERROR_CODE, RUN_FILE, TUNING_PREFIX, TuningRun
from storage.json_io import read_json, write_json_atomic
from storage.paths import JOB_METADATA_FILE, RuntimePaths
from utils.files import safe_rmtree
from utils.ids import JOB_PREFIX, is_valid_id
from utils.timeutil import utcnow

logger = logging.getLogger(__name__)

TUNING_DIR_NAME = "tuning"


class TuningStore:
    def __init__(self, paths: RuntimePaths, *, job_exists: Callable[[str], bool] | None = None) -> None:
        self._paths = paths
        self._job_exists = job_exists or (lambda job_id: paths.job_metadata(job_id).is_file())
        self._runs: dict[str, TuningRun] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ paths
    def job_tuning_dir(self, job_id: str) -> Path:
        return self._paths.job_dir(job_id) / TUNING_DIR_NAME

    def run_dir(self, job_id: str, tuning_id: str) -> Path:
        return self.job_tuning_dir(job_id) / tuning_id

    def run_file(self, job_id: str, tuning_id: str) -> Path:
        return self.run_dir(job_id, tuning_id) / RUN_FILE

    # ------------------------------------------------------------------ loading
    def load(self) -> int:
        """Index every ``jobs/<job>/tuning/<tn>/run.json`` whose job exists (corrupt files are skipped)."""
        loaded: dict[str, TuningRun] = {}
        jobs_root = self._paths.jobs
        if jobs_root.exists():
            for job_dir in jobs_root.iterdir():
                if not job_dir.is_dir() or not is_valid_id(job_dir.name, JOB_PREFIX):
                    continue
                tuning_root = job_dir / TUNING_DIR_NAME
                if not tuning_root.is_dir() or not (job_dir / JOB_METADATA_FILE).is_file():
                    continue
                for run_dir in tuning_root.iterdir():
                    if not run_dir.is_dir() or not is_valid_id(run_dir.name, TUNING_PREFIX):
                        continue
                    meta = run_dir / RUN_FILE
                    if not meta.is_file():
                        continue
                    try:
                        run = TuningRun.model_validate(read_json(meta))
                    except Exception as exc:  # noqa: BLE001 - one broken run must not break startup
                        logger.warning("Skipping unreadable tuning run %s: %s", run_dir.name, exc)
                        continue
                    if run.id != run_dir.name or run.job_id != job_dir.name:
                        logger.warning("Skipping tuning run with mismatched ids in %s", run_dir)
                        continue
                    loaded[run.id] = run
        with self._lock:
            self._runs = loaded
        logger.info("Loaded %d tuning run(s)", len(loaded))
        return len(loaded)

    def recover_interrupted(self) -> list[TuningRun]:
        """Mark runs left ``queued``/``running`` by a previous process as failed (``interrupted``)."""
        recovered: list[TuningRun] = []
        with self._lock:
            candidates = [r for r in self._runs.values() if r.is_active]
        for run in candidates:
            run = run.model_copy(deep=True)
            previous = run.status
            now = utcnow()
            run.status = JobStatus.FAILED
            run.finished_at = now
            run.error = JobError(
                code=INTERRUPTED_ERROR_CODE,
                message="The server stopped before this tuning run finished",
                details={"previous_status": str(previous), "step": run.progress.step_id},
            )
            for step in run.steps:
                if step.status == "running":
                    step.status = "failed"
                    step.message = "Interrupted"
            run.progress.message = "Interrupted"
            run.progress.updated_at = now
            self.save(run)
            recovered.append(run)
            logger.warning("Tuning run %s was %s at shutdown; marked failed (interrupted)", run.id, previous)
        return recovered

    # ------------------------------------------------------------------ queries
    def get(self, tuning_id: str) -> TuningRun | None:
        if not is_valid_id(tuning_id, TUNING_PREFIX):
            return None
        with self._lock:
            run = self._runs.get(tuning_id)
            if run is None:
                return None
            copy = run.model_copy(deep=True)
        return copy if self._job_exists(copy.job_id) else None

    def list_for_job(self, job_id: str) -> list[TuningRun]:
        """Runs of one job, newest first."""
        if not is_valid_id(job_id, JOB_PREFIX):
            return []
        with self._lock:
            items = [r.model_copy(deep=True) for r in self._runs.values() if r.job_id == job_id]
        items.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        return items

    def list_active(self, job_id: str | None = None) -> list[TuningRun]:
        with self._lock:
            return [
                r.model_copy(deep=True)
                for r in self._runs.values()
                if r.is_active and (job_id is None or r.job_id == job_id)
            ]

    def count(self) -> int:
        with self._lock:
            return len(self._runs)

    # ------------------------------------------------------------------ mutations
    def save(self, run: TuningRun) -> bool:
        """Persist ``run``. Returns ``False`` (and drops it from the index) if its job no longer exists."""
        if not is_valid_id(run.id, TUNING_PREFIX) or not is_valid_id(run.job_id, JOB_PREFIX):
            raise ValueError(f"Invalid tuning/job id: {run.id!r} / {run.job_id!r}")
        with self._lock:
            if not self._job_exists(run.job_id):
                self._runs.pop(run.id, None)
                logger.info("Not saving tuning run %s: job %s no longer exists", run.id, run.job_id)
                return False
            write_json_atomic(self.run_file(run.job_id, run.id), run.model_dump(mode="json"))
            self._runs[run.id] = run.model_copy(deep=True)
        return True

    def delete(self, tuning_id: str) -> bool:
        """Remove a run's folder and index entry."""
        if not is_valid_id(tuning_id, TUNING_PREFIX):
            return False
        with self._lock:
            run = self._runs.pop(tuning_id, None)
        if run is None:
            return False
        safe_rmtree(self.run_dir(run.job_id, run.id))
        return True

    def forget_job(self, job_id: str) -> list[str]:
        """Drop every index entry of a job that is being deleted (its folder goes with the job)."""
        with self._lock:
            ids = [r.id for r in self._runs.values() if r.job_id == job_id]
            for tuning_id in ids:
                self._runs.pop(tuning_id, None)
        return ids
