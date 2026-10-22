"""Vendored parsers (part-aware DESIGN.md sections 5.3 rule 5, 5.6; slice S6).

* ``VendoredPackage``: private namespace ``img3d_vendor.<name>``, absolute own imports rewritten with the same
  bindings, relative imports and namespace directories work, nothing written to disk, ``sys.path`` untouched,
  ``sys.modules["models"/"utils"]`` still Img3D's, dynamic own imports reported by the AST scan;
* the real submodules: source roots, top-level names, no dynamic own imports; importing the real model definitions
  (in a subprocess, so torch never enters this process) keeps ``sys.path`` and Img3D's ``models``/``utils``;
* the registry: both parsers ``installed`` on this machine, Sapiens2 conditions in ``ModelOut.license.conditions``;
* the parser post-processing (torch-free): taxonomy ids, character-relative sides, merged classes, soft PNGs.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from fakes import FakeEngine
from PIL import Image

import models as img3d_models
import utils as img3d_utils
from inference.providers import vendor_import
from inference.providers.catalog import CATALOG, SAPIENS2_SEG_0_4B, SEE_THROUGH_L2D
from inference.providers.registry import ProviderRegistry
from inference.providers.vendor_import import VendoredImportError, VendoredPackage
from inference.segmentation import sapiens2 as sapiens_mod
from inference.segmentation import see_through as see_mod
from parts.taxonomy import TAXONOMY

BACKEND = Path(__file__).resolve().parents[2] / "backend"
THIRD_PARTY = BACKEND / "third_party"
WEIGHTS = BACKEND / ".model" / "seg"
needs_submodules = pytest.mark.skipif(
    not (THIRD_PARTY / "see-through" / "common").is_dir() or not (THIRD_PARTY / "sapiens2" / "sapiens").is_dir(),
    reason="vendored submodules are not checked out",
)

FILES = {
    "src/models/__init__.py": "from utils.helpers import double\nVALUE = double(21)\n",
    "src/models/sibling.py": "NAME = 'sib'\n",
    "src/models/parser.py": (
        "import os, utils.helpers\n"
        "import utils as u\n"
        "import models.sibling\n"
        "from . import sibling\n"
        "def run():\n"
        "    from utils.helpers import double as dbl\n"
        "    return utils.helpers.double(2), u.helpers.double(3), sibling.NAME, models.sibling.NAME, dbl(5), os.sep\n"
    ),
    "src/utils/helpers.py": "def double(x):\n    return 2 * x\n",  # namespace-style package (no __init__.py)
    "src/dyn.py": (
        "import importlib\n"
        "importlib.import_module('utils.helpers')\n"
        "__import__('models')\n"
        "importlib.import_module('json')\n"
    ),
    "src/data/readme.txt": "not python\n",
}


@pytest.fixture
def fake_tree(runtime_dir: Path) -> Path:
    root = runtime_dir / "vendored"
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _unique() -> str:
    return f"s6test_{secrets.token_hex(4)}"


def test_private_namespace_keeps_bindings_and_touches_nothing(fake_tree: Path):
    path_before = list(sys.path)
    meta_before = [f for f in sys.meta_path if f is not vendor_import._FINDER]
    package = VendoredPackage(_unique(), fake_tree, source_dir="src")
    assert package.top_level_names() == {"models", "utils", "dyn"}
    parser = package.import_module("models.parser")
    assert parser.__name__ == f"img3d_vendor.{package.name}.models.parser"
    assert Path(parser.__file__).resolve() == (fake_tree / "src/models/parser.py").resolve()
    assert parser.run() == (4, 6, "sib", "sib", 10, os.sep)
    assert package.import_module("models").VALUE == 42  # package __init__ with a rewritten from-import
    helpers = sys.modules[f"img3d_vendor.{package.name}.utils.helpers"]
    assert helpers.double(4) == 8
    # Img3D's own top-level packages are untouched, sys.path too, and nothing was written next to the sources
    assert sys.modules["models"] is img3d_models and sys.modules["utils"] is img3d_utils
    for module in (img3d_models, img3d_utils):
        assert Path(module.__file__).resolve().is_relative_to(BACKEND.resolve())
    assert sys.path == path_before
    assert [f for f in sys.meta_path if f is not vendor_import._FINDER] == meta_before
    assert not list(fake_tree.rglob("__pycache__")) and not list(fake_tree.rglob("*.pyc"))
    assert all(name.startswith("img3d_vendor") for name in vendor_import.loaded_vendored_modules())


def test_scan_reports_dynamic_own_imports_only(fake_tree: Path):
    package = VendoredPackage(_unique(), fake_tree, source_dir="src")
    assert package.scan_dynamic_imports() == ["src/dyn.py:2", "src/dyn.py:3"]


def test_rewrite_leaves_foreign_and_relative_imports_alone():
    import ast

    tree = vendor_import.rewrite_source(
        "import os\nimport utils.a.b\nfrom utils import x\nfrom .utils import y\nfrom json import loads\n",
        "img3d_vendor.p",
        frozenset({"utils"}),
    )
    code = ast.unparse(tree)
    assert "import os" in code and "from json import loads" in code and "from .utils import y" in code
    assert "from img3d_vendor.p.utils import x" in code
    assert "import img3d_vendor.p.utils.a.b as _img3d_vendor_tmp_1" in code and "from img3d_vendor.p import utils" in code


def test_invalid_requests_are_refused(fake_tree: Path, runtime_dir: Path):
    package = VendoredPackage(_unique(), fake_tree, source_dir="src")
    for dotted in ("", ".models", "models..x", "json"):
        with pytest.raises(VendoredImportError):
            package.import_module(dotted)
    with pytest.raises(VendoredImportError):
        VendoredPackage("not-an-identifier", fake_tree)
    with pytest.raises(VendoredImportError):
        VendoredPackage(_unique(), runtime_dir / "missing", source_dir="src").import_module("models")


@needs_submodules
def test_real_submodules_have_static_imports_only():
    see = VendoredPackage("see_through", THIRD_PARTY / "see-through")
    sap = VendoredPackage("sapiens2", THIRD_PARTY / "sapiens2")
    assert see.source_root == (THIRD_PARTY / "see-through" / "common").resolve()
    assert sap.source_root == (THIRD_PARTY / "sapiens2" / "sapiens" / "backbones" / "standalone").resolve()
    assert see.top_level_names() == {"live2d", "modules", "utils"}
    assert sap.top_level_names() == {"sapiens", "sapiens2"}
    assert see.scan_dynamic_imports() == [] and sap.scan_dynamic_imports() == []


SUBPROCESS = r"""
import json, os, site, sys
user_site = os.path.normcase(os.path.abspath(site.getusersitepackages()))
sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p or ".")) != user_site]
sys.path.insert(0, sys.argv[1])
import models, utils
from pathlib import Path
before = list(sys.path)
from inference.providers.vendor_import import VendoredPackage
see = VendoredPackage("see_through", Path(sys.argv[1]) / "third_party" / "see-through")
sam = see.import_module("modules.semanticsam")
sap = VendoredPackage("sapiens2", Path(sys.argv[1]) / "third_party" / "sapiens2").import_module("sapiens2")
print(json.dumps({
    "sys_path_unchanged": sys.path == before,
    "models": models.__file__, "utils": utils.__file__,
    "sys_models": sys.modules["models"].__file__, "sys_utils": sys.modules["utils"].__file__,
    "classes": [sam.SemanticSam.__module__, sap.Sapiens2.__module__],
    "bare_vendored": sorted(n for n in ("modules", "live2d", "sapiens", "sapiens2") if n in sys.modules),
}))
"""


@needs_submodules
@pytest.mark.slow
def test_importing_the_real_model_definitions_keeps_img3d_packages():
    done = subprocess.run(
        [sys.executable, "-s", "-c", SUBPROCESS, str(BACKEND)], capture_output=True, text=True, timeout=300,
        cwd=str(BACKEND.parent / "test"),
    )
    assert done.returncode == 0, done.stderr[-2000:]
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["sys_path_unchanged"] is True
    assert out["sys_models"] == out["models"] and out["sys_utils"] == out["utils"]
    for key in ("models", "utils"):
        assert Path(out[key]).resolve().is_relative_to(BACKEND.resolve())
    assert out["classes"] == ["img3d_vendor.see_through.modules.semanticsam", "img3d_vendor.sapiens2.sapiens2"]
    assert out["bare_vendored"] == []
    for sub in ("see-through", "sapiens2"):
        assert not list((THIRD_PARTY / sub).rglob("__pycache__")), f"{sub}: bytecode written into the submodule"


@needs_submodules
@pytest.mark.skipif(
    not (WEIGHTS / "l2d_sam_iter2" / "checkpoint-18000.pt").is_file()
    or not (WEIGHTS / "sapiens2-seg-0.4b" / "model.safetensors").is_file(),
    reason="parser weights not installed",
)
def test_registry_reports_both_parsers_installed_with_the_sapiens2_conditions(make_settings):
    from services.model_registry_service import build_model_out

    heavy_before = {name for name in ("torch", "transformers") if name in sys.modules}
    settings = make_settings()
    registry = ProviderRegistry(settings, CATALOG, engine=FakeEngine(ready=False))
    for spec in (SEE_THROUGH_L2D, SAPIENS2_SEG_0_4B):
        status = registry.status(spec.id)
        assert (status.state, status.reason_code, status.reason) == ("installed", "ok", None)
    out = build_model_out(registry, settings, SAPIENS2_SEG_0_4B.id)
    assert list(out.license.conditions) == [
        "No biometric identification",
        "No deepfakes or impersonation",
        "No military use",
    ]
    assert out.vendored_source == "third_party/sapiens2"
    # constructing the providers imports the provider modules only (no torch, no vendored code yet)
    path_before = list(sys.path)
    providers = [registry.provider(spec.id) for spec in (SEE_THROUGH_L2D, SAPIENS2_SEG_0_4B)]
    assert [type(p).__name__ for p in providers] == ["SeeThroughProvider", "Sapiens2Provider"]
    assert sys.path == path_before
    assert {name for name in ("torch", "transformers") if name in sys.modules} == heavy_before
    assert sys.modules["models"] is img3d_models and sys.modules["utils"] is img3d_utils


def test_label_maps_target_taxonomy_ids():
    assert len(see_mod.CLASS_NAMES) == 19 == SEE_THROUGH_L2D.capabilities["classes"]
    assert len(sapiens_mod.CLASS_NAMES) == 29 == SAPIENS2_SEG_0_4B.capabilities["classes"]
    assert set(see_mod.LABEL_MAP) == set(see_mod.CLASS_NAMES)
    assert set(sapiens_mod.LABEL_MAP) == set(sapiens_mod.CLASS_NAMES) - {"Background", "Apparel"}
    for label_map in (see_mod.LABEL_MAP, sapiens_mod.LABEL_MAP):
        for target in label_map.values():
            if target in see_mod.SIDED_FAMILIES:
                assert f"{target}.left" in TAXONOMY and f"{target}.right" in TAXONOMY
            else:
                assert target in TAXONOMY, target
    assert see_mod.SeeThroughProvider.label_map is see_mod.LABEL_MAP
    assert sapiens_mod.Sapiens2Provider.label_map is sapiens_mod.LABEL_MAP


def _disc(shape: tuple[int, int], cx: int, cy: int, r: int) -> np.ndarray:
    yy, xx = np.mgrid[: shape[0], : shape[1]]
    return (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r


def test_masks_use_taxonomy_ids_and_character_relative_sides(runtime_dir: Path):
    shape = (100, 120)
    face = np.zeros(shape, bool)
    face[30:90, 30:90] = True
    eyes = _disc(shape, 45, 55, 5) | _disc(shape, 75, 55, 5) | _disc(shape, 10, 5, 0)  # + a 1 px speck
    ears = _disc(shape, 28, 60, 4) | _disc(shape, 50, 15, 6)  # side ear + an ear above the face (animal ear)
    planes = [
        ("face", np.where(face, 0.9, 0.1).astype(np.float32), face),
        ("eyes", np.where(eyes, 0.8, 0.0).astype(np.float32), eyes),
        ("ears", np.where(ears, 0.7, 0.0).astype(np.float32), ears),
        ("nose", np.zeros(shape, np.float32), np.zeros(shape, bool)),  # empty: omitted
        ("unmapped", np.ones(shape, np.float32), np.ones(shape, bool)),  # not in the label map: omitted
    ]
    out = runtime_dir / "parse"
    masks = see_mod.masks_from_planes(planes, see_mod.LABEL_MAP, out, source="see_through_l2d")
    by_id = {m.class_id: m for m in masks}
    assert set(by_id) == {"body.head.face", "eye.left", "eye.right", "body.head.ear.right", "appendage.animal_ears"}
    # image-right of the face centre (x = 60) is the character's left
    assert by_id["eye.left"].bbox[0] >= 70 and by_id["eye.right"].bbox[2] <= 51
    assert by_id["appendage.animal_ears"].bbox[3] <= 30
    for mask in masks:
        assert 0.0 <= mask.score <= 1.0 and mask.source == "see_through_l2d" and mask.path.parent == out
        png = np.asarray(Image.open(mask.path))
        assert png.dtype == np.uint8 and png.shape == shape
    assert by_id["eye.left"].score == pytest.approx(0.8, abs=0.01)
    assert len({m.instance for m in masks}) == len(masks)


def test_sapiens_classes_merge_into_one_taxonomy_mask(runtime_dir: Path):
    shape = (40, 40)
    upper = np.zeros(shape, bool)
    upper[0:20, 0:10] = True
    lower = np.zeros(shape, bool)
    lower[20:40, 0:10] = True
    planes = [
        ("Left_Upper_Arm", np.where(upper, 0.6, 0.05).astype(np.float32), upper),
        ("Left_Lower_Arm", np.where(lower, 0.7, 0.05).astype(np.float32), lower),
        ("Background", np.ones(shape, np.float32), ~(upper | lower)),
    ]
    masks = see_mod.masks_from_planes(planes, sapiens_mod.LABEL_MAP, runtime_dir / "p", source="sapiens2_seg_0_4b")
    assert [m.class_id for m in masks] == ["body.arm.left"]  # person-relative left == character left
    assert masks[0].bbox == (0, 0, 10, 40)
    assert masks[0].score == pytest.approx(0.7, abs=0.06)
