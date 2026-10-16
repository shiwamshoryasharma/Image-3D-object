"""Test doubles for prompt-to-image runs (slice S4). TEST-ONLY: never imported by the application.

* :class:`FakeTextToImageProvider` - a real :class:`~inference.imagegen.base.TextToImageProvider` subclass that draws
  a deterministic figure per seed with Pillow (no torch), records the thread of every call and can block inside
  ``generate`` so tests can cancel a running generation.
* :class:`FakeImageGenRegistry` - the parts of ``inference.providers.registry.ProviderRegistry`` the image generation
  service and executor use (specs, statuses, default model, provider lookup, VRAM checks, guard, offload).
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from PIL import Image, ImageDraw

from core.exceptions import JobCancelledError
from core.feature_errors import CapabilityUnavailableError
from inference.base import CancellationToken
from inference.imagegen.base import ImageGenOutput, ImageGenRequest, TextToImageProvider
from inference.providers.base import LicenseInfo, ModelSpec, ProviderStatus, StepProgress

T2I_MODEL_ID = "z_image_turbo"
T2I_REVISION = "f332072aa78be7aecdf3ee76d5c247082da564a6"

T2I_CAPABILITIES: Mapping[str, Any] = MappingProxyType(
    {
        "sizes": [[1024, 1024], [896, 1152], [1152, 896]],
        "steps": {"default": 9, "min": 1, "max": 50},
        "guidance": {"default": 0.0, "min": 0.0, "max": 0.0},
        "supports_negative_prompt": False,
        "max_images": 4,
    }
)


def make_t2i_spec(model_id: str = T2I_MODEL_ID, **overrides: Any) -> ModelSpec:
    spec = ModelSpec(
        id=model_id,
        kind="text_to_image",
        roles=("generator",),
        display_name="Fake text-to-image",
        provider="fake",
        repo_id="Tongyi-MAI/Z-Image-Turbo",
        revision=T2I_REVISION,
        local_subdir="t2i/Z-Image-Turbo",
        required_files=(),
        size_bytes=None,
        vram_peak_gb=24.0,
        ram_gb=21.0,
        cold_load_s=None,
        license=LicenseInfo(
            id="apache-2.0",
            name="Apache License 2.0",
            url="https://www.apache.org/licenses/LICENSE-2.0",
            commercial="allowed",
        ),
        default_approved=True,
        capabilities=T2I_CAPABILITIES,
    )
    return replace(spec, **overrides) if overrides else spec


class FakeTextToImageProvider(TextToImageProvider):
    """Draws a dark figure on a light-gray background (TRELLIS-friendly unless ``layout`` says otherwise).

    ``layout``: ``"figure"`` (centred, ready), ``"edge"`` (touches the left border) or ``"blank"`` (no subject).
    """

    def __init__(self, spec: ModelSpec | None = None, *, layout: str = "figure") -> None:
        super().__init__(spec or make_t2i_spec())
        self.layout = layout
        self.loaded = False
        self.on_gpu = False
        self.calls: list[tuple[str, str]] = []
        """``(method, thread name)`` of every call."""
        self.requests: list[ImageGenRequest] = []
        self.block_generate: threading.Event | None = None
        """When set to an event, ``generate`` waits on it (honouring the cancellation token)."""
        self.generate_started = threading.Event()
        self.fail_with: BaseException | None = None

    def _record(self, method: str) -> None:
        self.calls.append((method, threading.current_thread().name))

    def threads_of(self, method: str) -> list[str]:
        return [thread for name, thread in self.calls if name == method]

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            model_id=self.model_id,
            state="loaded" if self.loaded else "installed",
            reason_code="ok",
            reason=None,
            revision=self.spec.revision,
            loaded_device=("cuda" if self.on_gpu else "cpu") if self.loaded else None,
        )

    def load(self, cancel: CancellationToken) -> None:
        with self.lock:
            self._record("load")
            cancel.raise_if_cancelled()
            self.loaded = True

    def offload(self) -> None:
        with self.lock:
            self._record("offload")
            self.on_gpu = False

    def unload(self) -> None:
        with self.lock:
            self._record("unload")
            self.loaded = False
            self.on_gpu = False

    def generate(self, request: ImageGenRequest, progress: StepProgress, cancel: CancellationToken) -> ImageGenOutput:
        with self.lock:
            self._record("generate")
            self.requests.append(request)
            self.generate_started.set()
            if not self.loaded:
                raise RuntimeError("generate() called before load()")
            self.on_gpu = True
            try:
                if self.fail_with is not None:
                    raise self.fail_with
                if self.block_generate is not None:
                    while not self.block_generate.wait(0.01):
                        if cancel.is_cancelled:
                            raise JobCancelledError(cancel.reason or "cancelled")
                paths: list[Path] = []
                for index, seed in enumerate(request.seeds):
                    cancel.raise_if_cancelled()
                    path = request.output_dir / f"{index}.png"
                    self._draw(request.width, request.height, seed).save(path, format="PNG")
                    paths.append(path)
                    progress((index + 1) / len(request.seeds), f"Image {index + 1}")
                return ImageGenOutput(
                    paths=paths, seeds=list(request.seeds), timings={"to_device_s": 0.0}, metadata={"fake": True}
                )
            finally:
                self.on_gpu = False

    def _draw(self, width: int, height: int, seed: int) -> Image.Image:
        image = Image.new("RGB", (width, height), (200, 200, 200))
        if self.layout == "blank":
            return image
        draw = ImageDraw.Draw(image)
        shade = 20 + seed % 40
        if self.layout == "edge":
            draw.rectangle([0, height // 4, width // 3, 3 * height // 4], fill=(shade, 40, 90))
        else:
            draw.rectangle([width * 3 // 8, height // 6, width * 5 // 8, height * 5 // 6], fill=(shade, 40, 90))
        return image


class FakeImageGenRegistry:
    """Model registry double: one text-to-image provider (or none), with switchable usability."""

    def __init__(
        self,
        provider: FakeTextToImageProvider | None = None,
        *,
        usable: bool = True,
        state: str = "installed",
        reason_code: str = "ok",
        vram_error: BaseException | None = None,
        extra_specs: Sequence[ModelSpec] = (),
    ) -> None:
        self.t2i = provider if provider is not None else FakeTextToImageProvider()
        self._usable = usable
        self._state = state if not usable else "installed"
        self._reason_code = reason_code if not usable else "ok"
        self.vram_error = vram_error
        self._specs = [self.t2i.spec, *extra_specs]
        self.events: list[tuple[str, str, tuple[str, ...]]] = []
        self._lock = threading.Lock()

    def _record(self, event: str, model_ids: Sequence[str] = ()) -> None:
        with self._lock:
            self.events.append((event, threading.current_thread().name, tuple(model_ids)))

    # catalog / state
    def specs(self, kind: Any = None) -> list[ModelSpec]:
        return [spec for spec in self._specs if kind is None or spec.kind == kind]

    def status(self, model_id: str) -> ProviderStatus:
        if model_id == self.t2i.model_id and self._usable:
            return self.t2i.status()
        return ProviderStatus(
            model_id=model_id,
            state=self._state if model_id == self.t2i.model_id else "unavailable",  # type: ignore[arg-type]
            reason_code=(  # type: ignore[arg-type]
                self._reason_code if model_id == self.t2i.model_id else "not_approved"
            ),
            reason="switched off by the test",
            revision=None,
        )

    def statuses(self, kind: Any = None) -> list[ProviderStatus]:
        return [self.status(spec.id) for spec in self.specs(kind)]

    def usable(self, model_id: str) -> bool:
        return self.status(model_id).usable

    def default_model(self, kind: Any) -> str | None:
        return next((spec.id for spec in self.specs(kind) if self.usable(spec.id)), None)

    def provider(self, model_id: str) -> FakeTextToImageProvider:
        self._record("provider", (model_id,))
        if model_id != self.t2i.model_id or not self._usable:
            raise KeyError(model_id)
        return self.t2i

    def check_vram_static(self, model_id: str) -> None:
        if self.vram_error is not None:
            raise self.vram_error

    # GPU-thread hooks used by the run worker
    @contextlib.contextmanager
    def guard(self) -> Iterator[None]:
        self._record("guard_enter")
        try:
            yield
        finally:
            self._record("guard_exit")

    def ensure_vram(self, model_ids: Sequence[str]) -> None:
        self._record("ensure_vram", model_ids)

    def offload(self, model_ids: Sequence[str]) -> None:
        self._record("offload", model_ids)
        if self.t2i.model_id in model_ids:
            self.t2i.offload()


def insufficient_vram_error() -> CapabilityUnavailableError:
    return CapabilityUnavailableError(
        "Not enough free VRAM for z_image_turbo",
        capability="image_generation",
        reasons=[
            {"model_id": T2I_MODEL_ID, "state": "unavailable", "reason_code": "insufficient_vram", "reason": "test"}
        ],
    )
