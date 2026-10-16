export const meta = {
  name: 'img3d-part-aware-backend-phase-a-resume',
  description: 'Backend Phase A: S0 contract+guards, S1 skeleton (interfaces + plumbing in parallel), integration, adversarial review, fixes',
  phases: [
    { title: 'Implement', detail: 'S0, S1a (frozen interfaces), S1b (runtime plumbing) in parallel' },
    { title: 'Integrate', detail: 'reconcile S1a/S1b, protect generation_worker.py, full suite, app start with flags on/off' },
    { title: 'Review', detail: 'adversarial design-conformance + invariant review' },
    { title: 'Fix', detail: 'apply confirmed findings, re-run everything' },
  ],
}

const ROOT = 'E:/14-OCT-2026/Img3D'
const PA = ROOT + '/.claude/skills/img3d-part-aware'
const PY = 'backend\\python\\python.exe -s'
const SUITE = `${PY} -m pytest -c test\\pytest.ini test\\backend test\\aux_models -q -p no:cacheprovider`

const RULES = `
You are working on the Img3D part-aware milestone at ${ROOT} (Windows 11). Read first: ${ROOT}/.claude/CLAUDE.md, ${PA}/SKILL.md, ${PA}/DECISIONS.md, and the DESIGN.md (${PA}/DESIGN.md, authoritative, ~3000 lines) sections named in your task plus §0 (invariants I1-I12), §1 (layout, seams, protected files, layering) and §11 (slices). Use Grep to jump to sections. Existing API contract: ${ROOT}/.claude/skills/img3d-architecture/api-contract.md.
OWNERSHIP: create/edit ONLY the files your task owns. Other agents work IN PARALLEL in the same working tree on other files. Never edit, reformat, revert or delete files you do not own, even to fix a failing test; report it instead.
ENVIRONMENT: backend Python is ${ROOT}/backend/python/python.exe, ALWAYS run with -s, from the repo root; never any other Python. Install nothing (no pip/uv/npm). Tests, test scripts, caches and configs live only under ${ROOT}/test; outputs under test/outputs/part_aware/. Any command that loads real model weights onto the GPU must be wrapped: ${PY} test\\part_aware\\gpu_lock.py --label <task> -- <command>. Never open/list/analyse backend/runtime/jobs (never job_269a864ac597 or job_fa0c4771ec03). Never print or write backend/.env values. Do NOT git commit/stash/reset/checkout. Do not touch the paused tuner work beyond what you own.
QUALITY: production code matching the surrounding style (module docstrings, type hints, naming, comment density); no TODO stubs for things you own (S1 stubs are intentional where DESIGN.md says stub: frozen constructor + methods raising a clear not-implemented AppError / returning honest 'not_implemented' states); no fake data in the app (test doubles only under test/).
VERIFY: run your own tests, then the full suite: ${SUITE} (baseline before this milestone: 182 passed). Failures caused by files owned by other in-flight agents are not yours: report them.
REPORT: write ${PA}/progress/<task>.md (built, files, test counts, deviations from DESIGN.md with reasons, requests for other slices, open issues) and return the structured result.`

const RESUME = `
RESUME NOTE: a previous run of this task was STOPPED mid-way by the user's token budget. Files it wrote already exist in the tree and are INCOMPLETE/UNVERIFIED: read them first, keep what is correct, complete what is missing (including the tests your task owns), fix what is wrong; do not rewrite working code from scratch. 'git status' lists every touched file. Current full suite on this tree: 313 passed, 2 skipped (182 baseline + S0 + S1a tests). S0 is DONE (report ${PA}/progress/S0.md); its files are not yours.`

const RESULT = { type: 'object', properties: {
  task: { type: 'string' }, status: { type: 'string', enum: ['done', 'partial', 'blocked'] },
  files: { type: 'array', items: { type: 'string' } },
  tests: { type: 'object', properties: { command: { type: 'string' }, passed: { type: 'number' }, failed: { type: 'number' }, skipped: { type: 'number' }, notes: { type: 'string' } }, required: ['passed', 'failed', 'notes'] },
  deviations: { type: 'array', items: { type: 'string' } },
  requests: { type: 'array', items: { type: 'string' } },
  open_issues: { type: 'array', items: { type: 'string' } },
}, required: ['task', 'status', 'files', 'tests', 'deviations', 'requests', 'open_issues'] }

