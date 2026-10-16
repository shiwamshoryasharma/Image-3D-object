# Prompt-to-3D path: local text-to-image (and native text-to-3D) survey for Img3D

Date: 2026-10-16. Phase: READ-ONLY research (nothing installed, no models downloaded, backend not started).
Scope: how Img3D should add **prompt-to-3D** on top of the working TRELLIS.2 image-to-3D engine, on Windows 11 +
RTX 5090 32 GB + 130 GB RAM + embedded CPython 3.14.2 + torch 2.14.1+cu130 + transformers 4.57.6 (pinned).

Evidence legend used throughout:
- **[V]** verified by me in this session (read a local file, queried PyPI / Hugging Face Hub API / GitHub raw source).
- **[S]** sourced from a model card, licence text or web page (linked in section 13), not re-verified by running code.
- **[A]** assumption / estimate (no measurement on this machine; must be benchmarked before relying on it).

---------------------------------------------------------------------------------------------------

## 0. TL;DR

1. **Use text → image → existing TRELLIS.2.** Native text-to-3D is not viable here (TRELLIS v1 text models need
   spconv/kaolin/xformers/diff-gaussian-rasterization, none of which have Python 3.14 / cu130 / Windows wheels, and
   Microsoft itself recommends text→image→TRELLIS-image). TRELLIS.2 is image-only. [V]
2. **The hard stack constraint is `huggingface-hub`, not `transformers`.** transformers 4.57.6 requires
   `huggingface-hub<1.0` [V]. diffusers **0.39.0 (2026-07-03) is the last release accepting hub 0.36.2**
   (`>=0.34.0,<2.0`); 0.40.0 needs hub `>=1.23.0`, 0.41.0 needs `>=1.32.0` [V]. 0.39.0 already contains the fix for
   CVE-2026-44513 (trust_remote_code bypass, fixed in 0.38.0) [S].
3. **Primary: `Tongyi-MAI/Z-Image-Turbo`** (Apache-2.0, not gated, 6.15 B DiT + Qwen3-4B encoder, 8 NFEs, card says
   fits 16 GB VRAM; best Artificial-Analysis arena Elo (935) of everything that is permissive AND runs on our pinned
   stack) [V licence/size/stack, S Elo].
4. **Lightweight fallback: `black-forest-labs/FLUX.2-klein-4B`** (Apache-2.0, not gated, 3.88 B + Qwen3-4B
   encoder, 4 steps, ~13 GB VRAM per card, and it also does image editing / multi-reference — useful later for pose
   normalisation and extra reference views) [V licence/size/stack, S VRAM/steps].
5. **Install footprint for both: exactly two pure-Python wheels** — `diffusers==0.39.0`, `accelerate==1.15.0`.
   Every transitive dependency is already installed at a compatible version; no change to torch / transformers /
   huggingface-hub / tokenizers / safetensors pins [V by PyPI metadata + local site-packages listing; install itself
   not executed].
6. Optional later: Z-Image (base) for seed diversity + negative prompts (shares byte-identical text encoder + VAE with
   Turbo — only +12.3 GB) [V]; Animagine XL 4.0 (OpenRAIL++) as an anime-style SDXL option [V licence];
   HiDream-O1-Image (MIT, Elo 973, needs transformers==4.57.1 — same minor as our pin — custom code, skeleton/pose
   conditioning) as a quality/pose-control candidate to evaluate [V requirements/licence, S Elo].
7. Out for now: Qwen-Image-2.1 (research licence + transformers>=5.17 + diffusers 0.41), ERNIE-Image and GLM-Image
   (hard transformers>=5 gates in diffusers code), Ideogram 4 (non-commercial + transformers 5), FLUX.2 [dev]
   (non-commercial, 32 B + 24 B encoder), HunyuanImage 3.0 (83 B), all FLUX.1 [dev]-family (non-commercial),
   NoobAI-XL (explicit commercial prohibition) [V/S, details below].

---------------------------------------------------------------------------------------------------

## 1. Current Img3D facts that drive the design [V]

| Fact | Where |
|---|---|
| Pins: `torch==2.14.1+cu130` (l.12), `transformers==4.57.6` (l.47), `huggingface-hub==0.36.2` "must stay < 1.0 for transformers 4.57.x" (l.48), `safetensors==0.8.0` (l.49), `tokenizers==0.22.2` (l.50), `httpx==0.28.1` (l.62) | `backend/requirements.txt` |
| `diffusers` and `accelerate` are **not installed** in `backend/python/Lib/site-packages`; the user site (`%APPDATA%\Python\Python314\site-packages`) holds `huggingface_hub 1.32.0` (shadowing hazard, hence `python.exe -s`) | directory listing |
| Already installed transitive deps: PyYAML 6.0.3, regex 2026.9.29, requests 2.34.2, importlib_metadata 8.7.1, filelock 3.32.3, httpx 0.28.1, psutil 7.2.2, packaging 26.0, jinja2 3.1.6, fsspec 2026.7.0 | site-packages listing |
| transformers 4.57.6 itself requires `huggingface-hub<1.0,>=0.34.0`, `tokenizers<=0.23.0,>=0.22.0` | PyPI metadata |
| `low_vram: bool = True` default (TRELLIS.2 weights live in RAM, moved per stage) | `backend/config/settings.py:76` |
| After every run TRELLIS.2 models/`image_cond_model`/`rembg_model` are moved back to CPU and `torch.cuda.empty_cache()` runs (`_release_after_run`, `_free_gpu`); `unload()` drops the pipeline | `backend/inference/trellis/engine.py:289-328` |
| Single GPU thread: `ThreadPoolExecutor(max_workers=1, thread_name_prefix=GPU_THREAD_NAME)`; load and generate never overlap | `backend/workers/generation_worker.py:5,85` |
| Engine seam: `InferenceEngine` ABC (`base.py:234`), `GenerationRequest` (`base.py:142`), registry supports only `("trellis2",)` (`registry.py:22`) | `backend/inference/` |
| No prompt/text input anywhere in API/schemas/services (grep for "prompt" only hits Blender "user prompt" strings) | grep |
| `JobStage` values: queued, waiting_for_model, preprocessing, conditioning, sparse_structure, shape, texture, decoding, exporting, finalizing, done | `backend/models/enums.py:66-77` |
| TRELLIS.2 preprocessing: downscale so max side ≤ 1024, run BiRefNet (or use real alpha), bbox of `alpha > 0.8*255`, **square crop of side = max(bbox w,h) with zero margin**, premultiply RGB by alpha (black background) | `backend/third_party/TRELLIS.2/trellis2/pipelines/trellis2_image_to_3d.py:127-162`, `backend/inference/trellis/imaging.py` |
| TRELLIS.2 ships only `trellis2_image_to_3d.py` and `trellis2_texturing.py` pipelines (no text conditioning); README: ≥ 24 GB GPU | `backend/third_party/TRELLIS.2/` |
| Real jobs (metadata only): 1536/4096/2 M faces took 220.8 s total, of which exporting 165.5 s, shape 31.9 s, texture 15.7 s (`job_2496cdfeecc8/job.json`); others 423 s and 579 s | `backend/runtime/jobs/*/job.json` |
| Licensing context: THIRD_PARTY_NOTICES already states a working Img3D install is **non-commercial** because of nvdiffrast (NVIDIA Source Code License 1-Way Commercial) | `THIRD_PARTY_NOTICES.md:20` |
| Disk: E: has 712 GB free; `.model/` currently ~18 GB | `df`, `du` |

Implication: a T2I step adds seconds to a pipeline that already takes 3.5–10 minutes, so **quality and licence
matter far more than T2I speed**.

---------------------------------------------------------------------------------------------------

## 2. The compatibility constraint (diffusers ↔ huggingface-hub ↔ transformers) [V]

`setup.py` of each diffusers tag (fetched from GitHub raw) and PyPI release dates:

