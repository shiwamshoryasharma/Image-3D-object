# Auto Mesh Tuner — handoff

**PAUSED again on 2026-10-16** at the user's request (they want to add/optimize something new first).
First pause: 2026-10-15 ~14:35 (token budget). Read in this order to resume: this file → `DESIGN.md`
(authoritative contract) → `progress/*_report.md` → `progress/resume_briefs.md` → `research/*.md` only as needed.

## User request (verbatim source: `E:\14-OCT-2026\Img3D\.prompts\02.txt`)
Add "Auto Mesh Tuner": fix TRELLIS.2 meshes (finer mesh, less deformation, eye correction, one-side holes / missing
layers) via Blender >= 5.1 in `--background`, with automatic Blender detection (execute each exe for its real version,
highest stable >= 5.1, never 3.x/4.x), runtime priority (embedded pin > system > project-local portable > prompt),
exact prompt "Blender {version} is installed but Auto Mesh Tuner requires Blender 5.1 or newer. Download a supported
portable Blender?", official portable ZIP download (sha256-verified, resumable, safe-extract into runtime/blender, no
admin, never touch system Blender), process runner (stdout/stderr/exit code/timeouts/cancel), setup UI (detected
versions, selected runtime, System/Embedded, path, version, compatibility, download progress, Change Runtime, Auto Tune).

## Key finding
Every TRELLIS.2 GLB is a hollow **double-walled shell** (inner wall ~2 voxels behind the outer surface on 74–84% of
faces, joined at hole rims) — the root of the "missing layers / little hole on one side" symptom. Repair step removes
the inner wall via GPU per-face visibility (`gpu.init()` works in background on 5.2) + paired-wall ray test.

## Status per piece
| Piece | Owner files | Status |
|---|---|---|
| Launcher (detect/select/download/runner) | `backend/launcher/*`, `test/launcher/*` | DONE — 124 tests. Report: `progress/launcher_report.md` |
| Backend API/service/worker/CLI | settings, container, lifecycle, `api/v1/{blender,tunings}.py`, services, `workers/tuning_worker.py`, `scripts/auto_mesh_tuner.py` | DONE — 182 backend tests. Report: `progress/backend_report.md` (11 deviations, not yet folded into DESIGN.md) |
| Frontend UI | `frontend/src/**` meshTuner parts, `test/frontend/mock_backend.mjs` | DONE vs mock only (36/36 mock smoke). 2026-10-16 fixes applied (see below). NOT yet run against the real backend |
| Tuner framework | `blender_scripts/auto_mesh_tuner.py`, `img3d_amt/*` framework modules | DONE — 59 tests. 2026-10-16: suite now runs with the 4 processing steps STUBBED (see below) — **re-run not finished** |
| Rig optimization step | `img3d_amt/rig.py`, `test/mesh_tuner/rig/*` | DONE — 12 tests. Its rig_* option request is now implemented in `config.py` |
| Repair step (inner shell, islands, normals, holes) | `img3d_amt/{repair,visibility,holes}.py`, `test/mesh_tuner/repair/*` | **UNFINISHED** — files exist, unverified. Last real run (job_04535) died after 744 s in GPU visibility (view ~98/102, rc 4294967295); budget is 60 s. Head slice single-walled after, mid-torso still double. No report |
| Refine steps (face/eyes, symmetry, smoothing, topology) | `img3d_amt/{face,symmetry,smoothing,topology}.py`, `test/mesh_tuner/refine/*` | **UNFINISHED** — files exist, unverified; one new uncommitted `symmetry.py` change (below). No report |
| Tuner integration on all real jobs | all `blender_scripts`, `test/mesh_tuner/test_pipeline_real.py`, `test/outputs/mesh_tuner/integration/summary.md` | NOT STARTED |
| Live E2E (real backend + UI + real tuning), adversarial review, skills/memory finalization | — | NOT STARTED |

## What happened on 2026-10-16 (all UNCOMMITTED on `main`; last pushed commit `feb7bfe`)
Repair + refine subagents were relaunched with `progress/resume_briefs.md` and **stopped after a few minutes** by the
pause. Their only changes:
- `img3d_amt/symmetry.py` (refine): `local_repair` screens with the KD-tree nearest mirrored vertex, refines with exact
  BVH surface distance (`mirror_distances`) above `HYSTERESIS*tau`; noise floor from 20k samples; `tau >= TAU_MIN_PCT`
  (0.15 % diag); `CLUSTER_MAX_EXTENT_PCT` 2.5 → 4.0. Coherent, parses as Python 3.11, **not run in Blender yet**.