const S0 = `${RULES}

TASK S0 "Contract + guards" (DESIGN §11 row S0, §1.3, §10.1, §10.2). Owns exactly:
- ${ROOT}/.claude/skills/img3d-architecture/api-contract.md: add a new section "11. Part-aware layer" carrying DESIGN §2-§8 in contract form (taxonomy summary, manifest img3d.parts/1 summary, run resources + state machine, provider/registry states, every §6 endpoint table and JSON shape, WS events, errors/warning codes, storage layout, flags/settings), plus the settings rows, error codes and the five WS events in the contract's existing sections, plus the drift fixes listed in ${PA}/research/backend_contract_map.md §6.1 item 10. Mark it "Status: implemented incrementally (S1-S11); pending user review at milestone approval".
- ${ROOT}/.claude/skills/img3d-architecture/SKILL.md: layering note for the new packages (parts/, inference/providers, run framework, workflows) and the torch import rule.
- ${PA}/SKILL.md (already exists as an index written today): only extend it if something important is missing; keep it short.
- test/backend/test_protected_files.py + test/backend/protected_files.sha256 (sha256 after CRLF->LF normalisation of every §1.3 file EXCEPT workers/generation_worker.py, which the S1 integrator adds later). The test names the drifted file and says the change needs the user's approval. Include a small helper (in the test module or a CLI flag) that regenerates the hash file deliberately.
- test/backend/test_legacy_invariance.py + test/backend/fixtures/legacy_job_v1/job.json (a committed pre-change job.json fixture built from the current JobStore/Job model shape with fake values: no real user data; model.glb written at test time by test/backend/fakes.py helpers). Items: legacy-load, retry round-trip, byte/key identity of image jobs with part_aware_enabled true AND false (the setting does not exist until S1 lands: make those parametrised cases skip cleanly with a reason while Settings lacks the field, and pass once it exists), and the model.glb-untouched item skipped with reason "un-skipped by S10". The part-analyses list check skips until the route exists.
Acceptance: 182 existing + your new tests green (skips allowed only as described). Return {task:'S0',...}.`

const S1A = `${RULES}

TASK S1a "Frozen interfaces" = the data/interface half of DESIGN slice S1 (§11 row S1). Another agent (S1b) writes the runtime plumbing half IN PARALLEL: config/settings.py, workers/generation_worker.py, core/*, main.py, api/*, schemas/events.py, schemas/runs.py, models/runs.py (RunRecord, RunStep, RunProgress, RunWarning, ModelUse, StepDef...), storage/run_store.py, storage/run_layout.py, pipelines/runs/*, workers/run_worker.py, services/run_events.py, the stub services and services/part_files.py. Import those by the exact names DESIGN.md gives (e.g. models.runs.RunRecord); if they do not exist yet when you test, keep going and note it.
You own exactly (all NEW backend files, paths relative to backend/):
- schemas/{features,model_registry,image_generation,segmentation,parts,workflows}.py: ALL wire models of DESIGN §6 (+ §4.4.1, §4.5.x, §4.6.x, §4.7.x request/response models), RequestModel extra=forbid / ApiModel extra=ignore exactly like the existing schemas/common.py patterns.
- models/{image_generation,segmentation,part_analysis,workflow}.py: domain records (subclass models.runs.RunRecord per §4.3; results; workflow record per §4.7).
- parts/{__init__,types,taxonomy,manifest}.py: §2.3 taxonomy (data only, taxonomy_version=1, PartClass fields, aliases, z_priority, match_terms, part-id rules incl. Windows reserved names), §3.1-§3.10 types and the pydantic manifest img3d.parts/1 (with the §3.11 example able to round-trip), torch-free.
- inference/providers/{__init__,base}.py and inference/{imagegen,segmentation,parts}/{__init__,base}.py: §5.1 torch-free types and §5.2 role ABCs (only files and plain dataclasses cross the seam).
- services/workflow_catalog.py: §4.7.2 / §6.5 static node/port/template tables, params_schema per node type, legacy_job_node_map, locked refine nodes (hidden_by_default true).
- storage/workflow_store.py: the frozen-API stub of §7.2 (S11 implements it later; the stub's methods must exist with their final signatures).
- test/backend/test_taxonomy_manifest.py (§10.2 row).
Read DESIGN §2, §3, §4.3-§4.7 (request/record models), §5.1-§5.2, §6 (every shape), §7.2, §8.1 carefully: these are FROZEN interfaces later slices code against, so field names, types, defaults, literals and validation must match the document exactly. Where the document is internally inconsistent, follow §6 for wire shapes and record the choice. ${RESUME}
Return {task:'S1a',...}.`

