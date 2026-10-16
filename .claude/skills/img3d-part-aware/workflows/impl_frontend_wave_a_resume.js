export const meta = {
  name: 'img3d-part-aware-frontend-wave-a-resume',
  description: 'Frontend Wave A: F0 test infra, F4 tokens/primitives, F1 API layer then F2 pure libs + F3 workflow engine; gates; adversarial review; fixes',
  phases: [
    { title: 'Implement', detail: 'F0 + F4 in parallel with F1 -> (F2 || F3)' },
    { title: 'Gate', detail: 'tsc, oxlint, compiler check, unit tests, mock smoke; reconcile' },
    { title: 'Review', detail: 'adversarial review of wave A against UI_DESIGN.md' },
    { title: 'Fix', detail: 'apply confirmed findings' },
  ],
}

const ROOT = 'E:/14-OCT-2026/Img3D'
const PA = ROOT + '/.claude/skills/img3d-part-aware'
const LOCK = `backend\\python\\python.exe -s test\\part_aware\\gpu_lock.py --lock frontend-build`

const RULES = `
You are working on the Img3D part-aware milestone frontend at ${ROOT}/frontend (React 19, Vite 8, TS 6, Zustand 5, R3F, React Compiler via babel-plugin-react-compiler, oxlint; Node v22.17.0; Windows). Read first: ${ROOT}/.claude/CLAUDE.md, ${PA}/SKILL.md, ${PA}/UI_DESIGN.md (authoritative frontend design, ~2800 lines: read §0, §1 fully, §10, §11, and every section your slice cites), and ${PA}/DESIGN.md §6 (the backend API shapes, authoritative for wire types). Use Grep to jump to sections.
OWNERSHIP: create/edit ONLY the files your slice owns (UI_DESIGN §11 row). Other agents work IN PARALLEL in the same tree on other files (backend agents are also editing backend/ and test/backend/ right now: never touch those). Never edit, reformat or revert files you do not own; report needs instead. Never edit src/components/meshTuner/** (paused tuner).
COMMANDS (run from ${ROOT}): every tsc / vite / oxlint / npm run invocation must be wrapped in the shared build lock: ${LOCK} --label <slice> -- <command> (e.g. ... -- npm --prefix frontend run lint ; ... -- npx --prefix frontend tsc -b frontend). Never build into frontend/dist: use npx --prefix frontend vite build frontend --outDir ${ROOT}/test/.runtime/frontend/<slice>/dist --emptyOutDir when you need a bundle. Unit tests: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs" (the glob form; the directory form fails on Node 22.17). React Compiler check: node test/frontend/review/compiler_check.mjs. Install nothing (no npm install; @xyflow/react is a later slice). Do NOT git commit/stash/reset/checkout (read-only git like git show / git archive is fine). Tests, fixtures, mocks, caches only under ${ROOT}/test; outputs under test/outputs/frontend/.
QUALITY: production code matching the existing frontend style (CSS modules, hooks, zustand patterns, naming, comment density); React Compiler-safe (no mutation during render, no module-level mutable refs read in render); pure modules (UI_DESIGN §1.5) use only relative imports with explicit .ts extensions and no react/zustand/axios so Node strip-types can import them.
REPORT: write ${PA}/progress/<slice>.md (built, files, gate results, deviations from UI_DESIGN.md with reasons, requests to other slices, open issues) and return the structured result.`

const RESUME = `RESUME NOTE: a previous run of this slice was STOPPED mid-way by the user's token budget. Files it wrote already exist and are INCOMPLETE/UNVERIFIED: read them first, keep what is correct, complete what is missing (including the unit tests the slice owns), fix what is wrong; do not rewrite working code from scratch. 'git status' lists every touched file. Current state: unit tests 86/86 pass; oxlint clean; tsc -b fails only with "src/lib/featureMeta.ts: Cannot find module './partsModel.ts'" (F2 had not written partsModel/runSteps/errorText yet). F1 (API layer) is DONE (report ${PA}/progress/F1.md); its files are not yours.`

const RESULT = { type: 'object', properties: {
  task: { type: 'string' }, status: { type: 'string', enum: ['done', 'partial', 'blocked'] },
  files: { type: 'array', items: { type: 'string' } },
  gates: { type: 'string', description: 'which gates ran and their results' },
  deviations: { type: 'array', items: { type: 'string' } },
  requests: { type: 'array', items: { type: 'string' } },
  open_issues: { type: 'array', items: { type: 'string' } },
}, required: ['task', 'status', 'files', 'gates', 'deviations', 'requests', 'open_issues'] }

const slice = (id, extra) => `${RULES}

TASK ${id}: implement UI_DESIGN.md slice ${id} exactly as its §11 row specifies (Owns / Acceptance) and the sections it references. ${extra}
Acceptance = the §11 acceptance column for ${id} (minus checks that need a browser or another slice that is not merged yet: list those as deferred in your report). ${RESUME}
Return {task:'${id}',...}.`