- `test/mesh_tuner/repair/probes/bmesh_op_cost_probe.py` (repair, untracked): bmesh operator vs direct removal cost probe.

Lead (cross-area requests from the reports):
- **Framework suite isolation**: `test/mesh_tuner/framework/conftest.py` `tuner` fixture now runs
  `blender_helpers/run_with_injected_steps.py --mode stubs` (new mode: repair/face/topology/rig → skipped
  "stubbed for the framework test"), so the suite tests only the framework and no longer breaks as the real steps
  land; `test_malformed_json_and_missing_args_exit_2` uses the real entry script on 5.2 (entry-path coverage);
  the 4.2 test still uses the real entry. **Run `pytest test/mesh_tuner/framework` first when resuming (expect 59 pass).**
- `img3d_amt/config.py`: optional `rig_min_weight` (0..0.1, 0.001), `rig_max_influences` (1..8, 4),
  `rig_smooth_iterations` (0..20, 3), `rig_pose_checks` (true), `rig_pose_bones` (0..200, 40) — cfg.json/CLI only,
  not in the REST TuningOptions body (backend schema forbids unknown keys).
- Frontend: preview views `back` / `face_texture` labelled + ordered (`lib/meshTuner.ts`), `TuningPreview.shading?`
  and `BlenderRelease.host?` types (`api/types.ts`), release host shown from `host` (`BlenderDownloadPanel.tsx`),
  download start errors mapped by code incl. `confirmation_required` (`store/meshTunerStore.ts`). Gates passed:
  oxlint 0, tsc 0, `npm run build` OK, compiler check `[]` / 91 compiled functions.
- Already satisfied (no change needed): failed-run log URL fallback, 8 pending steps on new runs
  (`models/tuning.py default_steps`), large `pose_max_stretch` formatting.

## Resume plan (next session)
1. `backend\python\python.exe -s -m pytest -c test\pytest.ini test\mesh_tuner\framework -q -p no:cacheprovider` → 59 pass.
2. Backend `shading` passthrough (NOT started): add `shading: str | None = None` to `TuningPreview` in
   `backend/models/tuning.py`, to `TuningPreviewOut` in `backend/schemas/mesh_tuner.py` (built ~line 237), and carry it
   in `workers/tuning_worker.py::_move_previews` (~line 627); run `test/backend` (182).
3. Relaunch repair + refine agents with `progress/resume_briefs.md` (both in one message, background). Then the
   INTEGRATION brief from `progress/implement_workflow.js` (own agent or in-session).
4. Fold deviations into `DESIGN.md` "Deviations": framework report (10), backend report (11), rig report (5),
   frontend choices (4), plus the 2026-10-16 items above.
5. Live E2E: start backend (`backend\python\python.exe -s backend\main.py`), check `GET /api/v1/blender/runtime`
   (expect 5.2.2 selected, 4.2.17 incompatible), run Auto Tune on a real job from the UI, Original/Tuned toggle.
   Optional real portable download (≈404 MB → `backend\runtime\blender\`) — ask the user first.
6. Adversarial review → fix → write `SKILL.md` for this folder, update `.claude/CLAUDE.md` + `img3d-run-and-test`.
7. Commit (author `Shiwam Shorya Sharma <110902859+shiwamshoryasharma@users.noreply.github.com>`, no co-author
   trailer) and push only when the user asks — see `.claude/skills/img3d-repo-publishing/SKILL.md`.

## Rules learned
- Never open/process/render `backend/runtime/jobs/job_269a864ac597` (excluded input; reason in `CLAUDE.local.md`).
  It is no longer present in `runtime/jobs`; keep excluding it anyway.
- A newer job `job_fa0c4771ec03` exists with unknown content — not in any test list; do not use it unless the user
  says so.
- Blender: always `--python-exit-code`, map `blender-launcher.exe` → `blender.exe`, Job Object kill-tree, per-run TEMP,
  `BLENDER_USER_RESOURCES` isolation, paths ≤ 259 chars, User-Agent required for download.blender.org (403 otherwise).
- The Workflow tool needs the user's explicit opt-in; resume with Agent-tool subagents (`resume_briefs.md`).
- After stopping agents, check for orphaned `blender.exe` (`Get-CimInstance Win32_Process -Filter "Name='blender.exe'"`).
- Test commands: `backend\python\python.exe -s -m pytest -c test\pytest.ini test\launcher test\backend -q -p no:cacheprovider`
  and `... test\mesh_tuner\framework test\mesh_tuner\rig -q` (marker `blender`; network tests need `-m network`).
