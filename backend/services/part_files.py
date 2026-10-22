"""On-demand part-analysis files: per-part GLBs and ZIP bundles (part-aware DESIGN.md sections 6.4 and 7.2).

Published assets under ``jobs/<job>/parts/<pa>/asset/`` are immutable (I12): everything built on demand goes to
``runtime/cache/part_exports/<pa>/``. Builders run with ``asyncio.to_thread`` under a per-(analysis, file)
``asyncio.Lock`` and write to a temp name that is ``os.replace``d into place, so concurrent requests build a file
once and never see a partial one. :meth:`PartFiles.purge` removes the cache of one analysis (job-delete hook and
``DELETE /part-analyses/{id}``).

:func:`load_manifest` parses (and caches, per immutable asset) a published ``manifest.json``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import zipfile
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Final

from config.settings import Settings
from core.exceptions import ConflictError, NotFoundError
from models.enums import JobStatus
from models.part_analysis import PartAnalysisRun
from models.runs import PART_ANALYSIS_PREFIX
from parts.export import write_part_glb_from_reader
from parts.manifest import PartRecord, PartsManifest
from parts.reader import AssetReader
from parts.types import ASSEMBLED_GLB_FILE, MANIFEST_FILE
from storage.run_layout import RunLayout
from storage.run_store import RunStore
from utils.files import ensure_dir, retry_os, safe_rmtree, safe_unlink
from utils.ids import is_valid_id

logger = logging.getLogger(__name__)

PARTS_CACHE_DIR: Final = "parts"
BUNDLE_FILE: Final = "bundle.zip"
BUNDLE_WITH_BASE_FILE: Final = "bundle_with_base.zip"
BASE_MODEL_FILE: Final = "model.glb"
MESH_KINDS: Final = frozenset({"base", "physical"})
_STORED_SUFFIXES: Final = frozenset({".glb", ".png", ".webp"})
"""Already compressed (or texture-dominated) files are stored; JSON and raw buffers are deflated."""
_ZIP_DIRS: Final = ("buffers", "masks", "segmentation")
_MANIFEST_CACHE_SIZE: Final = 16

_manifest_lock = threading.Lock()
_manifest_cache: OrderedDict[tuple[str, int, int], PartsManifest] = OrderedDict()


def load_manifest(asset_dir: Path) -> PartsManifest:
    """The published manifest of ``asset_dir`` (cached by path, size and mtime; assets are immutable)."""
    path = Path(asset_dir) / MANIFEST_FILE
    try:
        stat = path.stat()
    except FileNotFoundError as exc:
        raise NotFoundError("The part layer has no manifest", details={"file": MANIFEST_FILE}) from exc
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    with _manifest_lock:
        cached = _manifest_cache.get(key)
        if cached is not None:
            _manifest_cache.move_to_end(key)
            return cached
    manifest = PartsManifest.model_validate_json(path.read_bytes())
    with _manifest_lock:
        _manifest_cache[key] = manifest
        while len(_manifest_cache) > _MANIFEST_CACHE_SIZE:
            _manifest_cache.popitem(last=False)
    return manifest


def find_part(manifest: PartsManifest, part_id: str) -> PartRecord | None:
    return next((p for p in manifest.parts if p.id == part_id), None)


class PartFiles:
    def __init__(self, *, settings: Settings, layout: RunLayout, store: RunStore[PartAnalysisRun]) -> None:
        self._settings = settings
        self._layout = layout
        self._store = store
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    # ------------------------------------------------------------------ lookups
    def completed_run(self, pa_id: str) -> PartAnalysisRun:
        """The run (404) if it completed with a published asset (else 409 ``not_completed``)."""
        run = self._store.get(pa_id) if is_valid_id(pa_id, PART_ANALYSIS_PREFIX) else None
        if run is None:
            raise NotFoundError("Part analysis not found", details={"analysis_id": pa_id})
        if run.status is not JobStatus.COMPLETED or run.result is None:
            raise ConflictError(
                f"Part analysis is {run.status}; its files exist once it has completed",
                details={"status": str(run.status), "reason": "not_completed"},
            )
        return run

    def asset_dir(self, run: PartAnalysisRun) -> Path:
        return self._layout.part_analysis_asset_dir(run.job_id, run.id)

    def manifest(self, run: PartAnalysisRun) -> PartsManifest:
        return load_manifest(self.asset_dir(run))

    def mesh_part(self, manifest: PartsManifest, part_id: str) -> PartRecord:
        """A mesh-owning record (404 unknown part, 409 ``no_mesh`` for regions and deferred parts)."""
        record = find_part(manifest, part_id)
        if record is None:
            raise NotFoundError("No such part in this analysis", details={"part_id": part_id})
        if record.kind not in MESH_KINDS:
            raise ConflictError(
                f"Part {part_id} is a {record.kind} record without its own mesh",
                details={"reason": "no_mesh", "part_id": part_id, "kind": record.kind},
            )
        return record

    # ------------------------------------------------------------------ builders
    async def part_glb(self, pa_id: str, part_id: str) -> Path:
        """Per-part GLB (pre-written by the analysis, or built on demand and cached)."""
        run = self.completed_run(pa_id)
        asset = self.asset_dir(run)
        manifest = await asyncio.to_thread(load_manifest, asset)
        record = self.mesh_part(manifest, part_id)
        if record.files.glb is not None and (asset / record.files.glb).is_file():
            return asset / record.files.glb
        target = self._layout.part_exports_dir(pa_id) / PARTS_CACHE_DIR / f"{part_id}.glb"
        return await self._build(pa_id, f"part:{part_id}", target, lambda tmp: _write_part(asset, part_id, tmp))

    async def bundle_zip(self, pa_id: str, *, include_base: bool = False) -> Path:
        """ZIP of the manifest, ``model_parts.glb``, buffers, masks, segmentation and per-part GLBs."""
        run = self.completed_run(pa_id)
        asset = self.asset_dir(run)
        manifest = await asyncio.to_thread(load_manifest, asset)
        part_paths: dict[str, Path] = {}
        for record in manifest.parts:
            if record.kind in MESH_KINDS:
                part_paths[record.id] = await self.part_glb(pa_id, record.id)
        name = BUNDLE_WITH_BASE_FILE if include_base else BUNDLE_FILE
        target = self._layout.part_exports_dir(pa_id) / name
        base = self._layout.paths.job_model(run.job_id) if include_base else None
        return await self._build(pa_id, name, target, lambda tmp: _write_zip(asset, part_paths, base, tmp))

    async def _build(self, pa_id: str, key: str, target: Path, write: Callable[[Path], None]) -> Path:
        lock = self._locks.setdefault((pa_id, key), asyncio.Lock())
        async with lock:
            if not target.is_file():
                await asyncio.to_thread(_build_atomic, target, write)
        return target

    def purge(self, pa_id: str) -> bool:
        """Remove ``runtime/cache/part_exports/<pa>/`` (``True`` if something was deleted). Never raises."""
        if not is_valid_id(pa_id, PART_ANALYSIS_PREFIX):
            return False
        for key in [k for k in self._locks if k[0] == pa_id]:
            self._locks.pop(key, None)
        return safe_rmtree(self._layout.part_exports_dir(pa_id))


def _build_atomic(target: Path, write: Callable[[Path], None]) -> None:
    ensure_dir(target.parent)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    started = time.perf_counter()
    try:
        write(tmp)
        retry_os(lambda: os.replace(tmp, target))
    finally:
        if tmp.exists():
            safe_unlink(tmp)
    logger.info("Built %s (%.0f kB, %.2f s)", target.name, target.stat().st_size / 1024, time.perf_counter() - started)


def _write_part(asset: Path, part_id: str, path: Path) -> None:
    write_part_glb_from_reader(AssetReader(asset, verify="none"), part_id, path)


def _write_zip(asset: Path, part_paths: dict[str, Path], base: Path | None, path: Path) -> None:
    with zipfile.ZipFile(path, "w", allowZip64=True) as bundle:
        _add(bundle, asset / MANIFEST_FILE, MANIFEST_FILE)
        _add(bundle, asset / ASSEMBLED_GLB_FILE, ASSEMBLED_GLB_FILE)
        for folder in _ZIP_DIRS:
            root = asset / folder
            if root.is_dir():
                for file in sorted(p for p in root.rglob("*") if p.is_file()):
                    _add(bundle, file, file.relative_to(asset).as_posix())
        for part_id, part_path in part_paths.items():
            _add(bundle, part_path, f"parts/{part_id}.glb")
        if base is not None and base.is_file():
            _add(bundle, base, BASE_MODEL_FILE)


def _add(bundle: zipfile.ZipFile, file: Path, name: str) -> None:
    stored = file.suffix.lower() in _STORED_SUFFIXES
    bundle.write(file, name, compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED,
                 compresslevel=None if stored else 6)
