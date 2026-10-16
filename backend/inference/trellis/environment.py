"""Runtime preparation for the TRELLIS.2 engine.

:func:`prepare_runtime` is called (from the GPU worker thread) before the pipeline is loaded. It

1. puts the vendored TRELLIS.2 source on ``sys.path``,
2. sets the environment variables upstream reads at import time (``ATTN_BACKEND``, ``SPARSE_CONV_BACKEND``,
   ``OPENCV_IO_ENABLE_OPENEXR``, ``HF_HOME``, offline flags, ...),
3. checks torch / CUDA / compute capability,
4. probes the native extensions (``o_voxel``, ``cumesh``, ``flex_gemm``, ``nvdiffrast.torch``, ``triton``),
5. selects the attention backend (``auto``: flash_attn → xformers → sdpa, each only if it imports *and* passes a
   tiny functional check),
6. imports the trellis2 modules the engine uses and applies the runtime adaptations from :mod:`.compat`.

Every problem is collected into a :class:`RuntimeReport` with an actionable message instead of being raised, so the
engine can report ``unavailable`` with a precise reason.

``PYTORCH_CUDA_ALLOC_CONF=expandable_segments`` (set by upstream ``app.py``) is deliberately NOT set: it is not
supported on Windows.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import logging
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

from inference.device import is_cuda_device, parse_cuda_index

logger = logging.getLogger(__name__)

ATTENTION_BACKENDS: tuple[str, ...] = ("flash_attn", "xformers", "sdpa")
"""Backends the engine can run with, in ``auto`` preference order."""

BUILD_EXTENSIONS_HINT = (
    r"run backend\scripts\build_extensions.cmd to build and install the native TRELLIS.2 extensions "
    r"(o_voxel, cumesh, flex_gemm, nvdiffrast) into backend\python"
)
_UV_INSTALL = (
    r"backend\python\python.exe -m uv pip install --python backend\python\python.exe --link-mode=copy {pkg}"
)


class TrellisSettings(Protocol):
    """Subset of :class:`config.settings.Settings` used by the TRELLIS.2 adapter."""

    model_dir: Path
    aux_model_root: Path
    trellis_source_dir: Path
    hf_home: Path
    hf_token: Any
    allow_model_downloads: bool
    device: str
    low_vram: bool
    attention_backend: str
    runtime_dir: Path


# --------------------------------------------------------------------------------------------------
# Report types
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModuleProbe:
    """Result of importing one native / optional module."""

    name: str
    ok: bool
    version: str | None = None
    error: str | None = None
    hint: str | None = None

    def describe(self) -> str:
        if self.ok:
            return f"{self.name} {self.version or ''}".strip()
        return f"{self.name}: {self.error}" + (f" -> {self.hint}" if self.hint else "")


@dataclass(slots=True)
class RuntimeReport:
    """Outcome of :func:`prepare_runtime`. ``ok`` is true when the pipeline can be loaded."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    torch_version: str | None = None
    cuda_version: str | None = None
    cuda_available: bool = False
    device_index: int = 0
    device_name: str | None = None
    compute_capability: str | None = None
    attention_backend: str | None = None
    attention_checks: dict[str, str] = field(default_factory=dict)
    native_modules: dict[str, ModuleProbe] = field(default_factory=dict)
    library_versions: dict[str, str] = field(default_factory=dict)
    aux_models_local: bool = False
    hf_offline: bool = False
    patches: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        if self.ok:
            return (
                f"TRELLIS.2 runtime ready: torch {self.torch_version} (CUDA {self.cuda_version}) on "
                f"{self.device_name} (sm_{(self.compute_capability or '').replace('.', '')}), "
                f"attention={self.attention_backend}"
            )
        head = self.errors[0].splitlines()[0]
        more = f" (+{len(self.errors) - 1} more)" if len(self.errors) > 1 else ""
        return f"TRELLIS.2 runtime unavailable: {head}{more}"

    def error_text(self) -> str | None:
        return "\n".join(self.errors) if self.errors else None

    def to_details(self) -> dict[str, Any]:
        """JSON-friendly summary for ``EngineStatus.details``."""
        return {
            "attention_backend": self.attention_backend,
            "attention_checks": dict(self.attention_checks),
            "torch_version": self.torch_version,
            "cuda_version": self.cuda_version,
            "device_name": self.device_name,
            "compute_capability": self.compute_capability,
            "native_modules": {
                name: (probe.version or "ok") if probe.ok else f"error: {probe.error}"
                for name, probe in self.native_modules.items()
            },
            "versions": dict(self.library_versions),
            "aux_models_local": self.aux_models_local,
            "hf_offline": self.hf_offline,
            "warnings": list(self.warnings),
        }


