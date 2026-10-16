"""What an executor's phase callables see of their run (part-aware DESIGN.md section 4.3).

A :class:`RunContext` is created by the run worker for one run and handed to every phase callable and to
``finalize``. It carries the cancellation token, the run's scratch directory, the configured device and the model
registry, and it is the executor's only way to report progress::

    with ctx.step("generate", "Generating images") as step:
        for i, seed in enumerate(seeds):
            ctx.cancel.raise_if_cancelled()
            ...
            step.progress((i + 1) / len(seeds))

``ctx.step`` marks the step ``running`` and then ``done`` (``warning`` when a warning was recorded for it), ``failed``
on an exception or ``cancelled`` when the token was cancelled. Every method is thread-safe.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from core.exceptions import JobCancelledError
from inference.base import CancellationToken
from models.runs import ModelUse, RunKind
from storage.run_layout import RUN_LOG_FILE

if TYPE_CHECKING:  # pragma: no cover
    from pipelines.runs.reporter import RunReporter

logger = logging.getLogger(__name__)

_MAX_STEP_MESSAGE = 500


class RunProviderRegistry(Protocol):
    """What the run framework needs from ``inference.providers.registry.ProviderRegistry`` (section 5.3)."""

    def guard(self) -> AbstractContextManager[None]:
        """``torch_globals_guard()``: snapshot/restore the torch globals around one GPU callable (GPU thread)."""
        ...

    def ensure_vram(self, model_ids: Sequence[str]) -> None:
        """Live VRAM check on the GPU thread; raises ``InsufficientVramError``."""
        ...

    def offload(self, model_ids: Sequence[str]) -> None:
        """Move the models' weights back to the CPU and empty the CUDA cache (GPU thread)."""
        ...

    def specs(self, kind: Any = None) -> Sequence[Any]:
        """Catalog entries (``ModelSpec``: ``id``, ``revision``, ``license.id``)."""
        ...

    def status(self, model_id: str) -> Any:
        """``ProviderStatus`` (``revision`` is the installed revision when known)."""
        ...


class StepHandle:
    """Handle of the step opened by :meth:`RunContext.step`."""

    __slots__ = ("_ctx", "details", "message", "step_id")

    def __init__(self, ctx: RunContext, step_id: str) -> None:
        self._ctx = ctx
        self.step_id = step_id
        self.details: dict[str, Any] = {}
        """Merged into ``RunStep.details`` when the step ends."""
        self.message: str | None = None
        """Final step message (``None`` keeps the last progress message)."""

    def progress(self, fraction: float, message: str | None = None) -> None:
        self._ctx.progress(self.step_id, fraction, message)

    def warn(self, code: str, message: str) -> None:
        self._ctx.warn(code, message, self.step_id)


class RunContext:
    def __init__(
        self,
        *,
        run_id: str,
        kind: RunKind,
        cancel: CancellationToken,
        work_dir: Path,
        device: str,
        registry: RunProviderRegistry | None,
        reporter: RunReporter[Any],
    ) -> None:
        self.run_id = run_id
        self.kind: RunKind = kind
        self.cancel = cancel
        self.work_dir = work_dir
        self.device = device
        self.registry = registry
        self._reporter = reporter

    @property
    def log_path(self) -> Path:
        """``work_dir/log.txt``: kept as ``jobs/<job>/parts/<pa>/log.txt`` when a part analysis fails."""
        return self.work_dir / RUN_LOG_FILE

    @contextmanager
    def step(self, step_id: str, message: str | None = None) -> Iterator[StepHandle]:
        """Run one step: ``running`` -> ``done`` | ``warning`` | ``failed`` | ``cancelled``."""
        self.cancel.raise_if_cancelled()
        handle = StepHandle(self, step_id)
        self._reporter.start_step(step_id, message)
        try:
            yield handle
        except JobCancelledError:
            self._reporter.finish_step(step_id, "cancelled", "Cancelled", handle.details)
            raise
        except BaseException as exc:
            if self.cancel.is_cancelled:
                self._reporter.finish_step(step_id, "cancelled", "Cancelled", handle.details)
            else:
                text = str(exc).strip() or type(exc).__name__
                self._reporter.finish_step(step_id, "failed", text[:_MAX_STEP_MESSAGE], handle.details)
            raise
        else:
            status = "warning" if self._reporter.has_warning(step_id) else "done"
            self._reporter.finish_step(step_id, status, handle.message, handle.details)

    def progress(self, step_id: str, fraction: float, message: str | None = None) -> None:
        """Fraction (0..1) of a running step; published at most 5/s, droppable."""
        self._reporter.step_progress(step_id, fraction, message)

    def warn(self, code: str, message: str, step_id: str | None = None) -> None:
        """Record a run warning (a warning code of section 6.8); a step that got one ends as ``warning``."""
        self._reporter.warn(code, message, step_id)

    def skip(self, step_id: str, reason: str) -> None:
        """Mark a pending step ``skipped`` with ``reason`` as its message."""
        self._reporter.skip_step(step_id, reason)

    def message(self, text: str) -> None:
        """Replace the run's progress message (published immediately)."""
        self._reporter.set_message(text)

    def use_model(self, model_id: str) -> ModelUse:
        """Record that this run used ``model_id`` (id, revision and licence id come from the registry)."""
        use = model_use(self.registry, model_id)
        self._reporter.use_model(use)
        return use


def model_use(registry: RunProviderRegistry | None, model_id: str) -> ModelUse:
    """Provenance entry of a model: catalog revision/licence, overridden by the installed revision when known."""
    revision: str | None = None
    license_id: str | None = None
    if registry is not None:
        try:
            spec = next((s for s in registry.specs() if getattr(s, "id", None) == model_id), None)
        except Exception:  # noqa: BLE001 - provenance must never fail a run
            logger.warning("Could not read the catalog entry of %s", model_id, exc_info=True)
            spec = None
        if spec is not None:
            revision = getattr(spec, "revision", None)
            license_info = getattr(spec, "license", None)
            license_id = getattr(license_info, "id", None) if license_info is not None else None
        try:
            status = registry.status(model_id)
        except Exception:  # noqa: BLE001
            status = None
        installed = getattr(status, "revision", None) if status is not None else None
        if isinstance(installed, str) and installed:
            revision = installed
    return ModelUse(id=model_id, revision=revision, license_id=license_id)
