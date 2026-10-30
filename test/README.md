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
| `frontend/` | Contract mock backend for UI work (part-aware routes and scenarios in `mock/`, request log, strict request keys), shared fixtures + golden request bodies, unit tests of the pure modules, mock smoke tests, review scripts (React Compiler, import boundaries, protected tuner files, bundle budget, browser-pane `ui_probe.js`) | `node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"` · `node test\frontend\ui_modes_mock_smoke.mjs` · `node test\frontend\mesh_tuner_mock_smoke.mjs` · `node test\frontend\review\import_boundaries.mjs` (also `compiler_check`, `protected_frontend`, `bundle_budget <dist>`) |
| `e2e/` | End-to-end generation against a running server | `test\e2e\e2e_generate.py --image <png>` |
| `lan_check/` | REST + WebSocket through the Vite proxy from another device | `test\lan_check\lan_proxy_check.py http://<lan-ip>:<port>` |
| `part_aware/` | Part-aware milestone: model probes, real-weight smoke runs, and `gpu_lock.py` (serialises GPU use across processes) | `test\part_aware\gpu_lock.py --label <name> -- <command ...>` |
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

## Generation and part-quality validation

`engine/run_quality_batch.py` runs the real engine on the anime, cat and house fixtures
under `images/`, captures the decoded source mesh, and records both source-support and
surface-coverage checks. Run it through `part_aware/gpu_lock.py`; use a new `--label`
for each run. For the high-resolution regression, pass `--resolution 1536
--texture-size 4096 --polygons 2000000 --seed 1719986157`. The model may reduce the
effective resolution when its token limit is reached; the report records that value.

`engine/render_quality_models.py` runs inside Blender under the same GPU lock and
renders the resulting batch (`--label`), including anime eye closeups in texture and
clay. `engine/probe_hole_fill_centers.py` audits native-added fan centers in saved
legacy decoded captures. `engine/run_reported_parts_validation.py` checks real
semantic extraction on a test reconstruction using the reported-job fixture copied
under `outputs/parts_quality_regression/reported/`; all job copies and exported parts
remain in an isolated test runtime. These diagnostic inputs and outputs are ignored.