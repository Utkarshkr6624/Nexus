import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/lib/api-client'
import { RequireAnonymous, RequireAuth } from '@/routes/guards'
import { useAuthStore } from '@/stores/auth-store'
import type { User } from '@/types'

/**
 * What the session gates say when the backend is not answering.
 *
 * The regression these cover is a lie told to the user: a stored session the
 * app could not verify was reported as no session at all, so the gate sent a
 * signed-in user to the sign-in form — after discarding their tokens — while
 * the backend they would have signed in against was unreachable. The login
 * form is a claim about the session, and an outage is not evidence for it.
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

const OFFLINE = new ApiError({
  status: 0,
  code: 'network_error',
  message: 'Failed to fetch',
})

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function unreachableSession(): void {
  window.localStorage.clear()
  useAuthStore.setState({
    accessToken: 'boot-access',
    refreshToken: 'boot-refresh',
    user: USER,
    status: 'unreachable',
    pending: false,
    error: null,
    bootError: OFFLINE,
  })
}

function renderProtected() {
  return render(
    <MemoryRouter initialEntries={['/projects']}>
      <Routes>
        <Route
          path="/projects"
          element={
            <RequireAuth>
              <p>Project list</p>
            </RequireAuth>
          }
        />
        <Route path="/login" element={<p>Sign in form</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  window.localStorage.clear()
  useAuthStore.setState({
    accessToken: null,
    refreshToken: null,
    user: null,
    status: 'anonymous',
    pending: false,
    error: null,
    bootError: null,
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('RequireAuth when the backend never answered', () => {
  it('reports the outage instead of sending a signed-in user to the sign-in form', () => {
    unreachableSession()

    renderProtected()

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Cannot reach the NEXUS backend')
    // The sign-in form is a claim that the session is gone. Nothing here is.
    expect(screen.queryByText('Sign in form')).not.toBeInTheDocument()
    expect(screen.getByText(/still signed in/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument()
  })

  it('recovers in place when Retry finds the backend again', async () => {
    unreachableSession()
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/auth/me')) return json(USER)
        return new Response(null, { status: 404 })
      }),
    )
    const user = userEvent.setup()
    renderProtected()

    await user.click(screen.getByRole('button', { name: /Retry/i }))

    // No reload, no re-entry of credentials: the same tab reaches its content.
    expect(await screen.findByText('Project list')).toBeInTheDocument()
    expect(useAuthStore.getState().status).toBe('authenticated')
    expect(useAuthStore.getState().bootError).toBeNull()
  })
})

describe('RequireAnonymous when the backend never answered', () => {
  it('shows the outage rather than a sign-in form that cannot succeed', async () => {
    unreachableSession()

    render(
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route
            path="/login"
            element={
              <RequireAnonymous>
                <p>Sign in form</p>
              </RequireAnonymous>
            }
          />
        </Routes>
      </MemoryRouter>,
    )

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.queryByText('Sign in form')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument()
  })
})
