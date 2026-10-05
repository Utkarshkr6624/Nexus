import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  MAX_CONVERSATION_TURNS,
  type VoiceError,
  type VoiceState,
} from '@/features/assistant/types'
import { resetAssistantStore, useAssistantStore } from '@/features/assistant/assistant-store'
import {
  describeActionFailure,
  describeRoutingFailure,
  useVoiceAssistant,
  type UseVoiceAssistantOptions,
  type VoiceAssistant,
} from '@/features/assistant/use-voice-assistant'
import { workKeys } from '@/features/work/hooks'
import { ApiError } from '@/lib/api-client'
import type { ConfirmActionRead, ProposeActionRead, RoutingDecisionRead } from '@/types/ml'

/**
 * jsdom implements no speech recognition and no speech synthesis, and the
 * backend is the only thing that can classify anything, so every test here
 * installs three fakes: a recogniser, a voice, and `fetch`.
 *
 * The fakes are literal on purpose. The hook's whole job is the state machine
 * between three unreliable parties, so a fake that "helpfully" behaved like the
 * real thing would hide exactly the edges these tests exist to pin.
 *
 * **A turn now makes two requests.** `POST /ml/route` decides where the
 * utterance goes, and — for an accepted turn — `POST /ml/action/propose` asks
 * what NEXUS could do about it. The two have different response shapes, so
 * `installEndpoints` below answers them separately; a stub that handed a
 * routing decision to the proposal endpoint would be answering a question with a
 * body the endpoint never sends. The single `respond` lever is kept for the
 * tests that only care about the turn, where the routing decision doubles as the
 * proposal answer — it carries no `proposed` flag, which the hook reads as a
 * refusal, which is the silent path.
 */

class FakeSpeechRecognition {
  static instances: FakeSpeechRecognition[] = []

  lang = ''
  continuous = false
  interimResults = false
  maxAlternatives = 0

  onstart: ((this: SpeechRecognition, ev: Event) => unknown) | null = null
  onend: ((this: SpeechRecognition, ev: Event) => unknown) | null = null
  onresult: ((this: SpeechRecognition, ev: SpeechRecognitionEvent) => unknown) | null = null
  onerror: ((this: SpeechRecognition, ev: SpeechRecognitionErrorEvent) => unknown) | null = null

  startCalls = 0
  stopCalls = 0
  abortCalls = 0

  constructor() {
    FakeSpeechRecognition.instances.push(this)
  }

  start(): void {
    this.startCalls += 1
  }

  stop(): void {
    this.stopCalls += 1
  }

  abort(): void {
    this.abortCalls += 1
  }

  emit(
    entries: ReadonlyArray<{ transcript: string; isFinal: boolean }>,
  ): void {
    this.onresult?.call(this as unknown as SpeechRecognition, resultEvent(entries))
  }

  emitError(error: SpeechRecognitionErrorCode): void {
    this.onerror?.call(this as unknown as SpeechRecognition, {
      error,
      message: `raw browser message for ${error}`,
    } as unknown as SpeechRecognitionErrorEvent)
  }

  emitEnd(): void {
    this.onend?.call(this as unknown as SpeechRecognition, new Event('end'))
  }
}

function resultEvent(
  entries: ReadonlyArray<{ transcript: string; isFinal: boolean }>,
): SpeechRecognitionEvent {
  const results = entries.map((entry) => {
    const alternative: SpeechRecognitionAlternative = {
      transcript: entry.transcript,
      confidence: 0.93,
    }
    return {
      isFinal: entry.isFinal,
      length: 1,
      item: () => alternative,
      0: alternative,
      [Symbol.iterator]: function* () {
        yield alternative
      },
    } as unknown as SpeechRecognitionResult
  })
  const indexed: Record<number, SpeechRecognitionResult> = {}
  results.forEach((result, index) => {
    indexed[index] = result
  })
  const list = {
    length: results.length,
    item: (index: number) => results[index] as SpeechRecognitionResult,
    ...indexed,
  } as unknown as SpeechRecognitionResultList
  return { resultIndex: 0, results: list } as unknown as SpeechRecognitionEvent
}

class FakeUtterance {
  text: string
  lang = ''
  rate = 1
  pitch = 1
  voice: SpeechSynthesisVoice | null = null
  onstart: (() => void) | null = null
  onend: (() => void) | null = null
  onerror: ((event: { error: string }) => void) | null = null

  constructor(text: string) {
    this.text = text
  }
}

const speakSpy = vi.fn()
const cancelSpeechSpy = vi.fn()

function installBrowserApis(): void {
  FakeSpeechRecognition.instances = []
  vi.stubGlobal('SpeechRecognition', FakeSpeechRecognition)
  vi.stubGlobal('webkitSpeechRecognition', undefined)
  vi.stubGlobal('SpeechSynthesisUtterance', FakeUtterance)
  vi.stubGlobal('speechSynthesis', {
    speak: speakSpy,
    cancel: cancelSpeechSpy,
    getVoices: () => [],
  })
}

function removeRecognition(): void {
  vi.stubGlobal('SpeechRecognition', undefined)
  vi.stubGlobal('webkitSpeechRecognition', undefined)
}

function removeSynthesis(): void {
  vi.stubGlobal('speechSynthesis', undefined)
}

function latestRecogniser(): FakeSpeechRecognition {
  const instance = FakeSpeechRecognition.instances.at(-1)
  if (instance === undefined) throw new Error('no recogniser was created')
  return instance
}

function spokenText(): string[] {
  return speakSpy.mock.calls.map((call) => (call[0] as FakeUtterance).text)
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function errorEnvelope(code: string, message: string, details: unknown = null, status = 422): Response {
  return json({ error: { code, message, details, request_id: 'r1' } }, status)
}

const ACCEPTED: RoutingDecisionRead = {
  intent: 'task_manage',
  confidence: 0.97,
  threshold: 0.6,
  status: 'accepted',
  destination: 'api/v1/tasks',
  destination_kind: 'router',
  target: {
    service: 'TaskService',
    module: 'app.services.task_service',
    entrypoint: 'TaskService.list',
  },
  reason: 'confidence above threshold',
  alternatives: [{ intent: 'project_manage', confidence: 0.09 }],
}

const UNCERTAIN: RoutingDecisionRead = {
  intent: 'analytics_insight',
  confidence: 0.41,
  threshold: 0.6,
  status: 'uncertain',
  destination: 'uncertain',
  destination_kind: 'router',
  target: null,
  reason: 'confidence below threshold',
  alternatives: [],
}

const GENERATION_UNAVAILABLE: RoutingDecisionRead = {
  intent: 'code_assist',
  confidence: 0.93,
  threshold: 0.6,
  status: 'generation_unavailable',
  destination: 'large-model:unavailable',
  destination_kind: 'large_model',
  target: null,
  reason: 'requires free-form generation',
  alternatives: [],
}

const OUT_OF_SCOPE: RoutingDecisionRead = {
  intent: 'out_of_scope',
  confidence: 0.88,
  threshold: 0.6,
  status: 'out_of_scope',
  destination: 'abstain',
  destination_kind: 'fallback',
  target: null,
  reason: 'no NEXUS surface matches',
  alternatives: [],
}

/** The routing answer for the sentence the proposal fixtures below belong to. */
const ACCEPTED_PROJECT: RoutingDecisionRead = {
  intent: 'project_manage',
  confidence: 0.98,
  threshold: 0.6,
  status: 'accepted',
  destination: 'api/v1/projects',
  destination_kind: 'router',
  target: {
    service: 'ProjectService',
    module: 'app.services.project_service',
    entrypoint: 'ProjectService.list',
  },
  reason: 'confidence above threshold',
  alternatives: [],
}

/** Shaped exactly as the live endpoint answers a creatable utterance. */
const CREATE_PROJECT_PROPOSAL: ProposeActionRead = {
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
    payload: {
      name: 'HelloWorld',
      description: null,
      priority: 'medium',
      start_date: null,
      target_date: null,
    },
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
    notes: [],
  },
  refusal: null,
}