# --------------------------------------------------------------------------------------------------
# Environment variables
# --------------------------------------------------------------------------------------------------


def _user_site_dir() -> str | None:
    try:
        import site

        return os.path.normcase(os.path.abspath(site.getusersitepackages()))
    except Exception:  # noqa: BLE001
        return None


def drop_user_site_packages() -> list[str]:
    """Remove the per-user site-packages (``%APPDATA%\\Python\\Python314\\site-packages``) from ``sys.path``.

    The embedded interpreter keeps the user site enabled even in isolated mode, and packages there (e.g. a
    ``huggingface_hub`` 1.x that breaks ``transformers`` 4.57) would shadow ``backend\\python\\Lib\\site-packages``.
    Returns the removed entries. Modules already imported from there stay loaded (see
    :func:`modules_from_user_site`).
    """
    user_site = _user_site_dir()
    if not user_site:
        return []
    removed = [p for p in sys.path if os.path.normcase(os.path.abspath(p)) == user_site]
    if removed:
        sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p)) != user_site]
    return removed


def modules_from_user_site() -> list[str]:
    """Top-level modules currently imported from the user site-packages."""
    user_site = _user_site_dir()
    if not user_site:
        return []
    found: set[str] = set()
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if path and os.path.normcase(os.path.abspath(path)).startswith(user_site + os.sep):
            found.add(name.split(".")[0])
    return sorted(found)


def ensure_source_on_path(source_dir: Path) -> None:
    """Put the vendored TRELLIS.2 checkout (the directory containing ``trellis2/``) first on ``sys.path``."""
    text = str(Path(source_dir).resolve())
    if text not in sys.path:
        sys.path.insert(0, text)


def configure_hf_environment(hf_home: Path) -> list[str]:
    """Point every Hugging Face cache at ``hf_home``. Must run before ``huggingface_hub``/``transformers`` import.

    Returns warnings (e.g. when ``huggingface_hub`` was already imported with another cache location).
    """
    warnings: list[str] = []
    hf_home = Path(hf_home)
    hf_home.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(hf_home)
    os.environ.setdefault("HF_MODULES_CACHE", str(hf_home / "modules"))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    constants = sys.modules.get("huggingface_hub.constants")
    if constants is not None and Path(getattr(constants, "HF_HOME", hf_home)) != hf_home:
        warnings.append(
            f"huggingface_hub was imported before HF_HOME was configured; its cache stays at "
            f"{getattr(constants, 'HF_HOME', '?')} for this process"
        )
    return warnings


def enable_hf_offline() -> None:
    """Forbid any Hugging Face network access (aux models are local). Also patches already-imported libraries."""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    constants = sys.modules.get("huggingface_hub.constants")
    if constants is not None:
        constants.HF_HUB_OFFLINE = True
    transformers_hub = sys.modules.get("transformers.utils.hub")
    if transformers_hub is not None and hasattr(transformers_hub, "_is_offline_mode"):
        transformers_hub._is_offline_mode = True


def is_hf_offline() -> bool:
    return os.environ.get("HF_HUB_OFFLINE", "").strip().lower() in {"1", "true", "yes", "on"}


def configure_trellis_environment(attention_backend: str, *, cache_root: Path | None = None) -> None:
    """Environment read by trellis2 / o_voxel / triton / flex_gemm at import time.

    ``cache_root`` (normally ``<runtime_dir>/cache``) receives the Triton kernel cache and flex_gemm's persistent
    autotune cache, which would otherwise be written to the user's home directory.
    """
    os.environ["ATTN_BACKEND"] = attention_backend  # dense attention: flash_attn | xformers | sdpa
    if attention_backend in ("flash_attn", "xformers"):
        os.environ["SPARSE_ATTN_BACKEND"] = attention_backend
    else:
        # Upstream's sparse attention has no SDPA path; compat.install_sparse_sdpa_fallback() provides it.
        os.environ.pop("SPARSE_ATTN_BACKEND", None)
    os.environ["SPARSE_CONV_BACKEND"] = "flex_gemm"
    os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"
    if cache_root is not None:
        triton_dir = Path(cache_root) / "triton"
        flex_gemm_dir = Path(cache_root) / "flex_gemm"
        triton_dir.mkdir(parents=True, exist_ok=True)
        flex_gemm_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("TRITON_CACHE_DIR", str(triton_dir))
        os.environ.setdefault("FLEX_GEMM_AUTOTUNE_CACHE_PATH", str(flex_gemm_dir / "autotune_cache.json"))


