import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { getModule } from '@/features/modules/catalog'
import JarvisPage from '@/pages/jarvis-page'

/**
 * The Jarvis page renders the server's own answers: status, outcome, guards and
 * proposals. It must never present an unreachable model as a working assistant,
 * and a side effect must only run through an explicit Approve.
 */

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const STATUS_DOWN = {
  enabled: true,
  model: { available: false, reason: 'cannot reach http://127.0.0.1:8765 (ConnectError)' },
  expected_adapter_sha256: 'd754a3c6',
  qualification: 'development candidate - NOT acceptance-qualified',
  tools: [
    {
      name: 'app_list_tasks',
      side_effect: 'read',
      requires_confirmation: false,
      available: true,
      reason: null,
    },
    {
      name: 'web_search',
      side_effect: 'read',
      requires_confirmation: false,
      available: false,
      reason: 'no search provider is configured (JARVIS_WEB_SEARCH_URL)',
    },
  ],
}

const STATUS_UP = {
  ...STATUS_DOWN,
  model: { available: true, model_id: 'Qwen/Qwen3-8B', adapter_sha256: 'd754a3c6b77f' },
}

function turn(over: Record<string, unknown>) {
  return {
    outcome: 'response',
    text: '',
    trace: [],
    proposal_id: null,
    proposal: null,
    guards: { honesty: 'pass', crisis: 'inactive' },
    steps: 1,
    model: { adapter_sha256: 'd754a3c6', qualification: 'dev' },
    nexus_classification: null,
    ...over,
  }
}

