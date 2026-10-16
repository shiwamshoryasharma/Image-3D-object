import { hostname } from 'node:os'
import react, { reactCompilerPreset } from '@vitejs/plugin-react'
import babel from '@rolldown/plugin-babel'
import { defineConfig, type ProxyOptions } from 'vite'

/**
 * Local FastAPI backend (backend/main.py). REST + WebSocket live under /api.
 * `IMG3D_BACKEND_URL` overrides the target (e.g. when the backend runs on another port).
 * The backend can stay bound to 127.0.0.1: LAN clients reach it through this proxy (same-origin, no CORS).
 */
const BACKEND_TARGET = process.env.IMG3D_BACKEND_URL ?? 'http://127.0.0.1:8000'

/**
 * Dev/preview servers listen on all interfaces so other devices on the same network can open the UI
 * (http://<this-pc-ip>:5173). Set `IMG3D_DEV_HOST=127.0.0.1` to keep it local-only.
 */
const LISTEN_HOST = process.env.IMG3D_DEV_HOST ?? '0.0.0.0'
const DEV_PORT = Number(process.env.IMG3D_DEV_PORT ?? 5173)
const PREVIEW_PORT = Number(process.env.IMG3D_PREVIEW_PORT ?? 4173)

/**
 * IP addresses and localhost are always accepted by Vite. Host *names* must be listed (DNS-rebinding protection),
 * so allow this machine's name (e.g. http://my-pc:5173, my-pc.local) plus any extra names in
 * `IMG3D_ALLOWED_HOSTS` (comma-separated).
 */
const machineName = hostname().toLowerCase()
const ALLOWED_HOSTS = [
  machineName,
  `${machineName}.local`,
  `${machineName}.lan`,
  ...(process.env.IMG3D_ALLOWED_HOSTS ?? '')
    .split(',')
    .map((name) => name.trim())
    .filter(Boolean),
]

const apiProxy: Record<string, ProxyOptions> = {
  '/api': {
    target: BACKEND_TARGET,
    ws: true,
    changeOrigin: false,
  },
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    babel({ presets: [reactCompilerPreset()] })
  ],
  server: {
    host: LISTEN_HOST,
    port: DEV_PORT,
    strictPort: true, // fail loudly instead of silently moving to another port LAN users don't know about
    allowedHosts: ALLOWED_HOSTS,
    proxy: apiProxy,
  },
  preview: {
    host: LISTEN_HOST,
    port: PREVIEW_PORT,
    strictPort: true,
    allowedHosts: ALLOWED_HOSTS,
    proxy: apiProxy,
  },
  build: {
    // three.js alone is ~700 kB minified; it is split out and only loaded with the viewer.
    chunkSizeWarningLimit: 900,
    rolldownOptions: {
      output: {
        // Rolldown (Vite 8) manual chunking: `output.codeSplitting.groups` (replaces Rollup's manualChunks).
        // Groups are captured in priority order and pull in their dependencies, so libraries shared with the
        // initial bundle (react, zustand) must be claimed first; otherwise the r3f group would absorb them and
        // force three.js into the initial download. three + r3f are only loaded with the lazy viewer chunk.
        codeSplitting: {
          groups: [
            { name: 'react', test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/, priority: 50 },
            { name: 'vendor', test: /node_modules[\\/](zustand|use-sync-external-store)[\\/]/, priority: 40 },
            { name: 'three', test: /node_modules[\\/]three[\\/]/, priority: 30 },
            {
              name: 'r3f',
              test: /node_modules[\\/](@react-three|three-stdlib|three-mesh-bvh|troika-[^\\/]+|camera-controls|maath|@monogrid|meshline|stats-gl|its-fine|suspend-react|react-reconciler|react-use-measure)[\\/]/,
              priority: 20,
            },
          ],
        },
      },
    },
  },
})
