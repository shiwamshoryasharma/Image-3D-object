"""Staged TRELLIS.2 image-to-3D run.

Replicates ``Trellis2ImageTo3DPipeline.run`` (same calls, same order, same seeding point) but split into the
engine stages of the contract, with per-step progress, cooperative cancellation and timings::

    preprocessing -> conditioning -> sparse_structure -> shape -> texture -> decoding -> exporting

Error mapping (OOM -> ``EngineError`` etc.) and GPU cleanup are done by :class:`inference.trellis.engine.TrellisEngine`.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import torch

from core.exceptions import EngineError, ValidationAppError
from inference.base import (
    CancellationToken,
    GenerationOutput,
    GenerationRequest,
    ProgressReporter,
    SamplerOverrides,
)
from inference.trellis.compat import hook_sampler_steps
from inference.trellis.decoding import decode_latent
from inference.trellis.export import EXPORT_STEPS, export_glb
from inference.trellis.imaging import (
    has_alpha_foreground,
    has_real_alpha,
    load_conditioning_image,
    make_preview,
    needs_upstream_preprocess,
)
from models.enums import JobStage

logger = logging.getLogger(__name__)

PIPELINE_TYPES: dict[int, str] = {512: "512", 1024: "1024_cascade", 1536: "1536_cascade"}
"""Contract resolution -> upstream ``pipeline_type``."""

SPARSE_STRUCTURE_RESOLUTION: dict[str, int] = {"512": 32, "1024": 64, "1024_cascade": 32, "1536_cascade": 32}
CASCADE_TARGET: dict[str, int] = {"1024_cascade": 1024, "1536_cascade": 1536}
MAX_NUM_TOKENS = 49152  # upstream default of Trellis2ImageTo3DPipeline.run

PREVIEW_FILENAME = "preview.png"
GLB_FILENAME = "model.glb"


def pipeline_type_for(resolution: int) -> str:
    try:
        return PIPELINE_TYPES[int(resolution)]
    except (KeyError, TypeError, ValueError):
        raise ValidationAppError(
            f"Unsupported resolution {resolution!r}; supported: {', '.join(map(str, PIPELINE_TYPES))}"
        ) from None


def merged_sampler_params(defaults: Mapping[str, Any], overrides: SamplerOverrides) -> dict[str, Any]:
    """Upstream defaults from ``pipeline.json`` with the request's overrides applied (what the sampler gets)."""
    return {**dict(defaults), **overrides.as_kwargs()}


class _StepCounter:
    """Per-stage step accounting driven by sampler hooks."""

    def __init__(
        self,
        reporter: ProgressReporter,
        cancel: CancellationToken,
        total: int,
        describe: Callable[[int, int], str],
    ) -> None:
        self._reporter = reporter
        self._cancel = cancel
        self.total = max(int(total), 1)
        self._describe = describe
        self.count = 0

    def before(self) -> None:
        self._cancel.raise_if_cancelled()

    def after(self) -> None:
        self.count += 1
        step = min(self.count, self.total)
        self._reporter.step(step, self.total, self._describe(step, self.total))
        self._cancel.raise_if_cancelled()


