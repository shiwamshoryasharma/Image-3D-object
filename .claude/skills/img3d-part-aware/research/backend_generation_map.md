# Backend generation-path map (TRELLIS.2 image-to-3D) — for the part-aware milestone

Phase: READ-ONLY understanding (2026-10-16). No project file was changed except this report.
Legend: **[V]** verified (file read / command run in this session), **[A]** assumption / inference not verified at runtime,
**[M]** measured from existing `runtime/jobs/*/job.json` metadata (7 completed jobs; `job_269a864ac597` /
`job_fa0c4771ec03` were not opened). All paths are relative to `E:\14-OCT-2026\Img3D\`.

---------------------------------------------------------------------------------------------------

## 0. One-paragraph summary

The generation path is a strict single-GPU-thread chain: `JobService.create_job` → `JobQueue` →
`GenerationWorker._process` → (gpu-worker thread) `ImageTo3DPipeline.run` → `TrellisEngine.generate` →
`TrellisGenerationRun.execute` (7 engine stages) → `export_glb` (`o_voxel.postprocess.to_glb`) → back in the
pipeline: `compute_glb_stats` → `JobResultData`. Every rich intermediate (premultiplied RGB crop, DINOv3 tokens,
sparse coords, shape/texture SLats, the 10–16 M-face decoded `MeshWithVoxel` + PBR voxel volume) lives only in
local variables of `TrellisGenerationRun.execute` and is deleted before the method returns; only `preview.png`
(RGB, no alpha) and `model.glb` (single mesh, 1 material, 2 textures, remeshed) are persisted. Export (remesh +
UV unwrap + bake) is the dominant cost (77–330 s, 46–66 % of total) and it **rebuilds topology** (`remesh=True`),
so any per-face part label must be computed geometrically, not by face index. The `InferenceEngine` seam is
clean (torch-free base, lazy registry, `UnavailableEngine` fallback) but is hard-wired to ONE engine in the
registry, worker, container and `/system`. Adding segmentation / text-to-image models is feasible on the same
GPU **only** through the existing single `gpu-worker` executor, using the same per-stage CPU⇄GPU swapping that
`low_vram=True` (the default) already uses for TRELLIS.

---------------------------------------------------------------------------------------------------

## 1. Exact stage sequence, progress stages and timing knobs

### 1.1 Call chain [V]

| # | Where | What |
|---|---|---|
| 1 | `backend/services/job_service.py:83-144` `JobService.create_job` | validate vs `EngineCapabilities` (`services/generation_settings.py:13-49`) and reference strategy; resolve seed (`secrets.randbelow` if `randomize_seed`); snapshot ALL references; `_prepare_inputs` (`:146-167`) writes `jobs/<id>/inputs/<image_id>.png` via `processing/image_ops.py:70-76 normalize_to_png` (EXIF applied, 8-bit RGB/RGBA) in a thread; `JobStore.save` + `JobQueue.put` |
| 2 | `backend/workers/queue.py:11-89` `JobQueue` | FIFO of job ids, event-loop only, `max_queue_size=16` (`config/settings.py:93`) |
| 3 | `backend/workers/generation_worker.py:257-276` `_run` → `:299-367` `_process` | creates `CancellationToken` + `JobProgressReporter`; status RUNNING; if engine not ready → `waiting_for_model` and `_wait_for_engine` (`:374-407`, poll 0.5 s, auto-load once); then `self._submit(self._execute_pipeline, …)` on the single executor (`:85`, `ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpu-worker")`) |
| 4 | `backend/workers/generation_worker.py:369-372` `_execute_pipeline` | gpu-worker thread → `self._pipeline.run(job, reporter, token)` |
| 5 | `backend/pipelines/image_to_3d.py:90-151` `ImageTo3DPipeline.run` | `resolve_references` (`:73-88`, all job inputs, primary first) → `get_strategy(mode)` (`pipelines/reference_strategy.py:75-79`; `PrimaryOnlyStrategy.select` `:45-51` returns `[primary]`) → `build_generation_params` (`:50-64`) → `GenerationRequest` (`:103-110`, `output_dir = jobs/<id>/`) → **`self._engine.generate(request, reporter, cancel)` (`:119`)** |
| 6 | `backend/inference/trellis/engine.py:352-423` `TrellisEngine.generate` | `_validate` (`:330-350`); holds `_lifecycle_lock` (`:359`); `torch.cuda.set_device(index)` (`:367-369`); builds `TrellisGenerationRun` (`:373-379`); `run.execute()` (`:389`); maps OOM/other errors to `EngineError` (`:396-409`); **always** `_release_after_run` (`:410-414`, moves models to CPU in low-VRAM mode + `gc.collect` + `empty_cache`, `:301-326`) |
| 7 | `backend/inference/trellis/generation.py:182-347` `TrellisGenerationRun.execute` | the 7 engine stages (table 1.2) under `torch.no_grad()` (`:194`) |
| 8 | `backend/inference/trellis/export.py:20-93` `export_glb` | `o_voxel.postprocess.to_glb(...)` (`:41-56`) then `glb.export(tmp, file_type="glb", extension_webp=...)` + `os.replace` (`:63-73`) |
| 9 | `backend/pipelines/image_to_3d.py:121-151` | `reporter.stage(FINALIZING)` (`:122`); `_place` (no-op: engine already wrote to the canonical paths); `compute_glb_stats(model.glb)` (`:128-133`); timings; `JobResultData` (metadata incl. `engine_mesh_stats`, not exposed by the API) |
| 10 | `backend/workers/generation_worker.py:419-455` `_finish` | status COMPLETED, `progress = done/100 %`, unthrottled publish + persist `job.json` |

### 1.2 Engine stages (emitted by `TrellisGenerationRun`) [V] + observed durations [M]

`JobStage` enum: `backend/models/enums.py:66-77`. Stage table (index/label/weight): `backend/models/job.py:21-34`
(STAGE_COUNT = 9). Percent formula: `backend/pipelines/progress.py:39-50`.

