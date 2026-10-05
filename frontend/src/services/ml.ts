/**
 * Typed wrappers over the Phase 11 intent-classifier endpoints and the Phase 13
 * action surface they feed.
 *
 * Four calls, all thin. Everything interesting about them belongs to the
 * backend, which is where the judgement actually lives:
 *
 * - `POST /ml/route` takes exactly one utterance and answers with one intent,
 *   a confidence and a routing status. It is a **classifier**, not a language
 *   model: it cannot read a conversation, hold a turn or write a reply, and no
 *   amount of prompting will make it do so. Anything that sends it more than
 *   the current sentence is sending data to a model with no way to use it.
 * - `GET /ml/status` answers 200 even when the runtime is degraded, because the
 *   endpoint reporting that a dependency is down is itself up. Callers branch
 *   on `available`, never on the status code.
 * - `POST /ml/action/propose` and `POST /ml/action/confirm` are the pair that
 *   takes an utterance past a destination. `proposeAction` writes nothing and
 *   returns either an action to agree to or a reason there is none;
 *   `confirmAction` is the only call in NEXUS that executes a sentence, and it
 *   only ever executes one the user has been shown.
 *
 * **A refusal arrives as a 200 with `proposed: false`, not as an error.** This
 * is the single most surprising thing about the pair, and the reason
 * `proposeAction` does not translate it: "NEXUS read that and would not act on
 * it" is an ordinary answer about an ordinary sentence, so a client that puts it
 * in a `catch` block renders a working system as broken and teaches users that
 * the assistant is unreliable. Branch on `proposed`; render
 * `refusal.reason` as information. The only errors on this pair are real
 * faults — 403 for a missing permission, 422 for a rejected payload, 503 for a
 * classifier that cannot run — and each of those is a genuine exception.
 *
 * **The timeout is set explicitly rather than inherited.** A classification is a
 * CPU-bound forward pass on a worker thread; `ROUTING_TIMEOUT_MS` is the
 * deadline the assistant is willing to hold a turn open for, and it is the same
 * number the UI shows as "still working on it". Letting the client default
 * apply would leave the two disagreeing the day either constant moved. Both
 * action calls pass it too: `proposeAction` runs the identical forward pass plus
 * extraction, and `confirmAction` inherits the number rather than introducing a
 * second deadline for a panel that only knows about one.
 *
 * **None of the four retries.** The shared query client already refuses to
 * retry a mutation, and repeating any of these requests here would be wrong:
 * `routeUtterance` failing with 503 `ml_unavailable` means the checkpoint is
 * not loaded on the server, and asking again a second later produces the same
 * 503 while making the user wait twice as long for the same honest answer;
 * `fetchMlStatus` is a query, so TanStack Query owns its own retry policy and
 * duplicating it here would be two policies disagreeing; and `confirmAction` is
 * a write whose idempotence is enforced server-side anyway, so a client-side
 * retry would only double the traffic. A caller that wants a second attempt
 * makes one — a turn is retried by the user, not by the client.
 */
import { ROUTING_TIMEOUT_MS } from '@/types/ml'
import { apiClient, queryFrom } from '@/lib/api-client'
import type {
  ConfirmActionRead,
  ConfirmActionRequest,
  MlStatusRead,
  ProposeActionRead,
  RoutingDecisionRead,
} from '@/types/ml'

/**
 * Resolved against the `/api/v1` base URL configured on the client.
 *
 * The action routes sit under the same `/ml` prefix the router uses: two routers
 * may share a prefix as long as the paths below it do not collide, and
 * `/ml/action/...` collides with neither `/ml/route` nor `/ml/status` because
 * none of the four takes a parameter.
 */
export const ML_ENDPOINTS = {
  route: '/ml/route',
  status: '/ml/status',
  proposeAction: '/ml/action/propose',
  confirmAction: '/ml/action/confirm',
} as const

/**
 * Classifies one utterance.
 *
 * `text` is sent verbatim; trimming and any client-side length check belong to
 * the caller that owns the transcript, because the backend already validates
 * both and a second rule here would only be a second rule to disagree.
 */
export function routeUtterance(text: string, signal?: AbortSignal): Promise<RoutingDecisionRead> {
  return apiClient.post<RoutingDecisionRead>(
    ML_ENDPOINTS.route,
    { text },
    {
      signal,
      // Every `/ml` route sits behind `AuthenticatedUser` and `analytics.read`:
      // the router answers "which NEXUS surface does this mean", which is an
      // oracle over the taxonomy and exactly the shape of a model-extraction
      // probe. No bearer token, no answer.
      auth: true,
      timeoutMs: ROUTING_TIMEOUT_MS,
    },
  )
}

