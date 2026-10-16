"""Pure-Python tests of the bpy-free Auto Mesh Tuner modules (img3d_amt.config / protocol), no Blender needed."""

from __future__ import annotations

import io
import json
import logging
import math
import sys
from pathlib import Path

import pytest

from amt_testlib import SCRIPTS_DIR

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from img3d_amt import MIN_BLENDER_VERSION, PROTOCOL_VERSION, __version__, protocol  # noqa: E402
from img3d_amt.config import (MAX_PATH_CHARS, ConfigError, TuningOptions, load_config)  # noqa: E402


# ------------------------------------------------------------------------------------------------ options


def test_package_constants() -> None:
    assert PROTOCOL_VERSION == 1
    assert MIN_BLENDER_VERSION == (5, 1, 0)
    assert __version__


def test_balanced_defaults() -> None:
    opts = TuningOptions.from_dict({})
    assert opts.to_dict() == TuningOptions().to_dict()
    assert opts.preset == "balanced" and opts.smoothing == 0.5 and opts.max_hole_edges == 300
    assert opts.symmetry == "auto" and opts.subdivision == "auto" and opts.save_blend is False


def test_presets_and_explicit_fields_win() -> None:
    cons = TuningOptions.from_dict({"preset": "conservative"})
    assert (cons.smoothing, cons.refine_density, cons.max_hole_edges, cons.symmetry) == (0.25, False, 120, "auto")
    strong = TuningOptions.from_dict({"preset": "STRONG"})
    assert (strong.smoothing, strong.max_hole_edges, strong.island_min_ratio) == (0.8, 1000, 0.003)
    mixed = TuningOptions.from_dict({"preset": "strong", "smoothing": 0.1, "max_hole_edges": None})
    assert mixed.smoothing == 0.1 and mixed.max_hole_edges == 1000  # null keeps the preset value


def test_clamping_and_unknown_keys_warn() -> None:
    warnings: list[str] = []
    opts = TuningOptions.from_dict({"max_hole_edges": 2, "smoothing": 1.5, "island_min_ratio": -1,
                                    "frobnicate": True}, warnings=warnings)
    assert (opts.max_hole_edges, opts.smoothing, opts.island_min_ratio) == (8, 1.0, 0.0)
    assert any("frobnicate" in w for w in warnings)
    assert sum("clamped" in w for w in warnings) == 3


def test_lenient_scalars() -> None:
    opts = TuningOptions.from_dict({"fill_holes": "false", "quads": 1, "max_hole_edges": "64", "smoothing": "0.3",
                                    "symmetry": " Repair "})
    assert opts.fill_holes is False and opts.quads is True and opts.max_hole_edges == 64
    assert opts.smoothing == 0.3 and opts.symmetry == "repair"


@pytest.mark.parametrize("options", [
    {"smoothing": "abc"}, {"smoothing": True}, {"smoothing": float("nan")}, {"fill_holes": "maybe"},
    {"fill_holes": 2}, {"symmetry": "mirror"}, {"subdivision": 1}, {"preset": "ultra"}, {"max_hole_edges": 10.5},
    {"remove_islands": [True]},
])
def test_invalid_options_raise(options: dict) -> None:
    with pytest.raises(ConfigError) as exc:
        TuningOptions.from_dict(options)
    assert exc.value.code == "invalid_options" and exc.value.exit_code == 2


def test_options_must_be_object() -> None:
    with pytest.raises(ConfigError):
        TuningOptions.from_dict([1, 2])  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------------ config


def _write(path: Path, data) -> Path:
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def test_cli_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = load_config(["--input", "in.glb", "--output-dir", "out", "--options", '{"preset": "conservative"}',
                       "--log-level", "debug"])
    assert cfg.input_path == str(tmp_path / "in.glb") and cfg.output_dir == str(tmp_path / "out")
    assert cfg.options.preset == "conservative" and cfg.log_level == "debug"
    assert cfg.model_path.endswith("tuned.glb") and cfg.report_path.endswith("report.json")
    assert cfg.blend_path is None and cfg.input_format == "glb"


@pytest.mark.parametrize("argv, code", [
    ([], "bad_arguments"),
    (["--input", "a.glb"], "bad_arguments"),
    (["--config", "c.json", "--input", "a.glb"], "bad_arguments"),
    (["--input", "a.glb", "--output-dir", "o", "--options", "{bad"], "invalid_options"),
    (["--input", "a.glb", "--output-dir", "o", "--what"], "bad_arguments"),
    (["--config", "does_not_exist.json"], "config_not_found"),
])
def test_cli_errors(argv: list[str], code: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError) as exc:
        load_config(argv)
    assert exc.value.code == code


