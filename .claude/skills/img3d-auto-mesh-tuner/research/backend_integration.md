# Auto Mesh Tuner: backend integration guide for Img3D

**Bottom line:** Retention never touches `runtime/blender/`. A tuning folder under `runtime/jobs/<id>/` is deleted together with its job. Register the new parts in 4 places: `core/container.py`, `core/lifecycle.py`, `api/router.py`, and optionally `api/deps.py`. Send events with `bus.publish_threadsafe(Event(...))`. Errors are new `AppError` subclasses. Settings are `IMG3D_BLENDER_*` fields on `Settings`. Run Blender with `subprocess.Popen` in a dedicated single-thread executor, always passing `--python-exit-code 1`.

No existing project file was modified. Probe scripts:
- `E:\14-OCT-2026\Img3D\test\mesh_tuner\probe\integration_probe.py`
- `E:\14-OCT-2026\Img3D\test\mesh_tuner\probe\integration_probe2.py`
- `E:\14-OCT-2026\Img3D\test\mesh_tuner\probe\integration\inside_paths.py`

Outputs:
- `E:\14-OCT-2026\Img3D\test\outputs\mesh_tuner\integration\integration_probe.json`
- `E:\14-OCT-2026\Img3D\test\outputs\mesh_tuner\integration\integration_probe2.json`

Line numbers below are 1-based within each file.

## 0. Verified by running the probes

| Check | Result |
|---|---|
| Retention pass (`RetentionService.run_once`, `orphan_min_age_s=0`) and `purge_temp()` | A job expired by age is deleted together with its `tuning/` subfolder. A fresh job keeps `tuning/`. An orphan `jobs/job_<hex>/` with only `tuning/` and no `job.json` is deleted. `jobs/tuning_shared` (not a valid id) is kept. `runtime/blender/5.2.2/blender.exe` is kept. `runtime/cache/blender` is kept. `runtime/temp/*.zip.part` is purged at startup. |
| asyncio subprocess on Windows | `ProactorEventLoop` works (`--version` returned in 0.138 s). `SelectorEventLoop` raises `NotImplementedError`. uvicorn 0.54 `asyncio_loop_factory(use_subprocess=False)` gives `ProactorEventLoop`, because `main.py` runs without reload or workers. |
| Python inside Blender (5.2.2 runs Py 3.13.13, 4.2.17 runs Py 3.11.7) | `ignore_environment=1` and `isolated=1`. The user site is disabled. A `PYTHONPATH` set in the child env is ignored. Arguments after `--` arrive intact, including `"a b"`. `bpy.app.version_cycle == "release"`. `import_scene.gltf` and `export_scene.gltf` exist under `--factory-startup`. Cold `-b --python-expr` takes 1.5 s on 5.2 and 0.4 s on 4.2. |
| Running a script with `--python` | The script's own folder is not on `sys.path`; Blender puts `...\5.2\scripts\startup`, `\modules` and `python313.zip` first. `__name__=="__main__"`, `__file__` is absolute, and the working folder is inherited from the caller. |
| Exit codes | A raised exception with `--python-exit-code 1` gives rc=1. Without the flag it gives rc=0 (silent failure). `sys.exit(3)` gives rc=3, and Blender exits immediately (no "Blender quit" line). |
| `--version` output | First line is `Blender 5.2.2 LTS`. Further lines include `build branch: blender-v5.2-release` and `build type: Release`. Blender 4.2 prints harmless `TBBmalloc: ...` noise on stderr. |
| Real input meshes | `backend\runtime\jobs\job_*\model.glb` are completed TRELLIS outputs of 41 to 88 MB. Example: `job_04535ae38930` has 1,697,772 vertices, 1,895,691 faces and 4096 textures, and spent 281 s exporting. |

## 1. Bootstrap and import rules

