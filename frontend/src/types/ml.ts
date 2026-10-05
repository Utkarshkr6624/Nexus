/**
 * Wire types for the Phase 11 intent classifier (`/api/v1/ml`) and the Phase 13
 * action surface it feeds (`/api/v1/ml/action/...`).
 *
 * The routing types mirror `backend/app/schemas/ml.py` and the action types
 * mirror `backend/app/schemas/actions.py`, both field for field and including
 * nullability. As with `api.ts`, a mismatch here fails at runtime rather than
 * compile time, because the response body is only typed by convention.
 *
 * The single most important thing to understand about these types: the
 * `RoutingStatus` values are **outcomes, not classes**. A `generation_unavailable`
 * answer carries an `intent` of `code_assist` — the classifier recognised the
 * request correctly — and the destination says NEXUS has nothing that can serve
 * it. Reading `intent` alone turns a correct recognition into an apparent
 * failure, so branch on `status` and read `intent` as the evidence behind it.
 *
 * The second thing worth knowing is that **a refusal is a response, not an
 * error**: `ProposeActionRead` is one shape with a `proposed` flag and exactly
 * one of `proposal` / `refusal` filled in, so "NEXUS read that and would not
 * act" arrives as a 200 and must not be rendered as a fault.
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

/* ------------------------------------------------------- propose → confirm --
 *
 * The two calls that take an utterance further than a destination:
 * `POST /ml/action/propose` describes the action a sentence asks for, and
 * `POST /ml/action/confirm` is the only path in NEXUS that executes one. These
 * mirror `backend/app/schemas/actions.py`.
 */

/**
 * Every write the proposal layer can name, across every surface.
 *
 * Mirrors `app.ml.actions.proposals.ActionKind`: not only the five creations the
 * assistant used to be limited to, but the updates, status transitions,
 * scheduling, tagging, publishing, archiving and **single-row deletions** as
 * well. The endpoint parses this value as an enum against a frozen table, so
 * anything outside the set is a 422 before the handler runs. Kept a union rather
 * than `string` so a typo in a `switch` is a compile error instead of a branch
 * that silently never fires — which matters more now that the set is this wide.
 *
 * **Destructive kinds are here, and that is not the same thing as being
 * unguarded.** Deletion is safe because of four separate guarantees and not
 * because of a list this file maintains: every write requires confirmation
 * (`requires_confirmation` is a property that is always `true`), a destructive
 * kind carries `destructive: true` and the dialog says so in as many words, a
 * bulk request naming a whole collection is refused rather than executed, and
 * the id is re-resolved through an owner-scoped lookup so a forged one is a 404
 * before anything is written. Which members are destructive is decided by the
 * backend's spec table and published on the proposal as a boolean; a client
 * branches on that boolean and never on this list.
 */
export type ActionKind =
  // Tasks
  | 'create_task'
  | 'update_task'
  | 'delete_task'
  | 'complete_task'
  | 'set_task_status'
  | 'schedule_task'
  | 'unschedule_task'
  | 'tag_task'
  | 'untag_task'
  // Projects
  | 'create_project'
  | 'update_project'
  | 'delete_project'
  | 'set_project_status'
  // Knowledge
  | 'create_note'
  | 'update_note'
  | 'delete_note'
  | 'archive_note'
  | 'publish_note'
  | 'create_bookmark'
  | 'delete_bookmark'
  | 'create_concept'
  | 'delete_concept'
  | 'create_link'
  | 'delete_link'
  // Learning
  | 'create_learning_goal'
  | 'update_learning_goal'
  | 'complete_learning_goal'
  | 'delete_learning_goal'
  | 'create_skill'
  | 'delete_skill'
  // Planner
  | 'create_event'
  | 'update_event'
  | 'delete_event'
  | 'create_session'
  | 'delete_session'
  // Developer
  | 'create_repository'
  // Account
  | 'update_profile'

/**
 * The closed vocabulary a refusal can report.
 *
 * Mirrors `app.ml.actions.proposals.ProposalReason`. Shipped beside the prose
 * because a refusal is only actionable if it names *which* failure it was:
 * `context_missing` is the one a dialog can offer a retry for (supply a project
 * and ask again), while `unsupported_intent` means the sentence asked for
 * something NEXUS does not do and no retry will change that. `reason` is the
 * sentence to show; this is the term to branch on.
 *
 * The distinction that matters most since deletion was allowed is the pair
 * `target_ambiguous` / `target_not_found` against `destructive_request`. A
 * reference that matched nothing, or several rows at once, is **its own refusal**
 * and never a best guess about which row was meant. `destructive_request` is now
 * narrower than the name suggests: it reports a request whose *scope* NEXUS will
 * not act on from one sentence — "delete all my tasks" — and never an ordinary
 * single-row delete, which is a proposal like any other.
 *
 * `task_reference_ambiguous` and `task_reference_not_found` are the older,
 * task-specific spellings of the two general terms and are retained because the
 * backend still emits them.
 */
