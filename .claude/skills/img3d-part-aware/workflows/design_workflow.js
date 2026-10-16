export const meta = {
  name: 'img3d-part-aware-design',
  description: 'Judge-panel design of the part-aware backend + four UI modes, synthesized, adversarially critiqued and revised',
  phases: [
    { title: 'Propose', detail: '3 backend + 2 frontend independent architecture proposals' },
    { title: 'Judge', detail: '3 judges per proposal set, scored' },
    { title: 'Synthesize', detail: 'authoritative DESIGN.md (backend+API) then UI_DESIGN.md' },
    { title: 'Critique', detail: 'requirements-coverage and feasibility critics' },
    { title: 'Revise', detail: 'apply critiques to both design docs' },
  ],
}

const ROOT = 'E:/14-OCT-2026/Img3D'
const PA = ROOT + '/.claude/skills/img3d-part-aware'
const RES = PA + '/research'
const DES = PA + '/design'

const USER_REQUEST = `Pause all mesh tuning/refinement work for now. First restructure and optimize the existing Image-to-3D backend around a Tripo-style part-aware pipeline.
Priority 1 - Backend architecture: Keep the current Image-to-3D generator. Add semantic asset decomposition before/after reconstruction. Separate physical assets where appropriate: eyes, eyebrows, eyelashes, hair, clothes, accessories, detachable objects. Keep continuous face/body topology as semantic regions instead of physically splitting everything. Support: prompt-to-3D, image-to-3D. Generated assets must preserve part relationships and be easy to optimize/reassemble later. Design the backend modularly so future tuning/refinement can consume individual parts. Do NOT implement tuning algorithms yet. Tuning/refinement remains disabled until I explicitly approve this backend layer. Do not break the existing working generation pipeline.
Priority 2 - UI system: Create four responsive UI layouts/styles selectable from Settings. 1. Current UI - Default: Preserve the existing UI as the default. Do not redesign it completely. Improve spacing, transitions, animations, responsiveness, loading states, panels and interaction quality. 2. Node UI: Add a ComfyUI-style node workflow. Nodes should represent generation stages such as: Input Image, Prompt, Segmentation, Image-to-3D, Part Extraction, Export. Allow connections between nodes. Keep the node engine modular for future tuning nodes. 3. Mobile UI: Dedicated smartphone layout. App-style interface. Floating bottom navigation. Touch-first controls. Large touch targets. Panels open as sheets/drawers. Generation controls must remain easy to access. 4. Tablet UI: Support both orientations: Landscape: floating left-side navigation. Portrait: floating bottom navigation similar to mobile. Layout must switch automatically based on orientation and available width.
UI requirements: Prompt-based generation and image-based generation must both be available from the UI. Users can switch UI style from Settings. Persist UI preference locally. Do not duplicate backend logic between UI modes. All UI modes must use the same API/backend. Maintain desktop, tablet and mobile responsiveness. Avoid fixed layouts that break at smaller resolutions.
Architecture: Backend API -> generation pipeline -> semantic/part-aware asset system -> shared state/API layer -> multiple UI renderers. Keep backend, generation, node graph logic and visual UI components separated.
Before modifying major working code: 1. Inspect the existing project structure. 2. Reuse working components. 3. Identify exactly what needs restructuring. 4. Implement incrementally. 5. Keep backward compatibility where practical. 6. Do not delete existing working functionality.
Current milestone: Backend part-aware architecture + four UI modes only. Do not proceed to automatic mesh tuning, eye correction, rig correction, reference-aware optimization or Blender tuning until I explicitly approve this milestone.`

