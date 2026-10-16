# Img3D — frontend

React 19 + Vite 8 (Rolldown) + TypeScript 6 single-page app for the local Img3D image-to-3D backend
(TRELLIS.2-4B). Dark, responsive UI: reference-set editor, generation settings, live progress, 3D viewer
(React Three Fiber + drei), model stats/GLB export, job history and an optional login screen.

## Scripts

| command | what it does |
|---|---|
| `npm run dev` | Vite dev server (React Compiler enabled). `/api` (REST + WebSocket) is proxied to the backend. |
| `npm run build` | `tsc -b` (strict) + production build to `dist/` (served by the backend at `/` when present). |
| `npm run preview` | Serves `dist/` with the same `/api` proxy. |
| `npm run lint` | oxlint. |

The proxy target defaults to `http://127.0.0.1:8000`; override it with `IMG3D_BACKEND_URL`
(e.g. `IMG3D_BACKEND_URL=http://127.0.0.1:8765 npm run dev`). To call a backend on another origin directly
(no proxy), build with `VITE_API_BASE_URL=http://host:port`.

The app runs fully offline: system fonts only, no CDN assets, and the viewer lights scenes with local
light-formers instead of downloadable HDR presets. three.js / R3F are code-split and only loaded with the viewer.

## Layout

```
src/
  api/          typed client for the REST/WS contract (snake_case types mirror api-contract.md)
    client.ts   axios instance, bearer token, ApiError {status, code, message, details, requestId}
    ws.ts       RealtimeClient: auto-reconnect with backoff, typed events, REST polling fallback
    auth.ts · system.ts · references.ts · jobs.ts · types.ts
  store/        zustand stores (auth, system, references, settings, jobs, ui, toasts)
  hooks/        session bootstrap, breakpoints, generate availability, timers, fullscreen
  lib/          formatting, storage, slot metadata, stage table, image validation, downloads
  components/   layout · references · settings · generation · viewer · history · auth · common
  styles/       design tokens (CSS variables) + global styles; components use CSS Modules
```

## Behaviour notes

- The backend is the source of truth. Live updates arrive over `/api/v1/ws` (`job.updated`, `system.updated`);
  while the socket is down the client polls `GET /system` and the active job, and a slow safety poll runs even
  while connected. A banner with retry is shown whenever the backend is unreachable.
- The reference set id is stored in `localStorage` (`img3d.referenceSetId`) and recreated on 404.
  Settings (`img3d.settings`) and viewer options (`img3d.ui`) are persisted too.
- When auth is enabled the token is kept in `localStorage` (`img3d.auth.token`) and sent as a bearer header;
  `<img>`, the GLB loader and the WebSocket use `?token=` as allowed by the contract. Any 401 returns to login.
- Keyboard: `Ctrl/Cmd + Enter` generates; menus, segmented controls and tabs are keyboard navigable.

UI test double (no GPU needed): `node ../test/frontend/mock_backend.mjs --port 8765`, then run the dev server or
preview with `IMG3D_BACKEND_URL=http://127.0.0.1:8765`.
