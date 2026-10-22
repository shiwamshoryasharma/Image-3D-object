"""Real See-through + Sapiens2 parser smoke (part-aware slice S6; GPU, real weights, vendored model code).

Runs the real S5 ``SegmentationExecutor`` through the real S2 ``ProviderRegistry`` (so the parsers are found
``installed`` from the vendored submodules and the local weights, nothing is overridden) on
``test/images/ALICE/ALICE_full_front_view.png`` and ``test/images/ANIME_CHARACTER_01/Alice.png`` as reference-image
sources, with ``profile: "anime"`` (See-through), ``profile: "realistic"`` (Sapiens2; set explicitly, every sample is
anime art) and ``profile: "auto"`` (must pick ``anime`` from the caption). Anime and realistic run twice (cold, warm).
Asserts taxonomy masks from the parser (face + at least one more class; hair and both eyes for anime), and that
``sys.path`` and
``sys.modules["models"/"utils"]`` are unchanged by the vendored imports. Writes the artifacts and ``report.json`` to
``test/outputs/part_aware/s6_smoke/``.

Skipped unless ``IMG3D_GPU_SMOKE=1``. Run under the GPU lock::

    set IMG3D_GPU_SMOKE=1
    backend\\python\\python.exe -s test\\part_aware\\gpu_lock.py --label s6 -- ^
        backend\\python\\python.exe -s -m pytest -c test\\pytest.ini test\\aux_models\\test_smoke_parsers.py -q -s
"""

from __future__ import annotations

import json
import os
import shutil
import site
import sys
import time
from collections.abc import Iterator
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

RUNTIME = ROOT / "test" / ".runtime" / "s6_smoke"
OUTPUT = ROOT / "test" / "outputs" / "part_aware" / "s6_smoke"
IMAGES = {
    "alice_front": ROOT / "test" / "images" / "ALICE" / "ALICE_full_front_view.png",
    "anime_alice": ROOT / "test" / "images" / "ANIME_CHARACTER_01" / "Alice.png",
}
PARSERS = {"anime": "see_through_l2d", "realistic": "sapiens2_seg_0_4b"}


class _IdleEngine:
    """The registry only asks whether the TRELLIS.2 engine is resident (VRAM rule); it is not."""

    def status(self) -> Any:
        return SimpleNamespace(is_ready=False)


def _module_file(name: str) -> str | None:
    module = sys.modules.get(name)
    return getattr(module, "__file__", None) if module is not None else None


@pytest.fixture(scope="module")
def smoke() -> Iterator[SimpleNamespace]:
    import torch

    if not torch.cuda.is_available():
        pytest.skip("CUDA is not available")
    import models  # noqa: F401 - Img3D's own top-level packages, bound before the vendored imports
    import utils  # noqa: F401
    from config.settings import Settings
    from inference.providers.catalog import CATALOG
    from inference.providers.registry import ProviderRegistry
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
    registry = ProviderRegistry(settings, CATALOG, engine=_IdleEngine())  # type: ignore[arg-type]
    states = {mid: (registry.status(mid).state, registry.status(mid).reason_code) for mid in PARSERS.values()}
    assert states == {mid: ("installed", "ok") for mid in PARSERS.values()}, states
    deps = ExecutorDeps(settings=settings, layout=layout, registry=registry, job_store=None,  # type: ignore[arg-type]
                        reference_store=None, stores={})  # type: ignore[arg-type]
    report: dict[str, Any] = {"registry_states": states, "cases": {}}
    before = SimpleNamespace(path=list(sys.path), models=_module_file("models"), utils=_module_file("utils"))
    torch.cuda.reset_peak_memory_stats()
    yield SimpleNamespace(executor=SegmentationExecutor(deps), layout=layout, registry=registry, report=report)
    report["peak_vram_gb"] = round(torch.cuda.max_memory_allocated() / 1e9, 2)
    report["sys_path_unchanged"] = sys.path == before.path
    report["sys_modules"] = {"models": _module_file("models"), "utils": _module_file("utils")}
    report["vendored_modules"] = sorted(n for n in sys.modules if n.startswith("img3d_vendor"))[:8]
    for model_id in PARSERS.values():
        registry.provider(model_id).unload()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, indent=1))
    assert report["sys_path_unchanged"]
    assert before.models == report["sys_modules"]["models"] and before.utils == report["sys_modules"]["utils"]
    for path in (before.models, before.utils):
        assert path is not None and Path(path).resolve().is_relative_to(BACKEND.resolve())