| Stage (weight) | Code | Work | Models touched (low-VRAM: moved to GPU only for the call) | Progress steps | Observed s [M] |
|---|---|---|---|---|---|
| `preprocessing` (5) | `generation.py:196-197` → `_preprocess` `:137-178` | load PNG (`imaging.py:38-44`), decide path (`imaging.py:63-65`): `alpha_crop` / `background_removed` (`pipeline.preprocess_image`, upstream `trellis2_image_to_3d.py:127-162`) / `untouched` (RGB as-is); save `preview.png` atomically (`:173-176`) | BiRefNet (only on `background_removed`) | 2 | 0.06–1.30 |
| `conditioning` (5) | `:200-204` | **`torch.manual_seed(seed)` at `:202`** (exactly where upstream seeds); `get_cond([image], 512)` and, for cascade, `get_cond([image], 1024)` | DINOv3 ViT-L/16 | none (0) | 0.78–5.49 |
| `sparse_structure` (15) | `:207-225` | `sample_sparse_structure(cond_512, ss_res, 1, overrides)` (upstream `:188-235`); empty-structure guard `:220-225` | SS flow DiT (1.3B) + TRELLIS-v1 SS decoder | `steps` (default 12) via sampler hook | 3.41–16.25 |
| `shape` (30) | `:228-278` | `512`: `sample_shape_slat`; `1024_cascade`/`1536_cascade`: `sample_shape_slat_cascade` (`:257-267`, upstream `:277-364`: LR pass → `shape_slat_decoder.upsample(×4)` → token-capped HR pass) | shape flow 512 (+1024), shape decoder (upsample only) | `steps` or `2*steps` (cascade) | 31.9–137.7 |
| `texture` (20) | `:281-291` | `sample_tex_slat(tex_cond, tex_model, shape_slat, overrides)` (upstream `:391-432`); `del cond_*`, `torch.cuda.empty_cache()` (`:290-291`) | tex flow 512 or 1024 | `steps` | 12.4–94.9 |
| `decoding` (8) | `:294-303` | `pipeline.decode_latent(shape_slat, tex_slat, resolution)[0]` (upstream `:455-486`) → `MeshWithVoxel`; `del shape_slat, tex_slat` (`:296`); empty-mesh guard | shape decoder, tex decoder | none | 1.30–27.0 |
| `exporting` (15) | `:306-315` → `export.py:41-93` | `to_glb`: fill holes → BVH → **narrow-band DC remesh** → simplify to `polygon_target` → UV unwrap → bake PBR from voxel volume → inpaint → trimesh; write GLB (WEBP textures) | none (CuMesh / nvdiffrast / FlexGEMM kernels) | 2 (`EXPORT_STEPS`) | 76.7–330.4 (bake 69.7–323.4, write 1.8–7.0) |
| `finalizing` (2) | `image_to_3d.py:122-139` | stats via trimesh + GLB JSON | none | none | 0.20–0.44 |

Totals [M]: 133–579 s for 1536 res / 4096 tex / 2 M faces (6 jobs) and 269 s for 1536/2048/1 M (1 job). Export is
46–66 % of total in every job. All 7 jobs ran `pipeline_type=1536_cascade`, `attention_backend=sdpa`, `low_vram=true`.

### 1.3 Timing / quality knobs [V]

| Knob | Where | Effect |
|---|---|---|
| `resolution` 512/1024/1536 | `GenerationSettings` (`schemas/jobs.py:35-47`) → `PIPELINE_TYPES` (`generation.py:44`) | pipeline type, cond resolutions, SS res (`:47`), cascade target (`:48`), decode grid |
| `MAX_NUM_TOKENS = 49152` | `generation.py:49` | cascade lowers HR res in 128 steps until tokens < cap (reported via `effective_resolution`) |
| sampler `steps` / `guidance_strength` / `guidance_rescale` / `rescale_t` (3 samplers) | `SamplerOverrides` (`inference/base.py:105-115`); bounds `schemas/jobs.py:22-26` (steps 1..50) | linear in steps for SS/shape/texture stages; defaults from `pipeline.json` (12 steps each; SS 7.5/0.7/5.0, shape 7.5/0.5/3.0, tex 1.0/0.0/3.0; `guidance_interval` [0.6,1.0] / [0.6,1.0] / [0.6,0.9] not overridable) |
| `polygon_target` 5 k..2 M | `export.py:49` `decimation_target` | simplify target after remesh |
| `texture_size` 512..4096 | `export.py:50` | bake raster size → dominant bake cost |
| `remove_background` | `imaging.py:63-65` | runs BiRefNet or not |
| `glb_webp_textures` (setting, default true) | `config/settings.py:80`, `export.py:68` | GLB size/write time |
| `low_vram` (default **true**) | `config/settings.py:76` | per-stage CPU⇄GPU moves (upstream code paths) |
| `attention_backend` auto | `config/settings.py:77`, `environment.py:388-425` | `sdpa` selected on this machine (flash_attn/xformers unavailable) + sparse SDPA fallback (`compat.py:212-256`) |
| Fixed export args | `export.py:51-53` | `remesh=True, remesh_band=1, remesh_project=0` (upstream `app.py` values) |
| Progress publish / persist | `pipelines/progress.py:33-34` | 0.2 s (≤5 msg/s) / 2.0 s `job.json` writes |

---------------------------------------------------------------------------------------------------

## 2. Intermediate representations inside the TRELLIS.2 adapter

All live inside `TrellisGenerationRun.execute` (`backend/inference/trellis/generation.py:182-347`) on the CUDA device
of the gpu-worker thread; none is persisted except `preview.png` and `model.glb`.

