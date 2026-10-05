import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { VoiceAssistant } from '@/features/assistant/components/voice-assistant'
import { resetAssistantStore } from '@/features/assistant/assistant-store'
import type { ConfirmActionRead, ProposeActionRead, RoutingDecisionRead } from '@/types/ml'

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
 *
 * **A turn is two requests now**, so the stub answers by endpoint: a routing
 * decision for `/ml/route`, a propose answer for `/ml/action/propose`, and a
 * confirm answer for `/ml/action/confirm`. Handing one fixture to all three
 * would be describing a backend that does not exist.
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

/** A refusal is a 200. This is the answer most utterances produce. */
const REFUSED: ProposeActionRead = {
  proposed: false,
  intent: 'task_manage',
  confidence: 0.98,
  proposal: null,
  refusal: {
    kind: 'create_task',
    intent: 'task_manage',
    confidence: 0.98,
    reason_code: 'context_missing',
    reason:
      "Creating a task needs the project it belongs to, and no project was supplied with the request. Open the project's board and add it there.",
    arguments: [],
    notes: [],
  },
}

const CREATE_PROJECT: ProposeActionRead = {
  proposed: true,
  intent: 'project_manage',
  confidence: 0.98,
  proposal: {
    kind: 'create_project',
    intent: 'project_manage',
    confidence: 0.98,
    summary: "Create a project named 'HelloWorld'.",
    requires_confirmation: true,
    destructive: false,
    permission: 'projects.write',
    service: 'ProjectService',
    module: 'app.services.project_service',
    entrypoint: 'create',
    payload_schema: 'ProjectCreate',
    payload: { name: 'HelloWorld', description: null, priority: 'medium' },
    target_id: null,
    target_label: null,
    arguments: [
      {
        field: 'title',
        value: 'HelloWorld',
        matched_text: 'add a project called HelloWorld',
        rule: "the utterance with 'a project called' removed",
      },
    ],
    notes: ['No start or target date was given, so NEXO did not guess one.'],
  },
  refusal: null,
}

const CREATED: ConfirmActionRead = {
  kind: 'create_project',
  entity: 'project',
  entity_id: 'a9e9ce1c-0000-4000-8000-000000000000',
  outcome: 'created',
  applied: true,
  message: "Created the project 'HelloWorld'.",
}

/** The routing answer for the sentence the create fixtures above belong to. */
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

/** The confirm request bodies this suite recorded, in order. */
function bodiesFor(fetchMock: ReturnType<typeof vi.fn>, path: string): unknown[] {
  return fetchMock.mock.calls
    .filter((call) => String(call[0]).includes(path))
    .map((call) => JSON.parse(String((call[1] as RequestInit).body)))
}

/**
 * A fresh client per test, with retries off.
 *
 * The shared singleton is registered with an `onSessionChange` handler that
 * clears it mid-test in jsdom, which strands every component at `pending`. The
 * defaults below are the ones in `src/app/query-client.ts`, not a relaxation of
 * them: the retry policy is what decides whether a failure arrives immediately
 * or after a few seconds.
 *
 * The client is returned alongside the render result so a test can watch cache
 * invalidation — which is the only way a new row appears without a refresh, and
 * therefore the one part of the write half with no visible symptom of its own.
 */
function renderAssistant() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0 },
      mutations: { retry: false },
    },
  })

  return {
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/assistant']}>
          <VoiceAssistant />
        </MemoryRouter>
      </QueryClientProvider>,
    ),
    client,
  }
}

/**
 * Answers each `/ml` endpoint with the body it actually gets in life.
 *
 * `propose` and `confirm` default to a refusal, which is the quiet path and the
 * one every test below the routing block exercises by default.
 */
