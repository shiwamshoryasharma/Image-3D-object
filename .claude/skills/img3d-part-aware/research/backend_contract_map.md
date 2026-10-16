# Backend contract map: adding part-aware assets and prompt-to-3D (read-only analysis)

Date: 2026-10-16. Phase: READ-ONLY understanding. No project file was changed except this report.
Scope: `backend/api/**`, `schemas/**`, `services/**`, `core/**`, `config/settings.py`, `models/**`, `main.py`,
`workers/**`, `pipelines/**`, `storage/**`, `inference/base.py` (+ TRELLIS adapter outputs), the architecture skill
(`SKILL.md`, `api-contract.md`), `test/backend/**` and the frontend API/store files that consume the contract.

Legend: **[V]** means verified (I read the code or ran the command). **[A]** means an assumption or a design proposal
that has not been checked. All line numbers are file-relative and come from `grep -n`.

Commands I ran (none of them wrote files): `pytest --collect-only -p no:cacheprovider` with
`PYTHONDONTWRITEBYTECODE=1 -B`. Result: `test/backend` has 173 tests and `test/aux_models` has 9, for 182 in total.
This matches the "182 backend tests" in HANDOFF.md **[V]**. `git status` showed no new files after the run **[V]**. I did
not start the backend, run any GPU work or open the forbidden jobs. I read the metadata of one other job:
`runtime/jobs/job_046e19f0ca62/job.json`.

---------------------------------------------------------------------------------------------------
## 0. TL;DR

1. **The working path is narrow and well separated.** The flow is `POST /jobs` → `JobService.create_job` →
   `JobQueue` → `GenerationWorker` (one GPU thread) → `GenerationPipeline.run` (only `ImageTo3DPipeline`
   exists) → `InferenceEngine.generate` (TRELLIS) → `JobResultData` → `job.updated` over the WebSocket and
   `GET /jobs/{id}/result`.
   - The cleanest seam is `GenerationPipeline` (`pipelines/base.py:11`). The worker calls a single
     `self._pipeline.run(...)` (`workers/generation_worker.py:372`). A `PipelineRouter(GenerationPipeline)` that
     dispatches on a new `Job.kind` adds prompt-to-3D and part-aware runs without changing the worker, queue,
     events, recovery or retention.
2. **Everything persisted is additive-friendly.**
   - `DomainModel` uses `extra="ignore"` (`models/types.py:30`). New optional fields with defaults load old
     `job.json` files, and an old backend ignores new fields.
   - `RequestModel` uses `extra="forbid"` (`schemas/common.py:21`). New request fields must be added to the schema
     before any client sends them.
   - New artifacts can live under `runtime/jobs/<job>/parts/`. Job deletion and retention already remove whole job
     folders.
3. **Several shapes are pinned exactly by tests. Do not change them; add beside them.**
   - The `/system` top-level keys, `limits` and `defaults` (`test_health_system.py:37,54,61`).
   - `EngineCapabilities.to_dict()` (`test/engine/test_engine_unit.py:81`).
   - The stage table and percent formula (`test_progress.py:56-67`), stage index 9 for `done` and the allowed stage
     names (`test_jobs.py:31-41,86,96`).
   - The error body keys (`helpers.py:60-62`) and the tuning `RUN_KEYS` (`test_mesh_tuner_api.py:46`).
   - The frontend hard-codes the stage list (`frontend/src/lib/stages.ts:10-19`) and a closed `JobStage` union
     (`api/types.ts:34-47`).
   - Consequence: **do not add `JobStage` values.** Part and prompt steps should go in a new optional `Job.steps`
     plan, and feature discovery should go on a new endpoint.
4. **The biggest technical traps for part-aware generation:**
   - **Overwrite:** TRELLIS always writes `model.glb` and `preview.png` into `request.output_dir`, which is the job
     folder (`generation.py:51-52,173,306`; `image_to_3d.py:101`). Per-part reconstruction must use a separate
     `parts/<pt>/` output directory, or it overwrites the base model.
   - **Topology change:** export remeshes and simplifies (`export.py:41-56`, `remesh=True`). Labels on the decoded
     mesh do not survive into `model.glb`, so regions must be labelled on the exported GLB or transferred to it.
   - **Lost placement:** every TRELLIS run is normalized to the unit AABB `[-0.5, 0.5]` (`export.py:16`). A part
     reconstructed on its own loses its placement, so the manifest needs per-part transforms and alignment data.
   - **Single GPU thread:** any segmentation or text-to-image model must run on the GPU worker thread. `generate`
     holds the engine `_lifecycle_lock` for the whole run (`engine.py:110,359`).
5. **Tuning disable switch:**
   - Today `blender_enabled=True` by default (`settings.py:118`), and the test suite pins that default
     (`test_blender_runtime_api.py:311`).
   - Zero-code way to disable it now: put `IMG3D_BLENDER_ENABLED=false` in `backend/.env`. The `.env` file has no
     `IMG3D_*` keys today **[V]**.
   - This one setting makes detection, selection, release, download and `POST /jobs/{id}/tunings` return 503
     `blender_unavailable`, and skips startup detection.
   - Add a new feature report (`GET /api/v1/features` or `/pipeline/catalog`) so all four UI modes hide tuning.
   - Gate all future part-consuming refinement behind a new default-false setting (for example
     `IMG3D_PART_REFINEMENT_ENABLED`) that has no implementation yet.

---------------------------------------------------------------------------------------------------
## 1. Request → job → result flow (end to end, file:line) [V]

