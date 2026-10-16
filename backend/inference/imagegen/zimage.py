"""Z-Image-Turbo text-to-image provider (part-aware DESIGN.md sections 4.4.4 and 5.6; ``DECISIONS.md`` decision 1).

Provider rules (enforced by review):

* Local, pinned snapshot only: ``ZImagePipeline.from_pretrained(local_dir, torch_dtype=torch.bfloat16,
  local_files_only=True)``. Never ``trust_remote_code``, never ``custom_pipeline``, never a network call.
* ``load()`` runs on a CPU thread and never touches CUDA. The weights live on a spinning disk (E:), where the loaders'
  memory-mapped, scattered page faults crawl (the feasibility probe measured 307 s for the first ``.to("cuda")``,
  and ~10 MB/s inside ``from_pretrained`` with other readers on the disk). ``load()`` therefore first reads every
  weight file once, sequentially in large chunks, so the OS file cache holds them; then ``from_pretrained`` runs and
  every parameter and buffer is copied into anonymous CPU RAM (``t.data = t.data.contiguous().clone()``). All disk
  reads happen here, never on the single GPU thread. The pipeline stays resident in RAM between runs.
* ``generate()`` runs on the ``gpu-worker`` thread: it moves ``text_encoder``, ``transformer`` and ``vae`` to the
  device one at a time (checking the cancellation token between moves), generates the candidates one at a time
  (batch size 1) with a private ``torch.Generator(device).manual_seed(seed_i)`` each (the global RNG is never seeded),
  calls ``torch.cuda.empty_cache()`` after each VAE decode and moves the weights back to the CPU in ``finally``.
* ``num_inference_steps`` / ``guidance_scale`` come from the request (catalog defaults 9 / 0.0; Z-Image-Turbo is
  distilled, so no negative prompt is used).
* Progress comes from ``callback_on_step_end``; cancellation sets ``pipe._interrupt = True`` and raises
  :class:`~core.exceptions.JobCancelledError`.
* The provider writes only into ``request.output_dir``.

torch and diffusers are imported inside functions only (section 1.4 rule 2).
"""

from __future__ import annotations

import gc
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from config.settings import Settings
from core.exceptions import JobCancelledError
from inference.base import CancellationToken
from inference.imagegen.base import ImageGenOutput, ImageGenRequest, TextToImageProvider
from inference.providers.base import LoadedDevice, ModelSpec, ModelUiState, ProviderStatus, StepProgress

logger = logging.getLogger(__name__)

COMPONENTS: Final[tuple[str, ...]] = ("text_encoder", "transformer", "vae")
"""Weight-carrying pipeline components, in the order the pipeline uses them (``model_cpu_offload_seq``)."""
WEIGHT_FILE_PATTERN: Final = "*.safetensors"
_PREFETCH_CHUNK_BYTES: Final = 16 * 1024 * 1024
_GIB: Final = 1024**3


def resolve_model_dir(spec: ModelSpec, settings: Settings) -> Path:
    """``settings.model_paths[id]`` when configured, otherwise ``settings.aux_model_root / spec.local_subdir``."""
    override = settings.model_paths.get(spec.id)
    if override is not None:
        return Path(override)
    if not spec.local_subdir:
        raise ValueError(f"Model {spec.id} has no local folder")
    return Path(settings.aux_model_root) / spec.local_subdir


