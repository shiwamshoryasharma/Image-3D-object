# Img3D — Architecture & API Contract (authoritative)

All backend modules, the frontend API layer and tests MUST conform to this document.
If something here is wrong, fix the contract first, then the code.

---------------------------------------------------------------------------------------------------
## 1. Repository layout

```
Img3D/
├── .claude/                     # skills + project memory (CLAUDE.md)
├── backend/
│   ├── main.py                  # SINGLE entry point (bootstraps sys.path, config, logging, app, engine, worker, uvicorn)
│   ├── requirements.txt
│   ├── .env / .env.example      # secrets (HF_TOKEN, UI_USERNAME, UI_PASSWORD) + optional IMG3D_* overrides
│   ├── python/                  # embedded CPython 3.14 (never use system python)
│   ├── python.bat               # user's wrapper (leave as-is)
│   ├── .model/                  # TRELLIS.2-4B weights + aux models (never re-download TRELLIS.2-4B)
│   ├── .toolchain/              # local CUDA build toolchain (gitignored)
│   ├── third_party/             # vendored TRELLIS.2 source + native extension sources
│   ├── scripts/                 # build_extensions.cmd, download_aux_models.py, auto_mesh_tuner.py (CLI), ...
│   ├── launcher/                # Blender runtime management (stdlib only, standalone; no FastAPI/backend imports)
│   ├── blender_scripts/         # Auto Mesh Tuner: runs INSIDE Blender's Python only (never imported by the backend)
│   ├── config/                  # settings (pydantic-settings)
│   ├── core/                    # logging, exceptions, lifecycle, container, events, security, middleware
│   ├── api/                     # FastAPI routers (v1), deps
│   ├── schemas/                 # Pydantic API schemas (request/response)
│   ├── models/                  # domain models + enums (NOT ML models)
│   ├── storage/                 # runtime paths + persistence (JSON metadata on disk)
│   ├── processing/              # image validation/normalization, mesh stats (+ part-aware 2D helpers, section 11)
│   ├── inference/               # engine abstraction (base.py) + trellis/ adapter (+ part-aware providers, section 11)
│   ├── parts/                   # part-aware asset library: taxonomy, manifest, geometry (torch-free, section 11)
│   ├── pipelines/               # generation orchestration + reference strategies (+ run executors, section 11)
│   ├── services/                # application services used by API
│   ├── workers/                 # job queue + GPU generation worker
│   ├── utils/                   # small helpers (ids, time, files)
│   ├── runtime/                 # DATA (gitignored): uploads/, jobs/, temp/, cache/, blender/
│   └── logs/                    # log files (gitignored contents)
├── frontend/                    # React 19 + Vite 8 + TS 6
└── test/                        # ALL tests, test scripts, test caches/configs; test/images = sample inputs
```

Python import style: backend root is put on `sys.path` by `main.py` (and by `test/conftest.py`), so imports are
absolute top-level: `from core.exceptions import NotFoundError`, `from inference.base import InferenceEngine`.
Every package directory has an `__init__.py`. Python 3.14 syntax allowed. Type hints everywhere.

---------------------------------------------------------------------------------------------------
## 2. Configuration (`config/settings.py`)

`Settings(BaseSettings)` (pydantic-settings), `get_settings()` cached accessor. Reads `backend/.env`
(path resolved relative to the backend dir, not CWD) with `extra="ignore"`. Env prefix `IMG3D_` for app settings,
EXCEPT these unprefixed secrets/keys that already exist in `.env`: `HF_TOKEN`, `UI_USERNAME`, `UI_PASSWORD`
(use `validation_alias=AliasChoices(...)`). Secrets are `SecretStr` and never logged.

| field | env | default |
|---|---|---|
| app_name | IMG3D_APP_NAME | "Img3D" |
| app_version | — | "1.0.0" |
| environment | IMG3D_ENVIRONMENT | "development" (`development`/`production`) |
| host / port | IMG3D_HOST / IMG3D_PORT | "127.0.0.1" / 8000 |
| cors_origins | IMG3D_CORS_ORIGINS | ["http://localhost:5173","http://127.0.0.1:5173"] (comma-separated or JSON list) |
| backend_dir | — | dir of settings package parent (computed) |
| model_dir | IMG3D_MODEL_DIR | backend/.model/TRELLIS.2-4B |
| aux_model_root | IMG3D_AUX_MODEL_ROOT | backend/.model |
| trellis_source_dir | IMG3D_TRELLIS_SOURCE_DIR | backend/third_party/TRELLIS.2 |
| hf_home | IMG3D_HF_HOME | backend/.model/.hf_cache |
| hf_token | HF_TOKEN | None (SecretStr) |
| allow_model_downloads | IMG3D_ALLOW_MODEL_DOWNLOADS | true (aux models only; TRELLIS.2-4B is never downloaded) |
| engine | IMG3D_ENGINE | "trellis2" |
| device | IMG3D_DEVICE | "cuda" |
| low_vram | IMG3D_LOW_VRAM | true |
| attention_backend | IMG3D_ATTENTION_BACKEND | "auto" (`auto`/`flash_attn`/`xformers`/`sdpa`) |
| preload_model | IMG3D_PRELOAD_MODEL | true |
| reference_mode | IMG3D_REFERENCE_MODE | "primary_only" (`primary_only`/`multi_view`; used when a request leaves `settings.reference_mode` null) |
| runtime_dir | IMG3D_RUNTIME_DIR | backend/runtime |
| log_dir | IMG3D_LOG_DIR | backend/logs |
| log_level | IMG3D_LOG_LEVEL | "INFO" |
| log_json_console | IMG3D_LOG_JSON_CONSOLE | false |
| max_upload_mb | IMG3D_MAX_UPLOAD_MB | 25 |
| max_image_pixels | IMG3D_MAX_IMAGE_PIXELS | 64_000_000 |
| min_image_side | IMG3D_MIN_IMAGE_SIDE | 64 |
| max_detail_images | IMG3D_MAX_DETAIL_IMAGES | 8 |
| max_queue_size | IMG3D_MAX_QUEUE_SIZE | 16 |
| job_retention_days | IMG3D_JOB_RETENTION_DAYS | 14 |
| max_stored_jobs | IMG3D_MAX_STORED_JOBS | 200 |
| reference_set_retention_days | IMG3D_REFERENCE_SET_RETENTION_DAYS | 30 |
| auth_enabled | IMG3D_AUTH_ENABLED | None → auto: enabled iff UI_USERNAME and UI_PASSWORD are both set |
| ui_username / ui_password | UI_USERNAME / UI_PASSWORD | None |
| auth_token_ttl_hours | IMG3D_AUTH_TOKEN_TTL_HOURS | 12 |
| auth_secret | IMG3D_AUTH_SECRET | None → random per process (tokens invalid after restart) |
| frontend_dist_dir | IMG3D_FRONTEND_DIST_DIR | ../frontend/dist (served at `/` if it exists) |
| glb_webp_textures | IMG3D_GLB_WEBP_TEXTURES | true |
| shutdown_timeout_s | IMG3D_SHUTDOWN_TIMEOUT_S | 20 |
| ws_ping_interval_s | IMG3D_WS_PING_INTERVAL_S | 20.0 (> 0; WebSocket `ping` interval) |
| device_info_timeout_s | IMG3D_DEVICE_INFO_TIMEOUT_S | 2.0 (> 0; `/system` never waits longer for device info) |
| device_info_cache_s | IMG3D_DEVICE_INFO_CACHE_S | 5.0 (>= 0; device info cache lifetime) |
| retention_interval_s | IMG3D_RETENTION_INTERVAL_S | 3600.0 (> 0; retention cleanup period) |
| blender_enabled | IMG3D_BLENDER_ENABLED | true (Auto Mesh Tuner master switch) |
| blender_executable | IMG3D_BLENDER_EXECUTABLE | None (admin override; wins when compatible) |
| blender_search_dirs | IMG3D_BLENDER_SEARCH_DIRS | [] (JSON list or `;`-separated) |
| blender_min_version | IMG3D_BLENDER_MIN_VERSION | "5.1" |
| blender_install_dir | IMG3D_BLENDER_INSTALL_DIR | None -> `<runtime_dir>/blender` |
| blender_allow_download | IMG3D_BLENDER_ALLOW_DOWNLOAD | true (always after explicit confirmation) |
| blender_download_base_url | IMG3D_BLENDER_DOWNLOAD_BASE_URL | "https://download.blender.org/release/" (https only) |
| blender_download_channel | IMG3D_BLENDER_DOWNLOAD_CHANNEL | "latest" (`latest`/`lts`) |
| blender_detect_on_startup | IMG3D_BLENDER_DETECT_ON_STARTUP | true (background task; `test/conftest.py` sets false) |
| blender_detect_timeout_s | IMG3D_BLENDER_DETECT_TIMEOUT_S | 20 |
| blender_tune_timeout_s | IMG3D_BLENDER_TUNE_TIMEOUT_S | 1800 |
| blender_max_queue_size | IMG3D_BLENDER_MAX_QUEUE_SIZE | 4 |

`Settings.auth_required` property, `Settings.paths` → `storage.paths.RuntimePaths`, `Settings.blender_runtime_dir`,
`Settings.blender_temp_dir` (`<runtime_dir>/temp/blender`), `Settings.blender_scripts_dir`.

Part-aware layer settings (added by slice S1; all optional, env prefix `IMG3D_`; effects in section 11.10).
`safe_summary()` is unchanged; the part-aware container logs its own `part_aware_summary()`.

| field | env | default |
|---|---|---|
| part_aware_enabled | IMG3D_PART_AWARE_ENABLED | true (master switch of section 11) |
| image_generation_enabled | IMG3D_IMAGE_GENERATION_ENABLED | true |
| segmentation_enabled | IMG3D_SEGMENTATION_ENABLED | true |
| part_analysis_enabled | IMG3D_PART_ANALYSIS_ENABLED | true |
| workflows_enabled | IMG3D_WORKFLOWS_ENABLED | true |
| mesh_tuning_enabled | IMG3D_MESH_TUNING_ENABLED | **false** (code gate on `POST /jobs/{id}/tunings`; `test/conftest.py` opts in for the test session) |
| part_refinement_enabled | IMG3D_PART_REFINEMENT_ENABLED | **false** |
| approved_models | IMG3D_APPROVED_MODELS | None → catalog ids with `default_approved=True` (JSON list or `,`/`;`-separated; blank → None; `[]` approves nothing) |
| model_paths | IMG3D_MODEL_PATHS | {} (JSON map model id → local path, relative to `backend/`) |
| allow_restricted_licenses | IMG3D_ALLOW_RESTRICTED_LICENSES | false |
| t2i_default_model | IMG3D_T2I_DEFAULT_MODEL | None → first usable text-to-image model |
| segmentation_default_profile | IMG3D_SEGMENTATION_DEFAULT_PROFILE | "auto" |
| run_queue_max_size | IMG3D_RUN_QUEUE_MAX_SIZE | 8 (1..100; shared by `ig_`, `sg_`, `pa_`) |
| aux_keep_models_in_ram | IMG3D_AUX_KEEP_MODELS_IN_RAM | true |
| aux_model_idle_unload_s | IMG3D_AUX_MODEL_IDLE_UNLOAD_S | 0 (never) |
| aux_vram_budget_gb | IMG3D_AUX_VRAM_BUDGET_GB | 28.0 |
| engine_resident_vram_gb | IMG3D_ENGINE_RESIDENT_VRAM_GB | 17.0 |
| aux_vram_margin_gb | IMG3D_AUX_VRAM_MARGIN_GB | 1.5 |
| prompt_max_chars | IMG3D_PROMPT_MAX_CHARS | 2000 (1..8000) |
| parts_default_views | IMG3D_PARTS_DEFAULT_VIEWS | 8 (0..24) |
| parts_min_part_faces | IMG3D_PARTS_MIN_PART_FACES | None (→ `max(50, round(1e-4·F))`) |
| parts_max_parts | IMG3D_PARTS_MAX_PARTS | 64 |
| run_max_defer_s | IMG3D_RUN_MAX_DEFER_S | 600 (0..3600; 0 = no jobs-first gate) |
| parts_export_cache_max_gb | IMG3D_PARTS_EXPORT_CACHE_MAX_GB | 10 |
| part_analysis_timeout_s | IMG3D_PART_ANALYSIS_TIMEOUT_S | 1800 |
| workflow_graph_max_bytes | IMG3D_WORKFLOW_GRAPH_MAX_BYTES | 65536 |
| workflow_sweep_interval_s | IMG3D_WORKFLOW_SWEEP_INTERVAL_S | 5.0 |
| imagegen_retention_days / segmentation_retention_days / workflow_retention_days | IMG3D_… | 14 / 14 / 30 |
| max_stored_image_generations / max_stored_segmentations / max_stored_workflows | IMG3D_… | 200 / 200 / 500 |
| persist_generation_intermediates | IMG3D_PERSIST_GENERATION_INTERMEDIATES | false (**inert**: not approved; always reported false) |

---------------------------------------------------------------------------------------------------
## 3. Domain enums (`models/enums.py`) — implemented, do not change values

```python
class ReferenceSlot(StrEnum):  front, back, left, right, face_front, face_side, detail
SINGLE_SLOTS = (front, back, left, right, face_front, face_side)      # max 1 image each
SLOT_PRIMARY_PRIORITY = (front, face_front, left, right, back, face_side, detail)
class JobStatus(StrEnum):      queued, running, completed, failed, cancelled
TERMINAL_STATUSES = {completed, failed, cancelled}
class JobStage(StrEnum):       queued, waiting_for_model, preprocessing, conditioning, sparse_structure,
                               shape, texture, decoding, exporting, finalizing, done
class EngineState(StrEnum):    not_loaded, loading, ready, error, unavailable
class ReferenceMode(StrEnum):  primary_only, multi_view
```

---------------------------------------------------------------------------------------------------
## 4. Errors (`core/exceptions.py`) — implemented

`AppError(message, *, code, status_code, details)` and subclasses:
`ValidationAppError`(422,"validation_error"), `NotFoundError`(404,"not_found"), `ConflictError`(409,"conflict"),
`PayloadTooLargeError`(413,"payload_too_large"), `UnsupportedMediaTypeError`(415,"unsupported_media_type"),
`AuthenticationError`(401,"unauthorized"), `QueueFullError`(429,"queue_full"),
`EngineUnavailableError`(503,"engine_unavailable"), `EngineError`(500,"engine_error"),
`JobCancelledError`(409,"job_cancelled"; raised inside the worker to abort), `StorageError`(500,"storage_error").

