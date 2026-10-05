import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { queryClient } from '@/app/query-client'
import { AppProviders } from '@/app/providers'
import TasksPage from '@/pages/tasks-page'
import { useAuthStore } from '@/stores/auth-store'
import { useToastStore } from '@/stores/toast-store'
import type { ApiErrorEnvelope, User } from '@/types/api'
import type { ActivityStats, Task, TaskStatus } from '@/types/work'

/**
 * The `/tasks` page's transition behaviour, asserted at the network boundary.
 *
 * The page is mounted for real — real providers, real memory router, real
 * `TasksPage` — and only `fetch` is stubbed. So a toast asserted here is a toast
 * the user would have seen, and a request asserted here is one the API would
 * have received.
 *
 * Two behaviours are pinned, both of which used to be wrong in the same way:
 * the affordance, and what happens when the bulk action meets a task the
 * lifecycle will not complete.
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

const PROJECT_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'

function task(id: string, title: string, status: TaskStatus): Task {
  return {
    id,
    project_id: PROJECT_ID,
    owner_id: USER.id,
    parent_id: null,
    title,
    description: null,
    status,
    priority: 'medium',
    start_date: null,
    due_date: null,
    estimated_minutes: null,
    actual_minutes: 0,
    completed_at: null,
    position: 0,
    created_at: '2026-01-01T09:00:00Z',
    updated_at: '2026-01-01T09:00:00Z',
    tag_ids: [],
    is_overdue: false,
    has_blocked_dependencies: false,
  }
}

const TODO = task('11111111-1111-4111-8111-111111111111', 'Write the migration plan', 'todo')
const RUNNING = task('22222222-2222-4222-8222-222222222222', 'Migrate the users table', 'in_progress')

const STATS: ActivityStats = {
  tasks: { total: 2, todo: 1, in_progress: 1, blocked: 0, completed: 0, cancelled: 0, overdue: 0 },
  hours_tracked: { this_week: 0, last_week: 0, all_time: 0 },
} as unknown as ActivityStats

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function envelope(
  code: string,
  message: string,
  status: number,
  details: Record<string, unknown> | null = null,
): Response {
  const body: ApiErrorEnvelope = {
    error: { code, message, details, request_id: 'req-probe' },
  }
  return json(body, status)
}

type Route = (url: string) => Response | Promise<Response>

/** The 422 the API answers for `todo -> completed`, verbatim in shape. */
const ILLEGAL_COMPLETION = () =>
  envelope('validation_error', "A task cannot move from 'todo' to 'completed'.", 422, {
    from: 'todo',
    to: 'completed',
    allowed: ['blocked', 'cancelled', 'in_progress'],
  })

/** A refusal the client could not have predicted: an open prerequisite. */
const OPEN_DEPENDENCY = () =>
  envelope(
    'validation_error',
    'This task is waiting on 1 unfinished prerequisite: "Rotate the signing key".',
    422,
    { prerequisites: ['Rotate the signing key'] },
  )

function installBackend(
  tasks: Task[],
  overrides: Record<string, Route> = {},
): string[] {
  const calls: string[] = []
  const routes: Record<string, Route> = {
    '/auth/me': () => json(USER),
    '/activity/stats': () => json(STATS),
    '/tags': () => json({ items: [], meta: { total: 0, limit: 100, offset: 0 } }),
    '/projects': () =>
      json({
        items: [{ id: PROJECT_ID, name: 'Atlas', status: 'active', priority: 'medium' }],
        meta: { total: 1, limit: 100, offset: 0 },
      }),
    '/tasks': () => json({ items: tasks, meta: { total: tasks.length, limit: 25, offset: 0 } }),
    // The transition routes answer with the task they moved, so a test that
    // does not override one sees a plausible success.
    '/complete': () => json({ ...RUNNING, status: 'completed' }),
    '/start': () => json({ ...RUNNING, status: 'in_progress' }),
    '/reopen': () => json({ ...RUNNING, status: 'todo' }),
    ...overrides,
  }

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      calls.push(url)
      // Longest fragment wins: `…/tasks/{id}/complete` contains `/tasks`, and
      // first-match would answer every transition with a listing page.
      const matched = Object.entries(routes)
        .filter(([fragment]) => url.includes(fragment))
        .sort(([a], [b]) => b.length - a.length)[0]
      if (matched) return matched[1](url)
      return envelope('not_found', `No stub matched ${url}`, 404)
    }),
  )
  return calls
}

function renderTasks() {
  const router = createMemoryRouter([{ path: '/tasks', element: <TasksPage /> }], {
    initialEntries: ['/tasks'],
  })
  return render(
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>,
  )
}

/** Every request the stub answered, by URL fragment, in order. */
function callsTo(calls: string[], fragment: string): string[] {
  return calls.filter((url) => url.includes(fragment))
}

