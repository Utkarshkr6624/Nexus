import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { VoiceAssistant } from '@/features/assistant/components/voice-assistant'
import { resetAssistantStore } from '@/features/assistant/assistant-store'
import type { RoutingDecisionRead } from '@/types/ml'

/**
 * The panel, mounted for real against the real hook.
 *
 * **Only the network and the browser are faked.** jsdom implements neither
 * `SpeechRecognition` nor `speechSynthesis`, and leaving both absent is not a
 * limitation of the harness but the situation the product ships to Firefox — so
 * the panel is asserted in its real, terminal `unsupported` state rather than in
 * a state only a stub could produce.
 *
 * The typed path is the one worth integrating over the microphone: it is the
 * accessibility story, it is the only way in on a browser with no recogniser,
 * and it is the road a spoken utterance takes afterwards. **The request body is
 * asserted as a whole**, because the privacy claim this surface rests on is that
 * a conversation is never sent to a model that cannot read one — and the backend
 * forbids unknown fields, so an accidental `history` key would be a 422 rather
 * than a leak. Pinning the body is what makes that claim testable.
 */

const ACCEPTED: RoutingDecisionRead = {
  intent: 'task_manage',
  confidence: 0.97,
  threshold: 0.62,
  status: 'accepted',
  destination: 'api/v1/tasks',
  destination_kind: 'router',
  target: {
    service: 'TaskService',
    module: 'app.services.task_service',
    entrypoint: 'TaskService.list',
  },
  reason: 'Task management is a validated NEXUS destination.',
  alternatives: [],
}

const GENERATION: RoutingDecisionRead = {
  intent: 'deep_reasoning',
  confidence: 0.83,
  threshold: 0.62,
  status: 'generation_unavailable',
  destination: 'large-model:unavailable',
  destination_kind: 'large_model',
  target: null,
  reason: 'Deep reasoning requires a generative model.',
  alternatives: [],
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

/**
 * A fresh client per test, with retries off.
 *
 * The shared singleton is registered with an `onSessionChange` handler that
 * clears it mid-test in jsdom, which strands every component at `pending`. The
 * defaults below are the ones in `src/app/query-client.ts`, not a relaxation of
 * them: the retry policy is what decides whether a failure arrives immediately
 * or after a few seconds.
 */
function renderAssistant() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0 },
      mutations: { retry: false },
    },
  })

  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/assistant']}>
        <VoiceAssistant />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function stubRoute(decision: RoutingDecisionRead) {
  const fetchMock = vi.fn(async () => jsonResponse(decision))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => {
  // The conversation lives in a module-level store, so it outlives a render.
  resetAssistantStore()
})

