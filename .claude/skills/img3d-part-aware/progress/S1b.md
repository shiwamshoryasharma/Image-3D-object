# S1b Runtime plumbing: progress report (2026-10-16)

Status: **done**. This is the wiring half of `DESIGN.md` §11 row S1. S1a (the frozen data interfaces) ran in parallel.
This run resumed a stopped run: the files it left were reviewed, kept where correct, fixed and completed, and the
three test files plus their fakes were written.

## Built
- **Seams into working code** (`DESIGN.md` §1.2):
  - S-a: `GenerationWorker.submit_gpu_task` and `accepting_gpu_tasks` only (17 diff lines in total, including the
    `TypeVar` import).
  - S-b: `ServiceContainer.part_aware`, `build_container(..., part_providers=)`,
    `lifecycle.startup` → `part_aware.start()` after the tuning worker starts, and `lifecycle.shutdown` →
    `part_aware.stop()` **before** `worker.stop()`.
  - S-c: `build_part_aware_router()` is mounted, the 5 `EventType` members exist, and `part_analysis.updated` is
    job-scoped in the WebSocket hub.
  - `main.create_app(..., part_providers=None)`.
- **Settings** (§8.1): every field, additive. `approved_models` and `model_paths` use `NoDecode` with before-validators,
  the same pattern as `cors_origins`. `model_paths` resolve against `backend/`. `part_aware_summary()` always reports
  `persist_generation_intermediates: false`. `safe_summary()` is unchanged.
- **`core/feature_errors.py`** (§6.8): `FeatureDisabledError`, `CapabilityUnavailableError`, `ModelUnavailableError`,
  `InvalidWorkflowError`, plus:
  - the run-side errors `InsufficientVramError` (503 `insufficient_vram`) and `JobDeletedError` (409 `job_deleted`);
  - `ServiceNotImplementedError`, raised by the S1 stubs: a `capability_unavailable` whose reason code is
    `not_implemented`;
  - `require_feature()` / `feature_enabled()`, which also apply the master switch to the run and workflow features.

  All of these are `AppError`s, so the existing handlers render them in the usual envelope.
- **`core/part_aware_container.py`**: `PartAwareContainer`, `PartProviderOverrides(providers, catalog, registry,
  executors)` and `build_part_aware()`. Lazy dotted-path tables mean later slices never edit this file:
  - executors: `pipelines.{image_generation,segmentation,part_analysis}:<Kind>Executor`;
  - registry: `inference.providers.registry:ProviderRegistry`;
  - catalog: `inference.providers.catalog:CATALOG`;
  - retention: `services.run_retention_service:RunRetentionService`.

  Lifecycle:
  - `start()` always loads the stores and runs `recover_interrupted` (runs and workflows). Only when
    `part_aware_enabled=true` does it also create the global run folders and start the `RunWorker`, the workflow
    service, retention and the engine-change → `features.updated` watcher.
  - `stop()` stops the workflow service, then the registry service, then the run worker. It runs before
    `worker.stop`.
  - It also registers the job-delete hook.