### 1.1 Bootstrap and wiring
| Step | Where |
|---|---|
| sys.path bootstrap (drop user site, backend first) | `main.py:26-37` |
| `create_app(settings, engine=, blender_runtime_factory=, blender_runner=)` (tests inject a FakeEngine) | `main.py:64-115` |
| Composition root `build_container(...)` | `main.py:84` → `core/container.py:63-173` |
| `ImageTo3DPipeline(engine, job_store, settings)`, the only pipeline | `core/container.py:84` |
| `GenerationWorker(engine, pipeline, job_store, queue, events, bus, on_system_changed)` | `core/container.py:87-95` |
| JobService, ResultService, RetentionService | `core/container.py:100-111` |
| Tuner block (BlenderRuntimeService, TuningStore, tuning JobQueue, TuningWorker, MeshTunerService), built unconditionally | `core/container.py:113-146` |
| Job delete hook → `MeshTunerService.on_job_deleting` | `core/container.py:147` |
| Lifespan startup: paths, purge temp, load stores, recover interrupted jobs and tunings, bind bus, start workers, optional preload, background tasks | `core/lifecycle.py:20-69` |
| Exception handlers, then middleware (RequestContext inside CORS), then `/api/v1` router, then the SPA mount | `main.py:100-114` |
| Router list (order matters only for path collisions) | `api/router.py:11-27` |
| DI accessors (`get_container` uses `connection.app.state.container`) | `api/deps.py:21-62` |
| Auth guards: JSON uses the Bearer header only (`AuthGuard`); files and WS also accept `?token=` (`FileAuthGuard`) | `api/deps.py:88-101` |

### 1.2 Reference set (the input side of image-to-3D)
- `POST /reference-sets` creates a set: `api/v1/references.py:48` → `ReferenceService.create_set`.
- `POST /reference-sets/{id}/images` uploads a multipart image.
  - Route: `api/v1/references.py:93-109`, using `LimitedUploadRoute`. That route checks auth before reading the
    body and enforces the streamed size limit (`api/upload_route.py:54-68`).
  - Service: `ReferenceService.upload_image` (`services/reference_service.py:130-197`).
    - It runs under the per-set `locked_set` lock (`:62-73`).
    - It validates by decoding with Pillow and makes a thumbnail.
    - It writes `<img>.<ext>` and `<img>_thumb.webp`.
    - It applies slot replacement and primary rules (`ReferenceSet.ensure_primary`, `models/reference.py:75`).
    - Storage: `ReferenceStore.save` (`storage/reference_store.py:83`) does an atomic write of
      `runtime/uploads/<rs>/set.json`.
- Reuse point for prompt-to-3D: `upload_image(set_id, data=png_bytes, filename=…, slot=…, label=…,
  make_primary=True)` is a plain async service call. A generated image can enter a set without going through HTTP.

### 1.3 Create job (event loop)
`POST /api/v1/jobs` → `api/v1/jobs.py:37-40` → `JobService.create_job` (`services/job_service.py:83-144`):
1. Resolve `reference_mode` from the request or `settings.reference_mode` (`:85`). Read `engine.capabilities()` (`:86`).
2. Take the per-set lock (`:88`). An empty set gives 422 (`:89-92`). A foreign `primary_image_id` gives 422
   (`:93-98`). An unknown set gives 404 from `locked_set` → `get_set`.
3. `validate_against_capabilities(settings, mode, caps)` lists every unsupported value as 422 with
   `loc ["body","settings",field]` (`services/generation_settings.py:13-49`). `get_strategy(mode).validate(caps)`
   (`:103`).
4. `queue.ensure_capacity()` returns 429 `queue_full` or 503 when closed (`workers/queue.py:51-58`).
5. Seed: `secrets.randbelow` if `randomize_seed` (`:106`). Normalized settings come from
   `settings.model_copy(update={"reference_mode": mode}).model_dump(mode="json")` (`:107`) and are stored
   verbatim as `Job.settings`.
6. Build `Job(id=new_job_id(), status=queued, engine=engine.name, references=[snapshot of every image in
   display order], …)` (`:108-124`).
7. `_prepare_inputs` copies every reference as a normalized PNG to `jobs/<job>/inputs/<img>.png`. It runs off the
   loop, one image at a time. On failure it rolls back the job folder and raises `StorageError` (`:146-167`).
8. `JobStore.save` (atomic `job.json`) and `queue.put`. On exception the job folder is deleted (`:127-132`).
9. Publish `job.updated` and send the debounced `system.updated` (`:142-143`). The response is 202 `JobOut`.

### 1.4 Run job (GPU thread)
`GenerationWorker._run` (`workers/generation_worker.py:257-276`) waits on `queue.get()`, then calls `_process`
(`:299-367`):
1. Create `JobProgressReporter(job, publish, persist=job_store.save, schedule_later)` (`:301-308`;
   `pipelines/progress.py:53`). Publishing is throttled to every 0.2 s with a trailing flush, and `job.json` is
   persisted at most every 2 s (`progress.py:33-34`).
2. `mutate(_start)` sets status `running` and stage `preprocessing` or `waiting_for_model`, and publishes
   unthrottled (`:316-323`).
3. If the engine is not ready, `_wait_for_engine` may trigger a load and fails with 503 `engine_unavailable` on
   error (`:374-407`).
4. `_submit(_execute_pipeline, snapshot, reporter, token)` runs on the single `ThreadPoolExecutor(1,
   "gpu-worker")` (`:85`, `:337-339`), which calls `self._pipeline.run(job, reporter, token)` (`:372`).
5. `ImageTo3DPipeline.run` (`pipelines/image_to_3d.py:90-151`):
   - `resolve_references` (`:73-88`): job-local inputs, primary first.
   - Strategy `select` (`:95-99`). `PrimaryOnlyStrategy` keeps `[:1]` (`reference_strategy.py:45`).
   - `build_generation_params` (`:50-64`) reads `job.settings` keys `resolution, polygon_target, texture_size,
     remove_background, advanced.*` and `job.seed`.
   - `GenerationRequest(job_id, images, all_references, params, output_dir=jobs/<job>, reference_mode)`
     (`:103-110`).
   - `engine.generate(...)` (`:119`), then `reporter.stage(FINALIZING)` (`:122`).
   - `_place` the GLB and preview at their canonical paths (`:123-126`, `:154-167`).
   - `compute_glb_stats(model.glb)` (`:129`; `processing/mesh_stats.py:39`). Timings, then `JobResultData`
     (`:141-151`).
6. `TrellisEngine.generate` (`inference/trellis/engine.py:352-423`) holds `_lifecycle_lock` and runs
   `TrellisGenerationRun.execute` (`generation.py:182-347`). Stages: preprocessing, conditioning,
   sparse_structure, shape, texture, decoding, exporting.
   - Preprocessing removes the background or crops to alpha and writes `preview.png`, an RGB copy capped in size
     (`:137-178`).
   - Export calls `o_voxel.postprocess.to_glb(..., remesh=True, decimation_target, texture_size)` and writes
     `model.glb` atomically (`export.py:20-93`).
