# Contributing to Img3D

Thanks for your interest! Img3D is a personal project by **Shiwam Shorya Sharma**; issues, ideas and pull requests
are welcome. By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Ground rules

- **License.** Contributions are accepted under the project license, [GPL-3.0](LICENSE). By opening a pull request
  you confirm that you wrote the change (or have the right to submit it) and agree to license it under GPL-3.0.
- **Third-party material.** Do not commit model weights, Python/CUDA/Blender binaries, `node_modules`, generated
  meshes or images you do not own. New dependencies must have a license compatible with GPL-3.0; add them to
  [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) in the same pull request.
- **Secrets.** Never commit `backend/.env`, tokens or passwords.
- **No fake generation.** The app must never return mock/placeholder 3D results; test doubles belong in `test/`.

## Project conventions

- Backend runs **only** on the embedded interpreter: `backend\python\python.exe -s ...`. Install packages with uv
  into that interpreter and pin them in `backend/requirements.txt`.
- `backend/main.py` is the single entry point. Respect the layering (`api → services → workers → pipelines →
  inference`); only `backend/inference/` may import `torch` / `trellis2`.
- API changes start in the contract: [`.claude/skills/img3d-architecture/api-contract.md`](.claude/skills/img3d-architecture/api-contract.md).
- Every test, probe, cache and test config lives under `test/`.
- Frontend: React 19 + TypeScript (strict), CSS modules, Zustand stores, API calls only through `src/api/`.

## Before you open a pull request

```powershell
$py = ".\backend\python\python.exe"
& $py -s -m pytest -c test\pytest.ini test\backend test\aux_models test\launcher -q -p no:cacheprovider
cd frontend; npm run lint; npm run build; cd ..
node test\frontend\review\compiler_check.mjs
```

Include in the pull request: what changed and why, how you tested it (GPU / Blender version if relevant) and
screenshots for UI changes.

## Reporting bugs

Open an issue with: Windows version, GPU + driver, the output of `GET /api/v1/system`, the relevant part of
`backend/logs/error.log` (remove any secrets) and steps to reproduce. Security problems: see
[SECURITY.md](SECURITY.md).
