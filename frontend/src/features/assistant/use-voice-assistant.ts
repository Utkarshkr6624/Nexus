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
 *    suggestion when a turn comes back `uncertain`. The action pair below is
 *    held to the same rule: it is the same classifier over the same one
 *    sentence, so it is sent the same one string and nothing else.
 * 2. **NEXO routes, it does not answer.** The assistant cannot compose prose, so
 *    nothing here speaks or renders as though it did. What it does is name a
 *    service and its destination, and the user decides what happens next.
 * 3. **`generation_unavailable` is a capability gap, not a fault.** Two intents
 *    are recognised correctly and then have nowhere to go, because NEXO runs no
 *    generative model. That is reported as a gap in the product, never as an
 *    error state or a retry prompt.
 *
 * **Routing is not the whole of it any more.** An accepted turn also asks
 * `POST /ml/action/propose` what NEXUS could *do* about the sentence, and
 * offers the user's own confirm before `POST /ml/action/confirm` writes
 * anything. Both halves survive: the destination and its "Go to X" button
 * answer "where does this go", which is a different question from "what would
 * this create", and a reader who asked the first still needs the answer.
 *
 * **Why the state is driven by handlers and callbacks, never by effects.**
 * React Compiler treats a synchronous `setState` inside a `useEffect` as a
 * render loop, and this hook would be full of them: feature detection on mount,
 * capturing the current route, clearing the error when recognition starts. All
 * of that happens in event handlers and in the recogniser's own callbacks, which
 * are event handlers too. The single effect marks the panel mounted and, on
 * unmount, aborts work in flight.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query'
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
import { confirmAction, proposeAction, routeUtterance } from '@/services/ml'
import { isAbortError } from '@/services/errors'
import { findModule } from '@/features/modules/catalog'
import { developerKeys } from '@/features/developer/hooks'
import { knowledgeKeys } from '@/features/knowledge/hooks'
import { learningKeys } from '@/features/learning/hooks'
import { plannerKeys } from '@/features/planner/hooks'
import { workKeys } from '@/features/work/hooks'
import { ApiError } from '@/lib/api-client'
import { plannerLocalTimezone } from '@/types/planner'
import type {
  ActionKind,
  ActionProposalRead,
  ConfirmActionRead,
  ConfirmActionRequest,
  ProposeActionRead,
  RoutingDecisionRead,
} from '@/types/ml'
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
  /**
   * The action the last turn described, awaiting the user's answer, or `null`.
   *
   * `null` is the ordinary case and covers three different things that all mean
   * the same thing to a reader: there was no proposal, there was a refusal, and
   * the reader has already answered. Nothing about the panel distinguishes them.
   */
  proposal: ActionProposalRead | null
  /** True while the confirm request is in flight. */
  confirming: boolean
  /** What the confirmed action actually did, or `null`. */
  actionOutcome: ConfirmActionRead | null
  /**
   * A failure of the action layer, or `null`.
   *
   * Separate from `error` on purpose: that one is the *turn* failing, and it
   * moves the panel into its `error` state. This one happened after a turn that
   * succeeded, so the routing decision it followed is still true and still on
   * screen. Retracting a good answer because an optional second question was
   * refused would be worse than the failure itself.
   */
  actionError: VoiceError | null
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
  /** Carries out the pending proposal. A no-op without one. */
  confirmProposal: () => void
  /** Discards the pending proposal. Makes no request. */
  cancelProposal: () => void
  dismissActionOutcome: () => void
  dismissActionError: () => void
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
  proposal: ActionProposalRead | null
  actionOutcome: ConfirmActionRead | null
  actionError: VoiceError | null
}