7. `_finish` (`:419-455`) sets `completed`/`failed`/`cancelled`. On completion it sets `result` and progress
   `done` / 100 %, then publishes and persists. Cancellation goes through `CancellationToken`
   (`inference/base.py:201-224`). Errors: `AppError` codes are kept; other exceptions go through
   `classify_exception` and become `out_of_memory` or `engine_error` (`:46-56`).

### 1.5 Read results
- `GET /jobs/{id}` (`jobs.py:53`) → `JobService.get_job`. It returns the live in-memory snapshot while the job is
  running (`job_service.py:68-76`).
- `GET /jobs/{id}/result` (`results.py:19`) → `ResultService.get_result` returns 409 unless completed
  (`result_service.py:27-38`).
- Files use FileAuthGuard, so `?token=` works:
  - `/preview`: PNG, no-cache (`results.py:24`).
  - `/model.glb`: inline (`:30`).
  - `/export/glb`: attachment `img3d_<job>.glb` (`:42`; `result_service.py:17`).
- `JobOut.from_domain` exposes `result` only when `status == completed` (`schemas/jobs.py:151-172`). URLs are
  built in `JobResultOut.from_domain` (`:117-130`).

---------------------------------------------------------------------------------------------------
## 2. Domain model [V]

### 2.1 IDs (`utils/ids.py`)
- Format: `<prefix>_<12 lowercase hex>`, regex `^[a-z]+_[0-9a-f]{12}$`.
- Existing prefixes: `rs` (set), `img` (image), `job` (job, `:14-16`), `tn` (tuning run, `models/tuning.py:23`).
- `is_valid_id(value, prefix)` (`:39`) **must** be checked before any filesystem access with a client id. The
  stores and `MeshTunerService.get_run` do this.
- New prefixes such as `pt` (part) or `pr` (part run) are valid under the same regex.

### 2.2 Enums (`models/enums.py`). The contract says "do not change values". These are seam files.
- `ReferenceSlot`: front, back, left, right, face_front, face_side, detail (`:8`).
  - `SINGLE_SLOTS`, `SLOT_PRIMARY_PRIORITY`, `SLOT_DISPLAY_ORDER`.
  - The slot list is pinned in `/system` limits (`test_health_system.py:54-60`). **Do not add a `mask` slot.**
- `JobStatus`: queued, running, completed, failed, cancelled. `TERMINAL_STATUSES` (`:53-63`).
- `JobStage`: queued, waiting_for_model, preprocessing, conditioning, sparse_structure, shape, texture, decoding,
  exporting, finalizing, done (`:66-77`).
- `EngineState` (`:80`).
- `ReferenceMode`: primary_only, multi_view (`:88`).

### 2.3 Persisted models (pydantic `DomainModel`, `extra="ignore"`, `models/types.py:27-30`)
- `ReferenceImage` (`models/reference.py:14`): id, set_id, slot, label, original_filename, mime_type, width,
  height, has_alpha, size_bytes, sha256, is_primary, created_at, file_name, thumbnail_name.
- `ReferenceSet` (`:36`): id, name, created_at, updated_at, images[].
  - Exactly one primary when non-empty (`ensure_primary`).
- `Job` (`models/job.py:102`): id, status, engine, created_at, started_at, finished_at, reference_set_id (str,
  required), primary_image_id (str, required), references[JobReference(image_id, slot, is_primary)],
  settings (dict; normalized GenerationSettings), seed, progress, error, result, cancel_requested.
- `JobProgress` (`:51`): stage, stage_label, stage_index, stage_count = 9, step, total_steps, percent, message,
  elapsed_seconds, eta_seconds, updated_at.
  - `STAGE_TABLE` index/label/weight (`:21-33`): preprocessing 5, conditioning 5, sparse 15, shape 30,
    texture 20, decoding 8, exporting 15, finalizing 2.
- `JobResultData` (`:89`): stats (MeshStats), timings, seed, resolution, effective_resolution, texture_size,
  polygon_target, has_preview, metadata (engine metadata, not exposed by the API).
- `MeshStats` (`:76`): vertices, faces, materials, textures, texture_size, has_uv, bbox_min, bbox_max,
  dimensions, file_size_bytes.
- `TuningRun` (`models/tuning.py:104`): id, job_id, status (JobStatus values), timestamps, options, progress
  {percent, step_id, step_index, step_count, message}, steps[TuningStep(id, label, status, message, duration_s,
  details)], metrics, warnings, validation, blender, result (TuningResultData files + stats), error,
  cancel_requested.
  - `STEP_TABLE` with fixed ids and weights (`:42-51`).
  - **This step-plan pattern is the model to copy for part-pipeline progress.**

### 2.4 API schemas
- Requests use `RequestModel` with `extra="forbid"`; unknown fields give 422 (`schemas/common.py:18-21`).
  Responses use `ApiModel` with `extra="ignore"` (`:12-15`).
- `GenerationSettings` (`schemas/jobs.py:35-47`):
  - resolution > 0, seed 0..2^31-1, randomize_seed, polygon_target > 0, texture_size > 0, remove_background,
    reference_mode | None, advanced {sparse_structure, shape, texture: SamplerSettings}.
  - SamplerSettings: steps 1..50, guidance 0..10, rescale 0..1, rescale_t 1..6 (`:22-32`).
  - Membership (resolution, texture size, polygon range, mode) is checked against engine capabilities at create
    time.
- `CreateJobRequest` (`:50-53`): `reference_set_id: str` (required), `primary_image_id: str | None`,
  `settings: GenerationSettings`.
- `JobOut` (`:133-172`): `settings: dict[str, Any]` is free-form, so new setting keys never break the response
  model. `JobResultOut` (`:104-130`).
- Error envelope `{error: {code, message, details, request_id}}` (`schemas/common.py:24-37`). Codes are in
  `core/exceptions.py:14-104`. The tuner's Blender codes are in `services/blender_runtime_service.py:58-118`.

### 2.5 Settings validation layers
1. Pydantic field bounds plus `extra=forbid`: 422 `validation_error`, enforced by FastAPI and the handlers.
2. Capability membership (`services/generation_settings.py:13`): 422 with `type: "unsupported_by_engine"` and
   `ctx.allowed`.
