import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createBrowserRouter } from 'react-router-dom'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { AppProviders } from '@/app/providers'
import { NAV_GROUPS } from '@/features/modules/catalog'
import { useAuthStore } from '@/stores/auth-store'
import { useThemeStore } from '@/stores/theme-store'
import type { User } from '@/types/api'

/**
 * End-to-end smoke test for the application shell: routing guards, sign-in,
 * the live health card, the ⌘K palette and the theme toggle, against a mocked
 * backend. It exercises the real router and the real stores.
 */

/*
 * Typed as `User` on purpose. An untyped object literal satisfies any fetch
 * stub, so the moment the wire shape drifts — `full_name` becoming
 * `display_name`, `is_superuser` becoming `role` + `permissions` — the mock
 * keeps answering with a body the app no longer understands and the failure
 * surfaces as a confusing timeout somewhere else in the tree instead of a
 * compile error here.
 */
const USER: User = {
  id: '11111111-1111-4111-8111-111111111111',
  email: 'ada@nexus.local',
  username: 'ada',
  display_name: 'Ada Lovelace',
  avatar_url: null,
  role: 'user',
  permissions: [],
  is_active: true,
  is_verified: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  last_login_at: null,
}

const SESSION_ID = '22222222-2222-4222-8222-222222222222'

const HEALTH = {
  status: 'healthy',
  app: 'NEXUS',
  version: '0.1.0',
  environment: 'development',
  database: { status: 'connected', latency_ms: 1.23 },
  uptime_seconds: 3725.5,
  timestamp: '2026-01-01T00:00:00Z',
}

/**
 * `greetingFor` has four branches, not three: between midnight and 05:00 the
 * dashboard says "Still up", so a three-way regex would fail by wall-clock time
 * rather than by regression.
 */
const GREETING = /Good (morning|afternoon|evening)|Still up/

/** The dashboard's `<h1>`, which greets by display name rather than by handle. */
function dashboardHeading() {
  return { name: new RegExp(`${GREETING.source}, Ada`) }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

type DataRouter = ReturnType<typeof createBrowserRouter>

let router: DataRouter

beforeAll(async () => {
  window.history.replaceState({}, '', '/login')
  const module = await import('@/routes/router')
  router = module.router
})

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/auth/login')) {
        return json({
          access_token: 'access-token',
          refresh_token: 'refresh-token',
          token_type: 'bearer',
          expires_in: 3600,
          session_id: SESSION_ID,
        })
      }
      if (url.includes('/auth/me')) return json(USER)
      if (url.includes('/auth/logout')) return new Response(null, { status: 204 })
      if (url.includes('/health')) return json(HEALTH)
      return json(
        { error: { code: 'not_found', message: 'Not found', details: null, request_id: 'r1' } },
        404,
      )
    }),
  )
  useThemeStore.getState().setTheme('dark')
})

function renderApp() {
  return render(
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>,
  )
}

