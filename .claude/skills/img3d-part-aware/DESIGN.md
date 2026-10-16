# Img3D part-aware milestone: AUTHORITATIVE backend design

Date: 2026-10-16. Status: **authoritative** for the backend half of the milestone "backend part-aware architecture +
four UI modes" (the UI half is `UI_DESIGN.md`). It supersedes `design/backend_a_incremental.md`,
`design/backend_b_asset_model.md` and `design/backend_c_pipeline_graph.md`. Those files remain as background only.

Precedence (highest first):
1. The user's messages.
2. `DECISIONS.md`: model choices, physical-separation policy and environment changes, as decided by the user on
   2026-10-16.
3. This document.
4. `.claude/skills/img3d-architecture/api-contract.md`. Slice S0 merges §6 of this document into it.
5. `research/*.md`.

How this document was built:
- **Base:** proposal A (incremental, risk-first). The judges' totals were A 127.5, B 116, C 116.
- **Grafts from B:** the asset model (separate `face_owner`/`face_labels` buffers, vertex maps, bit-exact
  reassembly), the `AssetReader`, the refinement records, a code-level tuning gate, and prompt hygiene.
- **Grafts from C:** locked future-tuning nodes, catalog templates, `legacy_job_node_map`, server-side graph
  validation, a live VRAM check, and a torch-free conditioning crop.
- **Fixes:** every fatal flaw the judges listed is fixed. §13.3 is the record.
- **Revision 2026-10-16:** the three critiques in `design/critic_*.md` were verified against the code and applied
  (blocker: opposite-sheet partner fill, §4.6.5). The open questions are resolved (§12). The "Revision log
  (2026-10-16)" at the end lists every item.

Legend: **[V]** = verified in code or on disk; **[M]** = measured on this machine; **[A]** = assumption that the named
test or smoke run must confirm. Paths are relative to the repo root (`E:/14-OCT-2026/Img3D`). Backend package paths may omit the
`backend/` prefix: `parts/…` means `backend/parts/…`. Test paths always start with `test/`.

Nothing in this document authorizes tuning, refinement, eye or rig correction, reference-aware optimization or
Blender work.

---------------------------------------------------------------------------------------------------
## 0. Goals, non-goals, hard constraints, invariants

### 0.1 Goals
| # | Goal | Where |
|---|---|---|
| G1 | Keep the working image-to-3D generator and its outputs exactly as they are | §0.4 I1–I4, §1.3 |
| G2 | Semantic asset decomposition **before** reconstruction (2D segmentation of the input image, `sg_` runs) and **after** it (lifting labels onto the generated mesh and cutting physical parts, `pa_` runs) | §4.5, §4.6 |
| G3 | Physical parts for eyes, hair, clothes, accessories and detachable objects. Eyebrows and eyelashes are physical part records whose geometry is deferred. Face and body stay one continuous mesh with semantic regions (`DECISIONS.md` §3) | §2 |
| G4 | Prompt-to-3D: Z-Image-Turbo text→image, then the **unchanged** image-to-3D path | §4.4 |
| G5 | Parts keep their relations, reassemble bit-exactly and are readable by a future tuning layer through one read API | §3 |
| G6 | Modular provider seams plus a model registry with honest states and licence metadata. The design works with any subset of approved models | §5 |
| G7 | One backend API for all four UI renderers: discovery, node catalog, server-side graph validation, workflows | §6 |
| G8 | Tuning and refinement stay disabled, behind feature flags **and** a code gate, with zero new tuning algorithms | §8.2 |

### 0.2 Non-goals (this milestone)
The following are out of scope:
- Any tuning, refinement, eye correction, rig correction, reference-aware optimization, or Blender work.
- Amodal completion: the hidden scalp, the body under clothes, eyeballs.
- Per-part regeneration (Tripo-style), per-part re-bake or re-atlas, and eyebrow or eyelash geometry.
- In-app model downloads, sidecar Python environments, and transformers 5.
- UI implementation (`UI_DESIGN.md` covers it).
- Python code that runs inside Blender.

### 0.3 Hard constraints and how they are met
| Constraint | Mechanism |
|---|---|
| Image-to-3D path and `model.glb` byte-for-byte unchanged when part features are off | The generation job is frozen. A hash test pins the protected files (§1.3). Part-awareness lives in separate run resources that only **read** finished jobs. `job.json`, `JobOut`, `CreateJobRequest` and `GenerationSettings` are not edited |
| Pinned `JobStage` list, percent formula, `/system` keys | Not touched. New progress lives on the new resources (§4.3). Discovery is `GET /features` (§6.1) |
| Additive API; request bodies reject unknown fields | Only new endpoints and new request models. Every new field is optional with a default. No existing request model changes |
| One GPU worker thread; models loaded one after another with offload | Every extra GPU step goes through `GenerationWorker.submit_gpu_task` onto the existing single `gpu-worker` executor, FIFO with load, generate and unload (§1.2 S-a, §4.8) |
| No new heavy native dependencies | Only already-installed packages: numpy, scipy, Pillow, opencv-headless, trimesh, nvdiffrast, cumesh, transformers 4.57.6, diffusers 0.39.0, accelerate 1.15.0 (§9) |
| Never upgrade transformers or huggingface-hub | Every install uses `-c backend\constraints.txt` (the file already exists [V]; it is edited in place, never recreated). The registry refuses text encoders written by transformers ≥ 5 |
| Tuning disabled and flagged; no new tuning algorithms | Code gate in `MeshTunerService.create_run`, `/features` flags, a `PartConsumerRegistry` that refuses registrations, and an import-boundary test (§8.2) |
| No fake or mock generation data in the app | A missing capability gives an honest 503 with a reason. The CPU `components` strategy produces real output. Test doubles live only under `test/` |
| Approved models: the six `DECISIONS.md` models (Q1, user decision) | Provider seams plus a registry whose default approved set is exactly those six (`default_approved=True`), overridable with `IMG3D_APPROVED_MODELS`. §5.5 shows what each subset yields |

### 0.4 Invariants (acceptance criteria for every slice)
| # | Invariant | Enforced by |
|---|---|---|
| I1 | The protected files (§1.3) are unchanged, so `model.glb`, `preview.png`, `job.json` and `JobOut` of every image job come from untouched code | `test/backend/test_protected_files.py` (sha256 after CRLF→LF normalization, compared with `test/backend/protected_files.sha256`) |
| I2 | No new code writes inside `jobs/<job>/` except under `jobs/<job>/parts/<pa>/`: `run.json`, `log.txt` on failure, and `asset/`, which is published by one atomic rename. `RunStore` never creates `jobs/<job>/` itself (§4.3) | `test_legacy_invariance.py` hashes `model.glb`, `preview.png` and `job.json` before and after a part analysis |
| I3 | No new `JobStage`. `STAGE_TABLE`, the percent formula, `/system` keys, `limits`, `defaults` and `EngineCapabilities.to_dict()` are unchanged | Protected files plus the existing pinned tests (`test_progress.py:56-67`, `test_health_system.py:37,54,61`, `test/engine/test_engine_unit.py:81`) |
| I4 | `CreateJobRequest`, `GenerationSettings` and `TuningOptions` are unchanged | Protected `schemas/jobs.py`; the tuner schema is not edited |
| I5 | One GPU thread. Every CUDA call of new code runs inside a callable submitted through `submit_gpu_task`, inside `torch_globals_guard()` (§4.9). `ModelProvider.load()` runs on a CPU thread and never touches CUDA | `test_gpu_task_seam.py` (thread-name check, no overlap), `test_run_framework.py`, `test/engine/test_aux_isolation.py` |
| I6 | No new pip package. transformers and huggingface-hub keep their versions | `test_import_boundaries.py`, plus `uv pip check` in the S4 acceptance |
| I7 | `api/`, `services/`, `pipelines/`, `parts/`, `processing/`, `storage/`, `models/`, `schemas/`, `core/` and `workers/` never import torch, diffusers, transformers, nvdiffrast, cumesh or trellis2 | AST scan in `test_import_boundaries.py` |
| I8 | No fake data in the app: unavailable means 503 with a reason | Code review; fakes only in `test/backend/fakes_*.py` |
| I9 | The 182 existing tests (173 backend + 9 aux_models) pass with **no assertion changed**. The only edit to existing test infrastructure is one `os.environ.setdefault` line in `test/conftest.py` (S9; Q3 default, §12) | Each slice runs `backend\python\python.exe -s -m pytest -c test\pytest.ini test\backend test\aux_models -q` |
| I10 | Part-aware code never imports tuner modules (`services.mesh_tuner_service`, `workers.tuning_worker`, `launcher`, `blender_scripts`) | `test_import_boundaries.py` |
| I11 | Every published part layer reassembles bit-exactly to `model.glb`: positions, UVs, normals and the face set, compared at the glTF accessor level | `parts/reassembly.py` runs inside every analysis; `test_parts_geometry.py` |
| I12 | A published asset directory (`jobs/<job>/parts/<pa>/asset/`) is immutable. A new analysis creates a new `pa_` | `parts/publish.py` refuses an existing target; on-demand exports go to `runtime/cache/` |

### 0.5 Architecture
```
 4 UI renderers (Default, Node, Mobile, Tablet)  ── one shared state/API layer (frontend, UI_DESIGN.md)
        │  REST + WS (/api/v1)
        ▼
 Backend API (api/v1/*)  existing routers unchanged  +  features, models, image-generations, segmentations,
        │                                                part-analyses, workflows (+ catalog, validate)
        ▼
 Services (torch-free)   JobService (unchanged) · FeatureService · ModelRegistryService · ImageGenerationService ·
        │                SegmentationService · PartAnalysisService · WorkflowService + WorkflowCompiler
        ▼
 Generation pipeline     job_ : ImageTo3DPipeline → TrellisEngine (FROZEN)
        │                ig_/sg_/pa_ : run executors (pipelines/*) on RunWorker → submit_gpu_task (same gpu-worker)
        ▼
 Part-aware asset system parts/* (torch-free): taxonomy, policy, weld, components, shells, labels, partition, seams,
                         relations, frames, gltf_io, buffers, export, validate, publish, reader, consumers, lineage
        ▼
 Provider seams          inference/providers (registry, catalog: torch-free) → inference/{imagegen,segmentation,parts}/
                         implementation modules (the only places that import torch/diffusers/transformers/nvdiffrast)
```

---------------------------------------------------------------------------------------------------
## 1. Module layout and layering

### 1.1 Package tree
`EDIT` = edits an existing file, `NEW` = new file. The slice ids refer to §11.
```
backend/
  config/settings.py                 EDIT  additive fields (§8.1)                                          S1
  core/container.py                  EDIT  + part_aware field, build_part_aware() call                     S1
  core/lifecycle.py                  EDIT  + part_aware.start()/stop() calls                                S1
  core/feature_errors.py             NEW   FeatureDisabledError, CapabilityUnavailableError, ModelUnavailableError,
                                           InvalidWorkflowError                                             S1
  core/part_aware_container.py       NEW   PartAwareContainer, PartProviderOverrides, build_part_aware()   S1
  main.py                            EDIT  create_app(..., part_providers=None)                             S1
  workers/generation_worker.py       EDIT  + submit_gpu_task, accepting_gpu_tasks (§1.2 S-a)                S1
  workers/run_worker.py              NEW   RunWorker (single consumer of the run queue; GPU/CPU phases)     S1
  api/router.py                      EDIT  + include build_part_aware_router()                             S1
  api/websocket_hub.py               EDIT  _JOB_SCOPED_EVENTS += part_analysis.updated                     S1
  api/part_routes.py                 NEW   build_part_aware_router()                                        S1
  api/part_deps.py                   NEW   DI accessors for the new services                                S1
  api/v1/features.py                 NEW   (S1 empty router; filled by S2)
  api/v1/model_registry.py           NEW   (S1 empty; S2)
  api/v1/image_generations.py        NEW   (S1 empty; S4)
  api/v1/segmentations.py            NEW   (S1 empty; S5)
  api/v1/part_analyses.py            NEW   (S1 empty; S10)
  api/v1/workflows.py                NEW   (S1 empty; S11)
  schemas/events.py                  EDIT  + 5 EventType members                                            S1
  schemas/{runs,features,model_registry,image_generation,segmentation,parts,workflows}.py
                                     NEW   ALL wire models of §6, frozen in S1                              S1
  schemas/references.py              EDIT  ReferenceImageOut.source (optional)                              S4
  models/{runs,image_generation,segmentation,part_analysis,workflow}.py   NEW domain records, frozen       S1
  models/reference.py                EDIT  ImageSource + ReferenceImage.source (omitted from set.json when None) S4
  services/reference_service.py      EDIT  upload_image(..., source=None) keyword                           S4
  services/mesh_tuner_service.py     EDIT  code gate (§8.2)                                                 S9
  storage/run_store.py               NEW   RunStore[R] (generic, tuner-pattern)                             S1
  storage/run_layout.py              NEW   path helpers over RuntimePaths (paths.py stays protected)        S1
  storage/workflow_store.py          NEW   S1 frozen-API stub (list_terminal, delete, active_child_ids);   S1→S11
                                           S11 implements it (S8 codes against the frozen API)
  pipelines/runs/{__init__,executor,reporter,context}.py   NEW run framework                               S1
  pipelines/image_generation.py      NEW   ig_ executor                                                     S4
  pipelines/prompt_templates.py      NEW                                                                    S4
  pipelines/segmentation.py          NEW   sg_ executor + reusable segment_image() used by pa_              S5
  pipelines/part_analysis.py         NEW   pa_ executor                                                     S10
  processing/conditioning.py         NEW   torch-free replica of upstream preprocess crop (§4.5.2)          S5
  processing/imagegen_readiness.py   NEW   3D-readiness check of generated images                           S4
  processing/seg2d/{__init__,fusion,eyes}.py  NEW  numpy/opencv/Pillow only                                S5
  parts/__init__.py, parts/types.py, parts/taxonomy.py, parts/manifest.py   NEW frozen interfaces           S1
  parts/{gltf_io,weld,components,shells,partition,seams,reassembly}.py      NEW geometry core              S3a
  parts/{labels,policy,relations,frames,buffers,export,validate,publish,reader,consumers,lineage}.py  NEW  S3b
  inference/providers/{__init__,base}.py                   NEW torch-free types + ABCs                     S1
  inference/imagegen/{__init__,base}.py, inference/segmentation/{__init__,base}.py,
  inference/parts/{__init__,base}.py                       NEW torch-free role ABCs                        S1
  inference/providers/{catalog,registry,unavailable,vram}.py   NEW torch-free                              S2
  inference/imagegen/zimage.py                             NEW torch/diffusers                             S4
  inference/segmentation/{gdino,sam2,florence2}.py         NEW torch/transformers                          S5
  inference/segmentation/{see_through,sapiens2}.py         NEW torch + vendored code (Q4 approved)         S6
  inference/providers/vendor_import.py                     NEW torch-free private-namespace importer (§5.6) S6
  inference/parts/{raster_lifter,cameras}.py               NEW torch/nvdiffrast/cumesh                     S7
  services/feature_service.py, services/model_registry_service.py      NEW (S1 stub; S2)
  services/image_generation_service.py                                  NEW (S1 stub; S4)
  services/segmentation_service.py                                      NEW (S1 stub; S5)
  services/part_analysis_service.py, services/part_files.py            NEW (S1 stub; S10)
  services/workflow_catalog.py       NEW   static node/port/template tables, legacy_job_node_map           S1
  services/workflow_compiler.py      NEW   graph → WorkflowRequest, GraphIssue[]                            S11
  services/workflow_service.py       NEW   (S1 stub; S11)
  services/run_events.py             NEW   publishers for the five new WS events                            S1
  services/run_retention_service.py  NEW                                                                    S8
  requirements.txt                   EDIT  + diffusers==0.39.0, accelerate==1.15.0 (already installed [V])  S4
  third_party/see-through/, third_party/sapiens2/   pinned git submodules (small modules only; Q4)          S6
```
Rules for this tree:
- S1 creates every router empty, every service class as a stub with a **frozen constructor**, and every wire and
  domain model (`schemas/*`, `models/*`) from this document.
- After S1, each file has exactly one owner (§11).
- `core/part_aware_container.py` is written once in S1 and is not edited afterwards.

### 1.2 The seams into working code (exact edits)

**S-a GPU seam (`workers/generation_worker.py`, about 15 lines after `_submit` at :123-127 [V]).**
```python
T = TypeVar("T")   # module top

    @property
    def accepting_gpu_tasks(self) -> bool:
        """False once stop() has begun (no new auxiliary GPU work may be queued)."""
        return not self._stopping

    def submit_gpu_task(self, fn: Callable[..., T], /, *args: Any) -> asyncio.Future[T]:
        """Run a blocking callable on the single gpu-worker thread, FIFO with model load/generation/unload.
        The callable must honour its CancellationToken and leave no weights on the GPU when it returns.
        Raises EngineUnavailableError("The server is shutting down") while stopping."""
        if self._stopping:
            raise EngineUnavailableError("The server is shutting down")
        return asyncio.wrap_future(self._submit(fn, *args))
```
- `is_busy` already counts every in-flight future [V :101-103].
- `running_job_id` [V :96-98], `_wait_for_engine` and `unload_engine` are unchanged. `RunWorker` reads
  `running_job_id` for its "jobs first" gate (§4.3).
- `_process` marks a job `running/preprocessing` ("Starting generation") **before** it queues `_execute_pipeline`
  on the FIFO executor [V :316-338]. A job that arrives while an auxiliary GPU phase runs therefore shows 0 % until
  that phase returns. §4.3 (jobs-first gate) and §4.4.4 (Z-Image load moved off the GPU thread) bound that wait to
  one short phase.
- S1 adds this file to the protected list right after the edit.

**S-b Container and lifecycle.**
- `core/container.py`:
  - Add the last dataclass field `part_aware: PartAwareContainer | None = None`.
  - `build_container(..., part_providers: PartProviderOverrides | None = None)` calls
    `build_part_aware(settings, paths=, bus=, engine=, gpu=worker, job_service=, job_store=, reference_service=,
    result_service=, overrides=part_providers)` before `return`.
  - `build_part_aware` registers `job_service.add_delete_hook(part_analysis_service.on_job_deleting)`
    (`job_service.py:60` [V]).
- `core/lifecycle.py`:
  - `startup`: after `container.tuning_worker.start()` (:44 [V]), add
    `if container.part_aware: await container.part_aware.start()`. This loads the stores, recovers interrupted runs
    and workflows, and starts the `RunWorker`, the workflow subscription and sweep, and the retention task. When
    `part_aware_enabled=false`, only the stores are loaded, so history stays readable.
  - `shutdown`: **before** `container.worker.stop(...)` (:84 [V]), add
    `await container.part_aware.stop(timeout=settings.shutdown_timeout_s)`. This cancels the active run, waits for its
    GPU task, fails queued runs as `interrupted`, and stops the workflow sweep. It must run before the generation
    worker shuts its executor down.
  - **Blender detection at startup (:60 [V]) is not changed.** Changing it was B's fatal flaw.

**S-c API wiring.**
- `api/router.py` includes `build_part_aware_router()`.
- `schemas/events.py` adds `FEATURES_UPDATED`, `IMAGE_GENERATION_UPDATED`, `SEGMENTATION_UPDATED`,
  `PART_ANALYSIS_UPDATED` and `WORKFLOW_UPDATED`.
- `api/websocket_hub.py:229` [V] adds `str(EventType.PART_ANALYSIS_UPDATED)` to `_JOB_SCOPED_EVENTS`.
- `main.create_app(..., part_providers=None)` is passed through to `build_container`. Tests inject doubles this way.

**S-d Settings.** New optional fields only (§8.1). `safe_summary()` is unchanged; the part-aware container logs its
own `part_aware_summary()`.

**S-e Tuning code gate** (S9): `MeshTunerService.create_run`. See §8.2.

**S-f Reference provenance** (S4):
- `models/reference.py` gains:
  ```python
  class ImageSource(DomainModel):
      kind: Literal["t2i"]; image_generation_id: str; candidate_index: int; model_id: str
      model_revision: str | None; license_id: str; seed: int; final_prompt_sha256: str
  ```
- It also adds `ReferenceImage.source: ImageSource | None = None`, with a
  `@model_serializer(mode="wrap")` that omits the key when it is `None`. The `set.json` of uploaded images therefore
  stays key-identical.
- `schemas/references.py`: `ReferenceImageOut.source: ImageSourceOut | None = None`. The response gains one key,
  `null` for uploads.
- `services/reference_service.py`: `upload_image(..., source: ImageSource | None = None)` is a keyword-only addition.

### 1.3 Protected files (sha256-pinned in S0; `workers/generation_worker.py` is added by S1 after S-a)
```
backend/pipelines/image_to_3d.py   backend/pipelines/progress.py      backend/pipelines/base.py
backend/pipelines/reference_strategy.py                                backend/inference/base.py
backend/inference/registry.py      backend/inference/unavailable.py   backend/inference/device.py
backend/inference/trellis/{assets,compat,engine,environment,export,generation,imaging,loader}.py
backend/models/job.py              backend/models/enums.py            backend/schemas/jobs.py
backend/schemas/system.py          backend/services/job_service.py    backend/services/system_service.py
backend/services/generation_settings.py                                backend/services/result_service.py
backend/storage/job_store.py       backend/storage/paths.py           backend/storage/reference_store.py
backend/processing/image_ops.py    backend/processing/image_validation.py  backend/processing/mesh_stats.py
backend/workers/queue.py           backend/workers/generation_worker.py (after S1)
```
`models/reference.py`, `schemas/references.py` and `services/reference_service.py` are **not** protected (S-f edits
them). They are guarded by behaviour tests instead (§10.2 `test_reference_provenance.py`).

Changing a protected file requires the user's approval and a hash update in the same commit. No protected-file change
is planned in this milestone (SD1 is deferred, Q5 → D1).

### 1.4 Layering rules
1. `api → services → {pipelines, parts, processing, storage, models, schemas, core}`.
   - Services may also import the torch-free `inference/*/base.py`, `inference/providers/{base,catalog,registry,
     unavailable,vram}.py`, and `inference/device.py`.
2. Only these modules import torch, diffusers, transformers, nvdiffrast, cumesh or vendored model code:
   `inference/imagegen/zimage.py`, `inference/segmentation/{gdino,sam2,florence2,see_through,sapiens2}.py`,
   `inference/parts/{raster_lifter,cameras}.py`, and the existing `inference/trellis/**`.
   - `inference/providers/registry.py` imports them lazily (`importlib.import_module` inside a function). It first
     calls `inference.trellis.environment.configure_hf_environment(settings.hf_home)` (idempotent, torch-free;
     §5.3), so no provider can import `huggingface_hub`/`transformers` before the HF cache is configured. On **any**
     `Exception` during the import (a broken CUDA DLL raises `OSError`, `inference/device.py:42-43` [V]) it
     substitutes an `UnavailableProvider` from `inference/providers/unavailable.py` with `reason_code="error"` and
     `reason = f"{type(exc).__name__}: {exc}"`.
   - Vendored model code (See-through, Sapiens2) is imported **only** through
     `inference/providers/vendor_import.py` (§5.6). Nothing inserts a vendored root into `sys.path`.
3. `parts/` is a torch-free library (numpy, scipy, Pillow, json, struct). It never imports `services`, `api` or
   `workers`.
4. The tuner modules (I10) and the protected files are never imported **for modification**. Reading public
   functions is allowed: `classify_exception`, `validate_against_capabilities`, `compute_glb_stats`, `new_id`,
   `write_json_atomic`, `processing.image_ops.normalize_to_png` / `make_thumbnail_webp`, and
   `inference.trellis.environment.configure_hf_environment` (all torch-free [V]).
5. `test/backend/test_import_boundaries.py` enforces rules 1–4 with an AST scan.
   - It allows `import torch` only inside function bodies of the rule-2 modules and inside
     `inference/providers/vram.py`.
   - `vram.py` calls the existing `inference.device.import_torch()` / `query_memory()` [V :30, :98].

---------------------------------------------------------------------------------------------------
## 2. Part taxonomy and the physical-versus-region policy

### 2.1 What "physical" can honestly mean on TRELLIS.2 output
- TRELLIS.2 has no notion of parts. Training merges all objects into one dual grid. One dual vertex per voxel means
  that touching surfaces fuse: eyeball and eyelid, scalp and hair, skin and clothes (`trellis2_internals.md` §2.1 [V]).
- Export remeshes into a **double-walled shell** with an outer sheet and an inner sheet (`remesh=True, band=1`).
  `model.glb` has about 2 M faces, UV-seam duplicates on 39–50 % of its vertices, and 2.2k–33k non-manifold edges
  [V/M].
- Therefore, in this milestone:
  - A **physical** part is a set of `model.glb` faces moved into its own mesh node. It is an **open surface patch**
    (outer and inner sheet), keeps the original UVs and the one shared atlas, and records its seam loops. The cut
    leaves an **opening** in the host, which is recorded and never filled. Hidden geometry (scalp, body under
    clothes, eyeball) **does not exist** and is recorded as `occluded_geometry_missing` / `needs: occluded_geometry`.
  - A **physical_deferred** part (eyebrows, eyelashes; also a physical class below `min_part_faces`) has a real part
    record: a 2D mask, a 3D surface region on its host (face labels), guides (`lid_curve`) and
    `geometry.status: "texture_only"`. It has **no** mesh node. Its geometry is the job of the future tuning layer,
    which needs approval.
  - A **region** is a semantic label on faces of a mesh-owning part (base or physical). Topology is not cut.
  - The **base** is the continuous remainder: body or object. It owns every face that is not in a physical part.

### 2.2 Groups (what the user toggles; `separate` in requests)
| Group | Classes | Kind when selected | Kind when not selected | Note shown in the UI (`/features`) |
|---|---|---|---|---|
| `eyes` | `eye.left`, `eye.right` | physical | region | "Eye area is cut as an open surface; the hidden eyeball is not generated." |
| `eyebrows` | `eyebrow.left`, `eyebrow.right` | physical_deferred | region | "Eyebrows are painted on; geometry deferred (mask + surface region now)." |
| `eyelashes` | `eyelash.left`, `eyelash.right` | physical_deferred | region | "Eyelash geometry deferred; mask + lid curve only." |
| `hair` | `hair`, `hair.facial` | physical | region | "The scalp under the hair is not generated (opening recorded)." |
| `clothes` | `clothing.top/.outer/.bottom/.dress/.legwear/.handwear/.footwear/.neckwear` | physical | region | "The body under clothes is not generated (opening recorded)." |
| `accessories` | `accessory.headwear/.eyewear/.earwear/.jewelry/.belt/.bag`, `appendage.tail/.wings/.animal_ears` | physical | region | – |
| `detachable` | `object.held`, `object.detached` | physical | region | – |
| `face` (region group) | `body.head.face`, `.ear.*`, `.nose`, `.mouth`, `.eyelid.*` | always region | – | – |
| `body` (region group) | `body`, `body.head`, `body.neck`, `body.torso`, `body.arm.*`, `body.hand.*`, `body.leg.*`, `body.foot.*`, `body.skin_other` | always region (`body` is the base) | – | – |
| `other` | `unknown` | region | – | unassigned foreground |

