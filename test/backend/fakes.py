"""TEST-ONLY inference engine double.

Implements :class:`inference.base.InferenceEngine` without any ML: it walks through every engine stage and
sampler step (honouring cancellation), copies the primary input as the preview and writes a tiny but real,
textured GLB with trimesh so mesh statistics are computed from an actual file. Never used by the application.
"""

from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import trimesh
from PIL import Image

from core.exceptions import EngineUnavailableError
from inference.base import (
    CancellationToken,
    EngineCapabilities,
    EngineStatus,
    GenerationOutput,
    GenerationRequest,
    InferenceEngine,
    LoadProgress,
    ProgressReporter,
    StatusCallback,
)
from models.enums import EngineState, JobStage
from utils.timeutil import utcnow

SAMPLED_STAGES = (JobStage.SPARSE_STRUCTURE, JobStage.SHAPE, JobStage.TEXTURE)
ENGINE_STAGES = (
    JobStage.PREPROCESSING,
    JobStage.CONDITIONING,
    JobStage.SPARSE_STRUCTURE,
    JobStage.SHAPE,
    JobStage.TEXTURE,
    JobStage.DECODING,
    JobStage.EXPORTING,
)


def write_test_glb(path: Path, *, texture_px: int = 64) -> Path:
    """Write a 1x1x1 textured cube (8 vertices, 12 faces, 1 material, 1 texture, UVs) as GLB."""
    mesh = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
    uv = np.clip(mesh.vertices[:, :2] + 0.5, 0.0, 1.0)
    texture = Image.new("RGB", (texture_px, texture_px), (200, 120, 50))
    material = trimesh.visual.material.PBRMaterial(
        baseColorTexture=texture, metallicFactor=0.0, roughnessFactor=0.8
    )
    mesh.visual = trimesh.visual.TextureVisuals(uv=uv, material=material)
    data = trimesh.Scene(mesh).export(file_type="glb")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@dataclass(eq=False)
class FakeEngine(InferenceEngine):
    """Configurable engine double. Thread-safe enough for the single GPU worker thread."""

    ready: bool = True
    steps: int = 3
    step_delay: float = 0.0
    load_delay: float = 0.0
    load_error: str | None = None
    fail_with: BaseException | None = None
    caps: EngineCapabilities = field(default_factory=EngineCapabilities)
    device: dict[str, Any] | None = field(
        default_factory=lambda: {
            "cuda_available": True,
            "name": "Fake GPU",
            "compute_capability": "12.0",
            "vram_total_mb": 32607,
            "vram_used_mb": 1000,
            "vram_free_mb": 31607,
            "torch_version": "fake",
            "cuda_version": "fake",
        }
    )
    device_delay: float = 0.0

    def __post_init__(self) -> None:
        self.name = "fake-engine"
        self._lock = threading.Lock()
        self._status = EngineStatus(
            state=EngineState.READY if self.ready else EngineState.NOT_LOADED,
            message="Ready" if self.ready else "Not loaded",
            loaded_at=utcnow() if self.ready else None,
        )
        self.gate = threading.Event()
        self.gate.set()  # open: generation does not block
        self.generation_started = threading.Event()
        self.requests: list[GenerationRequest] = []
        self.load_calls = 0
        self.unload_calls = 0
        self.generate_threads: list[str] = []

    # ------------------------------------------------------------------ test controls
    def hold(self) -> None:
        """Make the next generation block at its first sampler step until :meth:`release` (or cancel)."""
        self.gate.clear()

    def release(self) -> None:
        self.gate.set()

    # ------------------------------------------------------------------ InferenceEngine
    def status(self) -> EngineStatus:
        with self._lock:
            return self._status

    def _set(self, status: EngineStatus, on_status: StatusCallback | None) -> None:
        with self._lock:
            self._status = status
        if on_status:
            on_status(status)

    def capabilities(self) -> EngineCapabilities:
        return self.caps

    def load(self, on_status: StatusCallback | None = None) -> None:
        self.load_calls += 1
        if self.status().state is EngineState.READY:
            return
        total = 3
        for index in range(total):
            self._set(
                EngineStatus(
                    state=EngineState.LOADING,
                    message=f"Loading part_{index} ({index + 1}/{total})",
                    load_progress=LoadProgress(loaded=index, total=total, current=f"part_{index}"),
                ),
                on_status,
            )
            time.sleep(self.load_delay / total)
        if self.load_error:
            self._set(EngineStatus(state=EngineState.ERROR, message="Load failed", error=self.load_error), on_status)
            return
        self._set(EngineStatus(state=EngineState.READY, message="Ready", loaded_at=utcnow()), on_status)

    def unload(self) -> None:
        self.unload_calls += 1
        with self._lock:
            self._status = EngineStatus(state=EngineState.NOT_LOADED, message="Not loaded")

    def device_info(self) -> dict[str, Any] | None:
        if self.device_delay:
            time.sleep(self.device_delay)
        return self.device

    def generate(
        self, request: GenerationRequest, reporter: ProgressReporter, cancel: CancellationToken
    ) -> GenerationOutput:
        if not self.status().is_ready:
            raise EngineUnavailableError("Fake engine is not loaded")
        self.requests.append(request)
        self.generate_threads.append(threading.current_thread().name)
        self.generation_started.set()
        timings: dict[str, float] = {}
        for stage in ENGINE_STAGES:
            cancel.raise_if_cancelled()
            started = time.perf_counter()
            if stage in SAMPLED_STAGES:
                reporter.stage(stage, f"{stage} sampling", total_steps=self.steps)
                for step in range(1, self.steps + 1):
                    while not self.gate.wait(0.02):
                        cancel.raise_if_cancelled()
                    if self.step_delay:
                        time.sleep(self.step_delay)
                    cancel.raise_if_cancelled()
                    reporter.step(step, self.steps)
                    if stage is JobStage.SHAPE and self.fail_with is not None and step == 1:
                        raise self.fail_with
            else:
                reporter.stage(stage)
                if stage is JobStage.PREPROCESSING:
                    shutil.copyfile(request.primary.path, request.output_dir / "preview.png")
            timings[str(stage)] = round(time.perf_counter() - started, 4)

        glb_path = write_test_glb(request.output_dir / "model.glb")
        return GenerationOutput(
            glb_path=glb_path,
            preview_path=request.output_dir / "preview.png",
            effective_resolution=request.params.resolution,
            timings={**timings, "total": round(sum(timings.values()), 4)},
            mesh_stats={"raw_vertices": 8, "raw_faces": 12},
            metadata={"engine": "fake", "output_dir": request.output_dir},
        )
