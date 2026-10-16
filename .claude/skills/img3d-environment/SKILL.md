---
name: img3d-environment
description: How the Img3D backend Python environment, local CUDA build toolchain, native TRELLIS.2 extensions (o_voxel, cumesh, flex_gemm, nvdiffrast) and auxiliary HF models are set up on this Windows machine. Use before installing packages, compiling CUDA extensions, or debugging import/DLL/compiler errors in E:\14-OCT-2026\Img3D\backend.
---

# Img3D backend environment (Windows 11, RTX 5090)

## Hard rules
- **Only the embedded Python** at `backend\python\python.exe` (3.14.2, Windows embeddable). Never system Python, never a venv/conda.
- The embedded interpreter runs **isolated** (`python314._pth`): `sys.flags.isolated=1`, `safe_path=True`.
  - The script's directory is **not** on `sys.path` → `backend/main.py` bootstraps `sys.path` itself.
  - `PYTHONPATH`/`PYTHONHOME`/`PYTHONNOUSERSITE` env vars are ignored. Add paths in code.
  - **User site-packages shadowing**: despite isolation, `site` still adds
    `C:\Users\Admin\AppData\Roaming\Python\Python314\site-packages` to `sys.path` *before* the embedded
    `Lib\site-packages` (`PYTHONNOUSERSITE` is ignored in isolated mode). That folder holds other versions
    (huggingface_hub 1.32 breaks transformers 4.57). Always run ad-hoc commands with **`python.exe -s`**; every entry
    point (`main.py`, `scripts\*.py`, `test\conftest.py`, test runners) strips `site.getusersitepackages()` first.
- Install packages with uv into the embedded interpreter (proper resolution, upgrades, uninstall):
  ```powershell
  $py="E:\14-OCT-2026\Img3D\backend\python\python.exe"
  & $py -s -m uv pip install --python $py --link-mode=copy <pkgs>
  ```
  (`backend\python.bat -m pip install` also works but forces `--target`, which breaks upgrades/dependency resolution.)
- Never re-download `backend\.model\TRELLIS.2-4B` (weights are local).
- Do not modify system settings / install a system CUDA toolkit.

## Installed stack
- torch `2.14.1+cu130`, torchvision `0.29.1+cu130` (index `https://download.pytorch.org/whl/cu130`). GPU arch list includes `sm_120`.
- torch SDPA: flash backend **not** available on Windows; mem-efficient/cuDNN are.
- transformers pinned `4.57.6` (TRELLIS.2 DINOv3 code uses internals: `embeddings`, `rope_embeddings`, `layer`).
- Full list: `backend/requirements.txt`.

## Local CUDA build toolchain (no system CUDA install)
`backend\.toolchain\cuda\nvidia\cu13` is a CUDA_HOME assembled from NVIDIA pip wheels (`uv pip install --target backend\.toolchain\cuda --no-deps ...`):
- compiler: `nvidia-cuda-nvcc==13.4.92`, `nvidia-cuda-crt==13.4.92`, `nvidia-nvvm==13.4.92`
- runtime headers/import lib: `nvidia-cuda-runtime==13.4.92` (**must equal nvcc version** — CCCL `cuda_toolkit.h` errors otherwise)
- `nvidia-cuda-cccl==13.0.85`, and 13.0-era headers: cublas 13.0.2.14, cusparse 12.6.3.3, cusolver 12.0.4.66, curand 10.4.0.35, cufft 12.0.0.61, nvtx 13.0.85, nvrtc 13.0.88, profiler-api 13.0.85, nvjitlink 13.0.88

Findings (do not re-investigate):
- VS: "Visual Studio Community 2026" at `C:\VS Studio 2026` with MSVC **14.51** (default) and 14.29.
- nvcc 13.0 + MSVC 14.51 → `cudafe++` ACCESS_VIOLATION even on a bare kernel. ✗
- nvcc 13.0 + MSVC 14.29 → no C++20 (torch 2.14 extensions are built with `-std=c++20`). ✗
- **nvcc 13.4 + MSVC 14.51 → works**, no `-allow-unsupported-compiler` needed. ✓
- Extensions linked with 13.4 `cudart.lib` run fine against torch's bundled `cudart64_13.dll` (13.0).

### Build environment (cmd)
```bat
call "C:\VS Studio 2026\VC\Auxiliary\Build\vcvars64.bat"
set "CUDA_HOME=E:\14-OCT-2026\Img3D\backend\.toolchain\cuda\nvidia\cu13"
set "CUDA_PATH=%CUDA_HOME%"
set "PATH=%CUDA_HOME%\bin;E:\14-OCT-2026\Img3D\backend\python\Scripts;%PATH%"
set "TORCH_CUDA_ARCH_LIST=12.0"
set "DISTUTILS_USE_SDK=1"
set "MAX_JOBS=16"
```
Then `python.exe -m pip install --no-build-isolation --no-deps <src_dir>` (or `pip wheel ... -w <dir>`).
Torch adds `-std=c++20`/`/std:c++20`; extension setup.py files that hard-code `-std=c++17` / `/std:c++17` must be switched to C++20.
Reproducible script: `backend\scripts\build_extensions.cmd`. Smoke test: `test\env_setup\smoke_ext.cmd`.

