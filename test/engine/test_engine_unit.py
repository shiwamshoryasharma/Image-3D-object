"""Engine unit tests that need neither the native extensions nor the weights.

* capabilities / status contract,
* ``load()`` never raises and reports ``unavailable`` with an actionable message when the runtime is missing,
* ``generate()`` validation and not-ready behaviour,
* ``device_info()`` shape and thread-safety,
* sampler step hooks (progress + cancellation + restoration) on the real upstream Euler sampler,
* input-image helpers (alpha detection / mode normalization / preview).
"""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from core.exceptions import EngineUnavailableError, JobCancelledError, ValidationAppError
from inference.base import (
    CancellationToken,
    ConditioningImage,
    GenerationParams,
    GenerationRequest,
    NullProgressReporter,
)
from inference.trellis.engine import TRELLIS_CAPABILITIES, TrellisEngine
from inference.trellis.generation import PIPELINE_TYPES, merged_sampler_params, pipeline_type_for
from inference.trellis.imaging import (
    has_alpha_foreground,
    has_real_alpha,
    load_conditioning_image,
    make_preview,
    needs_upstream_preprocess,
    normalize_mode,
)
from inference.base import SamplerOverrides
from models.enums import EngineState, ReferenceMode, ReferenceSlot

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
SAMPLE_IMAGE = Path(__file__).resolve().parents[1] / "images" / "ALICE" / "ALICE_full_front_view.png"


