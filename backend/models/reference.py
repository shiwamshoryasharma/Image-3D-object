"""Reference set domain model (categorized reference images + primary selection rules)."""

from __future__ import annotations

from pydantic import Field

from models.enums import SINGLE_SLOTS, SLOT_DISPLAY_ORDER, SLOT_PRIMARY_PRIORITY, ReferenceSlot
from models.types import DomainModel, UtcDateTime

_DISPLAY_INDEX = {slot: index for index, slot in enumerate(SLOT_DISPLAY_ORDER)}
_PRIORITY_INDEX = {slot: index for index, slot in enumerate(SLOT_PRIMARY_PRIORITY)}


class ReferenceImage(DomainModel):
    """One uploaded reference image. Files live in ``runtime/uploads/<set_id>/``."""

    id: str
    set_id: str
    slot: ReferenceSlot
    label: str | None = None
    original_filename: str | None = None
    mime_type: str
    width: int
    height: int
    has_alpha: bool
    size_bytes: int
    sha256: str
    is_primary: bool = False
    created_at: UtcDateTime
    file_name: str
    """Stored original file name, e.g. ``img_0123456789ab.png``."""
    thumbnail_name: str
    """Stored thumbnail file name, e.g. ``img_0123456789ab_thumb.webp``."""


class ReferenceSet(DomainModel):
    """A named collection of categorized reference images with exactly one primary (when non-empty)."""

    id: str
    name: str | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime
    images: list[ReferenceImage] = Field(default_factory=list)

    # ------------------------------------------------------------------ queries
    @property
    def primary_image(self) -> ReferenceImage | None:
        return next((img for img in self.images if img.is_primary), None)

    @property
    def primary_image_id(self) -> str | None:
        primary = self.primary_image
        return primary.id if primary else None

    def get_image(self, image_id: str) -> ReferenceImage | None:
        return next((img for img in self.images if img.id == image_id), None)

    def images_in_slot(self, slot: ReferenceSlot) -> list[ReferenceImage]:
        return [img for img in self.images if img.slot == slot]

    def ordered_images(self) -> list[ReferenceImage]:
        """Images ordered by slot display order, then upload time."""
        return sorted(self.images, key=lambda img: (_DISPLAY_INDEX[img.slot], img.created_at, img.id))

    @staticmethod
    def is_single_slot(slot: ReferenceSlot) -> bool:
        return slot in SINGLE_SLOTS

    # ------------------------------------------------------------------ primary rules
    def set_primary(self, image_id: str) -> None:
        """Make ``image_id`` the only primary image. Caller guarantees the image exists."""
        for img in self.images:
            img.is_primary = img.id == image_id

    def ensure_primary(self) -> None:
        """Guarantee exactly one primary when non-empty (auto-promote by slot priority, then upload order)."""
        primaries = [img for img in self.images if img.is_primary]
        if len(primaries) == 1:
            return
        if len(primaries) > 1:
            keep = min(primaries, key=self._promotion_key)
            self.set_primary(keep.id)
            return
        if self.images:
            candidate = min(self.images, key=self._promotion_key)
            self.set_primary(candidate.id)

    @staticmethod
    def _promotion_key(img: ReferenceImage) -> tuple[int, object, str]:
        return (_PRIORITY_INDEX[img.slot], img.created_at, img.id)
