# `test/` — every test, probe, cache and test config of Img3D

All test code lives here, never in `backend/` or `frontend/`. Run everything with the **embedded** interpreter
(`backend\python\python.exe -s`) from the repository root.

## Layout

| Folder | What it covers | How to run |
|---|---|---|
| `backend/` | FastAPI API, services, storage, WebSocket, auth, settings (uses a **test-only** engine double in `fakes.py`) | `pytest -c test\pytest.ini test\backend -q` |
| `engine/` | TRELLIS.2 adapter: loader paths, SDPA fallback, parity with upstream, real generation smoke run | `pytest -c test\pytest.ini test\engine -q -p no:cacheprovider` · `test\engine\run_generation_smoke.py` |
| `aux_models/` | DINOv3 / BiRefNet / SS-decoder resolution + verification | `pytest ... test\aux_models -q` · `test\aux_models\verify_aux_models.py` |
| `env_setup/` | Native CUDA extensions and toolchain checks | `test\env_setup\verify_extensions.py` · `test\env_setup\smoke_ext.cmd` |
| `launcher/` | Blender runtime detection, selection, download, process runner | `pytest ... test\launcher -q` (`-m network` for live download tests) |
| `mesh_tuner/` | Auto Mesh Tuner inside Blender (framework, rig, repair, refine) + exploration probes | `pytest ... test\mesh_tuner\framework test\mesh_tuner\rig -q` |
| `frontend/` | Contract mock backend for UI work, React Compiler check | `node test\frontend\review\compiler_check.mjs` |
| `e2e/` | End-to-end generation against a running server | `test\e2e\e2e_generate.py --image <png>` |
| `lan_check/` | REST + WebSocket through the Vite proxy from another device | `test\lan_check\lan_proxy_check.py http://<lan-ip>:<port>` |
| `images/` | Sample inputs (personal data, not in Git — see its README) | — |

`pytest` above means `backend\python\python.exe -s -m pytest`.

## Generated folders (ignored by Git)

| Folder | Contents |
|---|---|
| `outputs/` | Generated meshes, renders, reports and comparison images from test runs |
| `.runtime/`, `.logs/` | Isolated backend runtime/log directories used by tests |
| `**/.pytest_tmp/`, `.pytest_cache/` | pytest temp + cache |
| `env_setup/cache/`, `env_setup/out/`, `env_setup/smoke_build/` | Triton/FlexGEMM kernel cache, extension verification outputs, build objects |
| `aux_models/outputs/`, `**/.hf_home/` | Background-removal outputs, Hugging Face remote-code cache |

## Notes

- The app itself never fakes generation; test doubles exist only in this folder.
- Exploration probes under `mesh_tuner/probe`, `mesh_tuner/*/explore` and `mesh_tuner/*/probes` were written for
  one development machine and may contain absolute paths; adjust them before reuse.
- Tests marked `blender` need Blender ≥ 5.1; `gpu` tests need the real GPU and model weights.