| diffusers | Released | `huggingface-hub` | `python_requires` | `safetensors` | `torch` (extra) | Usable with our pins? |
|---|---|---|---|---|---|---|
| 0.35.2 | 2025-10-15 | `>=0.34.0` | >=3.8 | >=0.3.1 | >=1.4 | yes, but CVE-2026-44513 |
| 0.36.0 | 2025-12-08 | `>=0.34.0,<2.0` | >=3.8 | >=0.3.1 | >=1.4 | yes, but CVE |
| 0.37.0/0.37.1 | 2026-03-05 / 03-25 | `>=0.34.0,<2.0` | >=3.10 | >=0.3.1 | >=1.4 | yes, but CVE |
| 0.38.0 | 2026-05-01 | `>=0.34.0,<2.0` | >=3.10 | >=0.8.0-rc.0 | >=1.4 | yes |
| **0.39.0** | **2026-07-03** | **`>=0.34.0,<2.0`** | >=3.10 | **>=0.8.0** (we have exactly 0.8.0) | >=2.6 | **yes — newest usable** |
| 0.40.0 | 2026-08-20 | `>=1.23.0,<2.0` | >=3.10 | >=0.8.0 | >=2.6 | **no** (hub 1.x breaks transformers 4.57) |
| 0.41.0 | 2026-10-06 | `>=1.32.0,<2.0` | >=3.10 | >=0.8.0 | >=2.6 | **no** |
| main (0.41.1.dev0) | — | `>=1.32.0,<3.0` | >=3.11 | — | — | no |

- diffusers 0.39.0 `install_requires` per PyPI: `importlib_metadata, filelock, httpx<1.0.0, huggingface-hub<2.0,>=0.34.0, numpy, regex!=2019.12.17, requests, safetensors>=0.8.0, Pillow` — all satisfied by what is installed. Wheel is `py3-none-any`.
- `transformers` is **not** an install requirement of diffusers (only the `test` extra: `transformers>=4.41.2`); compatibility is decided per pipeline by which text-encoder class it imports.
- Static check: every `huggingface_hub` symbol imported by diffusers 0.39.0 core (`hub_utils`, `constants`, `pipeline_utils`, `pipeline_loading_utils`, `modeling_utils`, `configuration_utils`, `dynamic_modules_utils`, `single_file_utils`) — `DDUFEntry, read_dduf_file, EntryNotFoundError, HfHubHTTPError, OfflineModeIsEnabled, RepositoryNotFoundError, RevisionNotFoundError, REGEX_COMMIT_HASH, HF_HUB_OFFLINE, HF_HUB_DISABLE_TELEMETRY, validate_hf_hub_args, split_torch_state_dict_into_shards, ModelCard, ModelCardData, create_repo, hf_hub_download, model_info, snapshot_download` — exists in the installed hub 0.36.2. (Static grep only; a real `import diffusers` smoke test is still required.)
- diffusers runtime self-check (`dependency_versions_check.py`, v0.39.0) only validates `python requests filelock numpy`.
- `accelerate==1.15.0` (2026-09-09, py3-none-any) requires `numpy>=1.17, packaging>=20.0, psutil, pyyaml, torch>=2.0.0, huggingface_hub>=0.21.0, safetensors>=0.4.3` — all satisfied. Needed for `enable_model_cpu_offload()` and fast `low_cpu_mem_usage` loading. In transformers 4.57.6 `low_cpu_mem_usage` is popped/ignored (`modeling_utils.py:4665`) and accelerate is only required for `device_map` — so adding accelerate should not change how TRELLIS.2 loads DINOv3 (`DINOv3ViTModel.from_pretrained(model_name)`, no device_map), but run the real-generation smoke test anyway.
- Security: CVE-2026-44513 (and related CVE-2026-44827, CVE-2026-45804): `trust_remote_code` bypass in `DiffusionPipeline.from_pretrained` via `custom_pipeline` / local snapshot custom components, fixed in 0.38.0 [S]. 0.39.0 is patched; still never pass `custom_pipeline`/`trust_remote_code` and load only from pinned local snapshots.

### 2.1 Text-encoder classes vs transformers 4.57.6 [V: grep of local transformers + diffusers v0.39.0 pipeline sources]

| diffusers 0.39.0 pipeline | Imports from transformers | Present in 4.57.6? | Hard gate in diffusers code |
|---|---|---|---|
| `ZImagePipeline` | `AutoTokenizer, PreTrainedModel` (repo uses `Qwen3Model` + `Qwen2Tokenizer`) | yes | none |
| `Flux2KleinPipeline` | `Qwen2TokenizerFast, Qwen3ForCausalLM` | yes | none |
| `Flux2Pipeline` (FLUX.2 dev) | `AutoProcessor, Mistral3ForConditionalGeneration` | yes | none |
| `FluxPipeline` / `FluxKontextPipeline` | CLIP + `T5EncoderModel, T5TokenizerFast` | yes | none |
| `StableDiffusion3Pipeline` | CLIP-L/G + T5 + Siglip | yes | none |
| `StableDiffusionXLPipeline` | CLIP | yes | none |
| `QwenImagePipeline` / `…EditPlus` / `…Layered` | `Qwen2_5_VLForConditionalGeneration, Qwen2Tokenizer(, Qwen2VLProcessor)` | yes | none |
| `HunyuanImagePipeline` | `ByT5Tokenizer, Qwen2_5_VLForConditionalGeneration, Qwen2Tokenizer, T5EncoderModel` | yes | none |
| `Lumina2Pipeline`, `SanaPipeline`, `SanaSprintPipeline` | `Gemma2PreTrainedModel, GemmaTokenizer(Fast)` | yes | none |
| `ChromaPipeline` | T5 | yes | none |
| `HiDreamImagePipeline` | CLIP + T5 + `LlamaForCausalLM` | yes | none |
| `CogView4Pipeline` | `GlmModel` | yes | none |
| `LongCatImagePipeline`, `Kandinsky5T2IPipeline` | `Qwen2_5_VLForConditionalGeneration` (+CLIP) | yes | none |
| `OvisImagePipeline` | `Qwen2TokenizerFast, Qwen3Model` | yes | none |
| `NucleusMoEImagePipeline` | `Qwen3VLForConditionalGeneration, Qwen3VLProcessor` | yes | none |
| `Krea2Pipeline` | `AutoTokenizer, Qwen3VLModel` | yes | none (repo config gated → format unverified) |
| `PRXPipeline` | `T5GemmaEncoder` | yes | none |
| `BriaFiboPipeline` | `SmolLM3ForCausalLM` | yes | none |
| `Ideogram4Pipeline` | `transformers.masking_utils.create_causal_mask` | yes (class) | encoder needs transformers 5.x per mirror card [S] |
| `ErnieImagePipeline` | `Mistral3Model`, **`Ministral3ForCausalLM`** | **no** (`Ministral3ForCausalLM` absent) | **`raise ImportError` if transformers < 5.0.0** (pipeline file l.36-37) |
| `GlmImagePipeline` | `GlmImageForConditionalGeneration, GlmImageProcessor` | **no** | classes are proxies unless transformers >= 5.0.0.dev0 (l.39) |
| `QwenImage21Pipeline` (Qwen-Image-2.1) | — | — | **only in diffusers 0.41.0**; card requires `transformers>=5.17` |

Config-format trap [V]: text-encoder `config.json` written by transformers 5.x uses `rope_parameters` (ERNIE-Image:
`transformers_version 5.2.0`, yarn rope inside `rope_parameters`, no top-level `rope_theta`). transformers 4.57 reads
`rope_theta`/`rope_scaling`, so such a config would silently fall back to default rope settings → wrong embeddings
without an error. Configs of the recommended models are 4.x-format: Z-Image-Turbo/Z-Image text encoder
`Qwen3ForCausalLM`, `transformers_version 4.51.0`, `rope_theta 1000000`; FLUX.2-klein-4B `Qwen3ForCausalLM`,
`4.56.1`, `rope_theta 1000000`; Qwen-Image-2512 `4.57.1`; LongCat-Image `4.41.2`; Nucleus-Image `4.57.0.dev0`.
**Rule for the app: refuse to register a model whose text-encoder config has `transformers_version >= 5`.**