def _run(smoke: SimpleNamespace, case: str, profile: str) -> tuple[Any, float, Path]:
    from inference.base import CancellationToken
    from models.segmentation import SOURCE_FILE, SegmentationRun, SegmentationSource
    from pipelines.runs.context import RunContext
    from pipelines.runs.reporter import RunReporter
    from processing.image_ops import normalize_to_png
    from utils.ids import new_id
    from utils.timeutil import utcnow

    sg_id = new_id("sg")
    run_dir = smoke.layout.segmentation_dir(sg_id)
    run_dir.mkdir(parents=True)
    normalize_to_png(IMAGES[case], run_dir / SOURCE_FILE)
    source = SegmentationSource(kind="reference_image", reference_set_id="rs_000000000000", image_id="img_000000000000")
    options = {"options": {"profile": profile}, "expected_terms": [], "prompt_phrases": {}}
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


def _record(smoke: SimpleNamespace, name: str, runs: list[tuple[Any, float, Path]], parser_id: str) -> dict[str, Any]:
    from parts.taxonomy import TAXONOMY

    snapshot, _elapsed, run_dir = runs[-1]
    result = snapshot.result
    assert result is not None
    labels = {label.category: label for label in result.labels}
    parse = next(step for step in snapshot.steps if step.id == "parse").details
    provider = smoke.registry.provider(parser_id)
    entry = {
        "subject": result.subject,
        "profile": result.profile,
        "parse": parse,
        "parser_timings": dict(getattr(provider, "last_timings", {})),
        "labels": {c: {"area_px": l.area_px, "confidence": round(l.confidence, 3), "bbox": l.bbox,
                       "sources": l.sources} for c, l in labels.items()},
        "warnings": [f"{w.code}: {w.message}" for w in snapshot.warnings],
        "timings": [dict(r.result.timings) if r.result else None for r, _e, _d in runs],
        "wall_s": [round(e, 2) for _r, e, _d in runs],
    }
    smoke.report["cases"][name] = entry
    out = OUTPUT / name
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(run_dir, out)
    assert all(category in TAXONOMY for category in labels), sorted(labels)
    return entry


@pytest.mark.gpu
@pytest.mark.slow
@pytest.mark.parametrize("case", ["alice_front", "anime_alice"])
@pytest.mark.parametrize("profile", ["anime", "realistic"])
def test_parser_profiles_on_samples(smoke, case, profile):
    parser_id = PARSERS[profile]
    runs = [_run(smoke, case, profile), _run(smoke, case, profile)]
    entry = _record(smoke, f"{case}_{profile}", runs, parser_id)
    assert entry["profile"] == profile, entry["warnings"]
    assert entry["parse"].get("parser") == parser_id and entry["parse"].get("masks", 0) > 0, entry["parse"]
    parsed = {c for c, label in entry["labels"].items() if parser_id in label["sources"]}
    assert "body.head.face" in parsed and len(parsed) >= 2, sorted(parsed)
    if profile == "anime":
        assert "hair" in parsed, sorted(parsed)
        for class_id in ("eye.left", "eye.right"):
            assert class_id in entry["labels"], sorted(entry["labels"])
    # realistic on anime art is out of Sapiens2's domain (measured: anime_alice -> face + one huge Upper_Clothing);
    # only the contract (taxonomy masks from the parser) is asserted, the coverage is recorded in report.json
    assert not any(w.startswith("profile_degraded") and parser_id in w for w in entry["warnings"]), entry["warnings"]


@pytest.mark.gpu
@pytest.mark.slow
@pytest.mark.parametrize("case", ["alice_front", "anime_alice"])
def test_auto_profile_picks_the_anime_parser(smoke, case):
    entry = _record(smoke, f"{case}_auto", [_run(smoke, case, "auto")], PARSERS["anime"])
    assert (entry["subject"], entry["profile"]) == ("anime_character", "anime"), entry["warnings"]
    assert entry["parse"].get("parser") == PARSERS["anime"]