| Representation | Variable / line | Type & shape | Notes |
|---|---|---|---|
| Source image | `_preprocess` `:142` | PIL `RGB`/`RGBA` (EXIF-transposed, `imaging.py:38-44`) | from `jobs/<id>/inputs/<img>.png` |
| Background matte | inside upstream `preprocess_image` (`trellis2_image_to_3d.py:143-149`) / `BiRefNet.__call__` (`pipelines/rembg/BiRefNet.py:31-41`, `putalpha` at `:40`) | 1024×1024 sigmoid → resized L mask | **Not accessible**: upstream converts to a fresh RGB copy, BiRefNet puts alpha on that local copy, the result is premultiplied and alpha is dropped. [V] |
| Conditioning image (`processed`, then `image`) | `_preprocess` `:158` / `:170`; used at `:203-204` | PIL **RGB**, square crop around `alpha > 0.8·255` bbox of the ≤1024-px-downscaled input, **premultiplied by alpha (black background)**; `untouched` mode: full RGB image | crop box may extend outside the image (PIL pads with black). Crop box/scale are not recorded. [V] |
| `preview.png` | `:173-176` | RGB PNG, ≤1024 px (`imaging.py:68-73`) | the only persisted 2D intermediate; equals the conditioning image (except downscale in `untouched`). [V] |
| DINOv3 conditioning | `cond_512` `:203`, `cond_1024` `:204` | `dict{'cond','neg_cond'}`; `cond` float tensor `[1, 1+4+(S/16)², 1024]` → **512: `[1,1029,1024]`, 1024: `[1,4101,1024]`**; token order `[CLS, 4 registers, patches row-major]`; `neg_cond = zeros_like` | `image_feature_extractor.py:59-118` (resizes to S×S LANCZOS, ImageNet norm, final `layer_norm`); config `num_register_tokens=4`, `hidden_size=1024`, `patch_size=16` (`.model/dinov3-vitl16-pretrain-lvd1689m/config.json`); prefix order `transformers/models/dinov3_vit/modeling_dinov3_vit.py:70`. Deleted at `:290`. [V] |
| SS noise / latent | upstream `:205-219` | `[1, 8, 16, 16, 16]` (flow `resolution=16, in_channels=8`) | `ss_flow_img_dit_1_3B_64_bf16.json` [V] |
| Sparse occupancy | upstream `:224-233` | decoder → `[1,1,64,64,64]` logits `>0`; max-pooled to 32³ for `512`/`*_cascade` | 64³ derived from checkpoint name + the `1024` path using 64 without pooling [A-minor] |
| `coords` | `:213-218`, deleted `:278` | `int32 [N, 4]` = (batch, x, y, z) on 32³; **N = 1 621–2 579** [M] | `sparse_voxels` in metadata |
| `shape_slat` | `:247-267`, deleted `:296` | `trellis2.modules.sparse.SparseTensor`: `feats [N_tok, 32]` (de-normalized with `shape_slat_normalization`), `coords [N_tok, 4]` on `res/16` grid (64³ for 1024, 96³ for 1536) | **N_tok = 16 050–26 713** [M], cap 49 152. `resolution` var = effective res. Tiny (~2–6 MB) — cheap to persist. |
| `tex_slat` | `:289`, deleted `:296` | `SparseTensor feats [N_tok, 32]`, same coords as `shape_slat` | tex flow `in_channels=64` = 32 noise + 32 shape concat (`slat_flow_imgshape2tex_*.json`) [V] |
| Decoder outputs | upstream `decode_shape_slat` `:366-389`, `decode_tex_slat` `:434-453` | shape decoder (`FlexiDualGridVaeDecoder`, `models/sc_vaes/fdg_vae.py:83-110`) → `Mesh` + `subs`; tex decoder (`SparseUnetVaeDecoder`, `out_channels=6`) guided by `subs` → `SparseTensor` 6 ch, `*0.5+0.5` | `Mesh.fill_holes()` applied in `decode_latent` (upstream `:474`) |
| **`mesh` (O-Voxel result)** | `:295`, deleted `:315` | `MeshWithVoxel` (`trellis2/representations/mesh/base.py:190-234`): `vertices float32 [V,3]`, `faces int32 [F,3]` in **Z-up unit cube [-0.5,0.5]³**; `origin=[-0.5]*3`, `voxel_size=1/res`, `coords [L,3]` (int voxel indices at res³), `attrs [L,6]` float in [0,1] laid out `base_color 0:3, metallic 3:4, roughness 4:5, alpha 5:6` (`loader.py:371-376`), `voxel_shape=[1,6,res,res,res]`; `query_attrs(xyz)` trilinear via FlexGEMM | **V = 4.76–8.29 M, F = 9.68–16.47 M** [M] (`decoded_vertices/faces`). All on CUDA. |
| Post-processing (inside `to_glb`, `o-voxel/o_voxel/postprocess.py:14-331`) | `export.py:41-56` | (1) `CuMesh.fill_holes(3e-2)` `:110`; (2) `cuBVH(vertices, faces)` `:122`; (3) remesh branch `:165-187`: `cumesh.remeshing.remesh_narrow_band_dc(center, scale, resolution=grid, band=1, project_back=0, bvh)` then `simplify(decimation_target)`; (4) `uv_unwrap` (CuMesh/xatlas-style charts, cone 90°) `:201-210`, vertex normals `:215-216`; (5) bake: nvdiffrast UV-space raster in 100 k-face chunks `:230-246`, texel → original surface via **`bvh.unsigned_distance(valid_pos, return_uvw=True)` → `face_id`** `:254-256`, FlexGEMM `grid_sample_3d` trilinear on the attr volume `:259-266`; (6) `cv2.inpaint` seam dilation `:288-292`; (7) `PBRMaterial` (RGBA baseColor + ORM-style metallicRoughness; `doubleSided=False` when remesh) `:296-304`; (8) **axis conversion Z-up → Y-up** (swap Y/Z, negate) `:312-315` | returns `trimesh.Trimesh` (single mesh, UVs, normals) |
| GLB on disk | `export.py:63-73` | 1 mesh, 1 material, 2 textures (`stats.materials=1, textures=2` [M]); 41–86 MB [M] | Y-up frame, unit-cube scale |

