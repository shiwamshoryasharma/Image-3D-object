# Part-aware 3D generation & 3D part segmentation — state of the art (Oct 2026) and a staged plan for Img3D

Research date: 2026-10-16. Phase: READ-ONLY understanding (no project code changed; nothing installed/downloaded; no GPU work).
Scope: Tripo-style part-aware asset generation, 3D part segmentation, amodal part completion, part representation in
game/DCC pipelines, and what Img3D (TRELLIS.2-4B on Windows 11 / embedded Python 3.14 / torch 2.14.1+cu130 / RTX 5090)
can do now vs. later.

Evidence legend (used everywhere below):
- **[V]** verified in this session: read the local file / ran a read-only command, or read the primary page / HF API JSON / LICENSE file.
- **[S]** stated by a web source (README, model card, paper abstract, vendor or third-party page); not tested locally.
- **[A]** assumption / inference / estimate that still needs validation.

Names of individual paper authors are intentionally omitted (project rule about personal names in tracked files);
papers are identified by title, arXiv id and organisation.

---------------------------------------------------------------------------------------------------------------------

## 0. Executive summary

1. **Vendors converge on "holistic generation → part segmentation → part completion".** Tripo exposes
   `POST /mesh/segment` (v1 geometry-based default `v1.0-20250506`; v2 semantic + geometry, Beta `v2.0-20260430`, with
   `segmentation_granularity`, `split_by_connectivity`, optional `ref_image`) and a separate part-completion step that
   only runs on a prior segmentation task [V/S]. Tripo 3.0/3.1 also have `generateParts` at generation time, but it is
   **incompatible with texture, PBR and quad** [S]. Tencent ships Hunyuan3D-Part = **P3-SAM** (native 3D segmentation)
   → **X-Part** (bbox-prompted part generation/completion) on any input mesh [V/S]. Lesson for Img3D: keep the textured
   TRELLIS.2 generator as-is and add part awareness **after** reconstruction; textured native part generation is still
   immature even commercially.
2. **No open 3D-native part model runs on the current Img3D runtime without new native builds.** Every 3D-native
   segmenter/generator found depends on at least one of spconv / torch_scatter / torch-cluster / flash-attn / kaolin /
   pytorch3d / detectron2 / Blender, and is tested on Linux + Python 3.10–3.12 + torch 2.4–2.5 + CUDA 12.x [V/S].
   Verified wheel facts: spconv 2.3.8 has **no cp314 and no CUDA-13 wheels** [V]; the PyG index for
   `torch-2.14.0+cu130` contains **only pyg_lib** (no torch_scatter/torch_cluster) [V]; kaolin 0.18.0 wheels stop at
   torch 2.8 / Python 3.12 [S]; flash-attn has no Windows/sm_120/py3.14 wheel [V, env skill].
3. **What is reliable NOW (no new native deps):** 2D open-vocabulary segmentation of the input image(s) with
   **Grounding DINO + SAM 2.1** (both Apache-2.0, both already inside the pinned `transformers 4.57.6`) → camera
   alignment by silhouette → **multi-view label lifting onto the final GLB with nvdiffrast face-ID rasterisation**
   (nvdiffrast 0.4.0 is already built for sm_120) → graph smoothing on face adjacency → **surface partitioning with
   seam bookkeeping** → glTF with a part node hierarchy + per-vertex `_FEATURE_ID_0` + `extras` + a JSON manifest.
   Physical parts keep the original UVs and share the original baked texture, so no re-bake is needed [A, high confidence].
4. **Important physical reality [A, high confidence]:** TRELLIS.2 output is one fused, remeshed surface (dual-contouring
   remesh in `o_voxel.postprocess.to_glb`, [V]); eyes/eyebrows/eyelashes are usually *painted*, hair/clothes are *fused*
   with the body. "Physical separation" now therefore means **cutting open surface patches with recorded boundary
   loops**; watertight/amodal parts (scalp under hair, body under clothes, real eyeballs, lash cards) are a LATER
   plug-in (HoloPart / X-Part / SAM3D-Part class of models). Eye correction stays out of scope (user rule).
5. **Pluggable LATER:** 3D-native segmentation (P3-SAM, PartField, GeoSAM2-interactive, PartSAM when released), amodal
   completion (HoloPart MIT, X-Part Tencent licence, SAM3D-Part when released), per-part generation (OmniPart, PartCrafter,
   PartPacker, FullPart, StructParts). Most need either native builds on the CUDA 13.4 toolchain or an out-of-process
   Linux/WSL2 sidecar — the latter conflicts with the "embedded Python only" rule and needs an explicit user decision.
