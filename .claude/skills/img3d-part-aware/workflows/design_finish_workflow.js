export const meta = {
  name: 'img3d-part-aware-design-finish',
  description: 'Finish the part-aware design: UI_DESIGN.md synthesis, 3 adversarial critics, sequential revision of both docs, resolution check',
  phases: [
    { title: 'Synthesize', detail: 'frontend synthesis -> UI_DESIGN.md (uses DESIGN.md API shapes)' },
    { title: 'Critique', detail: 'requirements, backend-feasibility, frontend-feasibility critics' },
    { title: 'Revise', detail: 'DESIGN.md first, then UI_DESIGN.md against the revised API' },
    { title: 'Check', detail: 'verify every blocker/major is resolved; one fix pass if not' },
  ],
}

const ROOT = 'E:/14-OCT-2026/Img3D'
const PA = ROOT + '/.claude/skills/img3d-part-aware'
const RES = PA + '/research'
const DES = PA + '/design'

const CONTEXT = `
PROJECT: Img3D at ${ROOT} (Windows 11, RTX 5090 32 GB, 130 GB RAM). Backend: FastAPI, embedded Python 3.14 at ${ROOT}/backend/python/python.exe (ALWAYS run with -s), torch 2.14.1+cu130, transformers 4.57.6 + huggingface-hub 0.36.2 pinned for TRELLIS.2, diffusers 0.39.0. Frontend: React 19 + Vite 8 + TS 6 + Zustand 5 + React Three Fiber, React Compiler (babel-plugin-react-compiler) and oxlint gates; Node v22.17.0. Project rules: ${ROOT}/.claude/CLAUDE.md. Existing contract: ${ROOT}/.claude/skills/img3d-architecture/api-contract.md.
MILESTONE (user request, verbatim in ${PA}/workflows/design_workflow.js USER_REQUEST): Tripo-style part-aware backend (semantic decomposition before/after reconstruction; physical parts for eyes, eyebrows, eyelashes, hair, clothes, accessories, detachable objects; continuous face/body stays one mesh with semantic regions; parts keep relationships and are easy to optimise/reassemble later; modular for future tuning that consumes individual parts; NO tuning algorithms; tuning/refinement disabled until the user approves; do not break the working generation pipeline), prompt-to-3D + image-to-3D, and FOUR UI modes selectable in Settings and persisted locally (1 Default = current UI preserved, polished spacing/transitions/animations/responsiveness/loading states/panels; 2 Node UI = ComfyUI-style node workflow with nodes Input Image, Prompt, Segmentation, Image-to-3D, Part Extraction, Export, connectable, modular engine for future tuning nodes; 3 Mobile = app-style, floating bottom nav, touch-first, large targets, sheets/drawers, generation controls easy to reach; 4 Tablet = landscape floating left nav, portrait floating bottom nav, auto switch by orientation + width). Prompt and image generation available in every mode; no duplicated backend logic between modes; one shared API/state layer; responsive everywhere, no fixed layouts. Architecture: Backend API -> generation pipeline -> part-aware asset system -> shared state/API layer -> multiple UI renderers.
AUTHORITATIVE DOCS (precedence): user messages > ${PA}/DECISIONS.md (user's model + policy choices) > ${PA}/DESIGN.md (backend + API, authoritative) > research ${RES}/*.md.
OPEN-QUESTION RESOLUTIONS for DESIGN.md section 12 (apply them; do not flag them as open):
- Q1 approved models: YES, the six DECISIONS.md models are approved by default (Z-Image-Turbo, grounding-dino-base, sam2.1-hiera-large, Florence-2-large, See-through l2d_sam_iter2, sapiens2-seg-0.4b). [user decision]
- Q4 vendoring: YES, DECISIONS.md says the See-through and Sapiens2 model code is vendored (small modules only, never their requirements); use pinned git submodules under backend/third_party/ + THIRD_PARTY_NOTICES rows + Sapiens2 licence conditions shown in the UI. [user decision]
- Q2 manual candidate pick inside a workflow: NO for v1 (two-call review flow only; D9 stays deferred). [assistant default, user may override]
- Q3 tuning code gate: YES, code gate in MeshTunerService.create_run + the one conftest line; Blender detection/download endpoints stay as they are but every tuner/Blender UI mount point is hidden in all four modes. [assistant default]
- Q5 SD1 intermediates: NOT approved -> deferred, flag stays inert, protected files untouched. [assistant default]
- Q6 SD2 bf16 re-save: NOT approved -> deferred. [assistant default]
- Q7 eye cut scope: default (cut only lifted eye faces; eyelid ring stays a base region). [assistant default]
HARD CONSTRAINTS: image-to-3D path and model.glb byte-identical when part features are off; frozen JobStage list / percent formula / /system keys; additive API (request bodies reject unknown fields, so new fields optional); single GPU worker thread, sequential model loads with offload; no new heavy native deps; never upgrade transformers/huggingface-hub (install only with -c backend/constraints.txt --only-binary ":all:"); tuning stays disabled; no fake/mock generation data in the app (test doubles only under test/); all tests, test scripts, caches and configs live under ${ROOT}/test only; sample images ${ROOT}/test/images/{ALICE,ANIME_CHARACTER_01}. Never open jobs job_269a864ac597 or job_fa0c4771ec03.
STYLE: concrete and implementable: exact paths, signatures, JSON shapes, tables, test files + assertions, slices with DISJOINT file ownership and acceptance criteria. No TBD placeholders.`