The default `separate` is all seven separable groups (the user's list).

### 2.3 Classes v1 (`parts/taxonomy.py`, data only, frozen in S1; `taxonomy_version = 1`)
Values are stable and are written to `face_labels.u16`, to `_FEATURE_ID_0`, and to the 2D `labels.png` of
segmentations.

| value | id | group | default kind | attach_to candidates (first match wins at runtime) | symmetric_with | sided |
|---|---|---|---|---|---|---|
| 0 | `unknown` | other | region | body | – | no |
| 1 | `body` | body | base | – | – | no |
| 2 | `body.head` | body | region | body | – | no |
| 3 | `body.head.face` | face | region | body.head | – | no |
| 4 / 5 | `body.head.ear.left` / `.right` | face | region | body.head | each other | yes |
| 6 | `body.head.nose` | face | region | body.head.face | – | no |
| 7 | `body.head.mouth` | face | region | body.head.face | – | no |
| 8 | `body.neck` | body | region | body | – | no |
| 9 | `body.torso` | body | region | body | – | no |
| 10 / 11 | `body.arm.left` / `.right` | body | region | body.torso | each other | yes |
| 12 / 13 | `body.hand.left` / `.right` | body | region | body.arm.\<side\> | each other | yes |
| 14 / 15 | `body.leg.left` / `.right` | body | region | body.torso | each other | yes |
| 16 / 17 | `body.foot.left` / `.right` | body | region | body.leg.\<side\> | each other | yes |
| 18 | `body.skin_other` | body | region | body | – | no |
| 20 / 21 | `eye.left` / `eye.right` | eyes | **physical** | body.head.eyelid.\<side\>, body.head.face | each other | yes |
| 22 / 23 | `eyebrow.left` / `.right` | eyebrows | **physical_deferred** | body.head.face | each other | yes |
| 24 / 25 | `eyelash.left` / `.right` | eyelashes | **physical_deferred** | body.head.eyelid.\<side\>, eye.\<side\> | each other | yes |
| 26 / 27 | `body.head.eyelid.left` / `.right` | face | region (2D ring derived around the eye mask, §4.5.4) | body.head.face | each other | yes |
| 30 | `hair` | hair | physical | body.head | – | no |
| 31 | `hair.facial` | hair | physical | body.head.face | – | no |
| 40 | `clothing.top` | clothes | physical | body.torso | – | no |
| 41 | `clothing.outer` | clothes | physical | clothing.top, body.torso | – | no |
| 42 | `clothing.bottom` | clothes | physical | body.torso, body.leg.* | – | no |
| 43 | `clothing.dress` | clothes | physical | body.torso | – | no |
| 44 | `clothing.legwear` | clothes | physical | body.leg.* | – | no |
| 45 | `clothing.handwear` | clothes | physical | body.hand.* | – | no |
| 46 | `clothing.footwear` | clothes | physical | body.foot.* | – | no |
| 47 | `clothing.neckwear` | clothes | physical | body.neck | – | no |
| 50 | `accessory.headwear` | accessories | physical | hair, body.head | – | no |
| 51 | `accessory.eyewear` | accessories | physical | body.head.face | – | no |
| 52 | `accessory.earwear` | accessories | physical | body.head.ear.* | – | no |
| 53 | `accessory.jewelry` | accessories | physical | body.neck, body.hand.*, body.arm.* | – | no |
| 54 | `accessory.belt` | accessories | physical | body.torso | – | no |
| 55 | `accessory.bag` | accessories | physical | body.torso | – | no |
| 56 | `appendage.tail` | accessories | physical | body.torso | – | no |
| 57 | `appendage.wings` | accessories | physical | body.torso | – | no |
| 58 | `appendage.animal_ears` | accessories | physical | hair, body.head | – | no |
| 60 | `object.held` | detachable | physical | body.hand.* (relation `held_by`) | – | no |
| 61 | `object.detached` | detachable | physical | – (scene-root sibling) | – | no |

- The data structure is `PartClass(value, id, group, label, default_kind, attach_to: tuple[str, ...],
  symmetric_with, sided, instance_split: bool, aliases: tuple[str, ...], z_priority: int)`.
- `instance_split` is true for `accessory.*`, `appendage.*`, `object.*`, `clothing.handwear` and `clothing.footwear`.
  For these, each welded component of the class with at least the effective `min_part_faces` (§4.6.2) faces becomes
  its own physical
  instance. For every other class (hair, tops, dresses …) all of the class's faces form one part, with
  `geometry.components` = the count. Smaller components of a split class join its largest instance.
- `aliases` holds the open-vocabulary phrases of `segmentation_models_research.md` §7.2. Examples:
  - `hair`: "hair, bangs, ponytail, braid, twin tails, ahoge".
  - `object.held`: "sword, gun, staff, wand, spear, shield, bow, umbrella, book, phone, cup".
- `z_priority` follows the fusion order of research §7.2: object 90, accessory 80, hair 70, eyewear 75, eyelash 60,
  eyebrow 55, eye 50, clothing.outer 45, clothing 40, appendage 35, body regions 10.
- API: `TAXONOMY: Mapping[str, PartClass]`, `BY_VALUE: Mapping[int, PartClass]`, `classes_for_groups(groups)`,
  `match_terms(text) -> list[str]` (whole-word, case-insensitive alias matching; no NLP model) and `to_out()`.
- Part ids:
  - Rule: `^[a-z][a-z0-9_]*(\.[a-z0-9_]+){0,5}$`, at most 80 characters, unique within a manifest.
  - The first instance of a class uses the class id. Further physical instances append `_2`, `_3` and so on to the
    last segment (`accessory.jewelry_2`). Geometry-only shells are `component_001`, `component_002` and so on.
  - The first segment must not be a Windows reserved device name (`con, prn, aux, nul, com1-9, lpt1-9`). Ids are
    used directly as file names.

### 2.4 Policy (`parts/policy.py`, S3b)
```python
Kind = Literal["base", "physical", "physical_deferred", "region"]

def decide_kind(cls: PartClass, separate: frozenset[str], faces: int, min_part_faces: int) -> tuple[Kind, str | None]:
    # min_part_faces is the EFFECTIVE value: request value, or max(50, round(1e-4·F)) when null (§4.6.2)
    if cls.id == "body":                       return "base", None
    if cls.group in {"body", "face", "other"}: return "region", None
    if cls.group not in separate:              return "region", None
    if cls.default_kind == "physical_deferred":return "physical_deferred", None
    if faces < max(min_part_faces, 1):         return "physical_deferred", "too_small_for_geometry"  # never silently dropped
    return "physical", None
```
Further rules, applied in this order:
1. **Kind comes from the policy, not from the segmenter.** Segmenters only label.
2. **Separate shells** (`split_shells=true`, the default). A welded connected component that is neither the base
   component nor an inner sheet (§4.6.5), and that holds less than 25 % of the faces, is handled as follows:
   - If its majority lifted label is in a selected separable group, or is `unknown`/unlabelled, it becomes a
     physical part. Its class is the majority label (`unknown` → id `component_###`). **No cut** is needed, so it
     gets no seam.
   - If its majority label is a body or face region, it stays on the base. An example is a separate leg piece
     (observed in job `dd0e…` [M]).
   - **Floaters.** An `unknown`/unlabelled shell with fewer than `floater_max_faces = max(min_part_faces,
     round(0.0005·F))` faces gets **no part record**. It stays on the base (owner 0, label 0) and is listed in
     `parts[body].geometry.floaters = [{component, faces, centroid}]`, plus one `floater_candidates` warning per
     analysis. Nothing is ever deleted. (Measured with the k=64 rule: ALICE has 45 separate non-inner shells, 20 under
     200 faces; the anime fixture 82, 46 under 200 [M critic].) The tag `floater_candidate` stays reserved in the
     schema but is no longer produced.
   - Labelled shells of a selected group follow `decide_kind` whatever their size (an earring shell is a real part).
3. **Part budget.** At most `max_parts` (default 64) physical parts. The smallest extra parts merge into the base as
   **`region` records that keep their class value** (an overflow `component_###` keeps value 0 and its own record),
   with warning `max_parts_exceeded`. `face_part` (§3.3) keeps their faces distinct.
4. **Eyes** are cut as the lifted `eye.*` faces only. Eyelid rings (26/27) stay base regions, so a future eye
   replacement has margin (Q7, assistant default, §12).
5. **Openings are recorded, never filled.** No hole filling, remeshing, smoothing or re-baking happens; that would
   be refinement.

### 2.5 Honest outcome per asset (what the user gets in this milestone)
| Asset | Output now | Missing (recorded as `refinement.needs`) |
|---|---|---|
| Eyes | Physical open patch per eye. Seams to the eyelid or face. Shared atlas | `occluded_geometry` (no eyeball), `closure` |
| Eyebrows | `physical_deferred`: 2D mask + 3D region (values 22/23) on the base | `geometry` |
| Eyelashes | `physical_deferred`: heuristic 2D mask + `lid_curve` guide + 3D region (24/25) | `geometry` |
| Hair | Physical open shell (outer + inner sheet). Seam loops to the head. Host opening recorded | `occluded_geometry` on the base (scalp), `closure` on the hair |
| Clothes | Physical per class/instance. Host openings recorded | `occluded_geometry` (body under clothes), `closure` |
| Accessories | Physical. Separate shells (earrings, glasses) are free cuts without seams | `closure` only if cut |
| Detachable objects | Physical. `held_by` relation to the nearest hand region | `closure` if cut from the hand |
| Face / body | Regions on the base (`_FEATURE_ID_0` + `face_labels`) | – |
| No segmentation model usable | `components` strategy: base + `component_###` shells (honest, unnamed) | everything semantic |

---------------------------------------------------------------------------------------------------
## 3. Part asset data model and manifest `img3d.parts/1`

### 3.1 Concepts
| Concept | Definition |
|---|---|
| Base mesh | `jobs/<job>/model.glb` exactly as TRELLIS export wrote it: 1 mesh, 1 triangle primitive, 1 material, 2 images, glTF Y-up, unit AABB. It is never modified |
| Base face space | Faces of the base primitive in index-accessor order, `f ∈ [0, F)`. Every per-face buffer uses it |
| Base vertex space | Vertices of the base primitive in accessor order, including the UV-seam duplicates |
| Welded vertex space | Base vertices grouped by bit-identical position: `weld_map[v] = w`. Used for adjacency, components and seams; never used to reorder output |
| Mesh node | A part that owns faces: the base (mesh index 0) or a physical part (1..n). `face_owner[f]` = mesh index |
| Part | A semantic unit with an id (§2.3), a taxonomy class and a kind (`base`, `physical`, `physical_deferred`, `region`) |
| Region | The faces of one class on a mesh node. `face_labels[f]` = taxonomy value of the finest class of face f |
| Seam | The ordered boundary loops created by cutting a physical part out of another owner |
| Relation | A typed, directed edge between two parts, or between the asset and a missing class |
| Analysis | One `pa_` run. It publishes one immutable part layer for one `model.glb`, identified by sha256 |

### 3.2 Asset invariants (tested in `test_parts_geometry.py` / `test_parts_export.py`)
| # | Invariant |
|---|---|
| A1 | `face_owner` is defined for every face. The mesh-node face counts sum to `F` |
| A2 | `face_labels` (semantics) is independent of `face_owner` (ownership). Regions and `physical_deferred` parts never change `face_owner` |
| A3 | The faces of mesh node k are `np.flatnonzero(face_owner == k)`, in ascending base order. Its vertices are `vmap_<id>` (§3.4) |
| A4 | Lossless reassembly. For every node, the written `POSITION`, `NORMAL` and `TEXCOORD_0` accessors equal the base accessors gathered by `vmap` (`np.array_equal` on raw float32), and `vmap[local_faces] == base.faces[node_faces]` row for row |
| A5 | One atlas. Every node references material 0. `model_parts.glb` has exactly the base number of materials, textures, images and samplers, and the image bytes are copied verbatim (no re-encode) |
| A6 | Explicit frames. Every coordinate is in the `asset` frame unless the record names another frame |
| A7 | Honest provenance. Every part lists `provenance.sources[]`. Unlabelled foreground is `unknown`. Nothing is invented or deleted |
| A8 | Refinement is locked. While `part_refinement_enabled=false`, every part has `refinement.status="untouched"` and `refinement.locked=true` |
| A9 | Forward compatibility. Readers ignore unknown fields. `schema` names the major version. A breaking change needs `img3d.parts/2` plus a reader-side migration |

### 3.3 Binary buffers (`parts/buffers.py`, S3b; files in `<analysis>/buffers/`)
- The buffers are raw little-endian files with no header, described in `manifest.buffers[]` (in the spirit of glTF
  accessors).
- A consumer reads them with `np.fromfile(path, dtype)` after checking `byte_length` and `sha256`.

| Buffer id / file | dtype | count | Indexes | Meaning |
|---|---|---|---|---|
| `face_labels` / `face_labels.u16` | uint16 | F | base faces | taxonomy value of the finest class (0 = unknown) |
| `face_owner` / `face_owner.u16` | uint16 | F | base faces | mesh index (0 = base) |
| `face_confidence` / `face_confidence.u8` | uint8 | F | base faces | label confidence × 255 (255 for geometry-only certainty) |
| `face_component` / `face_component.u32` | uint32 | F | base faces | welded connected-component id (0 = largest by area) |
| `face_part` / `face_part.u16` | uint16 | F | base faces | index into `manifest.parts[]` of the **finest** record covering the face (region or deferred record over its mesh-owning record); `0xFFFF` = no record (floaters, unlabelled base faces). Disambiguates records that share a class value on one host (`component_###`, deferred instances, overflow regions) |
| `face_flags` / `face_flags.u8` | uint8 | F | base faces | bit 0 = label taken from the opposite-sheet partner (§4.6.5 step 5.2); bit 1 = label from graph propagation (step 5.3); bit 2 = label from component fill (step 5.6); bits 3–7 = 0 (reserved) |
| `weld_map` / `weld_map.u32` | uint32 | V | base vertices | welded vertex id |
| `vmap_<part_id>` / `vmap_<part_id>.u32` | uint32 | V_k | node vertices | base vertex id of each vertex of node k (§3.4) |
| `seam_loops` / `seam_loops.u32` | uint32 | S | welded vertices | every seam loop concatenated; each loop is a slice `{start, count}` |

### 3.4 Mesh nodes and vertex maps (`parts/partition.py`, S3a)
```python
def build_mesh_nodes(base: BaseMesh, face_owner: np.ndarray, face_labels: np.ndarray) -> list[MeshNode]:
    """For owner k:
      faces_k  = flatnonzero(face_owner == k)                      # ascending base order
      U        = unique(base.faces[faces_k])                        # ascending base vertex ids
      local    = searchsorted(U, base.faces[faces_k])
      Region-border duplicates, only where incident faces of the node carry different labels:
        for each base vertex v of U, take the labels L_v of its incident node faces (ascending);
        min(L_v) keeps the original local vertex, and every other label l gets ONE appended duplicate of v,
        appended in (v, l) order; corners of faces with label l use that duplicate.
      vmap_k   = concat(U, dup_base_ids)                            # the first len(U) entries are ascending
      positions/normals/uv = base arrays gathered by vmap_k; feature_ids (uint16 per vertex) = the label of
      every face using that vertex (unique by construction).
    Nothing is welded, recomputed or reordered."""
```
- `MeshNode(index, part_id, faces_base: u32[n], vmap: u32[m], unique_count: int, local_faces: u32[n,3],
  feature_ids: u16[m])`.
- Duplicating vertices at region borders makes `_FEATURE_ID_0` exact per face in the GLB. A4 stays exact because
  each duplicate gathers the same base vertex.

### 3.5 Seams (`parts/seams.py`, S3a)
- **Seam edge.** A seam edge between physical part `a` and owner `b` (b ≠ a) is a welded edge with at least one
  incident face owned by `a` and at least one owned by `b`. Edges that are already open on the base are `base_open`.
  They are counted in `geometry.open_boundary_loops` and are not seams.
- **Loops.**
  - The edges of each pair `(a, b)` are chained into loops by walking welded vertices.
  - A vertex of degree ≠ 2 (a branch point, typical at non-manifold junctions) ends a chain, and that chain is
    recorded with `closed=false`.
  - The outer and inner sheets of a double-walled cut both contribute loops to the **same** seam record.
- **Pair convention.** `a` is the physical part. When both owners are physical, `a` is the one with the higher mesh
  index. `host_opening = (b is the base)`.
- **Record.** `{id: "seam_<n>", a, b, host_opening, boundary_edges, length, loops: [{start, count, closed, length}]}`.
- **Constraint for future consumers.** A consumer that closes, stitches or remeshes a part must keep the loop vertex
  positions fixed whenever `refinement.constraints.seam_lock` is true. That keeps reassembly crack-free.

### 3.6 Relations (`parts/relations.py`, S3b)
| type | a → b | Derived from | `metrics` | `source` | confidence |
|---|---|---|---|---|---|
| `attached_to` | physical part → the first `attach_to` candidate that is present (part or region) | the longest shared seam (`via` = seam id); for seamless shells, the nearest candidate region within `contact_eps = 2/effective_resolution` | `contact: "seam"\|"proximity"`, `shared_length`, `distance` | geometry | min(label confidences) |
| `region_of` | region / physical_deferred → the mesh node that owns most of its faces | `face_owner` majority | `faces` | geometry | 1.0 |
| `adjacent_to` | region ↔ region on the same node (stored once, a < b) | welded edges between faces with different labels | `border_length`, `border_edges` | geometry | 1.0 |
| `symmetric_with` | `*.left` ↔ `*.right` | both sides present | `plane: "x=0"`, `centroid_mirror_error`, `area_ratio` | geometry | 1 − clamp(error/0.05) |
| `layered_over` | `clothing.*` / `hair` → the body regions it overlaps in the input view | 2D mask overlap of the covering class over body classes | `overlap_px` | taxonomy_default | 0.5 |
| `occludes` | a is in front of b in the input view | depth comparison along shared 2D borders (lifter depth render) | `view: "input"`, `border_px`, `mean_depth_gap` | geometry | from the border length |
| `held_by` | `object.held*` → the nearest `body.hand.*` region | proximity | `distance` | geometry | geometric |
| `expected_missing` | `a = "asset"` → `b` = a class id (not a part) | prompt nouns or `extra_terms` that match a class that was not found | – | prompt \| extra_terms | 1.0 |

Record: `{id: "rel_<n>", type, a, b, via: seam id | null, metrics: {}, source, confidence}`.

### 3.7 Frames, views, placement (`parts/frames.py`, S3b)
- `frames.asset` = `{convention: "gltf", up: "+Y", front: "+Z", units: "normalized", aabb: [[-.5,-.5,-.5],[.5,.5,.5]]}`.
- `frames.trellis` = `{convention: "blender", up: "+Z", front: "-Y", to_asset: [1,0,0,0, 0,0,1,0, 0,-1,0,0, 0,0,0,1]}`.
  - The matrix is row-major with column vectors: x' = x, y' = z, z' = −y (`trellis2_internals.md` §3.1 [V]).
  - Only future latent or voxel providers use it (deferred D1/D8).
- `frames.conditioning` = `{file, width, height, source_image_id, source_size, scale, crop_box, alpha_threshold: 0.8,
  mode: alpha_crop|background_removed|untouched, method: recompute|persisted|template_match|identity, verified,
  residual}` (§4.5.2).
- `views[]`: the cameras used for lifting.
  `{"id": "input", "image_frame": "conditioning", "camera": {"model": "perspective", "yaw_deg", "pitch_deg",
  "fov_deg", "distance", "world_to_camera": [16 numbers, row-major], "intrinsics": {"fx","fy","cx","cy",
  "normalized": true}, "convention": "opencv"}, "fit": {"method": "silhouette_grid_refine", "iou", "accepted",
  "candidates_tested"}, "weight": 1.0}`.
  - Yaw and pitch are in the asset frame. The camera sits at `d·(sin yaw·cos pitch, sin pitch, cos yaw·cos pitch)`
    and looks at the origin, so yaw 0 is the front view.
  - Extra views have `id: "view_<i>"` and `weight: 0.5`.
- Per-part `frame = {pivot, axes, method}`.
  - The pivot is the centroid of the seam loops for cut parts (`seam_centroid`), the bbox centre for seamless shells
    (`bbox_center`), and the area-weighted centroid for regions and deferred parts (`area_centroid`).
  - The axes are the PCA of the part's welded vertices, made right-handed, each sign-stabilised towards the asset
    axes. With fewer than 3 vertices the axes are the identity.
- Per-part `placement = {transform: identity 4×4, method: "identity", error: null}`. Part vertices stay in the asset
  frame. Future regenerated parts will use `method: "registered"` with an alignment error.
- `side` is character-relative (`left|right|center|null`).
  - With an accepted front-ish fit, the character's left is image-right, which is +X.
  - With a rejected fit, `side` comes from the sign of the centroid x, with `provenance.sources[].method =
    "assumed_front"` and confidence 0.5.
  - `image_side` keeps the raw image-space side.

### 3.8 Provenance and lineage
- **`generator`:** `{app_version, engine: "trellis2", model: "TRELLIS.2-4B", pipeline_type, seed, resolution,
  effective_resolution, texture_size, polygon_target}`, copied from `JobResultData` and its metadata [V fields in
  `job.json`].
- **`source`:** `{model_file: "model.glb", model_sha256, model_bytes, vertices, faces, materials, images,
  face_order: "gltf_indices", vertex_order: "gltf_accessor", primary_image_id, origin}`.
  - `origin` is `{kind: "image"}`, or `{kind: "prompt", image_generation_id, model_id, model_revision, license_id,
    seed, final_prompt_sha256}`.
  - It is taken from the primary `ReferenceImage.source` while the set still exists.
- **`provenance.steps[]`:** `{id, models: [{id, revision, license_id}], duration_s}`.
- **`provenance.licences[]`:** every licence involved:
  - `mit` (TRELLIS.2, BiRefNet);
  - `dinov3-license`;
  - `nvidia-source-code-nc` (nvdiffrast, already required by export);
  - the T2I and segmentation licences actually used.
- **Per part:** `provenance = {sources: [{method: lifted|components|heuristic2d|taxonomy_default|prompt|
  assumed_front, provider, model, score}], votes}`, plus `confidence` and `expected` (whether a prompt noun asked
  for it).
- **Lineage:** when an earlier completed analysis of the same job has the same `source.model_sha256`, each new part
  gets `lineage.derived_from = [{part_id, analysis_id, face_iou}]` for the earlier parts with face-set IoU ≥ 0.5.
  - At most 3 entries are kept, highest IoU first.
  - The comparison is possible because both analyses share the base face space.
  - Every analysis recomputes its part ids.

### 3.9 Refinement state (description only; nothing consumes it in this milestone)
```json
"refinement": {"status": "untouched", "locked": true, "lock_reason": "part_refinement_disabled", "eligible": true,
               "needs": ["closure"],
               "constraints": {"seam_lock": true, "keep_uv_atlas": true, "keep_frame": true},
               "history": []}
```
- **`status` values reserved by the schema:** `untouched` (the only value produced now), `queued`, `running`,
  `refined`, `failed`, `rejected`.
- **`needs` lists facts, not operations:**
  - `occluded_geometry`: the base when a physical cut opened it, and eyes;
  - `geometry`: `physical_deferred` parts;
  - `closure`: a physical part with open boundary loops;
  - `low_confidence`: confidence < 0.5.
- **`eligible`** is false for `unknown` / `component_###` records.
- **Manifest level:** `refinement = {"enabled": false, "policy": "locked_until_approved", "consumers": []}`.

### 3.10 Manifest top-level fields (`parts/manifest.py`, pydantic, frozen in S1)
Field names follow the frontend parser (`design/frontend_b_command_engine.md` §4.2): `id`, `category`, `group`,
`kind`, `node_name`, `region`, `files`, `refinement`.

| Field | Type | Notes |
|---|---|---|
| `schema` | `"img3d.parts/1"` | pydantic field `schema_id`, alias `schema` |
| `analysis_id`, `job_id`, `created_at` | str, str, ISO-8601 Z | |
| `taxonomy_version` | 1 | |
| `generator`, `source` | §3.8 | |
| `frames`, `views` | §3.7 | `views: []` for `components` |
| `strategy` | `{id, segmentation: {source: none\|internal\|reused\|persisted, segmentation_id, profile, subject}, separate[], min_part_faces, max_parts, split_shells, views, models: [{id, revision, license_id}]}` | effective options (`min_part_faces` is the resolved number). `persisted` is reserved and never produced in this milestone (SD1 deferred) |
| `region_attribute` | `"_FEATURE_ID_0"` | |
| `label_table` | `[{value, category}]` | every value present in `face_labels` |
| `meshes` | `[{index, part_id, node_name, faces, vertices, unique_vertices, vertex_map: "vmap_<id>"}]` | index 0 = base |
| `parts` | `PartRecord[]` (§3.10.1) | |
| `seams`, `relations` | §3.5, §3.6 | |
| `buffers` | `[{id, file, dtype, components, count, byte_length, sha256, indexes, description}]` | §3.3 |
| `artifacts` | `{assembled_glb: {file, sha256, bytes, nodes}` (always written, never null), `part_glbs: {"<part_id>": file}, segmentation: {labels, overlay, frame, source} \| null, masks_dir, previews: [file], log}` | |
| `expectations` | `{source: "prompt"\|"extra_terms"\|null, expected_classes[], missing[]}` | |
| `provenance`, `refinement` | §3.8, §3.9 | |
| `reassembly` | `{method: "vmap_gather", verified, positions_exact, uvs_exact, normals_exact, faces_exact}` | all must be true to publish |
| `summary` | `{part_count, physical_count, deferred_count, region_count, unlabelled_face_ratio, partner_filled_ratio, cut_partner_consistency, camera_fit_iou}` | `partner_filled_ratio` = share of faces with `face_flags` bit 0; `cut_partner_consistency` = min over cut parts of the share of the part's faces whose partner (if any) has the same owner (`null` without cuts) |
| `warnings` | `[{code, message, part_id}]` | codes in §6.8 |

- The analysis directory `jobs/<job>/parts/<pa>/` holds `run.json` (the `RunStore` record, created when the run is
  queued), `log.txt` (failed runs only) and `asset/`. `asset/` is the published, immutable part layer.
- Every manifest path is relative to `asset/`.
- The one exception is `source.model_file`. It is the literal `model.glb`, relative to the job directory, which is
  `asset/../../..`.

#### 3.10.1 Part record
| Field | Type | Notes |
|---|---|---|
| `id` | part id (§2.3) | also the file stem |
| `category`, `group`, `label` | taxonomy class id, group id, display label | |
| `kind` | `base` \| `physical` \| `physical_deferred` \| `region` | |
| `side`, `image_side` | `left\|right\|center\|null` | §3.7 |
| `parent` | part id \| null | `body` for body-attached parts. Null for the base, `object.detached` and unknown shells |
| `attach_to` | part id \| null | the resolved candidate (§2.3) |
| `host` | part id \| null | the mesh node owning a region or deferred part. Null for mesh-owning parts |
| `symmetric_with` | part id \| null | |
| `node_name` | `part:<id>` \| null | glTF node in `model_parts.glb`. Null for regions and deferred parts |
| `mesh_index` | int \| null | |
| `region` | `{attribute: "_FEATURE_ID_0", value, face_count}` | faces that carry this class value |
| `files` | `{glb: file\|null, mask: file\|null, thumbnail: file\|null}` | mask = PNG in conditioning space |
| `stats` | `{faces, vertices, area, bbox_min, bbox_max, centroid}` | asset frame |
| `frame`, `placement` | §3.7 | |
| `geometry` | `{status: surface\|open_surface\|closed\|texture_only\|absent, open_boundary_loops, watertight, occluded_geometry_missing, components, source: lifted\|component\|none, tags: [], floaters: [{component, faces, centroid}]}` | tags: `inner_sheet_merged` (`floater_candidate` reserved, not produced). `floaters` only on the base record (§2.4 rule 2), `[]` elsewhere; the key is always present (§3.11 example) |
| `uv` | `{material: 0, atlas: "shared", uv_bbox, charts_cross_boundary}` | `true` for cut parts (atlas charts are not split) |
| `seams` | seam ids | |
| `annotations` | e.g. `{"lid_curve": {"space": "conditioning", "points": [[x,y],…], "method": "heuristic", "confidence": 0.6}}` | 2D guides. 3D guides are deferred |
| `confidence`, `expected` | float 0..1 \| null, bool | |
| `provenance`, `refinement`, `lineage` | §3.8, §3.9 | |


### 3.11 Example (abridged: one part of each kind; hashes shortened)
```json
{
  "schema": "img3d.parts/1", "analysis_id": "pa_3b1f0c9a2d7e", "job_id": "job_9f3c2a1b4d5e",
  "created_at": "2026-10-20T10:04:31Z", "taxonomy_version": 1,
  "generator": {"app_version": "1.0.0", "engine": "trellis2", "model": "TRELLIS.2-4B", "pipeline_type": "1536_cascade",
                "seed": 123456, "resolution": 1536, "effective_resolution": 1536, "texture_size": 4096,
                "polygon_target": 2000000},
  "source": {"model_file": "model.glb", "model_sha256": "4b1d0c…", "model_bytes": 82113340, "vertices": 1697772,
             "faces": 1895691, "materials": 1, "images": 2, "face_order": "gltf_indices",
             "vertex_order": "gltf_accessor", "primary_image_id": "img_7c1e0d9a2b3f",
             "origin": {"kind": "prompt", "image_generation_id": "ig_0123456789ab", "model_id": "z_image_turbo",
                        "model_revision": "f332072aa78be7aecdf3ee76d5c247082da564a6", "license_id": "apache-2.0",
                        "seed": 987654, "final_prompt_sha256": "c0ffee…"}},
  "frames": {
    "asset": {"convention": "gltf", "up": "+Y", "front": "+Z", "units": "normalized",
              "aabb": [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]]},
    "trellis": {"convention": "blender", "up": "+Z", "front": "-Y",
                "to_asset": [1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1]},
    "conditioning": {"file": "segmentation/source.png", "width": 1000, "height": 1000,
                     "source_image_id": "img_7c1e0d9a2b3f", "source_size": [896, 1152], "scale": 0.8888889,
                     "crop_box": null, "alpha_threshold": 0.8, "mode": "background_removed",
                     "method": "identity", "verified": true, "residual": 0.0}},
  "views": [{"id": "input", "image_frame": "conditioning",
             "camera": {"model": "perspective", "yaw_deg": 0.0, "pitch_deg": 2.0, "fov_deg": 30.0, "distance": 1.93,
                        "world_to_camera": [1, 0, 0, 0, 0, -1, 0, 0, 0, 0, -1, 1.93, 0, 0, 0, 1],
                        "intrinsics": {"fx": 1.866, "fy": 1.866, "cx": 0.5, "cy": 0.5, "normalized": true},
                        "convention": "opencv"},
             "fit": {"method": "silhouette_grid_refine", "iou": 0.91, "accepted": true, "candidates_tested": 1361},
             "weight": 1.0}],
  "strategy": {"id": "semantic_parts",
               "segmentation": {"source": "internal", "segmentation_id": null, "profile": "baseline",
                                "subject": "anime_character"},
               "separate": ["eyes", "eyebrows", "eyelashes", "hair", "clothes", "accessories", "detachable"],
               "min_part_faces": 190, "max_parts": 64, "split_shells": true, "views": 8,
               "models": [{"id": "grounding_dino_base", "revision": "12bdfa3120f3e7ec7b434d90674b3396eccf88eb",
                           "license_id": "apache-2.0"},
                          {"id": "sam2_1_hiera_large", "revision": "665f8e2ad61cf5f53d65644ff27c8ee525124610",
                           "license_id": "apache-2.0"},
                          {"id": "raster_lifter", "revision": null, "license_id": "nvidia-source-code-nc"}]},
  "region_attribute": "_FEATURE_ID_0",
  "label_table": [{"value": 0, "category": "unknown"}, {"value": 1, "category": "body"},
                  {"value": 3, "category": "body.head.face"}, {"value": 20, "category": "eye.left"},
                  {"value": 22, "category": "eyebrow.left"}, {"value": 30, "category": "hair"}],
  "meshes": [
    {"index": 0, "part_id": "body", "node_name": "part:body", "faces": 1402233, "vertices": 1283310,
     "unique_vertices": 1271110, "vertex_map": "vmap_body"},
    {"index": 1, "part_id": "hair", "node_name": "part:hair", "faces": 410112, "vertices": 377020,
     "unique_vertices": 377020, "vertex_map": "vmap_hair"},
    {"index": 2, "part_id": "eye.left", "node_name": "part:eye.left", "faces": 2210, "vertices": 1301,
     "unique_vertices": 1301, "vertex_map": "vmap_eye.left"}],
  "parts": [
    {"id": "body", "category": "body", "group": "body", "label": "Body", "kind": "base", "side": "center",
     "image_side": "center", "parent": null, "attach_to": null, "host": null, "symmetric_with": null,
     "node_name": "part:body", "mesh_index": 0,
     "region": {"attribute": "_FEATURE_ID_0", "value": 1, "face_count": 1201733},
     "files": {"glb": null, "mask": null, "thumbnail": null},
     "stats": {"faces": 1402233, "vertices": 1283310, "area": 1.912, "bbox_min": [-0.39, -0.5, -0.19],
               "bbox_max": [0.39, 0.5, 0.2], "centroid": [0.0, -0.04, 0.0]},
     "frame": {"pivot": [0.0, -0.04, 0.0], "axes": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "method": "area_centroid"},
     "placement": {"transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], "method": "identity", "error": null},
     "geometry": {"status": "open_surface", "open_boundary_loops": 6, "watertight": false,
                  "occluded_geometry_missing": true, "components": 2, "source": "component",
                  "tags": ["inner_sheet_merged"],
                  "floaters": [{"component": 41, "faces": 37, "centroid": [0.12, -0.31, 0.02]}]},
     "uv": {"material": 0, "atlas": "shared", "uv_bbox": [0.0, 0.0, 1.0, 1.0], "charts_cross_boundary": true},
     "seams": ["seam_0", "seam_1"], "annotations": {}, "confidence": 1.0, "expected": false,
     "provenance": {"sources": [{"method": "components", "provider": "img3d", "model": null, "score": 1.0}],
                    "votes": 0},
     "refinement": {"status": "untouched", "locked": true, "lock_reason": "part_refinement_disabled",
                    "eligible": true, "needs": ["occluded_geometry"],
                    "constraints": {"seam_lock": true, "keep_uv_atlas": true, "keep_frame": true}, "history": []},
     "lineage": {"derived_from": []}},
    {"id": "hair", "category": "hair", "group": "hair", "label": "Hair", "kind": "physical", "side": "center",
     "image_side": "center", "parent": "body", "attach_to": "body.head.face", "host": null, "symmetric_with": null,
     "node_name": "part:hair", "mesh_index": 1,
     "region": {"attribute": "_FEATURE_ID_0", "value": 30, "face_count": 410112},
     "files": {"glb": null, "mask": "masks/30.png", "thumbnail": "thumbs/hair.webp"},
     "stats": {"faces": 410112, "vertices": 377020, "area": 0.611, "bbox_min": [-0.31, 0.12, -0.2],
               "bbox_max": [0.31, 0.5, 0.17], "centroid": [0.0, 0.33, -0.02]},
     "frame": {"pivot": [0.0, 0.29, 0.01], "axes": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "method": "seam_centroid"},
     "placement": {"transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], "method": "identity", "error": null},
     "geometry": {"status": "open_surface", "open_boundary_loops": 4, "watertight": false,
                  "occluded_geometry_missing": false, "components": 2, "source": "lifted", "tags": [],
                  "floaters": []},
     "uv": {"material": 0, "atlas": "shared", "uv_bbox": [0.02, 0.41, 0.97, 0.99], "charts_cross_boundary": true},
     "seams": ["seam_0"], "annotations": {}, "confidence": 0.88, "expected": false,
     "provenance": {"sources": [{"method": "lifted", "provider": "baseline", "model": "sam2_1_hiera_large",
                                 "score": 0.88}], "votes": 51234},
     "refinement": {"status": "untouched", "locked": true, "lock_reason": "part_refinement_disabled",
                    "eligible": true, "needs": ["closure"],
                    "constraints": {"seam_lock": true, "keep_uv_atlas": true, "keep_frame": true}, "history": []},
     "lineage": {"derived_from": []}},
    {"id": "eyebrow.left", "category": "eyebrow.left", "group": "eyebrows", "label": "Left eyebrow",
     "kind": "physical_deferred", "side": "left", "image_side": "right", "parent": "body",
     "attach_to": "body.head.face", "host": "body", "symmetric_with": "eyebrow.right", "node_name": null,
     "mesh_index": null, "region": {"attribute": "_FEATURE_ID_0", "value": 22, "face_count": 1890},
     "files": {"glb": null, "mask": "masks/22.png", "thumbnail": "thumbs/eyebrow.left.webp"},
     "stats": {"faces": 1890, "vertices": 1012, "area": 0.0012, "bbox_min": [0.03, 0.27, 0.13],
               "bbox_max": [0.09, 0.29, 0.16], "centroid": [0.06, 0.28, 0.15]},
     "frame": {"pivot": [0.06, 0.28, 0.15], "axes": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "method": "area_centroid"},
     "placement": {"transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], "method": "identity", "error": null},
     "geometry": {"status": "texture_only", "open_boundary_loops": 0, "watertight": false,
                  "occluded_geometry_missing": false, "components": 1, "source": "lifted", "tags": [],
                  "floaters": []},
     "uv": {"material": 0, "atlas": "shared", "uv_bbox": [0.61, 0.70, 0.64, 0.71], "charts_cross_boundary": false},
     "seams": [], "annotations": {}, "confidence": 0.61, "expected": false,
     "provenance": {"sources": [{"method": "lifted", "provider": "baseline", "model": "sam2_1_hiera_large",
                                 "score": 0.61}], "votes": 2210},
     "refinement": {"status": "untouched", "locked": true, "lock_reason": "part_refinement_disabled",
                    "eligible": true, "needs": ["geometry"],
                    "constraints": {"seam_lock": true, "keep_uv_atlas": true, "keep_frame": true}, "history": []},
     "lineage": {"derived_from": []}},
    {"id": "body.head.face", "category": "body.head.face", "group": "face", "label": "Face", "kind": "region",
     "side": "center", "image_side": "center", "parent": "body", "attach_to": null, "host": "body",
     "symmetric_with": null, "node_name": null, "mesh_index": null,
     "region": {"attribute": "_FEATURE_ID_0", "value": 3, "face_count": 51230},
     "files": {"glb": null, "mask": "masks/3.png", "thumbnail": null},
     "stats": {"faces": 51230, "vertices": 26101, "area": 0.041, "bbox_min": [-0.11, 0.17, 0.05],
               "bbox_max": [0.11, 0.36, 0.19], "centroid": [0.0, 0.26, 0.15]},
     "frame": {"pivot": [0.0, 0.26, 0.15], "axes": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "method": "area_centroid"},
     "placement": {"transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], "method": "identity", "error": null},
     "geometry": {"status": "surface", "open_boundary_loops": 0, "watertight": false,
                  "occluded_geometry_missing": false, "components": 1, "source": "lifted", "tags": [],
                  "floaters": []},
     "uv": {"material": 0, "atlas": "shared", "uv_bbox": [0.55, 0.62, 0.71, 0.80], "charts_cross_boundary": false},
     "seams": [], "annotations": {}, "confidence": 0.9, "expected": false,
     "provenance": {"sources": [{"method": "lifted", "provider": "baseline", "model": "sam2_1_hiera_large",
                                 "score": 0.9}], "votes": 40112},
     "refinement": {"status": "untouched", "locked": true, "lock_reason": "part_refinement_disabled",
                    "eligible": true, "needs": [],
                    "constraints": {"seam_lock": true, "keep_uv_atlas": true, "keep_frame": true}, "history": []},
     "lineage": {"derived_from": []}}
  ],
  "seams": [{"id": "seam_0", "a": "hair", "b": "body", "host_opening": true, "boundary_edges": 3122, "length": 1.84,
             "loops": [{"start": 0, "count": 1561, "closed": true, "length": 0.93},
                       {"start": 1561, "count": 1561, "closed": true, "length": 0.91}]}],
  "relations": [
    {"id": "rel_0", "type": "attached_to", "a": "hair", "b": "body.head.face", "via": "seam_0",
     "metrics": {"contact": "seam", "shared_length": 1.84, "distance": 0.0}, "source": "geometry", "confidence": 0.88},
    {"id": "rel_1", "type": "region_of", "a": "eyebrow.left", "b": "body", "via": null, "metrics": {"faces": 1890},
     "source": "geometry", "confidence": 1.0},
    {"id": "rel_2", "type": "symmetric_with", "a": "eyebrow.left", "b": "eyebrow.right", "via": null,
     "metrics": {"plane": "x=0", "centroid_mirror_error": 0.004, "area_ratio": 0.97}, "source": "geometry",
     "confidence": 0.92},
    {"id": "rel_3", "type": "expected_missing", "a": "asset", "b": "object.held", "via": null, "metrics": {},
     "source": "prompt", "confidence": 1.0}],
  "buffers": [
    {"id": "face_labels", "file": "buffers/face_labels.u16", "dtype": "uint16", "components": 1, "count": 1895691,
     "byte_length": 3791382, "sha256": "a1c9…", "indexes": "base_faces", "description": "taxonomy value per base face"},
    {"id": "face_owner", "file": "buffers/face_owner.u16", "dtype": "uint16", "components": 1, "count": 1895691,
     "byte_length": 3791382, "sha256": "77e0…", "indexes": "base_faces", "description": "mesh index per base face"},
    {"id": "vmap_hair", "file": "buffers/vmap_hair.u32", "dtype": "uint32", "components": 1, "count": 377020,
     "byte_length": 1508080, "sha256": "19be…", "indexes": "node_vertices:hair",
     "description": "base vertex id per vertex of part:hair"}],
  "artifacts": {"assembled_glb": {"file": "model_parts.glb", "sha256": "9b7a…", "bytes": 84522108, "nodes": 4},
                "part_glbs": {},
                "segmentation": {"labels": "segmentation/labels.png", "overlay": "segmentation/overlay.png",
                                 "frame": "segmentation/frame.json", "source": "segmentation/source.png"},
                "masks_dir": "masks", "previews": ["previews/front.png", "previews/back.png"], "log": null},
  "expectations": {"source": "prompt", "expected_classes": ["object.held"], "missing": ["object.held"]},
  "provenance": {"steps": [{"id": "segmentation_2d", "models": [{"id": "grounding_dino_base", "revision": "12bdfa31…",
                                                                  "license_id": "apache-2.0"}], "duration_s": 2.4},
                           {"id": "label_lift", "models": [{"id": "raster_lifter", "revision": null,
                                                            "license_id": "nvidia-source-code-nc"}], "duration_s": 6.8}],
                 "licences": ["mit", "dinov3-license", "nvidia-source-code-nc", "apache-2.0"]},
  "refinement": {"enabled": false, "policy": "locked_until_approved", "consumers": []},
  "reassembly": {"method": "vmap_gather", "verified": true, "positions_exact": true, "uvs_exact": true,
                 "normals_exact": true, "faces_exact": true},
  "summary": {"part_count": 9, "physical_count": 5, "deferred_count": 4, "region_count": 12,
              "unlabelled_face_ratio": 0.04, "partner_filled_ratio": 0.43, "cut_partner_consistency": 0.97,
              "camera_fit_iou": 0.91},
  "warnings": [{"code": "charts_cross_boundary", "message": "UV charts of cut parts may cross part borders.",
                "part_id": null},
               {"code": "floater_candidates", "message": "1 small unlabelled shell stays on the base.",
                "part_id": "body"}]
}
```
In the example the conditioning frame has `method: "identity"`. That is correct here because the masks were computed
directly on the job's conditioning image (`preview.png`), so they need no mapping (§4.6.3).

### 3.12 `model_parts.glb` layout (`parts/gltf_io.py` + `parts/export.py`)
```
scene "img3d_parts"  extras.img3d = {schema: "img3d.parts/1", analysis_id, job_id, region_attribute: "_FEATURE_ID_0",
                                     manifest: "manifest.json"}
└─ node "img3d_asset" (identity)                     flat on purpose: hiding one part never hides another
   ├─ node "part:body"      mesh "body"   POSITION NORMAL TEXCOORD_0 _FEATURE_ID_0 (UNSIGNED_SHORT) indices (UNSIGNED_INT)
   │                        extras.img3d = {id: "body", kind: "base", parent: null, regions: [{value: 3, category: "body.head.face"}, …]}
   ├─ node "part:hair"      extras.img3d = {id: "hair", category: "hair", group: "hair", kind: "physical",
   │                                        parent: "body", attach_to: "body.head.face", seams: ["seam_0"]}
   └─ node "part:eye.left"  …
materials/textures/images/samplers: copied verbatim from model.glb (EXT_texture_webp included when used); every
primitive uses material 0; image bufferViews are copied byte for byte.
```
- **Every** `part:<id>` node carries `extras.img3d.id` (= the part id) plus `kind`; physical nodes also carry
  `category`, `group`, `parent`, `attach_to`, `seams`. Readers should match `extras.img3d.id` first: GLTFLoader
  sanitises node names (`part:eye.left` → `parteyeleft`). (UI BR4.)
- **Every** mesh node (base and physical) carries `_FEATURE_ID_0` with the region-border vertex duplicates of §3.4,
  so the per-face label is exact on every node. (UI BR4.)
- The real `model.glb` has 1 mesh, 1 primitive, `POSITION/NORMAL/TEXCOORD_0`, `UNSIGNED_INT` indices, two
  `EXT_texture_webp` images and no samplers; its texture entries have no `source`, so textures and
  `extensionsUsed`/`extensionsRequired` are copied verbatim [V critic, real fixture].
- **Reader and writer.** `parts/gltf_io.py` is a GLB reader and writer with no extra dependencies (json, struct,
  numpy).
  - `read_base_glb(path) -> BaseMesh` reads raw accessors and checks the sha256.
    - It accepts exactly one mesh with one triangle primitive.
    - It rejects sparse accessors and Draco or meshopt compression. Otherwise it raises `UnsupportedBaseMeshError`,
      which becomes run error `unsupported_base_mesh`.
  - `write_parts_glb(path, base, nodes: Sequence[NodeSpec], *, scene_extras) -> GlbWriteReport(bytes, sha256,
    nodes)`.
- **Why a direct writer instead of re-exporting through trimesh:**
  - Vertex and face order stay explicit (A4).
  - It avoids trimesh's V-flip round trip and normals heuristics.
  - It writes `_FEATURE_ID_0` as `UNSIGNED_SHORT`.
  - It never decodes or re-encodes the 4096² atlas (A5; one decoded copy is about 117 MB, `trellis2_internals.md`
    §4.2 [V]).
- **glTF details.** `POSITION` gets accessor `min`/`max`, as glTF requires. Buffer views are 4-byte aligned.
  glTF 2.0 §3.6.2.4 requires every vertex-attribute element to be 4-byte aligned, so `_FEATURE_ID_0`
  (componentType 5123 `UNSIGNED_SHORT`, `SCALAR`) is written into **its own buffer view with `byteStride: 4`**
  (2 bytes of zero padding per vertex) and `accessor.byteOffset = 0`. Tightly packed u16 would trigger
  glTF-Validator `MESH_PRIMITIVE_ACCESSOR_UNALIGNED`. Consequence for three.js: GLTFLoader exposes the attribute
  (lower-cased `_feature_id_0`) as an `InterleavedBufferAttribute`, so readers use `getX(i)`, never `.array[i]`.
- **Validation.**
  - The tests load the file with `trimesh.load(model_parts.glb, force="scene")`.
  - `processing.mesh_stats.compute_glb_stats` sums node faces to the base face count. It is multi-node safe
    (`mesh_stats.py:72-97` [V]).
  - At run time, the reassembly check re-reads the written accessors (A4).
- **Per-part GLBs** (`parts/<id>.glb`) use the same writer with one node.
  - Each one embeds the full atlas, so they are written at run time only when `export.per_part_glbs=true`.
  - Otherwise `services/part_files.py` builds them on demand into `runtime/cache/part_exports/<pa>/` (§6.4, §7).
- **Not emitted in v1:** the `EXT_mesh_features` / `EXT_structural_metadata` declarations (deferred D6). The
  `_FEATURE_ID_0` attribute and `extras` carry the data.

### 3.13 Read API and consumer seam (`parts/reader.py`, `parts/consumers.py`; torch-free; S3b)
```python
@dataclass(frozen=True, slots=True)
class PartGeometry:
    part_id: str; positions: np.ndarray; normals: np.ndarray | None; uv: np.ndarray | None
    faces: np.ndarray            # local uint32 [n,3]
    vertex_map: np.ndarray       # uint32 [m] -> base vertex ids
    base_faces: np.ndarray       # uint32 [n] base face ids
    seams: list[SeamLoops]; frame: PartFrame

class AssetReader:
    """The ONLY read API that future consumers (tuning, refinement, DCC export) may use."""
    def __init__(self, asset_dir: Path, *, verify: Literal["none", "hashes", "full"] = "hashes") -> None
    manifest: PartsManifest
    def base(self) -> BaseMesh                          # <job>/model.glb (asset_dir.parents[2]); sha256 == source.model_sha256
    def buffer(self, buffer_id: str) -> np.ndarray      # dtype/count/byte_length/sha256 checked
    def part(self, part_id: str) -> PartGeometry        # mesh-owning parts only; ValueError for regions/deferred
    def region_faces(self, part_id: str) -> np.ndarray  # flatnonzero(buffer("face_part") == index of part_id in parts[])
    def seams(self, part_id: str) -> list[SeamLoops]
    def relations(self, part_id: str | None = None) -> list[RelationRecord]
    def reassemble(self) -> BaseMesh                    # inverse of build_mesh_nodes; used by verify="full"

class PartConsumer(Protocol):
    id: str
    def accepts(self, part: PartRecord, manifest: PartsManifest) -> bool: ...
    def describe(self) -> ConsumerInfo: ...             # {id, label, version, handles_needs: [...]}

class PartConsumerRegistry:
    def __init__(self, *, enabled: bool) -> None
    def register(self, consumer: PartConsumer) -> None  # FeatureDisabledError(feature="part_refinement") while disabled
    def list(self) -> tuple[ConsumerInfo, ...]          # () in this milestone; zero implementations ship
```

### 3.14 Validation and publish (`parts/validate.py`, `parts/publish.py`; S3b)
- `validate_staging(staging_dir, manifest, base_path) -> list[str]` must return an empty list before any publish.
  It checks:
  - ids are well formed and unique;
  - every file path is relative, contains no `..`, resolves inside the staging directory and exists;
  - each buffer's dtype, count, byte length and sha256 match;
  - every region value appears in `label_table`;
  - mesh indices are contiguous and `face_owner < len(meshes)`;
  - `face_part` values are `0xFFFF` or `< len(parts)`; for every record, the faces with `face_part == i` carry the
    record's `region.value` and lie on its host (or on its own node for mesh-owning records); floaters listed in
    `geometry.floaters` have `face_part == 0xFFFF`;
  - mesh face counts sum to `source.faces`;
  - every `reassembly` flag is true;
  - refinement is locked;
  - the base `model.glb`, re-hashed **immediately before publish**, matches `source.model_sha256`.
- `publish(staging_dir, target_dir, job_exists: Callable[[], bool])`, where `target_dir` is `jobs/<job>/parts/<pa>/asset/`:
  1. Refuses an existing `target_dir` (`publish_conflict`).
  2. Re-checks `job_exists()` (`job_deleted`).
  3. Calls `utils.files.retry_os(lambda: os.replace(staging_dir, target_dir))` [V `utils/files.py:18-30`]: a
     directory rename on Windows fails with `PermissionError` while Defender or the indexer holds a file inside.
     Both directories are under `runtime_dir`, so the rename stays on one volume and is atomic. `target_dir.parent`
     (`jobs/<job>/parts/<pa>/`) already exists (created with `run.json`, §4.3); publish never creates it.

---------------------------------------------------------------------------------------------------

## 4. Pipelines

### 4.1 Resources
| Prefix | Resource | Produces | Queue / thread | Persistence |
|---|---|---|---|---|
| `job_` | image-to-3D job (**unchanged**) | `model.glb`, `preview.png` | `JobQueue` → `GenerationWorker` → `gpu-worker` | `runtime/jobs/<job>/job.json` |
| `ig_` | image generation (prompt → candidate images) | candidate PNGs | run queue → `RunWorker` → GPU phase via `submit_gpu_task` | `runtime/imagegen/<ig>/run.json` |
| `sg_` | 2D segmentation of a reference image (before reconstruction) or of a job's conditioning image | label map, masks, overlay | run queue → GPU phase + CPU phase | `runtime/segmentations/<sg>/run.json` |
| `pa_` | part analysis of a completed job (after reconstruction) | part layer (§3) | run queue → CPU and GPU phases | `runtime/jobs/<job>/parts/<pa>/run.json` |
| `wf_` | workflow: server-side chain of the above plus a job | links to child resources | event loop only (no GPU) | `runtime/workflows/<wf>/workflow.json` |

`ig_`, `sg_` and `pa_` share one run queue (`run_queue_max_size`, default 8). One `RunWorker` consumes it, one run at
a time.

### 4.2 Image-to-3D (unchanged path)
`POST /jobs` → `JobService.create_job` → `JobQueue` → `GenerationWorker._process` → `ImageTo3DPipeline.run` →
`TrellisEngine.generate`. This is exactly today's path [V contract map §1]. No file on it changes (§1.3).

New resources reach it in two ways only:
- `JobService.create_job(CreateJobRequest)`, called by workflows with the same validation as the HTTP route.
- Read-only access to finished jobs: `JobStore.job_dir/model_path/preview_path/inputs_dir`,
  `ResultService.model_file`, and `Job.result.metadata["preprocess"]`.

### 4.3 Run framework (`models/runs.py`, `storage/run_store.py`, `pipelines/runs/*`, `workers/run_worker.py`; S1)
```python
RunStatus = JobStatus                                          # queued | running | completed | failed | cancelled
StepStatus = Literal["pending", "running", "done", "warning", "skipped", "failed", "cancelled"]
RunKind = Literal["image_generation", "segmentation", "part_analysis"]

@dataclass(frozen=True, slots=True)
class StepDef:
    id: str; label: str; weight: float; device: Literal["cpu", "gpu"]

class RunStep(DomainModel):
    id: str; label: str; status: StepStatus = "pending"; message: str | None = None
    progress: float | None = None            # 0..1 within the step
    duration_s: float | None = None; details: dict[str, Any] = Field(default_factory=dict)

class RunProgress(DomainModel):
    percent: float = 0.0; step_id: str | None = None; step_index: int = 0; step_count: int = 0
    message: str | None = None; updated_at: UtcDateTime | None = None

class RunRecord(DomainModel):
    id: str; kind: RunKind; status: RunStatus = JobStatus.QUEUED
    created_at: UtcDateTime; started_at: UtcDateTime | None = None; finished_at: UtcDateTime | None = None
    options: dict[str, Any] = Field(default_factory=dict)       # effective (normalized) request
    progress: RunProgress = Field(default_factory=RunProgress)
    steps: list[RunStep] = Field(default_factory=list)
    warnings: list[RunWarning] = Field(default_factory=list)    # RunWarning(code, message, step_id | None)
    models: list[ModelUse] = Field(default_factory=list)        # ModelUse(id, revision, license_id) actually used
    error: JobError | None = None
    cancel_requested: bool = False
    workflow_id: str | None = None
```
`ImageGenerationRun`, `SegmentationRun` and `PartAnalysisRun` (in `models/{image_generation,segmentation,
part_analysis}.py`) subclass `RunRecord`. They add `result: <Kind>Result | None = None`, plus `job_id` where it
applies.

Progress units (UI BR8): `RunProgress.percent` is 0..100; `RunStep.progress` is **0..1** within the step;
`WorkflowStepOut.progress` (§4.7.5) is **0..100**.

**`RunStore.save` and job directories.** `RunStore` (`storage/run_store.py`) never creates `jobs/<job>/` itself. For
`pa_` runs it calls `(job_dir / "parts").mkdir(parents=False, exist_ok=True)` and then
`(job_dir / "parts" / pa_id).mkdir(parents=False, exist_ok=True)` before `write_json_atomic`. When retention or a
delete removed the job folder in between, `mkdir` raises `FileNotFoundError`, which `RunStore` maps to
`JobDeletedError` (run error `job_deleted`). There is no check-then-write window.

**Executor protocol (`pipelines/runs/executor.py`).**
```python
@dataclass(frozen=True, slots=True)
class RunPhase(Generic[R, P]):
    name: str
    device: Literal["cpu", "gpu"]
    steps: tuple[str, ...]                                   # step ids this phase may run
    fn: Callable[[R, P, "RunContext"], None]                 # mutates P (phase state) and files in ctx.work_dir
    models: tuple[str, ...] = ()                             # registry ids needed (VRAM check before a gpu phase)

class RunExecutor(Protocol[R, P]):
    kind: RunKind; prefix: str                               # "ig" | "sg" | "pa"
    def plan(self, run: R) -> tuple[StepDef, ...]            # steps that do not apply are returned and pre-marked skipped
    def phases(self, run: R) -> tuple[RunPhase[R, P], ...]   # ordered
    def prepare(self, run: R, work_dir: Path) -> P           # to_thread: snapshot inputs into work_dir
    def finalize(self, run: R, state: P, ctx: "RunContext") -> R   # to_thread: validate, publish, build result
```
`RunContext` (`pipelines/runs/context.py`):
- fields: `cancel: CancellationToken`, `work_dir: Path`, `device: str`, `registry: ProviderRegistry`;
- `step(step_id, message=None) -> ContextManager[StepHandle]` (running → done/warning/failed);
- `progress(step_id, fraction, message=None)`, `warn(code, message, step_id=None)`, `skip(step_id, reason)`,
  `use_model(model_id)`.

`RunReporter` (`pipelines/runs/reporter.py`) is a copy of the `JobProgressReporter` pattern:
- At most 5 messages/s per run, with a trailing flush; these progress messages are droppable.
- `run.json` is persisted at most every 2 s.
- Status and step transitions are always published and are never droppable.

**RunWorker (`workers/run_worker.py`).**
```python
class GpuTaskSubmitter(Protocol):
    def submit_gpu_task(self, fn: Callable[..., T], /, *args: Any) -> asyncio.Future[T]: ...
    @property
    def accepting_gpu_tasks(self) -> bool: ...
    @property
    def is_busy(self) -> bool: ...
    @property
    def running_job_id(self) -> str | None: ...          # exists on GenerationWorker [V :96-98]

class RunWorker:
    def __init__(self, *, settings: Settings, gpu: GpuTaskSubmitter, queue: JobQueue, job_queue: JobQueue,
                 executors: Mapping[RunKind, RunExecutor], stores: Mapping[RunKind, RunStore],
                 publishers: Mapping[RunKind, RunEventPublisher], registry: ProviderRegistry,
                 bus: EventBus, paths: RuntimePaths) -> None
    def start(self) -> None
    async def stop(self, timeout: float) -> None
    @property
    def running_id(self) -> str | None
    @property
    def running_kind(self) -> RunKind | None
    def live_snapshot(self, run_id: str) -> RunRecord | None
    def cancel_running(self, run_id: str, reason: str) -> RunRecord | None
    async def wait_finished(self, run_id: str, timeout: float) -> bool
```
Loop for one run:
1. Mark the run `running` and set `started_at`. If `gpu.is_busy`, set the message
   `"Waiting for the GPU (generation in progress)"`.
2. Run `state = await asyncio.to_thread(executor.prepare, run, work_dir)`. The work dir is
   `runtime/temp/<kind_dir>/<id>/` (§7).
3. For each phase in order, call `cancel.raise_if_cancelled()`, then:
   - **gpu — jobs-first gate:** before submitting, wait (polling every 0.5 s on the event loop, cancellable) until
     `job_queue.pending == 0 and gpu.running_job_id is None`, with the run message
     `"Waiting for the GPU (generation queued)"`. After `run_max_defer_s` (default 600 s) of waiting, submit anyway
     and add warning `gpu_defer_timeout`. Image-to-3D jobs therefore never queue behind more than one auxiliary
     phase that was already running when they arrived.
   - **gpu:** `await gpu.submit_gpu_task(_gpu_phase, phase, run, state, ctx)`. `_gpu_phase` runs on the GPU thread.
     It enters `ctx.registry.guard()` (= `inference.providers.vram.torch_globals_guard()`, §4.9), calls
     `ctx.registry.ensure_vram(phase.models)` (§4.8), then `phase.fn`, and in `finally` calls
     `ctx.registry.offload(phase.models)` and leaves the guard (restoring the torch globals).
   - **cpu:** `await loop.run_in_executor(self._cpu_executor, phase.fn, run, state, ctx)`. `self._cpu_executor` is a
     `ThreadPoolExecutor(1, "parts-cpu")`.

   Between phases the GPU is free, so a queued generation job may run in between.
4. Run `await asyncio.to_thread(executor.finalize, run, state, ctx)`, then mark the run `completed`.
5. Map errors to a final status:
   - `JobCancelledError` → `cancelled`.
   - `AppError` → `failed` with `exc.code`.
   - Any other exception → `failed`, classified by `workers.generation_worker.classify_exception`, which is public
     and read only. It yields `out_of_memory`; anything else is reported as `run_error`.
   - Watchdog on `pa_` runs → `failed` with `run_timeout`. It counts **active phase time only** (time inside
     `prepare`, a phase callable or `finalize`; never time spent in the jobs-first gate or queued on the GPU
     executor behind TRELLIS jobs) against `part_analysis_timeout_s`.
6. **Timeout and stop never race the GPU callable.** On watchdog expiry, `cancel_running` or `stop()`, the worker
   calls `token.cancel(reason)` and then `await asyncio.shield(phase_future)` (the `asyncio.wrap_future` of the
   submitted callable). Cancelling the awaiting task does not stop a running `concurrent.futures` task, so only
   after the callable has returned (it checks the token between model calls) does the worker set the final status.
   `stop(timeout)` waits at most `timeout` for that; afterwards it marks the run `interrupted` but still leaves
   `work_dir` to the startup `purge_temp`.
7. In `finally` (reached only after step 6), call `safe_rmtree(work_dir)`. A failed `pa_` first moves
   `work_dir/log.txt` to `jobs/<job>/parts/<pa>/log.txt`, after a job-existence check.

**State machine (every run kind).**
```
queued ──RunWorker picks──► running ──finalize ok──────────► completed
  │                            ├──AppError / exception──────► failed     (error.code kept, or out_of_memory / run_error)
  │                            ├──token cancelled by user───► cancelled
  │                            └──shutdown──────────────────► failed     (interrupted)
  ├──POST …/cancel──────────────────────────────────────────► cancelled
  └──queue closed at shutdown───────────────────────────────► failed     (interrupted)
startup: persisted queued|running ──────────────────────────► failed     (interrupted)
step:    pending ─► running ─► done | warning | failed | cancelled ;  pending ─► skipped (reason in message)
```
**Percent.** `percent = 100 · (Σ w(done|warning|skipped) + w(current) · progress(current)) / Σ w(all)`. It is clamped
to 0..100, never goes down, and is exactly 100 on `completed`.

### 4.4 Prompt-to-3D: image generation runs (`ig_`; S4)

#### 4.4.1 Request `ImageGenerationRequest` (`schemas/image_generation.py`, `RequestModel`, unknown fields → 422)
```json
{ "prompt": "a knight with a red cape and a sword",   // required; trimmed length 1..settings.prompt_max_chars (2000); schema hard cap 8000
  "negative_prompt": null,                           // ≤1000; 422 unsupported_by_model when the model has no negative prompt
  "model_id": null,                                  // null → registry.default_model("text_to_image")
  "preset": "character",                             // "character" | "object" | "raw"
  "style": null,                                     // null | "realistic" | "stylized" | "anime"
  "part_hint": true,                                 // append the part-separation clause (§4.4.3)
  "num_images": 1,                                   // 1..4 (must be 1 inside workflows); generated one at a time (§4.4.4)
  "seed": 0, "randomize_seed": true,                 // 0..2147483647; candidate i uses (seed + i) mod 2^31
  "width": null, "height": null,                     // null → preset default; the pair must be in model.capabilities.sizes
  "steps": null,                                     // null → model default; within the model bounds
  "guidance": null }                                 // null → model default; within the model bounds
```
Z-Image-Turbo capabilities (catalog, §5.4):
- `sizes`: `[[1024,1024],[896,1152],[1152,896]]`. Preset defaults: character 896×1152; object and raw 1024×1024.
- `steps`: default 9, range 1..50 (`DECISIONS.md` §1).
- `guidance`: default 0.0, min 0.0, max 0.0.
- `supports_negative_prompt`: false. `max_images`: 4.

Unsupported values return the existing `unsupported_by_engine` 422 shape with a new type:
`details=[{"loc": ["body", field], "type": "unsupported_by_model", "msg": …, "ctx": {"allowed": …}}]`.

#### 4.4.2 Prompt hygiene
- Strip the prompt, then reject any control character other than `\n` and `\t`. The error is 422
  `validation_error`, with details type `invalid_characters`.
- Enforce `settings.prompt_max_chars`.
- Logs record only `len(prompt)` and the first 12 hex digits of the sha256 of the final prompt. The prompt text is
  never logged.
- `run.json` stores `prompt` and `effective_prompt`, because the UI needs them for history and regeneration. These
  files are local runtime data.
- Provenance (`ImageSource`, manifest `source.origin`) stores `final_prompt_sha256`, the model id, the revision and
  the licence id.
- Providers never use `trust_remote_code` or `custom_pipeline`, and load only local, pinned snapshots
  (`local_files_only=True`).

#### 4.4.3 Templates (`pipelines/prompt_templates.py`)
`build_prompt(prompt, preset, style, part_hint) -> str` uses the exact texts of `text_to_image_research.md` §8.1–8.3,
reproduced here.

**Character** (896×1152):
```
A full-body 3D character reference image of {subject}. Exactly one character, standing upright in a neutral A-pose:
arms straight and angled about 30 degrees away from the torso, hands open and relaxed, feet shoulder-width apart and
flat on the ground. Front view, facing the camera directly, eye-level camera, telephoto lens with minimal perspective
distortion. The whole body is visible from the top of the head to the soles of the shoes, centered in the frame with
empty space on all sides. Neutral expression, eyes open, mouth closed, hair not covering the face. Plain seamless
{background} studio background, no floor line, no props, no text. Soft, even, diffuse lighting with no cast shadows and
no strong highlights. Sharp focus everywhere. {style_clause} {part_hint_clause}
```
**Object** (1024×1024):
```
A product-style 3D reference image of {subject}. A single complete object, fully visible and centered in the frame
with empty space on all sides, three-quarter front view from slightly above eye level. Plain seamless {background}
studio background, no other objects, no text. Soft, even, diffuse lighting with no cast shadows. Sharp focus,
true-to-life materials. {style_clause} {part_hint_clause}
```
**Raw** passes the prompt through verbatim.

Placeholders:
- `{subject}` is the trimmed user prompt.
- `{background}` is `light-gray`. It becomes `medium-gray` when the prompt contains, as a whole word,
  `white|silver|pale|snow|ivory|light gray|light grey`.
- Style clauses:
  - realistic: `Photorealistic, physically accurate materials and colors.`
  - stylized: `Stylized 3D game-asset render, clean readable shapes, hand-painted textures.`
  - anime: `Anime-style character, clean cel shading, crisp line art.`
- Part-hint clause: `Hair, clothing layers and accessories are clearly distinct from each other and from the skin;
  both eyes clearly visible.`
- Z-Image-Turbo is distilled, so the prompt is positive-only and no negative prompt is used.

**Expected terms.**
- `taxonomy.match_terms(prompt)` produces `options.expected_terms`, a list of class ids.
- Segmentation uses them later: they extend the vocabulary, and a term that is not found produces an
  `expected_missing` relation.

#### 4.4.4 Steps
| id | label | weight | phase / device | notes |
|---|---|---|---|---|
| `load_model` | Loading text-to-image model | 15 | **cpu** (`parts-cpu`) | `provider.load()`: `from_pretrained(..., low_cpu_mem_usage=False)` (or an explicit re-materialisation of every parameter) so the weights are **fully read into CPU RAM**; it never touches CUDA. Skipped if the model is already resident in RAM. Status `loading`. The measured 307 s first `.to("cuda")` was disk-bound lazy reading of the mmapped fp32 transformer [M]; moving that read here takes it off the single GPU thread |
| `generate` | Generating images | 70 | gpu | Moves `text_encoder`, `transformer`, `vae` to CUDA **one at a time**, checking the token between moves. Candidates are generated **one at a time** (batch size 1): each uses its own `torch.Generator(device).manual_seed(seed_i)`; the token is checked between candidates and `torch.cuda.empty_cache()` runs after each VAE decode (a batched 4 × 896×1152 decode can exceed the 23.3 GB single-image peak). Step progress from `callback_on_step_end`. Cancel sets `pipe._interrupt = True` and raises `JobCancelledError` |
| `check` | Checking 3D readiness | 10 | cpu | §4.4.5 |
| `save` | Saving candidates | 5 | cpu | PNG plus `_thumb.webp` (max side 384) via `processing.image_ops.make_thumbnail_webp` [V] |

Phases: `cpu["load_model"]` → `gpu["generate"]` → `cpu["check","save"]`. After `generate`, the provider moves the
weights back to CPU and calls `torch.cuda.empty_cache()`. Measured: back to 0.03 GB [M]. Target [A, measured in
`test_smoke_zimage.py`, S4 acceptance]: the first `.to("cuda")` after `load()` takes < 30 s.

`load()` and the GPU calls of one provider are serialised by a per-provider `threading.Lock`, so a
`POST /models/{id}/load` warm-up (§6.1) and a run never load the same model twice.

#### 4.4.5 Readiness check (`processing/imagegen_readiness.py`, torch-free; warnings only, never failures)
- **Background and foreground:**
  - The background colour is the median of a border ring 2 % wide.
  - Foreground = pixels whose Lab ΔE from that colour is above 12, followed by a 3 px morphological open.
- **Issues** (one vocabulary: the same `t2i_*` codes are used in `readiness.issues` and as run warning codes):
  - `t2i_touches_border`: foreground bbox within 1 % of the edge.
  - `t2i_multiple_subjects`: more than one component above 2 % of the area.
  - `t2i_coverage_low`: < 8 %. `t2i_coverage_high`: > 70 %.
  - `t2i_aspect_mismatch`: a character preset whose bbox is wider than tall.
- **Result:** `readiness = {ok, issues: ["t2i_touches_border", …], method: "border_color"}`, plus one `RunWarning`
  per issue with the same code and `message` = "Candidate <i>: <text>".

#### 4.4.6 Result (`ImageGenerationOut`)
```json
{ "id": "ig_0123456789ab", "kind": "image_generation", "status": "completed",
  "created_at": "…Z", "started_at": "…Z", "finished_at": "…Z", "workflow_id": null,
  "options": { "prompt": "…", "effective_prompt": "A full-body 3D character reference image of …", "negative_prompt": null,
               "model_id": "z_image_turbo", "preset": "character", "style": "anime", "part_hint": true, "num_images": 2,
               "seed": 987654, "width": 896, "height": 1152, "steps": 9, "guidance": 0.0,
               "expected_terms": ["object.held", "clothing.outer"], "final_prompt_sha256": "c0ffee…" },
  "models": [{"id": "z_image_turbo", "revision": "f332072aa78be7aecdf3ee76d5c247082da564a6", "license_id": "apache-2.0"}],
  "progress": {"percent": 100.0, "step_id": "save", "step_index": 4, "step_count": 4, "message": "Done", "updated_at": "…Z"},
  "steps": [{"id": "load_model", "label": "Loading text-to-image model", "status": "skipped",
             "message": "Model already in memory", "progress": 1.0, "duration_s": 0.0, "details": {}}],
  "warnings": [],
  "result": { "candidates": [
      { "index": 0, "seed": 987654, "width": 896, "height": 1152,
        "url": "/api/v1/image-generations/ig_0123456789ab/candidates/0",
        "thumbnail_url": "/api/v1/image-generations/ig_0123456789ab/candidates/0/thumbnail",
        "readiness": {"ok": true, "issues": [], "method": "border_color"},
        "used_as": [{"reference_set_id": "rs_…", "image_id": "img_…"}] } ],
      "timings": {"load_model": 0.0, "generate": 3.6, "check": 0.2, "save": 0.3, "total": 4.3} },
  "error": null }
```

#### 4.4.7 Use a candidate
`POST /image-generations/{ig}/candidates/{index}/use` with body
`{"reference_set_id": null, "slot": "front", "make_primary": true, "label": null}` (`RequestModel`):
1. The run must be `completed` (409 otherwise), and the index must exist (404 otherwise).
2. If `reference_set_id` is null, call `ReferenceService.create_set(name="Prompt: " + prompt[:60])`.
3. Call `ReferenceService.upload_image(set_id, data=png_bytes, filename=f"{ig}_{index}.png", slot=slot,
   label=label or ("Prompt: " + prompt)[:200], make_primary=make_primary, source=ImageSource(kind="t2i",
   image_generation_id=ig, candidate_index=index, model_id, model_revision, license_id, seed, final_prompt_sha256))`.
   - This is the existing validation and thumbnail path. Labels are capped at 200 characters
     (`schemas/references.py:13` [V]).
4. Append `{reference_set_id, image_id}` to `candidates[index].used_as` and persist the run.
5. Respond 201 `{"image": ReferenceImage, "set": ReferenceSet}`, the same body as an upload (contract §7.3).

A legacy-UI "retry" of the resulting job resends `reference_set_id + primary_image_id + settings`. It therefore
re-runs image-to-3D deterministically on the generated image.

#### 4.4.8 The two prompt-to-3D flows (every UI mode uses them)
- **One-shot:** `POST /workflows {source: {kind: "prompt", generation: {...}}, reconstruction: {...}, parts?: {...}}`
  (§4.7). The workflow imports candidate 0 automatically (`selection: "auto_first"`). It needs
  `pipeline.prompt_to_3d.available` **and** `workflow.available`; with `IMG3D_WORKFLOWS_ENABLED=false` only the
  review-first flow works (`POST /workflows` returns 503 `feature_disabled`).
- **Review first:**
  1. `POST /image-generations`.
  2. The user picks a candidate.
  3. `POST …/candidates/{n}/use`.
  4. `POST /jobs` (no parts) or `POST /workflows {source: {kind: "image", …}}` (with parts).

  A paused, resumable workflow step is not in v1 (Q2, assistant default; D9 stays deferred).

### 4.5 Segmentation runs (`sg_`): decomposition before reconstruction (S5)

#### 4.5.1 Request `SegmentationRequest`
```json
{ "source": {"kind": "reference_image", "reference_set_id": "rs_…", "image_id": "img_…"}   // before reconstruction
          | {"kind": "job", "job_id": "job_…"},                                          // a job's conditioning image (preview.png)
  "options": {                                                                            // SegmentationOptions (shared with pa_)
    "profile": "auto",              // "auto" | "baseline" | "anime" | "realistic"
    "subject": "auto",              // "auto" | "anime_character" | "realistic_human" | "object"
    "classes": null,                // null = whole taxonomy; else a subset of taxonomy ids (422 unknown_class)
    "extra_terms": [],              // ≤16 nouns, each ≤40 chars; aliases → class, else object.held
    "use_prompt_terms": true,       // add expected_terms of the ig_ run that produced the image (via ReferenceImage.source)
    "head_pass": true,              // second pass on a head crop (§4.5.3)
    "min_confidence": 0.35 } }      // 0.05..0.95
```
- At creation, the source is **snapshotted** into `runtime/segmentations/<sg>/source.png`.
  - Reference image: `processing.image_ops.normalize_to_png(original_path, source.png)` [V `image_ops.py:70`],
    the same function `JobService._prepare_inputs` applies to `jobs/<job>/inputs/<image>.png`
    [V `job_service.py:146-163`]. The run records `source_pixels_sha256` = sha256 of the decoded RGBA pixel bytes
    (`np.asarray(Image.open(p).convert("RGBA")).tobytes()`), which §4.6.3 compares (file bytes of a JPEG, WebP or
    EXIF-rotated original never equal the normalised job input).
  - Job: the job's `preview.png`, plus `inputs/<primary>.png` as `original.png` for the head pass.
- A job source must be `completed` (409).

#### 4.5.2 Conditioning frame (`processing/conditioning.py`, torch-free, exact replica of upstream; S5)
Upstream `Trellis2ImageTo3DPipeline.preprocess_image` (`third_party/TRELLIS.2/trellis2/pipelines/
trellis2_image_to_3d.py:127-162` [V]) works as follows. TRELLIS discards the crop box and the alpha.
1. Note whether the input is RGBA with any alpha < 255.
2. Downscale with `scale = min(1, 1024/max(w, h))` to `(int(w·scale), int(h·scale))`, using LANCZOS.
3. If there is no alpha, remove the background (BiRefNet).
4. Take the bbox of `alpha > 0.8·255` and its float `center`. Set
   `size = int(max(x1 − x0, y1 − y0))` and
   `box = (cx − size//2, cy − size//2, cx + size//2, cy + size//2)`.
5. Crop with `PIL.Image.crop` (float box, rounded by Pillow; out-of-range pixels become black).
6. Premultiply in float32: `(rgb/255 · a/255 · 255).astype(uint8)`.

```python
@dataclass(frozen=True, slots=True)
class CropTransform:
    mode: Literal["alpha_crop", "background_removed", "untouched"]
    source_size: tuple[int, int]; scale: float; resized_size: tuple[int, int]
    crop_box: tuple[int, int, int, int] | None    # PIL-rounded, resized pixels, may exceed bounds; None for untouched
    size: tuple[int, int]; alpha_threshold: float = 0.8; premultiplied: bool = True

@dataclass(frozen=True, slots=True)
class ConditioningFrame:
    transform: CropTransform
    method: Literal["recompute", "persisted", "template_match", "identity"]   # "persisted" reserved (SD1 deferred)
    verified: bool; residual: float               # mean |Δ| over foreground pixels, uint8 units

def upstream_resize(img: Image.Image) -> tuple[Image.Image, float]
def crop_from_alpha(rgba_resized: Image.Image) -> tuple[Image.Image, Image.Image, CropTransform]  # (RGB premultiplied, RGBA crop, T)
def predict_frame(source: Path, remove_background: bool) -> ConditioningFrame | None   # exact for real-alpha inputs; None otherwise
def recover_frame(source: Path, preview: Path, preprocess_mode: str, job_dir: Path) -> ConditioningFrame
def to_conditioning(mask: np.ndarray, frame: ConditioningFrame) -> np.ndarray            # nearest-neighbour
def to_source(mask: np.ndarray, frame: ConditioningFrame, source_size: tuple[int, int]) -> np.ndarray
```
`recover_frame` picks a method by priority (the `persisted` method is reserved in the literal for a future SD1 and has
no code path in this milestone):
1. **`recompute`** (`alpha_crop`): rerun the replica on the source alpha, then compare with `preview.png`.
   `verified = residual < 2.0`.
2. **`template_match`** (`background_removed`; the BiRefNet matte is not persisted).
   - The search is a pyramid: TM_SQDIFF on 1/8-scale images over the downscaled source padded by `size` on each
     side, keeping the top 3 candidates; then a ±8 px masked SSD at full resolution over the foreground
     `preview_luma > 8`.
   - `verified = residual < 6.0`.
3. **`identity`** (`untouched`): the preview is the source, scaled.

If a frame cannot be verified, the run continues in source space with warning `frame_unverified`. Nothing is
silently mis-registered.

The parity test `test/engine/test_prepared_conditioning.py` (torch; skipped without CUDA/trellis2) asserts:
- `np.array_equal(crop_from_alpha(...)[0], upstream.preprocess_image(img))` for RGBA inputs with odd and even sizes,
  bboxes at the image border, and inputs larger than 1024;
- for the rembg path, a deterministic stub rembg yields equal pixels too.

This pins the Pillow rounding and the truncation [A until the test runs].

#### 4.5.3 Profiles, roles and degradation
| profile | parser | detector | grounder | refiner | used when |
|---|---|---|---|---|---|
| `baseline` | – | `grounding_dino_base` | `florence2_large` (subject caption, prompt and extra terms) | `sam2_1_hiera_large` | objects, or when a parser is unavailable |
| `anime` | `see_through_l2d` (dense, 19 classes) | GDINO for small or rare classes (eyes, brows, jewelry, objects) | Florence-2 | SAM 2.1 | `subject = anime_character` |
| `realistic` | `sapiens2_seg_0_4b` (29 classes; person-relative L/R) | GDINO for eyes, brows, accessories | Florence-2 | SAM 2.1 | `subject = realistic_human` |

- **`profile: auto`** maps the subject to a profile.
- **`subject: auto`** uses the Florence-2 `<CAPTION>` with deterministic keyword rules:
  - `anime|manga|cartoon|chibi|illustration|drawing` → `anime_character`;
  - `person|man|woman|boy|girl|photo` → `realistic_human`;
  - anything else → `object`.

  A prompt-generated image with `style: "anime"` is `anime_character` directly. Without Florence-2 the subject is
  `object`.
- **Degradation.** When a role's model is not usable, the role is skipped with a warning such as
  `"see_through_l2d unavailable (vendored_source_missing); using baseline"`. Creation fails with 503
  `capability_unavailable` only when **no** detector+refiner pair and **no** parser is usable.
- **Head pass** (`head_pass=true`):
  - Take the head box (union of the face, hair-top and eye boxes, plus a 25 % margin) and map it to source space
    with `to_source` when the frame is verified.
  - Crop the **original** at full resolution, resize to 1008², and run detector, grounder and refiner for the small
    classes (eye, eyebrow, eyelash, mouth, nose, ear, earring, glasses, hair ornament). Then map back.
  - When the frame is not verified, the head pass crops the conditioning image instead, with warning
    `head_pass_low_res`.
  - Measured on ALICE: one "eyebrow" box landed on the tiara [M]. The head pass and the eyebrow rules of §4.5.4
    exist to fix this.

#### 4.5.4 Fusion and eye heuristics (`processing/seg2d/fusion.py`, `processing/seg2d/eyes.py`; numpy/opencv only)
- **Mapping.** Source labels map to taxonomy ids with the tables of `segmentation_models_research.md` §7.2. They are
  encoded as `Parser2D.label_map` plus `parts/taxonomy` aliases.
- **Per-pixel argmax over soft masks, ordered by `z_priority`:**
  - Dense classes (hair, face, clothes) prefer the parser.
  - Small or rare classes (eyes, brows, jewelry, objects) prefer detector + refiner.
  - Gates: `min_confidence`, and a minimum area of 0.02 % of the frame. Pixels that fail become `unknown`.
- **Sides.** Eyes, brows and lashes are split by connected components sorted by x. A component's side is
  character-relative: image-right = character left.
- **Eyebrows.**
  - GDINO "eyebrow" boxes, refined by SAM, are kept only if the box centre lies above an eye mask and within
    1.5·eye-height of it. This fixes the tiara case.
  - Fallback: the dark band above the eye mask (Otsu on L*), with confidence ≤ 0.4.
- **Eyelashes** (research §5).
  - Upper-lid curve = the Savitzky–Golay-smoothed per-column top boundary of the eye mask.
  - The band runs from −0.35·h to +0.12·h around that curve and extends 15 % past the outer canthus. It is
    intersected with dark pixels (Otsu on L* inside the eye bbox), minus the eyebrow and iris.
  - Output: the eyelash mask plus `annotations["eyelash.<side>"] = {lid_curve: [[x,y],…], method: "heuristic",
    confidence}`.
- **Eyelids (26/27).** The ring = `dilate(eye, 0.35·h_eye) − eye − eyebrow − eyelash`, intersected with the face
  mask.

#### 4.5.5 Steps
| id | weight | device | work |
|---|---|---|---|
| `prepare` | 5 | cpu | snapshot, downscale |
| `frame` | 5 | cpu | §4.5.2 (`predict_frame` for reference images; identity for job sources, whose image is already the conditioning image) |
| `parse` | 25 | gpu | parser; skipped if there is none |
| `detect` | 25 | gpu | GDINO + Florence-2 on the full image, then on the head crop. GDINO phrases are packed into batches of ≤ 200 tokenizer tokens (the `". "` separators counted; grounding-dino-base truncates at `max_text_len = 256` [V `config.json`]) and run one forward pass per batch, so no class is silently dropped (§5.6) |
| `refine` | 30 | gpu | SAM 2.1 per box (plus a positive centroid point) |
| `fuse` | 10 | cpu | §4.5.4 |

Phases: `cpu[prepare, frame]`, `gpu[parse, detect, refine]`, `cpu[fuse]`. Each provider offloads at the end of the
GPU phase. `pipelines/segmentation.py` exposes `segment_image(image: Path, original: Path | None,
frame: ConditioningFrame | None, options: SegmentationOptions, ctx: RunContext, out_dir: Path) ->
SegmentationResult` for reuse by `pa_` (internal pass and extra views).

#### 4.5.6 Artifacts and result
```
runtime/segmentations/<sg>/run.json  source.png  [original.png]  frame.json
                           labels.png (16-bit PNG, value = taxonomy value)  overlay.png (RGBA, ≤1024)
                           masks/<value>.png (8-bit soft mask)  segmentation.json (= result payload)
```
`SegmentationOut.result`:
```json
{ "space": "conditioning" | "source",
  "frame": {"mode": "alpha_crop", "method": "recompute", "scale": 0.5, "crop_box": [112, 40, 912, 840],
            "size": [800, 800], "verified": true, "residual": 0.7} | null,
  "subject": "anime_character", "profile": "baseline",
  "labels": [{"value": 30, "category": "hair", "group": "hair", "area_px": 51234, "confidence": 0.86,
              "bbox": [130, 42, 870, 610], "side": null, "sources": ["grounding_dino_base", "sam2_1_hiera_large"],
              "mask_url": "/api/v1/segmentations/sg_…/masks/30"}],
  "annotations": {"eyelash.left": {"lid_curve": [[401, 300], [430, 292]], "method": "heuristic", "confidence": 0.6}},
  "expected_terms_missing": ["object.held"],
  "overlay_url": "/api/v1/segmentations/sg_…/overlay", "labels_url": "/api/v1/segmentations/sg_…/labels",
  "timings": {"parse": 0.0, "detect": 1.1, "refine": 0.6, "fuse": 0.1, "total": 2.6} }
```
- `space = "conditioning"` covers job sources and reference images whose frame was predicted exactly (real alpha).
- Otherwise `space = "source"`.

### 4.6 Part analysis runs (`pa_`): decomposition after reconstruction (S10 orchestrates; S3a/S3b library; S7 lifter)

#### 4.6.1 Strategies
| id | needs | output | default when |
|---|---|---|---|
| `components` | nothing; CPU only (scipy) | Base + `component_###` shells (physical, `unknown`). Inner sheets merged (§4.6.5) | segmentation or lifter not usable |
| `semantic_regions` | segmentation + lifter | Labels on every face; **no cuts** (one node, regions only) | – |
| `semantic_parts` | segmentation + lifter | Labels, physical cuts for the selected groups, regions for the rest (§2.4) | segmentation and lifter usable |
| `per_part_regeneration` | – | **Reserved**: reported `available: false, code: "not_implemented"` (deferred D2) | never |

#### 4.6.2 Request `PartAnalysisOptions` (`schemas/parts.py`; also used by workflows)
```json
{ "strategy": null,                                   // null → /features default_strategy
  "segmentation_id": null,                            // reuse a completed sg_ (snapshot-copied); see §4.6.3. Must be null inside workflows
  "segmentation": null,                               // SegmentationOptions for the internal pass (null → defaults)
  "separate": ["eyes","eyebrows","eyelashes","hair","clothes","accessories","detachable"],
  "views": 8,                                         // extra multi-view renders, 0..24 (0 = input view only)
  "min_part_faces": null,                             // null → max(50, round(1e-4·F)) (200 at 2 M faces, 50 at 500 k); explicit 0..100000
  "max_parts": 64,                                    // 1..256
  "split_shells": true,
  "export": {"per_part_glbs": false, "previews": true} }   // PartExportOptions
```
- `PartExportOptions` is a `RequestModel` sub-model (`extra="forbid"`) with per-field defaults
  `per_part_glbs: bool = False`, `previews: bool = True`. `model_parts.glb` is **always** written, so
  `result.assembled_url` and `result.download_url` are never null. There is no `assembled_glb` option.
- **Partial nested objects** (UI BR2): every nested option model (`export`, `segmentation`, `SegmentationOptions`,
  `PartExportOptions`) accepts partial objects; omitted fields take their defaults; unknown keys are 422
  `extra_forbidden`. `{}` is valid everywhere an options object is accepted.
- The default `polygon_target` of a job is 500 000 [V `test_health_system.py:61-69`], so every face-count threshold is
  relative to `F` (here, §2.4 rule 2 and §4.6.5 steps 5.6–5.7), never an absolute number sized for 2 M faces.

Errors:
- 422 for an unknown group, a value out of range, or an unknown field.
- 404 / 409 for a `segmentation_id` that is unknown or not `completed`.
- 503 `capability_unavailable` when a semantic strategy is requested and segmentation or the lifter is not usable.

#### 4.6.3 Which 2D labels are lifted (decision order)
(`strategy.segmentation.source = "persisted"` is reserved for a future SD1 and has no code path here.)
1. **`reused`.** `segmentation_id` is given, and its result has `space = "conditioning"` with an accepted frame:
   - a job-source `sg_` of the same job; or
   - a reference-image `sg_` with a verified `recompute` frame (real-alpha input) whose `source_pixels_sha256`
     (§4.5.1) equals the sha256 of the decoded RGBA pixels of the job's `inputs/<primary>.png` (both come from
     `normalize_to_png` of the same original).