3. Reference strategy (`pipelines/reference_strategy.py:27-79`): 422.
4. Engine re-validation at generate time (`engine.py:330-350`): defence in depth, `ValidationAppError`.

### 2.6 On-disk layout and persisted JSON shapes
```
runtime/
  uploads/<rs>/set.json, <img>.<ext>, <img>_thumb.webp            ReferenceStore (storage/reference_store.py)
  jobs/<job>/job.json, inputs/<img>.png, preview.png, model.glb    JobStore + RuntimePaths (storage/paths.py:22-70)
  jobs/<job>/tuning/<tn>/run.json, tuned.glb, report.json, ...    TuningStore (storage/tuning_store.py:26)
  temp/ (purged at startup), cache/, blender/
```
- All JSON is written with `write_json_atomic` (temp file plus `os.replace`, `storage/json_io.py`).
- `JobStore.load` reads only `jobs/<valid job id>/job.json` and skips corrupt or mismatched files
  (`job_store.py:34-55`).
- `recover_interrupted` marks queued or running jobs as failed with `interrupted` (`:57-79`).
- `TuningStore.load` scans only `jobs/*/tuning/` (`tuning_store.py:47`).
- A new `jobs/<job>/parts/` folder is invisible to both stores **[V by reading]**.
- Real `job.json` sample (job_046e19f0ca62):
  - Fields: `{id, status, engine:"trellis2", created_at, started_at, finished_at, reference_set_id,
    primary_image_id, references[{image_id, slot:"detail", is_primary}], settings{resolution:1536, seed,
    randomize_seed, polygon_target:1000000, texture_size:2048, remove_background, reference_mode:"primary_only",
    advanced:null}, seed, progress{… stage:"done", stage_index:9, stage_count:9, percent:100.0}, error:null,
    result{stats{…}, timings{preprocessing … finalizing, total:269.218}, seed, resolution, effective_resolution,
    texture_size, polygon_target, has_preview, metadata{engine, model:"TRELLIS.2-4B", pipeline_type:
    "1536_cascade", preprocess:"alpha_crop", attention_backend:"sdpa", low_vram, sparse_voxels, shape_tokens,
    sampler_params, …, engine_mesh_stats}}, cancel_requested:false}`.
  - Files: `model.glb` 40,975,940 bytes, `preview.png` 560,142 bytes.

---------------------------------------------------------------------------------------------------
## 3. Event and WebSocket model [V]

### 3.1 Event bus
- `core/events.py`. `Event(type, data, ts, job_id, droppable)` (`:29`).
- Each subscriber has a bounded deque of 256 entries. On overflow the oldest droppable event is dropped first.
- `publish` must run on the loop thread (`:139`). `publish_threadsafe` always hops through
  `call_soon_threadsafe`, so the GPU thread and the loop share one FIFO order (`:150`).

### 3.2 Publishers
- `JobEventPublisher.publish(job, droppable)` sends `job.updated` with `JobOut` JSON and `Event.job_id = job.id`
  (`services/job_events.py:25-30`).
  - Status changes go through `reporter.mutate` and are never droppable. Progress is droppable and throttled to
    5/s (`pipelines/progress.py`).
- `SystemService.notify_changed()` sends a debounced (50 ms) `system.updated` carrying the full `SystemStatus`
  (`services/system_service.py:43,215-244`).
- `BlenderRuntimeService._on_change` sends `blender.updated`. Download progress frames are droppable.
- `TuningEventPublisher` sends `tuning.updated` with `Event.job_id = run.job_id` (`workers/tuning_worker.py:108-118`).

### 3.3 WebSocket
- Endpoint: `GET /api/v1/ws` (`api/v1/ws.py:12`) → `WebSocketHub.serve` (`api/websocket_hub.py:118-172`).
- Sequence: accept, check auth (close 4401), subscribe with the `?job_id=` predicate, send `hello`
  `{system}` (`:140`), then run the sender, a pinger (`ws_ping_interval_s` = 20 s), the receiver and a watchdog.
- A send timeout of 10 s drops slow clients. Shutdown closes with 1001.
- Job-scoped types are `{job.updated, tuning.updated}` (`:229`). A `?job_id=` connection receives only those
  types for its own job, plus every non-scoped type (`:233-242`).
- Wire format: `{"type","ts","data"}` (`schemas/events.py:30`). `EventType`: hello, job.updated,
  system.updated, ping, blender.updated, tuning.updated (`:13-21`).
- Client tolerance: the frontend `ws.ts` dispatch ignores unknown types (`default: break`,
  `frontend/src/api/ws.ts:254-276`). The e2e client also ignores them (`test/e2e/e2e_generate.py:191-201`).
  **A new event type such as `parts.updated` is safe for existing clients.**
  - If a new type is per job, add it to `_JOB_SCOPED_EVENTS` so `?job_id=` filtering works.

---------------------------------------------------------------------------------------------------
## 4. Extension points: what plugs in where, what changes and what stays

### 4.1 Recommended shape [A: design proposal]
Keep **Job** as the single unit of GPU work and add a job **kind**. Do not create a second GPU queue or worker.
```
kind = "image_to_3d"        (default; legacy path unchanged)
kind = "prompt_to_3d"       (text→image on the GPU thread, then the same image_to_3d path)
kind = "part_decomposition" (derived job: re-runs decomposition on a completed job's inputs and model.glb)
options: settings.parts (segmentation on/off, decomposition strategy, categories) — valid on all three kinds
```
- `Job.kind: Literal[...] = "image_to_3d"`. Old `job.json` files load as `image_to_3d` because `extra="ignore"`
  and the field has a default.
- `JobOut.kind`, `JobOut.source` (prompt info) and `JobOut.steps` (plan) are optional extra keys. JobOut keys are
  not pinned exactly in tests **[V: no `set(job) ==` assertion]**.
- `pipelines/router.py: PipelineRouter(GenerationPipeline)` replaces the single `ImageTo3DPipeline` passed in
  `core/container.py:84`.
  - For `kind == image_to_3d` and `settings.parts` absent or disabled it calls `ImageTo3DPipeline.run` unchanged.
    The legacy behaviour is then byte-identical, and the 173 + 9 tests keep their meaning.
  - `ServiceContainer.pipeline` is typed `ImageTo3DPipeline` (`container.py:45`). Widen it to
    `GenerationPipeline`. No test touches `container.pipeline` **[V grep]**.