Auto Mesh Tuner errors (`services/blender_runtime_service.py`, mapped from `launcher.errors`; the launcher's own
`code` is kept): `blender_not_found` 404 · `blender_incompatible` 422 · `blender_path_too_long` 422 ·
`blender_unavailable` 503 (message = the runtime's user prompt, e.g. "Blender 4.2.17 is installed but Auto Mesh Tuner
requires Blender 5.1 or newer. Download a supported portable Blender?") · `blender_download_disabled` 403 ·
`blender_download_in_progress` 409 · `blender_download_cancelled` 409 · `blender_release_unresolved` 502 ·
`blender_download_failed` / `blender_checksum_mismatch` / `blender_unsafe_archive` 502 · `confirmation_required` 422
(download without `confirm: true`).

Part-aware errors (`core/feature_errors.py`, slice S1; full table with `details` shapes in section 11.8):
`FeatureDisabledError`(503,"feature_disabled") · `CapabilityUnavailableError`(503,"capability_unavailable") ·
`ModelUnavailableError`(503,"model_unavailable") · `InvalidWorkflowError`(422,"invalid_workflow"). New per-field
`validation_error` detail types: `unsupported_by_model`, `conflicting_fields`, `invalid_characters`, `unknown_class`,
`graph_too_large`. Run and workflow failure codes (`run.error.code`) are listed in section 11.8.

Error response body for EVERY error (including FastAPI validation errors and unhandled exceptions):
```json
{ "error": { "code": "not_found", "message": "Job not found", "details": null, "request_id": "a1b2c3" } }
```
Unhandled exceptions → 500 `internal_error`, generic message, full traceback logged (never returned).

---------------------------------------------------------------------------------------------------
## 5. Inference seam (`inference/base.py`) — implemented; engines implement `InferenceEngine`

Key types (see file for exact definitions):
- `EngineCapabilities` (resolutions, texture_sizes, polygon_target min/max/default, max_reference_images,
  reference_modes, supports_cancel, supports_texture) + `to_dict()`.
- `EngineStatus(state, message, load_progress: LoadProgress|None, error, loaded_at, details)` + `to_dict()`.
- `SamplerOverrides(steps, guidance_strength, guidance_rescale, rescale_t)` (all optional).
- `GenerationParams(resolution, seed, polygon_target, texture_size, remove_background,
  sparse_structure, shape, texture, glb_webp_textures)`.
- `ConditioningImage(image_id, slot, path, is_primary)`.
- `GenerationRequest(job_id, images (primary first, strategy-selected), all_references, params, output_dir, reference_mode)`.
- `GenerationOutput(glb_path, preview_path, effective_resolution, timings, mesh_stats, metadata)`.
- `ProgressReporter` protocol: `stage(stage: JobStage, message=None, total_steps=None)`, `step(step, total_steps=None, message=None)`.
- `CancellationToken`: `cancel()`, `is_cancelled`, `raise_if_cancelled()` (raises `JobCancelledError`).
- `InferenceEngine(ABC)`: `name`, `status()`, `capabilities()`, `load(on_status=None)` (blocking, idempotent,
  thread-safe), `unload()`, `generate(request, reporter, cancel) -> GenerationOutput` (blocking; called from the
  worker thread; must call `cancel.raise_if_cancelled()` between stages and at every sampler step; must free GPU
  memory on exit, including on cancel/error).
- `inference/registry.py`: `create_engine(settings) -> InferenceEngine` (lazy-imports `inference.trellis.engine`
  so the API can start even if torch/extensions are broken → engine reports `unavailable`).

Engine stage order emitted for TRELLIS.2: preprocessing → conditioning → sparse_structure → shape → texture →
decoding → exporting. (`finalizing` and `done` are emitted by the pipeline/worker.)

Resolution → TRELLIS pipeline_type: 512→"512", 1024→"1024_cascade", 1536→"1536_cascade".
Upstream defaults: steps 12 each; SS guidance 7.5/rescale 0.7/rescale_t 5.0; shape 7.5/0.5/3.0; tex 1.0/0.0/3.0.
GLB export: `o_voxel.postprocess.to_glb(vertices, faces, attr_volume=mesh.attrs, coords=mesh.coords,
attr_layout=pipeline.pbr_attr_layout, grid_size=res, aabb=[[-.5]*3,[.5]*3], decimation_target=polygon_target,
texture_size=texture_size, remesh=True, remesh_band=1, remesh_project=0)` then `glb.export(path, extension_webp=...)`.

---------------------------------------------------------------------------------------------------
## 6. Multi-reference design

- A **ReferenceSet** holds categorized images. Slots: front, back, left, right, face_front, face_side (max 1 each)
  and detail (max `max_detail_images`). Exactly one image is primary when the set is non-empty.
- Primary rules: first uploaded image becomes primary; `make_primary=true` on upload or `PUT .../primary`
  changes it; deleting the primary auto-promotes the next image by `SLOT_PRIMARY_PRIORITY` then upload order.
- Uploading to an occupied single slot REPLACES the old image (old file deleted; primary flag carries over).
- A Job snapshots ALL references (ids, slots, primary flag) and copies the image files into the job dir, so
  later edits to the set don't affect it.
- `pipelines/reference_strategy.py`: `ReferenceStrategy` ABC with `select(references, capabilities) -> list[ConditioningImage]`.
  `PrimaryOnlyStrategy` (implemented, default) and `MultiViewStrategy` (placeholder: raises
  `ValidationAppError("multi_view reference mode is not supported by the current engine")` unless
  `capabilities.max_reference_images > 1`). Strategy chosen by `settings.reference_mode`.
  The engine receives `images` (selected) AND `all_references` (everything, for future engines).

---------------------------------------------------------------------------------------------------
## 7. REST API (prefix `/api/v1`) — JSON, snake_case fields, ISO-8601 UTC timestamps (`...Z`)

IDs: `rs_<12 hex>` reference set, `img_<12 hex>` image, `job_<12 hex>` job. Part-aware resources (section 11):
`ig_` image generation, `sg_` segmentation, `pa_` part analysis, `wf_` workflow (same `<prefix>_<12 hex>` format,
checked with `utils.ids.is_valid_id` before any filesystem access).

### 7.1 Auth (public endpoints)
- `GET /auth/status` → `{ "auth_required": bool, "authenticated": bool, "username": str|null }`
- `POST /auth/login` `{username, password}` → `{ "token": str, "token_type": "bearer", "expires_at": iso }` (401 on bad creds; constant-time compare)
- `POST /auth/logout` → 204 (revokes token)
When auth is required, all other endpoints except `/health` require `Authorization: Bearer <token>`;
GET file endpoints and the WebSocket ALSO accept `?token=<token>` (needed for <img>, GLTFLoader, WebSocket).

### 7.2 System
- `GET /health` (public) → `{ "status": "ok", "app": str, "version": str, "uptime_seconds": float, "time": iso }`
- `GET /system` → `SystemStatus`:
```json
{
  "app": {"name": "Img3D", "version": "1.0.0", "environment": "development"},
  "engine": {
    "name": "trellis2", "state": "loading", "message": "Loading shape_slat_flow_model_1024 (4/8)",
    "load_progress": {"loaded": 3, "total": 8, "current": "shape_slat_flow_model_1024"},
    "error": null, "loaded_at": null,
    "details": {"attention_backend": "sdpa", "model_dir": "...", "low_vram": true},
    "capabilities": {
      "resolutions": [512, 1024, 1536], "texture_sizes": [512, 1024, 2048, 4096],
      "polygon_target": {"min": 5000, "max": 2000000, "default": 500000},
      "max_reference_images": 1, "reference_modes": ["primary_only"],
      "supports_cancel": true, "supports_texture": true
    }
  },
  "device": {"cuda_available": true, "name": "NVIDIA GeForce RTX 5090", "compute_capability": "12.0",
             "vram_total_mb": 32607, "vram_used_mb": 9000, "vram_free_mb": 23607,
             "torch_version": "2.14.1+cu130", "cuda_version": "13.0"},
  "queue": {"pending": 0, "running_job_id": null, "max_size": 16},
  "limits": {"max_upload_mb": 25, "allowed_mime_types": ["image/png","image/jpeg","image/webp"],
             "max_detail_images": 8, "min_image_side": 64,
             "slots": ["front","back","left","right","face_front","face_side","detail"]},
  "defaults": {"resolution": 1024, "seed": 0, "randomize_seed": true, "polygon_target": 500000,
               "texture_size": 2048, "remove_background": true, "reference_mode": "primary_only"},
  "auth_required": false
}
```
  `device` is `null` if torch is not importable. `device` must be obtained WITHOUT blocking on the GPU worker.
- `POST /system/engine/load` → 202 SystemStatus (starts background load if not loaded/loading)
- `POST /system/engine/unload` → SystemStatus (409 if a job is running)

### 7.3 Reference sets
`ReferenceImage`:
```json
{ "id": "img_…", "set_id": "rs_…", "slot": "front", "label": null, "original_filename": "a.png",
  "mime_type": "image/png", "width": 1024, "height": 1536, "has_alpha": true, "size_bytes": 123,
  "sha256": "…", "is_primary": true, "created_at": "…Z",
  "urls": {"file": "/api/v1/reference-sets/rs_…/images/img_…/file",
           "thumbnail": "/api/v1/reference-sets/rs_…/images/img_…/thumbnail"} }
```
Additive (slice S4, section 11.6.6): `ReferenceImage` gains `"source": ImageSourceOut | null` (`null` for uploads;
set for images imported from a prompt-generated candidate). `set.json` omits the key for uploads.

`ReferenceSet`: `{ "id", "name": str|null, "created_at", "updated_at", "primary_image_id": str|null,
"images": ReferenceImage[] (ordered by slot order then created_at), "slot_limits": {"front":1,…,"detail":8} }`

- `POST /reference-sets` `{name?: str}` → 201 ReferenceSet
- `GET /reference-sets` → `{ "items": ReferenceSet[] }` (newest first)
- `GET /reference-sets/{set_id}` → ReferenceSet
- `DELETE /reference-sets/{set_id}` → 204
- `POST /reference-sets/{set_id}/images` multipart form: `file` (required), `slot` (required ReferenceSlot),
  `label` (optional), `make_primary` (optional bool) → 201 `{ "image": ReferenceImage, "set": ReferenceSet }`
  Errors: 413 too large, 415 not an image / unsupported type (validated by decoding with Pillow, not by extension),
  422 bad slot / too small / too many detail images.
- `PATCH /reference-sets/{set_id}/images/{image_id}` `{slot?, label?}` → ReferenceSet (409 if target single slot occupied)
- `DELETE /reference-sets/{set_id}/images/{image_id}` → ReferenceSet
- `PUT /reference-sets/{set_id}/primary` `{image_id}` → ReferenceSet
- `GET /reference-sets/{set_id}/images/{image_id}/file` → original bytes (correct content-type, cache headers)
- `GET /reference-sets/{set_id}/images/{image_id}/thumbnail` → `image/webp`, max side 384

### 7.4 Jobs
`GenerationSettings` (request; all optional with defaults):
```json
{ "resolution": 1024,              // 512 | 1024 | 1536
  "seed": 0,                       // 0..2147483647
  "randomize_seed": true,
  "polygon_target": 500000,        // 5000..2000000 (target face count after decimation)
  "texture_size": 2048,            // 512 | 1024 | 2048 | 4096
  "remove_background": true,       // true: RGBA alpha is used if present, else RMBG removes the background, then crop+center.
                                   // false: if the image has a real alpha channel it is still used for crop; otherwise the image is used untouched.
  "reference_mode": "primary_only",
  "advanced": {                    // optional; each sub-object optional; each field optional
    "sparse_structure": {"steps": 12, "guidance_strength": 7.5, "guidance_rescale": 0.7, "rescale_t": 5.0},
    "shape":            {"steps": 12, "guidance_strength": 7.5, "guidance_rescale": 0.5, "rescale_t": 3.0},
    "texture":          {"steps": 12, "guidance_strength": 1.0, "guidance_rescale": 0.0, "rescale_t": 3.0}
  } }
```
Bounds: steps 1..50, guidance_strength 0..10, guidance_rescale 0..1, rescale_t 1..6. Unknown fields → 422.

`CreateJobRequest`: `{ "reference_set_id": str, "primary_image_id": str|null, "settings": GenerationSettings }`
(primary override must belong to the set; set must have ≥1 image → else 422).

`Job`:
```json
{ "id": "job_…", "status": "running", "engine": "trellis2",
  "created_at": "…Z", "started_at": "…Z"|null, "finished_at": null,
  "reference_set_id": "rs_…", "primary_image_id": "img_…",
  "references": [{"image_id": "img_…", "slot": "front", "is_primary": true}],
  "settings": GenerationSettings,           // as submitted (normalized with defaults)
  "seed": 123456,                           // resolved seed actually used (set at creation)
  "queue_position": null,                   // 1-based while queued, else null
  "progress": {
     "stage": "shape", "stage_label": "Generating geometry", "stage_index": 4, "stage_count": 9,
     "step": 7, "total_steps": 24, "percent": 52.3, "message": "Shape SLat step 7/24",
     "elapsed_seconds": 14.2, "eta_seconds": 12.0, "updated_at": "…Z" },
  "error": null,                            // {code, message, details} when failed
  "result": null }                          // JobResult when completed
```
Stage table (index/label/weight for percent): queued(0,"Queued",0) waiting_for_model(0,"Waiting for model",0)
preprocessing(1,"Preparing image",5) conditioning(2,"Encoding image",5) sparse_structure(3,"Building structure",15)
shape(4,"Generating geometry",30) texture(5,"Generating texture",20) decoding(6,"Decoding mesh",8)
exporting(7,"Exporting GLB",15) finalizing(8,"Finalizing",2) done(9,"Done",0); stage_count = 9.
percent = sum(weights of completed stages) + weight(current) * step/total_steps (or 0 if no steps); 100 when completed.

`JobResult`:
```json
{ "model_url": "/api/v1/jobs/job_…/model.glb", "download_url": "/api/v1/jobs/job_…/export/glb",
  "preview_url": "/api/v1/jobs/job_…/preview",
  "stats": { "vertices": 250123, "faces": 499870, "materials": 1, "textures": 3, "texture_size": 2048,
             "has_uv": true, "bbox_min": [-0.5,-0.5,-0.5], "bbox_max": [0.5,0.5,0.5],
             "dimensions": [1.0,1.0,1.0], "file_size_bytes": 12345678 },
  "timings": {"preprocessing": 1.2, "conditioning": 0.8, "…": 0, "total": 42.0},
  "seed": 123456, "resolution": 1024, "effective_resolution": 1024,
  "texture_size": 2048, "polygon_target": 500000 }
```
- `POST /jobs` → 202 Job  (429 queue_full, 404 set/image not found, 422 invalid settings/ref mode)
- `GET /jobs?limit=20&status=` → `{ "items": Job[] }` newest first
- `GET /jobs/{job_id}` → Job
- `POST /jobs/{job_id}/cancel` → Job (queued → cancelled immediately; running → cancel requested, status becomes
  `cancelled` when the worker stops; terminal → 409)
- `DELETE /jobs/{job_id}` → 204 (409 if queued/running; deletes artifacts)
- `GET /jobs/{job_id}/result` → JobResult (409 if not completed)
- `GET /jobs/{job_id}/preview` → `image/png` processed conditioning image (404 until produced)
- `GET /jobs/{job_id}/model.glb` → `model/gltf-binary` inline (for the viewer)
- `GET /jobs/{job_id}/export/glb` → `model/gltf-binary` attachment, filename `img3d_<job_id>.glb`

### 7.5 Auto Mesh Tuner (Blender >= 5.1; full design: `.claude/skills/img3d-auto-mesh-tuner/DESIGN.md` section 6)
IDs: `tn_<12 hex>` tuning run (validated before any filesystem access).

`BlenderRuntimeOut`: `{state: detecting|ready|needs_download|downloading|error, mode: auto|system|embedded,
min_version, selected: BlenderInstallOut|null, selection_reason, installations: BlenderInstallOut[] (version desc),
message (user prompt when nothing is usable), warnings[], runtime_root, can_download, download: DownloadOut|null,
release: BlenderReleaseOut|null, last_scan_at, scan_duration_ms, error}`.
`BlenderInstallOut`: `{id, executable, kind: system|embedded, origins[], version, version_label, version_tuple[3], cycle,
is_lts, is_stable, build_hash, build_date, architecture, valid, compatible, incompatible_reason, error, probe_ms, selected}`.
`DownloadOut`: `{state: idle|resolving|downloading|verifying|extracting|validating|completed|failed|cancelled, version,
file_name, url, bytes_done, bytes_total, percent, speed_bps, eta_s, resumed, verified, error, error_code, install_dir,
started_at, updated_at}`. `BlenderReleaseOut`: `{version, series, file_name, url, host, size_bytes, sha256,
checksum_url, architecture, is_lts, release_date}`.

- `GET /blender/runtime` → BlenderRuntimeOut (the first call without a scan starts detection in the background)
- `POST /blender/runtime/detect` → BlenderRuntimeOut (forced rescan in a worker thread)
- `PUT /blender/runtime/selection` `{mode, executable?}` → BlenderRuntimeOut (404 blender_not_found, 422 blender_incompatible)
- `POST /blender/runtime/release` → BlenderReleaseOut (502 blender_release_unresolved)
- `POST /blender/runtime/download` `{confirm: true, version?: "X.Y.Z"}` → 202 BlenderRuntimeOut (409 in progress, 403 disabled, 422 when confirm is not true)
- `DELETE /blender/runtime/download` → 202 BlenderRuntimeOut (cancel; the partial file is kept for resuming)

`TuningOptions` (request body; all optional, `null` = preset value; unknown fields → 422): `preset`
(conservative|balanced|strong, default balanced), `remove_inner_shell`, `fill_holes`, `max_hole_edges` (8..5000),
`remove_islands`, `island_min_ratio` (0..0.05), `smoothing` (0..1), `refine_face`, `eye_correction`, `symmetry`
(auto|off|repair|symmetrize), `refine_density`, `quads`, `subdivision` (auto|off|modifier), `optimize_rig`,
`render_previews`, `save_blend`. Presets: conservative = smoothing .25, refine_density false, max_hole_edges 120;
strong = smoothing .8, max_hole_edges 1000, island_min_ratio .003.

`TuningRunOut`: `{id, job_id, status: queued|running|completed|failed|cancelled, created_at, started_at, finished_at,
options (effective), progress: {percent, step_id, step_index (1-based), step_count, message, updated_at},
steps: [{id, label, status: pending|running|done|warning|skipped|failed, message, duration_s}], metrics: Metric[],
warnings[], validation|null, blender: {executable, version, kind}|null, result: {model_url, download_url, report_url,
log_url, previews: [{name, view, variant, url}], stats: MeshStats|null}|null, error: {code, message, details}|null}`.
Step ids (fixed order): load, analyze, geometry_repair, face_refinement, topology_tuning, rig_optimization,
validation, export. Failure codes: `blender_unavailable`, `blender_timeout`, `blender_crashed`, `blender_script_error`,
`blender_launch_failed`, `blender_path_too_long`, `tuning_output_missing`, `input_missing`, `interrupted`, or the
script's own `error` event code.

- `POST /jobs/{job_id}/tunings` TuningOptions → 202 TuningRunOut (404 job, 409 job not completed, 503 blender_unavailable, 429 queue_full)
- `GET /jobs/{job_id}/tunings` → `{items: TuningRunOut[]}` newest first
- `GET /tunings/{tuning_id}` → TuningRunOut
- `POST /tunings/{tuning_id}/cancel` → 202 TuningRunOut (queued → cancelled; running → Blender killed; terminal → 409)
- `DELETE /tunings/{tuning_id}` → 204 (409 if queued/running)
- File routes (header or `?token=`): `GET /tunings/{id}/report` (report.json), `GET /tunings/{id}/model.glb` (inline,
  409 until completed), `GET /tunings/{id}/export/glb` (attachment `img3d_<job>_tuned_<tn>.glb`),
  `GET /tunings/{id}/previews/{name}` (`^[a-z0-9_]+\.png$`), `GET /tunings/{id}/log` (text/plain tail of at most
  256 KB, live while running).
`DELETE /jobs/{job_id}` first cancels the job's queued/running tunings (killing Blender), then deletes everything.

---------------------------------------------------------------------------------------------------
## 8. WebSocket — `GET /api/v1/ws` (upgrade), optional `?job_id=` filter, `?token=` when auth required

Server → client messages (JSON text frames):
```json
{ "type": "hello",          "ts": "…Z", "data": { "system": SystemStatus } }
{ "type": "job.updated",    "ts": "…Z", "data": Job }             // status/progress change (progress throttled ≤ 5/s per job; status changes never dropped)
{ "type": "system.updated", "ts": "…Z", "data": SystemStatus }    // engine state/load progress/queue change
{ "type": "blender.updated", "ts": "…Z", "data": BlenderRuntimeOut } // detection/selection/download (progress frames droppable)
{ "type": "tuning.updated", "ts": "…Z", "data": TuningRunOut }    // tuning status/steps/progress (progress <= 5/s, droppable)
{ "type": "ping",           "ts": "…Z", "data": {} }              // every ws_ping_interval_s (20 s)
// Part-aware layer (slice S1 registers the types; section 11.7 has scoping and throttling):
{ "type": "features.updated",         "ts": "…Z", "data": FeaturesOut }          // debounced 250 ms; never dropped
{ "type": "image_generation.updated", "ts": "…Z", "data": ImageGenerationOut }   // progress <= 5/s, droppable
{ "type": "segmentation.updated",     "ts": "…Z", "data": SegmentationOut }      // progress <= 5/s, droppable
{ "type": "part_analysis.updated",    "ts": "…Z", "data": PartAnalysisOut }      // job-scoped; progress <= 5/s
{ "type": "workflow.updated",         "ts": "…Z", "data": WorkflowOut }          // progress <= 2/s, droppable
```
`?job_id=` restricts the job-scoped `job.updated`, `tuning.updated` and `part_analysis.updated` events to that job;
other types always pass. Status and step transitions are never dropped; clients ignore unknown types.
Client → server: `{ "type": "pong" }` (optional), anything else ignored. Auth failure → close code 4401.
Server shutdown → close code 1001. The REST API remains the source of truth (clients may poll `GET /jobs/{id}`).

---------------------------------------------------------------------------------------------------
## 9. Backend runtime architecture

- `core/events.py` `EventBus`: async pub/sub; `publish(event)` thread-safe via `publish_threadsafe` using
  `loop.call_soon_threadsafe`; subscribers get bounded `asyncio.Queue`s (drop oldest progress events on overflow).
- `workers/queue.py` `JobQueue`: FIFO of job ids, max size, removal on cancel, positions.
- `workers/generation_worker.py` `GenerationWorker`: one asyncio task consuming the queue; runs each job's blocking
  pipeline in a dedicated single-thread `ThreadPoolExecutor` (GPU work never runs on the event loop). Waits for the
  engine to be ready (stage `waiting_for_model`); fails the job with `engine_unavailable` if the engine errored.
  Cancellation via per-job `CancellationToken`. Graceful stop: stop accepting, cancel current job, wait ≤ timeout.
- Engine load runs in the same GPU executor (so load and generate never overlap) — started at startup when
  `preload_model` is true; API is available immediately while loading.
- `pipelines/image_to_3d.py` `ImageTo3DPipeline.run(job, reporter, cancel)`: resolves references from the job dir,
  applies the ReferenceStrategy, builds `GenerationRequest`, calls `engine.generate`, computes stats
  (`processing/mesh_stats.py` via trimesh on the exported GLB), builds `JobResult`.
- `storage/`: `RuntimePaths` (uploads/, jobs/, temp/, cache/ under runtime_dir; created at startup; temp purged at
  startup); `ReferenceStore` (`runtime/uploads/<set_id>/set.json` + `<image_id>.<ext>` + `<image_id>_thumb.webp`);
  `JobStore` (`runtime/jobs/<job_id>/job.json`, `inputs/`, `preview.png`, `model.glb`). Atomic JSON writes
  (write temp + `os.replace`). On startup, jobs left queued/running are marked failed (`interrupted`).
  Retention cleanup task (hourly) enforces job_retention_days / max_stored_jobs / reference_set_retention_days.
- Auto Mesh Tuner: `services/blender_runtime_service.py` wraps `launcher.BlenderRuntimeManager` (blocking calls in
  threads, launcher errors → AppError, `blender.updated` via `publish_threadsafe`); detection runs as a startup
  background task (never in `build_container`). `workers/tuning_worker.py` `TuningWorker`: own asyncio consumer +
  `ThreadPoolExecutor(1, "blender-worker")` (never the GPU thread); copies the job's `model.glb` to
  `runtime/temp/tuning/<tn>/input.glb`, writes `cfg.json`, runs Blender through `launcher.BlenderProcessRunner`
  (Job Object kill-tree, per-run TEMP `runtime/temp/blender/<tn>`, `BLENDER_USER_RESOURCES`, timeout, cancel), maps
  `@@AMT@@` events to steps/progress, then moves the outputs into `runtime/jobs/<job>/tuning/<tn>/` (run.json,
  tuned.glb, report.json, blender.log, config.json, previews/*.png, optional tuned.blend) after re-checking that the
  job exists. `storage/tuning_store.py` persists `run.json`; runs left queued/running are marked failed
  (`interrupted`) at startup; shutdown cancels + kills Blender and cancels a portable download.
- `core/logging.py`: structured logging; console (human, colored) + rotating JSON file `logs/img3d.log`
  (10 MB × 5) + `logs/error.log` (WARNING+). Context vars: request_id, job_id. Uvicorn loggers routed through it.
- `core/middleware.py`: request-id (X-Request-ID in/out), access log with duration, security headers.
- `main.py`: bootstrap sys.path (drops the per-user site-packages, puts `backend/` first) → load settings
  (`Settings(**cli overrides)`) → `create_app()`: configure logging → build `ServiceContainer` → FastAPI with
  lifespan (startup: paths, stores, recover jobs, event bus, engine, worker, preload, cleanup task;
  shutdown: worker stop, close websockets, engine unload) → exception handlers → request-context + CORS middleware →
  routers → optional static frontend. `main()` then runs `_build_server(app, settings)`: an `Img3DServer`
  (`uvicorn.Server` subclass whose `shutdown()` first closes every WebSocket with 1001 via `ws_hub.close_all()`, before
  uvicorn tears connections down with 1012) on a `uvicorn.Config(app, host, port, log_config=None, access_log=False,
  server_header=False, proxy_headers=False, timeout_graceful_shutdown=max(1, int(shutdown_timeout_s)),
  lifespan="on")`, then `server.run()`. CLI flags: `--host/--port/--no-preload/--log-level`.
  Windows: Ctrl+C is handled cleanly and Ctrl+Break (SIGBREAK) is mapped to `KeyboardInterrupt`; the default
  `asyncio` loop is used.
- Part-aware layer (section 11): `PartAwareContainer` (built by `build_part_aware()` inside `build_container`, field
  `ServiceContainer.part_aware`) owns the run stores, the `RunWorker` (one run at a time; GPU phases go through
  `GenerationWorker.submit_gpu_task` onto the same `gpu-worker` thread, CPU phases on `parts-cpu`), the provider
  registry, the workflow sweep and the run retention task. Lifecycle: started after the tuning worker, stopped
  **before** `worker.stop()`.

---------------------------------------------------------------------------------------------------
## 10. Frontend architecture (React 19 + Vite 8 + TS 6, dark UI)

- Dev server proxies `/api` (incl. WebSocket) → `http://127.0.0.1:8000`; API base = `import.meta.env.VITE_API_BASE_URL ?? ""`.
- `src/api/` typed client mirroring section 7/8 types (snake_case kept): `client.ts` (axios instance, auth header,
  error normalization to `ApiError {status, code, message, details, requestId}`), `references.ts`, `jobs.ts`,
  `system.ts`, `auth.ts`, `ws.ts` (auto-reconnect w/ backoff, typed events), `types.ts`.
- State: zustand store(s). Components: references panel (7 slots + detail grid, drag/drop, primary star),
  settings panel (resolution segmented 512/1024/1536, seed + randomize, polygon target, texture size, advanced),
  generate/cancel, live progress (stage list + percent + ETA), viewer (R3F + drei: orbit/zoom, wireframe,
  material modes, env/grid toggles, auto-rotate, reset), model stats, GLB download, job history, system status,
  login screen when `auth_required`. Real data only — no mock generation.

---------------------------------------------------------------------------------------------------
## 11. Part-aware layer

**Status: implemented incrementally (S1-S11); pending user review at milestone approval.**

This section is the contract form of `.claude/skills/img3d-part-aware/DESIGN.md` §2-§8 (taxonomy, part asset and
manifest, run pipelines, providers and registry, API, storage, flags). Precedence: the user's messages >
`.claude/skills/img3d-part-aware/DECISIONS.md` > `DESIGN.md` > this section. If this section and `DESIGN.md` disagree,
`DESIGN.md` wins and this section is corrected first (contract-first rule at the top of this file).

Everything here is additive. Until the slice that implements an item has merged, its route is absent (S1 mounts the
new routers empty, so they answer 404) or its capability reports `available: false, code: "not_implemented"`. The
"Slice" columns name the owner (`DESIGN.md` §11). Nothing in this section authorizes tuning, refinement, eye or rig
correction, reference-aware optimization or Blender work.

### 11.0 Scope and invariants
Adds four resource kinds next to the unchanged `job_`: `ig_` (prompt → candidate images, Z-Image-Turbo), `sg_` (2D
segmentation of a reference image or a job's conditioning image), `pa_` (part analysis of a completed job: lift 2D
labels onto `model.glb`, cut physical parts, write the manifest) and `wf_` (server-side chain), plus discovery
(`/features`, `/models`). Out of scope: tuning/refinement, amodal completion (hidden scalp, body under clothes,
eyeballs), per-part regeneration or re-bake, eyebrow/eyelash geometry, in-app model downloads, transformers 5.

| # | Invariant (every slice) | Enforced by |
|---|---|---|
| I1 | The protected files (`DESIGN.md` §1.3) are unchanged, so `model.glb`, `preview.png`, `job.json` and `JobOut` of every image job come from untouched code | `test/backend/test_protected_files.py` + `protected_files.sha256` (sha256 after CRLF→LF) |
| I2 | New code writes inside `jobs/<job>/` only under `jobs/<job>/parts/<pa>/` (`run.json`, `log.txt` on failure, `asset/` published by one atomic rename); `RunStore` never creates `jobs/<job>/` | `test_legacy_invariance.py`, `test_part_analysis_api.py` |
| I3 | No new `JobStage`; `STAGE_TABLE`, the percent formula, `/system` keys, `limits`, `defaults`, `EngineCapabilities.to_dict()` unchanged | protected files + existing pinned tests |
| I4 | `CreateJobRequest`, `GenerationSettings`, `TuningOptions` unchanged | protected `schemas/jobs.py` |
| I5 | One GPU thread: every CUDA call of new code runs in a callable submitted through `GenerationWorker.submit_gpu_task`, inside `torch_globals_guard()`; `ModelProvider.load()` runs on a CPU thread | `test_gpu_task_seam.py`, `test_run_framework.py`, `test/engine/test_aux_isolation.py` |
| I6 | No new pip package; transformers 4.57.6 and huggingface-hub 0.36.2 keep their versions | `test_import_boundaries.py`, `uv pip check` |
| I7 | `api/ services/ pipelines/ parts/ processing/ storage/ models/ schemas/ core/ workers/` never import torch, diffusers, transformers, nvdiffrast, cumesh or trellis2 | AST scan in `test_import_boundaries.py` |
| I8 | No fake data in the app: unavailable means 503 with a reason | review; doubles only in `test/backend/fakes_*.py` |
| I9 | The 182 pre-milestone tests pass with no assertion changed (only one `os.environ.setdefault` line is added to `test/conftest.py`, S9) | full suite per slice |
| I10 | Part-aware code never imports tuner modules (`services.mesh_tuner_service`, `workers.tuning_worker`, `launcher`, `blender_scripts`) | `test_import_boundaries.py` |
| I11 | Every published part layer reassembles bit-exactly to `model.glb` (positions, UVs, normals, faces at accessor level) | `parts/reassembly.py` inside every analysis |
| I12 | A published `jobs/<job>/parts/<pa>/asset/` is immutable; a new analysis creates a new `pa_` | `parts/publish.py` refuses an existing target |

### 11.1 Packages and layering
```
backend/
  core/feature_errors.py, core/part_aware_container.py       S1   errors (11.8); PartAwareContainer + build_part_aware()
  api/part_routes.py, api/part_deps.py, api/v1/{features,model_registry,image_generations,segmentations,
      part_analyses,workflows}.py                             S1 empty → S2/S4/S5/S10/S11
  schemas/{runs,features,model_registry,image_generation,segmentation,parts,workflows}.py   S1 (frozen wire models)
  models/{runs,image_generation,segmentation,part_analysis,workflow}.py                     S1 (frozen domain records)
  storage/{run_store,run_layout,workflow_store}.py            S1 (workflow_store: frozen-API stub, S11 implements)
  pipelines/runs/{executor,reporter,context}.py               S1   run framework
  pipelines/{image_generation,prompt_templates,segmentation,part_analysis}.py              S4 / S5 / S10
  processing/{conditioning,imagegen_readiness}.py, processing/seg2d/{fusion,eyes}.py       S4 / S5 (numpy/opencv)
  parts/                                                      S1 types/taxonomy/manifest; S3a geometry; S3b asset layer
  inference/providers/{base,catalog,registry,unavailable,vram,vendor_import}.py            S1 / S2 / S6 (torch-free)
  inference/{imagegen,segmentation,parts}/base.py             S1   torch-free role ABCs
  inference/imagegen/zimage.py · inference/segmentation/{gdino,sam2,florence2,see_through,sapiens2}.py ·
  inference/parts/{raster_lifter,cameras}.py                  S4 / S5 / S6 / S7 (the only torch/diffusers/transformers importers)
  services/{feature,model_registry,image_generation,segmentation,part_analysis,workflow}_service.py,
  services/{part_files,run_events,workflow_catalog,workflow_compiler,run_retention_service}.py
  workers/run_worker.py                                       S1   RunWorker
```
Layering: `api → services → {pipelines, parts, processing, storage, models, schemas, core}`. Services may also import
the torch-free `inference/*/base.py`, `inference/providers/{base,catalog,registry,unavailable,vram}.py` and
`inference/device.py`. Only `inference/imagegen/zimage.py`, `inference/segmentation/{gdino,sam2,florence2,
see_through,sapiens2}.py`, `inference/parts/{raster_lifter,cameras}.py` and the existing `inference/trellis/**` import
torch, diffusers, transformers, nvdiffrast, cumesh or vendored model code; the registry imports them lazily
(`importlib.import_module` inside a function, after `configure_hf_environment(settings.hf_home)`), and any import
`Exception` becomes an `UnavailableProvider` with `reason_code="error"`. `inference/providers/vram.py` may
`import torch` inside function bodies only. Vendored code (See-through, Sapiens2) is imported only through
`inference/providers/vendor_import.py` (private `img3d_vendor.<name>.*` namespace; `sys.path` is never changed).
`parts/` is a torch-free library (numpy, scipy, Pillow, json, struct) and never imports `services`, `api` or
`workers`. Protected files and tuner modules are only read (public helpers such as `classify_exception`,
`validate_against_capabilities`, `compute_glb_stats`, `write_json_atomic`, `normalize_to_png`,
`make_thumbnail_webp`, `configure_hf_environment`).

Seams into working code (S1, `DESIGN.md` §1.2): `GenerationWorker.submit_gpu_task(fn, *args) -> asyncio.Future` and
`accepting_gpu_tasks` (raises `EngineUnavailableError("The server is shutting down")` while stopping);
`ServiceContainer.part_aware`; `create_app(..., part_providers=None)` (tests inject doubles); five new `EventType`
members; `_JOB_SCOPED_EVENTS += part_analysis.updated`; `JobService.add_delete_hook(part_analysis_service.on_job_deleting)`.

### 11.2 Part taxonomy (`parts/taxonomy.py`, `taxonomy_version = 1`, frozen in S1)
**Kinds.** `base` = the continuous remainder (body or object) that owns every face not in a physical part.
`physical` = a set of `model.glb` faces moved into its own mesh node: an open surface patch (outer + inner sheet) that
keeps the original UVs and the one shared atlas, with seam loops; the opening it leaves in the host is recorded and
never filled; hidden geometry is recorded as missing. `physical_deferred` = a real part record (2D mask, 3D surface
region on its host, guides such as `lid_curve`, `geometry.status: "texture_only"`) with no mesh node; its geometry is
left to the future, approved tuning layer. `region` = a semantic label on faces of a mesh-owning part; topology is not
cut.

**Groups** (`separate` in requests; default = all seven separable groups):

| Group | Classes | Kind when selected / not selected | Note (served by `/features`) |
|---|---|---|---|
| `eyes` | `eye.left`, `eye.right` | physical / region | "Eye area is cut as an open surface; the hidden eyeball is not generated." |
| `eyebrows` | `eyebrow.left`, `eyebrow.right` | physical_deferred / region | "Eyebrows are painted on; geometry deferred (mask + surface region now)." |
| `eyelashes` | `eyelash.left`, `eyelash.right` | physical_deferred / region | "Eyelash geometry deferred; mask + lid curve only." |
| `hair` | `hair`, `hair.facial` | physical / region | "The scalp under the hair is not generated (opening recorded)." |
| `clothes` | `clothing.top/.outer/.bottom/.dress/.legwear/.handwear/.footwear/.neckwear` | physical / region | "The body under clothes is not generated (opening recorded)." |
| `accessories` | `accessory.headwear/.eyewear/.earwear/.jewelry/.belt/.bag`, `appendage.tail/.wings/.animal_ears` | physical / region | – |
| `detachable` | `object.held`, `object.detached` | physical / region | – |
| `face` (region group) | `body.head.face`, `body.head.ear.*`, `body.head.nose`, `body.head.mouth`, `body.head.eyelid.*` | always region | – |
| `body` (region group) | `body` (base), `body.head`, `body.neck`, `body.torso`, `body.arm.*`, `body.hand.*`, `body.leg.*`, `body.foot.*`, `body.skin_other` | always region | – |
| `other` | `unknown` | region | unassigned foreground |

**Class values** (stable; written to `face_labels.u16`, `_FEATURE_ID_0` and 2D `labels.png`; a value is never reused):
0 `unknown` · 1 `body` · 2 `body.head` · 3 `body.head.face` · 4/5 `body.head.ear.left/.right` · 6 `body.head.nose` ·
7 `body.head.mouth` · 8 `body.neck` · 9 `body.torso` · 10/11 `body.arm.left/.right` · 12/13 `body.hand.left/.right` ·
14/15 `body.leg.left/.right` · 16/17 `body.foot.left/.right` · 18 `body.skin_other` · 20/21 `eye.left/.right` ·
22/23 `eyebrow.left/.right` · 24/25 `eyelash.left/.right` · 26/27 `body.head.eyelid.left/.right` · 30 `hair` ·
31 `hair.facial` · 40-47 `clothing.top, .outer, .bottom, .dress, .legwear, .handwear, .footwear, .neckwear` ·
50-55 `accessory.headwear, .eyewear, .earwear, .jewelry, .belt, .bag` · 56-58 `appendage.tail, .wings,
.animal_ears` · 60 `object.held` · 61 `object.detached`. Each class is a `PartClass(value, id, group, label,
default_kind, attach_to, symmetric_with, sided, instance_split, aliases, z_priority)`; the full table (attach-to
candidates, symmetry, aliases, z-priorities) is `DESIGN.md` §2.3. `instance_split` is true for `accessory.*`,
`appendage.*`, `object.*`, `clothing.handwear`, `clothing.footwear` (one physical instance per welded component of at
least the effective `min_part_faces`). API: `TAXONOMY`, `BY_VALUE`, `classes_for_groups(groups)`,
`match_terms(text)` (whole-word, case-insensitive alias match), `to_out()`.

**Part ids.** `^[a-z][a-z0-9_]*(\.[a-z0-9_]+){0,5}$`, at most 80 characters, unique within a manifest, used directly
as file stems. First instance = the class id; further instances append `_2`, `_3`, … to the last segment
(`accessory.jewelry_2`); geometry-only shells are `component_001`, … The first segment is never a Windows device name
(`con, prn, aux, nul, com1-9, lpt1-9`).

**Policy** (`parts/policy.py`, S3b): `decide_kind(cls, separate, faces, min_part_faces)` → `body` is `base`; groups
`body`/`face`/`other` are `region`; an unselected group is `region`; `physical_deferred` classes stay deferred; a
physical class below the effective `min_part_faces` becomes `physical_deferred` with warning
`too_small_for_geometry` (never dropped); otherwise `physical`. Then: the segmenter only labels, the policy decides
kinds; separate shells (`split_shells=true`) under 25 % of the faces become physical parts when their majority label
is a selected group or unknown (unknown → `component_###`, no seam), stay on the base when body/face-labelled, and
unknown shells below `floater_max_faces = max(min_part_faces, round(0.0005·F))` get no record (listed in
`parts[body].geometry.floaters`, warning `floater_candidates`, never deleted); at most `max_parts` (default 64)
physical parts (overflow merges into the base as `region` records keeping their class value, warning
`max_parts_exceeded`); eyes are cut as the lifted `eye.*` faces only (the eyelid ring 26/27 stays a base region);
openings are recorded, never filled.

### 11.3 Part asset and manifest `img3d.parts/1` (`parts/manifest.py`, pydantic, frozen in S1)
**Asset rules.** The base mesh is `jobs/<job>/model.glb` exactly as exported (1 mesh, 1 triangle primitive,
1 material, glTF Y-up, unit AABB) and is never modified. Per-face buffers index the base faces in index-accessor order;
the welded vertex space groups bit-identical positions (`weld_map`). A1 every face has an owner and node face counts
sum to `F`; A2 `face_labels` (semantics) is independent of `face_owner` (ownership); A3 node k = `flatnonzero(
face_owner == k)` in ascending base order with vertices `vmap_<id>`; A4 lossless reassembly (`np.array_equal` of
POSITION/NORMAL/TEXCOORD_0 gathered by `vmap`, and faces row for row); A5 one atlas (material 0 everywhere, image
bytes copied verbatim); A6 every coordinate is in the `asset` frame unless named otherwise; A7 honest provenance
(unlabelled foreground is `unknown`; nothing invented or deleted); A8 `refinement.status="untouched"` and
`locked=true` while `part_refinement_enabled=false`; A9 readers ignore unknown fields, `schema` names the major
version (a breaking change needs `img3d.parts/2` plus a reader-side migration).

**Directory** `jobs/<job>/parts/<pa>/`: `run.json` (from queue time), `log.txt` (failed runs only), `asset/`
(published by one `os.replace` from `runtime/temp/parts/<pa>/out/`, immutable):
```
asset/manifest.json        img3d.parts/1 (every path relative to asset/, except source.model_file = "model.glb"
                           relative to the job directory)
asset/model_parts.glb      assembled multi-node GLB (always written)
asset/buffers/*.u8|u16|u32 raw little-endian, no header
asset/parts/<id>.glb       only with export.per_part_glbs (otherwise built on demand into runtime/cache/part_exports/)
asset/masks/<value>.png    asset/thumbs/<id>.webp
asset/segmentation/source.png labels.png overlay.png frame.json segmentation.json
asset/previews/{front,back,left,right}.png
```

**Buffers** (`manifest.buffers[] = {id, file, dtype, components, count, byte_length, sha256, indexes, description}`;
read with `np.fromfile` after checking `byte_length` and `sha256`):

| id / file | dtype | count | meaning |
|---|---|---|---|
| `face_labels` / `face_labels.u16` | uint16 | F | taxonomy value of the finest class (0 = unknown) |
| `face_owner` / `face_owner.u16` | uint16 | F | mesh index (0 = base) |
| `face_confidence` / `face_confidence.u8` | uint8 | F | label confidence × 255 |
| `face_component` / `face_component.u32` | uint32 | F | welded connected-component id (0 = largest by area) |
| `face_part` / `face_part.u16` | uint16 | F | index into `parts[]` of the finest record covering the face; `0xFFFF` = none |
| `face_flags` / `face_flags.u8` | uint8 | F | bit 0 partner fill, bit 1 graph propagation, bit 2 component fill; bits 3-7 reserved (0) |
| `weld_map` / `weld_map.u32` | uint32 | V | welded vertex id per base vertex |
| `vmap_<part_id>` / `vmap_<part_id>.u32` | uint32 | V_k | base vertex id per vertex of mesh node k (ascending unique ids, then region-border duplicates) |
| `seam_loops` / `seam_loops.u32` | uint32 | S | all seam loops concatenated (welded vertex ids); each loop is a `{start, count}` slice |

**Manifest top level:**

| Field | Type / content |
|---|---|
| `schema` | `"img3d.parts/1"` (pydantic field `schema_id`, alias `schema`) |
| `analysis_id`, `job_id`, `created_at` | `pa_…`, `job_…`, ISO-8601 `…Z` |
| `taxonomy_version` | `1` |
| `generator` | `{app_version, engine: "trellis2", model: "TRELLIS.2-4B", pipeline_type, seed, resolution, effective_resolution, texture_size, polygon_target}` (from `JobResultData`) |
| `source` | `{model_file: "model.glb", model_sha256, model_bytes, vertices, faces, materials, images, face_order: "gltf_indices", vertex_order: "gltf_accessor", primary_image_id, origin}`; `origin` = `{kind: "image"}` or `{kind: "prompt", image_generation_id, model_id, model_revision, license_id, seed, final_prompt_sha256}` |
| `frames` | `asset` `{convention: "gltf", up: "+Y", front: "+Z", units: "normalized", aabb}`; `trellis` `{convention: "blender", up: "+Z", front: "-Y", to_asset: [16 numbers, row-major]}`; `conditioning` `{file, width, height, source_image_id, source_size, scale, crop_box, alpha_threshold: 0.8, mode: alpha_crop\|background_removed\|untouched, method: recompute\|persisted\|template_match\|identity, verified, residual}` (`persisted` is reserved, never produced) |
| `views` | cameras used for lifting: `{id: "input"\|"view_<i>", image_frame: "conditioning", camera: {model: "perspective", yaw_deg, pitch_deg, fov_deg, distance, world_to_camera, intrinsics: {fx, fy, cx, cy, normalized: true}, convention: "opencv"}, fit: {method, iou, accepted, candidates_tested}, weight}`; `[]` for `components` |
| `strategy` | `{id, segmentation: {source: none\|internal\|reused\|persisted, segmentation_id, profile, subject}, separate[], min_part_faces (resolved number), max_parts, split_shells, views, models: [{id, revision, license_id}]}` |
| `region_attribute` | `"_FEATURE_ID_0"` |
| `label_table` | `[{value, category}]` for every value present in `face_labels` |
| `meshes` | `[{index, part_id, node_name, faces, vertices, unique_vertices, vertex_map: "vmap_<id>"}]`, index 0 = base |
| `parts` | part records (below) |
| `seams` | `[{id: "seam_<n>", a, b, host_opening, boundary_edges, length, loops: [{start, count, closed, length}]}]` (`a` = the physical part; outer and inner sheet loops share one record; a branch point ends an open chain) |
| `relations` | `[{id: "rel_<n>", type, a, b, via: seam id\|null, metrics, source, confidence}]`; `type ∈ {attached_to, region_of, adjacent_to, symmetric_with, layered_over, occludes, held_by, expected_missing}` (`expected_missing`: `a = "asset"`, `b` = a class id) |
| `buffers` | table above |
| `artifacts` | `{assembled_glb: {file, sha256, bytes, nodes}` (never null), `part_glbs: {"<part_id>": file}, segmentation: {labels, overlay, frame, source} \| null, masks_dir, previews: [file], log}` |
| `expectations` | `{source: "prompt"\|"extra_terms"\|null, expected_classes[], missing[]}` |
| `provenance` | `{steps: [{id, models: [{id, revision, license_id}], duration_s}], licences: [ids]}` |
| `refinement` | `{enabled: false, policy: "locked_until_approved", consumers: []}` |
| `reassembly` | `{method: "vmap_gather", verified, positions_exact, uvs_exact, normals_exact, faces_exact}`; all true to publish |
| `summary` | `{part_count, physical_count, deferred_count, region_count, unlabelled_face_ratio, partner_filled_ratio, cut_partner_consistency (null without cuts), camera_fit_iou}` |
| `warnings` | `[{code, message, part_id}]` (codes in 11.8) |

**Part record:** `id`; `category` (class id), `group`, `label`; `kind`; `side`/`image_side` (`left|right|center|null`,
character-relative / raw image side); `parent` (`body` for body-attached parts, null for the base, `object.detached`
and unknown shells); `attach_to` (resolved candidate); `host` (mesh node owning a region or deferred part; null for
mesh-owning parts); `symmetric_with`; `node_name` (`part:<id>` or null); `mesh_index` (int or null); `region`
`{attribute: "_FEATURE_ID_0", value, face_count}`; `files` `{glb, mask, thumbnail}` (each file or null; mask = PNG in
conditioning space); `stats` `{faces, vertices, area, bbox_min, bbox_max, centroid}`; `frame` `{pivot, axes, method:
seam_centroid|bbox_center|area_centroid}`; `placement` `{transform: identity 4×4, method: "identity", error: null}`;
`geometry` `{status: surface|open_surface|closed|texture_only|absent, open_boundary_loops, watertight,
occluded_geometry_missing, components, source: lifted|component|none, tags: [], floaters: [{component, faces,
centroid}]}` (`floaters` only filled on the base record, the key is always present); `uv` `{material: 0, atlas:
"shared", uv_bbox, charts_cross_boundary}`; `seams` (ids); `annotations` (2D guides, e.g. `lid_curve`); `confidence`
(0..1 or null); `expected` (bool); `provenance` `{sources: [{method: lifted|components|heuristic2d|taxonomy_default|
prompt|assumed_front, provider, model, score}], votes}`; `refinement` `{status: "untouched", locked: true,
lock_reason: "part_refinement_disabled", eligible, needs: [occluded_geometry|geometry|closure|low_confidence],
constraints: {seam_lock: true, keep_uv_atlas: true, keep_frame: true}, history: []}` (reserved status values
`queued, running, refined, failed, rejected` are not produced); `lineage` `{derived_from: [{part_id, analysis_id,
face_iou}]}` (≤ 3 entries with IoU ≥ 0.5 from an earlier analysis of the same `model_sha256`). An abridged full
example is `DESIGN.md` §3.11.

**`model_parts.glb`.** Scene `img3d_parts` (`extras.img3d = {schema, analysis_id, job_id, region_attribute,
manifest: "manifest.json"}`) → node `img3d_asset` (identity) → one flat `part:<id>` node per mesh node. Every
`part:<id>` node carries `extras.img3d.id` and `kind` (physical nodes also `category, group, parent, attach_to,
seams`); readers match `extras.img3d.id` first (GLTFLoader sanitizes node names). Every mesh node has
`POSITION NORMAL TEXCOORD_0 _FEATURE_ID_0` (UNSIGNED_SHORT in its own bufferView with `byteStride: 4`; three.js exposes
it as an `InterleavedBufferAttribute`, read with `getX(i)`) and `UNSIGNED_INT` indices. Materials, textures, images
and samplers are copied verbatim (no re-encode). `EXT_mesh_features` is not declared in v1. Sparse accessors and
Draco/meshopt inputs are rejected (`unsupported_base_mesh`).

**Read API** (`parts/reader.py`, `parts/consumers.py`, torch-free; the only API future consumers may use):
`AssetReader(asset_dir, *, verify="none"|"hashes"|"full")` with `manifest`, `base()`, `buffer(id)`, `part(id)` (mesh-
owning parts only), `region_faces(id)`, `seams(id)`, `relations(id=None)`, `reassemble()`.
`PartConsumerRegistry(enabled=settings.part_refinement_enabled)`: `register()` raises `FeatureDisabledError` while
disabled; `list()` is `()` (no consumer ships). **Validation before publish** (`parts/validate.py`): well-formed unique
ids; relative paths inside the staging dir; buffer dtype/count/length/sha256; region values in `label_table`;
contiguous mesh indices; consistent `face_part`; face counts sum to `source.faces`; reassembly flags true; refinement
locked; `model.glb` re-hashed immediately before publish equals `source.model_sha256`. **Publish** refuses an existing
target (`publish_conflict`), re-checks that the job exists (`job_deleted`) and renames with `utils.files.retry_os`.

### 11.4 Run resources, scheduling and state machines
#### 11.4.1 Resources
| Prefix | Resource | Produces | Queue / thread | Persistence | Slice |
|---|---|---|---|---|---|
| `job_` | image-to-3D job (**unchanged**) | `model.glb`, `preview.png` | `JobQueue` → `GenerationWorker` → `gpu-worker` | `runtime/jobs/<job>/job.json` | – |
| `ig_` | image generation (prompt → candidate PNGs) | candidates | run queue → `RunWorker`; GPU phase via `submit_gpu_task` | `runtime/imagegen/<ig>/run.json` | S4 |
| `sg_` | 2D segmentation of a reference image (before reconstruction) or a job's conditioning image | label map, masks, overlay | run queue → GPU + CPU phases | `runtime/segmentations/<sg>/run.json` | S5 |
| `pa_` | part analysis of a completed job (after reconstruction) | part layer (11.3) | run queue → CPU + GPU phases | `runtime/jobs/<job>/parts/<pa>/run.json` | S10 |
| `wf_` | workflow: server-side chain of the above plus a job | links to child resources | event loop only (no GPU) | `runtime/workflows/<wf>/workflow.json` | S11 |

`ig_`, `sg_` and `pa_` share one run queue (`run_queue_max_size`, default 8); one `RunWorker` runs one run at a
time. New resources reach the image-to-3D path only through `JobService.create_job(CreateJobRequest)` (workflows,
same validation as `POST /jobs`) and read-only access to finished jobs.

#### 11.4.2 Common run shape (`models/runs.py` / `schemas/runs.py`, frozen in S1)
```json
{ "id": "ig_0123456789ab", "kind": "image_generation",      // image_generation | segmentation | part_analysis
  "status": "running",                                       // JobStatus values: queued|running|completed|failed|cancelled
  "created_at": "…Z", "started_at": "…Z", "finished_at": null,
  "workflow_id": null,                                       // the owning wf_ when created by a workflow
  "options": { },                                            // effective (normalized) request
  "models": [{"id": "z_image_turbo", "revision": "f332…", "license_id": "apache-2.0"}],   // models actually used
  "progress": {"percent": 41.0, "step_id": "generate", "step_index": 2, "step_count": 4, "message": "…", "updated_at": "…Z"},
  "steps": [{"id": "load_model", "label": "Loading text-to-image model", "status": "done",
             "message": null, "progress": 1.0, "duration_s": 0.0, "details": {}}],
  "warnings": [{"code": "t2i_touches_border", "message": "Candidate 0: …", "step_id": "check"}],
  "result": null,                                            // kind-specific result once completed
  "error": null }                                            // {code, message, details} (JobError shape)
```
`ImageGenerationOut`, `SegmentationOut` and `PartAnalysisOut` are this shape plus their `result`, and `job_id` where
it applies (part analyses; segmentations of a job source). Step statuses: `pending | running | done | warning |
skipped | failed | cancelled`. Progress units: `progress.percent` 0..100; `steps[].progress` **0..1** within the step;
`WorkflowOut.steps[].progress` **0..100**.

#### 11.4.3 State machine and percent (every run kind)
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
`percent = 100 · (Σ w(done|warning|skipped) + w(current) · progress(current)) / Σ w(all)`, clamped to 0..100,
monotonic, exactly 100 on `completed`. Progress messages are throttled to ≤ 5/s per run (droppable), `run.json` is
persisted at most every 2 s, status and step transitions are always published.

#### 11.4.4 Scheduling rules (`workers/run_worker.py`, `inference/providers/{registry,vram}.py`)
- **One GPU thread.** A GPU phase is `await gpu.submit_gpu_task(...)` on the existing `gpu-worker` executor (FIFO with
  engine load, generation and unload); CPU phases run on `ThreadPoolExecutor(1, "parts-cpu")`. Between phases the GPU
  is free, so a queued job can run in between.
- **Jobs-first gate.** Before submitting a GPU phase the worker waits (polling 0.5 s, cancellable) until
  `job_queue.pending == 0 and gpu.running_job_id is None` (message "Waiting for the GPU (generation queued)"); after
  `run_max_defer_s` (600 s) it submits anyway with warning `gpu_defer_timeout`.
- **Every auxiliary GPU callable** runs inside `torch_globals_guard()` (snapshots and restores torch default dtype,
  cudnn/TF32/matmul precision and reduced-precision flags, thread count, SDP flags, deterministic algorithms, grad
  mode; logs `[torch_guard] restored <names>` when it restored any), so same-seed TRELLIS reconstructions are
  unchanged. Providers seed only private `torch.Generator`s.
- **VRAM.** Static check at creation (503 `capability_unavailable`, `reason_code: insufficient_vram`):
  `spec.vram_peak_gb > aux_vram_budget_gb − (engine_resident_vram_gb if low_vram is false and the engine is ready
  else 0)`. Live check at the start of every GPU phase: `free_mb < (vram_peak_gb + aux_vram_margin_gb)·1024` fails
  the run with `insufficient_vram`. Weights stay in CPU RAM between runs (`aux_keep_models_in_ram`) and move to CUDA
  only inside a GPU phase; the phase's `finally` offloads and empties the cache.
- **Errors.** `JobCancelledError` → `cancelled`; `AppError` → `failed` with its code; any other exception →
  `classify_exception` (`out_of_memory`, else `run_error`); the `pa_` watchdog counts active phase time only →
  `run_timeout`. Timeout, cancel and stop never race the GPU callable: the token is cancelled, then the shielded GPU
  future is awaited before the final status is set and `work_dir` is removed. A failed `pa_` keeps
  `jobs/<job>/parts/<pa>/log.txt`.
- **Job folders.** `RunStore` saves `pa_` records with `mkdir(parents=False)` below an existing job folder; a vanished
  job raises `job_deleted` and is never recreated.

#### 11.4.5 `ig_` image generation (S4)
Steps: `load_model` "Loading text-to-image model" (15, **cpu**: weights fully read into CPU RAM, skipped when already
resident) → `generate` "Generating images" (70, gpu: components moved to CUDA one at a time; candidates generated one
at a time with batch size 1 and seed `(seed + i) mod 2^31`; cancel interrupts the pipeline) → `check` "Checking 3D
readiness" (10, cpu) → `save` "Saving candidates" (5, cpu: PNG + `_thumb.webp`, max side 384). Prompt templates:
presets `character` (896×1152), `object` and `raw` (1024×1024), styles `realistic|stylized|anime`, optional part-hint
clause; exact texts in `DESIGN.md` §4.4.3. Prompt hygiene: trimmed, control characters other than `\n`/`\t` → 422
`invalid_characters`; logs record only the length and the first 12 hex digits of the final prompt's sha256;
`run.json` stores `prompt` and `effective_prompt`; provenance stores `final_prompt_sha256`. Readiness issues (warnings,
never failures; same strings in `readiness.issues` and run warnings): `t2i_touches_border`, `t2i_multiple_subjects`,
`t2i_coverage_low`, `t2i_coverage_high`, `t2i_aspect_mismatch`.

#### 11.4.6 `sg_` segmentation (S5)
The source is snapshotted at creation into `runtime/segmentations/<sg>/source.png` (reference images through
`normalize_to_png`, with `source_pixels_sha256` = sha256 of the decoded RGBA pixels; job sources use the job's
`preview.png` plus `inputs/<primary>.png` as `original.png`). Steps: `prepare` (5, cpu) → `frame` (5, cpu:
conditioning frame `recompute` / `template_match` / `identity`; unverified → warning `frame_unverified`) → `parse` (25,
gpu; skipped without a parser) → `detect` (25, gpu: Grounding DINO + Florence-2 on the full image and the head crop) →
`refine` (30, gpu: SAM 2.1 per box) → `fuse` (10, cpu: z-priority fusion, eyebrow/eyelash/eyelid heuristics).
Profiles: `baseline` (GDINO + Florence-2 + SAM 2.1), `anime` (+ See-through parser), `realistic` (+ Sapiens2 parser);
`profile: auto` maps `subject` to a profile; `subject: auto` uses Florence-2 caption keywords. A missing role is
skipped with warning `profile_degraded`; creation fails with 503 `capability_unavailable` only when no
detector+refiner pair and no parser is usable. The head pass crops the original at full resolution (warning
`head_pass_low_res` when the frame is unverified).

#### 11.4.7 `pa_` part analysis (S10; library S3a/S3b, lifter S7)
| Strategy | Needs | Output |
|---|---|---|
| `components` | nothing (CPU, scipy) | base + `component_###` shells (inner sheets merged); default when segmentation or the lifter is unusable |
| `semantic_regions` | segmentation + lifter | labels on every face, no cuts (one node) |
| `semantic_parts` | segmentation + lifter | labels, physical cuts for the selected groups, regions for the rest; default when usable |
| `per_part_regeneration` | – | reserved: `available: false, code: "not_implemented"` |

Steps (weight, device): `prepare` "Preparing inputs" (4, cpu) → `segmentation_2d` "Segmenting image" (12, gpu) →
`camera_fit` "Aligning camera" (8, gpu) → `label_lift` "Lifting labels to 3D" (22, gpu) → `assign_labels` "Assigning
labels" (8, cpu) → `partition` "Separating parts" (16, cpu) → `export` "Exporting parts" (22, cpu) → `previews`
"Rendering previews" (8, gpu; skipped when the lifter is unusable or `export.previews=false`). `components` skips
`segmentation_2d`, `camera_fit`, `label_lift`, `assign_labels`. Labels lifted (decision order): `reused` = a given
`segmentation_id` whose result is in conditioning space with an accepted frame (a job-source `sg_` of the same job,
or a verified `recompute` frame whose `source_pixels_sha256` equals the job's primary input); otherwise `internal` = a
pass on the job's `preview.png` (exactly the conditioning image); a non-qualifying `segmentation_id` gives warning
`segmentation_rerun_for_alignment`. Camera fit accepts `iou ≥ 0.6`, else front view, warning `camera_fit_low_iou`,
confidences × 0.5 and `result.status = "partial"`. The reassembly check runs on the written files; a mismatch fails
with `partition_failed`. `finalize` validates and publishes atomically (11.3).

#### 11.4.8 `wf_` workflows (S11)
Steps (`WORKFLOW_STEPS` of `services/workflow_service.py`):

| step id | node | weight | child | created when |
|---|---|---|---|---|
| `text_to_image` | `prompt` | 6 | `ig_` | immediately (prompt source) |
| `import_image` | `input_image` | 1 | reference set + image | `ig_` completed → candidate 0 imported (`auto_first`); image source: `done` at creation; job source: `skipped` |
| `segmentation` | `segmentation` | 4 | `sg_` | after `import_image`, if `segmentation` is non-null (implies `parts` non-null) |
| `reconstruction` | `image_to_3d` | 70 | `job_` via `JobService.create_job` | after segmentation is done or skipped; job source: `skipped`, resource = that job |
| `part_extraction` | `part_extraction` | 17 | `pa_` with `segmentation_id = outputs.segmentation_id` | job completed, if `parts` is non-null |
| `export` | `export` | 2 | none (records URLs) | after the last producing step |

```
queued ─► running (first child created / first step done) ─► completed (all steps done|skipped)
running ─► failed     (child failed or child creation failed; error.code = "step_failed",
                       details = {step, child_kind, child_id, child_error})   outputs already produced stay set
queued|running ─► cancelled   (POST cancel: cancel the active child via its service; pending steps → skipped)
startup: queued|running ─► failed (interrupted); children are recovered by their own stores
```
v1 is linear and fail-fast. Creation is all-or-nothing (source exists, `validate_against_capabilities` on
`reconstruction.settings`, capability checks, run and job queue capacity → 429 `queue_full`). A later child-creation
429 fails the step with `queue_full`. `advance(wf_id)` is idempotent (per-workflow lock; triggered by child events,
a sweep every `workflow_sweep_interval_s` and once at startup). Workflow percent =
`Σ(step weight × child percent) / Σ weights of the present steps` (100 for done/skipped steps). Deleting a workflow
removes only its record (409 while active); children stay as history.

#### 11.4.9 Failure semantics
`ig_` failure or cancel creates no reference image; `sg_` failure fails a workflow at `segmentation`; a failed or
cancelled job keeps today's semantics and `pa_` creation is refused (409); a failed `pa_` publishes nothing and leaves
the job untouched (`outputs.model_url` stays set in a workflow); degraded segmentation inside `pa_` gives
`result.status = "partial"` with warnings; deleting a job runs the delete hook (cancels its analyses, waits ≤ 30 s,
purges their export cache); retention deleting a job mid-run gives `job_deleted`; shutdown cancels the active run,
awaits its GPU callable (≤ timeout) and marks queued runs and active workflows `interrupted` before the generation
worker stops; an OOM fails only that run.

### 11.5 Providers and model registry (`inference/providers/*`; types S1, registry and catalog S2)
Enumerations (torch-free, `inference/providers/base.py`):
- `ModelKind`: `text_to_image | segmentation | part_extraction | segmentation_3d | completion | part_generation` (the
  last three are future seams, `unsupported` now).
- `CommercialUse`: `allowed | allowed_with_conditions | revenue_capped | non_commercial | research_only | unknown`.
- `ModelUiState`: `not_installed | installed | loading | loaded | error | unavailable`.
- `ReasonCode` (one vocabulary for every `code` in `/features` and `ModelOut.reason_code`): `ok | unsupported |
  disabled | not_approved | license_blocked | vendored_source_missing | missing_dependency | not_installed |
  incompatible | insufficient_vram | error | not_implemented | engine_unavailable`.

Role seams (only files and plain dataclasses cross them, never tensors): `TextToImageProvider.generate`,
`Detector2D.detect`, `Grounder2D.caption/ground`, `MaskRefiner.refine`, `Parser2D.parse` (+ `label_map`),
`LabelLifter.upload/release/fit_camera/raster/render_textured/render_labels`; future `MeshSegmenter3D`,
`AmodalCompleter`, `PartGenerator` have no implementation. `ModelProvider`: `status()` (cheap, never blocks on the
GPU), `load(cancel)` (CPU thread, weights fully in RAM, never CUDA), `warm(cancel)`, `offload()`, `unload()`; one
`threading.Lock` per provider.

**State resolution** (first match wins; cached 5 s, invalidated on load/unload/error):
1. `spec.provider is None` → `unavailable / unsupported`.
2. Master flag or the flag of this kind off → `unavailable / disabled`.
3. `approval_required` and not in the effective approved set (`approved_models`, else `default_approved=True` ids) →
   `unavailable / not_approved`.
4. Licence `non_commercial | research_only | unknown`, `approval_required`, `allow_restricted_licenses=false` →
   `unavailable / license_blocked` (the lifter has `approval_required=False` and is exempt).
5. Vendored source missing → `unavailable / vendored_source_missing`; vendored code importing its own packages
   dynamically → `unavailable / incompatible`.
6. A `DependencySpec` missing or failing its specifier (metadata only, nothing imported) →
   `unavailable / missing_dependency`.
7. `required_files` missing under `model_paths[id]` or `aux_model_root/local_subdir` → `not_installed / not_installed`.
8. A text-encoder `config.json` with `transformers_version ≥ 5` → `unavailable / incompatible`.
9. Static VRAM check fails → `unavailable / insufficient_vram`.
10. Live provider state `loading | loaded | error` → that state; otherwise `installed / ok`.
`usable(id) = state ∈ {installed, loading, loaded}`.

**Catalog v1** (`inference/providers/catalog.py`; the six `DECISIONS.md` models are `default_approved=True`):

| id | kind / role | repo @ revision | local_subdir (under `backend/.model/`) | VRAM peak GB | licence | provider key | expected state |
|---|---|---|---|---|---|---|---|
| `z_image_turbo` | text_to_image / generator | `Tongyi-MAI/Z-Image-Turbo` @ `f332072aa78be7aecdf3ee76d5c247082da564a6` | `t2i/Z-Image-Turbo` | 24.0 | Apache-2.0 | `zimage` | installed |
| `grounding_dino_base` | segmentation / detector | `IDEA-Research/grounding-dino-base` @ `12bdfa3120f3e7ec7b434d90674b3396eccf88eb` | `seg/grounding-dino-base` | 2.0 | Apache-2.0 | `hf_grounding_dino` | installed |
| `sam2_1_hiera_large` | segmentation / refiner | `facebook/sam2.1-hiera-large` @ `665f8e2ad61cf5f53d65644ff27c8ee525124610` | `seg/sam2.1-hiera-large` | 2.0 | Apache-2.0 | `hf_sam2` | installed |
| `florence2_large` | segmentation / grounder | `florence-community/Florence-2-large` @ `4271c66b88cdbc05735372ec13b2360108de5317` | `seg/Florence-2-large` | 3.0 | MIT | `hf_florence2` | installed |
| `see_through_l2d` | segmentation / parser (anime) | `24yearsold/l2d_sam_iter2` @ `0b0608310fb7a89e32ecd7397147a249540cf1e7` | `seg/l2d_sam_iter2` | 4.0 | Apache-2.0 | `vendored_see_through` | `vendored_source_missing` until S6 |
| `sapiens2_seg_0_4b` | segmentation / parser (realistic) | `facebook/sapiens2-seg-0.4b` @ `449b3c5335e6722bb94990abdd1aa6e612432f22` | `seg/sapiens2-seg-0.4b` | 3.0 | Sapiens2 License (`allowed_with_conditions`: no biometric identification, no deepfakes or impersonation, no military use) | `vendored_sapiens2` | `vendored_source_missing` until S6 |
| `raster_lifter` | part_extraction / lifter | – | – | 4.0 | NVIDIA Source Code License (non-commercial; exempt, already required by export) | `nvdiffrast_raster` | installed |
| `p3sam`, `partfield` | segmentation_3d | – | – | – | – | `None` | `unsupported` (hidden) |
| `holopart` | completion | – | – | – | – | `None` | `unsupported` (hidden) |
| `x_part` | part_generation | – | – | – | – | `None` | `unsupported` (hidden) |

Segmentation revisions are read from `backend/.model/seg/download_manifest.json` at registry start (a mismatch is a
startup warning). `/models` never returns absolute paths (`local_path` is relative to `backend/`). Providers use
`from_pretrained(local_dir, local_files_only=True)` only: no `trust_remote_code`, no `custom_pipeline`, no network.
`POST /models/{id}/load` runs `load()` on a CPU thread, then queues `warm()` on the GPU thread behind the jobs-first
gate; the state is `loading` meanwhile and `features.updated` fires on every state change. Idle unload is off by
default (`aux_model_idle_unload_s=0`).

### 11.6 REST API additions (prefix `/api/v1`; all additive)
#### 11.6.0 Conventions
- **Auth.** **J** = `AuthGuard` (Bearer header only; `?token=` is rejected with 401). **F** = `FileAuthGuard` (header
  or `?token=`), used for every file route.
- **Ids.** Checked with `utils.ids.is_valid_id(value, prefix)` (`ig`, `sg`, `pa`, `wf`) before any filesystem access.
  File names come from manifest whitelists or anchored regexes applied before any path join; a resolved path must stay
  inside its run directory. Traversal attempts give 404 or 422 and never read outside the run directory.
- **Errors.** The existing envelope `{error: {code, message, details, request_id}}` (section 4).
- **Flags.** A mutating route whose flag is off returns 503 `feature_disabled`. GET routes always work (history stays
  readable).
- **Shapes.** Requests are `RequestModel` (`extra="forbid"`, unknown fields → 422 `extra_forbidden`); nested option
  objects accept partial objects (omitted fields take their defaults; `{}` is valid everywhere an options object is
  accepted). Responses are `ApiModel` (`extra="ignore"`). Timestamps are ISO-8601 UTC `…Z`.
- **Lists.** `GET /image-generations`, `/segmentations`, `/workflows` take `?limit=1..100` (default 20), newest
  first; out of range → 422. Responses are `{items: [...]}`.
- **Route order.** Static routes (`/workflows/catalog`, `/workflows/validate`, `/jobs/{id}/part-analyses/latest`) are
  registered before their `{id}` siblings.
- **Frontend rules.** Clients never build part, mask or GLB paths; they use the URLs from payloads. `GET /features`
  answering 404 means a legacy backend (image-to-3D only).

#### 11.6.1 Discovery (S2)
| Method | Path | Auth | Body / query | Success | Errors |
|---|---|---|---|---|---|
| GET | `/features` | J | – | 200 `FeaturesOut` | 401 |
| GET | `/models` | J | `?kind=` (a `ModelKind`), `?include_hidden=false` | 200 `{items: ModelOut[]}` | 401; 422 bad kind |
| GET | `/models/{model_id}` | J | – | 200 `ModelOut` | 401; 404 `not_found` |
| POST | `/models/{model_id}/load` | J | – | 202 `ModelOut` (state `loading`) | 404; 409 `conflict` (already loading, or in use by the running run); 503 `model_unavailable` / `feature_disabled` / `engine_unavailable` (shutting down) |
| POST | `/models/{model_id}/unload` | J | – | 202 `ModelOut` | 404; 409 (in use by the running run); 503 `engine_unavailable` (shutting down) |

`FeaturesOut` (also the payload of `features.updated`; `nodes` abridged, `part_completion` and `part_generation`
follow the `segmentation_3d` row):
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
  "models": [ /* ModelOut[] (full shape below), hidden_by_default entries excluded; each equals GET /models/{id} */ ],
  "taxonomy": {"version": 1, "groups": ["body", "face", "eyes", "eyebrows", "eyelashes", "hair", "clothes", "accessories", "detachable", "other"],
               "classes": [{"value": 30, "id": "hair", "group": "hair", "label": "Hair", "default_kind": "physical",
                            "attach_to": ["body.head"], "symmetric_with": null, "sided": false}]},
  "queues": {"runs": {"pending": 0, "running_id": null, "running_kind": null, "max_size": 8}},
  "limits": {"prompt_max_chars": 2000, "negative_prompt_max_chars": 1000, "extra_terms_max": 16, "views_max": 24,
             "workflow_graph_max_bytes": 65536, "max_parts": 256} }