## Native extension sources (vendored) — BUILT & VERIFIED 2026-10-14
- `backend\third_party\TRELLIS.2` — official microsoft/TRELLIS.2 code (`trellis2` package + `o-voxel`), commit 75fbf01.
- `backend\third_party\extensions\{FlexGEMM 6dd94a8, CuMesh 12289e1, nvdiffrast v0.4.0}`.
- Build: `cmd /c backend\scripts\build_extensions.cmd [all|cumesh|flex_gemm|nvdiffrast|o_voxel] [--if-missing]`
  (~6.3 min clean, MAX_JOBS=12; refuses to reinstall over .pyd files loaded by a running backend — stop it first).
- Source patches (build-only) are in `backend\third_party\patches\*.patch`, documented in `PATCHES.md`, re-applied
  idempotently by the build script: C++17→C++20 flags (CuMesh, FlexGEMM, o-voxel); o-voxel MSVC fixes (GCC `d`
  literal suffix C3688; size_t→int64_t narrowing C2398).
- Built for sm_120 only (no PTX). Another GPU → change `TORCH_CUDA_ARCH_LIST` and rebuild.
- Verify: `backend\python\python.exe -s test\env_setup\verify_extensions.py` (checks against reference implementations).
- FlexGEMM needs `triton-windows` at runtime. Cold-cache autotune/JIT costs 5–32 s per new kernel key; caches persist in
  `backend\runtime\cache\{triton,flex_gemm}` (never purge them). First generation on a fresh cache is slow.

## Attention backends
TRELLIS.2 sparse attention supports only xformers / flash_attn / flash_attn_3, and its sparse config ignores
`ATTN_BACKEND=sdpa`. The engine always installs an SDPA varlen fallback (`backend\inference\trellis\compat.py`,
4 bindings patched). Dense attention uses torch SDPA natively. Inference models use only full attention.
- xformers 0.0.35 (only version accepting torch 2.14) has **no forward kernel for sm_120 on Windows** → uninstalled.
- flash-attn: no Windows/sm_120/py3.14 wheel; do not build from source.

## Auxiliary models (required by pipeline.json, gated → HF_TOKEN in backend\.env)
Stored locally under `backend\.model\` so runtime is offline:
- `facebook/dinov3-vitl16-pretrain-lvd1689m` → `.model\dinov3-vitl16-pretrain-lvd1689m`
- `briaai/RMBG-2.0` → `.model\RMBG-2.0`
- `microsoft/TRELLIS-image-large` `ckpts/ss_dec_conv3d_16l8_fp16.{json,safetensors}` → `.model\TRELLIS-image-large\ckpts\`
Downloader: `backend\scripts\download_aux_models.py [--check]`. HF cache: `backend\.model\.hf_cache`.
Resolution logic: `backend\inference\trellis\assets.py` (`resolve_aux_models`, `download_aux_models`).

Update (later on 2026-10-14): DINOv3 access was granted — `.model\dinov3-vitl16-pretrain-lvd1689m\model.safetensors`
is present and real generations completed. RMBG-2.0 is still absent (BiRefNet is used).

Earlier status 2026-10-14: HF_TOKEN (fine-grained, `canReadGatedRepos=True`) was valid, but the account
had **not been granted access** to `facebook/dinov3-vitl16-pretrain-lvd1689m` (403) or `briaai/RMBG-2.0` (403).
- DINOv3 is mandatory (no substitute — TRELLIS.2 flow models are trained on its features). The user must request
  access on the model page (Meta approves manually), then run the downloader and click "Retry loading model".
- RMBG-2.0 is optional: `ZhengPeng7/BiRefNet` (MIT) is installed in `.model\BiRefNet` and used automatically
  (verified IoU 0.99). RMBG-2.0 is CC BY-NC (non-commercial) anyway.
- `einops` is required by the BiRefNet/RMBG remote code (pinned in requirements.txt).
- Do NOT pull DINOv3 from unofficial mirrors without the user's explicit decision (license + provenance).
  Official file metadata IS readable without access (`HfApi().model_info(repo, files_metadata=True)`): repo sha
  `ea8dc2863c51be0a264bab82070e3e8836b02d51`; `model.safetensors` = 1,212,559,808 bytes,
  sha256 `dcb2e45127cccbf1601e5f42fef165eea275c8e5213197e8dcf3f48822718179` — any copy must match this exactly.
- No DINOv3 weights exist elsewhere on this machine's drives (searched C:\Users\Admin, D:\, E:\ on 2026-10-14).