- `backend\main.py` L26-37 `_bootstrap_sys_path()` removes the user site-packages and inserts `BACKEND_DIR` at `sys.path[0]`. All imports are absolute top-level, e.g. `from launcher.runtime_manager import ...`. Contract §1 (`api-contract.md` L40-42) requires an `__init__.py` in every backend package folder.
- A new `backend\launcher\` package (with `__init__.py`) is importable as-is. There is no `launcher` module in `backend\python\Lib\site-packages`, the stdlib or `third_party` (checked). The engine adds `third_party\TRELLIS.2` to `sys.path` (`inference\trellis\environment.py` L192).
- `backend\blender_scripts\` must never be imported by the backend: it needs `bpy`, and must stay valid on Py 3.13 (5.1/5.2), so no 3.14-only syntax.
  - The entry script must add its own folder to `sys.path`.
  - Put the helper modules in one uniquely named package, e.g. `blender_scripts\img3d_tuner\`. Avoid generic names that would shadow Blender modules, such as `utils` or `progress_report` (Blender ships `progress_report`).
  - pytest won't collect it (`testpaths = backend`, `python_files = test_*.py`).
- Tests reach the same `sys.path` through `test\conftest.py` L19-21 and L31-33, and through `test\pytest.ini` `pythonpath = ../backend backend`.

## 2. Settings pattern (`backend\config\settings.py`)

**How settings work**
- `Settings(BaseSettings)` L35. `model_config` L38-46 sets `env_prefix="IMG3D_"`, `env_file=backend/.env`, `extra="ignore"`, `populate_by_name=True`, `validate_default=True`, `case_sensitive=False`.
- A field `blender_path` maps to env `IMG3D_BLENDER_PATH`.
- Unprefixed names exist only via `validation_alias=AliasChoices(...)` (L60, L88-93).
  - Do not use bare `BLENDER_*` names: the Blender child inherits `os.environ`, and Blender reads its own `BLENDER_USER_CONFIG`, `BLENDER_USER_SCRIPTS`, `BLENDER_SYSTEM_*` variables.

**Patterns to copy**
- **Lists from env:** `Annotated[list[str], NoDecode]` (L53) plus a before-validator (`_parse_cors` L106-119) that accepts a JSON list or a comma-separated string.
- **Blank string means None:** `_blank_secret_is_none` L121-126.
- **Relative paths:** `_resolve_paths` L137-152 resolves against `BACKEND_DIR` (not the working folder), but only for the names in the tuple L140-147.
  - A new `Path` field must be added to that tuple.
  - An optional `Path | None` needs its own after-validator, because the loop calls `.is_absolute()` on the value.
  - Use `object.__setattr__` as the existing code does.
- **Derived values:** `paths` is a cached property (L187-191) returning `RuntimePaths(self.runtime_dir)`.
- **Startup log:** `safe_summary()` L193-211 is the non-secret dict logged at startup; add the Blender fields there.
- **Access:** `get_settings()` (cached) L214-217. Tests build `Settings(_env_file=None, **overrides)`, which still reads `os.environ`.

**Recommended block** (put it after the "serving / runtime tuning" section):

```python
blender_enabled: bool = True
blender_path: Path | None = None                     # explicit blender.exe; wins over detection
blender_search_dirs: Annotated[list[Path], NoDecode] = Field(
    default_factory=lambda: [Path(r"C:\Program Files\Blender Foundation")])
