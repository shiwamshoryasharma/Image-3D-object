"""Runtime layout of the part-aware resources (part-aware DESIGN.md section 7.1).

::

    runtime/
      jobs/<job>/parts/<pa>/run.json  asset/...  log.txt        part analyses live inside their job
      imagegen/<ig>/run.json  candidates/<n>.png  candidates/<n>_thumb.webp
      segmentations/<sg>/run.json  source.png  [original.png]  frame.json  labels.png  overlay.png  masks/  ...
      workflows/<wf>/workflow.json  graph.json  layout.json
      cache/part_exports/<pa>/<part_id>.glb  bundle[_base].zip    on-demand exports
      temp/imagegen/<ig>/  temp/segmentations/<sg>/  temp/parts/<pa>/   run work dirs (purged at startup)

The paths are functions of :class:`~storage.paths.RuntimePaths` (which stays unchanged). No path component is named
``aux`` (a reserved Windows device name). Callers validate ids with :func:`utils.ids.is_valid_id` before joining.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from models.runs import RUN_FILE, RunKind
from storage.paths import RuntimePaths
from utils.files import ensure_dir

IMAGEGEN_DIR: Final = "imagegen"
SEGMENTATIONS_DIR: Final = "segmentations"
WORKFLOWS_DIR: Final = "workflows"
JOB_PARTS_DIR: Final = "parts"
PART_EXPORTS_DIR: Final = "part_exports"

CANDIDATES_DIR: Final = "candidates"
MASKS_DIR: Final = "masks"
ASSET_DIR: Final = "asset"
RUN_LOG_FILE: Final = "log.txt"
WORKFLOW_FILE: Final = "workflow.json"
WORKFLOW_GRAPH_FILE: Final = "graph.json"
WORKFLOW_LAYOUT_FILE: Final = "layout.json"

TEMP_DIRS: Final[dict[RunKind, str]] = {
    "image_generation": IMAGEGEN_DIR,
    "segmentation": SEGMENTATIONS_DIR,
    "part_analysis": JOB_PARTS_DIR,
}
"""``temp/<dir>/<run_id>/`` work directory name per run kind."""


@dataclass(frozen=True, slots=True)
class RunLayout:
    paths: RuntimePaths

    # ------------------------------------------------------------------ roots
    @property
    def imagegen_root(self) -> Path:
        return self.paths.root / IMAGEGEN_DIR

    @property
    def segmentations_root(self) -> Path:
        return self.paths.root / SEGMENTATIONS_DIR

    @property
    def workflows_root(self) -> Path:
        return self.paths.root / WORKFLOWS_DIR

    @property
    def part_exports_root(self) -> Path:
        return self.paths.cache / PART_EXPORTS_DIR

    def ensure(self) -> None:
        """Create the global roots (job-scoped folders are created only below existing jobs)."""
        for directory in (self.imagegen_root, self.segmentations_root, self.workflows_root, self.part_exports_root):
            ensure_dir(directory)

    # ------------------------------------------------------------------ image generations
    def imagegen_dir(self, ig_id: str) -> Path:
        return self.imagegen_root / ig_id

    def candidates_dir(self, ig_id: str) -> Path:
        return self.imagegen_dir(ig_id) / CANDIDATES_DIR

    def candidate_path(self, ig_id: str, index: int) -> Path:
        return self.candidates_dir(ig_id) / f"{int(index)}.png"

    def candidate_thumb_path(self, ig_id: str, index: int) -> Path:
        return self.candidates_dir(ig_id) / f"{int(index)}_thumb.webp"

    # ------------------------------------------------------------------ segmentations
    def segmentation_dir(self, sg_id: str) -> Path:
        return self.segmentations_root / sg_id

    def segmentation_masks_dir(self, sg_id: str) -> Path:
        return self.segmentation_dir(sg_id) / MASKS_DIR

    # ------------------------------------------------------------------ part analyses (inside their job)
    def job_parts_dir(self, job_id: str) -> Path:
        return self.paths.job_dir(job_id) / JOB_PARTS_DIR

    def part_analysis_dir(self, job_id: str, pa_id: str) -> Path:
        return self.job_parts_dir(job_id) / pa_id

    def part_analysis_asset_dir(self, job_id: str, pa_id: str) -> Path:
        return self.part_analysis_dir(job_id, pa_id) / ASSET_DIR

    def part_analysis_log(self, job_id: str, pa_id: str) -> Path:
        return self.part_analysis_dir(job_id, pa_id) / RUN_LOG_FILE

    def part_exports_dir(self, pa_id: str) -> Path:
        return self.part_exports_root / pa_id

    # ------------------------------------------------------------------ workflows
    def workflow_dir(self, wf_id: str) -> Path:
        return self.workflows_root / wf_id

    def workflow_file(self, wf_id: str) -> Path:
        return self.workflow_dir(wf_id) / WORKFLOW_FILE

    def workflow_graph_file(self, wf_id: str) -> Path:
        return self.workflow_dir(wf_id) / WORKFLOW_GRAPH_FILE

    def workflow_layout_file(self, wf_id: str) -> Path:
        return self.workflow_dir(wf_id) / WORKFLOW_LAYOUT_FILE

    # ------------------------------------------------------------------ run records and work dirs
    def run_root(self, kind: RunKind) -> Path | None:
        """Folder holding the run folders of a global kind (``None`` for job-scoped part analyses)."""
        if kind == "image_generation":
            return self.imagegen_root
        if kind == "segmentation":
            return self.segmentations_root
        return None

    def run_file(self, run_dir: Path) -> Path:
        return run_dir / RUN_FILE

    def temp_root(self, kind: RunKind) -> Path:
        return self.paths.temp / TEMP_DIRS[kind]

    def work_dir(self, kind: RunKind, run_id: str) -> Path:
        """Scratch directory of a running run (removed when it ends; ``purge_temp`` clears leftovers)."""
        return self.temp_root(kind) / run_id

    def work_log(self, kind: RunKind, run_id: str) -> Path:
        return self.work_dir(kind, run_id) / RUN_LOG_FILE
