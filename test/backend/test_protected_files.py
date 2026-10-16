"""Protected-file guard for the image-to-3D path (part-aware milestone, DESIGN.md §0.4 I1 and §1.3).

The part-aware layer must leave the working image-to-3D generator and its outputs (``model.glb``, ``preview.png``,
``job.json``, ``JobOut``) exactly as they are. Every file on that path is pinned in
``test/backend/protected_files.sha256`` by the sha256 of its content after CRLF -> LF normalisation, so a checkout
with ``core.autocrlf`` does not matter. A mismatch names the drifted file and fails: changing a protected file needs
the user's approval, and the pin is then updated in the same commit.

Update the hash file deliberately, and only after the user approved the change::

    backend\\python\\python.exe -s test\\backend\\test_protected_files.py --check
    backend\\python\\python.exe -s test\\backend\\test_protected_files.py --add backend/workers/generation_worker.py
    backend\\python\\python.exe -s test\\backend\\test_protected_files.py --update backend/models/job.py
    backend\\python\\python.exe -s test\\backend\\test_protected_files.py --update

``--check`` (the default) only reports drift and exits 1 when a pin no longer matches. ``--add`` pins new files and
leaves every existing pin as it is (slice S1 adds ``backend/workers/generation_worker.py`` right after its GPU seam
edit, DESIGN.md §1.2 S-a). ``--update PATH ...`` re-pins only the named, already pinned files; ``--update`` without
paths re-pins every listed file (plus any missing baseline entry), so it must never be used to silence an unreviewed
drift.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HASH_FILE = Path(__file__).resolve().with_name("protected_files.sha256")
CLI = r"backend\python\python.exe -s test\backend\test_protected_files.py"

BASELINE_PROTECTED: tuple[str, ...] = (
    "backend/pipelines/image_to_3d.py",
    "backend/pipelines/progress.py",
    "backend/pipelines/base.py",
    "backend/pipelines/reference_strategy.py",
    "backend/inference/base.py",
    "backend/inference/registry.py",
    "backend/inference/unavailable.py",
    "backend/inference/device.py",
    "backend/inference/trellis/assets.py",
    "backend/inference/trellis/compat.py",
    "backend/inference/trellis/engine.py",
    "backend/inference/trellis/environment.py",
    "backend/inference/trellis/export.py",
    "backend/inference/trellis/generation.py",
    "backend/inference/trellis/imaging.py",
    "backend/inference/trellis/loader.py",
    "backend/models/job.py",
    "backend/models/enums.py",
    "backend/schemas/jobs.py",
    "backend/schemas/system.py",
    "backend/services/job_service.py",
    "backend/services/system_service.py",
    "backend/services/generation_settings.py",
    "backend/services/result_service.py",
    "backend/storage/job_store.py",
    "backend/storage/paths.py",
    "backend/storage/reference_store.py",
    "backend/processing/image_ops.py",
    "backend/processing/image_validation.py",
    "backend/processing/mesh_stats.py",
    "backend/workers/queue.py",
)
"""DESIGN.md §1.3 without ``backend/workers/generation_worker.py`` (pinned by S1 after the S-a seam edit)."""

_HEADER = (
    "# Img3D protected files (part-aware milestone, DESIGN.md §0.4 I1 and §1.3).",
    "# sha256 of each file after CRLF -> LF normalisation, then two spaces and the path relative to the repo root.",
    "# Changing a protected file requires the user's approval; the pin is then updated in the same commit with",
    f"#   {CLI} --update <path>",
    "# New files are pinned with --add <path>; --check reports drift.",
)
_LINE_RE = re.compile(r"^(?P<digest>[0-9a-f]{64}) {2}(?P<path>\S.*)$")


# --------------------------------------------------------------------------------------------------
# Hashing and the hash file
# --------------------------------------------------------------------------------------------------


def normalized_sha256(path: Path) -> str:
    """sha256 (hex) of the file content with every CRLF replaced by LF."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _check_relative_path(rel: str) -> str:
    """Validate a repo-relative POSIX path (no absolute paths, no ``..``, no backslashes)."""
    if not rel or "\\" in rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        raise ValueError(f"not a repo-relative POSIX path: {rel!r}")
    if any(part in ("", ".", "..") for part in rel.split("/")):
        raise ValueError(f"path must not contain empty, '.' or '..' segments: {rel!r}")
    return rel


def read_hash_file(path: Path = HASH_FILE) -> dict[str, str]:
    """Parse the hash file into ``{relative path: sha256}`` (file order kept); malformed lines raise ``ValueError``."""
    entries: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE_RE.match(line)
        if match is None:
            raise ValueError(f"{path.name}:{number}: expected '<sha256 hex>  <path>', got {raw!r}")
        rel = _check_relative_path(match.group("path"))
        if rel in entries:
            raise ValueError(f"{path.name}:{number}: duplicate entry {rel}")
        entries[rel] = match.group("digest")
    return entries


