"""TEST-ONLY doubles for segmentation runs (never used by the application).

* :func:`character_image` draws a flat-colour test character (hair, face, eyes, eyebrows, a tiara on the hair, mouth,
  dress) where every part has its own colour key;
* the fake providers "see" those colours: :class:`FakeDetector` boxes the connected components of a class's colour
  (and, like the measured Grounding DINO failure on ALICE, also reports the tiara as an "eyebrow"),
  :class:`FakeRefiner` masks the class colours inside each box, :class:`FakeGrounder` captions with a fixed text;
* :class:`FakeSegmentationRegistry` is a registry double (``status``/``usable``/``provider``/``guard``/
  ``ensure_vram``/``offload``/``specs``) that records the thread of every provider call.

Everything works on the head crop as well (the crop is a resized cut of the same colours).
"""

from __future__ import annotations

import contextlib
import io
import threading
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from core.feature_errors import ModelUnavailableError
from inference.base import CancellationToken
from inference.providers.base import LicenseInfo, ModelProvider, ModelSpec, ProviderStatus
from inference.segmentation.base import Box2D, Detector2D, Grounder2D, Mask2D, MaskRefiner
from inference.segmentation.gdino import pack_phrases, phrase_entries

BACKGROUND = (240, 240, 240)
COLORS: dict[str, tuple[int, int, int]] = {
    "hair": (230, 120, 30),
    "skin": (250, 210, 180),
    "eye": (30, 60, 200),
    "brow": (90, 50, 20),
    "tiara": (250, 220, 0),
    "mouth": (200, 40, 60),
    "dress": (40, 160, 70),
}
CLASS_COLORS: dict[str, tuple[str, ...]] = {
    "hair": ("hair",),
    "body.head.face": ("skin", "eye", "brow", "mouth"),
    "eye.left": ("eye",),
    "eyebrow.left": ("brow",),
    "body.head.mouth": ("mouth",),
    "clothing.dress": ("dress",),
    "accessory.headwear": ("tiara",),
}
"""What each class mask covers (the face mask includes the features, like a real face mask)."""
DETECT_COLORS: dict[str, tuple[str, ...]] = {
    **{k: v for k, v in CLASS_COLORS.items() if k != "body.head.face"},
    "body.head.face": ("skin",),
    "eyebrow.left": ("brow", "tiara"),
}
"""What the fake detector boxes per class: the tiara is (wrongly) also an eyebrow."""
TIARA_BOX = (170, 40, 230, 58)
EYEBROW_Y = (154, 160)


def character_image(size: int = 400, *, rgba: bool = False) -> Image.Image:
    """The flat-colour test character on a light background (transparent background when ``rgba``)."""
    s = size / 400
    img = Image.new("RGBA", (size, size), (*BACKGROUND, 0 if rgba else 255))
    draw = ImageDraw.Draw(img)

    def box(x0: float, y0: float, x1: float, y1: float) -> tuple[int, int, int, int]:
        return int(x0 * s), int(y0 * s), int(x1 * s), int(y1 * s)

    draw.ellipse(box(95, 30, 305, 230), fill=(*COLORS["hair"], 255))
    draw.rectangle(box(130, 280, 270, 399), fill=(*COLORS["dress"], 255))
    draw.ellipse(box(120, 90, 280, 275), fill=(*COLORS["skin"], 255))
    draw.ellipse(box(151, 171, 179, 189), fill=(*COLORS["eye"], 255))
    draw.ellipse(box(221, 171, 249, 189), fill=(*COLORS["eye"], 255))
    draw.rectangle(box(148, EYEBROW_Y[0], 182, EYEBROW_Y[1]), fill=(*COLORS["brow"], 255))
    draw.rectangle(box(218, EYEBROW_Y[0], 252, EYEBROW_Y[1]), fill=(*COLORS["brow"], 255))
    draw.rectangle(box(*TIARA_BOX), fill=(*COLORS["tiara"], 255))
    draw.ellipse(box(182, 229, 218, 241), fill=(*COLORS["mouth"], 255))
    return img if rgba else img.convert("RGB")


