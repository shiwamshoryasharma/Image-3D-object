"""Reference strategies decide which job references are handed to the engine as conditioning images.

The data model always keeps every categorized reference; the strategy only selects. ``primary_only`` is what
TRELLIS.2 supports. ``multi_view`` is a placeholder for engines that condition on several views.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from core.exceptions import ValidationAppError
from inference.base import ConditioningImage, EngineCapabilities
from models.enums import SLOT_PRIMARY_PRIORITY, ReferenceMode

_PRIORITY = {slot: index for index, slot in enumerate(SLOT_PRIMARY_PRIORITY)}


def _primary_first(references: Sequence[ConditioningImage]) -> list[ConditioningImage]:
    primary = [ref for ref in references if ref.is_primary]
    if not primary:
        raise ValidationAppError("The job has no primary reference image")
    others = sorted((ref for ref in references if not ref.is_primary), key=lambda ref: _PRIORITY[ref.slot])
    return [primary[0], *others]


class ReferenceStrategy(ABC):
    mode: ReferenceMode

    def validate(self, capabilities: EngineCapabilities) -> None:
        """Raise :class:`ValidationAppError` if the engine cannot run this strategy."""
        if self.mode not in capabilities.reference_modes:
            raise ValidationAppError(
                f"Reference mode '{self.mode}' is not supported by the current engine",
                details={"reference_mode": str(self.mode), "supported": [str(m) for m in capabilities.reference_modes]},
            )

    @abstractmethod
    def select(
        self, references: Sequence[ConditioningImage], capabilities: EngineCapabilities
    ) -> list[ConditioningImage]:
        """Return the conditioning images (primary first)."""


class PrimaryOnlyStrategy(ReferenceStrategy):
    mode = ReferenceMode.PRIMARY_ONLY

    def select(
        self, references: Sequence[ConditioningImage], capabilities: EngineCapabilities
    ) -> list[ConditioningImage]:
        return _primary_first(references)[:1]


class MultiViewStrategy(ReferenceStrategy):
    mode = ReferenceMode.MULTI_VIEW

    def validate(self, capabilities: EngineCapabilities) -> None:
        if capabilities.max_reference_images <= 1:
            raise ValidationAppError("multi_view reference mode is not supported by the current engine")
        super().validate(capabilities)

    def select(
        self, references: Sequence[ConditioningImage], capabilities: EngineCapabilities
    ) -> list[ConditioningImage]:
        self.validate(capabilities)
        return _primary_first(references)[: capabilities.max_reference_images]


_STRATEGIES: dict[ReferenceMode, type[ReferenceStrategy]] = {
    ReferenceMode.PRIMARY_ONLY: PrimaryOnlyStrategy,
    ReferenceMode.MULTI_VIEW: MultiViewStrategy,
}


def get_strategy(mode: ReferenceMode | str) -> ReferenceStrategy:
    try:
        return _STRATEGIES[ReferenceMode(mode)]()
    except (KeyError, ValueError) as exc:
        raise ValidationAppError(f"Unknown reference mode '{mode}'") from exc
