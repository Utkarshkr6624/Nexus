/**
 * Wire types for the Phase 11 intent classifier (`/api/v1/ml`).
 *
 * These mirror `backend/app/schemas/ml.py` exactly. As with `api.ts`, a
 * mismatch here fails at runtime rather than compile time, because the response
 * body is only typed by convention.
 *
 * The single most important thing to understand about these types: the
 * `RoutingStatus` values are **outcomes, not classes**. A `generation_unavailable`
 * answer carries an `intent` of `code_assist` — the classifier recognised the
 * request correctly — and the destination says NEXUS has nothing that can serve
 * it. Reading `intent` alone turns a correct recognition into an apparent
 * failure, so branch on `status` and read `intent` as the evidence behind it.
 */

/**
 * Where a predicted intent is allowed to go.
 *
 * Mirrors `ml.datasets.taxonomy.DestinationKind`. `large_model` is the
 * generation-only kind, and Phase 12 routes it nowhere: NEXO runs no generative
 * model, so `large_model` means `large-model:unavailable`, not "escalate".
 */
export type DestinationKind = 'router' | 'large_model' | 'fallback'

/**
 * What NEXUS decided to do with the utterance.
 *
 * - `accepted` — confident enough to name an existing service.
 * - `uncertain` — a prediction came back but not strongly enough to act on;
 *   `target` is always `null`.
 * - `out_of_scope` — NEXUS has no surface for this (`destination: 'abstain'`).
 * - `generation_unavailable` — the request needs free-form generation, which
 *   NEXO does not perform (`destination: 'large-model:unavailable'`).
 */
export type RoutingStatus =
  | 'accepted'
  | 'out_of_scope'
  | 'uncertain'
  | 'generation_unavailable'

/**
 * One of the fourteen classes the Phase 10 model predicts.
 *
 * Kept as a union rather than `string` so a typo in a comparison is a compile
 * error. `out_of_scope` is a real class the model is scored on, not an absence
 * of one.
 */
export type IntentName =
  | 'account_admin'
  | 'analytics_insight'
  | 'career_track'
  | 'code_assist'
  | 'deep_reasoning'
  | 'developer_intel'
  | 'knowledge_capture'
  | 'knowledge_lookup'
  | 'learning_track'
  | 'out_of_scope'
  | 'project_manage'
  | 'risk_query'
  | 'schedule_plan'
  | 'task_manage'

/** The existing NEXUS service an accepted intent lands on. */
export interface ServiceTargetRead {
  /** Class name, e.g. `TaskService`. */
  service: string
  /** Import path, e.g. `app.services.task_service`. */
  module: string
  /** The call that starts the work, e.g. `TaskService.list`. */
  entrypoint: string
}

/** A runner-up class and the probability the model gave it. */
export interface IntentAlternative {
  intent: IntentName
  confidence: number
}

/** Request body for `POST /api/v1/ml/route`. */
export interface RouteUtterancePayload {
  /** Raw utterance. The backend validates length and screens credential shapes. */
  text: string
}

/**
 * How long the client will hold a turn open waiting for a classification.
 *
 * Lives with the wire contract rather than in the voice feature because it is a
 * property of the endpoint, not of the microphone: `services/ml.ts` sets it on
 * every request so the number the timeout enforces and the number a caller
 * reasons about can never drift apart.
 */
export const ROUTING_TIMEOUT_MS = 30_000

/** Response body for `POST /api/v1/ml/route`. */
export interface RoutingDecisionRead {
  intent: IntentName
  confidence: number
  threshold: number
  status: RoutingStatus
  destination: string
  destination_kind: DestinationKind
  /** `null` for every status other than `accepted`. */
  target: ServiceTargetRead | null
  reason: string
  alternatives: IntentAlternative[]
}

/** One entry of the label set, as the router understands it. */
export interface IntentRouteRead {
  intent: IntentName
  description: string
  destination: string
  destination_kind: DestinationKind
  /** `null` for a class with no router behind it. */
  service: string | null
  /** `null` when there is no first call to make. */
  entrypoint: string | null
}

/** What actually loaded, from where, and on what device. */
export interface ModelIdentityRead {
  base_model: string
  architecture: string
  device: string
  label_count: number
  max_sequence_length: number
  parameter_count: number
  checkpoint: string
  load_seconds: number
}

/**
 * Response body for `GET /api/v1/ml/status`.
 *
 * `available: false` is a normal, reportable state, not an error — the endpoint
 * answers 200 so a health check can distinguish "ML is off" from "the service is
 * down". `unavailable_reason` is the machine-readable term the backend resolved
 * (`checkpoint_missing`, `runtime_missing`, `disabled`, …) and is `null` when
 * the classifier can serve, so a caller branches on `available` and never on the
 * presence of a string.
 *
 * `taxonomy_version` is published so a client can tell which label set it is
 * being offered: a fourteen-intent response is a different surface from a
 * thirteen-intent one, and a UI built from this response should not describe a
 * capability the router no longer has.
 */
export interface MlStatusRead {
  enabled: boolean
  available: boolean
  /** `null` when the classifier can serve. */
  unavailable_reason: string | null
  model: ModelIdentityRead | null
  threshold: number
  taxonomy_version: string
  intents: IntentRouteRead[]
}