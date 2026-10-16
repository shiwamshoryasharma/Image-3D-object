"""On-demand part-analysis files: per-part GLBs and ZIP bundles (part-aware DESIGN.md sections 6.4 and 7.2).

S1 stub with the **frozen constructor**; slice S10 implements the builders (``asyncio.to_thread`` under a per-
(analysis, file) ``asyncio.Lock``, temp name + ``os.replace`` into ``runtime/cache/part_exports/<pa>/``). Published
assets under ``jobs/<job>/parts/<pa>/asset/`` are immutable (I12): everything built on demand goes to the cache.

:meth:`PartFiles.purge` is already real: the job-delete hook and ``DELETE /part-analyses/{id}`` use it.
"""

from __future__ import annotations

from pathlib import Path

from config.settings import Settings
from core.feature_errors import ServiceNotImplementedError
from models.part_analysis import PartAnalysisRun
from models.runs import PART_ANALYSIS_PREFIX
from storage.run_layout import RunLayout
from storage.run_store import RunStore
from utils.files import safe_rmtree
from utils.ids import is_valid_id

_CAPABILITY = "part_analysis"


class PartFiles:
    def __init__(self, *, settings: Settings, layout: RunLayout, store: RunStore[PartAnalysisRun]) -> None:
        self._settings = settings
        self._layout = layout
        self._store = store

    async def part_glb(self, pa_id: str, part_id: str) -> Path:
        """Per-part GLB (pre-written by the analysis, or built on demand and cached)."""
        raise ServiceNotImplementedError(_CAPABILITY, operation="Per-part GLB export")

    async def bundle_zip(self, pa_id: str, *, include_base: bool = False) -> Path:
        """ZIP of the manifest, ``model_parts.glb``, buffers, masks, segmentation and per-part GLBs."""
        raise ServiceNotImplementedError(_CAPABILITY, operation="ZIP export")

    def purge(self, pa_id: str) -> bool:
        """Remove ``runtime/cache/part_exports/<pa>/`` (``True`` if something was deleted). Never raises."""
        if not is_valid_id(pa_id, PART_ANALYSIS_PREFIX):
            return False
        return safe_rmtree(self._layout.part_exports_dir(pa_id))