const CONTEXT = `
PROJECT: Img3D at ${ROOT} (Windows 11, RTX 5090 32 GB, 130 GB RAM; embedded Python 3.14 backend/python/python.exe run with -s; torch 2.14.1+cu130; transformers 4.57.6 + huggingface-hub 0.36.2 pinned for TRELLIS.2; FastAPI backend; React 19 + Vite 8 + TS 6 + Zustand 5 + R3F frontend with React Compiler and oxlint). Authoritative existing contract: ${ROOT}/.claude/skills/img3d-architecture/api-contract.md. Project rules: ${ROOT}/.claude/CLAUDE.md.
USER REQUEST (verbatim):
${USER_REQUEST}
EVIDENCE (read the ones relevant to your task fully before designing; they contain verified file:line facts):
- ${RES}/backend_contract_map.md (API/service structure, pinned tests, extension points, tuner gating)
- ${RES}/backend_generation_map.md (generation path, intermediates, hook points A1-A4 / B0-B4, GPU sequencing)
- ${RES}/trellis2_internals.md (no native parts; voxel->face correspondence; lifting 2D masks; per-part export via shared atlas; texturing pipeline)
- ${RES}/part_aware_3d_research.md (Tripo/Hunyuan/open models, Windows feasibility, DCC/glTF part representation)
- ${RES}/segmentation_models_research.md (2D part segmentation options, taxonomy, physical-vs-region policy, eyelash rules)
- ${RES}/text_to_image_research.md (prompt-to-3D via diffusers 0.39.0 + Z-Image-Turbo / FLUX.2-klein-4B, pins, GPU sequencing, prompt template, licensing)
- ${RES}/environment_audit.md (what installs cleanly; constraints file; no new weights on disk except Animagine XL in a sibling project)
- ${RES}/frontend_architecture_map.md (current frontend, breakages, folder proposal, rules for React Compiler/zustand/oxlint)
- ${RES}/ui_modes_research.md (React Flow choice, pure TS workflow engine, mobile sheets/nav, tablet rail/bottom nav, a11y)
HARD CONSTRAINTS: keep the image-to-3D path and model.glb byte-for-byte behaviour unchanged when part features are off; do not change the pinned JobStage list / percent formula / /system keys (tests pin them); additive, backward-compatible API (request bodies reject unknown fields -> every new field optional); single GPU worker thread, models loaded sequentially with offload; no new heavy native deps (spconv/kaolin/flash-attn/xatlas unavailable); never upgrade transformers/huggingface-hub; tuning (Auto Mesh Tuner, eye/rig correction, refinement) stays disabled and feature-flagged with NO new tuning algorithms; no fake/mock generation data in the app (test doubles only under test/); MODEL CHOICES ARE PENDING THE USER'S DECISION (text-to-image: Z-Image-Turbo / FLUX.2-klein-4B / Animagine XL already on disk / none yet; segmentation: Grounding DINO + SAM 2.1 + Florence-2 baseline, optional anime parser See-through, optional SAM 3 gated, optional Sapiens2) - so design provider seams + a model registry with honest 'unavailable/not installed' states and licence metadata, and make the plan work whichever subset the user approves. Python code that runs in Blender is out of scope.
STYLE: concrete and implementable - exact module/file paths, class and function signatures, JSON shapes, endpoint tables with status/error codes, storage layout, event names, state machines, feature flags, test plan (which test files, what they assert), and an incremental implementation plan split into slices with DISJOINT file ownership suitable for parallel implementer agents, each slice with acceptance criteria. Never use placeholders like TBD for things you can decide; list genuinely open questions separately.`

const BACKEND_ANGLES = [
  { key: 'backend_a_incremental', angle: 'RISK-FIRST / INCREMENTAL: maximise backward compatibility and minimise change to working code. Prefer follow-up runs (like tuning runs) over modifying the generation job; the smallest set of new seams; strong guarantees model.glb and existing tests are untouched; feature flags everywhere.' },
  { key: 'backend_b_asset_model', angle: 'ASSET-MODEL-FIRST: start from the part-aware asset data model that future tuning/refinement and reassembly must consume - part taxonomy, physical part vs semantic region, part graph (parent/attachment/symmetry/occlusion/covers), per-face label arrays, seams/boundary loops for reversible splitting, transforms/frames, provenance, versioned manifest (img3d.parts/1), multi-node GLB + per-part GLBs sharing one atlas, refinement_state per part - then derive pipeline and API from it.' },
  { key: 'backend_c_pipeline_graph', angle: 'PIPELINE-GRAPH-FIRST: model generation as typed steps (text_to_image, preprocess, segmentation_2d, reconstruct, lift_labels, part_extraction, export, future: part_refine/tune) composed into a job plan that the Node UI graph compiles to; job kinds (image_to_3d, prompt_to_3d, part_decomposition re-run); step progress alongside the frozen stage list; provider registry for engines (t2i, segmentation, lifting, future 3D-native segmenters/completers); scheduling on the single GPU thread.' },
]
const FRONTEND_ANGLES = [
  { key: 'frontend_a_shells', angle: 'SHELLS-OVER-SHARED-STATE: keep existing stores/api; extract shared hooks/actions; split feature panels into content + card; four lazily loaded shells (default/node/mobile/tablet) that only compose shared panels; preferences store + Settings dialog; minimal churn to the Default UI with targeted polish.' },
  { key: 'frontend_b_command_engine', angle: 'COMMAND-LAYER + WORKFLOW-ENGINE-CENTRIC: a typed actions/command layer as the only way renderers change state; a pure TypeScript workflow engine (graph model, node registry, typed ports, validation, compile-to-request, stage/step-to-node status) used by ALL modes (linear modes render the same canonical graph as a form/stepper), so prompt/image/segmentation/part options are defined once; renderers are thin.' },
]