Also present locally but **not loaded** by the app [V]: `TRELLIS.2-4B/texturing_pipeline.json`,
`ckpts/shape_enc_next_dc_f16c32_fp16` (709 MB) and `ckpts/tex_enc_next_dc_f16c32_fp16` (709 MB). Upstream
`trellis2/pipelines/trellis2_texturing.py:183-222 encode_shape_slat` (mesh → O-Voxel via
`o_voxel.convert.mesh_to_flexible_dual_grid` → shape SLat) and `:375-408 run` (texture any mesh from an image) are
the natural future tool for re-texturing separated parts (e.g. surfaces exposed under hair/clothes). Not for this
milestone (refinement), but the design should leave room for it.

---------------------------------------------------------------------------------------------------

## 3. The `InferenceEngine` seam and how to register more engines

### 3.1 Current contract [V] (`backend/inference/base.py`)

- Torch-free module (imports only stdlib, `core.exceptions`, `models.enums`).
- `EngineCapabilities` (`:40-61`), `EngineStatus` (`:74-97`, immutable, replaced on transition), `LoadProgress`.
- `GenerationParams` (`:118-128`), `ConditioningImage` (`:131-138`), `GenerationRequest` (`:141-158`: `images`
  strategy-selected primary-first + `all_references` + `output_dir` + `reference_mode`), `GenerationOutput`
  (`:161-172`: `glb_path`, `preview_path`, `effective_resolution`, `timings`, `mesh_stats`, `metadata`; non-frozen
  slots dataclass; constructed only by keyword in `generation.py:340` and `test/backend/fakes.py:186`).
- `ProgressReporter` Protocol (`:180-188`) — `stage(JobStage, message, total_steps)` / `step(...)`; **stage must be a
  `JobStage` that exists in `STAGE_TABLE`** (`progress.py:87-111` indexes `STAGE_TABLE[stage]`).
- `CancellationToken` (`:201-224`).
- `InferenceEngine` ABC (`:234-272`): `status()`/`capabilities()` cheap+thread-safe; `load(on_status)` / `unload()` /
  `generate(request, reporter, cancel) -> GenerationOutput` blocking, called ONLY on the gpu-worker thread, must
  free transient GPU memory on every exit; optional `device_info()`.
- Registry (`backend/inference/registry.py:22-53`): `SUPPORTED_ENGINES = ("trellis2",)`; `create_engine(settings)`
  lazily imports `inference.trellis.engine` and returns `UnavailableEngine` (`inference/unavailable.py:23-61`) on
  import/construct failure — the API never imports torch.

### 3.2 Where "one engine" is hard-wired [V]

`core/container.py:79` (`engine = create_engine(settings)`), `:84` (`ImageTo3DPipeline(engine, …)`), `:86`
(`SystemService(settings, engine, …)`), `:87-95` (`GenerationWorker(engine=…)`), `:100-109` (`JobService(engine=…)`);
`GenerationWorker` lifecycle methods (`request_engine_load`, `_wait_for_engine`, `unload_engine`) all target
`self._engine`; `services/system_service.py:112-123` exposes one `EngineInfo`; `Job.engine` is a single string.

### 3.3 Recommended way to add a segmentation and a text-to-image engine (design, not implemented)

1. **Do not overload `InferenceEngine.generate`** (its output is a GLB). Add sibling torch-free ABCs next to it,
   sharing the lifecycle half of the contract:
   - `inference/base.py` (additive): a small `ModelEngine` base with `name`, `status()`, `capabilities()`,
     `load(on_status)`, `unload()`, `device_info()`, plus **new optional** `offload()` (move weights to CPU, keep
     loaded; default no-op) — `InferenceEngine` keeps its exact current signature.
   - `inference/segmentation_base.py`: `SegmentationEngine.segment(request: SegmentationRequest, reporter, cancel)
     -> SegmentationOutput` where the request carries an image path (+ optional foreground mask path), a label
     vocabulary (`eyes, eyebrows, eyelashes, hair, clothes, accessories, detachable, face, body, …`) and output dir;
     the output is a label-map PNG (uint8/uint16 ids) + per-label binary masks + `labels.json` (id → name, score,
     bbox) — files and plain dicts only, no tensors across the seam.
   - `inference/imagegen_base.py`: `ImageGenerationEngine.generate_image(prompt, negative, seed, size, out_path,
     reporter, cancel) -> ImageGenerationOutput(path, metadata)`.
   - `inference/part_base.py` (3D side, later): `PartDecomposer.decompose(mesh artifact paths, 2D label maps,
     camera/crop info) -> PartManifest`.
2. **Registry**: keep `create_engine(settings)` unchanged for the reconstruction engine and add
   `create_aux_engines(settings) -> dict[str, ModelEngine]` with the same lazy-import + `Unavailable*` pattern
   (e.g. `inference/segmentation/<impl>/engine.py`, `inference/imagegen/<impl>/engine.py`). Each implementation
   owns its torch/transformers imports exactly like `inference/trellis/`.
3. **Worker**: keep ONE gpu-worker executor (`generation_worker.py:85`). Give the worker an `EngineSet`
   (`reconstruction` + `aux`) and a tiny "GPU residency" policy: before running a stage that needs engine X, call
   `offload()` on any other engine that currently holds VRAM. With `low_vram=True` TRELLIS already returns to CPU
   after every run (`engine.py:312-326`), so the policy is mostly about aux engines and the `low_vram=False` case.
4. **API/services** only ever see `status().to_dict()` / `capabilities().to_dict()` → an additive
   `engines: {name: EngineInfo}` map in `/system` (keep the existing `engine` field for the current UI).
5. `pipelines/` composes engines (e.g. a `PartAwarePipeline` that wraps `ImageTo3DPipeline` and calls the
   segmentation engine before/after `engine.generate`); no pipeline imports torch.