def write_hash_file(entries: Mapping[str, str], path: Path = HASH_FILE) -> None:
    """Write the hash file (header comments, then one ``<sha256>  <path>`` line per entry, LF line endings)."""
    lines = [*_HEADER, *(f"{digest}  {rel}" for rel, digest in entries.items())]
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


def compute_entries(paths: Iterable[str], root: Path = REPO_ROOT) -> dict[str, str]:
    """Hash every listed file (order kept). A missing file raises ``FileNotFoundError`` naming it."""
    entries: dict[str, str] = {}
    for rel in paths:
        file_path = root / _check_relative_path(rel)
        if not file_path.is_file():
            raise FileNotFoundError(f"protected file does not exist: {rel}")
        entries[rel] = normalized_sha256(file_path)
    return entries


def drift_message(rel: str, expected: str, actual: str | None) -> str:
    """Failure text for a drifted or missing protected file."""
    what = "is missing" if actual is None else f"has changed (sha256 {actual} != pinned {expected})"
    return (
        f"Protected file {rel} {what}. Files listed in DESIGN.md §1.3 keep the image-to-3D path and model.glb "
        "byte-identical (invariant I1); changing one needs the user's explicit approval. After that approval, "
        f"re-pin it in the same commit with: {CLI} --update {rel}"
    )


# --------------------------------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------------------------------


def _collection_params() -> list[Any]:
    """One test case per pinned file; a broken hash file becomes one failing case instead of a collection error."""
    try:
        entries = read_hash_file()
    except (OSError, ValueError) as exc:
        return [pytest.param("<hash file>", "", str(exc), id="hash-file-unreadable")]
    return [pytest.param(rel, digest, None, id=rel) for rel, digest in entries.items()]


def test_hash_file_covers_every_baseline_protected_file():
    entries = read_hash_file()
    missing = [rel for rel in BASELINE_PROTECTED if rel not in entries]
    assert not missing, (
        f"{HASH_FILE.name} no longer pins {missing}. Removing a protected file from the list needs the user's "
        "approval (DESIGN.md §1.3)."
    )
    assert all(rel.startswith("backend/") for rel in entries), "only backend files are protected"


@pytest.mark.parametrize(("rel", "expected", "load_error"), _collection_params())
def test_protected_file_is_unchanged(rel: str, expected: str, load_error: str | None):
    assert load_error is None, f"{HASH_FILE.name} cannot be read: {load_error}"
    path = REPO_ROOT / rel
    actual = normalized_sha256(path) if path.is_file() else None
    assert actual == expected, drift_message(rel, expected, actual)


def test_normalization_ignores_line_endings_only(tmp_runtime: Path):
    lf = tmp_runtime / "lf.py"
    crlf = tmp_runtime / "crlf.py"
    edited = tmp_runtime / "edited.py"
    lf.write_bytes(b'"""Doc."""\n\nVALUE = 1\n')
    crlf.write_bytes(b'"""Doc."""\r\n\r\nVALUE = 1\r\n')
    edited.write_bytes(b'"""Doc."""\n\nVALUE = 2\n')
    assert normalized_sha256(lf) == normalized_sha256(crlf)
    assert normalized_sha256(lf) != normalized_sha256(edited)
    assert normalized_sha256(lf) == hashlib.sha256(lf.read_bytes()).hexdigest()


def test_hash_file_parser_rejects_malformed_entries(tmp_runtime: Path):
    target = tmp_runtime / "pins.sha256"
    good = "ab" * 32
    for bad in (
        f"{good} backend/a.py",  # one space
        f"{good.upper()}  backend/a.py",  # upper-case hex
        f"{good}  ../outside.py",
        f"{good}  C:/abs.py",
        f"{good}  backend\\a.py",
        f"{good}  backend/a.py\n{good}  backend/a.py",  # duplicate
    ):
        target.write_text(bad + "\n", encoding="utf-8")
        with pytest.raises(ValueError):
            read_hash_file(target)


