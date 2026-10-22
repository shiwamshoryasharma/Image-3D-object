"""Private-namespace importer for vendored model code (part-aware DESIGN.md section 5.6). Stdlib only, torch-free.

Img3D's own top-level packages have generic names (``models``, ``utils``, ``config``, ...) and come first on
``sys.path``; the vendored upstream trees (pinned git submodules under ``backend/third_party/``) import their own
top-level packages with the same generic names (See-through: ``from utils.torch_utils import ...``). Their sources
cannot be edited, and putting their roots on ``sys.path`` would bind the wrong packages either way. So:

* :class:`VendoredPackage` maps one vendored *source root* to the private namespace ``img3d_vendor.<name>``;
* one ``sys.meta_path`` finder (:class:`_VendorFinder`) answers **only** for ``img3d_vendor`` and its submodules and
  loads the files from the source root with an :class:`importlib.abc.SourceLoader` whose ``source_to_code`` applies
  an AST rewrite: every absolute ``import X...`` / ``from X... import ...`` whose ``X`` is one of the vendored
  top-level names becomes ``img3d_vendor.<name>.X...`` (relative imports are left alone);
* nothing is written to disk (the loader has no ``path_stats``/``set_data``, so no ``__pycache__``), ``sys.path`` is
  never touched, and ``sys.modules["utils"]`` / ``["models"]`` keep pointing under ``backend/``.

Source roots (relative to the submodule) are :data:`SOURCE_DIRS`: See-through's importable code lives in
``common/`` (``modules``, ``utils``, ``live2d``); Sapiens2 is used through its upstream standalone backbone
directory ``sapiens/backbones/standalone`` (pure torch). The full ``sapiens`` package is never imported: its
``__init__`` pulls in its training engine, whose packages (termcolor, rich, matplotlib, iopath, ...) are not
installed and are never installed for Img3D.

:meth:`VendoredPackage.scan_dynamic_imports` reports ``importlib.import_module("<own name>...")`` /
``__import__("<own name>...")`` calls with a constant own-name argument: such imports bypass the rewrite, so the
registry (rule 5 of section 5.3) reports the model ``incompatible`` when the list is not empty.
"""

from __future__ import annotations

import ast
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import ModuleType
from typing import Final

NAMESPACE: Final = "img3d_vendor"
"""Root of the private namespace; vendored modules live in ``sys.modules`` only under this prefix."""

SOURCE_DIRS: Final[dict[str, str]] = {
    "see_through": "common",
    "sapiens2": "sapiens/backbones/standalone",
}
"""Vendored package name -> importable source directory, relative to the submodule root."""

_DYNAMIC_IMPORT_FUNCS: Final = frozenset({"import_module", "__import__"})


class VendoredImportError(ImportError):
    """A vendored package is misconfigured (unknown name, missing source root, conflicting registration)."""


