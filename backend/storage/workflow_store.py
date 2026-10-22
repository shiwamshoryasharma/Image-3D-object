"""Workflow persistence: ``runtime/workflows/<wf>/workflow.json`` (+ ``graph.json`` / ``layout.json``) (part-aware
DESIGN.md sections 4.7 and 7.2).

Slice S1 froze the API that retention (S8) codes against (:meth:`WorkflowStore.list_terminal`,
:meth:`WorkflowStore.delete`, :meth:`WorkflowStore.active_child_ids`, plus ``load`` / ``recover_interrupted`` /
``layout`` for the container); slice S11 implements it here and adds what the workflow service needs (get, list,
save, the graph files and the child index).

The store is an in-memory index over the per-workflow folders, guarded by one re-entrant lock: retention calls it from
a worker thread and the workflow service from the event loop. ``active_child_ids`` and ``workflow_for_child`` are
answered from a cache that is rebuilt on every change, so the per-candidate retention check stays cheap. Saves are
atomic (temp file + ``os.replace``); a non-durable save only updates the index (progress between status changes).
A deleted workflow is never written again.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

from core.exceptions import StorageError
from models.enums import JobStatus
from models.job import JobError
from models.workflow import INTERRUPTED_ERROR_CODE, WORKFLOW_PREFIX, WorkflowRecord
from storage.json_io import read_json
from storage.paths import RuntimePaths
from storage.run_layout import RunLayout
from utils.files import atomic_write_bytes, safe_rmtree
from utils.ids import is_valid_id
from utils.timeutil import utcnow

logger = logging.getLogger(__name__)

INTERRUPTED_MESSAGE = "The server stopped before this workflow finished"


class WorkflowStore:
    """Frozen S1 API (``list_terminal`` / ``delete`` / ``active_child_ids``) plus the S11 service operations."""

    def __init__(self, paths: RuntimePaths) -> None:
        self._layout = RunLayout(paths)
        self._records: dict[str, WorkflowRecord] = {}
        self._deleted: set[str] = set()
        self._child_owner: dict[str, str] = {}
        self._active_children: frozenset[str] = frozenset()
        self._lock = threading.RLock()

    @property
    def layout(self) -> RunLayout:
        return self._layout

    # ------------------------------------------------------------------ startup
    def load(self) -> int:
        """Index the persisted workflows (unreadable or mismatched folders are skipped); returns how many loaded."""
        loaded: dict[str, WorkflowRecord] = {}
        root = self._layout.workflows_root
        if root.is_dir():
            for wf_dir in root.iterdir():
                if not wf_dir.is_dir() or not is_valid_id(wf_dir.name, WORKFLOW_PREFIX):
                    continue
                meta = self._layout.workflow_file(wf_dir.name)
                if not meta.is_file():
                    continue
                try:
                    record = WorkflowRecord.model_validate(read_json(meta))
                except Exception as exc:  # noqa: BLE001 - one broken workflow must not break startup
                    logger.warning("Skipping unreadable workflow %s: %s", wf_dir.name, exc)
                    continue
                if record.id != wf_dir.name:
                    logger.warning("Skipping workflow with a mismatched id in %s", wf_dir)
                    continue
                loaded[record.id] = record
        with self._lock:
            self._records = loaded
            self._deleted.clear()
            self._reindex()
        logger.info("Loaded %d workflow(s)", len(loaded))
        return len(loaded)

    def recover_interrupted(self) -> list[WorkflowRecord]:
        """Mark workflows left ``queued``/``running`` by a previous process as failed (``interrupted``).

        Their children are recovered by their own stores (runs) or by the job system.
        """
        with self._lock:
            candidates = [r.model_copy(deep=True) for r in self._records.values() if r.is_active]
        recovered: list[WorkflowRecord] = []
        for record in candidates:
            previous = record.status
            now = utcnow()
            record.status = JobStatus.FAILED
            record.finished_at = now
            record.error = JobError(
                code=INTERRUPTED_ERROR_CODE,
                message=INTERRUPTED_MESSAGE,
                details={"previous_status": str(previous), "step": record.current_step},
            )
            for step in record.steps:
                if step.status == "running":
                    step.status = "failed"
                    step.message = "Interrupted"
                    step.finished_at = now
                elif step.status == "pending":
                    step.status = "skipped"
            record.progress.message = "Interrupted"
            record.progress.updated_at = now
            try:
                self.save(record)
            except StorageError:
                logger.exception("Could not mark workflow %s as interrupted", record.id)
                continue
            recovered.append(record)
            logger.warning("Workflow %s was %s at shutdown; marked failed (interrupted)", record.id, previous)
        return recovered

    # ------------------------------------------------------------------ queries
    def get(self, wf_id: str) -> WorkflowRecord | None:
        if not is_valid_id(wf_id, WORKFLOW_PREFIX):
            return None
        with self._lock:
            record = self._records.get(wf_id)
            return record.model_copy(deep=True) if record is not None else None

    def list_runs(
        self,
        *,
        status: JobStatus | None = None,
        predicate: Callable[[WorkflowRecord], bool] | None = None,
        limit: int | None = None,
    ) -> list[WorkflowRecord]:
        """Matching workflows, newest first."""
        with self._lock:
            items = [r.model_copy(deep=True) for r in self._records.values()]
        items = [r for r in items if (status is None or r.status is status) and (predicate is None or predicate(r))]
        items.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        return items[:limit] if limit is not None else items

    def list_active(self) -> list[WorkflowRecord]:
        return self.list_runs(predicate=lambda r: r.is_active)

    def list_terminal(self) -> list[WorkflowRecord]:
        """Completed, failed and cancelled workflows, oldest first (retention candidates)."""
        items = self.list_runs(predicate=lambda r: r.is_terminal)
        items.reverse()
        return items

    def active_child_ids(self) -> frozenset[str]:
        """``ig_``/``sg_``/``pa_``/``job_`` ids referenced by queued or running workflows (retention skips them)."""
        with self._lock:
            return self._active_children

    def workflow_for_child(self, child_id: str) -> str | None:
        """The active workflow that created or uses ``child_id`` (event routing)."""
        with self._lock:
            return self._child_owner.get(child_id)

    def count(self) -> int:
        with self._lock:
            return len(self._records)

    # ------------------------------------------------------------------ mutations
    def save(self, record: WorkflowRecord, *, durable: bool = True) -> bool:
        """Index ``record`` and (when ``durable``) write ``workflow.json`` atomically.

        Returns ``False`` (nothing written) for a deleted workflow; raises ``StorageError`` on disk errors.
        """
        if not is_valid_id(record.id, WORKFLOW_PREFIX):
            raise ValueError(f"Invalid workflow id {record.id!r}")
        payload = (
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2).encode("utf-8")
            if durable
            else b""
        )
        with self._lock:
            if record.id in self._deleted:
                logger.info("Not saving workflow %s: it was deleted", record.id)
                return False
            if durable:
                try:
                    atomic_write_bytes(self._layout.workflow_file(record.id), payload)
                except OSError as exc:
                    raise StorageError("Could not write workflow.json", details={"workflow_id": record.id}) from exc
            self._records[record.id] = record.model_copy(deep=True)
            self._reindex()
        return True

    def write_graph(self, wf_id: str, graph: dict[str, Any] | None, layout: dict[str, Any] | None) -> None:
        """Store the posted Node-UI ``graph.json`` / ``layout.json`` next to the record (before its first save)."""
        files = ((self._layout.workflow_graph_file(wf_id), graph), (self._layout.workflow_layout_file(wf_id), layout))
        try:
            for path, value in files:
                if value is not None:
                    atomic_write_bytes(path, json.dumps(value, ensure_ascii=False).encode("utf-8"))
        except OSError as exc:
            raise StorageError("Could not write the workflow graph", details={"workflow_id": wf_id}) from exc

    def read_graph(self, wf_id: str) -> tuple[Any | None, Any | None]:
        """``(graph, layout)`` as stored (``None`` for a missing file)."""
        values: list[Any | None] = []
        for path in (self._layout.workflow_graph_file(wf_id), self._layout.workflow_layout_file(wf_id)):
            try:
                values.append(read_json(path) if path.is_file() else None)
            except Exception as exc:  # noqa: BLE001 - an unreadable file reads as missing
                logger.warning("Unreadable %s of workflow %s: %s", path.name, wf_id, exc)
                values.append(None)
        return values[0], values[1]

    def delete(self, wf_id: str) -> bool:
        """Remove a terminal workflow record and its folder. ``False`` when unknown or still active."""
        if not is_valid_id(wf_id, WORKFLOW_PREFIX):
            return False
        with self._lock:
            record = self._records.get(wf_id)
            if record is None or record.is_active:
                return False
            self._forget(wf_id)
        safe_rmtree(self._layout.workflow_dir(wf_id))
        return True

    def discard(self, wf_id: str) -> None:
        """Remove a workflow whatever its state (its creation was refused, so it never existed for the client)."""
        with self._lock:
            self._forget(wf_id)
        safe_rmtree(self._layout.workflow_dir(wf_id))

    # ------------------------------------------------------------------ internals
    def _forget(self, wf_id: str) -> None:
        self._records.pop(wf_id, None)
        self._deleted.add(wf_id)
        self._reindex()

    def _reindex(self) -> None:
        owners: dict[str, str] = {}
        for record in self._records.values():
            if record.is_active:
                for child_id in record.child_ids():
                    owners[child_id] = record.id
        self._child_owner = owners
        self._active_children = frozenset(owners)