const IDLE_LIFECYCLE: Lifecycle = {
  state: 'idle',
  error: null,
  interimTranscript: '',
  lastTranscript: '',
  lastDecision: null,
  lastAction: null,
  suggestion: null,
  proposal: null,
  actionOutcome: null,
  actionError: null,
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

/**
 * The request a proposal is built from.
 *
 * `generation` is the turn that asked for it. Aborting the previous request is
 * not on its own enough: a response that was already in flight can still land
 * after the next turn has started, and without this the panel would offer to
 * create something the reader has moved on from.
 */
interface ProposalRequest {
  text: string
  signal: AbortSignal
  generation: number
}

/** A confirm request, with the cancellation the panel tears down on unmount. */
interface ConfirmRequest {
  body: ConfirmActionRequest
  signal: AbortSignal
}

/**
 * One entry inside a `details` value, as a sentence fragment.
 *
 * The backend's 422 `details` are not a flat map of field to string. The two
 * shapes that arrive are a list of strings (`accepted_intents`) and a list of
 * `{ field, message, type }` objects (`errors`, from a payload that did not
 * validate) — and an object handed straight to `String()` is how a confirm
 * failure reached the user as `(errors: [object Object])`, which says nothing at
 * all about what went wrong.
 *
 * The `message` is preferred because it is the sentence the backend wrote for a
 * person; the `field` is folded in when the entry names one *and* the caller did
 * not already print it, so a per-field failure reads as
 * `name — String should have at least 1 character` rather than as a bare
 * complaint. Anything else falls through to a shape-appropriate rendering, and
 * an object with neither member is JSON-stringified rather than stringified, so
 * no value on any path can ever produce `[object Object]`.
 */
function describeDetailEntry(entry: unknown): string {
  if (typeof entry === 'string') return entry
  if (typeof entry === 'number' || typeof entry === 'boolean') return String(entry)
  if (entry !== null && typeof entry === 'object') {
    const record = entry as Record<string, unknown>
    const message = record.message
    if (typeof message === 'string' && message.length > 0) {
      const field = typeof record.field === 'string' ? record.field : ''
      return field.length > 0 ? `${field} — ${message}` : message
    }
    const parts = Object.entries(record)
      .filter((entry): entry is [string, string | number | boolean] => {
        const value = entry[1]
        return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
      })
      .map(([key, value]) => `${key}: ${String(value)}`)
    if (parts.length > 0) return parts.join(', ')
    try {
      return JSON.stringify(record) ?? ''
    } catch {
      return ''
    }
  }
  // `null` and `undefined` carry no information worth showing, and `String(null)`
  // reading "null" beside a field name reads as a value the user supplied.
  return ''
}

function flattenFieldErrors(details: Record<string, unknown>): string {
  const parts: string[] = []
  for (const [field, value] of Object.entries(details)) {
    if (value === null || value === undefined) continue
    if (Array.isArray(value)) {
      const rendered = value
        .map(describeDetailEntry)
        .filter((entry) => entry.length > 0)
        .join(', ')
      if (rendered.length > 0) parts.push(`${field}: ${rendered}`)
    } else {
      const rendered = describeDetailEntry(value)
      if (rendered.length > 0) parts.push(`${field}: ${rendered}`)
    }
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

/* --------------------------------------------------------------- actions -- */

/**
 * The wording for the failures the propose/confirm pair can genuinely return.
 *
 * **This does not reuse `VOICE_COPY`, on purpose.** A 403 on `/ml/route` means
 * the account may not ask the question at all; a 403 on `/action/confirm` means
 * it asked, was answered, and may not make *that* change. Same status, two
 * different problems with two different fixes, and the second one says nothing
 * whatever about whether the assistant is still working — which is the sentence
 * a reader of a proposal dialog most needs to hear.
 *
 * Every message says what did **not** happen. A panel that only says what went
 * wrong leaves the reader guessing whether the write landed, and on the action
 * pair the honest answer is not always "it did not".
 */
const ACTION_COPY = {
  proposeForbidden:
    'NEXUS read that, but this account is not allowed to ask the backend what to do with it. Nothing was changed, and routing still works.',
  proposeValidation:
    'NEXUS would not accept the request as it was written. Rephrase it as one short instruction — nothing was changed.',
  classifierUnavailable:
    "NEXUS's intent classifier is not running on this backend, so it could not work out what to do with that. Routing still works; carrying out a request needs the model loaded.",
  proposeUnreachable:
    'NEXUS could not reach the backend to ask what to do, so there is nothing to agree to. The decision above is unaffected.',
  confirmForbidden:
    'NEXUS read that, but this account is not allowed to make this kind of change, so nothing was written.',
  confirmValidation:
    'NEXUS would not accept the details it read out of that request, so nothing was written. Rephrase it with the value spelled out in full.',
  confirmNotFound:
    'The row that request referred to is not there any more, so nothing was written.',
  // Not the same as the propose wording, because on confirm it is genuinely
  // unknown: the request may have reached the backend before the connection
  // broke. Asking again is safe — the endpoint answers a repeat with the row it
  // already has rather than making a second one — and that is the honest advice
  // rather than a guess in either direction.
  confirmUnreachable:
    'NEXUS could not reach the backend, so it is not known whether anything was written. Ask again if you like — NEXUS will not create the same row twice.',
  fallback:
    'NEXUS could not carry that out. Nothing this panel claimed was changed.',
} as const

/**
 * Which of the two calls failed.
 *
 * The stage is a parameter because the two surfaces fail for different reasons
 * under the same status: a 422 on propose means the *utterance* was refused
 * (blank, too long, credential-shaped), while a 422 on confirm means the
 * *extracted payload* did not validate, and the advice for those is not the same.
 */
export type ActionStage = 'propose' | 'confirm'

/**
 * Maps a failure of either action call onto something a person can act on.
 *
 * Ordered as in `describeRoutingFailure` — transport and timeout first, because
 * they arrive with a status of `0` beside the 503 — and 503 before the auth
 * cases for the same reason: a runtime with no classifier loaded is a fact
 * about the backend, not about the session. A 401 reuses the routing copy
 * verbatim, because it is the same fact: the session is gone.
 */
export function describeActionFailure(cause: unknown, stage: ActionStage): VoiceError {
  const unreachable = stage === 'confirm' ? ACTION_COPY.confirmUnreachable : ACTION_COPY.proposeUnreachable
  if (!(cause instanceof ApiError)) {
    return { code: 'invalid_response', message: ACTION_COPY.fallback, retryable: true }
  }
  if (cause.isTimeout || cause.isTransportError) {
    return { code: 'timeout', message: unreachable, retryable: true }
  }
  if (cause.status === 503) {
    return {
      code: 'classifier_unavailable',
      message: ACTION_COPY.classifierUnavailable,
      retryable: false,
    }
  }
  if (cause.isUnauthorized) {
    return AUTH_ERRORS.unauthorized
  }
  if (cause.isForbidden) {
    return {
      code: 'not_permitted',
      message: stage === 'confirm' ? ACTION_COPY.confirmForbidden : ACTION_COPY.proposeForbidden,
      retryable: false,
    }
  }
  if (cause.isNotFound) {
    // Deliberately not branched by stage. A 404 on confirm is the row a
    // completion named having gone; a 404 on propose is unreachable from this
    // panel, which never sends a `project_id` for one to be resolved from, so
    // the sentence that is true of the reachable case is the one shipped.
    return {
      code: 'invalid_response',
      message: ACTION_COPY.confirmNotFound,
      retryable: false,
    }
  }
  if (cause.isValidationError) {
    const fields = flattenFieldErrors(cause.fieldErrors)
    const base =
      stage === 'confirm' ? ACTION_COPY.confirmValidation : ACTION_COPY.proposeValidation
    return {
      code: 'invalid_response',
      message: fields ? `${base} (${fields})` : base,
      retryable: false,
    }
  }
  return { code: 'invalid_response', message: ACTION_COPY.fallback, retryable: true }
}

/**
 * The cache root each action writes into.
 *
 * Typed `Partial` deliberately, and the lookup is guarded below: the backend can
 * add a kind before this client learns about it, and an unguarded call into an
 * `undefined` entry inside a mutation's `onSuccess` would be an unhandled
 * rejection — the exact failure this flow exists to stop being possible. The
 * same guard is what makes an unmapped kind a no-op rather than a fault.
 *
 * Keyed by `kind` rather than by the response's `entity`, because `kind` is what
 * the proposal named and what the confirm response echoes back. The two would
 * mostly agree, but not always: `complete_task` writes a task while naming an
 * action whose name says nothing about the row, and a kind is the coarser grain
 * — a note and a bookmark and a link all land in the same tree.
 *
 * The roots are the *aggregate* ones each feature already publishes, for the
 * reason `features/work/hooks.ts` gives when its own mutations invalidate
 * `workKeys.all()`: a new task moves the board, the project counts and the
 * activity feed at once, and picking the single "right" key is how a stale
 * surface ships. Knowledge, learning and the planner each have their own root and
 * are untouched by the others.
 *
 * **Every kind that writes to a query-cached surface is listed, including the
 * deletes.** A deleted row is invisible for exactly as long as a stale one, and
 * a delete that never invalidates leaves the reader looking at a card that is
 * already gone — which reads as the assistant inventing rows, and is worse than
 * the write having silently failed.
 *
 * `create_repository` and `delete_repository` are the only kinds that write
 * outside the task, project, knowledge, learning and planner trees, and they
 * invalidate the aggregate root for the same reason the others do: registering or
 * removing a folder changes the repository count in the developer summary, so the
 * whole `['developer']` tree goes stale at once — exactly as a scan does.
 * Registration itself runs no scan, so nothing else about the tree changes, but
 * picking the narrower `repositories` key would leave the summary card showing
 * the old count beside a list that already has the new row, and a reader has no
 * way to tell which of the two is wrong. A delete invalidates for the reason
 * every other delete here does: the row is gone, and a list still holding it is
 * worse than one that is merely stale.
 *
 * `update_profile` is the one kind with no entry, and it is absent on purpose
 * rather than missed: the signed-in user lives in the auth store, not in a
 * query, so there is no cache key to invalidate. The profile surfaces re-read
 * that store on their next render, and a plausible-looking key here would
 * invalidate the wrong tree for a write that has no tree.
 */
const CACHE_ROOT_BY_KIND: Partial<Record<ActionKind, () => readonly unknown[]>> = {
  create_task: workKeys.all,
  update_task: workKeys.all,
  delete_task: workKeys.all,
  complete_task: workKeys.all,
  set_task_status: workKeys.all,
  schedule_task: workKeys.all,
  unschedule_task: workKeys.all,
  tag_task: workKeys.all,
  untag_task: workKeys.all,
  create_project: workKeys.all,
  update_project: workKeys.all,
  delete_project: workKeys.all,
  set_project_status: workKeys.all,
  create_note: knowledgeKeys.all,
  update_note: knowledgeKeys.all,
  delete_note: knowledgeKeys.all,
  archive_note: knowledgeKeys.all,
  publish_note: knowledgeKeys.all,
  create_bookmark: knowledgeKeys.all,
  delete_bookmark: knowledgeKeys.all,
  create_concept: knowledgeKeys.all,
  delete_concept: knowledgeKeys.all,
  create_link: knowledgeKeys.all,
  delete_link: knowledgeKeys.all,
  create_learning_goal: learningKeys.all,
  update_learning_goal: learningKeys.all,
  complete_learning_goal: learningKeys.all,
  delete_learning_goal: learningKeys.all,
  create_skill: learningKeys.all,
  delete_skill: learningKeys.all,
  create_event: plannerKeys.all,
  update_event: plannerKeys.all,
  delete_event: plannerKeys.all,
  create_session: plannerKeys.all,
  delete_session: plannerKeys.all,
  create_repository: developerKeys.all,
  delete_repository: developerKeys.all,
}

/**
 * The confirm body, built from the proposal and nothing else.
 *
 * **Four fields for a creation, five for one that acts on a row, and always the
 * acknowledgement.** `target_id` is the row a non-creation acts on and is
 * meaningless for a creation, so it is left off entirely rather than sent as an
 * explicit `null`. `confirm_destructive` is copied from the proposal's
 * `destructive` flag — the backend's own answer to "does this discard a row" —
 * rather than derived from `kind`, so the flag cannot drift from the spec table
 * that decides it; it is sent on every confirm because the endpoint's model
 * forbids nothing and a client that omitted it for a creation would be relying
 * on a default that the destructive kinds depend on.
 *
 * The proposal is not editable anywhere in this panel, so the payload goes back
 * byte for byte as the backend published it — the endpoint re-validates it as
 * untrusted input regardless, and a client that "tidied" it could only introduce
 * a 422.
 */
export function confirmBodyFor(proposal: ActionProposalRead): ConfirmActionRequest {
  const body: ConfirmActionRequest = {
    kind: proposal.kind,
    intent: proposal.intent,
    payload: proposal.payload,
    confirm_destructive: proposal.destructive,
  }
  if (proposal.target_id !== null) body.target_id = proposal.target_id
  return body
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

  // Read only from a mutation's `onSuccess`, to invalidate the surface the
  // write just landed in. Present from the first render — TanStack Query
  // resolves it during render — so the callbacks below can close over it.
  const queryClient = useQueryClient()

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
  /** Bumped per turn; a proposal belonging to an older one is discarded. */
  const generationRef = useRef(0)
  /**
   * The propose request in flight.
   *
   * Its own controller rather than a second slot in `requestRef`, because the
   * two overlap: the routing call is finished by the time a proposal is asked
   * for, and reusing the one ref would abort the proposal the moment a *retry*
   * re-used the routing ref.
   */
  const proposalRequestRef = useRef<AbortController | null>(null)
  /** The confirm request in flight. Aborted on unmount, like the others. */
  const confirmRequestRef = useRef<AbortController | null>(null)

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
    // A proposal belongs to the turn that asked for it. Dropping the request and
    // the pending one together means a stale proposal can neither arrive late
    // nor sit on screen while the reader is already talking about something else.
    proposalRequestRef.current?.abort()
    proposalRequestRef.current = null
    const controller = new AbortController()
    requestRef.current = controller
    pendingRef.current = { text, retryable: true, at }
    previousRef.current = accepted ?? null
    generationRef.current += 1

    moveTo('processing')
    apply({
      interimTranscript: '',
      lastTranscript: text,
      lastDecision: null,
      lastAction: null,
      suggestion: null,
      error: null,
      proposal: null,
      actionOutcome: null,
      actionError: null,
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

      // Only an accepted turn is worth asking about. The proposal layer runs the
      // *same* classifier over the *same* string against the *same* threshold, so
      // an `uncertain`, `out_of_scope` or `generation_unavailable` decision
      // could only ever come back as a refusal — a request spent to be told
      // nothing, on every out-of-scope sentence a user ever types.
      if (decision.status === 'accepted') {
        proposeFor(request.text)
      }
    },
    onError: (cause) => {
      // An abort is our own doing — unmount, or the user cancelled — and must
      // not be reported as a failure they did not cause.
      if (!mountedRef.current || isAbortError(cause) || phaseRef.current !== 'processing') return
      failTurn(describeRoutingFailure(cause))
    },
  })

  /**
   * Asks what NEXUS would do about the sentence this turn carried.
   *
   * The same single utterance and nothing else: the proposal layer is the same
   * classifier, so a history would enlarge the request without informing it and
   * would send the reader's earlier sentences to the backend for no gain. The
   * zone is the caller's own, read through the planner's helper, because "by
   * friday" has to cut on the same instant a day planned on the board would.
   */
  function proposeFor(text: string): void {
    proposalRequestRef.current?.abort()
    const controller = new AbortController()
    proposalRequestRef.current = controller
    proposeMutation.mutate({ text, signal: controller.signal, generation: generationRef.current })
  }

  const proposeMutation = useMutation<ProposeActionRead, Error, ProposalRequest>({
    mutationFn: ({ text, signal }) => proposeAction(text, { signal, tz: plannerLocalTimezone() }),
    // Same reasoning as the routing call: a 503 means the checkpoint is not
    // loaded and asking again in two seconds produces the same 503, only slower.
    retry: false,
    onSuccess: (answer, request) => {
      if (!mountedRef.current || request.generation !== generationRef.current) return
      // **A refusal is a 200, and the common case.** Most things a person says
      // to an assistant are not a row to create, so `proposed: false` is the
      // ordinary answer rather than a failure: nothing is set, nothing is
      // shown, and the routing decision this arrived after is untouched. The
      // refusal's prose is deliberately not rendered — a panel that reddened
      // itself for a sentence that was not a create request would cry wolf on
      // nearly every turn.
      if (!answer.proposed || answer.proposal === null) return
      apply({ proposal: answer.proposal, actionError: null })
    },
    onError: (cause, request) => {
      if (!mountedRef.current || isAbortError(cause)) return
      // A failure belonging to a turn the reader has already moved past is not
      // news about this one, and putting it on screen would blame the sentence
      // in front of them for something the previous sentence did.
      if (request.generation !== generationRef.current) return
      // **The lifecycle does not move.** The turn that produced this failed
      // nowhere; it routed, and its routing decision is on screen. Moving to
      // `error` here would replace a good answer with a failure of an optional
      // second question, and would hand the panel's retry button — which re-runs
      // a *turn* — to a failure that re-running a turn would not fix.
      apply({ proposal: null, actionError: describeActionFailure(cause, 'propose') })
    },
  })

  const confirmMutation = useMutation<ConfirmActionRead, Error, ConfirmRequest>({
    mutationFn: ({ body, signal }) => confirmAction(body, signal),
    // Not because a retry would be wrong but because it would be *invisible*: the
    // endpoint already answers a repeat with `no_op` and the row that is already
    // there, so a client-side retry would turn one visible "no second copy" into
    // a second request the reader never asked for.
    retry: false,
    onSuccess: (result) => {
      if (!mountedRef.current) return
      apply({ proposal: null, actionOutcome: result, actionError: null })

      // Only a request that actually moved the row invalidates anything. A
      // `no_op` found the desired state already there, and refetching every
      // project and task list on the strength of it would be work with no
      // reason behind it.
      const root = CACHE_ROOT_BY_KIND[result.kind]
      if (result.applied && root !== undefined) {
        void queryClient.invalidateQueries({ queryKey: root() })
      }
    },
    onError: (cause) => {
      if (!mountedRef.current || isAbortError(cause)) return
      // The proposal stays put, so the dialog stays open with both answers back.
      // A failure that closed the dialog would take away the reader's only way
      // to try again.
      apply({ actionError: describeActionFailure(cause, 'confirm') })
    },
  })

  /** Carries out the pending proposal, or does nothing when there is none. */
  function confirmProposal(): void {
    const proposal = lifecycle.proposal
    if (proposal === null) return
    // Guarded by the mutation's own pending flag rather than by local state:
    // TanStack returns that to `false` the moment the promise settles, on the
    // success path and the failure path alike, so there is no branch in this
    // file that can leave the dialog's buttons switched off for good.
    if (confirmMutation.isPending) return
    const controller = new AbortController()
    confirmRequestRef.current = controller
    confirmMutation.mutate({ body: confirmBodyFor(proposal), signal: controller.signal })
  }

  /**
   * Discards the proposal. Makes no request — nothing has been written yet, so
   * there is nothing to undo, and sending a "cancel" would be inventing an
   * endpoint that does not exist.
   */
  function cancelProposal(): void {
    proposalRequestRef.current?.abort()
    proposalRequestRef.current = null
    apply({ proposal: null, actionError: null })
  }

  function dismissActionOutcome(): void {
    apply({ actionOutcome: null })
  }

  function dismissActionError(): void {
    apply({ actionError: null })
  }

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
      // A new utterance supersedes the last turn's pending agreement, exactly
      // as it supersedes its decision: leaving a confirm dialog open across a
      // new turn would ask the reader to agree to a sentence they have since
      // replaced.
      proposal: null,
      actionOutcome: null,
      actionError: null,
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
    // Every request in flight, not just the turn's. Cancelling and leaving a
    // proposal or a confirmation to land afterwards would put a dialog in front
    // of a panel the reader has already walked away from.
    proposalRequestRef.current?.abort()
    proposalRequestRef.current = null
    confirmRequestRef.current?.abort()
    confirmRequestRef.current = null
    pendingRef.current = null
    previousRef.current = null
    moveTo('idle')
    apply({ error: null, interimTranscript: '', proposal: null })
  }

  function clearConversation(): void {
    clearTurns()
    pendingRef.current = null
    previousRef.current = null
    // Bumped as well as cleared, so a proposal for the turn being erased cannot
    // land after the erase and repopulate a conversation the reader has just
    // emptied.
    generationRef.current += 1
    proposalRequestRef.current?.abort()
    proposalRequestRef.current = null
    const phase = phaseRef.current
    if (phase === 'idle' || phase === 'error') moveTo('idle')
    apply({
      lastTranscript: '',
      interimTranscript: '',
      lastDecision: null,
      lastAction: null,
      suggestion: null,
      error: null,
      proposal: null,
      actionOutcome: null,
      actionError: null,
    })
  }

  // Arms on mount, disarms and releases on unmount. Nothing here sets state, so
  // it is not a render loop; it stops a microphone, a voice and the three
  // requests that would otherwise outlive the panel.
  //
  // The arming half is not decoration. StrictMode mounts, runs the cleanup and
  // runs the effect again, so a flag that is only ever cleared would stay false
  // for the life of the panel and every `mountedRef` guard below would refuse
  // to do its job — no turn recorded, no error surfaced, no way out of
  // `speaking`.
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      recogniserRef.current?.abort()
      recogniserRef.current = null
      cancelSpeech()
      requestRef.current?.abort()
      requestRef.current = null
      proposalRequestRef.current?.abort()
      proposalRequestRef.current = null
      confirmRequestRef.current?.abort()
      confirmRequestRef.current = null
    }
  }, [])

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
    proposal: lifecycle.proposal,
    // Read from the mutation rather than mirrored into lifecycle state: TanStack
    // owns it, so it returns to `false` on every path out of the promise and
    // this file has no branch that could leave the dialog's buttons dead.
    confirming: confirmMutation.isPending,
    actionOutcome: lifecycle.actionOutcome,
    actionError: lifecycle.actionError,
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
    confirmProposal,
    cancelProposal,
    dismissActionOutcome,
    dismissActionError,
    cancel,
    clearConversation,
  }
}