2. **`internal`.** Run `segment_image` on the job's `preview.png`. **That file is exactly the conditioning image**
   [V generation map §2], so no frame mapping is needed. This is the default and it is pixel-exact for
   `background_removed` inputs, including every prompt-to-3D job.
   - Foreground for the camera fit, `alpha_crop` / `background_removed` jobs: `(preview_luma > 8) ∪ (labels > 0)`,
     followed by a 5 px morphological close and `binary_fill_holes`. This recovers near-black hair pixels.
   - Foreground for `untouched` jobs (`preprocess = "untouched"` keeps the real background,
     `generation.py:166-170` [V]): `labels > 0` only, and the camera fit also solves a 2D scale + translation
     (§4.6.5 step 3).
   - A `segmentation_id` that does not qualify under rule 1 (every pre-reconstruction `sg_` of an opaque upload or a
     prompt image, whose frame was only `template_match`ed) is **not** used for lifting: the internal pass runs and
     the analysis records warning `segmentation_rerun_for_alignment`. The manifest keeps the given id in
     `strategy.segmentation.segmentation_id` with `source = "internal"`; `asset/segmentation/` holds the internal
     pass that was actually lifted.

#### 4.6.4 Steps and phases
| id | label | w | device | `components` | `semantic_*` |
|---|---|---|---|---|---|
| `prepare` | Preparing inputs | 4 | cpu | `read_base_glb` + sha256; weld; adjacency; components; shells; per-face base colour | same, plus opposite-sheet partners (§4.6.5 step 2) and the segmentation snapshot |
| `segmentation_2d` | Segmenting image | 12 | gpu | skipped | internal pass (§4.6.3), or skipped (reused) |
| `camera_fit` | Aligning camera | 8 | gpu | skipped | silhouette grid fit |
| `label_lift` | Lifting labels to 3D | 22 | gpu | skipped | face-ID votes: input view + `views` extra views (render, segment, vote) |
| `assign_labels` | Assigning labels | 8 | cpu | skipped | argmax, opposite-sheet partner fill, colour-affinity propagation, mirror, smoothing, component fill, island merge |
| `partition` | Separating parts | 16 | cpu | components → parts | policy, `face_owner`, nodes, seams, relations, frames |
| `export` | Exporting parts | 22 | cpu | buffers, `model_parts.glb`, masks, thumbnails, manifest, reassembly check, validate | same |
| `previews` | Rendering previews | 8 | gpu | front/back/left/right label-colour renders (skipped when the lifter is unusable or `export.previews=false`) | same |

