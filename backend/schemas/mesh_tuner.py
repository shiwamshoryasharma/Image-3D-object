"""Auto Mesh Tuner API schemas: Blender runtime status/selection/download and tuning runs (DESIGN.md section 6)."""

from __future__ import annotations

from typing import Any, Final, Literal

from pydantic import Field

from models.job import MeshStats
from models.tuning import TuningRun
from schemas.common import API_V1_PREFIX, ApiModel, RequestModel
from schemas.jobs import JobErrorOut, MeshStatsOut
from utils.timeutil import isoformat_z

RuntimeModeIn = Literal["auto", "system", "embedded"]
PresetName = Literal["conservative", "balanced", "strong"]
SymmetryMode = Literal["auto", "off", "repair", "symmetrize"]
SubdivisionMode = Literal["auto", "off", "modifier"]

PRESETS: Final[dict[str, dict[str, Any]]] = {
    "conservative": {"smoothing": 0.25, "refine_density": False, "max_hole_edges": 120},
    "balanced": {},
    "strong": {"smoothing": 0.8, "max_hole_edges": 1000, "island_min_ratio": 0.003},
}
"""Preset overrides of :data:`OPTION_DEFAULTS` (DESIGN.md section 4); explicit request fields win."""

OPTION_DEFAULTS: Final[dict[str, Any]] = {
    "remove_inner_shell": True,
    "fill_holes": True,
    "max_hole_edges": 300,
    "remove_islands": True,
    "island_min_ratio": 0.001,
    "smoothing": 0.5,
    "refine_face": True,
    "eye_correction": True,
    "symmetry": "auto",
    "refine_density": True,
    "quads": False,
    "subdivision": "auto",
    "optimize_rig": True,
    "render_previews": True,
    "save_blend": False,
}


# --------------------------------------------------------------------------------------------------
# Blender runtime
# --------------------------------------------------------------------------------------------------


class BlenderInstallOut(ApiModel):
    id: str
    executable: str
    kind: Literal["system", "embedded"]
    origins: list[str]
    version: str | None
    version_label: str | None
    version_tuple: list[int] | None
    cycle: str | None
    is_lts: bool | None
    is_stable: bool | None
    build_hash: str | None = None
    build_date: str | None = None
    architecture: str | None
    valid: bool
    compatible: bool
    incompatible_reason: str | None
    error: str | None
    probe_ms: float | None = None
    selected: bool = False


class DownloadOut(ApiModel):
    state: str
    version: str | None
    file_name: str | None
    url: str | None
    bytes_done: int
    bytes_total: int | None
    percent: float | None
    speed_bps: float | None
    eta_s: float | None
    resumed: bool
    verified: bool | None
    error: str | None
    error_code: str | None
    install_dir: str | None
    started_at: str | None
    updated_at: str


class BlenderReleaseOut(ApiModel):
    version: str
    series: str
    file_name: str
    url: str
    host: str | None = None
    size_bytes: int | None
    sha256: str | None
    checksum_url: str | None
    architecture: str
    is_lts: bool | None
    release_date: str | None


class BlenderRuntimeOut(ApiModel):
    state: Literal["detecting", "ready", "needs_download", "downloading", "error"]
    mode: RuntimeModeIn
    min_version: str
    selected: BlenderInstallOut | None
    selection_reason: str | None
    installations: list[BlenderInstallOut]
    message: str | None
    warnings: list[str]
    runtime_root: str
    can_download: bool
    download: DownloadOut | None
    release: BlenderReleaseOut | None
    last_scan_at: str | None
    scan_duration_ms: float | None
    error: str | None


class RuntimeSelectionIn(RequestModel):
    mode: RuntimeModeIn
    executable: str | None = Field(default=None, max_length=1024)


class DownloadRequestIn(RequestModel):
    confirm: bool
    version: str | None = Field(default=None, pattern=r"^\d+\.\d+\.\d+$")


# --------------------------------------------------------------------------------------------------
# Tuning runs
# --------------------------------------------------------------------------------------------------


