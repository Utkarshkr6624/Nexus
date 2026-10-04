/**
 * The voice assistant's orchestrator: one lifecycle state machine over two
 * browser speech APIs and one backend call.
 *
 * Read this before changing anything here. NEXO has exactly one model, and it is
 * a 14-class intent classifier, not a language model. Three consequences run
 * through every line below:
 *
 * 1. **The model sees one utterance.** It has no dialogue state, no memory and
 *    no way to read a transcript. Conversation history is therefore never sent
 *    — not to protect the user's privacy alone, but because the model would
 *    ignore it. The history exists only in this browser, and only to offer a
 *    suggestion when a turn comes back `uncertain`.
 * 2. **NEXO routes, it does not answer.** The assistant cannot compose prose, so
 *    nothing here speaks or renders as though it did. What it does is name a
 *    service and its destination, and the user decides what happens next.
 * 3. **`generation_unavailable` is a capability gap, not a fault.** Two intents
 *    are recognised correctly and then have nowhere to go, because NEXO runs no
 *    generative model. That is reported as a gap in the product, never as an
 *    error state or a retry prompt.
 *
 * **Why the state is driven by handlers and callbacks, never by effects.**
 * React Compiler treats a synchronous `setState` inside a `useEffect` as a
 * render loop, and this hook would be full of them: feature detection on mount,
 * capturing the current route, clearing the error when recognition starts. All
 * of that happens in event handlers and in the recogniser's own callbacks, which
 * are event handlers too. The single effect is the unmount cleanup, which only
 * aborts work in flight.
 */
import { useMutation } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'

import {
  useAssistantStore,
  type AcceptedDestination,
} from './assistant-store'
import type {
  VoiceAction,
  VoiceContext,
  VoiceError,
  VoiceState,
  VoiceTurn,
} from './types'
import { routeUtterance } from '@/services/ml'
import { isAbortError } from '@/services/errors'
import { findModule } from '@/features/modules/catalog'
import { ApiError } from '@/lib/api-client'
import type { RoutingDecisionRead } from '@/types/ml'
import {
  createSpeechRecogniser,
  isSpeechRecognitionSupported,
  type SpeechRecogniser,
} from './speech-recognition'
import { cancel as cancelSpeech, isSpeechSynthesisSupported, speak } from './speech-synthesis'

/** Labels an unregistered route so the transcript never claims a wrong surface. */
const UNKNOWN_PAGE_LABEL = 'NEXUS'

/** A turn's id. Monotonic rather than random so a transcript is readable in a test. */
let turnSequence = 0

function nextTurnId(): string {
  turnSequence += 1
  return `turn-${turnSequence}`
}

/**
 * The whole of what the assistant can say out loud.
 *
 * These are routing statements, not replies. NEXO has no generative model, so a
 * spoken sentence that sounded like an answer would be a lie about what the
 * product does; each one names a service, a destination, or a gap.
 */
const VOICE_COPY = {
  noSpeech: 'NEXO did not hear anything. Try again, or type the request instead.',
  timeout:
    'The classifier did not answer in time. It is the only model NEXO runs, so nothing else can pick this up.',
  classifierUnavailable:
    "NEXO's intent classifier is not running on this backend, so the request could not be routed. Everything else in the app keeps working — typing still works, and voice routing needs the model loaded.",
  invalidResponse: 'NEXO could not use the answer it got back. Try again in a moment.',
  auth: 'NEXO could not route that because it does not know who is asking. Sign in again, then retry.',
  validation:
    'NEXO could not accept that request. Rephrase it as a single short instruction and try again.',
  synthesisFailed: 'NEXO lost track of what it was reading out. The result above is unchanged.',
} as const

const NO_SPEECH_ERROR: VoiceError = {
  code: 'no_speech',
  message: VOICE_COPY.noSpeech,
  retryable: false,
}

/**
 * A rejected session and a missing permission are different problems with
 * different fixes, so they get different codes even though the copy is shared.
 * Calling either of them `invalid_response` would have been defensible — the
 * code only has to be stable — but it would have made the two indistinguishable
 * in a bug report, and "your session ended" versus "your account cannot do this"
 * is exactly the distinction someone reads the log to make.
 */
const AUTH_ERRORS: Record<'unauthorized' | 'forbidden', VoiceError> = {
  unauthorized: { code: 'not_authenticated', message: VOICE_COPY.auth, retryable: false },
  forbidden: { code: 'not_permitted', message: VOICE_COPY.auth, retryable: false },
}