function stub(routes: Record<string, () => Response>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const handler = Object.entries(routes).find(([k]) => url.includes(k))?.[1]
    return handler ? handler() : json({}, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function bodies(fetchMock: ReturnType<typeof vi.fn>, path: string): unknown[] {
  return fetchMock.mock.calls
    .filter((call) => String(call[0]).includes(path))
    .map((call) => JSON.parse(String((call[1] as RequestInit).body)))
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/nexo']}>
        <JarvisPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('JarvisPage', () => {
  it('is a registered module in the Platform group', () => {
    expect(getModule('/nexo').label).toBe('NEXO')
  })

  it('says plainly when the model server is unreachable and lists blocked tools', async () => {
    stub({ '/jarvis/status': () => json(STATUS_DOWN) })
    renderPage()

    expect(await screen.findByText('Model not available')).toBeInTheDocument()
    expect(screen.getByText(/cannot reach http:\/\/127.0.0.1:8765/)).toBeInTheDocument()
    expect(screen.getByText(/1 run directly, 0 ask before running, 1/)).toBeInTheDocument()
    expect(screen.getByText(/no search provider is configured/)).toBeInTheDocument()
    expect(screen.getByText(STATUS_DOWN.qualification)).toBeInTheDocument()
    expect(screen.getByText(/Development candidate — not acceptance-qualified/)).toBeInTheDocument()
  })

  it('shows the server error instead of an answer when chat is refused', async () => {
    stub({
      '/jarvis/status': () => json(STATUS_UP),
      '/jarvis/chat': () =>
        json({ error: { code: 'jarvis_unavailable', message: 'Jarvis is not available.' } }, 503),
    })
    renderPage()
    const user = userEvent.setup()

    await screen.findByText('Model connected')
    await user.type(screen.getByLabelText('Message NEXO'), 'hello')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText(/Jarvis is not available/)).toBeInTheDocument()
  })

  it('renders when scrollIntoView returns a Promise, as newer Chrome does', async () => {
    // A concise-arrow effect returned that Promise and React crashed with "destroy is not a function".
    const scroll = vi.fn(() => Promise.resolve())
    Element.prototype.scrollIntoView = scroll as unknown as Element['scrollIntoView']
    stub({ '/jarvis/status': () => json(STATUS_UP) })
    try {
      const { unmount } = renderPage()
      expect(await screen.findByText('Model connected')).toBeInTheDocument()
      expect(scroll).toHaveBeenCalled()
      unmount() // runs effect cleanups: the crash happened here and on re-render
    } finally {
      delete (Element.prototype as Partial<Element>).scrollIntoView
    }
  })

  it('does not offer Send while the model is known to be off, and says how to start it', async () => {
    const fetchMock = stub({ '/jarvis/status': () => json(STATUS_DOWN) })
    renderPage()
    const user = userEvent.setup()

    await screen.findByText('Model not available')
    const box = screen.getByLabelText('Message NEXO')
    expect(box).toHaveAttribute('placeholder', 'Turn NEXO on above to start chatting')
    await user.type(box, 'hello{Enter}')
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    expect(bodies(fetchMock, '/jarvis/chat')).toHaveLength(0)
  })

  it('does not send when Enter only confirms an IME composition', async () => {
    const fetchMock = stub({ '/jarvis/status': () => json(STATUS_UP) })
    renderPage()
    await screen.findByText('Model connected')
    const box = screen.getByLabelText('Message NEXO')
    fireEvent.change(box, { target: { value: '你好' } })
    fireEvent.keyDown(box, { key: 'Enter', isComposing: true })
    expect(bodies(fetchMock, '/jarvis/chat')).toHaveLength(0)
    expect(box).toHaveValue('你好')
  })

  it('sends the conversation and renders the outcome, trace and guard result', async () => {
    const fetchMock = stub({
      '/jarvis/status': () => json(STATUS_UP),
      '/jarvis/chat': () =>
        json(
          turn({
            outcome: 'tool_completed',
            text: 'You have one open task.',
            trace: [
              {
                tool: 'app_list_tasks',
                arguments: {},
                status: 'executed_ok',
                result: { ok: true },
              },
            ],
            guards: { honesty: 'pass', crisis: 'replaced' },
          }),
        ),
    })
    renderPage()
    const user = userEvent.setup()

    expect(await screen.findByText('Model connected')).toBeInTheDocument()
    await user.type(screen.getByLabelText('Message NEXO'), 'What tasks do I have?')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText('You have one open task.')).toBeInTheDocument()
    expect(screen.getByText('tool_completed')).toBeInTheDocument()
    expect(screen.getByText(/safety guard replaced/)).toBeInTheDocument()
    expect(bodies(fetchMock, '/jarvis/chat')[0]).toMatchObject({
      messages: [{ role: 'user', content: 'What tasks do I have?' }],
    })
  })

  it('runs a proposal only after Approve, once', async () => {
    const fetchMock = stub({
      '/jarvis/status': () => json(STATUS_UP),
      '/jarvis/chat': () =>
        json(
          turn({
            outcome: 'confirmation_required',
            text: 'Nothing has been done yet; please approve or decline.',
            proposal_id: 'a'.repeat(32),
            proposal: [{ name: 'fs_write_file', arguments: { path: 'a.txt', content: 'x' } }],
          }),
        ),
      '/jarvis/confirm': () => json(turn({ outcome: 'tool_completed', text: 'Saved a.txt.' })),
    })
    renderPage()
    const user = userEvent.setup()

    await user.type(screen.getByLabelText('Message NEXO'), 'save a.txt')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText(/NEXO wants to run/)).toBeInTheDocument()
    expect(bodies(fetchMock, '/jarvis/confirm')).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: 'Approve' }))
    expect(await screen.findByText('Saved a.txt.')).toBeInTheDocument()
    await waitFor(() =>
      expect(bodies(fetchMock, '/jarvis/confirm')).toEqual([
        expect.objectContaining({ proposal_id: 'a'.repeat(32), approve: true }),
      ]),
    )
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })
})

