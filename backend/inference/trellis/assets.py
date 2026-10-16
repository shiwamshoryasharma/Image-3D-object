"""Auxiliary model assets required by ``TRELLIS.2-4B/pipeline.json``.

The TRELLIS.2-4B checkpoint folder does not contain every model its pipeline needs. ``pipeline.json`` references
three external Hugging Face repositories:

* ``image_cond_model``  -> ``facebook/dinov3-vitl16-pretrain-lvd1689m`` (DINOv3 ViT-L/16, gated, required)
* ``rembg_model``       -> ``briaai/RMBG-2.0`` (gated, non-commercial license); ``ZhengPeng7/BiRefNet`` (MIT) is
  an architecture-compatible fallback loaded by the same ``BiRefNet`` wrapper
* ``sparse_structure_decoder`` -> ``microsoft/TRELLIS-image-large`` ``ckpts/ss_dec_conv3d_16l8_fp16`` (MIT)

They are stored below a local *aux root* (``backend/.model`` by default) so that the runtime is fully offline::

    <aux_root>/dinov3-vitl16-pretrain-lvd1689m/{config.json, model.safetensors, ...}
    <aux_root>/RMBG-2.0/{config.json, birefnet.py, BiRefNet_config.py, model.safetensors, ...}
    <aux_root>/BiRefNet/{...}                                          (only if the fallback was needed)
    <aux_root>/TRELLIS-image-large/ckpts/ss_dec_conv3d_16l8_fp16.{json,safetensors}

:func:`resolve_aux_models` is purely local (no network) and is what the engine calls at load time.
:func:`download_aux_models` is the idempotent installer used by ``backend/scripts/download_aux_models.py``.

This module intentionally imports only the standard library and ``huggingface_hub`` (no torch) so it can be used
by tooling and by the API process without pulling in the ML stack.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from huggingface_hub import hf_hub_download, snapshot_download
from huggingface_hub.errors import (
    GatedRepoError,
    HfHubHTTPError,
    LocalEntryNotFoundError,
    RepositoryNotFoundError,
)

__all__ = [
    "BIREFNET_REPO",
    "DINOV3_REPO",
    "REMBG_KIND_BIREFNET",
    "REMBG_KIND_RMBG2",
    "RMBG_REPO",
    "SS_DEC_FILE",
    "SS_DEC_REPO",
    "AuxModelMissingError",
    "AuxModelPaths",
    "describe_aux_models",
    "download_aux_models",
    "resolve_aux_models",
]

# --------------------------------------------------------------------------------------------------
# Public constants
# --------------------------------------------------------------------------------------------------

DINOV3_REPO = "facebook/dinov3-vitl16-pretrain-lvd1689m"
RMBG_REPO = "briaai/RMBG-2.0"
BIREFNET_REPO = "ZhengPeng7/BiRefNet"
SS_DEC_REPO = "microsoft/TRELLIS-image-large"
SS_DEC_FILE = "ckpts/ss_dec_conv3d_16l8_fp16"
"""Repo-relative checkpoint prefix; ``.json`` and ``.safetensors`` are appended by ``trellis2.models.from_pretrained``."""

REMBG_KIND_RMBG2 = "rmbg2"
REMBG_KIND_BIREFNET = "birefnet"

_HF_URL = "https://huggingface.co"
_DOWNLOAD_COMMAND = r"backend\python\python.exe backend\scripts\download_aux_models.py"
_MAX_SAFETENSORS_HEADER = 100 * 1024 * 1024  # sanity bound for the JSON header of a .safetensors file


# --------------------------------------------------------------------------------------------------
# Repository specifications (internal)
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _RepoSpec:
    """What to fetch from one Hugging Face repo and how to decide that the local copy is complete."""

    repo_id: str
    dirname: str
    label: str
    required: tuple[str, ...]
    """Repo-relative files that must exist (and be intact) locally."""
    allow_patterns: tuple[str, ...] | None = None
    """``snapshot_download`` patterns; ``None`` means the repo is fetched file-by-file from ``required``."""
    ignore_patterns: tuple[str, ...] = ()
    gated: bool = False
    license_note: str = ""
    remote_code: bool = False
    """``config.json`` declares ``auto_map`` remote code whose modules must be present too."""

    @property
    def url(self) -> str:
        return f"{_HF_URL}/{self.repo_id}"


_DINOV3 = _RepoSpec(
    repo_id=DINOV3_REPO,
    dirname="dinov3-vitl16-pretrain-lvd1689m",
    label="DINOv3 ViT-L/16 image encoder (image conditioning, required)",
    required=("config.json", "model.safetensors"),
    allow_patterns=("config.json", "preprocessor_config.json", "model.safetensors", "LICENSE*"),
    gated=True,
    license_note="DINOv3 License; access is granted manually by Meta after you submit the form on the model page",
)

_RMBG = _RepoSpec(
    repo_id=RMBG_REPO,
    dirname="RMBG-2.0",
    label="BRIA RMBG-2.0 background removal (preferred)",
    required=("config.json", "model.safetensors"),
    allow_patterns=("config.json", "preprocessor_config.json", "*.py", "model.safetensors", "LICENSE*"),
    gated=True,
    license_note="BRIA RMBG-2.0 license (non-commercial use); access is granted automatically once accepted",
    remote_code=True,
)

_BIREFNET = _RepoSpec(
    repo_id=BIREFNET_REPO,
    dirname="BiRefNet",
    label="BiRefNet background removal (MIT fallback for RMBG-2.0)",
    required=("config.json", "model.safetensors"),
    allow_patterns=("config.json", "preprocessor_config.json", "*.py", "model.safetensors", "LICENSE*"),
    ignore_patterns=("handler.py",),
    license_note="MIT",
    remote_code=True,
)

_SS_DECODER = _RepoSpec(
    repo_id=SS_DEC_REPO,
    dirname="TRELLIS-image-large",
    label="TRELLIS v1 sparse-structure decoder (required)",
    required=(f"{SS_DEC_FILE}.json", f"{SS_DEC_FILE}.safetensors"),
    license_note="MIT",
)


# --------------------------------------------------------------------------------------------------
# Public types
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AuxModelPaths:
    """Resolved local locations of the auxiliary models.

    Attributes:
        dinov3_dir: Directory loadable with ``DINOv3ViTModel.from_pretrained``.
        rembg_dir: Directory loadable with ``AutoModelForImageSegmentation.from_pretrained(..., trust_remote_code=True)``.
        rembg_kind: ``"rmbg2"`` (briaai/RMBG-2.0) or ``"birefnet"`` (ZhengPeng7/BiRefNet fallback).
        ss_decoder_prefix: Checkpoint path WITHOUT extension, as expected by ``trellis2.models.from_pretrained``.
    """

    dinov3_dir: Path
    rembg_dir: Path
    rembg_kind: str  # "rmbg2" | "birefnet"
    ss_decoder_prefix: Path

    def as_dict(self) -> dict[str, str]:
        """JSON-friendly representation (e.g. for engine status details)."""
        return {
            "dinov3_dir": str(self.dinov3_dir),
            "rembg_dir": str(self.rembg_dir),
            "rembg_kind": self.rembg_kind,
            "ss_decoder_prefix": str(self.ss_decoder_prefix),
        }


class AuxModelMissingError(Exception):
    """One or more auxiliary models are not available locally.

    Attributes:
        missing: Hugging Face repo ids of the missing models (e.g. ``["facebook/dinov3-vitl16-pretrain-lvd1689m"]``).
        reasons: Optional per-repo failure reason recorded by :func:`download_aux_models` (e.g. gated access denied).
    """

    def __init__(self, missing: Sequence[str], message: str, *, reasons: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.missing: list[str] = list(missing)
        self.reasons: dict[str, str] = dict(reasons or {})
        self.message = message


# --------------------------------------------------------------------------------------------------
# Local integrity checks (no network)
# --------------------------------------------------------------------------------------------------


def _safetensors_intact(path: Path) -> bool:
    """Cheap integrity check: the header parses and the file size matches the declared tensor data size."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            raw = fh.read(8)
            if len(raw) != 8:
                return False
            (header_len,) = struct.unpack("<Q", raw)
            if header_len <= 0 or header_len > _MAX_SAFETENSORS_HEADER or 8 + header_len > size:
                return False
            header = json.loads(fh.read(header_len))
        data_end = max(
            (int(meta["data_offsets"][1]) for key, meta in header.items() if key != "__metadata__"),
            default=0,
        )
        return 8 + header_len + data_end == size
    except (OSError, ValueError, KeyError, TypeError, IndexError, struct.error):
        return False


