# Img3D part-aware milestone: environment audit (read-only)

Audit date: 2026-10-16, about 12:15 to 12:35 IST. Machine: Windows 11 Pro 10.0.26300, RTX 5090, 127 GB RAM.
Scope: what we can install and run for the part-aware backend (segmentation, prompt-to-3D text-to-image stage, mesh part tooling) and for the Node UI (node-graph library).

**Read-only guarantee.** No package was installed, no model was downloaded, and the backend was not started. The excluded jobs (`job_269a864ac597`, `job_fa0c4771ec03`) were never opened. Every `uv pip install` call used `--dry-run`. The npm lockfile probe ran on *copies* in the session scratchpad. Side effects outside the project: uv metadata cache (`C:\Users\Admin\AppData\Local\uv\cache`) and npm metadata cache. `git status` shows 11 modified and 2 untracked files that predate this audit (mtimes 11:56 to 12:01, from the paused Auto Mesh Tuner work), and this audit did not touch them. The third untracked entry, `.claude/skills/img3d-part-aware/`, holds this workflow's research outputs. `frontend/package-lock.json` is unchanged.

Legend: **[V]** = verified (I ran or read it), **[A]** = assumption or inference.

---

## 1. Embedded interpreter facts [V]

| Item | Value |
|---|---|
| Interpreter | `E:\14-OCT-2026\Img3D\backend\python\python.exe`, CPython **3.14.2** (MSC v.1944, AMD64), `win-amd64`, GIL enabled (`Py_GIL_DISABLED=0`). Wheel tag needed: **cp314-cp314-win_amd64** (or abi3 / py3-none-any) |
| pip / uv | pip 25.3, **uv 0.9.27** (2026-01-26), run as `python -s -m uv` |
| `_pth` | `python314._pth` contains `import site`, so the interpreter runs isolated (`sys.flags.isolated=1`) |
| User-site shadowing | Without `-s`, `no_user_site=0`. `C:\Users\Admin\AppData\Roaming\Python\Python314\site-packages` (0.29 GB) contains **huggingface_hub 1.32.0, fastapi 0.136.1, numpy 2.4.6, opencv_python_headless 4.13.0.92, pydantic 2.13.4, starlette 1.0.0, uvicorn 0.47.0, …**. With `-s` that path is gone from `sys.path`. Any new entry point, including a new segmentation or text-to-image worker, **must** keep stripping user-site, as `img3d-environment/SKILL.md` L13-17 already requires |
| Torch | `torch 2.14.1+cu130`, CUDA 13.0, cuDNN 92400 (9.24) |
| Build toolchain | `backend\.toolchain\cuda\nvidia\cu13\bin\nvcc.exe` = **13.4.92** (1.68 GB toolchain); MSVC 14.51 in `C:\VS Studio 2026`; no system CUDA (`CUDA_PATH` empty, nvcc not on PATH, which is intended) |
| Env health | `uv pip check`: "Checked 80 packages … All installed packages are compatible"; `pip check`: "No broken requirements found." |
| Embedded Python size | 4.29 GB |

### 1.1 Requested package presence [V] (`importlib.metadata`, run with `-s`)

| Package | Installed? | Version |
|---|---|---|
| diffusers | **NO** | |
| accelerate | **NO** | |
| sentencepiece | **NO** | |
| protobuf | **NO** | |
| timm | yes | 1.0.30 |
| opencv | yes (headless only) | `opencv-python-headless==5.0.0.93`. `opencv-python` and `opencv-contrib-python` are absent |
| scikit-image | **NO** | |
| trimesh | yes | 5.1.1 |
| scipy | yes | 1.18.1 |
| networkx | yes | 3.6.1 |
| rtree | **NO** | trimesh's `proximity`/`ray` helpers and some `split` paths are faster with it |
| also present | | transformers 4.57.6, huggingface_hub 0.36.2, tokenizers 0.22.2, safetensors 0.8.0, kornia 0.8.3, einops 0.8.2, plyfile 1.1.5, pandas 3.0.0, polars 1.37.1, numpy 2.4.1, pillow 12.3.0, triton-windows 3.8.0.post29, native: cumesh 0.0.1, flex_gemm 1.0.0, o_voxel 0.0.1, nvdiffrast 0.4.0 |
| also absent | | pymeshlab, open3d, xatlas, manifold3d, pyvista, segment-anything, sam2, ultralytics, onnxruntime(-gpu), rembg, mediapipe, insightface, xformers, flash-attn, bitsandbytes, peft, gguf, optimum |