class ZImageTurboProvider(TextToImageProvider):
    """``z_image_turbo`` (``Tongyi-MAI/Z-Image-Turbo``, Apache-2.0) behind the text-to-image seam."""

    def __init__(self, spec: ModelSpec, settings: Settings, *, model_dir: Path | None = None) -> None:
        super().__init__(spec)
        self._settings = settings
        self.model_dir = Path(model_dir) if model_dir is not None else resolve_model_dir(spec, settings)
        self._pipe: Any | None = None
        self._loading = False
        self._error: str | None = None
        self._device: LoadedDevice | None = None
        self._last_used_at: datetime | None = None
        self.last_load_timings: dict[str, float] = {}
        """``prefetch_s``, ``prefetch_gb``, ``from_pretrained_s`` and ``materialize_s`` of the last ``load()``."""

    # ------------------------------------------------------------------ state
    def status(self) -> ProviderStatus:
        state: ModelUiState
        if self._loading:
            state, reason_code, reason = "loading", "ok", None
        elif self._pipe is not None:
            state, reason_code, reason = "loaded", "ok", None
        elif self._error is not None:
            state, reason_code, reason = "error", "error", self._error
        else:
            state, reason_code, reason = "installed", "ok", None
        return ProviderStatus(
            model_id=self.model_id,
            state=state,
            reason_code=reason_code,
            reason=reason,
            revision=self.spec.revision,
            loaded_device=self._device if self._pipe is not None else None,
            last_used_at=self._last_used_at,
        )

    # ------------------------------------------------------------------ CPU thread
    def load(self, cancel: CancellationToken) -> None:
        """Read the pipeline fully into CPU RAM (idempotent; never touches CUDA)."""
        with self.lock:
            if self._pipe is not None:
                return
            cancel.raise_if_cancelled()
            self._loading = True
            try:
                self._pipe = self._load_pipeline(cancel)
                self._device = "cpu"
                self._error = None
            except JobCancelledError:
                self._pipe = None
                raise
            except Exception as exc:
                self._pipe = None
                self._error = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                self._loading = False
                if self._pipe is None:
                    gc.collect()

    def _load_pipeline(self, cancel: CancellationToken) -> Any:
        import torch
        from diffusers import ZImagePipeline

        if not self.model_dir.is_dir():
            raise FileNotFoundError(f"Model folder not found: {self.model_dir}")
        started = time.perf_counter()
        prefetched = self._prefetch_weights(cancel)
        read = time.perf_counter()
        pipe = ZImagePipeline.from_pretrained(
            str(self.model_dir), torch_dtype=torch.bfloat16, local_files_only=True
        )
        loaded = time.perf_counter()
        pipe.set_progress_bar_config(disable=True)
        for name in COMPONENTS:
            cancel.raise_if_cancelled()
            module = getattr(pipe, name, None)
            if module is None:
                raise RuntimeError(f"The Z-Image pipeline has no {name}")
            _materialize(module)
        finished = time.perf_counter()
        self.last_load_timings = {
            "prefetch_s": round(read - started, 3),
            "prefetch_gb": round(prefetched / 1e9, 2),
            "from_pretrained_s": round(loaded - read, 3),
            "materialize_s": round(finished - loaded, 3),
        }
        logger.info(
            "Z-Image-Turbo loaded into RAM in %.1f s (read %.1f GB in %.1f s, from_pretrained %.1f s, "
            "materialise %.1f s)",
            finished - started,
            prefetched / 1e9,
            read - started,
            loaded - read,
            finished - loaded,
        )
        return pipe

    def _prefetch_weights(self, cancel: CancellationToken) -> int:
        """Read every component's weight files once, sequentially, so the loaders' memory-mapped reads are served
        from the OS file cache instead of scattered disk seeks. Returns the number of bytes read."""
        files = sorted(path for name in COMPONENTS for path in (self.model_dir / name).glob(WEIGHT_FILE_PATTERN))
        buffer = memoryview(bytearray(_PREFETCH_CHUNK_BYTES))
        total = 0
        for path in files:
            with path.open("rb", buffering=0) as handle:
                while True:
                    cancel.raise_if_cancelled()
                    count = handle.readinto(buffer)
                    if not count:
                        break
                    total += count
        return total

    # ------------------------------------------------------------------ GPU thread
    def warm(self, cancel: CancellationToken) -> None:
        """One device round trip (CUDA context and kernels warm, weights back in RAM afterwards)."""
        with self.lock:
            if self._pipe is None:
                self.load(cancel)
            try:
                self._to_device(self._settings.device, cancel)
            finally:
                self.offload()

    def generate(self, request: ImageGenRequest, progress: StepProgress, cancel: CancellationToken) -> ImageGenOutput:
        import torch

        with self.lock:
            if self._pipe is None:
                logger.warning("Z-Image-Turbo was not resident in RAM; loading it on the GPU thread")
                self.load(cancel)
            pipe = self._pipe
            device = torch.device(request.device)
            on_cuda = device.type == "cuda"
            count = len(request.seeds)
            timings: dict[str, float] = {}
            paths: list[Path] = []
            try:
                if on_cuda:
                    torch.cuda.reset_peak_memory_stats(device)
                timings["to_device_s"] = round(self._to_device(request.device, cancel), 3)
                for index, seed in enumerate(request.seeds):
                    cancel.raise_if_cancelled()
                    started = time.perf_counter()
                    generator = torch.Generator(device=device).manual_seed(int(seed))
                    callback = _step_callback(index, count, request.steps, progress, cancel)
                    with torch.inference_mode():
                        output = pipe(
                            prompt=request.prompt,
                            negative_prompt=request.negative_prompt,
                            height=request.height,
                            width=request.width,
                            num_inference_steps=request.steps,
                            guidance_scale=request.guidance,
                            generator=generator,
                            callback_on_step_end=callback,
                            output_type="pil",
                        )
                    if cancel.is_cancelled:
                        raise JobCancelledError(cancel.reason or "Image generation was cancelled")
                    image = output.images[0]
                    path = request.output_dir / f"{index}.png"
                    image.save(path, format="PNG", compress_level=3)
                    paths.append(path)
                    del output, image
                    if on_cuda:
                        torch.cuda.empty_cache()
                    timings[f"candidate_{index}_s"] = round(time.perf_counter() - started, 3)
                    progress((index + 1) / count, f"Image {index + 1} of {count} done")
                metadata: dict[str, Any] = {"device": str(device)}
                if on_cuda:
                    metadata["peak_vram_gb"] = round(torch.cuda.max_memory_allocated(device) / _GIB, 2)
            finally:
                pipe._interrupt = False
                offload_started = time.perf_counter()
                self.offload()
                timings["offload_s"] = round(time.perf_counter() - offload_started, 3)
                self._last_used_at = datetime.now(UTC)
            seeds = [int(seed) for seed in request.seeds]
            return ImageGenOutput(paths=paths, seeds=seeds, timings=timings, metadata=metadata)

    def _to_device(self, device_name: str, cancel: CancellationToken) -> float:
        """Move the components to ``device_name`` one at a time; returns the elapsed seconds (synchronised)."""
        import torch

        device = torch.device(device_name)
        started = time.perf_counter()
        for name in COMPONENTS:
            cancel.raise_if_cancelled()
            getattr(self._pipe, name).to(device)
            self._device = "cuda" if device.type == "cuda" else "cpu"
        if device.type == "cuda":
            torch.cuda.synchronize(device)
        elapsed = time.perf_counter() - started
        logger.info("Z-Image-Turbo moved to %s in %.1f s", device, elapsed)
        return elapsed

    def offload(self) -> None:
        """Weights back to CPU RAM and the CUDA cache released."""
        with self.lock:
            pipe = self._pipe
            if pipe is None:
                return
            import torch

            for name in COMPONENTS:
                module = getattr(pipe, name, None)
                if module is not None:
                    module.to("cpu")
            self._device = "cpu"
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def unload(self) -> None:
        """Drop the pipeline entirely (RAM and VRAM)."""
        with self.lock:
            if self._pipe is None:
                return
            self.offload()
            self._pipe = None
            self._device = None
            gc.collect()
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            logger.info("Z-Image-Turbo unloaded")


