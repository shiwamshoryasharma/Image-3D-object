---
name: img3d-repo-publishing
description: How the Img3D repository is published to GitHub (shiwamshoryasharma/Image-3D-object) — what is tracked vs. ignored, the README-in-ignored-folder convention, git submodules for upstream sources, license/notice files, the privacy scan and commit identity rules. Use before committing, pushing, editing .gitignore, adding a dependency/model, or changing README/LICENSE/THIRD_PARTY_NOTICES in E:\14-OCT-2026\Img3D.
---

# Publishing Img3D to GitHub

Remote: `origin` = https://github.com/shiwamshoryasharma/Image-3D-object.git (public, default branch `main`).
First pushed 2026-10-16 on top of the GitHub-created "Initial commit" (never force-push `main`).

## Identity (user requirement)
- The only person named anywhere public is **Shiwam Shorya Sharma** (GitHub `shiwamshoryasharma`).
  No other names (no HF account names, OS user names, folder names that are people's names) in tracked files.
- Commit as `Shiwam Shorya Sharma <110902859+shiwamshoryasharma@users.noreply.github.com>` (GitHub noreply,
  matching the web-created initial commit; the user keeps the e-mail private on GitHub):
  `git -c user.email=110902859+shiwamshoryasharma@users.noreply.github.com commit ...`
- No `Co-Authored-By` trailers: the user asked for no other names.

## What is tracked
- Source: `backend\` (code, scripts, `requirements.txt`, `.env.example`), `frontend\` (src, configs,
  `package-lock.json`), `test\` (scripts, configs, conftest), `docs\assets\`, `.claude\` (memory + skills).
- Root files: `README.md`, `LICENSE` (GPL-3.0 text, verbatim from GitHub — never edit), `THIRD_PARTY_NOTICES.md`,
  `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `CONTRIBUTING.md`, `SECURITY.md`, `CITATION.cff`,
  `.gitignore`, `.gitattributes`, `.gitmodules`.

## What is NOT pushed (and how it is documented instead)
Convention: ignore the folder's contents with `folder/*` and re-include `!folder/README.md`, which explains what
lives there, where it comes from, how to recreate it and the license terms followed.
| Folder | README | Why not pushed |
|---|---|---|
| `backend\python\` | yes | CPython (PSF) + packages — third-party, 4.4 GB |
| `backend\.toolchain\` | yes | NVIDIA CUDA wheels (CUDA EULA), 1.7 GB |
| `backend\.model\` | yes | Model weights (MIT / DINOv3 License / CC BY-NC), ~18 GB |
| `test\images\` | yes | Personal test images (provenance not cleared for public) |
| `backend\runtime\`, `backend\logs\` | `.gitkeep` | Generated data |
| `test\outputs`, `.runtime`, `.logs`, `**\.pytest_tmp`, `**\.hf_home`, `env_setup\{cache,out,smoke_build}`, `aux_models\outputs`, `*.obj` | `test\README.md` | Generated data / caches |
| `.prompts\`, `.prompt\` | — | User's private prompts (explicit user rule) |
| `CLAUDE.local.md`, `.claude\settings.local.json` | — | Machine-local / private notes |
| `backend\.env` | — | Secrets |
`frontend\.gitignore` (Vite template) covers `node_modules`, `dist`, `*.local`. There is no `backend\.gitignore`
any more — the root `.gitignore` is the single source of truth (a `python/` dir rule there blocked re-inclusion).

## Upstream sources = submodules (`ignore = dirty`)
`backend\third_party\TRELLIS.2` (75fbf01), `extensions\CuMesh` (12289e1), `extensions\FlexGEMM` (6dd94a8),
`extensions\nvdiffrast` (v0.4.0 / 253ac4f). Build-only patches stay tracked in `third_party\patches\` +
`PATCHES.md` (`*.patch -text` in `.gitattributes` — byte-exact for `git apply`). Bumping a pin = check out the new
upstream commit inside the submodule, rebuild + verify, then `git add backend/third_party/<name>`.

## License facts (keep README / THIRD_PARTY_NOTICES consistent)
- Project: GPL-3.0 (chosen on GitHub; compatible with plyfile GPL-3.0+, easydict LGPL-3.0, Blender bpy scripts).
- Restrictions surfaced to users: nvdiffrast = NVIDIA Source Code License, **non-commercial**; RMBG-2.0 = Bria
  license / CC BY-NC 4.0 (optional, BiRefNet MIT is the default); DINOv3 = DINOv3 License (acceptable use,
  trade controls, acknowledge in publications). TRELLIS.2 code + weights, CuMesh, FlexGEMM, BiRefNet = MIT.
- New dependency or model → add it to `THIRD_PARTY_NOTICES.md` (version, license, link) in the same commit.
  Get license data from installed metadata, not memory (`importlib.metadata` in the embedded Python;
  `node_modules/<pkg>/package.json` for npm; Hugging Face API `cardData.license` for models).

## Pre-push checklist
1. `git status` + `git diff --cached --stat`: nothing from the ignored list above, no file > 1 MB unexpectedly.
2. Privacy scan over staged files (must print nothing): tokens (`hf_`, `gh?_`, `sk-`, `AKIA`, private keys), every
   value in `backend\.env` (compare in a script; never print them), and the private names/IPs listed in
   `CLAUDE.local.md`. Put machine-specific or sensitive notes in `CLAUDE.local.md`, not in tracked docs.
3. `git submodule status` shows the expected pins; `git fetch` and fast-forward on top of `origin/main`.
4. Push only when the user asked for it.