/**
 * What an `uncertain` turn can offer.
 *
 * This chip is the entire substitute for conversational memory. The classifier
 * is stateless, so the assistant cannot ask "what did you mean by *that*?" and
 * have the model reason about the exchange; the only continuity available is to
 * name the destination it accepted a moment ago and let the user accept it
 * again. Offering that is honest. Silently re-sending the previous utterance
 * with the new one attached would not be — the model cannot use it.
 */
export interface VoiceSuggestion {
  /** Human name for the service, e.g. `Tasks`. */
  label: string
  service: string
  entrypoint: string
  destination: string
  /** The surface the earlier accepted turn was asked from. */
  pageLabel: string
}

export interface UseVoiceAssistantOptions {
  /** BCP-47 tag for recognition. Defaults to the browser's own choice. */
  lang?: string
  /** Set false to route silently, for a user who has turned speech off. */
  speechEnabled?: boolean
}

export interface VoiceAssistant {
  state: VoiceState
  error: VoiceError | null
  /** Live partial transcript while the recogniser is still listening. */
  interimTranscript: string
  /** The final recognised or typed utterance for the last turn. */
  lastTranscript: string
  lastDecision: RoutingDecisionRead | null
  /** The service the last accepted turn would call, or `null`. */
  lastAction: VoiceAction | null
  /** Offered when a turn came back `uncertain`. */
  suggestion: VoiceSuggestion | null
  turns: VoiceTurn[]
  /** The newest turn, which is what a summary panel renders. */
  lastTurn: VoiceTurn | null
  context: VoiceContext | null
  canListen: boolean
  supportsRecognition: boolean
  supportsSynthesis: boolean
  startListening: () => void
  stopListening: () => void
  submitTranscript: (text: string) => void
  /** The same action under the name the panel's form calls it by. */
  submitText: (text: string) => void
  retry: () => void
  dismissError: () => void
  /** Abandons whatever is in flight and returns to `idle`. */
  cancel: () => void
  clearConversation: () => void
}

/** Everything the assistant knows right now about the turn in progress. */
interface Lifecycle {
  state: VoiceState
  error: VoiceError | null
  interimTranscript: string
  lastTranscript: string
  lastDecision: RoutingDecisionRead | null
  lastAction: VoiceAction | null
  suggestion: VoiceSuggestion | null
}

const IDLE_LIFECYCLE: Lifecycle = {
  state: 'idle',
  error: null,
  interimTranscript: '',
  lastTranscript: '',
  lastDecision: null,
  lastAction: null,
  suggestion: null,
}

/**
 * The request a turn is built from, carried through the mutation's variables.
 *
 * `at` is stamped when the turn is *asked for*, not when the answer lands: a
 * turn that takes four seconds to classify was spoken four seconds ago, and the
 * clock has to be read in the event handler that started it. React Compiler
 * treats this file's mutation callbacks as render, and `Date.now()` there is an
 * impure call during render — so the timestamp travels with the request rather
 * than being read again where it is used.
 */
interface TurnRequest {
  text: string
  signal: AbortSignal
  context: VoiceContext
  at: number
}

function flattenFieldErrors(details: Record<string, unknown>): string {
  const parts: string[] = []
  for (const [field, value] of Object.entries(details)) {
    if (Array.isArray(value)) parts.push(`${field}: ${value.map(String).join(', ')}`)
    else if (typeof value === 'string') parts.push(`${field}: ${value}`)
  }
  return parts.join('; ')
}

/**
 * Maps a backend failure onto something a person can act on.
 *
 * The order matters. A 503 arrives as an `ApiError` with `status: 0`-style
 * transport codes alongside it, and a timeout is also a transport failure, so
 * the specific cases are tested before the general one. `invalid_response` is
 * the fallback for every failure the vocabulary has no dedicated member for.
 */
export function describeRoutingFailure(cause: unknown): VoiceError {
  if (!(cause instanceof ApiError)) {
    return { code: 'invalid_response', message: VOICE_COPY.invalidResponse, retryable: true }
  }
  if (cause.isTimeout) {
    return { code: 'timeout', message: VOICE_COPY.timeout, retryable: true }
  }
  if (cause.isTransportError) {
    return {
      code: 'timeout',
      message: 'NEXO could not reach the backend, so nothing could be classified.',
      retryable: true,
    }
  }
  if (cause.status === 503) {
    return {
      code: 'classifier_unavailable',
      message: VOICE_COPY.classifierUnavailable,
      retryable: false,
    }
  }
  if (cause.isUnauthorized) {
    return AUTH_ERRORS.unauthorized
  }
  if (cause.isForbidden) {
    return AUTH_ERRORS.forbidden
  }
  if (cause.isValidationError) {
    const fields = flattenFieldErrors(cause.fieldErrors)
    return {
      code: 'invalid_response',
      message: fields ? `${VOICE_COPY.validation} (${fields})` : VOICE_COPY.validation,
      retryable: false,
    }
  }
  return { code: 'invalid_response', message: VOICE_COPY.invalidResponse, retryable: true }
}