export type ProposalReasonCode =
  | 'context_missing'
  | 'destructive_request'
  | 'entity_not_recognised'
  | 'field_not_recoverable'
  | 'payload_invalid'
  | 'target_ambiguous'
  | 'target_not_found'
  | 'task_reference_ambiguous'
  | 'task_reference_not_found'
  | 'title_not_recoverable'
  | 'unsupported_intent'
  | 'verb_not_recovered'

/**
 * One field NEXUS read out of the utterance, and the span it was read from.
 *
 * Published so a confirm dialog can show its own reasoning — *"due Friday,
 * matched 'for friday'"* — instead of asking the user to trust a bare value.
 * Every entry carries a non-empty `matched_text`, which is the guarantee that
 * the extractor *found* the value rather than invented it; a dialog rendering
 * these can show a near miss and let the user correct it.
 */
export interface ExtractArgumentRead {
  /** The payload field this argument fills. */
  field: string
  /** The value as text, which is what a dialog renders. */
  value: string
  /** The span of the utterance the value was read from. */
  matched_text: string
  /** The extraction rule that consumed that span. */
  rule: string
}

/**
 * One action the user is being asked to agree to, before anything is written.
 *
 * **`summary` is the reason this type exists.** The backend composed it at
 * proposal time from the winning intent and the extracted arguments, and it says
 * what will change, under what name, and when — so it is the sentence to render.
 * A client that reassembles its own sentence out of `payload` is a second
 * description of the same write that can drift from the first one. For a delete
 * that sentence is the only place the user is told what is about to be lost, so
 * it is rendered verbatim there too.
 *
 * `payload` beside it is what a confirm sends back, and `target_id` is the row a
 * non-creation acts on. `service`, `module`, `entrypoint` and `payload_schema`
 * describe the call that *will* run; the client sends none of them, because
 * `kind` alone is what the endpoint re-derives everything from — a body cannot
 * name a different service.
 *
 * `requires_confirmation` and `destructive` are published so a dialog can rely on
 * the invariants rather than hard-code them. The first is a property that is
 * always `true`: there is no path from an utterance to a write that skips this
 * step. The second is a **real field, derived from the backend's spec table**,
 * and it is `true` for the kinds that delete a row — which is what lets the
 * dialog warn that the write cannot be undone and lets the confirm endpoint
 * demand the extra acknowledgement in {@link ConfirmActionRequest}.
 */
export interface ActionProposalRead {
  kind: ActionKind
  /** Always a member of the fourteen-class taxonomy, as on the routing response. */
  intent: IntentName
  confidence: number
  /** The one sentence the user checks before agreeing. Render it as written. */
  summary: string
  /** Always `true`. There is no path that skips confirmation. */
  requires_confirmation: boolean
  /**
   * Whether this kind discards a row, from the backend's spec table.
   *
   * `true` for the `delete_*` kinds. A dialog renders the irreversibility in
   * words and switches its confirm button to the destructive variant; it must
   * not infer this from `kind`, because which kinds are destructive is the
   * backend's decision and can change without a client release.
   */
  destructive: boolean
  /** The capability the confirming call will be re-checked against. */
  permission: string
  /** Class name of the service that will run, e.g. `ProjectService`. */
  service: string
  /** Import path of that service's module. */
  module: string
  /** The method that will be called on it. */
  entrypoint: string
  /** The Pydantic model the payload validates against, e.g. `ProjectCreate`. */
  payload_schema: string
  /**
   * The validated payload, JSON-ready.
   *
   * Free-form by design: the schema it must satisfy is a property of `kind`, so
   * the backend publishes the object rather than a union of every kind's schema.
   * It is taken back verbatim on confirm, where the endpoint validates it as
   * untrusted input — which is what makes a field the user edited in the dialog
   * checked rather than honoured.
   */
  payload: Record<string, unknown>
  /** `null` for a creation; the row any other write acts on, deletes included. */
  target_id: string | null
  /** The caller's own name for `target_id`, so a dialog can quote it. */
  target_label: string | null
  /** Every extracted field with its provenance, in extraction order. */
  arguments: ExtractArgumentRead[]
  /** Softer observations, e.g. a date phrase that was seen but not resolved. */
  notes: string[]
}

/**
 * No action, and the closed-vocabulary reason why.
 *
 * **This is a 200, not an error.** "NEXUS read that and would not act on it" is
 * an ordinary answer about an ordinary sentence, and rendering it as a fault
 * teaches users that the assistant is broken when it is working. `reason` is the
 * prose to show; `reason_code` is the term to branch on, because only some of
 * these reasons are worth asking the user to change anything about.
 *
 * `arguments` is still populated when NEXUS got partway — it is what a dialog
 * shows to make a near miss legible instead of just opaque.
 */
