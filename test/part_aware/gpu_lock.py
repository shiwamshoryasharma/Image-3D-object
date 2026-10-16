"""Cross-process GPU lock for Img3D test runs (TEST-ONLY helper; not part of the app).

Several agents or terminals may implement and verify part-aware slices at the same time, but the RTX 5090 can only
hold one large model run at a time (Z-Image-Turbo peaks at 23.3 GB, TRELLIS.2 at ~17-24 GB). Every command that
loads real weights onto the GPU (smoke tests, probes, `test/engine` GPU tests, a real backend server used for E2E)
must run under this lock.

Usage (from the repository root):
    backend\\python\\python.exe -s test\\part_aware\\gpu_lock.py [--timeout 7200] [--label NAME] -- <command ...>

Examples:
    backend\\python\\python.exe -s test\\part_aware\\gpu_lock.py --label s5-smoke -- ^
        backend\\python\\python.exe -s -m pytest -c test\\pytest.ini test\\aux_models\\test_smoke_segmentation.py -q

    # inside Python
    from gpu_lock import gpu_lock
    with gpu_lock(label="probe"):
        ...

`--lock NAME` selects another machine-wide lock with the same semantics, e.g. `--lock frontend-build` serialises
`tsc -b` / `vite build` / `oxlint` runs of parallel frontend agents (they share tsbuildinfo and dist folders).

The lock is an OS byte-range lock (msvcrt.locking on Windows, fcntl.flock elsewhere) on
`test/.runtime/gpu.lock`, so it is released automatically when the holding process exits or crashes. The current
holder is written to `test/.runtime/gpu.lock.owner` for diagnostics only. Exit code = the wrapped command's exit code
(124 when the lock could not be acquired before the timeout).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

_TEST_ROOT = Path(__file__).resolve().parents[1]
LOCK_DIR = _TEST_ROOT / ".runtime"
LOCK_FILE = LOCK_DIR / "gpu.lock"
OWNER_FILE = LOCK_DIR / "gpu.lock.owner"
_LOCK_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-_")


def _lock_paths(name: str) -> tuple[Path, Path]:
    """`gpu` -> test/.runtime/gpu.lock; any other name (e.g. `frontend-build`) -> test/.runtime/<name>.lock."""
    if not name or set(name) - _LOCK_NAME_CHARS:
        raise ValueError(f"invalid lock name {name!r} (use a-z, 0-9, '-', '_')")
    return LOCK_DIR / f"{name}.lock", LOCK_DIR / f"{name}.lock.owner"

if os.name == "nt":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        with contextlib.suppress(OSError):
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:  # pragma: no cover - this project runs on Windows
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)


def _read_owner(owner_file: Path = OWNER_FILE) -> str:
    try:
        return owner_file.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


@contextlib.contextmanager
def gpu_lock(timeout_s: float = 7200.0, label: str = "", poll_s: float = 2.0, name: str = "gpu") -> Iterator[None]:
    """Hold the machine-wide lock `name` (default: the GPU) for the duration of the block. Raises TimeoutError."""
    lock_file, owner_file = _lock_paths(name)
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + timeout_s
        announced = False
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{name} lock busy for {timeout_s:.0f} s (holder: {_read_owner(owner_file)})")
            if not announced:
                print(f"[gpu_lock] waiting for {name} (holder: {_read_owner(owner_file)})", file=sys.stderr, flush=True)
                announced = True
            time.sleep(poll_s)
        owner = {"pid": os.getpid(), "label": label, "since": time.strftime("%Y-%m-%dT%H:%M:%S")}
        with contextlib.suppress(OSError):
            owner_file.write_text(json.dumps(owner), encoding="utf-8")
        print(f"[gpu_lock] {name} acquired ({label or 'unlabelled'})", file=sys.stderr, flush=True)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                owner_file.write_text("", encoding="utf-8")
            _unlock(fd)
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        print(__doc__, file=sys.stderr)
        return 2
    split = argv.index("--")
    parser = argparse.ArgumentParser(prog="gpu_lock.py")
    parser.add_argument("--timeout", type=float, default=7200.0, help="seconds to wait for the lock")
    parser.add_argument("--label", default="", help="free-text holder label shown to waiters")
    parser.add_argument("--lock", default="gpu", help="lock name: gpu (default) or e.g. frontend-build")
    opts = parser.parse_args(argv[:split])
    command = argv[split + 1 :]
    if not command:
        print("gpu_lock.py: no command given after --", file=sys.stderr)
        return 2
    # CreateProcess does not resolve relative paths written with forward slashes (git-bash style).
    exe = Path(command[0])
    if exe.exists():
        command[0] = str(exe.resolve())
    else:
        command[0] = shutil.which(command[0]) or command[0]
    try:
        with gpu_lock(timeout_s=opts.timeout, label=opts.label or " ".join(command)[:120], name=opts.lock):
            return subprocess.call(command)
    except TimeoutError as exc:
        print(f"[gpu_lock] {exc}", file=sys.stderr)
        return 124
    except ValueError as exc:
        print(f"[gpu_lock] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