/** jsdom has no speech recognition; this fake fires the browser events the recogniser wrapper listens to. */
class FakeRecognition {
  static last: FakeRecognition | null = null
  lang = ''
  continuous = false
  interimResults = false
  maxAlternatives = 0
  onresult: ((ev: unknown) => void) | null = null
  onerror: ((ev: unknown) => void) | null = null
  onend: ((ev: unknown) => void) | null = null
  started = 0
  constructor() {
    FakeRecognition.last = this
  }
  start() {
    this.started += 1
  }
  stop() {
    this.onend?.(new Event('end'))
  }
  abort() {
    this.onend?.(new Event('end'))
  }
  say(transcript: string) {
    this.onresult?.({
      resultIndex: 0,
      results: { length: 1, 0: { isFinal: true, 0: { transcript } } },
    })
    this.onend?.(new Event('end'))
  }
  fail(error: string) {
    this.onerror?.({ error, message: error })
    this.onend?.(new Event('end'))
  }
}

const PROPOSAL = turn({
  outcome: 'confirmation_required',
  text: 'Nothing has been done yet; please approve or decline.',
  proposal_id: 'b'.repeat(32),
  proposal: [{ name: 'fs_delete_file', arguments: { path: 'old.txt' } }],
})

describe('JarvisPage voice', () => {
  it('says voice is unsupported and keeps typing available when the browser has no recogniser', async () => {
    stub({ '/jarvis/status': () => json(STATUS_UP) })
    renderPage()

    expect(
      await screen.findByText(/Voice: Voice not supported in this browser/),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Talk to NEXO/ })).toBeDisabled()
    expect(screen.getByLabelText('Message NEXO')).toBeEnabled()
  })

  it('is disabled with a clear state while the model is unavailable', async () => {
    vi.stubGlobal('SpeechRecognition', FakeRecognition)
    stub({ '/jarvis/status': () => json(STATUS_DOWN) })
    renderPage()

    expect(await screen.findByText(/Voice: Off — turn the NEXO model on first/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Talk to NEXO/ })).toBeDisabled()
  })

  it('sends the finished utterance automatically through the same chat request as typing', async () => {
    vi.stubGlobal('SpeechRecognition', FakeRecognition)
    const fetchMock = stub({
      '/jarvis/status': () => json(STATUS_UP),
      '/jarvis/chat': () => json(turn({ text: 'Here you go.' })),
    })
    renderPage()
    const user = userEvent.setup()

    const talk = await screen.findByRole('button', { name: /Talk to NEXO/ })
    await waitFor(() => expect(talk).toBeEnabled())
    await user.click(talk)
    expect(FakeRecognition.last?.started).toBe(1)
    expect(screen.getByText(/Voice: Listening/)).toBeInTheDocument()
    expect(screen.getByText(/sent when you stop speaking/)).toBeInTheDocument()
    expect(bodies(fetchMock, '/jarvis/chat')).toHaveLength(0) // nothing sent before speech ends

    FakeRecognition.last?.say('what tasks do I have')
    expect(await screen.findByText('Here you go.')).toBeInTheDocument() // no Send press
    expect(bodies(fetchMock, '/jarvis/chat')).toEqual([
      expect.objectContaining({ messages: [{ role: 'user', content: 'what tasks do I have' }] }),
    ])
  })

  it('puts the transcript in the message box for review when automatic sending is off', async () => {
    vi.stubGlobal('SpeechRecognition', FakeRecognition)
    const fetchMock = stub({
      '/jarvis/status': () => json(STATUS_UP),
      '/jarvis/chat': () => json(turn({ text: 'Here you go.' })),
    })
    renderPage()
    const user = userEvent.setup()

    await user.click(await screen.findByLabelText(/Send automatically when I stop speaking/))
    const talk = screen.getByRole('button', { name: /Talk to NEXO/ })
    await waitFor(() => expect(talk).toBeEnabled())
    await user.click(talk)
    FakeRecognition.last?.say('what tasks do I have')
    await waitFor(() =>
      expect(screen.getByLabelText('Message NEXO')).toHaveValue('what tasks do I have'),
    )
    expect(bodies(fetchMock, '/jarvis/chat')).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText('Here you go.')).toBeInTheDocument()
  })

  it('speaks the actual reply after a voice request, and the speaker button mutes it', async () => {
    vi.stubGlobal('SpeechRecognition', FakeRecognition)
    const spoken: string[] = []
    vi.stubGlobal(
      'SpeechSynthesisUtterance',
      class {
        text: string
        onstart: (() => void) | null = null
        onend: (() => void) | null = null
        onerror: (() => void) | null = null
        constructor(text: string) {
          this.text = text
        }
      },
    )
    vi.stubGlobal('speechSynthesis', {
      speak: (u: { text: string }) => spoken.push(u.text),
      cancel: () => undefined,
      getVoices: () => [],
    })
    stub({
      '/jarvis/status': () => json(STATUS_UP),
      '/jarvis/chat': () => json(turn({ text: 'Here are some films.' })),
    })
    renderPage()
    const user = userEvent.setup()

    expect(await screen.findByRole('button', { name: /speaks replies: off/ })).toBeInTheDocument()
    const talk = screen.getByRole('button', { name: /Talk to NEXO/ })
    await waitFor(() => expect(talk).toBeEnabled())
    await user.click(talk)
    FakeRecognition.last?.say('find good movies')
    expect(await screen.findByText('Here are some films.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /speaks replies: on/ })).toBeInTheDocument()
    expect(spoken).toEqual(['Here are some films.']) // the server's reply text, nothing composed here

    await user.click(screen.getByRole('button', { name: /speaks replies: on/ }))
    await user.type(screen.getByLabelText('Message NEXO'), 'again')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(screen.getAllByText('Here are some films.')).toHaveLength(2))
    expect(spoken).toHaveLength(1) // muted: not spoken again
  })

  it('explains how to allow the microphone when permission is denied', async () => {
    vi.stubGlobal('SpeechRecognition', FakeRecognition)
    stub({ '/jarvis/status': () => json(STATUS_UP) })
    renderPage()
    const user = userEvent.setup()

    const talk = await screen.findByRole('button', { name: /Talk to NEXO/ })
    await waitFor(() => expect(talk).toBeEnabled())
    await user.click(talk)
    FakeRecognition.last?.fail('not-allowed')

    expect(await screen.findByText(/Voice: Microphone permission denied/)).toBeInTheDocument()
    expect(screen.getByText(/set Microphone to Allow/)).toBeInTheDocument()
  })
})