### 2.2 Other stack items [V unless noted]

| Package | Status for CPython 3.14 / Windows | Needed? |
|---|---|---|
| sentencepiece 0.2.2 (2026-07-12) | cp314 + cp314t win_amd64 wheels | Only for slow T5/Gemma tokenizers. FLUX.1 and SD3.5 repos ship `tokenizer.json` for T5 → fast tokenizer works without it. Not needed for Z-Image/Klein (Qwen BPE: `vocab.json`, `merges.txt`, `tokenizer.json`). |
| protobuf 7.36.2 | abi3 win wheel | Not needed. (diffusers' own `protobuf>=3.20.3,<4` is only a test/training extra.) |
| bitsandbytes 0.50.2 | `py3-none-win_amd64` wheel | Not needed (32 GB is enough for bf16 primary/fallback); sm_120 kernel coverage not verified. |
| torchao 0.18.0 | py3-none-any | Not needed; compatibility with torch 2.14 unverified. diffusers has built-in fp8 *storage* (`ModelMixin.enable_layerwise_casting`, `modeling_utils.py:435` in 0.39.0) and `enable_group_offload` (`:520`) with no extra package. |
| xformers 0.0.35 | no cp314 Windows wheel; no sm_120 forward kernel on Windows (environment skill) | Not needed — diffusers uses torch SDPA by default. |
| flash-attn | no Windows/sm_120/py3.14 wheel (environment skill) | Not needed for diffusers. HiDream-O1 needs a one-line flag edit (see 4.3). |
| spconv-cu124/cu126 2.3.8 | wheels only cp39–cp313; no cu128/cu130 package on PyPI | Only for TRELLIS v1 (rejected). |
| kaolin | docs: Python ≤ 3.12, torch ≤ 2.8 (S) | Only for TRELLIS v1 (rejected). |

---------------------------------------------------------------------------------------------------

## 3. Model survey

Sizes = sum of the **diffusers sub-folders actually needed** (transformer/unet + text encoder(s) + VAE), excluding
duplicate root single-file checkpoints, from the HF API `?blobs=true` [V]. Params from the HF safetensors index [V].
Elo = Artificial Analysis text-to-image arena, fetched 2026-10-16 [S]. "Peak VRAM (offload)" = largest single
component in bf16 + ~2-3 GB activations at ~1 MP with `enable_model_cpu_offload()` [A]. Because TRELLIS.2 runs in
low-VRAM mode and returns to CPU after each run, **"fits alongside TRELLIS.2 sequentially" reduces to "fits alone in
32 GB"**, provided the T2I pipeline also returns to CPU (diffusers calls `maybe_free_model_hooks()` at the end of
`__call__`, e.g. `pipeline_z_image.py:595`, `pipeline_flux2_klein.py:922` [V]) and `torch.cuda.empty_cache()` runs.

### 3.1 Licence / access / size

| Model (HF repo) | Licence (link in §13) | Commercial use | Gated | Params (DiT/UNet) | Download (needed) |
|---|---|---|---|---|---|
| **Z-Image-Turbo** (`Tongyi-MAI/Z-Image-Turbo`, sha `f332072a`) | Apache-2.0 | yes | no | 6.15 B (stored fp32: 24.6 GB) | 32.8 GB |
| Z-Image base (`Tongyi-MAI/Z-Image`, sha `04cc4abb`) | Apache-2.0 | yes | no | 6.15 B (bf16 12.3 GB) | 20.5 GB (12.3 GB if Turbo already present: text encoder + VAE byte-identical [V sha256]) |
| **FLUX.2 [klein] 4B** (`black-forest-labs/FLUX.2-klein-4B`, sha `e7b7dc27`) | Apache-2.0 | yes | no | 3.88 B | 16.0 GB (skip root `flux-2-klein-4b.safetensors`, 7.75 GB) |
| FLUX.2 [klein] base 4B | Apache-2.0 | yes | no | 3.88 B (undistilled) | 16.0 GB |
| FLUX.2 [klein] 9B | FLUX Non-Commercial License | **no** | auto | 9.08 B | 34.7 GB |
| FLUX.2 [dev] | FLUX Non-Commercial License | **no** | auto | 32.2 B + Mistral-Small-3.1-24B encoder | 112.8 GB |
| FLUX.1 [schnell] | Apache-2.0 | yes | auto (click-through) | 11.9 B | 33.7 GB |
| FLUX.1 [dev] / Krea [dev] / Kontext [dev] | FLUX.1 [dev] Non-Commercial License v1.1.1 (outputs usable commercially, model use non-commercial; filter-or-review duty) | **no** | auto | 11.9 B | 33.7 GB each |
| SD 3.5 Large / Large Turbo | Stability AI Community License | only if annual revenue < US$1 M, registration, "Powered by Stability AI" attribution | auto | 8.15 B | ~27.6 GB (fp16 encoder variants) |
| SD 3.5 Medium | Stability AI Community License | same | auto | 2.47 B | ~16.3 GB |
| SDXL base 1.0 | CreativeML OpenRAIL++-M | yes (use restrictions) | no | 2.57 B | ~7 GB (fp16) |
| **Animagine XL 4.0** (`cagliostrolab/animagine-xl-4.0`, sha `2b7c1b39`) | CreativeML OpenRAIL++-M | yes (use restrictions) | no | 2.57 B (SDXL) | 6.95 GB |
| Illustrious-XL v0.1 (`OnomaAIResearch/Illustrious-xl-early-release-v0`) | Fair AI Public License 1.0-SD | yes, but derivatives must stay open (copyleft) | no | 2.57 B | ~7 GB |
| Illustrious-XL v1.1 / v2.0 | tagged SDXL licence / creativeml-openrail-m (single-file only, no diffusers layout) | per tag; verify | no | 2.57 B | 6.94 GB single file |
| NoobAI-XL 1.1 / V-pred 1.0 | FAIPL-1.0-SD **+ "We prohibit any form of commercialization"** | **no** | no | 2.57 B | 6.95 GB |
| Qwen-Image / -2512 / -Edit-2511 / -Layered | Apache-2.0 | yes | no | 20.4 B + Qwen2.5-VL-7B | 57.7 GB each |
| Qwen-Image-2.1 / -2.1-Turbo | Qwen Research License (commercial needs separate licence) | **no** | no | 7.1 B + 17.5 GB encoder | 33.1 / 32.4 GB |
| HunyuanImage-2.1 (`hunyuanvideo-community/HunyuanImage-2.1-Diffusers`) | Tencent Hunyuan Community: **territory excludes EU, UK, South Korea**; >100 M MAU needs licence; outputs may not improve other models | yes outside excluded territories | no | 17.4 B | 53.1 GB |
| HunyuanImage-3.0 | Tencent Hunyuan Community | as above | no | 83 B MoE | 168.5 GB |
| Lumina-Image-2.0 | Apache-2.0 (+ Gemma Terms for Gemma-2-2B encoder) | yes | no | 2.6 B | 21.2 GB |
| PixArt-Σ XL 1024 | OpenRAIL++ | yes | no | 0.61 B | 21.8 GB (T5 fp32) |
| SANA 1.6B / SANA 1.5 4.8B / SANA-Sprint 1.6B | card now Apache-2.0 (+ Gemma Terms) | yes | no | 1.6 / 4.8 / 1.6 B | 25.8 / 15.9 / 9.7 GB |
| Chroma1-HD | Apache-2.0 | yes | no | 8.9 B | 27.5 GB |
| HiDream-I1 Full/Fast | MIT (+ Llama-3.1-8B encoder, Llama licence, gated) | yes (Llama terms) | Llama gated | 17.1 B | 47.2 GB + Llama 16 GB |
| **HiDream-O1-Image** (`HiDream-ai/HiDream-O1-Image`) | MIT (GitHub + card) | yes | no | 8.8 B (Qwen3-VL-8B based, pixel-space) | 35.2 GB (fp32 single repo) |
| CogView4-6B (`zai-org/CogView4-6B`) | Apache-2.0 (GLM-4-9B encoder) | yes | no | 6.4 B | 31.1 GB |
| LongCat-Image | Apache-2.0 | yes | no | 6 B | 29.3 GB |
| Ovis-Image-7B (`ATH-MaaS/Ovis-Image-7B`) | Apache-2.0 | yes | no | 7.4 B | ~21.8 GB |
| Kandinsky-5.0-T2I-Lite | MIT | yes | no | 6.0 B | 32.2 GB |
| Nucleus-Image (`NucleusAI/Nucleus-Image`) | Apache-2.0 | yes | no | 17 B MoE (~2 B active) | 51.6 GB |
| ERNIE-Image (+Turbo) | Apache-2.0 | yes | no | 8.0 B | 31.6 GB |
| GLM-Image | MIT | yes | no | 7 B DiT + 9 B AR | 35.8 GB |
| Krea 2 Raw / Turbo | Krea 2 Community License: commercial only if company-wide revenue < US$1 M; deployers must filter content | capped | auto | 12.8 B | 35.7 GB |
| Ideogram 4 (`ideogram-ai/ideogram-4-nf4`) | Ideogram 4 Non-Commercial | **no** | auto | 9.3 B (nf4) | 16.1 GB |
| BRIA FIBO | CC BY-NC 4.0 | **no** | auto | 8.3 B | 25.5 GB |
| PRX 1024 (Photoroom) | Apache-2.0 | yes | no | 1.17 B | 20.7 GB |

