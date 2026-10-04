/**
 * Thin typed wrappers over the Phase 7 risk, recommendation and intelligence
 * endpoints.
 *
 * No React here — every function is a promise-returning call the hooks in
 * `features/risk/hooks.ts` wrap in a `queryFn`/`mutationFn`.
 *
 * Four decisions are dictated by the backend rather than chosen here:
 *
 * - **Filters are single-valued.** `QueryParams` carries one scalar per key and
 *   the client's serialiser emits `?key=value`, not a repeated key, so a list of
 *   statuses cannot be sent at all. The types say so rather than pretending
 *   otherwise, because a silently dropped filter is worse than a missing one.
 * - **Transitions are separate functions, not one with a status argument.** The
 *   router exposes `POST /risks/{id}/{acknowledge|dismiss|resolve}` and
 *   `POST /recommendations/{id}/{accept|reject|complete|view}`, and each
 *   validates its own from-state. A generic `transition(id, status)` would let a
 *   caller build a request the server will refuse.
 * - **`GET /risks/summary` is a separate function from the list.** It answers a
 *   different question at a different cost — "does anything need me", in counts —
 *   and folding it into `fetchRisks` would make the dashboard widget pay for
 *   risks it does not render.
 * - **`POST /intelligence/evaluate` is a mutation, not a query.** It writes rows
 *   and emits activity events, so its result must never be cached as though it
 *   were a read. It answers `EvaluationRead`, the same shape the run history
 *   returns, which is what lets the caller replace one summary row with another.
 *
 * Failures are not handled here. `apiClient` throws `ApiError` with the status,
 * the machine-readable code and any field-level details, and the hooks decide
 * what a 404 (someone else's id) or a 409 (a transition the lifecycle forbids)
 * means for the screen.
 */
import { apiClient, queryFrom } from '@/lib/api-client'
import type {
  EvaluationParams,
  EvaluationRead,
  RecommendationListParams,
  RecommendationListRead,
  RecommendationRead,
  RiskListParams,
  RiskListRead,
  RiskRead,
  RiskSummaryRead,
  UUIDString,
} from '@/types/risk'

export const RISK_ENDPOINTS = {
  risks: '/risks',
  risk: (id: UUIDString) => `/risks/${id}`,
  riskSummary: '/risks/summary',
  riskAcknowledge: (id: UUIDString) => `/risks/${id}/acknowledge`,
  riskDismiss: (id: UUIDString) => `/risks/${id}/dismiss`,
  riskResolve: (id: UUIDString) => `/risks/${id}/resolve`,
  recommendations: '/recommendations',
  recommendation: (id: UUIDString) => `/recommendations/${id}`,
  recommendationAccept: (id: UUIDString) => `/recommendations/${id}/accept`,
  recommendationReject: (id: UUIDString) => `/recommendations/${id}/reject`,
  recommendationComplete: (id: UUIDString) => `/recommendations/${id}/complete`,
  recommendationView: (id: UUIDString) => `/recommendations/${id}/view`,
  evaluate: '/intelligence/evaluate',
  evaluations: '/intelligence/evaluations',
} as const

/**
 * Builds a query object, dropping anything unset.
 *
 * Typed as `object` rather than `Record<string, unknown>` because an interface
 * carries no implicit index signature and would not be assignable to that record
 * — the same reason `services/knowledge.ts` does it this way.
 */
/* ----------------------------------------------------------------------- risks */

/**
 * The Risk Center list, worst first: severity descending, then newest.
 *
 * Ordering is the server's, deliberately. A client-side sort would have to know
 * the severity ladder to reproduce it, and that ladder is exactly the kind of
 * thing that must have one owner.
 */
export function fetchRisks(
  params: RiskListParams = {},
  signal?: AbortSignal,
): Promise<RiskListRead> {
  return apiClient.get<RiskListRead>(RISK_ENDPOINTS.risks, { query: queryFrom(params), signal })
}