const PROPOSAL_SCHEMA = { type: 'object', properties: { file: { type: 'string' }, summary: { type: 'string' } }, required: ['file', 'summary'] }
const JUDGE_SCHEMA = {
  type: 'object',
  properties: {
    scores: { type: 'array', items: { type: 'object', properties: {
      key: { type: 'string' },
      requirements_coverage: { type: 'number' }, backward_compat_safety: { type: 'number' },
      feasibility_on_this_stack: { type: 'number' }, extensibility_for_future_tuning: { type: 'number' },
      clarity_implementability: { type: 'number' }, total: { type: 'number' },
      strengths: { type: 'array', items: { type: 'string' } }, weaknesses: { type: 'array', items: { type: 'string' } },
    }, required: ['key', 'total', 'strengths', 'weaknesses'] } },
    winner: { type: 'string' },
    best_ideas_to_graft: { type: 'array', items: { type: 'string' } },
    fatal_flaws: { type: 'array', items: { type: 'string' } },
  },
  required: ['scores', 'winner', 'best_ideas_to_graft', 'fatal_flaws'],
}

function proposalPrompt(kind, a) {
  const scope = kind === 'backend'
    ? 'Design the BACKEND part-aware architecture + prompt-to-3D + API contract additions + tuning feature flag (backend side), and specify exactly what the frontend needs from the API (capabilities/features endpoint, job/part shapes, events).'
    : 'Design the FRONTEND: four UI modes (Default polished, Node, Mobile, Tablet) as renderers over one shared state/API layer, Settings-based mode switch persisted locally, prompt+image input in every mode, part display (part list/visibility/highlight in the viewer), Node UI engine + React Flow adapter with nodes Input Image, Prompt, Segmentation, Image-to-3D, Part Extraction, Export (+ registry slot for future locked tuning nodes), mobile sheets/floating nav, tablet rail/bottom nav auto-switch, Default UI polish list, tuner UI hidden behind a feature flag, accessibility, performance budget, quality gates. Assume the backend exposes a features/capabilities endpoint, optional prompt input on job creation and part-analysis results with a manifest; state the exact API shapes you assume so they can be reconciled with the backend design.'
  return `${CONTEXT}\n\nYOUR TASK: write an independent ${kind.toUpperCase()} architecture proposal from this angle: ${a.angle}\n${scope}\nWrite the full proposal to ${DES}/${a.key}.md (Markdown, as long as needed). Do not modify any other project file. Return {file, summary} where summary is <= 400 words.`
}

phase('Propose')
const [backendProps, frontendProps] = await Promise.all([
  parallel(BACKEND_ANGLES.map(a => () => agent(proposalPrompt('backend', a), { label: a.key, phase: 'Propose', schema: PROPOSAL_SCHEMA }).then(r => r && { key: a.key, ...r }))),
  parallel(FRONTEND_ANGLES.map(a => () => agent(proposalPrompt('frontend', a), { label: a.key, phase: 'Propose', schema: PROPOSAL_SCHEMA }).then(r => r && { key: a.key, ...r }))),
])
const bp = backendProps.filter(Boolean), fp = frontendProps.filter(Boolean)
log(`proposals: backend ${bp.length}/3, frontend ${fp.length}/2`)

