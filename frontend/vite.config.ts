import { fileURLToPath, URL } from 'node:url'

import react from '@vitejs/plugin-react'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'

/**
 * Where the dev server and `vite preview` send proxied API traffic.
 *
 * Env var: `VITE_DEV_PROXY_TARGET` (read from `.env` files *and* the process
 * environment, so `docker compose` can inject it directly).
 *
 * - Local development: leave unset — the default reaches the API on :8000.
 * - Inside the Compose network: set `VITE_DEV_PROXY_TARGET=http://backend:8000`,
 *   which is the backend service name in docker-compose.yml.
 */
const DEFAULT_DEV_PROXY_TARGET = 'http://localhost:8000'

function resolveProxyTarget(raw: string | undefined): string {
  const trimmed = raw?.trim()
  return trimmed || DEFAULT_DEV_PROXY_TARGET
}

/**
 * Vendor libraries change on a completely different cadence from application
 * code. Splitting them out means editing a component re-downloads kilobytes
 * rather than the whole bundle. Rules are evaluated in order and the first
 * match wins, so the narrow packages are listed before the broad ones.
 */
const MANUAL_CHUNK_RULES: ReadonlyArray<readonly [name: string, matches: (pkg: string) => boolean]> = [
  ['charts', (pkg) => pkg === 'recharts' || pkg === 'recharts-scale' || pkg === 'victory-vendor'],
  ['icons', (pkg) => pkg === 'lucide-react'],
  [
    'radix',
    (pkg) => pkg.startsWith('@radix-ui/') || pkg === 'aria-hidden' || pkg === 'react-remove-scroll',
  ],
  [
    'router',
    (pkg) =>
      pkg === 'react-router' || pkg === 'react-router-dom' || pkg.startsWith('@remix-run/router'),
  ],
  ['data', (pkg) => pkg === '@tanstack/react-query' || pkg === 'zustand' || pkg === 'use-sync-external-store'],
  ['react', (pkg) => pkg === 'react' || pkg === 'react-dom' || pkg === 'scheduler'],
]

/** `@scope/name` or `name` for a module inside node_modules, else null. */
function packageNameOf(id: string): string | null {
  const match = /node_modules[/\\]((?:@[^/\\]+[/\\])?[^/\\]+)[/\\]/.exec(id)
  return match?.[1] ?? null
}

function manualChunk(id: string): string | undefined {
  if (!id.includes('node_modules')) return undefined
  const pkg = packageNameOf(id)
  if (!pkg) return undefined
  for (const [name, matches] of MANUAL_CHUNK_RULES) {
    if (matches(pkg)) return name
  }
  return undefined
}

// NEXUS — local-first dev stack.
//
// The dev server proxies backend traffic to the FastAPI server so the browser
// can call the API same-origin (`/api/v1/...`). That keeps cookies and CORS out
// of the picture during development and makes the proxy an accurate stand-in
// for a reverse proxy in production. `VITE_API_BASE_URL` overrides the base URL
// the client itself calls when set; `VITE_DEV_PROXY_TARGET` (above) moves where
// the dev server forwards it.
export default defineConfig(({ mode }) => {
  // Empty prefix: `.env` values plus anything already in the process environment.
  const env = loadEnv(mode, process.cwd(), '')
  const proxyTarget = resolveProxyTarget(env.VITE_DEV_PROXY_TARGET)

  const proxy = {
    '/api': {
      target: proxyTarget,
      changeOrigin: true,
    },
    // Root liveness endpoint lives outside the /api version prefix.
    '/health': {
      target: proxyTarget,
      changeOrigin: true,
    },
  }

  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      port: 5173,
      host: true,
      strictPort: true,
      proxy,
    },
    preview: {
      port: 4173,
      host: true,
      strictPort: true,
      // The built bundle calls the API same-origin, exactly as it will behind
      // the production reverse proxy, so preview needs the same routes.
      proxy,
    },
    build: {
      outDir: 'dist',
      sourcemap: true,
      rollupOptions: {
        output: {
          manualChunks: manualChunk,
        },
      },
    },
    test: {
      environment: 'jsdom',
      globals: false,
      setupFiles: ['./src/test/setup.ts'],
      css: false,
      // Measured, not guessed: the chart-heavy page tests settle in 1-2 s on an
      // idle machine and took up to 12 s when the suite ran beside other work.
      // Vitest's 5 s default turned that variance into seven intermittent
      // failures that had nothing to do with the code under test. Twenty
      // seconds is past every observed value and still far short of a hang, so a
      // genuinely stuck test still fails rather than blocking the run.
      testTimeout: 20_000,
      include: ['src/**/*.{test,spec}.{ts,tsx}'],
      coverage: {
        provider: 'v8',
        reporter: ['text', 'html'],
        include: ['src/**/*.{ts,tsx}'],
        exclude: ['src/**/*.test.{ts,tsx}', 'src/test/**'],
      },
    },
  }
})