- Phases: `cpu[prepare]` → `gpu[segmentation_2d, camera_fit, label_lift]` → `cpu[assign_labels, partition,
  export]` → `gpu[previews]`. `finalize` publishes atomically (§3.14) and builds the result.
- `components` needs no GPU unless previews are on.
- The GPU is free between phases, so a queued job is never blocked behind CPU partitioning.

#### 4.6.5 Algorithm
1. **Base:**
   - `read_base_glb(model.glb)` gives raw accessors.
   - Weld: `weld_map = np.unique(np.ascontiguousarray(positions).view(np.dtype((np.void, 12))).ravel(),
     return_inverse=True)[1].reshape(-1)`. On numpy 2.4.1 the inverse of a `(V, 1)` void view has shape `(V, 1)`
     [M: `(1618334, 1)` on ALICE], which would silently make `weld_map[faces]` `(F, 3, 1)`; the `.ravel()` /
     `.reshape(-1)` pair is mandatory.
   - Welded face adjacency: faces that share a welded edge, regardless of manifoldness.
   - Connected components: `scipy.sparse.csgraph.connected_components`, ordered by surface area (0 = largest).
   - Per-face base colour: sample the decoded baseColor atlas (Pillow) at the UV centroid; kept in CIE Lab.
2. **Shells (`parts/shells.py`)** — this fixes A's double-shell flaw.
   - For every component C except the largest, find a larger component K such that ≥ 95 % of C's vertices lie
     within `shell_eps = max(3/effective_resolution, 1.5·median_edge_length(K))` of K's vertices
     (`scipy.spatial.cKDTree`).
   - If one exists, C is the **inner sheet** of K. It merges into K's owner and gets tag `inner_sheet_merged`.
     This handles **detached** inner sheets only (343 small components, 19 080 faces on ALICE [M]).
   - Remaining components under 25 % of the faces are separate shells; floaters follow §2.4 rule 2.
   - **Opposite-sheet partners (the fix for rim-joined double walls).** On real TRELLIS.2 output the outer and inner
     sheet of the body are **one welded component**, joined at the rims of thin sheets: on ALICE, component 0 holds
     90.2 % of the faces, and 78.5 % of its faces have an opposite-normal face within `4/1536` **inside component 0
     itself** [M, re-measured 2026-10-16 with `np/scipy` on `test/outputs/engine_smoke/job_smoke165749/model.glb`].
     A component-level rule can never find that inner sheet. Therefore
     `parts/shells.py::pair_opposite_faces(centroids: f32[F,3], normals: f32[F,3], eps: float) -> i32[F]`
     (−1 = none) runs for every face:
     - `cKDTree` over all face centroids, `query(k=16, distance_upper_bound=shell_eps)`;
     - the partner of f is the nearest g with `dot(n_f, n_g) < −0.5` and `dot(c_g − c_f, n_f) < 0` (g lies behind
       f; the rule is symmetric for outer and inner faces);
     - measured cost on 1.94 M faces: tree build 0.38 s + query 0.14 s [M critic]; 2.9 s with `k=16` in the
       re-measurement [M]. Well inside the S3a budget.
3. **Camera fit (lifter, GPU):**
   - Silhouettes at 256² over yaw 0–350° in 10° steps × pitch −20…40° in 10° steps × FOV {10°, 30°, 50°} = 756
     candidates.
   - Camera distance per FOV: `distance = 1.05·r_bs / sin(fov/2)`, where `r_bs` is the bounding-sphere radius of the
     `model.glb` positions (centre = AABB centre). The object therefore fills the frame at every FOV instead of
     clipping at 10° or shrinking at 50°.
   - Each silhouette gets the tight-bbox square crop of §4.5.2. Score = IoU against the conditioning foreground
     (§4.6.3). For `untouched` jobs the crop is replaced by a 2D similarity fit (scale + translation of the
     silhouette bbox onto the `labels > 0` bbox) before scoring.
   - The top 5 are refined over ±5° in 1° steps (605 renders).
   - Accept the fit if `iou ≥ 0.6`. Otherwise use the front view (yaw 0, pitch 0, FOV 30) with `accepted=false` and
     warning `camera_fit_low_iou`; every lifted confidence is multiplied by 0.5 and `result.status = "partial"`.
4. **Votes:**
   - Face-ID + depth raster at the conditioning size.
   - A pixel with label k, confidence c and view-direction cosine |cos θ| adds `w_view·c·|cos θ|` to `votes[f, k]`.
     Votes are kept as sparse COO entries per (face, label).
   - The `views` extra views use `yaw_i = fit_yaw + 360·i/(views+1)` for i = 1..views, with pitch 0 for odd i and
     30° for even i.
     - Each is a textured unlit render (baseColor atlas) at 1024², segmented by the same profile, with
       `w_view = 0.5`.
   - The input view has `w_view = 1.0`.
   - `model.glb` has about 2 M faces, well below nvdiffrast's 2²⁴ float-ID limit. The decode rule is implemented
     anyway (`trellis2_internals.md` §3.2).
5. **Assignment (`parts/labels.py`):**
   1. Per face, argmax where the total vote is ≥ 0.2.
   2. **Partner fill.** Every unvoted face whose partner (step 2) is voted takes the partner's label, with
      confidence × 0.9, and sets `face_flags` bit 0. Faces of detached inner-sheet components that still have no
      label take the label of the nearest voted face centroid within `shell_eps` (also bit 0). Single pass: only a
      **voted** partner donates a label (`shell_eps` here is the global `max(3/effective_resolution,
      1.5·median_edge_length)`).
   3. Remaining unvoted faces: multi-source Dijkstra over welded adjacency from the labelled faces. The edge cost is
      `1 + (ΔE_Lab/10)²`. A face is assigned when its cost is ≤ 64 (bit 1); otherwise it stays `unknown`.
   4. Mirror completion for sided pairs, when the mirrored mesh error is ≤ `2/effective_resolution`.
   5. Two passes of 1-ring majority smoothing.
   6. **Component fill** (only `unknown` faces; never overwrites a voted, partner-filled or propagated label): in a
      welded component that holds **< 25 % of F** and is **not component 0**, where ≥ 90 % of the labelled faces
      share one label, the remaining `unknown` faces get that label (bit 2). On a robed character component 0 can be
      ≥ 90 % `clothing.dress`; it is exempt so the face, eye and hand labels on the base survive.
   7. **Island merge.** Labelled islands (connected same-label face sets) smaller than
      `min_island_faces = max(8, round(2e-5·F))` (40 at 2 M faces, 10 at 500 k) merge into the surrounding majority
      label. Islands of the groups `eyes`, `eyebrows`, `eyelashes` and of every `face` region are **never** merged
      (a 120-face lash band or a 180-face brow island at 500 k faces must survive; `decide_kind` turns a too-small
      physical class into `physical_deferred` with `too_small_for_geometry`, never into nothing).
   8. Confidence per face = winning vote share × (0.5 if the fit was rejected).
6. **Policy and partition:**
   - §2.4 gives the kinds. `face_owner` is 0 for the base. Physical parts are numbered 1..n in (taxonomy value,
     instance) order.
   - **Partner ownership rule:** a face whose label came from its partner (`face_flags` bit 0) always gets its
     partner's `face_owner`, so every cut takes **both** walls of a double-walled sheet. Two voted partners with
     different labels keep their own owners (a real contact between two classes).
   - `face_part` (§3.3) is filled after the records exist: for each face, the index of the finest record covering it.
   - Instances follow `PartClass.instance_split` (§2.3). Instance ids are `<class id>`, then `<class id>_2`, `_3`
     and so on, numbered by face count, largest first.
   - §3.4 builds the nodes, §3.5 the seams, §3.6 the relations and §3.7 the frames.
   - No hole filling, remeshing or smoothing of geometry.
7. **Export and checks:** buffers, `model_parts.glb`, masks (`masks/<value>.png`, copied in conditioning space) and
   thumbnails (`thumbs/<id>.webp`: a masked crop of the conditioning image, max side 256).
   - The reassembly check runs **on the written files** (A4). Any mismatch fails the run with `partition_failed`.
   - Then the manifest is written and validated (§3.14).

#### 4.6.6 Artifacts
- `jobs/<job>/parts/<pa>/run.json`: the `PartAnalysisRun` record. `RunStore` creates it when the run is queued
  and updates it in place.
- `jobs/<job>/parts/<pa>/log.txt`: failed runs only.
- `jobs/<job>/parts/<pa>/asset/`: published by one `os.replace` from `runtime/temp/parts/<pa>/out/` and immutable
  afterwards. It contains:
```
manifest.json       img3d.parts/1
model_parts.glb     assembled multi-node GLB (always written)
buffers/*.u8|u16|u32
parts/<id>.glb      only with export.per_part_glbs (otherwise built on demand, §6.4)
masks/<value>.png   thumbs/<id>.webp
segmentation/source.png labels.png overlay.png frame.json segmentation.json
previews/{front,back,left,right}.png
```
`model.glb`, `preview.png` and `job.json` are only read (I2). `TuningStore.load` scans only `jobs/*/tuning/` and
`JobStore.load` reads only `job.json` [V], so `parts/` is invisible to both.

#### 4.6.7 Result (`PartAnalysisOut.result`)
```json
{ "status": "completed",                                   // "completed" | "partial" (frame unverified, fit rejected, segmentation degraded)
  "manifest_url": "/api/v1/part-analyses/pa_…/manifest",
  "assembled_url": "/api/v1/part-analyses/pa_…/model.glb",
  "download_url": "/api/v1/part-analyses/pa_…/export/glb",
  "zip_url": "/api/v1/part-analyses/pa_…/export/zip",
  "overlay_url": "/api/v1/part-analyses/pa_…/segmentation/overlay.png",
  "face_labels_url": "/api/v1/part-analyses/pa_…/buffers/face_labels",
  "preview_urls": {"front": "/api/v1/part-analyses/pa_…/previews/front.png"},
  "region_attribute": "_FEATURE_ID_0",
  "part_count": 9, "physical_count": 5, "deferred_count": 4, "region_count": 12,
  "camera_fit_iou": 0.91, "unlabelled_face_ratio": 0.04, "partner_filled_ratio": 0.43, "cut_partner_consistency": 0.97,
  "parts": [{"id": "hair", "label": "Hair", "category": "hair", "group": "hair", "kind": "physical",
             "node_name": "part:hair", "region_value": 30, "faces": 410112, "confidence": 0.88, "side": "center",
             "model_url": "/api/v1/part-analyses/pa_…/parts/hair/model.glb",
             "download_url": "/api/v1/part-analyses/pa_…/parts/hair/export/glb",
             "mask_url": "/api/v1/part-analyses/pa_…/masks/30",
             "thumbnail_url": "/api/v1/part-analyses/pa_…/thumbs/hair.webp",
             "refinement": {"status": "untouched", "locked": true, "eligible": true, "needs": ["closure"]}}],
  "stats": { /* MeshStats of model_parts.glb */ },
  "timings": {"prepare": 2.1, "segmentation_2d": 3.0, "camera_fit": 4.2, "label_lift": 18.4, "assign_labels": 3.3,
              "partition": 9.9, "export": 14.7, "previews": 2.0, "total": 57.6} }
```
For regions and deferred parts, `model_url` and `download_url` are null. `assembled_url` and the top-level
`download_url` are never null (`model_parts.glb` is always written). The summary fields of §3.10
(`partner_filled_ratio`, `cut_partner_consistency`) are also copied into the result.
Nullability of the other result fields:
- `overlay_url` and `camera_fit_iou` are null for the `components` strategy (its `segmentation_2d` and `camera_fit`
  steps are skipped, §4.6.4). For semantic strategies they are non-null (a rejected fit still reports its IoU and
  makes the result `partial`).
- `cut_partner_consistency` is null when no part was cut (§3.10).
- `preview_urls` is `{}` when the `previews` step is skipped (lifter unusable or `parts.export.previews == false`,
  §4.6.4); otherwise it has one entry per rendered view.
- `zip_url`, `manifest_url`, `face_labels_url` and `region_attribute` are never null.

### 4.7 Workflows (`wf_`): the server-side chain (S11)

#### 4.7.1 Request `WorkflowRequest` (`schemas/workflows.py`)
```json
{ "name": null,                                                         // ≤120 chars
  "source": {"kind": "image", "reference_set_id": "rs_…", "primary_image_id": null}
          | {"kind": "prompt", "generation": ImageGenerationRequest}    // num_images must be 1
          | {"kind": "job", "job_id": "job_…"},                         // rebuild parts of a completed job
  "segmentation": null | SegmentationOptions,                           // non-null → sg_ BEFORE reconstruction; requires parts (below)
  "reconstruction": null | {"settings": GenerationSettings},            // the existing schema, unchanged; required for image/prompt, must be null for job
  "parts": null | PartAnalysisOptions,                                  // non-null → pa_ AFTER reconstruction; parts.export carries the export options
  "graph": null | WorkflowGraph,                                        // Node UI: typed graph, compiled server-side (§4.7.2)
  "layout": null | {} }                                                 // opaque Node-UI layout, ≤ workflow_graph_max_bytes, never interpreted
```
- There is **no top-level `export`** field (a body with one is 422 `extra_forbidden`). Export options live only in
  `parts.export` (`PartExportOptions`, §4.6.2), for workflows and direct analyses alike.
- If `graph` is present, `source`, `segmentation`, `reconstruction` and `parts` must be absent from the body
  (checked with `model_fields_set`). Otherwise the request fails with 422 `validation_error` and details type
  `conflicting_fields`.
- **Segmentation before reconstruction (one rule for the simple form and the graph form):**
  - `segmentation` non-null with `parts: null` → 422 `validation_error`, type `conflicting_fields`
    (`loc: ["body", "segmentation"]`). A segmentation preview without parts is the standalone
    `POST /segmentations` (§6.3).
  - `parts.segmentation_id` non-null inside a workflow → 422 `conflicting_fields` (`loc: ["body", "parts",
    "segmentation_id"]`); the workflow links its own `sg_`.
  - When `segmentation` and `parts` are both non-null, the workflow's `sg_` runs before reconstruction and the `pa_`
    child is created with `segmentation_id = outputs.segmentation_id`. §4.6.3 reuses it **only** for a verified
    `recompute` frame of a real-alpha upload (or a job source); otherwise (every prompt image and opaque upload) the
    `pa_` runs its internal pass on `preview.png` and records warning `segmentation_rerun_for_alignment`.
- The compiled simple form is stored as `request`.

`WorkflowGraph` (`schemas/workflows.py`):
```json
{"schema": "img3d.workflow_graph", "version": 1,
 "nodes": [{"id": "n_input", "type": "input_image", "params": {"reference_set_id": "rs_…", "primary_image_id": null}},
           {"id": "n_seg", "type": "segmentation", "params": {"profile": "auto"}},
           {"id": "n_recon", "type": "image_to_3d", "params": {"resolution": 1536, "texture_size": 2048}},
           {"id": "n_parts", "type": "part_extraction", "params": {"separate": ["eyes", "hair", "clothes"]}},
           {"id": "n_export", "type": "export", "params": {"per_part_glbs": false}}],
 "edges": [{"from": {"node": "n_input", "port": "images"}, "to": {"node": "n_seg", "port": "images"}},
           {"from": {"node": "n_input", "port": "images"}, "to": {"node": "n_recon", "port": "images"}},
           {"from": {"node": "n_recon", "port": "mesh"}, "to": {"node": "n_parts", "port": "mesh"}},
           {"from": {"node": "n_seg", "port": "masks"}, "to": {"node": "n_parts", "port": "masks"}},
           {"from": {"node": "n_parts", "port": "parts"}, "to": {"node": "n_export", "port": "asset"}}]}
```
Limits:
- Node id `^[a-z][a-z0-9_]{0,31}$`, at most 32 nodes and 64 edges.
- The serialized graph is at most `workflow_graph_max_bytes` (65536).
- Params are validated per node type with the node's pydantic model.

#### 4.7.2 Node catalog and compiler (`services/workflow_catalog.py` S1; `services/workflow_compiler.py` S11)
Port types: `image_set`, `segmentation`, `mesh`, `part_set`.

| Node type | Category | Inputs (accepts, required) | Outputs | Params model | Compiles to | State in this milestone |
|---|---|---|---|---|---|---|
| `input_image` | input | – | `images: image_set` | `{reference_set_id, primary_image_id}` | `source{kind: image}` | available |
| `prompt` | input | – | `images: image_set` | `ImageGenerationRequest` (`num_images` fixed to 1) | `source{kind: prompt}` | `pipeline.prompt_to_3d.available` |
| `job_result` | input | – | `images: image_set`, `mesh: mesh` | `{job_id}` | `source{kind: job}` | available |
| `segmentation` | analyze | `images` (image_set, required) | `masks: segmentation` | `SegmentationOptions` | `segmentation` | `pipeline.segmentation.available` |
| `image_to_3d` | generate | `images` (image_set, required) | `mesh: mesh` | `GenerationSettings` | `reconstruction` | engine not `error`/`unavailable` |
| `part_extraction` | parts | `mesh` (mesh, required), `masks` (segmentation, optional) | `parts: part_set` | `PartAnalysisOptions` minus `segmentation_id` and `export` | `parts` | `pipeline.part_extraction.available` |
| `export` | output | `asset` (mesh \| part_set, required) | – | `PartExportOptions` `{per_part_glbs, previews}` | `parts.export` (ignored when there is no `part_extraction`) | available |
| `part_refine`, `mesh_tune`, `eye_correction`, `rig_correction` | refine | `parts` (part_set) | `parts: part_set` | none | – | **locked**: `available:false, locked:true, code:"not_approved", reason:"Paused until approved", hidden_by_default: true` (Q3: every tuner mount point hidden in all four modes; the UI lists them only behind its preview flag); no executor exists |
| `segmentation_3d`, `part_completion`, `part_generation` | parts | `mesh` / `parts` | `parts` | none | – | `available:false, code:"unsupported"`, reason names the blocking dependency (spconv / torch_cluster / Linux sidecar); `hidden_by_default: true` |

Every node type has `max_instances = 1`.

Every node type has `params_schema` = `model_json_schema()` of its params model, always with a `properties` map
(`input_image`, `prompt`, `job_result`, `segmentation`, `image_to_3d`, `part_extraction`, `export`; `{"type":
"object", "properties": {}}` for the paramless locked/unsupported nodes). `prompt`'s schema lists every
`ImageGenerationRequest` field; `image_to_3d`'s lists the `GenerationSettings` fields. (UI BR3.)

**Extension rule (modular engine for future tuning nodes; nothing of it is implemented now).** An approved future
node type is added by exactly three additive changes and no edit to the compiler core:
1. a catalog entry in `services/workflow_catalog.py` with its params model and `compiles_to`;
2. one optional `WorkflowRequest` field (additive, default `None`; rejected with `node_locked` while its flag, e.g.
   `part_refinement_enabled`, is false);
3. one `WorkflowStepDef(id, node, weight, create_child)` row in the `WORKFLOW_STEPS` table of
   `services/workflow_service.py`.

The compiler maps node type → request field through `compiles_to`; `WorkflowService.advance` iterates
`WORKFLOW_STEPS`. The four refine nodes have catalog entries only (no field, no step row) until the user approves
tuning. No field is reserved in advance: a reserved schema for a node without an executor would be speculative tuning
API.

**Templates** (graphs in `services/workflow_catalog.py`): `image_to_3d`, `image_to_3d_parts`, `prompt_to_3d`,
`prompt_to_3d_parts`, `rebuild_parts` (job_result → part_extraction → export).

**`LEGACY_JOB_NODE_MAP`** (used by the Node UI to light nodes for plain `POST /jobs` jobs):
```json
{"queued": "image_to_3d", "waiting_for_model": "image_to_3d", "preprocessing": "input_image",
 "conditioning": "image_to_3d", "sparse_structure": "image_to_3d", "shape": "image_to_3d", "texture": "image_to_3d",
 "decoding": "image_to_3d", "exporting": "export", "finalizing": "export", "done": "export"}
```

**Compiler.**
```python
@dataclass(frozen=True, slots=True)
class GraphIssue:
    severity: Literal["error", "warning"]; code: str; message: str
    node_id: str | None = None; port: str | None = None; edge_index: int | None = None
    details: Mapping[str, Any] | None = None

@dataclass(frozen=True, slots=True)
class CompileResult:
    valid: bool
    issues: tuple[GraphIssue, ...]
    request: WorkflowRequest | None          # normalized simple form (graph kept alongside)
    mode: Literal["job", "workflow"] | None  # "job": the graph is exactly input_image → image_to_3d → export (no parts);
                                             # the client then posts its OWN CreateJobRequest to POST /jobs (UI BR1)

class WorkflowCompiler:
    def __init__(self, *, features: FeatureService, jobs: JobService, references: ReferenceService) -> None
    def compile(self, graph: WorkflowGraph) -> CompileResult           # pure apart from existence lookups
```
Issue codes:

| Code | Severity | Meaning |
|---|---|---|
| `graph_too_large` | error | node, edge or byte limit exceeded |
| `unknown_node_type` | error | |
| `unknown_port` | error | |
| `type_mismatch` | error | |
| `port_already_connected` | error | |
| `missing_required_input` | error | |
| `no_source` | error | |
| `multiple_sources` | error | more than one of input_image/prompt/job_result, or segmentation and image_to_3d fed by different sources |
| `cycle` | error | Kahn leftover |
| `too_many_instances` | error | |
| `node_locked` | error | |
| `unsupported_topology` | error | `job_result` + `image_to_3d`; part_extraction without a mesh source; a `segmentation` node whose `masks` does not feed an active `part_extraction.masks` (`node_id` = the segmentation node) |
| `capability_unavailable` | error | details = registry reasons |
| `feature_disabled` | error | |
| `invalid_param` | error | pydantic errors relocated to `nodes[i].params.<field>` |
| `not_found` | error | set, image or job does not exist |
| `no_sink` | warning | an implicit `export` is added |

A Segmentation node must feed `part_extraction.masks`; otherwise the compiler emits `unsupported_topology`. A
connected Segmentation node compiles to the top-level `segmentation`; the `pa_` gets the workflow's own `sg_` id at
run time (§4.7.1 rule; §4.6.3 decides whether it is reusable). So the graph form and the simple form obey the same
rule, and "simple form ≡ graph form" holds for every valid graph.

#### 4.7.3 Steps
| step id | node | weight | child | created when |
|---|---|---|---|---|
| `text_to_image` | `prompt` | 6 | `ig_` | immediately (prompt source) |
| `import_image` | `input_image` | 1 | reference set + image | `ig_` completed → candidate 0 imported (`auto_first`). Image source: `done` at creation. Job source: `skipped` |
| `segmentation` | `segmentation` | 4 | `sg_` (reference_image source; job source for `kind: job`) | after `import_image`, if `segmentation` is non-null (which implies `parts` non-null) |
| `reconstruction` | `image_to_3d` | 70 | `job_` via `JobService.create_job` | after segmentation is done or skipped. Job source: `skipped`, resource = that job |
| `part_extraction` | `part_extraction` | 17 | `pa_` with `segmentation_id = outputs.segmentation_id` (null without the segmentation step) | job completed, if `parts` is non-null |
| `export` | `export` | 2 | none (records URLs) | after the last producing step |

These rows are the `WORKFLOW_STEPS: tuple[WorkflowStepDef, ...]` table of `services/workflow_service.py`
(`WorkflowStepDef(id: str, node: str, weight: float, create_child: Callable[[WorkflowRecord, WorkflowContext],
Awaitable[ChildRef | None]])`; `None` = step skipped). §4.7.2 "Extension rule" adds rows here.

Step statuses: `pending | running | done | failed | skipped | cancelled`. A finished step carries
`artifacts: [{role, url}]`:
- `text_to_image`: candidate 0 thumbnail;
- `segmentation`: overlay;
- `reconstruction`: preview;
- `part_extraction`: front preview.

The Node UI uses these as node thumbnails while the workflow is still running.

#### 4.7.4 State machine and orchestration (v1: linear, fail-fast)
```
queued ─► running (first child created / first step done) ─► completed (all steps done|skipped)
running ─► failed     (child failed or child creation failed; error.code = "step_failed",
                       details = {step, child_kind, child_id, child_error})   outputs already produced stay set
queued|running ─► cancelled   (POST cancel: cancel the active child via its service; pending steps → skipped)
startup: queued|running ─► failed (interrupted); children are recovered by their own stores
```
- **`step_failed` details** (`error.details`, typed in `schemas/workflows.py` as `StepFailedDetails`):
  `{step: WorkflowStepId, child_kind: "image_generation" | "segmentation" | "job" | "part_analysis",
  child_id: str | null, child_error: {code: str, message: str, details: Any | null}}`. `child_error` has the child's
  `JobError` shape [V `models/job.py:70-73`, `schemas/jobs.py:85-88`] and is copied from the failed child's
  `error`; it is never null when `error.code == "step_failed"`. When the child could not be created, `child_id` is
  null and `child_error` is the creation error (`{code, message, details}` of the refused create call).
- **At creation (all-or-nothing; no child is created on failure):**
  - The source must exist (404). An empty set is 422.
  - `validate_against_capabilities` runs on `reconstruction.settings`. It is the public function in
    `services/generation_settings.py`, used read-only.
  - Capability checks return 503.
  - The run queue and the job queue must both have capacity, otherwise 429 `queue_full`.
