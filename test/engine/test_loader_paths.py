"""``pipeline.json`` → local checkpoint path resolution (no GPU, no torch, no network)."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from inference.trellis.loader import (
    MODEL_NAMES_TO_LOAD,
    AuxModelsUnavailableError,
    ModelFilesMissingError,
    TrellisPipelineLoader,
    read_pipeline_config,
    resolve_model_entries,
    resolve_model_spec,
    verify_entries,
)

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
MODEL_DIR = BACKEND_DIR / ".model" / "TRELLIS.2-4B"
AUX_ROOT = BACKEND_DIR / ".model"
SS_SPEC = "microsoft/TRELLIS-image-large/ckpts/ss_dec_conv3d_16l8_fp16"


# --------------------------------------------------------------------------------------------------
# fixtures / helpers (test doubles live only here)
# --------------------------------------------------------------------------------------------------


def write_safetensors(path: Path) -> None:
    """Smallest valid .safetensors file (one 1-element float32 tensor) so assets' integrity check passes."""
    header = json.dumps({"w": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<Q", len(header)) + header + b"\x00\x00\x80\x3f")


def write_checkpoint(prefix: Path) -> None:
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_name(prefix.name + ".json").write_text(json.dumps({"name": "X", "args": {}}), encoding="utf-8")
    write_safetensors(prefix.with_name(prefix.name + ".safetensors"))


def write_pipeline(model_dir: Path, models: dict[str, str] | None = None, name: str = "Trellis2ImageTo3DPipeline") -> None:
    models = models if models is not None else {
        "sparse_structure_decoder": SS_SPEC,
        **{n: f"ckpts/{n}_bf16" for n in MODEL_NAMES_TO_LOAD if n != "sparse_structure_decoder"},
    }
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "pipeline.json").write_text(json.dumps({"name": name, "args": {"models": models}}), encoding="utf-8")


def make_fake_aux_root(root: Path) -> None:
    """Layout accepted by ``inference.trellis.assets.resolve_aux_models`` (tiny placeholder files)."""
    dinov3 = root / "dinov3-vitl16-pretrain-lvd1689m"
    dinov3.mkdir(parents=True)
    (dinov3 / "config.json").write_text("{}", encoding="utf-8")
    write_safetensors(dinov3 / "model.safetensors")
    rmbg = root / "RMBG-2.0"
    rmbg.mkdir(parents=True)
    (rmbg / "config.json").write_text(
        json.dumps({"auto_map": {"AutoModelForImageSegmentation": "birefnet.BiRefNet"}}), encoding="utf-8"
    )
    (rmbg / "birefnet.py").write_text("# placeholder\n", encoding="utf-8")
    write_safetensors(rmbg / "model.safetensors")
    write_checkpoint(root / "TRELLIS-image-large" / "ckpts" / "ss_dec_conv3d_16l8_fp16")


def stand_in_settings(model_dir: Path, aux_root: Path, **overrides) -> SimpleNamespace:
    values = dict(
        model_dir=model_dir,
        aux_model_root=aux_root,
        trellis_source_dir=Path("unused"),
        hf_home=aux_root / ".hf_cache",
        hf_token=None,
        allow_model_downloads=False,
        device="cuda",
        low_vram=True,
        attention_backend="auto",
        runtime_dir=aux_root / "runtime",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


# --------------------------------------------------------------------------------------------------
# the real local pipeline.json
# --------------------------------------------------------------------------------------------------


@pytest.mark.skipif(not (MODEL_DIR / "pipeline.json").is_file(), reason="TRELLIS.2-4B not present locally")
def test_real_pipeline_json_resolves_to_local_paths() -> None:
    args = read_pipeline_config(MODEL_DIR)
    aux = SimpleNamespace(ss_decoder_prefix=AUX_ROOT / "TRELLIS-image-large" / "ckpts" / "ss_dec_conv3d_16l8_fp16")
    entries = resolve_model_entries(MODEL_DIR, args["models"], aux, AUX_ROOT)

    assert sorted(e.name for e in entries) == sorted(MODEL_NAMES_TO_LOAD)
    by_name = {e.name: e for e in entries}
    ss = by_name["sparse_structure_decoder"]
    assert ss.source == "aux"
    assert ss.prefix == aux.ss_decoder_prefix.resolve()
    assert ss.spec == SS_SPEC
    for entry in entries:
        assert entry.prefix.is_absolute()
        assert entry.prefix.suffix == "" or entry.prefix.suffix.startswith("_")  # no .json/.safetensors suffix
        if entry.name != "sparse_structure_decoder":
            assert entry.source == "model_dir"
            assert entry.prefix == (MODEL_DIR / Path(*entry.spec.split("/"))).resolve()
            assert entry.prefix.is_relative_to(MODEL_DIR.resolve())
            assert not entry.missing_files(), f"local weights missing for {entry.name}"
    # every non-aux checkpoint exists locally -> no TRELLIS.2-4B download can ever be needed
    verify_entries([e for e in entries if e.source == "model_dir"], MODEL_DIR)


@pytest.mark.skipif(not (MODEL_DIR / "pipeline.json").is_file(), reason="TRELLIS.2-4B not present locally")
def test_real_pipeline_json_sampler_and_aux_configs() -> None:
    args = read_pipeline_config(MODEL_DIR)
    for key in ("sparse_structure_sampler", "shape_slat_sampler", "tex_slat_sampler"):
        assert args[key]["name"] == "FlowEulerGuidanceIntervalSampler"
        assert args[key]["params"]["steps"] == 12
    assert args["image_cond_model"]["name"] == "DinoV3FeatureExtractor"
    assert args["rembg_model"]["name"] == "BiRefNet"
    assert args["default_pipeline_type"] == "1024_cascade"


# --------------------------------------------------------------------------------------------------
# resolution rules
# --------------------------------------------------------------------------------------------------


def test_resolve_spec_rules(tmp_path: Path) -> None:
    model_dir = tmp_path / "TRELLIS.2-4B"
    aux_root = tmp_path / "aux"
    aux = SimpleNamespace(ss_decoder_prefix=aux_root / "TRELLIS-image-large" / "ckpts" / "ss_dec_conv3d_16l8_fp16")

    prefix, source = resolve_model_spec("ckpts/foo_bf16", model_dir, aux, aux_root)
    assert (prefix, source) == ((model_dir / "ckpts" / "foo_bf16").resolve(), "model_dir")

    prefix, source = resolve_model_spec("ckpts\\foo_bf16", model_dir, aux, aux_root)  # windows separators
    assert prefix == (model_dir / "ckpts" / "foo_bf16").resolve()

    prefix, source = resolve_model_spec(SS_SPEC, model_dir, aux, aux_root)
    assert (prefix, source) == (aux.ss_decoder_prefix.resolve(), "aux")

    # other external repos map to the <aux_root>/<repo>/<path> mirror layout
    prefix, source = resolve_model_spec("someorg/other-repo/ckpts/thing", model_dir, aux, aux_root)
    assert (prefix, source) == ((aux_root / "other-repo" / "ckpts" / "thing").resolve(), "aux")

    # upstream tries <model_dir>/<spec> first: a local copy wins over the aux mapping
    write_checkpoint(model_dir / "microsoft" / "TRELLIS-image-large" / "ckpts" / "ss_dec_conv3d_16l8_fp16")
    prefix, source = resolve_model_spec(SS_SPEC, model_dir, aux, aux_root)
    assert source == "model_dir"
    assert prefix.is_relative_to(model_dir.resolve())


@pytest.mark.parametrize("bad", ["", "../escape/x", "ckpts/../../x", "/"])
def test_resolve_spec_rejects_invalid(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ModelFilesMissingError):
        resolve_model_spec(bad, tmp_path, None, tmp_path)


def test_ss_decoder_without_aux_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(AuxModelsUnavailableError):
        resolve_model_spec(SS_SPEC, tmp_path, None, tmp_path)


def test_entries_follow_pipeline_order_and_filter(tmp_path: Path) -> None:
    models = {"extra_unused_model": "ckpts/extra", **{n: f"ckpts/{n}" for n in reversed(MODEL_NAMES_TO_LOAD)}}
    aux = SimpleNamespace(ss_decoder_prefix=tmp_path / "ss")
    entries = resolve_model_entries(tmp_path, models, aux, tmp_path)
    assert [e.name for e in entries] == list(reversed(MODEL_NAMES_TO_LOAD))


def test_missing_entry_in_pipeline_json(tmp_path: Path) -> None:
    models = {n: f"ckpts/{n}" for n in MODEL_NAMES_TO_LOAD if n != "tex_slat_decoder"}
    with pytest.raises(ModelFilesMissingError, match="tex_slat_decoder"):
        resolve_model_entries(tmp_path, models, SimpleNamespace(ss_decoder_prefix=tmp_path / "ss"), tmp_path)


def test_read_pipeline_config_errors(tmp_path: Path) -> None:
    with pytest.raises(ModelFilesMissingError, match="never downloaded"):
        read_pipeline_config(tmp_path / "missing")
    write_pipeline(tmp_path / "wrong", name="SomethingElse")
    with pytest.raises(ModelFilesMissingError, match="expected"):
        read_pipeline_config(tmp_path / "wrong")
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "pipeline.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ModelFilesMissingError):
        read_pipeline_config(tmp_path / "broken")


def test_verify_reports_missing_main_and_aux_files(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    aux_root = tmp_path / "aux"
    write_pipeline(model_dir)
    aux = SimpleNamespace(ss_decoder_prefix=aux_root / "TRELLIS-image-large" / "ckpts" / "ss_dec_conv3d_16l8_fp16")
    entries = resolve_model_entries(model_dir, read_pipeline_config(model_dir)["models"], aux, aux_root)

    with pytest.raises(ModelFilesMissingError) as main_missing:
        verify_entries(entries, model_dir)
    assert len(main_missing.value.missing) == 2 * (len(MODEL_NAMES_TO_LOAD) - 1)

    for entry in entries:
        if entry.source == "model_dir":
            write_checkpoint(entry.prefix)
    with pytest.raises(AuxModelsUnavailableError) as aux_missing:
        verify_entries(entries, model_dir)
    assert any("ss_dec_conv3d_16l8_fp16" in m for m in aux_missing.value.missing)

    write_checkpoint(aux.ss_decoder_prefix)
    verify_entries(entries, model_dir)  # complete


# --------------------------------------------------------------------------------------------------
# loader.plan() end-to-end with assets.resolve_aux_models (local only)
# --------------------------------------------------------------------------------------------------


def test_loader_plan_with_local_aux(tmp_path: Path) -> None:
    model_dir = tmp_path / "TRELLIS.2-4B"
    aux_root = tmp_path / "aux"
    write_pipeline(model_dir)
    for name in MODEL_NAMES_TO_LOAD:
        if name != "sparse_structure_decoder":
            write_checkpoint(model_dir / "ckpts" / f"{name}_bf16")
    make_fake_aux_root(aux_root)

    progress: list[tuple[object, str]] = []
    loader = TrellisPipelineLoader(stand_in_settings(model_dir, aux_root), on_progress=lambda p, m: progress.append((p, m)))
    plan = loader.plan()

    assert plan.total_components == len(MODEL_NAMES_TO_LOAD) + 2
    assert plan.aux.rembg_kind == "rmbg2"
    assert plan.aux.dinov3_dir == aux_root / "dinov3-vitl16-pretrain-lvd1689m"
    ss = next(e for e in plan.entries if e.name == "sparse_structure_decoder")
    assert ss.prefix == (aux_root / "TRELLIS-image-large" / "ckpts" / "ss_dec_conv3d_16l8_fp16").resolve()
    assert not progress  # no download happened


@pytest.mark.parametrize(
    "allow,token,expected",
    [(False, "hf_x", "IMG3D_ALLOW_MODEL_DOWNLOADS is false"), (True, None, "HF_TOKEN is not set")],
)
def test_loader_plan_without_aux_and_no_download(tmp_path: Path, allow: bool, token: str | None, expected: str) -> None:
    model_dir = tmp_path / "TRELLIS.2-4B"
    write_pipeline(model_dir)
    settings = stand_in_settings(model_dir, tmp_path / "empty_aux", allow_model_downloads=allow, hf_token=token)
    with pytest.raises(AuxModelsUnavailableError) as info:
        TrellisPipelineLoader(settings).plan()
    message = str(info.value)
    assert expected in message
    assert "facebook/dinov3-vitl16-pretrain-lvd1689m" in message  # actionable: names the repo
    assert "hf_x" not in message  # never leaks the token


@pytest.mark.skipif(not (MODEL_DIR / "pipeline.json").is_file(), reason="TRELLIS.2-4B not present locally")
def test_loader_plan_on_this_machine() -> None:
    """Full local plan if the aux models have been installed (skipped otherwise)."""
    from inference.trellis.assets import AuxModelMissingError, resolve_aux_models

    try:
        resolve_aux_models(AUX_ROOT)
    except AuxModelMissingError as exc:
        pytest.skip(f"aux models not installed yet: {exc.missing}")
    plan = TrellisPipelineLoader(stand_in_settings(MODEL_DIR, AUX_ROOT)).plan()
    assert len(plan.entries) == len(MODEL_NAMES_TO_LOAD)
    for entry in plan.entries:
        assert not entry.missing_files()