def test_config_file_resolution_and_outputs(tmp_path: Path) -> None:
    cfg_path = _write(tmp_path / "cfg.json", {
        "schema": 1, "input": "input.glb", "output_dir": "out", "extra_top": 1,
        "options": {"save_blend": True, "weird": 1},
        "outputs": {"model": "m.glb", "report": "r.json", "blend": None, "previews_dir": "pv", "zzz": 1},
        "context": {"job_id": "job_x", "tuning_id": "tn_y", "source": "trellis2"}})
    cfg = load_config(["--config", str(cfg_path)])
    assert cfg.input_path == str(tmp_path / "input.glb")
    assert cfg.model_path == str(tmp_path / "out" / "m.glb") and cfg.report_path == str(tmp_path / "out" / "r.json")
    assert cfg.blend_path == str(tmp_path / "out" / "tuned.blend")  # save_blend + default name
    assert cfg.previews_path == str(tmp_path / "out" / "pv")
    assert cfg.context["job_id"] == "job_x" and cfg.config_path == str(tmp_path / "cfg.json")
    assert {w.split("'")[1] for w in cfg.warnings} == {"extra_top", "weird", "zzz"}


@pytest.mark.parametrize("data, code", [
    ("{not json", "bad_config"),
    ([1, 2], "bad_config"),
    ({"schema": 2, "input": "a.glb", "output_dir": "o"}, "unsupported_schema"),
    ({"schema": 1, "output_dir": "o"}, "bad_config"),
    ({"schema": 1, "input": "a.glb", "output_dir": "o", "outputs": {"model": "../x.glb"}}, "bad_config"),
    ({"schema": 1, "input": "a.glb", "output_dir": "o", "outputs": {"model": "x.obj"}}, "bad_config"),
    ({"schema": 1, "input": "a.glb", "output_dir": "o", "options": {"smoothing": "x"}}, "invalid_options"),
    ({"schema": 1, "input": "a.glb", "output_dir": "o", "log_level": "loud"}, "bad_config"),
    ({"schema": 1, "input": "a.glb", "output_dir": "o" * (MAX_PATH_CHARS + 5)}, "path_too_long"),
])
def test_config_file_errors(data, code: str, tmp_path: Path) -> None:
    cfg_path = _write(tmp_path / "cfg.json", data)
    with pytest.raises(ConfigError) as exc:
        load_config(["--config", str(cfg_path)])
    assert exc.value.code == code


# ------------------------------------------------------------------------------------------------ protocol


class _NumpyLike:
    def __init__(self, value):
        self.value = value

    def tolist(self):
        return self.value


def test_protocol_line_format() -> None:
    buf = io.StringIO()
    protocol.set_stream(buf)
    try:
        protocol.emit("log", level="info", message="héllo", values=_NumpyLike([1, 2.5]), bad=math.nan,
                      nested={"t": (1, 2), "inf": math.inf})
        protocol.step("load", "done", message="ok", duration_s=1.23456, progress=1.5, details={"n": 3})
        protocol.progress("load", 0.5, 0.025, "half")
        protocol.error("boom", "failed", step="load")
        protocol.emit("weird", obj=object())  # stringified, never raises
    finally:
        protocol.set_stream(None)
    lines = buf.getvalue().splitlines()
    assert all(line.startswith(protocol.SENTINEL) and line.isascii() for line in lines)
    first = json.loads(lines[0][len(protocol.SENTINEL):])
    assert first == {"event": "log", "level": "info", "message": "héllo", "values": [1, 2.5], "bad": None,
                     "nested": {"t": [1, 2], "inf": None}}
    assert ", " not in lines[0] and ": " not in lines[0]  # compact separators
    step = json.loads(lines[1][len(protocol.SENTINEL):])
    assert step["progress"] == 1.0 and step["duration_s"] == 1.235 and step["details"] == {"n": 3}
    err = json.loads(lines[3][len(protocol.SENTINEL):])
    assert err == {"event": "error", "code": "boom", "message": "failed", "step": "load", "traceback": None}
    with pytest.raises(ValueError):
        protocol.step("load", "exploded")


def test_protocol_logging_bridge() -> None:
    buf = io.StringIO()
    protocol.set_stream(buf)
    try:
        protocol.install_logging("info")
        logger = logging.getLogger("img3d_amt.some_step")
        logger.debug("hidden")
        logger.warning("careful %d", 3)
        try:
            raise RuntimeError("bad")
        except RuntimeError:
            logger.exception("step crashed")
    finally:
        protocol.set_stream(None)
    events = [json.loads(line[len(protocol.SENTINEL):]) for line in buf.getvalue().splitlines()]
    assert [e["level"] for e in events] == ["warning", "error"]
    assert events[0]["message"] == "careful 3" and "RuntimeError: bad" in events[1]["message"]
