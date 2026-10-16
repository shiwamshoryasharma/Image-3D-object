"""Reference set persistence: ``runtime/uploads/<set_id>/set.json`` plus the image files next to it.

The store keeps an in-memory index (loaded at startup) and is thread-safe. It hands out deep copies so callers
can mutate a set and :meth:`ReferenceStore.save` it without racing other readers.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from core.exceptions import StorageError
from models.reference import ReferenceImage, ReferenceSet
from storage.json_io import read_json, write_json_atomic
from storage.paths import SET_METADATA_FILE, RuntimePaths
from utils.files import atomic_write_bytes, safe_rmtree, safe_unlink
from utils.ids import REFERENCE_SET_PREFIX, is_valid_id

logger = logging.getLogger(__name__)


class ReferenceStore:
    def __init__(self, paths: RuntimePaths) -> None:
        self._paths = paths
        self._sets: dict[str, ReferenceSet] = {}
        self._lock = threading.RLock()

    @property
    def paths(self) -> RuntimePaths:
        return self._paths

    # ------------------------------------------------------------------ loading
    def load(self) -> int:
        """(Re)load every set from disk. Corrupt sets are skipped with a warning."""
        loaded: dict[str, ReferenceSet] = {}
        if self._paths.uploads.exists():
            for directory in self._paths.uploads.iterdir():
                if not directory.is_dir() or not is_valid_id(directory.name, REFERENCE_SET_PREFIX):
                    continue
                meta = directory / SET_METADATA_FILE
                if not meta.exists():
                    continue
                try:
                    ref_set = ReferenceSet.model_validate(read_json(meta))
                except Exception as exc:  # noqa: BLE001 - one broken set must not break startup
                    logger.warning("Skipping unreadable reference set %s: %s", directory.name, exc)
                    continue
                if ref_set.id != directory.name:
                    logger.warning("Skipping reference set with mismatched id in %s", directory)
                    continue
                loaded[ref_set.id] = ref_set
        with self._lock:
            self._sets = loaded
        logger.info("Loaded %d reference set(s)", len(loaded))
        return len(loaded)

    # ------------------------------------------------------------------ queries
    def get(self, set_id: str) -> ReferenceSet | None:
        if not is_valid_id(set_id, REFERENCE_SET_PREFIX):
            return None
        with self._lock:
            ref_set = self._sets.get(set_id)
            return ref_set.model_copy(deep=True) if ref_set else None

    def list(self) -> list[ReferenceSet]:
        """All sets, newest first."""
        with self._lock:
            items = [s.model_copy(deep=True) for s in self._sets.values()]
        items.sort(key=lambda s: (s.created_at, s.id), reverse=True)
        return items

    def count(self) -> int:
        with self._lock:
            return len(self._sets)

    def ids(self) -> set[str]:
        """Ids of every indexed set (cheap: no copies)."""
        with self._lock:
            return set(self._sets)

    # ------------------------------------------------------------------ mutations
    def save(self, ref_set: ReferenceSet) -> None:
        """Persist ``ref_set`` (atomic JSON write) and update the index."""
        with self._lock:
            set_dir = self._paths.set_dir(ref_set.id)
            set_dir.mkdir(parents=True, exist_ok=True)
            write_json_atomic(self._paths.set_metadata(ref_set.id), ref_set.model_dump(mode="json"))
            self._sets[ref_set.id] = ref_set.model_copy(deep=True)

    def delete(self, set_id: str) -> bool:
        """Delete a set and all of its files. Returns ``False`` if it did not exist."""
        if not is_valid_id(set_id, REFERENCE_SET_PREFIX):
            return False
        with self._lock:
            existed = self._sets.pop(set_id, None) is not None
        safe_rmtree(self._paths.set_dir(set_id))
        return existed

    # ------------------------------------------------------------------ files
    def image_path(self, image: ReferenceImage) -> Path:
        return self._paths.set_dir(image.set_id) / image.file_name

    def thumbnail_path(self, image: ReferenceImage) -> Path:
        return self._paths.set_dir(image.set_id) / image.thumbnail_name

    def write_image_files(self, image: ReferenceImage, original: bytes, thumbnail: bytes) -> None:
        try:
            atomic_write_bytes(self.image_path(image), original)
            atomic_write_bytes(self.thumbnail_path(image), thumbnail)
        except OSError as exc:
            self.delete_image_files(image)
            raise StorageError("Could not store the uploaded image") from exc

    def delete_image_files(self, image: ReferenceImage) -> None:
        safe_unlink(self.image_path(image))
        safe_unlink(self.thumbnail_path(image))