```
Derivation (`services/feature_service.py`, torch-free): every capability entry (pipeline, profile, strategy, node)
has `available`, `code` (a `ReasonCode`) and `reason` (null when available). `image_to_3d.available` = engine state
∉ {error, unavailable} (else `engine_unavailable`). `prompt_to_3d` needs `flags.image_generation`, a usable T2I model,
the static VRAM check and `image_to_3d` (not `flags.workflows`; the one-shot flow also needs `workflow.available`).
`segmentation.available` needs the flag and one usable profile (`baseline` = usable detector + refiner; `anime` /
`realistic` = their parser plus the baseline detector and refiner). `part_extraction.available` needs
`flags.part_analysis`; `components` is always available then; the semantic strategies also need segmentation and a
usable `raster_lifter`. `mesh_tuning.enabled = blender_enabled and mesh_tuning_enabled`;
`part_refinement.enabled = part_refinement_enabled`; `flags.persist_generation_intermediates` is always false.
`features.updated` is debounced by 250 ms and fires after any provider state change (including
`default_model_state` `installed → loading → loaded`), any engine state change that matters to a flag, a run-queue
change or a catalog ETag change. Clients fetch `/features` after every `hello` and on `features.updated`.

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

#### 11.6.2 Image generations (S4)
| Method | Path | Auth | Body / query | Success | Errors |
|---|---|---|---|---|---|
| POST | `/image-generations` | J | `ImageGenerationRequest` | 202 `ImageGenerationOut` | 422 `validation_error` (incl. `unsupported_by_model`, `invalid_characters`); 503 `feature_disabled`, `capability_unavailable` (no usable T2I or `insufficient_vram`), `model_unavailable` (explicit model not usable), `engine_unavailable` (shutting down); 429 `queue_full` |
| GET | `/image-generations` | J | `?limit=1..100 (20)&status=` | 200 `{items}`, newest first | 422 bad status |
| GET | `/image-generations/{ig_id}` | J | – | 200 `ImageGenerationOut` | 404 |
| POST | `/image-generations/{ig_id}/cancel` | J | – | 202 | 404; 409 terminal |
| DELETE | `/image-generations/{ig_id}` | J | – | 204 | 404; 409 active or referenced by an active workflow |
| GET | `/image-generations/{ig_id}/candidates/{index}` | F | `index` matches `^[0-3]$` | 200 `image/png`, `Cache-Control: private, max-age=3600` | 404 run, index or file; 409 not completed |
| GET | `/image-generations/{ig_id}/candidates/{index}/thumbnail` | F | as above | 200 `image/webp` | as above |
| POST | `/image-generations/{ig_id}/candidates/{index}/use` | J | `{reference_set_id?, slot="front", make_primary=true, label?}` | 201 `{image, set}` (the upload response shape) | 404 run, candidate or set; 409 not completed; 422 slot; 503 `feature_disabled` |

`ImageGenerationRequest`:
```json
{ "prompt": "a knight with a red cape and a sword",   // required; trimmed length 1..prompt_max_chars (2000); schema hard cap 8000
  "negative_prompt": null,                           // ≤1000; 422 unsupported_by_model when the model has no negative prompt
  "model_id": null,                                  // null → registry default text-to-image model
  "preset": "character",                             // "character" | "object" | "raw"
  "style": null,                                     // null | "realistic" | "stylized" | "anime"
  "part_hint": true,                                 // append the part-separation clause
  "num_images": 1,                                   // 1..4 (must be 1 inside workflows)
  "seed": 0, "randomize_seed": true,                 // 0..2147483647; candidate i uses (seed + i) mod 2^31
  "width": null, "height": null,                     // null → preset default; the pair must be in model capabilities.sizes
  "steps": null,                                     // null → model default; within the model bounds
  "guidance": null }                                 // null → model default; within the model bounds