### 3.2 VRAM, speed, 3D-suitability, stack status

| Model | bf16 resident (all comps) | Peak VRAM w/ model offload [A] | Default / recommended steps | Elo [S] | 3D-friendly single-subject renders [A unless noted] | Stack status on our pins |
|---|---|---|---|---|---|---|
| **Z-Image-Turbo** | 12.3 + 8.0 + 0.2 ≈ 20.5 GB | ~14-15 GB | 8 NFE (`num_inference_steps=9`, `guidance_scale=0.0`) [S card] | **935** | Very good prompt following (Qwen3-4B), photoreal + stylised; no negative prompt (CFG 0); low seed diversity [S card table] — fine for "one clean canonical render", weaker for "show me 4 options" | **OK** (diffusers 0.36+; encoder config 4.51.0) |
| Z-Image base | same | ~14-15 GB | 28-50 steps, CFG 3-5, negative prompt [S] | 889 | Higher diversity, negative prompts usable | **OK** |
| **FLUX.2 klein 4B** | 7.75 + 8.0 + 0.2 ≈ 16 GB | ~10-11 GB (card: "~13 GB VRAM") | 4 steps, `guidance_scale=1.0` [S card] | 838 | Good spatial adherence; T2I + edit + multi-reference in one model (`image=` arg, `pipeline_flux2_klein.py:616` [V]) | **OK** (diffusers 0.37+; encoder config 4.56.1) |
| FLUX.2 klein 9B | 18.2 + 16.4 ≈ 35 GB | ~20 GB | 4 | 930 | as 4B, better | OK stack, **NC licence** |
| FLUX.2 dev | 64 + 48 GB | does not fit without 4-bit | 50 | — | strong | stack OK, **too big + NC** |
| FLUX.1 schnell | 23.8 + 9.5 + 0.4 | ~26 GB (fp8 storage ~14 GB) | 4 | 741 | Superseded by klein 4B | OK (needs no sentencepiece: tokenizer.json present) |
| FLUX.1 dev family | same | ~26 GB | 28-50 | 789 (Krea dev 825) | — | OK stack, **NC** |
| SD 3.5 Large / Turbo | 16.3 + 9.5 + 1.6 | ~18-19 GB | 28-40 / 4 | n/a | Mediocre anatomy on full bodies; T5 + CLIP 77-token limit for CLIP part | OK, revenue-capped licence |
| SD 3.5 Medium | 4.9 + 9.5 + 1.6 | ~11 GB | 28-40 | n/a | weaker | OK |
| SDXL / Animagine XL 4.0 | 5.1 + 1.6 | ~8-9 GB | 25-28, CFG 4-7 [S card] | n/a | Best-in-class *anime look* in this list; tag prompting, CLIP 77 tokens, weak at abstract pose words; occasionally crops feet | **OK** (CLIP only — lowest risk) |
| Qwen-Image(-2512) | 40.9 + 16.6 | **>32 GB** in bf16 → fp8 storage (~20.4 GB) + offload | 50 (Lightning LoRAs need `peft`) | 896 | Excellent instruction following, slow | OK stack, heavy |
| Qwen-Image-Layered | same | same | — | — | Decomposes an image into RGBA layers (relevant to the *part-aware* milestone, see §10) | OK stack, heavy |
| Qwen-Image-2.1 | 14.2 + 17.5 | ~18 GB | 40 / Turbo 8 | **1040** | Best quality here; native RGBA (transparent background) output | **BLOCKED**: diffusers 0.41 + transformers>=5.17; research licence |
| HunyuanImage-2.1 | 34.9 + 16.6 + 0.9 | >32 GB bf16 → fp8 + offload | 50 (2K native) | n/a | good | OK stack, territory licence |
| HunyuanImage-3.0 | 168 GB | impossible (≥3×80 GB) | — | 931-987 | — | no |
| Lumina-Image-2.0 | 5.2 + 5.2 (bf16) | ~8 GB | 30-50 | n/a | dated | OK |
| PixArt-Σ | 1.2 + 9.5 | ~11 GB | 20 | n/a | dated | OK |
| SANA-Sprint 1.6B | 3.2 + 5.2 | ~7 GB | 1-4 | n/a | fast previews, weaker characters | OK |
| Chroma1-HD | 17.8 + 9.5 | ~20 GB | 35, CFG 5 | n/a | good, uncensored base | OK |
| HiDream-I1 | 34.2 + 9.5 + 2.8 + 16 (Llama) | fp8 needed | 50 / Fast 16 | n/a | good | OK stack, gated Llama |
| **HiDream-O1-Image** | ~17.6 GB bf16 (8.8 B) | ~20-24 GB [A] | 50 full (CFG 5) / 28 Dev | **973** (Dev: 808) | High; **skeleton (openpose) + layout conditioning** in its IP pipeline [S card] → can force A-pose | Not diffusers; custom code; `requirements.txt` pins **`transformers==4.57.1`** [V] (same minor as ours); uses `from diffusers import FlowMatchEulerDiscreteScheduler`; flash-attn optional but `models/pipeline.py:341` hard-codes `"use_flash_attn": True` → must be set False on Windows [V] |
| CogView4 | 12.7 + 17.6 | ~14 GB | 50 | n/a | OK | OK |
| LongCat-Image | 12.5 + 16.6 | ~17 GB [S card] | 50 | 854 | OK | OK |
| Ovis-Image-7B | 14.7 + 6.9 | ~16 GB | 50 | n/a | OK | OK |
| Kandinsky-5 Lite | 12.0 + 16.6 + 3.4 | ~14 GB | 50 | n/a | OK | OK |
| Nucleus-Image | 33.9 + 17.5 | fp8 needed | 50, CFG 8 | n/a | OK | OK (Qwen3-VL config 4.57.0.dev0) |
| ERNIE-Image(-Turbo) | 16.1 + 7.7 + 7.7 (PE) | ~18 GB | 50 / 8 | 904 / 912 | OK | **BLOCKED** (transformers>=5 ImportError) |
| GLM-Image | 13.9 + 20.2 | — | 50 | n/a | — | **BLOCKED** (transformers>=5) |
| Krea 2 Turbo / Raw | 25.6 + 8.9 | ~28 GB bf16 (tight) → fp8 storage ~15 GB | 8 (CFG 0) / 52 (CFG 3.5) [S] | 1012-1032 (variant mapping unclear) | high | Pipeline in 0.39.0, `Qwen3VLModel` present; **config gated → 4.x/5.x format unverified** |
| Ideogram 4 nf4 | 16 GB | — | 48 | 1000-1023 | high | **BLOCKED** (transformers 5 + NC + fused-attention layout caveat) [S] |
| BRIA FIBO | 16.6 + 6.2 | ~19 GB | 30 | n/a | JSON prompting | OK stack, **NC** |