/** A refusal is a 200. This is the shape most utterances produce. */
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

const CREATED_PROJECT: ConfirmActionRead = {
  kind: 'create_project',
  entity: 'project',
  entity_id: 'a9e9ce1c-0000-4000-8000-000000000000',
  outcome: 'created',
  applied: true,
  message: "Created the project 'HelloWorld'.",
}

/** The replay: the row is already there, so nothing was written. */
const PROJECT_ALREADY_THERE: ConfirmActionRead = {
  kind: 'create_project',
  entity: 'project',
  entity_id: 'a9e9ce1c-0000-4000-8000-000000000000',
  outcome: 'no_op',
  applied: false,
  message: "A project named 'HelloWorld' already exists; NEXO did not create a second one.",
}

const ROUTE_PATH = '/ml/route'
const PROPOSE_PATH = '/ml/action/propose'
const CONFIRM_PATH = '/ml/action/confirm'

interface FetchCall {
  url: string
  body: unknown
  signal: AbortSignal | null
}

let calls: FetchCall[] = []
let respond: (call: FetchCall) => Response | Promise<Response> = () => json(ACCEPTED)

function installFetch(handler?: (call: FetchCall) => Response | Promise<Response>): void {
  calls = []
  respond = handler ?? (() => json(ACCEPTED))
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const call: FetchCall = {
        url: String(input),
        body: typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : null,
        signal: init?.signal ?? null,
      }
      calls.push(call)
      return respond(call)
    }),
  )
}

/**
 * Answers each `/ml` endpoint with the body it actually receives in life.
 *
 * The proposal endpoint's shape is not the router's, and a stub that answered
 * both with one fixture would be describing a backend that does not exist. The
 * first matching path wins; anything unnamed falls through to `fallback`, so a
 * test can pin one endpoint and leave the other on the ordinary routing answer.
 */
function installEndpoints(
  endpoints: Record<string, () => Response>,
  fallback: () => Response = () => json(ACCEPTED),
): void {
  installFetch((call) => {
    for (const [path, answer] of Object.entries(endpoints)) {
      if (call.url.includes(path)) return answer()
    }
    return fallback()
  })
}

/**
 * Only the calls to one endpoint.
 *
 * A turn sends the utterance twice — once to be routed, once to be proposed an
 * action for — so a bare `calls.length` no longer says "one request per turn".
 * Counting one path is what each of these assertions is actually about, and it
 * stays true when the second half of the flow changes again.
 */
function callsTo(path: string): FetchCall[] {
  return calls.filter((call) => call.url.includes(path))
}

function routeCalls(): FetchCall[] {
  return callsTo(ROUTE_PATH)
}

interface Rendered {
  result: { current: VoiceAssistant }
  client: QueryClient
  unmount: () => void
}

function renderAssistant(
  path = '/tasks',
  options: UseVoiceAssistantOptions = {},
): Rendered {
  // A fresh client per render: the shared one would carry cache and mutation
  // state between tests, and a `retry` inherited from it would change what a
  // 503 does.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  // StrictMode, because `src/main.tsx` renders the app inside one. It mounts,
  // runs every effect's cleanup and runs the effect again, which is the only
  // way a test can catch an effect that tears something down without ever
  // re-arming it.
  //
  // It has to be `reactStrictMode`, not a `<StrictMode>` element in `wrapper`:
  // only the render option puts the boundary where React's double-invoke of
  // effects actually reaches this hook. With the element in the wrapper the
  // effects are *not* re-run, so the regression this guards was invisible — a
  // StrictMode test that never double-runs is decoration, not coverage.
  const rendered = renderHook(() => useVoiceAssistant(options), {
    reactStrictMode: true,
    wrapper: ({ children }: { children: ReactNode }) =>
      createElement(
        QueryClientProvider,
        { client },
        createElement(MemoryRouter, { initialEntries: [path] }, children),
      ),
  })
  return { result: rendered.result, client, unmount: rendered.unmount }
}

/**
 * Runs one typed turn end to end: submits it, waits for the backend answer to
 * land, and lets the read-aloud finish so the assistant is back at `idle` and
 * the next turn can begin.
 */
async function submit(
  result: { current: VoiceAssistant },
  text: string,
  decision: RoutingDecisionRead = ACCEPTED,
): Promise<void> {
  const before = useAssistantStore.getState().turns.length
  respond = () => json(decision)
  await act(async () => {
    result.current.submitTranscript(text)
    await Promise.resolve()
  })
  await waitFor(() => expect(useAssistantStore.getState().turns.length).toBe(before + 1))
  await settleSpeech(result)
}

/**
 * Submits a turn and leaves the assistant mid-reply.
 *
 * Used by the tests about what may *not* be started while a turn is still
 * moving: settling the speech first would put the assistant back at `idle`,
 * which is the one state where everything is allowed.
 */
async function submitWithoutSettling(result: { current: VoiceAssistant }): Promise<void> {
  const before = useAssistantStore.getState().turns.length
  respond = () => json(ACCEPTED)
  await act(async () => {
    result.current.submitTranscript('show my tasks')
    await Promise.resolve()
  })
  await waitFor(() => expect(result.current.state).toBe('speaking'))
  expect(useAssistantStore.getState().turns.length).toBe(before + 1)
}

/** Ends whatever `speechSynthesis` was given, which is what returns to `idle`. */
async function settleSpeech(result: { current: VoiceAssistant }): Promise<void> {
  if (result.current.state !== 'speaking') return
  await act(async () => {
    const utterance = speakSpy.mock.calls.at(-1)?.[0]
    if (utterance instanceof FakeUtterance) utterance.onend?.()
  })
  await waitFor(() => expect(result.current.state).toBe('idle'))
}