describe('VoiceAssistant', () => {
  it('resolves to a terminal unsupported state in a browser with no recogniser', () => {
    renderAssistant()

    // The pill's own tooltip names the condition, which pins the chip itself
    // rather than the button that repeats the same words.
    expect(
      screen.getByTitle(/This browser ships no speech recogniser/),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /not available in this browser/i })).toBeDisabled()
  })

  it('names the browsers that work and the ones that do not', () => {
    renderAssistant()

    expect(screen.getByText(/Chrome and Edge feature/)).toBeInTheDocument()
    expect(screen.getByText(/Firefox does not implement it at all/)).toBeInTheDocument()
  })

  it('discloses that the browser transcribes off the machine, and why NEXO accepts it', () => {
    renderAssistant()

    // The disclosure is not decoration and not buried: it is on the screen before
    // the microphone is ever pressed, and it names the vendor's servers.
    expect(screen.getByText(/leaves this device/)).toBeInTheDocument()
    expect(screen.getByText(/Google and Microsoft/)).toBeInTheDocument()
    // And the reason for tolerating it is the product constraint, not inertia.
    expect(screen.getByText(/a speech model of our own/)).toBeInTheDocument()
  })

  it('keeps typing available where there is no microphone at all', async () => {
    const fetchMock = stubRoute(ACCEPTED)
    const user = userEvent.setup()
    renderAssistant()

    const field = screen.getByLabelText(/Or type your request/i)
    expect(field).toBeEnabled()
    expect(screen.getByText('This is the only way in on this browser.')).toBeInTheDocument()

    await user.type(field, 'show me my open tasks')
    await user.click(screen.getByRole('button', { name: 'Classify' }))

    // Two regions state the newest decision — the panel's headline and the
    // conversation log — so the claim is that it reached both.
    await waitFor(() => expect(screen.getAllByText('Show my tasks')).toHaveLength(2))
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(field).toHaveValue('')
  })

  it('sends the single utterance and nothing else', async () => {
    const fetchMock = stubRoute(ACCEPTED)
    const user = userEvent.setup()
    renderAssistant()

    await user.type(screen.getByLabelText(/Or type your request/i), 'show me my open tasks')
    await user.click(screen.getByRole('button', { name: 'Classify' }))
    await waitFor(() => expect(screen.getAllByText('TaskService.list')).toHaveLength(2))

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toContain('/ml/route')
    expect(init.method).toBe('POST')
    // The whole privacy claim in one assertion: one string, no transcript, no
    // page, no history. A classifier with no dialogue state cannot use any of it.
    expect(JSON.parse(String(init.body))).toEqual({ text: 'show me my open tasks' })
  })

  it('keeps the typed path open after a failure, because typing is the way out', async () => {
    // `error` is a resting state, not a busy one: the hook accepts a typed turn
    // from it. The panel used to disable the field there anyway, which left a
    // user whose request had just failed staring at a disabled input and a retry
    // button — the one action they did not need, since they can simply type the
    // request they were about to say.
    //
    // A recogniser has to be stubbed in, or the panel never leaves `unsupported`
    // and the field is enabled for a reason that has nothing to do with this.
    const FakeRecognition = class {
      start(): void {}
      stop(): void {}
      abort(): void {}
    }
    vi.stubGlobal('SpeechRecognition', FakeRecognition)
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              error: {
                code: 'ml_unavailable',
                message: 'classifier is not loaded',
                details: null,
                request_id: 'r1',
              },
            }),
            { status: 503, headers: { 'Content-Type': 'application/json' } },
          ),
      ),
    )
    const user = userEvent.setup()
    renderAssistant()

    await user.type(screen.getByLabelText(/Or type your request/i), 'show me my tasks')
    await user.click(screen.getByRole('button', { name: 'Classify' }))
    await waitFor(() =>
      expect(screen.getAllByText(/is not running on this backend/i).length).toBeGreaterThan(0),
    )

    const field = screen.getByLabelText(/Or type your request/i)
    expect(field).toBeEnabled()

    await user.clear(field)
    await user.type(field, 'and now my projects')
    expect(screen.getByRole('button', { name: 'Classify' })).toBeEnabled()
  })

  it('keeps the conversation on screen, oldest first', async () => {
    stubRoute(ACCEPTED)
    const user = userEvent.setup()
    const { container } = renderAssistant()

    const field = screen.getByLabelText(/Or type your request/i)
    await user.type(field, 'show me my open tasks')
    await user.click(screen.getByRole('button', { name: 'Classify' }))
    await screen.findByText('show me my open tasks')

    await user.clear(field)
    await user.type(field, 'what are my open risks')
    await user.click(screen.getByRole('button', { name: 'Classify' }))

    await waitFor(() => {
      expect(container.querySelectorAll('li')).toHaveLength(2)
    })

    const entries = Array.from(container.querySelectorAll('li')).map((node) => node.textContent)
    expect(entries[0]).toContain('show me my open tasks')
    expect(entries[1]).toContain('what are my open risks')
  })

  it('reports a request that needs generation as a gap, not as a reply', async () => {
    stubRoute(GENERATION)
    const user = userEvent.setup()
    renderAssistant()

    await user.type(screen.getByLabelText(/Or type your request/i), 'help me think this through')
    await user.click(screen.getByRole('button', { name: 'Classify' }))

    expect(await screen.findAllByText('Needs generation')).toHaveLength(2)
    expect(screen.getAllByText(/no generative model/i).length).toBeGreaterThan(0)
    // Nothing here may read as though the assistant composed an answer.
    expect(screen.queryByText(/^Routed$/)).not.toBeInTheDocument()
  })

  it('mounts its live region empty, and keeps it mounted', () => {
    const { container, rerender } = renderAssistant()

    const region = container.querySelector('[aria-live="polite"]')
    expect(region).not.toBeNull()
    // Mounted *empty*: a live region that arrives with its first message is
    // frequently never announced at all, which is the whole reason for the rule.
    expect(region).toBeEmptyDOMElement()
    expect(region).toHaveAttribute('aria-atomic', 'true')

    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={['/assistant']}>
          <VoiceAssistant />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    expect(container.querySelector('[aria-live="polite"]')).not.toBeNull()
  })

  it('renders exactly one heading below the page masthead', () => {
    const { container } = renderAssistant()

    // The route owns the single `<h1>`; a panel that claimed another would give
    // the document two mastheads.
    expect(container.querySelectorAll('h1')).toHaveLength(0)
    expect(container.querySelectorAll('h2').length).toBeGreaterThan(0)
  })
})

describe('the destination an accepted turn names', () => {
  /**
   * Rendered with real routes so navigation is observable. The shared
   * `renderAssistant` mounts a bare router, where `navigate` would move the
   * location without anything rendering — which is precisely the "nothing
   * happened" failure this block exists to rule out.
   */
  function renderWithRoutes() {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    return render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/assistant']}>
          <Routes>
            <Route path="/assistant" element={<VoiceAssistant />} />
            <Route path="/projects" element={<p>Projects page</p>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
  }

  const PROJECTS: RoutingDecisionRead = {
    intent: 'project_manage',
    confidence: 0.98,
    threshold: 0.9,
    status: 'accepted',
    destination: 'api/v1/projects',
    destination_kind: 'router',
    target: {
      service: 'ProjectService',
      module: 'app.services.project_service',
      entrypoint: 'ProjectService.create',
    },
    reason: 'Project management is a validated NEXUS destination.',
    alternatives: [],
  }

  it('offers the route it just named, and the button takes you there', async () => {
    stubRoute(PROJECTS)
    const user = userEvent.setup()
    renderWithRoutes()

    await user.type(screen.getByLabelText(/Or type your request/i), 'add a project called hello')
    await user.click(screen.getByRole('button', { name: 'Classify' }))

    // Without this the turn names a destination and leaves the reader with no
    // way to reach it, which reads as the assistant having done nothing.
    const goTo = await screen.findByRole('button', { name: /go to projects/i })
    await user.click(goTo)

    await waitFor(() => expect(screen.getByText('Projects page')).toBeInTheDocument())
  })

  it('withholds the button when the destination names no page', async () => {
    // `api/v1/users` is a real destination with no page behind it, so the
    // honest outcome is no button rather than a link to a guess.
    stubRoute({ ...PROJECTS, destination: 'api/v1/users', intent: 'account_admin' })
    const user = userEvent.setup()
    renderWithRoutes()

    await user.type(screen.getByLabelText(/Or type your request/i), 'deactivate my account')
    await user.click(screen.getByRole('button', { name: 'Classify' }))

    // The decision itself is one text node, so it is a reliable settle point.
    await screen.findByText('deactivate my account')
    expect(screen.queryByRole('button', { name: /^go to /i })).toBeNull()
  })
})