- Stages are composable objects in `pipelines/stages/`, each with `id, label, weight, run(ctx, reporter, cancel)`:
  `prompt_to_image`, `segmentation_2d`, `reconstruction` (wraps ImageTo3DPipeline), `part_extraction_3d`,
  `assembly_export`.
  - Use strategy registries in `pipelines/parts/strategies.py`: `none | regions_only | image_masks |
    mesh_segmentation | per_part_regen`.
  - ML models stay behind `inference/` seams (§4.6).

### 4.2 Prompt-to-3D (new job kind)
- **Request.** Add an optional top-level field `source` to `CreateJobRequest` (`schemas/jobs.py:50`):
  `{"type": "prompt", "prompt": str (max ~2000), "negative_prompt"?, "image_seed"?, "image_settings"?}`.
  - `reference_set_id` becomes `str | None`. A model validator requires exactly one of `reference_set_id` or a
    prompt source.
  - JSON bodies have no global size limit (only uploads use `LimitedUploadRoute`), so prompt length must be
    capped in the schema.
- **Why top-level and not inside `settings`.** The frontend's `retry` resends `job.settings` verbatim together
  with `reference_set_id` and `primary_image_id` (`frontend/src/store/jobStore.ts:225-245`).
  - With the prompt inside `settings`, a retry would re-prompt and also carry a set id, which is ambiguous.
  - With `source` at top level, a legacy-UI retry becomes a deterministic image-to-3D re-run on the generated
    image, which is the desirable behaviour.
- **Keep JobOut non-null.** `Job.reference_set_id` and `primary_image_id` are required strings
  (`models/job.py:109-110`, `schemas/jobs.py:140-141`).
  1. At create time, make a new set named after the prompt and reserve an `img_` id.
  2. The job references that reserved image in slot `front`.
  3. The prompt stage (GPU thread) writes `inputs/<img>.png` and `source/prompt_image.png`.
  4. A loop-side completion hook imports the image into the set under the same id. This needs a new
     `ReferenceService.import_image(..., image_id=reserved)`, because `upload_image` mints its own id
     (`reference_service.py:157`).
  - The GenerationWorker has no completion hooks yet. Add `add_completion_hook(async fn(job))`, mirroring
    `JobService.add_delete_hook` (`job_service.py:60`). If the set was deleted meanwhile, log it and keep the job
    completed. Retry with that primary would then return 404 or 422.
- **Validation.** Prompt jobs need a text-to-image capability. If no T2I engine is installed, return 422 or 503
  `capability_unavailable`, the same pattern as `MultiViewStrategy` (`reference_strategy.py:54-66`).
  `diffusers` is **not installed** in the embedded environment **[V: site-packages listing]**.
- **Progress.** Report the text-to-image step under legacy stage `preprocessing`, with a descriptive message and
  `steps[0]` active. Do not add a JobStage (§4.5).
- **Alternative [A].** A two-request flow: `POST /reference-sets/{id}/generate-image` first, so the user reviews
  the image, then a normal job.
  - It is cleaner for "review the image first" UX, but needs a non-GLB job result type.
    `JobResultOut.model_url` and `stats` are required (`schemas/jobs.py:104-114`).
  - Recommend one-shot `source.prompt` first. Add a review step later as an `auto_continue=false` variant.

### 4.3 New pipeline options (segmentation on/off, decomposition strategy)
- Add `parts: PartOptions | None = None` to `GenerationSettings` (`schemas/jobs.py:35`) as a `RequestModel`
  with `extra="forbid"`:
  ```json
  "parts": { "enabled": true,
             "segmentation": "off" | "auto",
             "decomposition": "none" | "regions_only" | "image_masks" | "mesh_segmentation" | "per_part_regen",
             "separate": ["eyes","eyebrows","eyelashes","hair","clothes","accessories","detachable"],
             "regions": ["face","body","skin"],
             "export": {"part_files": true, "assembled_glb": true, "keep_base_model": true} }
  ```
- **Round-trip invariant.** Every key stored in `Job.settings` must be accepted again by `GenerationSettings`,
  because the frontend retry resends it. Putting `parts` inside `settings` satisfies this.
- **Byte-identical legacy jobs.** In `job_service.py:107`, dump with `exclude` for `parts` when it is `None`, so
  legacy `job.json` and `JobOut.settings` stay identical. Otherwise every new image job carries
  `"parts": null`, and an old frontend retry against an old backend would get 422.
- **Validation.** Extend `validate_against_capabilities` with a separate `PartCapabilities` provider (strategies
  available, categories supported, models loaded or installable).
  - **Do not** add fields to `EngineCapabilities.to_dict()`: the exact dict is pinned
    (`test/engine/test_engine_unit.py:81-90`).
  - `CapabilitiesOut` would drop unknown keys anyway (`ApiModel` ignore, `schemas/system.py:39`).
- `build_generation_params` (`image_to_3d.py:50`) ignores unknown settings keys, so the reconstruction stage
  is unaffected **[V]**.

### 4.4 New artifacts (part meshes, part manifest, masks)
Proposed layout inside the job folder, so delete and retention work unchanged
(`job_store.delete` → `safe_rmtree(job_dir)`; retention deletes terminal job folders; orphan purge looks only at
top-level job dirs):
```
jobs/<job>/parts/
  manifest.json                 # schema "img3d.parts/1" (below), atomic write
  segmentation/overlay.png      # colour overlay of 2D labels on the processed image
  segmentation/labels.png       # uint8/uint16 label map (lossless PNG) + legend in manifest
  masks/<pt>.png                # per-part 2D alpha masks (processed-image space)
  regions/face_labels.bin       # uint16 per face of model.glb (continuous topology, NOT split)
  <pt>/mesh.glb                 # physically separated asset (eyes, hair, clothes, ...)
  <pt>/preview.png, <pt>/inputs/crop.png   # per-part conditioning crop when per_part_regen
  assembled.glb                 # one scene: base body node + one named node per physical part
```
- Manifest sketch [A]:
  ```json
  { "schema": "img3d.parts/1", "job_id": "job_…", "created_at": "…Z",
    "source": {"kind": "image|prompt", "image_id": "img_…", "prompt": null},
    "base": {"model": "model.glb", "frame": "trellis_unit_aabb", "aabb": [[-0.5,-0.5,-0.5],[0.5,0.5,0.5]]},
    "strategy": {"segmentation": "auto", "decomposition": "mesh_segmentation", "models": {}},
    "parts": [{ "id": "pt_…", "label": "hair", "category": "hair", "kind": "physical|region",
                "parent": "pt_body", "side": "left|right|center|null",
                "files": {"mesh": "parts/pt_…/mesh.glb", "mask": "parts/masks/pt_….png"},
                "region": {"labels_file": "parts/regions/face_labels.bin", "value": 3, "face_count": 1234},
                "transform": [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1],
                "alignment": {"method": "none|bbox2d_depth|icp", "error": null},
                "stats": { /* MeshStats */ }, "watertight": false,
                "refinement": {"eligible": true, "status": "untouched"} }],
    "relations": [{"type": "attached_to|symmetric_with|inside|occludes", "a": "pt_…", "b": "pt_…"}],
    "warnings": [] }
  ```
