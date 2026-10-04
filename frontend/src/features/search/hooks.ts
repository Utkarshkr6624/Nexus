/**
 * TanStack Query bindings for the global search surface.
 *
 * **`searchKeys` is the single owner of the query-key shape.** Every key lives
 * under the `['search']` root and every key carries the resolved term, so two
 * different questions never share a cache entry and a re-typed query does not
 * paint over the previous one's results while it is in flight.
 *
 * **A blank term disables the query rather than being sent.** The backend
 * accepts a one-character minimum and rejects an empty `q` with a 422, but an
 * empty search box is not a *failed* search — it is a search that has not been
 * asked yet, and the page renders an invitation rather than an error. Encoding
 * that as `enabled` means the rule holds for every caller of this hook rather
 * than only for the page that happens to check the string first.
 *
 * **The term is trimmed before it reaches either.** The backend trims it too and
 * echoes the trimmed value back in `query`; trimming here means `  report  ` and
 * `report` share one cache entry instead of two.
 *
 * Retry policy is inherited from `app/query-client.ts`, which refuses to retry a
 * 4xx — so the 422 from an over-long term and the 403 from a caller without
 * `analytics.read` both surface on the first response, and are the page's to
 * explain.
 */

import { useQuery } from '@tanstack/react-query'

import { searchEverything } from '@/services/search'
import { SEARCH_MIN_QUERY_CHARS } from '@/types/search'
import type { SearchListParams, SearchResponse } from '@/types/search'

/**
 * Normalised, fixed-length key part.
 *
 * An unset param becomes `null` rather than being dropped, so a params object
 * re-created on every render hashes to the same key instead of thrashing the
 * cache. The two lists are sorted and joined because their order carries no
 * meaning to the backend — `types=task&types=note` and the reverse describe one
 * request — and without this a user who toggled the same two chips off and on
 * in the other order would pay for the fetch twice.
 */
function keyPart(params: SearchListParams): unknown[] {
  return [
    params.q.trim(),
    params.types ? [...params.types].sort().join(',') : null,
    params.project_id ?? null,
    params.status ?? null,
    params.priority ?? null,
    params.from ?? null,
    params.to ?? null,
    params.tag_ids ? [...params.tag_ids].sort().join(',') : null,
    params.limit ?? null,
    params.offset ?? null,
  ]
}

/** Stable key factory. Every key lives under the `['search']` root. */
export const searchKeys = {
  all: () => ['search'] as const,
  results: (params: SearchListParams) => ['search', 'results', ...keyPart(params)] as const,
}

/**
 * The one search query.
 *
 * `enabled` is false for a blank term, so an untouched page fires no request.
 * `placeholderData` is deliberately not `keepPreviousData`: this page shows a
 * busy state rather than stale rows, because a result set for the *previous*
 * term next to the box that has already changed is the classic "why is this not
 * what I searched for" bug.
 */
export function useSearch(params: SearchListParams) {
  const term = params.q.trim()

  return useQuery<SearchResponse>({
    queryKey: searchKeys.results({ ...params, q: term }),
    queryFn: ({ signal }) => searchEverything({ ...params, q: term }, signal),
    enabled: term.length >= SEARCH_MIN_QUERY_CHARS,
    staleTime: 30_000,
  })
}