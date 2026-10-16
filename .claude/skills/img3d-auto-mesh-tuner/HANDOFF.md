# Auto Mesh Tuner — handoff (paused 2026-10-15 ~14:35 at the user's request, token budget)

Read in this order to resume: this file → `DESIGN.md` (authoritative contract) → `progress/*_report.md` →
`research/*.md` only as needed.

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
| Launcher (detect/select/download/runner) | `backend/launcher/*` , `test/launcher/*` | DONE — 124 tests pass (incl. real 5.2.2/4.2.17 detection, kill-tree, live network resolve → 5.2.2 x64 sha256 3849d17a…). Report: `progress/launcher_report.md` |
| Backend API/service/worker/CLI | settings, container, lifecycle, `api/v1/{blender,tunings}.py`, services, `workers/tuning_worker.py`, `scripts/auto_mesh_tuner.py`, api-contract.md | DONE — 182 backend tests pass (138 baseline + 44 new, 1 real-Blender). Report: `progress/backend_report.md` (11 deviations listed there — fold into DESIGN.md "Deviations") |
| Frontend UI | `frontend/src/**` (components/meshTuner/*, store/meshTunerStore.ts, api/{blender,tunings}.ts, viewer toggle), `test/frontend/mock_backend.mjs` | DONE vs mock only — lint/tsc/build/compiler-check green (91 compiled fns), mock smoke 36/36. NOT yet run against the real backend. Report: `progress/frontend_report.md` |
| Tuner framework (protocol, config, context, io, meshdata, analysis, pipeline, cli, report, previews, validation) | `backend/blender_scripts/auto_mesh_tuner.py`, `img3d_amt/*` | DONE — 59 tests. Report: `progress/tuner_framework_report.md` |
| Rig optimization step | `img3d_amt/rig.py`, `test/mesh_tuner/rig/*` | DONE — 12 tests; says ONE framework test now fails (see its Requests item 1). Report: `progress/tuner_rig_report.md` |
| Repair step (inner shell, islands, normals, holes) | `img3d_amt/{repair,visibility,holes}.py`, `test/mesh_tuner/repair/*` | **INTERRUPTED mid-work** — files exist (repair.py 46 KB, visibility.py 35 KB, holes.py 38 KB) but unverified; no report |
| Refine steps (face/eyes, symmetry, smoothing, topology/density) | `img3d_amt/{face,symmetry,smoothing,topology}.py`, `test/mesh_tuner/refine/*` | **INTERRUPTED mid-work** — files exist, unverified; no report |
| Tuner integration on all real jobs | all `blender_scripts`, `test/mesh_tuner/test_pipeline_real.py`, `test/outputs/mesh_tuner/integration/summary.md` | NOT STARTED |
| Live E2E (real backend + UI + real tuning), adversarial review, skills/memory finalization | — | NOT STARTED |

## Resume plan (next session)
1. Re-run the remaining agents with the prompts in `progress/implement_workflow.js` (constants REPAIR, REFINE,
   INTEGRATION; the old run id cannot be resumed across sessions). Tell repair/refine agents their files already
   exist from an interrupted run: read them, finish, verify — don't restart from scratch. Then INTEGRATION.
2. Fix cross-area requests from the reports: framework test broken by rig (rig report item 1); frontend: handle
   `confirmation_required`, release `host`, log URL for failed runs, `back`/`face_texture` previews + `shading` key;
   fold backend/framework deviations into DESIGN.md.
3. Live E2E: start backend (`backend\python\python.exe -s backend\main.py`), check `GET /api/v1/blender/runtime`
   (expect 5.2.2 selected, 4.2.17 incompatible), run Auto Tune on a real job from the UI, Original/Tuned toggle.
   Optionally test the real portable download (≈404 MB → `backend\runtime\blender\`; E: has ~700 GB free) — ask the user first.
4. Adversarial review workflow → fix → write `SKILL.md` for this folder, update `.claude/CLAUDE.md` status +
   `img3d-run-and-test` commands.

## Rules learned this session
- Never open/process/render `backend/runtime/jobs/job_269a864ac597` (excluded input; reason in `CLAUDE.local.md`).
- Blender: always `--python-exit-code`, map `blender-launcher.exe` → `blender.exe`, Job Object kill-tree, per-run TEMP,
  `BLENDER_USER_RESOURCES` isolation, paths ≤ 259 chars, User-Agent required for download.blender.org (403 otherwise).
- Test commands: `backend\python\python.exe -s -m pytest -c test\pytest.ini test\launcher test\backend -q -p no:cacheprovider`
  and `... test\mesh_tuner\framework test\mesh_tuner\rig -q` (marker `blender`; network tests need `-m network`).
