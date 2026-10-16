"""Reference set use-cases: CRUD, validated uploads, slot rules and primary selection (contract section 6)."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePath

from config.settings import Settings
from core.exceptions import ConflictError, NotFoundError, ValidationAppError
from models.enums import SINGLE_SLOTS, ReferenceSlot
from models.reference import ImageSource, ReferenceImage, ReferenceSet
from processing.image_ops import make_thumbnail_webp
from processing.image_validation import validate_image_bytes
from schemas.references import UpdateReferenceImageRequest
from storage.reference_store import ReferenceStore
from utils.files import sha256_bytes
from utils.ids import new_image_id, new_reference_set_id
from utils.timeutil import utcnow

logger = logging.getLogger(__name__)

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_MAX_FILENAME = 255


@dataclass(frozen=True, slots=True)
class StoredFile:
    path: Path
    media_type: str
    filename: str | None = None


def sanitize_filename(name: str | None) -> str | None:
    """Keep only the base name of a client-supplied file name, without control characters."""
    if not name:
        return None
    base = PurePath(name.replace("\\", "/")).name
    base = _CONTROL_CHARS.sub("", base).strip()
    return base[:_MAX_FILENAME] or None


def _clean_label(label: str | None) -> str | None:
    if label is None:
        return None
    label = _CONTROL_CHARS.sub(" ", label).strip()
    return label or None


class ReferenceService:
    def __init__(self, settings: Settings, store: ReferenceStore) -> None:
        self._settings = settings
        self._store = store
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------ locking
    @asynccontextmanager
    async def locked_set(self, set_id: str) -> AsyncIterator[ReferenceSet]:
        """Hold the per-set lock (serializes mutations and job input snapshots) and yield a fresh copy.

        Raises ``NotFoundError`` before any lock is created, so unknown ids never allocate locks.
        """
        self.get_set(set_id)
        lock = self._locks.get(set_id)
        if lock is None:
            lock = self._locks[set_id] = asyncio.Lock()
        async with lock:
            yield self.get_set(set_id)

    # ------------------------------------------------------------------ sets
    def create_set(self, name: str | None) -> ReferenceSet:
        now = utcnow()
        ref_set = ReferenceSet(id=new_reference_set_id(), name=_clean_label(name), created_at=now, updated_at=now)
        self._store.save(ref_set)
        logger.info("Reference set %s created", ref_set.id)
        return ref_set

    def list_sets(self) -> list[ReferenceSet]:
        return self._store.list()

    def get_set(self, set_id: str) -> ReferenceSet:
        ref_set = self._store.get(set_id)
        if ref_set is None:
            raise NotFoundError("Reference set not found", details={"set_id": set_id})
        return ref_set

    async def delete_set(self, set_id: str) -> None:
        async with self.locked_set(set_id):
            await asyncio.to_thread(self._store.delete, set_id)
        self._locks.pop(set_id, None)
        logger.info("Reference set %s deleted", set_id)

    async def delete_set_if_stale(self, set_id: str, cutoff: datetime) -> bool:
        """Retention: delete the set only if it was not updated since ``cutoff``.

        Runs under the per-set lock and re-reads the set, so it never races an upload, an edit or a job snapshotting
        the set's images. Returns ``True`` if the set was deleted.
        """
        try:
            async with self.locked_set(set_id) as ref_set:
                if ref_set.updated_at >= cutoff:
                    return False
                await asyncio.to_thread(self._store.delete, set_id)
        except NotFoundError:
            return False
        self._locks.pop(set_id, None)
        logger.info("Reference set %s deleted by retention (last updated %s)", set_id, ref_set.updated_at)
        return True

    # ------------------------------------------------------------------ images
    def _get_image(self, ref_set: ReferenceSet, image_id: str) -> ReferenceImage:
        image = ref_set.get_image(image_id)
        if image is None:
            raise NotFoundError("Image not found in this reference set", details={"image_id": image_id})
        return image

    def _ensure_detail_capacity(self, ref_set: ReferenceSet) -> None:
        limit = self._settings.max_detail_images
        if len(ref_set.images_in_slot(ReferenceSlot.DETAIL)) >= limit:
            raise ValidationAppError(
                f"The detail slot already holds the maximum of {limit} images",
                details={"slot": str(ReferenceSlot.DETAIL), "max_detail_images": limit},
            )

    async def upload_image(
        self,
        set_id: str,
        *,
        data: bytes,
        filename: str | None,
        slot: ReferenceSlot,
        label: str | None = None,
        make_primary: bool = False,
        source: ImageSource | None = None,
    ) -> tuple[ReferenceImage, ReferenceSet]:
        """Validate and store an image. Single slots are replaced (primary flag carries over).

        ``source`` records where a generated image came from (e.g. a text-to-image candidate); uploads leave it
        ``None``.
        """
        async with self.locked_set(set_id) as ref_set:
            replaced: ReferenceImage | None = None
            if slot in SINGLE_SLOTS:
                existing = ref_set.images_in_slot(slot)
                replaced = existing[0] if existing else None
            else:
                self._ensure_detail_capacity(ref_set)

            info = await asyncio.to_thread(
                validate_image_bytes,
                data,
                min_side=self._settings.min_image_side,
                max_pixels=self._settings.max_image_pixels,
            )
            thumbnail = await asyncio.to_thread(make_thumbnail_webp, data)

            image_id = new_image_id()
            image = ReferenceImage(
                id=image_id,
                set_id=ref_set.id,
                slot=slot,
                label=_clean_label(label),
                original_filename=sanitize_filename(filename),
                mime_type=info.mime_type,
                width=info.width,
                height=info.height,
                has_alpha=info.has_alpha,
                size_bytes=len(data),
                sha256=sha256_bytes(data),
                is_primary=False,
                created_at=utcnow(),
                file_name=f"{image_id}{info.extension}",
                thumbnail_name=f"{image_id}_thumb.webp",
                source=source.model_copy(deep=True) if source is not None else None,
            )
            await asyncio.to_thread(self._store.write_image_files, image, data, thumbnail)

            ref_set.images.append(image)
            if replaced is not None:
                ref_set.images = [img for img in ref_set.images if img.id != replaced.id]
                if replaced.is_primary:
                    ref_set.set_primary(image.id)
            if make_primary:
                ref_set.set_primary(image.id)
            ref_set.ensure_primary()
            ref_set.updated_at = utcnow()
            try:
                self._store.save(ref_set)
            except Exception:
                self._store.delete_image_files(image)
                raise
            if replaced is not None:
                await asyncio.to_thread(self._store.delete_image_files, replaced)
                logger.info("Image %s replaced %s in slot %s of set %s", image.id, replaced.id, slot, set_id)
            else:
                logger.info("Image %s uploaded to slot %s of set %s", image.id, slot, set_id)
            stored = ref_set.get_image(image.id)
            assert stored is not None
            return stored, ref_set

    async def update_image(self, set_id: str, image_id: str, patch: UpdateReferenceImageRequest) -> ReferenceSet:
        async with self.locked_set(set_id) as ref_set:
            image = self._get_image(ref_set, image_id)
            if patch.slot is not None and patch.slot != image.slot:
                if patch.slot in SINGLE_SLOTS:
                    occupant = next((img for img in ref_set.images_in_slot(patch.slot) if img.id != image.id), None)
                    if occupant is not None:
                        raise ConflictError(
                            f"Slot '{patch.slot}' is already occupied",
                            details={"slot": str(patch.slot), "image_id": occupant.id},
                        )
                else:
                    self._ensure_detail_capacity(ref_set)
                image.slot = patch.slot
            if "label" in patch.model_fields_set:
                image.label = _clean_label(patch.label)
            ref_set.updated_at = utcnow()
            self._store.save(ref_set)
            return ref_set

    async def delete_image(self, set_id: str, image_id: str) -> ReferenceSet:
        async with self.locked_set(set_id) as ref_set:
            image = self._get_image(ref_set, image_id)
            ref_set.images = [img for img in ref_set.images if img.id != image_id]
            ref_set.ensure_primary()
            ref_set.updated_at = utcnow()
            self._store.save(ref_set)
            await asyncio.to_thread(self._store.delete_image_files, image)
            logger.info("Image %s deleted from set %s", image_id, set_id)
            return ref_set

    async def set_primary(self, set_id: str, image_id: str) -> ReferenceSet:
        async with self.locked_set(set_id) as ref_set:
            self._get_image(ref_set, image_id)
            ref_set.set_primary(image_id)
            ref_set.updated_at = utcnow()
            self._store.save(ref_set)
            return ref_set

    # ------------------------------------------------------------------ files
    def image_file(self, set_id: str, image_id: str) -> StoredFile:
        ref_set = self.get_set(set_id)
        image = self._get_image(ref_set, image_id)
        path = self._store.image_path(image)
        if not path.is_file():
            raise NotFoundError("Image file is missing", details={"image_id": image_id})
        return StoredFile(path=path, media_type=image.mime_type, filename=image.original_filename)

    def thumbnail_file(self, set_id: str, image_id: str) -> StoredFile:
        ref_set = self.get_set(set_id)
        image = self._get_image(ref_set, image_id)
        path = self._store.thumbnail_path(image)
        if not path.is_file():
            raise NotFoundError("Thumbnail is missing", details={"image_id": image_id})
        return StoredFile(path=path, media_type="image/webp")

    def original_path(self, image: ReferenceImage) -> Path:
        return self._store.image_path(image)