Speed [A, not measured]: on an RTX 4090, Z-Image-Turbo ≈ 2.3 s per 1024² image (8 steps) and klein-4B ≈ 1-2 s are
the commonly cited figures [S]; a 5090 should be at or below that. With `enable_model_cpu_offload()` add roughly
0.5-1.5 s per image for PCIe transfers (≈ 20 GB moved for Z-Image-Turbo), and 5-15 s for the first load from NVMe.
50-step CFG models (Z-Image base, Qwen-Image, HiDream-O1 full) are 10-25x slower per image (tens of seconds). All
negligible next to TRELLIS.2's 220-580 s.

### 3.3 Short notes on families (what matters for 3D)

- **Qwen3/Qwen2.5-VL-encoder DiTs (Z-Image, klein, Qwen-Image, LongCat, Ovis, Kandinsky 5, Nucleus):** natural-language
  instructions such as "standing in an A-pose, arms 30° from the torso, full body head to toe, plain light-gray
  background, no cast shadows" are followed far more reliably than by CLIP-conditioned SDXL [A, consistent with their
  benchmark claims]. Distilled variants (Z-Image-Turbo, klein distilled, Krea 2 Turbo) run without CFG → **no negative
  prompts**; every constraint must be phrased positively.
- **SDXL anime finetunes:** best anime aesthetics, tiny (≈7 GB), CLIP-only (zero transformers risk), but tag-based and
  77-token-limited; pose control is weak without ControlNet. Licences differ sharply: Animagine XL 4.0 OpenRAIL++
  (usable), Illustrious v0.1 FAIPL (copyleft), NoobAI explicit commercial ban.
- **FLUX.1 generation:** superseded on quality (Elo 741-825) and all non-schnell variants are non-commercial.
- **Edit-capable models** (FLUX.2 klein, Qwen-Image-Edit-2511, HiDream-O1 IP, FLUX.1 Kontext) open a second use beyond
  prompt-to-3D: *normalising user photos* (re-pose to A-pose, remove background clutter, generate a consistent back
  view for the existing `back`/`left`/`right` reference slots). Keep this as a future node; not part of this milestone.

---------------------------------------------------------------------------------------------------

## 4. Native text-to-3D alternatives

### 4.1 TRELLIS v1 text models [V metadata, S README]
- `microsoft/TRELLIS-text-base` (342 M, 0.70 GB), `-large` (1.1 B, 2.28 GB), `-xlarge` (2.0 B, 4.13 GB), MIT.
  `pipeline.json` (xlarge) = `TrellisTextTo3DPipeline` with `ss_flow_txt_dit_XL_16l8_fp16` + `slat_flow_txt_dit_XL_64l8p2_fp16`,
  plus **decoders pulled from `JeffreyXiang/TRELLIS-image-large`** (ss_dec, slat_dec_gs, slat_dec_rf, slat_dec_mesh) and
  `openai/clip-vit-large-patch14` as text encoder; 25 steps, CFG 7.5.
- Dependencies: sparse backend `spconv` (default) or `torchsparse`; attention `flash-attn` or `xformers`; `kaolin`,
  `diffoctreerast`, mip-splatting `diff-gaussian-rasterization`, `nvdiffrast`. README: "tested only on Linux".
  On our machine: spconv has no cp314 and no cu128/cu130 build; kaolin supports Python ≤ 3.12 / torch ≤ 2.8; xformers has
  no sm_120 Windows forward kernel; flash-attn has no Windows/sm_120/py3.14 wheel → would require a separate Python
  3.12 + torch 2.7-2.8/cu128 environment and source builds of 3-4 CUDA extensions.
- Quality: 64³ SLat / FlexiCubes at 256 — well below TRELLIS.2 1536³ cascades, and the TRELLIS README itself says:
  *"It is always recommended to do text to 3D generation by first generating images using text-to-image models and
  then using TRELLIS-image models"* (text-conditioned models are "less creative and detailed").
- Verdict: **do not integrate.** Text features cannot be fed into TRELLIS.2 either (its flow models are trained on
  DINOv3 image features).

### 4.2 Others
- **Hunyuan3D-2 / 2.1** (Tencent Hunyuan 3D Community licence, excludes EU/UK/South Korea): image-to-3D; their
  "text-to-3D" is also text→HunyuanDiT image→3D. 2.1 = 14.9 GB. No benefit over our TRELLIS.2 path.
- **Roblox Cube 3D v0.1 / v0.5** (OpenRAIL, 8.27 GB): native text-to-*shape* (no texture), lower fidelity. Possible
  curiosity node later; not a replacement.
- **Hunyuan3D-Part** (P3-SAM + X-Part, 9.5 GB, HF card has no licence tag) and **Qwen-Image-Layered** (Apache-2.0)
  are relevant to the *part-aware decomposition* research, not to prompt-to-3D — flagged for that report.

---------------------------------------------------------------------------------------------------

## 5. Recommendation

### 5.1 Primary — Z-Image-Turbo (`Tongyi-MAI/Z-Image-Turbo`, revision `f332072aa78be7aecdf3ee76d5c247082da564a6`)
Why: Apache-2.0, not gated; highest arena Elo (935) among models that are both permissive and loadable with
transformers 4.57.6 + hub 0.36.2; encoder config written by transformers 4.51.0; pipeline present since diffusers
0.36 and in 0.39.0; 8 NFEs; bf16 resident ≈ 20.5 GB so it can even stay fully on GPU between back-to-back T2I calls,
or use model offload (~14-15 GB peak) when TRELLIS.2 is next.
Settings: `torch_dtype=torch.bfloat16`, `num_inference_steps=9` (= 8 DiT forwards), `guidance_scale=0.0`,
1024×1024 for objects, 896×1152 (or 864×1152) for full-body characters (dimensions multiple of 16 [A]).
Known weakness: low seed diversity [S] → for "4 candidates" either vary the prompt suffix (camera/lighting phrasing)
or offer **Z-Image base** as a "Variations" option (same encoder + VAE files, +12.3 GB, CFG + negative prompts).

### 5.2 Lightweight fallback — FLUX.2 [klein] 4B (`black-forest-labs/FLUX.2-klein-4B`, revision `e7b7dc27f91deacad38e78976d1f2b499d76a294`)
Why: Apache-2.0, not gated, ~13 GB VRAM, 4 steps (`guidance_scale=1.0`), Qwen3-4B encoder (config 4.56.1),
pipeline in diffusers ≥ 0.37. Also an *editing/multi-reference* model, which gives the future node graph an
"Image Edit / Normalise Pose / Extra View" node without another download. Lower Elo (838) is the trade-off.
Download only the diffusers sub-folders (`allow_patterns`) to skip the 7.75 GB root single-file checkpoint.

### 5.3 Optional (behind the model registry, off by default)
| Option | When | Notes |
|---|---|---|
| Z-Image base | Variations / negative prompts | shares text_encoder + vae with Turbo (byte-identical LFS sha256: `328a91d3…`, `6cd087b3…`, `7ca841ee…`, vae `f5b59a26…`) |
| Animagine XL 4.0 (sha `2b7c1b39…`) | Anime style preset | OpenRAIL++; tag prompts; CLIP-only; 6.95 GB |
| HiDream-O1-Image | Quality + pose control evaluation | MIT, Elo 973; vendor `HiDream-ai/HiDream-O1-Image` as `backend/third_party/` submodule like TRELLIS.2; patch `use_flash_attn` → False; 35 GB fp32 download; slower (50 steps CFG); skeleton conditioning can enforce a canonical A-pose |
| Qwen-Image-2512 | Hard prompts / text on objects | Apache-2.0; needs fp8 storage via `enable_layerwise_casting` + offload; 57.7 GB download |
| Krea 2 Turbo | User who accepts revenue-capped licence | gated; verify text-encoder config format first |