const FRONT_PROPS = `${DES}/frontend_a_shells.md ; ${DES}/frontend_b_command_engine.md`

phase('Synthesize')
const ui = await agent(`${CONTEXT}

YOUR TASK: write the AUTHORITATIVE frontend design for this milestone to ${PA}/UI_DESIGN.md (create it; do not modify any other file).
Inputs:
- The two frontend proposals: ${FRONT_PROPS}. Read both fully.
- The frontend judge verdicts: ${DES}/workflow_results.json is a JSON list; the entries whose value has a 'scores' array with keys frontend_a_shells / frontend_b_command_engine are the three frontend judges (requirements, feasibility, simplicity lenses). Totals were frontend_a_shells 41 + ... (A won every judge). Read their best_ideas_to_graft and fatal_flaws in full and apply them: start from A (shells over shared state), graft the listed B ideas, fix every fatal / near-fatal flaw (e.g. Node v22.17 needs a glob for --experimental-strip-types --test; force effective source to 'image' when /features is 404 or prompt is unsupported; anchored vendor chunk regex; pure .ts modules for anything Node-tested).
- The backend design ${PA}/DESIGN.md: you MUST use exactly its API shapes. Read fully: section 0, 2.2-2.3 (groups/classes), 3.10-3.13 (manifest fields the UI reads), 4.4 (prompt-to-3D incl. presets/styles/review flow), 4.7 (workflows, graph format, issue codes, legacy_job_node_map), 6 (every endpoint, FeaturesOut, ModelOut, CatalogOut, events, errors, 6.9 action table), 8.2 (tuning gate exposure). Skim the rest. If the UI needs something DESIGN.md lacks, put it in a 'Backend requests' section instead of inventing a shape.
- Current frontend: ${ROOT}/frontend/src (142 files) and ${RES}/frontend_architecture_map.md + ${RES}/ui_modes_research.md. Verify the file paths you reference exist.
UI_DESIGN.md must contain: 0 goals/constraints/invariants (Default UI unchanged by default; image-to-3D request byte-identical to today's when parts are off; tuner UI hidden behind the /features mesh_tuning flag in all modes; no deletion of working functionality; uncommitted/paused tuner frontend files untouched except hiding mount points); 1 folder structure + layering (api / stores / actions / workflow engine / ui-mode shells / shared panels / components) with exact paths, what moves vs stays; 2 shared API layer additions (typed client functions for every DESIGN.md section 6 endpoint the UI uses, WS event handling, features store with legacy 404 handling); 3 preferences store, storage keys, Settings dialog, no-flash mode application, 'auto' mode resolution rules; 4 the four modes in detail: Default polish list (concrete: spacing tokens, transitions, skeleton/loading states, panel behaviours, reduced-motion); Node UI (pure-TS engine: types, node registry from CatalogOut, typed ports/colours, validation mirroring server issue codes, compile-to-WorkflowRequest, run via POST /workflows/validate then /jobs or /workflows, step/stage->node status incl. legacy_job_node_map, persistence + versioning, locked future tuning nodes) + React Flow (@xyflow/react 12.12.0, lazy loaded) adapter + accessible list/stepper fallback; Mobile shell (floating bottom nav, sheets with snap points, FAB, touch density tokens >=44px targets, safe areas, gestures); Tablet shell (rail vs bottom nav switching rules with exact breakpoints, orientation, hysteresis); 5 prompt + image input UX in every mode (prompt presets/styles from /features, review-first vs one-shot, cold start notice); 6 parts UX (part list, visibility toggles, highlight/isolate in the R3F viewer using the assembled GLB nodes part:<id> and _FEATURE_ID_0, physical / physical_deferred / region badges with the /features notes, per-part and assembled downloads via payload URLs only); 7 accessibility checklist; 8 performance/bundle budget + chunking; 9 responsiveness matrix (exact viewports to verify, e.g. 360x800, 390x844, 768x1024 portrait, 1024x768 landscape, 1180x820, 1366x768, 1920x1080); 10 quality gates + tests (engine unit tests under test/frontend/unit runnable with node --experimental-strip-types --test <glob>; golden request fixtures under test/frontend/fixtures; mock backend extensions in test/frontend/mock_backend.mjs mirroring DESIGN.md section 6 exactly with scenarios full, no-t2i, no-seg, disabled, legacy404; npm run build, npm run lint, test/frontend/review/compiler_check.mjs); 11 implementation slices with DISJOINT file ownership, order, dependencies on backend slices, acceptance criteria (sized for parallel implementer agents); 12 open questions (only genuinely undecided ones); 13 backend requests (if any).
Return a <= 400 word summary.`, { label: 'synth:frontend', phase: 'Synthesize' })
log('UI_DESIGN.md written')