# --------------------------------------------------------------------------------------------------
# Probes
# --------------------------------------------------------------------------------------------------

_NATIVE_MODULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    # (import name, distribution names for the version lookup, hint when missing)
    ("triton", ("triton-windows", "triton"), "install the Triton runtime: " + _UV_INSTALL.format(pkg="triton-windows")),
    ("flex_gemm", ("flex_gemm", "flex-gemm"), BUILD_EXTENSIONS_HINT),
    ("cumesh", ("cumesh",), BUILD_EXTENSIONS_HINT),
    ("nvdiffrast.torch", ("nvdiffrast",), BUILD_EXTENSIONS_HINT),
    ("o_voxel", ("o_voxel", "o-voxel"), BUILD_EXTENSIONS_HINT),
)


def _dist_version(names: Sequence[str]) -> str | None:
    for name in names:
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        except Exception:  # noqa: BLE001 - broken metadata must not break the probe
            continue
    return None


def describe_import_error(module: str, exc: BaseException) -> tuple[str, str | None]:
    """``(error, hint)`` for a failed import, distinguishing "not installed" from "broken dependency/DLL"."""
    top = module.split(".")[0]
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    if isinstance(exc, ModuleNotFoundError) and exc.name and exc.name.split(".")[0] != top:
        dep = exc.name.split(".")[0]
        return (
            f"import failed: missing Python dependency '{dep}' ({text})",
            "install it into the embedded Python: " + _UV_INSTALL.format(pkg=dep),
        )
    if isinstance(exc, ModuleNotFoundError):
        return f"not installed ({text})", None
    if "DLL load failed" in text or isinstance(exc, OSError):
        return (
            f"native library failed to load ({type(exc).__name__}: {text})",
            "the extension was probably built against another torch/CUDA version; rebuild it - "
            + BUILD_EXTENSIONS_HINT,
        )
    return f"import failed ({type(exc).__name__}: {text})", None


def probe_module(module: str, dist_names: Sequence[str] = (), hint: str | None = None) -> ModuleProbe:
    """Import ``module`` and report success, version or a precise error."""
    try:
        imported = importlib.import_module(module)
    except BaseException as exc:  # noqa: BLE001 - SystemExit from broken C extensions must not kill the probe
        if isinstance(exc, KeyboardInterrupt):
            raise
        error, specific_hint = describe_import_error(module, exc)
        return ModuleProbe(name=module, ok=False, error=error, hint=specific_hint or hint)
    version = _dist_version(dist_names) or getattr(imported, "__version__", None)
    return ModuleProbe(name=module, ok=True, version=str(version) if version else None)


def probe_native_modules() -> dict[str, ModuleProbe]:
    return {name: probe_module(name, dists, hint) for name, dists, hint in _NATIVE_MODULES}


# --------------------------------------------------------------------------------------------------
# Attention backend selection
# --------------------------------------------------------------------------------------------------


def _reference_attention(torch: ModuleType, q: Any, k: Any, v: Any, seqlens: Sequence[int]) -> Any:
    """Per-sequence float32 softmax attention on packed ``[T, H, C]`` tensors."""
    outs = []
    start = 0
    for length in seqlens:
        qs, ks, vs = (t[start : start + length].float() for t in (q, k, v))
        weights = torch.einsum("qhc,khc->hqk", qs, ks) * qs.shape[-1] ** -0.5
        outs.append(torch.einsum("hqk,khc->qhc", weights.softmax(dim=-1), vs))
        start += length
    return torch.cat(outs, dim=0)


def _tiny_inputs(torch: ModuleType, device: Any) -> tuple[Any, list[int]]:
    seqlens = [16, 9]
    generator = torch.Generator(device="cpu").manual_seed(1234)
    qkv = torch.randn(sum(seqlens), 3, 2, 64, generator=generator).to(device=device, dtype=torch.bfloat16)
    return qkv, seqlens