const LENSES = [
  'REQUIREMENTS lens: check every clause of the user request is satisfied (parts list, physical vs region, prompt+image, reassembly/relationships, future-tuning consumption, tuning disabled, four UI modes details, settings persistence, no duplicated logic, responsiveness, incremental/back-compat).',
  'FEASIBILITY lens: check against the verified stack facts in the research files (transformers 4.57.6/hub 0.36.2 pins, diffusers 0.39.0 only, single GPU thread + VRAM, TRELLIS.2 fused single-surface output, export remesh/bake cost, Windows/py3.14 wheels, pinned tests, React Compiler rules). Penalise anything that would break the working pipeline or cannot run here.',
  'SIMPLICITY/RISK lens: penalise over-engineering, unnecessary new concepts, large rewrites of working code, and unclear ownership; reward small additive seams and clear incremental slices with acceptance tests.',
]
function judgePrompt(kind, props, lens) {
  return `${CONTEXT}\n\nYOU ARE A JUDGE (${lens})\nRead these ${kind} proposals fully: ${props.map(p => p.file).join(' ; ')}. Score each 0-10 on requirements_coverage, backward_compat_safety, feasibility_on_this_stack, extensibility_for_future_tuning, clarity_implementability (total = sum). Name the winner, the best ideas from the others worth grafting onto it (specific, actionable), and any fatal flaws (things that would break the working pipeline, violate a hard constraint or cannot run on this stack). Read-only: do not modify files.`
}

phase('Judge')
const [bJudges, fJudges] = await Promise.all([
  parallel(LENSES.map((l, i) => () => agent(judgePrompt('backend', bp, l), { label: `judge:backend:${i + 1}`, phase: 'Judge', schema: JUDGE_SCHEMA }))),
  parallel(LENSES.map((l, i) => () => agent(judgePrompt('frontend', fp, l), { label: `judge:frontend:${i + 1}`, phase: 'Judge', schema: JUDGE_SCHEMA }))),
])
const bj = bJudges.filter(Boolean), fj = fJudges.filter(Boolean)
function tally(judges) {
  const totals = {}
  for (const j of judges) for (const s of j.scores) totals[s.key] = (totals[s.key] || 0) + (s.total || 0)
  return totals
}
const bTotals = tally(bj), fTotals = tally(fj)
log(`backend totals ${JSON.stringify(bTotals)} | frontend totals ${JSON.stringify(fTotals)}`)

phase('Synthesize')
const backendDesign = await agent(`${CONTEXT}\n\nYOUR TASK: write the AUTHORITATIVE backend design for this milestone to ${PA}/DESIGN.md, synthesising the proposals ${bp.map(p => p.file).join(' ; ')} using the judges' verdicts below: start from the highest-scoring proposal, graft the listed best ideas, and fix every fatal flaw. JUDGE VERDICTS (JSON): ${JSON.stringify(bj)} TOTALS: ${JSON.stringify(bTotals)}.\nDESIGN.md must contain: 0 goals/non-goals + hard constraints; 1 module layout (exact paths) and layering rules; 2 part taxonomy (ids, physical vs region policy per part incl. eyes/eyebrows/eyelashes/hair/clothes/accessories/detachable objects, and what 'physical' means given TRELLIS.2 fused output - be honest about geometry that does not exist yet) ; 3 part asset data model + manifest schema (img3d.parts/1) with JSON examples, per-face labels, seams, relations, frames/transforms, provenance, refinement_state for future tuning; 4 pipeline: image-to-3D (unchanged path) + prompt-to-3D (text->image->existing pipeline) + pre-reconstruction 2D segmentation + post-reconstruction lifting/extraction, GPU scheduling and VRAM sequencing, seed-parity preservation, timings; 5 provider seams + model registry (t2i, segmentation, lifting, future 3D segmenters/completers) with install state, licence metadata, honest 'unavailable' states, and the candidate providers per the pending user decision; 6 API contract additions (endpoint table with methods, bodies, responses, status/error codes; features/capabilities endpoint; WebSocket events; file routes with auth guards), all additive; 7 storage layout + retention + migration of old job.json; 8 feature flags incl. tuning disabled by default (how, without breaking the 20 tuner API tests); 9 dependency changes (exact pins, constraints file, install procedure gated on user approval); 10 test plan (files + assertions, incl. legacy-load, retry round-trip, model.glb untouched, parity, multi-node GLB stats, file-route auth); 11 implementation slices with DISJOINT file ownership, dependencies/order and acceptance criteria (sized for parallel implementer agents); 12 open questions for the user. Then return a <= 500 word summary.`, { label: 'synth:backend', phase: 'Synthesize' })