```
Unsupported values return 422 `validation_error` with `details=[{"loc": ["body", field], "type":
"unsupported_by_model", "msg": …, "ctx": {"allowed": …}}]`. Z-Image-Turbo: sizes `[[1024,1024],[896,1152],[1152,896]]`
(character default 896×1152, object/raw 1024×1024), steps default 9 (1..50), guidance 0.0 (0..0), no negative
prompt, max 4 images.

`ImageGenerationOut.options` and `result`:
```json
{ "options": { "prompt": "…", "effective_prompt": "A full-body 3D character reference image of …", "negative_prompt": null,
               "model_id": "z_image_turbo", "preset": "character", "style": "anime", "part_hint": true, "num_images": 2,
               "seed": 987654, "width": 896, "height": 1152, "steps": 9, "guidance": 0.0,
               "expected_terms": ["object.held", "clothing.outer"], "final_prompt_sha256": "c0ffee…" },
  "result": { "candidates": [
      { "index": 0, "seed": 987654, "width": 896, "height": 1152,
        "url": "/api/v1/image-generations/ig_0123456789ab/candidates/0",
        "thumbnail_url": "/api/v1/image-generations/ig_0123456789ab/candidates/0/thumbnail",
        "readiness": {"ok": true, "issues": [], "method": "border_color"},
        "used_as": [{"reference_set_id": "rs_…", "image_id": "img_…"}] } ],
      "timings": {"load_model": 0.0, "generate": 3.6, "check": 0.2, "save": 0.3, "total": 4.3} } }