/**
 * Compact counts for the dashboard widget.
 *
 * `needs_attention` is the one field the widget leads with, and it is computed
 * server-side so the dashboard and the Risk Center cannot disagree about what
 * counts as needing attention. Returns all-zero counts with
 * `needs_attention: false` on an account with nothing flagged — an empty state,
 * not an error.
 */
export function fetchRiskSummary(signal?: AbortSignal): Promise<RiskSummaryRead> {
  return apiClient.get<RiskSummaryRead>(RISK_ENDPOINTS.riskSummary, { signal })
}

/**
 * "Seen, still true, no longer shouting at me."
 *
 * Acknowledging is not resolving: the risk keeps being re-detected and keeps
 * updating, it simply stops competing for attention.
 */
export function acknowledgeRisk(id: UUIDString, signal?: AbortSignal): Promise<RiskRead> {
  return apiClient.post<RiskRead>(RISK_ENDPOINTS.riskAcknowledge(id), undefined, { signal })
}

/** "This does not apply to me." The risk is dismissed and will not come back. */
export function dismissRisk(id: UUIDString, signal?: AbortSignal): Promise<RiskRead> {
  return apiClient.post<RiskRead>(RISK_ENDPOINTS.riskDismiss(id), undefined, { signal })
}

/**
 * "The condition is over."
 *
 * Normally written by the detection pass when it stops re-finding a live risk.
 * The route exists so a user can close one early; the same lifecycle validation
 * applies, so an already-terminal risk is a 409 rather than a silent success.
 */
export function resolveRisk(id: UUIDString, signal?: AbortSignal): Promise<RiskRead> {
  return apiClient.post<RiskRead>(RISK_ENDPOINTS.riskResolve(id), undefined, { signal })
}

/* ------------------------------------------------------------- recommendations */

/** Open or answered suggestions, newest first, filtered by status and type. */
export function fetchRecommendations(
  params: RecommendationListParams = {},
  signal?: AbortSignal,
): Promise<RecommendationListRead> {
  return apiClient.get<RecommendationListRead>(RISK_ENDPOINTS.recommendations, {
    query: queryFrom(params),
    signal,
  })
}

/** "I will do this." Records the response and stamps `responded_at`. */
export function acceptRecommendation(
  id: UUIDString,
  signal?: AbortSignal,
): Promise<RecommendationRead> {
  return apiClient.post<RecommendationRead>(RISK_ENDPOINTS.recommendationAccept(id), undefined, {
    signal,
  })
}

/**
 * "Not for me."
 *
 * Rejection is a legitimate answer rather than a dismissal of the risk: the
 * underlying condition may still be true, and the same suggestion may be
 * re-raised later if it still applies.
 */
export function rejectRecommendation(
  id: UUIDString,
  signal?: AbortSignal,
): Promise<RecommendationRead> {
  return apiClient.post<RecommendationRead>(RISK_ENDPOINTS.recommendationReject(id), undefined, {
    signal,
  })
}

/** "Done." The terminal state a user reaches after acting on the suggestion. */
export function completeRecommendation(
  id: UUIDString,
  signal?: AbortSignal,
): Promise<RecommendationRead> {
  return apiClient.post<RecommendationRead>(RISK_ENDPOINTS.recommendationComplete(id), undefined, {
    signal,
  })
}

/* -------------------------------------------------------------- intelligence */

/**
 * Runs detection now, and returns what the pass found.
 *
 * A mutation: it upserts risks, resolves the ones that no longer apply, raises
 * recommendations and writes activity events. Two callers racing this produce one
 * consistent state rather than two partial ones, because the deduplication is
 * anchored on a partial unique index rather than on a read-then-write race.
 *
 * `window_days` is optional because the backend owns the default; sending
 * nothing asks for the engine's own window rather than one invented here.
 */
export function evaluateIntelligence(
  params: EvaluationParams = {},
  signal?: AbortSignal,
): Promise<EvaluationRead> {
  return apiClient.post<EvaluationRead>(RISK_ENDPOINTS.evaluate, undefined, {
    query: queryFrom({ window_days: params.window_days }),
    signal,
  })
}