/** Submits a turn that is expected to fail, and waits for the error to surface. */
async function submitExpectingError(
  result: { current: VoiceAssistant },
  text: string,
  handler: (call: FetchCall) => Response | Promise<Response>,
): Promise<void> {
  respond = handler
  await act(async () => {
    result.current.submitTranscript(text)
    await Promise.resolve()
  })
  await waitFor(() => expect(result.current.state).toBe('error'))
}

beforeEach(() => {
  installBrowserApis()
  installFetch()
  resetAssistantStore()
  speakSpy.mockClear()
  cancelSpeechSpy.mockClear()
})

describe('feature detection', () => {
  it('starts idle and willing to listen when the browser can listen', () => {
    const { result } = renderAssistant()
    expect(result.current.state).toBe('idle')
    expect(result.current.supportsRecognition).toBe(true)
    expect(result.current.canListen).toBe(true)
  })

  it('is permanently unsupported without a recogniser, and never leaves that state', async () => {
    removeRecognition()
    const { result } = renderAssistant()

    expect(result.current.state).toBe('unsupported')
    expect(result.current.canListen).toBe(false)

    act(() => {
      result.current.startListening()
      result.current.stopListening()
      result.current.retry()
      result.current.cancel()
    })

    expect(result.current.state).toBe('unsupported')
    expect(FakeSpeechRecognition.instances).toHaveLength(0)
  })

  it('still classifies typed input where there is no recogniser at all', async () => {
    removeRecognition()
    const { result } = renderAssistant('/tasks')

    await submit(result, 'show my tasks')

    // `unsupported` is terminal — it describes the microphone, not the
    // classifier — and the text field is the only way in on such a browser, so
    // it must keep working while the state stays where it is.
    expect(result.current.state).toBe('unsupported')
    expect(routeCalls()).toHaveLength(1)
    expect(result.current.lastDecision?.status).toBe('accepted')
    expect(result.current.turns).toHaveLength(1)
  })
})

describe('the happy path', () => {
  it('runs idle → listening → processing → speaking → idle on one voice turn', async () => {
    const { result } = renderAssistant()
    const seen: VoiceState[] = [result.current.state]

    act(() => {
      result.current.startListening()
    })
    seen.push(result.current.state)

    const recogniser = latestRecogniser()
    respond = () => json(ACCEPTED)
    act(() => {
      recogniser.emit([{ transcript: 'show my open tasks', isFinal: true }])
    })
    await waitFor(() => expect(result.current.state).toBe('speaking'))
    seen.push(result.current.state)

    act(() => {
      const utterance = speakSpy.mock.calls.at(-1)?.[0] as FakeUtterance
      utterance.onend?.()
    })
    await waitFor(() => expect(result.current.state).toBe('idle'))
    seen.push(result.current.state)

    expect(seen).toEqual(['idle', 'listening', 'speaking', 'idle'])
  })

  it('shows interim text while listening and never sends it onward', async () => {
    const { result } = renderAssistant()

    act(() => {
      result.current.startListening()
    })
    act(() => {
      latestRecogniser().emit([{ transcript: 'show my ', isFinal: false }])
    })

    // Slice 1 trims before it reports, so the panel never shows a ragged edge.
    expect(result.current.interimTranscript).toBe('show my')

    respond = () => json(ACCEPTED)
    act(() => {
      latestRecogniser().emit([
        { transcript: 'show my ', isFinal: false },
        { transcript: 'show my open tasks', isFinal: true },
      ])
    })

    await waitFor(() => expect(routeCalls()).toHaveLength(1))
    expect(calls[0]?.body).toEqual({ text: 'show my open tasks' })
    expect(result.current.interimTranscript).toBe('')
  })

  it('names the service and destination rather than writing a reply', async () => {
    const { result } = renderAssistant()
    await submit(result, 'show my tasks')

    await waitFor(() => expect(result.current.lastDecision).not.toBeNull())
    expect(result.current.lastAction).toEqual({
      service: 'TaskService',
      entrypoint: 'TaskService.list',
      destination: 'api/v1/tasks',
    })
    // The classifier routes; it does not compose prose. What is spoken has to
    // read as a routing statement or the UI is lying about what NEXO is.
    expect(spokenText()).toEqual(['Routing to Task.'])
  })

  it('records the turn with what was heard and what was decided', async () => {
    const { result } = renderAssistant()
    await submit(result, '  show my tasks  ')

    await waitFor(() => expect(result.current.turns).toHaveLength(1))
    const [turn] = result.current.turns
    expect(turn?.transcript).toBe('show my tasks')
    expect(turn?.decision?.intent).toBe('task_manage')
    expect(turn?.error).toBeNull()
    expect(result.current.lastTurn).toEqual(turn)
  })

  it('goes straight to idle when the browser cannot speak', async () => {
    removeSynthesis()
    const { result } = renderAssistant()

    await submit(result, 'show my tasks')

    await waitFor(() => expect(result.current.state).toBe('idle'))
    expect(result.current.supportsSynthesis).toBe(false)
    expect(speakSpy).not.toHaveBeenCalled()
  })

  it('stays silent when the user has turned speech off', async () => {
    const { result } = renderAssistant('/tasks', { speechEnabled: false })

    await submit(result, 'show my tasks')

    await waitFor(() => expect(result.current.state).toBe('idle'))
    expect(result.current.lastDecision?.status).toBe('accepted')
    expect(speakSpy).not.toHaveBeenCalled()
  })

  it('reports a recognised generation request as a capability gap, not a failure', async () => {
    const { result } = renderAssistant()

    await submit(result, 'write me a function that sorts a list', GENERATION_UNAVAILABLE)

    await waitFor(() => expect(result.current.lastDecision).not.toBeNull())
    expect(result.current.state).toBe('idle')
    expect(result.current.error).toBeNull()
    expect(result.current.lastAction).toBeNull()
    expect(result.current.suggestion).toBeNull()
    expect(spokenText()[0]).toContain('no generative model')
  })

  it('says plainly that an out-of-scope request has nowhere to go', async () => {
    const { result } = renderAssistant()

    await submit(result, 'what is the weather in Oslo', OUT_OF_SCOPE)

    await waitFor(() => expect(result.current.lastDecision).not.toBeNull())
    expect(result.current.error).toBeNull()
    expect(spokenText()).toEqual(['That is outside what NEXUS can route.'])
  })
})

/**
 * Regression guard for the mounted flag.
 *
 * The hook tells a late response not to touch a dead tree by consulting
 * `mountedRef`. That ref was armed by `useRef(true)` and disarmed by the single
 * effect's cleanup — but the effect never re-armed it, and React's StrictMode
 * runs mount → cleanup → effect on the first mount. Under `npm run dev` the flag
 * was therefore false for the whole life of the panel, which silently disabled
 * the three guards that depend on it: a successful turn was never recorded or
 * spoken, a failure was never surfaced, and a `speaking` phase could never end.
 *
 * Nothing below asserts on a ref. It asserts on the symptom — a whole turn,
 * end to end, under StrictMode — because the symptom is what the user sees and
 * what a future refactor could break in some other way.
 */
