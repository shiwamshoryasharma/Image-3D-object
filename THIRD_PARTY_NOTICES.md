# Third-party notices

Img3D's own source code is © 2026 **Shiwam Shorya Sharma** and licensed under the
[GNU General Public License v3.0](LICENSE).

Img3D stands on the work of many open-source projects and research models. This file lists them, their licenses
and the policies I follow when using them. **None of the third-party code, binaries or model weights below is
copied into this repository**: upstream sources are linked as Git submodules pinned to their original repositories,
and packages, toolchains and weights are downloaded by each user from the official source.

> This file is a good-faith summary, not legal advice. The license text published by each project is what
> governs. Read it before you download or use a component, especially for any commercial use.

---

## 1. Read this first — usage restrictions

| Component | Restriction | Effect on Img3D |
|---|---|---|
| **nvdiffrast** (NVIDIA) | NVIDIA Source Code License (1-Way Commercial): **non-commercial use only** ("research or evaluation purposes") | Img3D needs it to export textured GLBs, so a working Img3D install is **for non-commercial use** unless you obtain other terms from NVIDIA |
| **DINOv3** (Meta) | DINOv3 License: acceptable use + trade-control terms; research publications must acknowledge DINOv3; redistribution must include the license | Required image encoder; every user accepts the license on Hugging Face themselves |
| **RMBG-2.0** (BRIA AI) | Bria RMBG-2.0 license (CC BY-NC 4.0): **non-commercial only** | **Optional.** Img3D uses MIT-licensed BiRefNet by default |
| **Blender** (Blender Foundation) | GNU GPL; Python scripts that use the `bpy` API must be GPL-compatible | `backend/blender_scripts/` is GPL-3.0 as part of Img3D; Blender runs as a separate program and is never bundled |

Other notes:
- Img3D is distributed as **source code only**. It does not ship nvdiffrast, model weights, Python, CUDA or Blender.
  If you ever distribute a packaged build that includes them, each component's own terms apply in addition to
  the GPL, and nvdiffrast's terms are not GPL-compatible.
