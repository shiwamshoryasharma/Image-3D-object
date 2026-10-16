"""Import boundaries of the part-aware layer (part-aware DESIGN.md section 1.4, invariants I6, I7, I10).

AST scans over ``backend/`` (no module is imported for the scans):

* I7 - ``api/``, ``services/``, ``pipelines/``, ``parts/``, ``processing/``, ``storage/``, ``models/``, ``schemas/``,
  ``core/`` and ``workers/`` never import torch, diffusers, transformers, nvdiffrast, cumesh or trellis2 (not even
  inside a function or through ``importlib.import_module("torch")``);
* rule 2 - inside ``inference/{providers,imagegen,segmentation,parts}/`` only the provider implementation modules
  import those packages, and only inside function bodies; ``inference/providers/vram.py`` may import torch inside
  functions; every other module there (the role ABCs, registry, catalog, ...) is torch-free;
* rules 1 and 3 - part-aware modules never import ``api`` (except the API layer itself), ``parts/`` never imports
  ``services``/``api``/``workers``, lower layers never import ``services``/``workers``, and code outside
  ``inference/`` reaches inference only through the torch-free modules;
* I10 - part-aware modules never import the paused tuner (``services.mesh_tuner_service``, ``workers.tuning_worker``,
  ``launcher``, ``blender_scripts``);
* no part-aware module touches ``sys.path`` (vendored code goes through ``inference/providers/vendor_import.py``).

A subprocess then imports every ``parts.*`` module and the other torch-free part-aware modules (and the whole app
through ``main``) and checks that no heavy package ended up in ``sys.modules``.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Final


BACKEND = Path(__file__).resolve().parents[2] / "backend"

HEAVY: Final = frozenset({"torch", "diffusers", "transformers", "nvdiffrast", "cumesh", "trellis2"})
TORCH_FREE_PACKAGES: Final = (
    "api",
    "services",
    "pipelines",
    "parts",
    "processing",
    "storage",
    "models",
    "schemas",
    "core",
    "workers",
)
NEW_INFERENCE_PACKAGES: Final = ("inference/providers", "inference/imagegen", "inference/segmentation", "inference/parts")
PROVIDER_IMPLEMENTATIONS: Final = frozenset(
    {
        "inference/imagegen/zimage.py",
        "inference/segmentation/gdino.py",
        "inference/segmentation/sam2.py",
        "inference/segmentation/florence2.py",
        "inference/segmentation/see_through.py",
        "inference/segmentation/sapiens2.py",
        "inference/parts/raster_lifter.py",
        "inference/parts/cameras.py",
    }
)
"""Section 1.4 rule 2: the only modules (besides ``inference/trellis/**``) that import the heavy packages."""
TORCH_IN_FUNCTIONS_ONLY: Final = frozenset({"inference/providers/vram.py"})
TUNER_MODULES: Final = ("services.mesh_tuner_service", "workers.tuning_worker", "launcher", "blender_scripts")
TORCH_FREE_INFERENCE: Final = frozenset(
    {
        "inference",
        "inference.base",
        "inference.device",
        "inference.imagegen",
        "inference.imagegen.base",
        "inference.segmentation",
        "inference.segmentation.base",
        "inference.parts",
        "inference.parts.base",
        "inference.providers",
        "inference.providers.base",
        "inference.providers.catalog",
        "inference.providers.registry",
        "inference.providers.unavailable",
        "inference.providers.vram",
        "inference.trellis.environment",  # configure_hf_environment (torch-free, read-only use, rule 4)
    }
)
"""What code outside ``inference/`` may import from it (section 1.4 rules 1 and 4)."""

PART_AWARE_DIRS: Final = (
    "parts",
    "pipelines/runs",
    "processing/seg2d",
    "inference/providers",
    "inference/imagegen",
    "inference/segmentation",
    "inference/parts",
)
PART_AWARE_FILES: Final = (
    "core/feature_errors.py",
    "core/part_aware_container.py",
    "api/part_routes.py",
    "api/part_deps.py",
    "api/v1/features.py",
    "api/v1/model_registry.py",
    "api/v1/image_generations.py",
    "api/v1/segmentations.py",
    "api/v1/part_analyses.py",
    "api/v1/workflows.py",
    "schemas/runs.py",
    "schemas/features.py",
    "schemas/model_registry.py",
    "schemas/image_generation.py",
    "schemas/segmentation.py",
    "schemas/parts.py",
    "schemas/workflows.py",
    "models/runs.py",
    "models/image_generation.py",
    "models/segmentation.py",
    "models/part_analysis.py",
    "models/workflow.py",
    "storage/run_store.py",
    "storage/run_layout.py",
    "storage/workflow_store.py",
    "workers/run_worker.py",
    "services/feature_service.py",
    "services/model_registry_service.py",
    "services/image_generation_service.py",
    "services/segmentation_service.py",
    "services/part_analysis_service.py",
    "services/part_files.py",
    "services/workflow_catalog.py",
    "services/workflow_compiler.py",
    "services/workflow_service.py",
    "services/run_events.py",
    "services/run_retention_service.py",
    "pipelines/image_generation.py",
    "pipelines/prompt_templates.py",
    "pipelines/segmentation.py",
    "pipelines/part_analysis.py",
    "processing/conditioning.py",
    "processing/imagegen_readiness.py",
)
"""Every module the milestone adds (section 1.1). Modules of later slices are scanned once they exist."""

S1_MODULES: Final = (
    "core/feature_errors.py",
    "core/part_aware_container.py",
    "api/part_routes.py",
    "api/part_deps.py",
    "schemas/runs.py",
    "models/runs.py",
    "storage/run_store.py",
    "storage/run_layout.py",
    "workers/run_worker.py",
    "services/run_events.py",
    "pipelines/runs/executor.py",
    "pipelines/runs/context.py",
    "pipelines/runs/reporter.py",
)
COMPOSITION_ROOT: Final = "core/part_aware_container.py"
"""Wires services and workers together (like ``core/container.py``); exempt from the lower-layer rule."""


@dataclass(frozen=True, slots=True)
class ImportRef:
    module: str
    """Absolute dotted module name (relative imports resolved)."""
    line: int
    in_function: bool
    dynamic: bool = False
    """``importlib.import_module("...")`` / ``__import__("...")`` with a literal name."""


def _rel(path: Path) -> str:
    return path.relative_to(BACKEND).as_posix()


def _module_name(rel: str) -> str:
    parts = rel.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _package_of(rel: str) -> str:
    name = _module_name(rel)
    return name if rel.endswith("__init__.py") else name.rpartition(".")[0]


def _py_files(directory: str) -> Iterator[Path]:
    root = BACKEND / directory
    if root.is_dir():
        yield from sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


@cache
def _tree(rel: str) -> ast.Module:
    return ast.parse((BACKEND / rel).read_text("utf-8"), filename=rel)


class _ImportCollector(ast.NodeVisitor):
    def __init__(self, rel: str) -> None:
        self._package = _package_of(rel)
        self._depth = 0
        self.imports: list[ImportRef] = []
        self.sys_path_lines: list[int] = []

    def _function(self, node: ast.AST) -> None:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    visit_FunctionDef = _function  # noqa: N815
    visit_AsyncFunctionDef = _function  # noqa: N815
    visit_Lambda = _function  # noqa: N815

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            self.imports.append(ImportRef(alias.name, node.lineno, self._depth > 0))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.level:
            base = self._package.split(".") if self._package else []
            if node.level > 1:
                base = base[: len(base) - (node.level - 1)]
            module = ".".join([*base, node.module] if node.module else base)
        else:
            module = node.module or ""
        self.imports.append(ImportRef(module, node.lineno, self._depth > 0))
        for alias in node.names:  # "from inference import providers" imports a submodule
            submodule = f"{module}.{alias.name}" if module else alias.name
            if _is_backend_module(submodule):
                self.imports.append(ImportRef(submodule, node.lineno, self._depth > 0))

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        if name in {"import_module", "__import__"} and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                self.imports.append(ImportRef(first.value, node.lineno, self._depth > 0, dynamic=True))
        if (
            isinstance(func, ast.Attribute)
            and func.attr in {"insert", "append", "extend", "remove", "clear"}
            and _is_sys_path(func.value)
        ):
            self.sys_path_lines.append(node.lineno)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        for target in node.targets:
            if _is_sys_path(target) or (isinstance(target, ast.Subscript) and _is_sys_path(target.value)):
                self.sys_path_lines.append(node.lineno)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
        if _is_sys_path(node.target):
            self.sys_path_lines.append(node.lineno)
        self.generic_visit(node)


@cache
def _is_backend_module(dotted: str) -> bool:
    """``True`` if ``dotted`` names a module or package of ``backend/`` (not an attribute of one)."""
    path = BACKEND.joinpath(*dotted.split("."))
    return path.with_suffix(".py").is_file() or (path / "__init__.py").is_file()


def _is_sys_path(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "path"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


@cache
def _collect(rel: str) -> _ImportCollector:
    collector = _ImportCollector(rel)
    collector.visit(_tree(rel))
    return collector


def _imports(rel: str) -> list[ImportRef]:
    return _collect(rel).imports


def _top(module: str) -> str:
    return module.split(".", 1)[0]


def _is_under(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _part_aware_modules() -> list[str]:
    found = {_rel(p) for directory in PART_AWARE_DIRS for p in _py_files(directory)}
    found |= {rel for rel in PART_AWARE_FILES if (BACKEND / rel).is_file()}
    return sorted(found)


def _fmt(violations: list[str]) -> str:
    return "\n  " + "\n  ".join(violations)


# --------------------------------------------------------------------------------------------------
# I7 and rule 2: heavy packages
# --------------------------------------------------------------------------------------------------


def test_the_scan_sees_the_s1_modules():
    missing = [rel for rel in S1_MODULES if not (BACKEND / rel).is_file()]
    assert not missing, f"S1 modules missing: {missing}"
    assert set(S1_MODULES) <= set(_part_aware_modules())


def test_torch_free_packages_never_import_heavy_packages():
    violations = [
        f"{_rel(path)}:{ref.line} imports {ref.module}"
        for package in TORCH_FREE_PACKAGES
        for path in _py_files(package)
        for ref in _imports(_rel(path))
        if _top(ref.module) in HEAVY
    ]
    assert not violations, "I7: torch-free packages import heavy packages:" + _fmt(violations)


def test_new_inference_modules_import_heavy_packages_only_lazily_and_only_where_allowed():
    violations: list[str] = []
    for directory in NEW_INFERENCE_PACKAGES:
        for path in _py_files(directory):
            rel = _rel(path)
            for ref in _imports(rel):
                top = _top(ref.module)
                if top not in HEAVY:
                    continue
                if rel in PROVIDER_IMPLEMENTATIONS and ref.in_function:
                    continue
                if rel in TORCH_IN_FUNCTIONS_ONLY and ref.in_function and top == "torch":
                    continue
                where = "inside a function" if ref.in_function else "at module level"
                violations.append(f"{rel}:{ref.line} imports {ref.module} {where}")
    assert not violations, "Section 1.4 rule 2 violated:" + _fmt(violations)


# --------------------------------------------------------------------------------------------------
# Rules 1 and 3: layering of the part-aware modules
# --------------------------------------------------------------------------------------------------


def test_part_aware_layering():
    violations: list[str] = []
    for rel in _part_aware_modules():
        package = rel.split("/", 1)[0]
        for ref in _imports(rel):
            module = ref.module
            if package != "api" and _is_under(module, "api"):
                violations.append(f"{rel}:{ref.line} imports {module} (only the API layer imports api)")
            if package == "parts" and any(_is_under(module, p) for p in ("services", "workers", "pipelines")):
                violations.append(f"{rel}:{ref.line} imports {module} (parts/ is a standalone library)")
            lower = package in {"pipelines", "parts", "processing", "storage", "models", "schemas"} or (
                package == "core" and rel != COMPOSITION_ROOT
            )
            if lower and any(_is_under(module, p) for p in ("services", "workers")):
                violations.append(f"{rel}:{ref.line} imports {module} (lower layers never import services/workers)")
            if package != "inference" and _is_under(module, "inference") and module not in TORCH_FREE_INFERENCE:
                violations.append(f"{rel}:{ref.line} imports {module} (outside inference/ only torch-free modules)")
    assert not violations, "Section 1.4 rules 1/3 violated:" + _fmt(violations)


def test_part_aware_modules_never_import_the_paused_tuner():
    violations = [
        f"{rel}:{ref.line} imports {ref.module}"
        for rel in _part_aware_modules()
        for ref in _imports(rel)
        if any(_is_under(ref.module, tuner) for tuner in TUNER_MODULES)
    ]
    assert not violations, "I10: part-aware code imports tuner modules:" + _fmt(violations)


def test_part_aware_modules_never_touch_sys_path():
    violations = [f"{rel}:{line}" for rel in _part_aware_modules() for line in _collect(rel).sys_path_lines]
    assert not violations, "Part-aware code modifies sys.path (use vendor_import.py):" + _fmt(violations)


# --------------------------------------------------------------------------------------------------
# The scanner itself (a broken scanner would pass everything)
# --------------------------------------------------------------------------------------------------


def test_the_scanner_detects_violations():
    source = (
        "import sys\n"
        "import importlib\n"
        "from torch import nn\n"
        "from . import executor\n"
        "def f():\n"
        "    import diffusers\n"
        "    importlib.import_module('transformers.models')\n"
        "sys.path.insert(0, 'x')\n"
        "sys.path[:] = []\n"
    )
    rel = "pipelines/runs/_scanner_probe.py"
    collector = _ImportCollector(rel)
    collector.visit(ast.parse(source))
    found = {(ref.module, ref.in_function, ref.dynamic) for ref in collector.imports}
    assert ("torch", False, False) in found
    assert ("pipelines.runs.executor", False, False) in found
    assert not any(module == "torch.nn" for module, _f, _d in found)  # attributes are not modules
    assert ("diffusers", True, False) in found
    assert ("transformers.models", True, True) in found
    assert collector.sys_path_lines == [8, 9]


# --------------------------------------------------------------------------------------------------
# Subprocess import: nothing heavy ends up in sys.modules
# --------------------------------------------------------------------------------------------------


def _torch_free_import_targets() -> list[str]:
    modules = {_module_name(_rel(p)) for p in _py_files("parts")}
    for directory in NEW_INFERENCE_PACKAGES:
        modules |= {_module_name(_rel(p)) for p in _py_files(directory) if _rel(p) not in PROVIDER_IMPLEMENTATIONS}
    modules |= {_module_name(_rel(p)) for p in _py_files("pipelines/runs")}
    modules |= {
        _module_name(rel)
        for rel in PART_AWARE_FILES
        if (BACKEND / rel).is_file() and not rel.startswith("inference/")
    }
    modules |= {"core.container", "main"}
    return sorted(modules)


_PROBE = r"""
import json, os, site, sys
user_site = os.path.normcase(os.path.abspath(site.getusersitepackages()))
sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p or ".")) != user_site]
sys.path.insert(0, sys.argv[1])
import importlib
heavy = set(json.loads(sys.argv[2]))
failures = {}
for name in json.loads(sys.argv[3]):
    try:
        importlib.import_module(name)
    except Exception as exc:
        failures[name] = f"{type(exc).__name__}: {exc}"
loaded = sorted(m for m in sys.modules if m.split(".")[0] in heavy)
print(json.dumps({"loaded": loaded, "failures": failures}))
"""


def test_importing_torch_free_modules_loads_no_heavy_package():
    targets = _torch_free_import_targets()
    assert "parts.taxonomy" in targets and "workers.run_worker" in targets
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-s", "-c", _PROBE, str(BACKEND), json.dumps(sorted(HEAVY)), json.dumps(targets)],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        cwd=str(BACKEND.parent / "test"),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["failures"] == {}, result["failures"]
    assert result["loaded"] == [], f"heavy packages imported by torch-free modules: {result['loaded']}"