```
`…/use`: the run must be `completed` (409) and the index must exist (404). A null `reference_set_id` creates a set
named `"Prompt: " + prompt[:60]`. The image goes through the existing upload path (`ReferenceService.upload_image`,
validation and thumbnail) with `label = label or ("Prompt: " + prompt)[:200]` and `source = ImageSource(kind="t2i",
…)`; `{reference_set_id, image_id}` is appended to `candidates[index].used_as`. A legacy retry of the resulting job
re-runs image-to-3D deterministically on the generated image.

#### 11.6.3 Segmentations (S5)
| Method | Path | Auth | Body / query | Success | Errors |
|---|---|---|---|---|---|
| POST | `/segmentations` | J | `SegmentationRequest` | 202 `SegmentationOut` | 404 set, image or job; 409 job not completed; 422; 429; 503 `feature_disabled` / `capability_unavailable` |
| GET | `/segmentations` | J | `?limit=1..100 (20)&status&reference_set_id&job_id` | 200 `{items}`, newest first | 422 |
| GET | `/segmentations/{sg_id}` | J | – | 200 `SegmentationOut` | 404 |
| POST | `/segmentations/{sg_id}/cancel` | J | – | 202 | 404; 409 |
| DELETE | `/segmentations/{sg_id}` | J | – | 204 | 404; 409 active or used by an active workflow |
| GET | `/segmentations/{sg_id}/overlay` | F | – | `image/png` | 404; 409 |
| GET | `/segmentations/{sg_id}/labels` | F | – | `image/png` (16-bit) | 404; 409 |
| GET | `/segmentations/{sg_id}/masks/{value}` | F | `value` matches `^\d{1,5}$` and is in the result | `image/png` | 404; 409 |
| GET | `/segmentations/{sg_id}/source` | F | – | `image/png` (snapshot) | 404 |

`SegmentationRequest` (`options` = `SegmentationOptions`, shared with `pa_` and the workflow `segmentation` field):
```json
{ "source": {"kind": "reference_image", "reference_set_id": "rs_…", "image_id": "img_…"}   // before reconstruction
          | {"kind": "job", "job_id": "job_…"},                                          // a job's conditioning image (preview.png)
  "options": {
    "profile": "auto",              // "auto" | "baseline" | "anime" | "realistic"
    "subject": "auto",              // "auto" | "anime_character" | "realistic_human" | "object"
    "classes": null,                // null = whole taxonomy; else taxonomy ids (422 unknown_class)
    "extra_terms": [],              // ≤16 nouns, each ≤40 chars; aliases → class, else object.held
    "use_prompt_terms": true,       // add expected_terms of the ig_ run that produced the image (via ReferenceImage.source)
    "head_pass": true,              // second pass on a head crop
    "min_confidence": 0.35 } }      // 0.05..0.95