class TuningOptionsIn(RequestModel):
    """``POST /jobs/{job_id}/tunings`` body. Every field is optional; ``null`` means "preset default"."""

    preset: PresetName = "balanced"
    remove_inner_shell: bool | None = None
    fill_holes: bool | None = None
    max_hole_edges: int | None = Field(default=None, ge=8, le=5000)
    remove_islands: bool | None = None
    island_min_ratio: float | None = Field(default=None, ge=0.0, le=0.05)
    smoothing: float | None = Field(default=None, ge=0.0, le=1.0)
    refine_face: bool | None = None
    eye_correction: bool | None = None
    symmetry: SymmetryMode | None = None
    refine_density: bool | None = None
    quads: bool | None = None
    subdivision: SubdivisionMode | None = None
    optimize_rig: bool | None = None
    render_previews: bool | None = None
    save_blend: bool | None = None

    def effective(self) -> dict[str, Any]:
        """Preset defaults, then explicit (non-null) fields: the options actually sent to Blender."""
        values: dict[str, Any] = {"preset": self.preset, **OPTION_DEFAULTS, **PRESETS[self.preset]}
        for key in OPTION_DEFAULTS:
            explicit = getattr(self, key)
            if explicit is not None:
                values[key] = explicit
        return values


class TuningProgressOut(ApiModel):
    percent: float
    step_id: str | None
    step_index: int
    step_count: int
    message: str | None
    updated_at: str | None


class TuningStepOut(ApiModel):
    id: str
    label: str
    status: Literal["pending", "running", "done", "warning", "skipped", "failed"]
    message: str | None
    duration_s: float | None


class TuningBlenderOut(ApiModel):
    executable: str
    version: str | None
    kind: str | None


class TuningPreviewOut(ApiModel):
    name: str
    view: str | None
    variant: str | None
    url: str


class TuningResultOut(ApiModel):
    model_url: str
    download_url: str
    report_url: str | None
    log_url: str
    previews: list[TuningPreviewOut]
    stats: MeshStatsOut | None


class TuningRunOut(ApiModel):
    id: str
    job_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    created_at: str
    started_at: str | None
    finished_at: str | None
    options: dict[str, Any]
    progress: TuningProgressOut
    steps: list[TuningStepOut]
    metrics: list[dict[str, Any]]
    warnings: list[str]
    validation: dict[str, Any] | None
    blender: TuningBlenderOut | None
    result: TuningResultOut | None
    error: JobErrorOut | None

    @classmethod
    def from_domain(cls, run: TuningRun) -> TuningRunOut:
        base = tuning_base_url(run.id)
        result: TuningResultOut | None = None
        if run.result is not None and str(run.status) == "completed":
            data = run.result
            result = TuningResultOut(
                model_url=f"{base}/model.glb",
                download_url=f"{base}/export/glb",
                report_url=f"{base}/report" if data.report_file else None,
                log_url=f"{base}/log",
                previews=[
                    TuningPreviewOut(name=p.name, view=p.view, variant=p.variant, url=f"{base}/previews/{p.name}")
                    for p in data.previews
                ],
                stats=_stats_out(data.stats),
            )
        return cls(
            id=run.id,
            job_id=run.job_id,
            status=str(run.status),  # type: ignore[arg-type]
            created_at=isoformat_z(run.created_at) or "",
            started_at=isoformat_z(run.started_at),
            finished_at=isoformat_z(run.finished_at),
            options=dict(run.options),
            progress=TuningProgressOut(
                percent=run.progress.percent,
                step_id=run.progress.step_id,
                step_index=run.progress.step_index,
                step_count=run.progress.step_count,
                message=run.progress.message,
                updated_at=isoformat_z(run.progress.updated_at),
            ),
            steps=[
                TuningStepOut(id=s.id, label=s.label, status=s.status, message=s.message, duration_s=s.duration_s)
                for s in run.steps
            ],
            metrics=list(run.metrics),
            warnings=list(run.warnings),
            validation=run.validation,
            blender=TuningBlenderOut.model_validate(run.blender.model_dump()) if run.blender else None,
            result=result,
            error=JobErrorOut.model_validate(run.error.model_dump()) if run.error else None,
        )


class TuningRunList(ApiModel):
    items: list[TuningRunOut]


def tuning_base_url(tuning_id: str) -> str:
    return f"{API_V1_PREFIX}/tunings/{tuning_id}"


def _stats_out(stats: MeshStats | None) -> MeshStatsOut | None:
    return MeshStatsOut.model_validate(stats.model_dump()) if stats is not None else None