describe('application shell', () => {
  it('redirects an anonymous visitor from a protected route to /login', async () => {
    window.history.replaceState({}, '', '/dashboard')
    await router.navigate('/dashboard')
    renderApp()

    // The brand is rendered by `AuthShell` separately from its title, so the
    // heading is the screen's own name for itself and nothing more.
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/login')
  })

  it('signs in against the real auth endpoints and lands on the dashboard', async () => {
    const user = userEvent.setup()
    renderApp()

    await screen.findByRole('heading', { name: 'Sign in' })
    await user.type(screen.getByLabelText('Email'), 'ada@nexus.local')
    await user.type(screen.getByLabelText('Password'), 'correct-horse-battery')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    // `/dashboard` is lazily loaded (`src/routes/lazy-pages.ts`), and the chunk
    // now pulls in the analytics surface with it, so the heading can land well
    // after the default 1s `findByRole` budget. The siblings below that also
    // wait on a lazy route pass an explicit timeout for the same reason.
    expect(
      await screen.findByRole('heading', dashboardHeading(), { timeout: 20_000 }),
    ).toBeInTheDocument()
    expect(useAuthStore.getState().status).toBe('authenticated')
    expect(useAuthStore.getState().accessToken).toBe('access-token')
  }, 30_000)

  it('renders the shell, the sidebar groups and the live health card', async () => {
    renderApp()
    await screen.findByRole('heading', dashboardHeading())

    const nav = screen.getByRole('navigation', { name: 'Primary' })

    // Grouping is a Phase 2 change: the rail now renders the registry's
    // categories, so the expected order is derived from the registry rather
    // than hand-listed here.
    const grouped = NAV_GROUPS.flatMap((group) => group.items)
    expect(grouped.length).toBeGreaterThan(1)
    expect(within(nav).getAllByRole('link').map((link) => link.getAttribute('href'))).toEqual(
      grouped.map((item) => item.to),
    )

    // A group of one is a home row, not a section: only the multi-item groups
    // get a heading, so the Dashboard sits above "Work" with nothing over it.
    const headings = within(nav).getAllByRole('heading').map((heading) => heading.textContent)
    expect(headings).toEqual(
      NAV_GROUPS.filter((group) => group.items.length > 1).map((group) => group.label),
    )

    // /settings is a footer row, outside the grouped rail.
    expect(within(nav).queryByRole('link', { name: 'Settings' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Settings' })).toHaveAttribute('href', '/settings')

    expect(screen.getByText('Backend health')).toBeInTheDocument()

    // "healthy" is shown twice by design: once as the KPI, once as the status badge.
    await waitFor(() => expect(screen.getAllByText('healthy').length).toBeGreaterThan(0))
    expect(screen.getByText('0.1.0')).toBeInTheDocument()
    expect(screen.getByText('development')).toBeInTheDocument()
    expect(screen.getByText('connected')).toBeInTheDocument()
    expect(screen.getByText('1h 2m')).toBeInTheDocument()
  })

  it('opens the command palette with Ctrl+K and navigates to a module', async () => {
    const user = userEvent.setup()
    renderApp()
    await screen.findByRole('heading', dashboardHeading())

    await user.keyboard('{Control>}k{/Control}')

    const dialog = await screen.findByRole('dialog', { name: 'Command palette' })
    const filter = within(dialog).getByLabelText('Filter commands, destinations and records')

    await user.type(filter, 'planner')
    await waitFor(() => {
      expect(within(dialog).getAllByRole('option')).toHaveLength(1)
    })

    await user.keyboard('{Enter}')
    // Assert the navigation only. Phase 4 replaced the `ModulePage` placeholder
    // with a real planner that mounts its own queries, and this fixture's mocked
    // `fetch` answers those with a 404 envelope — the page correctly renders its
    // error surface. What this test owns is "the palette routes you", so pinning
    // the destination's data-dependent copy would only couple it to which request
    // happens to resolve.
    expect(router.state.location.pathname).toBe('/planner')
  })

  it('applies the theme preference to the document element', async () => {
    const user = userEvent.setup()
    await router.navigate('/dashboard')
    renderApp()
    await screen.findByRole('heading', dashboardHeading())

    await user.click(screen.getByRole('button', { name: /Theme:/ }))
    await user.click(await screen.findByRole('menuitemradio', { name: 'Light' }))

    expect(useThemeStore.getState().resolvedTheme).toBe('light')
    await waitFor(() => expect(document.documentElement.classList.contains('dark')).toBe(false))
  })

  it('degrades to a retryable error state when the backend is down', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/auth/me')) return json(USER)
        throw new TypeError('Failed to fetch')
      }),
    )
    const { queryClient } = await import('@/app/query-client')
    queryClient.clear()

    await router.navigate('/dashboard')
    renderApp()

    // The shared query client retries transport failures twice before settling,
    // with an exponential backoff — three attempts cost several seconds, which
    // is past the default 5s test timeout and has to be allowed for explicitly.
    expect(
      await screen.findByText('Cannot reach the NEXUS backend', {}, { timeout: 20_000 }),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument()
    // No stack traces, no raw transport internals.
    expect(screen.queryByText(/TypeError/)).not.toBeInTheDocument()
  }, 30_000)
})