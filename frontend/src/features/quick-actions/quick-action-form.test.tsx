import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { QuickActionForm } from '@/features/quick-actions/quick-action-form'
import { findQuickAction } from '@/features/quick-actions/quick-actions'
import type { QuickActionId } from '@/features/quick-actions/quick-actions'

/**
 * Quick actions, against a stubbed backend.
 *
 * The whole point of this surface is that it is not a mock. A command palette
 * that *looks* like it created something is the worst possible failure here: the
 * user walks away believing a task exists. So the tests below assert three
 * separate things that a fake would pass and a real integration must not —
 * that nothing is written before the button is pressed, that the body sent is
 * the body the endpoint documents, and that a refusal is reported as a refusal.
 */

const CREATE_TASK = findQuickAction('create-task')
const CREATE_NOTE = findQuickAction('create-note')
const SCHEDULE_TIME = findQuickAction('schedule-time')

if (!CREATE_TASK?.form || !CREATE_NOTE?.form || !SCHEDULE_TIME?.form) {
  throw new Error('the quick-action registry is missing a form these tests exercise')
}

const PROJECT_A = '11111111-1111-4111-8111-111111111111'
const PROJECT_B = '22222222-2222-4222-8222-222222222222'

function project(id: string, name: string) {
  return {
    id,
    owner_id: '33333333-3333-4333-8333-333333333333',
    name,
    description: null,
    status: 'active',
    priority: 'medium',
    start_date: null,
    target_date: null,
    completed_at: null,
    archived_at: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function apiFailure(message: string, status = 422): Response {
  return json(
    { error: { code: 'validation_error', message, details: null, request_id: 'req-1' } },
    status,
  )
}

interface Recorded {
  url: string
  method: string
  body: unknown
}

interface Stub {
  calls: Recorded[]
  postsTo: (path: string) => Recorded[]
}

function stubFetch(
  handler: (url: string, method: string, body: RequestInit['body']) => Response,
): Stub {
  const calls: Recorded[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      const raw = init?.body
      const body = typeof raw === 'string' ? JSON.parse(raw) : null
      calls.push({ url, method, body })
      return handler(url, method, body)
    }),
  )
  return {
    calls,
    postsTo: (path) => calls.filter((call) => call.method === 'POST' && call.url.includes(path)),
  }
}

function renderForm(actionId: QuickActionId, onDone = vi.fn()) {
  const action = findQuickAction(actionId)
  if (!action?.form) throw new Error(`${actionId} has no form`)
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <QuickActionForm action={action} onDone={onDone} onCancel={vi.fn()} />
    </QueryClientProvider>,
  )
  return { onDone }
}

/** The projects picker is only populated once `GET /projects` has answered. */
async function awaitProjects(): Promise<HTMLSelectElement> {
  return (await screen.findByLabelText<HTMLSelectElement>('Project')) as HTMLSelectElement
}