- **Later:** if a child cannot be created (429 at that moment), the step fails with `queue_full` and the workflow
  fails. There is no "waiting" retry state in v1.
- **`WorkflowService.advance(wf_id)` is idempotent:**
  1. Take the per-workflow `asyncio.Lock`.
  2. Read each child's state from its service.
  3. Map child states onto steps.
  4. Create the next child once its predecessors are `done`.
  5. Persist atomically and publish `workflow.updated`.
- **What triggers `advance`:**
  - `EventBus.subscribe(predicate)` for `image_generation.updated`, `segmentation.updated`, `job.updated` and
    `part_analysis.updated` whose id is in the active-children index;
  - a reconciliation sweep every `workflow_sweep_interval_s` (5 s) over active workflows only, which guards against
    dropped events (`core/events.py` bounded queues [V]);
  - once at startup.
- **Delete:**
  - `DELETE /workflows/{wf}` removes only the workflow record. It returns 409 while the workflow is active.
  - Children remain as normal history.
  - `steps[].resource.exists` is computed at read time.
- **Percent:** `Σ(step weight × child percent) / Σ weights of the present steps`. Child percent is the run's or job's
  `progress.percent`, and 100 for steps that are done or skipped.

#### 4.7.5 `WorkflowOut`
```json
{ "id": "wf_…", "name": null, "status": "running", "created_at": "…Z", "started_at": "…Z", "finished_at": null,
  "request": { /* normalized WorkflowRequest without graph/layout */ }, "graph_stored": true,
  "current_step": "reconstruction",
  "progress": {"percent": 41.0, "message": "Generating geometry", "updated_at": "…Z"},
  "steps": [
    {"id": "text_to_image", "node": "prompt", "node_id": "n_prompt", "status": "done", "message": null, "progress": 100.0,
     "warning_count": 1,
     "resource": {"kind": "image_generation", "id": "ig_…", "exists": true},
     "artifacts": [{"role": "candidate", "url": "/api/v1/image-generations/ig_…/candidates/0/thumbnail"}]},
    {"id": "import_image", "node": "input_image", "node_id": null, "status": "done",
     "resource": {"kind": "reference_image", "id": "img_…", "reference_set_id": "rs_…", "exists": true}, "artifacts": []},
    {"id": "segmentation", "node": "segmentation", "node_id": "n_seg", "status": "done",
     "resource": {"kind": "segmentation", "id": "sg_…", "exists": true},
     "artifacts": [{"role": "overlay", "url": "/api/v1/segmentations/sg_…/overlay"}]},
    {"id": "reconstruction", "node": "image_to_3d", "node_id": "n_recon", "status": "running", "progress": 38.5,
     "resource": {"kind": "job", "id": "job_…", "exists": true}, "artifacts": []},
    {"id": "part_extraction", "node": "part_extraction", "node_id": "n_parts", "status": "pending", "resource": null, "artifacts": []},
    {"id": "export", "node": "export", "node_id": "n_export", "status": "pending", "resource": null, "artifacts": []} ],
  "outputs": {"reference_set_id": "rs_…", "image_id": "img_…", "segmentation_id": "sg_…", "job_id": "job_…",
              "part_analysis_id": null, "model_url": null, "parts_model_url": null, "manifest_url": null},
  "error": null }
```
`node_id` is the graph node that owns the step. It is null for steps that are implicit in a simple-form request.
`steps[].progress` is 0..100 (the child's `progress.percent`); `RunStep.progress` inside a run is 0..1 (UI BR8).
`steps[].warning_count: int = 0` (additive response field, `WorkflowStepOut`) = `len(child.warnings)` of the step's
`ig_`/`sg_`/`pa_` child, refreshed by `advance` whenever it reads the child state (§4.7.4 step 2); it is always 0
for `import_image`, `reconstruction` (jobs carry no warnings) and `export` steps and for steps without a resource.
The Node UI shows a `done` step with `warning_count > 0` in its `warning` state (UI §4.3.9).

### 4.8 GPU scheduling and VRAM sequencing
**One executor.** Everything runs FIFO on `ThreadPoolExecutor(1, "gpu-worker")` [V `generation_worker.py:85`]:
engine load, job generation, unload, and every auxiliary GPU phase (`submit_gpu_task`). CPU phases of runs use the
separate `parts-cpu` thread.

Timeline of a prompt → 3D + parts workflow (default `low_vram=true`, so TRELLIS holds no VRAM between runs [V
`engine.py:312-326`]):
```
gpu-worker:            [ig_: Z-Image to(cuda) per component → 9 steps per candidate → to(cpu), empty_cache]
                       [sg_ (optional): GDINO/Florence/SAM2 to(cuda) → run → to(cpu)]
                       [job_: TRELLIS.2 (unchanged; per-stage CPU⇄GPU moves; reseeds at generation.py:202)]
                       [pa_ gpu: GDINO/SAM2 (internal pass) → lifter camera fit + votes + extra views → offload]
                       [pa_ gpu: previews]
parts-cpu:  [ig_ load_model: weights fully read into RAM (cold ~5 min, once)]   [pa_ prepare] … [assign/partition/export]
event loop: workflow advance (no GPU), candidate import (ReferenceService), job creation (JobService),
            RunWorker jobs-first gate (a GPU phase waits while a job is pending or running, ≤ run_max_defer_s)
```
Every auxiliary GPU phase runs inside `torch_globals_guard()` (§4.9).
**VRAM rules (`inference/providers/registry.py` + `vram.py`, S2).**
1. **Static check at creation (503 `capability_unavailable`, `reason_code: insufficient_vram`):**
   `spec.vram_peak_gb > aux_vram_budget_gb − (engine_resident_vram_gb if settings.low_vram is False and the engine
   is ready else 0)`.
   - Defaults: budget 28, resident 17.
   - With `low_vram=false`, Z-Image-Turbo (24 GB) is refused honestly, because there is no engine offload path in
     this milestone (D7).
   - `/features` reports the same reason.
2. **Live check at the start of every GPU phase, on the GPU thread:** `free_mb` comes from
   `inference.device.query_memory(import_torch(), index)` [V :98]. If
   `free_mb < (spec.vram_peak_gb + aux_vram_margin_gb)·1024`, the run fails with `insufficient_vram` (503-class run
   error) instead of an OOM. The default margin is 1.5 GB.
3. **Residency:**
   - Providers keep weights in CPU RAM between runs (`aux_keep_models_in_ram=true`; RAM 130 GB [V]). They move to
     `cuda` only inside a GPU phase.
   - The phase's `finally` calls `offload()`: weights to CPU, then `torch.cuda.empty_cache()`.
   - Idle unload is off by default (`aux_model_idle_unload_s=0`), because a Z-Image cold load costs ~337 s [M].
4. **Warm-up:** `POST /models/{id}/load` runs `provider.load()` with `asyncio.to_thread` (CPU only, under the
   provider lock; this pays the disk read before the user's first prompt), then queues `warm()` (one
   `to(cuda)`/`to(cpu)` cycle, inside `torch_globals_guard()`) as a GPU task behind the jobs-first gate. While either
   runs, the state is `loading`; `features.updated` fires on every state change (§6.1).
5. **OOM:** the run fails with `out_of_memory` (via `classify_exception`). `offload()` and `empty_cache()` still run
   in `finally`. The next job is unaffected.

### 4.9 Seed parity
- TRELLIS seeds with `torch.manual_seed(seed)` at the start of conditioning, inside `engine.generate`
  (`generation.py:202` [V]). Auxiliary work never runs inside `engine.generate`; it is always a separate GPU task
  before or after.
- So whatever an auxiliary task does to the global torch RNG, the next job reseeds.
- Reseeding is not enough on its own: TRELLIS draws its noise with global-default-dtype CPU `torch.randn(...)`
  (`trellis2_image_to_3d.py:208,254,299,343,412` [V]), and torch globals that a diffusers/transformers provider may
  leave changed survive `manual_seed` on the shared `gpu-worker` thread. Therefore
  `inference/providers/vram.py::torch_globals_guard()` (a context manager; torch imported inside the function)
  snapshots and restores, around **every** auxiliary GPU callable (`RunWorker._gpu_phase`, `warm()`):
  - `torch.get_default_dtype()`;
  - `torch.backends.cudnn.benchmark`, `.deterministic`, `.allow_tf32`;
  - `torch.backends.cuda.matmul.allow_tf32`; `torch.get_float32_matmul_precision()`;
  - `torch.backends.cuda.matmul.allow_fp16_reduced_precision_reduction` and
    `torch.backends.cuda.matmul.allow_bf16_reduced_precision_reduction` [M torch 2.14.1: both readable/writable
    attributes, default `True`];
  - `torch.get_num_threads()` (restored with `torch.set_num_threads(n)`; process-wide intra-op pool) [M default 12];
  - the flash, mem-efficient and math SDP enable flags (`torch.backends.cuda.{flash,mem_efficient,math}_sdp_enabled()`);
  - `torch.are_deterministic_algorithms_enabled()` (and its `warn_only` flag);
  - `torch.is_grad_enabled()` (thread-local, but the thread is shared).
  On exit the guard compares each global with its snapshot, restores the changed ones and, when at least one was
  changed, logs `logger.info("[torch_guard] restored %s", names)` (`logger = logging.getLogger(__name__)` →
  `inference.providers.vram`; `names` = sorted list of the dotted names above, e.g.
  `["torch.backends.cuda.matmul.allow_tf32", "torch.get_default_dtype"]`). Nothing is logged when nothing changed.
  This makes globals that real diffusers/transformers providers flip observable, which the CPU fake-pipeline test
  cannot show: the S4 and S5 smokes and the S10 `run_parts_smoke.py` copy these records into
  `test/outputs/part_aware/torch_guard.json` = `{"s4_zimage" | "s5_segmentation" | "s10_parts": [{"ts": "…Z",
  "restored": [names]}]}` (each smoke rewrites only its own key; §10.2). A global that real providers flip but that is
  not in this list stays invisible; adding one is a one-line change plus a `test_aux_isolation.py` case.
  `ProviderRegistry.guard()` returns it. With that, same-seed reconstructions are unchanged.
- The T2I provider uses a private `torch.Generator(device).manual_seed(seed_i)` per candidate. Segmentation and
  lifting are deterministic inference, with no sampling.
- `test/engine/test_generation_parity.py` stays unchanged and green. The new `test/engine/test_aux_isolation.py`
  (S2; torch on CPU, no CUDA needed) runs that file's fake pipeline twice with the same seed, the second time after a
  `torch_globals_guard()`-wrapped callable submitted through `submit_gpu_task` that flips every guarded global
  (including `torch.set_default_dtype(torch.float64)`, both reduced-precision flags set to `False` and
  `torch.set_num_threads(1)`). It asserts identical call sequences and RNG fingerprints (same `_rng_fingerprint`
  recipe), that every global equals its snapshot, and that the `[torch_guard] restored` record lists exactly the
  flipped names; a negative control without the guard shows the fingerprint change.

### 4.10 Timings (RTX 5090, this machine)
| Step | Cold | Warm | Source |
|---|---|---|---|
| Z-Image-Turbo load | 30 s (`from_pretrained`) + 307 s (first `.to("cuda")`, disk-bound: fp32 transformer on E:) [M `DECISIONS.md`]. Redesigned: the disk read moves into the CPU `load_model` step (~5 min, `parts-cpu`), the first GPU transfer then < 30 s [A, S4 smoke] | 0 (resident) | [M] / [A] |
| Z-Image-Turbo generate 896×1152, 9 steps | 16.7 s | 3.6 s; peak VRAM 23.3 GB | [M] |
| GDINO-B + SAM 2.1-L (+ Florence-2-L) | first call 55 s / 15 s (CUDA warm-up) | ~1–3 s per image; peak 2.1 GB | [M] / [A] |
| Image-to-3D (TRELLIS.2, 1536 res, 2 M faces) | – | 133–579 s (export 46–66 %) | [M] generation map §1.2 |
| `pa_` `components` (CPU, 2 M faces) | – | 15–60 s | [A] (S3a acceptance: < 60 s) |
| `pa_` semantic, `views=8` | + segmentation warm-up if cold | camera fit 3–10 s, extra views 15–40 s, CPU 30–60 s ⇒ about 1–2 min total | [A] measured in the S10 acceptance |
| On-demand per-part GLB | – | 1–5 s each (writes the atlas bytes) | [A] |

### 4.11 Failure and cancellation semantics
| Situation | Effect |
|---|---|
| `ig_` fails or is cancelled | The run is `failed`/`cancelled`. No reference image is created. A workflow fails at `text_to_image` |
| `sg_` fails | The run fails. A workflow fails at `segmentation` (fail-fast). The user can rerun without the segmentation node; `pa_` still runs its internal pass |
| Job fails or is cancelled | Existing semantics, unchanged. The workflow fails or is cancelled. `pa_` creation is refused (409) |
| `pa_` fails | The job, `model.glb` and `job.json` are untouched (I2). Nothing is published; staging is removed. The workflow fails with `outputs.model_url` still set |
| Segmentation degraded inside `pa_` (no parser, rejected fit, unverified frame) | `result.status = "partial"`, with warnings |
| Job deleted during `pa_` | The delete hook cancels the analysis and waits ≤ 30 s. If retention deleted the job without hooks, publish fails with `job_deleted`, and `RunStore.save` cannot recreate the folder (`mkdir(parents=False)` raises `FileNotFoundError` → `job_deleted`, §4.3) |
| Watchdog expiry (`run_timeout`) | Counted over active phase time only. The token is cancelled and the worker awaits the shielded GPU future before it sets `failed` and removes `work_dir` (§4.3 step 6) |
| Server shutdown | `part_aware.stop()` cancels the active run, awaits its shielded GPU future (≤ timeout), and marks queued runs and active workflows `interrupted`. Then the generation worker stops |
| OOM in an auxiliary step | That run fails with `out_of_memory`. Weights are offloaded in `finally`. Jobs are unaffected |

---------------------------------------------------------------------------------------------------

## 5. Provider seams and the model registry

### 5.1 Torch-free types (`inference/providers/base.py`, S1)
```python
ModelKind = Literal["text_to_image", "segmentation", "part_extraction",
                    "segmentation_3d", "completion", "part_generation"]          # last three: future, unsupported now
CommercialUse = Literal["allowed", "allowed_with_conditions", "revenue_capped", "non_commercial",
                        "research_only", "unknown"]
ModelUiState = Literal["not_installed", "installed", "loading", "loaded", "error", "unavailable"]
ReasonCode = Literal["ok", "unsupported", "disabled", "not_approved", "license_blocked", "vendored_source_missing",
                     "missing_dependency", "not_installed", "incompatible", "insufficient_vram", "error",
                     "not_implemented", "engine_unavailable"]
# One vocabulary for every `code` in /features (pipeline, profiles, strategies, nodes) and ModelOut.reason_code.
# not_implemented: reserved strategy or an executor module not merged yet; engine_unavailable: image_to_3d gating.

@dataclass(frozen=True, slots=True)
class LicenseInfo:
    id: str; name: str; url: str; commercial: CommercialUse
    gated: bool = False; requires_acceptance: bool = False
    attribution: str | None = None; conditions: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class DependencySpec:
    distribution: str            # importlib.metadata name, e.g. "diffusers"
    requirement: str             # e.g. "==0.39.0" (packaging.specifiers; the package is never imported)

@dataclass(frozen=True, slots=True)
class ModelSpec:
    id: str                      # ^[a-z0-9_]{2,40}$
    kind: ModelKind
    roles: tuple[str, ...]       # "generator" | "detector" | "grounder" | "refiner" | "parser" | "lifter" | future roles
    display_name: str
    provider: str | None         # implementation key, resolved lazily; None = unsupported in this build
    repo_id: str | None
    revision: str | None         # pinned; segmentation revisions are read from backend/.model/seg/download_manifest.json
    local_subdir: str | None     # relative to settings.aux_model_root (backend/.model)
    required_files: tuple[str, ...]
    size_bytes: int | None
    vram_peak_gb: float
    ram_gb: float
    cold_load_s: float | None    # measured where known
    license: LicenseInfo
    dependencies: tuple[DependencySpec, ...] = ()
    vendored_source: str | None = None        # e.g. "third_party/see-through"
    approval_required: bool = True
    default_approved: bool = False            # True for the DECISIONS.md models (Q1)
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    unsupported_reason: str | None = None
    hidden_by_default: bool = False
    notes: str | None = None

@dataclass(frozen=True, slots=True)
class ProviderStatus:
    model_id: str; state: ModelUiState; reason_code: ReasonCode; reason: str | None
    revision: str | None = None; loaded_device: Literal["cpu", "cuda"] | None = None
    last_used_at: datetime | None = None

class ModelProvider(ABC):                      # load() on a CPU thread (never touches CUDA); warm/offload/unload/run
    spec: ModelSpec                             # only on the GPU thread; one threading.Lock per provider serialises all
    @abstractmethod
    def status(self) -> ProviderStatus: ...             # cheap, thread-safe, never blocks on the GPU
    @abstractmethod
    def load(self, cancel: CancellationToken) -> None: ...   # weights FULLY materialised in CPU RAM (idempotent; no CUDA)
    def warm(self, cancel: CancellationToken) -> None: ...   # optional one to(cuda)/to(cpu) cycle (default no-op)
    @abstractmethod
    def offload(self) -> None: ...                      # weights -> CPU; torch.cuda.empty_cache()
    @abstractmethod
    def unload(self) -> None: ...                       # drop weights; gc.collect(); empty_cache()

class StepProgress(Protocol):
    def __call__(self, fraction: float, message: str | None = None) -> None: ...
```

### 5.2 Role ABCs (torch-free; only files and plain dataclasses cross the seam, never tensors)
```python
# inference/imagegen/base.py
@dataclass(frozen=True, slots=True)
class ImageGenRequest:
    run_id: str; prompt: str; negative_prompt: str | None; width: int; height: int
    steps: int; guidance: float; seeds: tuple[int, ...]; output_dir: Path; device: str
@dataclass(slots=True)
class ImageGenOutput:
    paths: list[Path]; seeds: list[int]; timings: dict[str, float]; metadata: dict[str, Any]
class TextToImageProvider(ModelProvider):
    @abstractmethod
    def generate(self, request: ImageGenRequest, progress: StepProgress, cancel: CancellationToken) -> ImageGenOutput: ...

# inference/segmentation/base.py
@dataclass(frozen=True, slots=True)
class Box2D: class_id: str; xyxy: tuple[float, float, float, float]; score: float; source: str; phrase: str
@dataclass(frozen=True, slots=True)
class Mask2D: class_id: str; path: Path; score: float; bbox: tuple[int, int, int, int]; source: str; instance: int
class Detector2D(ModelProvider):      # Grounding DINO: phrases -> boxes
    @abstractmethod
    def detect(self, image: Path, phrases: Mapping[str, tuple[str, ...]], *, box_threshold: float,
               text_threshold: float, cancel: CancellationToken) -> list[Box2D]: ...
class Grounder2D(ModelProvider):      # Florence-2: caption, phrase grounding
    @abstractmethod
    def caption(self, image: Path, cancel: CancellationToken) -> str: ...
    @abstractmethod
    def ground(self, image: Path, phrases: Mapping[str, tuple[str, ...]], cancel: CancellationToken) -> list[Box2D]: ...
class MaskRefiner(ModelProvider):     # SAM 2.1: boxes (+ points) -> masks
    @abstractmethod
    def refine(self, image: Path, boxes: Sequence[Box2D], out_dir: Path, cancel: CancellationToken) -> list[Mask2D]: ...
class Parser2D(ModelProvider):        # See-through l2d (anime), Sapiens2-seg (realistic): closed-set dense masks
    label_map: Mapping[str, str]      # source class -> taxonomy id (segmentation research §7.2)
    @abstractmethod
    def parse(self, image: Path, out_dir: Path, cancel: CancellationToken) -> list[Mask2D]: ...

# inference/parts/base.py
@dataclass(frozen=True, slots=True)
class MeshArrays:
    positions: np.ndarray; faces: np.ndarray; uv: np.ndarray | None; atlas_rgb: np.ndarray | None   # asset frame
@dataclass(frozen=True, slots=True)
class CameraSpec: yaw_deg: float; pitch_deg: float; fov_deg: float; distance: float
@dataclass(frozen=True, slots=True)
class CameraFit(CameraSpec):
    crop: tuple[float, float, float, float]   # normalized square crop that makes the render match the conditioning framing
    iou: float; accepted: bool; method: str; candidates_tested: int
@dataclass(frozen=True, slots=True)
class CameraGrid:
    yaws: tuple[float, ...]; pitches: tuple[float, ...]; fovs: tuple[float, ...]
    refine_top_k: int = 5; refine_range_deg: float = 5.0; refine_step_deg: float = 1.0; accept_iou: float = 0.6
@dataclass(slots=True)
class RasterOut: face_id: np.ndarray; depth: np.ndarray; cos: np.ndarray     # int32 (-1 = none), float32, float32
class LabelLifter(ModelProvider):     # nvdiffrast; no weights; approval_required=False
    @abstractmethod
    def upload(self, mesh: MeshArrays) -> MeshHandle: ...
    @abstractmethod
    def release(self, handle: MeshHandle) -> None: ...
    @abstractmethod
    def fit_camera(self, handle: MeshHandle, foreground: np.ndarray, grid: CameraGrid,
                   cancel: CancellationToken) -> CameraFit: ...
    @abstractmethod
    def raster(self, handle: MeshHandle, camera: CameraSpec, size: tuple[int, int],
               crop: tuple[float, float, float, float] | None) -> RasterOut: ...
    @abstractmethod
    def render_textured(self, handle: MeshHandle, camera: CameraSpec, size: tuple[int, int]) -> np.ndarray: ...
    @abstractmethod
    def render_labels(self, handle: MeshHandle, camera: CameraSpec, size: tuple[int, int],
                      face_colors: np.ndarray) -> np.ndarray: ...

# Future seams: interfaces only, zero implementations; catalog entries report "unsupported"
class MeshSegmenter3D(ModelProvider):
    def segment_mesh(self, mesh: MeshArrays, cancel: CancellationToken) -> np.ndarray: ...   # per-face labels
class AmodalCompleter(ModelProvider):
    def complete(self, part: PartGeometry, cancel: CancellationToken) -> Path: ...
class PartGenerator(ModelProvider):
    def generate_parts(self, image: Path, masks: Sequence[Mask2D], cancel: CancellationToken) -> list[Path]: ...
```
`inference/parts/cameras.py` (S7) re-implements `extrinsics_look_at` and `intrinsics_from_fov_xy`. `utils3d` is not
installed [V `trellis2_internals.md` §3.2].

### 5.3 State resolution (`inference/providers/registry.py`, S2)
The first rule that matches wins. Results are cached for 5 s and invalidated on load, unload or error.
1. `spec.provider is None` → `unavailable / unsupported` (reason = `unsupported_reason`).
2. The master flag or the flag for this kind is off → `unavailable / disabled`.
3. `approval_required` and the id is not in `effective_approved_models` → `unavailable / not_approved`.
   `effective_approved_models` is `settings.approved_models` when set, otherwise the ids with
   `default_approved=True`.
4. The licence is `non_commercial`, `research_only` or `unknown`, `approval_required` is true, and
   `allow_restricted_licenses` is false → `unavailable / license_blocked`. The lifter is exempt: it has
   `approval_required=False`, and nvdiffrast is already required by export.
5. `vendored_source` has no `__init__.py` or entry module → `unavailable / vendored_source_missing`. A vendored tree
   whose modules import their own top-level packages dynamically (`importlib.import_module("<vendored name>…")` or
   `__import__` with a vendored name, found by the AST scan of `vendor_import.py`, §5.6) →
   `unavailable / incompatible`.
6. A `DependencySpec` is absent or fails its specifier (`importlib.metadata.version` + `packaging.specifiers`) →
   `unavailable / missing_dependency`. Nothing is imported in this check.
7. `required_files` do not all match under `model_paths.get(id) or aux_model_root/local_subdir` →
   `not_installed / not_installed`.
8. Any text-encoder `config.json` with `transformers_version` ≥ 5 → `unavailable / incompatible` (T2I research §2.1).
9. The static VRAM check of §4.8 fails → `unavailable / insufficient_vram`.
10. The live provider state is `loading`, `loaded` or `error` → that state. Otherwise → `installed / ok`.

`usable(id) = state in {installed, loading, loaded}`.

```python
class ProviderRegistry:
    def __init__(self, settings: Settings, catalog: Sequence[ModelSpec], *, engine: InferenceEngine,
                 overrides: PartProviderOverrides | None = None, on_change: Callable[[], None] | None = None) -> None
    def specs(self, kind: ModelKind | None = None) -> list[ModelSpec]
    def status(self, model_id: str) -> ProviderStatus
    def statuses(self, kind: ModelKind | None = None) -> list[ProviderStatus]
    def usable(self, model_id: str) -> bool
    def provider(self, model_id: str) -> ModelProvider              # lazy import; ModelUnavailableError if not usable.
                                                                    # Calls configure_hf_environment(settings.hf_home) first
                                                                    # and asserts os.environ["HF_HOME"] == str(hf_home)
    def guard(self) -> ContextManager[None]                         # torch_globals_guard() (§4.9); GPU thread
    def resolve_role(self, role: str, preferred: Sequence[str] = ()) -> ModelProvider | None
    def default_model(self, kind: ModelKind) -> str | None          # settings.t2i_default_model or the first usable
    def check_vram_static(self, model_id: str) -> None             # CapabilityUnavailableError(insufficient_vram)
    def ensure_vram(self, model_ids: Sequence[str]) -> None        # GPU thread; live check (§4.8); InsufficientVramError
    def offload(self, model_ids: Sequence[str]) -> None            # GPU thread
    def idle_providers(self, now: datetime) -> list[ModelProvider]
```
**HF environment order.** `configure_hf_environment` "must run before huggingface_hub/transformers import"
[V `environment.py:195-213`], but today it runs only inside `prepare_runtime` at engine load
[V `environment.py:565`, `engine.py:199-201`]. With `preload_model=false`, or when the first action is a prompt, a
warm-up or a segmentation, a provider would import transformers first; transformers would then fix
`HF_MODULES_CACHE` in the user's home (BiRefNet uses `trust_remote_code=True`,
`third_party/TRELLIS.2/trellis2/pipelines/rembg/BiRefNet.py:10-11` [V]) and the engine report would gain
"huggingface_hub was imported before HF_HOME was configured". `provider()` therefore calls the torch-free
`configure_hf_environment(settings.hf_home)` before every lazy import. It sets exactly the values `prepare_runtime`
sets later (idempotent; the later call returns no warning), so the image-to-3D path is unchanged.
`enable_hf_offline()` is **not** called early: TRELLIS enables offline mode only after its aux models are found
locally and may download them otherwise [V `environment.py:510-525`, `loader.py:250-281`]; providers stay offline
through `local_files_only=True`.

### 5.4 Catalog v1 (`inference/providers/catalog.py`, S2)
It follows `DECISIONS.md`. The six chosen models are `default_approved=True` (Q1).

| id | kind / roles | repo @ revision | local_subdir (under `backend/.model/`) | size | VRAM peak | licence (commercial) | deps / vendored | provider | state on this machine today |
|---|---|---|---|---|---|---|---|---|---|
| `z_image_turbo` | text_to_image / generator | `Tongyi-MAI/Z-Image-Turbo` @ `f332072aa78be7aecdf3ee76d5c247082da564a6` | `t2i/Z-Image-Turbo` [V] | 32.85 GB [V] | **24.0** (measured 23.3 [M]); `ram_gb` 21 [A]; `cold_load_s` 337 [M] | Apache-2.0 (allowed, not gated) | `diffusers==0.39.0`, `accelerate==1.15.0` [V installed] | `zimage` | installed |
| `grounding_dino_base` | segmentation / detector | `IDEA-Research/grounding-dino-base` @ `12bdfa3120f3e7ec7b434d90674b3396eccf88eb` [V manifest] | `seg/grounding-dino-base` | 0.93 GB | 2.0 (stack peak 2.1 [M]) | Apache-2.0 | `transformers==4.57.6` | `hf_grounding_dino` | installed |
| `sam2_1_hiera_large` | segmentation / refiner | `facebook/sam2.1-hiera-large` @ `665f8e2ad61cf5f53d65644ff27c8ee525124610` | `seg/sam2.1-hiera-large` | 0.90 GB | 2.0 | Apache-2.0 | `transformers==4.57.6` (`Sam2Model`) | `hf_sam2` | installed |
| `florence2_large` | segmentation / grounder | `florence-community/Florence-2-large` @ `4271c66b88cdbc05735372ec13b2360108de5317` | `seg/Florence-2-large` | 1.56 GB | 3.0 | MIT | `transformers==4.57.6` (`Florence2ForConditionalGeneration`, fp16) | `hf_florence2` | installed |
| `see_through_l2d` | segmentation / parser (anime_character) | `24yearsold/l2d_sam_iter2` @ `0b0608310fb7a89e32ecd7397147a249540cf1e7` | `seg/l2d_sam_iter2` (`checkpoint-18000.pt`, `torch.load(weights_only=True)`) | 1.29 GB | 4.0 [A] | Apache-2.0 | vendored `third_party/see-through` | `vendored_see_through` | unavailable / `vendored_source_missing` [V] |
| `sapiens2_seg_0_4b` | segmentation / parser (realistic_human) | `facebook/sapiens2-seg-0.4b` @ `449b3c5335e6722bb94990abdd1aa6e612432f22` | `seg/sapiens2-seg-0.4b` (`model.safetensors`; the byte-identical `sapiens2_0.4b_seg.safetensors` is ignored) | 1.63 GB used (3.25 GB on disk) | 3.0 [A] | `LicenseInfo(id="sapiens2-license", name="Sapiens2 License", url="https://huggingface.co/facebook/sapiens2-seg-0.4b", commercial="allowed_with_conditions", conditions=("No biometric identification", "No deepfakes or impersonation", "No military use"))` — the UI shows `license.conditions` (Q4) | vendored `third_party/sapiens2` | `vendored_sapiens2` | unavailable / `vendored_source_missing` [V] |
| `raster_lifter` | part_extraction / lifter | – | – | – | 4.0 [A] | NVIDIA Source Code License, `non_commercial` (already required by TRELLIS export; exempt) | `nvdiffrast==0.4.0`, `cumesh==0.0.1` [V] | `nvdiffrast_raster` | installed |
| `p3sam` | segmentation_3d / segmenter | `tencent/Hunyuan3D-Part` (p3sam, 451 MB) | – | 0.45 GB | – | Tencent Hunyuan 3D-Part Community Licence | needs spconv, torch_scatter (no cp314/cu13 wheels) | `None` | unavailable / `unsupported` (hidden) |
| `partfield` | segmentation_3d / segmenter | `nvidia/PartField` | – | 1.24 GB | – | NVIDIA License (non-commercial) | torch_scatter, open3d, pymeshlab | `None` | unavailable / `unsupported` (hidden) |
| `holopart` | completion / completer | `VAST-AI/HoloPart` | – | 3.83 GB | – | MIT | diso, torch_cluster, numpy 1.x pin | `None` | unavailable / `unsupported` (hidden) |
| `x_part` | part_generation / generator | `tencent/Hunyuan3D-Part` (X-Part) | – | 9.0 GB | – | Tencent licence | Sonata/spconv stack | `None` | unavailable / `unsupported` (hidden) |

- Segmentation revisions are read at registry start from `backend/.model/seg/download_manifest.json` [V], keyed by
  folder name. A mismatch with the catalog value is a startup warning, not an error.
- Not in the catalog: FLUX.2-klein-4B, Animagine XL 4.0 and SAM 3 (not chosen, `DECISIONS.md` §1–2), and
  non-commercial face/clothes parsers. Adding a model later takes one `ModelSpec` plus one provider module.
- `/models` never returns absolute paths. `local_path` is relative to `backend/`.

### 5.5 What each approved subset yields
| Approved and usable | `prompt_to_3d` | Segmentation profiles | `pa_` strategies | Character output |
|---|---|---|---|---|
| nothing (`IMG3D_APPROVED_MODELS=[]`) | unavailable (`not_approved`) | none | `components` | base + `component_###` shells, honest and unnamed |
| Z-Image-Turbo only | available | none | `components` | the same, for prompt and image jobs |
| + GDINO + SAM 2.1 (+ Florence-2) — **today** | per T2I | `baseline` | `components`, `semantic_regions`, `semantic_parts` | hair, clothes, accessories and objects physical; eyes physical when ≥ `min_part_faces`; brows and lashes deferred (heuristics); face and body regions |
| + See-through (after S6; Q4 approved) | | + `anime` | same | denser anime hair/clothing/face coverage |
| + Sapiens2 (after S6; Q4 approved) | | + `realistic` | same | limbs, hands and clothing regions for realistic humans |
| lifter not importable | per T2I | `sg_` still works | `components` only | geometry-only parts |

### 5.6 Provider implementation rules (each module states them in its docstring; enforced by review)
- `from_pretrained(local_dir, local_files_only=True)` is always used.
  - Never `trust_remote_code`. Never `custom_pipeline`. Never a network call. Anonymous Hub use needs
    `token=False` (`DECISIONS.md` gotcha), but providers never touch the Hub.
- Z-Image-Turbo:
  - `ZImagePipeline.from_pretrained(local, torch_dtype=torch.bfloat16, local_files_only=True,
    low_cpu_mem_usage=False)` in `load()` on a CPU thread, so the weights are fully read into RAM there [M probe
    works with the default; `low_cpu_mem_usage=False` / explicit materialisation is A, measured in S4]. If diffusers
    0.39 rejects the flag for a component, `load()` instead forces every parameter and buffer resident
    (`p.data = p.data.contiguous().clone()`), still on the CPU.
  - `num_inference_steps=9`, `guidance_scale=0.0`.
  - `generate()` loops over the candidates with batch size 1, checks the token between candidates and calls
    `torch.cuda.empty_cache()` after each VAE decode (§4.4.4).
  - `callback_on_step_end` drives progress. Cancel sets `pipe._interrupt = True` and raises `JobCancelledError`.
- Grounding DINO (`inference/segmentation/gdino.py`): `detect()` packs the phrase vocabulary into batches of ≤ 200
  tokenizer tokens (`". "` separators counted) and runs one forward pass per batch; grounding-dino-base truncates at
  `max_text_len = 256` [V `backend/.model/seg/grounding-dino-base/config.json`], and the whole taxonomy alias list
  (§2.3) is far longer than the 9 probe phrases. It logs the batch count and keeps it in
  `self.last_batch_count: int` and `self.last_class_ids: frozenset[str]` (every class id sent) for the smoke test.
- Seeds come from a private `torch.Generator(device).manual_seed(seed_i)`. The global torch RNG is never seeded.
- `device = settings.device`. Weights go to CPU in `finally`.
- Florence-2 runs in fp16. GDINO and SAM 2.1 use the transformers 4.57.6 native classes
  (`AutoModelForZeroShotObjectDetection`, `Sam2Model`/`Sam2Processor`) [M probe].
- See-through loads with `torch.load(path, weights_only=True)`. Their `requirements*.txt` are never installed.
- **Vendored imports never touch `sys.path`.** Img3D's own top-level packages have generic names (`models`, `utils`,
  `config`, `core`, `api`, `schemas`, `services`, `storage`, `processing`, `pipelines`, `inference`, `parts`) and
  sit first on `sys.path` [V `backend/main.py:26-35`]; the TRELLIS.2 root at `sys.path[0]` exposes `configs`,
  `assets`, `data_toolkit` [V `environment.py:188-192`, `backend/third_party/TRELLIS.2/`]. A vendored
  `import utils` / `from models import …` would bind to the wrong package, and a prepended vendored root could shadow
  Img3D modules. The submodules are pinned upstream commits, so their imports cannot be edited. Therefore
  `inference/providers/vendor_import.py` (S6; stdlib only) provides:
  ```python
  class VendoredPackage:
      def __init__(self, name: str, root: Path) -> None          # name: "see_through" | "sapiens2"
      def top_level_names(self) -> frozenset[str]                # packages/modules directly under root
      def scan_dynamic_imports(self) -> list[str]                # "file:line" of importlib/__import__ uses of own names
      def import_module(self, dotted: str) -> ModuleType          # e.g. "models.parser" -> img3d_vendor.see_through.models.parser
  ```
  A `sys.meta_path` finder that handles **only** the private namespace `img3d_vendor.<name>.*` loads files from
  `root` with an `importlib.abc.SourceLoader` whose `source_to_code` applies an AST `NodeTransformer`: every absolute
  `import X…` / `from X… import` with `X in top_level_names()` becomes `img3d_vendor.<name>.X…`. Nothing is written to
  disk, `sys.path` is unchanged, and `sys.modules["utils"]` / `["models"]` keep pointing under `backend/`. Registry
  step 5 (§5.3) lazily calls `scan_dynamic_imports()` when the module exists (it does not before S6 merges, when
  the sources are missing anyway).
