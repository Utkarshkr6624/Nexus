/**
 * Typed wrappers over the Phase 11 intent-classifier endpoints.
 *
 * Two calls, both thin. Everything interesting about them belongs to the
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
 *
 * **The timeout is set explicitly rather than inherited.** A classification is a
 * CPU-bound forward pass on a worker thread; `ROUTING_TIMEOUT_MS` is the
 * deadline the assistant is willing to hold a turn open for, and it is the same
 * number the UI shows as "still working on it". Letting the client default
 * apply would leave the two disagreeing the day either constant moved.
 *
 * **Neither call retries.** The shared query client already refuses to retry a
 * mutation, and repeating the request here would be wrong for both of them:
 * `routeUtterance` failing with 503 `ml_unavailable` means the checkpoint is
 * not loaded on the server, and asking again a second later produces the same
 * 503 while making the user wait twice as long for the same honest answer;
 * `fetchMlStatus` is a query, so TanStack Query owns its own retry policy and
 * duplicating it here would be two policies disagreeing. A caller that wants a
 * second attempt makes one — a turn is retried by the user, not by the client.
 */
import { ROUTING_TIMEOUT_MS } from '@/types/ml'
import { apiClient } from '@/lib/api-client'
import type { MlStatusRead, RoutingDecisionRead } from '@/types/ml'

/** Resolved against the `/api/v1` base URL configured on the client. */
export const ML_ENDPOINTS = {
  route: '/ml/route',
  status: '/ml/status',
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
      // Both `/ml` routes sit behind `AuthenticatedUser` and `analytics.read`:
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