export interface ProposalRefusalRead {
  /** The kind NEXUS understood but would not propose, when it knows one. */
  kind: ActionKind | null
  intent: IntentName
  confidence: number
  reason_code: ProposalReasonCode
  /** Human-readable explanation, safe to render to a user. */
  reason: string
  /** What NEXUS did manage to read, so the dialog can show the near miss. */
  arguments: ExtractArgumentRead[]
  /** Softer observations, if any. */
  notes: string[]
}

/**
 * The answer to "what would NEXUS do about this sentence".
 *
 * **Branch on `proposed`, and treat `proposal === null` as the ordinary case
 * rather than as a failed request.** Exactly one of `proposal` and `refusal` is
 * populated, never both and never neither; `proposed` is redundant with that on
 * purpose, so a caller never has to test two nullable fields to find out whether
 * it may render a confirm button. The top-level `intent`/`confidence` are the
 * classifier's answer on their own — they are present on a refusal too, which is
 * what lets the existing "Go to X" routing survive an unproposable sentence.
 */
export interface ProposeActionRead {
  proposed: boolean
  intent: IntentName
  confidence: number
  /** `null` on every refusal. */
  proposal: ActionProposalRead | null
  /** `null` whenever a proposal came back. */
  refusal: ProposalRefusalRead | null
}

/**
 * What a caller submits to carry out a proposal it has shown the user.
 *
 * **Every field is untrusted, and the backend checks all of them.** `kind` names
 * the frozen spec the endpoint looks up, so the payload schema, the permission
 * and the entry point are not this body's to choose; `intent` must be an intent
 * the kind can only have come from; the payload is re-validated with unknown
 * keys rejected; `target_id` is re-resolved through an owner-scoped lookup, so
 * another account's id is a 404 before any row is written; and a kind whose
 * spec says `destructive` is refused outright unless the extra acknowledgement
 * below is set.
 *
 * The request model forbids extra keys, so this object is the whole body — a
 * client cannot smuggle a hint past the endpoint.
 */
export interface ConfirmActionRequest {
  kind: ActionKind
  /** The intent the proposal reported. A disagreement is a 422. */
  intent: IntentName
  /** The proposal's payload, possibly edited by the user. Validated as untrusted input. */
  payload: Record<string, unknown>
  /**
   * The user's second acknowledgement of an irreversible write.
   *
   * Always sent, and set from the proposal's `destructive` flag rather than from
   * `kind`: the flag is the backend's own answer to "does this discard a row",
   * so the client cannot drift from it. A destructive kind confirmed without
   * this is a 422 — which is what makes it a real barrier rather than a
   * decorative one, since the flag travels from the endpoint that proposed the
   * action to the endpoint that would carry it out.
   */
  confirm_destructive: boolean
  /** `null` unless the action acts on a row, e.g. `complete_task` or `delete_task`. */
  target_id?: string | null
}

/**
 * What kind of row was written.
 *
 * The backend declares this as `str`, but every value comes from a fixed
 * kind→entity table rather than from free text, so it is narrowed here: a switch
 * on `entity` — the branch that decides which query key to invalidate — is then
 * checkable at compile time.
 */
export type ConfirmEntity =
  | 'bookmark'
  | 'concept'
  | 'event'
  | 'learning_goal'
  | 'link'
  | 'note'
  | 'profile'
  | 'project'
  | 'repository'
  | 'session'
  | 'skill'
  | 'task'

/**
 * What actually happened, said by the service that did it.
 *
 * **`outcome` is the field to branch on, and `no_op` is a success, not a
 * failure.** A replayed confirm — a double-click, or a retry after a timeout —
 * reports the row that is already there with `applied: false`, because the
 * desired state *was* reached by the first call and telling the user their
 * second press failed would be a lie about a system that worked.
 *
 * `deleted` is a first-class outcome rather than an error: a delete is the
 * service reporting the row it removed, and it renders through the same
 * sentence the backend composed. There is no `undo` to pair it with — that is
 * why the dialog warned before the press, not after it.
 *
 * `message` is the sentence to show. It is written from the service's return
 * value rather than from what the caller hoped for, so render it instead of
 * composing one: "Created the project 'HelloWorld'." is a fact about the
 * database, and a client-assembled equivalent is not.
 */
export interface ConfirmActionRead {
  kind: ActionKind
  entity: ConfirmEntity
  /** The row's identifier, as the service returned it. */
  entity_id: string
  outcome: 'created' | 'deleted' | 'no_op' | 'updated'
  /** Whether *this request* changed anything. `false` on a replay. */
  applied: boolean
  /** One truthful sentence describing what happened. */
  message: string
}