"""Unit tests for ``backend/inference/trellis/assets.py`` (no network, no GPU).

Run::

    E:\\14-OCT-2026\\Img3D\\backend\\python\\python.exe -s -m pytest -p no:cacheprovider E:\\14-OCT-2026\\Img3D\\test\\aux_models\\test_assets.py

The fake model folders written here are test doubles (tiny but structurally valid files) and only ever live in a
temporary directory.
"""

from __future__ import annotations

import json
import os
import site
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
_user_site = os.path.normcase(os.path.abspath(site.getusersitepackages()))
sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p)) != _user_site]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from huggingface_hub.errors import GatedRepoError  # noqa: E402

from inference.trellis import assets  # noqa: E402
from inference.trellis.assets import (  # noqa: E402
    BIREFNET_REPO,
    DINOV3_REPO,
    RMBG_REPO,
    SS_DEC_REPO,
    AuxModelMissingError,
    AuxModelPaths,
    download_aux_models,
    resolve_aux_models,
)

_AUTO_MAP_CONFIG = {
    "auto_map": {
        "AutoConfig": "BiRefNet_config.BiRefNetConfig",
        "AutoModelForImageSegmentation": "birefnet.BiRefNet",
    }
}


def _write_safetensors(path: Path, *, truncate: int = 0) -> None:
    """Write a minimal valid safetensors file (one float32 tensor of 4 elements)."""
    header = json.dumps({"w": {"dtype": "F32", "shape": [4], "data_offsets": [0, 16]}}).encode()
    blob = struct.pack("<Q", len(header)) + header + b"\x00" * 16
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob[: len(blob) - truncate] if truncate else blob)


def _make_dinov3(root: Path) -> None:
    d = root / "dinov3-vitl16-pretrain-lvd1689m"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps({"model_type": "dinov3_vit"}), encoding="utf-8")
    _write_safetensors(d / "model.safetensors")


def _make_birefnet_like(root: Path, dirname: str, *, with_code: bool = True) -> None:
    d = root / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(_AUTO_MAP_CONFIG), encoding="utf-8")
    _write_safetensors(d / "model.safetensors")
    if with_code:
        (d / "birefnet.py").write_text("# remote code\n", encoding="utf-8")
        (d / "BiRefNet_config.py").write_text("# remote config\n", encoding="utf-8")


def _make_ss_decoder(root: Path) -> None:
    d = root / "TRELLIS-image-large" / "ckpts"
    d.mkdir(parents=True, exist_ok=True)
    (d / "ss_dec_conv3d_16l8_fp16.json").write_text(json.dumps({"name": "SparseStructureDecoder"}), encoding="utf-8")
    _write_safetensors(d / "ss_dec_conv3d_16l8_fp16.safetensors")


def _make_all(root: Path, *, rmbg: bool = True, birefnet: bool = False) -> None:
    _make_dinov3(root)
    if rmbg:
        _make_birefnet_like(root, "RMBG-2.0")
    if birefnet:
        _make_birefnet_like(root, "BiRefNet")
    _make_ss_decoder(root)


class ResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="img3d_aux_")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_empty_root_reports_all_missing_with_actionable_message(self) -> None:
        with self.assertRaises(AuxModelMissingError) as ctx:
            resolve_aux_models(self.root)
        err = ctx.exception
        self.assertEqual(err.missing, [DINOV3_REPO, RMBG_REPO, SS_DEC_REPO])
        msg = str(err)
        self.assertIn(f"https://huggingface.co/{DINOV3_REPO}", msg)
        self.assertIn(f"https://huggingface.co/{RMBG_REPO}", msg)
        self.assertIn(BIREFNET_REPO, msg)
        self.assertIn("download_aux_models.py", msg)

    def test_complete_root_prefers_rmbg2(self) -> None:
        _make_all(self.root, rmbg=True, birefnet=True)
        paths = resolve_aux_models(self.root)
        self.assertIsInstance(paths, AuxModelPaths)
        self.assertEqual(paths.rembg_kind, "rmbg2")
        self.assertEqual(paths.rembg_dir, self.root / "RMBG-2.0")
        self.assertEqual(paths.dinov3_dir, self.root / "dinov3-vitl16-pretrain-lvd1689m")
        self.assertEqual(paths.ss_decoder_prefix, self.root / "TRELLIS-image-large" / "ckpts" / "ss_dec_conv3d_16l8_fp16")
        # trellis2.models.from_pretrained appends the extensions itself.
        self.assertTrue(Path(f"{paths.ss_decoder_prefix}.json").is_file())
        self.assertTrue(Path(f"{paths.ss_decoder_prefix}.safetensors").is_file())

    def test_falls_back_to_birefnet(self) -> None:
        _make_all(self.root, rmbg=False, birefnet=True)
        paths = resolve_aux_models(self.root)
        self.assertEqual(paths.rembg_kind, "birefnet")
        self.assertEqual(paths.rembg_dir, self.root / "BiRefNet")

    def test_truncated_safetensors_is_incomplete(self) -> None:
        _make_all(self.root)
        _write_safetensors(self.root / "dinov3-vitl16-pretrain-lvd1689m" / "model.safetensors", truncate=5)
        with self.assertRaises(AuxModelMissingError) as ctx:
            resolve_aux_models(self.root)
        self.assertEqual(ctx.exception.missing, [DINOV3_REPO])
        self.assertIn("model.safetensors", str(ctx.exception))

    def test_missing_remote_code_is_incomplete(self) -> None:
        _make_all(self.root, rmbg=False)
        _make_birefnet_like(self.root, "RMBG-2.0", with_code=False)
        with self.assertRaises(AuxModelMissingError) as ctx:
            resolve_aux_models(self.root)
        self.assertEqual(ctx.exception.missing, [RMBG_REPO])
        self.assertIn("birefnet.py", str(ctx.exception))


class DownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="img3d_aux_dl_")
        self.root = Path(self._tmp.name)
        self.logs: list[str] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _no_network(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted for an already-complete model")

    def test_complete_root_is_skipped_without_network(self) -> None:
        _make_all(self.root)
        with (
            mock.patch.object(assets, "snapshot_download", side_effect=self._no_network),
            mock.patch.object(assets, "hf_hub_download", side_effect=self._no_network),
        ):
            paths = download_aux_models(self.root, token=None, log=self.logs.append)
        self.assertEqual(paths.rembg_kind, "rmbg2")
        self.assertEqual(len(self.logs), 3)
        self.assertTrue(all("already complete" in line and "skipping" in line for line in self.logs))

    def _fake_snapshot(self, *, gated: set[str]):
        def fake(*, repo_id: str, local_dir: str, **_: object) -> str:
            if repo_id in gated:
                raise GatedRepoError(f"gated {repo_id}", response=None)
            root = Path(local_dir).parent
            if repo_id == DINOV3_REPO:
                _make_dinov3(root)
            elif repo_id in (RMBG_REPO, BIREFNET_REPO):
                _make_birefnet_like(root, Path(local_dir).name)
            return local_dir

        return fake

    def _fake_file(self, *, repo_id: str, filename: str, local_dir: str, **_: object) -> str:
        _make_ss_decoder(Path(local_dir).parent)
        return str(Path(local_dir) / filename)

    def test_gated_rmbg_falls_back_to_birefnet(self) -> None:
        _make_dinov3(self.root)
        with (
            mock.patch.object(assets, "snapshot_download", side_effect=self._fake_snapshot(gated={RMBG_REPO})),
            mock.patch.object(assets, "hf_hub_download", side_effect=self._fake_file),
        ):
            paths = download_aux_models(self.root, token="x", log=self.logs.append)
        self.assertEqual(paths.rembg_kind, "birefnet")
        self.assertTrue(any("falling back" in line for line in self.logs))

    def test_gated_rmbg_without_fallback_raises_with_reason(self) -> None:
        _make_dinov3(self.root)
        with (
            mock.patch.object(assets, "snapshot_download", side_effect=self._fake_snapshot(gated={RMBG_REPO})),
            mock.patch.object(assets, "hf_hub_download", side_effect=self._fake_file),
            self.assertRaises(AuxModelMissingError) as ctx,
        ):
            download_aux_models(self.root, token="x", log=self.logs.append, allow_birefnet_fallback=False)
        self.assertEqual(ctx.exception.missing, [RMBG_REPO])
        self.assertIn("access denied", ctx.exception.reasons[RMBG_REPO])
        self.assertFalse((self.root / "BiRefNet").exists())

    def test_gated_dinov3_is_reported(self) -> None:
        with (
            mock.patch.object(assets, "snapshot_download", side_effect=self._fake_snapshot(gated={DINOV3_REPO})),
            mock.patch.object(assets, "hf_hub_download", side_effect=self._fake_file),
            self.assertRaises(AuxModelMissingError) as ctx,
        ):
            download_aux_models(self.root, token="x", log=self.logs.append)
        self.assertEqual(ctx.exception.missing, [DINOV3_REPO])
        self.assertIn(f"https://huggingface.co/{DINOV3_REPO}", str(ctx.exception))
        self.assertIn("download failed: access denied", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