### 5.4 Rejected (for this milestone)
Qwen-Image-2.1/-Turbo (research licence; diffusers 0.41 + transformers≥5.17 → needs hub 1.x), ERNIE-Image, GLM-Image,
Ideogram 4 (transformers 5; Ideogram also NC), FLUX.2 dev / klein 9B / FLUX.1 dev family / BRIA FIBO (non-commercial),
NoobAI (commercial ban), HunyuanImage 3.0 (83 B), HunyuanImage 2.1 (territory carve-out, 53 GB, no edge over Z-Image),
SD 3.5 (revenue cap + registration + attribution, weaker), PixArt-Σ / Lumina 2 / SANA (dated quality; Gemma terms),
HiDream-I1 (gated Llama encoder, 63 GB), TRELLIS v1 text (section 4.1).

If the project ever needs a transformers-5 model (Qwen-Image-2.1 etc.), do **not** upgrade the shared environment;
run it in a **sidecar process** with its own site-packages overlay (same embedded python, `-s`, sys.path bootstrapped
in code), GPU handed over sequentially by the GPU worker — the same pattern Img3D already uses for Blender
(`launcher.BlenderProcessRunner`). Not needed for the primary/fallback.

---------------------------------------------------------------------------------------------------

## 6. Exact pins and install procedure (NOT executed — needs the user's go-ahead)

Add to `backend/requirements.txt` (new section "Text-to-image (prompt-to-3D)"):
```
diffusers==0.39.0          # last release allowing huggingface-hub<1.0 (0.40.0 needs hub>=1.23, 0.41.0 hub>=1.32)
accelerate==1.15.0         # enable_model_cpu_offload / fast loading; requires huggingface_hub>=0.21
```
Unchanged and must stay: `torch==2.14.1+cu130`, `transformers==4.57.6`, `huggingface-hub==0.36.2`,
`tokenizers==0.22.2`, `safetensors==0.8.0`, `httpx==0.28.1`.
Optional only if a T5/Gemma *slow* tokenizer model is added later: `sentencepiece==0.2.2`.
Do not add: xformers, flash-attn, bitsandbytes, torchao, peft (until a LoRA is actually used).

Procedure (embedded interpreter only, per environment skill):
```powershell
$py = "E:\14-OCT-2026\Img3D\backend\python\python.exe"
# constraints file (e.g. test\env_setup\constraints_t2i.txt) pinning torch/torchvision/transformers/huggingface-hub/tokenizers/safetensors
& $py -s -m uv pip install --python $py --link-mode=copy --dry-run -c <constraints> diffusers==0.39.0 accelerate==1.15.0
# expect: exactly 2 packages to install, 0 upgrades/downgrades; then run without --dry-run
& $py -s -m uv pip check --python $py
& $py -s -c "import diffusers, accelerate, transformers, huggingface_hub; from diffusers import ZImagePipeline, Flux2KleinPipeline; print(diffusers.__version__, accelerate.__version__, transformers.__version__, huggingface_hub.__version__)"
```
Then re-run the existing TRELLIS.2 real-generation smoke test (`.claude/skills/img3d-run-and-test`) to prove the
image-to-3D path is unaffected.

Model storage (offline at runtime, like the aux models): `backend/.model/t2i/<model_id>/` via
`snapshot_download(repo_id, revision=<pinned sha>, local_dir=..., allow_patterns=["model_index.json", "scheduler/*",
"text_encoder/*", "tokenizer/*", "transformer/*", "vae/*"])`, then `Pipeline.from_pretrained(local_dir,
torch_dtype=torch.bfloat16, local_files_only=True)`; never `custom_pipeline`, never `trust_remote_code`.

---------------------------------------------------------------------------------------------------

## 7. Integration design (keeps the image-to-3D path untouched)

```
POST prompt ─► T2I job (GPU worker, same single thread) ─► N candidate PNGs ─► user picks (or auto-pick) ─►
reference set (slot "front", provenance stored) ─► existing POST /jobs ─► TRELLIS.2 (unchanged) ─► GLB
```
- **Seam:** new package `backend/inference/t2i/` (mirrors `inference/trellis/`): `base.py` (`TextToImageEngine` ABC:
  `status()`, `capabilities()`, `load()`, `unload()`, `generate(request, reporter, cancel) -> list[Path]`),
  `registry.py` (model registry, lazy import of diffusers so the API starts without it), `diffusers_engine.py`
  (Z-Image / klein / SDXL adapters). API/services never import torch/diffusers (same non-negotiable as TRELLIS).
- **Pipeline:** `backend/pipelines/prompt_to_image.py` builds the prompt from a preset template (section 8), runs the
  engine, validates the result (8.4), stores candidates.
- **GPU sequencing:** run T2I on the *same* `ThreadPoolExecutor(max_workers=1)` as TRELLIS.2
  (`workers/generation_worker.py:85`) so they never overlap. Before T2I: TRELLIS.2 is already on CPU in low-VRAM mode
  (`engine.py:312-328`); if `low_vram=False`, call an explicit offload/unload first (TRELLIS.2 resident + Z-Image peak
  would exceed 32 GB [A]). After T2I: `maybe_free_model_hooks()` (automatic at end of `__call__`) +
  `torch.cuda.empty_cache()`. Keep the T2I pipeline cached in CPU RAM (≈ 20.5 GB for Z-Image-Turbo, 16 GB for klein;
  130 GB RAM available) with an idle-unload timer setting.
- **Progress / cancel:** `callback_on_step_end` exists in both pipelines (`pipeline_z_image.py:318,570`;
  `pipeline_flux2_klein.py:631`) → `reporter.step(i+1, total)`; on cancel set `pipe._interrupt = True` (both expose
  `interrupt`, `pipeline_z_image.py:294`, `pipeline_flux2_klein.py:609`) then raise `JobCancelledError`.
- **Seeds:** one `torch.Generator("cuda").manual_seed(seed + i)` per candidate; record each seed.
- **Enums:** append `JobStage.TEXT_TO_IMAGE = "text_to_image"` (append only; existing values unchanged per contract).
- **Proposed API (to be added to `api-contract.md` after approval):**
  - `GET /api/v1/t2i/models` → registry entries with `installed`, `size_bytes`, `vram_estimate_gb`, `default_steps`,
    `supports_negative_prompt`, `supports_editing`, and a `license` object (section 9).
  - `POST /api/v1/t2i/models/{model_id}/install` `{ "accept_license": true, "license_version": "…" }` → 202; WS events
    `t2i_model.install_progress`.
  - `POST /api/v1/t2i/generations` `{ prompt, preset: "character"|"object"|"raw", style: "realistic"|"stylized"|"anime",
    model_id?, num_images: 1..4, seed, randomize_seed, width?, height?, steps?, guidance?, negative_prompt? }` → 202,
    queued on the shared GPU queue; result = candidates (`runtime/t2i/<gen_id>/cand_<n>.png` + `generation.json`).
  - `POST /api/v1/t2i/generations/{gen_id}/candidates/{n}/use` `{ reference_set_id?: null|str, slot: "front" }` →
    copies through `ReferenceStore` (same validation/thumbnail path as uploads) → returns the reference set; then the
    existing `POST /api/v1/jobs`.
  - Optional one-shot convenience later: `POST /api/v1/jobs` with `prompt_source` instead of `reference_set_id`
    (T2I stage then image-to-3D stages in one job).
- **Node UI mapping:** `Prompt` node → `Text-to-Image` node (model, preset, style, seed, N) → `Input Image` →
  `Segmentation` → `Image-to-3D` → `Part Extraction` → `Export`. Same REST calls as the default/mobile/tablet UIs.

---------------------------------------------------------------------------------------------------

## 8. TRELLIS-friendly prompt templates