blender_min_version: str = "5.1"                     # validate "^\d+\.\d+(\.\d+)?$"
blender_install_dir: Path | None = None              # None -> <runtime_dir>/blender
blender_allow_download: bool = True                  # mirrors allow_model_downloads (L61)
blender_download_url: str | None = None
blender_download_sha256: str | None = None
blender_detect_on_startup: bool = True
blender_detect_timeout_s: float = Field(default=30.0, gt=0)
blender_tune_timeout_s: float = Field(default=1800.0, gt=0)
blender_max_queue_size: int = Field(default=4, ge=1, le=100)
```

- Make `blender_install_dir` derive from `runtime_dir`, not from a backend-relative default. `test\conftest.py` L38 points `IMG3D_RUNTIME_DIR` at `test\.runtime\...`, while a `"runtime/blender"` default would resolve to the real `backend\runtime\blender` during tests.
- Update in the same change:
  - the contract §2 table (`api-contract.md` L52-89);
  - `backend\.env.example`, which documents every `IMG3D_*` variable commented out;
  - the parse for `blender_search_dirs` (same shape as `_parse_cors`).

## 3. Storage layout, retention and temp (the critical question)

**`RuntimePaths`** (`storage\paths.py` L29-92)
- Properties `uploads`, `jobs`, `temp`, `cache`.
- Helpers `job_dir/job_metadata/job_inputs/job_preview/job_model(job_id)`.
- Constants L22-26: `job.json`, `inputs`, `preview.png`, `model.glb`.
- `ensure()` L73-76 creates only root, uploads, jobs, temp and cache.
- `purge_temp()` L78-92 deletes everything in `temp/`; it runs only at startup (`core\lifecycle.py` L24-27).
- The class is a frozen slots dataclass, so a `blender` property would mean editing this file. The alternative is computing `settings.runtime_dir / "blender"` inside `launcher`.

**Job folder** (`storage\job_store.py`)
- Layout: `runtime/jobs/<job_id>/job.json`, `inputs/<image_id>.png`, `preview.png`, `model.glb`.
- `JobStore.save(job)` L106-110 writes `job.json` atomically and stores a deep copy in an in-memory index under an RLock.
- `delete(job_id)` L112-118 removes the entry from the index, then `safe_rmtree(job_dir)`, which takes the whole folder including any `tuning/`.
- Path helpers L121-131: `model_path`, `preview_path`, `job_dir`, `inputs_dir`.
- `job.json` fields come from `models\job.py` L102-118: `id, status, engine, created_at, started_at, finished_at, reference_set_id, primary_image_id, references[], settings{}, seed, progress{}, error, result{stats, timings, seed, resolution, effective_resolution, texture_size, polygon_target, has_preview, metadata}, cancel_requested`.
- Result files are not recorded as paths in `job.json`. They sit at fixed paths, and `result.has_preview` flags the preview.
- Serving: `ResultService.model_file()` (`services\result_service.py` L40-45) gives 409 unless the job is completed and 404 if the file is missing. It returns `StoredFile(path, "model/gltf-binary", "img3d_<job_id>.glb")`, which `api\v1\results.py` L30-51 serves with `FileResponse`.

**`RetentionService`** (`services\retention_service.py`)
- Runs `run_forever` every `retention_interval_s` (default 3600) after a 5 s delay. It is started in `lifecycle.py` L49-56.
- `cleanup_jobs` L60-77 deletes **terminal** jobs whose `finished_at` (or `created_at`) is older than `job_retention_days` (14), then the oldest beyond `max_stored_jobs` (200). It uses `JobStore.delete`.
- `cleanup_reference_sets` L80-89 only touches uploads.
- `purge_orphans` L92-99 and `_purge_orphans_in` L101-123 only scan `jobs/` and `uploads/`. They delete a folder only when all of these hold:
  - its name is a valid `job_<12hex>` or `rs_<12hex>` id (L108);
  - it is not in the index;
  - it has no `job.json`/`set.json` (L111);
  - it is at least `ORPHAN_MIN_AGE_S = 3600` s old (L31, L118).

**Verdict**
- `runtime/blender/` and `runtime/cache/*` are never touched by anything.
- `runtime/jobs/<id>/tuning/...` lives and dies with its job: expiry is based on the job's `finished_at`, not the tuning date. Retention also deletes it when the job falls past the 200-job limit, and `DELETE /api/v1/jobs/{id}` deletes it too.
- `JobService.delete_job` (`services\job_service.py` L190-197) only blocks queued or running jobs. **A running tuning does not block deletion.**

**Races to guard against**
1. **Delete during a tuning run.** If Blender still has `model.glb` open, Windows can refuse the delete (the case described in the retention docstring L6-8). `safe_rmtree` then leaves a folder without `job.json`, which becomes an orphan purged after 1 h.
   - Copy the input into `runtime/temp/tuning/<tn_id>/` first.
   - Before moving outputs into place with `replace_file`, check `job_store.get(job_id) is not None`.
2. **Late write.** A write into a deleted job folder recreates `jobs/<id>/` without `job.json`. It is purged one hour later, as verified.
3. **Make DELETE return 409 during tuning.** Add a small check in `JobService.delete_job` (an existing file), or cancel the tuning when the job is deleted.

**Recommended layout**
- Outputs: `runtime/jobs/<job_id>/tuning/<tn_id>/{tuning.json, model_tuned.glb, report.json, blender.log, config.json}`.
- Working files: `runtime/temp/tuning/<tn_id>/`.
- Blender: `runtime/blender/{selection.json, blender-5.x.y-windows-x64/}` for installs, with download staging in `runtime/temp/blender-download/` (purged on restart, which is right for `.part` files). Extract to `runtime/blender/.staging-<rand>` and then `os.replace` into place.
- Alternative: `backend\.blender\`, matching the `.model` and `.toolchain` convention. This would need gitignore entries (see §11).

## 4. Container and lifecycle registration

**`core\container.py`**
- `ServiceContainer` is `@dataclass(eq=False)` (L31-50). The required fields are L33-48; `background_tasks` (L49) and `started` (L50) have defaults.
- Add new **required** fields before L49, or the dataclass raises an error (a required field cannot follow defaulted ones). Suggested fields: `blender_runtime: BlenderRuntimeManager`, `tuning_store: TuningStore`, `tuning_worker: TuningWorker`, `mesh_tuner_service: MeshTunerService`.
- `build_container(settings, *, engine=None)` L53-111 constructs everything synchronously from `create_app` (`main.py` L72).
  - Never execute Blender here: tests build an app per test.
  - Insert construction after `result_service` (L90). The tuning service needs `job_service` or `result_service` and `job_store`.
  - `ws_hub` is imported lazily (L55) to respect layering.
  - For a test fake, add an optional keyword `blender_runner: BlenderRunner | None = None`, mirroring `engine=` (and to `main.create_app` L62).

**`core\lifecycle.py` startup** (L19-58)
- After `job_store.recover_interrupted()` (L32-34): call `tuning_store.load()` and `recover_interrupted()`, marking running or queued tunings as failed with `"interrupted"`.
- After `bus.bind_loop(...)` (L36) and `worker.start()` (L38): call `container.tuning_worker.start()`.
- In the background-task tuple (L49-53), add `(container.blender_runtime.detect(), "blender-detect")` when `settings.blender_detect_on_startup`. The API must stay available immediately; these tasks are cancelled at shutdown (L66-69).

**`core\lifecycle.py` shutdown** (L61-89)
- After `worker.stop` (L71-74) and before `ws_hub.close_all()` (L76-79): `await container.tuning_worker.stop(timeout=settings.shutdown_timeout_s)` (kill Blender) and `await container.blender_runtime.aclose()` (cancel the download and delete the `.part` file).
- `main.py` L197-204 only force-exits when the **GPU** worker is busy. A still-running `blender-worker` executor thread would be joined at interpreter exit, and a `blender.exe` left alive outlives the backend. `stop()` must kill the process tree.

## 5. Events and the WebSocket envelope

- **Event type:** `core\events.py` L28-36 `Event(type: str, data: Any, ts: datetime = utcnow(), job_id: str | None = None, droppable: bool = False)`.
- **Publishing:**
  - `EventBus.publish(event)` L139-148 is loop-thread only.
  - `publish_threadsafe(event)` L150-163 works from any thread; it is a no-op before `bind_loop` or after `close`.
  - `call_soon_threadsafe(cb, *a)` L165-176 and `call_later_threadsafe(delay, cb, *a)` L178-186 schedule work on the loop.
- **Back-pressure:** subscriber queues are bounded at 256 (L25). On overflow the oldest `droppable` event is dropped first (L72-79).
- **Wire format:** `schemas\events.py` L26-28 `ws_message(event)` produces `{"type": str, "ts": "YYYY-MM-DDTHH:MM:SS.mmmZ", "data": Any}`. `EventType` StrEnum L13-17 has `hello`, `job.updated`, `system.updated`, `ping`.
  - A new type needs no edit: `ws_message` is generic. Put `TunerEventType(StrEnum)` (`BLENDER_UPDATED = "blender.updated"`, `TUNING_UPDATED = "tuning.updated"`) in a new schema module.
- **Hub** (`api\websocket_hub.py`):
  - `hello` carries only `{"system": SystemStatus}` (L137-140), so clients fetch the initial Blender and tuning state over REST. Contract §8 says REST is the source of truth.
  - **`data` must be JSON-serializable** (`model_dump(mode="json")`). An unserializable payload makes `_sender` (L204-216) drop the client.
  - `_job_filter` (L228-237) filters **only** `job.updated` by `job_id`; every other type reaches `?job_id=` clients. Per-job filtering of `tuning.updated` needs an edit there.
- **Publisher pattern to copy:** `services\job_events.py` `JobEventPublisher.publish(job, *, droppable=False)` (L25-30) dumps the API model and calls `publish_threadsafe`.
  - Status changes: `droppable=False`.
  - Progress: `droppable=True`, throttled to at most 5/s (`pipelines\progress.py` `DEFAULT_PUBLISH_INTERVAL_S=0.2`).
- **Frontend:** `frontend\src\api\ws.ts` `dispatch()` L248-264 ignores unknown types (`default: break`), so new types are backward compatible. Add cases and types in `ws.ts` and `types.ts` (`WsServerMessage` union L420).
- **Proposed messages:**
  - `{"type":"blender.updated","ts":…,"data": BlenderRuntimeStatus}`: detection result, selection change and download progress (progress droppable).
  - `{"type":"tuning.updated","ts":…,"data": TuningOut}` with `Event.job_id = tuning.job_id`.

## 6. API conventions

- **Prefix:** `API_V1_PREFIX="/api/v1"` (`schemas\common.py` L9), applied in `api\router.py` L11-23 `build_api_router()` via `include_router`. Register new routers there (e.g. `from api.v1 import ..., blender, tunings` and include `blender.router`, `tunings.router`, `tunings.files_router`).
- **Router shape:**
  - JSON routes: `APIRouter(prefix="/...", tags=[...], dependencies=[AuthGuard], responses=ERROR_RESPONSES)` (`api\v1\jobs.py` L16).
  - File routes: a separate router with `dependencies=[FileAuthGuard]`, which also accepts `?token=` (`api\v1\results.py` L12-13), serving `FileResponse(path, media_type=..., filename=..., content_disposition_type="inline"|"attachment", headers={"Cache-Control": "private, max-age=3600"})` (L15, L30-51).
- **Dependencies** (`api\deps.py`):
  - `get_container(connection)` L19-20 and `ContainerDep` L23.
  - Service getters L26-43 and `*ServiceDep = Annotated[..., Depends(...)]` L46-50.
  - `require_auth` L76-79 (header only) with `AuthGuard` L88; `require_auth_allow_query` L82-85 with `FileAuthGuard` L89.
  - Either add `get_mesh_tuner_service` and `get_blender_runtime` plus Dep aliases, or use `ContainerDep` directly, as `references.py` L39-40 does.
- **Status codes:**
  - 201 creates a resource; 202 starts async work (`POST /jobs`, `/system/engine/load`).
  - 204 uses `response_class=Response` (`jobs.py` L63-66).
  - Lists are `{"items": [...]}`.
  - Successful responses are bare objects; only errors have an envelope.
- **Schemas** (`schemas\common.py`):
  - `ApiModel` (L12-15, `extra="ignore", from_attributes=True`) for responses.
  - `RequestModel` (L18-21, `extra="forbid"`, so unknown fields give 422) for bodies.
  - Bounds via `Field(ge=, le=)` (`schemas\jobs.py` L22-47).
  - Responses build their URLs from `API_V1_PREFIX` (`JobResultOut.from_domain` L116-130).
  - Domain models persisted to disk use `models.types.DomainModel` (L27-30) and `UtcDateTime` (L19-24, serializes as ISO `…Z`).
- **IDs:** `utils\ids.py` `new_id(prefix) -> str` (L22-24), `is_valid_id(value, prefix) -> bool` (L39-42). Use `TUNING_PREFIX = "tn"`, giving `tn_<12hex>`, and validate the client id before any filesystem access.

**Proposed routes**

| Method and path | Response and errors |
|---|---|
| `GET /blender/runtime` | `BlenderRuntimeStatus` with `detected[{path, version, label, stable, compatible, source:"system"\|"embedded"}]`, `selected`, `min_version`, `download{state, bytes, total, percent, error}` |
| `POST /blender/runtime/detect` | 200, status |
| `PUT /blender/runtime/selection {path}` | Validates by executing the exe; 422 if incompatible |
| `POST /blender/runtime/download` | 202; 409 if already running; 503 if downloads are disabled |
| `POST /blender/runtime/download/cancel` | |
| `POST /jobs/{job_id}/tunings {options}` | 202 `TuningOut`; 409 if the job is not completed (reuse `ResultService.model_file`); 503 `blender_unavailable`; 429 if the queue is full |
| `GET /jobs/{job_id}/tunings` | `{items}` |
| `GET /jobs/{job_id}/tunings/{tn}` | `TuningOut` |
| `POST /jobs/{job_id}/tunings/{tn}/cancel` | |
| `DELETE /jobs/{job_id}/tunings/{tn}` | 204 |
| `GET /jobs/{job_id}/tunings/{tn}/report` | Report JSON |
| `GET /jobs/{job_id}/tunings/{tn}/model.glb` and `.../export/glb` | Served by `files_router` |

None of these collide with the existing `/jobs/{job_id}` routes (`jobs.py`, `results.py`).

## 7. Errors

- **Base class:** `core\exceptions.py` L14-40 `AppError(message, *, code=None, status_code=None, details=None)`. Existing subclasses: `ValidationAppError` 422, `NotFoundError` 404, `ConflictError` 409, `QueueFullError` 429, `EngineUnavailableError` 503, `EngineError` 500, `JobCancelledError` 409, `StorageError` 500.
- **Handlers:** `core\error_handlers.py` `register_exception_handlers` L154-158 handles `AppError` and every subclass, so new subclasses work without registration.
  - Body: `{"error":{"code","message","details","request_id"}}` (`error_payload` L47-48).
  - Status 500 and above is logged at error level with a traceback (L95-96). Unhandled exceptions return a generic 500 `internal_error` (L147-151).
- **New errors:** `core/exceptions.py` is a declared seam file (`img3d-architecture/SKILL.md`), so put them in `launcher\errors.py`:
  - `class BlenderUnavailableError(AppError): status_code=503; code="blender_unavailable"`
  - `class BlenderError(AppError): status_code=500; code="blender_error"`
  - For incompatible versions, reuse `ValidationAppError(..., code="blender_incompatible")`; for a download already in progress, `ConflictError`.
  - Add all codes to contract §4 (`api-contract.md` L109-122).
- **In tests:** `helpers.assert_error(response, status, code)` (`test\backend\helpers.py` L56-67) checks the exact shape, including the `x-request-id` header.

## 8. Threading and cancellation

| Context | What runs there |
|---|---|
| Event-loop thread | API handlers; `JobQueue` (loop only, `workers\queue.py` L1); `EventBus.publish`; `SystemService` broadcasts |
| GPU thread | `GenerationWorker._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpu-worker")` (L85), via `_submit` (L124-128). **Never put Blender work here**: it would serialize with generation. |
| Default pool (`asyncio.to_thread`) | Short blocking I/O |
| `JobStore` | Thread-safe (RLock, deep copies, `job_store.py` L3, L27) |

**Reuse as-is**
- `JobQueue(max_size)`: `put(id)->int` (raises `QueueFullError`), `await get()->str|None`, `remove(id)->bool`, `position(id)`, `ensure_capacity()`, `close()->list[str]`. It works for tuning ids; its error messages say "job queue".
- `inference.base.CancellationToken` (L201-224): `cancel(reason=None)`, `is_cancelled`, `reason`, `raise_if_cancelled()`, which raises `JobCancelledError`. `inference.base` imports no torch.

**Tuning worker recipe** (mirror `GenerationWorker.start/stop/_process`, L187-208 and L299-367)
- One asyncio task consumes the queue, plus a dedicated `ThreadPoolExecutor(max_workers=1, thread_name_prefix="blender-worker")`.
- The blocking runner uses `subprocess.Popen([exe, "--background", "--factory-startup", "--python-exit-code", "1", "--python", entry_py, "--", "--config", cfg_json], stdout=PIPE, stderr=STDOUT, creationflags=CREATE_NO_WINDOW)`.
  - Merge stderr into stdout, or read it on a second thread; otherwise a full pipe deadlocks.
  - Parse lines starting `@@IMG3D {json}`; the script must emit them with `json.dumps(..., ensure_ascii=True)` and `flush=True`.
  - Decode with `errors="replace"` and write raw lines to `blender.log`.
- **Cancel:** set the token and kill the process tree from the loop thread with `psutil` (pinned `psutil==7.2.2`, `requirements.txt` L29). `readline()` then hits end-of-file and the worker maps the result to `cancelled`.
- **Timeout:** use `blender_tune_timeout_s`.
- **Exit-code mapping:** rc≠0 means failed. A large rc such as `0xC0000005` means a crash.
- **Why Popen and not asyncio subprocess:** the tests escalate unraisable exceptions to errors (`filterwarnings = error::pytest.PytestUnraisableExceptionWarning` in `pytest.ini`). Unclosed Proactor pipe transports finalized after loop close can trigger that.
- **Detection:** `await asyncio.to_thread(subprocess.run, [exe, "--version"], timeout=blender_detect_timeout_s, creationflags=CREATE_NO_WINDOW)`. It takes about 0.13 s, and the timeout bounds the default-executor join at exit.
- **Optional:** a Windows Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (via ctypes) guarantees `blender.exe` dies if the backend crashes.

## 9. Logging

- Use `logging.getLogger(__name__)` (most modules); explicit names exist too: `"img3d"` (`main.py` L57), `"img3d.lifecycle"`, `"img3d.errors"`.
- JSON lines go to `logs\img3d.log` (`JsonFormatter` L125-148: `ts, level, logger, message, request_id, job_id, thread, extra, exception`) and WARNING-and-above to `error.log`.
- `bind_job_id(job_id)` (context manager, L309-316) also works in worker threads. `extra={"tuning_id": ...}` appears under `"extra"`.
- `SecretRegistry` (L76-102) redacts the HF token, UI password, auth secret, `?token=` values and `Bearer` tokens in every line.
- Keep Blender's raw output in a per-tuning `blender.log`; send only a summary and the tail to the app log.

## 10. Helpers to reuse (exact signatures)

| Helper | Location |
|---|---|
| `write_json_atomic(path: Path, data: Any) -> None` (raises `StorageError`), `read_json(path: Path) -> Any` | `storage\json_io.py` L13-19, L22-33 |
| `atomic_write_bytes(path, data)`, `replace_file(src, dst)`, `safe_unlink(path) -> bool`, `safe_rmtree(path) -> bool`, `retry_os(func, *, attempts=8, delay=0.05)`, `ensure_dir(path) -> Path`, `sha256_bytes(data) -> str` | `utils\files.py` L41, L56, L67, L79, L24, L36, L101 |
| `utcnow()`, `isoformat_z(dt)`, `utcnow_iso()`, `parse_iso(s)` | `utils\timeutil.py` |
| `to_jsonable(value)`, `finite_or(value, default=0.0)` | `utils\jsonsafe.py` L10, L24 |
| `compute_glb_stats(path: Path, *, fallback_texture_size: int \| None = None) -> MeshStats` (trimesh; slow on 80 MB files, so run it off the loop) | `processing\mesh_stats.py` L39 |
| `MeshStats`, `JobError(code, message, details)`, `JobStatus` / `TERMINAL_STATUSES` | `models\job.py` L76-86, L70-73; `models\enums.py` L53-63 |
| `JobService.get_job(job_id) -> Job` (404 `NotFoundError`), `ResultService.model_file(job_id) -> StoredFile` (409/404) | |
| `StoredFile(path, media_type, filename=None)`, `GLB_MEDIA_TYPE`, `export_filename` | `services\reference_service.py` L32-36; `services\result_service.py` L14-18 |

## 11. Gitignore

- Root `.gitignore`: `backend/runtime/*` with `!backend/runtime/.gitkeep`, plus `backend/python/`, `.toolchain/`, `.model/`, `.env`, `test/outputs/`, `test/.runtime/`, `__pycache__/`.
- `backend\.gitignore`: `runtime/*` with `!runtime/.gitkeep`, plus `python/`, `.toolchain/`, `.model/`, `.env`.
- So `runtime/blender/` is already ignored. A `backend\.blender\` folder would need new entries in both files.
- Blender writes `blender_scripts\**\__pycache__` (cp313), which `__pycache__/` already covers.
- `test\mesh_tuner\probe\` is not ignored.

## 12. Tests

**How the harness builds the app**
- `test\conftest.py` sets env defaults at L38-42: `IMG3D_RUNTIME_DIR=test\.runtime\default`, `IMG3D_LOG_DIR`, `IMG3D_PRELOAD_MODEL=false`, `IMG3D_FRONTEND_DIST_DIR`, `NO_COLOR`. L43-44 removes secrets from the env.
- `test\backend\conftest.py` fixtures:
  - `runtime_dir` (L38-45): a unique `test\.runtime\<name>-<hex>`, removed after the test.
  - `tmp_runtime` (L48-51).
  - `make_settings(**overrides)` (L54-71): `Settings(_env_file=None, ...)`, auth off, ping interval 0.5.
  - `make_app(*, engine=None, settings=None, raise_server_exceptions=True, **overrides) -> AppHarness(app, client, engine, settings, .container)` (L74-91): calls `main.create_app(settings, engine=FakeEngine())`; the `TestClient` context runs the lifespan.
  - `harness` (L94-96) and `client` (L99-101).
- `helpers.py`: `assert_error`, `create_set_with_image`, `create_job`, `wait_for(pred, timeout)`, `wait_for_job(client, id, statuses)`, `ws_receive(ws, timeout)`, `ws_collect_until(ws, stop)`, `live_uvicorn(app, port)`, `free_port()`.
- Async calls on the app loop: `h.client.portal.call(h.container.retention_service.run_once)` (`test_recovery.py` L99).
- `fakes.py`: `write_test_glb(path)` (L48-60) writes a real 12-face textured cube GLB, a good tuning input. `FakeEngine.hold()/release()` (L105-110) can gate a job.

**Adding new tests**
- `test\backend\test_blender_runtime.py` and `test_mesh_tuner_api.py` are auto-collected.
  - Inject a test-only `FakeBlenderRunner` through the new `create_app(..., blender_runner=)` keyword; keep the fake in `test\backend\` (e.g. `fakes_blender.py`).
  - Or point the runner's command prefix at `[sys.executable, "-s", <test fake script>]`.
  - Pass `blender_detect_on_startup=False` through `make_app(...)`.
  - Optionally add `os.environ.setdefault("IMG3D_BLENDER_DETECT_ON_STARTUP", "false")` to `test\conftest.py`. Otherwise every app startup in the existing ~138 tests runs the real `blender.exe`.
- Real-Blender track: `test\mesh_tuner\test_*.py`, skipped when no Blender ≥5.1 is found, run explicitly:
  `& $py -s -m pytest -c E:\14-OCT-2026\Img3D\test\pytest.ini E:\14-OCT-2026\Img3D\test\mesh_tuner -q`
- A new `blender` marker must be registered under `markers =` in `pytest.ini` (`--strict-markers` is on).
- Test outputs go under `test\outputs\mesh_tuner\` only.

## 13. Recommended files

**New**
- `backend\launcher\__init__.py`
- `backend\launcher\errors.py`
- `backend\launcher\blender_version.py`: parses `^Blender (\d+)\.(\d+)(?:\.(\d+))?\s*(.*)$`; a suffix of `LTS` or empty means stable; `Alpha`/`Beta`/`Release Candidate` means not stable.
- `backend\launcher\blender_detect.py`: candidates are `blender_path`, `shutil.which("blender")`, `<search_dir>\*\blender.exe` and `<install_dir>\*\blender.exe`. Skip folders without an exe (the empty "Blender 4.4" and "Blender 4.5"). Execute `--version` to confirm. 4.2.17 is listed with `compatible:false`. Pick the highest stable version at or above min.
- `backend\launcher\blender_download.py`: temp staging, sha256 check, extract, `os.replace`.
- `backend\launcher\blender_runner.py`
- `backend\launcher\runtime_manager.py`: `BlenderRuntimeManager` state plus `selection.json`, publishes `blender.updated`.
- `backend\blender_scripts\tune_mesh.py` (entry; inserts its own folder into `sys.path`; reads `--config`).
- `backend\blender_scripts\img3d_tuner\{__init__,protocol,io_gltf,repair,refine,symmetry,quads,subdivide,rig_weights,validate}.py`
- `backend\models\tuning.py` (`DomainModel`; reuse the `JobStatus` values)
- `backend\storage\tuning_store.py` (JobStore pattern: RLock, deep copies, `load`, `recover_interrupted`)
- `backend\schemas\mesh_tuner.py`
- `backend\services\mesh_tuner_service.py`
- `backend\workers\tuning_worker.py`
- `backend\api\v1\blender.py` and `backend\api\v1\tunings.py`

**Edited (minimal)**
- `config\settings.py`: fields, path validator, `safe_summary`.
- `core\container.py`: fields before L49, construction after L90, optional runner keyword.
- `main.py` `create_app`: optional `blender_runner` keyword, only for test injection.
- `core\lifecycle.py`: startup and shutdown hooks (§4).
- `api\router.py`: include the routers.
- `api\deps.py`: optional Dep aliases.
- Optional: `services\job_service.py` `delete_job` returns 409 during tuning; `api\websocket_hub.py` `_job_filter` for tuning events.
- `backend\.env.example`
- `.claude\skills\img3d-architecture\api-contract.md` (§1 layout, §2 settings, §4 errors, §7 routes, §8 events, §9 runtime) and `SKILL.md` layering.
- `test\pytest.ini` (marker) and `test\conftest.py` (detection off).
- Frontend: `frontend\src\api\{types.ts, ws.ts}` plus new `blender.ts` and `tunings.ts`.
- If `httpx` is used for downloads, move it from the "Tests / tooling" block to runtime deps in `requirements.txt`, or use `urllib.request`.

## 14. Pitfalls

1. **Always pass `--python-exit-code 1`.** Without it, a script exception exits with rc=0 (verified).
2. **The Blender entry script must add its own folder to `sys.path`** (verified it is absent). Blender's `scripts\modules` come first, so use unique module names.
3. **Blender's Python ignores the environment** (verified). Configuration must travel as arguments after `--` or as a config JSON path. Prefer one config file: it avoids Windows quoting and the 32,767-character command-line limit.
4. **The embedded backend Python runs isolated.** Always run it with `-s`. New packages need `__init__.py`. Never import `bpy` or `blender_scripts` from the backend.
5. **Don't execute Blender in `build_container` or synchronously in startup.** Use a background task, and keep detection off in tests.
6. **Keep the tuning worker separate.** Don't use the GPU executor or the generation `JobQueue` for tuning; `JobQueue` and `EventBus.publish` are loop-thread only, so use `publish_threadsafe` from threads.
7. **Kill Blender on shutdown and cancel.** `main.py` only force-exits for a busy GPU worker, and Windows does not kill child processes when the parent dies.
8. **Pipes and encoding:** use `stderr=STDOUT`, `CREATE_NO_WINDOW`, `ensure_ascii=True` JSON, and `errors="replace"` decoding.
9. **Retention removes tuning outputs with their job** after 14 days (based on the job's finish date) or past 200 jobs. Running tunings do not block `DELETE /jobs/{id}`. Copy inputs to `temp/` and re-check the job before finalizing.
10. **Settings paths:** new `Path` fields are resolved only if listed in `_resolve_paths`, or handled by their own validator for optional paths. Otherwise they are relative to the working folder. Derive the install dir from `runtime_dir` for test isolation.
11. **Don't use bare `BLENDER_*` env names**; they collide with Blender's own variables.
12. **asyncio subprocess fails on `SelectorEventLoop`** (`NotImplementedError`). It works under the uvicorn Proactor loop, but Popen in a thread is safer under pytest's unraisable-as-error filter.
13. **`hello` does not include Blender state.** The frontend must call `GET /blender/runtime` on connect and refresh on `blender.updated`.