describe('JarvisPage model switch', () => {
  const lifecycle = (state: string) => ({
    state,
    error: null,
    on_demand: true,
    owned: state !== 'off',
    idle_unload_minutes: 10,
    idle_seconds: 0,
    last_unload: null,
  })

  it('shows the model is off and loads it only when the user turns it on', async () => {
    let state = 'off'
    const fetchMock = stub({
      '/jarvis/status': () =>
        json({
          ...(state === 'ready' ? STATUS_UP : STATUS_DOWN),
          lifecycle: lifecycle(state),
        }),
      '/jarvis/model/load': () => {
        state = 'ready'
        return json(lifecycle('loading'))
      },
    })
    renderPage()
    const user = userEvent.setup()

    expect(await screen.findByText('NEXO model is off')).toBeInTheDocument()
    expect(screen.getByText(/switches itself off after 10 minutes/)).toBeInTheDocument()
    expect(bodies(fetchMock, '/jarvis/model/load')).toHaveLength(0) // never loaded by itself

    await user.click(screen.getByRole('button', { name: 'Turn NEXO on' }))
    expect(await screen.findByText('NEXO model is ready')).toBeInTheDocument()
    expect(fetchMock.mock.calls.filter((c) => String(c[0]).includes('/jarvis/model/load'))).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Turn NEXO off' })).toBeInTheDocument()
  })

  it('turns the model off through the unload endpoint', async () => {
    let state = 'ready'
    const fetchMock = stub({
      '/jarvis/status': () =>
        json({ ...(state === 'ready' ? STATUS_UP : STATUS_DOWN), lifecycle: lifecycle(state) }),
      '/jarvis/model/unload': () => {
        state = 'off'
        return json(lifecycle('off'))
      },
    })
    renderPage()
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Turn NEXO off' }))
    expect(await screen.findByText('NEXO model is off')).toBeInTheDocument()
    expect(fetchMock.mock.calls.filter((c) => String(c[0]).includes('/jarvis/model/unload'))).toHaveLength(1)
    expect(screen.getByRole('button', { name: /Talk to NEXO/ })).toBeDisabled()
  })
})