const F0 = slice('F0 Test infrastructure', `Notes: (1) the bundle baseline and protected_frontend.sha256 must come from commit 8654b90, NOT the live tree (other slices are editing frontend/src right now): export it read-only (git -C ${ROOT} archive 8654b90 frontend | tar -x -C ${ROOT}/test/.runtime/frontend/baseline_8654b90), give that copy access to frontend/node_modules (a directory junction: cmd /c mklink /J <copy>\\frontend\\node_modules ${ROOT}\\frontend\\node_modules) and build it under the build lock with --outDir test/.runtime/frontend/baseline_8654b90/dist. (2) The mock (test/frontend/mock_backend.mjs + test/frontend/mock/*) must mirror DESIGN.md §6 shapes EXACTLY, with strict request validation (extra_forbidden / conflicting_fields), the five scenarios, a request log with raw bodies, and a parts GLB with byteStride-4 _FEATURE_ID_0 and extras.img3d.id on every part node; the existing mesh_tuner_mock_smoke.mjs must keep passing. (3) ui_probe.js BASELINE needs today's UI rendered in a browser: if the built-in browser tools (mcp__Claude_Browser__*) are available to you, serve the baseline build with the mock (legacy404) and record it at the three baseline viewports; otherwise write the probe with BASELINE = null plus a clear "record me" instruction and report it as deferred.`)
const F4 = slice('F4 Tokens + primitives', `The desktop Default UI must look identical without data-density (except the P13 contrast tokens).`)
const F1 = slice('F1 API layer', `src/api/partAwareTypes.ts is the frozen §2.1 wire-type module the F2/F3 agents will import right after you finish: match §2.1 and DESIGN §6 exactly.`)
const F2 = slice('F2 Pure libraries', `F1 (API layer incl. src/api/partAwareTypes.ts) is merged; F0, F3 and F4 run in parallel with you. Golden request fixtures live under test/frontend/fixtures (owned by F0, written in parallel): if a fixture you need is missing, write the test to read it and report the request.`)
const F3 = slice('F3 Workflow engine', `F1 (API layer incl. src/api/partAwareTypes.ts) is merged; F0, F2 and F4 run in parallel with you. The node registry is built from the DESIGN §6.5 CatalogOut shape; refine nodes are locked and hidden by default.`)

phase('Implement')
const F1_DONE = { task: 'F1', status: 'done', files: [], gates: 'finished in the stopped run; see progress/F1.md', deviations: [], requests: [], open_issues: [] }
const resumed = await parallel([
  () => agent(F0, { label: 'F0 test infra', phase: 'Implement', schema: RESULT }),
  () => agent(F4, { label: 'F4 tokens+primitives', phase: 'Implement', schema: RESULT }),
  () => agent(F2, { label: 'F2 pure libs', phase: 'Implement', schema: RESULT }),
  () => agent(F3, { label: 'F3 workflow engine', phase: 'Implement', schema: RESULT }),
])
const impl = [F1_DONE, ...resumed].filter(Boolean)
log('wave A: ' + impl.map(r => `${r.task}:${r.status}`).join(' | '))

phase('Gate')
const gate = await agent(`${RULES}

TASK FRONTEND-WAVE-A-GATE. Slices F0, F1, F2, F3, F4 just finished (results: ${JSON.stringify(impl)}; reports in ${PA}/progress/). You now own ALL files of those five slices (UI_DESIGN §11 rows F0-F4) and nothing else.
Run every §10.1 gate that applies at this stage under the build lock: tsc -b, oxlint, compiler check (0 events), all unit tests (glob form), test/frontend/review/import_boundaries.mjs, bundle_budget.mjs against a private build, the mock smoke for all 5 scenarios, mesh_tuner_mock_smoke.mjs. Reconcile cross-slice mismatches (F2/F3 vs F1 types, fixtures vs tests, requests in the results) inside your owned files. Confirm the Default UI is behaviourally unchanged (no caller of the new modules yet except tests; no src/components/meshTuner edit: check with git diff --stat). Write ${PA}/progress/F_waveA_gate.md. Return {task:'F-waveA-gate',...}.`, { label: 'wave A gate', phase: 'Gate', schema: RESULT })

phase('Review')
const FIND = { type: 'object', properties: { findings: { type: 'array', items: { type: 'object', properties: {
  severity: { type: 'string', enum: ['blocker', 'major', 'minor'] }, file: { type: 'string' }, line: { type: 'number' },
  problem: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' } },
  required: ['severity', 'file', 'problem', 'evidence', 'fix'] } }, summary: { type: 'string' } }, required: ['findings', 'summary'] }
const review = await agent(`${RULES}

YOU ARE AN ADVERSARIAL REVIEWER (read-only; do not edit files). Review frontend wave A (UI_DESIGN §11 rows F0-F4) as now in the tree, plus ${PA}/progress/F_waveA_gate.md. Check: wire types vs DESIGN.md §6 field by field (every endpoint, enum literal, optional/nullable); mock fidelity to DESIGN §6 (status codes, error envelope, strict validation, scenarios, WS events); request builders' G1 byte-identity with today's POST /jobs body (compare against the current frontend request code); workflow engine validation/compile vs DESIGN §4.7.2 issue codes and the graph ≡ simple-form rule; pure-module import rules; React Compiler safety of F4 primitives; Sheet/Dialog accessibility; anything that would break later slices. Run the gates/tests you need. Only real issues with evidence and exact fixes.`, { label: 'review wave A', phase: 'Review', schema: FIND })

phase('Fix')
let fix = null
if (review && review.findings.length) {
  fix = await agent(`${RULES}

TASK FRONTEND-WAVE-A-FIX. You own ALL files of UI_DESIGN §11 rows F0-F4. Apply these findings (verify each; fix blockers/majors, minors when cheap; reject only with evidence): ${JSON.stringify(review.findings)}. Re-run all wave-A gates. Append "Review fixes" to ${PA}/progress/F_waveA_gate.md. Return {task:'F-waveA-fix',...}.`, { label: 'wave A fix', phase: 'Fix', schema: RESULT })
}
return { impl, gate, review, fix }