- Providers never write outside the `output_dir` / `out_dir` they are given.

---------------------------------------------------------------------------------------------------
## 6. API contract additions (prefix `/api/v1`; all additive)

### 6.0 Conventions
- **Auth.**
  - **J** = `AuthGuard` (Bearer header only).
  - **F** = `FileAuthGuard` (header or `?token=`) [V `api/deps.py:88-101`]. It is used for every file route,
    because `<img>`, GLTFLoader and downloads need `?token=`.
- **Ids.** Every id is checked with `utils.ids.is_valid_id(value, prefix)` before any filesystem access [V
  `utils/ids.py:39`]. Prefixes: `ig`, `sg`, `pa`, `wf`. File names come from manifest whitelists or anchored regexes,
  applied before any `Path` join. A resolved path must stay inside its run directory.
- **Errors.** Every error uses the existing envelope `{error: {code, message, details, request_id}}`.
- **Flags.** A mutating route whose flag is off returns 503 `feature_disabled`. GET routes always work, so history
  stays readable.
- **Shapes.** Requests are `RequestModel` (`extra="forbid"`). Responses are `ApiModel` (`extra="ignore"`). Timestamps
  are ISO-8601 UTC `…Z`. Nested request objects accept **partial** objects: omitted fields take their per-field
  defaults, unknown keys are 422 (`extra_forbidden`) (UI BR2).
- **Lists.** Every new list route (`GET /image-generations`, `/segmentations`, `/workflows`) takes
  `?limit=1..100` (default 20), newest first; a value outside the range is 422 (UI BR5).
- **Route order.** Static routes (`/workflows/catalog`, `/workflows/validate`, `/jobs/{id}/part-analyses/latest`) are
  registered **before** their `{id}` siblings.

### 6.1 Discovery
| Method | Path | Auth | Body / query | Success | Errors |
|---|---|---|---|---|---|
| GET | `/features` | J | – | 200 `FeaturesOut` | 401 |
| GET | `/models` | J | `?kind=` (a `ModelKind`), `?include_hidden=false` | 200 `{items: ModelOut[]}` | 401; 422 bad kind |
| GET | `/models/{model_id}` | J | – | 200 `ModelOut` | 401; 404 `not_found` |
| POST | `/models/{model_id}/load` | J | – | 202 `ModelOut` (state `loading`; `load()` on a CPU thread, then `warm()` queued on the GPU thread behind the jobs-first gate, §4.8 rule 4) | 404; 409 `conflict` (already loading, or in use by the running run); 503 `model_unavailable` / `feature_disabled` / `engine_unavailable` (shutting down) |
| POST | `/models/{model_id}/unload` | J | – | 202 `ModelOut` | 404; 409 (in use by the running run); 503 `engine_unavailable` (shutting down) |

`FeaturesOut` (also the payload of `features.updated`):
```json
{ "schema": "img3d.features/1",
  "api": {"version": "1.1.0", "workflows": true, "part_analyses": true, "image_generations": true, "segmentations": true},
  "flags": {"part_aware": true, "image_generation": true, "segmentation": true, "part_analysis": true, "workflows": true,
            "mesh_tuning": false, "part_refinement": false, "persist_generation_intermediates": false},
  "pipeline": {
    "image_to_3d": {"available": true, "code": "ok", "reason": null},
    "prompt_to_3d": {"available": true, "code": "ok", "reason": null, "max_prompt_chars": 2000,
                     "presets": ["character", "object", "raw"], "styles": ["realistic", "stylized", "anime"],
                     "supports_negative_prompt": false, "default_model_id": "z_image_turbo",
                     "default_model_state": "installed", "cold_start_s": 337, "max_images": 4,
                     "sizes": [[1024, 1024], [896, 1152], [1152, 896]], "selection_modes": ["auto_first"]},
    "segmentation": {"available": true, "code": "ok", "reason": null,
                     "subjects": ["auto", "anime_character", "realistic_human", "object"],
                     "profiles": [{"id": "baseline", "available": true, "code": "ok", "reason": null},
                                  {"id": "anime", "available": false, "code": "vendored_source_missing",
                                   "reason": "See-through code is not vendored under backend/third_party/see-through"},
                                  {"id": "realistic", "available": false, "code": "vendored_source_missing",
                                   "reason": "Sapiens2 code is not vendored under backend/third_party/sapiens2"}],
                     "default_profile": "auto", "max_extra_terms": 16, "before_reconstruction": true},
    "part_extraction": {"available": true, "code": "ok", "reason": null,
                        "strategies": [{"id": "components", "label": "Geometry components", "available": true, "code": "ok", "reason": null, "requires_segmentation": false},
                                       {"id": "semantic_regions", "label": "Semantic regions", "available": true, "code": "ok", "reason": null, "requires_segmentation": true},
                                       {"id": "semantic_parts", "label": "Semantic parts", "available": true, "code": "ok", "reason": null, "requires_segmentation": true},
                                       {"id": "per_part_regeneration", "label": "Per-part regeneration", "available": false, "code": "not_implemented", "reason": "Per-part regeneration is not implemented in this milestone.", "requires_segmentation": true}],
                        "default_strategy": "semantic_parts",
                        "separable_groups": [{"id": "eyes", "label": "Eyes", "default_kind": "physical", "default_selected": true,
                                              "note": "Eye area is cut as an open surface; the hidden eyeball is not generated."},
                                             {"id": "eyebrows", "label": "Eyebrows", "default_kind": "physical_deferred", "default_selected": true,
                                              "note": "Eyebrows are painted on; geometry deferred (mask + surface region now)."},
                                             {"id": "eyelashes", "label": "Eyelashes", "default_kind": "physical_deferred", "default_selected": true,
                                              "note": "Eyelash geometry deferred; mask + lid curve only."},
                                             {"id": "hair", "label": "Hair", "default_kind": "physical", "default_selected": true,
                                              "note": "The scalp under the hair is not generated (opening recorded)."},
                                             {"id": "clothes", "label": "Clothes", "default_kind": "physical", "default_selected": true,
                                              "note": "The body under clothes is not generated (opening recorded)."},
                                             {"id": "accessories", "label": "Accessories", "default_kind": "physical", "default_selected": true, "note": null},
                                             {"id": "detachable", "label": "Detachable objects", "default_kind": "physical", "default_selected": true, "note": null}],
                        "region_groups": [{"id": "face", "label": "Face", "default_kind": "region", "default_selected": true, "note": null},
                                          {"id": "body", "label": "Body", "default_kind": "region", "default_selected": true, "note": null}],
                        "max_views": 24, "default_views": 8, "rebuild": true, "per_part_glbs": "on_demand"}},
  "workflow": {"available": true, "code": "ok", "reason": null, "catalog_url": "/api/v1/workflows/catalog",
               "catalog_etag": "\"sha256-3f1c…\"", "max_nodes": 32, "max_edges": 64, "graph_max_bytes": 65536,
               "selection_modes": ["auto_first"],
               "legacy_job_node_map": {"queued": "image_to_3d", "waiting_for_model": "image_to_3d", "preprocessing": "input_image",
                                       "conditioning": "image_to_3d", "sparse_structure": "image_to_3d", "shape": "image_to_3d",
                                       "texture": "image_to_3d", "decoding": "image_to_3d", "exporting": "export",
                                       "finalizing": "export", "done": "export"}},
  "nodes": [{"type": "input_image", "title": "Input Image", "category": "input", "available": true, "locked": false, "code": "ok", "reason": null, "hidden_by_default": false},
            {"type": "prompt", "title": "Prompt", "category": "input", "available": true, "locked": false, "code": "ok", "reason": null, "hidden_by_default": false},
            {"type": "job_result", "title": "Existing Result", "category": "input", "available": true, "locked": false, "code": "ok", "reason": null, "hidden_by_default": false},
            {"type": "segmentation", "title": "Segmentation", "category": "analyze", "available": true, "locked": false, "code": "ok", "reason": null, "hidden_by_default": false},
            {"type": "image_to_3d", "title": "Image-to-3D", "category": "generate", "available": true, "locked": false, "code": "ok", "reason": null, "hidden_by_default": false},
            {"type": "part_extraction", "title": "Part Extraction", "category": "parts", "available": true, "locked": false, "code": "ok", "reason": null, "hidden_by_default": false},
            {"type": "export", "title": "Export", "category": "output", "available": true, "locked": false, "code": "ok", "reason": null, "hidden_by_default": false},
            {"type": "part_refine", "title": "Part Refine", "category": "refine", "available": false, "locked": true, "code": "not_approved", "reason": "Paused until approved", "hidden_by_default": true},
            {"type": "mesh_tune", "title": "Mesh Tune", "category": "refine", "available": false, "locked": true, "code": "not_approved", "reason": "Paused until approved", "hidden_by_default": true},
            {"type": "eye_correction", "title": "Eye Correction", "category": "refine", "available": false, "locked": true, "code": "not_approved", "reason": "Paused until approved", "hidden_by_default": true},
            {"type": "rig_correction", "title": "Rig Correction", "category": "refine", "available": false, "locked": true, "code": "not_approved", "reason": "Paused until approved", "hidden_by_default": true},
            {"type": "segmentation_3d", "title": "3D Segmentation", "category": "parts", "available": false, "locked": false, "code": "unsupported", "reason": "Needs spconv/torch_scatter (no Python 3.14 / CUDA 13 wheels)", "hidden_by_default": true}],
  "mesh_tuning": {"enabled": false, "reason": "Paused until the part-aware backend layer is approved"},
  "part_refinement": {"enabled": false, "reason": "Paused until the part-aware backend layer is approved"},
  "models": [ /* ModelOut[]: the FULL ModelOut shape below (license.conditions, approved, kind, state, …), hidden_by_default entries excluded; each entry equals GET /models/{id} */ ],
  "taxonomy": {"version": 1, "groups": ["body", "face", "eyes", "eyebrows", "eyelashes", "hair", "clothes", "accessories", "detachable", "other"],
               "classes": [{"value": 30, "id": "hair", "group": "hair", "label": "Hair", "default_kind": "physical",
                            "attach_to": ["body.head"], "symmetric_with": null, "sided": false}]},
  "queues": {"runs": {"pending": 0, "running_id": null, "running_kind": null, "max_size": 8}},
  "limits": {"prompt_max_chars": 2000, "negative_prompt_max_chars": 1000, "extra_terms_max": 16, "views_max": 24,
             "workflow_graph_max_bytes": 65536, "max_parts": 256} }
```
The `nodes` list above is abridged. `part_completion` and `part_generation` follow the `segmentation_3d` row.

Derivation (`services/feature_service.py`, torch-free):
- Every `code` is a `ReasonCode` (§5.1); every capability entry (pipeline, profile, strategy, node) has `available`,
  `code` and `reason` (`null` when available).
- `image_to_3d.available` = `engine.status().state ∉ {error, unavailable}`. Otherwise `code` is `engine_unavailable`.
- `prompt_to_3d` needs `flags.image_generation`, a usable T2I model, the static VRAM check, and `image_to_3d`. It
  does **not** depend on `flags.workflows` (the review-first flow needs no workflow); the one-shot flow additionally
  needs `workflow.available` (§4.4.8), which clients check themselves.
- `segmentation.available` needs the flag and at least one usable profile.
  - `baseline` needs a usable detector and refiner.
  - `anime` and `realistic` need their parser, plus the baseline detector and refiner for the small classes.
- `part_extraction.available` needs `flags.part_analysis`. `components` is always available when the flag is on. The
  semantic strategies also need segmentation and a usable `raster_lifter`.
- `mesh_tuning.enabled = settings.blender_enabled and settings.mesh_tuning_enabled`.
  `part_refinement.enabled = settings.part_refinement_enabled`. Both are false by default.
- `features.updated` is debounced by 250 ms. It fires after any provider state change (including every
  `pipeline.prompt_to_3d.default_model_state` transition `installed → loading → loaded` after
  `POST /models/{id}/load`, UI BR6), any engine state change that matters to a flag, a run-queue change, or a catalog
  ETag change.
- Refine nodes (`part_refine`, `mesh_tune`, `eye_correction`, `rig_correction`) are `hidden_by_default: true`
  (Q3: hidden in all four modes; the backend is the single source).

`ModelOut`:
```json
{ "id": "z_image_turbo", "kind": "text_to_image", "roles": ["generator"], "display_name": "Z-Image-Turbo",
  "state": "installed", "reason_code": "ok", "reason": null, "approved": true, "is_default": true,
  "repo_id": "Tongyi-MAI/Z-Image-Turbo", "revision": "f332072aa78be7aecdf3ee76d5c247082da564a6",
  "size_bytes": 32850000000, "vram_estimate_gb": 24.0, "ram_gb": 21.0, "cold_load_s": 337,
  "license": {"id": "apache-2.0", "name": "Apache License 2.0", "url": "https://huggingface.co/Tongyi-MAI/Z-Image-Turbo",
              "commercial": "allowed", "gated": false, "requires_acceptance": false, "accepted": true, "conditions": []},
  "dependencies": [{"distribution": "diffusers", "requirement": "==0.39.0", "installed": "0.39.0", "ok": true}],
  "vendored_source": null, "local_path": ".model/t2i/Z-Image-Turbo",
  "install": {"supported": false, "hint": "Weights are installed manually under backend/.model/t2i/Z-Image-Turbo (DECISIONS.md)",
              "bytes_done": null, "bytes_total": null},
  "capabilities": {"sizes": [[1024, 1024], [896, 1152], [1152, 896]], "steps": {"default": 9, "min": 1, "max": 50},
                   "guidance": {"default": 0.0, "min": 0.0, "max": 0.0}, "supports_negative_prompt": false, "max_images": 4},
  "loaded_device": null, "last_used_at": null, "hidden_by_default": false }
```

### 6.2 Image generations
| Method | Path | Auth | Body / query | Success | Errors |
|---|---|---|---|---|---|
| POST | `/image-generations` | J | `ImageGenerationRequest` | 202 `ImageGenerationOut` | 422 `validation_error` (incl. `unsupported_by_model`, `invalid_characters`); 503 `feature_disabled`, `capability_unavailable` (no usable T2I, or `insufficient_vram`), `model_unavailable` (explicit model not usable), `engine_unavailable` (shutting down); 429 `queue_full` |
| GET | `/image-generations` | J | `?limit=1..100 (20)&status=` | 200 `{items}`, newest first | 422 bad status |
| GET | `/image-generations/{ig_id}` | J | – | 200 | 404 |
| POST | `/image-generations/{ig_id}/cancel` | J | – | 202 | 404; 409 terminal |
| DELETE | `/image-generations/{ig_id}` | J | – | 204 | 404; 409 active or referenced by an active workflow |
| GET | `/image-generations/{ig_id}/candidates/{index}` | F | `index` matches `^[0-3]$` | 200 `image/png`, `Cache-Control: private, max-age=3600` | 404 run, index or file; 409 not completed |
| GET | `/image-generations/{ig_id}/candidates/{index}/thumbnail` | F | as above | 200 `image/webp` | as above |
| POST | `/image-generations/{ig_id}/candidates/{index}/use` | J | `{reference_set_id?, slot="front", make_primary=true, label?}` | 201 `{image, set}` (the upload response shape) | 404 run, candidate or set; 409 not completed; 422 slot; 503 `feature_disabled` |

### 6.3 Segmentations
| Method | Path | Auth | Body / query | Success | Errors |
|---|---|---|---|---|---|
| POST | `/segmentations` | J | `SegmentationRequest` | 202 `SegmentationOut` | 404 set, image or job; 409 job not completed; 422; 429; 503 `feature_disabled` / `capability_unavailable` |
| GET | `/segmentations` | J | `?limit=1..100 (20)&status&reference_set_id&job_id` | 200 `{items}`, newest first | 422 |
| GET | `/segmentations/{sg_id}` | J | – | 200 | 404 |
| POST | `/segmentations/{sg_id}/cancel` | J | – | 202 | 404; 409 |
| DELETE | `/segmentations/{sg_id}` | J | – | 204 | 404; 409 active or used by an active workflow |
| GET | `/segmentations/{sg_id}/overlay` | F | – | `image/png` | 404; 409 |
| GET | `/segmentations/{sg_id}/labels` | F | – | `image/png` (16-bit) | 404; 409 |
| GET | `/segmentations/{sg_id}/masks/{value}` | F | `value` matches `^\d{1,5}$` and is in the result | `image/png` | 404; 409 |
| GET | `/segmentations/{sg_id}/source` | F | – | `image/png` (snapshot) | 404 |

### 6.4 Part analyses
| Method | Path | Auth | Body / query | Success | Errors |
|---|---|---|---|---|---|
| POST | `/jobs/{job_id}/part-analyses` | J | `PartAnalysisOptions` (optional body) | 202 `PartAnalysisOut` | 404 job; 409 job not completed or `model.glb` missing; 404/409 `segmentation_id`; 422; 429; 503 `feature_disabled` / `capability_unavailable` |
| GET | `/jobs/{job_id}/part-analyses` | J | – | 200 `{items}`, newest first | 404 job |
| GET | `/jobs/{job_id}/part-analyses/latest` | J | – | 200, the newest **completed** analysis | 404 job, or 404 `not_found` with `details.reason: "none"` |
| GET | `/part-analyses/{pa_id}` | J | – | 200 | 404 |
| POST | `/part-analyses/{pa_id}/cancel` | J | – | 202 | 404; 409 |
| DELETE | `/part-analyses/{pa_id}` | J | – | 204 (also purges `runtime/cache/part_exports/<pa>/`) | 404; 409 active |
| GET | `/part-analyses/{pa_id}/manifest` | F | – | 200 `application/json`, `Cache-Control: no-cache` | 404; 409 not completed |
| GET | `/part-analyses/{pa_id}/model.glb` | F | – | `model/gltf-binary`, inline | 404; 409 |
| GET | `/part-analyses/{pa_id}/export/glb` | F | – | attachment `img3d_<job>_parts_<pa>.glb` | 404; 409 |
| GET | `/part-analyses/{pa_id}/export/zip` | F | `?include_base=false` | `application/zip` attachment `img3d_<job>_parts_<pa>.zip`. Built on demand into the cache: manifest, `model_parts.glb`, buffers, masks, segmentation, per-part GLBs, and `model.glb` when `include_base` | 404; 409 |
| GET | `/part-analyses/{pa_id}/parts/{part_id}/model.glb` | F | `part_id` must be in the manifest | inline GLB (pre-written, or built on demand and cached) | 404 unknown part; 409 `conflict` with `details.reason: "no_mesh"` (region or deferred part) or `"not_completed"` |
| GET | `/part-analyses/{pa_id}/parts/{part_id}/export/glb` | F | as above | attachment `img3d_<job>_<part_id>.glb` | as above |
| GET | `/part-analyses/{pa_id}/masks/{value}` | F | `^\d{1,5}$`, present in `label_table` | `image/png` | 404; 409 |
| GET | `/part-analyses/{pa_id}/thumbs/{name}` | F | `^[a-z0-9_.]{1,90}\.webp$`, a manifest `files.thumbnail` | `image/webp` | 404; 409 |
| GET | `/part-analyses/{pa_id}/segmentation/{name}` | F | `name ∈ {source.png, labels.png, overlay.png}` | `image/png` | 404; 409 |
| GET | `/part-analyses/{pa_id}/previews/{name}` | F | `^(front\|back\|left\|right)\.png$` | `image/png` | 404; 409 |
| GET | `/part-analyses/{pa_id}/buffers/{buffer_id}` | F | `buffer_id` is in `manifest.buffers` | `application/octet-stream`, headers `X-Img3d-Dtype`, `X-Img3d-Count` | 404; 409 |
| GET | `/part-analyses/{pa_id}/log` | F | – | `text/plain`, tail ≤ 256 KB | 404 |

- `DELETE /jobs/{job_id}` (existing route) first runs the registered delete hook. The hook cancels the job's active
  analyses, waits ≤ 30 s, and forgets them.
- The existing `safe_rmtree` then removes `parts/` together with the job. The hook also purges the export cache of
  those analyses.
- `services/part_files.py` builds on-demand files with `asyncio.to_thread` under a per-(pa, file) `asyncio.Lock`. It
  writes to a temp name and then `os.replace`s.

### 6.5 Workflows
| Method | Path | Auth | Body / query | Success | Errors |
|---|---|---|---|---|---|
| GET | `/workflows/catalog` | J | `If-None-Match` | 200 `CatalogOut` + `ETag`; 304 (empty body, same `ETag`) when unchanged | 401 |
| POST | `/workflows/validate` | J | `WorkflowRequest` (simple form or `graph`) | 200 `ValidationReport {valid, issues: GraphIssue[], mode: "job"\|"workflow"\|null, request: WorkflowRequest\|null}`. Never creates anything. For `mode: "job"` the client posts its own `CreateJobRequest` to `POST /jobs` (no server copy is returned) | 401; 422 only for a malformed body |
| POST | `/workflows` | J | `WorkflowRequest` | 202 `WorkflowOut` | 404 source; 409 job source not completed; 422 `validation_error` (incl. `conflicting_fields` for `graph` + simple fields, `segmentation` without `parts`, or a non-null `parts.segmentation_id`; `extra_forbidden` e.g. a top-level `export`; `unsupported_by_engine`, `unsupported_by_model`, `graph_too_large`) or 422 `invalid_workflow` (`details.issues`); 429 `queue_full`; 503 `feature_disabled` / `capability_unavailable` |
| GET | `/workflows` | J | `?limit=1..100 (20)&status&job_id&image_generation_id` | 200 `{items}`, newest first | 422 |
| GET | `/workflows/{wf_id}` | J | – | 200 | 404 |
| GET | `/workflows/{wf_id}/graph` | J | – | 200 `{graph, layout}` | 404 (none stored) |
| POST | `/workflows/{wf_id}/cancel` | J | – | 202 | 404; 409 terminal |
| DELETE | `/workflows/{wf_id}` | J | – | 204 | 404; 409 active |

`WorkflowOut` is the §4.7.5 shape, including `steps[].warning_count` (int, default 0). For `error.code ==
"step_failed"`, `error.details` is the §4.7.4 `StepFailedDetails` object with
`child_error: {code: str, message: str, details: Any | null}`.

`CatalogOut`:
```json
{"schema": "img3d.workflow_catalog/1", "etag": "\"sha256-3f1c…\"",
 "port_types": [{"id": "image_set", "label": "Image"}, {"id": "segmentation", "label": "Masks"},
                {"id": "mesh", "label": "Mesh"}, {"id": "part_set", "label": "Parts"}],
 "node_types": [{"type": "segmentation", "version": 1, "category": "analyze", "title": "Segmentation",
                 "inputs": [{"id": "images", "label": "Image", "accepts": ["image_set"], "required": true, "multiple": false}],
                 "outputs": [{"id": "masks", "label": "Masks", "type": "segmentation"}],
                 "params_schema": { /* SegmentationOptions.model_json_schema(); every node type has one with "properties" (§4.7.2) */ },
                 "defaults": {"profile": "auto", "subject": "auto", "classes": null, "extra_terms": [],
                              "use_prompt_terms": true, "head_pass": true, "min_confidence": 0.35},
                 "compiles_to": "segmentation", "max_instances": 1, "available": true, "locked": false,
                 "code": "ok", "reason": null, "hidden_by_default": false}],
 "templates": [{"id": "image_to_3d", "title": "Image to 3D", "graph": { /* WorkflowGraph */ }},
               {"id": "image_to_3d_parts", "title": "Image to 3D with parts", "graph": {}},
               {"id": "prompt_to_3d", "title": "Prompt to 3D", "graph": {}},
               {"id": "prompt_to_3d_parts", "title": "Prompt to 3D with parts", "graph": {}},
               {"id": "rebuild_parts", "title": "Rebuild parts of a result", "graph": {}}],
 "legacy_job_node_map": { /* §4.7.2 */ },
 "limits": {"max_nodes": 32, "max_edges": 64, "graph_max_bytes": 65536}}
```
- The ETag is the sha256 of the canonical JSON (sorted keys), so it changes whenever node availability changes.
- The frontend's pure-TS workflow engine stays the UI source of truth for layout and interaction. It locks nodes and
  validates locally from this catalog. `POST /workflows/validate` is the authoritative server check, debounced by
  the UI.

### 6.6 WebSocket events (envelope `{type, ts, data}`; existing types and `hello` unchanged)
| type | data | `Event.job_id` (scoping) | Throttle / droppable |
|---|---|---|---|
| `features.updated` | `FeaturesOut` | – (always delivered) | debounced 250 ms; never droppable |
| `image_generation.updated` | `ImageGenerationOut` | – | progress ≤ 5/s, droppable; status and step changes never dropped |
| `segmentation.updated` | `SegmentationOut` | the job id for job sources (not in the scoped set, so delivered to all) | same |
| `part_analysis.updated` | `PartAnalysisOut` | the analysis's job id; **job-scoped** (`?job_id=` filter) | same |
| `workflow.updated` | `WorkflowOut` | – | progress ≤ 2/s, droppable; step transitions never dropped |

- Old clients ignore unknown types (`frontend/src/api/ws.ts:254-276`, `test/e2e/e2e_generate.py:191-201` [V]).
- Clients fetch `/features` after every `hello` and on `features.updated`.

### 6.7 Changed existing shape (additive only)
- `ReferenceImage` responses gain `source: ImageSourceOut | null`, with `ImageSourceOut = {kind: "t2i",
  image_generation_id, candidate_index, model_id, model_revision, license_id, seed}`. The prompt hash is not exposed;
  the UI links to the `ig_` run for the text. Uploads return `null`.
- `set.json` omits the key for uploads (§1.2 S-f).
- No other existing request or response shape changes.

### 6.8 Errors and warning codes
| class (`core/feature_errors.py`) | HTTP | code | details |
|---|---|---|---|
| `FeatureDisabledError` | 503 | `feature_disabled` | `{"feature": "mesh_tuning" \| "part_refinement" \| …, "flag": "IMG3D_…"}` |
| `CapabilityUnavailableError` | 503 | `capability_unavailable` | `{"capability": "segmentation", "reasons": [{"model_id", "state", "reason_code", "reason"}]}` |
| `ModelUnavailableError` | 503 | `model_unavailable` | `{"model_id", "state", "reason_code"}` |
| `InvalidWorkflowError` | 422 | `invalid_workflow` | `{"issues": GraphIssue[]}` |
| existing `ValidationAppError` | 422 | `validation_error` | per-field `type ∈ {unsupported_by_model, conflicting_fields, invalid_characters, unknown_class, graph_too_large}` plus pydantic's own types (`extra_forbidden`, `missing`, range errors) |
| existing `ConflictError` / `NotFoundError` / `QueueFullError` / `EngineUnavailableError` | 409 / 404 / 429 / 503 | unchanged | new uses add `details.reason` |

**Run error codes** (`run.error.code`): `interrupted`, `out_of_memory`, `insufficient_vram`, `run_error`,
`run_timeout`, `source_missing`, `job_deleted`, `unsupported_base_mesh`, `generation_failed`, `segmentation_failed`,
`camera_fit_failed`, `lifting_failed`, `partition_failed`, `export_failed`, `publish_conflict`. Workflows add
`step_failed` and `queue_full`.

**Warning codes:** `t2i_touches_border`, `t2i_multiple_subjects`, `t2i_coverage_low`, `t2i_coverage_high`,
`t2i_aspect_mismatch`, `profile_degraded`, `frame_unverified`, `head_pass_low_res`,
`segmentation_rerun_for_alignment`, `camera_fit_low_iou`, `too_small_for_geometry`, `max_parts_exceeded`,
`expected_part_missing`, `charts_cross_boundary`, `low_confidence`, `floater_candidates`, `gpu_defer_timeout`
(the jobs-first gate gave up after `run_max_defer_s`, §4.3). `readiness.issues` (§4.4.5) use the same `t2i_*`
strings.

### 6.9 What each UI action calls (identical in all four modes; only the renderer differs)
| User action | Calls | Live updates |
|---|---|---|
| Image → 3D (no parts) | **unchanged** `POST /jobs` | `job.updated` |
| Image → 3D + parts | `POST /workflows {source: image, reconstruction, parts, segmentation?}` (`segmentation` only together with `parts`; export options in `parts.export`) | `workflow.updated`, `job.updated`, `part_analysis.updated` |
| Prompt → 3D (± parts), one-shot | `POST /workflows {source: prompt, reconstruction, parts?}` | `workflow.updated`, `image_generation.updated` |
| Prompt → review image → 3D | `POST /image-generations` → `…/use` → `POST /jobs` or `POST /workflows` (image source) | `image_generation.updated`, then as above |
| Segmentation preview (Node UI node, mobile sheet) | `POST /segmentations {source: reference_image}` | `segmentation.updated` |
| Re-run parts on a finished job | `POST /jobs/{id}/part-analyses` (or a workflow with source `job`) | `part_analysis.updated` |
| Viewer "Parts" source | `GET /jobs/{id}/part-analyses/latest` → `result.assembled_url` (nodes `part:<id>`, `_FEATURE_ID_0` on `part:body`) | – |
| Parts list, part download | `result.parts[]` (`model_url`, `download_url`, `mask_url`, `thumbnail_url`) | – |
| Node UI run | `POST /workflows/validate {graph}` (debounced) → `mode == "job"` ? `POST /jobs` with the client-built body (byte-identical to the Default UI) : `POST /workflows {graph, layout}` | `workflow.updated` (steps carry `node_id` and `artifacts`) or `job.updated` + `legacy_job_node_map` |
| Capability gating, licences, warm-up | `GET /features`, `GET /models`, `POST /models/{id}/load` | `features.updated` |
| History provenance ("from prompt") | the primary `ReferenceImage.source`, or `GET /workflows?job_id=` | – |

Rules for the frontend:
- It never builds part, mask or GLB paths itself; it uses the URLs from payloads.
- `/features` returning 404 means a legacy backend: image-to-3D only (`frontend_b_command_engine.md` §4.3).

---------------------------------------------------------------------------------------------------

## 7. Storage, retention, migration

### 7.1 Layout
```
backend/runtime/
  jobs/<job>/job.json  inputs/  preview.png  model.glb                     unchanged
  jobs/<job>/tuning/<tn>/…                                                unchanged (paused tuner)
  jobs/<job>/parts/<pa>/run.json                                         RunStore record (from queue time)
  jobs/<job>/parts/<pa>/asset/…                                           §4.6.6 (published atomically, immutable; removed with the job)
  jobs/<job>/parts/<pa>/log.txt                                          failed analyses only (log tail)
  (no jobs/<job>/intermediates/: SD1 is deferred, Q5 → D1)
  imagegen/<ig>/run.json  candidates/<n>.png  candidates/<n>_thumb.webp
  segmentations/<sg>/run.json source.png [original.png] frame.json labels.png overlay.png masks/ segmentation.json
  workflows/<wf>/workflow.json  graph.json  layout.json
  cache/part_exports/<pa>/<part_id>.glb  bundle[_base].zip                on-demand exports (LRU, §7.2)
  temp/imagegen/<ig>/  temp/segmentations/<sg>/  temp/parts/<pa>/         work dirs; purged at startup by the existing purge_temp
```
- `storage/run_layout.py` holds these paths as functions of `RuntimePaths`. `storage/paths.py` stays protected.
- No path component is named `aux`, which is a reserved Windows device name.
- `runtime/cache/` already exists, holding the triton and flex_gemm caches [V].
- `ig_` and `sg_` runs are global, because they are not tied to a job. `pa_` runs live inside their job, so job
  delete and retention remove them automatically [V `job_store.py:112-118`].

### 7.2 Retention (`services/run_retention_service.py`, S8; runs every `retention_interval_s` [V default 3600 s])
| Store | Age limit | Count limit | Notes |
|---|---|---|---|
| `imagegen/` | `imagegen_retention_days` = 14 | `max_stored_image_generations` = 200 | Terminal runs only. Runs referenced by an active workflow are skipped |
| `segmentations/` | `segmentation_retention_days` = 14 | `max_stored_segmentations` = 200 | same |
| `workflows/` | `workflow_retention_days` = 30 | `max_stored_workflows` = 500 | terminal only |
| `jobs/<job>/parts/` | existing job retention (14 days / 200 jobs) | – | Retention deletes jobs without running hooks (`retention_service.py:60-77` [V]). That is why the `pa_` publish re-checks `job_exists` and `RunStore.save` only `mkdir(parents=False)`s below an existing job folder (§4.3) |
| `cache/part_exports/` | entries whose `pa_` no longer exists | `parts_export_cache_max_gb` = 10, oldest access first | rebuilt on demand |

"Referenced by an active workflow" comes from the frozen `storage/workflow_store.py` API that S1 creates as a stub
and S11 implements:
```python
class WorkflowStore:
    def list_terminal(self) -> list[WorkflowRecord]          # completed | failed | cancelled, oldest first
    def delete(self, wf_id: str) -> bool                     # False when unknown or active
    def active_child_ids(self) -> frozenset[str]             # ig_/sg_/pa_/job_ ids of queued|running workflows
```
The S1 stub returns `[]`, `False` and `frozenset()`. `test_run_retention.py` (S8) uses a fake with the same API, so
S8 does not wait for S11.

Disk use with the defaults:
- `model_parts.glb` is about the size of `model.glb`, 41–86 MB [M].
- Buffers add about 25 MB for 2 M faces.
- Per-part GLBs and ZIPs are not written by default; they are built on demand into the bounded cache.

### 7.3 Migration and compatibility
- **Old `job.json`:** nothing to migrate. The file and its model are untouched. Any completed legacy job can get a
  part analysis later (retroactive parts).
- **Old backends:** a backend reading new data ignores `parts/` (`JobStore.load` reads only `job.json` [V]). It loads
  `set.json` entries carrying `source` because `DomainModel` uses `extra="ignore"` [V `models/types.py:30`]. It
  ignores the new top-level folders.
- **Manifests:** they carry `schema` and `taxonomy_version`. Readers ignore unknown fields (A9). A breaking change
  needs `img3d.parts/2` plus a reader-side migration in `parts/reader.py`.
- **Taxonomy values:** stable. New classes get new values. A value is never reused.

---------------------------------------------------------------------------------------------------
## 8. Feature flags and settings