describe('JarvisPage approvals', () => {
  it('explains what approval means and shows the target and reason on the card', async () => {
    stub({ '/jarvis/status': () => json(STATUS_UP), '/jarvis/chat': () => json(PROPOSAL) })
    renderPage()
    const user = userEvent.setup()

    expect(
      await screen.findByText(/Ordinary conversation never needs approval/),
    ).toBeInTheDocument()
    await user.type(screen.getByLabelText('Message NEXO'), 'delete old.txt')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText(/Permanently delete file/)).toBeInTheDocument()
    expect(screen.getByText('old.txt')).toBeInTheDocument()
  })

  it('warns on the card about a value that is not in the request', async () => {
    stub({
      '/jarvis/status': () => json(STATUS_UP),
      '/jarvis/chat': () =>
        json({
          ...PROPOSAL,
          proposal: [
            { name: 'app_create_task', arguments: { title: 'NEXO 7<PASSWORD>' }, unverified: ['title'] },
          ],
        }),
    })
    renderPage()
    const user = userEvent.setup()

    await screen.findByText('Model connected')
    await user.type(screen.getByLabelText('Message NEXO'), 'add NEXO 7a3f9c')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText(/Check title: not in your words/)).toBeInTheDocument()
  })

  it('declining sends approve=false once and shows that nothing ran', async () => {
    const fetchMock = stub({
      '/jarvis/status': () => json(STATUS_UP),
      '/jarvis/chat': () => json(PROPOSAL),
      '/jarvis/confirm': () => json(turn({ outcome: 'denied_by_user', text: 'Okay, I left it.' })),
    })
    renderPage()
    const user = userEvent.setup()

    await user.type(screen.getByLabelText('Message NEXO'), 'delete old.txt')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await user.click(await screen.findByRole('button', { name: 'Decline' }))

    expect(await screen.findByText(/Declined — nothing was run/)).toBeInTheDocument()
    expect(bodies(fetchMock, '/jarvis/confirm')).toEqual([
      expect.objectContaining({ proposal_id: 'b'.repeat(32), approve: false }),
    ])
  })

  it('reports an expired or lost approval as such, with nothing run', async () => {
    stub({
      '/jarvis/status': () => json(STATUS_UP),
      '/jarvis/chat': () => json(PROPOSAL),
      '/jarvis/confirm': () =>
        json({ error: { code: 'not_found', message: 'No pending approval' } }, 404),
    })
    renderPage()
    const user = userEvent.setup()

    await user.type(screen.getByLabelText('Message NEXO'), 'delete old.txt')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await user.click(await screen.findByRole('button', { name: 'Approve' }))

    expect(
      await screen.findByText(/expired, was already used, or the server restarted/),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })

  it('never says an approval ran or did not run when no answer came back', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/jarvis/status')) return json(STATUS_UP)
      if (url.includes('/jarvis/chat')) return json(PROPOSAL)
      throw new TypeError('Failed to fetch') // the connection dropped during the approval
    })
    vi.stubGlobal('fetch', fetchMock)
    renderPage()
    const user = userEvent.setup()

    await user.type(screen.getByLabelText('Message NEXO'), 'delete old.txt')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await user.click(await screen.findByRole('button', { name: 'Approve' }))

    expect(await screen.findByText(/not known whether the action ran/)).toBeInTheDocument()
    expect(screen.queryByText(/Approved — the result is below/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })

  it('keeps the card approvable when the server refused before taking the proposal (503 busy)', async () => {
    const fetchMock = stub({
      '/jarvis/status': () => json(STATUS_UP),
      '/jarvis/chat': () => json(PROPOSAL),
      '/jarvis/confirm': () =>
        json({ error: { code: 'jarvis_unavailable', message: 'NEXO is loading.' } }, 503),
    })
    renderPage()
    const user = userEvent.setup()

    await user.type(screen.getByLabelText('Message NEXO'), 'delete old.txt')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await user.click(await screen.findByRole('button', { name: 'Approve' }))

    expect(await screen.findByText(/NEXO is loading/)).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Approve' })).toBeEnabled()
    expect(bodies(fetchMock, '/jarvis/confirm')).toHaveLength(1)
  })

  it('sends one confirmation for a double click on Approve', async () => {
    let release: (r: Response) => void = () => undefined
    const fetchMock = stub({
      '/jarvis/status': () => json(STATUS_UP),
      '/jarvis/chat': () => json(PROPOSAL),
    })
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/jarvis/status')) return json(STATUS_UP)
      if (url.includes('/jarvis/chat')) return json(PROPOSAL)
      return new Promise<Response>((resolve) => (release = resolve))
    })
    renderPage()
    const user = userEvent.setup()

    await user.type(screen.getByLabelText('Message NEXO'), 'delete old.txt')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    const approve = await screen.findByRole('button', { name: 'Approve' })
    approve.click()
    approve.click() // same tick: React has not re-rendered the disabled button yet
    release(json(turn({ outcome: 'tool_completed', text: 'Deleted old.txt.' })))

    expect(await screen.findByText('Deleted old.txt.')).toBeInTheDocument()
    expect(bodies(fetchMock, '/jarvis/confirm')).toHaveLength(1)
    expect(screen.getByText(/Approved — the result is below/)).toBeInTheDocument()
  })
})