Full freeze: 80 distributions, captured at the top of the session. `backend/requirements.txt` L1-75 is the pin file and matches the freeze.

---

## 2. uv dry-run resolution (exact outputs, trimmed) [V]

Command form: `& $py -s -m uv pip install --python $py --dry-run --only-binary ":all:" <pkgs>`.
`--only-binary :all:` makes uv fail instead of falling back to a source build, which **proves a cp314 win_amd64 or universal wheel exists**.

```
===== diffusers (unpinned) =====
Resolved 26 packages in 744ms
Would uninstall 1 package / Would install 3 packages
 + diffusers==0.41.0
 + hf-xet==1.7.0
 - huggingface-hub==0.36.2
 + huggingface-hub==1.33.0          <-- WOULD BREAK transformers 4.57.6 (needs hub<1.0)
===== accelerate =====
 + accelerate==1.15.0                (torch untouched)
===== sentencepiece =====
 + sentencepiece==0.2.2
===== protobuf =====
 + protobuf==7.36.2
===== diffusers + transformers==4.57.6 + huggingface-hub==0.36.2 =====
Resolved 26 packages in 453ms
 + diffusers==0.39.0                 <-- newest diffusers compatible with the TRELLIS pins
===== transformers (no upgrade flag) =====
Audited 1 package in 5ms / Would make no changes
===== transformers --upgrade-package transformers =====
Would uninstall 3 / install 12:
 - huggingface-hub==0.36.2  + huggingface-hub==2.2.0
 - tokenizers==0.22.2       + tokenizers==0.23.3
 - transformers==4.57.6     + transformers==5.20.0
 + hf-xet 1.7.0, httpcore2 2.13.1, httpx2 2.13.1, markdown-it-py 4.2.0, mdurl 0.1.2, rich 15.0.0,
   shellingham 1.5.4, truststore 0.10.5, typer 0.27.3
===== combined: diffusers accelerate sentencepiece protobuf transformers==4.57.6 huggingface-hub==0.36.2 tokenizers==0.22.2 =====
Resolved 37 packages in 19ms / Would install 4 packages
 + accelerate==1.15.0  + diffusers==0.39.0  + protobuf==7.36.2  + sentencepiece==0.2.2
===== scikit-image =====   + lazy-loader==0.6  + scikit-image==0.26.0  + tifffile==2026.10.15
===== rtree =====          + rtree==1.4.1
```

**Important trap [V]:** `uv pip install` does **not** check the requirements of packages that are already installed. A bare `uv pip install diffusers` would silently move hub to 1.33.0 and break transformers 4.57.6 (`transformers` requires `huggingface-hub<1.0,>=0.34.0`, read from installed metadata). Always install with a constraints file (section 9).

### 2.1 Why diffusers 0.39.0 is the ceiling [V] (PyPI JSON `requires_dist`)

| diffusers | Released (UTC) | `huggingface-hub` requirement | Compatible with hub 0.36.2? |
|---|---|---|---|
| 0.39.0 | 2026-07-03 | `<2.0,>=0.34.0` (+ `httpx<1.0.0`, `safetensors>=0.8.0`) | **YES** |
| 0.40.0 | 2026-08-20 | `<2.0,>=1.23.0` | NO |
| 0.41.0 (latest) | 2026-10-06 | `<2.0,>=1.32.0` | NO |