phase('Critique')
const CRIT_SCHEMA = { type: 'object', properties: {
  file: { type: 'string' }, blockers: { type: 'number' }, majors: { type: 'number' }, minors: { type: 'number' },
  summary: { type: 'string' } }, required: ['file', 'blockers', 'majors', 'minors', 'summary'] }
const CRITICS = [
  { key: 'critic_requirements', lens: 'REQUIREMENTS COVERAGE + CROSS-DOC CONSISTENCY: go clause by clause through the user request and check DESIGN.md + UI_DESIGN.md cover each one concretely (not just mentioned). Check backend and UI designs agree EXACTLY on every endpoint, request/response field, event, flag, issue code and URL field used by the UI. Check the open-question resolutions above are reflected. Flag over-engineering and simpler alternatives that keep all requirements.' },
  { key: 'critic_backend_feasibility', lens: 'BACKEND FEASIBILITY + SAFETY: open the cited backend files under ' + ROOT + '/backend and test files under ' + ROOT + '/test and verify DESIGN.md claims ([V] marks, line numbers, signatures): would anything break the working generation pipeline, seed parity, pinned tests (JobStage list, percent formula, /system keys, the 20 tuner API tests), file-route auth, or Windows/py3.14 behaviour? Is GPU/VRAM sequencing on the single gpu-worker thread sound (Z-Image 23.3 GB peak, TRELLIS.2, seg stack 2.1 GB)? Are the slices truly disjoint and correctly ordered (S0..S11)? Are the geometry algorithms (weld, shells/inner-sheet merge, partition vmaps, seams, raster lifting with nvdiffrast, glTF writer) implementable with numpy/scipy only within the stated time budgets on a 2M-face mesh?' },
  { key: 'critic_frontend_feasibility', lens: 'FRONTEND FEASIBILITY: open the current frontend code under ' + ROOT + '/frontend/src, package.json, vite.config.ts, tsconfig*, test/frontend/* and verify UI_DESIGN.md: React Compiler rules (no mutation during render, hooks rules), oxlint config, zustand 5 patterns already used, R3F viewer structure (how part visibility/highlight would actually work with the GLB loader in use), @xyflow/react 12.12.0 + React 19 compatibility, lazy-loading/chunking, Node 22.17 strip-types test runner limits (extensionless imports fail), mobile sheets/safe-area CSS feasibility, and that every referenced existing file/symbol exists. Are the frontend slices disjoint, ordered, and consistent with the backend slice order?' },
]
const crits = (await parallel(CRITICS.map(c => () => agent(`${CONTEXT}

YOU ARE AN ADVERSARIAL DESIGN CRITIC. Lens: ${c.lens}
Read ${PA}/DESIGN.md and ${PA}/UI_DESIGN.md (fully for the parts your lens covers) plus any code/research needed to verify. Write your critique to ${DES}/${c.key}.md as a numbered list of concrete issues, each with severity (blocker/major/minor), evidence (file:line or doc section), and the exact fix. Only real issues: do not pad. Do not edit the design docs or any other file. Return {file, blockers, majors, minors, summary (<= 250 words)}.`, { label: c.key, phase: 'Critique', schema: CRIT_SCHEMA })))).filter(Boolean)
log('critiques: ' + crits.map(c => `${c.file.split('/').pop()} B${c.blockers}/M${c.majors}/m${c.minors}`).join(', '))
const critFiles = crits.map(c => c.file).join(' ; ')