def png_of(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _color_mask(rgb: np.ndarray, names: Sequence[str], tolerance: int = 40) -> np.ndarray:
    mask = np.zeros(rgb.shape[:2], dtype=bool)
    for name in names:
        diff = np.abs(rgb.astype(np.int16) - np.array(COLORS[name], dtype=np.int16)).max(axis=2)
        mask |= diff <= tolerance
    return mask


def _components(mask: np.ndarray, min_area: int) -> list[tuple[int, int, int, int]]:
    import cv2

    count, _labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    boxes = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area >= min_area:
            boxes.append((x, y, x + w, y + h))
    return boxes


def _spec(model_id: str, role: str) -> ModelSpec:
    return ModelSpec(
        id=model_id,
        kind="segmentation",
        roles=(role,),
        display_name=model_id,
        provider="fake",
        repo_id=None,
        revision="fake-rev",
        local_subdir=None,
        required_files=(),
        size_bytes=None,
        vram_peak_gb=0.0,
        ram_gb=0.0,
        cold_load_s=None,
        license=LicenseInfo(id="apache-2.0", name="Apache-2.0", url="https://www.apache.org/licenses/LICENSE-2.0",
                            commercial="allowed"),
    )


class _FakeProvider(ModelProvider):
    def __init__(self, model_id: str, role: str, calls: list[tuple[str, str, str]]) -> None:
        super().__init__(_spec(model_id, role))
        self.calls = calls
        self.loaded = False

    def _record(self, what: str) -> None:
        self.calls.append((self.model_id, what, threading.current_thread().name))

    def status(self) -> ProviderStatus:
        return ProviderStatus(self.model_id, "loaded" if self.loaded else "installed", "ok", None, revision="fake-rev")

    def load(self, cancel: CancellationToken) -> None:
        with self.lock:
            self._record("load")
            self.loaded = True

    def offload(self) -> None:
        self._record("offload")

    def unload(self) -> None:
        self.loaded = False


class FakeDetector(_FakeProvider, Detector2D):
    def __init__(self, calls: list[tuple[str, str, str]], *, gate: threading.Event | None = None) -> None:
        super().__init__("grounding_dino_base", "detector", calls)
        self.gate = gate
        self.started = threading.Event()
        self.last_batch_count = 0
        self.last_class_ids: frozenset[str] = frozenset()
        self.requests: list[frozenset[str]] = []

    def detect(
        self,
        image: Path,
        phrases: Mapping[str, tuple[str, ...]],
        *,
        box_threshold: float,
        text_threshold: float,
        cancel: CancellationToken,
    ) -> list[Box2D]:
        self._record("detect")
        self.started.set()
        if self.gate is not None:
            while not self.gate.wait(0.02):
                cancel.raise_if_cancelled()
        entries = phrase_entries(phrases)
        self.last_batch_count = len(pack_phrases(entries, lambda text: len(text.split()) + 2))
        self.last_class_ids = frozenset(cid for cid, _ in entries)
        self.requests.append(self.last_class_ids)
        rgb = np.asarray(Image.open(image).convert("RGB"))
        min_area = max(4, rgb.shape[0] * rgb.shape[1] // 20000)
        boxes: list[Box2D] = []
        for class_id in self.last_class_ids:
            names = DETECT_COLORS.get(class_id)
            if names is None:
                continue
            for xyxy in _components(_color_mask(rgb, names), min_area):
                boxes.append(Box2D(class_id, tuple(float(v) for v in xyxy), 0.8, self.model_id, class_id))  # type: ignore[arg-type]
        return boxes


class FakeRefiner(_FakeProvider, MaskRefiner):
    def __init__(self, calls: list[tuple[str, str, str]]) -> None:
        super().__init__("sam2_1_hiera_large", "refiner", calls)

    def refine(self, image: Path, boxes: Sequence[Box2D], out_dir: Path, cancel: CancellationToken) -> list[Mask2D]:
        self._record("refine")
        rgb = np.asarray(Image.open(image).convert("RGB"))
        out_dir.mkdir(parents=True, exist_ok=True)
        masks: list[Mask2D] = []
        for index, box in enumerate(boxes):
            x0, y0, x1, y1 = (int(round(v)) for v in box.xyxy)
            region = np.zeros(rgb.shape[:2], dtype=bool)
            region[y0:y1, x0:x1] = True
            names = CLASS_COLORS.get(box.class_id, ())
            if box.class_id == "eyebrow.left":
                names = ("brow", "tiara")
            mask = region & _color_mask(rgb, names)
            if not mask.any():
                continue
            path = out_dir / f"{index:03d}.png"
            Image.fromarray(mask.astype(np.uint8) * 255, "L").save(path)
            ys, xs = np.nonzero(mask)
            masks.append(
                Mask2D(box.class_id, path, box.score, (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1),
                       self.model_id, index)
            )
        return masks


class FakeGrounder(_FakeProvider, Grounder2D):
    def __init__(self, calls: list[tuple[str, str, str]], caption: str = "an anime girl with orange hair") -> None:
        super().__init__("florence2_large", "grounder", calls)
        self.caption_text = caption
        self.grounded: list[str] = []

    def caption(self, image: Path, cancel: CancellationToken) -> str:
        self._record("caption")
        return self.caption_text

    def ground(self, image: Path, phrases: Mapping[str, tuple[str, ...]], cancel: CancellationToken) -> list[Box2D]:
        self._record("ground")
        self.grounded.extend(p for _cid, p in phrase_entries(phrases))
        return []


class FakeSegmentationRegistry:
    """Registry double: the given providers are usable; every other model id reports ``unavailable``."""

    def __init__(
        self,
        *,
        detector: bool = True,
        refiner: bool = True,
        grounder: bool = True,
        caption: str = "an anime girl with orange hair",
        gate: threading.Event | None = None,
    ) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.events: list[tuple[str, str, tuple[str, ...]]] = []
        self.providers: dict[str, ModelProvider] = {}
        if detector:
            self.providers["grounding_dino_base"] = FakeDetector(self.calls, gate=gate)
        if refiner:
            self.providers["sam2_1_hiera_large"] = FakeRefiner(self.calls)
        if grounder:
            self.providers["florence2_large"] = FakeGrounder(self.calls, caption)

    @property
    def detector(self) -> FakeDetector:
        return self.providers["grounding_dino_base"]  # type: ignore[return-value]

    @property
    def grounder(self) -> FakeGrounder:
        return self.providers["florence2_large"]  # type: ignore[return-value]

    def status(self, model_id: str) -> ProviderStatus:
        provider = self.providers.get(model_id)
        if provider is not None:
            return provider.status()
        if model_id in {"see_through_l2d", "sapiens2_seg_0_4b"}:
            return ProviderStatus(model_id, "unavailable", "vendored_source_missing", "vendored source missing")
        return ProviderStatus(model_id, "unavailable", "not_approved", "not approved in this test")

    def usable(self, model_id: str) -> bool:
        return self.status(model_id).usable

    def provider(self, model_id: str) -> ModelProvider:
        provider = self.providers.get(model_id)
        if provider is None:
            status = self.status(model_id)
            raise ModelUnavailableError(
                f"{model_id} is unavailable", model_id=model_id, state=status.state, reason_code=status.reason_code
            )
        return provider

    @contextlib.contextmanager
    def guard(self) -> Iterator[None]:
        self.events.append(("guard", threading.current_thread().name, ()))
        yield

    def ensure_vram(self, model_ids: Sequence[str]) -> None:
        self.events.append(("ensure_vram", threading.current_thread().name, tuple(model_ids)))

    def offload(self, model_ids: Sequence[str]) -> None:
        self.events.append(("offload", threading.current_thread().name, tuple(model_ids)))
        for model_id in model_ids:
            provider = self.providers.get(model_id)
            if provider is not None:
                provider.offload()

    def specs(self, kind: Any = None) -> list[Any]:
        return [SimpleNamespace(id=p.model_id, revision="fake-rev", license=SimpleNamespace(id="apache-2.0"))
                for p in self.providers.values()]

    def threads(self, what: str) -> set[str]:
        return {thread for _mid, call, thread in self.calls if call == what}