describe('under StrictMode', () => {
  it('completes a full turn: submitted, recorded, spoken, and back to idle', async () => {
    const { result } = renderAssistant('/tasks')

    // Nothing has been said yet, and the panel is willing to listen — the flag
    // must already be armed at the end of mount, not merely true by default.
    expect(result.current.state).toBe('idle')
    expect(result.current.canListen).toBe(true)

    await submit(result, 'show my open tasks')

    const [turn] = result.current.turns
    expect(turn?.transcript).toBe('show my open tasks')
    expect(turn?.decision?.intent).toBe('task_manage')
    expect(turn?.error).toBeNull()
    expect(spokenText()).toEqual(['Routing to Task.'])
    expect(result.current.state).toBe('idle')
  })

  it('does not strand the panel in processing, which would block every later turn', async () => {
    const { result } = renderAssistant('/tasks')

    await submit(result, 'show my open tasks')

    // The failure mode was the first utterance moving the machine to
    // `processing` and nothing ever moving it back, which left `canListen`
    // false and made every subsequent submission a no-op.
    expect(result.current.state).not.toBe('processing')
    expect(result.current.canListen).toBe(true)

    await submit(result, 'show my projects')

    expect(result.current.turns).toHaveLength(2)
    expect(result.current.state).toBe('idle')
  })

  it('surfaces a failure instead of leaving it unreported', async () => {
    const { result } = renderAssistant('/tasks')

    await submitExpectingError(result, 'show my open tasks', () =>
      errorEnvelope('ml_unavailable', 'the classifier is not loaded', null, 503),
    )

    expect(result.current.error?.code).toBe('classifier_unavailable')
    expect(result.current.turns).toHaveLength(1)
    expect(result.current.turns[0]?.error?.code).toBe('classifier_unavailable')
  })

  it('ends the read-aloud instead of waiting for an event that never arrives', async () => {
    const { result } = renderAssistant('/tasks')

    respond = () => json(ACCEPTED)
    await act(async () => {
      result.current.submitTranscript('show my open tasks')
      await Promise.resolve()
    })
    await waitFor(() => expect(result.current.state).toBe('speaking'))

    await settleSpeech(result)

    expect(result.current.state).toBe('idle')
    expect(result.current.canListen).toBe(true)
  })
})

describe('the state machine guards', () => {
  it('ignores a second start while already listening', () => {
    const { result } = renderAssistant()

    act(() => {
      result.current.startListening()
      result.current.startListening()
    })

    expect(FakeSpeechRecognition.instances).toHaveLength(1)
    expect(latestRecogniser().startCalls).toBe(1)
  })

  it('does not fire a request when start is pressed mid-processing', async () => {
    const { result } = renderAssistant()
    await submitWithoutSettling(result)

    act(() => {
      result.current.startListening()
    })

    expect(result.current.state).toBe('speaking')
    // One routing request for the turn already under way, and nothing for the
    // press that was correctly ignored.
    expect(routeCalls()).toHaveLength(1)
    expect(FakeSpeechRecognition.instances).toHaveLength(0)
  })

  it('ignores typed input while a turn is in flight', async () => {
    const { result } = renderAssistant()
    await submitWithoutSettling(result)

    await act(async () => {
      result.current.submitTranscript('and my projects')
      await Promise.resolve()
    })

    expect(routeCalls()).toHaveLength(1)
  })

  it('ignores blank typed input entirely', async () => {
    const { result } = renderAssistant()

    await act(async () => {
      result.current.submitTranscript('   \n  ')
      await Promise.resolve()
    })

    expect(calls).toHaveLength(0)
    expect(result.current.state).toBe('idle')
  })

  it('returns to idle when the user stops listening, without calling the backend', () => {
    const { result } = renderAssistant()

    act(() => {
      result.current.startListening()
    })
    const recogniser = latestRecogniser()
    act(() => {
      result.current.stopListening()
    })

    expect(result.current.state).toBe('idle')
    expect(recogniser.stopCalls).toBe(1)
    expect(calls).toHaveLength(0)
  })

  it('leaves listening when the browser ends the session with nothing heard', () => {
    const { result } = renderAssistant()

    act(() => {
      result.current.startListening()
    })
    act(() => {
      latestRecogniser().emitEnd()
    })

    expect(result.current.state).toBe('error')
    expect(result.current.error?.code).toBe('no_speech')
    expect(result.current.turns).toHaveLength(1)
    expect(result.current.turns[0]?.transcript).toBe('')
  })

  it('records silence as its own turn and offers no retry, because there is nothing to retry', () => {
    const { result } = renderAssistant()

    act(() => {
      result.current.startListening()
    })
    act(() => {
      latestRecogniser().emitError('no-speech')
      latestRecogniser().emitEnd()
    })

    expect(result.current.state).toBe('error')
    act(() => {
      result.current.retry()
    })
    expect(calls).toHaveLength(0)
  })

  it('surfaces a denied microphone with the recogniser’s own copy', () => {
    const { result } = renderAssistant()

    act(() => {
      result.current.startListening()
    })
    act(() => {
      latestRecogniser().emitError('not-allowed')
    })

    expect(result.current.state).toBe('error')
    expect(result.current.error?.code).toBe('permission_denied')
    expect(result.current.error?.retryable).toBe(true)
  })

  it('recovers from an error by listening again', () => {
    const { result } = renderAssistant()

    act(() => {
      result.current.startListening()
    })
    act(() => {
      latestRecogniser().emitError('not-allowed')
    })
    expect(result.current.canListen).toBe(true)

    act(() => {
      result.current.startListening()
    })

    expect(result.current.state).toBe('listening')
    expect(result.current.error).toBeNull()
  })
})

describe('the model sees one utterance and nothing else', () => {
  it('sends only the second turn’s text, never the transcript', async () => {
    const { result } = renderAssistant()

    await submit(result, 'log my hours against the atlas project')
    await waitFor(() => expect(result.current.turns).toHaveLength(1))
    await submit(result, 'show my overdue tasks')
    await waitFor(() => expect(result.current.turns).toHaveLength(2))

    expect(routeCalls()).toHaveLength(2)
    expect(routeCalls()[1]?.body).toEqual({ text: 'show my overdue tasks' })
    const secondBody = JSON.stringify(routeCalls()[1]?.body)
    // The classifier has no dialogue state, so a transcript in the body would be
    // data the model cannot use and the user did not agree to send.
    expect(secondBody).not.toContain('atlas')
    expect(Object.keys(routeCalls()[1]?.body as object)).toEqual(['text'])
  })

  it('never sends conversation history on a retry either', async () => {
    const { result } = renderAssistant()

    await submitExpectingError(result, 'show my tasks', () =>
      Promise.reject(new TypeError('Failed to fetch')),
    )

    respond = () => Promise.reject(new TypeError('Failed to fetch'))
    await act(async () => {
      result.current.retry()
      await Promise.resolve()
    })
    await waitFor(() => expect(routeCalls()).toHaveLength(2))

    expect(routeCalls()).toHaveLength(2)
    expect(routeCalls()[1]?.body).toEqual({ text: 'show my tasks' })
  })
})