beforeEach(() => {
  vi.unstubAllGlobals()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('quick action requests', () => {
  it('writes nothing until the user submits', async () => {
    const user = userEvent.setup()
    const stub = stubFetch((url, method) => {
      if (url.includes('/projects')) return json({ items: [project(PROJECT_A, 'Atlas')], meta: {} })
      if (method === 'POST') return json({ id: PROJECT_A, title: 'Rotate staging' })
      return json({}, 500)
    })

    renderForm('create-task')
    await awaitProjects()

    await user.type(screen.getByLabelText('Task title'), 'Rotate staging')
    await user.selectOptions(screen.getByLabelText('Project'), PROJECT_A)

    // Typing and choosing are not consent to write.
    expect(stub.postsTo('/tasks')).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: 'Create' }))

    expect(await screen.findByText(/Created/)).toBeInTheDocument()
    expect(stub.postsTo('/tasks')).toHaveLength(1)
  })

  it('posts a task to /tasks with the documented body', async () => {
    const user = userEvent.setup()
    const stub = stubFetch((url, method) => {
      if (url.includes('/projects')) {
        return json({
          items: [project(PROJECT_A, 'Atlas'), project(PROJECT_B, 'Beacon')],
          meta: {},
        })
      }
      if (method === 'POST') return json({ id: PROJECT_A, title: 'Rotate the staging credentials' })
      return json({}, 500)
    })

    renderForm('create-task')
    await awaitProjects()

    await user.type(screen.getByLabelText('Task title'), 'Rotate the staging credentials')
    await user.selectOptions(screen.getByLabelText('Project'), PROJECT_B)
    // The shared `Label` appends "Optional" to the visible text, so the
    // optional fields are matched by prefix rather than by whole string.
    fireEvent.change(screen.getByLabelText(/^Due date/), { target: { value: '2026-03-04' } })

    await user.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(stub.postsTo('/tasks')).toHaveLength(1))
    const [post] = stub.postsTo('/tasks')
    expect(post?.url).toBe('/api/v1/tasks')
    expect(post?.body).toEqual({
      project_id: PROJECT_B,
      title: 'Rotate the staging credentials',
      due_date: '2026-03-04',
    })
  })

  it('omits the optional fields rather than guessing them', async () => {
    const user = userEvent.setup()
    const stub = stubFetch((url, method) => {
      if (url.includes('/projects')) return json({ items: [project(PROJECT_A, 'Atlas')], meta: {} })
      if (method === 'POST') return json({ id: PROJECT_A, title: 'Rotate staging' })
      return json({}, 500)
    })

    renderForm('create-task')
    await awaitProjects()

    await user.type(screen.getByLabelText('Task title'), 'Rotate staging')
    await user.selectOptions(screen.getByLabelText('Project'), PROJECT_A)
    await user.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(stub.postsTo('/tasks')).toHaveLength(1))
    // The backend defaults priority server-side; pinning "medium" here would
    // make a deliberate choice indistinguishable from an untouched form.
    expect(Object.keys(stub.postsTo('/tasks')[0]?.body as object)).toEqual(['project_id', 'title'])
  })

  it('reports a created task with the name the server echoed back', async () => {
    const user = userEvent.setup()
    stubFetch((url, method) => {
      if (url.includes('/projects')) return json({ items: [project(PROJECT_A, 'Atlas')], meta: {} })
      if (method === 'POST') return json({ id: PROJECT_A, title: 'Rotate the staging credentials' })
      return json({}, 500)
    })

    const { onDone } = renderForm('create-task')
    await awaitProjects()

    await user.type(screen.getByLabelText('Task title'), 'Rotate the staging credentials')
    await user.selectOptions(screen.getByLabelText('Project'), PROJECT_A)
    await user.click(screen.getByRole('button', { name: 'Create' }))

    expect(await screen.findByText('Created “Rotate the staging credentials”.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Done' }))
    expect(onDone).toHaveBeenCalledOnce()
  })

  it('reports a refusal as a refusal and never as a creation', async () => {
    const user = userEvent.setup()
    stubFetch((url, method) => {
      if (url.includes('/projects')) return json({ items: [project(PROJECT_A, 'Atlas')], meta: {} })
      if (method === 'POST') return apiFailure('Title is already used by a sibling task.')
      return json({}, 500)
    })

    renderForm('create-task')
    await awaitProjects()

    await user.type(screen.getByLabelText('Task title'), 'Duplicate')
    await user.selectOptions(screen.getByLabelText('Project'), PROJECT_A)
    await user.click(screen.getByRole('button', { name: 'Create' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Title is already used by a sibling task.',
    )
    expect(screen.getByText(/Nothing was created\./)).toBeInTheDocument()
    expect(screen.queryByText(/^Created /)).not.toBeInTheDocument()
  })

  it('reports a transport failure as a failure rather than swallowing it', async () => {
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).includes('/projects')) {
          return json({ items: [project(PROJECT_A, 'Atlas')], meta: {} })
        }
        throw new TypeError('Failed to fetch')
      }),
    )

    renderForm('create-task')
    await awaitProjects()

    await user.type(screen.getByLabelText('Task title'), 'Rotate staging')
    await user.selectOptions(screen.getByLabelText('Project'), PROJECT_A)
    await user.click(screen.getByRole('button', { name: 'Create' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/Failed to fetch/)
    expect(screen.queryByText(/^Created /)).not.toBeInTheDocument()
  })

  it('posts a note to the knowledge endpoint, not the work one', async () => {
    const user = userEvent.setup()
    const stub = stubFetch((_url, method) => {
      if (method === 'POST') return json({ id: PROJECT_A, title: 'Migration notes' })
      return json({}, 500)
    })

    renderForm('create-note')
    await user.type(screen.getByLabelText('Note title'), 'Migration notes')
    await user.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(stub.postsTo('/knowledge/notes')).toHaveLength(1))
    expect(stub.postsTo('/knowledge/notes')[0]?.url).toBe('/api/v1/knowledge/notes')
    expect(stub.postsTo('/knowledge/notes')[0]?.body).toEqual({ title: 'Migration notes' })
    // A note form has no project field, so it must not spend a request on one.
    expect(stub.calls.some((call) => call.url.includes('/projects'))).toBe(false)
  })

  it('sends the chosen wall-clock time as a real instant', async () => {
    const user = userEvent.setup()
    const stub = stubFetch((_url, method) => {
      if (method === 'POST') return json({ id: PROJECT_A, title: 'Design review' })
      return json({}, 500)
    })

    renderForm('schedule-time')
    await user.type(screen.getByLabelText('What is it for'), 'Design review')
    fireEvent.change(screen.getByLabelText('Date'), { target: { value: '2026-04-02' } })
    fireEvent.change(screen.getByLabelText('From'), { target: { value: '09:00' } })
    fireEvent.change(screen.getByLabelText('To'), { target: { value: '10:30' } })

    await user.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(stub.postsTo('/calendar')).toHaveLength(1))
    const body = stub.postsTo('/calendar')[0]?.body as {
      title: string
      starts_at: string
      ends_at: string
    }
    expect(body.title).toBe('Design review')
    // Local wall-clock, stated in UTC: what a person typing "09:00" meant.
    expect(body.starts_at).toMatch(/Z$/)
    expect(new Date(body.ends_at).getTime()).toBeGreaterThan(new Date(body.starts_at).getTime())
  })

  it('refuses to submit a task when there is no project to file it under', async () => {
    const user = userEvent.setup()
    const stub = stubFetch((_url, method) => {
      if (_url.includes('/projects')) return json({ items: [], meta: { total: 0 } })
      if (method === 'POST') return json({}, 500)
      return json({}, 500)
    })

    renderForm('create-task')

    expect(await screen.findByText(/You have no projects yet/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create' })).toBeDisabled()
    await user.type(screen.getByLabelText('Task title'), 'Orphan')
    expect(stub.postsTo('/tasks')).toHaveLength(0)
  })
})
