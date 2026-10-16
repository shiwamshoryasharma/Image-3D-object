"""Engine used when the real engine cannot even be imported (torch / CUDA / native extensions broken).

This is not a fake: it never produces output. It lets the API start and report *why* generation is
unavailable (``GET /system`` → ``engine.state == "unavailable"``) instead of crashing at import time.
"""

from __future__ import annotations

from core.exceptions import EngineUnavailableError
from inference.base import (
    CancellationToken,
    EngineCapabilities,
    EngineStatus,
    GenerationOutput,
    GenerationRequest,
    InferenceEngine,
    ProgressReporter,
    StatusCallback,
)
from models.enums import EngineState


class UnavailableEngine(InferenceEngine):
    def __init__(self, name: str, reason: str, *, details: dict[str, object] | None = None) -> None:
        self.name = name
        self._reason = reason
        self._status = EngineStatus(
            state=EngineState.UNAVAILABLE,
            message="Inference engine is unavailable",
            error=reason,
            details=dict(details or {}),
        )

    @property
    def reason(self) -> str:
        return self._reason

    def status(self) -> EngineStatus:
        return self._status

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities()

    def load(self, on_status: StatusCallback | None = None) -> None:
        # Nothing can be loaded; re-announce the unavailable status so callers observe a terminal state.
        if on_status is not None:
            on_status(self._status)

    def unload(self) -> None:
        return None

    def generate(
        self,
        request: GenerationRequest,
        reporter: ProgressReporter,
        cancel: CancellationToken,
    ) -> GenerationOutput:
        raise EngineUnavailableError(
            f"Inference engine '{self.name}' is unavailable: {self._reason}",
            details={"reason": self._reason},
        )