```
`SegmentationOut.result`:
```json
{ "space": "conditioning",                  // "conditioning" (job sources, exactly predicted real-alpha frames) | "source"
  "frame": {"mode": "alpha_crop", "method": "recompute", "scale": 0.5, "crop_box": [112, 40, 912, 840],
            "size": [800, 800], "verified": true, "residual": 0.7},          // or null
  "subject": "anime_character", "profile": "baseline",
  "labels": [{"value": 30, "category": "hair", "group": "hair", "area_px": 51234, "confidence": 0.86,
              "bbox": [130, 42, 870, 610], "side": null, "sources": ["grounding_dino_base", "sam2_1_hiera_large"],
              "mask_url": "/api/v1/segmentations/sg_…/masks/30"}],
  "annotations": {"eyelash.left": {"lid_curve": [[401, 300], [430, 292]], "method": "heuristic", "confidence": 0.6}},
  "expected_terms_missing": ["object.held"],
  "overlay_url": "/api/v1/segmentations/sg_…/overlay", "labels_url": "/api/v1/segmentations/sg_…/labels",
  "timings": {"parse": 0.0, "detect": 1.1, "refine": 0.6, "fuse": 0.1, "total": 2.6} }
```

#### 11.6.4 Part analyses (S10)
| Method | Path | Auth | Body / query | Success | Errors |
|---|---|---|---|---|---|
| POST | `/jobs/{job_id}/part-analyses` | J | `PartAnalysisOptions` (optional body) | 202 `PartAnalysisOut` | 404 job; 409 job not completed or `model.glb` missing; 404/409 `segmentation_id`; 422; 429; 503 `feature_disabled` / `capability_unavailable` |
| GET | `/jobs/{job_id}/part-analyses` | J | – | 200 `{items}`, newest first | 404 job |
| GET | `/jobs/{job_id}/part-analyses/latest` | J | – | 200, the newest **completed** analysis | 404 job, or 404 `not_found` with `details.reason: "none"` |
| GET | `/part-analyses/{pa_id}` | J | – | 200 `PartAnalysisOut` | 404 |
| POST | `/part-analyses/{pa_id}/cancel` | J | – | 202 | 404; 409 |
| DELETE | `/part-analyses/{pa_id}` | J | – | 204 (also purges `runtime/cache/part_exports/<pa>/`) | 404; 409 active |
| GET | `/part-analyses/{pa_id}/manifest` | F | – | 200 `application/json`, `Cache-Control: no-cache` | 404; 409 not completed |
| GET | `/part-analyses/{pa_id}/model.glb` | F | – | `model/gltf-binary`, inline | 404; 409 |
| GET | `/part-analyses/{pa_id}/export/glb` | F | – | attachment `img3d_<job>_parts_<pa>.glb` | 404; 409 |
| GET | `/part-analyses/{pa_id}/export/zip` | F | `?include_base=false` | `application/zip` attachment `img3d_<job>_parts_<pa>.zip`, built on demand into the cache (manifest, `model_parts.glb`, buffers, masks, segmentation, per-part GLBs, `model.glb` when `include_base`) | 404; 409 |
| GET | `/part-analyses/{pa_id}/parts/{part_id}/model.glb` | F | `part_id` must be in the manifest | inline GLB (pre-written, or built on demand and cached) | 404 unknown part; 409 `conflict` with `details.reason: "no_mesh"` (region or deferred part) or `"not_completed"` |
| GET | `/part-analyses/{pa_id}/parts/{part_id}/export/glb` | F | as above | attachment `img3d_<job>_<part_id>.glb` | as above |
| GET | `/part-analyses/{pa_id}/masks/{value}` | F | `^\d{1,5}$`, present in `label_table` | `image/png` | 404; 409 |
| GET | `/part-analyses/{pa_id}/thumbs/{name}` | F | `^[a-z0-9_.]{1,90}\.webp$`, a manifest `files.thumbnail` | `image/webp` | 404; 409 |
| GET | `/part-analyses/{pa_id}/segmentation/{name}` | F | `name ∈ {source.png, labels.png, overlay.png}` | `image/png` | 404; 409 |
| GET | `/part-analyses/{pa_id}/previews/{name}` | F | `^(front\|back\|left\|right)\.png$` | `image/png` | 404; 409 |
| GET | `/part-analyses/{pa_id}/buffers/{buffer_id}` | F | `buffer_id` in `manifest.buffers` | `application/octet-stream`, headers `X-Img3d-Dtype`, `X-Img3d-Count` | 404; 409 |
| GET | `/part-analyses/{pa_id}/log` | F | – | `text/plain`, tail ≤ 256 KB | 404 |

`DELETE /jobs/{job_id}` (existing) first runs the registered delete hook (cancels the job's active analyses, waits
≤ 30 s, forgets them and purges their export cache); `safe_rmtree` then removes `parts/` with the job. On-demand files
are built with `asyncio.to_thread` under a per-(pa, file) lock, written to a temp name and `os.replace`d.

`PartAnalysisOptions` (`schemas/parts.py`; also the workflow `parts` field):
```json
{ "strategy": null,                                   // null → /features default_strategy
  "segmentation_id": null,                            // reuse a completed sg_ (snapshot-copied); must be null inside workflows
  "segmentation": null,                               // SegmentationOptions for the internal pass (null → defaults)
  "separate": ["eyes","eyebrows","eyelashes","hair","clothes","accessories","detachable"],
  "views": 8,                                         // extra multi-view renders, 0..24 (0 = input view only)
  "min_part_faces": null,                             // null → max(50, round(1e-4·F)); explicit 0..100000
  "max_parts": 64,                                    // 1..256
  "split_shells": true,
  "export": {"per_part_glbs": false, "previews": true} }   // PartExportOptions (partial objects allowed)
