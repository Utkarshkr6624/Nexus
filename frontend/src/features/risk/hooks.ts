/**
 * TanStack Query bindings for the Phase 7 risk, recommendation and evaluation
 * surface.
 *
 * **`riskKeys` is the single owner of the query-key shape.** Every key lives
 * under the `['risk']` root, and every key that describes a *filtered view*
 * carries the resolved filter. The risk and recommendation trees use a
 * `list`/`detail` pair of segments so that invalidating one never sweeps the
 * other: `['risk','list']` is a prefix of the list keys only, and
 * `['risk','recommendation','list']` is a prefix of the recommendation list keys
 * only. A single flat segment per resource would have made "refresh the
 * suggestions" and "refresh the findings" the same operation, and the
 * recommendation list does not live inside the risk list.
 *
 * **The severity band is a server-side filter.** It was a client-side one until
 * `GET /risks` grew a `severity` parameter, which moved the narrowing into a
 * single indexed query and — the reason it mattered — made `total` and
 * `by_severity` describe the filtered set rather than one page of it. That was
 * the whole limitation of the earlier design: a band filter narrowed a page the
 * server had already cut, so it could not count past the page and the pager had
 * to be withdrawn. Both problems are gone; the pager now works under a band.
 * The band is still part of the query key, because two bands are two views and
 * must not share a cache entry.
 *
 * **Transitions write the row they changed straight into the cache before
 * invalidating.** The lifecycle routes answer with the updated resource, so the
 * detail entry can be replaced exactly rather than refetched, which is what
 * stops an acknowledged risk from flickering back to `active` while the list
 * refetch is in flight. The list and the summary are still invalidated, because
 * a transition removes a row from the live set and moves a band count, and only
 * the server knows which.
 *
 * **Invalidation targets are chosen by blast radius, not by symmetry.** A risk
 * transition invalidates the risk lists, the risk details and the summary, plus
 * the recommendation lists — resolving a risk expires the suggestions that
 * hung off it, so leaving those cached would show a suggestion the backend has
 * already made moot. A recommendation transition invalidates the recommendation
 * lists and the risk *details*, because `RiskRead.recommendations` is a nested
 * projection carrying its own `status`. It does not invalidate the risk
 * summary, which counts risks and not suggestions.
 *
 * `useRunEvaluation` is the one mutation that invalidates the entire tree,
 * because a detection pass rewrites risks, resolves stale ones, raises and
 * expires suggestions, appends a run summary and emits activity events at once.
 * Picking the keys it "should" affect is how a Risk Center ends up showing a
 * fresh header count over a list from before the run.
 *
 * **Retry policy is inherited.** `app/query-client.ts` refuses to retry a 4xx,
 * so the 404 from someone else's id and the 409 from an illegal transition both
 * surface on the first response and are the page's to explain.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query'

import {
  acceptRecommendation,
  acknowledgeRisk,
  completeRecommendation,
  dismissRisk,
  evaluateIntelligence,
  fetchRecommendations,
  fetchRisks,
  fetchRiskSummary,
  rejectRecommendation,
  resolveRisk,
} from '@/services/risk'
import type {
  EvaluationListParams,
  EvaluationParams,
  EvaluationRead,
  RecommendationListParams,
  RecommendationListRead,
  RecommendationRead,
  RiskListParams,
  RiskListRead,
  RiskRead,
  RiskSeverity,
  RiskSummaryRead,
  UUIDString,
} from '@/types/risk'

type Enabled = { enabled?: boolean }

/**
 * The Risk Center query.
 *
 * `severity` is the one field that is not sent to the server, and it is
 * declared here rather than smuggled in through {@link RiskListParams} so the
 * wire type and the view type cannot be confused for one another.
 */
export interface RiskQuery extends RiskListParams {
  /** Server-side band filter; sent as `?severity=`. */
  severity?: RiskSeverity
}

/**
 * Normalised, fixed-length key part. An unset param becomes `null` rather than
 * being dropped, so a params object re-created on every render hashes to the
 * same key instead of thrashing the cache.
 */
