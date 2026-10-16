I've integrated the Auto Mesh Tuner into the FastAPI backend as specified in DESIGN §1, §3, §4 and §6, and the full backend suite passes: **182 passed** (138 baseline + 44 new). That includes one test against the real installed Blender 5.2.2.

## Files
**Created:**
- `backend/models/tuning.py`
- `backend/storage/tuning_store.py`
- `backend/schemas/mesh_tuner.py`
- `backend/services/blender_runtime_service.py`
- `backend/services/mesh_tuner_service.py`
- `backend/workers/tuning_worker.py`
- `backend/api/v1/blender.py`
- `backend/api/v1/tunings.py`
- `backend/scripts/auto_mesh_tuner.py`
- `test/backend/fakes_blender.py`
- `test/backend/blender_side/fake_tuner.py`
- `test/backend/test_blender_runtime_api.py`
- `test/backend/test_mesh_tuner_api.py`

**Edited:**
- `backend/config/settings.py`, `backend/core/container.py`, `backend/core/lifecycle.py`
- `backend/main.py` (only the new `create_app` keyword arguments)
- `backend/api/router.py`, `backend/api/deps.py`, `backend/api/websocket_hub.py`
- `backend/schemas/events.py`, `backend/services/job_service.py`, `backend/.env.example`
- `.claude/skills/img3d-architecture/api-contract.md` (new §7.5, plus settings, errors, WebSocket and runtime sections) and `SKILL.md` (layering line)
- `test/conftest.py` (startup detection off by default)
- `test/backend/conftest.py` (registers the `blender` marker; needed because pytest runs with `--strict-markers`)

## Public API
- **Settings:** all twelve `IMG3D_BLENDER_*` fields from §6. Derived paths:
  - runtime dir: `<runtime_dir>/blender` unless `IMG3D_BLENDER_INSTALL_DIR` is set
  - per-run temp: `temp/blender`
  - scripts: `backend/blender_scripts`

  The Blender fields are added to `safe_summary`.
- **`create_app` / `build_container`** take two optional test hooks: `blender_runtime_factory=(launcher_settings, on_change) -> BlenderRuntimeManager` and `blender_runner=`.
- **Endpoints:** every route in §6, with the status and error codes as specified. A "Blender not available" error (503 `blender_unavailable`) carries the runtime's exact message, e.g. "Blender 4.2.17 is installed but Auto Mesh Tuner requires Blender 5.1 or newer. Download a supported portable Blender?"
- **Errors:** launcher errors are mapped to new error classes in `blender_runtime_service.py`:

  | Status | Code |
  |---|---|
  | 404 | `blender_not_found` |
  | 422 | `blender_incompatible`, `blender_path_too_long` |
  | 503 | `blender_unavailable` |
  | 403 | `blender_download_disabled` |
  | 409 | `blender_download_in_progress`, `blender_download_cancelled` |
  | 502 | `blender_release_unresolved`, `blender_download_failed`, `blender_checksum_mismatch` |
- **WebSocket:** `blender.updated` (download-progress frames can be dropped under load) and `tuning.updated` (progress throttled to ≤ 5/s; status and step changes are never dropped). A `?job_id=` connection only gets `job.updated` and `tuning.updated` for that job.
- **Worker:** its own asyncio consumer and queue, plus a single `blender-worker` thread (never the GPU thread).
  - It copies the job's model to `temp/tuning/<tn>/input.glb`, writes `cfg.json`, and runs Blender through the launcher runner with cancel, timeout, log file, user-resources dir and per-run temp.
  - It re-checks that the job still exists, then moves the outputs into `jobs/<job>/tuning/<tn>/` and computes mesh stats off the event loop.
- **Lifecycle:**
  - Startup: interrupted runs are marked failed (`interrupted`); detection runs as a background task.
  - Shutdown: kills Blender and cancels any download.
  - Deleting a job first cancels its tunings and waits for Blender to exit.

## Testing
- Command: `backend/python/python.exe -s -m pytest -c test/pytest.ini test/backend test/aux_models -q -p no:cacheprovider` → 182 passed in 64 s.
- The 44 new tests ran three more times: all passed each time.
- `test/launcher` plus the new files together: 167 passed, 1 skipped (the launcher's network test, which needs an explicit opt-in).
- **Real-Blender test** (`fake_tuner.py` through the real runner and worker, Blender 5.2.2): a full run took 1.9 s. Cancel took 0.04 s and the Blender process was confirmed gone.
- **Real tuner through the backend, on a copy of a 79 MB job:**
  - Completed in 58.3 s.
  - The tuned GLB is 89 MB; computing its stats took about 0.5 s.
  - API latency during the run: median 2.4 ms, max 21 ms (the event loop was never blocked).
- **CLI:**

  | Command | Result |
  |---|---|
  | `detect --json` | 0.59 s, exit 0; 5.2.2 LTS selected, 4.2.17 shown as incompatible ("older than 5.1") |
  | `status` | 0.39 s |
  | `release` (network) | 1.9 s; 5.2.2 LTS x64, 385.7 MB, sha256 `3849d17a…b535` (matches DESIGN) |
  | `tune` on a cube | 3.3 s |
  | `tune` on the real 40 MB job_046e19f0ca62 | 31 s |

  A fake-backed test covers the download prompt: declined → exit 3; `--yes` → downloads, then runs.
- Outputs are in `test/outputs/mesh_tuner/backend/`.

## Known limitations
- Deleting a job waits up to 30 s for its cancelled Blender to exit (normally about 0.1 s).
- The hourly retention cleanup deletes old jobs without the tuning hook. A run caught by this is dropped at finalize, not cancelled.
- The CLI's default output folder, `runtime/cli_tunings`, is never cleaned automatically.
- Running the CLI wrote `backend/runtime/blender/blender_runtime.json` and `detect_cache.json`. That is where the app keeps its runtime selection, so this is expected.

## Deviations from DESIGN
1. The test hook is a factory (`blender_runtime_factory`) rather than a manager instance, because `BlenderRuntimeManager` only accepts `on_change` in its constructor.
2. New error code `confirmation_required` (422) when `confirm` is not true. A generic launcher error (e.g. `blender_invalid_mode`) returns 422 with its own code.
3. Cancelling a run that has already finished returns 409.
4. The report and log routes use the file auth guard (header or `?token=`).
5. Extra response fields: `build_hash`, `build_date` and `probe_ms` on each installation; `host` on the release.
6. `GET /blender/runtime` starts a background scan if none has run yet.
7. `IMG3D_BLENDER_SEARCH_DIRS` accepts a JSON list or a `;`-separated string.
8. Failed runs keep `blender.log`, `config.json` and `report.json` (if any), so the log route works for them too.
9. Step warnings appear live during a run and are replaced by the result's warning list on completion.
10. The options body rejects unknown keys with 422, following the project's request-model convention.
11. `JobService` gained `add_delete_hook()`.

## Requests for other areas
- **DESIGN owner:** record deviations 1–11 in the Deviations section.
- **Frontend:**
  - Handle the `confirmation_required` code.
  - Use the new `host` field on the release.
  - For a failed run, `result` is null, so build the log URL from the run id (`/api/v1/tunings/{id}/log`).
- **Blender-side agents:** on real runs, `geometry_repair` and `face_refinement` currently report "skipped: not implemented yet".
- **`test/pytest.ini` owner:** optionally register the `blender` marker centrally.