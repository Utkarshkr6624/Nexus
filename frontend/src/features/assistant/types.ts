/**
 * The voice assistant's own vocabulary: what it can be doing, what can go
 * wrong, and what one turn of a conversation is.
 *
 * These are deliberately separate from `@/types/ml`, which describes the
 * backend's classifier and action contracts. The distinction matters: the
 * classifier is single-utterance and stateless, and the assistant is a session
 * with history and a lifecycle. Conflating them is how a conversation manager
 * ends up quietly sending history to a model that only ever wanted one sentence.
 */

import type { RoutingDecisionRead } from '@/types/ml'

/**
 * The assistant's lifecycle, in the order the states actually occur.
 *
 * `unsupported` is terminal and resolved at mount: a browser with no
 * speech-recognition implementation can never leave it, so it is a state the UI
 * renders rather than an error the UI catches. `idle` is the only state in which
 * a new utterance may begin.
 */
export type VoiceState =
  | 'unsupported'
  | 'idle'
  | 'listening'
  | 'processing'
  | 'speaking'
  | 'error'

/**
 * A failure the user can be told something useful about.
 *
 * Every member is a *recognised* condition with copy written for it. There is no
 * `unknown` case by design: an unrecognised condition maps to `recognition`
 * rather than surfacing a raw browser code, because "network" or
 * "service-not-allowed" shown to a user is a worse experience than an honest
 * "speech recognition failed".
 */
export type VoiceErrorCode =
  /** The user or the browser refused microphone access. */
  | 'permission_denied'
  /** No microphone, or the OS would not give us one. */
  | 'microphone_unavailable'
  /** Listening started but no speech arrived. Not an error condition. */
  | 'no_speech'
  /** The recogniser failed for a reason we cannot name more precisely. */
  | 'recognition_failed'
  /** Recognition is not available in this browser at all. */
  | 'not_supported'
  /** The request exceeded the assistant's own deadline. */
  | 'timeout'
  /** The backend could not classify — 503 `ml_unavailable`, most often. */
  | 'classifier_unavailable'
  /** The session is gone or was never there — a 401. */
  | 'not_authenticated'
  /**
   * The account is signed in but not allowed — a 403.
   *
   * Two different denials share this one code: the `/ml` route gate
   * (`analytics.read`), and the action's own capability on
   * `/ml/action/confirm` (`projects.write`, `tasks.write`, …). They are one code
   * because they are one fact to the reader — this account may not do this —
   * and the *messages* differ, because what to do about it differs.
   */
  | 'not_permitted'
  /** The backend answered something the assistant cannot use. */
  | 'invalid_response'
  /** Speech synthesis could not start or was cut off mid-utterance. */
  | 'synthesis_failed'

/** A failure with the message the UI shows. */
export interface VoiceError {
  code: VoiceErrorCode
  /** User-facing copy. Never a stack trace, a browser code or a URL. */
  message: string
  /** Whether retrying the same action could plausibly succeed. */
  retryable: boolean
}

/**
 * One exchange, from utterance to routing decision.
 *
 * `transcript` is what the recogniser heard and `decision` is what the backend
 * made of it; keeping both means the UI can always show the user exactly what
 * was understood, which is the difference between a wrong answer and a
 * *visible* wrong answer.
 */
export interface VoiceTurn {
  id: string
  /** The recognised utterance, trimmed. Empty when recognition heard nothing. */
  transcript: string
  /** The classifier's answer, or `null` when the turn failed before that. */
  decision: RoutingDecisionRead | null
  /** The error that ended this turn, or `null` when it completed. */
  error: VoiceError | null
  /** Epoch milliseconds, for ordering and for the "x seconds ago" affordance. */
  at: number
}

/**
 * The validated NEXUS call an accepted turn maps onto.
 *
 * **Routing, not a to-do list.** This names a destination the reader can be sent
 * to; it does not name a row to create. The action a sentence actually asks for
 * arrives separately, as an `ActionProposalRead` from `POST /ml/action/propose`,
 * and is only ever carried out after the reader has agreed to it. Keeping the
 * two apart is the point: "this goes to Projects" and "this creates a project"
 * are different claims, and one must not be read as the other.
 */
export interface VoiceAction {
  /** The service the classifier named, e.g. `TaskService`. */
  service: string
  /** The call it would make, e.g. `TaskService.list`. */
  entrypoint: string
  /** The API router behind it, e.g. `api/v1/tasks`. */
  destination: string
}

/**
 * Conversation context handed to the UI.
 *
 * NEXO's classifier classifies **one utterance**. It has no dialogue state and
 * cannot be given any without changing what it is, so `previousIntent` is used
 * only to offer a suggestion when the current turn comes back `uncertain` —
 * never to rewrite the text sent for classification, and never to influence the
 * model's prediction.
 */
export interface VoiceContext {
  /** The route the user was on, e.g. `/analytics`. */
  pathname: string
  /** The human label for that route, e.g. `Analytics`. */
  pageLabel: string
  /** The most recent accepted intent in this conversation, if any. */
  previousIntent?: string
}

/** How many turns to keep. Bounded so a long session cannot grow without limit. */
export const MAX_CONVERSATION_TURNS = 20

/** How long a single recognition may run before the assistant gives up on it. */
export const RECOGNITION_TIMEOUT_MS = 15_000