function listKeyPart(params: RiskListParams & { severity?: RiskSeverity } = {}): unknown[] {
  return [
    params.status ?? null,
    params.risk_type ?? null,
    params.severity ?? null,
    params.limit ?? null,
    params.offset ?? null,
  ]
}

function recommendationKeyPart(params: RecommendationListParams = {}): unknown[] {
  return [
    params.status ?? null,
    params.recommendation_type ?? null,
    params.limit ?? null,
    params.offset ?? null,
  ]
}

/** Stable key factory. Every key lives under the `['risk']` root. */
export const riskKeys = {
  all: () => ['risk'] as const,
  risks: () => ['risk', 'list'] as const,
  riskList: (params: RiskQuery = {}) => ['risk', 'list', ...listKeyPart(params)] as const,
  riskDetails: () => ['risk', 'detail'] as const,
  riskDetail: (id: UUIDString) => ['risk', 'detail', id] as const,
  summary: () => ['risk', 'summary'] as const,
  recommendations: () => ['risk', 'recommendation', 'list'] as const,
  recommendationList: (params: RecommendationListParams = {}) =>
    ['risk', 'recommendation', 'list', ...recommendationKeyPart(params)] as const,
  recommendationDetails: () => ['risk', 'recommendation', 'detail'] as const,
  recommendationDetail: (id: UUIDString) => ['risk', 'recommendation', 'detail', id] as const,
  evaluations: () => ['risk', 'evaluation'] as const,
  evaluationHistory: (params: EvaluationListParams = {}) =>
    ['risk', 'evaluation', params.limit ?? null] as const,
}

/**
 * The request parameters.
 *
 * The band goes on the wire. `GET /risks` grew a `severity` parameter during
 * Phase 7, so the narrowing the UI used to do in JavaScript now happens in one
 * indexed query: the band counts as a third equality column on
 * `ix_risks_owner_status_severity`, and `by_severity` and `total` describe the
 * filtered set rather than one page of it.
 */
function wireParams(params: RiskQuery): RiskListParams {
  return {
    ...(params.status ? { status: params.status } : {}),
    ...(params.risk_type ? { risk_type: params.risk_type } : {}),
    ...(params.severity ? { severity: params.severity } : {}),
    ...(params.limit !== undefined ? { limit: params.limit } : {}),
    ...(params.offset !== undefined ? { offset: params.offset } : {}),
  }
}

/* ----------------------------------------------------------------- queries */

/**
 * The Risk Center list, worst first.
 *
 * `placeholderData` keeps the previous page while a new filter or page is
 * fetched, so changing a filter or paging does not blank the list for a frame.
 * The rows on screen during that frame are the previous answer, and the page can
 * read the query's `isPlaceholderData` flag and say so, rather than presenting
 * them as the current answer for a filter that has not loaded yet.
 */
