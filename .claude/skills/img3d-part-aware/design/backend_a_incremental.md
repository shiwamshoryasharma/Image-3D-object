# Backend proposal A: risk-first, incremental part-aware layer

Date: 2026-10-16. Status: design proposal (no code changed). Angle: **maximum backward compatibility, minimum change
to working code.** The working image-to-3D job stays as it is. All new behaviour is added as **follow-up runs** that
read a finished job, plus a **workflow** resource that chains public service calls. The tuner is an existing example
of this pattern. Every new capability has a feature flag.

Inputs read in full: `api-contract.md`, `.claude/CLAUDE.md`, all nine `research/*.md` reports,
`img3d-part-aware/DECISIONS.md` (the user's decisions, which take priority over the "pending" wording in the task),
and the source files cited below. Line numbers come from the current working tree.

Legend: **[V]** means verified in the code or on disk during this session. **[A]** means a design assumption that
an implementation slice must check.

---------------------------------------------------------------------------------------------------
## 0. Decisions

1. **The generation job is frozen.** These files are not edited at all:
   - `pipelines/image_to_3d.py`, `pipelines/progress.py`, `pipelines/base.py`
   - `inference/**` (today's files)
   - `models/job.py`, `models/enums.py`
   - `schemas/jobs.py`, `schemas/system.py`
   - `services/job_service.py`, `services/system_service.py`, `services/generation_settings.py`
   - `storage/job_store.py`, `storage/paths.py`

   A hash-pinning test enforces this (§19.1). As a result, `model.glb`, `job.json`, `JobOut`, `JobStage`, the
   percent formula and `/system` are byte-for-byte and key-for-key unchanged.
2. **Part awareness uses three new "run" resources and one orchestrator.** Each run follows the pattern of the
   tuning runs (`tn_`):

   | Prefix | Resource | What it does | Where it runs |
   |---|---|---|---|
   | `ig_` | image generation | prompt → candidate images (Z-Image-Turbo) | GPU thread |
   | `sg_` | segmentation | 2D semantic parts of an input image, **before** reconstruction, or of a finished job's conditioning image | GPU thread |
   | `pa_` | part analysis | **after** reconstruction: read `model.glb`, then produce part labels, cut physical parts, regions, manifest and assembled GLB | GPU thread (`components` strategy: CPU) |
   | `wf_` | workflow | server-side chain prompt → image → segmentation → **existing job** → part analysis → export. This is the Node-UI graph run on the server | event loop only |

3. **Only four seams touch working code** (§4):
   - (S-a) `GenerationWorker.submit_gpu_task`: about 12 lines. All extra GPU work shares the existing single
     `gpu-worker` thread.
   - (S-b) one optional `ServiceContainer.part_aware` field plus start/stop calls in `core/lifecycle.py`.
   - (S-c) router inclusion, new `EventType` members, one more job-scoped WebSocket event, and an extra
     `create_app(part_providers=…)` keyword for test doubles.
   - (S-d) new optional fields in `Settings`.

   Everything else is new modules.
4. **Prompt-to-3D needs no change to `CreateJobRequest`.** The flow is:
   1. The `ig_` run generates the image.
   2. The candidate is imported through the **existing** `ReferenceService.upload_image`, so it gets the same
      validation and thumbnail as an upload.
   3. The job is created through the **existing** `JobService.create_job`.

   A legacy-UI "retry" therefore re-runs image-to-3D on the generated image (the correct behaviour).
5. **Discovery:** `GET /api/v1/features` and `GET /api/v1/models` report honest states for each model, each
   capability and each feature flag, with licence metadata. `/system` is not touched.
6. **The models follow `DECISIONS.md`.** They are already on disk and `diffusers==0.39.0` and `accelerate==1.15.0`
   are already installed [V]:
   - Text-to-image: Z-Image-Turbo.
   - 2D segmentation: Grounding DINO base, SAM 2.1-L, Florence-2-large, See-through `l2d_sam_iter2` and
     Sapiens2-seg-0.4B.

   The registry is still generic. A model is usable only if it is approved, installed, its dependencies and any
   vendored code are present, and it fits the VRAM budget. See-through and Sapiens2 report
   `missing_dependency / vendored_source_missing` until their code is vendored under `backend/third_party/` [V: not
   present today].
7. **Physical policy (from `DECISIONS.md` §3):**
   - Hair, clothes, accessories, detachable objects and the **eye areas** are cut into separate open meshes. Each cut
     has seam records, keeps the shared texture atlas and needs no re-bake.
   - Eyebrows and eyelashes are `physical_deferred` records. Each has a mask, a surface region and metadata (lid
     curve). Their geometry is left to the future tuning layer.
   - Face and body skin stay one mesh with semantic regions.
8. **The `components` strategy works today with zero ML.** It uses welded connected components on the CPU. It
   proves the whole contract (manifest, assembled GLB, file routes, UI) with real data before any segmenter
   integration lands.
9. **Tuning stays off.** There are four gates (§17):
   - `IMG3D_BLENDER_ENABLED=false` in `backend/.env`. This is the existing hard gate, already enforced and tested.
   - `/features` reports `mesh_tuning.enabled=false` and `part_refinement.enabled=false`.
   - New code never imports tuner modules (an import-boundary test checks this).
   - The `PartConsumer` registry refuses registrations while `part_refinement_enabled=false`. It has no
     implementations.

---------------------------------------------------------------------------------------------------
## 1. Invariants (acceptance criteria for every slice)

| # | Invariant | How it is enforced |
|---|---|---|
| I1 | `model.glb`, `preview.png`, `job.json` and `JobOut` of an image-to-3D job are produced by untouched code | `test/backend/test_protected_files.py` compares sha256 values (CRLF-normalized) of the protected files with `test/backend/protected_files.sha256` |
| I2 | A part analysis never writes into `jobs/<job>/` except `jobs/<job>/parts/<pa>/` | Writes go to `runtime/temp/parts/<pa>/` and are moved after a `job_exists` re-check. A test hashes `model.glb`, `preview.png` and `job.json` before and after a run |
| I3 | No new `JobStage`, no change to `STAGE_TABLE`, percent formula, `/system` keys, `limits`, `defaults`, `EngineCapabilities.to_dict()` | Protected files plus the existing tests (`test_progress.py:56-67`, `test_health_system.py:37,54,61`, `test_engine_unit.py:81`) |
| I4 | Existing request bodies are unchanged (`CreateJobRequest`, `GenerationSettings`, `TuningOptions`) | Protected `schemas/jobs.py`. New bodies are new models |
| I5 | One GPU thread. Load, generation, unload and every auxiliary GPU step are FIFO on `ThreadPoolExecutor(1, "gpu-worker")` | Seam S-a. `test_gpu_task_seam.py` checks the thread name and that nothing overlaps |
| I6 | No new heavy native dependencies. `transformers`/`huggingface-hub` are never upgraded | Installs only with `-c backend/constraints.txt` [V: file exists, pins diffusers 0.39.0 and accelerate 1.15.0]. Import-boundary test |
| I7 | API and services never import torch, diffusers, transformers, nvdiffrast or cumesh | AST scan in `test/backend/test_import_boundaries.py` over `api/`, `services/`, `pipelines/`, `parts/`, `processing/`, `storage/`, `models/`, `schemas/`, `core/`, `workers/` |
| I8 | No fake generation data in the app. Unavailable means an honest 503 with a reason. Test doubles live only under `test/` | Code review plus `fakes_*.py` under `test/backend/` |
| I9 | All 182 existing tests (173 backend + 9 aux_models) pass **without modification** | Each slice runs `pytest test/backend test/aux_models`. `git diff --stat` on existing test files is empty |
| I10 | Tuner modules are never imported by part-aware code | Import-boundary test: `services.mesh_tuner_service`, `workers.tuning_worker`, `launcher`, `blender_scripts` are forbidden in new packages |

Protected files (frozen in S0; S1 adds `workers/generation_worker.py` after its seam edit):
```
backend/pipelines/image_to_3d.py         backend/pipelines/progress.py        backend/pipelines/base.py
backend/pipelines/reference_strategy.py  backend/inference/base.py            backend/inference/registry.py
backend/inference/unavailable.py         backend/inference/device.py          backend/inference/trellis/*.py
backend/models/job.py                    backend/models/enums.py              backend/models/reference.py
backend/schemas/jobs.py                  backend/schemas/system.py            backend/schemas/references.py
backend/services/job_service.py          backend/services/system_service.py   backend/services/generation_settings.py
backend/services/reference_service.py    backend/services/result_service.py   backend/storage/job_store.py
backend/storage/paths.py                 backend/storage/reference_store.py   backend/processing/*.py
```

---------------------------------------------------------------------------------------------------
## 2. What is reused (existing public APIs only)

| Existing piece | Used for | Notes |
|---|---|---|
| `GenerationWorker._executor` (`workers/generation_worker.py:85`) through the new `submit_gpu_task` | every auxiliary GPU step | FIFO with `_load_engine_blocking` (:144), `_execute_pipeline` (:369) and unload (:172, :217) |
| `JobQueue` (`workers/queue.py:11-89`) | the run queue (`run_queue_max_size`) | Ids are prefixed (`ig_`, `sg_`, `pa_`), so one queue serves all three run kinds |
| `JobService.create_job(CreateJobRequest)` (`services/job_service.py:83`) | workflow reconstruction step | Same validation, same 429/422/404 |
| `JobService.add_delete_hook` (`:60`) | cancel and forget active part analyses before a job folder is deleted | Same pattern as `MeshTunerService.on_job_deleting` |
| `ReferenceService.create_set(name)` (`:76`) and `upload_image(set_id, data=, filename=, slot=, label=, make_primary=)` (`:130`) | import a T2I candidate as a normal reference image | Pillow validation, thumbnail, slot rules and primary rules all reused. `label` is at most 200 chars (`schemas/references.py:13`) |
| `ResultService.model_file(job_id)` (`:40`) | "job is completed and `model.glb` exists" check (404/409) | Same as the tuner's `create_run` |
| `JobStore.inputs_dir / preview_path / model_path / job_dir` | read-only access to job artifacts | |
| `Job.result.metadata["preprocess"]` | conditioning-frame recovery (`alpha_crop` / `background_removed` / `untouched`) | Already persisted [V: job.json sample in the contract map §2.6] |
| `EventBus.publish / publish_threadsafe / subscribe(predicate)` (`core/events.py:123-165`) | new events and the workflow service's internal subscription | |
| `CancellationToken` (`inference/base.py:201-224`) | cancellation of every run | |
| `utils.ids.new_id / is_valid_id`, `storage.json_io.write_json_atomic / read_json`, `utils.files.safe_rmtree / replace_file` | ids, atomic JSON, moves | |
| `processing.mesh_stats.compute_glb_stats` | stats of the assembled and per-part GLBs | Safe for multi-node GLBs (sums nodes) |
| Tuning-run pattern (`models/tuning.py`, `storage/tuning_store.py`, `workers/tuning_worker.py`, `services/mesh_tuner_service.py`) | template for the generic run framework (§5) | Only copied, never imported (I10) |

---------------------------------------------------------------------------------------------------
## 3. Architecture

```
 /api/v1  (existing routers, unchanged)            /api/v1  (new routers: api/part_routes.py)
 auth system references jobs results tunings       features models image-generations segmentations
 blender ws                                        part-analyses workflows (+ jobs/{id}/part-analyses)
        │                                                   │
        ▼                                                   ▼
 JobService ── JobQueue ── GenerationWorker      Feature/ModelRegistry/ImageGeneration/Segmentation/
        ▲                     │   ▲               PartAnalysis/Workflow services (torch-free)
        │ create_job          │   │ submit_gpu_task (seam S-a)          │
        │ add_delete_hook     │   └──────────── RunWorker (run_queue) ◄─┘
        └──── WorkflowService (EventBus subscription + 5 s reconciliation sweep)
                                                   │
                                   executors (torch-free orchestration, pipelines/*)
                                                   │
                     ProviderRegistry ── providers (torch only in implementation modules)
                     inference/imagegen/zimage.py, inference/segmentation/{gdino,sam2,florence2,
                     see_through,sapiens2}.py, inference/parts/raster_lifter.py (nvdiffrast + cumesh)
```

Layering (enforced by I7):
- `api → services → {pipelines, parts, processing, storage, models, schemas}` and then `inference/*/base.py`
  (torch-free ABCs).
- Implementation modules under `inference/` are the only ones that import torch, diffusers, transformers,
  nvdiffrast or cumesh. They are imported lazily by `inference/providers/registry.py`.

New package tree (all new files unless marked EDIT):
```
backend/
  config/settings.py                         EDIT (additive fields, §16)
  core/container.py                          EDIT (+ part_aware field, build call)
  core/lifecycle.py                          EDIT (+ start/stop calls)
  core/feature_errors.py                     FeatureDisabledError, CapabilityUnavailableError, ModelUnavailableError
  core/part_aware_container.py               PartAwareContainer + build_part_aware()
  main.py                                    EDIT (create_app(..., part_providers=None))
  workers/generation_worker.py               EDIT (+ submit_gpu_task, accepting_gpu_tasks)
  workers/run_worker.py                      RunWorker (one asyncio consumer of run_queue)
  api/router.py                              EDIT (+ include build_part_aware_router())
  api/websocket_hub.py                       EDIT (_JOB_SCOPED_EVENTS += part_analysis.updated)
  api/part_routes.py                         build_part_aware_router()
  api/part_deps.py                           DI accessors for the new services
  api/v1/features.py  api/v1/model_registry.py  api/v1/image_generations.py
  api/v1/segmentations.py  api/v1/part_analyses.py  api/v1/workflows.py
  schemas/events.py                          EDIT (+ EventType members)
  schemas/features.py  schemas/model_registry.py  schemas/image_generation.py
  schemas/segmentation.py  schemas/parts.py  schemas/workflows.py  schemas/runs.py
  models/runs.py  models/image_generation.py  models/segmentation.py  models/part_analysis.py  models/workflow.py
  storage/run_store.py  storage/workflow_store.py  storage/run_layout.py
  pipelines/runs/{__init__,executor,reporter,context}.py
  pipelines/image_generation.py  pipelines/prompt_templates.py  pipelines/segmentation.py  pipelines/part_analysis.py
  processing/seg2d/{__init__,frame,fusion,eyes,readiness}.py      (numpy/opencv/Pillow only)
  parts/{__init__,taxonomy,policy,manifest,weld,components,partition,seams,gltf_export,reassembly,consumers}.py
  inference/providers/{__init__,base,catalog,registry,unavailable}.py   (torch-free)
  inference/imagegen/{__init__,base}.py  inference/imagegen/zimage.py               (zimage.py: torch/diffusers)
  inference/segmentation/{__init__,base}.py
  inference/segmentation/{gdino,sam2,florence2,see_through,sapiens2}.py          (torch/transformers/vendored)
  inference/parts/{__init__,base}.py  inference/parts/raster_lifter.py           (raster_lifter: torch/nvdiffrast/cumesh)
  services/feature_service.py  services/model_registry_service.py  services/image_generation_service.py
  services/segmentation_service.py  services/part_analysis_service.py  services/workflow_service.py
  services/run_events.py  services/run_retention_service.py
  third_party/see-through/   third_party/sapiens2/          git submodules (small modules only, never their requirements)
```

---------------------------------------------------------------------------------------------------
## 4. The four seams (exact edits)

### 4.1 S-a: GPU task seam in `workers/generation_worker.py` (additive; about 12 lines after `_submit`, :124-128)

```python
    @property
    def accepting_gpu_tasks(self) -> bool:
        """``False`` once :meth:`stop` has begun (no new auxiliary GPU work may be queued)."""
        return not self._stopping

    def submit_gpu_task(self, fn: Callable[..., T], /, *args: Any) -> asyncio.Future[T]:
        """Run a blocking callable on the single GPU thread, FIFO with model load/generation/unload.

        Used by auxiliary runs (text-to-image, segmentation, part lifting). The callable must honour its own
        CancellationToken and leave no weights on the GPU when it returns (move to CPU + empty_cache in finally).
        Raises EngineUnavailableError("The server is shutting down") while stopping.
        """
        if self._stopping:
            raise EngineUnavailableError("The server is shutting down")
        return asyncio.wrap_future(self._submit(fn, *args))
```
Notes:
- `T = TypeVar("T")` is added at module top.
- `is_busy` already reflects every in-flight future, so `shutdown_engine` (:213) skips the unload while an auxiliary
  task runs. That is correct because the shutdown order (§4.2) stops the run worker first.
- `running_job_id`, `unload_engine` and `_wait_for_engine` are unchanged. A queued auxiliary task simply runs before
  or after the next job in FIFO order.

### 4.2 S-b: container and lifecycle

`core/container.py`:
- Add the last dataclass field `part_aware: PartAwareContainer | None = None`. It has a default, so it goes after
  `started`.
- At the end of `build_container(...)`, before the `return`, add:
  ```python
  part_aware = build_part_aware(settings, paths=paths, bus=bus, engine=engine, gpu=worker, job_service=job_service,
                                job_store=job_store, reference_service=reference_service,
                                result_service=result_service, overrides=part_providers)
  ```
  and pass it into `ServiceContainer(...)`.
- The new keyword `part_providers: PartProviderOverrides | None = None` is added to `build_container`.
- `build_part_aware` registers `job_service.add_delete_hook(part_service.on_job_deleting)`.

`core/lifecycle.py`:
- `startup`: after `container.tuning_worker.start()` (:44), add
  `if container.part_aware is not None: await container.part_aware.start()`. This loads the stores, marks
  interrupted runs and workflows, starts `RunWorker`, starts the workflow subscription and sweep, and starts the
  retention task. Nothing is started when `part_aware_enabled=false`, except store loading so that history stays
  readable.
- `shutdown`: **before** `container.worker.stop(...)` (:84), add
  `await container.part_aware.stop(timeout=settings.shutdown_timeout_s)`. This order matters: the run worker cancels
  its token, waits for the in-flight GPU task and fails queued runs as `interrupted` before the generation worker
  stops and `shutdown_engine` shuts the executor down (:223).

### 4.3 S-c: API wiring

- `api/router.py`: add `api.include_router(build_part_aware_router())`. It holds the six new routers. There are no
  path collisions: `/jobs/{id}/part-analyses` does not clash with `results` or `tunings` (`/jobs/{id}/tunings`,
  `/jobs/{id}/model.glb|export/glb|preview|result`).
- `schemas/events.py` `EventType` gains `FEATURES_UPDATED = "features.updated"`,
  `IMAGE_GENERATION_UPDATED = "image_generation.updated"`, `SEGMENTATION_UPDATED = "segmentation.updated"`,
  `PART_ANALYSIS_UPDATED = "part_analysis.updated"` and `WORKFLOW_UPDATED = "workflow.updated"`.
- `api/websocket_hub.py:229`: add `str(EventType.PART_ANALYSIS_UPDATED)` to `_JOB_SCOPED_EVENTS`.
- `main.create_app(..., part_providers: PartProviderOverrides | None = None)` is passed through to
  `build_container`. Tests use it to inject doubles.

### 4.4 S-d: settings

New optional fields in `config/settings.py` (§16). `safe_summary()` is not changed. The part-aware container logs
its own `part_aware_summary()` at startup.

---------------------------------------------------------------------------------------------------
## 5. Generic run framework (shared by `ig_`, `sg_`, `pa_`)

### 5.1 Domain (`models/runs.py`)
```python
RunStatus = JobStatus                     # queued | running | completed | failed | cancelled
StepStatus = Literal["pending", "running", "done", "warning", "skipped", "failed"]

@dataclass(frozen=True, slots=True)
class StepDef:
    id: str; label: str; weight: float; device: Literal["cpu", "gpu"]

class RunStep(DomainModel):
    id: str; label: str
    status: StepStatus = "pending"
    message: str | None = None
    progress: float | None = None          # 0..1 within the step
    duration_s: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)

class RunProgress(DomainModel):
    percent: float = 0.0
    step_id: str | None = None
    step_index: int = 0                     # 1-based; 0 before the first step
    step_count: int = 0
    message: str | None = None
    updated_at: UtcDateTime | None = None

class RunRecord(DomainModel):
    id: str
    kind: Literal["image_generation", "segmentation", "part_analysis"]
    status: RunStatus = JobStatus.QUEUED
    created_at: UtcDateTime
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None
    options: dict[str, Any] = Field(default_factory=dict)       # effective (normalized) request
    progress: RunProgress = Field(default_factory=RunProgress)
    steps: list[RunStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    models: list[dict[str, Any]] = Field(default_factory=list)  # [{id, revision, license_id}] actually used
    error: JobError | None = None
    cancel_requested: bool = False
    workflow_id: str | None = None                               # set when created by a workflow
```
`ImageGenerationRun`, `SegmentationRun` and `PartAnalysis` subclass `RunRecord` and add `result` (§7–§9) plus
`job_id` where relevant.

Percent (monotonic, clamped to 0..100, and exactly 100 on `completed`):
`percent = 100 · (Σ w(done|warning|skipped) + w(current) · current.progress) / Σ w(all steps)`.

### 5.2 State machine (every run kind)
```
queued ──(RunWorker picks)──► running ──(executor ok)──────────► completed
  │                              ├──(AppError / exception)──────► failed   (error.code kept, or out_of_memory/run_error)
  │                              ├──(token cancelled by user)───► cancelled
  │                              └──(shutdown)──────────────────► failed   (interrupted)
  ├──(POST …/cancel)────────────────────────────────────────────► cancelled
  └──(queue closed at shutdown)─────────────────────────────────► failed   (interrupted)
startup: any persisted queued|running ──────────────────────────► failed   (interrupted)
step:    pending ─► running ─► done | warning | failed ;  pending ─► skipped (not applicable / reused input)
```

### 5.3 Persistence (`storage/run_store.py`)
```python
class RunStore(Generic[R]):
    def __init__(self, *, model: type[R], prefix: str, run_file: str = "run.json",
                 locate: Callable[[R], Path],                 # final run dir
                 scan: Callable[[], Iterable[Path]],          # candidate run dirs for load()
                 job_exists: Callable[[str], bool] | None = None) -> None
    def load(self) -> int
    def recover_interrupted(self) -> list[R]
    def get(self, run_id: str) -> R | None                     # validates is_valid_id(prefix) first
    def list(self, *, job_id: str | None = None, status: JobStatus | None = None, limit: int | None = None) -> list[R]
    def list_active(self, *, job_id: str | None = None) -> list[R]
    def save(self, run: R) -> bool                             # False (and no write) if job-scoped and the job is gone
    def delete(self, run_id: str) -> bool                      # safe_rmtree(run dir)
    def forget_job(self, job_id: str) -> list[str]
    def run_dir(self, run: R) -> Path
```
It is thread-safe (RLock), hands out deep copies and uses `write_json_atomic`. This is the same contract as
`TuningStore`, made generic.

### 5.4 Execution
`pipelines/runs/executor.py`:
```python
class RunExecutor(Protocol[R, P]):
    kind: str                                                 # "image_generation" | "segmentation" | "part_analysis"
    prefix: str                                               # "ig" | "sg" | "pa"
    def plan(self, run: R) -> tuple[StepDef, ...]            # steps not applicable are returned and pre-marked skipped
    def needs_gpu(self, run: R) -> bool
    def prepare(self, run: R, work_dir: Path) -> P           # worker thread (to_thread): snapshot inputs into work_dir
    def execute(self, run: R, prepared: P, ctx: RunContext) -> Any        # GPU thread or the CPU thread; blocking
    def finalize(self, run: R, prepared: P, outcome: Any) -> R            # worker thread: move outputs, build result
```
`pipelines/runs/context.py`:
```python
class RunContext:
    cancel: CancellationToken
    work_dir: Path                                            # runtime/temp/<kind>/<run_id>/
    device: str                                               # settings.device ("cuda")
    registry: ProviderRegistry
    def step(self, step_id: str, message: str | None = None) -> ContextManager[StepHandle]  # running → done/failed
    def progress(self, step_id: str, fraction: float, message: str | None = None) -> None
    def warn(self, text: str) -> None
    def skip(self, step_id: str, reason: str) -> None
```
`pipelines/runs/reporter.py` `RunReporter`:
- `snapshot()`, `mutate(fn, publish, persist)`.
- Throttled publishing: at most 5 messages per second, trailing flush, marked droppable.
- Persists at most every 2 s.
- Status changes are always published and never droppable.

This copies `JobProgressReporter` and the tuning `RunReporter`.

`workers/run_worker.py`:
```python
class GpuTaskSubmitter(Protocol):
    def submit_gpu_task(self, fn: Callable[..., T], /, *args: Any) -> asyncio.Future[T]: ...
    @property
    def accepting_gpu_tasks(self) -> bool: ...
    @property
    def is_busy(self) -> bool: ...

class RunWorker:
    def __init__(self, *, settings: Settings, gpu: GpuTaskSubmitter, queue: JobQueue,
                 executors: Mapping[str, RunExecutor], stores: Mapping[str, RunStore],
                 publishers: Mapping[str, RunEventPublisher], bus: EventBus, paths: RuntimePaths) -> None
    def start(self) -> None
    async def stop(self, timeout: float) -> None
    @property
    def running_id(self) -> str | None
    @property
    def running_kind(self) -> str | None
    def live_snapshot(self, run_id: str) -> RunRecord | None
    def cancel_running(self, run_id: str, reason: str) -> RunRecord | None
    async def wait_finished(self, run_id: str, timeout: float) -> bool
```
Loop for one run (`_process`):
1. Mark the run `running` and set `started_at`. If `gpu.is_busy`, set the message to
   `"Waiting for the GPU (generation in progress)"`.
2. `prepared = await asyncio.to_thread(executor.prepare, run, work_dir)`.
3. Dispatch: if `executor.needs_gpu(run)`, call `await gpu.submit_gpu_task(executor.execute, run, prepared, ctx)`.
   Otherwise run it on the worker's own `ThreadPoolExecutor(1, "parts-cpu")`.
4. Call `await asyncio.to_thread(executor.finalize, …)` and then mark `completed`.
5. On `JobCancelledError` mark `cancelled`. On `AppError` mark `failed` with `exc.code`. On any other exception mark
   `failed` via `classify_exception` (imported from `workers.generation_worker`: an existing public function, read
   only).
6. `finally`: `safe_rmtree(work_dir)`. A failed `pa_` run keeps `work_dir/log.txt` by moving it to
   `jobs/<job>/parts/<pa>/log.txt` (job-existence check first).

### 5.5 GPU memory and the VRAM budget
- Providers keep weights in CPU RAM between runs (`aux_keep_models_in_ram=true`). They move to `cuda` only inside
  `execute`. `finally` calls `provider.offload()` (weights to CPU, `torch.cuda.empty_cache()`).
- A provider idle for more than `aux_model_idle_unload_s` (600 s) is unloaded. The RunWorker submits `unload()` to
  the GPU thread when it is idle.
- **Budget check:** `available_vram_gb = aux_vram_budget_gb (28.0) − (engine_resident_vram_gb (17.0) if
  settings.low_vram is False and engine.status().is_ready else 0)`.
  - If `spec.vram_peak_gb > available_vram_gb`, the run fails fast with 503 `capability_unavailable`
    (`reason_code: insufficient_vram`). `/features` reports the same.
  - With the default `low_vram=true` [V: `settings.py:76`], TRELLIS.2 returns all weights to CPU after every run
    (`engine.py:312-326`), so the budget is the full 28 GB. Z-Image-Turbo needs about 15 GB with offload.
- **RNG isolation:** providers use private `torch.Generator` objects and never call the global `torch.manual_seed`.
  Auxiliary work never runs inside `engine.generate`. TRELLIS reseeds globally at conditioning
  (`generation.py:202`), so same-seed reproducibility of jobs is unaffected. The parity test
  `test/engine/test_generation_parity.py` stays green.
- **OOM:** the run fails with `out_of_memory` (via `classify_exception`). `offload()` and `empty_cache()` still run in
  `finally`. The next generation job is unaffected.

---------------------------------------------------------------------------------------------------
## 6. Model registry and provider seams

### 6.1 Torch-free types (`inference/providers/base.py`)
```python
ModelKind = Literal["text_to_image", "segmentation", "part_extraction"]
CommercialUse = Literal["allowed", "allowed_with_conditions", "revenue_capped", "non_commercial",
                        "research_only", "unknown"]
ModelUiState = Literal["not_installed", "installed", "loading", "loaded", "error", "unavailable"]
ReasonCode = Literal["ok", "disabled", "not_approved", "license_blocked", "missing_dependency",
                     "vendored_source_missing", "not_installed", "incompatible", "insufficient_vram", "error"]

@dataclass(frozen=True, slots=True)
class LicenseInfo:
    id: str; name: str; url: str; commercial: CommercialUse
    gated: bool = False; requires_acceptance: bool = False
    attribution: str | None = None; conditions: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class DependencySpec:
    distribution: str            # importlib.metadata name, e.g. "diffusers"
    requirement: str             # e.g. "==0.39.0" (checked with packaging.specifiers)

@dataclass(frozen=True, slots=True)
class ModelSpec:
    id: str                      # ^[a-z0-9_]{2,40}$
    kind: ModelKind
    roles: tuple[str, ...]       # segmentation: "detector"|"grounder"|"refiner"|"parser"; t2i: "generator"; lifter: "lifter"
    display_name: str
    provider: str                # implementation key resolved lazily by the registry
    repo_id: str | None
    revision: str | None         # pinned when known; else read from the download manifest
    local_subdir: str            # relative to settings.aux_model_root
    required_files: tuple[str, ...]  # globs that must all match for "installed"
    size_bytes: int | None
    vram_peak_gb: float
    ram_gb: float
    license: LicenseInfo
    dependencies: tuple[DependencySpec, ...] = ()
    vendored_source: str | None = None   # path under backend/, e.g. "third_party/see-through"
    approval_required: bool = True
    default_approved: bool = False       # True for DECISIONS.md models
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    notes: str | None = None

@dataclass(frozen=True, slots=True)
class ProviderStatus:
    model_id: str; state: ModelUiState; reason_code: ReasonCode; reason: str | None
    revision: str | None = None; loaded_device: Literal["cpu", "cuda"] | None = None
    last_used_at: datetime | None = None

class ModelProvider(ABC):            # GPU-thread methods: load/offload/unload/run
    spec: ModelSpec
    @abstractmethod
    def status(self) -> ProviderStatus: ...                  # cheap, thread-safe
    @abstractmethod
    def load(self, cancel: CancellationToken) -> None: ...  # weights → CPU RAM (idempotent)
    @abstractmethod
    def offload(self) -> None: ...                          # weights → CPU; torch.cuda.empty_cache()
    @abstractmethod
    def unload(self) -> None: ...                           # drop weights; gc.collect(); empty_cache()

class StepProgress(Protocol):
    def __call__(self, fraction: float, message: str | None = None) -> None: ...
```
The role ABCs follow. They take and return files plus plain dataclasses only; no tensors cross the seam.
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
class Box2D:
    class_id: str; xyxy: tuple[float, float, float, float]; score: float; source: str; phrase: str
@dataclass(frozen=True, slots=True)
class Mask2D:
    class_id: str; path: Path; score: float; bbox: tuple[int, int, int, int]; source: str; instance: int
class Detector2D(ModelProvider):       # Grounding DINO: phrases → boxes
    @abstractmethod
    def detect(self, image: Path, phrases: Mapping[str, tuple[str, ...]], *, box_threshold: float,
               text_threshold: float, cancel: CancellationToken) -> list[Box2D]: ...
class Grounder2D(ModelProvider):       # Florence-2: caption, phrase grounding, referring segmentation
    @abstractmethod
    def caption(self, image: Path, cancel: CancellationToken) -> str: ...
    @abstractmethod
    def ground(self, image: Path, phrases: Mapping[str, tuple[str, ...]], cancel: CancellationToken) -> list[Box2D]: ...
class MaskRefiner(ModelProvider):      # SAM 2.1: boxes (+points) → masks
    @abstractmethod
    def refine(self, image: Path, boxes: Sequence[Box2D], out_dir: Path, cancel: CancellationToken) -> list[Mask2D]: ...
class Parser2D(ModelProvider):         # See-through l2d (anime), Sapiens2-seg (realistic): closed-set dense masks
    label_map: Mapping[str, str]       # source class → taxonomy class id (§9.4)
    @abstractmethod
    def parse(self, image: Path, out_dir: Path, cancel: CancellationToken) -> list[Mask2D]: ...

# inference/parts/base.py
@dataclass(frozen=True, slots=True)
class MeshArrays:
    vertices: np.ndarray; faces: np.ndarray; uv: np.ndarray | None; texture_png: Path | None   # glTF frame (Y-up)
@dataclass(frozen=True, slots=True)
class CameraFit:
    yaw_deg: float; pitch_deg: float; fov_deg: float; iou: float; accepted: bool; method: str
class LabelLifter(ModelProvider):      # nvdiffrast raster; no weights; approval_required=False
    @abstractmethod
    def fit_camera(self, mesh: MeshArrays, silhouette: np.ndarray, cancel: CancellationToken) -> CameraFit: ...
    @abstractmethod
    def face_ids(self, mesh: MeshArrays, camera: CameraFit, size: int) -> np.ndarray: ...       # int32 HxW, -1 = none
    @abstractmethod
    def render_views(self, mesh: MeshArrays, cameras: Sequence[CameraFit], size: int, out_dir: Path,
                     cancel: CancellationToken) -> list[tuple[Path, np.ndarray]]: ...     # (rgb png, face ids)
```

### 6.2 Catalog v1 (`inference/providers/catalog.py`). It follows `DECISIONS.md`; every entry is approved by default.

| id | kind / roles | repo_id @ revision | local_subdir (under `backend/.model/`) | size | VRAM peak [A] | licence | deps / vendored | provider key |
|---|---|---|---|---|---|---|---|---|
| `z_image_turbo` | text_to_image / generator | `Tongyi-MAI/Z-Image-Turbo` @ `f332072aa78be7aecdf3ee76d5c247082da564a6` | `t2i/Z-Image-Turbo` [V] | 32.85 GB [V] | 15 | Apache-2.0, allowed, not gated | `diffusers==0.39.0`, `accelerate==1.15.0` [V installed] | `zimage` |
| `grounding_dino_base` | segmentation / detector | `IDEA-Research/grounding-dino-base` @ `12bdfa3120f3e7ec7b434d90674b3396eccf88eb` [V manifest] | `seg/grounding-dino-base` | 0.93 GB | 2 | Apache-2.0 | `transformers==4.57.6` | `hf_grounding_dino` |
| `sam2_1_hiera_large` | segmentation / refiner | `facebook/sam2.1-hiera-large` @ `665f8e2ad61cf5f53d65644ff27c8ee525124610` | `seg/sam2.1-hiera-large` | 0.90 GB | 2 | Apache-2.0 | `transformers==4.57.6` (`Sam2Model`) | `hf_sam2` |
| `florence2_large` | segmentation / grounder | `florence-community/Florence-2-large` @ `4271c66b88cdbc05735372ec13b2360108de5317` | `seg/Florence-2-large` | 1.56 GB | 3 | MIT | `transformers==4.57.6` (`Florence2ForConditionalGeneration`) | `hf_florence2` |
| `see_through_l2d` | segmentation / parser (anime_character) | `24yearsold/l2d_sam_iter2` @ `0b0608310fb7a89e32ecd7397147a249540cf1e7` | `seg/l2d_sam_iter2` (`checkpoint-18000.pt`) | 1.29 GB | 4 | Apache-2.0 | vendored `third_party/see-through` (missing today [V]); `torch.load(weights_only=True)` | `vendored_see_through` |
| `sapiens2_seg_0_4b` | segmentation / parser (realistic_human) | `facebook/sapiens2-seg-0.4b` @ `449b3c5335e6722bb94990abdd1aa6e612432f22` | `seg/sapiens2-seg-0.4b` | 3.25 GB | 3 | Sapiens2 License, `allowed_with_conditions` (no biometric identification, deepfakes/impersonation or military use) | vendored `third_party/sapiens2` (missing today [V]) | `vendored_sapiens2` |
| `raster_lifter` | part_extraction / lifter | – | – | – | 4 | NVIDIA Source Code License (nvdiffrast), `non_commercial`. Already required by TRELLIS.2 export, so no new restriction | `nvdiffrast==0.4.0`, `cumesh==0.0.1` [V installed] | `nvdiffrast_raster` |

- Revisions are read from `backend/.model/seg/download_manifest.json` [V] (keyed by folder name) when present.
  Z-Image-Turbo's revision comes from `DECISIONS.md`.
- Not in the v1 catalog: FLUX.2-klein-4B, Animagine XL 4.0 and SAM 3 (not chosen, `DECISIONS.md` §1–2), and the
  non-commercial face and clothes parsers. Adding a model later means one `ModelSpec` plus one provider module.

### 6.3 State resolution (`inference/providers/registry.py`)
The first match wins. Results are cached for 5 s and invalidated on load, unload or error.
1. The master flag or the kind's flag is off → `unavailable / disabled`.
2. `approval_required` and the id is not in `effective_approved_models` → `unavailable / not_approved`.
   `effective_approved_models = settings.approved_models if set, else the ids with default_approved=True`.
3. `license.commercial ∈ {non_commercial, research_only, unknown}` and `approval_required` and not
   `allow_restricted_licenses` → `unavailable / license_blocked`. The lifter is exempt (`approval_required=False`).
4. `vendored_source` missing (no `__init__.py`/entry module) → `unavailable / vendored_source_missing`.
5. Any `DependencySpec` is absent or fails its version check (`importlib.metadata.version` +
   `packaging.specifiers`) → `unavailable / missing_dependency`. No torch import happens here.
6. `required_files` do not match under `model_paths.get(id) or aux_model_root/local_subdir` →
   `not_installed / not_installed`.
7. Compatibility probe: a text encoder `config.json` with `transformers_version >= 5` → `unavailable / incompatible`
   (the rule from the T2I research §2.1).
8. The provider instance reports `loading`, `loaded` or `error` → that state. Otherwise `installed / ok`.

`ProviderRegistry` API:
```python
class ProviderRegistry:
    def __init__(self, settings: Settings, catalog: Sequence[ModelSpec], *,
                 overrides: PartProviderOverrides | None = None) -> None
    def specs(self, kind: ModelKind | None = None) -> list[ModelSpec]
    def status(self, model_id: str) -> ProviderStatus
    def statuses(self, kind: ModelKind | None = None) -> list[ProviderStatus]
    def usable(self, model_id: str) -> bool                          # installed|loaded|loading
    def provider(self, model_id: str) -> ModelProvider                # lazy import; raises ModelUnavailableError
    def resolve_role(self, role: str, preferred: Sequence[str] = ()) -> ModelProvider | None
    def default_model(self, kind: ModelKind) -> str | None            # t2i_default_model or first usable
    def check_vram(self, model_id: str, *, engine_ready: bool) -> None   # raises CapabilityUnavailableError
    def idle_providers(self, now: datetime) -> list[ModelProvider]
```
Provider implementation rules (enforced by review and listed in each module docstring):
- `from_pretrained(local_dir, local_files_only=True)`. Never `trust_remote_code`, never `custom_pipeline`.
- `torch_dtype=torch.bfloat16` for Z-Image-Turbo with `num_inference_steps=9` and `guidance_scale=0.0`
  (`DECISIONS.md` §1).
- Cancellation through `callback_on_step_end`, which sets `pipe._interrupt = True` and raises `JobCancelledError`
  (T2I research §7).
- A private `torch.Generator(device).manual_seed(seed_i)` per candidate.
- Use `device = settings.device`. Weights go back to CPU in `finally`.
- Never write outside `output_dir`. See-through loads with `torch.load(path, weights_only=True)`.

---------------------------------------------------------------------------------------------------
## 7. Prompt-to-3D: image generation runs (`ig_`)

### 7.1 Flow
`POST /image-generations` (prompt) → `ig_` run on the GPU thread → N candidate PNGs →
`POST /image-generations/{ig}/candidates/{n}/use` → `ReferenceService.upload_image` (validation and thumbnail) →
reference set plus image → existing `POST /jobs`.

`POST /workflows` with `source.kind="prompt"` does all of this server-side (§10).

### 7.2 Request `ImageGenerationRequest` (`schemas/image_generation.py`, `RequestModel`, unknown fields → 422)
```json
{ "prompt": "a knight with a red cape and a sword",   // required; trimmed length 1..settings.prompt_max_chars (2000)
  "negative_prompt": null,                           // ≤1000; 422 unsupported_by_model if the model has no negative prompt
  "model_id": null,                                  // null → registry.default_model("text_to_image")
  "preset": "character",                             // "character" | "object" | "raw"
  "style": null,                                     // null | "realistic" | "stylized" | "anime"
  "part_hint": true,                                 // add the "parts clearly distinct" clause (T2I research §8.3)
  "num_images": 1,                                   // 1..4
  "seed": 0, "randomize_seed": true,                 // 0..2147483647
  "width": null, "height": null,                     // null → preset default; the pair must be in model.capabilities.sizes
  "steps": null,                                     // null → model default; within model bounds
  "guidance": null }                                 // null → model default; within model bounds
```
Z-Image-Turbo capabilities (from the catalog):
- `sizes`: `[[1024,1024],[896,1152],[1152,896]]`. Preset defaults: character 896×1152, object and raw 1024×1024.
- `steps`: default 9, range 1..50.
- `guidance`: default 0.0, min 0.0, max 0.0.
- `supports_negative_prompt`: false. `max_images`: 4.

Validation follows the existing `unsupported_by_engine` pattern: a 422 with
`details=[{"loc": ["body", field], "type": "unsupported_by_model", "ctx": {"allowed": …}}]`.

### 7.3 Prompt templates (`pipelines/prompt_templates.py`)
- `build_prompt(prompt, preset, style, part_hint) -> str` uses the character and object templates, style clauses,
  part-aware hint and light-subject background switch from `text_to_image_research.md` §8.1–8.3. `raw` passes the
  prompt through verbatim.
- The effective prompt is stored in `options.effective_prompt` and shown in the UI.
- Prompt nouns are matched against the taxonomy aliases (§9.4) by whole-word, case-insensitive matching. They are
  stored as `options.expected_terms` and later fed to segmentation (`use_prompt_terms`).

### 7.4 Steps
| id | label | weight | device | notes |
|---|---|---|---|---|
| `load_model` | Loading text-to-image model | 15 | gpu | `provider.load()`; skipped if already in RAM |
| `generate` | Generating images | 70 | gpu | step progress from `callback_on_step_end` |
| `check` | Checking 3D readiness | 10 | cpu-in-gpu-task | `processing/seg2d/readiness.py` (§7.5) |
| `save` | Saving candidates | 5 | cpu-in-gpu-task | PNG plus `_thumb.webp` (max side 384) |

### 7.5 Readiness check (torch-free; it produces warnings, never failures)
- The background colour is the median of a 2% border ring. The foreground is pixels with Lab ΔE above 12 from that
  colour, followed by a morphological open.
- Issues: `touches_border` (foreground bbox within 1% of the edge), `multiple_subjects` (more than one component
  above 2% area), `coverage_low` (< 8%), `coverage_high` (> 70%), `aspect_mismatch` (a character bbox wider than
  tall).
- Result: `readiness = {ok, issues[], method: "border_color"}`.

### 7.6 Result shape (`ImageGenerationOut`)
```json
{ "id": "ig_0123456789ab", "kind": "image_generation", "status": "completed",
  "created_at": "…Z", "started_at": "…Z", "finished_at": "…Z", "workflow_id": null,
  "options": { "prompt": "…", "effective_prompt": "A full-body 3D character reference image of …", "negative_prompt": null,
               "model_id": "z_image_turbo", "preset": "character", "style": null, "part_hint": true, "num_images": 2,
               "seed": 987654, "width": 896, "height": 1152, "steps": 9, "guidance": 0.0, "expected_terms": ["sword","cape"] },
  "models": [{"id": "z_image_turbo", "revision": "f332072aa78be7aecdf3ee76d5c247082da564a6", "license_id": "apache-2.0"}],
  "progress": {"percent": 100.0, "step_id": "save", "step_index": 4, "step_count": 4, "message": "Done", "updated_at": "…Z"},
  "steps": [{"id": "load_model", "label": "Loading text-to-image model", "status": "done", "message": null, "progress": 1.0, "duration_s": 6.1}],
  "warnings": [],
  "result": { "candidates": [
      { "index": 0, "seed": 987654, "width": 896, "height": 1152,
        "url": "/api/v1/image-generations/ig_0123456789ab/candidates/0",
        "thumbnail_url": "/api/v1/image-generations/ig_0123456789ab/candidates/0/thumbnail",
        "readiness": {"ok": true, "issues": [], "method": "border_color"},
        "used_as": [{"reference_set_id": "rs_…", "image_id": "img_…"}] } ],
      "timings": {"load_model": 6.1, "generate": 3.4, "check": 0.2, "save": 0.3, "total": 10.0} },
  "error": null }
```

### 7.7 "Use candidate"
`POST /image-generations/{ig}/candidates/{index}/use` with body
`{"reference_set_id": null, "slot": "front", "make_primary": true, "label": null}` (`RequestModel`).
1. Load the run. It must be `completed` (else 409). The index must be in range (else 404).
2. If `reference_set_id` is null, call `ReferenceService.create_set(name="Prompt: " + prompt[:60])`.
3. Call `upload_image(set_id, data=png_bytes, filename=f"{ig}_{index}.png", slot=slot,
   label=label or ("Prompt: " + prompt)[:200], make_primary=make_primary)`.
4. Append `{reference_set_id, image_id}` to `candidates[index].used_as` and persist the run.
5. Respond 201 `{"image": ReferenceImage, "set": ReferenceSet}`. This is the same body as the upload endpoint
   (contract §7.3).

---------------------------------------------------------------------------------------------------
## 8. Segmentation runs (`sg_`): decomposition before or after reconstruction

### 8.1 Request `SegmentationRequest`
```json
{ "source": {"kind": "reference_image", "reference_set_id": "rs_…", "image_id": "img_…"}   // before reconstruction
          | {"kind": "job", "job_id": "job_…"},                                          // after: the job's conditioning image
  "options": {
    "profile": "auto",              // "auto" | "baseline" | "anime" | "realistic"
    "subject": "auto",              // "auto" | "anime_character" | "realistic_human" | "object"
    "classes": null,                // null = whole taxonomy; else a subset of taxonomy ids (422 for unknown ids)
    "extra_terms": [],              // ≤16 free nouns, each ≤40 chars → object.held / accessory.* via aliases, else object.detached
    "use_prompt_terms": true,       // add expected_terms of the ig_ run that produced the image (if any)
    "head_pass": true,              // second high-resolution pass on the head crop taken from the ORIGINAL image
    "min_confidence": 0.35 } }      // 0.05..0.95
```
- At creation the source image is **snapshotted** into `runtime/segmentations/<sg>/source.png`. For a reference
  image this is the original file. For a job it is `inputs/<primary>.png` plus `preview.png`. Later edits to the
  set or job do not affect the run.
- A job source must be `completed` (409 otherwise).

### 8.2 Conditioning frame recovery (`processing/seg2d/frame.py`, torch-free, no adapter change)
TRELLIS discards the crop box and alpha (`generation.py:137-178`). The frame is recovered **outside** the adapter:
```python
@dataclass(frozen=True, slots=True)
class ConditioningFrame:
    mode: Literal["alpha_crop", "background_removed", "untouched", "predicted"]
    method: Literal["recompute", "template_match", "identity"]
    scale: float                                  # original → downscaled (≤1024 long side)
    crop_box: tuple[int, int, int, int]           # in downscaled pixels; may extend outside (black padding)
    size: int                                     # square side == preview size
    verified: bool; residual: float               # mean |Δ| over foreground (uint8 units)

def predict_frame(original: Image.Image, remove_background: bool) -> ConditioningFrame        # before reconstruction
def recover_frame(original: Image.Image, preview: Image.Image, preprocess_mode: str) -> ConditioningFrame
def to_conditioning(mask: np.ndarray, frame: ConditioningFrame) -> np.ndarray               # original → conditioning
def to_original(mask: np.ndarray, frame: ConditioningFrame, original_size: tuple[int, int]) -> np.ndarray
```
- **Downscale:** the long side goes to ≤1024 with the same resampling as upstream (`trellis2_image_to_3d.py:137-140`).
  S6 pins this with a parity test against the upstream `preprocess_image` alpha path (`test/engine/`, requires
  torch).
- **`alpha_crop`:** bbox of `alpha > 0.8·255`, side = `max(w, h)`, centred, no margin, premultiplied on black. The
  result is verified against `preview.png` (`residual < 2.0` → `verified`).
- **`background_removed`:** the matte is unknown, so `cv2.matchTemplate(orig_padded, preview, TM_SQDIFF,
  mask=preview_luma > 8)` runs on the downscaled original padded by `size` on each side. `verified` requires
  `residual < 6.0`. The foreground mask is `preview_luma > 8` (`mask_method: "luma"`).
- **`untouched`:** identity at `scale`.
- **Before reconstruction (`predict_frame`):** if the source has a real alpha channel the frame is exact
  (`alpha_crop`, `predicted`). Otherwise masks stay in original space and the part analysis maps them later with
  `recover_frame`.
- If verification fails, a warning (`frame_unverified`) is added and the run continues in original space. Nothing
  is silently mis-registered.

### 8.3 Profiles and roles (composition over the decided models)
| profile | parser | detector | grounder | refiner | used when |
|---|---|---|---|---|---|
| `baseline` | – | `grounding_dino_base` | `florence2_large` (extra and prompt terms) | `sam2_1_hiera_large` | objects; or when a parser is unavailable |
| `anime` | `see_through_l2d` (dense 19 classes) | GDINO for small or rare classes (eyes, brows, jewelry, objects) | Florence-2 | SAM 2.1 | `subject = anime_character` |
| `realistic` | `sapiens2_seg_0_4b` (29 classes; person-relative L/R) | GDINO for eyes, brows, accessories | Florence-2 | SAM 2.1 | `subject = realistic_human` |

- `profile: auto` maps the subject to a profile.
- `subject: auto` asks Florence-2 `<CAPTION>` and applies deterministic keyword rules: `anime|manga|cartoon|chibi|
  illustration|drawing` → `anime_character`; `person|man|woman|boy|girl|photo` → `realistic_human`; anything else →
  `object`. If Florence-2 is unavailable the subject is `object`, so the profile is `baseline`.
- **Degradation:** when a role's model is not usable, that role is skipped with a warning such as
  `"see_through_l2d unavailable (vendored_source_missing); using baseline"`.
  - The run fails with 503 `capability_unavailable` at creation only if **no** detector+refiner pair and no parser
    is usable.
- Steps:

  | id | weight | device | work |
  |---|---|---|---|
  | `prepare` | 5 | cpu | snapshot, downscale |
  | `frame` | 5 | cpu | §8.2 |
  | `parse` | 25 | gpu | parser; skipped if none |
  | `detect` | 25 | gpu | GDINO + Florence-2 on the full image, then on the head crop when `head_pass` |
  | `refine` | 30 | gpu | SAM 2.1 per box |
  | `fuse` | 10 | cpu | §8.4 |

### 8.4 Fusion and eye heuristics (`processing/seg2d/fusion.py`, `processing/seg2d/eyes.py`, numpy only)
- Source labels are mapped to taxonomy ids with the tables in `segmentation_models_research.md` §7.2 (See-through
  V2 19 classes, Sapiens2 29 classes, GDINO/Florence vocabulary). The tables are encoded as `Parser2D.label_map`
  plus `parts/taxonomy.ALIASES`.
- **z-priority (front-most first):** `object.* > accessory.* > hair (front) > accessory.eyewear > eyelash > eyebrow >
  eye > clothing.outer > clothing.* > hair (back) > body regions`.
  - Dense classes prefer the parser.
  - Small or rare classes prefer detector+refiner.
- Gates: per-pixel argmax over soft masks; `min_confidence`; a minimum area of 0.02% of the frame → `unknown`.
- Eyes are split by connected components sorted by x. The character's left is image-right for a front view.
  `side` is character-relative.
- Eyelash band: the upper-lid curve is the Savitzky–Golay-smoothed per-column top boundary of the eye mask. The band
  runs from −0.35·h to +0.12·h around it, extends 15% past the outer canthus, and is intersected with dark pixels
  (Otsu on L* inside the eye bbox). The output is the eyelash mask plus `annotations["eyelash.left"] =
  {"lid_curve": [[x, y], …], "method": "heuristic", "confidence": …}`.
- Eyebrows: GDINO "eyebrow" boxes refined by SAM. Fallback: a dark band above the eye mask, with `confidence ≤ 0.4`.

### 8.5 Artifacts and result
```
runtime/segmentations/<sg>/run.json  source.png  [source_preview.png]  frame.json
                           labels.png (uint16, value = taxonomy label value)  overlay.png (RGBA, ≤1024)
                           masks/<value>.png (8-bit soft mask)  segmentation.json (= result payload)
```
`SegmentationOut.result`:
```json
{ "space": "original" | "conditioning",
  "frame": {"mode": "alpha_crop", "method": "recompute", "scale": 0.5, "crop_box": [112, 40, 912, 840], "size": 800,
            "verified": true, "residual": 0.7} | null,
  "subject": "anime_character", "profile": "anime",
  "labels": [{"value": 30, "category": "hair", "group": "hair", "area_px": 51234, "confidence": 0.86,
              "bbox": [130, 42, 870, 610], "sources": ["see_through_l2d"],
              "mask_url": "/api/v1/segmentations/sg_…/masks/30"}],
  "annotations": {"eyelash.left": {"lid_curve": [[401, 300], [430, 292]], "method": "heuristic", "confidence": 0.6}},
  "expected_terms_missing": ["sword"],
  "overlay_url": "/api/v1/segmentations/sg_…/overlay", "labels_url": "/api/v1/segmentations/sg_…/labels",
  "timings": {"parse": 0.4, "detect": 1.1, "refine": 0.6, "fuse": 0.1, "total": 2.6} }
```

---------------------------------------------------------------------------------------------------
## 9. Part analysis runs (`pa_`): decomposition after reconstruction

### 9.1 Strategies
| id | needs | output | default when |
|---|---|---|---|
| `components` | nothing (CPU, trimesh/scipy) | Welded connected components. The largest by area is `body`; the others are `unknown` physical shells (`component_###`). | segmentation not usable |
| `semantic_regions` | segmentation + lifter | Semantic labels on every face. **No cuts**: one mesh with region ids. | – |
| `semantic_parts` | segmentation + lifter | Semantic labels, **physical cuts** for the selected groups, regions for the rest (`DECISIONS.md` §3) | segmentation and lifter usable |
| `per_part_regeneration` | – | **Reserved.** Reported `available:false, code:"not_implemented"` (Tripo-style per-part generation; deferred D2) | never |

### 9.2 Request `PartAnalysisOptions` (also used by workflows)
```json
{ "strategy": null,                                   // null → /features default_strategy
  "segmentation_id": null,                            // reuse a completed sg_ run (snapshot-copied into the analysis)
  "segmentation": null,                               // SegmentationOptions for an internal pass when segmentation_id is null
  "separate": ["eyes","eyebrows","eyelashes","hair","clothes","accessories","detachable"],   // groups that may be physical
  "views": 8,                                         // extra multi-view segmentation renders 0..24 (0 = input view only)
  "min_part_faces": 200,                              // 0..100000
  "export": {"assembled_glb": true, "per_part_glbs": false, "face_labels": true, "masks": true,
             "previews": true, "zip": false} }
```
- 422 for unknown groups, `views` out of range, or a `segmentation_id` that is not `completed` (409) or not found
  (404).
- 503 `capability_unavailable` if the strategy needs segmentation or the lifter and they are not usable.

### 9.3 Steps (unused steps are pre-marked `skipped`)
| id | label | w | device | `components` | `semantic_*` |
|---|---|---|---|---|---|
| `prepare` | Preparing inputs | 4 | cpu | sha256 of `model.glb`; load mesh; weld | same, plus copy the segmentation snapshot |
| `segmentation_2d` | Segmenting image | 14 | gpu | skipped | internal pass (§8 executor functions) or skipped (reused) |
| `camera_fit` | Aligning camera | 8 | gpu | skipped | silhouette grid fit (§9.5) |
| `label_lift` | Lifting labels to 3D | 26 | gpu | skipped | face-ID votes, multi-view, propagation |
| `components` | Analysing components | 10 | cpu | connected components → labels | component snapping |
| `partition` | Separating parts | 18 | cpu | partition | policy, cuts, seams, regions |
| `export` | Exporting parts | 20 | cpu | GLB, manifest, sidecars | plus previews (GPU render via lifter when `export.previews`) |

`needs_gpu = strategy != "components"`.

### 9.4 Taxonomy v1 (`parts/taxonomy.py`: data only, owned by S1, frozen after S1)
Label values are stable integers stored in `face_labels.u16`, `labels.png` and `_FEATURE_ID_0`. Groups match the
UI groups.

| value | id | group | default kind | attach_to | symmetric_with |
|---|---|---|---|---|---|
| 0 | `unknown` | other | region | body | – |
| 1 | `body` | body | base (host mesh) | – | – |
| 2 | `body.head` | body | region | body | – |
| 3 | `body.head.face` | face | region | body.head | – |
| 4 / 5 | `body.head.ear.left` / `.right` | face | region | body.head | each other |
| 6 | `body.head.nose` | face | region | body.head.face | – |
| 7 | `body.head.mouth` | face | region | body.head.face | – |
| 8 | `body.neck` | body | region | body | – |
| 9 | `body.torso` | body | region | body | – |
| 10 / 11 | `body.arm.left` / `.right` | body | region | body.torso | each other |
| 12 / 13 | `body.hand.left` / `.right` | body | region | body.arm.* | each other |
| 14 / 15 | `body.leg.left` / `.right` | body | region | body.torso | each other |
| 16 / 17 | `body.foot.left` / `.right` | body | region | body.leg.* | each other |
| 18 | `body.skin_other` | body | region | body | – |
| 20 / 21 | `eye.left` / `eye.right` | eyes | **physical** (cut eye area) | body.head.face | each other |
| 22 / 23 | `eyebrow.left` / `.right` | eyebrows | **physical_deferred** | body.head.face | each other |
| 24 / 25 | `eyelash.left` / `.right` | eyelashes | **physical_deferred** | eye.<side> | each other |
| 30 | `hair` | hair | physical | body.head | – |
| 31 | `hair.facial` | hair | physical | body.head.face | – |
| 40–47 | `clothing.top`, `.outer`, `.bottom`, `.dress`, `.legwear`, `.handwear`, `.footwear`, `.neckwear` | clothes | physical | body.torso / limbs / neck | – |
| 50–58 | `accessory.headwear`, `.eyewear`, `.earwear`, `.jewelry`, `.belt`, `.bag`, `appendage.tail`, `appendage.wings`, `appendage.animal_ears` | accessories | physical | head / face / ear / neck / torso | – |
| 60 / 61 | `object.held` / `object.detached` | detachable | physical | body.hand.* (held_by) / none | – |

`ALIASES` maps class ids to prompt phrases (for example `hair`: "hair, bangs, ponytail, braid, twin tails, ahoge").
It uses the vocabulary of `segmentation_models_research.md` §7.2.

### 9.5 Algorithm (semantic strategies; components only from step 5)
1. **Mesh:** `trimesh.load(model.glb, force="mesh", process=False)` keeps face order, which is the order of
   primitive 0. Weld positions (rounded 1e-6) give `weld_map: uint32[V]` and the welded face adjacency
   (`parts/weld.py`).
2. **2D labels in the conditioning frame:**
   - Reused or internal segmentation.
   - If the space is `original`, recover the frame (§8.2) with `job.result.metadata["preprocess"]`, `preview.png`
     and `inputs/<primary>.png`, then warp the label map.
   - The conditioning alpha comes from `labels > 0 ∪ (preview_luma > 8)`.
3. **Camera fit** (`LabelLifter.fit_camera`, GPU):
   - Silhouettes at 256² over yaw 0–350° in 10° steps, pitch −20…40° in 10° steps and FOV {10, 30, 50}.
   - Each silhouette gets the tight-bbox square crop (§8.2). Score = IoU against the conditioning alpha. The top 5
     are refined by ±5° in 1° steps.
   - The fit is accepted if `iou ≥ 0.6`. Otherwise the front view (camera on glTF +Z) is used with
     `accepted=false`, a warning `camera_fit_low_iou`, and every lifted confidence multiplied by 0.5.
4. **Votes:**
   - Face-ID render at the conditioning size. A pixel with label k and confidence c adds `c·|cos θ|` to
     `votes[f, k]`.
   - When `views > 0`: render textured views (yaw ring at pitch 0 and 30), run the same segmentation profile per
     view, and add votes with weight 0.5.
   - Labels are per face of `model.glb`; `model.glb` has ~2M faces, which is well below nvdiffrast's 2²⁴ ID limit.
5. **Assignment:**
   - `argmax` where total votes ≥ 0.2.
   - Unvoted faces (back faces, the inner wall of the remeshed double shell) take the label of the nearest voted
     face centroid within `2.5/effective_resolution` (`scipy.spatial.cKDTree`).
   - Then two passes of majority smoothing over welded face adjacency.
   - Remaining faces → `unknown`.
   - Component snapping: a welded component where ≥90% of faces share one label gets that label (separate shells
     such as glasses and earrings).
   - Labelled islands smaller than `min_part_faces` merge into the surrounding majority label.
6. **Policy** (`parts/policy.py`):
   ```python
   def decide_kind(class_id: str, group: str, separate: set[str], faces: int, min_part_faces: int) -> Kind:
       if group in {"body", "face", "other"}: return "region"           # base host is "base"
       if group not in separate: return "region"
       if TAXONOMY[class_id].default_kind == "physical_deferred": return "physical_deferred"
       if faces < max(min_part_faces, 1): return "region"                # + warning "too_small_to_separate"
       return "physical"
   ```
   Eyes are `physical` (`DECISIONS.md` §3). Eyebrows and eyelashes are always `physical_deferred`: their faces stay
   in the body with region values 22–25, and their mask and lid curve go into the manifest.
7. **Partition** (`parts/partition.py`, `parts/seams.py`):
   - Each physical part becomes `glb.submesh([face_idx], append=True)` (UVs and material are kept).
   - Every submesh gets the **same** `PBRMaterial` object, so the GLB holds one material and one set of images.
   - Seam loops are welded edges with exactly one incident face in the part. They are chained into ordered loops
     (`uint32` weld ids). Each part records `open_boundary`.
   - The host `body` is all faces not in physical parts. It carries `_FEATURE_ID_0` (float32 per vertex). Vertices
     are duplicated only where two region labels meet, so per-face values stay exact.
   - No hole filling, remeshing or smoothing (that would be tuning).
8. **Reassembly check** (`parts/reassembly.py`):
   - The face partition covers every original face exactly once.
   - Each part face's welded positions equal the original's (max error 0.0).
   - The result is recorded as `manifest.reassembly.verified`. On failure the run fails with `partition_failed`.
9. **Export** (`parts/gltf_export.py`): see §9.7. Previews are front, back, left and right renders with labels
   coloured (lifter, GPU) when `export.previews` is set.

`components` strategy: step 1 → welded connected components (`scipy.sparse.csgraph.connected_components`) → the
largest by surface area is `body` (base) → the rest are `component_001…` (`unknown`, `physical`, `open_boundary`
from their own boundary edges) → steps 7–9.

### 9.6 Artifacts (`jobs/<job>/parts/<pa>/`, moved from `runtime/temp/parts/<pa>/` after a `job_exists` re-check)
```
run.json                 PartAnalysis record
manifest.json            img3d.parts/1 (§9.8)
model_parts.glb          assembled multi-node GLB (if export.assembled_glb)
face_labels.u16          little-endian uint16, one per face of model.glb primitive 0, in index order
face_confidence.u8       0..255 (semantic strategies)
weld_map.u32             model.glb vertex → welded id
seams/<seam_id>_<k>.u32  ordered welded-vertex loops
parts/<part_id>.glb      per-part GLBs (if export.per_part_glbs; each embeds the shared atlas, about +model size each)
masks/<value>.png        snapshot of the 2D masks (conditioning frame)
segmentation/labels.png overlay.png frame.json segmentation.json   (snapshot or internal pass)
previews/<view>.png      front|back|left|right label renders (if export.previews)
bundle.zip               manifest + GLBs + sidecars + masks (if export.zip)
log.txt                  run log tail (kept on failure)
```
`model.glb` is only read. I2 is tested by hashing it before and after.

### 9.7 Assembled GLB layout (`model_parts.glb`)
```
scene "img3d_parts"  extras.img3d = {"schema": "img3d.parts/1", "analysis_id": "pa_…", "job_id": "job_…",
                                     "region_attribute": "_FEATURE_ID_0"}
└─ node "img3d_asset" (identity)
   ├─ node "part:body"            mesh "body"   POSITION NORMAL TEXCOORD_0 _FEATURE_ID_0
   │                              extras.img3d = {"id": "body", "kind": "base", "regions": [{"value": 3, "category": "body.head.face"}, …]}
   ├─ node "part:hair"            extras.img3d = {"id": "hair", "category": "hair", "group": "hair", "kind": "physical",
   │                                              "attach_to": "body.head", "seams": ["s1"]}
   ├─ node "part:eye.left"        …
   └─ node "part:clothing.top"    …
materials: 1 (shared PBR, doubleSided=false as in model.glb)   images: 2 (WebP when settings.glb_webp_textures)
```
- All transforms are identity, in the same frame as `model.glb` (glTF Y-up, unit cube `[-0.5, 0.5]³`).
- Deferred parts have no node. They exist as regions on `part:body` plus manifest records.
- The `EXT_mesh_features` declaration is **not** emitted in v1, because trimesh `extensionsUsed` bookkeeping is
  unverified [A]. `_FEATURE_ID_0` plus `extras` carry the data.

### 9.8 Manifest `img3d.parts/1` (`parts/manifest.py`: pydantic, frozen in S1)
The field names follow the frontend parser expectations (`frontend_b_command_engine.md` §4.2).
```json
{ "schema": "img3d.parts/1",
  "analysis_id": "pa_…", "job_id": "job_…", "created_at": "…Z",
  "generator": {"app_version": "1.0.0", "engine": "trellis2", "seed": 123456, "resolution": 1536, "effective_resolution": 1536},
  "source": {"model_file": "model.glb", "model_sha256": "…", "vertices": 1697772, "faces": 1895691,
             "primary_image_id": "img_…", "origin": {"kind": "image" | "prompt", "image_generation_id": "ig_…" | null}},
  "frame": {"space": "gltf", "up": "+Y", "front": "+Z", "aabb": [[-0.5,-0.5,-0.5],[0.5,0.5,0.5]]},
  "strategy": {"id": "semantic_parts", "segmentation_id": "sg_…" | null, "profile": "anime", "subject": "anime_character",
               "models": [{"id": "see_through_l2d", "revision": "0b06…", "license_id": "apache-2.0"}], "options": {}},
  "camera": {"yaw_deg": 180.0, "pitch_deg": 0.0, "fov_deg": 30.0, "iou": 0.91, "accepted": true, "method": "silhouette_grid"} | null,
  "taxonomy_version": 1,
  "region_attribute": "_FEATURE_ID_0",
  "label_table": [{"value": 0, "category": "unknown"}, {"value": 1, "category": "body"}, {"value": 30, "category": "hair"}],
  "parts": [
    { "id": "hair", "category": "hair", "group": "hair", "label": "Hair",
      "kind": "physical",                          // "base" | "physical" | "physical_deferred" | "region"
      "parent": "body", "attach_to": "body.head", "side": null, "symmetric_with": null,
      "node_name": "part:hair", "host": null,
      "region": {"attribute": "_FEATURE_ID_0", "value": 30, "face_count": 210334},
      "files": {"glb": "parts/hair.glb" | null, "mask": "masks/30.png" | null},
      "stats": {"faces": 210334, "vertices": 120345, "area": 0.12, "bbox_min": [-0.3,0.1,-0.2], "bbox_max": [0.3,0.5,0.2]},
      "geometry": {"open_boundary": true, "watertight": false, "occluded_geometry_missing": true,
                   "source": "lifted" | "component" | "none"},
      "seams": ["s1"],
      "annotations": {},                           // e.g. eyelash: {"lid_curve": [[x,y],…], "space": "conditioning"}
      "confidence": 0.81, "provenance": {"sources": ["see_through_l2d", "sam2_1_hiera_large"], "votes": 51234},
      "refinement": {"eligible": true, "status": "untouched", "locks": {"seam": true, "uv": true}} } ],
  "seams": [{"id": "s1", "parts": ["hair", "body"], "loops": [{"file": "seams/s1_0.u32", "closed": true, "edge_count": 812, "length": 1.84}]}],
  "relations": [{"type": "attached_to", "a": "hair", "b": "body", "via": "s1"},
                {"type": "layered_over", "a": "clothing.top", "b": "body", "via": null},
                {"type": "symmetric_with", "a": "eye.left", "b": "eye.right", "via": null},
                {"type": "held_by", "a": "object.held", "b": "body.hand.right", "via": null},
                {"type": "region_of", "a": "eyebrow.left", "b": "body", "via": null}],
  "artifacts": {"assembled_glb": "model_parts.glb", "face_labels": {"file": "face_labels.u16", "dtype": "uint16", "count": 1895691},
                "face_confidence": "face_confidence.u8", "weld_map": {"file": "weld_map.u32", "dtype": "uint32", "count": 1697772},
                "segmentation": {"labels": "segmentation/labels.png", "overlay": "segmentation/overlay.png", "frame": "segmentation/frame.json"},
                "previews": ["previews/front.png"], "zip": null},
  "reassembly": {"method": "concatenate_and_weld", "verified": true, "max_position_error": 0.0},
  "summary": {"part_count": 9, "physical_count": 6, "deferred_count": 4, "region_count": 12, "unlabelled_face_ratio": 0.02},
  "warnings": [] }
```
Validation (`PartsManifest.model_validate` plus `validate_manifest_paths(manifest, run_dir)`):
- Ids match `^[a-z][a-z0-9_]*(\.[a-z0-9_]+){0,5}$`, at most 80 chars, and are unique.
- Every `files.*`, `seams[].loops[].file` and `artifacts.*` path is relative, has no `..`, and resolves inside the
  run directory.
- Region values exist in `label_table`.
- `refinement.status` is always `"untouched"` in this milestone.

### 9.9 `PartAnalysisOut`
```json
{ "id": "pa_…", "kind": "part_analysis", "job_id": "job_…", "workflow_id": null, "status": "completed",
  "created_at": "…Z", "started_at": "…Z", "finished_at": "…Z",
  "options": { /* effective PartAnalysisOptions incl. resolved strategy */ },
  "models": [ … ], "progress": { … }, "steps": [ … ], "warnings": [],
  "result": {
    "status": "completed",                                    // "completed" | "partial" (unverified frame / low IoU)
    "manifest_url": "/api/v1/part-analyses/pa_…/manifest",
    "assembled_url": "/api/v1/part-analyses/pa_…/model.glb",
    "download_url": "/api/v1/part-analyses/pa_…/export/glb",
    "zip_url": null,
    "overlay_url": "/api/v1/part-analyses/pa_…/segmentation/overlay.png",
    "face_labels_url": "/api/v1/part-analyses/pa_…/face-labels",
    "preview_urls": {"front": "/api/v1/part-analyses/pa_…/previews/front.png"},
    "part_count": 9, "physical_count": 6, "deferred_count": 4, "region_count": 12,
    "camera_fit_iou": 0.91, "unlabelled_face_ratio": 0.02,
    "parts": [{"id": "hair", "label": "Hair", "group": "hair", "kind": "physical", "node_name": "part:hair",
               "region_value": 30, "faces": 210334, "confidence": 0.81,
               "model_url": "/api/v1/part-analyses/pa_…/parts/hair.glb" | null,
               "mask_url": "/api/v1/part-analyses/pa_…/masks/30" | null}],
    "stats": { /* MeshStats of model_parts.glb */ },
    "timings": {"prepare": 2.1, "segmentation_2d": 3.0, "camera_fit": 1.8, "label_lift": 6.4, "components": 1.2,
                "partition": 4.9, "export": 9.7, "total": 29.1} } | null,
  "error": null }
```

---------------------------------------------------------------------------------------------------
## 10. Workflows (`wf_`): server-side chaining (the Node-UI run)

### 10.1 Request `WorkflowRequest` (`schemas/workflows.py`)
```json
{ "name": null,                                              // ≤120 chars
  "source": {"kind": "image", "reference_set_id": "rs_…", "primary_image_id": null}
          | {"kind": "prompt", "generation": ImageGenerationRequest, "selection": "auto_first" | "manual",
             "reference_set_id": null},
  "segmentation": null | SegmentationOptions,               // non-null → segmentation BEFORE reconstruction (sg_ on the imported/selected image)
  "reconstruction": {"settings": GenerationSettings},       // the existing schema, imported unchanged from schemas/jobs.py
  "parts": null | PartAnalysisOptions,                      // non-null → part analysis AFTER reconstruction (reuses the sg_ above)
  "export": {"assembled_glb": true, "per_part_glbs": false, "zip": false},
  "graph": null | { … } }                                   // opaque Node-UI graph JSON, ≤ settings.workflow_graph_max_bytes (65536) when serialized
```
Validation at creation (all-or-nothing; no child is created on failure):
- The source exists: set and image 404, empty set 422.
- `validate_against_capabilities` for `reconstruction.settings`, reusing the public function
  `services/generation_settings.py` (read only).
- T2I is usable for a prompt source (503).
- Segmentation and lifter are usable for semantic strategies (503).
- Flags are on (503 `feature_disabled`).
- Graph size (422 `graph_too_large`).

### 10.2 Steps (`node` = Node-UI node type)
| step id | node | weight | child resource | created when |
|---|---|---|---|---|
| `text_to_image` | `prompt` | 6 | `ig_` | immediately (prompt source) |
| `import_image` | `input_image` | 1 | reference image (`img_`, `rs_`) | `ig_` completed and a candidate was chosen (`auto_first` → index 0; `manual` → `POST /workflows/{wf}/select-candidate`). For an image source it is `done` at creation (the existing image) |
| `segmentation` | `segmentation` | 4 | `sg_` (reference_image source) | after `import_image`, if `segmentation` is non-null |
| `reconstruction` | `image_to_3d` | 70 | `job_` via `JobService.create_job` | after `segmentation` is done or skipped |
| `part_extraction` | `part_extraction` | 17 | `pa_` with `segmentation_id` of the step above | job completed, if `parts` is non-null |
| `export` | `export` | 2 | none (records URLs) | after the last producing step |

Step status values are `pending | waiting | awaiting_input | running | done | failed | skipped | cancelled`.
`waiting` means the child queue is full (429): the step retries on every sweep with the message
`"Generation queue is full; retrying"`.

### 10.3 State machine
```
queued ─► running (first child created / first step done) ─► completed (all steps done|skipped)
running ─► failed     (a child failed, or a non-retryable create error; error.code = "step_failed",
                       details = {step, child_kind, child_id, child_error})
queued|running ─► cancelled   (POST cancel: cancel the active child via its service; pending steps → skipped)
startup: queued|running ─► failed (interrupted); children are recovered by their own stores
```
`WorkflowService.advance(wf_id)` is idempotent:
1. Take the per-workflow `asyncio.Lock`.
2. Read the state of each child from its service.
3. Map child states onto steps.
4. Create the next child when its predecessors are `done`.
5. Persist atomically and publish `workflow.updated`.

Triggers:
- (a) `EventBus.subscribe(predicate)` for `image_generation.updated`, `segmentation.updated`, `job.updated` and
  `part_analysis.updated` events whose id is in the active-children index.
- (b) A reconciliation sweep every `workflow_sweep_interval_s` (5 s) over active workflows only. This protects
  against dropped events on a full subscriber queue (`core/events.py:61-79`).
- (c) Once at startup.

Delete semantics:
- `DELETE /workflows/{wf}` removes only the workflow record. It returns 409 while active.
- Children stay as normal history (jobs, analyses, generations), with their own delete endpoints.
- `WorkflowOut` computes `steps[].resource.exists` at read time, so deleted children show as missing.

### 10.4 `WorkflowOut`
```json
{ "id": "wf_…", "name": null, "status": "running", "created_at": "…Z", "started_at": "…Z", "finished_at": null,
  "request": { /* normalized WorkflowRequest without graph */ }, "graph_stored": true,
  "current_step": "reconstruction",
  "progress": {"percent": 41.0, "message": "Generating geometry", "updated_at": "…Z"},
  "steps": [
    {"id": "text_to_image", "node": "prompt", "status": "done", "message": null, "progress": 100.0,
     "resource": {"kind": "image_generation", "id": "ig_…", "exists": true}},
    {"id": "import_image", "node": "input_image", "status": "done",
     "resource": {"kind": "reference_image", "id": "img_…", "reference_set_id": "rs_…", "exists": true}},
    {"id": "segmentation", "node": "segmentation", "status": "done", "resource": {"kind": "segmentation", "id": "sg_…", "exists": true}},
    {"id": "reconstruction", "node": "image_to_3d", "status": "running", "progress": 38.5,
     "resource": {"kind": "job", "id": "job_…", "exists": true}},
    {"id": "part_extraction", "node": "part_extraction", "status": "pending", "resource": null},
    {"id": "export", "node": "export", "status": "pending", "resource": null} ],
  "outputs": {"reference_set_id": "rs_…", "image_id": "img_…", "segmentation_id": "sg_…", "job_id": "job_…",
              "part_analysis_id": null, "model_url": null, "parts_model_url": null, "manifest_url": null, "zip_url": null},
  "error": null }
```
Workflow percent = Σ (step weight × child percent) / Σ weights of the steps present. Child percent is
`progress.percent` of the `ig_`/`sg_`/`pa_` run or of the job, and 100 for done or skipped steps.

---------------------------------------------------------------------------------------------------
## 11. Discovery: `GET /api/v1/features`

The service is torch-free. It is computed from settings, `engine.status()`, `ProviderRegistry` (cached 5 s) and
`RunWorker`/`run_queue`. The shape is a superset of what `frontend_b_command_engine.md` §4.2 expects.
```json
{ "schema": "img3d.features/1",
  "api": {"version": "1.1.0", "workflows": true, "part_analyses": true, "image_generations": true, "segmentations": true},
  "flags": {"part_aware": true, "image_generation": true, "segmentation": true, "part_analysis": true, "workflows": true,
            "mesh_tuning": false, "part_refinement": false},
  "pipeline": {
    "image_to_3d": {"available": true, "code": "ok", "reason": null},
    "prompt_to_3d": {"available": true, "code": "ok", "reason": null,
                     "max_prompt_chars": 2000, "presets": ["character","object","raw"], "styles": ["realistic","stylized","anime"],
                     "supports_negative_prompt": false, "default_model_id": "z_image_turbo", "max_images": 4,
                     "sizes": [[1024,1024],[896,1152],[1152,896]], "selection_modes": ["auto_first","manual"]},
    "segmentation": {"available": true, "code": "ok", "reason": null,
                     "subjects": ["auto","anime_character","realistic_human","object"],
                     "profiles": [{"id": "baseline", "available": true, "code": "ok", "reason": null},
                                  {"id": "anime", "available": false, "code": "vendored_source_missing",
                                   "reason": "See-through code is not vendored under backend/third_party/see-through"},
                                  {"id": "realistic", "available": false, "code": "vendored_source_missing", "reason": "…"}],
                     "default_profile": "auto", "max_extra_terms": 16, "before_reconstruction": true},
    "part_extraction": {"available": true, "code": "ok", "reason": null,
                        "strategies": [{"id": "components", "label": "Geometry components", "available": true, "requires_segmentation": false},
                                       {"id": "semantic_regions", "label": "Semantic regions", "available": true, "requires_segmentation": true},
                                       {"id": "semantic_parts", "label": "Semantic parts", "available": true, "requires_segmentation": true},
                                       {"id": "per_part_regeneration", "label": "Per-part regeneration", "available": false,
                                        "code": "not_implemented", "requires_segmentation": true}],
                        "default_strategy": "semantic_parts",
                        "separable_groups": [{"id": "eyes", "label": "Eyes", "default_kind": "physical", "default_selected": true, "note": "Eye area is cut as an open mesh"},
                                             {"id": "eyebrows", "label": "Eyebrows", "default_kind": "physical_deferred", "default_selected": true, "note": "Geometry deferred; mask + surface region"},
                                             {"id": "eyelashes", "label": "Eyelashes", "default_kind": "physical_deferred", "default_selected": true, "note": "Geometry deferred; mask + lid curve"},
                                             {"id": "hair", "label": "Hair", "default_kind": "physical", "default_selected": true, "note": null},
                                             {"id": "clothes", "label": "Clothes", "default_kind": "physical", "default_selected": true, "note": null},
                                             {"id": "accessories", "label": "Accessories", "default_kind": "physical", "default_selected": true, "note": null},
                                             {"id": "detachable", "label": "Detachable objects", "default_kind": "physical", "default_selected": true, "note": null}],
                        "region_groups": [{"id": "face", "label": "Face", "default_kind": "region", "default_selected": true, "note": null},
                                          {"id": "body", "label": "Body", "default_kind": "region", "default_selected": true, "note": null}],
                        "max_views": 24, "rebuild": true} },
  "mesh_tuning": {"enabled": false, "reason": "Paused until the part-aware backend layer is approved"},
  "part_refinement": {"enabled": false, "reason": "Paused until the part-aware backend layer is approved"},
  "models": [ /* ModelOut summaries (§12.2) */ ],
  "taxonomy": {"version": 1, "groups": ["body","face","eyes","eyebrows","eyelashes","hair","clothes","accessories","detachable","other"],
               "classes": [{"value": 30, "id": "hair", "group": "hair", "label": "Hair", "default_kind": "physical",
                            "attach_to": "body.head", "symmetric_with": null}]},
  "queues": {"runs": {"pending": 0, "running_id": null, "running_kind": null, "max_size": 8}},
  "nodes": [
    {"type": "input_image", "title": "Input Image", "category": "input", "available": true, "code": "ok",
     "inputs": [], "outputs": [{"id": "image", "type": "image_set"}], "workflow_field": "source[kind=image]"},
    {"type": "prompt", "title": "Prompt", "category": "input", "available": true, "code": "ok",
     "inputs": [], "outputs": [{"id": "image", "type": "image_set"}], "workflow_field": "source[kind=prompt]",
     "params_schema": { /* ImageGenerationRequest.model_json_schema() */ }},
    {"type": "segmentation", "title": "Segmentation", "category": "analyze", "available": true, "code": "ok",
     "inputs": [{"id": "image", "accepts": ["image_set"], "required": true}], "outputs": [{"id": "masks", "type": "segmentation"}],
     "workflow_field": "segmentation", "params_schema": { /* SegmentationOptions */ }},
    {"type": "image_to_3d", "title": "Image-to-3D", "category": "generate", "available": true, "code": "ok",
     "inputs": [{"id": "image", "accepts": ["image_set"], "required": true}], "outputs": [{"id": "mesh", "type": "mesh"}],
     "workflow_field": "reconstruction", "params_schema": { /* GenerationSettings */ }, "max_instances": 1},
    {"type": "part_extraction", "title": "Part Extraction", "category": "parts", "available": true, "code": "ok",
     "inputs": [{"id": "mesh", "accepts": ["mesh"], "required": true}, {"id": "masks", "accepts": ["segmentation"], "required": false}],
     "outputs": [{"id": "parts", "type": "part_set"}], "workflow_field": "parts", "params_schema": { /* PartAnalysisOptions */ }},
    {"type": "export", "title": "Export", "category": "output", "available": true, "code": "ok",
     "inputs": [{"id": "asset", "accepts": ["mesh", "part_set"], "required": true}], "outputs": [],
     "workflow_field": "export", "params_schema": { /* ExportOptions */ }} ],
  "limits": {"prompt_max_chars": 2000, "negative_prompt_max_chars": 1000, "extra_terms_max": 16,
             "workflow_graph_max_bytes": 65536, "views_max": 24} }
```
Derivation rules:
- `image_to_3d.available = engine.status().state not in {error, unavailable}`. `code` is `engine_unavailable`
  otherwise.
- `prompt_to_3d` requires `flags.image_generation`, a usable T2I model and the VRAM check, plus `image_to_3d`.
- `segmentation.available` requires the flag and at least one usable profile.
- `part_extraction.available` requires `flags.part_analysis`. `components` is always available when the flag is on.
  The semantic strategies need segmentation plus `raster_lifter`.
- `mesh_tuning.enabled = settings.blender_enabled and settings.mesh_tuning_approved`. `part_refinement.enabled =
  settings.part_refinement_enabled`. Both are false by default.
- The `nodes` catalog is advisory. The frontend's pure-TS registry stays the UI source of truth, and it can lock
  nodes from `available`. There are **no** refine or tuning nodes in the backend catalog.
  - Port types follow `ui_modes_research.md` §4.2 with one deliberate difference: `prompt` outputs `image_set`,
    because the backend materializes the prompt as a reference image before any consumer. That makes "exactly one
    source" structural.
- `features.updated` (WebSocket) carries the full object. It is debounced 250 ms after any provider state change,
  flag-relevant engine state change, or run-queue change.

---------------------------------------------------------------------------------------------------
## 12. Endpoint table (all new; prefix `/api/v1`; JSON uses the error envelope of contract §4)

Auth: **J** = `AuthGuard` (header only). **F** = `FileAuthGuard` (header or `?token=`).
"flag 503" means 503 `feature_disabled` when the relevant flag is off. GET endpoints work regardless, so history
stays readable.

### 12.1 Discovery
| Method | Path | Auth | Body / query | Success | Errors |
|---|---|---|---|---|---|
| GET | `/features` | J | – | 200 `FeaturesOut` | 401 |
| GET | `/models` | J | `?kind=text_to_image\|segmentation\|part_extraction` | 200 `{items: ModelOut[]}` | 401, 422 bad kind |
| GET | `/models/{model_id}` | J | – | 200 `ModelOut` | 401, 404 `not_found` |

### 12.2 `ModelOut`
```json
{ "id": "z_image_turbo", "kind": "text_to_image", "roles": ["generator"], "display_name": "Z-Image-Turbo",
  "state": "installed", "reason_code": "ok", "reason": null,
  "approved": true, "is_default": true, "repo_id": "Tongyi-MAI/Z-Image-Turbo",
  "revision": "f332072aa78be7aecdf3ee76d5c247082da564a6", "size_bytes": 32850000000, "vram_estimate_gb": 15.0, "ram_gb": 21.0,
  "license": {"id": "apache-2.0", "name": "Apache License 2.0", "url": "https://huggingface.co/Tongyi-MAI/Z-Image-Turbo",
              "commercial": "allowed", "gated": false, "requires_acceptance": false, "accepted": true, "conditions": []},
  "dependencies": [{"distribution": "diffusers", "requirement": "==0.39.0", "installed": "0.39.0", "ok": true}],
  "vendored_source": null, "local_path": ".model/t2i/Z-Image-Turbo",
  "install": {"supported": false, "hint": "Weights are installed manually under backend/.model/t2i/Z-Image-Turbo (see DECISIONS.md)", "bytes_done": null, "bytes_total": null},
  "capabilities": {"sizes": [[1024,1024],[896,1152],[1152,896]], "steps": {"default": 9, "min": 1, "max": 50},
                   "guidance": {"default": 0.0, "min": 0.0, "max": 0.0}, "supports_negative_prompt": false, "max_images": 4},
  "loaded_device": null, "last_used_at": null }
```
`local_path` is relative to `backend/`. Absolute paths are never returned.

### 12.3 Image generations
| Method | Path | Auth | Body / query | Success | Errors |
|---|---|---|---|---|---|
| POST | `/image-generations` | J | `ImageGenerationRequest` | 202 `ImageGenerationOut` | 422 `validation_error` / `unsupported_by_model`; 503 `feature_disabled`, `capability_unavailable` (no usable T2I or VRAM), `model_unavailable` (explicit model not usable); 429 `queue_full`; 503 `engine_unavailable` while shutting down |
| GET | `/image-generations` | J | `?limit=1..100 (20)&status=` | 200 `{items}` newest first | 422 bad status |
| GET | `/image-generations/{ig_id}` | J | – | 200 | 404 |
| POST | `/image-generations/{ig_id}/cancel` | J | – | 202 | 404; 409 terminal |
| DELETE | `/image-generations/{ig_id}` | J | – | 204 | 404; 409 active or referenced by an active workflow |
| GET | `/image-generations/{ig_id}/candidates/{index}` | F | – | 200 `image/png`, `Cache-Control: private, max-age=3600` | 404 run, index or file; 409 not completed |
| GET | `/image-generations/{ig_id}/candidates/{index}/thumbnail` | F | – | 200 `image/webp` | as above |
| POST | `/image-generations/{ig_id}/candidates/{index}/use` | J | `{reference_set_id?, slot="front", make_primary=true, label?}` | 201 `{image, set}` | 404 run, candidate or set; 409 not completed; 422 slot or validation; 413/415 from upload validation (cannot happen for valid PNGs); flag 503 |

### 12.4 Segmentations
| Method | Path | Auth | Body / query | Success | Errors |
|---|---|---|---|---|---|
| POST | `/segmentations` | J | `SegmentationRequest` | 202 `SegmentationOut` | 404 set, image or job; 409 job not completed; 422; 429; 503 `feature_disabled` / `capability_unavailable` |
| GET | `/segmentations` | J | `?limit&status&reference_set_id&job_id` | 200 `{items}` | 422 |
| GET | `/segmentations/{sg_id}` | J | – | 200 | 404 |
| POST | `/segmentations/{sg_id}/cancel` | J | – | 202 | 404, 409 |
| DELETE | `/segmentations/{sg_id}` | J | – | 204 | 404; 409 active or used by an active workflow |
| GET | `/segmentations/{sg_id}/overlay` | F | – | `image/png` | 404, 409 |
| GET | `/segmentations/{sg_id}/labels` | F | – | `image/png` (uint16) | 404, 409 |
| GET | `/segmentations/{sg_id}/masks/{value}` | F | `value` matches `^\d{1,5}$` | `image/png` | 404, 409 |
| GET | `/segmentations/{sg_id}/source` | F | – | `image/png` (snapshot) | 404 |

### 12.5 Part analyses
| Method | Path | Auth | Body / query | Success | Errors |
|---|---|---|---|---|---|
| POST | `/jobs/{job_id}/part-analyses` | J | `PartAnalysisOptions` (optional body) | 202 `PartAnalysisOut` | 404 job; 409 job not completed or `model.glb` missing; 404/409 `segmentation_id`; 422; 429; 503 `feature_disabled` / `capability_unavailable` |
| GET | `/jobs/{job_id}/part-analyses` | J | – | 200 `{items}` newest first | 404 job |
| GET | `/jobs/{job_id}/part-analyses/latest` | J | – | 200 newest **completed** | 404 job or none |
| GET | `/part-analyses/{pa_id}` | J | – | 200 | 404 |
| POST | `/part-analyses/{pa_id}/cancel` | J | – | 202 | 404, 409 |
| DELETE | `/part-analyses/{pa_id}` | J | – | 204 | 404; 409 active |
| GET | `/part-analyses/{pa_id}/manifest` | F | – | 200 JSON, `no-cache` | 404; 409 not completed |
| GET | `/part-analyses/{pa_id}/model.glb` | F | – | `model/gltf-binary` inline | 404, 409 |
| GET | `/part-analyses/{pa_id}/export/glb` | F | – | attachment `img3d_<job>_parts_<pa>.glb` | 404, 409 |
| GET | `/part-analyses/{pa_id}/export/zip` | F | – | attachment `img3d_<job>_parts_<pa>.zip` | 404 (`export.zip` was false), 409 |
| GET | `/part-analyses/{pa_id}/parts/{part_id}.glb` | F | `part_id` is in the manifest whitelist | `model/gltf-binary` attachment `img3d_<job>_<part_id>.glb` | 404, 409 |
| GET | `/part-analyses/{pa_id}/masks/{value}` | F | `^\d{1,5}$` | `image/png` | 404, 409 |
| GET | `/part-analyses/{pa_id}/segmentation/{name}` | F | `name ∈ {labels.png, overlay.png}` | `image/png` | 404, 409 |
| GET | `/part-analyses/{pa_id}/previews/{name}` | F | `^(front\|back\|left\|right)\.png$` | `image/png` | 404, 409 |
| GET | `/part-analyses/{pa_id}/face-labels` | F | – | `application/octet-stream` (uint16 LE) | 404, 409 |
| GET | `/part-analyses/{pa_id}/log` | F | – | `text/plain` tail ≤256 KB | 404 |

`DELETE /jobs/{job_id}` (existing) first runs the registered delete hook. The hook cancels the job's active analyses
and waits up to 30 s, then forgets them. The existing `safe_rmtree` then removes `parts/` together with the job.

### 12.6 Workflows
| Method | Path | Auth | Body / query | Success | Errors |
|---|---|---|---|---|---|
| POST | `/workflows` | J | `WorkflowRequest` | 202 `WorkflowOut` | 404 source; 422 (incl. `graph_too_large`, `unsupported_by_engine`, `unsupported_by_model`); 429 `queue_full` (the first child cannot be queued); 503 `feature_disabled` / `capability_unavailable` |
| GET | `/workflows` | J | `?limit&status&job_id&image_generation_id` | 200 `{items}` newest first | 422 |
| GET | `/workflows/{wf_id}` | J | – | 200 | 404 |
| GET | `/workflows/{wf_id}/graph` | J | – | 200 stored graph JSON | 404 (none stored) |
| POST | `/workflows/{wf_id}/select-candidate` | J | `{index: 0..3}` | 200 `WorkflowOut` | 404; 409 not `awaiting_input`; 422 |
| POST | `/workflows/{wf_id}/cancel` | J | – | 202 | 404, 409 terminal |
| DELETE | `/workflows/{wf_id}` | J | – | 204 | 404, 409 active |

---------------------------------------------------------------------------------------------------
## 13. WebSocket events (envelope `{type, ts, data}`; the existing types are unchanged)

| type | data | `Event.job_id` (scoping) | throttle / droppable |
|---|---|---|---|
| `features.updated` | `FeaturesOut` | – (always delivered) | debounced 250 ms; never droppable |
| `image_generation.updated` | `ImageGenerationOut` | – | progress ≤5/s droppable; status changes never dropped |
| `segmentation.updated` | `SegmentationOut` | the job id for job sources, else none (not scoped) | same |
| `part_analysis.updated` | `PartAnalysisOut` | the analysis job id; **job-scoped** (`?job_id=` filter) | same |
| `workflow.updated` | `WorkflowOut` | – | progress ≤2/s droppable; step transitions never dropped |

Old clients ignore unknown types (`frontend/src/api/ws.ts` default branch; `test/e2e/e2e_generate.py:191-201`).
The `hello` message is unchanged. Clients fetch `/features` after `hello`.

---------------------------------------------------------------------------------------------------
## 14. Errors (`core/feature_errors.py`; new module, subclasses of `AppError`, existing handlers render them)

| class | status | code | details |
|---|---|---|---|
| `FeatureDisabledError` | 503 | `feature_disabled` | `{"flag": "IMG3D_PART_ANALYSIS_ENABLED"}` |
| `CapabilityUnavailableError` | 503 | `capability_unavailable` | `{"capability": "segmentation", "reasons": [{"model_id", "state", "reason_code", "reason"}]}` |
| `ModelUnavailableError` | 503 | `model_unavailable` | `{"model_id", "state", "reason_code"}` |
| (existing) `ValidationAppError` | 422 | `validation_error` | `type ∈ {unsupported_by_model, graph_too_large, unknown_class, …}` in the per-field details |
| (existing) `ConflictError` / `NotFoundError` / `QueueFullError` | 409 / 404 / 429 | `conflict` / `not_found` / `queue_full` | – |

Run error codes in `run.error.code`:
`interrupted`, `out_of_memory`, `run_error`, `source_missing`, `job_deleted`, `segmentation_failed`,
`camera_fit_failed`, `lifting_failed`, `partition_failed`, `export_failed`, `generation_failed`, `step_failed`
(workflows).

---------------------------------------------------------------------------------------------------
## 15. Storage, retention, deletion

```
backend/runtime/
  jobs/<job>/ …                                   (unchanged)
  jobs/<job>/parts/<pa>/ …                        (§9.6; removed with the job by JobStore.delete / retention)
  imagegen/<ig>/run.json, candidates/<n>.png, candidates/<n>_thumb.webp
  segmentations/<sg>/run.json, source.png, labels.png, overlay.png, masks/, frame.json, segmentation.json
  workflows/<wf>/workflow.json, graph.json
  temp/imagegen/<ig>/ temp/segmentations/<sg>/ temp/parts/<pa>/      (purged at startup by the existing purge_temp)
```
- No path component is named `aux` (a reserved Windows device name).
- `storage/run_layout.py` holds the path helpers as functions of `RuntimePaths`. `storage/paths.py` is protected
  and stays untouched.
- `TuningStore.load` scans only `jobs/*/tuning/` and `JobStore.load` reads only `job.json` [V], so `parts/` is
  invisible to both.
- **Retention** (`services/run_retention_service.py`, background task on `retention_interval_s`):
  - Terminal `ig_` runs older than `imagegen_retention_days` (14), or beyond `max_stored_image_generations` (200),
    are deleted.
  - The same applies to `sg_` (14 days, 200 runs) and to terminal `wf_` (30 days, 500 workflows).
  - Runs referenced by an active workflow are skipped.
- `pa_` runs live inside the job folder, so job retention removes them. Retention deletes jobs without hooks
  (`retention_service.py:60-77`), so the part worker re-checks `job_exists` before moving outputs. This is the
  tuner pattern. `RunStore.save` refuses to recreate a deleted job folder.
- Disk: defaults keep per-part GLBs and the zip **off**. `model_parts.glb` is about the size of `model.glb`
  (41–86 MB [V: generation map §5.1]).

---------------------------------------------------------------------------------------------------
## 16. Feature flags and settings (additive `Settings` fields; env prefix `IMG3D_`)

| field | env | default | effect |
|---|---|---|---|
| `part_aware_enabled` | `IMG3D_PART_AWARE_ENABLED` | `true` | Master switch. When false: no RunWorker and no workflow sweep; mutating new endpoints → 503 `feature_disabled`; `/features` flags false. Read endpoints still work |
| `image_generation_enabled` | `IMG3D_IMAGE_GENERATION_ENABLED` | `true` | `ig_` creation and prompt workflows |
| `segmentation_enabled` | `IMG3D_SEGMENTATION_ENABLED` | `true` | `sg_` creation and semantic strategies |
| `part_analysis_enabled` | `IMG3D_PART_ANALYSIS_ENABLED` | `true` | `pa_` creation |
| `workflows_enabled` | `IMG3D_WORKFLOWS_ENABLED` | `true` | `wf_` creation |
| `mesh_tuning_approved` | `IMG3D_MESH_TUNING_APPROVED` | `false` | Reporting gate for `/features.mesh_tuning`. The hard gate is `IMG3D_BLENDER_ENABLED=false` (§17) |
| `part_refinement_enabled` | `IMG3D_PART_REFINEMENT_ENABLED` | `false` | `PartConsumerRegistry.register()` raises `FeatureDisabledError`; `/features.part_refinement` |
| `approved_models` | `IMG3D_APPROVED_MODELS` | `None` → catalog ids with `default_approved=True` (the six `DECISIONS.md` models) | JSON list or `,`/`;`-separated ids. An explicit empty list approves nothing |
| `model_paths` | `IMG3D_MODEL_PATHS` | `{}` | JSON map from id to a local path override (relative paths resolve against `backend/`) |
| `allow_restricted_licenses` | `IMG3D_ALLOW_RESTRICTED_LICENSES` | `false` | Required for `non_commercial` / `research_only` / `unknown` models (none in catalog v1 except the lifter, which is exempt) |
| `t2i_default_model` | `IMG3D_T2I_DEFAULT_MODEL` | `None` → first usable | |
| `segmentation_default_profile` | `IMG3D_SEGMENTATION_DEFAULT_PROFILE` | `"auto"` | |
| `run_queue_max_size` | `IMG3D_RUN_QUEUE_MAX_SIZE` | `8` (1..100) | Shared queue for `ig_`/`sg_`/`pa_` |
| `aux_keep_models_in_ram` | `IMG3D_AUX_KEEP_MODELS_IN_RAM` | `true` | |
| `aux_model_idle_unload_s` | `IMG3D_AUX_MODEL_IDLE_UNLOAD_S` | `600` (0 = never) | |
| `aux_vram_budget_gb` | `IMG3D_AUX_VRAM_BUDGET_GB` | `28.0` | §5.5 |
| `engine_resident_vram_gb` | `IMG3D_ENGINE_RESIDENT_VRAM_GB` | `17.0` | Subtracted when `low_vram=false` and the engine is ready |
| `prompt_max_chars` | `IMG3D_PROMPT_MAX_CHARS` | `2000` (1..8000) | |
| `workflow_graph_max_bytes` | `IMG3D_WORKFLOW_GRAPH_MAX_BYTES` | `65536` | |
| `workflow_sweep_interval_s` | `IMG3D_WORKFLOW_SWEEP_INTERVAL_S` | `5.0` | |
| `part_analysis_timeout_s` | `IMG3D_PART_ANALYSIS_TIMEOUT_S` | `1800` | Watchdog: cancels the token and fails with `run_error` (`timeout`) |
| `imagegen_retention_days` / `segmentation_retention_days` / `workflow_retention_days` | `IMG3D_…` | `14` / `14` / `30` | |
| `max_stored_image_generations` / `max_stored_segmentations` / `max_stored_workflows` | `IMG3D_…` | `200` / `200` / `500` | |
| `persist_generation_intermediates` | `IMG3D_PERSIST_GENERATION_INTERMEDIATES` | `false` | **Reserved for deferred D1**: no effect in this milestone, and its presence is reported in `/features.api` |

The tests use `make_settings(**overrides)` with `_env_file=None` (`test/backend/conftest.py:60-75` [V]). New tests
pass the new fields as overrides, and existing tests see only the defaults.

---------------------------------------------------------------------------------------------------
## 17. Tuning and refinement gating (backend side)

| Gate | What | Status / proof |
|---|---|---|
| G1 (hard) | `IMG3D_BLENDER_ENABLED=false` in `backend/.env`. Every mutating tuner endpoint returns 503 `blender_unavailable`, and startup detection is skipped | Existing behaviour, tested by `test_blender_runtime_api.py:167-176`. Zero code change. It is a user-local file, so slice S0 tells the user to set it (`.env` is not edited by agents) |
| G2 | `/features.mesh_tuning.enabled=false` (`blender_enabled and mesh_tuning_approved`) and `/features.part_refinement.enabled=false` | All four UI modes hide the tuner and refine nodes from one flag. Test: `test_features_api.py` |
| G3 | New packages never import `services.mesh_tuner_service`, `workers.tuning_worker`, `launcher` or `blender_scripts` | `test_import_boundaries.py` |
| G4 | `parts/consumers.py`: a `PartConsumer` protocol plus `PartConsumerRegistry` that refuses `register()` while `part_refinement_enabled=false`. Zero implementations. The manifest exposes `refinement.eligible/status/locks` for a future consumer | `test_part_consumers.py` |

```python
class PartConsumer(Protocol):
    id: str
    def accepts(self, part: ManifestPart) -> bool: ...
    def plan(self, manifest: PartsManifest, part_ids: Sequence[str]) -> Mapping[str, Any]: ...   # no execution API yet

class PartConsumerRegistry:
    def __init__(self, settings: Settings) -> None
    def register(self, consumer: PartConsumer) -> None     # FeatureDisabledError("part refinement") while disabled
    def consumers(self) -> list[PartConsumer]              # always [] in this milestone
```
The `blender_enabled` default (pinned at `test_blender_runtime_api.py:311`) is **not** changed in code. A code-level
gate on the existing tuner endpoints would need a test-settings override in an existing test file. That is open
question Q3.

---------------------------------------------------------------------------------------------------
## 18. What the frontend needs from this API

### 18.1 Flows per action (identical in all four UI modes; only the renderer differs)
| User action | Calls | Live updates |
|---|---|---|
| Image → 3D (no parts) | **unchanged:** `POST /jobs` | `job.updated` |
| Image → 3D + parts | `POST /workflows {source: image, parts: {...}, segmentation?: {...}}` | `workflow.updated`; `job.updated` for the reconstruction step detail; `part_analysis.updated` |
| Prompt → 3D (± parts) | `POST /workflows {source: prompt, generation: {...}, selection}` | `workflow.updated` (+ `image_generation.updated`). With `selection: "manual"`: the step `awaiting_input` → show candidates → `POST /workflows/{id}/select-candidate` |
| Prompt → image only (preview) | `POST /image-generations`, then `…/candidates/{n}/use` | `image_generation.updated` |
| Segment an input image (Node "Segmentation" preview) | `POST /segmentations {source: reference_image}` | `segmentation.updated` |
| Re-run parts on a finished job | `POST /jobs/{id}/part-analyses` | `part_analysis.updated` |
| Viewer "Parts" source | `GET /jobs/{id}/part-analyses/latest`, then `result.assembled_url` (named nodes `part:<id>`, `_FEATURE_ID_0` on `part:body`) | – |
| Parts list | `result.parts[]` (or the manifest via `result.manifest_url`) | – |
| Capability gating, model badges, licence display | `GET /features` at session sync, after `hello`, on `features.updated` | `features.updated` |
| Node UI "Run" | compile graph → `WorkflowRequest` (with `graph`) → `POST /workflows`. Node status = `steps[].node` / `steps[].status` | `workflow.updated` |
| History provenance ("from prompt …") | `GET /workflows?job_id=` | – |

Rules:
- The frontend never builds part, mask or GLB paths. It uses URLs from payloads.
- `/features` 404 (a legacy backend) means image-to-3D only. This is already planned in
  `frontend_b_command_engine.md` §4.3.

### 18.2 Reconciliation with `frontend_b_command_engine.md` §4 (only `api/features.ts` and `api/parts.ts` change)
| Frontend B expects | This proposal | Adapter rule |
|---|---|---|
| E1 `GET /features` (`pipeline.*`, `models`, `mesh_tuning`, `part_refinement`) | Same keys plus extras (`flags`, `taxonomy`, `nodes`, `queues`, `limits`) | Direct |
| E2 `POST /jobs` with `source` and `settings.parts` | `POST /workflows` (same information, different shape). `POST /jobs` unchanged | `job.generate` → `workflows.create` when prompt or parts are on |
| E3 `Job.steps` | `WorkflowOut.steps` (ids = node types) | Project the workflow steps |
| E4/E5/E7/E8 `/jobs/{id}/parts…` | `/jobs/{id}/part-analyses/latest` → `result.*_url` | URL indirection |
| E9 rebuild | `POST /jobs/{id}/part-analyses` | Direct |
| E10 model install | not in v1 (`install.supported=false`) | Hides the button |
| WS `features.updated` | Same | Direct |
| Manifest field names (`id`, `category`, `group`, `kind`, `node_name`, `region`, `files`, `refinement`) | Same (§9.8) | Direct |

### 18.3 TypeScript additions (mirror of §7–§13; `frontend/src/api/types.ts`, snake_case kept)
`FeaturesOut`, `CapabilityOut`, `ModelOut`, `RunStep`, `RunProgress`, `ImageGenerationRequest`,
`ImageGenerationOut`, `SegmentationRequest`, `SegmentationOut`, `PartAnalysisOptions`, `PartAnalysisOut`,
`PartsManifest`, `WorkflowRequest`, `WorkflowOut`, and the WS message union members for the five new event types.
The existing `JobStage` union and `PIPELINE_STAGES` stay untouched, because no job stage changes.

### 18.4 Test double (`test/frontend/mock_backend.mjs`, owned by the frontend slice)
- New routes mirror §12.
- New flags: `--features-scenario full|no-t2i|no-seg|disabled|legacy404`, `--parts-scenario ok|partial|fail`,
  `--t2i-scenario ok|fail|slow` and `--workflow-seconds N`.
- Responses use the exact shapes above. GLBs reuse the mock's existing cube writer with named nodes `part:body` and
  `part:hair`.

---------------------------------------------------------------------------------------------------
## 19. Test plan (all new files under `test/`; no existing test file is edited)

### 19.1 Guards (S0/S1)
| File | Asserts |
|---|---|
| `test/backend/test_protected_files.py` + `protected_files.sha256` | sha256 (CRLF-normalized) of every protected file (§1) equals the recorded value. A failure names the file and reminds the editor that the change needs explicit approval |
| `test/backend/test_import_boundaries.py` | AST scan: no `torch`/`diffusers`/`transformers`/`nvdiffrast`/`cumesh`/`trellis2` import outside `inference/**` implementation modules; no tuner imports in new packages (I7, I10) |
| `test/backend/test_legacy_invariance.py` | With `part_aware_enabled` true **and** false: a FakeEngine job has an identical `job.json` key set, `JobOut` key set, WS stage sequence, final percent 100, `stage_index` 9. `/system` keys are unchanged. A legacy `job.json` without any new file loads and serves `/result`. A part analysis leaves `model.glb`, `preview.png` and `job.json` sha256 unchanged |
| `test/backend/test_gpu_task_seam.py` | `submit_gpu_task` runs on a `gpu-worker*` thread. Tasks interleave FIFO with a FakeEngine job and never overlap (counters guarded by a lock). It raises `engine_unavailable` after `stop()`. `shutdown` with an in-flight auxiliary task completes within the timeout (stop order) |

### 19.2 Per feature
| File | Asserts |
|---|---|
| `test_features_api.py` | Top-level keys. Flags mirror settings. `mesh_tuning.enabled` is false when `blender_enabled` is false or `mesh_tuning_approved` is false. `part_refinement` is false. Strategies and profiles reflect fake registry states. `nodes` has 6 types and no refine. `features.updated` arrives after a fake provider changes state |
| `test_model_registry.py` | Each state in §6.3 using temp dirs and fake `DependencySpec` distributions: disabled, not_approved, license_blocked, vendored_source_missing, missing_dependency, not_installed, incompatible (transformers_version 5 config), installed, loaded/error via a fake provider. Every catalog entry has a licence and a URL. No absolute path in `ModelOut` |
| `test_run_framework.py` | `RunStore` atomic save, load, `recover_interrupted`, refusal for a deleted job. Reporter throttling and monotonic percent. Cancel while queued and while running. Shutdown fails queued runs as `interrupted`. OOM-style exceptions become `out_of_memory` |
| `test_image_generation_api.py` (with `fakes_imagegen.py`: a real procedural PNG writer, test-only) | 503 when no T2I (fake registry not approved). Happy path to `completed` via WS. Candidate PNG and WebP served with `?token=` when auth is on. `use` creates a set and image through the real `ReferenceService` and they are visible in `GET /reference-sets/{id}`. 422 for empty or too-long prompt, `num_images=5`, bad size, negative prompt on Turbo, unknown field. Cancel and delete rules |
| `test_segmentation_api.py` (with `fakes_segmentation.py`: deterministic colour-threshold masks) | Reference-image and job sources. Snapshot isolation (the set image is replaced after creation and the run is unaffected). 409 for a non-completed job. Degradation warning when the parser is missing. File routes, value regex, auth via `?token=` |
| `test_frame_recovery.py` | Synthetic RGBA → upstream-equivalent crop → `recover_frame` exact (`alpha_crop`). Template match recovers the offset for `background_removed`. Identity for `untouched`. Unverified case yields a warning |
| `test/engine/test_frame_parity.py` (torch; slow marker) | `processing/seg2d/frame.py` alpha path equals upstream `preprocess_image` output pixel for pixel |
| `test_parts_core.py` (with `parts_fixtures.py`: two touching spheres, a capped cylinder, a UV-seamed cube) | Weld map. Components. Policy table (eyes physical, brows and lashes deferred, face and body region). Partition covers every face once. Seam loops are closed and ordered. glTF round-trip: 1 material, 2 images, node names `part:*`, `_FEATURE_ID_0` present, extras preserved. Reassembly verified. Manifest validation rejects `..`, absolute paths, duplicate ids and unknown region values |
| `test_part_analysis_api.py` (FakeEngine variant writing a two-shell GLB; `fakes_lifting.py`: an orthographic numpy face-ID rasterizer, test-only) | `components` strategy end to end on the CPU (completed; manifest valid; file routes). Semantic strategy with fake segmenter and lifter. 409 for a non-completed job. Job delete cancels an active analysis and removes `parts/`. Retention deleting a job mid-run gives `job_deleted` and no folder resurrection. `?job_id=` WS scoping of `part_analysis.updated` |
| `test_workflows_api.py` | Image + parts chain to completed. Prompt + `auto_first`. Prompt + `manual` (awaiting_input → select-candidate). Child failure → `step_failed`. Cancel mid-reconstruction cancels the job. 429 from the job queue → `waiting` then success. Restart marks `interrupted`. `graph_too_large`. Children survive workflow delete. The sweep recovers a dropped event (bus subscription closed manually) |
| `test_part_consumers.py` | `register()` raises while disabled and works when the override is true (test-only consumer). `consumers()` is empty by default |
| `test_run_retention.py` | Age and count limits per store. Skips runs referenced by active workflows |
| `test/aux_models/test_part_providers_smoke.py` (GPU, real weights; skip unless the files exist) | Z-Image-Turbo one 512×512 image at 2 steps (shape and finite values; offload leaves less than 1 GB allocated). GDINO + SAM 2.1 on `test/images/ANIME_CHARACTER_01` produces a non-empty `hair` mask. Raster lifter face IDs on a 20k-face mesh. Then a real TRELLIS.2 smoke test (`img3d-run-and-test`) shows unchanged behaviour |

Gate per slice: `backend\python\python.exe -s -m pytest test/backend test/aux_models -q` → all green. The existing
182 tests are unchanged and pass. New tests pass.

---------------------------------------------------------------------------------------------------
## 20. Implementation slices (disjoint file ownership)

Phase A is sequential. Phase B slices run in parallel. Phase C runs after B. "Owns" means only that slice creates
or edits the file. Interfaces frozen in S1 may be edited later only with S1-owner sign-off.

| Slice | Phase | Owns (files) | Depends on | Acceptance criteria |
|---|---|---|---|---|
| **S0 Contract + guards** | A1 | `.claude/skills/img3d-architecture/api-contract.md` (new §11 "Part-aware layer" with §5–§17 of this doc, plus the drift fixes listed in the contract map §6.1 item 10), `test/backend/test_protected_files.py`, `test/backend/protected_files.sha256`, `test/backend/test_legacy_invariance.py` (baseline part; the part-analysis hash assertion is skipped until S4) | – | 182 + new tests green. Hash file covers §1. Message to the user: set `IMG3D_BLENDER_ENABLED=false` in `.env` |
| **S1 Skeleton + frozen interfaces** | A2 | EDIT `config/settings.py`, `workers/generation_worker.py`, `core/container.py`, `core/lifecycle.py`, `main.py`, `api/router.py`, `api/websocket_hub.py`, `schemas/events.py`. NEW `core/feature_errors.py`, `core/part_aware_container.py`, `api/part_routes.py`, `api/part_deps.py`, empty `api/v1/{features,model_registry,image_generations,segmentations,part_analyses,workflows}.py` routers, `models/runs.py`, `schemas/runs.py`, `storage/run_store.py`, `storage/run_layout.py`, `pipelines/runs/*`, `workers/run_worker.py`, `services/run_events.py`, `inference/providers/base.py`, `inference/imagegen/base.py`, `inference/segmentation/base.py`, `inference/parts/base.py`, `parts/taxonomy.py`, `parts/manifest.py`, stub service classes (constructors frozen, no methods yet) in `services/{feature,model_registry,image_generation,segmentation,part_analysis,workflow}_service.py`, `test/backend/test_gpu_task_seam.py`, `test/backend/test_import_boundaries.py`, `test/backend/test_run_framework.py`. Updates `protected_files.sha256` to add `generation_worker.py` | S0 | App starts with and without `part_aware_enabled`. No new route answers except 404 (empty routers). Old tests green. Seam tests green. Import-boundary test green |
| **S2 Registry + features** | B | `inference/providers/{catalog,registry,unavailable}.py`, `services/model_registry_service.py`, `services/feature_service.py`, `schemas/features.py`, `schemas/model_registry.py`, `api/v1/features.py`, `api/v1/model_registry.py`, `test/backend/test_features_api.py`, `test/backend/test_model_registry.py` | S1 | `/features` and `/models` per §11–§12.2 with real on-disk states on this machine (Z-Image `installed`; See-through and Sapiens2 `vendored_source_missing`; the rest `installed`) |
| **S3 Parts core (CPU)** | B | `parts/{policy,weld,components,partition,seams,gltf_export,reassembly,consumers}.py`, `test/backend/parts_fixtures.py`, `test/backend/test_parts_core.py`, `test/backend/test_part_consumers.py` | S1 | Unit tests green. Export of a 2M-face synthetic mesh under 60 s on the CPU [A, measured in the test log] |
| **S5 Image generation** | B | `models/image_generation.py`, `schemas/image_generation.py`, `pipelines/image_generation.py`, `pipelines/prompt_templates.py`, `processing/seg2d/readiness.py`, `services/image_generation_service.py`, `api/v1/image_generations.py` (fills the S1 stub), `inference/imagegen/zimage.py`, `backend/requirements.txt` (add `diffusers==0.39.0`, `accelerate==1.15.0`), `test/backend/fakes_imagegen.py`, `test/backend/test_image_generation_api.py` | S1 (+S2 registry at integration: S5 uses the frozen `ProviderRegistry` interface with overrides in tests) | API tests green with fakes. The real smoke test (section in `test/aux_models/test_part_providers_smoke.py::test_zimage`, owned here) generates one image. The TRELLIS.2 real smoke test still passes after the requirements pin |
| **S6 Segmentation** | B | `models/segmentation.py`, `schemas/segmentation.py`, `pipelines/segmentation.py`, `processing/seg2d/{__init__,frame,fusion,eyes}.py`, `services/segmentation_service.py`, `api/v1/segmentations.py`, `inference/segmentation/{gdino,sam2,florence2}.py`, `test/backend/fakes_segmentation.py`, `test/backend/test_segmentation_api.py`, `test/backend/test_frame_recovery.py`, `test/engine/test_frame_parity.py`, smoke `…::test_baseline_segmentation` | S1 | Baseline profile works on `test/images/ANIME_CHARACTER_01` and `ALICE` (masks for hair, face, eyes). Degradation is honest |
| **S6b Vendored parsers** | B (parallel to S6; separate files) | `backend/third_party/see-through/` and `backend/third_party/sapiens2/` (git submodules, small modules only), `backend/third_party/README.md` entry, `THIRD_PARTY_NOTICES.md` rows, `inference/segmentation/{see_through,sapiens2}.py`, smoke `…::test_parsers` | S1 | Registry states flip to `installed`. Parsers produce taxonomy masks. No new pip packages (only `-c backend/constraints.txt` if any is unavoidable, reported first) |
| **S8 Raster lifter** | B | `inference/parts/raster_lifter.py`, `test/backend/fakes_lifting.py`, smoke `…::test_raster_lifter` | S1 | Camera fit on a real job finds IoU ≥ 0.6 for a front-view character. Face IDs are correct on a 2M-face GLB (spot-checked by reprojection) |
| **S9 Retention** | B | `services/run_retention_service.py`, `test/backend/test_run_retention.py` | S1 | Tests green |
| **S4 Part analysis** | C | `models/part_analysis.py`, `schemas/parts.py`, `pipelines/part_analysis.py`, `services/part_analysis_service.py`, `api/v1/part_analyses.py`, `test/backend/test_part_analysis_api.py`; enables the skipped assertion in `test_legacy_invariance.py` (an S0-owned file, edited with S0-owner sign-off) | S2, S3, S6, S8 | `components` works on a real completed job (real `model.glb`, real files). `semantic_parts` works on a real anime job. `model.glb` hash unchanged. Manifest valid |
| **S7 Workflows** | C | `models/workflow.py`, `schemas/workflows.py`, `storage/workflow_store.py`, `services/workflow_service.py`, `api/v1/workflows.py`, `test/backend/test_workflows_api.py` | S4, S5, S6 | Prompt → parts end to end with fakes. One real run (Z-Image-Turbo → TRELLIS.2 → `semantic_parts`) completes and its outputs are inspectable |
| **F-API (frontend team)** | C | `frontend/src/api/{features,parts,workflows,imageGenerations,segmentations}.ts`, the type additions in `frontend/src/api/types.ts`, `test/frontend/mock_backend.mjs` routes (§18.4) | S0 contract | Mock smoke green; no UI breakage |

Ownership notes:
- `core/part_aware_container.py` (S1) constructs every service through the frozen constructors. Later slices never
  edit it.
- Routers are created empty by S1 and filled only by their owning slice.
- `test/aux_models/test_part_providers_smoke.py` is created by S1 as an empty module with one function stub per
  owner (`test_zimage`, `test_baseline_segmentation`, `test_parsers`, `test_raster_lifter`). Each owner edits only
  their own function. If parallel edits to one file are a concern, use separate files `test_smoke_<owner>.py`
  (recommended).

---------------------------------------------------------------------------------------------------
## 21. Deferred items (each needs explicit user approval; nothing here is in this milestone)

| Id | Item | Why deferred |
|---|---|---|
| D1 | Persist generation intermediates (`latents.npz`, `conditioning.png` RGBA + `conditioning.json`) from `inference/trellis/generation.py` behind `IMG3D_PERSIST_GENERATION_INTERMEDIATES` | Touches the protected TRELLIS adapter and the parity test surface. Frame recovery (§8.2) covers alignment without it |
| D2 | `per_part_regeneration` (Tripo-style: generate a part from a masked crop, then register it into the asset frame) | Loses registration (unit-cube renormalization); multiplies the 133–579 s runtime |
| D3 | Part-clean atlas export (charts per part, one xatlas pack, one bake; `trellis2_internals.md` §4.3) | Re-bake cost; current cut parts share the original atlas |
| D4 | Tuning consumers (Blender per-part repair, eye and rig correction, lash cards, amodal completion) | User rule: tuning stays disabled until approval |
| D5 | Model install/download API (`POST /models/{id}/install`) | Models are already on disk; manual install keeps the state machine small |
| D6 | `EXT_mesh_features` / `EXT_structural_metadata` declarations | trimesh `extensionsUsed` bookkeeping unverified [A] |

---------------------------------------------------------------------------------------------------
## 22. Risks and mitigations

| # | Risk | Mitigation |
|---|---|---|
| R1 | An auxiliary GPU step OOMs or fragments VRAM before a TRELLIS job | Single executor; `offload()` + `empty_cache()` in `finally`; VRAM budget check; OOM fails only that run. Measure `max_memory_allocated` in the smoke tests |
| R2 | Shutdown leaves an auxiliary task on the GPU thread | `part_aware.stop()` runs before `worker.stop()`: cancel token, wait for the timeout. Tested in `test_gpu_task_seam.py` |
| R3 | A dropped event leaves a workflow stuck | 5 s reconciliation sweep plus startup advance. Tested |
| R4 | Retention deletes a job during a part analysis | Temp work dir, `job_exists` re-check before move, `RunStore.save` refusal (tuner pattern). Tested |
| R5 | Frame recovery is wrong, so labels are misaligned | Residual verification; warnings `frame_unverified` and `camera_fit_low_iou`; `result.status="partial"`; confidences halved |
| R6 | Remeshed double-walled shell or non-manifold junctions produce sliver labels | Labels come only from outside visibility; inner faces take the label of the nearest voted face; 1-ring majority smoothing; `min_part_faces` merge |
| R7 | Thin painted parts (brows, lashes) cannot be cut | `physical_deferred` by policy (`DECISIONS.md` §3). Masks, regions and the lid curve are kept for D4 |
| R8 | Disk growth | Per-part GLBs and zip off by default; retention for the new top-level stores |
| R9 | Licence drift (Sapiens2 conditions, nvdiffrast NC) | Registry licence metadata surfaced in `/features` and `/models`; `THIRD_PARTY_NOTICES.md` rows in S6b; `allow_restricted_licenses` gate for future entries |
| R10 | Vendored code is incompatible with Python 3.14 | Isolated provider modules; honest `vendored_source_missing` / `error`; the baseline profile keeps working |
| R11 | Dependency drift (diffusers vs hub pin) | Already installed under `backend/constraints.txt` [V]; S5 adds the pins to `requirements.txt` and re-runs the TRELLIS smoke test |
| R12 | A protected-file hash test blocks a legitimate future change | This is intended: the change needs approval and a deliberate hash update in the same commit |
| R13 | The tuner is still callable if the user forgets G1 | G2 hides it in all UIs; Q3 offers a code gate |

---------------------------------------------------------------------------------------------------
## 23. Genuinely open questions (only the user can decide these)

- **Q1** See-through and Sapiens2: may they be vendored as git submodules under `backend/third_party/`, as
  `DECISIONS.md` implies? This adds a repo change and `THIRD_PARTY_NOTICES` entries, and the Sapiens2 License
  conditions are shown in the UI.
- **Q2** Prompt-to-3D default selection:
  - `auto_first` (one-shot, default here), or
  - `manual` (always pick among N candidates before the 3–10 min reconstruction).
- **Q3** Tuner hard gate:
  - Is `IMG3D_BLENDER_ENABLED=false` in `.env` sufficient?
  - Or should the code also refuse tuner mutations unless `IMG3D_MESH_TUNING_APPROVED=true`? That requires adding
    `mesh_tuning_approved=True` to the tuner tests' settings, which means editing an existing test file.
- **Q4** Eye cut scope: is the cut "eye area" the lifted `eye.*` faces only (default here), or should it include a
  ring of eyelid faces so that a future eye replacement has margin? The ring would be recorded as
  `body.head.eye_socket` regions, which needs taxonomy v1 extra values 26/27.
- **Q5** Should a workflow with `parts` also export per-part GLBs and the zip by default? This is useful for
  DCC/Blender hand-off. Each per-part GLB embeds the full texture atlas, about the size of `model.glb` each.
- **Q6** Approve D1 (persisting latents and the RGBA conditioning image) now or later? It would allow full-resolution
  relabelling without the frame recovery heuristics, but it touches the TRELLIS adapter.