function stubEndpoints({
  route = () => jsonResponse(ACCEPTED),
  propose = () => jsonResponse(REFUSED),
  confirm = () => jsonResponse(CREATED),
}: {
  route?: () => Response
  propose?: () => Response
  confirm?: () => Response
} = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/ml/action/confirm')) return confirm()
    if (url.includes('/ml/action/propose')) return propose()
    return route()
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function stubRoute(decision: RoutingDecisionRead, propose: ProposeActionRead = REFUSED) {
  return stubEndpoints({ route: () => jsonResponse(decision), propose: () => jsonResponse(propose) })
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
    // Exactly one routing request for one turn. Scoped to the endpoint because
    // the turn now also asks what could be created, and this test is about the
    // typed path still working rather than about the total.
    const routingCalls = fetchMock.mock.calls.filter((call) =>
      String(call[0]).includes('/ml/route'),
    )
    expect(routingCalls).toHaveLength(1)
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

/**
 * The confirm step, as the reader meets it.
 *
 * These are rendered through the panel rather than around the dialog component
 * on purpose: what matters is that a turn produces one, that it says the
 * backend's sentence rather than a locally composed one, and that neither answer
 * leaves the panel somewhere it cannot be operated from. A dialog tested on its
 * own would pass while the hook never opened it.
 */
describe('the confirmation a creatable turn produces', () => {
  /**
   * Types one request and presses Classify, returning the same session so the
   * rest of the case drives the panel through it. `setup()` is called once per
   * test: a second one would mean a second document to type into.
   */
  async function ask(text: string) {
    const user = userEvent.setup()
    await user.type(screen.getByLabelText(/Or type your request/i), text)
    await user.click(screen.getByRole('button', { name: 'Classify' }))
    return user
  }

  it('offers the backend’s own sentence, with a Confirm and a Cancel', async () => {
    stubEndpoints({
      route: () => jsonResponse(PROJECTS),
      propose: () => jsonResponse(CREATE_PROJECT),
    })
    renderAssistant()
    await ask('add a project called HelloWorld')

    const dialog = await screen.findByRole('dialog')

    // The sentence is composed by the proposal layer from what the extractor
    // actually read. A locally written equivalent would be a second description
    // of the same write, free to disagree with the one being agreed to.
    expect(dialog).toHaveAccessibleDescription("Create a project named 'HelloWorld'.")
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled()
    // The reasoning travels with it: what was read, and from which span.
    expect(screen.getByText('HelloWorld')).toBeInTheDocument()
    expect(screen.getByText(/read from “add a project called HelloWorld”/)).toBeInTheDocument()
    expect(
      screen.getByText('No start or target date was given, so NEXO did not guess one.'),
    ).toBeInTheDocument()
  })

  it('is keyboard-operable: Escape is a cancel, and it discards the proposal', async () => {
    const fetchMock = stubEndpoints({
      route: () => jsonResponse(PROJECTS),
      propose: () => jsonResponse(CREATE_PROJECT),
    })
    renderAssistant()
    const user = await ask('add a project called HelloWorld')
    await screen.findByRole('dialog')

    await user.keyboard('{Escape}')

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    // Escape cancelled; it did not confirm. Nothing was written.
    expect(bodiesFor(fetchMock, '/action/confirm')).toHaveLength(0)
    // And the panel is usable again rather than trapped behind a dead dialog.
    // `Classify` is only ever enabled with a draft in the field, so the field is
    // the thing that has to be live — and typing has to bring the button back.
    const field = screen.getByLabelText(/Or type your request/i)
    expect(field).toBeEnabled()
    await user.type(field, 'show my open tasks')
    expect(screen.getByRole('button', { name: 'Classify' })).toBeEnabled()
  })

  it('confirms with exactly what the proposal published, and shows what happened', async () => {
    const fetchMock = stubEndpoints({
      route: () => jsonResponse(PROJECTS),
      propose: () => jsonResponse(CREATE_PROJECT),
      confirm: () => jsonResponse(CREATED),
    })
    const { client } = renderAssistant()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    const user = await ask('add a project called HelloWorld')
    await screen.findByRole('dialog')

    await user.click(screen.getByRole('button', { name: 'Confirm' }))

    // The dialog closes and the service's own sentence takes its place. It is
    // written from what the service returned rather than from what the client
    // hoped for, which is the only reason it can be trusted.
    //
    // Twice on purpose: once where the reader can see it, and once in the panel's
    // polite live region, because the dialog closing is otherwise the only thing
    // a screen-reader user is told about.
    const shown = await screen.findAllByText("Created the project 'HelloWorld'.")
    expect(shown).toHaveLength(2)
    expect(shown.some((node) => node.closest('[aria-live="polite"]'))).toBe(true)
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    expect(bodiesFor(fetchMock, '/action/confirm')).toEqual([
      {
        kind: 'create_project',
        intent: 'project_manage',
        payload: CREATE_PROJECT.proposal?.payload,
      },
    ])
    // Without this the new project exists in the database and nowhere on screen
    // until the reader reloads.
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['work'] })
    // Routing still answers its own question afterwards.
    expect(screen.getByRole('button', { name: /go to projects/i })).toBeInTheDocument()
  })

  it('cancels without a request and leaves the routing decision standing', async () => {
    const fetchMock = stubEndpoints({
      route: () => jsonResponse(PROJECTS),
      propose: () => jsonResponse(CREATE_PROJECT),
    })
    renderAssistant()
    const user = await ask('add a project called HelloWorld')
    await screen.findByRole('dialog')
    const before = fetchMock.mock.calls.length

    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(fetchMock.mock.calls).toHaveLength(before)
    expect(bodiesFor(fetchMock, '/action/confirm')).toHaveLength(0)
    // "Go to Projects" is a different question from "create a project", and it
    // survives a proposal the reader declined.
    expect(screen.getByRole('button', { name: /go to projects/i })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('treats a refusal as nothing to do — no dialog, no alert, nothing disabled', async () => {
    const fetchMock = stubEndpoints({
      route: () => jsonResponse(PROJECTS),
      propose: () => jsonResponse(REFUSED),
    })
    renderAssistant()
    const user = await ask('add a task called hello there')

    // The routing decision is the settle point; the refusal lands after it, so
    // the assertions below are made once the proposal request has been made.
    await screen.findByText('add a task called hello there')
    await waitFor(() => expect(bodiesFor(fetchMock, '/action/propose')).toHaveLength(1))

    // `proposed: false` is a 200 and the ordinary answer. A panel that reddened
    // itself here would be crying wolf on nearly every turn, because most things
    // a person says to an assistant are not a row to create.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByText(/NEXUS could not/i)).not.toBeInTheDocument()
    // The panel is not left mid-flight with its controls switched off. `Classify`
    // is only ever enabled with a draft in the field, so the field is the thing
    // that has to be live — and typing has to bring the button back.
    const field = screen.getByLabelText(/Or type your request/i)
    expect(field).toBeEnabled()
    await user.type(field, 'show my open tasks')
    expect(screen.getByRole('button', { name: 'Classify' })).toBeEnabled()
    // And the routing answer is untouched.
    expect(screen.getByRole('button', { name: /go to projects/i })).toBeInTheDocument()
  })

  it('explains a refused confirmation without closing the dialog or disabling it', async () => {
    stubEndpoints({
      route: () => jsonResponse(PROJECTS),
      propose: () => jsonResponse(CREATE_PROJECT),
      confirm: () =>
        new Response(
          JSON.stringify({
            error: {
              code: 'forbidden',
              message: 'You do not have permission to perform this action.',
              details: null,
              request_id: 'r1',
            },
          }),
          { status: 403, headers: { 'Content-Type': 'application/json' } },
        ),
    })
    renderAssistant()
    const user = await ask('add a project called HelloWorld')
    await screen.findByRole('dialog')

    await user.click(screen.getByRole('button', { name: 'Confirm' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/nothing was written/i)
    // Still the same proposal, still both answers. Closing here would take away
    // the reader's only way to try again.
    expect(screen.getByText("Create a project named 'HelloWorld'.")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled()
  })
})