const S1B = `${RULES}

TASK S1b "Runtime plumbing" = the wiring half of DESIGN slice S1 (§11 row S1). Another agent (S1a) writes the frozen data interfaces IN PARALLEL: schemas/{features,model_registry,image_generation,segmentation,parts,workflows}.py, models/{image_generation,segmentation,part_analysis,workflow}.py, parts/{__init__,types,taxonomy,manifest}.py, inference/providers/{__init__,base}.py, inference/{imagegen,segmentation,parts}/{__init__,base}.py, services/workflow_catalog.py, storage/workflow_store.py. Import those by the exact names DESIGN.md gives; if they do not exist yet when you test, keep going (lazy imports where DESIGN says lazy) and note it. Agent S0 writes test_protected_files.py/protected_files.sha256 (do NOT edit that hash file; the integrator adds generation_worker.py after you).
You own exactly (paths relative to backend/ unless they start with test/):
- EDIT config/settings.py (§8.1 fields, additive; safe_summary unchanged), workers/generation_worker.py (§1.2 S-a submit_gpu_task + accepting_gpu_tasks, ~15 lines, nothing else), core/container.py + core/lifecycle.py (§1.2 S-b exact edits), main.py (create_app(..., part_providers=None)), api/router.py, api/websocket_hub.py (§1.2 S-c), schemas/events.py (5 EventType members).
- NEW core/feature_errors.py (§6.8 classes mapped through the existing error envelope/handlers), core/part_aware_container.py (PartAwareContainer, PartProviderOverrides, build_part_aware(); lazy dotted-path executor/provider tables so later slices never edit it; start()/stop() semantics of §1.2 S-b incl. stop BEFORE worker.stop; with part_aware_enabled=false only stores load), api/part_routes.py, api/part_deps.py, empty routers api/v1/{features,model_registry,image_generations,segmentations,part_analyses,workflows}.py (mounted, no routes yet → 404).
- NEW schemas/runs.py and models/runs.py (§4.3 run records, steps, progress, warnings, ModelUse, StepDef, RunKind...).
- NEW storage/run_store.py (generic RunStore[R], atomic save/load, recover_interrupted, refusal to recreate a deleted job folder: mkdir(parents=False), Windows replace retries), storage/run_layout.py (§7.1 paths over RuntimePaths; paths.py is protected, do not edit it).
- NEW pipelines/runs/{__init__,executor,reporter,context}.py and workers/run_worker.py (§4.3 executor protocol, RunContext, RunReporter throttling 5/s + persist ≤ every 2 s, RunWorker loop incl. CPU 'parts-cpu' executor, GPU phases via submit_gpu_task, registry.guard()/ensure_vram/offload calls through the registry Protocol, jobs-first gate with run_max_defer_s and warning gpu_defer_timeout, watchdog counting only active phase time, error mapping incl. classify_exception → out_of_memory/run_error, shutdown → interrupted, work_dir cleanup only after the GPU callable returned). Read §4.3, §4.8, §4.11 fully.
- NEW services/run_events.py (publishers for the five WS events, debounce/throttle rules of §6.6), stub services services/{feature,model_registry,image_generation,segmentation,part_analysis,workflow}_service.py with FROZEN constructors (DESIGN gives the dependencies) + services/part_files.py stub.
- NEW tests test/backend/test_gpu_task_seam.py, test/backend/test_import_boundaries.py, test/backend/test_run_framework.py (§10.2 rows; fakes go in new test/backend/fakes_runs.py which you also own).
Acceptance: app starts with part_aware_enabled true and false (TestClient via the existing test helpers), new routers mounted and empty (404), all 182 old tests green with no assertion change, the three new test files green. ${RESUME}
Return {task:'S1b',...}.`

phase('Implement')
const S0_DONE = { task: 'S0', status: 'done', files: [], tests: { passed: 313, failed: 0, notes: 'finished in the stopped run; see progress/S0.md' }, deviations: [], requests: ['see progress/S0.md'], open_issues: [] }
const impl = [S0_DONE, ...(await parallel([
  () => agent(S1A, { label: 'S1a interfaces', phase: 'Implement', schema: RESULT }),
  () => agent(S1B, { label: 'S1b plumbing', phase: 'Implement', schema: RESULT }),
])).filter(Boolean)]
log('implement: ' + impl.map(r => `${r.task}:${r.status} ${r.tests.passed}p/${r.tests.failed}f`).join(' | '))