### 3.4 Dependency facts relevant to new engines [V]

Embedded Python 3.14.2; torch 2.14.1+cu130, torchvision 0.29.1+cu130, transformers **4.57.6**, huggingface-hub
**0.36.2**, trimesh 5.1.1, numpy 2.4.1, pillow 12.3.0, opencv-python-headless 5.0.0.93, scipy 1.18.1, timm 1.0.30,
kornia 0.8.3, einops 0.8.2, networkx 3.6.1, safetensors 0.8.0, triton-windows 3.8.0.post29, o_voxel 0.0.1,
cumesh 0.0.1, flex_gemm 1.0.0, nvdiffrast 0.4.0. **Not installed**: diffusers, accelerate, scikit-image,
scikit-learn, pygltflib, open3d, pymeshlab, xatlas, onnxruntime, segment-anything, sam2.

- The pinned transformers 4.57.6 already ships these model families (directory listing of
  `backend/python/Lib/site-packages/transformers/models/`): `sam`, `sam2`, `sam2_video`, `sam_hq`, `grounding_dino`,
  `mm_grounding_dino`, `owlv2`, `owlvit`, `clipseg`, `mask2former`, `oneformer`, `segformer`, `upernet`, `eomt`,
  `florence2`, `dpt`, `depth_anything`. ⇒ a 2D segmentation engine can be built **without installing any package**
  (weights must be downloaded later; licenses to be checked per checkpoint — not verified here).
- Text-to-image needs a new package. PyPI metadata (looked up 2026-10-16): **diffusers 0.41.0 requires
  `huggingface-hub<2.0,>=1.32.0`** → incompatible with the hub 0.36.2 pin that transformers 4.57.6 / TRELLIS.2 need;
  **diffusers 0.35.2 (2025-10-15) requires `huggingface-hub>=0.34.0`** → compatible. Any install must use
  `--no-deps`-style care and be verified with the TRELLIS import probe (`environment.py:452-483`).

---------------------------------------------------------------------------------------------------

## 4. GPU / VRAM handling and implications for extra models

### 4.1 Facts [V]

- **Single GPU thread**: `GenerationWorker._executor = ThreadPoolExecutor(max_workers=1, "gpu-worker")`
  (`generation_worker.py:85`). `engine.load()` (`:144-155`), `pipeline.run()` (`:369-372`) and `engine.unload()`
  (`:165-179`, `:210-223`) are all submitted there, so they never overlap. The tuning worker has its own
  `blender-worker` thread and runs Blender as a subprocess (`workers/tuning_worker.py:4-5, :304`); it never touches
  the CUDA context of this process.
- `TrellisEngine` serializes `load/unload/generate` with an `RLock` (`engine.py:110, :189, :290, :359`) and calls
  `torch.cuda.set_device(index)` per run (`:367-369`); upstream hard-codes `"cuda"` (`loader.py:382-384`).
- **`low_vram=True` is the default** (`config/settings.py:76`). Upstream `Trellis2ImageTo3DPipeline.to()` does not
  move anything when `low_vram` (`trellis2_image_to_3d.py:119-125`); every stage method moves its model to GPU and
  back (`:145-149, :175-179, :210-229, :258-269, :303-326, :382-388, :414-426, :448-452`). Engine cleanup after any
  outcome moves every model + DINOv3 + rembg back to CPU and resets `shape_slat_decoder.low_vram`
  (`engine.py:312-326`), then `gc.collect()` + `torch.cuda.empty_cache()` (`:301-310`); exceptions have their
  tracebacks dropped to release frames holding tensors (`:84-99, :413`).
- OOM mapping: `_is_oom` (`engine.py:76-81`) → `EngineError` with hint (`:58-61`); worker `classify_exception`
  (`generation_worker.py:46-56`).
- `PYTORCH_CUDA_ALLOC_CONF=expandable_segments` deliberately NOT set (Windows) (`environment.py:17-18`).
- Weight sizes on disk (`backend/.model`): 5 flow DiTs × 2.58 GB (bf16) = 12.9 GB; shape decoder 0.95 GB; tex decoder
  0.95 GB; SS decoder 0.15 GB; DINOv3 1.21 GB (fp32 safetensors); BiRefNet 0.44 GB ⇒ ≈ 16.6 GB resident in system
  RAM in low-VRAM mode. `RMBG-2.0/` holds only `.cache/` ⇒ the runtime uses `rembg_kind=birefnet`
  (`assets.py:339-356`).
- Device info for `/system` uses `torch.cuda.mem_get_info` from any thread (`inference/device.py:98-101, :142-176`).

### 4.2 Implications for adding segmentation / text-to-image on the same GPU

1. **Every CUDA call must run on the gpu-worker thread.** A second executor for segmentation would race TRELLIS
   for VRAM (decoded mesh + export already hold multi-GB buffers at 1536/4096 [A]) and break the
   "load never overlaps generate" guarantee. Aux engines must be scheduled through the same executor.
2. **Follow the low-VRAM pattern** for aux models: keep weights on CPU, move to GPU for the stage, move back, then
   `empty_cache()`. Segmentation models in the 0.2–1 GB class [A] are negligible next to TRELLIS stages. A
   text-to-image model (several GB to >20 GB in bf16 depending on model [A]) must run **before** any TRELLIS stage
   of the job (it produces the input image) and be fully offloaded/unloaded before `preprocessing`.
3. With `low_vram=False` TRELLIS keeps ≈16.6 GB of weights on the 32 GB card between jobs; a large text-to-image
   model then requires an engine-level `offload()` (not available today — needs the additive seam method from 3.3)
   or a full `unload()`/`load()` cycle (reload time not measured).
