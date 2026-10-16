"""Run configuration: ``TunerConfig`` (cfg.json or CLI flags) and ``TuningOptions`` (DESIGN sections 3 and 4).

Rules
* ``cfg.json``: ``{"schema": 1, "input", "output_dir", "options", "outputs", "context"}``; relative paths are
  resolved against the config file's folder. Optional extra top-level key ``log_level`` (debug|info|warning|error).
* CLI: ``--config <cfg.json>`` OR ``--input <file> --output-dir <dir> [--options <json>] [--log-level <lvl>]``.
* Options: preset defaults first, explicit (non-null) fields win. Unknown keys are ignored with a warning,
  out-of-range numbers are clamped with a warning, wrong types / unknown enum values raise ``ConfigError``
  (-> exit code 2).

No bpy import here (unit-testable outside Blender).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
from typing import Any

log = logging.getLogger(__name__)

CONFIG_SCHEMA = 1
MAX_PATH_CHARS = 259
"""Blender executables are not longPathAware (DESIGN section 0): every path we touch must stay <= 259 chars."""
LONGEST_PREVIEW_NAME = "before_three_quarter.png"
LOG_LEVEL_CHOICES = ("debug", "info", "warning", "error")

PRESETS: dict[str, dict[str, Any]] = {
    "conservative": {"smoothing": 0.25, "refine_density": False, "max_hole_edges": 120},
    "balanced": {},
    "strong": {"smoothing": 0.8, "max_hole_edges": 1000, "island_min_ratio": 0.003},
}
SYMMETRY_MODES = ("auto", "off", "repair", "symmetrize")
SUBDIVISION_MODES = ("auto", "off", "modifier")

# name -> (kind, arg): kind in bool | int | float | enum; arg = (lo, hi) for numbers, choices for enums
_OPTION_SPECS: dict[str, tuple[str, Any]] = {
    "remove_inner_shell": ("bool", None),
    "fill_holes": ("bool", None),
    "max_hole_edges": ("int", (8, 5000)),
    "remove_islands": ("bool", None),
    "island_min_ratio": ("float", (0.0, 0.05)),
    "smoothing": ("float", (0.0, 1.0)),
    "refine_face": ("bool", None),
    "eye_correction": ("bool", None),
    "symmetry": ("enum", SYMMETRY_MODES),
    "refine_density": ("bool", None),
    "quads": ("bool", None),
    "subdivision": ("enum", SUBDIVISION_MODES),
    "optimize_rig": ("bool", None),
    "render_previews": ("bool", None),
    "save_blend": ("bool", None),
}
_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})


class ConfigError(Exception):
    """Invalid arguments / configuration (exit code 2)."""

    exit_code = 2

    def __init__(self, message: str, code: str = "bad_config") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ----------------------------------------------------------------------------------------------------------------
# TuningOptions
# ----------------------------------------------------------------------------------------------------------------


@dataclass
class TuningOptions:
    """Effective tuning options (DESIGN section 4). Build with :meth:`from_dict`; read the fields directly."""

    preset: str = "balanced"
    remove_inner_shell: bool = True
    fill_holes: bool = True
    max_hole_edges: int = 300
    remove_islands: bool = True
    island_min_ratio: float = 0.001
    smoothing: float = 0.5
    refine_face: bool = True
    eye_correction: bool = True
    symmetry: str = "auto"
    refine_density: bool = True
    quads: bool = False
    subdivision: str = "auto"
    optimize_rig: bool = True
    render_previews: bool = True
    save_blend: bool = False

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None, *, warnings: list[str] | None = None) -> TuningOptions:
        """Parse API/cfg options: preset defaults, then explicit non-null fields; validate, clamp, warn.

        Raises:
            ConfigError: ``data`` is not an object, unknown preset/enum value, or a value of the wrong type.
        """
        sink = warnings if warnings is not None else []
        if data is None:
            data = {}
        if not isinstance(data, Mapping):
            raise ConfigError(f"'options' must be a JSON object, got {type(data).__name__}", "invalid_options")
        preset_raw = data.get("preset")
        preset = "balanced" if preset_raw is None else _coerce_enum("preset", preset_raw, tuple(PRESETS))
        values: dict[str, Any] = {f.name: f.default for f in fields(cls)}
        values["preset"] = preset
        values.update(PRESETS[preset])
        for key, raw in data.items():
            if key == "preset":
                continue
            spec = _OPTION_SPECS.get(key)
            if spec is None:
                sink.append(f"Unknown option '{key}' ignored")
                continue
            if raw is None:  # null == "not provided": keep the preset/default value
                continue
            kind, arg = spec
            if kind == "bool":
                values[key] = _coerce_bool(key, raw)
            elif kind == "enum":
                values[key] = _coerce_enum(key, raw, arg)
            else:
                number = _coerce_int(key, raw) if kind == "int" else _coerce_float(key, raw)
                lo, hi = arg
                if number < lo or number > hi:
                    clamped = min(max(number, lo), hi)
                    sink.append(f"Option '{key}'={number} is outside [{lo}, {hi}]; clamped to {clamped}")
                    number = clamped
                values[key] = number
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict of the effective options."""
        return asdict(self)