/** The toasts on screen, flattened for assertion. */
function toasts(): { title: string; description: string }[] {
  return useToastStore.getState().toasts.map(({ title, description }) => ({
    title,
    description: description ?? '',
  }))
}

async function selectAll(): Promise<void> {
  for (const name of ['Select Write the migration plan', 'Select Migrate the users table']) {
    await userEvent.click(await screen.findByRole('checkbox', { name }))
  }
}

beforeEach(() => {
  queryClient.clear()
  window.localStorage.clear()
  useToastStore.getState().dismissAll()
  useAuthStore.setState({
    accessToken: 'access-token',
    refreshToken: 'refresh-token',
    user: USER,
    status: 'authenticated',
    pending: false,
    error: null,
  })
})

describe('the tasks page', () => {
  it('offers Start on a task nobody has started, and no Complete', async () => {
    installBackend([TODO, RUNNING])
    renderTasks()

    expect(await screen.findByRole('button', { name: /^Start Write the migration plan$/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Complete Write the migration plan$/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Complete Migrate the users table$/ })).toBeInTheDocument()
  })

  it('starts a task through the route that makes completing it possible', async () => {
    const calls = installBackend([TODO])
    renderTasks()

    await userEvent.click(await screen.findByRole('button', { name: /^Start Write the migration plan$/ }))

    await waitFor(() => expect(calls.some((url) => url.includes('/start'))).toBe(true))
    expect(calls.some((url) => url.includes('/complete'))).toBe(false)
    await waitFor(() => expect(toasts().at(-1)?.title).toBe('Task started'))
  })

  it('completes only the tasks the lifecycle allows, and says which it skipped', async () => {
    const calls = installBackend([TODO, RUNNING])
    renderTasks()
    await selectAll()

    await userEvent.click(await screen.findByRole('button', { name: /Complete selected/ }))

    await waitFor(() =>
      expect(toasts().some((toast) => toast.title === '1 of 2 completed')).toBe(true),
    )
    const toast = toasts().at(-1)!
    // The refusal is legible: which task, and why.
    expect(toast.description).toContain('Write the migration plan')
    expect(toast.description).toContain('to do')
    expect(toast.description).toContain('started')
    // The skipped task is not silently dropped — it stays selected.
    expect(toast.description).toContain('still selected')
    expect(screen.getByRole('checkbox', { name: 'Select Write the migration plan' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Select Migrate the users table' })).not.toBeChecked()

    // Exactly one completion was attempted, and it was the legal one.
    const completions = callsTo(calls, '/complete')
    expect(completions).toHaveLength(1)
    expect(calls.some((url) => url.includes('11111111-1111-4111-8111-111111111111/complete'))).toBe(false)
  })

  it('reports a refusal the client could not have predicted in full', async () => {
    const calls = installBackend([RUNNING], {
      '/complete': OPEN_DEPENDENCY,
    })
    renderTasks()

    await userEvent.click(
      await screen.findByRole('checkbox', { name: 'Select Migrate the users table' }),
    )
    await userEvent.click(await screen.findByRole('button', { name: /Complete selected/ }))

    await waitFor(() => expect(calls.some((url) => url.includes('/complete'))).toBe(true))
    await waitFor(() => expect(toasts().at(-1)?.title).toBe('0 of 1 completed'))
    // The server's own sentence, not a count of refusals.
    expect(toasts().at(-1)?.description).toContain('unfinished prerequisite')
  })

  it('completes everything when everything may be completed', async () => {
    const calls = installBackend([RUNNING])
    renderTasks()

    await userEvent.click(
      await screen.findByRole('checkbox', { name: 'Select Migrate the users table' }),
    )
    await userEvent.click(await screen.findByRole('button', { name: /Complete selected/ }))

    await waitFor(() => expect(toasts().at(-1)?.title).toBe('1 task completed'))
    expect(toasts().at(-1)?.description).toBe('')
    expect(callsTo(calls, '/complete')).toHaveLength(1)
  })

  it('never reports a count of refusals with no explanation', async () => {
    // The shape of the old toast, asserted as absent: "0 of 1 completed —
    // 1 refused — A task cannot move from 'todo' to 'completed'." The count of
    // refusals told the user nothing they could act on.
    installBackend([TODO], { '/complete': ILLEGAL_COMPLETION })
    renderTasks()

    await userEvent.click(
      await screen.findByRole('checkbox', { name: 'Select Write the migration plan' }),
    )
    await userEvent.click(await screen.findByRole('button', { name: /Complete selected/ }))

    await waitFor(() => expect(toasts().length).toBeGreaterThan(0))
    for (const toast of toasts()) {
      expect(toast.description).not.toMatch(/^\d+ refused/)
    }
    // The lifecycle answered before the round trip, so no request was wasted.
    expect(toasts().at(-1)?.title).toBe('0 of 1 completed')
  })
})