- **Regions without splitting.** trimesh 5.1.1, the pinned version, exports `mesh.vertex_attributes` as
  glTF application attributes with a forced `_` prefix, and re-imports `_*` attributes
  (`site-packages/trimesh/exchange/gltf/__init__.py:987-1005`, `:1662-1669`) **[V]**.
  - A `_PART_ID` vertex attribute is therefore feasible with existing dependencies.
  - The faces sidecar stays authoritative, because glTF has no per-face attributes.
  - Standard option: Cesium `EXT_mesh_features` (`_FEATURE_ID_n` vertex attributes or textures; status "Draft"):
    https://github.com/CesiumGS/glTF/tree/3d-tiles-next/extensions/2.0/Vendor/EXT_mesh_features **[V page]**.
  - The glTF 2.0 spec requires application-specific attribute names to start with `_`:
    https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html.
- **Domain.** Add `JobResultData.parts: PartsSummary | None = None` (counts, manifest present, part ids and
  labels). Expose `JobResultOut.parts: PartsResultOut | None` with `manifest_url`, `assembled_url` and per-part
  `{id, label, kind, model_url, mask_url}`.
  - `JobResultOut` keys are not exact-pinned. `test_jobs.py:114` only compares `/result` to `job.result`.
    Legacy jobs gain `"parts": null`.
- **Stats.** `compute_glb_stats` already sums every scene node (`processing/mesh_stats.py:72-97`), so it works
  for `assembled.glb` and per-part GLBs. Keep `JobResultData.stats` = the base `model.glb` for compatibility.
- **Path rules.**
  - Part ids must pass `is_valid_id(part_id, "pt")`.
  - File names must come from a fixed whitelist or a regex like `PREVIEW_NAME_RE` (`workers/tuning_worker.py`),
    before any `Path` join.
  - Serve files through `ResultService`-style methods that return `StoredFile` (`reference_service.py:32`).

### 4.5 Progress model (must not touch the pinned stage table)
- Pinned:
  - `compute_percent` values (`test_progress.py:56-67`).
  - `stage_index == 9` at done (`test_jobs.py:96`).
  - Stage names must be in `STAGE_ORDER + [queued, waiting_for_model]` (`test_jobs.py:31-41,86`).
  - The frontend `PIPELINE_STAGES` indices 1..8 (`lib/stages.ts:10-19`) and the closed `JobStage` union.
- Proposal [A]: add `Job.steps: list[PipelineStep] | None = None`, shaped like `TuningStep` (`id, label,
  status pending|running|done|warning|skipped|failed, message, duration_s, progress`), with stable ids equal to
  the Node-UI node types: `input_image | prompt | segmentation | image_to_3d | part_extraction | export`.
  - For legacy jobs, `steps` is `None` and the progress object is unchanged.
  - For new kinds, give `JobProgressReporter` a "plan window". The reconstruction stage maps `compute_percent` into
    `[lo, hi]` of the overall percent. Pre-steps report as `preprocessing` and post-steps as `finalizing`, with a
    clear `message`. `percent = max(old, new)` keeps it monotonic (`progress.py:87-128`).
- Node UI: light up nodes from `job.steps[*].status` delivered by the existing `job.updated` events. No new event
  type is needed for in-job steps.

### 4.6 Inference seam additions (`inference/base.py` is a stable seam; changes must be additive)
1. **Optional preprocess hook.** Add `InferenceEngine.preprocess(path, remove_background) -> ProcessedImage` with
   a default `NotImplementedError` or `None`.
   - The 2D segmentation stage needs the processed RGBA image: background removed, cropped, centred.
   - Today that image exists only inside `TrellisGenerationRun._preprocess`, and the saved `preview.png` is RGB
     and size-capped (`generation.py:173-176`; `imaging.py:68-73`).
   - Add `GenerationRequest.preprocessed: Path | None = None`, so `generate` can skip a second RMBG/BiRefNet pass
     and use the same pixels the masks were computed on (mask and geometry alignment).
2. **New ABCs beside `InferenceEngine`.** Add `TextToImageEngine` and `SegmentationEngine`, each with
   load/unload/status/run, created lazily through a registry like `inference/registry.py:31-60`. The
   `UnavailableEngine` pattern reports why a capability is missing, with no fake output.
   - They must run **on the GPU thread inside `pipeline.run`**.
   - VRAM budget: 32 GB. TRELLIS.2-4B weights on disk are 16 GB **[V du]**. A FLUX-class T2I would not co-reside
     **[A]**, so stages must load and unload explicitly.
   - Every model must honour `CancellationToken` per step and free GPU memory in `finally`, as TRELLIS does
     (`engine.py:410-414`).
   - Their status should be exposed through the new features/catalog endpoint, not `SystemStatus.engine`.
3. **Per-part reconstruction** (`per_part_regen`) must call `engine.generate` with
   `output_dir = jobs/<job>/parts/<pt>/`. The engine hard-codes `model.glb` and `preview.png` inside
   `output_dir` (`generation.py:51-52`).
   - `ConditioningImage.slot` can be `ReferenceSlot.DETAIL`.
   - Each run is normalized to the unit AABB (`export.py:16,48`), so alignment transforms are mandatory.