4. RAM is not the constraint (130 GB): TRELLIS ≈16.6 GB + a large T2I model fit in RAM simultaneously.
5. **RNG**: parity with upstream is pinned by `test/engine/test_generation_parity.py` (call sequence + torch RNG
   fingerprints). Any new GPU work that consumes the global torch RNG must happen **before** `torch.manual_seed`
   (`generation.py:202`) or after sampling, or use a private `torch.Generator`; otherwise same-seed results change.
6. Progress/cancel: new stages need `CancellationToken` checks and `ProgressReporter` calls; new `JobStage` values
   need `STAGE_TABLE` entries (`models/job.py:21-34`) or `JobProgressReporter.stage` raises `KeyError`.

---------------------------------------------------------------------------------------------------

## 5. Job artifact layout, retention and result statistics

### 5.1 On-disk layout [V] (`storage/paths.py:3-10, :22-26`; `storage/job_store.py`; `storage/tuning_store.py:1-5, :26`)

```
backend/runtime/
  uploads/<rs_id>/set.json, <img_id>.<ext>, <img_id>_thumb.webp        (ReferenceStore)
  jobs/<job_id>/
    job.json                     # Job (DomainModel, extra="ignore"), atomic writes (json_io.py:13-19)
    inputs/<img_id>.png          # normalized PNG of EVERY reference of the set (all slots), written at creation
    preview.png                  # processed conditioning image, RGB, <=1024 px (engine writes directly)
    model.glb                    # final GLB (engine writes directly; tmp + os.replace)
    tuning/<tn_id>/run.json, tuned.glb, report.json, blender.log, config.json, previews/*.png[, tuned.blend]
  temp/      (purged at startup; tuning copies at temp/tuning/<tn>/, Blender TEMP at temp/blender/<tn>)
  cache/     (triton/, flex_gemm/autotune_cache.json — environment.py:246-252)
  blender/   (managed portable Blender)
```

The engine's `output_dir` IS the job dir (`image_to_3d.py:101`), so `_place` (`:153-167`) is a no-op for TRELLIS.
Observed sizes [M]: `model.glb` 41–86 MB, `preview.png` 0.43–0.67 MB, `job.json` 3.5–4.5 KB.

### 5.2 Retention [V] (`services/retention_service.py`)

Hourly (`retention_interval_s=3600`, first pass after 5 s; `:146-155`): terminal jobs older than
`job_retention_days=14` deleted, then oldest terminal beyond `max_stored_jobs=200` (`:60-77`;
`config/settings.py:94-95`); reference sets older than 30 days (lock-aware); unindexed job/set dirs without metadata
older than 1 h removed (`:31, :92-123`). `JobStore.delete` removes the whole job dir (`job_store.py:112-118`), so
**anything written under `jobs/<id>/` (e.g. `parts/…`) is cleaned automatically**. Startup marks queued/running jobs
`failed/interrupted` (`job_store.py:57-79`). Capacity today: 200 × ~85 MB ≈ 17 GB worst case.

### 5.3 Stats [V] (`processing/mesh_stats.py`)

`compute_glb_stats(path, fallback_texture_size)` (`:39-69`): reads the GLB JSON chunk (`read_glb_json` `:24-36`) for
`materials`, `textures`, `has_uv` (any primitive with `TEXCOORD_0`); loads with `trimesh.load(force="scene",
process=False)` (`:72-97`) and **sums** vertices/faces over `scene.graph.nodes_geometry`, largest material texture
side, `scene.bounds` → `bbox_min/max`, `dimensions` (NaN-safe); falls back to accessor counts. It is multi-node safe,
so a multi-node parts GLB would be counted correctly. Timings: engine per-stage seconds (`generation.py:129`) +
`finalizing` + pipeline `total` (`image_to_3d.py:135-139`). `JobResultData.metadata` (engine metadata +
`engine_mesh_stats`) is persisted but **not exposed** by `JobResultOut` (`schemas/jobs.py:101-128`).

---------------------------------------------------------------------------------------------------

## 6. Concrete hook points

### 6(a) PRE-reconstruction decomposition (2D segmentation of the conditioning image)

| Id | Hook | Data in scope | Pros / cons |
|---|---|---|---|
| **A1** | `generation.py:196-197`, i.e. right after `_preprocess` returns (`:177-178`) and **before** the seed (`:202`) | `image` (premultiplied RGB square crop = exactly what DINOv3 sees), `preview_path`, `preprocess_mode`, `self._request` (job_id, `images`, `all_references`, `params`, `output_dir`), `pipeline.rembg_model`, `pipeline.image_cond_model` | Pixel-aligned with conditioning; RNG-safe. But it is inside the TRELLIS adapter (segmentation model would be coupled to it) and the alpha matte is already lost. Needs a seam split (see recommendation). |
| **A2** | `generation.py:200-204` (conditioning) | `cond_512['cond'] [1,1029,1024]`, `cond_1024['cond'] [1,4101,1024]` | Free DINOv3 patch features (32×32 / 64×64 grid after 5 prefix tokens) for feature-based grouping or caching; deleted at `:290`. |
| **A3** | `pipelines/image_to_3d.py:94-119` (before `engine.generate`) | `Job`, `all_references` (ALL slots: front/back/left/right/face_front/face_side/detail), `params`, `output_dir` | Engine-agnostic, torch-free composition point; but the engine's crop is not known yet → masks computed here are in **original-image** coordinates, not conditioning coordinates. |
| **A4** | `pipelines/image_to_3d.py:121-127` (after generate, before stats) using `jobs/<id>/preview.png` | `output` (`GenerationOutput`), `preview.png`, job dir | Zero engine change; preview == conditioning image. Cons: RGB only (black background ambiguous with dark hair), so the foreground mask must be re-derived; cannot influence reconstruction. |