def _coerce_bool(key: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in _TRUE | _FALSE:
        return value.strip().lower() in _TRUE
    raise ConfigError(f"Option '{key}' must be a boolean (got {value!r})", "invalid_options")


def _coerce_float(key: str, value: Any) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"Option '{key}' must be a number (got {value!r})", "invalid_options")
    try:
        number = float(value.strip()) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"Option '{key}' must be a number (got {value!r})", "invalid_options") from None
    if not math.isfinite(number):
        raise ConfigError(f"Option '{key}' must be a finite number (got {value!r})", "invalid_options")
    return number


def _coerce_int(key: str, value: Any) -> int:
    number = _coerce_float(key, value)
    if number != int(number):
        raise ConfigError(f"Option '{key}' must be an integer (got {value!r})", "invalid_options")
    return int(number)


def _coerce_enum(key: str, value: Any, choices: tuple[str, ...]) -> str:
    if isinstance(value, str) and value.strip().lower() in choices:
        return value.strip().lower()
    raise ConfigError(f"Option '{key}' must be one of {', '.join(choices)} (got {value!r})", "invalid_options")


# ----------------------------------------------------------------------------------------------------------------
# TunerConfig
# ----------------------------------------------------------------------------------------------------------------


@dataclass
class OutputSpec:
    """Output file names, all relative to ``TunerConfig.output_dir`` (plain names, no folders)."""

    model: str = "tuned.glb"
    report: str = "report.json"
    blend: str | None = None
    """``.blend`` name; the file is written only when ``options.save_blend`` (default name ``tuned.blend``)."""
    previews_dir: str = "previews"


@dataclass
class TunerConfig:
    """Everything one run needs. Paths are absolute and normalized."""

    input_path: str
    output_dir: str
    options: TuningOptions
    outputs: OutputSpec = field(default_factory=OutputSpec)
    context: dict[str, Any] = field(default_factory=dict)
    schema: int = CONFIG_SCHEMA
    log_level: str = "info"
    config_path: str | None = None
    warnings: list[str] = field(default_factory=list)
    """Non-fatal configuration warnings (unknown keys, clamped values) - reported as run warnings."""

    @property
    def model_path(self) -> str:
        return os.path.join(self.output_dir, self.outputs.model)

    @property
    def report_path(self) -> str:
        return os.path.join(self.output_dir, self.outputs.report)

    @property
    def blend_path(self) -> str | None:
        if not self.options.save_blend:
            return None
        return os.path.join(self.output_dir, self.outputs.blend or "tuned.blend")

    @property
    def previews_path(self) -> str:
        return os.path.join(self.output_dir, self.outputs.previews_dir)

    @property
    def input_format(self) -> str:
        """Lower-case extension without dot (``glb``, ``obj`` ...)."""
        return os.path.splitext(self.input_path)[1].lower().lstrip(".")

    def to_dict(self) -> dict[str, Any]:
        return {"schema": self.schema, "input": self.input_path, "output_dir": self.output_dir,
                "options": self.options.to_dict(), "outputs": asdict(self.outputs), "context": dict(self.context)}


class _ArgParser(argparse.ArgumentParser):
    """argparse that raises ``ConfigError`` instead of printing usage and calling ``sys.exit(2)``."""

    def error(self, message: str) -> None:  # type: ignore[override]
        raise ConfigError(f"Invalid arguments: {message}", "bad_arguments")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = _ArgParser(prog="auto_mesh_tuner.py", add_help=False,
                        description="Img3D Auto Mesh Tuner (runs inside Blender >= 5.1).")
    parser.add_argument("--config", help="path to cfg.json")
    parser.add_argument("--input", help="input mesh (glb/gltf/obj/fbx/ply/stl/blend)")
    parser.add_argument("--output-dir", dest="output_dir", help="output folder")
    parser.add_argument("--options", help="TuningOptions as a JSON object string")
    parser.add_argument("--log-level", dest="log_level", choices=LOG_LEVEL_CHOICES, default=None)
    return parser


def load_config(argv: list[str]) -> TunerConfig:
    """Parse the script arguments (everything after Blender's ``--``) into a validated ``TunerConfig``.

    Raises:
        ConfigError: bad arguments, unreadable/invalid cfg.json, invalid options or output names, paths too long.
    """
    args = build_arg_parser().parse_args(argv)
    if args.config and (args.input or args.output_dir or args.options):
        raise ConfigError("Use either --config or --input/--output-dir/--options, not both", "bad_arguments")
    if args.config:
        cfg = _load_config_file(args.config)
    elif args.input or args.output_dir:
        if not args.input or not args.output_dir:
            raise ConfigError("--input and --output-dir are both required without --config", "bad_arguments")
        options_raw: Any = None
        if args.options:
            try:
                options_raw = json.loads(args.options)
            except json.JSONDecodeError as exc:
                raise ConfigError(f"--options is not valid JSON: {exc}", "invalid_options") from None
        cfg = _build_config({"schema": CONFIG_SCHEMA, "input": args.input, "output_dir": args.output_dir,
                             "options": options_raw}, base_dir=os.getcwd(), config_path=None)
    else:
        raise ConfigError("Missing arguments: pass --config <cfg.json> or --input <file> --output-dir <dir>",
                          "bad_arguments")
    if args.log_level:
        cfg.log_level = args.log_level
    return cfg


