---
name: img3d-run-and-test
description: Launch, smoke-test and test the Img3D app (FastAPI backend on :8000, Vite frontend on :5173), including real TRELLIS.2 generation runs with the sample images in test\images. Use when asked to run, start, verify, or debug the running Img3D app.
---

# Running Img3D

Backend (single entry point; serves the built frontend at http://127.0.0.1:8000 too):
```powershell
cd E:\14-OCT-2026\Img3D\backend
.\python\python.exe -s main.py            # flags: --host --port --log-level --no-preload
```
(`.\python.bat main.py` also works — main.py strips the user site-packages itself.)

Frontend dev server (hot reload, proxies /api + WebSocket to :8000):
```powershell
cd E:\14-OCT-2026\Img3D\frontend
npm run dev                                # http://localhost:5173
```
Production UI: `npm run build` → backend serves `frontend\dist` at `/`.

LAN access (other devices on the same network): Vite dev/preview listen on `0.0.0.0` (`vite.config.ts`):
- Open `http://<this-pc-lan-ip>:5173` (this machine's IP + host name: see `CLAUDE.local.md`) or
  `http://<pc-name>:5173`. REST + WebSocket go through the Vite proxy, so the backend stays on 127.0.0.1 (no CORS).
- Env overrides: `IMG3D_DEV_HOST` (127.0.0.1 = local only), `IMG3D_DEV_PORT` (5173), `IMG3D_PREVIEW_PORT` (4173),
  `IMG3D_ALLOWED_HOSTS` (extra host names; IPs/localhost always allowed; unknown names get 403 by design),
  `IMG3D_BACKEND_URL` (proxy target). `strictPort` is on: a busy port fails instead of silently moving.
- Windows Firewall already has inbound "Node.js JavaScript Runtime" allow rules (Private+Public). Don't change firewall settings.
- Backend-served UI on the LAN instead: `main.py --host 0.0.0.0` (needs a python.exe firewall allow — user's call).
- Login (UI_USERNAME/UI_PASSWORD) is what protects the app on the LAN; traffic is plain HTTP.
- LAN smoke test: `test\lan_check\lan_proxy_check.py http://<ip>:<port>` (REST GET/POST/DELETE + WebSocket via proxy).

- Auth is ON when `UI_USERNAME` + `UI_PASSWORD` are set in `backend\.env` (override: `IMG3D_AUTH_ENABLED=false`).
- API docs: http://127.0.0.1:8000/api/docs. Logs: `backend\logs\img3d.log` (JSON), `error.log`.
- Model load takes ~75–95 s (upstream random-init + weight load per model); UI shows per-component progress.
- Engine `unavailable` (e.g. missing aux models) is retryable without restart: "Retry loading model" in the
  engine status popover, or `POST /api/v1/system/engine/load`.

# Tests (all under E:\14-OCT-2026\Img3D\test)
```powershell
$py="E:\14-OCT-2026\Img3D\backend\python\python.exe"
& $py -s -m pytest -c E:\14-OCT-2026\Img3D\test\pytest.ini E:\14-OCT-2026\Img3D\test\backend E:\14-OCT-2026\Img3D\test\aux_models -q
& $py -s -m pytest -c E:\14-OCT-2026\Img3D\test\pytest.ini E:\14-OCT-2026\Img3D\test\engine -q -p no:cacheprovider
& $py -s E:\14-OCT-2026\Img3D\test\env_setup\verify_extensions.py          # native CUDA extensions
& $py -s E:\14-OCT-2026\Img3D\test\aux_models\verify_aux_models.py         # DINOv3 / BiRefNet / SS decoder
& $py -s E:\14-OCT-2026\Img3D\test\engine\run_generation_smoke.py          # real TRELLIS.2 run (needs DINOv3)
& $py -s E:\14-OCT-2026\Img3D\test\e2e\e2e_generate.py --image E:\14-OCT-2026\Img3D\test\images\ALICE\ALICE_full_front_view.png  # against a running server
```
- Backend API tests use a TEST-ONLY engine double (`test\backend\fakes.py`); the app itself never fakes generation.
- Frontend: `npm run build` (tsc + vite) and `npm run lint` (oxlint) must be clean; React Compiler check:
  `node test\frontend\review\compiler_check.mjs`. `test\frontend\mock_backend.mjs` is a contract mock for UI work only.
- Sample inputs: `test\images\ALICE\*` (front/back/side/face views + detail sheet), `test\images\ANIME_CHARACTER_01\*`.
- Test outputs go to `test\outputs\` (gitignored). Never write test artifacts elsewhere.
- Baseline (2026-10-16, before the part-aware milestone code): `test\backend` + `test\aux_models` = 182 passed, ~80 s.
- **GPU lock (part-aware milestone onward):** any command that puts real weights on the GPU must run under
  `& $py -s E:\14-OCT-2026\Img3D\test\part_aware\gpu_lock.py --label <name> -- <command ...>` (OS file lock on
  `test\.runtime\gpu.lock`, auto-released on exit; exit 124 = timed out waiting). Parallel agents share one 32 GB GPU.
- Part-aware probes / smoke scripts: `test\part_aware\` (see `.claude\skills\img3d-part-aware\SKILL.md`).