```
`model_parts.glb` is always written: there is no `assembled_glb` option (`export: {"assembled_glb": false}` is 422
`extra_forbidden`). Errors: 422 unknown group, out-of-range value or unknown field; 404/409 unknown or not completed
`segmentation_id`; 503 `capability_unavailable` when a semantic strategy is requested without usable segmentation
or lifter.

`PartAnalysisOut.result`:
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
Nullability: `model_url`/`download_url` of regions and deferred parts are null; `assembled_url`, the top-level
`download_url`, `zip_url`, `manifest_url`, `face_labels_url` and `region_attribute` are never null; `overlay_url` and
`camera_fit_iou` are null for `components`; `cut_partner_consistency` is null when nothing was cut; `preview_urls` is
`{}` when the `previews` step was skipped.

#### 11.6.5 Workflows (S11; node catalog tables S1)
| Method | Path | Auth | Body / query | Success | Errors |
|---|---|---|---|---|---|
| GET | `/workflows/catalog` | J | `If-None-Match` | 200 `CatalogOut` + `ETag`; 304 (empty body, same `ETag`) when unchanged | 401 |
| POST | `/workflows/validate` | J | `WorkflowRequest` (simple form or `graph`) | 200 `ValidationReport {valid, issues: GraphIssue[], mode: "job"\|"workflow"\|null, request: WorkflowRequest\|null}`; never creates anything; for `mode: "job"` the client posts its own `CreateJobRequest` to `POST /jobs` | 401; 422 only for a malformed body |
| POST | `/workflows` | J | `WorkflowRequest` | 202 `WorkflowOut` | 404 source; 409 job source not completed; 422 `validation_error` (incl. `conflicting_fields`, `extra_forbidden` e.g. a top-level `export`, `unsupported_by_engine`, `unsupported_by_model`, `graph_too_large`) or 422 `invalid_workflow` (`details.issues`); 429 `queue_full`; 503 `feature_disabled` / `capability_unavailable` |
| GET | `/workflows` | J | `?limit=1..100 (20)&status&job_id&image_generation_id` | 200 `{items}`, newest first | 422 |
| GET | `/workflows/{wf_id}` | J | – | 200 `WorkflowOut` | 404 |
| GET | `/workflows/{wf_id}/graph` | J | – | 200 `{graph, layout}` | 404 (none stored) |
| POST | `/workflows/{wf_id}/cancel` | J | – | 202 | 404; 409 terminal |
| DELETE | `/workflows/{wf_id}` | J | – | 204 | 404; 409 active |

`WorkflowRequest`:
```json
{ "name": null,                                                         // ≤120 chars
  "source": {"kind": "image", "reference_set_id": "rs_…", "primary_image_id": null}
          | {"kind": "prompt", "generation": ImageGenerationRequest}    // num_images must be 1
          | {"kind": "job", "job_id": "job_…"},                         // rebuild parts of a completed job
  "segmentation": null | SegmentationOptions,                           // non-null → sg_ BEFORE reconstruction; requires parts
  "reconstruction": null | {"settings": GenerationSettings},            // the existing schema, unchanged; required for image/prompt, null for job
  "parts": null | PartAnalysisOptions,                                  // non-null → pa_ AFTER reconstruction; parts.export carries export options
  "graph": null | WorkflowGraph,                                        // Node UI: typed graph, compiled server-side
  "layout": null | {} }                                                 // opaque Node-UI layout, ≤ workflow_graph_max_bytes, never interpreted