- **Run framework** (§4.3):
  - `models/runs.py`: `RunRecord`, `RunStep`, `RunProgress`, `RunWarning`, `ModelUse`, `StepDef`, `RunKind`, id
    prefixes, run error codes and warning codes.
  - `schemas/runs.py`: `RunOutBase`, `run_out_fields()`, `RunQueueOut`.
  - `storage/run_layout.py`: the §7.1 paths over `RuntimePaths`; `paths.py` is untouched.
  - `storage/run_store.py`: a generic `RunStore[R]`:
    - atomic writes with `os.replace` retries;
    - `recover_interrupted`;
    - tombstones for deleted runs;
    - runs of vanished jobs are hidden;
    - job-scoped saves use `mkdir(parents=False)` and a writer that never creates parents, mapping
      `FileNotFoundError` to `JobDeletedError`.
  - `pipelines/runs/{executor,context,reporter}.py`:
    - `RunPhase` and the `RunExecutor` protocol;
    - `ExecutorDeps` and `ExecutorTable` (missing module → `not_implemented`; broken module → `error`);
    - `RunContext` (`step`/`progress`/`warn`/`skip`/`message`/`use_model`/`log_path`) and `model_use()`;
    - `RunReporter`: progress at most 5/s with a trailing flush, only when something was held back; `run.json` at
      most every 2 s with a trailing persist; transitions never dropped; percent monotonic, capped at 99.9 while
      running, exactly 100 on `completed`.
  - `workers/run_worker.py`: `RunWorker` with the `parts-cpu` CPU executor:
    - GPU phases go through `submit_gpu_task`, after the jobs-first gate (`run_max_defer_s`, warning
      `gpu_defer_timeout`, cancellable);
    - each GPU phase runs inside `registry.guard()`, with `ensure_vram` before it and `offload` in `finally`;
    - the watchdog counts active time only (`prepare`, phase callables, `finalize`);
    - error mapping: `JobCancelledError` → `cancelled`, `AppError` → its own code, otherwise `classify_exception` →
      `out_of_memory` or `run_error`. A shutdown cancel gives `interrupted`; a timeout gives `run_timeout`;
    - submitted futures are never cancelled, so the final status and the `work_dir` removal wait for the callable to
      return;
    - `stop(timeout)` abandons a run only after `timeout`, and then leaves its `work_dir` for `purge_temp`;
    - a failed `pa_` keeps its `log.txt`;
    - also provides `enqueue`, `cancel_queued`, `cancel_running`, `wait_finished`, `live_snapshot` and
      `queue_status`.
- **`services/run_events.py`** (§6.6):
  - `RunEventPublisher`: thread-safe. Its default `job_id` is `run.job_id` (part analyses, and segmentations with a
    job source). An optional `min_interval` coalesces droppable events with a trailing publish of the latest state.
    Workflows use 0.5 s, i.e. at most 2/s.
  - `FeaturesEventPublisher`: debounced 250 ms, never droppable, identical payloads are not re-sent, silent while
    discovery is not implemented, and a notification before startup does not block later ones.
- **API**:
  - `api/part_routes.py` mounts every `ROUTERS` tuple of the six `api/v1` modules.
  - `api/part_deps.py` holds the DI accessors.
  - The six routers are empty, use J/F auth, and are prefixed where the module has a prefix. Every path answers 404
    until its slice adds routes.
- **Stub services with frozen constructors**: `FeatureService`, `ModelRegistryService`, `ImageGenerationService`,
  `SegmentationService`, `PartAnalysisService`, `WorkflowService`, `PartFiles`. Their public methods raise
  `ServiceNotImplementedError` (honest 503). Three parts are already real:
  - `PartAnalysisService.on_job_deleting`: cancels active analyses, waits ≤ 30 s, forgets them and purges the export
    cache;
  - `PartFiles.purge`;
  - the `WorkflowService.start`/`stop` hooks (no-ops until S11).

### Fixes made in this resumed run
- `FeaturesEventPublisher.notify()`: a notification before the bus had a loop used to set `_scheduled` forever, which
  silenced `features.updated`. It is now ignored until startup binds the loop.
- `RunReporter` trailing flush: it re-published even when nothing had been held back. It now publishes only pending
  updates.
- `PartAwareContainer.start()`: global run folders are created only when the layer is enabled, so a disabled layer
  writes nothing.
- Formatting in `part_analysis_service.on_job_deleting`.

## Files (all paths relative to `E:\14-OCT-2026\Img3D\`)
- Edited:
  - `backend\config\settings.py`
  - `backend\workers\generation_worker.py`
  - `backend\core\container.py`
  - `backend\core\lifecycle.py`
  - `backend\main.py`
  - `backend\api\router.py`
  - `backend\api\websocket_hub.py`
  - `backend\schemas\events.py`
