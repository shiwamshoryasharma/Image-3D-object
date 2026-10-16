# Img3D — project memory

Local, production-style Image-to-3D app: React/Vite frontend + modular FastAPI backend running Microsoft
TRELLIS.2-4B locally on an RTX 5090 (Windows 11).

Owner / sole author: **Shiwam Shorya Sharma** (GitHub `shiwamshoryasharma`). Never put any other person's name in
tracked files, licenses, docs or commit metadata.

Machine-local private notes (gitignored): `CLAUDE.local.md` in the repo root — read it too.

## Paths
- Root: `E:\14-OCT-2026\Img3D`
- Backend: `backend\` — entry point `backend\main.py` (the ONLY entry point)
- Frontend: `frontend\` (Vite project already initialized — never re-create it)
- Model weights: `backend\.model\TRELLIS.2-4B` (local only — never re-download)
- Aux models (DINOv3, BiRefNet, RMBG-2.0, TRELLIS v1 SS decoder): `backend\.model\...`
- Tests / test scripts / test caches / test configs: `test\` only. Sample inputs: `test\images\{ALICE,ANIME_CHARACTER_01}`.
- User prompts: `.prompts\` (private, never pushed).

## Repository
- GitHub: https://github.com/shiwamshoryasharma/Image-3D-object (public, branch `main`, license GPL-3.0).
- Upstream sources in `backend\third_party\` are git submodules (TRELLIS.2, CuMesh, FlexGEMM, nvdiffrast).
- Before any commit/push follow `.claude\skills\img3d-repo-publishing\SKILL.md` (ignore policy, privacy scan,
  license files, commit identity).

## Non-negotiables
- Backend uses ONLY the embedded Python `backend\python\python.exe` (3.14, isolated mode). No system Python.
- No fake/mock generation data in the app (test doubles live only under `test\`).
- TRELLIS.2 is isolated behind `backend\inference\` (engine adapter). API/services never import torch/trellis2.
- Secrets come from `backend\.env` (HF_TOKEN, UI_USERNAME, UI_PASSWORD) — never print or commit them.
- Keep the multi-reference data model (7 categorized slots + primary) intact even though the engine is single-image.

## Skills (read before working in that area)
- `.claude\skills\img3d-architecture\` — architecture + authoritative API contract (`api-contract.md`).
- `.claude\skills\img3d-environment\` — embedded Python, local CUDA 13.4 toolchain, native extension builds, aux models.
- `.claude\skills\img3d-run-and-test\` — launch commands, test suites, real-generation smoke tests.
- `.claude\skills\img3d-repo-publishing\` — git/GitHub publishing, `.gitignore` policy, licenses, pre-push checks.
- `.claude\skills\img3d-auto-mesh-tuner\` — tuner design contract, handoff, progress reports, research.

## In progress: Auto Mesh Tuner (Blender >= 5.1 runtime + mesh repair)
PAUSED (2026-10-15, and again 2026-10-16 so the user can add/optimize something new first). Resume from
`.claude\skills\img3d-auto-mesh-tuner\HANDOFF.md` (status table, what changed, numbered resume plan); agent prompts in
`progress\resume_briefs.md`; contract in `DESIGN.md`. The 2026-10-15 code is pushed (marked experimental in README);
the 2026-10-16 resume edits (framework-test stubbing, rig_* options, frontend fixes, one symmetry.py change) are
uncommitted on `main`.

## History
- 2026-10-14: backend, frontend, native extensions, BiRefNet + SS decoder done and verified (138 tests green).
  DINOv3 was gated at first; it is now present in `backend\.model\dinov3-vitl16-pretrain-lvd1689m` and real
  generations completed (e.g. 1536 res / 4096 tex / 2M faces in ~200–580 s on the RTX 5090).
- 2026-10-16: first push to GitHub — README, THIRD_PARTY_NOTICES, CoC, CONTRIBUTING, SECURITY, CITATION.cff,
  folder READMEs for ignored folders, submodules, privacy scrub (details in the publishing skill).