### 8.1 New `Settings` fields (`config/settings.py`, S1; env prefix `IMG3D_`; all optional)
| field | env | default | effect |
|---|---|---|---|
| `part_aware_enabled` | `IMG3D_PART_AWARE_ENABLED` | `true` | Master switch. When false: no `RunWorker`, no workflow sweep, mutating new endpoints return 503 `feature_disabled`, `/features` flags are false. Reads still work |
| `image_generation_enabled` | `IMG3D_IMAGE_GENERATION_ENABLED` | `true` | `ig_` creation and prompt workflows |
| `segmentation_enabled` | `IMG3D_SEGMENTATION_ENABLED` | `true` | `sg_` creation and the semantic strategies |
| `part_analysis_enabled` | `IMG3D_PART_ANALYSIS_ENABLED` | `true` | `pa_` creation |
| `workflows_enabled` | `IMG3D_WORKFLOWS_ENABLED` | `true` | `wf_` creation and validate |
| `mesh_tuning_enabled` | `IMG3D_MESH_TUNING_ENABLED` | **`false`** | Code gate on `POST /jobs/{id}/tunings` (§8.2) and `/features.mesh_tuning` |
| `part_refinement_enabled` | `IMG3D_PART_REFINEMENT_ENABLED` | **`false`** | `PartConsumerRegistry.register()` raises `FeatureDisabledError`. Manifests stay locked |
| `approved_models` | `IMG3D_APPROVED_MODELS` | `None` → catalog ids with `default_approved=True` (the six `DECISIONS.md` models, Q1) | Declared `Annotated[list[str] \| None, NoDecode]` with a `field_validator(mode="before")`, exactly like `cors_origins` [V `config/settings.py:65,134`]: pydantic-settings would otherwise JSON-decode the value and crash `Settings()` on `a,b`. Accepts a JSON list, or `,`/`;`-separated ids; an empty or whitespace-only string → `None`; the JSON list `[]` approves nothing |
| `model_paths` | `IMG3D_MODEL_PATHS` | `{}` | JSON map id → local path override (relative to `backend/`) |
| `allow_restricted_licenses` | `IMG3D_ALLOW_RESTRICTED_LICENSES` | `false` | Needed for `non_commercial` / `research_only` / `unknown` models. None are in catalog v1 except the exempt lifter |
| `t2i_default_model` | `IMG3D_T2I_DEFAULT_MODEL` | `None` → first usable | |
| `segmentation_default_profile` | `IMG3D_SEGMENTATION_DEFAULT_PROFILE` | `"auto"` | |
| `run_queue_max_size` | `IMG3D_RUN_QUEUE_MAX_SIZE` | `8` (1..100) | Shared by `ig_`, `sg_` and `pa_` |
| `aux_keep_models_in_ram` | `IMG3D_AUX_KEEP_MODELS_IN_RAM` | `true` | |
| `aux_model_idle_unload_s` | `IMG3D_AUX_MODEL_IDLE_UNLOAD_S` | `0` (never) | The Z-Image cold load is ~337 s [M]. RAM is 130 GB |
| `aux_vram_budget_gb` | `IMG3D_AUX_VRAM_BUDGET_GB` | `28.0` | Static check (§4.8) |
| `engine_resident_vram_gb` | `IMG3D_ENGINE_RESIDENT_VRAM_GB` | `17.0` | Subtracted when `low_vram=false` and the engine is ready |
| `aux_vram_margin_gb` | `IMG3D_AUX_VRAM_MARGIN_GB` | `1.5` | Live check margin (§4.8) |
| `prompt_max_chars` | `IMG3D_PROMPT_MAX_CHARS` | `2000` (1..8000) | |
| `parts_default_views` | `IMG3D_PARTS_DEFAULT_VIEWS` | `8` (0..24) | Default `views` |
| `parts_min_part_faces` / `parts_max_parts` | `IMG3D_PARTS_MIN_PART_FACES` / `IMG3D_PARTS_MAX_PARTS` | `None` (→ `max(50, round(1e-4·F))`, §4.6.2) / `64` | Request defaults |
| `run_max_defer_s` | `IMG3D_RUN_MAX_DEFER_S` | `600` (0..3600; 0 = no jobs-first gate) | Longest wait of an auxiliary GPU phase behind pending/running jobs (§4.3) |
| `parts_export_cache_max_gb` | `IMG3D_PARTS_EXPORT_CACHE_MAX_GB` | `10` | §7.2 |
| `part_analysis_timeout_s` | `IMG3D_PART_ANALYSIS_TIMEOUT_S` | `1800` | Watchdog → `run_timeout` |
| `workflow_graph_max_bytes` | `IMG3D_WORKFLOW_GRAPH_MAX_BYTES` | `65536` | |
| `workflow_sweep_interval_s` | `IMG3D_WORKFLOW_SWEEP_INTERVAL_S` | `5.0` | |
| `imagegen_retention_days` / `segmentation_retention_days` / `workflow_retention_days` | `IMG3D_…` | `14` / `14` / `30` | |
| `max_stored_image_generations` / `max_stored_segmentations` / `max_stored_workflows` | `IMG3D_…` | `200` / `200` / `500` | |
| `persist_generation_intermediates` | `IMG3D_PERSIST_GENERATION_INTERMEDIATES` | `false` | **Inert**: SD1 is not approved (Q5, deferred D1), so no code reads it and `/features.flags.persist_generation_intermediates` is always reported `false` whatever the env says. Kept only so a future SD1 needs no settings change |

- `test_settings.py` pins only existing defaults [V contract map §5.1], so these additions are safe.
- The tests build settings with `make_settings(**overrides)` and `_env_file=None` [V `test/backend/conftest.py:60-75`].
  New tests pass the new fields as overrides. **Caution:** after S9, `test/conftest.py` sets
  `IMG3D_MESH_TUNING_ENABLED=true` for the whole test session (§8.2 G2), and `make_settings` does not override it, so
  every test that asserts the tuning default must pass `mesh_tuning_enabled=False` explicitly or
  `monkeypatch.delenv("IMG3D_MESH_TUNING_ENABLED")` (as `test_settings.py:18-25` does).

### 8.2 Tuning and refinement stay disabled (code gate without breaking any of the 20 tuner API tests)
| Gate | What | Proof |
|---|---|---|
| G1 code gate (S9) | In `MeshTunerService.create_run`, right after the existing `self._results.model_file(job_id)` (404/409 [V :97]) and before the `stopping` and `require_runtime()` checks, add: `if not self._settings.mesh_tuning_enabled: raise FeatureDisabledError("Auto Mesh Tuner is paused until approved", feature="mesh_tuning", flag="IMG3D_MESH_TUNING_ENABLED")` → 503 `feature_disabled`. Listing, reading, cancelling and deleting existing runs, and their file routes, still work (history) | `test/backend/test_tuning_gate.py` |
| G2 test opt-in (S9) | `test/conftest.py` gains one line next to the existing `IMG3D_BLENDER_DETECT_ON_STARTUP` default [V]: `os.environ.setdefault("IMG3D_MESH_TUNING_ENABLED", "true")`. Pydantic-settings reads environment variables even with `_env_file=None`, so every tuner test sees `true` without edits. This covers `make_tuner_app` (`test_mesh_tuner_api.py:57`), `test_shutdown_interrupts_running_tuning` (builds `make_settings` directly, :404-405 [V]), `test_real_blender_plumbing` (:522) and the CLI test. `test_tuning_gate.py` passes `mesh_tuning_enabled=False` explicitly; init kwargs beat the environment | All 182 tests green |
| G3 lifecycle unchanged | Startup Blender detection still depends only on `blender_enabled and blender_detect_on_startup` [V `lifecycle.py:60`]. `test_blender_runtime_api.py::test_detection_on_startup_runs_in_background` is unaffected. Changing this was B's fatal flaw | Existing suite |
| G4 exposure | `/features.mesh_tuning.enabled = blender_enabled and mesh_tuning_enabled` (false). All four UI modes hide every tuner and Blender-runtime mount point from this one flag, together with the frontend build flag (`UI_DESIGN.md` §3.9). The Blender detection/download endpoints stay as they are (Q3 default) | `test_features_api.py` |
| G5 optional zero-code hard switch | The user may also put `IMG3D_BLENDER_ENABLED=false` in `backend/.env`, which disables Blender runtime management entirely [V contract map §5.2]. Agents never edit `.env` | – |
| G6 refinement | `part_refinement_enabled=false`: the consumer registry refuses registrations, manifests are locked (A8), refine nodes are `locked` and `hidden_by_default: true`, and the compiler rejects them with `node_locked` | `test_reader_consumers.py`, `test_workflow_compiler.py` |
| G7 isolation | New packages never import tuner modules (I10) | `test_import_boundaries.py` |

The developer CLI `backend/scripts/auto_mesh_tuner.py` does not go through `MeshTunerService` [V]. It is run only
explicitly by the user and is not changed.

---------------------------------------------------------------------------------------------------
## 9. Dependencies

### 9.1 No new pip packages
Everything this milestone needs is already installed in the embedded Python [V environment audit;
`DECISIONS.md`]:

| Package | Pin | Used by |
|---|---|---|
| `diffusers` | `==0.39.0` (the ceiling: 0.40+ needs huggingface-hub ≥ 1.x) | `inference/imagegen/zimage.py` |
| `accelerate` | `==1.15.0` | diffusers loading |
| `transformers` | `==4.57.6` (never upgraded) | GDINO (`AutoModelForZeroShotObjectDetection`), SAM 2.1 (`Sam2Model`), Florence-2 (`Florence2ForConditionalGeneration`) |
| `huggingface-hub` | `==0.36.2` (never upgraded) | – |
| `torch` / `torchvision` | `==2.14.1+cu130` / `==0.29.1+cu130` | providers, lifter |
| `nvdiffrast` / `cumesh` | `0.4.0` / `0.0.1` (local builds) | lifter |
| `numpy` / `scipy` / `pillow` / `opencv-python-headless` / `trimesh` | `2.4.1` / `1.18.1` / `12.3.0` / `5.0.0.93` / `5.1.1` | `parts/`, `processing/` (trimesh only in tests) |

Not added: `rtree`, `scikit-image`, `manifold3d`, `xatlas`, `spconv`, `kaolin`, `flash-attn`, `utils3d`. scipy
(`cKDTree`, `csgraph`) and numpy cover every need.

### 9.2 File changes
- `backend/requirements.txt` (S4): add `diffusers==0.39.0` and `accelerate==1.15.0` under a "Part-aware
  (text-to-image)" heading. Both are already installed, so this only records the pins (`DECISIONS.md` "still to do").
- `backend/constraints.txt`: **it already exists and already pins diffusers 0.39.0 and accelerate 1.15.0 [V].** No
  slice recreates it. If a pin ever has to change, the file is edited in place, keeping every TRELLIS.2 pin, and only
  with the user's approval.
- `.gitmodules` + `backend/third_party/{see-through,sapiens2}/` (S6; Q4 approved by the user): pinned commits. Only the small
  model modules are used, and their requirements are never installed. If either needs a package that is not
  installed, S6 stops and reports instead of installing it.

### 9.3 Install procedure (only if an approved future change needs a package)
```
$py = ".\backend\python\python.exe"
& $py -s -m uv pip install --python $py --link-mode=copy --only-binary ":all:" -c backend\constraints.txt --dry-run <pkg>==<ver>
#   expect: only the named package(s), 0 upgrades/downgrades; otherwise STOP and ask the user
& $py -s -m uv pip install --python $py --link-mode=copy --only-binary ":all:" -c backend\constraints.txt <pkg>==<ver>
& $py -s -m uv pip check
& $py -s test\part_aware\gpu_lock.py --label install-probe -- $py -s test\engine\run_runtime_probe.py   # TRELLIS.2 import probe
# then the real TRELLIS.2 smoke test from .claude\skills\img3d-run-and-test, also wrapped in test\part_aware\gpu_lock.py
```

---------------------------------------------------------------------------------------------------
## 10. Test plan
Command per slice:
`backend\python\python.exe -s -m pytest -c test\pytest.ini test\backend test\aux_models -q`. The GPU and engine track
always runs under the GPU lock (rule below):
`backend\python\python.exe -s test\part_aware\gpu_lock.py --label <slice> -- backend\python\python.exe -s -m pytest -c test\pytest.ini test\engine -q`.
- Every existing test stays green with no assertion changes (I9).
- New tests use test doubles that live only under `test/backend/fakes_*.py`. Doubles write real small PNG and GLB
  files.
- **Real-weight runs (every slice, every smoke, every acceptance step that loads weights onto the GPU):**
  - Every command is wrapped:
    `backend\python\python.exe -s test\part_aware\gpu_lock.py --label <slice> -- <cmd>` (the lock is
    `test/.runtime/gpu.lock`; the 32 GB GPU holds one large model run at a time: Z-Image 23.3 GB, TRELLIS.2
    16.8–17.8 GB [M `test/part_aware/fixtures.json`]). Phase B slices run in parallel, their GPU steps never do.
  - Inputs come only from `test/images/{ALICE,ANIME_CHARACTER_01}` or from the read-only fixtures `alice_front` /
    `anime_alice` of `test/part_aware/fixtures.json` (`test/outputs/engine_smoke/job_smoke165749`, `…170427`),
    **copied** into a test runtime `test/.runtime/<slice>/<case>/` (a job folder plus a synthesised `job.json`)
    before anything writes. Jobs that a smoke needs are created fresh through `POST /jobs` by the script, against a
    backend the script starts itself with **absolute** paths
    `IMG3D_RUNTIME_DIR=E:\14-OCT-2026\Img3D\test\.runtime\<slice>` and
    `IMG3D_LOG_DIR=E:\14-OCT-2026\Img3D\test\.runtime\<slice>\logs`. A relative value would not work:
    `config/settings.py:39-44,206-220` [V] resolves every relative path against `BACKEND_DIR`, not the working
    directory, so `test\.runtime\<slice>` would land in `backend/test/.runtime/<slice>` (jobs, uploads, temp and the
    triton/flex_gemm caches under `runtime/cache`), and the logs would stay in `backend/logs`. Scripts build both
    values the way `test/e2e/server_smoke.py:34-39,61-62` [V] does:
    ```python
    REPO = Path(__file__).resolve().parents[2]          # test/<dir>/<script>.py → repo root
    runtime_dir = REPO / "test" / ".runtime" / slice
    env["IMG3D_RUNTIME_DIR"] = str(runtime_dir)
    env["IMG3D_LOG_DIR"] = str(runtime_dir / "logs")
    # Popen(..., cwd=str(REPO / "test"), env=env)       # the CWD must not matter (server_smoke.py:76)
    ```
  - **Runtime refusal rule (exit code 2, like `EXCLUDED_JOB_IDS`).** `GET /system` cannot be used for this:
    `SystemStatus` reports no paths [V `schemas/system.py:99-106`] and its keys are frozen (I3). Instead:
    1. Before the backend starts: both `Path(env[...]).resolve()` values must satisfy `.is_relative_to(REPO / "test")`.
    2. After `GET /health` is 200: `<log_dir>/img3d.log` must exist, and its first JSON line whose `message` starts
       with `"Configuration: "` [V `core/lifecycle.py:23`, `core/logging.py:268-271` JSON lines] must contain
       `"'runtime_dir': " + repr(str(runtime_dir.resolve()))` (the `safe_summary()` dict repr, `settings.py:293`;
       the backend stores the resolved path, `settings.py:220`).
    3. After every `POST /jobs`: `<runtime_dir>/jobs/<job_id>/job.json` must exist (the check never touches
       `backend/runtime/`). On failure the script cancels the job, stops the backend and exits 2.
    The scripts never attach to an already running backend (no `--base-url` option), so the environment is always
    the one they built.
  - Nothing reads, lists, opens or analyses `backend/runtime/jobs/`. In particular `job_269a864ac597` and
    `job_fa0c4771ec03` are never opened: every smoke script defines
    `EXCLUDED_JOB_IDS = frozenset({"job_269a864ac597", "job_fa0c4771ec03"})` and exits with code 2 if one is passed
    or found in its runtime.
  - Outputs, logs and caches go under `test/outputs/part_aware/` and `test/.runtime/` only.

### 10.1 Required compatibility coverage (where each mandated check lives)
| Check | File | Assertion |
|---|---|---|
| Legacy load | `test_legacy_invariance.py` | The committed pre-change fixture `test/backend/fixtures/legacy_job_v1/job.json` (plus a `model.glb` written at test time by `fakes.write_test_glb`) is placed in the runtime dir. It loads at startup; `GET /jobs/{id}` and `GET /jobs/{id}/result` return 200; `GET /jobs/{id}/part-analyses` returns `{items: []}` |
| Retry round-trip | `test_legacy_invariance.py` | `POST /jobs` with a completed job's stored `settings` plus `reference_set_id`/`primary_image_id` returns 202, and the stored settings dict is equal (`extra="forbid"` round trip) |
| `model.glb` untouched | `test_legacy_invariance.py` (S10 un-skips it) and `test_part_analysis_api.py` | sha256 of `model.glb`, `preview.png` and `job.json` before and after a `components` and a semantic analysis are equal |
| Byte and key identity of image jobs | `test_legacy_invariance.py` | With `part_aware_enabled` true **and** false: the `job.json` key set equals `LEGACY_JOB_KEYS` (taken from the fixture); there is no `parts` key in settings; the `JobOut` key set is unchanged; the WS stage sequence is unchanged; the final percent is 100 and `stage_index` is 9; `/system` top-level keys are unchanged |
| Parity | `test/engine/test_generation_parity.py` (unchanged), `test/engine/test_prepared_conditioning.py` (S5), `test/engine/test_aux_isolation.py` (S2) | Same call sequence and RNG fingerprints. The replica crop is pixel-equal to upstream `preprocess_image`. An auxiliary GPU callable that flips torch globals between two same-seed runs changes nothing (§4.9) |
| Multi-node GLB stats | `test_gltf_io.py` | `compute_glb_stats(model_parts.glb)` sums node faces to the base face count, `materials == 1`, and `textures` equals the base; `trimesh.load(..., force="scene")` succeeds |
| File-route auth | `test_part_analysis_api.py`, `test_segmentation_api.py`, `test_image_generation_api.py` | With auth enabled, every F route returns 401 without a token and 200 with `?token=` (pattern from `test_mesh_tuner_api.py:460` [V]); every J route rejects `?token=` (401) |
| Path traversal | same files | `../`, `%2e%2e`, backslashes, absolute paths, ids failing `is_valid_id`, a `part_id` not in the manifest, and a buffer id not in the manifest all give 404 or 422 and never read outside the run dir |

