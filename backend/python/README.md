# `backend/python/` — embedded Python runtime (not in Git)

> Only this README is tracked. The interpreter and its packages (~4.4 GB) stay on the local machine.

## What lives here

A private, self-contained **CPython 3.14.2 (64-bit, Windows)** used exclusively by the Img3D backend —
no system Python, venv or conda is ever involved.

| Path | Contents |
|---|---|
| `python.exe`, `python314.dll`, `python314.zip`, `DLLs/` | CPython 3.14.2 runtime from [python.org](https://www.python.org/downloads/windows/) |
| `python314._pth` | Makes the interpreter **isolated** (ignores `PYTHONPATH`/`PYTHONHOME`) and enables `site` |
| `include/`, `libs/` | CPython headers + import library — required to compile the native CUDA extensions |
| `Lib/site-packages/` | Packages pinned in [`backend/requirements.txt`](../requirements.txt) + the locally built extensions (`o_voxel`, `cumesh`, `flex_gemm`, `nvdiffrast`) |
| `LICENSE.txt` | The PSF license shipped with CPython |

`python314._pth` content:

```text
import site
tcl
Scripts
Lib
libs
include
Doc
DLLs
.
python314.zip
```

## How to recreate it

1. Put a CPython **3.14.2 x64** layout that includes `include/` and `libs/` into this folder (for example the
   python.org NuGet package `python` 3.14.2, `tools/` folder, or a copy of a full python.org install).
2. Add the `python314._pth` above.
3. Bootstrap pip/uv once, then install the pinned stack **into this interpreter only**:

   ```powershell
   $py = ".\backend\python\python.exe"
   & $py -m ensurepip
   & $py -s -m pip install uv
   & $py -s -m uv pip install --python $py --link-mode=copy -r backend\requirements.txt
   ```

4. Build the native extensions: `cmd /c backend\scripts\build_extensions.cmd` (see
   [`backend/third_party/README.md`](../third_party/README.md)).

Always start it with `-s` (`python.exe -s ...`): the per-user site-packages folder would otherwise shadow the
pinned packages. `backend/main.py` and every script strip it automatically.

## Licenses I follow

- **CPython** — [Python Software Foundation License Version 2](https://docs.python.org/3/license.html)
  (`LICENSE.txt` in this folder). Used unmodified, for local execution only; not redistributed by this repository.
- **Installed packages** — each keeps its own license (MIT, BSD, Apache-2.0, LGPL-3.0, GPL-3.0+, MPL-2.0 …).
  They are installed from PyPI / the PyTorch index by the user and are **not** redistributed here.
  The full list with versions, licenses and links is in [`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md).
