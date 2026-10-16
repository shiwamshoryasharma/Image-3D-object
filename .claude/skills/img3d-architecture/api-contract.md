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
│   ├── processing/              # image validation/normalization, mesh stats
│   ├── inference/               # engine abstraction (base.py) + trellis/ adapter
│   ├── pipelines/               # generation orchestration + reference strategies
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

IDs: `rs_<12 hex>` reference set, `img_<12 hex>` image, `job_<12 hex>` job.

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
{ "type": "ping",           "ts": "…Z", "data": {} }              // every 20 s
```
`?job_id=` restricts the job-scoped `job.updated` and `tuning.updated` events to that job; other types always pass.
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
- `main.py`: bootstrap sys.path → load settings → configure logging → build `ServiceContainer` → `create_app()`
  with lifespan (startup: paths, stores, recover jobs, event bus, engine, worker, preload, cleanup task;
  shutdown: worker stop, close websockets, engine unload) → CORS → routers → exception handlers → optional static
  frontend → `uvicorn.run(app, host, port)` with `--host/--port/--no-preload/--log-level` CLI flags.
  Windows: handle Ctrl+C cleanly; `asyncio` default loop is fine.

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