Design rules derived from TRELLIS.2 preprocessing [V code, §1] and generic image-to-3D guidance [S]:
square crop tight around the alpha bbox (so the subject must be fully inside the frame — cut feet are unrecoverable);
background removed by BiRefNet with `alpha > 0.8` (plain, contrasting, seamless background; no floor or cast shadow
that could be kept as foreground); lighting gets baked into albedo (flat, even, diffuse light); conditioning is a
single view (front view, minimal perspective). A-pose (not T-pose) keeps the bbox compact (body occupies more of the
square crop), separates arms from torso (helps part segmentation and later rigging) and avoids fused hands/hips.

### 8.1 Character preset (Z-Image-Turbo / klein / Qwen-family; positive-only, no CFG)
```
A full-body 3D character reference image of {subject}. Exactly one character, standing upright in a neutral A-pose:
arms straight and angled about 30 degrees away from the torso, hands open and relaxed, feet shoulder-width apart and
flat on the ground. Front view, facing the camera directly, eye-level camera, telephoto lens with minimal perspective
distortion. The whole body is visible from the top of the head to the soles of the shoes, centered in the frame with
empty space on all sides. Neutral expression, eyes open, mouth closed, hair not covering the face. Plain seamless
light-gray studio background, no floor line, no props, no text. Soft, even, diffuse lighting with no cast shadows and
no strong highlights. Sharp focus everywhere. {style_clause}
```
Resolution: 896×1152 (portrait; after TRELLIS downscale to ≤1024 the figure still spans ~900-1000 px).

### 8.2 Object preset
```
A product-style 3D reference image of {subject}. A single complete object, fully visible and centered in the frame
with empty space on all sides, three-quarter front view from slightly above eye level. Plain seamless light-gray
studio background, no other objects, no text. Soft, even, diffuse lighting with no cast shadows. Sharp focus,
true-to-life materials. {style_clause}
```
Resolution: 1024×1024.

### 8.3 Style clauses and part-aware hint
- realistic: `Photorealistic, physically accurate materials and colors.`
- stylized: `Stylized 3D game-asset render, clean readable shapes, hand-painted textures.`
- anime: `Anime-style character, clean cel shading, crisp line art.`
- part-aware hint (optional toggle for the decomposition milestone): `Hair, clothing layers and accessories are
  clearly distinct from each other and from the skin; both eyes clearly visible.`
- If the subject is white/very light (e.g. "white robe", "silver armor"), switch background to `plain seamless
  medium-gray` (simple keyword heuristic or a UI toggle) so BiRefNet keeps a clean edge.

### 8.4 Negative prompt (only for CFG models: Z-Image base, SDXL/Animagine, SD 3.5, HiDream-O1 full)
```
cropped, cut-off feet, out of frame, multiple characters, multiple views, character sheet, turnaround, grid, text,
watermark, logo, busy background, scenery, floor, ground shadow, cast shadow, dramatic lighting, rim light, bokeh,
depth of field, motion blur, extreme perspective, foreshortening, dynamic pose, sitting, crossed arms, hands in
pockets, holding object, back view, side view
```
SDXL/Animagine tag variant [A, tag efficacy unverified]: `1girl|1boy, {character tags}, solo, full body, standing,
arms at sides, straight-on, looking at viewer, simple background, grey background, {style tags}, masterpiece, high
score, great score, absurdres` (Animagine 4.0 card: 28 steps, CFG 5).

### 8.5 Automatic 3D-readiness check (cheap, reuses BiRefNet already in the TRELLIS.2 pipeline)
After generation, run the matte once and flag/regenerate a candidate if: (a) the alpha bbox touches the image border
(cropped limbs), (b) more than one large connected foreground component (multiple subjects/props), (c) foreground
area < ~8 % or > ~70 % of the frame, (d) aspect of bbox inconsistent with preset (e.g. character wider than tall).
Store the matte as RGBA so the image-to-3D job can skip a second background-removal pass (real alpha is already
honoured by `imaging.needs_upstream_preprocess`).

Prompt expansion (future, [A]): klein's text encoder is a full `Qwen3ForCausalLM`, so a short user prompt could be
rewritten into the template by the same weights without another download; Z-Image's repo stores `Qwen3Model`
(text-encoder shards 1-2 are byte-identical to `Qwen/Qwen3-4B`, shard 3 differs) — not for generation.

---------------------------------------------------------------------------------------------------

## 9. Exposing model choice and licensing in the app

- **Registry with licence metadata** (backend source of truth, e.g. `backend/config/t2i_models.py`): `id`, `display_name`,
  `repo_id`, `revision` (pinned sha), `pipeline_class`, `allow_patterns`, `download_size_bytes`, `vram_estimate_gb`,
  `default_steps`, `guidance`, `supports_negative_prompt`, `supports_editing`, `max_tokens`, and
  `license = { id, name, url, commercial: "allowed" | "revenue_capped" | "non_commercial" | "research_only",
  gated: bool, requires_acceptance: bool, territory_excluded: [..], content_filter_required: bool,
  attribution: str|null }`. Ship only Apache/MIT/OpenRAIL entries enabled by default.
- **Settings:** `t2i_model` (default `z-image-turbo`), `t2i_model_root` (`.model/t2i`), `t2i_allow_restricted_licenses`
  (default false — hides NC/research/revenue-capped entries), `t2i_keep_in_ram` (true), `t2i_offload`
  (`model` | `none` | `sequential`), `t2i_idle_unload_minutes`.
- **UI (all four modes share it via the shared state/API layer):** a model picker in Settings and in the Text-to-Image
  node/panel with a licence badge (green Apache/MIT/OpenRAIL, amber revenue-capped, red non-commercial/research),
  size + VRAM, "Installed / Download" state; an explicit licence-acceptance dialog (link to licence, version, timestamp
  persisted server-side in `runtime/t2i/license_acceptances.json`) before downloading any non-Apache/MIT model; gated
  repos explain that HF_TOKEN access is required.
