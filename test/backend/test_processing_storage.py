"""Processing (validation, normalization, mesh stats), storage helpers and the engine registry."""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

import pytest
from fakes import write_test_glb
from helpers import image_bytes, png_bytes
from PIL import Image

from core.exceptions import (
    EngineUnavailableError,
    PayloadTooLargeError,
    StorageError,
    UnsupportedMediaTypeError,
    ValidationAppError,
)
from inference.base import CancellationToken, NullProgressReporter
from inference.registry import create_engine
from inference.unavailable import UnavailableEngine
from models.enums import EngineState, ReferenceMode, ReferenceSlot
from processing.image_ops import make_thumbnail_webp, normalize_to_png
from processing.image_validation import validate_image_bytes
from processing.mesh_stats import compute_glb_stats, read_glb_json
from storage.json_io import read_json, write_json_atomic


def test_validate_image_bytes_reports_facts():
    info = validate_image_bytes(png_bytes((300, 200), mode="RGBA"), min_side=64, max_pixels=10**7)
    assert (info.format, info.mime_type, info.extension) == ("PNG", "image/png", ".png")
    assert (info.width, info.height, info.has_alpha) == (300, 200, True)
    info = validate_image_bytes(image_bytes("JPEG", (300, 200), exif_orientation=8), min_side=64, max_pixels=10**7)
    assert (info.width, info.height) == (200, 300)


def test_validate_image_bytes_errors():
    with pytest.raises(UnsupportedMediaTypeError):
        validate_image_bytes(b"\x89PNG\r\n\x1a\n garbage", min_side=1, max_pixels=10**7)
    with pytest.raises(UnsupportedMediaTypeError):
        validate_image_bytes(image_bytes("BMP", (100, 100)), min_side=1, max_pixels=10**7)
    with pytest.raises(PayloadTooLargeError):
        validate_image_bytes(png_bytes((2000, 1000)), min_side=1, max_pixels=1_000_000)
    with pytest.raises(ValidationAppError):
        validate_image_bytes(png_bytes((64, 10)), min_side=64, max_pixels=10**7)


def test_normalize_and_thumbnail(tmp_runtime: Path):
    destination = tmp_runtime / "out.png"
    result = normalize_to_png(image_bytes("WEBP", (500, 250)), destination)
    assert (result.width, result.height, result.has_alpha) == (500, 250, False)
    with Image.open(destination) as img:
        assert img.format == "PNG" and img.mode == "RGB"
    thumb = Image.open(BytesIO(make_thumbnail_webp(png_bytes((1000, 2000)))))
    assert thumb.size == (192, 384)


def test_mesh_stats_on_real_glb(tmp_runtime: Path):
    path = write_test_glb(tmp_runtime / "cube.glb", texture_px=32)
    gltf = read_glb_json(path)
    assert gltf["asset"]["version"] == "2.0"
    stats = compute_glb_stats(path, fallback_texture_size=2048)
    assert (stats.vertices, stats.faces, stats.materials, stats.textures) == (8, 12, 1, 1)
    assert stats.has_uv is True
    assert stats.texture_size == 32
    assert stats.dimensions == [1.0, 1.0, 1.0]
    assert stats.file_size_bytes == path.stat().st_size


def test_mesh_stats_rejects_non_glb(tmp_runtime: Path):
    path = tmp_runtime / "x.glb"
    path.write_bytes(b"not a glb file at all")
    with pytest.raises(ValueError):
        compute_glb_stats(path)


def test_atomic_json_roundtrip(tmp_runtime: Path):
    path = tmp_runtime / "nested" / "data.json"
    write_json_atomic(path, {"a": 1, "text": "é"})
    assert read_json(path) == {"a": 1, "text": "é"}
    write_json_atomic(path, {"a": 2})
    assert read_json(path) == {"a": 2}
    assert [p.name for p in path.parent.iterdir()] == ["data.json"]  # no temp files left
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(StorageError):
        read_json(path)


def test_registry_falls_back_to_unavailable_engine(monkeypatch):
    from config.settings import Settings

    monkeypatch.setitem(sys.modules, "inference.trellis.engine", None)  # simulate a broken import
    engine = create_engine(Settings(_env_file=None))
    assert isinstance(engine, UnavailableEngine)
    status = engine.status()
    assert status.state is EngineState.UNAVAILABLE
    assert "inference.trellis.engine" in (status.error or "")  # ModuleNotFoundError (an ImportError)
    engine.load()  # never raises
    assert engine.status().state is EngineState.UNAVAILABLE
    with pytest.raises(EngineUnavailableError):
        engine.generate(None, NullProgressReporter(), CancellationToken())  # type: ignore[arg-type]

    unknown = create_engine(Settings(_env_file=None, engine="other"))
    assert isinstance(unknown, UnavailableEngine)
    assert "Unknown engine" in unknown.reason


def test_reference_strategies():
    from inference.base import ConditioningImage, EngineCapabilities
    from pipelines.reference_strategy import MultiViewStrategy, PrimaryOnlyStrategy, get_strategy

    refs = [
        ConditioningImage("img_b", ReferenceSlot.BACK, Path("b.png")),
        ConditioningImage("img_f", ReferenceSlot.FRONT, Path("f.png"), is_primary=True),
        ConditioningImage("img_l", ReferenceSlot.LEFT, Path("l.png")),
    ]
    caps = EngineCapabilities()
    assert [r.image_id for r in PrimaryOnlyStrategy().select(refs, caps)] == ["img_f"]
    with pytest.raises(ValidationAppError, match="multi_view reference mode is not supported"):
        MultiViewStrategy().validate(caps)
    multi_caps = EngineCapabilities(
        max_reference_images=2, reference_modes=(ReferenceMode.PRIMARY_ONLY, ReferenceMode.MULTI_VIEW)
    )
    assert [r.image_id for r in MultiViewStrategy().select(refs, multi_caps)] == ["img_f", "img_l"]
    assert isinstance(get_strategy("primary_only"), PrimaryOnlyStrategy)
    with pytest.raises(ValidationAppError):
        get_strategy("bogus")


def test_app_uses_only_embedded_packages():
    import fastapi
    import pydantic
    import starlette

    embedded = (Path(__file__).resolve().parents[2] / "backend" / "python").resolve()
    for module in (fastapi, pydantic, starlette):
        assert embedded in Path(module.__file__).resolve().parents, module.__file__


def test_import_main_has_no_side_effects():
    import importlib

    main = importlib.import_module("main")
    assert callable(main.create_app) and callable(main.main)