- New, under `backend\`:
  - `core\feature_errors.py`
  - `core\part_aware_container.py`
  - `api\part_routes.py`
  - `api\part_deps.py`
  - `api\v1\{features,model_registry,image_generations,segmentations,part_analyses,workflows}.py`
  - `schemas\runs.py`
  - `models\runs.py`
  - `storage\run_store.py`
  - `storage\run_layout.py`
  - `pipelines\runs\{__init__,executor,reporter,context}.py`
  - `workers\run_worker.py`
  - `services\run_events.py`
  - `services\{feature,model_registry,image_generation,segmentation,part_analysis,workflow}_service.py`
  - `services\part_files.py`
- New tests:
  - `test\backend\test_gpu_task_seam.py`
  - `test\backend\test_import_boundaries.py`
  - `test\backend\test_run_framework.py`
  - `test\backend\fakes_runs.py`

## Tests
- Own tests: 69 passed (run 3 times in a row, about 19 s each time, no flakes).
  - `test_run_framework.py`, 39 tests:
    - `RunStore`: round trip, listing, recovery, the deleted-job folder never resurrected (stale-index and pre-check
      variants), tombstones;
    - reporter: throttling, monotonic percent, 2 s persistence;
    - CPU and GPU threads, plus guard/ensure_vram/offload order;
    - skipped steps;
    - cancel while queued, in a GPU phase, in a CPU phase, and in the gate;
    - shutdown;
    - OOM → `out_of_memory`, other errors → `run_error`, `AppError` codes, `insufficient_vram`;
    - missing executor or registry;
    - `pa_` log kept;
    - event throttling;
    - watchdog ignores the gate (3 s held, 2 s timeout) and GPU queueing; timeout with a token-ignoring callable
      (`work_dir` still present when it returns);
    - stop waits for the callable; stop timeout leaves `work_dir`;
    - gate holds while a job is pending; gives up after `run_max_defer_s=1` with `gpu_defer_timeout`;
    - executor table and `import_dotted`;
    - publishers;
    - layout.
  - `test_gpu_task_seam.py`, 22 tests:
    - `gpu-worker` thread;
    - FIFO interleave job → aux A → aux B → job with `max_active == 1`;
    - refused after `stop()`;
    - shutdown with an in-flight aux run finishes within the timeout, the aux callable returns before `worker.stop()`,
      and the engine is still unloaded;
    - app starts with the flag on and off, with the empty routers answering 404 on 18 paths;
    - startup recovery with the flag on and off;
    - job delete forgets analyses and purges their cache;
    - event types;
    - WebSocket job scoping of `part_analysis.updated`;
    - envelope of every §6.8 class;
    - `require_feature`;
    - settings defaults, env parsing and ranges.
  - `test_import_boundaries.py`, 8 tests:
    - I7 AST scan of the 10 torch-free packages, including literal `importlib.import_module`;
    - rule 2 for the new inference packages;
    - layering for the 51 part-aware modules present today;
    - I10 (no tuner imports);
    - no `sys.path` edits;
    - a self-test of the scanner;
    - a subprocess import of 53 torch-free modules, including `parts.*`, `core.container` and `main`, loads no heavy
      package.
- Full suite `backend\python\python.exe -s -m pytest -c test\pytest.ini test\backend test\aux_models -q
  -p no:cacheprovider`: **391 passed, 2 skipped** in 93 s.
  - Baseline: 182 before the milestone; 313 passed / 2 skipped when this run resumed.
  - The 78 new passes are these 69 tests plus 9 from parallel agents.
  - The 2 skips are S0's (they un-skip with S10).
  - All 182 old tests are green and no assertion was changed.
  - The only warning is the existing "Unknown config option: cache_dir", caused by `-p no:cacheprovider`.

## Deviations from DESIGN.md (with reasons)
1. **`StepDef.skip_reason`** (an extra optional field). §4.3 says steps that do not apply are "returned and pre-marked
   skipped", but `StepDef` had no place for the reason. `steps_from_plan()` turns it into a `skipped` step whose
   message is the reason.
2. **Extra keyword parameters with defaults.**
   - `RunWorker`: `on_queue_changed` (drives `features.updated`), `publish_interval`, `persist_interval` and
     `gate_poll_interval` (tests).
   - `build_part_aware`: `job_queue` (the jobs-first gate input) and `reference_store` (in `ExecutorDeps`).
3. **Extra `RunWorker` methods** (`enqueue`, `cancel_queued`, `queue_status`, `stopping`, `started`) beyond §4.3. These
   are the operations the services need (429/503 at creation, cancel of a queued run, `/features.queues.runs`).
4. **Executor construction contract:** `Executor(deps: ExecutorDeps)`, with fields `settings`, `layout`, `registry`,
   `job_store`, `reference_store` and `stores`. §11 only says executors resolve "lazily through dotted-path tables";
   one constructor shape keeps the container frozen.
5. **Job-scoped writes use the store's own `_write_atomic`**, not `write_json_atomic`. The shared writer calls
   `path.parent.mkdir(parents=True)`, which would recreate a deleted job folder (I2 / §4.3).
6. **Extra error classes** `InsufficientVramError`, `JobDeletedError` and `ServiceNotImplementedError` in
   `core/feature_errors.py`. §5.3 names `InsufficientVramError`, and §4.3 names `JobDeletedError`. The stub error is
   how "unavailable means 503 with a reason" (I8) holds before each slice merges.
7. **Shutdown wording.** A run cancelled by shutdown ends `failed`/`interrupted`. The step that was running when the
   cancel arrived records `cancelled`, because the token reached it; the run status follows §4.3.
8. **`features.updated` dedup:** identical payloads are not re-sent (debounce plus dedup). Clients still fetch
   `/features` after every `hello`.

## Requests for other slices
- **Integrator:** pin the worker now that S-a is in:
  `backend\python\python.exe -s test\backend\test_protected_files.py --add backend/workers/generation_worker.py`.
  This was not done here, as instructed.
- **Owner of `backend/api/v1/ws.py`** (no slice owns it; S0 asked S1): change its docstring to point at contract §8 for
  the event list. It is not in S1b's file list, so it was left untouched.
- **S2:**
  - `inference/providers/registry.py` must define `ProviderRegistry(settings, catalog, *, engine, overrides, on_change)`.
    `overrides` is the `PartProviderOverrides` (use `.providers` by model id).
  - `inference/providers/catalog.py` must export `CATALOG` (a sequence of `ModelSpec`).
  - The registry must satisfy `pipelines.runs.context.RunProviderRegistry`: `guard()`, `ensure_vram(ids)`,
    `offload(ids)`, `specs()`, `status(id)`.
  - `FeatureService` reads `executors.availability(kind).code` (`ok` / `not_implemented` / `error`) and
    `runs.queue_status()`.
  - Call `FeaturesEventPublisher.notify()` (through `FeatureService.notify_changed()` or the registry's `on_change`) on
    state changes.
- **S4 / S5 / S10:**
  - Executors live at `pipelines.image_generation:ImageGenerationExecutor`, `pipelines.segmentation:SegmentationExecutor`
    and `pipelines.part_analysis:PartAnalysisExecutor`, constructed as `Cls(ExecutorDeps)`, with `kind` and `prefix`
    attributes.
  - Services create runs with `runs.enqueue(run)` (it persists, queues and publishes); creation gives 429 when full and
    503 while stopping.
  - Cancel with `runs.cancel_queued(id)`, and otherwise `runs.cancel_running(id)`.
  - Read live state with `runs.live_snapshot(id)` before the store.
  - Flag checks use `core.feature_errors.require_feature(settings, "<feature>")`.
- **S8:** `services/run_retention_service.py:RunRetentionService(settings=, layout=, stores=, workflow_store=)` with
  `async run_forever(interval_s)`. The container starts it only when the layer is enabled. `RunStore.list_terminal()`
  (oldest first) and `delete()` are available.
- **S9:** `FeatureDisabledError(message, feature=, flag=)` and `Settings.mesh_tuning_enabled` exist.
- **S11:** `WorkflowService.start()`/`stop(timeout)` are called by the container. The workflow event publisher
  serialises with `WorkflowService.to_out(record)`.

## Open issues
- `generation_worker.py` is not yet in `protected_files.sha256` (integrator step above).
- The `api/v1/ws.py` docstring is still stale (see Requests).
- The DESIGN gives no explicit service constructor signatures. The ones written here are now frozen (§11) and are
  listed in the stub modules.