/** `TaskService` reads as "Task"; an unrecognisable name is left alone. */
function humaniseServiceName(service: string): string {
  const trimmed = service.endsWith('Service') ? service.slice(0, -'Service'.length) : service
  const spaced = trimmed.replace(/([a-z0-9])([A-Z])/g, '$1 $2').trim()
  return spaced.length > 0 ? spaced : service
}

/** The validated action an accepted decision maps onto, or `null`. */
function actionFor(decision: RoutingDecisionRead): VoiceAction | null {
  const target = decision.target
  if (decision.status !== 'accepted' || !target) return null
  return {
    service: target.service,
    entrypoint: target.entrypoint,
    destination: decision.destination,
  }
}

/**
 * What the assistant reads back after a decision.
 *
 * Every branch is a statement about routing. There is deliberately no branch
 * that answers the user's question, because there is no model here that could.
 */
function spokenConfirmation(decision: RoutingDecisionRead): string | null {
  switch (decision.status) {
    case 'accepted': {
      const target = decision.target
      return target ? `Routing to ${humaniseServiceName(target.service)}.` : null
    }
    case 'generation_unavailable':
      return 'NEXO recognised that request, but it runs no generative model, so there is nothing here that can answer it.'
    case 'out_of_scope':
      return 'That is outside what NEXUS can route.'
    case 'uncertain':
      return 'I am not confident enough to route that. Try naming the surface you want.'
  }
}