phase('Revise')
const backRev = await agent(`${CONTEXT}

YOUR TASK: revise ${PA}/DESIGN.md (edit it in place with targeted edits; keep its structure; only this file plus DECISIONS.md) to resolve every blocker and major issue that concerns the BACKEND design or the API contract in the critiques ${critFiles} (minors where cheap). Verify each issue against the code before changing anything; reject an issue only with evidence. Also: rewrite section 12 so it records the open-question resolutions given above (mark which are user decisions and which are assistant defaults the user may override) and lists only genuinely open items. In ${PA}/DECISIONS.md append a short section 'Defaults applied 2026-10-16 (assistant, pending user override)' listing Q2, Q3, Q5, Q6, Q7 defaults (do not change the user's decisions). If the UI needs an API addition that a critique or UI_DESIGN.md 'Backend requests' asks for and it is reasonable, add it to DESIGN.md section 6 (additive only). Append a 'Revision log (2026-10-16)' section to DESIGN.md listing each critique item you handled (critic file + number) and how it was resolved or why it was rejected. Return a <= 300 word summary including every API shape you changed or added (exact names) so the frontend reviser can follow.`, { label: 'revise:backend', phase: 'Revise' })

const frontRev = await agent(`${CONTEXT}

YOUR TASK: revise ${PA}/UI_DESIGN.md (edit in place; only this file) to resolve every blocker and major issue that concerns the FRONTEND design or cross-doc consistency in the critiques ${critFiles} (minors where cheap). DESIGN.md has just been revised by the backend reviser; its summary of API changes: <<<${backRev}>>>. Re-read DESIGN.md section 6 (and any section the critiques cite) and make UI_DESIGN.md use exactly those shapes; remove 'Backend requests' items that DESIGN.md now covers. Verify each issue against the frontend code before changing anything; reject only with evidence. Append a 'Revision log (2026-10-16)' section listing each critique item handled and how. Return a <= 300 word summary.`, { label: 'revise:frontend', phase: 'Revise' })

phase('Check')
const CHECK_SCHEMA = { type: 'object', properties: {
  unresolved: { type: 'array', items: { type: 'object', properties: {
    critic: { type: 'string' }, item: { type: 'string' }, severity: { type: 'string' }, problem: { type: 'string' }, fix: { type: 'string' } },
    required: ['critic', 'item', 'severity', 'problem', 'fix'] } },
  inconsistencies: { type: 'array', items: { type: 'string' } },
  verdict: { type: 'string' } }, required: ['unresolved', 'inconsistencies', 'verdict'] }
const check = await agent(`${CONTEXT}

YOU ARE A SKEPTICAL RESOLUTION CHECKER. For every blocker and major item in the critiques ${critFiles}, check whether the revised ${PA}/DESIGN.md and ${PA}/UI_DESIGN.md actually resolve it (read the relevant sections; a revision-log claim is not proof). Also diff the API shapes: every endpoint/field/event/flag/URL field the UI_DESIGN.md uses must exist with the same name and type in DESIGN.md section 6 (list each mismatch). Read-only. Return unresolved items (with exact fix), inconsistencies, and a one-paragraph verdict.`, { label: 'check:resolution', phase: 'Check', schema: CHECK_SCHEMA })

let fix = null
if (check && (check.unresolved.length || check.inconsistencies.length)) {
  log(`check: ${check.unresolved.length} unresolved, ${check.inconsistencies.length} inconsistencies -> fix pass`)
  fix = await agent(`${CONTEXT}

YOUR TASK: apply these remaining fixes to ${PA}/DESIGN.md and/or ${PA}/UI_DESIGN.md (targeted in-place edits; keep both docs consistent; DESIGN.md section 6 is the API source of truth). Unresolved critique items: ${JSON.stringify(check.unresolved)}. Cross-doc inconsistencies: ${JSON.stringify(check.inconsistencies)}. Verify against code where needed. Add the items to each doc's 'Revision log (2026-10-16)'. Return a <= 200 word summary.`, { label: 'fix:final', phase: 'Check' })
}

return { ui, crits, backRev, frontRev, check, fix }
