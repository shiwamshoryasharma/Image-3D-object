# `backend/.toolchain/` — local CUDA build toolchain (not in Git)

> Only this README is tracked. The toolchain (~1.7 GB) is NVIDIA software installed from NVIDIA's official
> PyPI wheels; it is never redistributed by this repository.

## What lives here

`cuda/nvidia/cu13/` is a `CUDA_HOME` assembled from NVIDIA's pip wheels, so the native extensions can be
compiled **without installing a system-wide CUDA Toolkit**:

| Wheel | Version | Purpose |
|---|---|---|
| `nvidia-cuda-nvcc`, `nvidia-cuda-crt`, `nvidia-nvvm` | 13.4.92 | CUDA compiler (`nvcc`, `cudafe++`, NVVM) |
| `nvidia-cuda-runtime` | 13.4.92 | Runtime headers + `cudart.lib` (**must match the nvcc version**) |
| `nvidia-cuda-cccl` | 13.0.85 | CUB / Thrust / libcu++ headers |
| `nvidia-cublas`, `-cusparse`, `-cusolver`, `-curand`, `-cufft`, `-nvtx`, `-cuda-nvrtc`, `-cuda-profiler-api`, `-nvjitlink` | 13.0-era | Headers needed by PyTorch's extension build |

Exact versions are documented in [`.claude/skills/img3d-environment/SKILL.md`](../../.claude/skills/img3d-environment/SKILL.md).

## How to recreate it

```powershell
$py = ".\backend\python\python.exe"
& $py -s -m uv pip install --python $py --target backend\.toolchain\cuda --no-deps `
    nvidia-cuda-nvcc==13.4.92 nvidia-cuda-crt==13.4.92 nvidia-nvvm==13.4.92 nvidia-cuda-runtime==13.4.92 `
    nvidia-cuda-cccl==13.0.85 nvidia-cublas==13.0.2.14 nvidia-cusparse==12.6.3.3 nvidia-cusolver==12.0.4.66 `
    nvidia-curand==10.4.0.35 nvidia-cufft==12.0.0.61 nvidia-nvtx==13.0.85 nvidia-cuda-nvrtc==13.0.88 `
    nvidia-cuda-profiler-api==13.0.85 nvidia-nvjitlink==13.0.88
```

Then run `cmd /c backend\scripts\build_extensions.cmd`, which uses this folder as `CUDA_HOME`
(override with `IMG3D_CUDA_HOME` to use a regular CUDA 13.x install instead).

Known-good pairing on Windows: **nvcc 13.4 + MSVC 14.51 (Visual Studio 2026)**. nvcc 13.0 crashes in `cudafe++`
with that compiler.

## Licenses I follow

- All components are governed by the [NVIDIA CUDA Toolkit EULA](https://docs.nvidia.com/cuda/eula/index.html)
  and the license files inside each wheel's `*.dist-info/`.
- Installed by the user from NVIDIA's official wheels, used only to compile locally, and **not redistributed**.