def _check_flash_attn(torch: ModuleType, device: Any) -> None:
    flash_attn = importlib.import_module("flash_attn")
    qkv, seqlens = _tiny_inputs(torch, device)
    cu = torch.tensor([0, seqlens[0], sum(seqlens)], dtype=torch.int32, device=device)
    out = flash_attn.flash_attn_varlen_qkvpacked_func(qkv, cu, max(seqlens))
    ref = _reference_attention(torch, qkv[:, 0], qkv[:, 1], qkv[:, 2], seqlens)
    _assert_close(torch, out, ref)


def _check_xformers(torch: ModuleType, device: Any) -> None:
    xops = importlib.import_module("xformers.ops")
    qkv, seqlens = _tiny_inputs(torch, device)
    q, k, v = (t.unsqueeze(0) for t in qkv.unbind(dim=1))
    mask = xops.fmha.BlockDiagonalMask.from_seqlens(seqlens, seqlens)
    out = xops.memory_efficient_attention(q, k, v, mask)[0]
    ref = _reference_attention(torch, qkv[:, 0], qkv[:, 1], qkv[:, 2], seqlens)
    _assert_close(torch, out, ref)


def _check_sdpa(torch: ModuleType, device: Any) -> None:
    qkv, seqlens = _tiny_inputs(torch, device)
    outs = []
    start = 0
    for length in seqlens:
        q, k, v = (t[start : start + length].unsqueeze(0).transpose(1, 2) for t in qkv.unbind(dim=1))
        outs.append(torch.nn.functional.scaled_dot_product_attention(q, k, v).transpose(1, 2)[0])
        start += length
    ref = _reference_attention(torch, qkv[:, 0], qkv[:, 1], qkv[:, 2], seqlens)
    _assert_close(torch, torch.cat(outs, dim=0), ref)


def _assert_close(torch: ModuleType, out: Any, ref: Any) -> None:
    if not torch.isfinite(out).all():
        raise RuntimeError("non-finite output")
    err = (out.float() - ref).abs().max().item()
    if err > 5e-2:
        raise RuntimeError(f"numerical mismatch vs reference (max abs err {err:.3g})")


_ATTENTION_CHECKS: dict[str, Callable[[ModuleType, Any], None]] = {
    "flash_attn": _check_flash_attn,
    "xformers": _check_xformers,
    "sdpa": _check_sdpa,
}


def select_attention_backend(
    torch: ModuleType, requested: str, device: Any
) -> tuple[str | None, dict[str, str], list[str]]:
    """Pick the attention backend.

    Returns ``(backend | None, {backend: "ok" | reason}, warnings)``. ``requested="auto"`` tries flash_attn,
    xformers, sdpa in order. An explicitly requested backend that fails its check falls back to sdpa (warning).
    """
    requested = (requested or "auto").strip().lower()
    warnings: list[str] = []
    checks: dict[str, str] = {}
    if requested == "auto":
        order = list(ATTENTION_BACKENDS)
    elif requested in _ATTENTION_CHECKS:
        order = [requested] + ([] if requested == "sdpa" else ["sdpa"])
    else:
        warnings.append(f"Unknown attention backend {requested!r}; using auto selection")
        order = list(ATTENTION_BACKENDS)

    for name in order:
        try:
            _ATTENTION_CHECKS[name](torch, device)
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, KeyboardInterrupt):
                raise
            if isinstance(exc, ImportError):
                reason = describe_import_error(name, exc)[0]
            else:
                reason = f"functional check failed ({type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''})"
            checks[name] = reason
            continue
        checks[name] = "ok"
        if requested not in ("auto", name):
            warnings.append(
                f"Requested attention backend {requested!r} is unusable ({checks.get(requested)}); using {name!r}"
            )
        return name, checks, warnings
    return None, checks, warnings


# --------------------------------------------------------------------------------------------------
# trellis2 import
# --------------------------------------------------------------------------------------------------

