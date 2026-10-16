"""Download or verify the auxiliary Hugging Face models required by TRELLIS.2-4B's ``pipeline.json``.

Models (stored under ``backend/.model`` so that the app runs offline):

* ``facebook/dinov3-vitl16-pretrain-lvd1689m``  (gated, required)
* ``briaai/RMBG-2.0``                            (gated; falls back to MIT ``ZhengPeng7/BiRefNet``)
* ``microsoft/TRELLIS-image-large`` ``ckpts/ss_dec_conv3d_16l8_fp16.{json,safetensors}``

The Hugging Face token is read from ``backend/.env`` (``HF_TOKEN=...``) and is never printed.

Usage (from the project root)::

    backend\\python\\python.exe backend\\scripts\\download_aux_models.py               # download what is missing
    backend\\python\\python.exe backend\\scripts\\download_aux_models.py --check       # verify only (no network)
    backend\\python\\python.exe backend\\scripts\\download_aux_models.py --no-birefnet-fallback

Exit codes: 0 = all models available, 1 = models missing (actionable message printed), 2 = bad usage.
"""

from __future__ import annotations

import argparse
import os
import site
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = BACKEND_DIR / ".env"
DEFAULT_AUX_ROOT = BACKEND_DIR / ".model"
DEFAULT_HF_HOME = DEFAULT_AUX_ROOT / ".hf_cache"


def _bootstrap_sys_path() -> None:
    """Make ``backend/`` importable and keep the interpreter self-contained.

    The embedded interpreter runs in isolated mode, so neither the script directory nor ``PYTHONPATH`` is on
    ``sys.path``. Its ``._pth`` file still enables the per-user site-packages directory
    (``%APPDATA%\\Python\\Python314\\site-packages``), which is shared with any other CPython 3.14 on the machine
    and can shadow the embedded packages with incompatible versions; it is removed here.
    """
    try:
        user_site = os.path.normcase(os.path.abspath(site.getusersitepackages()))
    except Exception:  # noqa: BLE001 - site may be minimal in embedded builds
        user_site = None
    if user_site:
        sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p)) != user_site]
    backend = str(BACKEND_DIR)
    if backend not in sys.path:
        sys.path.insert(0, backend)


def _load_env_values() -> dict[str, str]:
    """Values from ``backend/.env`` (empty dict if the file does not exist). Values are never printed."""
    from dotenv import dotenv_values

    if not ENV_FILE.is_file():
        return {}
    return {k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None}


def _resolve_dir(value: str | None, default: Path) -> Path:
    if not value:
        return default
    path = Path(value).expanduser()
    return path if path.is_absolute() else (BACKEND_DIR / path).resolve()


def _format_size(num_bytes: int) -> str:
    gib = num_bytes / (1024**3)
    if gib >= 1:
        return f"{gib:.2f} GB"
    return f"{num_bytes / (1024**2):.1f} MB"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="download_aux_models.py",
        description="Download/verify the auxiliary models (DINOv3, RMBG-2.0/BiRefNet, TRELLIS v1 SS decoder).",
    )
    parser.add_argument(
        "--aux-root",
        type=str,
        default=None,
        help="target directory (default: IMG3D_AUX_MODEL_ROOT from backend/.env or env, else backend/.model)",
    )
    parser.add_argument(
        "--no-birefnet-fallback",
        action="store_true",
        help="do not download ZhengPeng7/BiRefNet when briaai/RMBG-2.0 is not accessible",
    )
    parser.add_argument("--check", action="store_true", help="only verify the local copies (no network access)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _bootstrap_sys_path()

    env_values = _load_env_values()
    aux_root = _resolve_dir(
        args.aux_root or env_values.get("IMG3D_AUX_MODEL_ROOT") or os.environ.get("IMG3D_AUX_MODEL_ROOT"),
        DEFAULT_AUX_ROOT,
    )
    hf_home = _resolve_dir(env_values.get("IMG3D_HF_HOME") or os.environ.get("IMG3D_HF_HOME"), DEFAULT_HF_HOME)

    # Must be set before huggingface_hub is imported (it reads HF_HOME at import time).
    os.environ["HF_HOME"] = str(hf_home)
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    from inference.trellis.assets import (
        AuxModelMissingError,
        describe_aux_models,
        download_aux_models,
        resolve_aux_models,
    )

    token = (env_values.get("HF_TOKEN") or os.environ.get("HF_TOKEN") or "").strip() or None

    print(f"aux root : {aux_root}")
    print(f"HF_HOME  : {hf_home}")
    print(f"HF token : {'provided' if token else 'NOT SET (gated repos will fail)'}")
    print(f"mode     : {'check (local only)' if args.check else 'download missing'}")
    print()

    try:
        if args.check:
            paths = resolve_aux_models(aux_root)
        else:
            paths = download_aux_models(
                aux_root,
                token=token,
                hf_home=hf_home,
                log=lambda msg: print(msg, flush=True),
                allow_birefnet_fallback=not args.no_birefnet_fallback,
            )
    except AuxModelMissingError as exc:
        print()
        print(exc.message, file=sys.stderr)
        return 1

    print()
    print("Auxiliary models ready:")
    for label, path, size in describe_aux_models(paths):
        print(f"  {label}")
        print(f"      {path}  ({_format_size(size)})")
    print(f"  rembg_kind = {paths.rembg_kind}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