- **Generated 3D assets.** The licenses of TRELLIS.2 and the other models do not grant rights in the images you
  feed them. Only use input images you have the rights to, and follow the Hugging Face
  [Content Policy](https://huggingface.co/content-policy) and the model licenses for what you generate.

---

## 2. AI models (Hugging Face) — downloaded into `backend/.model/`

| Model | Authors | License | Link |
|---|---|---|---|
| TRELLIS.2-4B | Microsoft (Xiang et al.) | MIT | [huggingface.co/microsoft/TRELLIS.2-4B](https://huggingface.co/microsoft/TRELLIS.2-4B) |
| TRELLIS-image-large (sparse-structure decoder only) | Microsoft | MIT | [huggingface.co/microsoft/TRELLIS-image-large](https://huggingface.co/microsoft/TRELLIS-image-large) |
| DINOv3 ViT-L/16 (LVD-1689M) | Meta AI | [DINOv3 License](https://ai.meta.com/resources/models-and-libraries/dinov3-license/) | [huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m](https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m) |
| BiRefNet | Peng Zheng et al. | MIT | [huggingface.co/ZhengPeng7/BiRefNet](https://huggingface.co/ZhengPeng7/BiRefNet) |
| RMBG-2.0 (optional) | BRIA AI | Bria RMBG-2.0 / [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) | [huggingface.co/briaai/RMBG-2.0](https://huggingface.co/briaai/RMBG-2.0) |

Downloads go through the Hugging Face Hub and are subject to the
[Hugging Face Terms of Service](https://huggingface.co/terms-of-service).

---

## 3. Upstream source code — Git submodules in `backend/third_party/`

| Project | Pinned commit | License | Repository |
|---|---|---|---|
| TRELLIS.2 (`trellis2`, `o-voxel`) | `75fbf01` | MIT — © Microsoft Corporation | [github.com/microsoft/TRELLIS.2](https://github.com/microsoft/TRELLIS.2) |
| CuMesh | `12289e1` | MIT — © 2025 Jianfeng Xiang | [github.com/JeffreyXiang/CuMesh](https://github.com/JeffreyXiang/CuMesh) |
| FlexGEMM | `6dd94a8` | MIT — © 2025 Jianfeng Xiang | [github.com/JeffreyXiang/FlexGEMM](https://github.com/JeffreyXiang/FlexGEMM) |
| nvdiffrast | `v0.4.0` | [NVIDIA Source Code License (1-Way Commercial)](https://github.com/NVlabs/nvdiffrast/blob/main/LICENSE.txt) — © 2020 NVIDIA Corporation | [github.com/NVlabs/nvdiffrast](https://github.com/NVlabs/nvdiffrast) |
| Eigen (nested in o-voxel) | upstream pin | MPL-2.0 | [gitlab.com/libeigen/eigen](https://gitlab.com/libeigen/eigen) |
| cubvh (nested in CuMesh) | upstream pin | MIT — © 2022 kiui | [github.com/ashawkey/cubvh](https://github.com/ashawkey/cubvh) |

The files in `backend/third_party/patches/` modify MIT-licensed upstream files for build compatibility only and
are provided under the same MIT terms. Upstream copyright and license notices are kept unchanged.

---

## 4. Python packages — installed into `backend/python/`

Direct dependencies from [`backend/requirements.txt`](backend/requirements.txt). Their own dependencies keep their
own licenses (list them with `pip-licenses` or `importlib.metadata`).

| Package | Version | License | Project |
|---|---|---|---|
| torch | 2.14.1+cu130 | BSD-3-Clause (+ bundled Apache-2.0, MIT, BSL-1.0 parts) | [pytorch.org](https://pytorch.org) |
| torchvision | 0.29.1+cu130 | BSD-3-Clause | [pytorch/vision](https://github.com/pytorch/vision) |
| fastapi | 0.143.0 | MIT | [fastapi/fastapi](https://github.com/fastapi/fastapi) |
| starlette | 1.8.0 | BSD-3-Clause | [Kludex/starlette](https://github.com/Kludex/starlette) |
| uvicorn | 0.54.0 | BSD-3-Clause | [uvicorn.dev](https://uvicorn.dev/) |
| httptools | 0.9.0 | MIT | [MagicStack/httptools](https://github.com/MagicStack/httptools) |
| watchfiles | 1.3.0 | MIT | [samuelcolvin/watchfiles](https://github.com/samuelcolvin/watchfiles) |
| websockets | 17.2 | BSD-3-Clause | [python-websockets/websockets](https://github.com/python-websockets/websockets) |
| pydantic / pydantic-core | 2.14.1 / 2.50.1 | MIT | [pydantic/pydantic](https://github.com/pydantic/pydantic) |
| pydantic-settings | 2.15.0 | MIT | [pydantic/pydantic-settings](https://github.com/pydantic/pydantic-settings) |
| python-multipart | 0.0.32 | Apache-2.0 | [Kludex/python-multipart](https://github.com/Kludex/python-multipart) |
| python-dotenv | 1.2.4 | BSD-3-Clause | [theskumar/python-dotenv](https://github.com/theskumar/python-dotenv) |
| psutil | 7.2.2 | BSD-3-Clause | [giampaolo/psutil](https://github.com/giampaolo/psutil) |
| colorama | 0.4.6 | BSD-3-Clause | [tartley/colorama](https://github.com/tartley/colorama) |
| pillow | 12.3.0 | MIT-CMU | [python-pillow.github.io](https://python-pillow.github.io) |
| numpy | 2.4.1 | BSD-3-Clause (+ 0BSD, MIT, Zlib, CC0-1.0 parts) | [numpy.org](https://numpy.org) |
| opencv-python-headless | 5.0.0.93 | Apache-2.0 | [opencv/opencv-python](https://github.com/opencv/opencv-python) |
| imageio | 2.38.1 | BSD-2-Clause | [imageio/imageio](https://github.com/imageio/imageio) |
| trimesh | 5.1.1 | MIT | [mikedh/trimesh](https://github.com/mikedh/trimesh) |
| scipy | 1.18.1 | BSD-3-Clause | [scipy.org](https://scipy.org/) |
| plyfile | 1.1.5 | **GPL-3.0-or-later** | [dranjan/python-plyfile](https://github.com/dranjan/python-plyfile) |
| zstandard | 0.25.0 | BSD-3-Clause | [indygreg/python-zstandard](https://github.com/indygreg/python-zstandard) |
| transformers | 4.57.6 | Apache-2.0 | [huggingface/transformers](https://github.com/huggingface/transformers) |
| huggingface-hub | 0.36.2 | Apache-2.0 | [huggingface/huggingface_hub](https://github.com/huggingface/huggingface_hub) |
| safetensors | 0.8.0 | Apache-2.0 | [huggingface/safetensors](https://github.com/huggingface/safetensors) |
| tokenizers | 0.22.2 | Apache-2.0 | [huggingface/tokenizers](https://github.com/huggingface/tokenizers) |
| easydict | 1.13 | **LGPL-3.0** | [makinacorpus/easydict](https://github.com/makinacorpus/easydict) |
| einops | 0.8.2 | MIT | [arogozhnikov/einops](https://github.com/arogozhnikov/einops) |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | [tqdm.github.io](https://tqdm.github.io) |
| ninja | 1.13.2 | Apache-2.0 | [ninja-build.org](https://ninja-build.org/) |
| triton-windows | 3.8.0.post29 | MIT | [triton-lang/triton-windows](https://github.com/triton-lang/triton-windows) |
| kornia | 0.8.3 | Apache-2.0 | [kornia.github.io](https://kornia.github.io/) |
| timm | 1.0.30 | Apache-2.0 | [huggingface/pytorch-image-models](https://github.com/huggingface/pytorch-image-models) |
| httpx | 0.28.1 | BSD-3-Clause | [encode/httpx](https://github.com/encode/httpx) |
| pytest | 9.1.1 | MIT | [pytest.org](https://docs.pytest.org/) |
| uv (installer) | 0.9.27 | MIT OR Apache-2.0 | [astral-sh/uv](https://github.com/astral-sh/uv) |

Native extensions built locally from section 3: `o_voxel`, `cumesh`, `flex_gemm` (MIT), `nvdiffrast` (NVIDIA SCL).

---

## 5. Frontend packages — installed into `frontend/node_modules/`

Direct dependencies from [`frontend/package.json`](frontend/package.json) (installed versions).

| Package | Version | License | Project |
|---|---|---|---|
| react / react-dom | 19.3.0 | MIT | [react/react](https://github.com/react/react) |
| three | 0.186.1 | MIT | [mrdoob/three.js](https://github.com/mrdoob/three.js) |
| @react-three/fiber | 9.8.1 | MIT | [pmndrs/react-three-fiber](https://github.com/pmndrs/react-three-fiber) |
| @react-three/drei | 10.7.9 | MIT | [pmndrs/drei](https://github.com/pmndrs/drei) |
| zustand | 5.0.15 | MIT | [pmndrs/zustand](https://github.com/pmndrs/zustand) |
| axios | 1.20.0 | MIT | [axios/axios](https://github.com/axios/axios) |
| react-dropzone | 20.1.3 | MIT | [react-dropzone/react-dropzone](https://github.com/react-dropzone/react-dropzone) |
| lucide-react | 1.57.0 | ISC | [lucide-icons/lucide](https://github.com/lucide-icons/lucide) |
| vite (dev) | 8.3.4 | MIT | [vitejs/vite](https://github.com/vitejs/vite) |
| @vitejs/plugin-react (dev) | 6.1.2 | MIT | [vitejs/vite-plugin-react](https://github.com/vitejs/vite-plugin-react) |
| @rolldown/plugin-babel (dev) | 0.2.4 | MIT | [rolldown/plugins](https://github.com/rolldown/plugins) |
| @babel/core (dev) | 7.29.7 | MIT | [babel/babel](https://github.com/babel/babel) |
| babel-plugin-react-compiler (dev) | 1.0.0 | MIT | [react/react](https://github.com/react/react) |
| typescript (dev) | 6.0.3 | Apache-2.0 | [microsoft/TypeScript](https://github.com/microsoft/TypeScript) |
| oxlint (dev) | 1.88.0 | MIT | [oxc-project/oxc](https://github.com/oxc-project/oxc) |
| @types/* (dev) | — | MIT | [DefinitelyTyped](https://github.com/DefinitelyTyped/DefinitelyTyped) |

A production build (`frontend/dist`) bundles code from these packages; keep their license notices if you
redistribute a build.

---

## 6. Runtimes and tools (installed by the user, never bundled)

| Tool | License | Notes |
|---|---|---|
| CPython 3.14.2 | [PSF License](https://docs.python.org/3/license.html) | Embedded interpreter in `backend/python/` |
| NVIDIA CUDA 13.x components (pip wheels) | [NVIDIA CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html) | Local build toolchain in `backend/.toolchain/` |
| Blender ≥ 5.1 | [GNU GPL](https://www.blender.org/about/license/) | Auto Mesh Tuner runs it as a separate process; the optional portable download comes from download.blender.org after your confirmation |
| Microsoft Visual Studio / Build Tools (MSVC) | Microsoft license terms | Needed only to compile the native extensions |
| Node.js / npm | MIT / Artistic-2.0 | Frontend tooling |

---

## 7. Codes of conduct and policies of the projects I use

When contributing to, reporting issues on or discussing the upstream projects, their own rules apply:

- Microsoft (TRELLIS.2, TypeScript): [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/)
- Python: [PSF Code of Conduct](https://policies.python.org/python.org/code-of-conduct/)
- PyTorch: [PyTorch Code of Conduct](https://github.com/pytorch/pytorch/blob/main/CODE_OF_CONDUCT.md)
- Hugging Face: [Code of Conduct](https://github.com/huggingface/transformers/blob/main/CODE_OF_CONDUCT.md) ·
  [Content Policy](https://huggingface.co/content-policy) · [Terms of Service](https://huggingface.co/terms-of-service)
- React: [Code of Conduct](https://github.com/react/react/blob/main/CODE_OF_CONDUCT.md)
- Blender: [Code of Conduct](https://developer.blender.org/docs/handbook/communication/code_of_conduct/) ·
  [License](https://www.blender.org/about/license/) · [Logo & trademark](https://www.blender.org/about/logo/)
- Meta DINOv3: [DINOv3 License](https://ai.meta.com/resources/models-and-libraries/dinov3-license/) (includes acceptable-use terms)
- NVIDIA: [nvdiffrast license](https://github.com/NVlabs/nvdiffrast/blob/main/LICENSE.txt) · [CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html)

Img3D's own community rules: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

---

## 8. Trademarks

Microsoft, TRELLIS, NVIDIA, CUDA, RTX, Meta, DINOv3, Hugging Face, Blender, Python, PyTorch, React and all other
names are trademarks of their respective owners and are used here only to identify the software Img3D works
with. Img3D is an independent personal project and is **not affiliated with or endorsed by** any of them.

---

## 9. Research citation

If you use Img3D's results in research, cite the underlying model:

```bibtex
@article{xiang2025trellis2,
  title   = {Native and Compact Structured Latents for 3D Generation},
  author  = {Xiang, Jianfeng and Chen, Xiaoxue and Xu, Sicheng and Wang, Ruicheng and Lv, Zelong and Deng, Yu and
             Zhu, Hongyuan and Dong, Yue and Zhao, Hao and Yuan, Nicholas Jing and Yang, Jiaolong},
  journal = {Tech report},
  year    = {2025}
}
```

and acknowledge DINOv3, as its license requires for publications.