class TrellisGenerationRun:
    """One generation. Create, call :meth:`execute` once, discard."""

    def __init__(
        self,
        pipeline: Any,
        request: GenerationRequest,
        reporter: ProgressReporter,
        cancel: CancellationToken,
        *,
        attention_backend: str | None = None,
        max_num_tokens: int = MAX_NUM_TOKENS,
    ) -> None:
        self._pipeline = pipeline
        self._request = request
        self._reporter = reporter
        self._cancel = cancel
        self._attention_backend = attention_backend
        self._max_num_tokens = max_num_tokens
        self.current_stage: JobStage | None = None
        self.timings: dict[str, float] = {}

    # ------------------------------------------------------------------ helpers

    @contextlib.contextmanager
    def _stage(self, stage: JobStage, message: str, total_steps: int | None = None) -> Iterator[None]:
        self._cancel.raise_if_cancelled()
        self.current_stage = stage
        self._reporter.stage(stage, message, total_steps)
        started = time.perf_counter()
        yield
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        elapsed = time.perf_counter() - started
        self.timings[str(stage)] = round(elapsed, 3)
        logger.info("[%s] %s finished in %.2fs", self._request.job_id, stage, elapsed)

    def _hooked(self, sampler: Any, counter: _StepCounter) -> contextlib.AbstractContextManager[None]:
        return hook_sampler_steps(sampler, before_step=counter.before, after_step=counter.after)

    # ------------------------------------------------------------------ stages

    def _preprocess(self, output_dir: Path) -> tuple[Any, Path, str]:
        pipeline, params = self._pipeline, self._request.params
        source = Path(self._request.primary.path)
        if not source.is_file():
            raise EngineError(f"Input image not found: {source}", details={"stage": str(JobStage.PREPROCESSING)})
        image = load_conditioning_image(source)
        self._cancel.raise_if_cancelled()

        if needs_upstream_preprocess(image, params.remove_background):
            if has_real_alpha(image):
                mode, message = "alpha_crop", "Cropping to the image's alpha mask"
                if not has_alpha_foreground(image):
                    raise EngineError(
                        "The image's alpha channel contains no opaque foreground (no pixel above 80% opacity); "
                        "use an image with a visible subject.",
                        details={"stage": str(JobStage.PREPROCESSING), "reason": "empty_alpha"},
                    )
            else:
                mode, message = "background_removed", "Removing background"
            self._reporter.step(1, 2, message)
            try:
                processed = pipeline.preprocess_image(image)
            except ValueError as exc:
                if "zero-size array" not in str(exc):
                    raise
                raise EngineError(
                    "Background removal found no foreground object in the image; try another image or disable "
                    "background removal.",
                    details={"stage": str(JobStage.PREPROCESSING), "reason": "no_foreground"},
                ) from None
        else:
            mode = "untouched"
            self._reporter.step(1, 2, "Using the image as-is (no alpha, background removal disabled)")
            processed = image.convert("RGB")

        self._cancel.raise_if_cancelled()
        preview_path = output_dir / PREVIEW_FILENAME
        tmp = preview_path.with_name(preview_path.name + ".tmp")
        make_preview(processed).save(tmp, format="PNG")
        os.replace(tmp, preview_path)
        self._reporter.step(2, 2, f"Prepared {processed.width}x{processed.height} conditioning image")
        return processed, preview_path, mode

    # ------------------------------------------------------------------ run

    def execute(self) -> GenerationOutput:
        request, pipeline, params = self._request, self._pipeline, self._request.params
        pipeline_type = pipeline_type_for(params.resolution)
        output_dir = Path(request.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        models = pipeline.models

        ss_params = merged_sampler_params(pipeline.sparse_structure_sampler_params, params.sparse_structure)
        shape_params = merged_sampler_params(pipeline.shape_slat_sampler_params, params.shape)
        tex_params = merged_sampler_params(pipeline.tex_slat_sampler_params, params.texture)

        with torch.no_grad():
            # -- preprocessing -------------------------------------------------------------------------
            with self._stage(JobStage.PREPROCESSING, "Preparing image", total_steps=2):
                image, preview_path, preprocess_mode = self._preprocess(output_dir)

            # -- conditioning (seeded exactly where upstream seeds: after preprocessing) ----------------
            cond_res = "512px" if pipeline_type == "512" else "512px + 1024px"
            with self._stage(JobStage.CONDITIONING, f"Encoding image with DINOv3 ({cond_res})"):
                torch.manual_seed(int(params.seed))
                cond_512 = pipeline.get_cond([image], 512)
                cond_1024 = pipeline.get_cond([image], 1024) if pipeline_type != "512" else None

            # -- sparse structure --------------------------------------------------------------------
            ss_steps = int(ss_params.get("steps", 12))
            counter = _StepCounter(
                self._reporter, self._cancel, ss_steps, lambda i, n: f"Sparse structure step {i}/{n}"
            )
            with self._stage(JobStage.SPARSE_STRUCTURE, "Sampling sparse structure", total_steps=counter.total):
                with self._hooked(pipeline.sparse_structure_sampler, counter):
                    coords = pipeline.sample_sparse_structure(
                        cond_512,
                        SPARSE_STRUCTURE_RESOLUTION[pipeline_type],
                        1,
                        params.sparse_structure.as_kwargs(),
                    )
                sparse_voxels = int(coords.shape[0])
                if sparse_voxels == 0:
                    raise EngineError(
                        "The model produced an empty sparse structure for this image (no geometry); try another "
                        "seed, a clearer subject, or enable background removal.",
                        details={"stage": str(JobStage.SPARSE_STRUCTURE), "reason": "empty_structure"},
                    )

            # -- shape ---------------------------------------------------------------------------------
            shape_steps = int(shape_params.get("steps", 12))
            target = CASCADE_TARGET.get(pipeline_type)
            if target is None:
                counter = _StepCounter(
                    self._reporter, self._cancel, shape_steps, lambda i, n: f"Shape SLat step {i}/{n}"
                )
            else:
                counter = _StepCounter(
                    self._reporter,
                    self._cancel,
                    2 * shape_steps,
                    lambda i, n, s=shape_steps, t=target: (
                        f"Shape SLat step {i}/{n} ({'512 pass' if i <= s else f'{t} pass'})"
                    ),
                )
            label = "Generating geometry" + (f" (cascade 512 -> {target})" if target else " (512)")
            with self._stage(JobStage.SHAPE, label, total_steps=counter.total):
                with self._hooked(pipeline.shape_slat_sampler, counter):
                    if pipeline_type == "512":
                        shape_slat = pipeline.sample_shape_slat(
                            cond_512, models["shape_slat_flow_model_512"], coords, params.shape.as_kwargs()
                        )
                        resolution = 512
                    elif pipeline_type == "1024":
                        shape_slat = pipeline.sample_shape_slat(
                            cond_1024, models["shape_slat_flow_model_1024"], coords, params.shape.as_kwargs()
                        )
                        resolution = 1024
                    else:
                        shape_slat, resolution = pipeline.sample_shape_slat_cascade(
                            cond_512,
                            cond_1024,
                            models["shape_slat_flow_model_512"],
                            models["shape_slat_flow_model_1024"],
                            512,
                            target,
                            coords,
                            params.shape.as_kwargs(),
                            self._max_num_tokens,
                        )
                resolution = int(resolution)
                shape_tokens = int(shape_slat.feats.shape[0])
                if target is not None and resolution != target:
                    logger.info(
                        "[%s] cascade resolution reduced from %d to %d (token limit %d)",
                        request.job_id, target, resolution, self._max_num_tokens,
                    )
                self._reporter.step(
                    counter.total, counter.total, f"Geometry latent ready ({shape_tokens:,} tokens, {resolution}^3)"
                )
            del coords

            # -- texture -------------------------------------------------------------------------------
            tex_steps = int(tex_params.get("steps", 12))
            counter = _StepCounter(
                self._reporter, self._cancel, tex_steps, lambda i, n: f"Texture SLat step {i}/{n}"
            )
            tex_cond = cond_512 if pipeline_type == "512" else cond_1024
            tex_model = models["tex_slat_flow_model_512" if pipeline_type == "512" else "tex_slat_flow_model_1024"]
            with self._stage(JobStage.TEXTURE, "Generating texture", total_steps=counter.total):
                with self._hooked(pipeline.tex_slat_sampler, counter):
                    tex_slat = pipeline.sample_tex_slat(tex_cond, tex_model, shape_slat, params.texture.as_kwargs())
            del cond_512, cond_1024, tex_cond
            torch.cuda.empty_cache()

            # -- decoding ------------------------------------------------------------------------------
            with self._stage(JobStage.DECODING, f"Decoding mesh and PBR voxels at {resolution}^3"):
                mesh = decode_latent(pipeline, shape_slat, tex_slat, resolution)[0]
                del shape_slat, tex_slat
                decoded_vertices = int(mesh.vertices.shape[0])
                decoded_faces = int(mesh.faces.shape[0])
                if decoded_faces == 0:
                    raise EngineError(
                        "The decoded mesh is empty; try another seed or image.",
                        details={"stage": str(JobStage.DECODING), "reason": "empty_mesh"},
                    )

            # -- exporting -----------------------------------------------------------------------------
            glb_path = output_dir / GLB_FILENAME
            with self._stage(
                JobStage.EXPORTING,
                f"Remeshing, UV unwrapping and baking {params.texture_size}px textures",
                total_steps=EXPORT_STEPS,
            ):
                export_stats = export_glb(
                    mesh, pipeline, resolution, params, glb_path, self._reporter, self._cancel
                )
            del mesh

        self.timings["total"] = round(time.perf_counter() - started, 3)
        metadata: dict[str, Any] = {
            "engine": "trellis2",
            "model": "TRELLIS.2-4B",
            "pipeline_type": pipeline_type,
            "requested_resolution": int(params.resolution),
            "effective_resolution": resolution,
            "seed": int(params.seed),
            "preprocess": preprocess_mode,
            "attention_backend": self._attention_backend,
            "low_vram": bool(getattr(pipeline, "low_vram", False)),
            "sparse_voxels": sparse_voxels,
            "shape_tokens": shape_tokens,
            "sampler_params": {
                "sparse_structure": ss_params,
                "shape": shape_params,
                "texture": tex_params,
            },
            "polygon_target": int(params.polygon_target),
            "texture_size": int(params.texture_size),
            "glb_webp_textures": bool(params.glb_webp_textures),
        }
        mesh_stats = {**export_stats, "decoded_vertices": decoded_vertices, "decoded_faces": decoded_faces}
        return GenerationOutput(
            glb_path=glb_path,
            preview_path=preview_path,
            effective_resolution=resolution,
            timings=dict(self.timings),
            mesh_stats=mesh_stats,
            metadata=metadata,
        )