class VendoredPackage:
    """One vendored source tree, importable as ``img3d_vendor.<name>.*`` (never through ``sys.path``)."""

    def __init__(self, name: str, root: Path, *, source_dir: str | None = None) -> None:
        if not name.isidentifier():
            raise VendoredImportError(f"Vendored package name {name!r} is not an identifier")
        sub = source_dir if source_dir is not None else SOURCE_DIRS.get(name, ".")
        self.name = name
        self.root = Path(root)
        self.source_root = (self.root / sub).resolve()
        self.prefix = f"{NAMESPACE}.{name}"

    def __repr__(self) -> str:
        return f"VendoredPackage({self.name!r}, source_root={str(self.source_root)!r})"

    # ------------------------------------------------------------------ discovery
    def top_level_names(self) -> frozenset[str]:
        """Modules (``x.py``) and packages (directories holding Python files) directly under the source root."""
        names: set[str] = set()
        if not self.source_root.is_dir():
            return frozenset()
        for entry in self.source_root.iterdir():
            if entry.is_file() and entry.suffix == ".py" and entry.stem.isidentifier() and entry.stem != "__init__":
                names.add(entry.stem)
            elif entry.is_dir() and entry.name.isidentifier() and _holds_python(entry):
                names.add(entry.name)
        return frozenset(names)

    def scan_dynamic_imports(self) -> list[str]:
        """``"<file relative to the submodule>:<line>"`` of ``import_module``/``__import__`` calls on own names."""
        own = self.top_level_names()
        findings: list[str] = []
        for path in sorted(self.source_root.rglob("*.py")):
            try:
                tree = ast.parse(path.read_bytes(), filename=str(path))
            except (SyntaxError, ValueError, OSError):
                continue  # an unparsable file cannot be imported either; the import itself reports it
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                func = node.func
                called = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
                if called not in _DYNAMIC_IMPORT_FUNCS:
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    if first.value.split(".", 1)[0] in own:
                        findings.append(f"{_relative(path, self.root)}:{node.lineno}")
        return findings

    # ------------------------------------------------------------------ import
    def import_module(self, dotted: str) -> ModuleType:
        """``"modules.semanticsam"`` -> the module ``img3d_vendor.<name>.modules.semanticsam``."""
        if not dotted or dotted.startswith(".") or any(not part.isidentifier() for part in dotted.split(".")):
            raise VendoredImportError(f"Invalid vendored module name {dotted!r}")
        if dotted.split(".", 1)[0] not in self.top_level_names():
            raise VendoredImportError(f"{dotted!r} is not a top-level module of {self!r}")
        _FINDER.register(self)
        return importlib.import_module(f"{self.prefix}.{dotted}")

    def private_name(self, dotted: str) -> str:
        """The ``sys.modules`` key a vendored module gets."""
        return f"{self.prefix}.{dotted}"


# --------------------------------------------------------------------------------------------------
# AST rewrite
# --------------------------------------------------------------------------------------------------


class _RewriteOwnImports(ast.NodeTransformer):
    """Absolute imports of the vendored top-level names -> ``img3d_vendor.<name>.<...>`` (same bindings)."""

    def __init__(self, prefix: str, own: frozenset[str]) -> None:
        self.prefix = prefix
        self.own = own
        self._counter = 0

    def _is_own(self, dotted: str | None) -> bool:
        return bool(dotted) and dotted.split(".", 1)[0] in self.own  # type: ignore[union-attr]

    def visit_Import(self, node: ast.Import) -> ast.AST | list[ast.stmt]:
        if not any(self._is_own(alias.name) for alias in node.names):
            return node
        out: list[ast.stmt] = []
        for alias in node.names:
            if not self._is_own(alias.name):
                out.append(ast.Import(names=[alias]))
            elif alias.asname is not None:
                # import X.Y as Z  ->  import img3d_vendor.n.X.Y as Z
                out.append(ast.Import(names=[ast.alias(name=f"{self.prefix}.{alias.name}", asname=alias.asname)]))
            elif "." not in alias.name:
                # import X  ->  import img3d_vendor.n.X as X
                out.append(ast.Import(names=[ast.alias(name=f"{self.prefix}.{alias.name}", asname=alias.name)]))
            else:
                # import X.Y  ->  binds X with X.Y loaded:
                #   import img3d_vendor.n.X.Y as _tmp; from img3d_vendor.n import X; del _tmp
                self._counter += 1
                tmp = f"_img3d_vendor_tmp_{self._counter}"
                top = alias.name.split(".", 1)[0]
                out.append(ast.Import(names=[ast.alias(name=f"{self.prefix}.{alias.name}", asname=tmp)]))
                out.append(ast.ImportFrom(module=self.prefix, names=[ast.alias(name=top)], level=0))
                out.append(ast.Delete(targets=[ast.Name(id=tmp, ctx=ast.Del())]))
        for stmt in out:
            ast.copy_location(stmt, node)
        return out

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.AST:
        if node.level == 0 and self._is_own(node.module):
            node.module = f"{self.prefix}.{node.module}"
        return node


def rewrite_source(source: bytes | str, prefix: str, own: frozenset[str], filename: str = "<vendored>") -> ast.Module:
    """The module AST with own absolute imports rewritten (exposed for the tests)."""
    tree = ast.parse(source, filename=filename)
    tree = _RewriteOwnImports(prefix, own).visit(tree)
    return ast.fix_missing_locations(tree)