describe('conversation continuity with a stateless classifier', () => {
  it('offers the previous accepted service when a turn comes back uncertain', async () => {
    const { result } = renderAssistant('/analytics')

    await submit(result, 'show my open tasks')
    await waitFor(() => expect(result.current.turns).toHaveLength(1))

    await submit(result, 'and the trends', UNCERTAIN)
    await waitFor(() => expect(result.current.turns).toHaveLength(2))

    // A stateless classifier cannot be told what "and the trends" refers to, so
    // the only continuity available is naming what was accepted a moment ago.
    expect(result.current.suggestion).toEqual({
      label: 'Task',
      service: 'TaskService',
      entrypoint: 'TaskService.list',
      destination: 'api/v1/tasks',
      pageLabel: 'Analytics',
    })
  })

  it('offers nothing when there is no accepted turn to offer', async () => {
    const { result } = renderAssistant()

    await submit(result, 'and the trends', UNCERTAIN)
    await waitFor(() => expect(result.current.turns).toHaveLength(1))

    expect(result.current.suggestion).toBeNull()
    expect(result.current.state).toBe('idle')
  })

  it('never offers a suggestion for a recognised request with nowhere to go', async () => {
    const { result } = renderAssistant()

    await submit(result, 'show my tasks')
    await waitFor(() => expect(result.current.turns).toHaveLength(1))
    await submit(result, 'refactor this module for me', GENERATION_UNAVAILABLE)
    await waitFor(() => expect(result.current.turns).toHaveLength(2))

    expect(result.current.suggestion).toBeNull()
  })
})

describe('backend failures', () => {
  it('names the missing classifier rather than blaming the network', async () => {
    const { result } = renderAssistant()
    await submitExpectingError(result, 'show my tasks', () =>
      errorEnvelope('ml_unavailable', 'model not loaded', null, 503),
    )

    const error = result.current.error as VoiceError
    expect(error.code).toBe('classifier_unavailable')
    expect(error.retryable).toBe(false)
    expect(error.message).toMatch(/not running on this backend/i)
    // The user has to be told the rest of the product still works, or a missing
    // checkpoint reads as a broken application.
    expect(error.message).toMatch(/typing still works/i)
  })

  it('surfaces a 422 with its field-level detail', async () => {
    const { result } = renderAssistant()
    await submitExpectingError(result, 'show my tasks', () =>
      errorEnvelope('validation_error', 'text is too long', {
        text: ['String should have at most 500 characters'],
      }),
    )

    expect(result.current.error?.code).toBe('invalid_response')
    expect(result.current.error?.message).toContain('at most 500 characters')
    expect(result.current.turns).toHaveLength(1)
    expect(result.current.turns[0]?.decision).toBeNull()
    expect(result.current.turns[0]?.transcript).toBe('show my tasks')
  })

  it('says who is missing when the session is rejected', async () => {
    const { result } = renderAssistant()
    await submitExpectingError(result, 'show my tasks', () =>
      errorEnvelope('unauthorized', 'Not authenticated', null, 401),
    )

    expect(result.current.error?.message).toMatch(/does not know who is asking/i)
    expect(result.current.error?.retryable).toBe(false)
  })

  it('reports a transport failure as a timeout the user can act on', async () => {
    const { result } = renderAssistant()
    await submitExpectingError(result, 'show my tasks', () =>
      Promise.reject(new TypeError('Failed to fetch')),
    )

    expect(result.current.error?.code).toBe('timeout')
    expect(result.current.error?.retryable).toBe(true)
  })

  it('does not retry a 503 on its own', async () => {
    const { result } = renderAssistant()
    await submitExpectingError(result, 'show my tasks', () =>
      errorEnvelope('ml_unavailable', 'model not loaded', null, 503),
    )
    await new Promise((resolve) => setTimeout(resolve, 20))

    // A checkpoint that is not loaded will not load itself in the two seconds a
    // retry would wait; the client must not spend the user's time proving it.
    expect(calls).toHaveLength(1)
  })

  it('re-runs a retryable turn only when the user asks', async () => {
    const { result } = renderAssistant()
    await submitExpectingError(result, 'show my tasks', () =>
      Promise.reject(new TypeError('Failed to fetch')),
    )

    respond = () => json(ACCEPTED)
    await act(async () => {
      result.current.retry()
      await Promise.resolve()
    })

    await waitFor(() => expect(result.current.turns).toHaveLength(2))
    expect(routeCalls()).toHaveLength(2)
    expect(routeCalls()[1]?.body).toEqual({ text: 'show my tasks' })
  })

  it('will not retry a turn that failed for a reason retrying cannot fix', async () => {
    const { result } = renderAssistant()
    await submitExpectingError(result, 'show my tasks', () =>
      errorEnvelope('unauthorized', 'Not authenticated', null, 403),
    )

    await act(async () => {
      result.current.retry()
      await Promise.resolve()
    })

    expect(calls).toHaveLength(1)
  })

  it('dismisses an error back to idle without re-running anything', async () => {
    const { result } = renderAssistant()
    await submitExpectingError(result, 'show my tasks', () =>
      Promise.reject(new TypeError('Failed to fetch')),
    )

    act(() => {
      result.current.dismissError()
    })

    expect(result.current.state).toBe('idle')
    expect(result.current.error).toBeNull()
    expect(calls).toHaveLength(1)
  })
})

describe('describeRoutingFailure', () => {
  it('separates a client timeout from an unreachable backend, and marks both retryable', () => {
    const timedOut = describeRoutingFailure(
      new ApiError({ status: 0, code: 'timeout', message: 'Request timed out' }),
    )
    const offline = describeRoutingFailure(
      new ApiError({ status: 0, code: 'network_error', message: 'Network request failed' }),
    )

    expect(timedOut.code).toBe('timeout')
    expect(offline.code).toBe('timeout')
    expect(timedOut.retryable).toBe(true)
    expect(offline.retryable).toBe(true)
  })

  it('falls back to invalid_response for a server fault it cannot explain', () => {
    const error = describeRoutingFailure(
      new ApiError({ status: 500, code: 'internal_error', message: 'boom' }),
    )

    expect(error.code).toBe('invalid_response')
    expect(error.retryable).toBe(true)
  })

  it('never leaks a raw backend message into the copy', () => {
    const error = describeRoutingFailure(
      new ApiError({
        status: 500,
        code: 'internal_error',
        message: 'Traceback (most recent call last): File "/app/app/ml/runtime.py"',
      }),
    )

    expect(error.message).not.toContain('Traceback')
    expect(error.message).not.toContain('/app/app/ml')
  })

  it('tells an expired session apart from a missing permission', () => {
    // Same words to the user — both are "you cannot do this right now" — but
    // different codes, because they have different fixes and a support log that
    // cannot tell them apart is a log that cannot answer "why did this happen".
    const expired = describeRoutingFailure(
      new ApiError({ status: 401, code: 'unauthorized', message: 'expired' }),
    )
    const forbidden = describeRoutingFailure(
      new ApiError({ status: 403, code: 'forbidden', message: 'no analytics.read' }),
    )

    expect(expired.code).toBe('not_authenticated')
    expect(forbidden.code).toBe('not_permitted')
    expect(expired.code).not.toBe(forbidden.code)
    expect(expired.retryable).toBe(false)
    expect(forbidden.retryable).toBe(false)
  })
})