def test_cli_pins_deliberately_on_a_copy(tmp_runtime: Path):
    copy = tmp_runtime / "protected_files.sha256"
    stale = {rel: "0" * 64 for rel in BASELINE_PROTECTED[:3]}
    write_hash_file(stale, copy)

    def run(*args: str) -> int:
        return main([*args, "--hash-file", str(copy)])

    assert run("--check") == 1  # drift and unpinned baseline files are reported, nothing is written
    assert read_hash_file(copy) == stale

    # --add pins a new file and never re-pins an existing (drifted) entry; repeating it is a no-op.
    extra = "backend/models/types.py"
    assert run("--add", extra) == 0
    assert run("--add", extra) == 0
    pinned = read_hash_file(copy)
    assert list(pinned) == [*BASELINE_PROTECTED[:3], extra]
    assert {rel: pinned[rel] for rel in BASELINE_PROTECTED[:3]} == stale
    assert pinned[extra] == normalized_sha256(REPO_ROOT / extra)

    # --update PATH re-pins only the named file.
    assert run("--update", BASELINE_PROTECTED[0]) == 0
    pinned = read_hash_file(copy)
    assert pinned[BASELINE_PROTECTED[0]] == normalized_sha256(REPO_ROOT / BASELINE_PROTECTED[0])
    assert pinned[BASELINE_PROTECTED[1]] == "0" * 64

    # --update without paths re-pins everything and adds the missing baseline entries.
    assert run("--update") == 0
    pinned = read_hash_file(copy)
    assert list(pinned) == [*BASELINE_PROTECTED[:3], extra, *BASELINE_PROTECTED[3:]]
    assert pinned == compute_entries(pinned)
    assert run("--check") == 0
    assert copy.read_bytes().count(b"\r\n") == 0

    write_hash_file({**pinned, extra: "0" * 64}, copy)
    for bad in (
        ("--add", "../outside.py"),
        ("--add", "backend/does_not_exist.py"),
        ("--add", extra),  # already pinned with another digest: only --update may re-pin it
        ("--update", "backend/main.py"),  # not pinned yet: use --add
        ("--add", extra, "--update"),  # one mode per call
    ):
        with pytest.raises(SystemExit):
            run(*bad)
    assert read_hash_file(copy)[extra] == "0" * 64


# --------------------------------------------------------------------------------------------------
# CLI (deliberate regeneration; never run by the test suite against the real hash file)
# --------------------------------------------------------------------------------------------------


def _report(entries: Mapping[str, str]) -> list[str]:
    """Print one status line per pinned file; return the drifted paths."""
    drifted: list[str] = []
    for rel, expected in entries.items():
        path = REPO_ROOT / rel
        actual = normalized_sha256(path) if path.is_file() else None
        status = "ok     " if actual == expected else ("MISSING" if actual is None else "CHANGED")
        if actual != expected:
            drifted.append(rel)
        print(f"{status}  {rel}")
    return drifted


def _pin_new(current: Mapping[str, str], paths: Sequence[str]) -> dict[str, str]:
    """``--add``: append new pins; an existing pin is never changed (a differing digest is an error)."""
    entries = dict(current)
    for rel, digest in compute_entries(paths).items():
        if rel in entries and entries[rel] != digest:
            raise ValueError(
                f"{rel} is already pinned with another digest; re-pin it with --update {rel} after the user's approval"
            )
        entries[rel] = digest
    return entries


def _repin(current: Mapping[str, str], paths: Sequence[str]) -> dict[str, str]:
    """``--update``: re-pin the named pinned files, or every pin plus missing baseline entries when none is named."""
    if not paths:
        return compute_entries([*current, *(rel for rel in BASELINE_PROTECTED if rel not in current)])
    unknown = [rel for rel in paths if rel not in current]
    if unknown:
        raise ValueError(f"not pinned yet (use --add): {', '.join(unknown)}")
    return {**current, **compute_entries(paths)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="test_protected_files.py",
        description="Check or deliberately re-pin the protected image-to-3D files (DESIGN.md §1.3).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="report drift without writing (default)")
    mode.add_argument("--add", nargs="+", metavar="PATH", help="pin new repo-relative files; existing pins are kept")
    mode.add_argument(
        "--update",
        nargs="*",
        metavar="PATH",
        help="re-pin the named pinned files (every pin when no PATH is given); only after the user's approval",
    )
    parser.add_argument("--hash-file", type=Path, default=HASH_FILE, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    hash_file: Path = args.hash_file
    current = read_hash_file(hash_file) if hash_file.is_file() else {}
    if args.add is None and args.update is None:
        drifted = _report(current)
        missing = [rel for rel in BASELINE_PROTECTED if rel not in current]
        for rel in missing:
            print(f"UNPINNED  {rel}")
        return 1 if drifted or missing else 0

    try:
        entries = _pin_new(current, args.add) if args.add is not None else _repin(current, args.update)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    changed = [rel for rel, digest in entries.items() if current.get(rel) != digest]
    write_hash_file(entries, hash_file)
    print(f"Pinned {len(entries)} file(s) in {hash_file}; changed: {changed or 'none'}")
    if changed:
        print("Reminder: protected-file changes need the user's approval and belong in the same commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