def _load_config_file(path: str) -> TunerConfig:
    path = os.path.abspath(path)
    if len(path) > MAX_PATH_CHARS:
        raise ConfigError(f"Config path is longer than {MAX_PATH_CHARS} characters: {path}", "path_too_long")
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}", "config_not_found") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Config file unreadable: {path}: {exc}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config file is not valid JSON: {path}: {exc}") from None
    return _build_config(data, base_dir=os.path.dirname(path), config_path=path)


_TOP_LEVEL_KEYS = frozenset({"schema", "input", "output_dir", "options", "outputs", "context", "log_level"})


def _build_config(data: Any, *, base_dir: str, config_path: str | None) -> TunerConfig:
    if not isinstance(data, Mapping):
        raise ConfigError("Config must be a JSON object")
    warnings: list[str] = []
    for key in data:
        if key not in _TOP_LEVEL_KEYS:
            warnings.append(f"Unknown config key '{key}' ignored")
    schema = data.get("schema", CONFIG_SCHEMA)
    if isinstance(schema, bool) or schema != CONFIG_SCHEMA:
        raise ConfigError(f"Unsupported config schema {schema!r} (expected {CONFIG_SCHEMA})", "unsupported_schema")
    input_path = _require_path(data, "input", base_dir)
    output_dir = _require_path(data, "output_dir", base_dir)
    options = TuningOptions.from_dict(data.get("options"), warnings=warnings)
    outputs = _parse_outputs(data.get("outputs"), warnings)
    context = data.get("context") or {}
    if not isinstance(context, Mapping):
        raise ConfigError("'context' must be a JSON object")
    log_level = data.get("log_level") or "info"
    if log_level not in LOG_LEVEL_CHOICES:
        raise ConfigError(f"'log_level' must be one of {', '.join(LOG_LEVEL_CHOICES)}")
    cfg = TunerConfig(input_path=input_path, output_dir=output_dir, options=options, outputs=outputs,
                      context=dict(context), schema=CONFIG_SCHEMA, log_level=log_level,
                      config_path=config_path, warnings=warnings)
    _check_path_lengths(cfg)
    return cfg


def _require_path(data: Mapping[str, Any], key: str, base_dir: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"'{key}' must be a non-empty path string")
    value = os.path.expanduser(value.strip())
    if not os.path.isabs(value):
        value = os.path.join(base_dir, value)
    return os.path.normpath(os.path.abspath(value))


_OUTPUT_SUFFIX = {"model": ".glb", "report": ".json", "blend": ".blend", "previews_dir": ""}


def _parse_outputs(data: Any, warnings: list[str]) -> OutputSpec:
    spec = OutputSpec()
    if data is None:
        return spec
    if not isinstance(data, Mapping):
        raise ConfigError("'outputs' must be a JSON object")
    for key, value in data.items():
        if key not in _OUTPUT_SUFFIX:
            warnings.append(f"Unknown outputs key '{key}' ignored")
            continue
        if value is None:
            if key == "blend":
                spec.blend = None
            continue  # keep defaults for the others
        if not isinstance(value, str) or not _is_plain_name(value):
            raise ConfigError(f"outputs.{key} must be a plain file name without folders (got {value!r})")
        suffix = _OUTPUT_SUFFIX[key]
        if suffix and not value.lower().endswith(suffix):
            raise ConfigError(f"outputs.{key} must end with {suffix} (got {value!r})")
        setattr(spec, key, value)
    return spec


def _is_plain_name(name: str) -> bool:
    if not name or name in (".", "..") or name != name.strip():
        return False
    if any(ch in name for ch in '\\/:*?"<>|\0'):
        return False
    return os.path.basename(name) == name


def _check_path_lengths(cfg: TunerConfig) -> None:
    candidates = {"input": cfg.input_path, "output model": cfg.model_path, "report": cfg.report_path,
                  "previews": os.path.join(cfg.previews_path, LONGEST_PREVIEW_NAME)}
    if cfg.blend_path:
        candidates["blend"] = cfg.blend_path
    for label, path in candidates.items():
        if len(path) > MAX_PATH_CHARS:
            raise ConfigError(f"The {label} path is longer than {MAX_PATH_CHARS} characters "
                              f"({len(path)}): {path}", "path_too_long")