describe('unmount', () => {
  it('aborts a request that is still in flight', async () => {
    // A promise that never settles, so the request is genuinely still open when
    // the panel goes away.
    installFetch(() => new Promise<Response>(() => undefined))
    const { result, unmount } = renderAssistant()

    await act(async () => {
      result.current.submitTranscript('show my tasks')
      await Promise.resolve()
    })
    expect(result.current.state).toBe('processing')
    expect(calls[0]?.signal?.aborted).toBe(false)

    unmount()

    expect(calls[0]?.signal?.aborted).toBe(true)
  })

  it('aborts the recogniser so the microphone is released', () => {
    const { result, unmount } = renderAssistant()

    act(() => {
      result.current.startListening()
    })
    const recogniser = latestRecogniser()
    expect(recogniser.abortCalls).toBe(0)

    unmount()

    expect(recogniser.abortCalls).toBe(1)
  })

  it('stops speaking so the browser is not left talking to a closed panel', async () => {
    const { result, unmount } = renderAssistant()
    await submitWithoutSettling(result)

    unmount()

    expect(cancelSpeechSpy).toHaveBeenCalled()
  })

  it('keeps a late answer out of the transcript after unmount', async () => {
    let release: ((response: Response) => void) | null = null
    installFetch(
      () =>
        new Promise<Response>((resolve) => {
          release = resolve
        }),
    )
    const { result, unmount } = renderAssistant()

    await act(async () => {
      result.current.submitTranscript('show my tasks')
      await Promise.resolve()
    })
    unmount()

    await act(async () => {
      release?.(json(ACCEPTED))
      await Promise.resolve()
    })

    expect(useAssistantStore.getState().turns).toHaveLength(0)
  })
})

/**
 * Submits a turn whose proposal endpoint answers `proposal`.
 *
 * The routing answer is the project one, so the decision and the proposal
 * describe the same sentence. The confirm endpoint is left on the routing answer
 * — a body it never sends — so a test that never confirms cannot pass by
 * accident, and a test that does has to install its own.
 */
async function submitProposable(
  result: { current: VoiceAssistant },
  proposal: ProposeActionRead,
  text = 'add a project called HelloWorld',
): Promise<void> {
  installEndpoints({
    [ROUTE_PATH]: () => json(ACCEPTED_PROJECT),
    [PROPOSE_PATH]: () => json(proposal),
  })
  const before = useAssistantStore.getState().turns.length
  await act(async () => {
    result.current.submitTranscript(text)
    await Promise.resolve()
  })
  await waitFor(() => expect(useAssistantStore.getState().turns.length).toBe(before + 1))
  await settleSpeech(result)
}

/** Presses Confirm and waits for the confirm endpoint's answer to land. */
async function pressConfirm(
  result: { current: VoiceAssistant },
  answer: () => Response,
): Promise<void> {
  respond = (call) => (call.url.includes(CONFIRM_PATH) ? answer() : json(ACCEPTED_PROJECT))
  await act(async () => {
    result.current.confirmProposal()
    await Promise.resolve()
  })
  await waitFor(() => expect(callsTo(CONFIRM_PATH)).toHaveLength(1))
  // `confirming` comes from the mutation rather than from local state, so this
  // is the assertion that no error path left the dialog's buttons switched off.
  await waitFor(() => expect(result.current.confirming).toBe(false))
}

/** The one proposal that names a row rather than creating one. */
const COMPLETE_TASK_PROPOSAL: ProposeActionRead = {
  proposed: true,
  intent: 'task_manage',
  confidence: 0.95,
  proposal: {
    kind: 'complete_task',
    intent: 'task_manage',
    confidence: 0.95,
    summary: "Mark the task 'draft the API contract' as done.",
    requires_confirmation: true,
    destructive: false,
    permission: 'tasks.write',
    service: 'TaskService',
    module: 'app.services.task_service',
    entrypoint: 'set_status',
    payload_schema: 'TaskStatusChange',
    payload: { status: 'done', note: null },
    target_id: '7c1f0f2e-0000-4000-8000-000000000000',
    target_label: 'draft the API contract',
    arguments: [
      {
        field: 'task',
        value: 'draft the API contract',
        matched_text: 'draft the API contract',
        rule: 'matched against the caller’s open tasks',
      },
    ],
    notes: [],
  },
  refusal: null,
}

/**
 * The write half of a turn.
 *
 * Every assertion here is about something the user can observe — a request that
 * was or was not made, a body that is exactly what the backend published, a
 * cache that was invalidated, a control that came back — because the failure
 * modes of this flow are all silent: a proposal that never arrives, a confirm
 * that never fires, a dialog that never re-enables.
 */