```
Rules: no top-level `export` (422 `extra_forbidden`); `graph` together with `source`/`segmentation`/
`reconstruction`/`parts` → 422 `conflicting_fields`; `segmentation` without `parts` → 422 `conflicting_fields`
(`loc: ["body", "segmentation"]`; a preview without parts is `POST /segmentations`); a non-null
`parts.segmentation_id` → 422 `conflicting_fields`. With both `segmentation` and `parts`, the `pa_` child receives the
workflow's own `sg_` id (reused only under the 11.4.7 rule; otherwise warning `segmentation_rerun_for_alignment`). The
compiled simple form is stored as `request`.

`WorkflowGraph` (node id `^[a-z][a-z0-9_]{0,31}$`, ≤ 32 nodes, ≤ 64 edges, serialized ≤ `workflow_graph_max_bytes`;
params validated per node type with the node's pydantic model):
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
Node catalog (`services/workflow_catalog.py`; port types `image_set`, `segmentation`, `mesh`, `part_set`; every type
has `max_instances = 1` and a `params_schema` with a `properties` map):

| Node type | Category | Inputs | Outputs | Params | Compiles to | State |
|---|---|---|---|---|---|---|
| `input_image` | input | – | `images: image_set` | `{reference_set_id, primary_image_id}` | `source{kind: image}` | available |
| `prompt` | input | – | `images: image_set` | `ImageGenerationRequest` (`num_images` = 1) | `source{kind: prompt}` | `pipeline.prompt_to_3d.available` |
| `job_result` | input | – | `images`, `mesh` | `{job_id}` | `source{kind: job}` | available |
| `segmentation` | analyze | `images` (required) | `masks: segmentation` | `SegmentationOptions` | `segmentation` | `pipeline.segmentation.available` |
| `image_to_3d` | generate | `images` (required) | `mesh: mesh` | `GenerationSettings` | `reconstruction` | engine not `error`/`unavailable` |
| `part_extraction` | parts | `mesh` (required), `masks` (optional) | `parts: part_set` | `PartAnalysisOptions` minus `segmentation_id`, `export` | `parts` | `pipeline.part_extraction.available` |
| `export` | output | `asset` (mesh \| part_set, required) | – | `PartExportOptions` | `parts.export` | available |
| `part_refine`, `mesh_tune`, `eye_correction`, `rig_correction` | refine | `parts` | `parts` | none | – | **locked**: `available: false, locked: true, code: "not_approved", reason: "Paused until approved", hidden_by_default: true`; no executor |
| `segmentation_3d`, `part_completion`, `part_generation` | parts | `mesh` / `parts` | `parts` | none | – | `available: false, code: "unsupported"` (reason names the blocking dependency), `hidden_by_default: true` |

Templates: `image_to_3d`, `image_to_3d_parts`, `prompt_to_3d`, `prompt_to_3d_parts`, `rebuild_parts`.
`legacy_job_node_map` (Node UI lighting for plain `POST /jobs` jobs): `queued, waiting_for_model, conditioning,
sparse_structure, shape, texture, decoding → image_to_3d`; `preprocessing → input_image`; `exporting, finalizing,
done → export`. A future approved node is added by exactly three additive changes (catalog entry, one optional
`WorkflowRequest` field rejected with `node_locked` while its flag is off, one `WORKFLOW_STEPS` row); no field is
reserved in advance.

`GraphIssue` = `{severity: "error"|"warning", code, message, node_id, port, edge_index, details}`. Codes (all
`error` except `no_sink`): `graph_too_large`, `unknown_node_type`, `unknown_port`, `type_mismatch`,
`port_already_connected`, `missing_required_input`, `no_source`, `multiple_sources`, `cycle`, `too_many_instances`,
`node_locked`, `unsupported_topology` (`job_result` + `image_to_3d`; part extraction without a mesh source; a
`segmentation` node not feeding an active `part_extraction.masks`, `node_id` = that node), `capability_unavailable`
(details = registry reasons), `feature_disabled`, `invalid_param` (pydantic errors relocated to
`nodes[i].params.<field>`), `not_found`, `no_sink` (warning; an implicit `export` is added). `mode: "job"` means the
graph is exactly `input_image → image_to_3d → export` without parts. Simple form ≡ graph form for every valid graph.

`WorkflowOut`:
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
Step statuses: `pending | running | done | failed | skipped | cancelled`; `node_id` is null for steps implicit in a
simple-form request; `steps[].progress` is 0..100; `warning_count` (default 0) = `len(child.warnings)` of an
`ig_`/`sg_`/`pa_` child, always 0 for `import_image`, `reconstruction` and `export`. Finished steps carry
`artifacts: [{role, url}]` (candidate thumbnail, overlay, preview, front preview). For `error.code == "step_failed"`,
`error.details` = `StepFailedDetails {step, child_kind: "image_generation"|"segmentation"|"job"|"part_analysis",
child_id: str|null, child_error: {code, message, details}}` (`child_error` is never null; `child_id` is null when the
child could not be created).

`CatalogOut`:
```json
{"schema": "img3d.workflow_catalog/1", "etag": "\"sha256-3f1c…\"",
 "port_types": [{"id": "image_set", "label": "Image"}, {"id": "segmentation", "label": "Masks"},
                {"id": "mesh", "label": "Mesh"}, {"id": "part_set", "label": "Parts"}],
 "node_types": [{"type": "segmentation", "version": 1, "category": "analyze", "title": "Segmentation",
                 "inputs": [{"id": "images", "label": "Image", "accepts": ["image_set"], "required": true, "multiple": false}],
                 "outputs": [{"id": "masks", "label": "Masks", "type": "segmentation"}],
                 "params_schema": { /* SegmentationOptions.model_json_schema() */ },
                 "defaults": {"profile": "auto", "subject": "auto", "classes": null, "extra_terms": [],
                              "use_prompt_terms": true, "head_pass": true, "min_confidence": 0.35},
                 "compiles_to": "segmentation", "max_instances": 1, "available": true, "locked": false,
                 "code": "ok", "reason": null, "hidden_by_default": false}],
 "templates": [{"id": "image_to_3d", "title": "Image to 3D", "graph": { /* WorkflowGraph */ }},
               {"id": "image_to_3d_parts", "title": "Image to 3D with parts", "graph": {}},
               {"id": "prompt_to_3d", "title": "Prompt to 3D", "graph": {}},
               {"id": "prompt_to_3d_parts", "title": "Prompt to 3D with parts", "graph": {}},
               {"id": "rebuild_parts", "title": "Rebuild parts of a result", "graph": {}}],
 "legacy_job_node_map": { /* as above */ },
 "limits": {"max_nodes": 32, "max_edges": 64, "graph_max_bytes": 65536}}
```
The ETag is the sha256 of the canonical JSON (sorted keys), so it changes whenever node availability changes. The
frontend's workflow engine stays the source of truth for layout and interaction; `POST /workflows/validate` is the
authoritative server check (debounced by the UI).

#### 11.6.6 Changed existing shapes (additive only, S4)
- `ReferenceImage` responses gain `source: ImageSourceOut | null` with `ImageSourceOut = {kind: "t2i",
  image_generation_id, candidate_index, model_id, model_revision, license_id, seed}` (the prompt hash is not exposed;
  uploads return `null`). The domain `ReferenceImage.source` (`ImageSource`, adds `final_prompt_sha256`) is omitted
  from `set.json` when `None`, so the `set.json` of uploads stays key-identical.
- `ReferenceService.upload_image(..., source=None)` gains a keyword-only parameter. No other existing request or
  response shape changes; `job.json`, `JobOut`, `CreateJobRequest`, `GenerationSettings`, `/system` are untouched.

#### 11.6.7 What each UI action calls (identical in all four UI modes)
| User action | Calls | Live updates |
|---|---|---|
| Image → 3D (no parts) | **unchanged** `POST /jobs` | `job.updated` |
| Image → 3D + parts | `POST /workflows {source: image, reconstruction, parts, segmentation?}` | `workflow.updated`, `job.updated`, `part_analysis.updated` |
| Prompt → 3D (± parts), one-shot | `POST /workflows {source: prompt, reconstruction, parts?}` (needs `prompt_to_3d` and `workflow` available) | `workflow.updated`, `image_generation.updated` |
| Prompt → review image → 3D | `POST /image-generations` → `…/candidates/{n}/use` → `POST /jobs` or `POST /workflows` (image source) | `image_generation.updated`, then as above |
| Segmentation preview | `POST /segmentations {source: reference_image}` | `segmentation.updated` |
| Re-run parts on a finished job | `POST /jobs/{id}/part-analyses` (or a workflow with source `job`) | `part_analysis.updated` |
| Viewer "Parts" source | `GET /jobs/{id}/part-analyses/latest` → `result.assembled_url` | – |
| Parts list, part download | `result.parts[]` URLs | – |
| Node UI run | `POST /workflows/validate {graph}` → `mode == "job"` ? `POST /jobs` (client-built body) : `POST /workflows {graph, layout}` | `workflow.updated` or `job.updated` + `legacy_job_node_map` |
| Capability gating, licences, warm-up | `GET /features`, `GET /models`, `POST /models/{id}/load` | `features.updated` |
| History provenance | the primary `ReferenceImage.source`, or `GET /workflows?job_id=` | – |

### 11.7 WebSocket events (envelope `{type, ts, data}`; `hello` and existing types unchanged; S1 registers them)
| type | data | `Event.job_id` / scoping | Throttle |
|---|---|---|---|
| `features.updated` | `FeaturesOut` | – (always delivered) | debounced 250 ms; never droppable |
| `image_generation.updated` | `ImageGenerationOut` | – | progress ≤ 5/s, droppable; status and step changes never dropped |
| `segmentation.updated` | `SegmentationOut` | the job id for job sources (not in the scoped set, so delivered to all) | same |
| `part_analysis.updated` | `PartAnalysisOut` | the analysis's job id; **job-scoped** (`?job_id=` filter) | same |
| `workflow.updated` | `WorkflowOut` | – | progress ≤ 2/s, droppable; step transitions never dropped |

Old clients ignore unknown types (`frontend/src/api/ws.ts`, `test/e2e/e2e_generate.py`).

### 11.8 Errors, run error codes and warning codes
| class (`core/feature_errors.py`) | HTTP | code | details |
|---|---|---|---|
| `FeatureDisabledError` | 503 | `feature_disabled` | `{"feature": "mesh_tuning" \| "part_refinement" \| …, "flag": "IMG3D_…"}` |
| `CapabilityUnavailableError` | 503 | `capability_unavailable` | `{"capability": "segmentation", "reasons": [{"model_id", "state", "reason_code", "reason"}]}` |
| `ModelUnavailableError` | 503 | `model_unavailable` | `{"model_id", "state", "reason_code"}` |
| `InvalidWorkflowError` | 422 | `invalid_workflow` | `{"issues": GraphIssue[]}` |
| existing `ValidationAppError` | 422 | `validation_error` | per-field `type ∈ {unsupported_by_model, conflicting_fields, invalid_characters, unknown_class, graph_too_large}` plus pydantic types (`extra_forbidden`, `missing`, range errors) |
| existing `ConflictError` / `NotFoundError` / `QueueFullError` / `EngineUnavailableError` | 409 / 404 / 429 / 503 | unchanged | new uses add `details.reason` |

**Run error codes** (`run.error.code`): `interrupted`, `out_of_memory`, `insufficient_vram`, `run_error`,
`run_timeout`, `source_missing`, `job_deleted`, `unsupported_base_mesh`, `generation_failed`, `segmentation_failed`,
`camera_fit_failed`, `lifting_failed`, `partition_failed`, `export_failed`, `publish_conflict`. Workflows add
`step_failed` and `queue_full`.

**Warning codes** (`warnings[].code` of runs and manifests): `t2i_touches_border`, `t2i_multiple_subjects`,
`t2i_coverage_low`, `t2i_coverage_high`, `t2i_aspect_mismatch`, `profile_degraded`, `frame_unverified`,
`head_pass_low_res`, `segmentation_rerun_for_alignment`, `camera_fit_low_iou`, `too_small_for_geometry`,
`max_parts_exceeded`, `expected_part_missing`, `charts_cross_boundary`, `low_confidence`, `floater_candidates`,
`gpu_defer_timeout`. `readiness.issues` use the same `t2i_*` strings.

### 11.9 Storage, retention, migration
```
backend/runtime/
  jobs/<job>/job.json  inputs/  preview.png  model.glb                    unchanged
  jobs/<job>/tuning/<tn>/…                                               unchanged (paused tuner)
  jobs/<job>/parts/<pa>/run.json  asset/…  [log.txt]                     part analyses (removed with the job)
  imagegen/<ig>/run.json  candidates/<n>.png  candidates/<n>_thumb.webp
  segmentations/<sg>/run.json  source.png  [original.png]  frame.json  labels.png (16-bit)  overlay.png
                     masks/<value>.png  segmentation.json
  workflows/<wf>/workflow.json  graph.json  layout.json
  cache/part_exports/<pa>/<part_id>.glb  bundle[_base].zip               on-demand exports (bounded LRU)
  temp/imagegen/<ig>/  temp/segmentations/<sg>/  temp/parts/<pa>/        work dirs; purged at startup
```
Paths are functions of `RuntimePaths` in `storage/run_layout.py` (`storage/paths.py` stays protected); no path
component is named `aux`. There is no `jobs/<job>/intermediates/` (SD1 not approved). `TuningStore.load` scans only
`jobs/*/tuning/` and `JobStore.load` reads only `job.json`, so `parts/` is invisible to both.

Retention (`services/run_retention_service.py`, S8, every `retention_interval_s`): `imagegen/` and `segmentations/`
14 days / 200 entries, `workflows/` 30 days / 500 entries (terminal runs only; ids in
`WorkflowStore.active_child_ids()` are skipped); `jobs/<job>/parts/` follows the existing job retention (which deletes
jobs without hooks, hence the publish re-check and `mkdir(parents=False)`); `cache/part_exports/` drops entries whose
`pa_` is gone and keeps ≤ `parts_export_cache_max_gb` (oldest access first). `WorkflowStore` frozen API:
`list_terminal()`, `delete(wf_id) -> bool`, `active_child_ids() -> frozenset[str]`.

Migration: old `job.json` needs nothing (any completed legacy job can get a part analysis later); an old backend
ignores `parts/`, the new top-level folders and the `source` key in `set.json` (`DomainModel` has `extra="ignore"`);
manifests carry `schema` and `taxonomy_version` (readers ignore unknown fields; breaking changes need
`img3d.parts/2`); taxonomy values are stable and never reused.

### 11.10 Feature flags and settings (defaults in section 2)
- `part_aware_enabled=false`: no `RunWorker`, no workflow sweep; mutating new endpoints return 503
  `feature_disabled`; `/features` flags are false; stores are still loaded so history stays readable.
- `image_generation_enabled` gates `ig_` creation and prompt workflows; `segmentation_enabled` gates `sg_` creation
  and the semantic strategies; `part_analysis_enabled` gates `pa_` creation; `workflows_enabled` gates `wf_` creation
  and validate (with it off, only the review-first prompt flow works).
- `mesh_tuning_enabled` (default **false**): code gate in `MeshTunerService.create_run` (after the existing 404/409
  checks: 503 `feature_disabled`, `details.feature == "mesh_tuning"`); listing, reading, cancelling and deleting runs
  and their files still work. `/features.mesh_tuning.enabled = blender_enabled and mesh_tuning_enabled`.
- `part_refinement_enabled` (default **false**): `PartConsumerRegistry.register()` raises `FeatureDisabledError`;
  manifests stay locked; refine nodes stay `locked` + `hidden_by_default`; the compiler rejects them (`node_locked`).
- `approved_models` overrides the default approved set (the six `DECISIONS.md` models); `allow_restricted_licenses`
  is needed for `non_commercial`/`research_only`/`unknown` models (none in catalog v1 except the exempt lifter).
- `persist_generation_intermediates` is inert (no code reads it; reported false).

### 11.11 Tuning and refinement stay disabled
Gates: G1 the `mesh_tuning_enabled` code gate (S9); G2 `test/conftest.py` sets
`os.environ.setdefault("IMG3D_MESH_TUNING_ENABLED", "true")` so the existing tuner tests need no edits; G3 startup
Blender detection is unchanged (`blender_enabled and blender_detect_on_startup`); G4 `/features.mesh_tuning` drives
every UI mount point (all four modes hide tuner and Blender-runtime UI); G5 the user may set
`IMG3D_BLENDER_ENABLED=false` in `backend/.env` (agents never edit `.env`); G6 refinement locked (above); G7 new
packages never import tuner modules. `backend/scripts/auto_mesh_tuner.py` is unchanged and run only by the user.

### 11.12 Not in this milestone (each needs explicit approval)
D1 generation intermediates (would edit protected TRELLIS files); D2 per-part regeneration; D3 part-clean atlas /
re-bake; D4 tuning consumers (Blender repair, eye/rig correction, lash cards, amodal completion); D5 model install or
download API; D6 `EXT_mesh_features` declarations; D7 engine offload for `low_vram=false`; D8 voxel-space labelling;
D9 paused workflow step for manual candidate pick; D10 3D segmenters/completers/part generators; D11 RGBA prompt
candidates; D12 Z-Image bf16 re-save; D13 reserved `WorkflowRequest` fields for refine nodes.