phase('Integrate')
const integ = await agent(`${RULES}

TASK S1-INTEGRATE. Agents S0, S1a and S1b just finished in parallel. Their results: ${JSON.stringify(impl)}. Their reports are in ${PA}/progress/{S0,S1a,S1b}.md.
You now own ALL files of S0 and S1 (DESIGN §11 rows S0 and S1, plus test/backend/fakes_runs.py) and nothing else.
1. Reconcile S1a/S1b: every cross-import resolves; names/fields match DESIGN.md exactly; no duplicate definitions; requests listed in the results are handled.
2. Add workers/generation_worker.py to test/backend/protected_files.sha256 (S1 row) after confirming its diff is only the §1.2 S-a seam.
3. Verify: full suite ${SUITE} green with no existing assertion changed; app import + startup with part_aware_enabled true and false (both via TestClient and a real '${PY} backend\\main.py --no-preload' start against an ABSOLUTE test runtime IMG3D_RUNTIME_DIR=${ROOT}/test/.runtime/s1_integrate and IMG3D_LOG_DIR=${ROOT}/test/.runtime/s1_integrate/logs on a free port, then GET /api/v1/health and stop it); new routers return 404; protected files test green; import-boundaries test green; legacy invariance parametrised cases now run (not skipped) for part_aware_enabled true/false.
4. Fix whatever is broken inside your owned files. Write ${PA}/progress/S1_integrate.md. Return {task:'S1-integrate',...}.`, { label: 'S1 integrate', phase: 'Integrate', schema: RESULT })

phase('Review')
const FIND = { type: 'object', properties: { findings: { type: 'array', items: { type: 'object', properties: {
  severity: { type: 'string', enum: ['blocker', 'major', 'minor'] }, file: { type: 'string' }, line: { type: 'number' },
  problem: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' } },
  required: ['severity', 'file', 'problem', 'evidence', 'fix'] } }, summary: { type: 'string' } }, required: ['findings', 'summary'] }
const reviews = (await parallel([
  ['conformance', 'DESIGN CONFORMANCE of the frozen interfaces: compare schemas/{features,model_registry,image_generation,segmentation,parts,workflows,runs}.py, models/*, parts/{types,taxonomy,manifest}.py, inference/*/base.py, services/workflow_catalog.py, storage/workflow_store.py field-by-field against DESIGN §2-§8 (names, types, defaults, literals, extra=forbid vs ignore, validation limits, error codes). Every mismatch later slices would trip over is a finding.'],
  ['safety', 'SAFETY + CORRECTNESS of the plumbing: generation_worker seam, container/lifecycle start/stop order, RunWorker concurrency (single GPU thread, cancellation, watchdog, shutdown → interrupted, work_dir cleanup races, jobs-first gate), RunStore atomicity + job-deleted refusal on Windows, settings parsing (IMG3D_APPROVED_MODELS empty/";"/"," forms), websocket job-scoped events, error envelope, import-boundary test strength, protected-file hash coverage of §1.3, legacy invariance test strength. Run the suite and targeted experiments; look for real bugs, not style.'],
].map(([k, lens]) => () => agent(`${RULES}

YOU ARE AN ADVERSARIAL REVIEWER (read-only: do not edit any file). Lens: ${lens}
Scope: the S0 + S1 files (DESIGN §11 rows S0 and S1) as now in the tree; integration report ${PA}/progress/S1_integrate.md. Verify each finding against the code and DESIGN.md before reporting; only real issues with concrete evidence and an exact fix. Return findings + a short summary.`, { label: `review:${k}`, phase: 'Review', schema: FIND })))).filter(Boolean)
const findings = reviews.flatMap(r => r.findings).filter(f => f.severity !== 'minor' || true)
log(`review findings: ${findings.length} (${findings.filter(f => f.severity === 'blocker').length} blocker, ${findings.filter(f => f.severity === 'major').length} major)`)

phase('Fix')
let fix = null
if (findings.length) {
  fix = await agent(`${RULES}

TASK S1-FIX. You own ALL files of S0 and S1 (DESIGN §11 rows S0, S1, plus test/backend/fakes_runs.py). Apply these review findings (verify each first; fix blockers and majors, and minors when cheap and safe; reject only with evidence): ${JSON.stringify(findings)}.
Then re-run the full suite ${SUITE} and the startup check (part_aware_enabled true/false). Append a "Review fixes" section to ${PA}/progress/S1_integrate.md listing each finding → fixed / rejected (why). Return {task:'S1-fix',...}.`, { label: 'S1 fix', phase: 'Fix', schema: RESULT })
}
return { impl, integ, reviews, fix }