- **Provenance:** store `source = { kind: "t2i", generation_id, model_id, repo_id, revision, license_id, prompt, seed,
  steps }` on the reference image (`set.json`) and copy it into `job.json` → shown on the result card ("Generated from
  prompt with Z-Image-Turbo — Apache-2.0") and written into the exported GLB `asset.extras` (or a sidecar JSON).
- **Notices:** add each enabled model to `THIRD_PARTY_NOTICES.md` (model, licence, link, "weights not distributed");
  keep the existing statement that an install is non-commercial because of nvdiffrast — choosing Apache-2.0 T2I
  models keeps the T2I part from adding new restrictions.

---------------------------------------------------------------------------------------------------

## 10. Cross-links for the part-aware milestone (do not implement now)

- **Qwen-Image-Layered** (Apache-2.0, `QwenImageLayeredPipeline` present in diffusers 0.39.0 [V]) decomposes an image
  into RGBA layers — a candidate "semantic asset decomposition *before* reconstruction" node (hair / clothes /
  accessories layers → separate TRELLIS.2 runs). Heavy (20 B; fp8 storage + offload needed).
- **Hunyuan3D-Part** (P3-SAM + X-Part) — 3D part segmentation/generation *after* reconstruction; check its licence
  (HF card has none; Hunyuan 3D licences exclude EU/UK/KR).
- **FLUX.2 klein edit / HiDream-O1 skeleton** — pose normalisation of user photos into A-pose before image-to-3D.

---------------------------------------------------------------------------------------------------

## 11. Risks and open questions

| Risk | Mitigation |
|---|---|
| diffusers 0.39.0 never tested on CPython 3.14 by us | import + tiny-pipeline smoke test under `test/` before wiring the API |
| Pinned to an older diffusers; future security fixes land only in hub-1.x releases | load only pinned local snapshots, no remote code; revisit when TRELLIS.2 works on transformers 5 (then move all three: transformers 5.x, hub 1.x, diffusers latest) |
| accelerate presence alters TRELLIS.2 loading | static check says no (4.57.6 ignores `low_cpu_mem_usage`); confirm with the real-generation smoke test |
| VRAM estimates are not measured | measure `torch.cuda.max_memory_allocated()` per stage (T2I and TRELLIS.2) and record in job metadata |
| Z-Image-Turbo low seed diversity | prompt-suffix variation or Z-Image base "Variations" mode |
| Transformers-5-format configs silently misload | registry check on `text_encoder/config.json` `transformers_version < 5` |
| Arena Elo is a general-purpose metric, not "3D-readiness" | build a small eval set in `test/` (10 character + 10 object prompts) → T2I → TRELLIS.2 → compare; pick primary from that |
| HF gating / token | primary and fallback are **not gated**; HF_TOKEN not needed for them |

---------------------------------------------------------------------------------------------------

## 12. What was verified vs assumed (summary)

Verified [V]: all package versions/constraints in §2; all text-encoder class availability; pipeline presence per
diffusers tag; licences/gating/params/file sizes from HF API; encoder config `transformers_version`/rope fields;
Z-Image base/Turbo shared-file hashes; HiDream-O1 `requirements.txt` (`transformers==4.57.1`) and flash-attn flag;
TRELLIS v1 text pipeline.json; spconv wheel matrix; local code facts in §1.
From sources [S]: VRAM claims on cards (Z-Image "16G", klein "~13GB"), steps/guidance, arena Elo, licence summaries
(FLUX.1 dev, Stability Community, Tencent Hunyuan, Krea 2, Qwen research, NoobAI), CVE details, community speed figures.
Assumed [A]: peak-VRAM-with-offload figures, 5090 timings, prompt-template effectiveness, SDXL tag efficacy,
readiness-check thresholds.

---------------------------------------------------------------------------------------------------

## 13. Sources

Package metadata / code
- diffusers setup.py per tag: https://github.com/huggingface/diffusers/blob/v0.39.0/setup.py (also v0.35.2 … v0.41.0, main)
- diffusers PyPI: https://pypi.org/project/diffusers/ · accelerate: https://pypi.org/project/accelerate/ · transformers 4.57.6: https://pypi.org/project/transformers/4.57.6/
- sentencepiece https://pypi.org/project/sentencepiece/ · spconv-cu126 https://pypi.org/project/spconv-cu126/ · bitsandbytes https://pypi.org/project/bitsandbytes/ · torchao https://pypi.org/project/torchao/
- diffusers pipelines (v0.39.0): https://github.com/huggingface/diffusers/tree/v0.39.0/src/diffusers/pipelines
- CVE-2026-44513: https://nvd.nist.gov/vuln/detail/CVE-2026-44513 · https://osv.dev/vulnerability/GHSA-98h9-4798-4q5v
- Kaolin install notes: https://kaolin.readthedocs.io/en/latest/notes/installation.html

Model cards / licences
- Z-Image-Turbo https://huggingface.co/Tongyi-MAI/Z-Image-Turbo · Z-Image https://huggingface.co/Tongyi-MAI/Z-Image
- FLUX.2 klein 4B https://huggingface.co/black-forest-labs/FLUX.2-klein-4B · 9B https://huggingface.co/black-forest-labs/FLUX.2-klein-9B · FLUX.2 dev https://huggingface.co/black-forest-labs/FLUX.2-dev · blog https://bfl.ai/blog/flux2-klein-towards-interactive-visual-intelligence
- FLUX.1 schnell https://huggingface.co/black-forest-labs/FLUX.1-schnell · FLUX.1 dev licence https://huggingface.co/black-forest-labs/FLUX.1-dev/blob/main/LICENSE.md · Kontext https://huggingface.co/black-forest-labs/FLUX.1-Kontext-dev · Krea dev https://huggingface.co/black-forest-labs/FLUX.1-Krea-dev
- SD 3.5 https://huggingface.co/stabilityai/stable-diffusion-3.5-large · Stability AI Community License https://stability.ai/community-license-agreement · SDXL https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0
- Animagine XL 4.0 https://huggingface.co/cagliostrolab/animagine-xl-4.0 · Illustrious v0.1 https://huggingface.co/OnomaAIResearch/Illustrious-xl-early-release-v0 · Illustrious v2.0 https://huggingface.co/OnomaAIResearch/Illustrious-XL-v2.0 · NoobAI-XL 1.1 https://huggingface.co/Laxhar/noobai-XL-1.1 · FAIPL-1.0-SD https://freedevproject.org/faipl-1.0-sd/
- Qwen-Image https://huggingface.co/Qwen/Qwen-Image · 2512 https://huggingface.co/Qwen/Qwen-Image-2512 · Layered https://huggingface.co/Qwen/Qwen-Image-Layered · 2.1 https://huggingface.co/Qwen/Qwen-Image-2.1 · 2.1-Turbo https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo · news https://datanorth.ai/news/qwen-releases-qwen-image-2-1
- HunyuanImage 2.1 licence https://github.com/Tencent-Hunyuan/HunyuanImage-2.1/blob/master/LICENSE · diffusers repo https://huggingface.co/hunyuanvideo-community/HunyuanImage-2.1-Diffusers · 3.0 https://huggingface.co/tencent/HunyuanImage-3.0
- Lumina 2 https://huggingface.co/Alpha-VLLM/Lumina-Image-2.0 · PixArt-Σ https://huggingface.co/PixArt-alpha/PixArt-Sigma-XL-2-1024-MS · SANA-Sprint https://huggingface.co/Efficient-Large-Model/Sana_Sprint_1.6B_1024px_diffusers · Gemma terms https://ai.google.dev/gemma/terms
- Chroma1-HD https://huggingface.co/lodestones/Chroma1-HD · HiDream-I1 https://huggingface.co/HiDream-ai/HiDream-I1-Full · HiDream-O1 https://huggingface.co/HiDream-ai/HiDream-O1-Image · code https://github.com/HiDream-ai/HiDream-O1-Image
- CogView4 https://huggingface.co/zai-org/CogView4-6B · LongCat-Image https://huggingface.co/meituan-longcat/LongCat-Image · Ovis-Image https://huggingface.co/ATH-MaaS/Ovis-Image-7B · GLM-Image https://huggingface.co/zai-org/GLM-Image · Kandinsky 5 https://huggingface.co/kandinskylab/Kandinsky-5.0-T2I-Lite-sft-Diffusers · Nucleus-Image https://huggingface.co/NucleusAI/Nucleus-Image · ERNIE-Image https://huggingface.co/baidu/ERNIE-Image · PRX https://huggingface.co/Photoroom/prx-1024-t2i-beta · BRIA FIBO https://huggingface.co/briaai/FIBO
- Krea 2 https://huggingface.co/krea/Krea-2-Turbo · licence https://krea.ai/krea-2-licensing · https://dev.to/damogallagher/krea-releases-krea-2-as-open-weights-for-image-generation-13pe
- Ideogram 4 https://huggingface.co/ideogram-ai/ideogram-4-nf4 · mirror note https://huggingface.co/unsloth/ideogram-4-nf4-diffusers
- Arena Elo: https://artificialanalysis.ai/text-to-image/arena/leaderboard-text (fetched 2026-10-16)
- Speed/diversity community notes: https://apatero.com/blog/flux-klein-vs-z-image-turbo · https://saascrmreview.com/z-image-turbo-review/ · https://apatero.com/blog/z-image-turbo-diversity-diffusers-sampling-guide-2025

3D
- TRELLIS v1 README https://github.com/microsoft/TRELLIS · text models https://huggingface.co/microsoft/TRELLIS-text-xlarge (also -base, -large)
- TRELLIS.2 https://huggingface.co/microsoft/TRELLIS.2-4B · Hunyuan3D-2.1 https://huggingface.co/tencent/Hunyuan3D-2.1 · Hunyuan3D-Part https://huggingface.co/tencent/Hunyuan3D-Part · Cube 3D https://huggingface.co/Roblox/cube3d-v0.5
- TRELLIS.2 input guidance https://fal.ai/learn/devs/trellis-2-image-to-3d-prompt-guide · https://docs.comfy.org/tutorials/3d/trellis2