def settings_for(tmp_path: Path, **overrides) -> SimpleNamespace:
    values = dict(
        model_dir=BACKEND_DIR / ".model" / "TRELLIS.2-4B",
        aux_model_root=BACKEND_DIR / ".model",
        trellis_source_dir=BACKEND_DIR / "third_party" / "TRELLIS.2",
        hf_home=tmp_path / "hf_home",
        hf_token=None,
        allow_model_downloads=False,
        device="cuda",
        low_vram=True,
        attention_backend="auto",
        runtime_dir=tmp_path / "runtime",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def request_for(tmp_path: Path, **params) -> GenerationRequest:
    image = ConditioningImage("img_000000000001", ReferenceSlot.FRONT, SAMPLE_IMAGE, True)
    return GenerationRequest(
        job_id="job_000000000001",
        images=[image],
        all_references=[image],
        params=GenerationParams(**params),
        output_dir=tmp_path / "job",
    )


# --------------------------------------------------------------------------------------------------
# contract
# --------------------------------------------------------------------------------------------------


def test_capabilities_match_contract(tmp_path: Path) -> None:
    caps = TrellisEngine(settings_for(tmp_path)).capabilities()
    assert caps is TRELLIS_CAPABILITIES
    assert caps.to_dict() == {
        "resolutions": [512, 1024, 1536],
        "texture_sizes": [512, 1024, 2048, 4096],
        "polygon_target": {"min": 5000, "max": 2000000, "default": 500000},
        "max_reference_images": 1,
        "reference_modes": ["primary_only"],
        "supports_cancel": True,
        "supports_texture": True,
    }


def test_initial_status(tmp_path: Path) -> None:
    engine = TrellisEngine(settings_for(tmp_path))
    status = engine.status()
    assert engine.name == "trellis2"
    assert status.state is EngineState.NOT_LOADED
    data = status.to_dict()
    assert data["details"]["low_vram"] is True
    assert data["details"]["model_dir"].endswith("TRELLIS.2-4B")
    assert data["details"]["attention_backend"] == "auto"


def test_pipeline_type_mapping() -> None:
    assert PIPELINE_TYPES == {512: "512", 1024: "1024_cascade", 1536: "1536_cascade"}
    assert pipeline_type_for(1536) == "1536_cascade"
    with pytest.raises(ValidationAppError):
        pipeline_type_for(768)


def test_sampler_overrides_apply_over_upstream_defaults() -> None:
    defaults = {"steps": 12, "guidance_strength": 7.5, "guidance_rescale": 0.7, "guidance_interval": [0.6, 1.0], "rescale_t": 5.0}
    merged = merged_sampler_params(defaults, SamplerOverrides(steps=20, rescale_t=3.0))
    assert merged == {**defaults, "steps": 20, "rescale_t": 3.0}
    assert merged_sampler_params(defaults, SamplerOverrides()) == defaults


# --------------------------------------------------------------------------------------------------
# lifecycle without a usable runtime
# --------------------------------------------------------------------------------------------------


def test_load_reports_unavailable_without_raising(tmp_path: Path) -> None:
    engine = TrellisEngine(settings_for(tmp_path, trellis_source_dir=tmp_path / "no_such_checkout"))
    seen: list[EngineState] = []
    engine.load(on_status=lambda status: seen.append(status.state))
    status = engine.status()
    assert status.state is EngineState.UNAVAILABLE
    assert "TRELLIS.2 source not found" in (status.error or "")
    assert "IMG3D_TRELLIS_SOURCE_DIR" in (status.error or "")
    assert seen[0] is EngineState.LOADING and seen[-1] is EngineState.UNAVAILABLE
    # unload keeps the actionable unavailable status
    engine.unload()
    assert engine.status().state is EngineState.UNAVAILABLE


def test_load_reports_unavailable_for_non_cuda_device(tmp_path: Path) -> None:
    engine = TrellisEngine(settings_for(tmp_path, device="cpu"))
    engine.load()
    status = engine.status()
    assert status.state is EngineState.UNAVAILABLE
    assert "IMG3D_DEVICE=cuda" in (status.error or "")


def test_generate_requires_ready_engine(tmp_path: Path) -> None:
    engine = TrellisEngine(settings_for(tmp_path))
    with pytest.raises(EngineUnavailableError):
        engine.generate(request_for(tmp_path, resolution=512), NullProgressReporter(), CancellationToken())


@pytest.mark.parametrize(
    "params",
    [
        {"resolution": 768},
        {"texture_size": 3000},
        {"polygon_target": 10},
        {"polygon_target": 3_000_000},
        {"seed": -1},
    ],
)
def test_generate_validates_params(tmp_path: Path, params: dict) -> None:
    engine = TrellisEngine(settings_for(tmp_path))
    with pytest.raises(ValidationAppError):
        engine.generate(request_for(tmp_path, **params), NullProgressReporter(), CancellationToken())


def test_generate_rejects_multi_view(tmp_path: Path) -> None:
    engine = TrellisEngine(settings_for(tmp_path))
    request = request_for(tmp_path)
    multi = GenerationRequest(
        job_id=request.job_id, images=request.images, all_references=request.all_references,
        params=request.params, output_dir=request.output_dir, reference_mode=ReferenceMode.MULTI_VIEW,
    )
    with pytest.raises(ValidationAppError, match="multi_view"):
        engine.generate(multi, NullProgressReporter(), CancellationToken())


# --------------------------------------------------------------------------------------------------
# device info
# --------------------------------------------------------------------------------------------------


def test_device_info_shape_and_threads(tmp_path: Path, cuda_available: bool) -> None:
    engine = TrellisEngine(settings_for(tmp_path))
    results: list[dict | None] = []

    def worker() -> None:
        for _ in range(5):
            results.append(engine.device_info())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 20
    info = results[-1]
    assert info is not None
    assert set(info) == {
        "cuda_available", "name", "compute_capability", "vram_total_mb", "vram_used_mb", "vram_free_mb",
        "torch_version", "cuda_version",
    }
    assert info["cuda_available"] is cuda_available
    if cuda_available:
        assert info["vram_total_mb"] > 0
        assert info["vram_used_mb"] + info["vram_free_mb"] == pytest.approx(info["vram_total_mb"], abs=2)
        assert info["compute_capability"].count(".") == 1


# --------------------------------------------------------------------------------------------------
# sampler hooks on the real upstream sampler (CPU, dummy velocity model)
# --------------------------------------------------------------------------------------------------


def _sampler():
    import torch
    from trellis2.pipelines.samplers import FlowEulerGuidanceIntervalSampler

    sampler = FlowEulerGuidanceIntervalSampler(sigma_min=1e-5)
    calls: list[float] = []

    def model(x_t, t, cond, **kwargs):
        calls.append(float(t[0]))
        return torch.zeros_like(x_t)

    noise = torch.randn(1, 8, 4, 4, 4)
    kwargs = dict(
        cond=torch.zeros(1, 3, 8), neg_cond=torch.zeros(1, 3, 8), steps=6, guidance_strength=7.5,
        guidance_rescale=0.0, guidance_interval=[0.6, 1.0], rescale_t=5.0, verbose=False,
    )
    return sampler, model, noise, kwargs, calls


def test_sampler_hook_reports_every_step_and_restores() -> None:
    from inference.trellis import compat

    sampler, model, noise, kwargs, _calls = _sampler()
    steps: list[int] = []
    checks: list[int] = []
    assert "sample_once" not in vars(sampler)
    with compat.hook_sampler_steps(
        sampler, before_step=lambda: checks.append(1), after_step=lambda: steps.append(len(steps) + 1)
    ):
        assert "sample_once" in vars(sampler)
        result = sampler.sample(model, noise, **kwargs)
    assert steps == [1, 2, 3, 4, 5, 6]
    assert len(checks) == 6
    assert result.samples.shape == noise.shape
    assert "sample_once" not in vars(sampler)  # class method visible again


def test_sampler_hook_cancellation_propagates_and_restores() -> None:
    from inference.trellis import compat

    sampler, model, noise, kwargs, calls = _sampler()
    token = CancellationToken()
    done: list[int] = []

    def after() -> None:
        done.append(1)
        if len(done) == 2:
            token.cancel("user requested")
        token.raise_if_cancelled()

    with pytest.raises(JobCancelledError, match="user requested"):
        with compat.hook_sampler_steps(sampler, before_step=token.raise_if_cancelled, after_step=after):
            sampler.sample(model, noise, **kwargs)
    assert len(done) == 2
    assert "sample_once" not in vars(sampler)
    # guidance interval [0.6, 1.0]: the first steps run CFG (2 model calls), so at most 4 calls happened
    assert 2 <= len(calls) <= 4


def test_silent_tqdm_passthrough() -> None:
    from inference.trellis import compat

    assert list(compat._silent_tqdm([1, 2, 3], desc="x", disable=False)) == [1, 2, 3]


# --------------------------------------------------------------------------------------------------
# upstream print capture (sys.stdout is process-wide; only the loading thread is captured)
# --------------------------------------------------------------------------------------------------


def test_capture_prints_only_captures_the_calling_thread(capsys, caplog) -> None:
    import logging

    from inference.trellis import compat

    target = logging.getLogger("test.capture")
    caplog.set_level(logging.INFO, logger="test.capture")
    with compat.capture_prints(target):
        print("from the loading thread")
        print("partial line without newline", end="")
        other = threading.Thread(target=lambda: print("from another thread", flush=True))
        other.start()
        other.join()
    captured = [r.getMessage() for r in caplog.records if r.name == "test.capture"]
    assert captured == ["from the loading thread", "partial line without newline"]
    assert "from another thread" in capsys.readouterr().out


def test_capture_prints_is_reentrancy_safe(capsys) -> None:
    import logging
    import sys

    from inference.trellis import compat

    class StdoutHandler(logging.Handler):
        """A handler that resolves sys.stdout at emit time (would recurse without the guard)."""

        def emit(self, record: logging.LogRecord) -> None:
            sys.stdout.write(f"handler:{record.getMessage()}\n")

    target = logging.getLogger("test.capture.reentrant")
    target.propagate = False
    handler = StdoutHandler()
    target.addHandler(handler)
    try:
        with compat.capture_prints(target):
            print("hello")
    finally:
        target.removeHandler(handler)
    assert "handler:hello" in capsys.readouterr().out


# --------------------------------------------------------------------------------------------------
# image helpers
# --------------------------------------------------------------------------------------------------


def test_sample_image_has_real_alpha() -> None:
    image = load_conditioning_image(SAMPLE_IMAGE)
    assert image.mode == "RGBA"
    assert has_real_alpha(image)
    assert has_alpha_foreground(image)
    assert needs_upstream_preprocess(image, remove_background=False)  # alpha is still used for the crop


def test_alpha_rules() -> None:
    opaque = Image.new("RGBA", (16, 16), (10, 20, 30, 255))
    assert not has_real_alpha(opaque)
    assert needs_upstream_preprocess(opaque, remove_background=True)  # -> background removal
    assert not needs_upstream_preprocess(opaque, remove_background=False)  # -> untouched
    rgb = Image.new("RGB", (16, 16))
    assert not has_real_alpha(rgb) and has_alpha_foreground(rgb)
    faint = Image.new("RGBA", (16, 16), (0, 0, 0, 100))
    assert has_real_alpha(faint) and not has_alpha_foreground(faint)


def test_normalize_mode_variants() -> None:
    palette = Image.new("P", (8, 8))
    palette.info["transparency"] = 0
    assert normalize_mode(palette).mode == "RGBA"
    assert normalize_mode(Image.new("LA", (8, 8))).mode == "RGBA"
    assert normalize_mode(Image.new("L", (8, 8))).mode == "RGB"
    assert normalize_mode(Image.new("CMYK", (8, 8))).mode == "RGB"
    wide = Image.new("I;16", (8, 8), 65535)
    converted = normalize_mode(wide)
    assert converted.mode == "RGB" and converted.getpixel((0, 0)) == (255, 255, 255)
    half = normalize_mode(Image.new("I;16", (8, 8), 32768))
    assert half.getpixel((0, 0))[0] == 128


def test_preview_is_capped() -> None:
    big = Image.new("RGB", (3000, 1500))
    preview = make_preview(big)
    assert max(preview.size) == 1024 and preview.mode == "RGB"
    small = Image.new("RGBA", (300, 200))
    assert make_preview(small).size == (300, 200)