TRELLIS_MODULES: tuple[str, ...] = (
    "trellis2",
    "trellis2.modules.attention",
    "trellis2.modules.sparse",
    "trellis2.modules.sparse.attention",
    "trellis2.models.sparse_structure_flow",
    "trellis2.models.sparse_structure_vae",
    "trellis2.models.structured_latent_flow",
    "trellis2.models.sc_vaes.sparse_unet_vae",
    "trellis2.models.sc_vaes.fdg_vae",
    "trellis2.pipelines.samplers",
    "trellis2.pipelines.rembg",
    "trellis2.modules.image_feature_extractor",
    "trellis2.pipelines.trellis2_image_to_3d",
    "o_voxel.postprocess",
)
"""Everything the engine touches, imported up-front so failures surface before weights are loaded and so the SDPA
fallback can patch every module that bound the original attention function."""


def import_trellis_modules(report: RuntimeReport) -> bool:
    import warnings

    from inference.trellis import compat

    # Upstream docstrings contain invalid escapes ("\m"); with dont_write_bytecode they would warn on every start.
    with compat.capture_prints(logging.getLogger("trellis2.upstream")), warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=SyntaxWarning)
        for name in TRELLIS_MODULES:
            try:
                importlib.import_module(name)
            except BaseException as exc:  # noqa: BLE001
                if isinstance(exc, KeyboardInterrupt):
                    raise
                error, hint = describe_import_error(name, exc)
                if "transformers" in str(exc) or "huggingface" in str(exc):
                    shadowed = modules_from_user_site()
                    if shadowed:
                        hint = (
                            f"packages are being imported from the user site-packages ({_user_site_dir()}: "
                            f"{', '.join(shadowed)}); remove it from sys.path at startup "
                            "(inference.trellis.environment.drop_user_site_packages) before any import"
                        )
                    else:
                        hint = hint or (
                            "the image encoder needs transformers==4.57.6 with huggingface_hub<1.0: "
                            + _UV_INSTALL.format(pkg='"transformers==4.57.6" "huggingface_hub<1.0"')
                        )
                logger.debug("Importing %s failed", name, exc_info=True)
                report.errors.append(f"Cannot import {name}: {error}" + (f" -> {hint}" if hint else ""))
                return False
    return True


def configure_trellis_backends(attention_backend: str) -> None:
    """Set the upstream backend switches directly (covers modules imported before the env vars were set)."""
    attn_config = importlib.import_module("trellis2.modules.attention.config")
    sparse_config = importlib.import_module("trellis2.modules.sparse.config")
    attn_config.BACKEND = attention_backend
    sparse_config.CONV = "flex_gemm"
    if attention_backend in ("flash_attn", "xformers"):
        sparse_config.ATTN = attention_backend


def _library_versions(torch: ModuleType) -> dict[str, str]:
    versions = {"torch": str(torch.__version__), "cuda": str(torch.version.cuda)}
    for dist in ("transformers", "huggingface_hub", "safetensors", "trimesh", "triton-windows"):
        version = _dist_version((dist,))
        if version:
            versions[dist] = version
    return versions


# --------------------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------------------


def _check_aux_models_local(settings: TrellisSettings, report: RuntimeReport) -> None:
    try:
        from inference.trellis.assets import AuxModelMissingError, resolve_aux_models
    except Exception as exc:  # noqa: BLE001
        report.warnings.append(f"Auxiliary model resolver unavailable ({type(exc).__name__}: {exc})")
        return
    try:
        resolve_aux_models(Path(settings.aux_model_root))
    except AuxModelMissingError:
        report.aux_models_local = False
        return
    except Exception as exc:  # noqa: BLE001
        report.warnings.append(f"Could not check auxiliary models ({type(exc).__name__}: {exc})")
        return
    report.aux_models_local = True
    enable_hf_offline()


def prepare_runtime(settings: TrellisSettings) -> RuntimeReport:
    """Prepare the process for TRELLIS.2 (see module docstring). Never raises for expected problems."""
    report = RuntimeReport()
    try:
        _prepare(settings, report)
    finally:
        for warning in report.warnings:
            logger.warning("%s", warning)
    if report.ok:
        logger.info("%s", report.summary())
    return report


