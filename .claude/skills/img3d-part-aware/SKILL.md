---
name: img3d-part-aware
description: Part-aware milestone of Img3D — Tripo-style semantic part decomposition (2D segmentation → lift labels onto the TRELLIS.2 mesh → physical parts + semantic regions + manifest img3d.parts/1), prompt-to-3D (Z-Image-Turbo → TRELLIS.2), and the four UI modes (Default, Node, Mobile, Tablet). Use before touching backend/parts, backend/inference/{imagegen,segmentation,parts,providers}, the run/workflow APIs, the UI-mode shells, or when resuming this milestone in E:\14-OCT-2026\Img3D.
---

# Img3D part-aware milestone

**Resume here:** [HANDOFF.md](HANDOFF.md) (status table, numbered resume plan, facts). Tuning stays OFF until the
user explicitly approves this milestone — never continue `.claude/skills/img3d-auto-mesh-tuner/`.

## Documents (precedence: user messages > DECISIONS > DESIGN/UI_DESIGN > research)
| File | What |
|---|---|
| [DECISIONS.md](DECISIONS.md) | The user's model + separation-policy choices, environment changes, measured probes, assistant defaults |
| [DESIGN.md](DESIGN.md) | Authoritative backend + API design: taxonomy, manifest, pipelines (`ig_`/`sg_`/`pa_`/`wf_` runs), providers/registry, §6 API, flags, tests, §11 slices S0–S11 |
| `UI_DESIGN.md` | Authoritative frontend design: shared API/state layer, preferences, four mode shells, Node engine, parts UX, slices |
| `../img3d-architecture/api-contract.md` §11 | Contract form of DESIGN §2–§8 (endpoints, JSON shapes, events, codes, settings), written by S0 |
| `progress/*.md` | Per-slice reports (built, files, tests, deviations, requests, open issues) |
| `research/*.md` | Verified maps of the codebase and model research (file:line evidence) |
| `design/*.md` | Losing/background proposals, judge verdicts (`workflow_results.json`) and critiques |
| `workflows/*.js` | The workflow scripts used (cannot be resumed across sessions; copy + edit to re-run phases) |

## Core rules (details in DESIGN.md §0)
- Image-to-3D path and `model.glb` stay byte-identical; protected files are sha256-pinned
  (`test/backend/protected_files.sha256`). Part-awareness lives in separate runs that only read finished jobs.
- No new `JobStage`, no change to the percent formula or `/system` keys; API additions are optional fields/new routes.
- Every CUDA call of new code goes through `GenerationWorker.submit_gpu_task` (one `gpu-worker` thread).
- Only `inference/{imagegen,segmentation,parts}/*` implementation modules import torch/diffusers/transformers;
  `parts/` is a torch-free numpy/scipy library.
- Install nothing without `-c backend\constraints.txt --only-binary ":all:"`; diffusers 0.39.0, transformers 4.57.6,
  huggingface-hub 0.36.2 are frozen. HF Hub calls for public repos need `token=False`.

## Testing (all under `test/`)
- Backend suite: `backend\python\python.exe -s -m pytest -c test\pytest.ini test\backend test\aux_models -q`
  (baseline 2026-10-16: 182 passed in ~80 s).
- Part-aware probes/smokes: `test\part_aware\` (outputs to `test\outputs\part_aware\`). Sample images:
  `test\images\ALICE\ALICE_full_front_view.png`, `test\images\ANIME_CHARACTER_01\Alice.png`.
- **GPU lock:** wrap every command that loads real weights onto the GPU (smoke tests, probes, engine GPU tests, a
  real server for E2E) with `backend\python\python.exe -s test\part_aware\gpu_lock.py --label <name> -- <cmd ...>`
  so parallel agents never co-load two large models (Z-Image 23.3 GB peak).