### 4.7 New endpoints (all additive under `/api/v1`, `AuthGuard` or `FileAuthGuard`, `ERROR_RESPONSES`)
| Endpoint | Purpose | Notes |
|---|---|---|
| `GET /features` (or `/pipeline/catalog`) | Feature flags plus node catalog: `{t2i: {available, reason}, segmentation: {...}, parts: {strategies, categories}, mesh_tuning: {enabled:false, reason:"awaiting approval"}, nodes: [{type, inputs, outputs, params_schema}]}` | Keep `SystemStatus` untouched: its top-level keys, `limits` and `defaults` are exact-pinned (`test_health_system.py:37,54,61`). The node param schemas come from Pydantic `model_json_schema()`, so all four UI modes share one source of truth. |
| `POST /jobs` (extended) | `source` + `settings.parts` | Same 202 `JobOut`. |
| `GET /jobs?kind=` | History filter | Copy `_parse_status` (`jobs.py:19-34`). |
| `GET /jobs/{id}/parts` | Manifest JSON (409 until completed or when not produced) | Mirror `ResultService._completed_job` (`result_service.py:27`). |
| `GET /jobs/{id}/parts/{pt}/model.glb` / `/mask` / `GET /jobs/{id}/parts/assembled.glb` / `/segmentation/overlay` | Files | FileAuthGuard, id validation, whitelisted names. Note the export filename pattern `img3d_<job>_<pt>.glb`. |
| `POST /jobs/{id}/parts/rebuild` | Creates a derived `part_decomposition` job (new strategy on the same base) | Copy `model.glb` and inputs into the new job at creation, the same isolation rule as reference snapshots. |
- Route collision check: `tunings.jobs_router` already owns `/jobs/{id}/tunings` (`api/v1/tunings.py:14,22`).
  `results.files_router` owns `/jobs/{id}/model.glb|export/glb|preview`. `/jobs/{id}/parts…` does not collide.
- Contract-first rule (from `SKILL.md`): update `api-contract.md` §3/§7/§8/§9 before the code. Also update
  `frontend/src/api/types.ts` and `test/frontend/mock_backend.mjs` (1038 lines, it mirrors every route).

### 4.8 Change vs. keep matrix
| Keep exactly | Additive change |
|---|---|
| `JobStage`, `STAGE_TABLE`, `STAGE_COUNT`, `compute_percent` | `Job.kind`, `Job.source`, `Job.steps`, `JobResultData.parts` (all optional with defaults) |
| `EngineCapabilities.to_dict()`, `SystemStatus` keys, `limits`, `defaults` | `GET /features` / `pipeline catalog` |
| `ReferenceSlot` values (and the `/system` slot list) | Masks stored per job under `parts/`, never as reference slots |
| `JobResultOut` existing fields and URLs, GLB file names `model.glb` / `preview.png` | `JobResultOut.parts`, part file routes |
| Error envelope keys, existing error codes | New codes, e.g. `capability_unavailable`, `parts_unavailable` |
| `GenerationWorker` single executor, `JobQueue`, `JobStore` | `PipelineRouter`, worker completion hooks |
| `CreateJobRequest.reference_set_id` semantics for image jobs | `reference_set_id` optional only when `source.type == "prompt"` |
| WS envelope `{type, ts, data}`, existing types | Optional `parts.updated` only for out-of-job runs (add to `_JOB_SCOPED_EVENTS`) |

---------------------------------------------------------------------------------------------------
## 5. Auto Mesh Tuner wiring and keeping it disabled [V]

### 5.1 How it is wired
- **Settings** `blender_*` (`config/settings.py:118-131`): `blender_enabled=True`, detection on startup,
  timeout 1800 s, queue 4. They appear in `safe_summary` (`:300-309`). The default is pinned
  (`test_blender_runtime_api.py:311`).
- **Container:** `BlenderRuntimeService`, `TuningStore`, `JobQueue(blender_max_queue_size)`, `TuningWorker` (its
  own `blender-worker` thread) and `MeshTunerService` are always built (`core/container.py:113-146`). The delete
  hook `on_job_deleting` is registered at `:147`.
- **Lifecycle:**
  - Tuning store load and recover run always (`core/lifecycle.py:36-39`).
  - `tuning_worker.start()` runs always (`:44`).
  - Blender detection runs only if `blender_enabled and blender_detect_on_startup` (`:60-64`).
  - Shutdown stops the tuning worker and closes the runtime (`:88-97`).
- **Routers** `blender.router` and the three tunings routers are always mounted (`api/router.py:22-25`).
- **Gate:**
  - `BlenderRuntimeService._require_enabled()` raises 503 `blender_unavailable` with `DISABLED_MESSAGE`
    (`services/blender_runtime_service.py:46,284-286`). It is used by `detect`, `select`, `resolve_release`,
    `start_download`, `cancel_download` and `require_runtime`.
  - `status()` / `_to_out` report `state:"error", can_download:false` with the disabled message (`:310-313`).
  - `MeshTunerService.create_run` checks the job first (404/409), then calls `require_runtime()`, which returns
    503 when disabled (`services/mesh_tuner_service.py:96-101`).
  - Listing, reading, cancelling and deleting existing runs, and their files, still work, which is fine for history.
  - Tests pin this behaviour (`test_blender_runtime_api.py:167-176`).
- **Events:** `blender.updated` and `tuning.updated`. Frontend: `meshTunerStore.ts:435` maps 503
  `blender_unavailable` to a message.

### 5.2 Cleanest way to keep tuning disabled until approval
1. **Now, zero code:** add `IMG3D_BLENDER_ENABLED=false` to `backend/.env`. It is gitignored and local; today the
   file has no `IMG3D_*` keys **[V]**.
   - The tests are unaffected: they use `Settings(_env_file=None)` (`test/backend/conftest.py:74`) and the
     environment does not set the flag.
   - Effect: no Blender process is ever executed, and every mutating tuner endpoint returns 503.
2. **In the new architecture:** report `features.mesh_tuning = {enabled: settings.blender_enabled and
   <approved>, reason}` from the new features endpoint.
   - All four UI modes (Default, Node, Mobile, Tablet) hide or disable Auto Tune from that single flag.
   - The Node UI catalog contains **no** tuning or refinement node types.