def _prepare(settings: TrellisSettings, report: RuntimeReport) -> None:
    # 0) never let the per-user site-packages shadow the embedded interpreter's packages
    if drop_user_site_packages():
        logger.info("Removed the user site-packages from sys.path (embedded interpreter packages only)")
    shadowed = modules_from_user_site()
    if shadowed:
        message = (
            f"Modules already imported from the user site-packages ({_user_site_dir()}): {', '.join(shadowed)}. "
            "They shadow backend\\python\\Lib\\site-packages; the entry point must remove the user site from "
            "sys.path before importing anything (see inference.trellis.environment.drop_user_site_packages)."
        )
        report.warnings.append(message)

    # 1) vendored source
    source_dir = Path(settings.trellis_source_dir)
    if not (source_dir / "trellis2" / "__init__.py").is_file():
        report.errors.append(
            f"TRELLIS.2 source not found: {source_dir} does not contain trellis2/__init__.py "
            "(set IMG3D_TRELLIS_SOURCE_DIR to the vendored checkout, normally backend\\third_party\\TRELLIS.2)"
        )
        return
    ensure_source_on_path(source_dir)

    # 2) environment that must exist before huggingface_hub / transformers / triton are imported
    report.warnings.extend(configure_hf_environment(Path(settings.hf_home)))
    runtime_dir = getattr(settings, "runtime_dir", None)
    configure_trellis_environment(
        "sdpa",  # provisional; re-set once the backend has been selected
        cache_root=Path(runtime_dir) / "cache" if runtime_dir else None,
    )
    _check_aux_models_local(settings, report)
    report.hf_offline = is_hf_offline()

    # 3) torch + CUDA
    if not is_cuda_device(settings.device):
        report.errors.append(
            f"TRELLIS.2 requires a CUDA GPU but IMG3D_DEVICE={settings.device!r}; set IMG3D_DEVICE=cuda"
        )
        return
    try:
        torch = importlib.import_module("torch")
    except BaseException as exc:  # noqa: BLE001
        if isinstance(exc, KeyboardInterrupt):
            raise
        report.errors.append(
            f"PyTorch failed to import ({type(exc).__name__}: {exc}) -> reinstall torch 2.14.1+cu130 into "
            r"backend\python (index https://download.pytorch.org/whl/cu130)"
        )
        return
    report.torch_version = str(torch.__version__)
    report.cuda_version = str(torch.version.cuda) if torch.version.cuda else None
    report.library_versions = _library_versions(torch)
    if not torch.cuda.is_available():
        report.errors.append(
            f"CUDA is not available to torch {torch.__version__} (CUDA build: {torch.version.cuda}); "
            "check the NVIDIA driver and that a CUDA-enabled torch wheel is installed"
        )
        return
    index = parse_cuda_index(settings.device)
    if index >= torch.cuda.device_count():
        report.errors.append(
            f"IMG3D_DEVICE={settings.device!r} but only {torch.cuda.device_count()} CUDA device(s) are visible"
        )
        return
    torch.cuda.set_device(index)
    report.cuda_available = True
    report.device_index = index
    props = torch.cuda.get_device_properties(index)
    report.device_name = str(props.name)
    report.compute_capability = f"{props.major}.{props.minor}"
    arch = f"sm_{props.major}{props.minor}"
    arch_list = list(torch.cuda.get_arch_list())
    if arch not in arch_list:
        report.warnings.append(
            f"torch {torch.__version__} has no native kernels for {arch} (built for {', '.join(arch_list)}); "
            "kernels will be JIT-compiled from PTX if possible"
        )

    # 4) native extensions
    report.native_modules = probe_native_modules()
    for probe in report.native_modules.values():
        if not probe.ok:
            report.errors.append(f"Native module '{probe.name}' {probe.error}" + (f" -> {probe.hint}" if probe.hint else ""))

    # 5) attention backend
    backend, checks, attn_warnings = select_attention_backend(
        torch, settings.attention_backend, torch.device("cuda", index)
    )
    report.attention_checks = checks
    report.warnings.extend(attn_warnings)
    if backend is None:
        report.errors.append(
            "No usable attention backend: " + "; ".join(f"{k}: {v}" for k, v in checks.items())
        )
        return
    report.attention_backend = backend
    configure_trellis_environment(backend)

    if not report.ok:
        return

    # 6) trellis2 import + runtime adaptations
    if not import_trellis_modules(report):
        return
    configure_trellis_backends(backend)

    from inference.trellis import compat

    compat.silence_upstream_output()
    report.patches.append("quiet_upstream_output")
    if backend == "sdpa":
        patched = compat.install_sparse_sdpa_fallback()
        report.patches.append(f"sparse_sdpa_fallback({len(patched)} bindings)")
