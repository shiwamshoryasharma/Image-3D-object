export const meta = {
  name: 'img3d-part-aware-frontend-waves-bc',
  description: 'Frontend waves B+C (lean): F5 stores/actions/hooks, then F6 + F7 + F8 in parallel, then F9a — one implementer per slice',
  phases: [{ title: 'Wave B', detail: 'F5' }, { title: 'Wave C', detail: 'F6 || F7 || F8, then F9a' }],
}
const ROOT = 'E:/14-OCT-2026/Img3D'
const PA = ROOT + '/.claude/skills/img3d-part-aware'
const LOCK = `backend\\python\\python.exe -s test\\part_aware\\gpu_lock.py --lock frontend-build`

const RULES = `
Img3D part-aware milestone frontend, ${ROOT}/frontend (React 19, Vite 8, TS 6, Zustand 5, R3F, React Compiler, oxlint; Node 22.17; Windows). Read: ${ROOT}/.claude/CLAUDE.md, ${PA}/SKILL.md, and in ${PA}/UI_DESIGN.md (authoritative; Grep to jump, do not read all 2900 lines) §0, §1.2-§1.6, §11 (your row) and the sections your row cites; ${PA}/DESIGN.md §6 for wire shapes; ${PA}/progress/F_waveA.md for requests addressed to you. Waves A is merged (api/, lib/, workflow/, styles, common primitives, test infra incl. mock + fixtures + ui_probe).
USER SPEC POINT (binding): the existing Default UI is preserved; the Auto Tune card and Blender pill stay where they are but LOCKED while /features.mesh_tuning.enabled is false (UI_DESIGN §3.9 "Tuner lock"); nothing working is deleted.
OWNERSHIP: create/edit only files in your §11 row; others work in parallel in the same tree (backend agents edit backend/ and test/backend/: never touch those). Never edit src/components/meshTuner/**.
COMMANDS from ${ROOT}: wrap every tsc/vite/oxlint/npm run in ${LOCK} --label <slice> -- <cmd>. Build only into ${ROOT}/test/.runtime/frontend/<slice>/dist (never frontend/dist). Unit tests: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs". Gates: tsc -b frontend, npm --prefix frontend run lint, node test/frontend/review/compiler_check.mjs, node test/frontend/review/import_boundaries.mjs, node test/frontend/review/protected_frontend.mjs, node test/frontend/ui_modes_mock_smoke.mjs, node test/frontend/mesh_tuner_mock_smoke.mjs. No npm install. No git commit/stash/reset/checkout. Do NOT use the browser pane (browser checks are batched later); list browser-only acceptance items as deferred.
TOKEN BUDGET IS TIGHT: Grep before Read, read only needed sections, keep the report short (<= 50 lines).
REPORT: ${PA}/progress/<slice>.md and return the structured result.`
const RESULT = { type: 'object', properties: {
  task: { type: 'string' }, status: { type: 'string', enum: ['done', 'partial', 'blocked'] }, gates: { type: 'string' },
  deferred: { type: 'array', items: { type: 'string' } }, deviations: { type: 'array', items: { type: 'string' } },
  requests: { type: 'array', items: { type: 'string' } }, open_issues: { type: 'array', items: { type: 'string' } },
}, required: ['task', 'status', 'gates', 'deferred', 'deviations', 'requests', 'open_issues'] }
const run = (id, phase, extra = '') => agent(`${RULES}\n\nTASK ${id}: implement UI_DESIGN.md slice ${id} per its §11 row. ${extra}\nReturn {task:'${id}',...}.`, { label: id, phase, schema: RESULT })

phase('Wave B')
const f5 = await run('F5', 'Wave B')
phase('Wave C')
const prev = `F5 result: ${JSON.stringify(f5)}.`
const wc = await parallel([
  () => run('F6', 'Wave C', prev),
  () => run('F7', 'Wave C', prev),
  () => run('F8', 'Wave C', prev),
])
const f9a = await run('F9a', 'Wave C', `${prev} F6/F7/F8 results: ${JSON.stringify(wc)}.`)
return { f5, wc, f9a }
