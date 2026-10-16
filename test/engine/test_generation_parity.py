"""Parity of the staged engine run with upstream ``Trellis2ImageTo3DPipeline.run`` (no weights, no GPU needed).

A *recording* test double subclasses the real upstream pipeline class and overrides only the heavy stage methods
(``preprocess_image``, ``get_cond``, ``sample_*``, ``decode_latent``). Each override records its arguments plus a
fingerprint of the torch RNG state at call time and then consumes randomness (like the real sampling noise does).
The real upstream ``run()`` and :class:`inference.trellis.generation.TrellisGenerationRun` are executed against
identical doubles and must produce the *same* call sequence, arguments and RNG states. This pins seed placement,
conditioning resolutions, ``ss_res`` mapping, cascade arguments/token cap, model selection and the decode
resolution to upstream semantics.

Engine-level tests on the same double check progress/cancellation, sampler-hook restoration, low-VRAM model
placement after cancel/error and the OOM mapping.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

torch = pytest.importorskip("torch")

from trellis2.pipelines.trellis2_image_to_3d import Trellis2ImageTo3DPipeline  # noqa: E402

from core.exceptions import EngineError, JobCancelledError  # noqa: E402
from inference.base import (  # noqa: E402
    CancellationToken,
    ConditioningImage,
    GenerationParams,
    GenerationRequest,
    SamplerOverrides,
)
from inference.trellis import generation as generation_module  # noqa: E402
from inference.trellis.engine import TrellisEngine  # noqa: E402
from inference.trellis.generation import TrellisGenerationRun  # noqa: E402
from models.enums import EngineState, JobStage, ReferenceSlot  # noqa: E402

UPSTREAM_DEFAULTS = {
    "sparse_structure": {"steps": 3, "guidance_strength": 7.5, "guidance_rescale": 0.7,
                         "guidance_interval": [0.6, 1.0], "rescale_t": 5.0},
    "shape": {"steps": 4, "guidance_strength": 7.5, "guidance_rescale": 0.5,
              "guidance_interval": [0.6, 1.0], "rescale_t": 3.0},
    "texture": {"steps": 2, "guidance_strength": 1.0, "guidance_rescale": 0.0,
                "guidance_interval": [0.6, 0.9], "rescale_t": 3.0},
}


# --------------------------------------------------------------------------------------------------
# test doubles
# --------------------------------------------------------------------------------------------------


class _Device:
    """Records ``.to()`` / ``.cpu()`` placement like an ``nn.Module`` / upstream wrapper would."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.device = "cpu"
        self.low_vram = False

    def to(self, device: Any) -> _Device:
        self.device = str(device)
        return self

    def cpu(self) -> _Device:
        self.device = "cpu"
        return self

    def __repr__(self) -> str:
        return self.name


class _Sampler:
    def __init__(self, name: str) -> None:
        self.name = name

    def sample_once(self, *args: Any, **kwargs: Any) -> None:
        return None


def _rng_fingerprint() -> str:
    return hashlib.sha1(torch.random.get_rng_state().numpy().tobytes()).hexdigest()[:16]