def _materialize(module: Any) -> None:
    """Copy every parameter and buffer of ``module`` into freshly allocated CPU memory (detaches memory-mapped
    storage, so the weight files are read now). Shared (tied) parameters are visited once and stay shared."""
    import torch

    with torch.no_grad():
        for tensor in (*module.parameters(), *module.buffers()):
            if tensor.device.type == "cpu":
                tensor.data = tensor.data.contiguous().clone()


def _step_callback(
    index: int, count: int, steps: int, progress: StepProgress, cancel: CancellationToken
) -> Callable[[Any, int, Any, dict[str, Any]], dict[str, Any]]:
    """``callback_on_step_end``: step progress across all candidates and cooperative cancellation."""

    def on_step_end(pipe: Any, step: int, _timestep: Any, callback_kwargs: dict[str, Any]) -> dict[str, Any]:
        done = min(step + 1, steps)
        progress((index + done / max(steps, 1)) / count, f"Image {index + 1} of {count}: step {done}/{steps}")
        if cancel.is_cancelled:
            pipe._interrupt = True
        return callback_kwargs

    return on_step_end


def create_provider(spec: ModelSpec, settings: Settings) -> ZImageTurboProvider:
    """Factory used by the model registry for the catalog provider key ``zimage``."""
    return ZImageTurboProvider(spec, settings)
