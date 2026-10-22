# `backend/third_party/` — upstream sources (Git submodules)

The upstream projects are **not copied** into this repository. Each one is a Git submodule pinned to the exact
upstream commit Img3D is built and tested against. Clone with submodules:

```powershell
git clone --recursive https://github.com/shiwamshoryasharma/Image-3D-object.git
# already cloned without --recursive?
git submodule update --init --recursive
```

| Path | Upstream | Pinned commit | License | Used for |
|---|---|---|---|---|
| `TRELLIS.2/` | [microsoft/TRELLIS.2](https://github.com/microsoft/TRELLIS.2) | `75fbf01` | MIT | `trellis2` package (imported at runtime) + `o-voxel` extension |
| `extensions/CuMesh/` | [JeffreyXiang/CuMesh](https://github.com/JeffreyXiang/CuMesh) | `12289e1` | MIT | CUDA mesh processing (`cumesh`) |
| `extensions/FlexGEMM/` | [JeffreyXiang/FlexGEMM](https://github.com/JeffreyXiang/FlexGEMM) | `6dd94a8` | MIT | Sparse convolution kernels (`flex_gemm`, Triton) |
| `extensions/nvdiffrast/` | [NVlabs/nvdiffrast](https://github.com/NVlabs/nvdiffrast) | `v0.4.0` (`253ac4f`) | NVIDIA Source Code License (1-Way Commercial) — **non-commercial** | Rasterization for texture baking / GLB export |
| `see-through/` | [shitagaki-lab/see-through](https://github.com/shitagaki-lab/see-through) | `df019de` | Apache-2.0 | Anime part parser model code (`common/modules/semanticsam.py` + SAM-HQ modelling) for the part-aware `anime` profile |
| `sapiens2/` | [facebookresearch/sapiens2](https://github.com/facebookresearch/sapiens2) | `7e5bae8` | Sapiens2 License (custom; conditions: no biometric identification, no deepfakes/impersonation, no military use) | Standalone backbone (`sapiens/backbones/standalone/sapiens2.py`) for the part-aware `realistic` profile |

Nested submodules come along with `--recursive`: Eigen (in `TRELLIS.2/o-voxel/third_party/eigen`, MPL-2.0) and
cubvh (in `CuMesh/third_party/cubvh`, MIT).

## What Img3D adds on top

- [`patches/`](patches/) — **build-only** patches (C++17 → C++20 flags and two MSVC fixes) so the sources compile
  with torch 2.14 + MSVC 14.51 + nvcc 13.4. They change no runtime behaviour. Every change is explained in
  [`PATCHES.md`](PATCHES.md).
- `backend/scripts/build_extensions.cmd` applies the patches idempotently and builds all four extensions into the
  embedded Python:

  ```powershell
  cmd /c backend\scripts\build_extensions.cmd               # all four (≈6 min clean build)
  cmd /c backend\scripts\build_extensions.cmd cumesh        # just one: cumesh | flex_gemm | nvdiffrast | o_voxel
  .\backend\python\python.exe -s test\env_setup\verify_extensions.py   # verify against reference implementations
  ```

The build targets `TORCH_CUDA_ARCH_LIST=12.0` (RTX 50-series). Set another value for other GPUs.

## Vendored segmentation parsers (part-aware milestone)

`see-through/` and `sapiens2/` are model-code sources only. Nothing from them is built or installed, and their
`requirements*.txt` / `pyproject.toml` dependencies are **never** installed. Img3D imports them through
`backend/inference/providers/vendor_import.py`, which loads them under the private module namespace
`img3d_vendor.<name>` (their `import utils` / `from models import …` are rewritten in memory), so `sys.path` and
Img3D's own `models`, `utils`, … packages are never touched and no `__pycache__` is written into the submodules.

- See-through: source root `common/` (`modules`, `utils`, `live2d`); only `modules.semanticsam` and what it
  imports are executed (model definitions). Weights: `backend/.model/seg/l2d_sam_iter2/checkpoint-18000.pt`,
  read with `torch.load(weights_only=True)`.
- Sapiens2: source root `sapiens/backbones/standalone/` (pure torch). The full `sapiens` package is not imported
  (its training engine needs termcolor, rich, matplotlib, iopath, …). Weights:
  `backend/.model/seg/sapiens2-seg-0.4b/model.safetensors`.

The model registry reports a parser `vendored_source_missing` while its submodule is not checked out
(`git submodule update --init backend/third_party/see-through backend/third_party/sapiens2`).

## Licenses I follow

- Upstream code is fetched from the original repositories and keeps its original license and copyright notices.
  The patch files are modifications of MIT-licensed files and are provided under the same MIT terms.
- **nvdiffrast** may be used **non-commercially only** (research or evaluation). Because Img3D needs it to export
  textured meshes, a working Img3D installation inherits that restriction — see
  [`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md).