Recommendation for (a): add an **optional, additive engine capability** "prepared input": in `_preprocess`
persist an alpha-preserving `conditioning.png` (RGBA crop) plus `conditioning.json` (crop box in source pixels,
downscale factor, mode) by re-implementing upstream's crop math in torch-free `imaging.py` and asserting pixel
equality of the RGB result with upstream `preprocess_image` in a new test (the parity test must stay green). Then
run the segmentation engine in the pipeline layer (A4 position, before `finalizing`) on `conditioning.png` — no
segmentation code inside `inference/trellis/`. Per-part *separate* reconstructions from masked images (Tripo-like
"generate each part") are possible later via A3 but lose 3D registration (each run is re-cropped and re-normalized
to the unit cube) and multiply the 133–579 s runtime — not recommended for this milestone.

### 6(b) POST-reconstruction decomposition (label faces / split part meshes / multi-node GLB + manifest)

| Id | Hook | Data in scope | Notes |
|---|---|---|---|
| **B0** | `generation.py:294-296`, before `del shape_slat, tex_slat` | `shape_slat`, `tex_slat` (`SparseTensor [N_tok,32]` each), `coords`, effective `resolution`, `pipeline_type`, seed | **Persist latents** (`latents.npz`: coords int32 `[N,4]`, feats fp16/fp32, res; ≈2–6 MB) → any later part pipeline can re-decode the full-resolution mesh + voxel attrs in ~1–27 s without re-sampling. Highest value / lowest risk change in the adapter. |
| **B1** | `generation.py:297-306` (after decode, before export) | `mesh: MeshWithVoxel` on CUDA (Z-up, V 4.8–8.3 M, F 9.7–16.5 M), `mesh.coords [L,3]`, `mesh.attrs [L,6]`, `pipeline` (for `pbr_attr_layout`), `params`, `output_dir`, `reporter`, `cancel` | Best place to (1) compute **per-face labels at full resolution** on GPU: connected components (`cumesh.CuMesh.read_connected_components` `cumesh/cumesh.py:125-134` — eyes/accessories are often separate shells [A]), voxel-label volumes sampled with `grid_sample_3d`, or 2D-mask projection; (2) **physically split** detachable parts with `CuMesh.remove_faces(face_mask)` (`:246-256`) / `Mesh.remove_faces` (`representations/mesh/base.py:59-69`). Vectorized torch only (Python loops over 16 M faces are infeasible). |
| **B2** | `export.py:41-56` (`to_glb`) | same as B1 + `GLB_AABB`, `grid_size=resolution` | `remesh=True` rebuilds topology → face indices of the decoded mesh do **not** survive. Two options: (i) **single bake, split after**: run `to_glb` once (unchanged), then transfer labels to the output mesh by querying a `cumesh.cuBVH` of the decoded mesh with output face centroids (`bvh.unsigned_distance(..., return_uvw=True)` → `face_id`, `cumesh/bvh.py:54-84`; same trick `to_glb` uses at `postprocess.py:254`) and convert to Y-up; split by label with trimesh `submesh` — all parts share the one UV atlas/material → no extra bake cost; (ii) **per-part `to_glb`** (each physically separated part exported from its own face subset with the shared `coords/attrs`): independent atlases (better for later per-part optimization) but multiplies the 77–330 s export cost; note the non-remesh branch calls `remove_small_connected_components(1e-5)` (`postprocess.py:143, :156`) which can delete tiny parts. |
| **B3** | `pipelines/image_to_3d.py:121-139` (after export, before stats) | `model.glb` (Y-up, ~2 M faces, shared atlas), `preview.png`/`conditioning.png`, 2D label maps from 6(a), job dir, `reporter` (`finalizing`), `cancel` | Engine-agnostic post step: build `parts.glb` (multi-node `trimesh.Scene`, node names `part:<id>`, identity transforms, glTF `extras` per node) + `parts.json` manifest; compute per-part stats with `compute_glb_stats`-style helpers. Runs on the gpu-worker thread already. |
| **B4** | New run type modelled on tuning runs: `jobs/<job_id>/parts/<pt_id>/` + `run.json` (pattern: `storage/tuning_store.py`, `workers/tuning_worker.py`) but scheduled on the **gpu-worker** executor | `model.glb` (+ `latents.npz` if B0 exists), job inputs, label maps | Decouples decomposition from the working generation job (re-runnable, can be applied to old jobs, independent retries, no change to the generation stage table). Needs a GPU task queue in `GenerationWorker` (today it only consumes `JobQueue` of job ids, `:257-276`). |

Recommended combination (incremental, nothing removed):
1. B0 — persist latents (+ `conditioning.png/json` from 6(a)) in the adapter; additive `GenerationOutput.artifacts:
   dict[str, Path] = {}` field.
2. Keep `model.glb` exactly as today (the viewer `frontend/src/components/viewer/ModelScene.tsx:75`, the export
   endpoint and the paused tuner `workers/tuning_worker.py:527` all consume it).
3. Implement decomposition as a separate GPU-scheduled "parts run" (B4) that re-decodes from latents (B1 data at full
   resolution) when available, labels faces, writes `parts/<pt_id>/parts.glb` + `parts.json` (+ optional per-part
   GLBs) using option B2(i) first; per-part atlases (B2(ii)) later behind a flag.
4. Manifest (`parts.json`) minimum: schema version; source job id + seed + engine; coordinate frame (`glTF Y-up,
   unit cube [-0.5,0.5]`, plus the Z-up TRELLIS frame for latents); per part: id, semantic label, kind
   (`physical` | `region`), node name in `parts.glb`, face count/range or region face-index file, bbox, area,
   pivot, `parent` (e.g. eyes → head region), `attached_to` + contact metric, `symmetry_pair` (left/right eye),
   material/texture refs, provenance (2D mask id, method, confidence). Continuous face/body topology stays ONE mesh
   whose regions are stored as face-label arrays (glTF custom attribute `_PART_ID`, or primitives split by region
   in the same node) — not physically split, per the user's requirement.

---------------------------------------------------------------------------------------------------

## 7. Risks to the working pipeline