const uiDesign = await agent(`${CONTEXT}\n\nYOUR TASK: write the AUTHORITATIVE frontend design for this milestone to ${PA}/UI_DESIGN.md, synthesising the proposals ${fp.map(p => p.file).join(' ; ')} using the judges' verdicts: start from the highest-scoring, graft the best ideas, fix fatal flaws. JUDGE VERDICTS (JSON): ${JSON.stringify(fj)} TOTALS: ${JSON.stringify(fTotals)}.\nIt MUST use exactly the API shapes defined in the backend design ${PA}/DESIGN.md (read it first; if the frontend needs something the backend design lacks, list it in a 'Backend requests' section instead of inventing a different shape).\nUI_DESIGN.md must contain: folder structure + layering (api/stores/actions/workflow engine/ui-mode shells/shared panels/components) with exact paths; what moves vs stays (no deletion of working functionality; uncommitted tuner files untouched); preferences store + storage keys + Settings dialog + no-flash mode application; the four modes in detail (Default polish list; Node UI engine API (types, registry, typed ports/colours, validation, compile-to-request, status mapping, persistence/versioning) + React Flow adapter + lazy loading + list/stepper fallback; Mobile shell: floating bottom nav, sheets with snap points, FAB, touch density tokens, safe areas; Tablet shell: rail vs bottom nav switching rules with exact breakpoints/orientation/hysteresis); prompt + image input UX in every mode; parts UX (part list, visibility, highlight, physical vs region badges, download per part / assembled); tuner hidden behind a flag in all modes; accessibility checklist; performance/bundle budget; responsiveness matrix (viewports to verify); quality gates + new tests (engine unit tests runnable with Node type stripping under test/, mock backend extensions, compiler check); implementation slices with DISJOINT file ownership, order and acceptance criteria. Then return a <= 500 word summary.`, { label: 'synth:frontend', phase: 'Synthesize' })

phase('Critique')
const CRITICS = [
  { key: 'critic_requirements', agentType: 'code-modernization:architecture-critic', lens: 'REQUIREMENTS COVERAGE: go clause by clause through the USER REQUEST and check DESIGN.md + UI_DESIGN.md cover each one concretely (not just mentioned). Also check backend and UI designs agree exactly on every API shape, event and flag. Flag over-engineering and simpler alternatives.' },
  { key: 'critic_feasibility', agentType: 'code-modernization:architecture-critic', lens: 'FEASIBILITY + SAFETY: verify claims against the actual code (open the cited files) and the research evidence: would anything break the working generation pipeline, seed parity, pinned tests, the frozen stage list, the tuner tests, or the React Compiler/oxlint gates? Are GPU/VRAM sequencing, licences, Windows/py3.14 packaging and the slice ownership plan (truly disjoint files, correct ordering) sound?' },
]
const critiques = await parallel(CRITICS.map(c => () => agent(`${CONTEXT}\n\nYOU ARE AN ADVERSARIAL DESIGN CRITIC. Lens: ${c.lens}\nRead ${PA}/DESIGN.md and ${PA}/UI_DESIGN.md fully (and any code/research needed to verify). Write your critique to ${DES}/${c.key}.md as a numbered list of concrete issues, each with severity (blocker/major/minor), evidence (file:line or doc section), and the exact fix. Do not edit the design docs. Return a <= 300 word summary with the blocker/major counts.`, { label: c.key, phase: 'Critique', agentType: c.agentType })))

phase('Revise')
const revision = await agent(`${CONTEXT}\n\nYOUR TASK: revise ${PA}/DESIGN.md and ${PA}/UI_DESIGN.md to resolve every blocker and major issue in the critiques ${DES}/critic_requirements.md and ${DES}/critic_feasibility.md (and minors where cheap). Keep both docs consistent with each other. Append a 'Revision log' section to each doc listing each critique item and how it was resolved (or why it was rejected, with evidence). Return a <= 400 word summary of what changed and the final list of open questions for the user.`, { label: 'revise', phase: 'Revise' })

return { backendTotals: bTotals, frontendTotals: fTotals, backendDesign, uiDesign, critiques: critiques.filter(Boolean), revision, proposals: [...bp, ...fp] }
