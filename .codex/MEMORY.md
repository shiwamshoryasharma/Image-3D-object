# Img3D durable project memory

Inspected 2026-10-29. Relative paths below are rooted at E:/14-OCT-2026/Img3D.
This is project-file memory; load it through [AGENTS.md](AGENTS.md).

## Project

Img3D is a local image-to-3D application: modular FastAPI backend, TRELLIS.2-4B
inference, React 19 / Vite 8 / TypeScript 6 frontend, Three.js / React Three
Fiber viewer, and a separate Blender Auto Mesh Tuner subsystem. Existing source
also includes prompt-to-image, segmentation, part analysis, workflows and four
UI shells. Code presence does not imply final milestone approval.

The documented target is Windows 11 with an RTX 5090 (32 GB). That hardware
description comes from existing project records; no GPU probe was run for this
setup task. The embedded interpreter was directly checked: Python 3.14.2,
isolated mode enabled.

## User rules and invariants

- Input images for model-generation or validation: test/images only. Eight PNGs
  were found in ALICE and ANIME_CHARACTER_01; the primary smoke image is
  test/images/ALICE/ALICE_full_front_view.png.
- Every testing layer, script, config, cache, log, report and generated build
  belongs under test. Prefer test/outputs for deliverables and test/.runtime
  for disposable execution state. Do not run tests against production job data.
- Backend commands use backend/python/python.exe -s. Add -B for read-only
  inspection to avoid creating bytecode. No system Python, venv or conda for
  backend work.
- backend/main.py is the sole application entry point. The frontend is already
  initialized; do not recreate it.
- New GPU work goes through GenerationWorker.submit_gpu_task. Real-weight test
  processes use test/part_aware/gpu_lock.py; do not overlap GPU processes.
- API, services, workers, pipelines, storage, processing and backend/parts stay
  free of heavyweight model imports. Inference implementation modules own them.
- Preserve existing job artifacts and the generation/progress contracts. The
  user-authorized 2026-10-29 export correction changes future exports only; see
  [generation quality](memory/generation-quality.md).
  Protected backend files are listed in test/backend/protected_files.sha256;
  frontend tuner files have a separate protected list under test/frontend/review.
- Preserve seven categorized reference slots and primary selection. Existing
  reconstruction uses a primary image; multiple stored references do not imply
  true multi-view conditioning.
- No fake generation in application code. Test doubles remain under test.
- Auto Mesh Tuner remains disabled pending explicit milestone approval.
  Default mode keeps its existing tuner UI visible but locked; the other new
  modes do not add tuner UI.
- Never expose backend/.env values or copy private details into these notes.
  Do not inspect or reuse production backend/runtime/jobs as test fixtures.
- Generate no images unless explicitly requested.

## Environment constraints

backend/constraints.txt and backend/requirements.txt are the local sources of
truth. Recorded pins include torch 2.14.1+cu130, torchvision 0.29.1+cu130,
transformers 4.57.6, huggingface-hub 0.36.2 and diffusers 0.39.0.
Never casually upgrade that stack. Package changes use the embedded interpreter,
uv, the constraints file, binary wheels and copy linking.

Weights live in backend/.model; reuse local assets. A model directory existing
does not establish complete files or readiness. Historical .claude notes contain
older download failures; use current checks before drawing conclusions.

## Where to continue

- [Generation/parts follow-up](memory/generation-quality-followup.md): latest
  high-resolution failures, revised exporter and semantic-request fixes; check
  its validation status before making completion claims.

- [Generation quality fix](memory/generation-quality.md): latest tested export
  corrections, diagnostics, model artifacts and limits.

- [Project map](memory/project-map.md): source locations and architecture.
- [Status and evidence](memory/status.md): dated checkpoint and pending work.
- [Validation skill](skills/img3d-validation/SKILL.md): test selection and commands.
- [Original Claude memory](../.claude/CLAUDE.md): project history and skill index.
- [Latest milestone resume](../.claude/skills/img3d-part-aware/RESUME.md): read
  dated updates before the older task table.
- [Accepted decisions](../.claude/skills/img3d-part-aware/DECISIONS.md): precedence
  over superseded design wording.
- [API contract](../.claude/skills/img3d-architecture/api-contract.md): sections
  7-8 for original APIs, 11 for additive part-aware behavior.
- [Publishing guidance](../.claude/skills/img3d-repo-publishing/SKILL.md): consult
  when publication is requested; this setup performed no commit or push.
