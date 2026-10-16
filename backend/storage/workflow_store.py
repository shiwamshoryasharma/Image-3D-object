"""Workflow persistence: ``runtime/workflows/<wf>/workflow.json`` (+ ``graph.json`` / ``layout.json``) (part-aware
DESIGN.md sections 4.7 and 7.2).

Slice S1 freezes the API that retention (S8) codes against; slice S11 implements the store behind it. Until S11 merges
no workflow can be created (``POST /workflows`` is not implemented), so this stub truthfully reports an empty store:
:meth:`WorkflowStore.list_terminal` returns ``[]``, :meth:`WorkflowStore.delete` returns ``False`` and
:meth:`WorkflowStore.active_child_ids` returns ``frozenset()``. It never reads or writes the disk.
"""

from __future__ import annotations

import logging

from models.workflow import WorkflowRecord
from storage.paths import RuntimePaths
from storage.run_layout import RunLayout

logger = logging.getLogger(__name__)


class WorkflowStore:
    """Frozen API (S1 stub; S11 implements it with the same signatures)."""

    def __init__(self, paths: RuntimePaths) -> None:
        self._layout = RunLayout(paths)

    @property
    def layout(self) -> RunLayout:
        return self._layout

    def load(self) -> int:
        """Index the persisted workflows; returns how many were loaded (the stub persists none)."""
        return 0

    def recover_interrupted(self) -> list[WorkflowRecord]:
        """Mark workflows left ``queued``/``running`` by a previous process as failed (``interrupted``)."""
        return []

    def list_terminal(self) -> list[WorkflowRecord]:
        """Completed, failed and cancelled workflows, oldest first (retention candidates)."""
        return []

    def delete(self, wf_id: str) -> bool:
        """Remove a terminal workflow record and its folder. ``False`` when unknown or still active."""
        logger.debug("Workflow store stub: nothing to delete for %s", wf_id)
        return False

    def active_child_ids(self) -> frozenset[str]:
        """``ig_``/``sg_``/``pa_``/``job_`` ids referenced by queued or running workflows (retention skips them)."""
        return frozenset()
