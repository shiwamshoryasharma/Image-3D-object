"""Generic run persistence (part-aware DESIGN.md sections 4.3 and 7.1).

One :class:`RunStore` per run kind indexes the ``run.json`` records in memory and writes them atomically:

* global kinds (image generations, segmentations): ``runtime/<root>/<run_id>/run.json``;
* job-scoped part analyses: ``runtime/jobs/<job>/parts/<pa>/run.json``.

**Job folders are never recreated.** A job-scoped save creates ``parts/`` and ``parts/<pa>/`` with
``mkdir(parents=False)`` below an existing job folder and writes the record without creating any parent, so when a
delete or retention removed the job in between, the save raises :class:`~core.feature_errors.JobDeletedError` (run
error ``job_deleted``) instead of resurrecting ``jobs/<job>/``. There is no check-then-write window. Records of a
deleted run are never written again either (tombstones), and runs whose job vanished are hidden.

Thread-safe (the run worker's CPU and GPU threads persist progress); hands out deep copies. ``os.replace`` is
retried on transient Windows sharing violations.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Generic, TypeVar

from core.exceptions import StorageError
from core.feature_errors import JobDeletedError
from models.enums import JobStatus
from models.job import JobError
from models.runs import INTERRUPTED_ERROR_CODE, RUN_FILE, RUN_PREFIXES, RunKind, RunRecord
from storage.json_io import read_json
from storage.paths import JOB_METADATA_FILE
from storage.run_layout import JOB_PARTS_DIR, RunLayout
from utils.files import retry_os, safe_rmtree, safe_unlink
from utils.ids import JOB_PREFIX, is_valid_id
from utils.timeutil import utcnow

logger = logging.getLogger(__name__)

R = TypeVar("R", bound=RunRecord)

INTERRUPTED_MESSAGE = "The server stopped before this run finished"


class RunStore(Generic[R]):
    def __init__(
        self,
        layout: RunLayout,
        *,
        kind: RunKind,
        record_type: type[R],
        job_exists: Callable[[str], bool] | None = None,
    ) -> None:
        self._layout = layout
        self._paths = layout.paths
        self.kind: RunKind = kind
        self.prefix: str = RUN_PREFIXES[kind]
        self.record_type: type[R] = record_type
        self._root = layout.run_root(kind)
        self._job_exists = job_exists or (lambda job_id: self._paths.job_metadata(job_id).is_file())
        self._runs: dict[str, R] = {}
        self._deleted: set[str] = set()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ layout
    @property
    def job_scoped(self) -> bool:
        """``True`` for part analyses (records live inside their job's folder)."""
        return self._root is None

    def run_dir_for(self, run_id: str, job_id: str | None = None) -> Path:
        if self._root is not None:
            return self._root / run_id
        if job_id is None:
            raise ValueError(f"A job id is needed to locate {run_id}")
        return self._layout.part_analysis_dir(job_id, run_id)

    def run_dir(self, run: RunRecord) -> Path:
        return self.run_dir_for(run.id, _job_id_of(run))

    def run_file(self, run: RunRecord) -> Path:
        return self.run_dir(run) / RUN_FILE

    def job_exists(self, job_id: str) -> bool:
        return is_valid_id(job_id, JOB_PREFIX) and self._job_exists(job_id)

    # ------------------------------------------------------------------ loading
    def load(self) -> int:
        """Index every readable record of this kind (corrupt or mismatched files are skipped)."""
        loaded: dict[str, R] = {}
        for run_dir, job_id in self._iter_run_dirs():
            meta = run_dir / RUN_FILE
            if not meta.is_file():
                continue
            try:
                run = self.record_type.model_validate(read_json(meta))
            except Exception as exc:  # noqa: BLE001 - one broken run must not break startup
                logger.warning("Skipping unreadable %s run %s: %s", self.kind, run_dir.name, exc)
                continue
            if run.id != run_dir.name or run.kind != self.kind or (job_id is not None and _job_id_of(run) != job_id):
                logger.warning("Skipping %s run with mismatched ids in %s", self.kind, run_dir)
                continue
            loaded[run.id] = run
        with self._lock:
            self._runs = loaded
            self._deleted.clear()
        logger.info("Loaded %d %s run(s)", len(loaded), self.kind.replace("_", " "))
        return len(loaded)

    def _iter_run_dirs(self) -> Iterator[tuple[Path, str | None]]:
        if self._root is not None:
            if self._root.is_dir():
                for run_dir in self._root.iterdir():
                    if run_dir.is_dir() and is_valid_id(run_dir.name, self.prefix):
                        yield run_dir, None
            return
        jobs_root = self._paths.jobs
        if not jobs_root.is_dir():
            return
        for job_dir in jobs_root.iterdir():
            if not job_dir.is_dir() or not is_valid_id(job_dir.name, JOB_PREFIX):
                continue
            parts_root = job_dir / JOB_PARTS_DIR
            if not parts_root.is_dir() or not (job_dir / JOB_METADATA_FILE).is_file():
                continue
            for run_dir in parts_root.iterdir():
                if run_dir.is_dir() and is_valid_id(run_dir.name, self.prefix):
                    yield run_dir, job_dir.name

    def recover_interrupted(self) -> list[R]:
        """Mark runs left ``queued``/``running`` by a previous process as failed (``interrupted``)."""
        recovered: list[R] = []
        with self._lock:
            candidates = [r.model_copy(deep=True) for r in self._runs.values() if r.is_active]
        for run in candidates:
            previous = run.status
            now = utcnow()
            run.status = JobStatus.FAILED
            run.finished_at = now
            run.error = JobError(
                code=INTERRUPTED_ERROR_CODE,
                message=INTERRUPTED_MESSAGE,
                details={"previous_status": str(previous), "step": run.progress.step_id},
            )
            for step in run.steps:
                if step.status == "running":
                    step.status = "failed"
                    step.message = "Interrupted"
            run.progress.message = "Interrupted"
            run.progress.updated_at = now
            try:
                self.save(run)
            except JobDeletedError:
                continue
            recovered.append(run)
            logger.warning("%s run %s was %s at shutdown; marked failed (interrupted)", self.kind, run.id, previous)
        return recovered

    # ------------------------------------------------------------------ queries
    def get(self, run_id: str) -> R | None:
        if not is_valid_id(run_id, self.prefix):
            return None
        with self._lock:
            run = self._runs.get(run_id)
            copy = run.model_copy(deep=True) if run is not None else None
        if copy is None or not self._visible(copy):
            return None
        return copy

    def list_runs(
        self,
        *,
        status: JobStatus | None = None,
        job_id: str | None = None,
        workflow_id: str | None = None,
        predicate: Callable[[R], bool] | None = None,
        limit: int | None = None,
    ) -> list[R]:
        """Matching runs, newest first."""
        with self._lock:
            items = [r.model_copy(deep=True) for r in self._runs.values()]
        items = [
            r
            for r in items
            if (status is None or r.status is status)
            and (job_id is None or _job_id_of(r) == job_id)
            and (workflow_id is None or r.workflow_id == workflow_id)
            and (predicate is None or predicate(r))
            and self._visible(r)
        ]
        items.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        return items[:limit] if limit is not None else items

    def list_for_job(self, job_id: str) -> list[R]:
        if not is_valid_id(job_id, JOB_PREFIX):
            return []
        return self.list_runs(job_id=job_id)

    def list_active(self, job_id: str | None = None) -> list[R]:
        return self.list_runs(job_id=job_id, predicate=lambda r: r.is_active)

    def list_terminal(self) -> list[R]:
        """Terminal runs, oldest first (retention order)."""
        items = self.list_runs(predicate=lambda r: r.is_terminal)
        items.reverse()
        return items

    def ids(self) -> set[str]:
        with self._lock:
            return set(self._runs)

    def count(self) -> int:
        with self._lock:
            return len(self._runs)

    def _visible(self, run: RunRecord) -> bool:
        if self._root is not None:
            return True
        job_id = _job_id_of(run)
        return job_id is not None and self.job_exists(job_id)

    # ------------------------------------------------------------------ mutations
    def save(self, run: R) -> bool:
        """Persist ``run`` atomically and index it.

        Returns ``False`` (nothing written) for a run that was deleted. Raises :class:`JobDeletedError` when the job
        folder of a part analysis no longer exists (the folder is not recreated) and ``StorageError`` on disk errors.
        """
        if not is_valid_id(run.id, self.prefix) or run.kind != self.kind:
            raise ValueError(f"Invalid {self.kind} run id {run.id!r} (kind {run.kind!r})")
        job_id = _job_id_of(run)
        if self._root is None and (job_id is None or not is_valid_id(job_id, JOB_PREFIX)):
            raise ValueError(f"Invalid job id {job_id!r} for run {run.id}")
        payload = json.dumps(run.model_dump(mode="json"), ensure_ascii=False, indent=2).encode("utf-8")
        with self._lock:
            if run.id in self._deleted:
                logger.info("Not saving %s run %s: it was deleted", self.kind, run.id)
                return False
            run_dir = self._prepare_dir(run, job_id)
            try:
                _write_atomic(run_dir / RUN_FILE, payload)
            except FileNotFoundError as exc:
                if self._root is None and job_id is not None:
                    self._runs.pop(run.id, None)
                    raise JobDeletedError(job_id=job_id) from exc
                raise StorageError(f"Could not write {RUN_FILE}", details={"run_id": run.id}) from exc
            except OSError as exc:
                raise StorageError(f"Could not write {RUN_FILE}", details={"run_id": run.id}) from exc
            self._runs[run.id] = run.model_copy(deep=True)
        return True

    def _prepare_dir(self, run: RunRecord, job_id: str | None) -> Path:
        if self._root is not None:
            try:
                self._root.mkdir(parents=True, exist_ok=True)
                run_dir = self._root / run.id
                run_dir.mkdir(exist_ok=True)
            except OSError as exc:
                raise StorageError("Could not create the run folder", details={"run_id": run.id}) from exc
            return run_dir
        assert job_id is not None
        job_dir = self._paths.job_dir(job_id)
        parts_dir = job_dir / JOB_PARTS_DIR
        run_dir = parts_dir / run.id
        if not self._job_exists(job_id):
            self._runs.pop(run.id, None)
            raise JobDeletedError(job_id=job_id)
        try:
            # parents=False: never recreate jobs/<job>/ when a delete or retention removed it in between.
            parts_dir.mkdir(parents=False, exist_ok=True)
            run_dir.mkdir(parents=False, exist_ok=True)
        except FileNotFoundError as exc:
            self._runs.pop(run.id, None)
            raise JobDeletedError(job_id=job_id) from exc
        except OSError as exc:
            raise StorageError("Could not create the run folder", details={"run_id": run.id}) from exc
        return run_dir

    def delete(self, run_id: str) -> bool:
        """Remove a run's folder and index entry; later saves of that run are refused."""
        if not is_valid_id(run_id, self.prefix):
            return False
        with self._lock:
            run = self._runs.pop(run_id, None)
            if run is None:
                return False
            self._deleted.add(run_id)
        safe_rmtree(self.run_dir(run))
        return True

    def forget_job(self, job_id: str) -> list[str]:
        """Drop every index entry of a job that is being deleted (their folders go with the job)."""
        with self._lock:
            ids = [r.id for r in self._runs.values() if _job_id_of(r) == job_id]
            for run_id in ids:
                self._runs.pop(run_id, None)
                self._deleted.add(run_id)
        return ids


def _job_id_of(run: RunRecord) -> str | None:
    value = getattr(run, "job_id", None)
    return value if isinstance(value, str) else None


def _write_atomic(path: Path, data: bytes) -> None:
    """Temp file + ``os.replace`` in ``path.parent``, which must already exist (no parent is ever created)."""
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
