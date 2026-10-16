"""Offline loader for the TRELLIS.2-4B image-to-3D pipeline.

Replicates ``Trellis2ImageTo3DPipeline.from_pretrained`` without ever touching the network for TRELLIS.2-4B:

* ``<model_dir>/pipeline.json`` is read and every ``models`` entry is resolved to a local checkpoint prefix
  (``ckpts/...`` → under ``model_dir``; ``microsoft/TRELLIS-image-large/ckpts/ss_dec_conv3d_16l8_fp16`` →
  :attr:`AuxModelPaths.ss_decoder_prefix`),
* each model is loaded with ``trellis2.models.from_pretrained(<absolute prefix>)`` (local because both ``.json``
  and ``.safetensors`` exist), reporting per-model :class:`LoadProgress`,
* samplers / normalization / DINOv3 / background-removal model are constructed from the same config, with the
  Hugging Face repo ids replaced by the local auxiliary model directories.

Path resolution is torch-free (unit-testable without a GPU); torch/trellis2 are imported only in :meth:`load`.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from inference.base import LoadProgress

if TYPE_CHECKING:  # pragma: no cover
    from inference.trellis.assets import AuxModelPaths
    from inference.trellis.environment import TrellisSettings

logger = logging.getLogger(__name__)

PIPELINE_CONFIG = "pipeline.json"
PIPELINE_CLASS = "Trellis2ImageTo3DPipeline"

MODEL_NAMES_TO_LOAD: tuple[str, ...] = (
    "sparse_structure_flow_model",
    "sparse_structure_decoder",
    "shape_slat_flow_model_512",
    "shape_slat_flow_model_1024",
    "shape_slat_decoder",
    "tex_slat_flow_model_512",
    "tex_slat_flow_model_1024",
    "tex_slat_decoder",
)
"""Same list (and order) as ``Trellis2ImageTo3DPipeline.model_names_to_load``."""

IMAGE_COND_COMPONENT = "image_cond_model"
REMBG_COMPONENT = "rembg_model"

DEFAULT_SS_DEC_REPO = "microsoft/TRELLIS-image-large"

ProgressCallback = Callable[[LoadProgress, str], None]


class _AuxLike(Protocol):
    ss_decoder_prefix: Path


# --------------------------------------------------------------------------------------------------
# Errors (mapped to EngineState.UNAVAILABLE by the engine)
# --------------------------------------------------------------------------------------------------


class ModelFilesMissingError(Exception):
    """TRELLIS.2-4B files (pipeline.json / checkpoints) are missing. They are never downloaded automatically."""

    def __init__(self, message: str, missing: list[str] | None = None) -> None:
        super().__init__(message)
        self.missing = list(missing or [])


class AuxModelsUnavailableError(Exception):
    """Auxiliary models are missing and cannot be downloaded (downloads disabled or no HF token)."""

    def __init__(self, message: str, missing: list[str] | None = None) -> None:
        super().__init__(message)
        self.missing = list(missing or [])


# --------------------------------------------------------------------------------------------------
# Path resolution (no torch)
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelEntry:
    """One ``pipeline.json`` model entry resolved to a local checkpoint prefix (path WITHOUT extension)."""

    name: str
    spec: str
    prefix: Path
    source: str  # "model_dir" | "aux"

    @property
    def config_file(self) -> Path:
        return self.prefix.with_name(self.prefix.name + ".json")

    @property
    def weights_file(self) -> Path:
        return self.prefix.with_name(self.prefix.name + ".safetensors")

    def missing_files(self) -> list[Path]:
        return [p for p in (self.config_file, self.weights_file) if not p.is_file()]


def read_pipeline_config(model_dir: Path) -> dict[str, Any]:
    """Return the ``args`` object of ``<model_dir>/pipeline.json``."""
    path = Path(model_dir) / PIPELINE_CONFIG
    if not path.is_file():
        raise ModelFilesMissingError(
            f"TRELLIS.2-4B pipeline config not found: {path}. The weights are expected locally at "
            f"{model_dir} (they are never downloaded automatically; restore the folder or set IMG3D_MODEL_DIR).",
            [str(path)],
        )
    try:
        with path.open("r", encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ModelFilesMissingError(f"Cannot read {path}: {exc}", [str(path)]) from exc
    name = config.get("name")
    if name != PIPELINE_CLASS:
        raise ModelFilesMissingError(f"{path} describes {name!r}, expected {PIPELINE_CLASS!r}", [str(path)])
    args = config.get("args")
    if not isinstance(args, dict) or not isinstance(args.get("models"), dict):
        raise ModelFilesMissingError(f"{path} has no 'args.models' mapping", [str(path)])
    return args


def _has_checkpoint(prefix: Path) -> bool:
    return prefix.with_name(prefix.name + ".json").is_file() and prefix.with_name(prefix.name + ".safetensors").is_file()


def resolve_model_spec(
    spec: str,
    model_dir: Path,
    aux: _AuxLike | None,
    aux_root: Path | None = None,
    *,
    ss_dec_repo: str = DEFAULT_SS_DEC_REPO,
) -> tuple[Path, str]:
    """Resolve one ``pipeline.json`` model spec to ``(absolute prefix, source)``.

    Mirrors upstream ``Pipeline.from_pretrained``: a spec is first tried relative to ``model_dir``. Specs of the
    form ``<org>/<repo>/<path>`` that are not present there are external Hugging Face checkpoints:
    the TRELLIS v1 SS decoder maps to ``aux.ss_decoder_prefix``; any other repo maps to the mirror layout
    ``<aux_root>/<repo>/<path>`` used by :mod:`inference.trellis.assets`.
    """
    clean = spec.strip().replace("\\", "/").strip("/")
    parts = [p for p in clean.split("/") if p]
    if not parts or any(p in (".", "..") for p in parts):
        raise ModelFilesMissingError(f"Invalid model path in {PIPELINE_CONFIG}: {spec!r}")
    local = Path(model_dir).joinpath(*parts)
    if parts[0] == "ckpts" or len(parts) < 3 or _has_checkpoint(local):
        return local.resolve(), "model_dir"

    repo_id = f"{parts[0]}/{parts[1]}"
    if repo_id == ss_dec_repo:
        if aux is None:
            raise AuxModelsUnavailableError(f"{spec} requires the auxiliary SS decoder, which is not resolved")
        return Path(aux.ss_decoder_prefix).resolve(), "aux"
    if aux_root is not None:
        return Path(aux_root).joinpath(parts[1], *parts[2:]).resolve(), "aux"
    return local.resolve(), "model_dir"


def resolve_model_entries(
    model_dir: Path,
    models: Mapping[str, str],
    aux: _AuxLike | None,
    aux_root: Path | None = None,
    *,
    names: tuple[str, ...] = MODEL_NAMES_TO_LOAD,
    ss_dec_repo: str = DEFAULT_SS_DEC_REPO,
) -> list[ModelEntry]:
    """Resolve the models the pipeline loads (in ``pipeline.json`` order, filtered by ``names``)."""
    entries: list[ModelEntry] = []
    for name, spec in models.items():
        if name not in names:
            continue
        prefix, source = resolve_model_spec(spec, model_dir, aux, aux_root, ss_dec_repo=ss_dec_repo)
        entries.append(ModelEntry(name=name, spec=spec, prefix=prefix, source=source))
    missing_names = [n for n in names if n not in models]
    if missing_names:
        raise ModelFilesMissingError(
            f"{PIPELINE_CONFIG} is missing model entries: {', '.join(missing_names)}", missing_names
        )
    return entries


def verify_entries(entries: list[ModelEntry], model_dir: Path) -> None:
    """Raise :class:`ModelFilesMissingError` (TRELLIS.2 files) or :class:`AuxModelsUnavailableError` if any
    checkpoint file is absent."""
    missing_main = [str(p) for e in entries if e.source == "model_dir" for p in e.missing_files()]
    missing_aux = [str(p) for e in entries if e.source == "aux" for p in e.missing_files()]
    if missing_main:
        raise ModelFilesMissingError(
            f"TRELLIS.2-4B weights are incomplete under {model_dir}; missing: {', '.join(missing_main)}. "
            "Restore the files (TRELLIS.2-4B is never downloaded automatically).",
            missing_main,
        )
    if missing_aux:
        raise AuxModelsUnavailableError(
            f"Auxiliary checkpoint files are missing: {', '.join(missing_aux)}. "
            r"Run backend\python\python.exe backend\scripts\download_aux_models.py",
            missing_aux,
        )


@dataclass(frozen=True, slots=True)
class LoadPlan:
    """Everything needed to build the pipeline, resolved to local paths."""

    args: dict[str, Any]
    entries: list[ModelEntry]
    aux: AuxModelPaths

    @property
    def total_components(self) -> int:
        return len(self.entries) + 2  # + image conditioning model + background removal model


# --------------------------------------------------------------------------------------------------
# Loader
# --------------------------------------------------------------------------------------------------


def _secret(value: Any) -> str | None:
    if value is None:
        return None
    getter = getattr(value, "get_secret_value", None)
    text = getter() if callable(getter) else str(value)
    return text.strip() or None


class TrellisPipelineLoader:
    """Builds a ready-to-use ``Trellis2ImageTo3DPipeline`` from local files.

    Args:
        settings: application settings (see :class:`inference.trellis.environment.TrellisSettings`).
        on_progress: called with ``(LoadProgress, message)`` before each component is loaded and once at the end.
    """

    def __init__(self, settings: TrellisSettings, *, on_progress: ProgressCallback | None = None) -> None:
        self._settings = settings
        self._on_progress = on_progress

    # ------------------------------------------------------------------ resolution

    def resolve_aux(self) -> AuxModelPaths:
        """Locate auxiliary models; download them only if allowed and a token exists."""
        from inference.trellis.assets import AuxModelMissingError, download_aux_models, resolve_aux_models
        from inference.trellis.environment import enable_hf_offline

        aux_root = Path(self._settings.aux_model_root)
        try:
            return resolve_aux_models(aux_root)
        except AuxModelMissingError as exc:
            token = _secret(getattr(self._settings, "hf_token", None))
            if not (self._settings.allow_model_downloads and token):
                why = (
                    "IMG3D_ALLOW_MODEL_DOWNLOADS is false"
                    if not self._settings.allow_model_downloads
                    else "HF_TOKEN is not set in backend\\.env"
                )
                raise AuxModelsUnavailableError(f"{exc.message}\n(Automatic download skipped: {why}.)", exc.missing) from exc
            missing = list(exc.missing)

        self._emit(
            LoadProgress(0, len(MODEL_NAMES_TO_LOAD) + 2, "aux_models"),
            f"Downloading auxiliary models: {', '.join(missing)}",
        )
        logger.info("Auxiliary models missing (%s); downloading into %s", ", ".join(missing), aux_root)
        aux = download_aux_models(
            aux_root,
            token=token,
            hf_home=Path(self._settings.hf_home),
            log=lambda line: logger.info("%s", line),
        )
        enable_hf_offline()
        return aux

    def plan(self) -> LoadPlan:
        """Resolve config + every checkpoint path and verify the files exist (no torch)."""
        model_dir = Path(self._settings.model_dir)
        args = read_pipeline_config(model_dir)
        aux = self.resolve_aux()
        try:
            from inference.trellis.assets import SS_DEC_REPO as ss_dec_repo
        except ImportError:  # pragma: no cover - assets without the constant
            ss_dec_repo = DEFAULT_SS_DEC_REPO
        entries = resolve_model_entries(
            model_dir, args["models"], aux, Path(self._settings.aux_model_root), ss_dec_repo=ss_dec_repo
        )
        verify_entries(entries, model_dir)
        return LoadPlan(args=args, entries=entries, aux=aux)

    # ------------------------------------------------------------------ loading

    def load(self, plan: LoadPlan | None = None) -> Any:
        """Load every component and return the pipeline moved to CUDA (``low_vram`` keeps weights on CPU)."""
        import torch
        import trellis2.models as trellis_models
        from trellis2.modules import image_feature_extractor
        from trellis2.pipelines import rembg, samplers
        from trellis2.pipelines.trellis2_image_to_3d import Trellis2ImageTo3DPipeline

        from inference.trellis.compat import capture_prints

        plan = plan or self.plan()
        args = plan.args
        total = plan.total_components
        models: dict[str, Any] = {}

        for index, entry in enumerate(plan.entries):
            self._emit(LoadProgress(index, total, entry.name), f"Loading {entry.name} ({index + 1}/{total})")
            started = time.perf_counter()
            with capture_prints():
                models[entry.name] = trellis_models.from_pretrained(str(entry.prefix))
            logger.info("Loaded %s from %s in %.1fs", entry.name, entry.prefix, time.perf_counter() - started)

        def build_sampler(key: str) -> tuple[Any, dict[str, Any]]:
            cfg = args[key]
            return getattr(samplers, cfg["name"])(**cfg.get("args", {})), dict(cfg.get("params", {}))

        ss_sampler, ss_params = build_sampler("sparse_structure_sampler")
        shape_sampler, shape_params = build_sampler("shape_slat_sampler")
        tex_sampler, tex_params = build_sampler("tex_slat_sampler")

        index = len(plan.entries)
        self._emit(LoadProgress(index, total, IMAGE_COND_COMPONENT), f"Loading {IMAGE_COND_COMPONENT} ({index + 1}/{total})")
        started = time.perf_counter()
        cond_cfg = args["image_cond_model"]
        with capture_prints():
            image_cond_model = getattr(image_feature_extractor, cond_cfg["name"])(
                **{**cond_cfg.get("args", {}), "model_name": str(plan.aux.dinov3_dir)}
            )
        logger.info("Loaded %s from %s in %.1fs", IMAGE_COND_COMPONENT, plan.aux.dinov3_dir, time.perf_counter() - started)

        index += 1
        self._emit(LoadProgress(index, total, REMBG_COMPONENT), f"Loading {REMBG_COMPONENT} ({index + 1}/{total})")
        started = time.perf_counter()
        rembg_cfg = args["rembg_model"]
        with capture_prints():
            rembg_model = getattr(rembg, rembg_cfg["name"])(
                **{**rembg_cfg.get("args", {}), "model_name": str(plan.aux.rembg_dir)}
            )
        logger.info(
            "Loaded %s (%s) from %s in %.1fs",
            REMBG_COMPONENT, plan.aux.rembg_kind, plan.aux.rembg_dir, time.perf_counter() - started,
        )

        pipeline = Trellis2ImageTo3DPipeline(
            models=models,
            sparse_structure_sampler=ss_sampler,
            shape_slat_sampler=shape_sampler,
            tex_slat_sampler=tex_sampler,
            sparse_structure_sampler_params=ss_params,
            shape_slat_sampler_params=shape_params,
            tex_slat_sampler_params=tex_params,
            shape_slat_normalization=args["shape_slat_normalization"],
            tex_slat_normalization=args["tex_slat_normalization"],
            image_cond_model=image_cond_model,
            rembg_model=rembg_model,
            low_vram=bool(self._settings.low_vram),
            default_pipeline_type=args.get("default_pipeline_type", "1024_cascade"),
        )
        pipeline._pretrained_args = args  # parity with upstream from_pretrained
        # __init__ already sets pbr_attr_layout and _device; keep them explicit like the upstream classmethod.
        pipeline.pbr_attr_layout = {
            "base_color": slice(0, 3),
            "metallic": slice(3, 4),
            "roughness": slice(4, 5),
            "alpha": slice(5, 6),
        }

        self._emit(
            LoadProgress(total, total, None),
            "Moving pipeline to CUDA" + (" (low-VRAM mode: weights stay in RAM)" if pipeline.low_vram else ""),
        )
        # Upstream hard-codes "cuda" (current device) in several places; the engine selects the device index with
        # torch.cuda.set_device() on the worker thread, so the pipeline always targets the current device.
        pipeline.to(torch.device("cuda"))
        return pipeline

    def _emit(self, progress: LoadProgress, message: str) -> None:
        if self._on_progress is None:
            return
        try:
            self._on_progress(progress, message)
        except Exception:  # noqa: BLE001 - a broken listener must not break loading
            logger.exception("Load progress callback failed")