describe('JarvisPage search and speech failures', () => {
  it('lists the real search results and says what was searched', async () => {
    stub({
      '/jarvis/status': () => json(STATUS_UP),
      '/jarvis/chat': () =>
        json(
          turn({
            outcome: 'tool_completed',
            text: 'Citizen Kane is often ranked first.',
            trace: [
              {
                tool: 'web_search',
                arguments: { query: 'good movies' },
                status: 'executed_ok',
                result: {
                  ok: true,
                  data: {
                    provider: 'wikipedia',
                    coverage: 'Wikipedia articles only, not the general web',
                    results: [
                      { title: 'Citizen Kane', url: 'https://en.wikipedia.org/wiki/Citizen_Kane' },
                      { title: 'Bad', url: 'javascript:alert(1)' },
                    ],
                  },
                },
              },
            ],
          }),
        ),
    })
    renderPage()
    const user = userEvent.setup()

    await user.type(screen.getByLabelText('Message NEXO'), 'search good movies')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText(/Wikipedia articles only, not the general web/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Citizen Kane' })).toHaveAttribute(
      'href',
      'https://en.wikipedia.org/wiki/Citizen_Kane',
    )
    expect(screen.queryByRole('link', { name: 'Bad' })).not.toBeInTheDocument()
  })

  it('says so when a spoken reply fails, and keeps the text', async () => {
    vi.stubGlobal(
      'SpeechSynthesisUtterance',
      class {
        constructor(public text: string) {}
      },
    )
    vi.stubGlobal('speechSynthesis', {
      speak: () => {
        throw new Error('audio device busy')
      },
      cancel: () => undefined,
      getVoices: () => [],
    })
    stub({
      '/jarvis/status': () => json(STATUS_UP),
      '/jarvis/chat': () => json(turn({ text: 'Hello there.' })),
    })
    renderPage()
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: /speaks replies: off/ }))
    await user.type(screen.getByLabelText('Message NEXO'), 'hi')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText('Hello there.')).toBeInTheDocument()
    expect(await screen.findByText(/could not read that reply aloud/)).toBeInTheDocument()
  })
})
