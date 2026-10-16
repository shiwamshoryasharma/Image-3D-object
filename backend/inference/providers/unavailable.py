"""Honest stand-in for a provider whose implementation could not be imported (part-aware DESIGN.md section 1.4).

:meth:`inference.providers.registry.ProviderRegistry.provider` imports implementation modules lazily. When that
import (or the construction) raises - a missing implementation module, a broken CUDA DLL raising ``OSError``, a
missing attribute - the registry keeps an :class:`UnavailableProvider` in its place, so the model reports the
failure (``reason = "<ExceptionType>: <message>"``) instead of breaking startup or pretending to work. Every operation
of it raises :class:`~core.feature_errors.ModelUnavailableError`; ``offload``/``unload`` are no-ops. Torch-free.
"""

from __future__ import annotations

from typing import Final

from core.feature_errors import ModelUnavailableError
from inference.base import CancellationToken
from inference.providers.base import ModelProvider, ModelSpec, ModelUiState, ProviderStatus, ReasonCode

UNAVAILABLE_REASON_CODES: Final = frozenset({"error", "not_implemented"})


class UnavailableProvider(ModelProvider):
    """A model whose implementation is not importable: state ``error`` (``reason_code="error"``) or
    ``unavailable`` (``reason_code="not_implemented"``, the implementation module is not part of this build)."""

    def __init__(self, spec: ModelSpec, *, reason_code: ReasonCode = "error", reason: str) -> None:
        if reason_code not in UNAVAILABLE_REASON_CODES:
            raise ValueError(f"UnavailableProvider reason_code must be one of {sorted(UNAVAILABLE_REASON_CODES)}")
        super().__init__(spec)
        self.reason_code: ReasonCode = reason_code
        self.reason = reason

    @property
    def state(self) -> ModelUiState:
        return "error" if self.reason_code == "error" else "unavailable"

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            model_id=self.spec.id, state=self.state, reason_code=self.reason_code, reason=self.reason
        )

    def _refuse(self) -> ModelUnavailableError:
        return ModelUnavailableError(
            f"{self.spec.display_name} is not available: {self.reason}",
            model_id=self.spec.id,
            state=self.state,
            reason_code=self.reason_code,
        )

    def load(self, cancel: CancellationToken) -> None:
        raise self._refuse()

    def warm(self, cancel: CancellationToken) -> None:
        raise self._refuse()

    def offload(self) -> None:
        return None

    def unload(self) -> None:
        return None