class RecordingPipeline(Trellis2ImageTo3DPipeline):
    """Upstream pipeline class with recording stage methods (everything else is upstream code)."""

    def __init__(self, *, effective_cascade_resolution: int | None = None, fail: dict[str, BaseException] | None = None,
                 cancel_in_shape: CancellationToken | None = None, low_vram: bool = False) -> None:
        super().__init__(models=None)  # upstream returns early; attributes are set below
        self.models = {name: _Device(name) for name in Trellis2ImageTo3DPipeline.model_names_to_load}
        self.sparse_structure_sampler = _Sampler("ss")
        self.shape_slat_sampler = _Sampler("shape")
        self.tex_slat_sampler = _Sampler("tex")
        self.sparse_structure_sampler_params = dict(UPSTREAM_DEFAULTS["sparse_structure"])
        self.shape_slat_sampler_params = dict(UPSTREAM_DEFAULTS["shape"])
        self.tex_slat_sampler_params = dict(UPSTREAM_DEFAULTS["texture"])
        self.image_cond_model = _Device("image_cond_model")
        self.rembg_model = _Device("rembg_model")
        self.low_vram = low_vram
        self.default_pipeline_type = "1024_cascade"
        self.pbr_attr_layout = {"base_color": slice(0, 3), "metallic": slice(3, 4),
                                "roughness": slice(4, 5), "alpha": slice(5, 6)}
        self._device = "cuda"
        self.calls: list[tuple[Any, ...]] = []
        self._effective = effective_cascade_resolution
        self._fail = fail or {}
        self._cancel_in_shape = cancel_in_shape

    # -- helpers ---------------------------------------------------------------------------------
    def _record(self, name: str, *args: Any) -> None:
        # The RNG state only matters after the seed is set (upstream seeds right after preprocessing); the two
        # runs start from deliberately different prior states, so pre-seed calls are recorded without it.
        fingerprint = None if name == "preprocess_image" else _rng_fingerprint()
        self.calls.append((name, fingerprint, *args))
        if name in self._fail:
            raise self._fail[name]
        torch.randn(8)  # consume RNG like the real noise sampling

    def _run_steps(self, sampler: _Sampler, steps: int, *, device_model: _Device | None = None) -> None:
        if device_model is not None and self.low_vram:
            device_model.to(self.device)  # like upstream: moved to the GPU for sampling, back to CPU afterwards
        for _ in range(steps):
            sampler.sample_once()
        if device_model is not None and self.low_vram:
            device_model.cpu()

    @staticmethod
    def _params(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
        return {**defaults, **overrides}

    # -- recorded upstream stage methods ---------------------------------------------------------
    def preprocess_image(self, input: Image.Image) -> Image.Image:
        self._record("preprocess_image", input.mode, input.size)
        return input.convert("RGB").resize((48, 48))

    def get_cond(self, image: Any, resolution: int, include_neg_cond: bool = True) -> dict:
        self._record("get_cond", resolution, [i.size for i in image], include_neg_cond)
        return {"cond": f"cond{resolution}", "neg_cond": f"neg{resolution}"}

    def sample_sparse_structure(self, cond: dict, resolution: int, num_samples: int = 1,
                                sampler_params: dict = {}) -> torch.Tensor:  # noqa: B006 - upstream signature
        merged = self._params(self.sparse_structure_sampler_params, sampler_params)
        self._record("sample_sparse_structure", cond["cond"], resolution, num_samples, merged)
        self._run_steps(self.sparse_structure_sampler, merged["steps"],
                        device_model=self.models["sparse_structure_flow_model"])
        return torch.zeros(7, 4, dtype=torch.int32)

    def sample_shape_slat(self, cond: dict, flow_model: Any, coords: torch.Tensor,
                          sampler_params: dict = {}) -> Any:  # noqa: B006
        merged = self._params(self.shape_slat_sampler_params, sampler_params)
        self._record("sample_shape_slat", cond["cond"], flow_model.name, int(coords.shape[0]), merged)
        self._run_steps(self.shape_slat_sampler, merged["steps"], device_model=flow_model)
        return SimpleNamespace(feats=torch.zeros(11, 32), tag="shape_slat")

    def sample_shape_slat_cascade(self, lr_cond: dict, cond: dict, flow_model_lr: Any, flow_model: Any,
                                  lr_resolution: int, resolution: int, coords: torch.Tensor,
                                  sampler_params: dict = {}, max_num_tokens: int = 49152) -> Any:  # noqa: B006
        merged = self._params(self.shape_slat_sampler_params, sampler_params)
        self._record("sample_shape_slat_cascade", lr_cond["cond"], cond["cond"], flow_model_lr.name,
                     flow_model.name, lr_resolution, resolution, int(coords.shape[0]), merged, max_num_tokens)
        self._run_steps(self.shape_slat_sampler, merged["steps"], device_model=flow_model_lr)
        if self.low_vram:  # upstream: decoder moved to the GPU with low_vram=True for the upsample
            self.models["shape_slat_decoder"].to(self.device)
            self.models["shape_slat_decoder"].low_vram = True
        if "shape" in self._fail or self._cancel_in_shape is not None:
            # emulate a failure in the middle of the cascade (decoder still on the GPU)
            for _ in range(2):
                self.shape_slat_sampler.sample_once()
                if self._cancel_in_shape is not None:
                    self._cancel_in_shape.cancel("cancel inside cascade")
            raise self._fail.get("shape", RuntimeError("unreachable"))
        if self.low_vram:
            self.models["shape_slat_decoder"].cpu()
            self.models["shape_slat_decoder"].low_vram = False
        self._run_steps(self.shape_slat_sampler, merged["steps"], device_model=flow_model)
        return SimpleNamespace(feats=torch.zeros(13, 32), tag="shape_slat"), self._effective or resolution

    def sample_tex_slat(self, cond: dict, flow_model: Any, shape_slat: Any,
                        sampler_params: dict = {}) -> Any:  # noqa: B006
        merged = self._params(self.tex_slat_sampler_params, sampler_params)
        self._record("sample_tex_slat", cond["cond"], flow_model.name, shape_slat.tag, merged)
        self._run_steps(self.tex_slat_sampler, merged["steps"], device_model=flow_model)
        return SimpleNamespace(tag="tex_slat")

    def decode_latent(self, shape_slat: Any, tex_slat: Any, resolution: int) -> list[Any]:
        self._record("decode_latent", shape_slat.tag, tex_slat.tag, resolution)
        return [SimpleNamespace(vertices=torch.zeros(10, 3), faces=torch.zeros(12, 3), tag="mesh")]


class RecordingReporter:
    def __init__(self) -> None:
        self.events: list[tuple[Any, ...]] = []

    def stage(self, stage: JobStage, message: str | None = None, total_steps: int | None = None) -> None:
        self.events.append(("stage", str(stage), total_steps))

    def step(self, step: int, total_steps: int | None = None, message: str | None = None) -> None:
        self.events.append(("step", step, total_steps))

    def stages(self) -> list[str]:
        return [e[1] for e in self.events if e[0] == "stage"]

    def max_step(self, stage: str) -> tuple[int, int | None]:
        current, best = None, (0, None)
        for event in self.events:
            if event[0] == "stage":
                current = event[1]
            elif current == stage and event[1] >= best[0]:
                best = (event[1], event[2])
        return best


@pytest.fixture
def fake_export(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace the o_voxel GLB export (needs CUDA + real mesh) with a recorder that writes a placeholder file."""
    calls: list[dict[str, Any]] = []

    def export(mesh, pipeline, resolution, params, out_path, reporter, cancel):  # noqa: ANN001
        cancel.raise_if_cancelled()
        calls.append({"mesh": mesh.tag, "resolution": resolution, "polygon_target": params.polygon_target,
                      "texture_size": params.texture_size, "layout": pipeline.pbr_attr_layout})
        Path(out_path).write_bytes(b"glTF-test-double")
        reporter.step(2, 2, "written")
        return {"vertices": 1, "faces": 1, "file_size_bytes": 16}

    monkeypatch.setattr(generation_module, "export_glb", export)
    return calls


def _image(tmp_path: Path, mode: str = "RGB") -> Path:
    path = tmp_path / "inputs" / "img_000000000001.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    color = (200, 30, 40) if mode == "RGB" else (200, 30, 40, 255)
    Image.new(mode, (80, 64), color).save(path)
    return path


def _request(tmp_path: Path, image_path: Path, **params: Any) -> GenerationRequest:
    image = ConditioningImage("img_000000000001", ReferenceSlot.FRONT, image_path, True)
    return GenerationRequest(job_id="job_000000000001", images=[image], all_references=[image],
                             params=GenerationParams(**params), output_dir=tmp_path / "job")


def _upstream_calls(pipeline_type: str, seed: int, overrides: dict[str, SamplerOverrides], image: Image.Image,
                    preprocess: bool, effective: int | None = None) -> list[tuple[Any, ...]]:
    upstream = RecordingPipeline(effective_cascade_resolution=effective)
    torch.manual_seed(987654)  # unrelated prior state: run() must reseed
    upstream.run(
        image,
        seed=seed,
        sparse_structure_sampler_params=overrides["sparse_structure"].as_kwargs(),
        shape_slat_sampler_params=overrides["shape"].as_kwargs(),
        tex_slat_sampler_params=overrides["texture"].as_kwargs(),
        preprocess_image=preprocess,
        pipeline_type=pipeline_type,
    )
    return upstream.calls


# --------------------------------------------------------------------------------------------------
# parity with upstream run()
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("resolution,pipeline_type", [(512, "512"), (1024, "1024_cascade"), (1536, "1536_cascade")])
@pytest.mark.parametrize("with_overrides", [False, True])
def test_staged_run_matches_upstream_run(tmp_path: Path, fake_export, resolution: int, pipeline_type: str,
                                         with_overrides: bool) -> None:
    overrides = {
        "sparse_structure": SamplerOverrides(steps=5, guidance_strength=6.0) if with_overrides else SamplerOverrides(),
        "shape": SamplerOverrides(steps=2, rescale_t=2.5) if with_overrides else SamplerOverrides(),
        "texture": SamplerOverrides(guidance_rescale=0.2) if with_overrides else SamplerOverrides(),
    }
    seed = 123456
    image_path = _image(tmp_path)
    with Image.open(image_path) as src:
        source = src.convert("RGB")
    expected = _upstream_calls(pipeline_type, seed, overrides, source, preprocess=True)

    ours = RecordingPipeline()
    reporter = RecordingReporter()
    torch.manual_seed(42)  # different prior state than upstream: only the seed placement may matter
    request = _request(tmp_path, image_path, resolution=resolution, seed=seed, remove_background=True,
                       sparse_structure=overrides["sparse_structure"], shape=overrides["shape"],
                       texture=overrides["texture"])
    output = TrellisGenerationRun(ours, request, reporter, CancellationToken()).execute()

    assert ours.calls == expected
    assert output.effective_resolution == {512: 512, 1024: 1024, 1536: 1536}[resolution]
    assert fake_export[0]["resolution"] == output.effective_resolution
    assert fake_export[0]["layout"] is ours.pbr_attr_layout
    assert output.metadata["pipeline_type"] == pipeline_type
    assert reporter.stages() == ["preprocessing", "conditioning", "sparse_structure", "shape", "texture",
                                 "decoding", "exporting"]
    shape_steps = (overrides["shape"].steps or UPSTREAM_DEFAULTS["shape"]["steps"])
    expected_shape_total = shape_steps * (1 if pipeline_type == "512" else 2)
    assert reporter.max_step("shape") == (expected_shape_total, expected_shape_total)
    ss_steps = overrides["sparse_structure"].steps or UPSTREAM_DEFAULTS["sparse_structure"]["steps"]
    assert reporter.max_step("sparse_structure") == (ss_steps, ss_steps)
    assert set(output.timings) == {"preprocessing", "conditioning", "sparse_structure", "shape", "texture",
                                   "decoding", "exporting", "total"}
    for sampler in (ours.sparse_structure_sampler, ours.shape_slat_sampler, ours.tex_slat_sampler):
        assert "sample_once" not in vars(sampler)  # hooks restored
    assert (tmp_path / "job" / "preview.png").is_file() and (tmp_path / "job" / "model.glb").is_file()


def test_untouched_image_matches_upstream_without_preprocessing(tmp_path: Path, fake_export) -> None:
    """``remove_background=false`` + opaque image == upstream ``run(preprocess_image=False)``."""
    image_path = _image(tmp_path)
    with Image.open(image_path) as src:
        source = src.convert("RGB")
    no = {k: SamplerOverrides() for k in ("sparse_structure", "shape", "texture")}
    expected = _upstream_calls("1024_cascade", 7, no, source, preprocess=False)
    ours = RecordingPipeline()
    request = _request(tmp_path, image_path, resolution=1024, seed=7, remove_background=False)
    output = TrellisGenerationRun(ours, request, RecordingReporter(), CancellationToken()).execute()
    assert ours.calls == expected
    assert output.metadata["preprocess"] == "untouched"


def test_opaque_rgba_with_background_removal_runs_preprocess(tmp_path: Path, fake_export) -> None:
    image_path = _image(tmp_path, mode="RGBA")
    ours = RecordingPipeline()
    request = _request(tmp_path, image_path, resolution=512, seed=1, remove_background=True)
    output = TrellisGenerationRun(ours, request, RecordingReporter(), CancellationToken()).execute()
    assert ours.calls[0][0] == "preprocess_image" and ours.calls[0][2] == "RGBA"
    assert output.metadata["preprocess"] == "background_removed"


def test_reduced_cascade_resolution_is_reported_and_used(tmp_path: Path, fake_export) -> None:
    """1536 cascade above the token cap: upstream lowers the resolution; decode + export must use it."""
    image_path = _image(tmp_path)
    with Image.open(image_path) as src:
        source = src.convert("RGB")
    no = {k: SamplerOverrides() for k in ("sparse_structure", "shape", "texture")}
    expected = _upstream_calls("1536_cascade", 3, no, source, preprocess=True, effective=1280)
    ours = RecordingPipeline(effective_cascade_resolution=1280)
    request = _request(tmp_path, image_path, resolution=1536, seed=3)
    output = TrellisGenerationRun(ours, request, RecordingReporter(), CancellationToken()).execute()
    assert ours.calls == expected
    assert expected[-1] == ("decode_latent", expected[-1][1], "shape_slat", "tex_slat", 1280)
    assert output.effective_resolution == 1280
    assert output.metadata["requested_resolution"] == 1536
    assert fake_export[0]["resolution"] == 1280


# --------------------------------------------------------------------------------------------------
# engine-level behaviour on the double (cancel / errors / low-VRAM cleanup)
# --------------------------------------------------------------------------------------------------


def _ready_engine(tmp_path: Path, pipeline: RecordingPipeline) -> TrellisEngine:
    settings = SimpleNamespace(
        model_dir=tmp_path / "model", aux_model_root=tmp_path / "aux", trellis_source_dir=tmp_path / "src",
        hf_home=tmp_path / "hf", hf_token=None, allow_model_downloads=False, device="cuda", low_vram=True,
        attention_backend="sdpa", runtime_dir=tmp_path / "runtime",
    )
    engine = TrellisEngine(settings)
    engine._pipeline = pipeline  # test-only injection of the double
    engine._set_status(EngineState.READY, message="test double ready")
    return engine


def _assert_all_on_cpu(pipeline: RecordingPipeline) -> None:
    for component in [*pipeline.models.values(), pipeline.image_cond_model, pipeline.rembg_model]:
        assert component.device == "cpu", f"{component.name} left on {component.device}"
    assert pipeline.models["shape_slat_decoder"].low_vram is False
    for sampler in (pipeline.sparse_structure_sampler, pipeline.shape_slat_sampler, pipeline.tex_slat_sampler):
        assert "sample_once" not in vars(sampler)


def test_cancel_inside_cascade_restores_low_vram_placement(tmp_path: Path, fake_export) -> None:
    cancel = CancellationToken()
    pipeline = RecordingPipeline(low_vram=True, cancel_in_shape=cancel)
    engine = _ready_engine(tmp_path, pipeline)
    reporter = RecordingReporter()
    with pytest.raises(JobCancelledError, match="cancel inside cascade"):
        engine.generate(_request(tmp_path, _image(tmp_path), resolution=1024, seed=5), reporter, cancel)
    _assert_all_on_cpu(pipeline)
    assert reporter.stages()[-1] == "shape"
    assert engine.status().state is EngineState.READY
    assert not (tmp_path / "job" / "model.glb").exists()


def test_cancelled_error_object_propagates_unchanged(tmp_path: Path, fake_export) -> None:
    marker = JobCancelledError("raised by the worker")
    pipeline = RecordingPipeline(low_vram=True, fail={"sample_tex_slat": marker})
    engine = _ready_engine(tmp_path, pipeline)
    with pytest.raises(JobCancelledError) as excinfo:
        engine.generate(_request(tmp_path, _image(tmp_path), resolution=512, seed=5), RecordingReporter(),
                        CancellationToken())
    assert excinfo.value is marker
    _assert_all_on_cpu(pipeline)


def test_out_of_memory_maps_to_engine_error(tmp_path: Path, fake_export) -> None:
    pipeline = RecordingPipeline(low_vram=True, fail={"shape": torch.OutOfMemoryError("CUDA out of memory.")})
    engine = _ready_engine(tmp_path, pipeline)
    with pytest.raises(EngineError) as excinfo:
        engine.generate(_request(tmp_path, _image(tmp_path), resolution=1536, seed=5), RecordingReporter(),
                        CancellationToken())
    assert excinfo.value.details["reason"] == "out_of_memory"
    assert excinfo.value.details["stage"] == "shape"
    _assert_all_on_cpu(pipeline)
    assert engine.status().state is EngineState.READY


def test_unexpected_error_maps_to_engine_error(tmp_path: Path, fake_export) -> None:
    pipeline = RecordingPipeline(low_vram=True, fail={"decode_latent": ValueError("boom")})
    engine = _ready_engine(tmp_path, pipeline)
    with pytest.raises(EngineError, match="Generation failed during decoding: ValueError: boom"):
        engine.generate(_request(tmp_path, _image(tmp_path), resolution=512, seed=5), RecordingReporter(),
                        CancellationToken())
    _assert_all_on_cpu(pipeline)


def test_cancel_before_start_runs_nothing(tmp_path: Path, fake_export) -> None:
    pipeline = RecordingPipeline()
    engine = _ready_engine(tmp_path, pipeline)
    cancel = CancellationToken()
    cancel.cancel("early")
    with pytest.raises(JobCancelledError, match="early"):
        engine.generate(_request(tmp_path, _image(tmp_path), resolution=512), RecordingReporter(), cancel)
    assert pipeline.calls == []
