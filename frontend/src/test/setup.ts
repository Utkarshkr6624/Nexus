import '@testing-library/jest-dom/vitest'

import { cleanup, configure } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'

/**
 * Environment shims for jsdom, shared by every test file.
 *
 * jsdom implements neither layout nor a browser-grade fetch, and both gaps
 * break real code paths rather than test-only ones:
 *
 * - Radix (dropdown menus, tooltips, the command palette) calls
 *   `scrollIntoView` when it positions a viewport; jsdom has no layout engine
 *   and leaves the method undefined.
 * - `api-client` forwards an `AbortSignal` to `fetch`. React Query builds that
 *   signal from a Node/undici `Request`, which is not the jsdom realm, so undici
 *   rejects the foreign signal object. No route in this app declares a loader,
 *   so dropping the signal in tests is safe.
 * - jsdom has no `window.matchMedia` at all, so anything that consults
 *   `prefers-color-scheme` would silently degrade. The shim below reports
 *   "no dark preference" until a test stubs `window.matchMedia` itself.
 *
 * They are installed per test rather than once at import time, because
 * `unstubAllGlobals()` in the same hook would otherwise tear them down after the
 * first test. Individual test files therefore stay free of one-off shims.
 */
function installEnvironmentShims(): void {
  const NativeRequest = globalThis.Request
  vi.stubGlobal(
    'Request',
    class extends NativeRequest {
      constructor(input: RequestInfo | URL, init?: RequestInit) {
        super(input, { ...init, signal: undefined })
      }
    },
  )

  if (typeof Element.prototype.scrollIntoView !== 'function') {
    Element.prototype.scrollIntoView = () => undefined
  }

  // Recharts' `ResponsiveContainer` subscribes to `ResizeObserver` in an effect,
  // and jsdom implements neither layout nor the observer. Without this, rendering
  // any chart throws `ResizeObserver is not defined` and takes the tree with it,
  // so the analytics surfaces could not be rendered in a test at all.
  if (typeof globalThis.ResizeObserver !== 'function') {
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe(): void {}
        unobserve(): void {}
        disconnect(): void {}
      },
    )
  }

  if (typeof window.matchMedia !== 'function') {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: (query: string): MediaQueryList =>
        ({
          media: query,
          matches: false,
          onchange: null,
          addEventListener: () => undefined,
          removeEventListener: () => undefined,
          addListener: () => undefined,
          removeListener: () => undefined,
          dispatchEvent: () => false,
        }) as unknown as MediaQueryList,
    })
  }
}

// Testing Library resolves `findBy*` against a 1 s default, which is shorter
// than a recharts surface needs to paint under load — the failure surfaced as a
// "unable to find" on an element that was present, which is a worse message than
// a timeout and points the reader at the wrong thing. Raised to match the
// suite's own `testTimeout` rather than patched per call site.
configure({ asyncUtilTimeout: 20_000 })

beforeEach(() => {
  vi.unstubAllGlobals()
  installEnvironmentShims()
})

afterEach(() => {
  cleanup()
  // Tests stub `fetch` and friends; leaving those behind leaks into the next one.
  vi.unstubAllGlobals()
})