describe('a turn that can become a row', () => {
  it('asks for a proposal after an accepted turn, with the same one utterance', async () => {
    const { result } = renderAssistant()
    await submitProposable(result, CREATE_PROJECT_PROPOSAL)

    await waitFor(() => expect(result.current.proposal).not.toBeNull())
    // The sentence the backend composed, not one assembled here: it encodes what
    // the extractor actually read, which is the thing being agreed to.
    expect(result.current.proposal?.summary).toBe("Create a project named 'HelloWorld'.")

    const asked = callsTo(PROPOSE_PATH)
    expect(asked).toHaveLength(1)
    expect(asked[0]?.body).toEqual({ text: 'add a project called HelloWorld' })
    // The zone rides in the query string, so a date the user said resolves on
    // the same instant a day planned on the board would.
    expect(asked[0]?.url).toContain('tz=')
  })

  it('keeps the routing decision and its destination while a proposal is pending', async () => {
    const { result } = renderAssistant()
    await submitProposable(result, CREATE_PROJECT_PROPOSAL)
    await waitFor(() => expect(result.current.proposal).not.toBeNull())

    // Routing answers "where does this go"; a proposal answers "what would this
    // create". Losing the first to the second would break a reader who asked
    // only for the destination.
    expect(result.current.lastAction).toEqual({
      service: 'ProjectService',
      entrypoint: 'ProjectService.list',
      destination: 'api/v1/projects',
    })
    expect(result.current.error).toBeNull()
    expect(result.current.state).toBe('idle')
    expect(result.current.canListen).toBe(true)
  })

  it('reads a refusal as nothing to do rather than as a failure', async () => {
    const { result } = renderAssistant()
    await submitProposable(result, REFUSED, 'add a task called hello there')

    // Wait for the proposal request to have landed, so "nothing" is observed
    // after the answer rather than before it.
    await waitFor(() => expect(callsTo(PROPOSE_PATH)).toHaveLength(1))
    await act(async () => {
      await Promise.resolve()
    })

    expect(result.current.proposal).toBeNull()
    expect(result.current.actionError).toBeNull()
    expect(result.current.error).toBeNull()
    expect(result.current.state).toBe('idle')
    expect(result.current.canListen).toBe(true)
    // The turn itself is still recorded and still routed: a refusal says NEXUS
    // read the sentence, which is information and not a fault.
    expect(result.current.turns).toHaveLength(1)
    expect(result.current.lastDecision?.status).toBe('accepted')
  })

  it('never sends the transcript to the proposal endpoint either', async () => {
    const { result } = renderAssistant()
    await submitProposable(result, CREATE_PROJECT_PROPOSAL, 'add a project called HelloWorld')
    await waitFor(() => expect(callsTo(PROPOSE_PATH)).toHaveLength(1))

    const body = callsTo(PROPOSE_PATH)[0]?.body as Record<string, unknown>
    expect(Object.keys(body)).toEqual(['text'])
    // Same model, same rule: it reads one string and has no way to use a
    // transcript, so sending one would cost privacy and buy nothing.
    expect(JSON.stringify(body)).not.toContain('pageLabel')
  })

  it('sends exactly the kind, intent and payload the proposal published', async () => {
    const { result } = renderAssistant()
    await submitProposable(result, CREATE_PROJECT_PROPOSAL)
    await waitFor(() => expect(result.current.proposal).not.toBeNull())

    await pressConfirm(result, () => json(CREATED_PROJECT))

    const confirmed = callsTo(CONFIRM_PATH)
    expect(confirmed).toHaveLength(1)
    expect(confirmed[0]?.body).toEqual({
      kind: 'create_project',
      intent: 'project_manage',
      payload: CREATE_PROJECT_PROPOSAL.proposal?.payload,
    })
    // No `target_id: null` on a creation. The field is not part of the story and
    // an explicit null would be a fourth key to have to keep correct.
    expect(Object.keys(confirmed[0]?.body as object).sort()).toEqual(['intent', 'kind', 'payload'])
  })

  it('carries the target on a completion, because the endpoint needs it', async () => {
    const { result } = renderAssistant()
    await submitProposable(result, COMPLETE_TASK_PROPOSAL, 'mark the API contract as done')
    await waitFor(() => expect(result.current.proposal).not.toBeNull())

    await pressConfirm(result, () =>
      json({
        kind: 'complete_task',
        entity: 'task',
        entity_id: '7c1f0f2e-0000-4000-8000-000000000000',
        outcome: 'updated',
        applied: true,
        message: "Marked the task 'draft the API contract' as done.",
      }),
    )

    // The four fields for a completion, where the row it acts on is named. A
    // client that sent three here would earn a 422 on every completion.
    expect(callsTo(CONFIRM_PATH)[0]?.body).toEqual({
      kind: 'complete_task',
      intent: 'task_manage',
      payload: { status: 'done', note: null },
      target_id: '7c1f0f2e-0000-4000-8000-000000000000',
    })
  })

  it('invalidates the work tree so the new row appears without a refresh', async () => {
    const { result, client } = renderAssistant()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    await submitProposable(result, CREATE_PROJECT_PROPOSAL)
    await waitFor(() => expect(result.current.proposal).not.toBeNull())

    await pressConfirm(result, () => json(CREATED_PROJECT))

    // The aggregate root, for the reason `features/work/hooks.ts` gives: a new
    // project moves the list, the counts and the feed, and picking one key is
    // how a stale surface ships.
    expect(invalidate).toHaveBeenCalledWith({ queryKey: workKeys.all() })
  })

  it('invalidates nothing when the backend reports the row was already there', async () => {
    const { result, client } = renderAssistant()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    await submitProposable(result, CREATE_PROJECT_PROPOSAL)
    await waitFor(() => expect(result.current.proposal).not.toBeNull())

    await pressConfirm(result, () => json(PROJECT_ALREADY_THERE))

    expect(result.current.actionOutcome).toEqual(PROJECT_ALREADY_THERE)
    expect(invalidate).not.toHaveBeenCalled()
  })

  it('discards the proposal when the reader cancels, without a request', async () => {
    const { result } = renderAssistant()
    await submitProposable(result, CREATE_PROJECT_PROPOSAL)
    await waitFor(() => expect(result.current.proposal).not.toBeNull())
    const before = calls.length

    act(() => {
      result.current.cancelProposal()
    })

    // Nothing was written, so there is nothing to undo and no endpoint to undo
    // it through — the cancel is local on purpose.
    expect(calls).toHaveLength(before)
    expect(callsTo(CONFIRM_PATH)).toHaveLength(0)
    expect(result.current.proposal).toBeNull()
    expect(result.current.actionError).toBeNull()
    expect(result.current.state).toBe('idle')
    expect(result.current.canListen).toBe(true)
  })

  it('confirms nothing when there is no proposal to agree to', async () => {
    const { result } = renderAssistant()
    await submitProposable(result, REFUSED, 'add a task called hello there')
    await waitFor(() => expect(callsTo(PROPOSE_PATH)).toHaveLength(1))

    act(() => {
      result.current.confirmProposal()
    })

    expect(callsTo(CONFIRM_PATH)).toHaveLength(0)
  })

  it('supersedes the previous turn’s proposal, so nothing stale is on screen', async () => {
    const { result } = renderAssistant()
    await submitProposable(result, CREATE_PROJECT_PROPOSAL)
    await waitFor(() => expect(result.current.proposal).not.toBeNull())

    await submitProposable(result, REFUSED, 'show my open tasks')

    // The second turn refused, and a refusal may not be allowed to leave the
    // first turn's proposal sitting there waiting for an answer about a sentence
    // the reader has replaced.
    await act(async () => {
      await Promise.resolve()
    })
    expect(result.current.proposal).toBeNull()
  })

  it('keeps the proposal on screen when the confirm is refused, and re-enables both answers', async () => {
    const { result } = renderAssistant()
    await submitProposable(result, CREATE_PROJECT_PROPOSAL)
    await waitFor(() => expect(result.current.proposal).not.toBeNull())

    await pressConfirm(result, () =>
      errorEnvelope('forbidden', 'You do not have permission to perform this action.', null, 403),
    )

    // Closing the dialog on failure would take away the reader's only way to
    // try again, and would hide which proposal they were agreeing to.
    expect(result.current.proposal).not.toBeNull()
    expect(result.current.actionError?.code).toBe('not_permitted')
    expect(result.current.actionError?.message).toMatch(/nothing was written/i)
    expect(result.current.confirming).toBe(false)
    // The turn is untouched: it routed, and it routed successfully.
    expect(result.current.state).toBe('idle')
    expect(result.current.error).toBeNull()
    expect(result.current.canListen).toBe(true)
  })

  it('does not move the lifecycle when the payload is rejected', async () => {
    const { result } = renderAssistant()
    await submitProposable(result, CREATE_PROJECT_PROPOSAL)
    await waitFor(() => expect(result.current.proposal).not.toBeNull())

    await pressConfirm(result, () =>
      errorEnvelope('validation_error', 'The confirmed payload is not valid for this action.', {
        unknown_fields: ['colour'],
      }),
    )

    expect(result.current.actionError?.message).toMatch(/nothing was written/i)
    expect(result.current.actionError?.message).toContain('colour')
    expect(result.current.state).toBe('idle')
    expect(result.current.error).toBeNull()
  })

  it('says the classifier is unavailable without retracting the decision', async () => {
    const { result } = renderAssistant()
    installEndpoints({
      [ROUTE_PATH]: () => json(ACCEPTED_PROJECT),
      [PROPOSE_PATH]: () => errorEnvelope('ml_unavailable', 'no classifier', null, 503),
    })
    await act(async () => {
      result.current.submitTranscript('add a project called HelloWorld')
      await Promise.resolve()
    })
    await waitFor(() => expect(result.current.actionError).not.toBeNull())
    await settleSpeech(result)

    expect(result.current.actionError?.code).toBe('classifier_unavailable')
    expect(result.current.actionError?.retryable).toBe(false)
    // The routing decision survived. Turning the panel's `error` state on for
    // an optional second question would retract a good answer.
    expect(result.current.error).toBeNull()
    expect(result.current.state).toBe('idle')
    expect(result.current.lastDecision?.status).toBe('accepted')
    expect(result.current.proposal).toBeNull()
  })

  it('asks for no proposal at all when the turn was not accepted', async () => {
    const { result } = renderAssistant()
    await submit(result, 'what is the weather in Oslo', OUT_OF_SCOPE)

    // The proposal layer runs the same classifier over the same string against
    // the same threshold, so this could only ever come back a refusal. Spending
    // a request to be told nothing, on every out-of-scope sentence, is waste.
    expect(callsTo(PROPOSE_PATH)).toHaveLength(0)
    expect(result.current.proposal).toBeNull()
  })
})