3. **Future part-level refinement:** add a new setting such as `part_refinement_enabled: bool = False`. Define only
   the consumer interface (e.g. `pipelines/parts/consumers.py: PartConsumer` protocol plus a registry that refuses
   registration while the flag is false), with zero implementations.
   - The part pipeline must not import `services.mesh_tuner_service` or `workers.tuning_worker`.
   - Do **not** flip the `blender_enabled` default in code. That would break `test_blender_runtime_api.py:311`
     and the tuner API tests that rely on it (`test_mesh_tuner_api.py`, 20 tests).
4. The manifest's `refinement.status: "untouched"` and `eligible` fields let a later tuner consume individual
   parts once the user approves.

---------------------------------------------------------------------------------------------------
## 6. Risks and test coverage gaps

### 6.1 Risks
1. **Base model overwrite.** Any second `engine.generate` call with `output_dir=job_dir` replaces `model.glb` and
   `preview.png` (`image_to_3d.py:101`; `generation.py:173,306`). Use a mandatory per-part `output_dir` and add a
   test asserting the base GLB hash is unchanged.
2. **Label and topology mismatch.** Export remeshes and decimates (`export.py:41-56`), so labels must be computed
   on the final GLB or projected onto it.
3. **Lost relative placement** for per-part regeneration, because of unit-AABB normalization. Manifest transforms
   plus alignment error are required. "Easy to reassemble" depends on this.
4. **GPU contention and OOM.** There is one GPU executor and the engine lock (`generation_worker.py:85`;
   `engine.py:110,359`). An extra model loaded off-thread would race TRELLIS. OOM maps to `out_of_memory`
   (`generation_worker.py:46-56`), but a mid-plan OOM leaves partial `parts/` content, so write it to a temp
   folder and move it on success, the same way the tuner does.
5. **Retry semantics** (`jobStore.ts:225-245`): new request data outside `settings` is silently dropped by legacy
   retry. Data inside `settings` must round-trip through `extra="forbid"`.
6. **Pinned exact shapes** (see the §0 list). Adding keys to `/system`, `limits`, `defaults` or
   `EngineCapabilities.to_dict()` breaks tests and the contract.
7. **Frontend stage coupling.** New `JobStage` values would render wrongly in the existing progress list and
   violate the TS union.
8. **Long jobs.** Per-part regeneration multiplies runtime (a real job took 269 s, `job_046e19f0ca62`). The 16-slot
   queue and the 2 s persistence behave the same, but the ETA formula assumes the legacy weights.
9. **Licensing of new models** (T2I, segmentation) must be checked separately; this is outside this report.
   Existing note: RMBG-2.0 is non-commercial and its folder is effectively empty (1 KB), so BiRefNet (MIT) is the
   active fallback **[V du + `assets.py:7-8,112-126`]**.
10. **Contract drift found** **[V]**:
    - `api-contract.md` §2 lacks `reference_mode` (`settings.py:79`), `ws_ping_interval_s`,
      `device_info_timeout_s`, `device_info_cache_s` and `retention_interval_s` (`:112-115`).
    - §9 says `uvicorn.run`, but the code uses a custom `Img3DServer` (`main.py:122-147`).
    - The `ws.py` docstring lists only 4 event types (`api/v1/ws.py:14`).
    - Fix these in the same contract-first update.

### 6.2 Test coverage gaps (relevant to this milestone)
- There is no test that a **legacy `job.json`** (without new fields) still loads and serves `/result` after
  schema extension. Only corrupt-file skipping is tested (`test_recovery.py:57`).
- There is no **settings round-trip** test (POST `job.settings` back as a new job), even though the frontend's
  retry depends on it.
- No test covers a **multi-node GLB** in `compute_glb_stats` (the test uses a single cube, `test_processing_storage.py:60`).
- No test asserts that the **engine output dir** isolation (base `model.glb` untouched by other generate calls).
- No test pins **unknown WS event types** being ignored by clients (the behaviour is correct by code reading only).
- No backend test covers **auth on new file routes**. Copy the `test_file_routes_accept_query_token_when_auth_enabled`
  pattern (`test_mesh_tuner_api.py:460`).
- Missing tests for the new code itself:
  - `kind` dispatch.
  - Prompt-job validation (exactly one source) and its 422/503 when T2I is unavailable.
  - Part manifest schema validation and part-id path-traversal rejection.
  - Cancellation inside new stages.
  - The plan-window progress staying monotonic.
  - `features.mesh_tuning.enabled == false` when `blender_enabled=false`.
- The fake engine (`test/backend/fakes.py:156-193`) writes only `model.glb` and `preview.png` into
  `request.output_dir`. It needs test-only doubles for T2I and segmentation engines that write real small
  PNG/GLB files. Test doubles stay under `test/`, per CLAUDE.md.
- Frontend: `test/frontend/mock_backend.mjs` must learn the new endpoints, or the UI-mode smoke tests will drift
  from the backend.

---------------------------------------------------------------------------------------------------
## 7. Suggested incremental order (backend only; every step keeps the 182 tests green) [A]
1. Contract-first: update `api-contract.md` (new §: kinds, source, parts options, manifest, features endpoint,
   steps) and fix the drift in 6.1 item 10.
2. Add `.env` `IMG3D_BLENDER_ENABLED=false` (user-local), plus the `GET /features` endpoint with a
   `mesh_tuning.enabled` flag and tests.
3. Domain: optional `Job.kind/source/steps`, `JobResultData.parts`, `GenerationSettings.parts` (excluded when
   None), with legacy-load and round-trip tests.
4. `PipelineRouter` plus stage interfaces. The legacy path delegates to `ImageTo3DPipeline` unchanged. Add the
   plan-window reporter.
5. Part artifacts: `RuntimePaths.job_parts*`, manifest writer and reader, `ResultService` part files, routes and
   id validation. First strategy: `regions_only` on the exported GLB (CPU/trimesh, no new model), so the contract
   is proven end-to-end with real data.
6. Inference seams: `preprocess` hook, then `SegmentationEngine` and `TextToImageEngine` ABCs plus registries
   reporting `unavailable` until models are approved and installed.
7. Prompt-to-3D kind (create set, reserve image id, completion hook to import the image).
8. `part_decomposition` derived jobs (`POST /jobs/{id}/parts/rebuild`) and `per_part_regen` with alignment
   transforms.
9. Tuning remains off. Only the `PartConsumer` protocol exists, gated by `part_refinement_enabled=False`.