diffusers has no hard dependency on transformers. The `torch` extra wants `torch>=2.6` and `accelerate>=0.31.0`, both satisfied. All three diffusers versions ship `py3-none-any` wheels (0.39.0: 5.37 MB). Licence: Apache-2.0 (https://github.com/huggingface/diffusers/blob/main/LICENSE).

### 2.2 Newest transformers [V]
transformers **5.20.0** needs hub 2.2.0 and tokenizers 0.23.3. TRELLIS.2's DINOv3 wrapper uses transformers 4.x internals (`img3d-environment/SKILL.md` L30, `requirements.txt` L47), so **do not upgrade**. That would break the working generator.

---

## 3. Compatibility verdict table

| Package | Status | Dry-run version | Win cp314 wheel (file, size) | Licence | Verdict |
|---|---|---|---|---|---|
| diffusers | absent | **0.39.0** (pinned stack) / 0.41.0 (unpinned) | `diffusers-0.39.0-py3-none-any.whl` 5.37 MB | Apache-2.0 | **OK at ==0.39.0 only**; 0.40+ is incompatible with hub 0.36.2 |
| accelerate | absent | 1.15.0 | `accelerate-1.15.0-py3-none-any.whl` 0.38 MB | Apache-2.0 | OK (needs `torch>=2.0.0`, `huggingface_hub>=0.21.0`; torch untouched) |
| sentencepiece | absent | 0.2.2 | `sentencepiece-0.2.2-cp314-cp314-win_amd64.whl` 1.29 MB | Apache-2.0 | OK (only needed for T5/Llama-style tokenizers; not needed for SDXL) |
| protobuf | absent | 7.36.2 | `protobuf-7.36.2-cp310-abi3-win_amd64.whl` 0.44 MB | BSD-3-Clause | OK |
| scikit-image | absent | 0.26.0 (+lazy-loader 0.6, tifffile 2026.10.15) | `scikit_image-0.26.0-cp314-cp314-win_amd64.whl` 11.47 MB | BSD-3-Clause | OK |
| rtree | absent | 1.4.1 | `rtree-1.4.1-py3-none-win_amd64.whl` 0.37 MB | MIT | OK |
| timm | 1.0.30 | | | Apache-2.0 | present |
| opencv | headless 5.0.0.93 | | | Apache-2.0 | present; **do not add** `opencv-python` or `opencv-contrib-python` (duplicate `cv2`) |
| trimesh | 5.1.1 | | | MIT | present (split/merge, scene graph, GLB node export) |
| scipy | 1.18.1 | | | BSD-3 | present |
| networkx | 3.6.1 | | | BSD-3 | present (part adjacency graph) |
| transformers latest | 4.57.6 pinned | 5.20.0 available | | Apache-2.0 | **DO NOT upgrade** |

### 3.1 Optional extras probed (binary-only dry-runs) [V]

| Package | Result | Wheel / size | Licence | Note |
|---|---|---|---|---|
| manifold3d | + 3.5.4 | `cp314-cp314-win_amd64` 1 MB | Apache-2.0 | Robust boolean/union for reassembly checks. OK |
| fast-simplification | + 0.2.0 | `cp314-cp314-win_amd64` 0.3 MB | MIT | Per-part decimation (future tuning). OK |
| pygltflib | + 1.16.5 (+6 deps) | pure | MIT | Low-level glTF extras/node metadata. Optional; trimesh may suffice |
| segment-anything | + 1.0 | `py3-none-any` | Apache-2.0 | SAM v1 code only; the same capability already exists in transformers (`SamModel`) |
| **sam2** | **FAIL**: "all versions of sam2 have no usable wheels" | sdist only | Apache-2.0 | Would need a source build. Use **transformers `Sam2Model`** instead (already installed, see section 7) |
| **xatlas** | **FAIL**: no wheels for cp314 | | MIT | UV atlas per part needs a source build or the existing cumesh/o_voxel UV path |
| **fpsample** | **FAIL**: only cp37 to cp312 wheels | | MIT | not needed |
| onnxruntime-gpu | + 1.31.0 (+flatbuffers, protobuf) | `cp314-cp314-win_amd64` 160.9 MB | MIT | Wheel exists. **[A]** CUDA 13 / sm_120 provider compatibility not verified; prefer torch-native models |
| onnxruntime | + 1.31.0 | cp314 wheel | MIT | CPU fallback |
| pymeshlab | + 2025.7.post1 (+msvc-runtime) | `cp314-cp314-win_amd64` 54 MB | **GPL-3.0** | Licence risk for redistribution; keep it out of the core |
| open3d | + 0.20.0 (37 packages incl. dash, flask, ipython, jupyter) | `cp314-cp314-win_amd64` 75.7 MB | MIT | Heavy dependency fan-out; not recommended |
| ultralytics | + 8.4.178 **plus `opencv-python==5.0.0.93`** (duplicate cv2) and matplotlib | `py3-none-any` | **AGPL-3.0** | Reject (licence + cv2 clash). With the scratch constraints file uv backtracked for 2 min 34 s to an older version |
| mediapipe | + 1.1.1 **plus `opencv-contrib-python`**, sounddevice, matplotlib | `py3-none-win_amd64` 21.7 MB | Apache-2.0 | cv2 clash; reject unless isolated |
| rembg | + 2.0.85 (+numba 0.68, llvmlite 0.50, scikit-image, pymatting, pooch …) | `py3-none-any` | MIT | Not needed (BiRefNet already in place) |
| peft | + 0.21.2 (+accelerate) | `py3-none-any` | Apache-2.0 | Only if LoRA style adapters are wanted for text-to-image |

---

## 4. Disk [V] (`Get-PSDrive` / `Get-Volume`)

| Drive | Label | Used | **Free** | Total |
|---|---|---|---|---|
| C: | (none) | 1469.4 GB | **392.6 GB** | 1862.0 GB |
| D: | MODELS | 622.7 GB | **259.3 GB** | 882.0 GB |
| E: (Img3D) | QUARK | 269.9 GB | **711.1 GB** | 981.0 GB |

Img3D model store `backend\.model` = **16.80 GB**. Space is not a constraint: SDXL fp16 is about 7 GB and SAM2.1-L is about 0.9 GB **[A]**.

## 5. GPU and RAM [V]

```
nvidia-smi --query-gpu=...
NVIDIA GeForce RTX 5090, 617.42, 32607 MiB total, 1854 MiB used, 30334 MiB free, 0 %, 49 C, 34.55 W, compute_cap 12.0
NVIDIA-SMI 617.42 | KMD 617.42 | CUDA UMD Version: 13.4 | WDDM | P8
Processes: only desktop C+G (chrome, ChatGPT, PowerToys, explorer …); no Img3D python; port 8000 not listening.
RAM: 127.1 GB visible, 88.5 GB free.
```
The Img3D backend uses `low_vram: true`, `attention_backend: sdpa`, `pipeline_type: 1536_cascade` (from `job_5b72e3768e6e/job.json` result.metadata). job.json does not record peak VRAM. Recent 1536/4096-tex runs took 133 to 202 s with default samplers and 423 to 579 s with advanced 25 to 50 steps, and **`exporting` is the dominant stage (77 to 330 s, bake about 98 s)**. That matters because per-part export multiplies this cost.
**[A]** With 30 GB free, a text-to-image model (SDXL fp16 about 7 to 10 GB at runtime) should run as a *separate stage*, unloaded or CPU-offloaded before TRELLIS.2 loads. With 88 GB of free RAM, `enable_model_cpu_offload()` (accelerate) is viable.

---

## 6. Existing model weights on this machine [V]

Search method: `backend\.model` fully; `%USERPROFILE%\.cache\huggingface\hub` and `\.cache\torch` fully; D:\ and E:\ top-level folders recursed to depth 5 or 6 for weight extensions (skipping `$RECYCLE.BIN`, `System Volume Information`, `SteamLibrary`, `WSL*`, and `E:\14-OCT-2026` itself); C:\ to depth 2; `C:\Users\Admin` (excluding AppData and .cache) to depth 4; AppData to depth 2 by folder name.
**No ComfyUI, Automatic1111, Forge, Fooocus, InvokeAI, StabilityMatrix, SwarmUI or Pinokio install exists** on C:, D: or E: within those depths. The only hits are `C:\Users\Admin\.openshot_qt\comfyui` (empty OpenShot integration folder) and Ollama.

### 6.1 Img3D's own `backend\.model` (16.80 GB)
| File | Size | Role / licence |
|---|---|---|
| `TRELLIS.2-4B\ckpts\*` (5 × 1.3B DiT bf16 at 2.41 GB, enc/dec 0.66 to 0.88 GB) | about 15 GB | generator, MIT (https://huggingface.co/microsoft/TRELLIS.2-4B) |
| `dinov3-vitl16-pretrain-lvd1689m\model.safetensors` | 1.13 GB | conditioning, DINOv3 licence (`license:other`, gated manual) |
| `BiRefNet\model.safetensors` | 423.9 MB | **foreground segmentation in use**, MIT (https://huggingface.co/ZhengPeng7/BiRefNet) |
| `TRELLIS-image-large\ckpts\ss_dec_conv3d_16l8_fp16.safetensors` | 140.8 MB | sparse-structure decoder, MIT |
| `RMBG-2.0\` | **no weights** (only `.cache`) | gated, CC BY-NC (`license:other`), still absent |
| `.hf_cache` | 0.00 GB | |

### 6.2 Candidates for segmentation or part parsing (outside Img3D)
| Path | Size | Model | Licence | Usable for part-aware? |
|---|---|---|---|---|
| `E:\14-07-2026_SSS\IMG_1233\models\Ultralytics\YOLO26\yolo26{s,m,l,x}-seg.pt` (+det/pose/obb/cls) | 22 to 136 MB each | YOLO26 instance-seg (COCO classes) | **AGPL-3.0** (https://huggingface.co/Ultralytics/YOLO26) | Not recommended (licence, needs ultralytics, coarse COCO classes with no eyes/hair/clothes) |
| `E:\14-07-2026_SSS\IMG_1233\models\facebook\detr-resnet-50-panoptic\pytorch_model.bin` | 164 MB | DETR panoptic (COCO) | Apache-2.0 | Weak: COCO panoptic has no face or garment parts |
| `D:\10-SEP-2026\DepthCloud\backend\.models\detr-resnet-50\model.safetensors` | 159 MB | DETR detection | Apache-2.0 | No |
| `D:\10-SEP-2026\DepthCloud\backend\.models\depth_pro.pt` | 1.77 GB | Apple Depth Pro | `apple-amlr` (research only) | Depth only; licence-restricted |
| `E:\14-07-2026_SSS\IMG_1233\models\zoedepth-nyu-kitti\model.safetensors`; `%USERPROFILE%\.cache\huggingface\hub\models--Intel--zoedepth-nyu{,-kitti}` | 1.29 to 1.32 GB, plus 1.28 GB | ZoeDepth | MIT | Depth only |
| `%USERPROFILE%\.cache\torch\hub\checkpoints\fasterrcnn_resnet50_fpn_coco-258fb6c6.pth`, `resnet50-0676ba61.pth` | 160 MB, 98 MB | torchvision | BSD-3 | No |

**There are no SAM, SAM2, face-parsing (BiSeNet/SegFormer-face), human-parsing (SCHP/ATR/LIP), Mask2Former/OneFormer, GroundingDINO, OWL or CLIPSeg weights anywhere in the searched scope.** A part segmenter would need a new download, which is the user's decision.

### 6.3 Candidates for text-to-image (prompt-to-3D front stage)
All are in the sibling project `D:\10-SEP-2026\T2I` (its own embedded Python 3.14.2 with **diffusers 0.40.0, transformers 5.18.0, huggingface-hub 1.33.0, accelerate 1.15.0, torch 2.14.1+cu132**; FastAPI on **port 7860, default bind 0.0.0.0** per its README L34-35).

| Path | Size | Model | Licence | Notes |
|---|---|---|---|---|
| `D:\10-SEP-2026\T2I\.models\animagine-xl-4.0\animagine-xl-4.0.safetensors` | 6.46 GB | Animagine XL 4.0 (SDXL, anime) | CreativeML Open RAIL++-M (`openrail++`) https://huggingface.co/cagliostrolab/animagine-xl-4.0 | Single-file SDXL; usable with diffusers 0.39 `StableDiffusionXLPipeline.from_single_file` **[A]**. Configs are cached in T2I's HF cache |
| `D:\10-SEP-2026\T2I\.models\anime-desire-illustrious\anime-2d-ill_v3.fp16.safetensors` | 6.46 GB | Illustrious-XL fine-tune | `openrail`, HF tag **not-for-all-audiences** | **Unsuitable as a product default** |
| `D:\10-SEP-2026\T2I\.models\OrangeMixs\Models\AbyssOrangeMix3\AOM3_orangemixs.safetensors` | 1.99 GB | SD1.5 merge | CreativeML OpenRAIL-M https://huggingface.co/WarriorMama777/OrangeMixs | SD1.5 quality is low for 3D conditioning |
| `D:\10-SEP-2026\T2I\.models\Noct-Q-Anime-Uncensored-Qwen-Image-2.1\NoctQA_V1_int8_convrot.safetensors` | 6.76 GB | Qwen-Image 2.1 int8 "convrot" | unknown | **Not loadable**: ComfyUI-only quant format, and the required text encoder and VAE (`.models/Qwen-Image-2.1/...`) are **absent**. NSFW-oriented name |
| `D:\10-SEP-2026\T2I\.cache\huggingface\hub\blobs\3e\3e6084…` (1160 MB), `blobs\6f\6fe1cd…` (319 MB) | 1.16 GB, 0.32 GB | SD1.5 `safety_checker` and `stabilityai/sd-vae-ft-mse` (MIT) | | SDXL-base and SD1.5 repos there hold **configs/tokenizers only** (symlinks), no UNet weights |
| `%USERPROFILE%\.cache\huggingface\hub\models--*` (animagine, illustrious, gemma-4, Qwen*, TRELLIS.2-4B, YOLO26, detr*) | 1 file each, 0 GB | metadata stubs only | | No weights |

### 6.4 VLM / LLM weights (prompt expansion, part-list reasoning, captioning) [V]
| Path | Size | transformers 4.57.6 support | Licence (HF tag) |
|---|---|---|---|
| `D:\25-03-2026\LLM2.0\.models\Qwen\Qwen3-8B\` (5 shards) | about 15.3 GB | `Qwen3ForCausalLM` yes | apache-2.0 |
| `D:\25-03-2026\LLM2.0\.models\Qwen\Qwen3-Omni-30B-A3B-Instruct\` (15 shards) | about 65.6 GB | `Qwen3OmniMoeForConditionalGeneration` yes | `other` |
| `D:\25-03-2026\LLM2.0\.models\gemma-4-E4B-it\model.safetensors` | 14.89 GB | **NO**: config `architectures: Gemma4ForConditionalGeneration`, `transformers_version: 5.5.0.dev0` | apache-2.0 |
| `C:\ollama\models` (manifest `registry.ollama.ai/library/qwen3.8/27b`) | 16.5 GB blobs | via Ollama only | |

---

## 7. Segmentation capability already inside transformers 4.57.6 (no pip install needed) [V]

`hasattr(transformers, X)` checks: **SamModel, SamHQModel, Sam2Model, Sam2VideoModel, EdgeTamModel, Mask2FormerForUniversalSegmentation, OneFormerForUniversalSegmentation, MaskFormerForInstanceSegmentation, EomtForUniversalSegmentation, SegformerForSemanticSegmentation, UperNetForSemanticSegmentation, CLIPSegForImageSegmentation, GroundingDinoForObjectDetection, MMGroundingDinoForObjectDetection, OwlViTForObjectDetection, Owlv2ForObjectDetection, Florence2ForConditionalGeneration, DepthAnythingForDepthEstimation, DPTForDepthEstimation, DepthProForDepthEstimation, ZoeDepthForDepthEstimation, DINOv3ViTModel, Dinov2Model, CLIPTextModel, T5EncoderModel, Qwen2_5_VLForConditionalGeneration, Qwen3VLForConditionalGeneration, Mistral3ForConditionalGeneration, Gemma3ForConditionalGeneration, SiglipModel, Siglip2Model** are all present. **Sam3Model, Sam3TrackerModel and Gemma4ForConditionalGeneration are absent** (they need transformers 5.x).
Implication: an image-space part segmenter (SegFormer face/human-parsing checkpoints, SAM2.1 via `Sam2Model`, GroundingDINO or OWLv2 for open-vocabulary boxes leading to SAM masks) can run **in the existing interpreter with zero new pip packages**. Only weights would need downloading (for example `facebook/sam2.1-hiera-large`, Apache-2.0, ungated per the HF API). Weight choice is a separate research item.

---

## 8. Frontend toolchain and node-graph libraries [V]

```
node v22.17.0   npm 11.16.0   pnpm: not installed
vite@8 engines: node "^20.19.0 || >=22.12.0"  -> OK
npm ls --depth=0: react 19.3.0, react-dom 19.3.0, zustand 5.0.15, vite 8.3.4, typescript 6.0.3,
  @react-three/fiber 9.8.1, @react-three/drei 10.7.9, three 0.186.1, axios 1.20.0, lucide-react 1.57.0,
  react-dropzone 20.1.3, oxlint 1.88.0, babel-plugin-react-compiler 1.0.0, @vitejs/plugin-react 6.1.2
```

| Library | `npm view` | Licence | Status |
|---|---|---|---|
| **@xyflow/react** | **12.12.0**, modified 2026-10-11; peerDeps `react >=17, react-dom >=17, @types/react >=17, @types/react-dom >=17`; deps `zustand ^4.4.0, classcat ^5.0.3, @xyflow/system 0.0.83` (d3-drag/zoom/selection/interpolate); unpacked 1.22 MB | MIT (https://github.com/xyflow/xyflow/blob/main/LICENSE) | **Recommended**: React-native, typed, maintained |
| litegraph.js | 0.7.18, last modified **2024-01-08** | MIT (https://github.com/jagenjo/litegraph.js) | Unmaintained, canvas/imperative, not React |
| @comfyorg/litegraph | 0.17.2, modified 2026-10-02, **`deprecated: "Package no longer supported…"`** | MIT | **Do not use** (ComfyUI folded it into its frontend repo) **[A, reason]** |
| rete | 2.0.6, modified 2025-06-30 | MIT | Alternative; needs extra React plugins |

**Lockfile probe [V]** (copies of `package.json` and `package-lock.json` in the scratchpad, `npm install @xyflow/react@12.12.0 --package-lock-only --ignore-scripts`): resolves with **no ERESOLVE or peer error**, adding 19 lock entries: `@xyflow/react 12.12.0`, `@xyflow/system 0.0.83`, nested `@xyflow/react/node_modules/zustand 4.5.7` (the same nested zustand 4.5.7 already exists under `tunnel-rat` from drei, so this is not a new pattern; the app's own zustand 5.0.15 is untouched), `classcat 5.0.5`, `d3-{color,dispatch,drag,ease,interpolate,selection,timer,transition,zoom}`, and `@types/d3-*`. **[A]** React Compiler compatibility with React Flow 12 is expected but unverified until a build runs.

---

## 9. Recommendations

1. **Keep the TRELLIS.2 stack frozen.** Add a constraints file (proposed `backend/constraints.txt`, not created) pinning `torch==2.14.1+cu130, torchvision==0.29.1+cu130, transformers==4.57.6, huggingface-hub==0.36.2, tokenizers==0.22.2, safetensors==0.8.0, numpy==2.4.1, pillow==12.3.0, opencv-python-headless==5.0.0.93, scipy==1.18.1, trimesh==5.1.1, timm==1.0.30`, and **forbidding** `opencv-python<0` and `opencv-contrib-python<0`. Verified with a scratch copy:
   ```
   uv pip install --dry-run --only-binary :all: -c constraints_probe.txt diffusers accelerate sentencepiece protobuf scikit-image rtree manifold3d fast-simplification
   -> Would install 10 packages, 0 uninstalls:
      accelerate 1.15.0, diffusers 0.39.0, fast-simplification 0.2.0, lazy-loader 0.6, manifold3d 3.5.4,
      protobuf 7.36.2, rtree 1.4.1, scikit-image 0.26.0, sentencepiece 0.2.2, tifffile 2026.10.15
   ```
   Proposed install command (NOT run; needs approval in the implementation phase):
   `& $py -s -m uv pip install --python $py --link-mode=copy --only-binary ":all:" -c backend\constraints.txt diffusers==0.39.0 accelerate==1.15.0 rtree==1.4.1 scikit-image==0.26.0 manifold3d==3.5.4`. Add `sentencepiece`/`protobuf` only if a T5- or Llama-tokenized text-to-image model is chosen. Then run `uv pip check`.
2. **Prompt-to-3D:** run text-to-image in the Img3D interpreter with **diffusers 0.39.0**, behind an engine-adapter interface like `backend/inference/`, so the model can be swapped later. Load it lazily and unload or CPU-offload it before TRELLIS.2 runs. The only good-licence local weights are **Animagine XL 4.0** (anime-styled, openrail++). A neutral default such as SDXL-base, which has no local UNet weights, would need a download, which is the user's decision. Calling the sibling T2I app over HTTP (port 7860) is possible but couples Img3D to an external folder with a different torch build (cu132) and a 0.0.0.0 bind, so it is not recommended as the primary path.
3. **Part segmentation:** use transformers-native classes (section 7) and **avoid ultralytics** (AGPL, cv2 clash), **mediapipe** (cv2 clash), **pymeshlab** (GPL-3), **open3d** (dependency bloat) and **sam2 pip** (no wheel). The existing BiRefNet stays the foreground-matte stage.
4. **Mesh part tooling:** trimesh 5.1.1, scipy, networkx and numpy are enough for connected-component splitting, part graphs, per-part GLB nodes and extras. Add rtree for proximity queries and manifold3d for watertight reassembly validation. Do not add xatlas (no cp314 wheel); reuse the cumesh/o_voxel UV path or per-part export via the existing exporter. Per-part export cost matters because export already dominates job time.
5. **Node UI:** use `@xyflow/react@12.12.0` (MIT). It resolves cleanly with React 19.3 and zustand 5. Keep graph state in a separate store and treat the nested zustand 4 as an internal xyflow detail.
6. **Hygiene:** every new worker or entry point must strip user-site (shadowing hub 1.32), as `main.py` does. The 13 pre-existing uncommitted files (11 modified, 2 untracked, all from the paused tuner work) should be committed or stashed by the user before the restructuring begins.

## 10. Verified vs assumed

**Verified:** all package presence and versions; all dry-run outputs; PyPI `requires_dist`, wheel filenames and sizes; HF licence tags (HF API); disk, GPU and RAM numbers; weight-file paths and sizes; transformers class availability; npm metadata; xyflow lock resolution; T2I sibling interpreter versions; Gemma-4 config architecture.
**Assumed:** SDXL single-file loading of the local Animagine checkpoint with diffusers 0.39.0 (not executed); runtime VRAM of SDXL and co-residency with TRELLIS.2; onnxruntime-gpu CUDA 13 compatibility; React Compiler and React Flow 12 interplay; the reason @comfyorg/litegraph was deprecated; sizes of not-yet-downloaded weights.
