"""The part consumer seam (part-aware DESIGN.md section 3.13; slice S3b).

A *part consumer* is a future, approved refinement or tuning component that reads a published part layer through
:class:`parts.reader.AssetReader` and declares which refinement ``needs`` (section 3.9) it can supply. This milestone
ships **zero** implementations: :class:`PartConsumerRegistry` refuses every registration while part refinement is
disabled (``FeatureDisabledError``, feature ``part_refinement``), so ``list()`` is always ``()`` and every manifest says
``refinement.consumers == []``.

Torch-free: no numpy needed.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from core.feature_errors import FEATURE_FLAGS, FeatureDisabledError
from parts.manifest import ConsumerRecord, PartRecord, PartsManifest
from parts.types import RefinementNeed

REFINEMENT_FEATURE: Final = "part_refinement"
REFINEMENT_FLAG_ENV: Final = FEATURE_FLAGS[REFINEMENT_FEATURE][1]


@dataclass(frozen=True, slots=True)
class ConsumerInfo:
    """What a consumer says about itself: ``{id, label, version, handles_needs}``."""

    id: str
    label: str
    version: str
    handles_needs: tuple[RefinementNeed, ...] = ()

    def to_record(self) -> ConsumerRecord:
        """The manifest ``refinement.consumers[]`` entry."""
        return ConsumerRecord(id=self.id, label=self.label, version=self.version,
                              handles_needs=list(self.handles_needs))


@runtime_checkable
class PartConsumer(Protocol):
    """A future part consumer (tuning, refinement, DCC export). It only reads through ``AssetReader``."""

    id: str

    def accepts(self, part: PartRecord, manifest: PartsManifest) -> bool: ...

    def describe(self) -> ConsumerInfo: ...


class PartConsumerRegistry:
    """Registry of part consumers; it refuses every registration while part refinement is disabled."""

    def __init__(self, *, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._consumers: dict[str, PartConsumer] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def register(self, consumer: PartConsumer) -> None:
        """Add ``consumer``. Raises :class:`FeatureDisabledError` while refinement is disabled and
        :class:`ValueError` for a duplicate id or an id that differs from ``describe().id``."""
        if not self._enabled:
            raise FeatureDisabledError(
                f"Part refinement is disabled ({REFINEMENT_FLAG_ENV}=false); part consumers cannot be registered",
                feature=REFINEMENT_FEATURE,
                flag=REFINEMENT_FLAG_ENV,
            )
        if not isinstance(consumer, PartConsumer):
            raise TypeError("a part consumer needs an 'id', accepts() and describe()")
        info = consumer.describe()
        if info.id != consumer.id:
            raise ValueError(f"consumer id {consumer.id!r} differs from describe().id {info.id!r}")
        if consumer.id in self._consumers:
            raise ValueError(f"a part consumer with id {consumer.id!r} is already registered")
        self._consumers[consumer.id] = consumer

    def list(self) -> tuple[ConsumerInfo, ...]:
        """Every registered consumer, in registration order (``()`` in this milestone)."""
        return tuple(consumer.describe() for consumer in self._consumers.values())

    def consumers_for(self, part: PartRecord, manifest: PartsManifest) -> tuple[PartConsumer, ...]:
        """The registered consumers that accept ``part`` (none while disabled)."""
        return tuple(consumer for consumer in self._consumers.values() if consumer.accepts(part, manifest))

    def manifest_records(self) -> tuple[ConsumerRecord, ...]:
        return tuple(info.to_record() for info in self.list())

    def __iter__(self) -> Iterator[PartConsumer]:
        return iter(tuple(self._consumers.values()))

    def __len__(self) -> int:
        return len(self._consumers)


__all__ = [
    "REFINEMENT_FEATURE",
    "REFINEMENT_FLAG_ENV",
    "ConsumerInfo",
    "PartConsumer",
    "PartConsumerRegistry",
]
