# Local patches to vendored third-party sources

The vendored sources under `backend/third_party` are kept at their upstream commits. A few **build-only**
patches are needed to compile them on this machine's toolchain:

- Windows 11, MSVC **14.51** (Visual Studio 2026, default toolset)
- nvcc **13.4** (`backend/.toolchain/cuda/nvidia/cu13`, assembled from NVIDIA pip wheels)
- torch **2.14.1+cu130**, whose `torch.utils.cpp_extension` compiles every extension with
  `-std=c++20` (nvcc) and `/std:c++20` (cl)

No patch changes runtime behaviour or numerics. Every patch is stored as a git diff in
[`patches/`](patches/) and is (re)applied idempotently by `backend/scripts/build_extensions.cmd`
(`git apply --reverse --check` detects an applied patch; otherwise `git apply --check` + `git apply`).
To re-vendor a source tree, check out the upstream commit listed below and run the build script.

| patch file | source tree (git repo root) | upstream commit |
|---|---|---|
| `patches/cumesh.patch` | `extensions/CuMesh` | `12289e1` (JeffreyXiang/CuMesh) |
| `patches/flex_gemm.patch` | `extensions/FlexGEMM` | `6dd94a8` (JeffreyXiang/FlexGEMM) |
| `patches/o_voxel.patch` | `TRELLIS.2` (only `o-voxel/`) | `75fbf01` (microsoft/TRELLIS.2) |
| *(none)* | `extensions/nvdiffrast` | `253ac4f` (NVlabs/nvdiffrast v0.4.0) - builds unmodified |

---------------------------------------------------------------------------------------------------

## 1. C++17 -> C++20 in `setup.py` (CuMesh, FlexGEMM, o-voxel)

**Files**
- `extensions/CuMesh/setup.py` - `/std:c++17`, `-std=c++17`, `-Xcompiler=/std:c++17` (Windows) and
  `-std=c++17` (Linux branch) -> `c++20`
- `extensions/FlexGEMM/setup.py` - same flags in the Windows and Linux `extra_compile_args` -> `c++20`
- `TRELLIS.2/o-voxel/setup.py` - `"-std=c++17"` in the `cxx` and `nvcc` arg lists -> `"-std=c++20"`

**Why.** torch 2.14's `BuildExtension` prepends `-std=c++20` to the nvcc command line and only adds
`/std:c++20` for cl when no `/std:` flag is present. The hard-coded C++17 flags come *after* torch's, so:
- nvcc: `incompatible redefinition for option 'std', the last value of this option was used` -> the CUDA
  sources would be compiled as C++17 (verified with nvcc 13.4);
- `-Xcompiler=/std:c++17` makes cl override `/std:c++20` (`D9025`) for the host side of `.cu` files;
- `/std:c++17` for `.cpp` files suppresses torch's `/std:c++20`.

torch 2.14 headers require C++20, so the flags are switched to C++20 (linux branches too, for
consistency; a no-op on this machine).

## 2. MSVC compile errors in o-voxel C++ sources

MSVC (`/std:c++20` implies `/permissive-`) rejects two GCC-isms that upstream compiles fine on Linux.

**a) Non-standard `d` floating literal suffix** - `error C3688: invalid literal suffix 'd'`
- `TRELLIS.2/o-voxel/src/convert/flexible_dual_grid.cpp:307` `1e-6d` -> `1e-6`
- `TRELLIS.2/o-voxel/src/convert/flexible_dual_grid.cpp:337` `0.0d`  -> `0.0`

An unsuffixed floating literal is already `double`, so the values and types are unchanged.

**b) Narrowing `size_t` -> `int64_t` in brace-initialised tensor sizes** -
`error C2398: conversion from 'size_t' to '_Ty' requires a narrowing conversion`
- `TRELLIS.2/o-voxel/src/io/filter_neighbor.cpp:80, 166` `torch::zeros({N, C}, ...)`
- `TRELLIS.2/o-voxel/src/io/filter_parent.cpp:83, 154` `torch::zeros({N_leaf, C}, ...)`
- `TRELLIS.2/o-voxel/src/io/svo.cpp:74, 136` `torch::from_blob(ptr, {vec.size()}, ...)`

Each size is wrapped in `static_cast<int64_t>(...)` (the element type of `at::IntArrayRef`).
Sizes are far below 2^63, so the result is identical.

---------------------------------------------------------------------------------------------------

## Build notes (not patches)

- All four extensions are built in-tree (`build/` and `*.egg-info` inside each source tree; ignored by the
  upstream `.gitignore` of TRELLIS.2, CuMesh and FlexGEMM, untracked in nvdiffrast which has none) with
  `pip install --no-build-isolation --no-deps`, so re-runs are incremental (ninja). Deleting a `build/`
  directory forces a clean rebuild of that extension.
- Remaining compiler warnings are upstream and harmless: `C4996` (deprecated torch APIs),
  `#3189-D` ("module" keyword under C++20), `#20208-D` (`long double` treated as `double` in device code),
  `D9025` in cubvh (it deliberately `-U`ndefines torch's `__CUDA_NO_HALF_*` macros), and `D9002` for the
  GCC-style `-O3`/`-std=c++20` in o-voxel's `cxx` args (cl ignores them; setuptools already passes `/O2`).
- FlexGEMM's `setup.py` writes/merges its Triton autotune cache into `%USERPROFILE%\.flex_gemm\autotune_cache.json`
  (upstream behaviour). At runtime the path can be redirected with `FLEX_GEMM_AUTOTUNE_CACHE_PATH`.
- `nvdiffrast` 0.4.0 builds unmodified.