### 10.2 Test files and what they assert
| File (owner) | Asserts |
|---|---|
| `test_protected_files.py` + `protected_files.sha256` (S0) | The sha256 (CRLF→LF) of every protected file (§1.3) matches. A failure names the file and says the change needs the user's approval |
| `test_import_boundaries.py` (S1) | AST scan: rules of §1.4; no tuner imports in new packages; a subprocess import of every `parts.*` module leaves `torch`/`diffusers`/`transformers` out of `sys.modules` |
| `test_gpu_task_seam.py` (S1) | `submit_gpu_task` runs on a `gpu-worker*` thread. Tasks interleave FIFO with a FakeEngine job and never overlap (lock-guarded counters). It raises `engine_unavailable` after `stop()`. Shutdown with an in-flight auxiliary task completes within the timeout (stop order) |
| `test_run_framework.py` (S1) | `RunStore` atomic save/load/`recover_interrupted`; a save after the job folder was removed raises `job_deleted` and leaves **no** `jobs/<job>/` behind (`mkdir(parents=False)`). Reporter throttling and monotonic percent. Phases alternate CPU/GPU on the right threads; `_gpu_phase` enters and leaves `registry.guard()` (fake registry records it). Cancel while queued, during a GPU phase and during a CPU phase. Shutdown marks queued runs `interrupted`. OOM-style errors become `out_of_memory`. Watchdog gives `run_timeout` and counts only active phase time (a run held 3 s in the jobs-first gate with a 2 s timeout does not time out). A GPU fake that ignores the token for 2 s after a timeout/stop does **not** have its `work_dir` deleted before it returns. Jobs-first gate: with a fake `job_queue.pending == 1` the GPU phase is not submitted until it drops to 0, and with `run_max_defer_s=1` it is submitted after 1 s with warning `gpu_defer_timeout` |
| `test_taxonomy_manifest.py` (S1) | Taxonomy ids and values are unique; symmetric pairs are mutual; groups cover the 7 separable groups plus face/body; reserved Windows names are rejected; `match_terms` maps "katana"→`object.held`, "twin tails"→`hair`, "cat ears"→`appendage.animal_ears`. Manifest: the §3.11 example round-trips; unknown fields are ignored; a wrong schema major is rejected; absolute and `..` paths and duplicate ids are rejected |
| `test_model_registry.py` (S2) | Every state of §5.3 with temp dirs and fake `DependencySpec` distributions (unsupported, disabled, not_approved, license_blocked, vendored_source_missing, missing_dependency, not_installed, incompatible via a `transformers_version: "5.2.0"` config, insufficient_vram with `low_vram=false`, installed, loading/loaded/error via a fake provider). Every entry has a licence and a URL. No absolute path in `ModelOut`. Revisions come from `download_manifest.json`. `approved_models=[]` approves nothing; `IMG3D_APPROVED_MODELS="z_image_turbo,grounding_dino_base"` and `"a;b"` parse to lists, `""` → `None` (no `Settings()` crash). `status("sapiens2_seg_0_4b")` → `ModelOut.license.conditions == ["No biometric identification", "No deepfakes or impersonation", "No military use"]` and `commercial == "allowed_with_conditions"`. A fake provider module raising `OSError` at import → `UnavailableProvider`, `reason_code == "error"`. **HF order (subprocess):** build the app, call `registry.provider()` for a fake provider module that imports `huggingface_hub`, then `configure_hf_environment(settings.hf_home)` returns `[]` (no "imported before HF_HOME" warning) and `os.environ["HF_HOME"] == str(settings.hf_home)` |
| `test_features_api.py` (S2) | Top-level keys. Flags mirror settings. `mesh_tuning.enabled`: with `make_settings(mesh_tuning_enabled=False)` → false; with `monkeypatch.delenv("IMG3D_MESH_TUNING_ENABLED")` + `Settings(_env_file=None)` the code default is false; with `blender_enabled=False, mesh_tuning_enabled=True` → false (the G2 opt-in makes the plain test default `true`, §8.1). `part_refinement` is false; `flags.persist_generation_intermediates` is false even with the env set to `true`. The 4 refine nodes are `locked:true` **and** `hidden_by_default:true`. Every strategy, profile, node and pipeline entry has `available`, `code` (a `ReasonCode`) and `reason`; `per_part_regeneration.reason` is non-null. Each `features.models` entry equals `GET /models/{id}`. Strategies and profiles reflect fake registry states. `legacy_job_node_map` is exact. `features.updated` arrives after a fake provider state change. `/system` is unchanged. `POST /models/{id}/load` returns 202 → `loading` → `loaded` with a fake provider |
| `test_gltf_io.py` (S3a) | Raw accessor read equals the written values. Multi-primitive, sparse and Draco inputs are rejected. The writer copies image bufferViews byte-identically. `POSITION` min/max are present. `_FEATURE_ID_0` has componentType 5123, its own bufferView with `byteStride == 4`, and `accessor.byteOffset % 4 == 0`; every vertex-attribute accessor satisfies `(bufferView.byteOffset + accessor.byteOffset) % 4 == 0`. Every `part:<id>` node has `extras.img3d.id`; every mesh node has `_FEATURE_ID_0`. 4-byte alignment. trimesh loads the output; 1 material; same image count; multi-node stats |
| `test_parts_geometry.py` (S3a; fixtures in `parts_fixtures.py`: two touching spheres, a capped cylinder with UV seams, a double-walled sphere, a **rim-joined double-walled open hemisphere** (outer and inner wall welded at the rim, one component, like TRELLIS.2 output), a mesh with a non-manifold junction, plus floaters; generator `uv_sphere(n_faces, *, double_walled=False, rim_joined=False, open_hemisphere=False)` is public for S3b's tests) | Weld groups UV duplicates only; `weld_map.shape == (V,)` (numpy 2.4 inverse-shape guard). Components. Shells: the double-walled sphere yields **one** part (inner sheet merged, tagged). `pair_opposite_faces` on the rim-joined hemisphere pairs ≥ 95 % of the inner-wall faces with outer-wall faces and none with the same wall; floaters get no record and are listed in `geometry.floaters`, never deleted. Partition is complete (A1). The vmap rule (ascending + appended duplicates). **Bit-exact reassembly of POSITION, NORMAL, TEXCOORD_0 and indices** from the written files (A4). Seams: closed loops on a clean cut; open chains at the non-manifold junction; inner and outer sheet loops in the same record. A 2 M-face synthetic partition + write takes < 60 s (time logged) |
| `test_parts_labels.py` (S3b) | Argmax threshold. **Partner fill** on the rim-joined hemisphere: votes only on the outer wall → the inner wall takes the outer label (`face_flags` bit 0) and, after partition, the cut part contains **both** walls (`cut_partner_consistency ≥ 0.9`). Colour-affinity propagation stops at a colour edge. Mirror completion within tolerance. Smoothing removes single-face islands. Component fill never overwrites a voted label and never runs on component 0 or a component ≥ 25 % of F. Islands below `min_island_faces` merge, except `eyes`/`eyebrows`/`eyelashes`/`face` islands. A 500 k-face case (`parts_fixtures.uv_sphere(n_faces=500_000)` with painted votes: a ~180-face brow island and a ~120-face lash band) keeps both records |
| `test_parts_policy.py` (S3b) | The `decide_kind` matrix: eyes physical; brows and lashes deferred; face/body region; unselected group → region; physical below the effective `min_part_faces` (`max(50, round(1e-4·F))` when null) → deferred + `too_small_for_geometry`. A body-labelled shell stays on the base. An unknown shell below `floater_max_faces` gets no record (`floater_candidates` warning). The part budget merges with `max_parts_exceeded` into region records that keep their class value |
| `test_parts_export.py` (S3b) | Full export on fixtures. Manifest validates; buffer sha256s; relations (`attached_to` via the longest seam, `symmetric_with`, `region_of`, `expected_missing`); frames; refinement locked; lineage IoU between two analyses of the same fixture. Publish is atomic, goes through `retry_os` (a fake `os.replace` raising `PermissionError` twice still publishes) and refuses an existing target. `job_deleted` when the job vanished. A tampered buffer, a base sha mismatch and an inconsistent `face_part` are rejected |
| `test_reader_consumers.py` (S3b) | `AssetReader.part()` equals the per-part GLB accessors; `region_faces` returns exactly the `face_part == i` faces, also for two deferred instances sharing one class value; `reassemble()` equals the base; `verify="hashes"` detects tampering. `PartConsumerRegistry(enabled=False).register()` raises `FeatureDisabledError`; `list()` is `()`; a test-only consumer registers when `enabled=True` |
| `test_image_generation_api.py` (S4; `fakes_imagegen.py` writes procedural PNGs) | 503 when no T2I is usable (not approved). Happy path to `completed` over WS. Candidates and thumbnails are served (F auth). `use` creates a set and an image through the real `ReferenceService`, visible in `GET /reference-sets/{id}` with `source` set. `readiness.issues` and the run warnings use the same `t2i_*` codes (a fake candidate touching the border yields `"t2i_touches_border"` in both). With `num_images=3` the fake provider records three batch-size-1 calls. The `load_model` step runs on `parts-cpu`, not `gpu-worker` (thread-name check). 422 for an empty or too-long prompt, control characters, `num_images=5`, a bad size, a negative prompt on Turbo, an unknown field. Cancel and delete rules. caplog contains no prompt text |
| `test_prompt_templates.py` (S4) | Each preset × style gives the exact §4.4.3 text; the medium-gray switch; `part_hint`; `expected_terms` |
| `test_reference_provenance.py` (S4) | Uploads without `source` keep the `set.json` key set identical to before (fixture); `ReferenceImageOut.source` is null; an image with `source` round-trips; `test_references.py` is unchanged and green |
| `test_segmentation_api.py` (S5; `fakes_segmentation.py`: deterministic colour-threshold masks) | Reference-image and job sources. The reference snapshot is `normalize_to_png` output: for a JPEG upload, `source_pixels_sha256` equals the pixel hash of the job's `inputs/<image>.png` created from the same set image. Snapshot isolation (replacing the set image after creation does not affect the run). 409 for a job that is not completed. Degradation warning when the parser is missing. File routes and the value regex. F auth |
| `test_frame_recovery.py` (S5) | Synthetic RGBA → replica crop → `recover_frame` exact (`recompute`, verified). Template match recovers a known offset for `background_removed`. Identity for `untouched`. No code path returns `method == "persisted"`. The unverified case gives a warning |
| `test_seg_fusion.py` (S5) | z-priority; area and confidence gates → `unknown`; left/right split; the eyebrow rule rejects a box that is not above an eye (tiara case); the lash band lies inside the eye bbox margin; the lid curve is monotonic; the eyelid ring excludes eye/brow/lash |
| `test/engine/test_prepared_conditioning.py` (S5; torch; skipped without trellis2) | `crop_from_alpha` equals upstream `preprocess_image` pixel for pixel (odd and even sizes, border bboxes, inputs > 1024), and the stub-rembg path too |
| `test/engine/test_aux_isolation.py` (S2; torch CPU) | §4.9: `torch_globals_guard()` restores every guarded global (incl. `allow_fp16_reduced_precision_reduction`, `allow_bf16_reduced_precision_reduction` and `torch.get_num_threads()`) after a callable flips them all; the caplog record `[torch_guard] restored <names>` lists exactly the flipped set (sorted names equal); a callable that flips nothing logs no record; two same-seed fake-pipeline runs around such a callable have identical call sequences and RNG fingerprints; negative control without the guard differs |
| `test/engine/test_raster_lifter.py` (S7; GPU, under `gpu_lock.py`) | Face-ID decode; camera distance rule `1.05·r_bs/sin(fov/2)` keeps the silhouette inside the frame at FOV 10° and ≥ 50 % of it at 50°; front camera orientation (image-right = +X, rows top-down); silhouette fit IoU ≥ 0.9 on a synthetic textured mesh; a 2 M-face raster; `render_textured` matches the atlas colour at a known face |
| `test_run_retention.py` (S8; `FakeWorkflowStore` with the frozen §7.2 API) | Age and count limits per store; runs whose ids are in `active_child_ids()` are skipped; terminal workflows past the limits are deleted through `delete()`; orphan export-cache entries are purged; the LRU cap |
| `test_tuning_gate.py` (S9) | With `mesh_tuning_enabled=False` (explicit): `POST /jobs/{id}/tunings` gives 404 for an unknown job, 409 for an incomplete job, then 503 `feature_disabled` with `details.feature == "mesh_tuning"`; list and read of runs still return 200. With the default test environment (`true`): 202 |
| `test_part_analysis_api.py` (S10; FakeEngine writing a textured two-shell GLB; `fakes_lifting.py`: numpy orthographic face-ID rasterizer) | `components` end to end on the CPU (manifest valid, files served). Semantic strategy with the fake segmenter and lifter. 409 for an incomplete job. Job delete cancels an active analysis and removes `parts/`. Retention deleting a job mid-run gives `job_deleted` and no folder resurrection. `?job_id=` scoping of `part_analysis.updated`. On-demand per-part GLB and ZIP are cached and purged on delete. `latest` semantics. `result.assembled_url` is non-null with `export: {"per_part_glbs": false}` and with `export: {}`; `export: {"assembled_glb": false}` is 422 `extra_forbidden`; `min_part_faces: null` resolves to `max(50, round(1e-4·F))` in `manifest.strategy`. A `segmentation_id` of an opaque-upload `sg_` gives `strategy.segmentation.source == "internal"` plus warning `segmentation_rerun_for_alignment`; a real-alpha upload whose `source_pixels_sha256` matches gives `"reused"` |
| `test_workflow_compiler.py` (S11) | Each template compiles. Every issue code of §4.7.2 on a crafted graph. The trivial graph gives `mode == "job"` and `request.reconstruction.settings` equal to the node's params (no `job_request` field exists). A Segmentation node feeding no `part_extraction.masks` gives `unsupported_topology` with `node_id` = that node. The Export node's `{per_part_glbs: true}` compiles to `request.parts.export.per_part_glbs == true`. Simple form ≡ graph form (equal `WorkflowRequest`) for the 6 valid combinations of {segmentation, parts, per_part_glbs} (of the 8, the two with segmentation on and parts off are 422 `conflicting_fields`, §4.7.1, and are asserted as such). Refine nodes give `node_locked`. Unavailable nodes give `capability_unavailable`. Every catalog node type has `params_schema.properties`. When `test/frontend/fixtures/graphs/*.json` exist (UI-owned, each with `expected_issues`), each compiles to exactly those issue codes (UI BR7) |
| `test_workflows_api.py` (S11) | Image + parts chain to completed. Prompt one-shot. Job-source rebuild. With `segmentation` + `parts`, the `pa_` child is created with `segmentation_id == outputs.segmentation_id`. 422 `conflicting_fields` for `segmentation` without `parts` and for a non-null `parts.segmentation_id`; 422 `extra_forbidden` for a top-level `export`. A child failure gives `step_failed` with outputs kept. Cancel mid-reconstruction cancels the job. 429 at creation when a queue is full; a child-creation 429 later gives `queue_full`. Restart marks `interrupted`. `graph_too_large` and `conflicting_fields`. Children survive workflow delete. The sweep recovers a dropped event (bus subscription closed manually). Step `artifacts` and `node_id`. `steps[].warning_count` equals the child's `len(warnings)` (a fake `ig_` with a `t2i_touches_border` warning → 1 on `text_to_image`; `reconstruction` and `export` → 0). A failed `pa_` child gives `error.details.child_error == {code, message, details}` copied from the child's `error` |
| `test_vendor_import.py` (S6; torch-free; synthetic vendored tree under `test/.runtime/vendor_fixture/` with `utils/__init__.py` and `models/x.py` doing `from utils import helper`) | `VendoredPackage("fixture", root).import_module("models.x")` binds `helper` from the synthetic tree; afterwards `sys.path` is unchanged and `sys.modules["models"].__file__` / `sys.modules["utils"].__file__` still point under `backend/`; a tree using `importlib.import_module("utils")` is reported by `scan_dynamic_imports()` (→ registry `incompatible`) |
| Smoke (real weights, skipped when files are absent; every run under `gpu_lock.py`): `test/aux_models/test_smoke_zimage.py` (S4), `test_smoke_segmentation.py` (S5), `test_smoke_parsers.py` (S6) | Z-Image: `load()` on a CPU thread, then the first `.to("cuda")` takes < 30 s (time logged); one 512×512 image at 2 steps, finite values, < 1 GB allocated after offload. GDINO + SAM 2.1 on `test/images/ANIME_CHARACTER_01` and `ALICE`: non-empty `hair`, `body.head.face` and both `eye.*` masks; warm time logged; the GDINO batch count per image is logged and `last_class_ids` covers every requested class id. Parsers: taxonomy masks; `sys.path` unchanged after the provider import. Z-Image and segmentation smokes run each GPU call inside `torch_globals_guard()` and copy the `[torch_guard] restored …` records (caplog, logger `inference.providers.vram`) into `test/outputs/part_aware/torch_guard.json` under keys `s4_zimage` / `s5_segmentation` (§4.9; informational, not a pass/fail gate) |
| Manual GPU run `test/part_aware/run_parts_smoke.py` (S10; under `gpu_lock.py --label s10`) | The script starts the backend itself with `IMG3D_RUNTIME_DIR=E:\14-OCT-2026\Img3D\test\.runtime\parts_smoke` and `IMG3D_LOG_DIR=E:\14-OCT-2026\Img3D\test\.runtime\parts_smoke\logs` (built as `REPO/"test"/".runtime"/"parts_smoke"`, §10 intro) and applies the runtime refusal rule (paths under `REPO/"test"`, the `Configuration:` log line, `job.json` under the test runtime; exit 2 otherwise); ALICE and ANIME_CHARACTER_01 jobs created fresh through `POST /jobs` (or, with `--from-fixture`, the `alice_front`/`anime_alice` fixtures copied in); `EXCLUDED_JOB_IDS` refusal (exit 2). Per job: `pa_` components and semantic_parts (views 0 and 8): manifest valid, reassembly exact, `model.glb` hash unchanged, **`unlabelled_face_ratio ≤ 0.10`** and **`cut_partner_consistency ≥ 0.90`** on both, timings and camera IoU written to `test/outputs/part_aware/`; every `[torch_guard] restored …` message of `<log_dir>/img3d.log` copied into `test/outputs/part_aware/torch_guard.json` under key `s10_parts` (§4.9). Then the real TRELLIS.2 smoke shows unchanged behaviour |

---------------------------------------------------------------------------------------------------
## 11. Implementation slices (disjoint file ownership)
Rules:
- "Owns" means only that slice creates or edits the file. Ownership of the S1 stub files (empty routers, stub
  services) passes to the named slice once S1 has merged.
- Interfaces frozen in S1 (`schemas/*`, `models/*`, `parts/{types,taxonomy,manifest}.py`, `inference/*/base.py`,
  `services/workflow_catalog.py`, the `storage/workflow_store.py` API of §7.2, the service constructors) change only
  with S1-owner sign-off.
- Every slice ends with the full backend suite green and I1–I12 respected.
- **The S1 container resolves executors and providers lazily** through dotted-path tables, e.g.
  `"pipelines.image_generation:ImageGenerationExecutor"`. A module that is not merged yet makes its feature report
  `available:false, code:"not_implemented"`. Later slices therefore never edit `core/part_aware_container.py`.

| Slice | Phase | Owns | Depends on | Acceptance criteria |
|---|---|---|---|---|
| **S0 Contract + guards** | A1 (sequential) | `.claude/skills/img3d-architecture/api-contract.md` (new §11 "Part-aware layer" with §2–§8 of this document; §2 settings rows; §4 error codes; §8 the five WS events; plus the drift fixes from contract map §6.1 item 10), `.claude/skills/img3d-architecture/SKILL.md` (layering note), `.claude/skills/img3d-part-aware/SKILL.md` (new index of research, design and decisions), `test/backend/test_protected_files.py`, `test/backend/protected_files.sha256`, `test/backend/test_legacy_invariance.py`, `test/backend/fixtures/legacy_job_v1/job.json` | – | The user approves the contract. 182 + new tests green. The hash file covers §1.3 (without `generation_worker.py`). `test_legacy_invariance.py` items legacy-load, retry and key identity are green; the `model.glb`-untouched item is skipped until S10 |
| **S1 Skeleton + frozen interfaces** | A2 (sequential) | EDIT `config/settings.py`, `workers/generation_worker.py`, `core/container.py`, `core/lifecycle.py`, `main.py`, `api/router.py`, `api/websocket_hub.py`, `schemas/events.py`. NEW `core/feature_errors.py`, `core/part_aware_container.py`, `api/part_routes.py`, `api/part_deps.py`, empty `api/v1/{features,model_registry,image_generations,segmentations,part_analyses,workflows}.py`, `schemas/{runs,features,model_registry,image_generation,segmentation,parts,workflows}.py`, `models/{runs,image_generation,segmentation,part_analysis,workflow}.py`, `storage/{run_store,run_layout}.py`, frozen-API stub `storage/workflow_store.py` (§7.2; S11 implements it), `pipelines/runs/*`, `workers/run_worker.py`, `services/run_events.py`, `services/workflow_catalog.py`, stub `services/{feature,model_registry,image_generation,segmentation,part_analysis,workflow}_service.py` + `services/part_files.py`, `inference/providers/{__init__,base}.py`, `inference/{imagegen,segmentation,parts}/{__init__,base}.py`, `parts/{__init__,types,taxonomy,manifest}.py`; tests `test_gpu_task_seam.py`, `test_import_boundaries.py`, `test_run_framework.py`, `test_taxonomy_manifest.py`; adds `generation_worker.py` to `protected_files.sha256` (S0 sign-off) | S0 | App starts with `part_aware_enabled` true and false. The new routers are mounted and empty (404). All old tests green. The seam, boundary, framework and taxonomy/manifest tests are green |
| **S2 Registry + discovery** | B | `inference/providers/{catalog,registry,unavailable,vram}.py`, `services/feature_service.py`, `services/model_registry_service.py`, `api/v1/features.py`, `api/v1/model_registry.py`, `test_model_registry.py`, `test_features_api.py`, `test/engine/test_aux_isolation.py` | S1 | `/features` and `/models` per §6.1 with the real on-disk states on this machine: Z-Image, GDINO, SAM 2.1, Florence-2 and the lifter `installed`; See-through and Sapiens2 `vendored_source_missing` (until S6); the 3D entries `unsupported` and hidden; the refine nodes `hidden_by_default`. Load/unload endpoints work with a fake provider. `torch_globals_guard` isolation test green |
| **S3a Parts geometry core** | B | `parts/{gltf_io,weld,components,shells,partition,seams,reassembly}.py`, `test/backend/parts_fixtures.py`, `test_gltf_io.py`, `test_parts_geometry.py` | S1 | Tests green. Bit-exact reassembly. Double-walled sphere = 1 part. `pair_opposite_faces` pairs the rim-joined hemisphere walls. On the real `alice_front` fixture (read in place, nothing written): weld + components + shells + partners < 60 s on the CPU and component 0 partner coverage ≥ 70 % (both logged) |
| **S3b Parts asset layer** | B (final acceptance after S3a merges) | `parts/{labels,policy,relations,frames,buffers,export,validate,publish,reader,consumers,lineage}.py`, `test_parts_labels.py`, `test_parts_policy.py`, `test_parts_export.py`, `test_reader_consumers.py` | S1 (S3a for integration) | Tests green. The exported manifest validates against the S1 model and has the §3.11 shape. The consumer registry refuses while disabled |
| **S4 Prompt-to-image + provenance** | B | `pipelines/image_generation.py`, `pipelines/prompt_templates.py`, `processing/imagegen_readiness.py`, `services/image_generation_service.py`, `api/v1/image_generations.py`, `inference/imagegen/zimage.py`, EDIT `models/reference.py`, `schemas/references.py`, `services/reference_service.py`, `backend/requirements.txt`, `test/backend/fakes_imagegen.py`, `test_image_generation_api.py`, `test_prompt_templates.py`, `test_reference_provenance.py`, `test/aux_models/test_smoke_zimage.py` | S1 (S2 for the real registry; the tests use overrides) | API tests green with fakes. The real smoke (under `gpu_lock.py`) generates one image, and the first `.to("cuda")` after the CPU `load()` takes < 30 s (logged; if not, report the measured time and keep the CPU load step). `uv pip check` is clean. The TRELLIS runtime probe and the real TRELLIS.2 smoke still pass (under `gpu_lock.py`). `test_references.py` is unchanged and green |
| **S5 Segmentation** | B | `pipelines/segmentation.py`, `processing/conditioning.py`, `processing/seg2d/{__init__,fusion,eyes}.py`, `services/segmentation_service.py`, `api/v1/segmentations.py`, `inference/segmentation/{gdino,sam2,florence2}.py`, `test/backend/fakes_segmentation.py`, `test_segmentation_api.py`, `test_frame_recovery.py`, `test_seg_fusion.py`, `test/engine/test_prepared_conditioning.py`, `test/aux_models/test_smoke_segmentation.py` | S1 | The baseline profile on `test/images/ALICE` and `test/images/ANIME_CHARACTER_01` (smoke under `gpu_lock.py`) yields hair, face and both eye masks, with no eyebrow on the tiara (head pass + rule). Warm time per image and the GDINO batch count are logged. Degradation is honest. The parity test is green |
| **S6 Vendored parsers** (Q4 approved) | B | `backend/third_party/see-through/`, `backend/third_party/sapiens2/` (pinned submodules), `.gitmodules`, `backend/third_party/README.md`, `THIRD_PARTY_NOTICES.md` (S6 rows first; see disjointness), `inference/providers/vendor_import.py`, `inference/segmentation/{see_through,sapiens2}.py`, `test/backend/test_vendor_import.py`, `test/aux_models/test_smoke_parsers.py` | S1, S5 (fusion `label_map` contract) | Registry states flip to `installed`. The `anime` and `realistic` profiles produce taxonomy masks on the samples (under `gpu_lock.py`). `sys.path` is unchanged after a provider import and `sys.modules["models"/"utils"]` still point under `backend/`. The Sapiens2 conditions appear in `ModelOut.license.conditions`. No pip install (stop and report if one is unavoidable) |
| **S7 Raster lifter** | B | `inference/parts/{raster_lifter,cameras}.py`, `test/backend/fakes_lifting.py`, `test/engine/test_raster_lifter.py` | S1 | The GPU test is green (under `gpu_lock.py`). On the `alice_front` and `anime_alice` fixtures of `test/part_aware/fixtures.json`, **copied** into `test/.runtime/s7/<case>/` (never read from `backend/runtime/jobs`), the camera fit reaches IoU ≥ 0.6 (logged) |
| **S8 Retention** | B | `services/run_retention_service.py`, `test_run_retention.py` | S1 (frozen `WorkflowStore` API, §7.2) | Tests green with `FakeWorkflowStore` |
| **S9 Tuning gate** (Q3 default) | B | `services/mesh_tuner_service.py`, `test/conftest.py` (one line), `test_tuning_gate.py` | S1 (`FeatureDisabledError`, the setting) | `test_tuning_gate.py` green. All 20 tuner API tests, `test_blender_runtime_api.py` and the CLI test green **with no other edits** |
| **S10 Part analysis** | C | `pipelines/part_analysis.py`, `services/part_analysis_service.py`, `services/part_files.py`, `api/v1/part_analyses.py`, `test_part_analysis_api.py`, `test/part_aware/run_parts_smoke.py`; un-skips the `model.glb`-untouched item in `test_legacy_invariance.py` (S0 file; one line, with S0 sign-off) | S2, S3a, S3b, S5, S7 | `run_parts_smoke.py` (§10.2; under `gpu_lock.py`, test runtime, `EXCLUDED_JOB_IDS` refusal) on jobs it creates from `test/images/ALICE` and `test/images/ANIME_CHARACTER_01`: `components` and `semantic_parts` with manifest valid, reassembly exact, `model.glb` hash unchanged, `unlabelled_face_ratio ≤ 0.10`, `cut_partner_consistency ≥ 0.90`, timings and IoU recorded, views 0 vs 8 compared in the smoke report |
| **S11 Workflows** | C | `services/workflow_compiler.py`, `services/workflow_service.py`, `storage/workflow_store.py` (implements the S1 stub API), `api/v1/workflows.py`, `test_workflow_compiler.py`, `test_workflows_api.py` | S2, S4, S5, S10 | Prompt → parts end to end with fakes. One real run (Z-Image-Turbo → TRELLIS.2 → `semantic_parts`, under `gpu_lock.py`, test runtime) completes, with outputs inspectable from the API. Validate returns the documented issue codes |
| **Frontend mock** | (UI plan) | `test/frontend/mock_backend.mjs` additions | S0 contract | Owned by the `UI_DESIGN.md` slice plan. It must mirror §6 shapes exactly, with scenarios `full`, `no-t2i`, `no-seg`, `disabled` and `legacy404` |

Order:
1. **Phase A:** S0 → S1.
2. **Phase B (parallel; GPU steps serialised by `gpu_lock.py`):** S2, S3a, S3b, S4, S5, S6, S7, S8, S9.
3. **Phase C:** S10, then S11.

SD1 and SD2 are not approved (Q5, Q6) and are no longer slices; their specifications are kept in §13.1 (D1, D12).

Disjointness check:
- `config/settings.py`, `schemas/*` and `models/*` belong to S1 only. The exceptions are `models/reference.py` and
  `schemas/references.py`, which belong to S4.
- `storage/workflow_store.py`: S1 writes the frozen-API stub, then S11 owns it.
- `requirements.txt` belongs to S4 only. `THIRD_PARTY_NOTICES.md` belongs to S6, then to the UI slice F11 (xyflow,
  `@xyflow/system`, d3-*, classcat and nested zustand 4 rows) after S6 has merged; until that hand-over F11 gives its
  rows to the S6 owner. `test/conftest.py` belongs to
  S9 only.
- Each router file and each service has a single owner after S1.

---------------------------------------------------------------------------------------------------
## 12. Resolved questions (2026-10-16) and what is still open
The former open questions Q1–Q7 are resolved. "User decision" = decided by the user (`DECISIONS.md` §Decisions).
"Assistant default" = applied by the assistant on 2026-10-16 and recorded in `DECISIONS.md` §"Defaults applied
2026-10-16"; **the user may override any of them** at any time, and an override changes only the rows listed under
"Applied in".

| Q | Resolution | Source | Applied in |
|---|---|---|---|
| Q1 Approved models | The six `DECISIONS.md` models are approved by default (`default_approved=True`): Z-Image-Turbo, grounding-dino-base, sam2.1-hiera-large, Florence-2-large, See-through `l2d_sam_iter2`, sapiens2-seg-0.4b. `IMG3D_APPROVED_MODELS` stays as an override (`[]` approves nothing) | **user decision** | §0.3, §5.3, §5.4, §8.1 |
| Q2 Manual candidate pick inside a workflow | Not in v1. Review-first is the two-call flow (`POST /image-generations` → `…/use` → `POST /jobs` or `POST /workflows`); one-shot uses `auto_first`. D9 stays deferred | assistant default (user may override) | §4.4.8, §13.1 D9 |
| Q3 Tuning code gate | Code gate in `MeshTunerService.create_run` plus the one `test/conftest.py` line (§8.2 G1/G2). The Blender detection/download endpoints stay as they are; every tuner and Blender UI mount point is hidden in all four modes; the four refine nodes are `hidden_by_default: true` | assistant default (user may override) | §8.2, §6.1, §4.7.2, S9; `UI_DESIGN.md` §3.9 |
| Q4 Vendoring | See-through and Sapiens2 model code are vendored as pinned git submodules under `backend/third_party/` (small modules only, never their requirements), imported through `vendor_import.py` (never `sys.path`); `THIRD_PARTY_NOTICES.md` rows; the Sapiens2 conditions are shown in the UI from `ModelOut.license.conditions` | **user decision** | S6, §5.4, §5.6, §9.2; `UI_DESIGN.md` §6.2 |
| Q5 SD1 generation intermediates | Not approved → deferred (D1). `persist_generation_intermediates` stays inert and is always reported `false`; the protected TRELLIS files are untouched; `persisted` stays a reserved literal with no code path | assistant default (user may override) | §1.3, §4.5.2, §4.6.3, §7.1, §8.1, §13.1 D1 |
| Q6 SD2 Z-Image bf16 re-save | Not approved → deferred (D12). The cold-load cost is attacked instead by moving the disk read to the CPU `load_model` step (§4.4.4) | assistant default (user may override) | §4.4.4, §13.1 D12 |
| Q7 Eye cut scope | Cut only the lifted `eye.*` faces; the eyelid ring (values 26/27) stays a base region | assistant default (user may override) | §2.4 rule 4 |

**Genuinely open items: none that blocks implementation.** The remaining user touch-points are gates, not open
design questions:
1. **S0 gate.** The user approves the merged API contract before S1 starts (§11 S0 acceptance).
2. **Tuning stays on hold** until the user explicitly approves this milestone; nothing here starts it.
3. **Conditional re-asks** (only if a measurement forces them): if S4 measures the first `.to("cuda")` after the CPU
   `load()` at ≥ 30 s, Q6 (SD2) is put to the user again with that number; if S6 finds that vendored code needs a pip
   package that is not installed, S6 stops and asks instead of installing (§9.2).

---------------------------------------------------------------------------------------------------
## 13. Deferred items, risks, synthesis record

### 13.1 Deferred (each needs explicit approval; none is in this milestone)
| Id | Item | Why deferred |
|---|---|---|
| D1 | SD1 generation intermediates — **not approved 2026-10-16 (Q5)**. Spec kept for a future approval: behind `IMG3D_PERSIST_GENERATION_INTERMEDIATES`, the TRELLIS adapter (`inference/trellis/generation.py`, `imaging.py`, protected: hash update in the same commit) writes `jobs/<job>/intermediates/{conditioning_rgba.png, conditioning.json, latents.npz}` after `decode_latent`, using no RNG; `test/engine/test_capture_parity.py` asserts flag off = no new files and unchanged `test_generation_parity.py`, flag on = same call sequence, same RNG fingerprints and pixel-equal conditioning; `pa_` then reports `strategy.segmentation.source = "persisted"` and `recover_frame` gains the `persisted` method | Touches the protected TRELLIS adapter |
| D2 | `per_part_regeneration` (Tripo-style: generate a part from a masked crop, then register it) | Loses registration (unit-cube renormalization); multiplies the 133–579 s runtime |
| D3 | Part-clean atlas (charts per part, one xatlas pack, one bake; `trellis2_internals.md` §4.3) | Re-bake cost; would replace `charts_cross_boundary` |
| D4 | Tuning consumers (Blender per-part repair, eye and rig correction, lash cards, amodal completion) | User rule: tuning stays off until approved |
| D5 | Model install/download API | Models are already on disk; manual install keeps the state machine small |
| D6 | `EXT_mesh_features` / `EXT_structural_metadata` declarations | Not needed by the viewer; the attribute and extras carry the data |
| D7 | Engine offload for `low_vram=false` (an additive `InferenceEngine.release_vram()`) | Would edit protected files; the default `low_vram=true` already frees the GPU |
| D8 | Voxel-space labelling from persisted latents | Needs D1 (SD1, not approved) |
| D9 | Paused workflow step for manual candidate selection | Q2 (assistant default: not in v1; the two-call review flow covers it) |
| D10 | 3D segmenters, completers and part generators (P3-SAM, PartField, HoloPart, X-Part) | Need spconv / torch_cluster / Linux sidecars; catalog shows them as `unsupported` |
| D11 | RGBA candidates for prompt jobs (colour-key matte, so jobs take the exact `alpha_crop` path) | A hard key matte would change reconstruction quality compared with BiRefNet |
| D12 | SD2 Z-Image bf16 re-save — **not approved 2026-10-16 (Q6)**. Spec kept: `backend/scripts/convert_zimage_bf16.py` writes `backend/.model/t2i/Z-Image-Turbo-bf16/` (about 12 GB on E:, selected through `IMG3D_MODEL_PATHS`), measures the cold `.to("cuda")`, leaves the original folder untouched | Disk use; the CPU `load_model` step (§4.4.4) is tried first |
| D13 | Reserved `WorkflowRequest` fields for refine nodes | Added only with an approved tuning node (§4.7.2 extension rule) |

### 13.2 Risks and mitigations
| # | Risk | Mitigation |
|---|---|---|
| R1 | An auxiliary GPU step OOMs or fragments VRAM before a TRELLIS job | Single executor; offload + `empty_cache` in `finally`; static and live VRAM checks; OOM fails only that run |
| R2 | Shutdown leaves auxiliary work on the GPU thread | `part_aware.stop()` runs before `worker.stop()`; tested in `test_gpu_task_seam.py` |
| R3 | A dropped event leaves a workflow stuck | 5 s reconciliation sweep plus startup advance; tested |
| R4 | Retention deletes a job during an analysis | Temp work dir, `job_exists` re-check, `RunStore.save` refusal, atomic publish; tested |
| R5 | Labels are misaligned with the mesh | Default internal segmentation on the exact conditioning image (`preview.png`); frame verification; `camera_fit_low_iou` → `partial` with halved confidences |
| R6 | Double shell and non-manifold junctions produce bogus parts, slivers, or cuts that take only the outer sheet | Detached inner-sheet merge plus per-face opposite-sheet partners with the partner ownership rule (§4.6.5 steps 2, 5.2, 6); colour-affinity propagation; smoothing; F-relative thresholds; open seam chains recorded honestly; S10 smoke gates `unlabelled_face_ratio ≤ 0.10` and `cut_partner_consistency ≥ 0.90` |
| R7 | Thin painted parts (brows, lashes) cannot be cut | `physical_deferred` by policy; masks, regions and the lid curve are kept for the future layer |
| R8 | The replica crop drifts from upstream | `test_prepared_conditioning.py` pixel equality; the replica is used only for frames and masks, never fed to TRELLIS |
| R9 | Disk growth | Per-part GLBs and ZIPs on demand into a capped cache; retention for the new stores |
| R10 | Licence drift (Sapiens2 conditions, nvdiffrast NC) | Licence metadata in `/models` and `/features`; manifest `provenance.licences`; notices in S6 |
| R11 | Vendored code is incompatible with Python 3.14 | Isolated provider modules; honest `vendored_source_missing` / `error`; the baseline profile keeps working |
| R12 | The Z-Image cold start surprises users (~5.5 min) or blocks image-to-3D jobs | The disk read runs in the CPU `load_model` step, off the GPU thread (§4.4.4); jobs-first gate (§4.3); `cold_start_s` in `/features`; the `loading` state; `POST /models/{id}/load` warm-up; models stay resident (idle unload off) |
| R15 | Auxiliary providers change torch globals that TRELLIS depends on | `torch_globals_guard()` around every auxiliary GPU callable; `test_aux_isolation.py` (§4.9) |
| R16 | Vendored code binds to Img3D's `models`/`utils` packages | `vendor_import.py` private namespace + AST import rewrite; `sys.path` never changed (§5.6) |
| R17 | Parallel slices overload the GPU or touch excluded jobs | `gpu_lock.py` around every real-weight command; inputs only from `test/images` and copied fixtures; `EXCLUDED_JOB_IDS` refusal (§10) |
| R13 | The protected-file test blocks a legitimate change | Intended: such a change needs approval and a deliberate hash update in the same commit |
| R14 | trimesh does not load the hand-written GLB in some edge case | `test_gltf_io.py` loads the output with trimesh and checks the stats; three.js and Blender compatibility of `_FEATURE_ID_0` (UNSIGNED_SHORT) is checked in the S10 smoke [A] |

### 13.3 Synthesis record (judge findings → resolution)
| Finding (judges) | Resolution in this document |
|---|---|
| Base = A (highest total, 127.5): frozen working path, four seams, run resources | §0.3, §1.2, §1.3, §4.1–§4.3 |
| A's tuner endpoints stay callable without a `.env` edit (all three judges) | Code gate in `create_run` (§8.2 G1) with a one-line test environment opt-in (G2). B's lifecycle change, a fatal flaw, is **not** copied (G3) |
| B's code gate would break `test_shutdown_interrupts_running_tuning` (make_settings direct) and `test_detection_on_startup_runs_in_background` | The opt-in goes into the root `test/conftest.py` environment, which every settings construction sees. The lifecycle is untouched. Verified against `test_mesh_tuner_api.py:57,404,522` and `test_blender_runtime_api.py:107` [V] |
| B/C would recreate `backend/constraints.txt` | §9.2: it exists; it is never recreated; it is edited in place only with approval |
| C's provider root `.model/providers/<id>` mismatched the on-disk layout | `local_subdir` = `t2i/Z-Image-Turbo`, `seg/<name>` under `aux_model_root` [V] (§5.4) |
| C added keys to every legacy `job.json` | No job model or `job.json` change at all (§0.3, I1) |
| Z-Image budgeted at 15 GB vs the measured 23.3 GB peak | Catalog 24.0 GB, `cold_load_s` 337, `loading` state, static and live checks (§4.8, §5.4) |
| A's `components` strategy would emit the double-shell inner wall as a part | Inner-sheet merge (§4.6.5 step 2) plus B's shell policy (§2.4) |
| Graft B: `face_owner` vs `face_labels`, vmaps, arrays-not-submesh, bit-exact UV/normal reassembly | §3.2–§3.4, §3.12 (a direct GLB writer: stronger than trimesh `Trimesh(process=False)`, because it also preserves the atlas bytes) |
| Graft B: `AssetReader`, consumer `accepts`/`describe`, enriched part records, buffer descriptors, base re-hash, atomic publish | §3.3, §3.6–§3.10, §3.13, §3.14 |
| Graft C: locked refine nodes, templates, `legacy_job_node_map`, server-side validation | §4.7.2, §6.1 `nodes`/`workflow`, §6.5 catalog/validate |
| Graft C: live VRAM check | §4.8 rule 2 |
| Graft B/C: torch-free conditioning replica with pixel-equality test; D1 specified as a flag-off feature | §4.5.2, D1 (§13.1; not approved, Q5) |
| Graft B: `ReferenceImage.source` provenance; prompt hygiene | §1.2 S-f, §4.4.2, §6.7 |
| Judge 3: workflows v1 linear and fail-fast, no "waiting" retry; decide on manual selection | §4.7.4; manual pick resolved by Q2 (assistant default: two-call review flow) |
| Judge 1: confirm `DECISIONS.md` approval or default to none | Q1 resolved by the user: the six models approved by default; `IMG3D_APPROVED_MODELS=[]` flips it |
| B: CPU-heavy partitioning on the GPU thread blocks the queue | Run phases: CPU work on `parts-cpu` (§4.3, §4.6.4) |
| A: per-part GLBs off; B: on (disk N×) | Off by default, built **on demand** into a capped cache (§6.4, §7.2) |

---------------------------------------------------------------------------------------------------
## Revision log (2026-10-16)
Each critique item was checked against the code (or, for the blocker, re-measured on the real fixture) before any
change. "UI scope" = the item concerns only `UI_DESIGN.md`; it is left to the frontend reviser and needs no backend
change.

### `design/critic_backend_feasibility.md`
| # | Sev. | Verification | Resolution |
|---|---|---|---|
| 1 | blocker | Re-measured on `test/outputs/engine_smoke/job_smoke165749/model.glb`: 389 welded components, component 0 = 90.2 % of F, 78.5 % of its faces have an opposite-normal face within 4/1536 inside component 0 | **Applied.** `parts/shells.py::pair_opposite_faces`, partner fill (§4.6.5 step 5.2), `face_flags.u8` (§3.3), partner ownership rule (step 6), summary `partner_filled_ratio` / `cut_partner_consistency`, rim-joined hemisphere fixture + tests, S3a real-fixture check, S10 gates `unlabelled_face_ratio ≤ 0.10` and `cut_partner_consistency ≥ 0.90` |
| 2 | major | `polygon_target` default 500 000 [V `test_health_system.py:61-69`] | **Applied.** `min_island_faces = max(8, round(2e-5·F))`; eyes/brows/lashes/face islands never merged; component fill only fills `unknown`, never on component 0 or a component ≥ 25 % of F; `min_part_faces` default `null` → `max(50, round(1e-4·F))`; 500 k test |
| 3 | major | [V `environment.py:195-213, :565`, `engine.py:199-201`, `BiRefNet.py:10-11`] | **Applied in part.** `ProviderRegistry.provider()` calls `configure_hf_environment` before every lazy import and asserts `HF_HOME`; subprocess test. **Rejected:** the early `enable_hf_offline()` call. TRELLIS switches offline only after finding its aux models locally and may download them otherwise [V `environment.py:510-525`, `loader.py:250-281`], so an early global switch could break the image-to-3D path |
| 4 | major | TRELLIS noise = default-dtype CPU `torch.randn` [V `trellis2_image_to_3d.py:208,254,299,343,412`] | **Applied.** `vram.py::torch_globals_guard()` around every auxiliary GPU callable (§4.9, I5). Tested with the parity fake pipeline (`test/engine/test_aux_isolation.py`) instead of a real-GPU fingerprint, which real runs do not expose |
| 5 | major | Job marked running before it is queued on the FIFO executor [V `generation_worker.py:316-338`] | **Applied.** `load_model` is a CPU step that fully materialises the weights; per-component GPU moves; jobs-first gate (`job_queue`, `running_job_id`, `run_max_defer_s`, warning `gpu_defer_timeout`); warm-up = CPU load + GPU warm; S4 acceptance `< 30 s` [A] |
| 6 | major | [V `main.py:26-35`, `environment.py:188-192`; the TRELLIS root has `configs/`, `assets/`, `data_toolkit/`] | **Applied.** `inference/providers/vendor_import.py`: private `img3d_vendor.<name>` namespace + AST import rewrite, `sys.path` untouched (pinned submodules cannot be edited, so "rewrite the imports in the vendoring commit" is not possible); dynamic self-imports → `incompatible`; `test_vendor_import.py` |
| 7 | major | [V `test/part_aware/gpu_lock.py` docstring, `test/part_aware/fixtures.json`] | **Applied.** §10 real-weight rules (gpu_lock wrapper, copied fixtures, test runtime, `EXCLUDED_JOB_IDS`); S4–S7, S10, S11 acceptance; §9.3 |
| 8 | major | Doc-internal (S8 needed `workflow_store.py`, created only in S11) | **Applied** (option 2): S1 frozen-API stub `WorkflowStore.list_terminal/delete/active_child_ids` (§7.2); S11 implements it; S8 tests with a fake |
| 9 | minor | glTF 2.0 §3.6.2.4 vertex-attribute alignment | **Applied.** `_FEATURE_ID_0` in its own bufferView with `byteStride: 4`; test; three.js reads it as an `InterleavedBufferAttribute` (§3.12) |
| 10 | minor | Doc-internal | **Applied.** Unknown shells below `floater_max_faces` get no record (`geometry.floaters`); `face_part.u16` buffer; `region_faces` via `face_part`; overflow keeps its class value |
| 11 | minor | Doc-internal | **Applied.** Token cancel + `asyncio.shield` before the final status and `rmtree`; watchdog over active phase time; tests (§4.3 steps 5–7) |
| 12 | minor | [V `utils/files.py:18-30` `retry_os`] | **Applied.** `RunStore` uses `mkdir(parents=False)` → `job_deleted`; publish goes through `retry_os` |
| 13 | minor | Doc-internal | **Applied.** Candidates are generated one at a time, with `empty_cache` after each VAE decode |
| 14 | minor | [V `grounding-dino-base/config.json` `max_text_len: 256`] | **Applied.** Phrase batches of ≤ 200 tokens; the smoke logs the batch count and class coverage |
| 15 | minor | [V `generation.py:166-170`, untouched mode] | **Applied.** `distance = 1.05·r_bs/sin(fov/2)`; untouched foreground = `labels > 0` + 2D similarity fit |
| 16 | minor | [V `job_service.py:146-163` `normalize_to_png`] | **Applied.** `sg_` reference snapshot via `normalize_to_png`; `source_pixels_sha256` compared |
| 17 | minor | (a) [M numpy 2.4.1 inverse shape `(1618334, 1)`]; (b) [V `device.py:42-43`]; (c) [V `settings.py:65,134`] | **Applied.** Weld snippet fixed; provider import catches `Exception` → `reason_code="error"`; `approved_models` uses `NoDecode` + a before-validator |
| 18 | minor | [V `test/backend/conftest.py:60-75`, `test_settings.py:18-25`] | **Applied** (same as requirements #15) |

### `design/critic_requirements.md`
| # | Sev. | Resolution |
|---|---|---|
| 1 | major | **Applied.** §12 rewritten as resolutions (user decisions vs assistant defaults); S6/S9 unblocked; Phase B lists both; the SD1/SD2 rows and Phase D removed (specs kept as D1/D12); §1.3, I2, §7.1, §8.1, the parity row, R8 and R12 updated; the `persisted` branch removed (literal reserved); Sapiens2 `LicenseInfo.conditions` + a `test_model_registry.py` assertion |
| 2 | major | **Applied (backend half).** §4.7.1 rule: `segmentation` requires `parts` (422 `conflicting_fields`); `parts.segmentation_id` must be null in workflows; the `pa_` gets the workflow's `sg_` id and reuses it only per §4.6.3. Graph: an unconnected Segmentation node → `unsupported_topology`. Tests added. The UI copy and derive changes are UI scope |
| 3 | major | **Applied.** `WorkflowRequest.export` removed; `PartExportOptions {per_part_glbs=false, previews=true}`; `assembled_glb` removed; `assembled_url` never null; the Export node compiles to `parts.export`; `part_extraction` params exclude `export` |
| 4 | major | UI scope (UI1 fingerprint baseline) |
| 5 | major | **Applied** (with backend #7): §10 intro, S7/S10 inputs named, `EXCLUDED_JOB_IDS`, gpu_lock |
| 6 | minor | UI scope |
| 7 | minor | **Applied.** `ReasonCode` += `not_implemented`, `engine_unavailable`; every strategy has `reason` |
| 8 | minor | **Applied.** `readiness.issues` use the `t2i_*` codes |
| 9 | minor | **Applied.** `limit=1..100 (20)`; `params_schema` for every node; `extras.img3d.id` and `_FEATURE_ID_0` on every node; progress units; BR6 confirmed |
| 10 | minor | **Applied (backend half).** `job_request` removed from `CompileResult` and `ValidationReport`; §6.9 row updated |
| 11 | minor | **Applied (backend half).** §4.4.8 and §6.1 state that one-shot needs `workflow.available`; the UI blocking rule is UI scope |
| 12 | minor | **Applied.** `FeaturesOut.models` = full `ModelOut[]`; test |
| 13 | minor | **Applied.** Refine nodes `hidden_by_default: true`; test |
| 14 | minor | **Applied in part.** Extension rule (catalog entry + one optional field + one `WORKFLOW_STEPS` row). **Rejected:** reserving `refine: list[RefineStepRequest]` now. A schema for a node without an executor is speculative tuning API, and adding an optional field later is additive (D13) |
| 15 | minor | **Applied.** `test_features_api.py` uses explicit `mesh_tuning_enabled=False` / `delenv`; §8.1 caution |

### `design/critic_frontend_feasibility.md`
| # | Resolution |
|---|---|
| 25 | **Applied.** §11 disjointness: `THIRD_PARTY_NOTICES.md` passes from S6 to F11 after S6 merges |
| 23 | Backend note only: §6.5 now states that the 304 has an empty body; the axios config is UI scope |
| 1–22, 24 | UI scope; no backend change needed (#15 duplicates requirements #6) |

### `UI_DESIGN.md` §13 Backend requests
BR1 closed (no `job_request`; the client posts its own body). BR2 closed (partial nested objects, §6.0 and §4.6.2;
no top-level `export`). BR3: §4.7.2 `params_schema` for every node. BR4: §3.12. BR5: §6.0 `limit` 1..100. BR6:
§6.1. BR7 accepted (`test_workflow_compiler.py` reads `test/frontend/fixtures/graphs/*.json` when present). BR8:
§4.3 and §4.7.5.

### Follow-up pass (re-check of the applied fixes, cross-doc diff with `UI_DESIGN.md`)
| Item | Verification | Resolution |
|---|---|---|
| backend #7 + requirements #5: relative `IMG3D_RUNTIME_DIR=test\.runtime\<slice>` | [V `config/settings.py:39-44,206-220`: relative paths resolve against `BACKEND_DIR`; `test/e2e/server_smoke.py:34-39,61-62` and `test/conftest.py:39-40` use absolute paths] | **Applied.** §10 intro and the §10.2 `run_parts_smoke.py` row use absolute `IMG3D_RUNTIME_DIR=E:\14-OCT-2026\Img3D\test\.runtime\<slice>` and `IMG3D_LOG_DIR=…\<slice>\logs`, built from `REPO = Path(__file__).resolve().parents[2]`. The critique's `GET /system` check is replaced: `SystemStatus` reports no paths [V `schemas/system.py:99-106`] and its keys are frozen. Instead, a runtime refusal rule (exit 2): paths under `REPO/"test"` before start, the `Configuration:` JSON log line in `<log_dir>/img3d.log` names the runtime dir [V `core/lifecycle.py:23`, `core/logging.py:268-271`], and `job.json` exists under the test runtime after each `POST /jobs`; no attach mode |
| backend #4: torch globals outside the guard list | [M torch 2.14.1: `allow_fp16_reduced_precision_reduction` / `allow_bf16_reduced_precision_reduction` readable and writable, default `True`; `get_num_threads()` = 12] | **Applied.** §4.9 guard list += the two reduced-precision flags and `torch.get_num_threads()`; the guard logs `[torch_guard] restored %s` with the sorted names; S4/S5/S10 smokes copy the records into `test/outputs/part_aware/torch_guard.json`; `test_aux_isolation.py` asserts the logged names equal the flipped set (§10.2) |
| Cross-doc: `WorkflowOut.error.details.child_error` untyped | [V `models/job.py:70-73`, `schemas/jobs.py:85-88` `JobError {code, message, details}`] | **Applied.** §4.7.4 `StepFailedDetails` with `child_error: {code, message, details}`; restated under §6.5; `test_workflows_api.py` assertion |
| Cross-doc: `PartAnalysisOut.result` nullability | Doc-internal (§4.6.4 skip rules) | **Applied.** §4.6.7: `overlay_url` and `camera_fit_iou` null for `components`; `preview_urls` `{}` when `previews` is skipped; the never-null fields listed |
| Cross-doc: `geometry.floaters` missing in the §3.11 example | Doc-internal | **Applied.** `"floaters": []` on the `hair`, `eyebrow.left` and `body.head.face` records; §3.10.1 says the key is always present |
| Cross-doc: step `warning` state had no data | Doc-internal (UI §4.3.9) | **Applied** (DESIGN option): additive `steps[].warning_count: int = 0` = `len(child.warnings)` (§4.7.5, §6.5); `test_workflows_api.py` assertion. The UI reads it instead of tracking child runs |
| Cross-doc: "all 8 combinations … that are valid" | Doc-internal (§4.7.1: segmentation without parts is 422) | **Applied.** §10.2 `test_workflow_compiler.py`: "the 6 valid combinations"; the 2 invalid ones are asserted as 422 |
| Cross-doc: §7.2 table split by the `WorkflowStore` code block | Doc-internal | **Applied.** The `cache/part_exports/` row is back in the table; the code block follows the table |
| Cross-doc: unwrapped GPU-track command in the §10 intro | Doc-internal | **Applied.** Shown wrapped in `gpu_lock.py --label <slice> -- …` |
| Cross-doc: everything else (endpoints, WS events, flags, `ReasonCode`, `GRAPH_ISSUE_CODES`, warning and error codes, URL fields) | Diff against UI §2.1/§2.2 | Matches; no change |