export function useRisks(
  params: RiskQuery = {},
  options: Enabled = {},
): UseQueryResult<RiskListRead> {
  return useQuery({
    queryKey: riskKeys.riskList(params),
    queryFn: ({ signal }) => fetchRisks(wireParams(params), signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

/**
 * The dashboard's compact counts.
 *
 * A separate query from the list because it answers a different question at a
 * different cost, and a dashboard widget should not have to fetch a page of
 * risks to know whether anything needs it.
 */
export function useRiskSummary(options: Enabled = {}): UseQueryResult<RiskSummaryRead> {
  return useQuery({
    queryKey: riskKeys.summary(),
    queryFn: ({ signal }) => fetchRiskSummary(signal),
    enabled: options.enabled,
  })
}

/** Open or answered suggestions, newest first. */
export function useRecommendations(
  params: RecommendationListParams = {},
  options: Enabled = {},
): UseQueryResult<RecommendationListRead> {
  return useQuery({
    queryKey: riskKeys.recommendationList(params),
    queryFn: ({ signal }) => fetchRecommendations(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

/* --------------------------------------------------------------- mutations */

/** Replaces a detail entry with the row the transition returned. */
function cacheRiskTransition(queryClient: QueryClient, updated: RiskRead): void {
  queryClient.setQueryData(riskKeys.riskDetail(updated.id), updated)
  void queryClient.invalidateQueries({ queryKey: riskKeys.risks() })
  void queryClient.invalidateQueries({ queryKey: riskKeys.riskDetails() })
  void queryClient.invalidateQueries({ queryKey: riskKeys.summary() })
  // Resolving a risk expires the suggestions that hung off it, so the
  // recommendation lists are refreshed by every transition rather than only by
  // the one that can cause it.
  void queryClient.invalidateQueries({ queryKey: riskKeys.recommendations() })
}

function cacheRecommendationTransition(
  queryClient: QueryClient,
  updated: RecommendationRead,
): void {
  queryClient.setQueryData(riskKeys.recommendationDetail(updated.id), updated)
  void queryClient.invalidateQueries({ queryKey: riskKeys.recommendations() })
  void queryClient.invalidateQueries({ queryKey: riskKeys.recommendationDetails() })
  // `RiskRead.recommendations` is a nested projection with its own `status`, so
  // a cached risk row would keep showing a suggestion that has been answered.
  void queryClient.invalidateQueries({ queryKey: riskKeys.riskDetails() })
}

/**
 * "Seen, still true, no longer shouting at me."
 *
 * Acknowledging is not resolving: the risk keeps being re-detected and keeps
 * updating, it simply stops competing for attention. A 409 means the risk had
 * already moved on, and the invalidation on the way out leaves the list showing
 * where it actually ended up rather than where it was.
 */
export function useAcknowledgeRisk(): UseMutationResult<RiskRead, Error, UUIDString> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: UUIDString) => acknowledgeRisk(id),
    onSuccess: (updated) => cacheRiskTransition(queryClient, updated),
  })
}

/** "This does not apply to me." The risk is closed and will not be raised again. */
export function useDismissRisk(): UseMutationResult<RiskRead, Error, UUIDString> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: UUIDString) => dismissRisk(id),
    onSuccess: (updated) => cacheRiskTransition(queryClient, updated),
  })
}

/** "The condition is over." Normally written by the detection pass itself. */
export function useResolveRisk(): UseMutationResult<RiskRead, Error, UUIDString> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: UUIDString) => resolveRisk(id),
    onSuccess: (updated) => cacheRiskTransition(queryClient, updated),
  })
}

/** "I will do this." Stamps `responded_at`; the suggestion stays open. */
export function useAcceptRecommendation(): UseMutationResult<
  RecommendationRead,
  Error,
  UUIDString
> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: UUIDString) => acceptRecommendation(id),
    onSuccess: (updated) => cacheRecommendationTransition(queryClient, updated),
  })
}

/** "Not for me." A legitimate answer, not a rejection of the underlying risk. */
export function useRejectRecommendation(): UseMutationResult<
  RecommendationRead,
  Error,
  UUIDString
> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: UUIDString) => rejectRecommendation(id),
    onSuccess: (updated) => cacheRecommendationTransition(queryClient, updated),
  })
}

/** "Done." The terminal state a user reaches after acting on the suggestion. */
export function useCompleteRecommendation(): UseMutationResult<
  RecommendationRead,
  Error,
  UUIDString
> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: UUIDString) => completeRecommendation(id),
    onSuccess: (updated) => cacheRecommendationTransition(queryClient, updated),
  })
}

/**
 * Runs detection now and invalidates everything it can have touched.
 *
 * `window_days` is optional because the backend owns the default; passing
 * nothing asks for the engine's own window rather than one invented here. The
 * run summary is returned rather than left in the response body, so the caller
 * can tell "nothing to judge" (`evaluated: false`, with the reason) from
 * "judged, and found nothing" — two different results that arrive as the same
 * HTTP 200 with different counts.
 */
export function useRunEvaluation(): UseMutationResult<
  EvaluationRead,
  Error,
  EvaluationParams | undefined
> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (params?: EvaluationParams) => evaluateIntelligence(params ?? {}),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: riskKeys.all() })
    },
  })
}
