export const meta = {
  name: 'img3d-part-aware-backend-phase-b',
  description: 'Backend Phase B (lean): one implementer per slice — S2, S3a->S3b, S4, S5->S6, S7, S8 — no reviewer agents',
  phases: [{ title: 'Implement', detail: 'six independent chains, GPU steps serialised by gpu_lock.py' }],
}
const ROOT = 'E:/14-OCT-2026/Img3D'
const PA = ROOT + '/.claude/skills/img3d-part-aware'
const PY = 'backend\\python\\python.exe -s'
const SUITE = `${PY} -m pytest -c test\\pytest.ini test\\backend test\\aux_models -q -p no:cacheprovider`

const RULES = `
Img3D part-aware milestone, repo ${ROOT} (Windows 11). Read: ${ROOT}/.claude/CLAUDE.md, ${PA}/SKILL.md, ${PA}/DECISIONS.md, and in ${PA}/DESIGN.md (authoritative; use Grep to jump) §0, §1.4, §11 (your row) plus the sections your task names. S0 and S1 are merged: build on the existing frozen interfaces (schemas/*, models/*, parts/{types,taxonomy,manifest}.py, inference/*/base.py, services/workflow_catalog.py, core/part_aware_container.py lazy dotted-path tables, pipelines/runs/*, workers/run_worker.py, ExecutorDeps) — read them; do not edit them (they belong to S1); if one is wrong, work around it and report it.
OWNERSHIP: create/edit ONLY the files in your §11 row. Other implementers run IN PARALLEL in the same tree; never edit, reformat or revert their files.
ENV: ${ROOT}/backend/python/python.exe ALWAYS with -s, from the repo root; install nothing; tests/scripts/caches/configs only under ${ROOT}/test, outputs under test/outputs/part_aware/. Every command that loads real weights on the GPU: ${PY} test\\part_aware\\gpu_lock.py --label <slice> -- <command>. Real inputs only from test/images or the read-only fixtures in test/part_aware/fixtures.json (copy into test/.runtime/<slice>/ before writing anything). Never open backend/runtime/jobs (never job_269a864ac597 / job_fa0c4771ec03). Never print backend/.env values. No git commit/stash/reset/checkout. No tuning work.
QUALITY: production code in the surrounding style; no placeholders; no fake data in the app (doubles only under test/).
TOKEN BUDGET IS TIGHT: read only what you need (Grep before Read; do not read whole 3000-line docs), keep reports short.
VERIFY: your tests, then ${SUITE} (currently 392 passed, 2 skipped). Failures caused by other in-flight slices: report, do not fix.
REPORT: ${PA}/progress/<slice>.md (<= 60 lines: built, files, test counts, measured numbers, deviations, requests, open issues) and return the structured result.`

const RESULT = { type: 'object', properties: {
  task: { type: 'string' }, status: { type: 'string', enum: ['done', 'partial', 'blocked'] },
  tests: { type: 'string' }, measured: { type: 'string' },
  deviations: { type: 'array', items: { type: 'string' } }, requests: { type: 'array', items: { type: 'string' } },
  open_issues: { type: 'array', items: { type: 'string' } },
}, required: ['task', 'status', 'tests', 'deviations', 'requests', 'open_issues'] }

const T = (id, extra) => `${RULES}\n\nTASK ${id}: implement DESIGN.md slice ${id} per its §11 row (Owns / Depends on / Acceptance) and the sections it references. ${extra}\nReturn {task:'${id}',...}.`
const run = (id, extra) => agent(T(id, extra), { label: id, phase: 'Implement', schema: RESULT })

const results = await parallel([
  () => run('S2', 'Sections §4.8, §4.9, §5.3-§5.6, §6.1. Real on-disk states on this machine must come out right (Z-Image, GDINO, SAM 2.1, Florence-2, lifter installed; See-through/Sapiens2 vendored_source_missing until S6).'),
  () => run('S3a', 'Sections §3.1-§3.5, §3.12, §4.6.5 (incl. pair_opposite_faces for the rim-joined double wall). Measure on the real alice_front fixture read in place (nothing written): weld+components+shells+partners time and component-0 partner coverage.')
        .then(a => run('S3b', `S3a has just finished (its result: ${JSON.stringify(a)}). Sections §2.4, §3.6-§3.11, §3.13, §3.14, §4.6.5 labelling steps.`).then(b => [a, b])),
  () => run('S4', 'Sections §1.2 S-f, §4.4, §6.2, §6.7, §9.2. The real Z-Image smoke runs under gpu_lock.py. Report the measured first .to("cuda") time after the CPU load.'),
  () => run('S5', 'Sections §4.5, §6.3. Real GDINO+SAM2.1 smoke on test/images ALICE and ANIME_CHARACTER_01 under gpu_lock.py; report masks found, the tiara/eyebrow case and warm time.')
        .then(a => run('S6', `S5 has just finished (its result: ${JSON.stringify(a)}). The user approved vendoring (DECISIONS.md): add shitagaki-lab/see-through and facebookresearch/sapiens2 as pinned git submodules under backend/third_party/ (git submodule add is allowed for exactly these two; never install their requirements). READ every vendored module you import before importing it (no network calls, no code execution at import beyond model definitions); if a module needs a package that is not installed, stop and report instead of installing.`).then(b => [a, b])),
  () => run('S7', 'Sections §4.6.4 cameras, §4.6.5 lifting, §3.7. GPU test under gpu_lock.py; camera-fit IoU on both fixtures copied into test/.runtime/s7/.'),
  () => run('S8', 'Sections §7.2. Use a FakeWorkflowStore with the frozen §7.2 API.'),
])
return results