def _json_valid(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as fh:
            json.load(fh)
        return True
    except (OSError, ValueError):
        return False


def _file_intact(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    if path.suffix == ".safetensors":
        return _safetensors_intact(path)
    if path.suffix == ".json":
        return _json_valid(path)
    return True


def _remote_code_modules(config_path: Path) -> list[str]:
    """Module files referenced by ``config.json`` ``auto_map`` (e.g. ``"birefnet.BiRefNet"`` -> ``birefnet.py``)."""
    try:
        with config_path.open("r", encoding="utf-8") as fh:
            auto_map = json.load(fh).get("auto_map") or {}
    except (OSError, ValueError, AttributeError):
        return []
    modules: set[str] = set()
    for ref in auto_map.values():
        refs = ref if isinstance(ref, list) else [ref]
        for item in refs:
            if isinstance(item, str) and "." in item and "--" not in item:  # skip cross-repo refs
                modules.add(item.rsplit(".", 1)[0].replace(".", "/") + ".py")
    return sorted(modules)


def _missing_files(spec: _RepoSpec, local_dir: Path) -> list[str]:
    """Repo-relative files of ``spec`` that are absent or corrupt under ``local_dir``."""
    missing = [rel for rel in spec.required if not _file_intact(local_dir / rel)]
    if spec.remote_code and "config.json" not in missing:
        modules = _remote_code_modules(local_dir / "config.json")
        if not modules:
            missing.append("config.json:auto_map")
        missing.extend(m for m in modules if not _file_intact(local_dir / m))
    return missing


def _dir_size(path: Path) -> int:
    """Total size of the model files under ``path`` (ignores the huggingface_hub ``.cache`` bookkeeping dir)."""
    if not path.exists():
        return 0
    return sum(
        p.stat().st_size for p in path.rglob("*") if p.is_file() and ".cache" not in p.relative_to(path).parts
    )


def _fmt_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"  # pragma: no cover


# --------------------------------------------------------------------------------------------------
# Resolution (local only)
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Problem:
    spec: _RepoSpec
    local_dir: Path
    files: tuple[str, ...]
    note: str = ""


def _build_message(aux_root: Path, problems: Sequence[_Problem], reasons: Mapping[str, str]) -> str:
    lines = [f"Auxiliary models required by TRELLIS.2 are missing under {aux_root}:"]
    for problem in problems:
        spec = problem.spec
        lines.append(f"  - {spec.repo_id} [{spec.label}]")
        lines.append(f"      expected at: {problem.local_dir}")
        if problem.files:
            lines.append(f"      missing/incomplete files: {', '.join(problem.files)}")
        if problem.note:
            lines.append(f"      note: {problem.note}")
        reason = reasons.get(spec.repo_id)
        if reason:
            lines.append(f"      download failed: {reason}")
        if spec.gated:
            lines.append(
                f"      gated repo: log in to Hugging Face with the account that owns HF_TOKEN, open {spec.url} "
                f"and accept the license ({spec.license_note})."
            )
    retry = (
        f"then run `{_DOWNLOAD_COMMAND}` from the project root (use --check to verify without downloading) "
        "and click 'Retry loading model' in the UI (or restart the backend)."
    )
    reason_texts = [reasons.get(p.spec.repo_id, "") for p in problems]
    if any("not been granted" in r or "HTTP 403" in r for r in reason_texts):
        # The token authenticated fine; the account simply lacks access to the gated repo(s).
        lines.append(
            "How to fix: HF_TOKEN is valid, but its Hugging Face account has not been granted access to the gated "
            "repo(s) above. Open each model page while logged in as that account and request access / accept the "
            f"license (DINOv3 access is approved manually by Meta and can take a while), {retry}"
        )
    else:
        lines.append(
            "How to fix: make sure backend\\.env has HF_TOKEN=<read token> for a Hugging Face account that has been "
            "granted access to the gated repo(s) above (request access on each model page), " + retry
        )
    return "\n".join(lines)


def _resolve(aux_root: Path, reasons: Mapping[str, str] | None = None) -> AuxModelPaths:
    aux_root = Path(aux_root)
    reasons = dict(reasons or {})
    problems: list[_Problem] = []

    dinov3_dir = aux_root / _DINOV3.dirname
    if files := _missing_files(_DINOV3, dinov3_dir):
        problems.append(_Problem(_DINOV3, dinov3_dir, tuple(files), note="no fallback exists; DINOv3 is required"))

    rmbg_dir = aux_root / _RMBG.dirname
    birefnet_dir = aux_root / _BIREFNET.dirname
    rembg_dir, rembg_kind = rmbg_dir, REMBG_KIND_RMBG2
    if rmbg_missing := _missing_files(_RMBG, rmbg_dir):
        if not _missing_files(_BIREFNET, birefnet_dir):
            rembg_dir, rembg_kind = birefnet_dir, REMBG_KIND_BIREFNET
        else:
            problems.append(
                _Problem(
                    _RMBG,
                    rmbg_dir,
                    tuple(rmbg_missing),
                    note=(
                        f"alternatively the MIT-licensed fallback {BIREFNET_REPO} at {birefnet_dir} is accepted "
                        f"(downloaded automatically when {RMBG_REPO} is not accessible)"
                    ),
                )
            )
            if BIREFNET_REPO in reasons:
                reasons[RMBG_REPO] = "; ".join(
                    filter(None, [reasons.get(RMBG_REPO), f"fallback {BIREFNET_REPO}: {reasons[BIREFNET_REPO]}"])
                )

    ss_dir = aux_root / _SS_DECODER.dirname
    if files := _missing_files(_SS_DECODER, ss_dir):
        problems.append(_Problem(_SS_DECODER, ss_dir, tuple(files)))

    if problems:
        missing = [p.spec.repo_id for p in problems]
        raise AuxModelMissingError(
            missing,
            _build_message(aux_root, problems, reasons),
            reasons={k: v for k, v in reasons.items() if k in missing},
        )

    return AuxModelPaths(
        dinov3_dir=dinov3_dir,
        rembg_dir=rembg_dir,
        rembg_kind=rembg_kind,
        ss_decoder_prefix=ss_dir / Path(*SS_DEC_FILE.split("/")),
    )


def resolve_aux_models(aux_root: Path) -> AuxModelPaths:
    """Locate the auxiliary models under ``aux_root`` without touching the network.

    Background removal prefers ``RMBG-2.0`` and falls back to ``BiRefNet`` when only the latter is present.

    Raises:
        AuxModelMissingError: if DINOv3, the SS decoder, or both background-removal models are missing/incomplete.
    """
    return _resolve(aux_root)


# --------------------------------------------------------------------------------------------------
# Download (idempotent)
# --------------------------------------------------------------------------------------------------


def _describe_error(exc: BaseException, spec: _RepoSpec) -> str:
    """Human-readable, secret-free description of a download failure."""
    if isinstance(exc, GatedRepoError):
        status = getattr(getattr(exc, "response", None), "status_code", None) or "401/403"
        if status == 401:
            return (
                f"access denied (HTTP 401): {spec.repo_id} is gated and no valid HF_TOKEN was sent - set HF_TOKEN in "
                f"backend\\.env and accept the license at {spec.url}"
            )
        return (
            f"access denied (HTTP {status}): {spec.repo_id} is gated and the account behind HF_TOKEN has not been "
            f"granted access - accept the license at {spec.url}"
        )
    if isinstance(exc, RepositoryNotFoundError):
        return (
            f"repository not found or not accessible (HTTP 401/404): check that HF_TOKEN is valid and that "
            f"{spec.url} is accessible to your account"
        )
    if isinstance(exc, HfHubHTTPError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (401, 403):
            return (
                f"authentication/authorization failed (HTTP {status}): HF_TOKEN missing or invalid, or the license "
                f"at {spec.url} has not been accepted"
            )
        return f"HTTP error {status}: {str(exc).splitlines()[0][:300]}"
    if isinstance(exc, LocalEntryNotFoundError):
        return "network unavailable or HF_HUB_OFFLINE=1 (could not reach huggingface.co)"
    first_line = str(exc).splitlines()[0][:300] if str(exc) else ""
    return f"{type(exc).__name__}: {first_line}"


def _fetch(
    spec: _RepoSpec,
    local_dir: Path,
    *,
    token: str | None,
    cache_dir: Path | None,
    log: Callable[[str], None],
) -> str | None:
    """Ensure ``spec`` is complete in ``local_dir``. Returns ``None`` on success or a failure reason."""
    if not _missing_files(spec, local_dir):
        log(f"[aux] {spec.repo_id}: already complete at {local_dir} ({_fmt_size(_dir_size(local_dir))}), skipping")
        return None

    log(f"[aux] {spec.repo_id}: downloading -> {local_dir}")
    local_dir.mkdir(parents=True, exist_ok=True)
    common = {
        "repo_id": spec.repo_id,
        "local_dir": str(local_dir),
        "token": token,
        "cache_dir": str(cache_dir) if cache_dir else None,
    }
    try:
        if spec.allow_patterns is not None:
            snapshot_download(
                allow_patterns=list(spec.allow_patterns),
                ignore_patterns=list(spec.ignore_patterns) or None,
                **common,
            )
        else:
            for filename in spec.required:
                hf_hub_download(filename=filename, **common)
    except Exception as exc:  # noqa: BLE001 - every failure is reported, never swallowed silently
        reason = _describe_error(exc, spec)
        log(f"[aux] {spec.repo_id}: FAILED - {reason}")
        return reason

    if still_missing := _missing_files(spec, local_dir):
        reason = f"download finished but files are missing or corrupt: {', '.join(still_missing)}"
        log(f"[aux] {spec.repo_id}: FAILED - {reason}")
        return reason

    log(f"[aux] {spec.repo_id}: OK ({_fmt_size(_dir_size(local_dir))})")
    return None


def download_aux_models(
    aux_root: Path,
    *,
    token: str | None,
    hf_home: Path | None = None,
    log: Callable[[str], None] = print,
    allow_birefnet_fallback: bool = True,
) -> AuxModelPaths:
    """Download every missing auxiliary model into ``aux_root`` and return the resolved paths.

    Idempotent: repos whose local copy is already complete are skipped without any network access. Only files
    needed for inference are fetched (configs, remote-code ``*.py``, ``*.safetensors`` weights); ONNX exports,
    ``pytorch_model.bin`` duplicates, docs and images are skipped.

    ``briaai/RMBG-2.0`` is tried first; if it cannot be fetched (e.g. license not accepted) and
    ``allow_birefnet_fallback`` is true, the MIT-licensed ``ZhengPeng7/BiRefNet`` is downloaded instead.
    DINOv3 has no fallback.

    Args:
        aux_root: Directory that holds the aux model folders (``backend/.model``).
        token: Hugging Face access token (required for the gated DINOv3 / RMBG-2.0 repos). Never logged.
        hf_home: Hugging Face home; its ``hub`` sub-folder is used as download cache. Callers that also want the
            Xet/module caches there must set the ``HF_HOME`` environment variable before importing this module.
        log: Progress sink (one line per message).
        allow_birefnet_fallback: Set to ``False`` to require RMBG-2.0 (no BiRefNet download).

    Raises:
        AuxModelMissingError: if a required model is still unavailable afterwards (with per-repo reasons).
    """
    aux_root = Path(aux_root)
    aux_root.mkdir(parents=True, exist_ok=True)
    token = token or None  # an empty string must not be sent as a credential
    cache_dir = Path(hf_home) / "hub" if hf_home is not None else None
    reasons: dict[str, str] = {}

    def fetch(spec: _RepoSpec) -> bool:
        reason = _fetch(spec, aux_root / spec.dirname, token=token, cache_dir=cache_dir, log=log)
        if reason:
            reasons[spec.repo_id] = reason
        return reason is None

    fetch(_DINOV3)

    if not fetch(_RMBG):
        birefnet_dir = aux_root / _BIREFNET.dirname
        if allow_birefnet_fallback:
            log(f"[aux] {RMBG_REPO} unavailable - falling back to {BIREFNET_REPO} (MIT)")
            fetch(_BIREFNET)
        elif not _missing_files(_BIREFNET, birefnet_dir):
            log(f"[aux] {RMBG_REPO} unavailable - an existing {BIREFNET_REPO} copy at {birefnet_dir} will be used")
        else:
            log(f"[aux] {RMBG_REPO} unavailable and the {BIREFNET_REPO} fallback is disabled")

    fetch(_SS_DECODER)

    return _resolve(aux_root, reasons)


def describe_aux_models(paths: AuxModelPaths) -> list[tuple[str, Path, int]]:
    """``(label, path, size_bytes)`` rows for a resolved :class:`AuxModelPaths` (used by the CLI summary)."""
    ss_files = [paths.ss_decoder_prefix.with_name(paths.ss_decoder_prefix.name + ext) for ext in (".json", ".safetensors")]
    rembg_repo = RMBG_REPO if paths.rembg_kind == REMBG_KIND_RMBG2 else BIREFNET_REPO
    return [
        (DINOV3_REPO, paths.dinov3_dir, _dir_size(paths.dinov3_dir)),
        (f"{rembg_repo} (rembg_kind={paths.rembg_kind})", paths.rembg_dir, _dir_size(paths.rembg_dir)),
        (f"{SS_DEC_REPO}/{SS_DEC_FILE}", paths.ss_decoder_prefix, sum(f.stat().st_size for f in ss_files if f.exists())),
    ]