1. **Seed/parity regression** — `test/engine/test_generation_parity.py` pins call order, args and RNG state vs
   upstream `run()`. Changing `_preprocess` to keep alpha, adding model calls between `generation.py:202` and the
   samplers, or calling upstream methods differently breaks it (and same-seed reproducibility). Mitigate: work before
   the seed / after sampling, private `torch.Generator`, new parity assertions for the RGBA path.
2. **Stage table coupling** — `STAGE_TABLE`/`STAGE_COUNT=9` (`models/job.py:21-34`), percent formula tests
   (`test/backend/test_progress.py:56`, `:79` asserts `stage_count == 9`), contract §7.4, and the frontend hard-coded
   lists (`frontend/src/api/types.ts:34-46 JOB_STAGES`, `frontend/src/lib/stages.ts:10-19 PIPELINE_STAGES`). New
   `JobStage` values need coordinated backend+frontend+contract changes; an unknown stage raises `KeyError` in the
   reporter. A separate parts run (B4) avoids touching the generation stage table.
3. **VRAM peaks** — decode/export at 1536/4096 already handle 10–16 M-face meshes on GPU; running segmentation or
   part splitting between decode and export (B1) stacks on that peak. Prefer re-decoding from latents in a separate
   run, after `_release_after_run`. Never introduce a second CUDA thread.
4. **Topology rebuild** — `remesh=True, band=1, project_back=0` regenerates the surface on a narrow band at the full
   grid; touching shells (eyes in sockets, hair on scalp, clothes on body) may be fused or thickened [A, not
   verified by rendering]. Thin assets (eyelashes, eyebrows) are frequently only texture in TRELLIS outputs [A];
   honest outputs for these may be *regions/texture masks*, not physical meshes — physically separating them may
   require generating new geometry (out of scope; would be "refinement").
5. **Export cost multiplication** — per-part `to_glb` multiplies the dominant 77–330 s stage; single bake + split
   (B2(i)) avoids it.
6. **Dependency breakage** — installing diffusers (latest needs huggingface-hub ≥1.32) or a segmentation package
   could upgrade `huggingface_hub`/`transformers` and break TRELLIS/DINOv3 imports (`environment.py:467-479`).
   Prefer transformers-native segmentation models; pin any new package and run the runtime probe + engine tests.
7. **API compatibility** — `RequestModel` is `extra="forbid"` (`schemas/common.py:18-21`): new request fields must be
   optional with defaults (old clients keep working; a new client against an old server gets 422).
   `DomainModel` is `extra="ignore"` (`models/types.py:27-30`) → old `job.json` files load fine with new optional
   fields. `Job.reference_set_id`/`primary_image_id` are required (`models/job.py:109-110`): prompt-to-3D should
   first materialize the generated image as a normal reference (set + image) so the existing job flow is untouched.
8. **Disk/retention** — parts GLB ≈ another `model.glb` (41–86 MB) per job; persisted decoded meshes would be
   hundreds of MB [A] (V×12 B + F×12 B + L×24 B) — keep to latents (MB-scale) and make heavy artifacts opt-in.
9. **Paused tuner** — it copies `model.glb` and its Blender scripts (`backend/blender_scripts/img3d_amt/face.py`,
   `rig.py`, …) assume the current single-mesh GLB; do not change `model.glb` semantics; tuning stays disabled.
10. **Frame confusion** — latents/decoded mesh are Z-up; GLB is Y-up after `postprocess.py:312-315`. Every manifest and
    label array must declare its frame.
11. **Fake engine** — `test/backend/fakes.py` implements the seam; additive optional seam methods with defaults keep
    it valid; abstract additions would break it (and `UnavailableEngine`).

---------------------------------------------------------------------------------------------------

## 8. Appendix — per-job metadata [M]

| job | res/tex/poly | eff | preprocess | sparse voxels | shape tokens | decoded V / F | out V / F | total s | export s (bake) |
|---|---|---|---|---|---|---|---|---|---|
| job_04535ae38930 | 1536/4096/2M | 1536 | alpha_crop | 1787 | 18140 | 6.20M / 12.97M | 1.70M / 1.90M | 423.5 | 281.6 (274.9) |
| job_046e19f0ca62 | 1536/2048/1M | 1536 | alpha_crop | 1828 | 18556 | 5.90M / 12.04M | 0.87M / 1.00M | 269.2 | 116.8 (114.8) |
| job_2496cdfeecc8 | 1536/4096/2M | 1536 | alpha_crop | 1788 | 18739 | 6.47M / 13.62M | 1.62M / 1.92M | 220.8 | 165.5 (158.2) |
| job_57425383ed74 | 1536/4096/2M | 1536 | alpha_crop | 2579 | 26713 | 8.29M / 16.47M | 1.78M / 1.86M | 202.0 | 116.7 (109.6) |
| job_5b72e3768e6e | 1536/4096/2M | 1536 | alpha_crop | 1650 | 16050 | 4.76M / 9.68M | 1.62M / 1.98M | 166.3 | 105.4 (97.7) |
| job_a68b9057a882 | 1536/4096/2M | 1536 | alpha_crop | 1873 | 19591 | 6.66M / 13.83M | 1.63M / 1.93M | 579.0 | 330.4 (323.4) |
| job_dd0e629ac377 | 1536/4096/2M | 1536 | background_removed | 1621 | 16873 | 5.68M / 12.43M | 1.63M / 1.99M | 133.1 | 76.7 (69.7) |

Local model licenses (from `backend/.model/README.md`, links there): TRELLIS.2-4B MIT
(https://huggingface.co/microsoft/TRELLIS.2-4B), TRELLIS-image-large MIT, BiRefNet MIT
(https://huggingface.co/ZhengPeng7/BiRefNet), DINOv3 License (gated,
https://ai.meta.com/resources/models-and-libraries/dinov3-license/), RMBG-2.0 CC BY-NC 4.0 (optional, not present).
Vendored TRELLIS.2 at upstream commit `75fbf01` (build-only patches, `backend/third_party/PATCHES.md`).