export function useVoiceAssistant(options: UseVoiceAssistantOptions = {}): VoiceAssistant {
  const { lang, speechEnabled = true } = options

  const { pathname } = useLocation()
  // Resolved through `findModule` so a detail route (`/tasks/17`) inherits its
  // parent's name rather than reporting itself unknown.
  const pageLabel = findModule(pathname)?.label ?? UNKNOWN_PAGE_LABEL

  // Feature detection happens during render and is deliberately not stored in
  // state. A browser's speech APIs do not appear or disappear mid-session, so a
  // stored copy could only ever disagree with the truth, and `unsupported` is
  // derived below rather than entered — a terminal state that cannot be left.
  const supportsRecognition = isSpeechRecognitionSupported()
  const supportsSynthesis = isSpeechSynthesisSupported()

  const [lifecycle, setLifecycle] = useState<Lifecycle>(IDLE_LIFECYCLE)

  const turns = useAssistantStore((store) => store.turns)
  const accepted = useAssistantStore((store) => store.accepted)
  const addTurn = useAssistantStore((store) => store.addTurn)
  const recordAccepted = useAssistantStore((store) => store.recordAccepted)
  const clearTurns = useAssistantStore((store) => store.clearTurns)

  /**
   * Mirrors the lifecycle so guards read the current value inside callbacks
   * that were created by an earlier render. Written only from handlers, never
   * during render.
   */
  const phaseRef = useRef<VoiceState>('idle')
  const recogniserRef = useRef<SpeechRecogniser | null>(null)
  const requestRef = useRef<AbortController | null>(null)
  /** The turn in flight: its text, when it was asked for, whether it may repeat. */
  const pendingRef = useRef<{ text: string; retryable: boolean; at: number } | null>(null)
  /** The accepted destination as it stood when the current turn began. */
  const previousRef = useRef<AcceptedDestination | null>(null)
  /** False once the panel is gone; keeps a late response out of a dead tree. */
  const mountedRef = useRef(true)

  const state: VoiceState = supportsRecognition ? lifecycle.state : 'unsupported'

  function apply(patch: Partial<Lifecycle>): void {
    setLifecycle((previous) => ({ ...previous, ...patch }))
  }

  function moveTo(next: VoiceState): void {
    phaseRef.current = next
    setLifecycle((previous) => (previous.state === next ? previous : { ...previous, state: next }))
  }

  function showError(error: VoiceError): void {
    phaseRef.current = 'error'
    apply({ state: 'error', error, interimTranscript: '' })
  }

  /** Records the failed turn in the transcript *and* surfaces it. */
  function failTurn(error: VoiceError): void {
    const pending = pendingRef.current
    if (pending) {
      addTurn({
        id: nextTurnId(),
        transcript: pending.text,
        decision: null,
        error,
        at: pending.at,
      })
      pendingRef.current = { text: pending.text, retryable: error.retryable, at: pending.at }
    } else {
      // No utterance to attach: the recogniser failed before hearing anything, so
      // the transcript says nothing rather than claiming an empty request.
      addTurn({
        id: nextTurnId(),
        transcript: '',
        decision: null,
        error,
        at: Date.now(),
      })
    }
    showError(error)
  }

  function finishSpeaking(): void {
    if (!mountedRef.current) return
    moveTo('idle')
  }

  /**
   * Reads the routing result back, unless the browser has no speech synthesis
   * or the user has turned it off.
   *
   * `speak` reports whether audio actually started, and a `false` means **no
   * callback will fire at all**. Waiting for an `onEnd` that is never coming is
   * how an assistant gets stuck saying "speaking" for ever, so the return value
   * is branched on rather than assumed.
   */
  function speakDecision(decision: RoutingDecisionRead): void {
    const phrase = supportsSynthesis && speechEnabled ? spokenConfirmation(decision) : null
    if (!phrase) {
      moveTo('idle')
      return
    }

    moveTo('speaking')
    let reported = false
    const started = speak(phrase, {
      onEnd: finishSpeaking,
      onError: () => {
        reported = true
        // Not recorded as a turn: the classification already succeeded and the
        // transcript says so. This only cost the user the read-aloud.
        showError({
          code: 'synthesis_failed',
          message: VOICE_COPY.synthesisFailed,
          retryable: false,
        })
      },
    })
    // `false` with no error means the browser took the text and said nothing —
    // be quiet rather than leave a state waiting on an event.
    if (!started && !reported) moveTo('idle')
  }

  /**
   * Sends exactly one utterance. Nothing else.
   *
   * The classifier has no dialogue state — it maps a string to one of fourteen
   * labels — so attaching the transcript would enlarge the request, not inform
   * it, and would send the user's earlier utterances to the backend for no gain.
   * That is why history is not a parameter here and why there is no code path
   * that could add it.
   */
  function run(text: string, context: VoiceContext, at: number): void {
    requestRef.current?.abort()
    const controller = new AbortController()
    requestRef.current = controller
    pendingRef.current = { text, retryable: true, at }
    previousRef.current = accepted ?? null

    moveTo('processing')
    apply({
      interimTranscript: '',
      lastTranscript: text,
      lastDecision: null,
      lastAction: null,
      suggestion: null,
      error: null,
    })
    routeMutation.mutate({ text, signal: controller.signal, context, at })
  }

  const routeMutation = useMutation<RoutingDecisionRead, Error, TurnRequest>({
    mutationFn: ({ text, signal }) => routeUtterance(text, signal),
    // Explicit rather than inherited. A 503 `ml_unavailable` means the
    // checkpoint is not loaded on the server and will not be loaded in the two
    // seconds a retry would wait; repeating it only doubles the wait for the
    // same honest answer. A failed turn is offered back to the user, who
    // decides — the client never decides for them.
    retry: false,
    onSuccess: (decision, request) => {
      if (!mountedRef.current) return
      addTurn({
        id: nextTurnId(),
        transcript: request.text,
        decision,
        error: null,
        at: request.at,
      })
      pendingRef.current = null

      const previous = previousRef.current
      if (decision.status === 'accepted' && decision.target) {
        recordAccepted({
          intent: decision.intent,
          service: decision.target.service,
          entrypoint: decision.target.entrypoint,
          destination: decision.destination,
          context: request.context,
        })
      }

      apply({
        lastDecision: decision,
        lastAction: actionFor(decision),
        // `generation_unavailable` is a recognised request with nowhere to go,
        // not an uncertainty, so it never produces a suggestion.
        suggestion:
          decision.status === 'uncertain' && previous ? suggestionFor(previous) : null,
      })
      speakDecision(decision)
    },
    onError: (cause) => {
      // An abort is our own doing — unmount, or the user cancelled — and must
      // not be reported as a failure they did not cause.
      if (!mountedRef.current || isAbortError(cause) || phaseRef.current !== 'processing') return
      failTurn(describeRoutingFailure(cause))
    },
  })

  function suggestionFor(previous: AcceptedDestination): VoiceSuggestion {
    return {
      label: humaniseServiceName(previous.service),
      service: previous.service,
      entrypoint: previous.entrypoint,
      destination: previous.destination,
      pageLabel: previous.context.pageLabel,
    }
  }

  function currentContext(): VoiceContext {
    return {
      pathname,
      pageLabel,
      previousIntent: accepted?.intent,
    }
  }

  function submitTranscript(raw: string): void {
    const text = raw.trim()
    // Typed input takes the same road as a recognised utterance, so a user with
    // no microphone — or in Firefox, which ships no recogniser at all — is not
    // left with a feature that only works out loud.
    if (text.length === 0) return
    if (phaseRef.current !== 'idle') return
    run(text, currentContext(), Date.now())
  }

  function startListening(): void {
    if (!supportsRecognition) return
    const phase = phaseRef.current
    // `error` is a resting state, not a busy one: starting again is exactly
    // what the user asked for when they dismissed it.
    if (phase !== 'idle' && phase !== 'error') return

    pendingRef.current = null
    phaseRef.current = 'listening'
    apply({
      state: 'listening',
      error: null,
      interimTranscript: '',
      lastTranscript: '',
      lastDecision: null,
      lastAction: null,
      suggestion: null,
    })

    // A fresh recogniser per turn. One utterance per session: `continuous` would
    // hold the microphone open and make "stop" ambiguous, because the browser —
    // not us — decides when a phrase has ended.
    const recogniser = createSpeechRecogniser({
      lang,
      onInterim: (text) => apply({ interimTranscript: text }),
      onFinal: (text) => {
        recogniserRef.current = null
        phaseRef.current = 'idle'
        apply({ interimTranscript: '' })
        run(text, currentContext(), Date.now())
      },
      onError: (error) => {
        recogniserRef.current = null
        failTurn(error)
      },
      onEnd: () => {
        recogniserRef.current = null
        // Ending without a final result and without an error means the browser
        // simply stopped listening. Left alone the assistant would sit in
        // `listening` for ever with nothing to end it.
        if (phaseRef.current === 'listening') failTurn(NO_SPEECH_ERROR)
      },
    })
    recogniserRef.current = recogniser
    recogniser.start()
  }

  function stopListening(): void {
    if (phaseRef.current !== 'listening') return
    recogniserRef.current?.stop()
    recogniserRef.current = null
    phaseRef.current = 'idle'
    apply({ state: 'idle', interimTranscript: '' })
  }

  function retry(): void {
    if (phaseRef.current !== 'error') return
    const pending = pendingRef.current
    // Only the user's own decision re-runs a turn, and only when re-running it
    // could plausibly work. Nothing here retries on its own.
    if (!pending || !pending.retryable || pending.text.trim().length === 0) return
    run(pending.text, currentContext(), Date.now())
  }

  function dismissError(): void {
    if (lifecycle.error === null) return
    pendingRef.current = null
    previousRef.current = null
    moveTo('idle')
    apply({ error: null, interimTranscript: '' })
  }

  function cancel(): void {
    recogniserRef.current?.abort()
    recogniserRef.current = null
    // Module-level because the speech queue is: cancelling is about stopping
    // the browser's audio, not about this assistant's state.
    cancelSpeech()
    requestRef.current?.abort()
    requestRef.current = null
    pendingRef.current = null
    previousRef.current = null
    moveTo('idle')
    apply({ error: null, interimTranscript: '' })
  }

  function clearConversation(): void {
    clearTurns()
    pendingRef.current = null
    previousRef.current = null
    const phase = phaseRef.current
    if (phase === 'idle' || phase === 'error') moveTo('idle')
    apply({
      lastTranscript: '',
      interimTranscript: '',
      lastDecision: null,
      lastAction: null,
      suggestion: null,
      error: null,
    })
  }

  // Cleanup only. Nothing here sets state, so it is not a render loop; it stops
  // a microphone, a voice and a request that would otherwise outlive the panel.
  useEffect(
    () => () => {
      mountedRef.current = false
      recogniserRef.current?.abort()
      recogniserRef.current = null
      cancelSpeech()
      requestRef.current?.abort()
      requestRef.current = null
    },
    [],
  )

  return {
    state,
    error: lifecycle.error,
    interimTranscript: lifecycle.interimTranscript,
    lastTranscript: lifecycle.lastTranscript,
    lastDecision: lifecycle.lastDecision,
    lastAction: lifecycle.lastAction,
    suggestion: lifecycle.suggestion,
    turns,
    lastTurn: turns.length > 0 ? (turns[turns.length - 1] ?? null) : null,
    context: accepted?.context ?? null,
    canListen: supportsRecognition && (state === 'idle' || state === 'error'),
    supportsRecognition,
    supportsSynthesis,
    startListening,
    stopListening,
    submitTranscript,
    submitText: submitTranscript,
    retry,
    dismissError,
    cancel,
    clearConversation,
  }
}