6. **Licences gate the roadmap.** Img3D is public GPL-3.0. Compatible: Apache-2.0 (SAM 2.x, Grounding DINO, GeoSAM2),
   MIT (HoloPart, PartCrafter, FullPart, SAMPart3D, OmniPart's own code). Not vendorable / restricted: NVIDIA
   non-commercial (PartField, PartPacker, SegFormer-derived weights), Tencent Hunyuan 3D-Part Community Licence
   (excludes EU/UK/South Korea, >1M MAU clause, outputs may not train other models), SAM Licence (SAM 3, SAM 3D; custom
   terms, gated), CC BY-NC 4.0 (Sapiens, Sonata weights), CelebAMask-HQ-derived face parsers (non-commercial).
7. **Prompt-to-3D** is not native to TRELLIS.2 (image-conditioned only) → text → image (T2I) → the same image-to-3D +
   part pipeline. Open Apache-2.0 candidates verified: Z-Image-Turbo (~32.8 GB files), FLUX.2 [klein] 4B (~16 GB
   transformer + text encoder). `diffusers` is **not installed** [V]; compatibility with the transformers 4.57.6 pin is
   unverified [A]. The prompt's nouns also seed the segmentation vocabulary.

---------------------------------------------------------------------------------------------------------------------

## 1. Img3D baseline facts that matter for parts (all [V] unless marked)

| Fact | Where |
|---|---|
| Generation runs `pipeline.decode_latent(...)` → `MeshWithVoxel`, then exports via `o_voxel.postprocess.to_glb(..., remesh=True, remesh_band=1, remesh_project=0, aabb=[[-0.5]*3,[0.5]*3])` | `backend/inference/trellis/generation.py:182-300` (`TrellisGenerationRun.execute`), `backend/inference/trellis/export.py:15-57` (`GLB_AABB`, `export_glb`) |
| `to_glb` steps: `fill_holes(max_hole_perimeter=3e-2)` → cuBVH of the decoded mesh → **dual-contouring remesh** (`cumesh.remeshing.remesh_narrow_band_dc`) → simplify to `decimation_target` → `uv_unwrap` → texture bake by rasterising in UV space with nvdiffrast (face id stored in alpha) → mapping texels back to the original mesh via `bvh.unsigned_distance(..., return_uvw=True)` → trilinear sampling of the attribute volume | `backend/python/Lib/site-packages/o_voxel/postprocess.py` (`to_glb`) |
| `MeshWithVoxel` carries `vertices, faces, origin, voxel_size, coords, attrs, voxel_shape, layout` and `query_attrs(xyz)` (trilinear `grid_sample_3d`) — a natural carrier for a future **label volume** | `backend/third_party/TRELLIS.2/trellis2/representations/mesh/base.py:190-240` |
| The decoded `MeshWithVoxel` is discarded after export → today only `model.glb` survives | `generation.py`, `export.py` |
| Typical output: 1,697,772 vertices / 1,895,691 faces, **1 material, 2 textures**, 4096 px, ~82 MB GLB (1536 res, 2M face target) | `backend/runtime/jobs/job_04535ae38930/job.json` |
| Pipeline entry: `ImageTo3DPipeline.run()` → `engine.generate()` → `FINALIZING` (`compute_glb_stats`) → `JobResultData` | `backend/pipelines/image_to_3d.py:90-152` |
| Multi-reference model kept: slots `front, back, left, right, face_front, face_side, detail`; engine uses primary only | api-contract §3, §6; job.json `references` |
| `JobStage` enum values are frozen by the contract ("do not change values") | `.claude/skills/img3d-architecture/api-contract.md` §3 |
| Post-generation run-resource precedent: Auto Mesh Tuner (`POST /jobs/{id}/tunings` → `tn_<12hex>`, own worker, steps, file routes) | api-contract §7.5; `backend/workers/tuning_worker.py`, `backend/services/mesh_tuner_service.py` |
| Installed (embedded Py 3.14): torch 2.14.1+cu130, torchvision 0.29.1+cu130, transformers 4.57.6, huggingface_hub 0.36.2, numpy 2.4.1, scipy 1.18.1, networkx 3.6.1, trimesh 5.1.1, opencv-python-headless 5.0.0.93, kornia 0.8.3, timm 1.0.30, einops 0.8.2, safetensors 0.8.0, triton-windows 3.8.0.post29; native: nvdiffrast 0.4.0, cumesh 0.0.1, flex_gemm 1.0.0, o_voxel 0.0.1 | `python.exe -s -m pip list`, site-packages listing |
| **Not** installed: diffusers, onnxruntime, xatlas, pymeshlab, open3d, fpsample, spconv, torch_scatter, kaolin, pytorch3d, flash-attn, xformers | same |
| transformers 4.57.6 ships `sam`, `sam2`, `sam2_video`, `sam_hq`, `grounding_dino`, `mm_grounding_dino`, `florence2`, `clipseg`, `segformer`, `mask2former`, `oneformer`, `eomt`, `upernet`, `dinov3_vit` — **no `sam3`** | `backend/python/Lib/site-packages/transformers/models/` |
| trimesh 5.1.1 glTF export: `mesh.metadata` → mesh `extras` (+ `gltf_extensions` key → `extensions`); `vertex_attributes` → custom attributes, auto-prefixed with `_`; scene graph edge `metadata` → **node `extras`/`extensions`**; scene `metadata` → scene `extras`; materials de-duplicated by hash | `trimesh/exchange/gltf/__init__.py:632-760, 855-1010`; `trimesh/scene/transforms.py:300-350` |
| Licence of vendored TRELLIS.2: MIT (Microsoft) | `backend/third_party/TRELLIS.2/LICENSE` |

Implication: a part layer can run **entirely after** `model.glb` exists, without touching
`inference/trellis/*` — zero risk to the working generator. A voxel-space label carrier would need an optional engine
change (persist `coords/attrs`), so it is a LATER option.

---------------------------------------------------------------------------------------------------------------------

## 2. How commercial systems expose parts

### 2.1 Tripo (VAST)
- **Tripo 3.0** announced 2025-08-20 with "intelligent part segmentation" [S, BusinessWire]. Later **3.1** [S].
- **Generation-time parts:** `generateParts` (bool, default false) on Tripo 3.0, 3.0 Multi View, 3.1, 3.1 Multi View:
  "Generate segmented 3D model parts. Incompatible with texture, pbr, and quad." [S, Scenario docs]. Also
  `smartLowPoly` ("hand-crafted low-poly topology", face limit 1k–20k) [S].
- **Post-hoc segmentation API** `POST https://openapi.tripo3d.com/v3/mesh/segment` [V, Tripo docs]:
  - `input`: prior `task_id` | `file_token` | public URL; GLB/GLTF/FBX/OBJ/STL, ≤150 MB.
  - `model`: `v1.0-20250506` (geometry-based, default) | `v2.0-20260430` (semantic labelling + geometry, Beta).
  - v2 only: `segmentation_granularity` (`simple|balanced|detailed`), `split_by_connectivity` (default true),
    `ref_image` (if given, granularity and connectivity are ignored).
  - Output: `output.model_url` (GLB) + `rendered_image_url`; example cost 40 credits. Internal GLB structure (node per
    part?) is **not documented** [V].
- **Part names / completion:** fal.ai's wrapper returns `part_names` and says they can be passed to the remesh
  endpoint to keep parts intact [S]. ComfyUI nodes: `TripoSmartSegmentNode` (source model|image, granularity
  coarse/medium/fine, free-text `hint`, outputs GLB + `part_names` + `parts` description + mask, ~180 s) and
  `TripoMeshCompleteNode` (input = segmentation task id, optional comma-separated `part_names`; "requires a prior
  segmentation step") [S].
- Takeaways: (a) parts are named semantically and addressable by name; (b) segmentation granularity is a user
  parameter; (c) connectivity split is a default sub-step; (d) a reference image can steer semantic segmentation —
  exactly the 2D→3D lifting idea; (e) completion is a separate, optional, per-part operation.

### 2.2 Tencent Hunyuan3D
- **Hunyuan3D-Part** (released Sep 2025; HF repo created 2025-09-23) = **P3-SAM** + **X-Part** [V, HF API / GitHub].
  "The holistic mesh is fed to P3-SAM to obtain semantic features, part segmentations and part bounding boxes; X-Part
  then generates the complete parts" [S]. "P3-SAM can handle any input mesh" [S]. Open release of X-Part is a
  "light version"; the full one is in Hunyuan3D-Studio [S].
- Studio (cloud): part-level generation + polygon generation + semantic UV; third-party reports "PartGen 1.5" with
  1536³ splitting and brush editing [S, unverified]. Hosted APIs: Tencent Cloud component identification/generation;
  fal.ai `fal-ai/hunyuan-3d/v3.1/part` ($0.45/generation) [S].
- Licence: **TENCENT HUNYUAN 3D-PART COMMUNITY LICENSE AGREEMENT** — territory excludes the EU, UK and South Korea;
  >1M MAU requires a separate licence; outputs may not be used to improve other AI models [V, LICENSE].

### 2.3 Others
- **Meta SAM 3D** (Nov 2025): *SAM 3D Objects* = image + object mask → object geometry/texture/layout (Gaussian splat
  `.ply` + mesh decoder) — **object-level, not part segmentation** [V]. *SAM 3D Body* = image → Momentum Human Rig body
  mesh [S]. Both SAM Licence, gated.
- Cloud APIs are useful as reference behaviour, but uploading user assets is a privacy/cost decision → opt-in only.

---------------------------------------------------------------------------------------------------------------------

## 3. Open models — catalogue

Feasibility = on the *current* Img3D runtime (Windows 11, embedded CPython 3.14, torch 2.14.1+cu130, sm_120, no venv,
CUDA 13.4 + MSVC 14.51 local toolchain): **High** = pure PyTorch/transformers; **Medium** = 1–2 source builds of small
extensions or swapping a dep for a pure-torch equivalent; **Low** = needs spconv / flash-attn / kaolin / pytorch3d /
detectron2 / Linux-only wheels / Blender pipelines → realistically a separate Linux/WSL2 sidecar.

| Model (org, venue) | Kind | Input → output | Licence (code / weights) | Weights (HF sizes) | GPU (stated) | Notable native deps | Feasibility | On existing TRELLIS.2 mesh? |
|---|---|---|---|---|---|---|---|---|
| **P3-SAM** (Tencent, arXiv 2509.06784) | 3D-native seg (point-promptable + auto mask merge) | mesh (.glb) → part masks, part bboxes, point-wise semantic features | Tencent Hunyuan 3D-Part Community Licence [V] | `p3sam/p3sam.safetensors` 451 MB [V] | n/s | Sonata (spconv, torch_scatter; flash-attn optional), chamfer3D ext, fpsample, numba; tested Py3.10/torch 2.4.0+cu121 [S] | Low | **Yes** |
| **X-Part** (Tencent, arXiv 2509.08643) | part generation / decomposition (completes parts) | holistic mesh + bboxes + semantic features → part shapes | Tencent licence [V] | `model` 6.63 GB + `conditioner` 1.76 GB + `shapevae` 0.66 GB ≈ 9.0 GB [V] (README still says weights "TODO" [S] — conflicting) | n/s (large) | Sonata + Hunyuan3D-2.1 stack [S/A] | Low | **Yes** (needs P3-SAM boxes) |
| **PartField** (NVIDIA, ICCV 2025, arXiv 2504.11451) | 3D-native feature field → class-agnostic hierarchical parts | mesh/.ply splat/point cloud → feature field → agglomerative part tree (mesh) or K-means | **NVIDIA License, non-commercial** (§3.3) [V] | `model_objaverse.ckpt` 1.24 GB [V] | n/s; OOM hint: lower `n_point_per_face` 2000→500 [S] | Py3.10, torch 2.4.0 cu124, lightning 2.2, torch-scatter, open3d, pymeshlab, libigl, vtk [S] | Medium (torch_scatter replaceable; cp314 wheels for open3d/pymeshlab uncertain [A]) | **Yes** (no semantic names) |
| **SAMPart3D** (VAST/HKU, arXiv 2411.07184) | zero-shot multi-granularity seg (+VLM labelling) | mesh + Blender 16-view RGB/depth renders → per-shape trained scale-conditioned MLP → parts | MIT [S] | PTv3-object backbone `ptv3-object.pth` [S] | n/s | PTv3 (spconv/flash-attn typical [A]), Blender 4.0 [S] | Low (per-shape optimisation, minutes [A]) | **Yes** |
| **SAMesh** (arXiv 2408.13679) | zero-shot seg via SAM2 on multi-view normal/SDF renders, lifted & merged | mesh → parts (class-agnostic) | not stated [S] | SAM2 ckpt | n/s | pyrender/OpenGL, SAM2; tested Py3.12/CUDA 11.8 [S] | Medium–High as a *re-implementation* (nvdiffrast + transformers SAM2) [A] | **Yes** |
| **GeoSAM2** (VAST, CVPR 2026, arXiv 2508.14036) | interactive 3D part seg (2D click/mask on one view → propagated over 12 views → back-projected) | mesh → **per-face labels** | **Apache-2.0** [V] | `geosam2.pt` 616 MB / bf16 308 MB, ungated [V] | n/s | bundled SAM2 + optional CUDA CC op (`GEOSAM2_BUILD_CUDA=0` to skip), Blender ≥4.0 renders, Linux, Py≥3.10 (tested 3.12), torch≥2.3 [V] | Medium–High (swap Blender rendering for nvdiffrast [A]) | **Yes** (interactive) |
| **PartSAM** (ICLR 2026, arXiv 2509.21965) | promptable native 3D seg, triplane encoder, "Segment-Every-Part" incl. internal parts | 3D shape + prompt → parts | no code found [S] | — | — | — | n/a | Yes (paper) |
| **HoloPart** (VAST, arXiv 2504.07943) | **amodal part completion** | mesh + per-face part mask (`mask.npy`) → complete parts | **MIT** [V] | transformer 3.23 GB + VAE 0.49 GB + part_encoder 0.11 GB ≈ 3.83 GB [V] | n/s | diffusers, peft, jaxtyping, **diso**, **torch-cluster**, `numpy==1.22.3` pin (no py3.14 build [A]) [V] | Medium | **Yes** (needs segmentation first) |
| **OmniPart** (HKU/VAST, SIGGRAPH Asia 2025, arXiv 2507.06165) | part-aware generation | image + 2D part-ID mask (`.exr`, ID in 3 channels) → bbox plan (autoregressive) → spatially-conditioned rectified flow → all parts | code **MIT** [V]; NOTICE includes PartField-derived code under NVIDIA Source Code Licence [S] | ≈3.01 GB (TRELLIS-v1-style ss/slat flows + decoders) [V] | n/s | torch 2.4.0 cu121, **spconv-cu120**, xformers 0.0.27.post2, **flash-attn cp310 linux wheel**, nvdiffrast/diff-gaussian-rasterization cp310 linux wheels, detectron2, segment-anything, torch-scatter [V requirements] | Low | **No** (regenerates; TRELLIS v1 latents ≠ TRELLIS.2 O-Voxel) |
| **PartCrafter** (NeurIPS 2025) | part generation (joint N parts) | image (+`--num_parts` or Gemini-API part suggestion) → N part meshes | **MIT** [V] | transformer 2.88 GB + DINOv2 0.61 GB + VAE 0.49 GB ≈ 3.97 GB [V] | ≥8 GB [S] | TripoSG stack, torch 2.5.1 cu124, **torch-cluster**, EGL for render [V]; community Windows fork [S] | Medium | **No** |
| **PartPacker** (NVIDIA, arXiv 2506.09980) | part generation via dual volume packing | image → parts (packed into 2 complementary volumes) | **NVIDIA non-commercial** [V] | `flow.pt` 2.50 GB + `vae.pt` 0.68 GB [V] | ~10 GB fp16 [S] | pure-python list (kiui, fpsample, pymcubes, pymeshlab, onnxruntime, rembg); flash-attn optional; Windows documented (Py3.10/CUDA 12.4/torch 2.5.1) [V/S] | Medium–High technically; licence blocks distribution | **No** |
| **FullPart** (ICLR 2026, arXiv 2510.26140) | part generation, each part in its own full-res voxel grid + textured refinement | image → bbox layout → per-part structures → textured parts | MIT [S] | Google Drive [S]; dataset PartVerse-XL (40K objects / 320K parts) | n/s | TRELLIS v1 + spconv, kaolin, nvdiffrast, diffoctreerast, mip-splatting, flash-attn/xformers, torch 2.4.0 cu118 [S] | Low | **No** |
| **StructParts** (HKU/Tencent et al., arXiv 2610.21850, 15 Oct 2026) | part generation from a *segmented scaffold* | image + coarse mesh + face-level part mask (e.g. from P3-SAM) → per-part meshes (Hunyuan3D-2.1 DiT finetune) | code status unknown [S] | — | training 8×H200 [S] | — | n/a | **Yes-ish** (scaffold format = segmented mesh) |
| **SAM3D-Part** (arXiv 2609.15639, Sep 2026) | prompt-driven part *generation* on an existing object | mesh + part prompt → completed queried part placed in source frame (per-voxel correspondences; cache of previous parts) | code "will be available" [V] | — | — | — | n/a | **Yes** (by design) |
| **UniPart** (ByteDance et al., CVPR 2026) | joint geometry + part latent (Geom-Seg VecSet) | image → parts | no code found [S] | — | — | — | n/a | No |
| **AutoPartGen** (Meta/Oxford, NeurIPS 2025) | autoregressive part generation, decides part count | image / 2D masks / existing 3D object → parts | no code found [S] | — | — | — | n/a | Yes (paper) |
| **PartGen** (Meta/Oxford, CVPR 2025) | multi-view diffusion part seg + per-part completion + reconstruction | image/text/3D → parts | no code found [S] | — | — | — | n/a | Yes (paper) |
| **Part123** (SIGGRAPH 2024) | part-aware reconstruction (multi-view diffusion + SAM masks + contrastive NeuS) | image → part-segmented recon | no code found [S] | — | — | — | n/a | No |
| CubePart (2605.28763), MegaParts (2608.14783, up to 300 parts), SCULPT (2608.13541), PartMat (2608.01825) | part generation variants | — | paper-only per search [S, unverified] | — | — | — | n/a | — |
| "PartTrellis" (HF `AuroraRyan/PartTrellis`) | reported as TRELLIS.2/O-Voxel dual-volume part generation [S, search snippet] | — | page now returns **HTTP 401** [V] | — | — | — | treat as unavailable | — |
| **SAM 3D Objects** (Meta, Nov 2025) | object-level image→3D (not parts) | image + mask → shape/texture/layout (splat .ply, mesh decoder) | SAM Licence [V] | ≈13.1 GB (ss_generator 6.69 GB, slat_generator 4.91 GB, …), gated manual [V] | **≥32 GB VRAM** [V] | Linux-64 only, kaolin + pytorch3d (torch 2.5.1 cu121), Hydra patch [V] | Low | No (useful LATER to regenerate a detachable object) |
| **PartUV** (SIGGRAPH Asia 2025, arXiv 2511.16659) | utility: part-based UV unwrapping using PartField's part tree | mesh → per-part charts | not verified [S]; inherits PartField licence concerns [A] | — | — | PyPI `partuv` [S] | Medium | Yes (LATER re-atlas per part) |

Notes on comparative quality (from StructParts' Oct 2026 tables [V]): layout-conditioned part generation — released
X-Part CD 0.114 / F@0.05 0.63 (trained on ~2M closed objects) > HoloPart 0.182 > OmniPart 0.238 > FullPart 0.375;
image-to-parts — X-Part 0.043 ≈ StructParts 0.045 < FullPart 0.051 < PartPacker 0.068 < OmniPart 0.088 < HoloPart
0.092 < PartCrafter 0.105 (lower CD is better). The paper reports PartPacker/PartCrafter "lack controllability" and
OmniPart/FullPart show boundary collisions on complex structures [S]. These are object datasets (furniture/props),
not characters — character-specific behaviour (hair/clothes/eyes) is **not** benchmarked anywhere found [A].

### 3.1 2D segmenters usable *before* reconstruction (and on rendered views after it)

| Model | What it gives | Licence | Size | In installed transformers 4.57.6? |
|---|---|---|---|---|
| **Grounding DINO** (`IDEA-Research/grounding-dino-base`) | text-prompted boxes ("hair . eye . eyebrow . jacket . sword") | **Apache-2.0**, ungated [V] | 933 MB [V] | **Yes** (`grounding_dino`, `mm_grounding_dino`) [V] |
| **SAM 2.1** (`facebook/sam2.1-hiera-large`) | box/point → high-quality masks (+ video propagation usable across rendered view sequences) | **Apache-2.0**, ungated [V] | 898 MB [V] (smaller hiera variants exist [A]) | **Yes** (`sam2`, `sam2_video`) [V] |
| SAM-HQ | sharper mask boundaries (hair) | Apache-2.0 [A] | ~2.5 GB for ViT-H [A] | **Yes** (`sam_hq`) [V] |
| Florence-2 | caption/OD/referring segmentation (polygons) | MIT [A] | 0.23–0.77 B params [A] | **Yes** (`florence2`) [V] |
| **SAM 3 / 3.1** (`facebook/sam3`) | text noun phrase → *all* instance masks in one model (best fit for open vocabulary) | **SAM Licence** (custom; derivatives distributed only under it; prohibited uses; gated manual) [V] | 848M params, 3.44 GB [V] | **No** — Transformers v5.x only (docs v5.20.0) [V]; official pkg needs Py≥3.12, torch≥2.7 [S] → conflicts with the 4.57.6 pin TRELLIS.2 needs |
| `jonathandinu/face-parsing` (SegFormer-b5, CelebAMask-HQ) | 19 classes incl. `l_eye, r_eye, l_brow, r_brow, u_lip, l_lip, mouth, hair, hat, ear_r (earring), neck_l (necklace), eye_g (glasses), cloth` | **non-commercial research/education** [V card] | 84.6M params [S] | arch yes (`segformer`) |
| `mattmdjaga/segformer_b2_clothes` (ATR) | 18 classes: hat, hair, sunglasses, upper-clothes, skirt, pants, dress, belt, L/R shoe, face, L/R leg, L/R arm, bag, scarf | "other" → links NVlabs SegFormer licence (NVIDIA, non-commercial) [S] | 27.4M params [S] | arch yes |
| Sapiens / Sapiens2 seg (Meta) | 28 body-part classes (hair, face-neck, lips, teeth, tongue, apparel, limbs …) | CC BY-NC 4.0 (Sapiens); "Sapiens2 License" (unverified) [S] | 0.3–2B [A] | No |
| Anime-Face-Segmentation (UNet/MobileNetV2) | 7 classes: bg, hair, eye, mouth, face, skin, clothes — **no eyebrows/eyelashes** | MIT [V] | small | No (plain PyTorch) |

No public parser with an **eyelash** class was found [S]. Eyelashes/eyebrows must come from open-vocabulary prompting,
small-region heuristics (dark thin strokes above/around eye masks), or user correction.

---------------------------------------------------------------------------------------------------------------------

## 4. Native-dependency reality check for this machine

| Dependency | Status for cp314 + torch 2.14 + cu130 on Windows | Who needs it |
|---|---|---|
| spconv | latest 2.3.8: wheels cp39–cp313 only, CUDA ≤12.6 variants (`spconv-cu126` has win_amd64 up to cp313); **no cp314, no cu13** [V] | Sonata/PTv3 (P3-SAM, X-Part, SAMPart3D), TRELLIS v1 (OmniPart, FullPart) |
| torch_scatter / torch_cluster | PyG `torch-2.14.0+cu130` index holds **only `pyg_lib-0.9.0` (cp310-abi3, incl. win_amd64)** [V]; scatter ops → `torch.Tensor.scatter_reduce`; FPS → own kernel or `fpsample` [A] | PartField, Sonata, HoloPart, PartCrafter |
| flash-attn | no Windows/sm_120/py3.14 wheel; do not build [V env skill] | optional almost everywhere; required by OmniPart/FullPart setups |
| xformers | 0.0.35 has no sm_120 Windows forward kernel → uninstalled [V env skill] | OmniPart (pinned 0.0.27.post2) |
| kaolin | 0.18.0 wheels for torch 2.3–2.8, Python 3.9–3.12 (Windows supported) [S] | SAM 3D Objects, FullPart |
| pytorch3d | no current Windows wheels; source build needed [S] | SAM 3D Objects |
| diso, cubvh, chamfer3D | small CUDA extensions; buildable in principle with nvcc 13.4 + MSVC 14.51 (C++20 patch pattern already used for CuMesh/FlexGEMM/o-voxel) [A] | HoloPart/TripoSG, P3-SAM |
| Blender | 4.0+ headless renders | SAMPart3D, GeoSAM2 (replaceable by nvdiffrast) |
| numpy pins (`==1.22.3`, `==1.26.4`) | numpy 2.4.1 installed; old pins have no py3.14 wheels [A] | HoloPart, Sonata demo |

Conclusion: nothing 3D-native is drop-in. The cheapest future integrations are (1) GeoSAM2-style interactive lifting
(Apache-2.0, SAM2 + rendering we already have), (2) HoloPart (MIT, needs diso + FPS replacement), (3) PartField
(non-commercial; needs torch_scatter replacement). Sonata/PTv3/TRELLIS-v1-based models (P3-SAM, X-Part, SAMPart3D,
OmniPart, FullPart) need spconv → sidecar territory.

---------------------------------------------------------------------------------------------------------------------

## 5. How game/DCC pipelines represent "physical parts" vs "semantic regions"

### 5.1 Industry conventions (character assets)
- **Physical parts = separate meshes/objects** when they must be hidden, swapped, simulated or skinned differently:
  hair (strand grooms or cards; spring bones), clothing layers, accessories, props/weapons. MetaHuman, VRoid/VRM and
  Character Creator all ship hair and clothing as separate meshes/components [S/A].
- **Small facial elements = separate submeshes (material slots), often inside the head object:**
  - MetaHuman face material set slots: `EyeLeft, EyeRight, EyeShell, LacrimalFluid, Teeth, Eyelashes, EyelashesHiLods`
    (`EMetaHumanCharacterTeethAndEyesSlot`) [S, UE API docs].
  - VRoid exports Face / Body / Hair separately; the face contains several meshes/submeshes for eyelashes, eyeballs,
    mouth interior, with many face materials [S, forum + Cluster guide].
  - Character Creator: eyes are several meshes (iris, eyeball, plus eye-occlusion and tear-line layers), merged into
    one for the neutral body [S, Reallusion wiki/manual].
- **Semantic regions on continuous skin = material/texture regions or vertex groups, not cuts:** body skin is one
  continuous mesh (so skinning/blend shapes deform seamlessly); head/body/arm/leg skin differ by material slot or mask.
  This is exactly the user's requirement "keep continuous face/body topology as semantic regions".

### 5.2 glTF 2.0 mechanisms (spec rules [V])
- **Node hierarchy**: a node has at most one mesh, a transform and children; parts become child nodes of an asset root,
  grouped by semantic parent (head → eyes, hair; torso → clothes). Node `name` is free but not guaranteed unique — use
  `extras` for stable ids.
- **Mesh primitives = submeshes**: one mesh may have several primitives, each with its own material and index
  accessor; primitives *may* share vertex attribute accessors (same `POSITION`), which keeps a single vertex pool while
  partitioning faces into regions [A: DCC importer behaviour with shared accessors must be tested — Blender/three.js
  may duplicate].
- **Custom vertex attributes**: "Application-specific attribute semantics MUST start with an underscore" and "MUST NOT
  use unsigned int component type" [V, glTF 2.0 spec] → store part ids as `UNSIGNED_SHORT`/`UNSIGNED_BYTE`
  (as int16/uint8 data) or `FLOAT`, e.g. `_PART_ID`.
- **EXT_mesh_features** (Cesium vendor extension, status *Draft*) [V]: per-vertex `_FEATURE_ID_n` attributes (or
  feature-ID textures) declared in `featureIds: [{featureCount, attribute, nullFeatureId, label, propertyTable}]`;
  with **EXT_structural_metadata** the id indexes a property table (name, kind, parent, …). This is the standardised way
  to mark *regions inside one continuous mesh* — it degrades gracefully (viewers ignore it; data survives as `_FEATURE_ID_0`).
- **`extras`** on asset/scene/node/mesh/material: free JSON for part ids, kinds, relations, schema version.
- **Feature-ID texture** (alternative): a NEAREST-sampled label texture in the same UV layout — good for regions
  smaller than triangles (painted eyebrows/eyelashes) [A useful for texture-only parts].
- **VRM 1.0 (`VRMC_vrm`)**: humanoid bones → glTF node indices, plus `firstPerson`, `expressions`, `lookAt`;
  `VRMC_springBone` for hair [V top-level / A details]. Stable node naming now makes a later rig/VRM export cheaper.
- `KHR_node_visibility` (hide/show parts) — status unclear, tooling marks it experimental [S]; do not depend on it.

### 5.3 Tooling facts relevant to Img3D
- trimesh 5.1.1 (installed) round-trips: mesh `extras` (`metadata`), node `extras` (graph edge `metadata`), custom
  `_` vertex attributes, and extension dicts via a `gltf_extensions` key [V code]. So `EXT_mesh_features` +
  `EXT_structural_metadata` JSON can be emitted without a new dependency [A: `extensionsUsed` bookkeeping must be checked].
- Blender glTF importer maps `extras` to custom properties [A]; whether `_PART_ID` custom attributes import as
  mesh attributes depends on the Blender version/importer options [A — verify against Blender ≥5.1 used by the
  paused tuner].

---------------------------------------------------------------------------------------------------------------------

## 6. Recommended staged architecture for Img3D

### 6.1 Principles
1. **Do not touch the generator.** Part analysis is a post-generation run resource (mirroring the Auto Mesh Tuner
   pattern) that reads `model.glb` (+ inputs, + preview) and writes into the job folder. `model.glb` and the 9-stage
   job contract stay byte-for-byte compatible; `JobStage` values are not changed.
2. **One canonical truth per asset: per-face labels on the exported mesh** (`face_labels` aligned with `model.glb`
   face order) + a versioned JSON manifest. Everything else (split meshes, assembled GLB, previews) is derived and
   re-derivable.
3. **Physical vs region is a policy on the taxonomy, not a property of the segmenter.** Segmenters produce labels;
   the partitioner decides what to cut.
4. **Separation never loses information:** every split vertex keeps `orig_vid`/`weld_id`; every cut records its
   boundary loop; reassembly must reproduce the original mesh exactly (testable invariant).
5. **Torch isolation** like `inference/`: only `parts/segmentation2d/*` and `parts/lifting/*` import torch /
   transformers / nvdiffrast; API/services stay torch-free.
6. **Pluggable later stages** behind small ABCs with capability + licence metadata; default implementations are
   "unavailable" (same pattern as `inference/unavailable.py`).
7. **Tuning stays disabled.** The manifest exposes `tunable`/`seam_lock` flags for a future consumer; no tuning code.

### 6.2 Taxonomy and default policy

| Class (id) | Default kind | Rationale | NOW output | LATER |
|---|---|---|---|---|
| `body_skin` (torso, arms, legs, hands, feet, neck) | region | must deform as one surface | face labels on body mesh | SAM 3D Body / rig priors |
| `face_skin`, `nose`, `ears`, `lips`, `mouth` | region | part of head surface | region | — |
| `eye_l`, `eye_r` | region, `detachable_candidate=true` | TRELLIS.2 eyes are usually painted/shallow on the face surface [A] | region (+ optional cut patch) | eyeball replacement / amodal (= "eye correction", paused) |
| `eyebrow_l/r` | region (texture) unless raised geometry detected | normally texture-only [A] | region / feature-ID texture | card geometry |
| `eyelash_l/r` | region (texture mask) | rarely resolved as geometry at 1536³ [A] | region if detected | lash cards |
| `hair` (incl. bangs, ponytail, ahoge) | **physical** | separate in all DCC pipelines; simulation | split patch + seam loops (scalp hole recorded) | amodal scalp/hair completion |
| `facial_hair` | physical if volumetric else region | — | policy by thickness test | — |
| `clothing_*` (upper, lower, dress, outerwear, shoes, gloves, hat) | **physical** | layered over body | split + seam loops (body under cloth missing) | amodal body completion |
| `accessory_*` (glasses, earrings, necklace, belt, bag, armour piece) | **physical** | swappable; often already separate components | split; prefer connected components | — |
| `object_*` (weapon, held prop, detachable item) | **physical** | detachable | split at contact boundary | regenerate with object model |
| `teeth`, `tongue`, `mouth_interior` | region | rarely visible | region | — |
| `unknown` | region | safe default | region | — |

Every class has aliases for prompting (e.g. `hair`: "hair, bangs, ponytail, braid"), an optional symmetric pair
(`eye_l↔eye_r`), and a default parent (`eye_l` → `head`).

### 6.3 Data model (manifest `img3d.parts/1`, proposal)

```json
{
  "schema": "img3d.parts/1",
  "job_id": "job_…", "analysis_id": "pa_…", "source_glb": "model.glb",
  "source_counts": {"vertices": 1697772, "faces": 1895691},
  "space": {"aabb": [[-0.5,-0.5,-0.5],[0.5,0.5,0.5]], "up": "+Y"},
  "segmenter": {"id": "gdino_sam2", "versions": {"grounding_dino": "…", "sam2": "…"}, "vocabulary": ["hair","eye", "…"]},
  "camera_alignment": {"azimuth_deg": 0.0, "elevation_deg": 0.0, "scale": 1.0, "offset": [0,0], "silhouette_iou": 0.93},
  "labels": [{"id": 3, "class": "hair", "kind": "physical", "face_count": 210334, "area": 0.12, "confidence": 0.81}],
  "parts": [
    {"part_id": "hair", "class": "hair", "kind": "physical", "parent": "head", "node_name": "hair",
     "file": "parts/hair.glb", "faces": 210334, "bbox": [[…],[…]], "material_ref": 0,
     "open_boundary": true, "seam_ids": ["s1"], "amodal_completed": false, "tunable": true, "seam_lock": true}
  ],
  "regions": [{"region_id": "eye_l", "class": "eye_l", "host_part": "body", "feature_id": 7, "symmetric_with": "eye_r"}],
  "seams": [{"seam_id": "s1", "parts": ["hair", "body"], "loops": [{"weld_ids_ref": "seams/s1_0.u32", "closed": true, "length": 1.84}]}],
  "relations": [{"type": "attached_to", "a": "hair", "b": "body", "via": "s1"},
                {"type": "layered_over", "a": "clothing_upper", "b": "body"},
                {"type": "symmetric_with", "a": "eye_l", "b": "eye_r"}],
  "artifacts": {"face_labels": "face_labels.u16", "assembled_glb": "model_parts.glb", "previews": ["previews/front.png"]}
}
```
Binary sidecars: `face_labels.u16` (len = faces of `model.glb`), `weld_map.u32` (glb vertex → welded id),
`seams/*.u32` (ordered loops). Keep JSON small; arrays in sidecars.

### 6.4 NOW: stage-by-stage pipeline (all on installed components except two HF weight sets)

**A0. Contracts only** — taxonomy, manifest dataclasses, plugin ABCs, API schemas. No ML.

**A1. 2D semantic segmentation (before reconstruction; also reused after).**
- Segmenter: Grounding DINO (one forward with all class phrases joined by " . ") → boxes → SAM 2.1 masks per box
  (SAM-HQ optional for hair). Both via transformers 4.57.6, Apache-2.0, ~1.8 GB weights stored under
  `backend/.model/` (download = user-approved step, not part of this phase).
- Inputs: the primary image (already background-removed by BiRefNet in preprocessing) + optional `face_front` /
  `face_side` reference slots for high-resolution eye/brow masks (the multi-reference data model finally pays off).
- Output: label map (uint16 PNG) + per-label score table. Store as a part-ID image so that a future OmniPart-style
  plugin can consume it directly (OmniPart expects an image + per-pixel part-ID mask [S]).
- Prompt-to-3D: the prompt's nouns extend the vocabulary (e.g. "knight with sword and cape" → `object_sword`,
  `clothing_cape`).

**A2. Reconstruction** — unchanged TRELLIS.2 job.

**A3. Camera alignment (input view ↔ canonical mesh).** Render silhouettes of `model.glb` with nvdiffrast at an
azimuth sweep (e.g. 72 × 5°, elevations −10/0/+10°) and pick the view maximising IoU with the input alpha mask; refine
scale/offset from mask moments. TRELLIS.2's canonical "front" direction should be determined once empirically and
then used as the prior [A].

**A4. Multi-view label lifting.**
- Rasterise face-ID buffers (nvdiffrast `rasterize`, triangle id in channel 3 — same trick `to_glb` uses [V]) for the
  aligned input view and N additional views (16–24: two azimuth rings + top/bottom) at 1024–2048 px.
- Input view: project the A1 label map onto visible faces.
- Other views: render the textured mesh (baked base colour via `dr.texture`), run the same 2D segmenter, project.
- Per-face vote accumulation weighted by visibility × |cos(view angle)| × pixel coverage × segmenter score.
- Unlabelled/contested faces: label propagation on the face-adjacency graph (scipy.sparse, iterative) with
  colour/normal affinity; optional boundary snapping to texture edges/creases. Memory for ~1.9M faces is fine on
  130 GB RAM [A]. Optionally run on a decimated proxy and transfer to full resolution via nearest-surface lookup
  (cumesh `cuBVH.unsigned_distance` is already used by `to_glb` [V]).
- Connected-component cleanup; physical classes with separate components (earrings, glasses) are taken as-is
  (Tripo's `split_by_connectivity` analogue).
- Expected runtime tens of seconds on the 5090 [A]; GPU work must queue behind generation (single-GPU lock) [A —
  check `workers/queue.py`].

**A5. Partitioning with seam bookkeeping.**
- Weld `model.glb` vertices by position first (UV seams duplicate vertices) → `weld_map`.
- For each physical label: extract faces → submesh with `_ORIG_VID` (and `weld_id`); boundary edges = edges with one
  incident face inside the part → chained into ordered loops → `SeamRecord`.
- Physical parts keep original UVs and reference the **same baked texture** → identical appearance, no re-bake; trimesh
  de-duplicates materials by hash [V], so the 4K images should be written once in the assembled GLB [A].
- Body stays one continuous mesh carrying all region labels; regions encoded as `_FEATURE_ID_0` (vertices duplicated
  only where needed so each face's label is exact) **plus** the per-face sidecar. No hole filling, no remesh, no
  smoothing (that would be tuning).
- Invariant test: reassemble (concatenate + weld by `weld_id`) → same face set, Hausdorff 0.

**A6. Export.** `model_parts.glb`: root node `asset` → `body` (regions via `_FEATURE_ID_0` + `EXT_mesh_features` /
`EXT_structural_metadata` table of labels) → children `hair`, `clothing_upper`, `accessory_glasses`, … each with node
`extras.img3d = {part_id, class, kind, parent, seam_ids}`; scene `extras.img3d = {schema, analysis_id}`. Optional
`parts/<part_id>.glb` per part. Legacy `model.glb` untouched.

### 6.5 Backend placement (proposal, mirrors existing layering)
```
backend/parts/                      # new package; torch only in segmentation2d/ and lifting/
  taxonomy.py   manifest.py   policy.py
  segmentation2d/{base.py, gdino_sam2.py, registry.py}
  lifting/{cameras.py, raster.py, vote.py, smooth.py}
  partition/{weld.py, split.py, seams.py, regions.py}
  export/{gltf_parts.py}
  plugins/{base.py (PartSegmenter3D, AmodalCompleter, PartGenerator ABCs), unavailable.py, registry.py}
backend/pipelines/part_analysis.py  # orchestrates A1..A6 for one job (like ImageTo3DPipeline)
backend/workers/part_worker.py      # mirrors tuning_worker.py
backend/services/part_service.py    backend/storage/part_store.py    backend/schemas/parts.py
```
API (additive; follows §7.5 tuner conventions): `POST /jobs/{job_id}/part-analyses {mode: regions|split,
vocabulary?, use_references?, views?, segmenter?}` → 202 `PartAnalysisOut` (`pa_<12hex>`); `GET /jobs/{job_id}/part-analyses`;
`GET /part-analyses/{id}`; cancel/delete; files: `/manifest`, `/model.glb` (assembled), `/parts/{part_id}.glb`,
`/previews/{name}`, `/segmentation/{name}`. A pre-reconstruction `POST /reference-sets/{set_id}/images/{image_id}/segmentation`
lets the Node UI's "Segmentation" node run before "Image-to-3D". Optional job setting `parts: {auto: false, mode}`
(default off) chains an analysis after a successful job without changing job stages. WebSocket:
`part_analysis.updated`. All four UI modes call these same endpoints; the node graph is a client-side composition of
backend run resources, not a second backend.

### 6.6 LATER: plug-in points (disabled until approved)
| Plug-in | Contract | Candidates (licence, feasibility) |
|---|---|---|
| `PartSegmenter3D` | mesh (+ optional prompts/2D masks) → per-face labels + scores | GeoSAM2 (Apache-2.0, Medium–High; interactive correction UI), PartField (NVIDIA NC, Medium; class-agnostic hierarchy), P3-SAM (Tencent, Low), PartSAM (no code), SAMPart3D (MIT, Low) |
| `Segmenter2D` (alt.) | image + vocabulary → label map | SAM 3 (SAM Licence, gated, needs transformers v5 → out-of-process), face/clothes parsers (non-commercial, opt-in) |
| `AmodalCompleter` | part patch + context → watertight completed part in same frame | HoloPart (MIT, Medium), X-Part (Tencent, Low), SAM3D-Part (pending code) |
| `PartGenerator` | image/prompt + part masks/layout → parts | OmniPart (MIT code + NVIDIA-NC derived code, Low), PartCrafter (MIT, Medium), PartPacker (NVIDIA NC), FullPart (MIT, Low), StructParts (unknown) |
| `ObjectRegenerator` | crop/mask of a detachable object → standalone asset | TRELLIS.2 itself on an inpainted crop (High, reuses engine) [A]; SAM 3D Objects (Low) |
| Per-part UV re-atlas | part → new UV + re-bake | PartUV (licence unverified), xatlas (not installed) |
| Tuning consumers | per-part GLB + manifest + seams | paused Auto Mesh Tuner (Blender) — **disabled** |

Runtime options for Low-feasibility plug-ins: (a) native builds with the CUDA 13.4/MSVC 14.51 toolchain (spconv is a
large cumm-based build — high effort [A]); (b) an out-of-process sidecar (WSL2/Linux env) behind the same plugin
interface — **conflicts with the "embedded Python only" non-negotiable; requires the user's explicit decision**;
(c) cloud APIs (Tripo `/mesh/segment`, Hunyuan part) — opt-in only (privacy, cost, licence/territory).

### 6.7 Prompt-to-3D path
TRELLIS.2 conditions on DINOv3 image features only (`pipeline.get_cond([image], ...)`, [V]); there is no text model in
`backend/.model/` [V]. So: prompt → T2I → (BiRefNet) → existing image-to-3D → same part analysis. Candidates verified
on HF: **Z-Image-Turbo** (Apache-2.0, ungated, diffusers, ~32.8 GB files) and **FLUX.2 [klein] 4B** (Apache-2.0,
ungated, diffusers, ~7.75 GB transformer + ~8.04 GB text encoder) [V]. Needs `diffusers` (absent) and a check that it
coexists with transformers 4.57.6 [A]; T2I and TRELLIS.2 should not be resident simultaneously at 1536 res on 32 GB [A].
Prompt templates that ask for full body, neutral pose, plain background and visibly separated accessories should make
segmentation and lifting more reliable [A].

### 6.8 Validation plan (for the implementation phase)
- Unit tests under `test/` with synthetic meshes (two touching spheres, capped cylinder, mesh with UV seams): weld,
  split, seam loops, reassembly invariant, glTF round-trip of `extras`/`_FEATURE_ID_0` via trimesh.
- Real smoke on `test/images/{ALICE, ANIME_CHARACTER_01}`: % faces labelled, input-view vs multi-view label agreement,
  part counts, previews per part; manual check in Blender.
- Explicitly measure anime performance of Grounding DINO + SAM 2.1 on `eyebrow`/`eyelash`/`eye` (unknown [A]).

### 6.9 Risks / open questions
1. Stylised/anime faces: open-vocab detectors may miss eyebrows/eyelashes → keep them as regions and offer manual
   correction (GeoSAM2-like click) later.
2. Back-side labels rely on segmenting renders of generated textures (lower quality than the input view) [A].
3. Thin structures (strands, lashes) bleed across labels at mesh resolution; feature-ID textures may be better for
   painted details [A].
4. Hair/clothes cuts leave holes in the body; consumers must treat `open_boundary=true` parts as non-watertight until
   an `AmodalCompleter` exists.
5. Licences: never vendor NVIDIA-NC, Tencent, SAM-Licence or CC-BY-NC code/weights into the GPL-3.0 repo; plug-ins
   that need them download at the user's request and are documented in THIRD_PARTY_NOTICES. Tencent licence territory
   exclusions (EU/UK/KR) may matter depending on where the app is used.
6. transformers is pinned at 4.57.6 for TRELLIS.2's DINOv3 internals [V env skill]; anything needing transformers v5
   (SAM 3) must be isolated or wait.
7. Single GPU: part analysis, T2I and generation must share one queue/lock [A].

---------------------------------------------------------------------------------------------------------------------

## 7. Sources

Vendors
- Tripo mesh segmentation API: https://developers.tripo3d.com/en/docs/mesh-segment.md
- Tripo options (generateParts, smartLowPoly, segmentation v1/v2) via Scenario: https://docs.scenario.com/get-started/generation/3d-model-generation/3d-model-generation-tripo-ai
- ComfyUI Tripo nodes: https://docs.comfy.org/built-in-nodes/TripoSmartSegmentNode.md , https://docs.comfy.org/built-in-nodes/TripoMeshCompleteNode.md
- fal.ai Tripo segment: https://fal.ai/models/tripo3d/tripo/segment/llms.txt
- Tripo 3.0 press release: https://www.businesswire.com/news/home/20250820723593/en/Tripo-the-Frontrunner-of-3D-AI-Boom-Supercharges-New-Era-in-Content-Creation-with-3.0-Upgrade
- Hunyuan3D-Part: https://github.com/Tencent-Hunyuan/Hunyuan3D-Part , licence https://github.com/Tencent-Hunyuan/Hunyuan3D-Part/blob/main/LICENSE , weights https://huggingface.co/tencent/Hunyuan3D-Part
- fal Hunyuan 3D v3.1 part: https://fal.ai/models/fal-ai/hunyuan-3d/v3.1/part ; Scenario Hunyuan guide: https://help.scenario.com/articles/5967392966-hunyuan-3d-models-the-essentials

Open models / papers
- P3-SAM https://arxiv.org/abs/2509.06784 ; X-Part https://arxiv.org/abs/2509.08643
- PartField https://github.com/nv-tlabs/PartField (licence https://github.com/nv-tlabs/PartField/blob/main/LICENSE), https://arxiv.org/abs/2504.11451, weights https://huggingface.co/mikaelaangel/partfield-ckpt
- SAMPart3D https://github.com/Pointcept/SAMPart3D , https://arxiv.org/abs/2411.07184
- SAMesh https://github.com/gtangg12/samesh , https://arxiv.org/abs/2408.13679
- GeoSAM2 https://github.com/VAST-AI-Research/GeoSAM2 , https://huggingface.co/VAST-AI/GeoSAM2 , https://arxiv.org/abs/2508.14036
- PartSAM https://arxiv.org/abs/2509.21965
- HoloPart https://github.com/VAST-AI-Research/HoloPart , https://huggingface.co/VAST-AI/HoloPart , https://arxiv.org/abs/2504.07943
- OmniPart https://github.com/HKU-MMLab/OmniPart , https://huggingface.co/omnipart/OmniPart , https://arxiv.org/abs/2507.06165
- PartCrafter https://github.com/wgsxm/PartCrafter , https://huggingface.co/wgsxm/PartCrafter
- PartPacker https://github.com/NVlabs/PartPacker (licence https://github.com/NVlabs/PartPacker/blob/main/license.md), https://huggingface.co/nvidia/PartPacker
- FullPart https://github.com/hkdsc/fullpart , https://arxiv.org/abs/2510.26140
- StructParts https://arxiv.org/html/2610.21850 ; SAM3D-Part https://arxiv.org/abs/2609.15639 ; UniPart https://arxiv.org/abs/2512.09435
- AutoPartGen https://arxiv.org/abs/2507.13346 ; PartGen https://arxiv.org/abs/2412.18608 ; Part123 https://arxiv.org/abs/2405.16888
- CubePart https://arxiv.org/html/2605.28763 ; MegaParts https://arxiv.org/pdf/2608.14783 ; SCULPT https://arxiv.org/abs/2608.13541 ; PartMat https://arxiv.org/abs/2608.01825
- PartUV https://arxiv.org/abs/2511.16659
- SAM 3D Objects https://github.com/facebookresearch/sam-3d-objects (setup https://github.com/facebookresearch/sam-3d-objects/blob/main/doc/setup.md), https://huggingface.co/facebook/sam-3d-objects
- SAM 3D Body https://github.com/facebookresearch/sam-3d-body
- SAM 3 https://github.com/facebookresearch/sam3 (licence https://github.com/facebookresearch/sam3/blob/main/LICENSE), https://huggingface.co/docs/transformers/model_doc/sam3
- Sonata https://github.com/facebookresearch/sonata

2D segmenters
- https://huggingface.co/IDEA-Research/grounding-dino-base ; https://huggingface.co/facebook/sam2.1-hiera-large
- https://huggingface.co/jonathandinu/face-parsing ; https://huggingface.co/mattmdjaga/segformer_b2_clothes
- Sapiens: https://huggingface.co/facebook/sapiens-seg-1b-torchscript ; Anime-Face-Segmentation: https://github.com/siyeong0/Anime-Face-Segmentation

Dependencies
- spconv wheels: https://pypi.org/simple/spconv-cu126/ , https://pypi.org/simple/spconv/
- PyG wheels: https://data.pyg.org/whl/torch-2.14.0+cu130.html
- kaolin install notes: https://kaolin.readthedocs.io/en/latest/notes/installation.html

Formats / pipelines
- glTF 2.0 spec: https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html
- EXT_mesh_features: https://github.com/CesiumGS/glTF/tree/3d-tiles-next/extensions/2.0/Vendor/EXT_mesh_features
- VRMC_vrm 1.0: https://github.com/vrm-c/vrm-specification/blob/master/specification/VRMC_vrm-1.0/README.md
- MetaHuman slots: https://dev.epicgames.com/documentation/unreal-engine/API/Plugins/MetaHumanCharacter/EMetaHumanCharacterTeethAndEyesS-
- Character Creator eyes: https://wiki.reallusion.com/Content_Dev:CC_Eyes_Teeth_Replacement
- KHR_node_visibility (status): https://gltf-transform.dev/modules/extensions/classes/KHRNodeVisibility

Prompt-to-3D candidates
- https://huggingface.co/Tongyi-MAI/Z-Image-Turbo ; https://huggingface.co/black-forest-labs/FLUX.2-klein-4B
