"""Runtime directory layout.

::

    runtime/
      uploads/<set_id>/set.json, <image_id>.<ext>, <image_id>_thumb.webp
      jobs/<job_id>/job.json, inputs/<image_id>.png, preview.png, model.glb
      temp/      (purged at startup)
      cache/
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from utils.files import ensure_dir, safe_rmtree

logger = logging.getLogger(__name__)

SET_METADATA_FILE = "set.json"
JOB_METADATA_FILE = "job.json"
JOB_INPUTS_DIR = "inputs"
JOB_PREVIEW_FILE = "preview.png"
JOB_MODEL_FILE = "model.glb"


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    root: Path

    @property
    def uploads(self) -> Path:
        return self.root / "uploads"

    @property
    def jobs(self) -> Path:
        return self.root / "jobs"

    @property
    def temp(self) -> Path:
        return self.root / "temp"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    # ------------------------------------------------------------------ reference sets
    def set_dir(self, set_id: str) -> Path:
        return self.uploads / set_id

    def set_metadata(self, set_id: str) -> Path:
        return self.set_dir(set_id) / SET_METADATA_FILE

    # ------------------------------------------------------------------ jobs
    def job_dir(self, job_id: str) -> Path:
        return self.jobs / job_id

    def job_metadata(self, job_id: str) -> Path:
        return self.job_dir(job_id) / JOB_METADATA_FILE

    def job_inputs(self, job_id: str) -> Path:
        return self.job_dir(job_id) / JOB_INPUTS_DIR

    def job_preview(self, job_id: str) -> Path:
        return self.job_dir(job_id) / JOB_PREVIEW_FILE

    def job_model(self, job_id: str) -> Path:
        return self.job_dir(job_id) / JOB_MODEL_FILE

    # ------------------------------------------------------------------ lifecycle
    def ensure(self) -> None:
        """Create every runtime directory."""
        for directory in (self.root, self.uploads, self.jobs, self.temp, self.cache):
            ensure_dir(directory)

    def purge_temp(self) -> int:
        """Delete everything inside ``temp/``. Returns the number of removed entries."""
        removed = 0
        if not self.temp.exists():
            return 0
        for entry in self.temp.iterdir():
            try:
                if entry.is_dir():
                    safe_rmtree(entry)
                else:
                    entry.unlink(missing_ok=True)
                removed += 1
            except OSError as exc:  # pragma: no cover - best effort
                logger.warning("Could not purge temp entry %s: %s", entry, exc)
        return removed