/**
 * What the classifier runtime is doing: loaded, disabled, or failed to load.
 *
 * Answers 200 in every one of those cases, so a degraded runtime is data rather
 * than an exception and a UI can explain it without an error branch.
 */
export function fetchMlStatus(signal?: AbortSignal): Promise<MlStatusRead> {
  return apiClient.get<MlStatusRead>(ML_ENDPOINTS.status, {
    signal,
    auth: true,
  })
}

/* -------------------------------------------------------- propose → confirm */

/**
 * The context an utterance cannot supply for itself.
 *
 * The endpoint resolves all three server-side, from the caller's own rows, and
 * refuses rather than guessing what it is missing — a task creation with no
 * project comes back as `context_missing`, not as a card filed under a board
 * nobody chose. So these are the only two things a client can add, and both are
 * optional because most utterances need neither.
 */
export interface ProposeActionOptions {
  /** Caller-side cancellation, e.g. from a React Query mutation. */
  signal?: AbortSignal
  /**
   * The project a created task should belong to, as a UUID string.
   *
   * Required for a task creation. Another account's project is a **404, never a
   * 403** — a 403 would confirm the id exists, which turns this endpoint into a
   * directory of other people's boards.
   */
  projectId?: string
  /**
   * The caller's IANA zone, e.g. `Europe/Berlin`.
   *
   * Resolved through the same helper the planner routes use, so a date extracted
   * from "by friday" and a day planned on the board cut on the same instant.
   * Omitted uses the deployment default.
   */
  tz?: string
}

/**
 * Asks what NEXUS would do about one sentence. Writes nothing.
 *
 * The text is sent verbatim and untransformed: the model was trained on raw
 * dataset strings, so trimming it client-side would be a distribution shift the
 * checkpoint has never seen. The backend validates length and screens
 * credential-shaped text itself, and a second rule here would only be a second
 * rule to disagree with it.
 *
 * **Resolves for a refusal as well as for a proposal.** Branch on `proposed`:
 * `false` with a populated `refusal` is a 200 that means "NEXUS read that and
 * would not act", and turning it into a thrown error would misreport a working
 * system as a broken one. What does throw are the genuine faults — 403 for a
 * missing permission, 404 for a project the caller does not own, 422 for text
 * the classifier will not accept, 503 for a runtime with no classifier loaded.
 */
export function proposeAction(
  text: string,
  options: ProposeActionOptions = {},
): Promise<ProposeActionRead> {
  const { signal, projectId, tz } = options
  return apiClient.post<ProposeActionRead>(
    ML_ENDPOINTS.proposeAction,
    // `undefined` is dropped by `JSON.stringify`, so an option the caller did
    // not set does not become an explicit `null` in the body.
    { text, project_id: projectId },
    {
      signal,
      query: queryFrom({ tz }),
      // Same gate as `/ml/route` — `AuthenticatedUser` and `analytics.read` —
      // because propose runs the identical classifier over the identical text.
      // The *action's* own capability is a second, separate check the confirm
      // endpoint performs; this call only names a thing to agree to.
      auth: true,
      timeoutMs: ROUTING_TIMEOUT_MS,
    },
  )
}

/**
 * Carries out a proposal the user has confirmed.
 *
 * The body is sent exactly as given and nothing is added to it, because every
 * field in it is checked: `kind` selects a frozen spec server-side, which is
 * what supplies the payload schema, the permission and the entry point, so a
 * body cannot reach a different service than `kind` names. The payload goes back
 * as free-form JSON for the same reason — the schema it must satisfy is a
 * property of `kind`, and unknown keys are rejected, so a field the user edited
 * in the confirm dialog is validated rather than honoured.
 *
 * **Idempotent, and a replay is a success.** A second identical confirm returns
 * `outcome: 'no_op'` with `applied: false` and the id of the row already there,
 * because the desired state was reached by the first call. That is why this
 * function never retries on its own: the server already answers the duplicate
 * correctly.
 */
export function confirmAction(
  body: ConfirmActionRequest,
  signal?: AbortSignal,
): Promise<ConfirmActionRead> {
  return apiClient.post<ConfirmActionRead>(ML_ENDPOINTS.confirmAction, body, {
    signal,
    // Behind `AuthenticatedUser` and `analytics.read`, then re-checked against
    // the action's own permission — so a 403 here is the second of those two
    // checks, and the one that names the capability the proposal asked for.
    auth: true,
    timeoutMs: ROUTING_TIMEOUT_MS,
  })
}