describe('describeActionFailure', () => {
  it('tells the two denials apart, because they have different fixes', () => {
    const asking = describeActionFailure(
      new ApiError({ status: 403, code: 'forbidden', message: 'no analytics.read' }),
      'propose',
    )
    const acting = describeActionFailure(
      new ApiError({ status: 403, code: 'forbidden', message: 'no projects.write' }),
      'confirm',
    )

    expect(asking.code).toBe('not_permitted')
    expect(acting.code).toBe('not_permitted')
    expect(asking.retryable).toBe(false)
    // Same status, two different problems: one says this account may not ask,
    // the other says it may not make this change — and only the first implies
    // the assistant is broken.
    expect(asking.message).not.toBe(acting.message)
    expect(acting.message).toMatch(/nothing was written/i)
  })

  it('tells the two rejections apart, because the advice differs', () => {
    const utterance = describeActionFailure(
      new ApiError({ status: 422, code: 'validation_error', message: 'text too long' }),
      'propose',
    )
    const payload = describeActionFailure(
      new ApiError({ status: 422, code: 'validation_error', message: 'payload invalid' }),
      'confirm',
    )

    expect(utterance.message).toMatch(/one short instruction/i)
    expect(payload.message).toMatch(/details it read out/i)
    expect(payload.retryable).toBe(false)
  })

  it('tells the reader that routing still works when the model is missing', () => {
    const error = describeActionFailure(
      new ApiError({ status: 503, code: 'ml_unavailable', message: 'checkpoint missing' }),
      'propose',
    )

    expect(error.code).toBe('classifier_unavailable')
    expect(error.message).toMatch(/routing still works/i)
    // A checkpoint that is not loaded will not load itself in two seconds.
    expect(error.retryable).toBe(false)
  })

  it('says a lost confirm is unknown, and that asking again is safe', () => {
    const error = describeActionFailure(
      new ApiError({ status: 0, code: 'network_error', message: 'Failed to fetch' }),
      'confirm',
    )

    expect(error.code).toBe('timeout')
    expect(error.retryable).toBe(true)
    // On a write, a dropped connection does not mean the write did not happen —
    // claiming either way would be a guess dressed as a fact.
    expect(error.message).toMatch(/not known whether anything was written/i)
    expect(error.message).toMatch(/will not create the same row twice/i)
  })

  it('never leaks a raw backend message into the copy', () => {
    const error = describeActionFailure(
      new ApiError({
        status: 500,
        code: 'internal_error',
        message: 'Traceback (most recent call last): File "/app/app/ml/runtime.py"',
      }),
      'confirm',
    )

    expect(error.message).not.toContain('Traceback')
    expect(error.message).not.toContain('/app/app/ml')
  })
})

describe('the conversation store', () => {
  it('keeps at most MAX_CONVERSATION_TURNS, dropping the oldest', () => {
    const store = useAssistantStore.getState()
    for (let index = 0; index < MAX_CONVERSATION_TURNS + 5; index += 1) {
      store.addTurn({
        id: `turn-${index}`,
        transcript: `utterance ${index}`,
        decision: ACCEPTED,
        error: null,
        at: index,
      })
    }

    const { turns } = useAssistantStore.getState()
    expect(turns).toHaveLength(MAX_CONVERSATION_TURNS)
    expect(turns[0]?.id).toBe('turn-5')
    expect(turns[turns.length - 1]?.id).toBe(`turn-${MAX_CONVERSATION_TURNS + 4}`)
  })

  it('never mutates the array a subscriber is already holding', () => {
    const store = useAssistantStore.getState()
    store.addTurn({ id: 'a', transcript: 'a', decision: null, error: null, at: 0 })
    const first = useAssistantStore.getState().turns

    useAssistantStore.getState().addTurn({ id: 'b', transcript: 'b', decision: null, error: null, at: 1 })

    expect(useAssistantStore.getState().turns).not.toBe(first)
    expect(first).toHaveLength(1)
  })

  it('clears the accepted destination with the turns, not after it', async () => {
    const { result } = renderAssistant()
    await submit(result, 'show my tasks')
    await waitFor(() => expect(result.current.turns).toHaveLength(1))
    expect(useAssistantStore.getState().accepted).not.toBeNull()

    act(() => {
      result.current.clearConversation()
    })

    expect(useAssistantStore.getState().turns).toHaveLength(0)
    expect(useAssistantStore.getState().accepted).toBeNull()
    expect(result.current.suggestion).toBeNull()
    expect(result.current.lastDecision).toBeNull()
    expect(result.current.state).toBe('idle')
  })

  it('does not write the conversation to browser storage', async () => {
    const { result } = renderAssistant()
    await submit(result, 'a private thing I said')
    await waitFor(() => expect(result.current.turns).toHaveLength(1))

    expect(window.localStorage.length).toBe(0)
    expect(window.sessionStorage.length).toBe(0)
  })
})