"""Run framework shared by image generations, segmentations and part analyses (part-aware DESIGN.md section 4.3).

* :mod:`pipelines.runs.executor` - the executor protocol (plan, phases, prepare, finalize), its dependencies and the
  lazy dotted-path executor table;
* :mod:`pipelines.runs.context` - what phase callables see (cancellation, work dir, registry, progress API);
* :mod:`pipelines.runs.reporter` - the thread-safe live run state (throttled publishing, persistence, percent).

The worker that drives executors is :class:`workers.run_worker.RunWorker`.
"""

from __future__ import annotations

from pipelines.runs.context import RunContext, RunProviderRegistry, StepHandle, model_use
from pipelines.runs.executor import (
    DottedPathMissing,
    ExecutorAvailability,
    ExecutorDeps,
    ExecutorTable,
    RunExecutor,
    RunPhase,
    import_dotted,
)
from pipelines.runs.reporter import RunReporter, compute_run_percent

__all__ = [
    "DottedPathMissing",
    "ExecutorAvailability",
    "ExecutorDeps",
    "ExecutorTable",
    "RunContext",
    "RunExecutor",
    "RunPhase",
    "RunProviderRegistry",
    "RunReporter",
    "StepHandle",
    "compute_run_percent",
    "import_dotted",
    "model_use",
]