# --------------------------------------------------------------------------------------------------
# Finder and loaders (only for the private namespace)
# --------------------------------------------------------------------------------------------------


class _EmptyPackageLoader(importlib.abc.Loader):
    """Synthetic packages: ``img3d_vendor``, ``img3d_vendor.<name>`` and directories without ``__init__.py``."""

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        return None


class _VendoredSourceLoader(importlib.abc.SourceLoader):
    """Loads one vendored file, rewrites its own imports, never writes bytecode (no ``path_stats``/``set_data``)."""

    def __init__(self, fullname: str, path: Path, package: VendoredPackage, own: frozenset[str]) -> None:
        self._fullname = fullname
        self._path = path
        self._package = package
        self._own = own

    def get_filename(self, fullname: str) -> str:
        return str(self._path)

    def get_data(self, path: str) -> bytes:
        with open(path, "rb") as handle:
            return handle.read()

    def source_to_code(self, data: bytes, path: str, *, _optimize: int = -1) -> object:  # type: ignore[override]
        tree = rewrite_source(data, self._package.prefix, self._own, filename=path)
        return compile(tree, path, "exec", dont_inherit=True)


class _VendorFinder(importlib.abc.MetaPathFinder):
    """Resolves ``img3d_vendor[.<name>[.<dotted>]]`` from the registered source roots; ``None`` for anything else."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._packages: dict[str, VendoredPackage] = {}
        self._own: dict[str, frozenset[str]] = {}

    def register(self, package: VendoredPackage) -> None:
        if not package.source_root.is_dir():
            raise VendoredImportError(f"Vendored source root is missing: {package.source_root}")
        with self._lock:
            known = self._packages.get(package.name)
            if known is not None and known.source_root != package.source_root:
                raise VendoredImportError(
                    f"Vendored package {package.name!r} is already registered for {known.source_root}"
                )
            if known is None:
                self._packages[package.name] = package
                self._own[package.name] = package.top_level_names()
            if self not in sys.meta_path:
                sys.meta_path.insert(0, self)

    def registered(self) -> dict[str, Path]:
        with self._lock:
            return {name: pkg.source_root for name, pkg in self._packages.items()}

    def find_spec(
        self, fullname: str, path: Sequence[str] | None = None, target: ModuleType | None = None
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname != NAMESPACE and not fullname.startswith(NAMESPACE + "."):
            return None
        parts = fullname.split(".")
        if len(parts) == 1:
            return _package_spec(fullname, [])
        with self._lock:
            package = self._packages.get(parts[1])
            own = self._own.get(parts[1], frozenset())
        if package is None:
            return None
        if len(parts) == 2:
            return _package_spec(fullname, [str(package.source_root)])
        if parts[2] not in own:
            return None
        location = package.source_root.joinpath(*parts[2:])
        init = location / "__init__.py"
        module_file = location.with_suffix(".py")
        if init.is_file():
            loader = _VendoredSourceLoader(fullname, init, package, own)
            spec = importlib.util.spec_from_file_location(
                fullname, init, loader=loader, submodule_search_locations=[str(location)]
            )
            return spec
        if module_file.is_file():
            loader = _VendoredSourceLoader(fullname, module_file, package, own)
            return importlib.util.spec_from_file_location(fullname, module_file, loader=loader)
        if location.is_dir() and _holds_python(location):
            return _package_spec(fullname, [str(location)])  # namespace-style directory without __init__.py
        return None


def _package_spec(fullname: str, locations: list[str]) -> importlib.machinery.ModuleSpec:
    spec = importlib.machinery.ModuleSpec(fullname, _EmptyPackageLoader(), is_package=True)
    spec.submodule_search_locations = locations
    return spec


def _holds_python(directory: Path) -> bool:
    try:
        return any(child.suffix == ".py" for child in directory.iterdir() if child.is_file())
    except OSError:
        return False


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def loaded_vendored_modules() -> Iterator[str]:
    """Names of the vendored modules currently in ``sys.modules`` (for the tests and diagnostics)."""
    return (name for name in list(sys.modules) if name == NAMESPACE or name.startswith(NAMESPACE + "."))


_FINDER: Final = _VendorFinder()
