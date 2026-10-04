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
  describeRoutingFailure,
  useVoiceAssistant,
  type UseVoiceAssistantOptions,
  type VoiceAssistant,
} from '@/features/assistant/use-voice-assistant'
import { ApiError } from '@/lib/api-client'
import type { RoutingDecisionRead } from '@/types/ml'

/**
 * jsdom implements no speech recognition and no speech synthesis, and the
 * backend is the only thing that can classify anything, so every test here
 * installs three fakes: a recogniser, a voice, and `fetch`.
 *
 * The fakes are literal on purpose. The hook's whole job is the state machine
 * between three unreliable parties, so a fake that "helpfully" behaved like the
 * real thing would hide exactly the edges these tests exist to pin.
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
    expect(calls).toHaveLength(1)
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

    await waitFor(() => expect(calls).toHaveLength(1))
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
    expect(calls).toHaveLength(1)
    expect(FakeSpeechRecognition.instances).toHaveLength(0)
  })

  it('ignores typed input while a turn is in flight', async () => {
    const { result } = renderAssistant()
    await submitWithoutSettling(result)

    await act(async () => {
      result.current.submitTranscript('and my projects')
      await Promise.resolve()
    })

    expect(calls).toHaveLength(1)
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

    expect(calls).toHaveLength(2)
    expect(calls[1]?.body).toEqual({ text: 'show my overdue tasks' })
    const secondBody = JSON.stringify(calls[1]?.body)
    // The classifier has no dialogue state, so a transcript in the body would be
    // data the model cannot use and the user did not agree to send.
    expect(secondBody).not.toContain('atlas')
    expect(Object.keys(calls[1]?.body as object)).toEqual(['text'])
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
    await waitFor(() => expect(calls).toHaveLength(2))

    expect(calls).toHaveLength(2)
    expect(calls[1]?.body).toEqual({ text: 'show my tasks' })
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
    expect(calls).toHaveLength(2)
    expect(calls[1]?.body).toEqual({ text: 'show my tasks' })
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