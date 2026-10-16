"""Real Grounding DINO + SAM 2.1 + Florence-2 segmentation smoke (part-aware slice S5; GPU, real weights).

Runs the real ``SegmentationExecutor`` (baseline profile; the anime/realistic parsers are not installed before S6) on
``test/images/ALICE/ALICE_full_front_view.png`` and ``test/images/ANIME_CHARACTER_01/Alice.png`` as reference-image
sources, then on the read-only TRELLIS fixtures of ``test/part_aware/fixtures.json`` as job sources (copied into
``test/.runtime/s5_smoke/``; ``backend/runtime/jobs`` is never read). Each case runs twice: the second run is the
warm time (weights resident in CPU RAM, moved to the GPU for the phase and back). Asserts hair, face and both eye
masks, and that no eyebrow lies on the tiara (every eyebrow sits above an eye, within 1.5 eye heights). Writes the
artifacts and ``report.json`` to ``test/outputs/part_aware/s5_smoke/``.

Skipped unless ``IMG3D_GPU_SMOKE=1`` (the default suite never loads weights). Run under the GPU lock::

    set IMG3D_GPU_SMOKE=1
    backend\\python\\python.exe -s test\\part_aware\\gpu_lock.py --label s5-smoke -- ^
        backend\\python\\python.exe -s -m pytest -c test\\pytest.ini test\\aux_models\\test_smoke_segmentation.py -q -s
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import site
import sys
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
_user_site = os.path.normcase(os.path.abspath(site.getusersitepackages()))
sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p)) != _user_site]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

if os.environ.get("IMG3D_GPU_SMOKE") != "1":
    pytest.skip("GPU smoke: set IMG3D_GPU_SMOKE=1 and run under test/part_aware/gpu_lock.py", allow_module_level=True)

RUNTIME = ROOT / "test" / ".runtime" / "s5_smoke"
OUTPUT = ROOT / "test" / "outputs" / "part_aware" / "s5_smoke"


def _fixtures() -> dict[str, dict[str, str]]:
    """``{case: {"dir": ...}}`` from fixtures.json. Its ``regenerate`` string holds unescaped Windows backslashes,
    so the file is not strict JSON; only the fixture ``dir`` entries are read."""
    text = (ROOT / "test" / "part_aware" / "fixtures.json").read_text(encoding="utf-8")
    return {case: {"dir": path} for case, path in re.findall(r'"(\w+)":\s*\{[^{}]*?"dir":\s*"([^"]+)"', text)}


FIXTURES = _fixtures()
IMAGES = {
    "alice_front": ROOT / "test" / "images" / "ALICE" / "ALICE_full_front_view.png",
    "anime_alice": ROOT / "test" / "images" / "ANIME_CHARACTER_01" / "Alice.png",
}
MODEL_DIRS = {
    "grounding_dino_base": ("seg/grounding-dino-base", "grounding-dino-base", "detector"),
    "sam2_1_hiera_large": ("seg/sam2.1-hiera-large", "sam2.1-hiera-large", "refiner"),
    "florence2_large": ("seg/Florence-2-large", "Florence-2-large", "grounder"),
}


class SmokeRegistry:
    """Test-only registry over the three real transformers providers (the S2 registry may not be merged yet)."""

    def __init__(self, settings: Any) -> None:
        from inference.providers.base import LicenseInfo, ModelSpec, ProviderStatus
        from inference.segmentation.florence2 import Florence2Provider
        from inference.segmentation.gdino import GroundingDinoProvider
        from inference.segmentation.sam2 import Sam2Provider

        manifest = json.loads((BACKEND / ".model" / "seg" / "download_manifest.json").read_text(encoding="utf-8"))
        classes = {"grounding_dino_base": GroundingDinoProvider, "sam2_1_hiera_large": Sam2Provider,
                   "florence2_large": Florence2Provider}
        self._status_cls = ProviderStatus
        self.providers: dict[str, Any] = {}
        for model_id, (subdir, folder, role) in MODEL_DIRS.items():
            spec = ModelSpec(
                id=model_id, kind="segmentation", roles=(role,), display_name=model_id, provider=model_id,
                repo_id=manifest[folder]["repo"], revision=manifest[folder]["revision"], local_subdir=subdir,
                required_files=("config.json",), size_bytes=manifest[folder]["size_bytes"], vram_peak_gb=3.0,
                ram_gb=2.0, cold_load_s=None,
                license=LicenseInfo(id="apache-2.0" if model_id != "florence2_large" else "mit", name="", url="",
                                    commercial="allowed"),
            )
            self.providers[model_id] = classes[model_id](spec, settings)

    def status(self, model_id: str) -> Any:
        provider = self.providers.get(model_id)
        if provider is None:
            return self._status_cls(model_id, "unavailable", "vendored_source_missing", "not part of the S5 smoke")
        return provider.status()

    def usable(self, model_id: str) -> bool:
        return self.status(model_id).usable

    def provider(self, model_id: str) -> Any:
        return self.providers[model_id]

    @contextlib.contextmanager
    def guard(self) -> Iterator[None]:
        yield

    def ensure_vram(self, model_ids: Sequence[str]) -> None:
        return None

    def offload(self, model_ids: Sequence[str]) -> None:
        for model_id in model_ids:
            if model_id in self.providers:
                self.providers[model_id].offload()

    def specs(self, kind: Any = None) -> list[Any]:
        return [p.spec for p in self.providers.values()]


@pytest.fixture(scope="module")
def smoke() -> Iterator[SimpleNamespace]:
    import torch

    if not torch.cuda.is_available():
        pytest.skip("CUDA is not available")
    from config.settings import Settings
    from inference.trellis.environment import configure_hf_environment
    from pipelines.runs.executor import ExecutorDeps
    from pipelines.segmentation import SegmentationExecutor
    from storage.paths import RuntimePaths
    from storage.run_layout import RunLayout

    shutil.rmtree(RUNTIME, ignore_errors=True)
    RUNTIME.mkdir(parents=True)
    settings = Settings(_env_file=None, runtime_dir=RUNTIME / "runtime", preload_model=False, auth_enabled=False)
    configure_hf_environment(settings.hf_home)
    layout = RunLayout(RuntimePaths(settings.runtime_dir))
    layout.ensure()
    registry = SmokeRegistry(settings)
    deps = ExecutorDeps(settings=settings, layout=layout, registry=registry, job_store=None,  # type: ignore[arg-type]
                        reference_store=None, stores={})  # type: ignore[arg-type]
    report: dict[str, Any] = {"cases": {}}
    torch.cuda.reset_peak_memory_stats()
    yield SimpleNamespace(executor=SegmentationExecutor(deps), layout=layout, registry=registry, report=report)
    report["peak_vram_gb"] = round(torch.cuda.max_memory_allocated() / 1e9, 2)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))


def _run(smoke: SimpleNamespace, case: str, source_kind: str) -> tuple[Any, float, Path]:
    from inference.base import CancellationToken
    from models.segmentation import ORIGINAL_FILE, SOURCE_FILE, SegmentationRun, SegmentationSource
    from pipelines.runs.context import RunContext
    from pipelines.runs.reporter import RunReporter
    from processing.image_ops import normalize_to_png
    from utils.ids import new_id
    from utils.timeutil import utcnow

    sg_id = new_id("sg")
    run_dir = smoke.layout.segmentation_dir(sg_id)
    run_dir.mkdir(parents=True)
    options: dict[str, Any] = {"options": {}, "expected_terms": [], "prompt_phrases": {}}
    if source_kind == "reference_image":
        normalize_to_png(IMAGES[case], run_dir / SOURCE_FILE)
        source = SegmentationSource(kind="reference_image", reference_set_id="rs_000000000000", image_id="img_000000000000")
    else:
        fixture = ROOT / FIXTURES[case]["dir"]
        if not fixture.is_dir():
            pytest.skip(f"fixture {case} missing: {FIXTURES[case]['dir']}")
        copy = RUNTIME / "fixtures" / case
        if not copy.is_dir():
            shutil.copytree(fixture / "inputs", copy / "inputs")
            shutil.copyfile(fixture / "preview.png", copy / "preview.png")
            shutil.copyfile(fixture / "summary.json", copy / "summary.json")
        summary = json.loads((copy / "summary.json").read_text(encoding="utf-8"))
        shutil.copyfile(copy / "preview.png", run_dir / SOURCE_FILE)
        shutil.copyfile(next((copy / "inputs").glob("*.png")), run_dir / ORIGINAL_FILE)
        options["preprocess"] = _find(summary, "preprocess")
        source = SegmentationSource(kind="job", job_id="job_000000000000")
    run = SegmentationRun(id=sg_id, created_at=utcnow(), options=options, source=source)
    executor = smoke.executor
    reporter = RunReporter(run, publish=lambda *_a, **_k: None, persist=lambda _r: True)
    reporter.set_plan(executor.plan(run))
    work_dir = smoke.layout.work_dir("segmentation", sg_id)
    ctx = RunContext(run_id=sg_id, kind="segmentation", cancel=CancellationToken(), work_dir=work_dir,
                     device="cuda", registry=smoke.registry, reporter=reporter)
    started = time.perf_counter()
    state = executor.prepare(run, work_dir)
    for phase in executor.phases(run):
        if phase.device == "gpu":
            with smoke.registry.guard():
                try:
                    phase.fn(run, state, ctx)
                finally:
                    smoke.registry.offload(phase.models)
        else:
            phase.fn(run, state, ctx)
    final = executor.finalize(run, state, ctx)
    elapsed = time.perf_counter() - started
    snapshot = reporter.snapshot()
    snapshot.result = final.result
    shutil.rmtree(work_dir, ignore_errors=True)
    return snapshot, elapsed, run_dir


def _find(data: Any, key: str) -> Any:
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for value in data.values():
            found = _find(value, key)
            if found is not None:
                return found
    return None


def _check_and_record(smoke: SimpleNamespace, name: str, cold: tuple[Any, float, Path], warm: tuple[Any, float, Path]) -> None:
    run, cold_s, _cold_dir = cold
    warm_run, warm_s, run_dir = warm
    result = warm_run.result
    assert result is not None
    labels = {label.category: label for label in result.labels}
    steps = {step.id: step for step in warm_run.steps}
    detect = steps["detect"].details
    rejected = steps["fuse"].details.get("rejected", [])
    eyes = [labels[c] for c in ("eye.left", "eye.right") if c in labels]
    brows = [labels[c] for c in ("eyebrow.left", "eyebrow.right") if c in labels]
    smoke.report["cases"][name] = {
        "space": result.space,
        "frame": result.frame.model_dump() if result.frame is not None else None,
        "subject": result.subject,
        "profile": result.profile,
        "labels": {c: {"area_px": l.area_px, "confidence": round(l.confidence, 3), "bbox": l.bbox,
                       "sources": l.sources} for c, l in labels.items()},
        "annotations": sorted(result.annotations),
        "warnings": [f"{w.code}: {w.message}" for w in warm_run.warnings],
        "gdino_batches": detect.get("gdino_batches"),
        "gdino_classes": detect.get("gdino_classes"),
        "head_batches": detect.get("head_batches"),
        "head_box": detect.get("head_box"),
        "head_full_resolution": detect.get("head_full_resolution"),
        "boxes": detect.get("boxes"),
        "head_boxes": detect.get("head_boxes"),
        "eyebrow_rejections": [r for r in rejected if r.get("reason") == "eyebrow_not_above_eye"],
        "timings_cold": dict(run.result.timings) if run.result else None,
        "timings_warm": dict(result.timings),
        "wall_cold_s": round(cold_s, 2),
        "wall_warm_s": round(warm_s, 2),
    }
    out = OUTPUT / name
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(run_dir, out)
    for class_id in ("hair", "body.head.face", "eye.left", "eye.right"):
        assert class_id in labels, f"{name}: {class_id} missing ({sorted(labels)})"
    for brow in brows:
        cx = (brow.bbox[0] + brow.bbox[2]) / 2
        cy = (brow.bbox[1] + brow.bbox[3]) / 2
        assert any(
            cy < eye.bbox[1] and eye.bbox[1] - cy <= 1.5 * (eye.bbox[3] - eye.bbox[1]) + 2
            and eye.bbox[0] - (eye.bbox[2] - eye.bbox[0]) <= cx <= eye.bbox[2] + (eye.bbox[2] - eye.bbox[0])
            for eye in eyes
        ), f"{name}: eyebrow {brow.bbox} is not just above an eye"
    assert detect.get("gdino_batches", 0) >= 2  # the whole alias vocabulary needs several 200-token batches


@pytest.mark.gpu
@pytest.mark.slow
@pytest.mark.parametrize("case", ["alice_front", "anime_alice"])
@pytest.mark.parametrize("source_kind", ["reference_image", "job"])
def test_baseline_segmentation_on_samples(smoke, case, source_kind):
    cold = _run(smoke, case, source_kind)
    warm = _run(smoke, case, source_kind)
    _check_and_record(smoke, f"{case}_{source_kind}", cold, warm)
