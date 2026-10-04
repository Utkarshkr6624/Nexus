/**
 * Thin typed wrapper over the global search endpoint.
 *
 * No React here — the function below is the promise the hook in
 * `features/search/hooks.ts` wraps in a `queryFn`.
 *
 * **Two query parameters are repeated lists.** `QueryParams` is a flat record,
 * so `api-client` can only emit each key once, and the backend parses `types`
 * and `tag_ids` as lists — it would read `?tag_ids=a,b` as one unparseable id.
 * Those two therefore go on the path as repeats, exactly as
 * `services/work.ts` and `services/planner.ts` already do for `tag_ids` and
 * `task_ids`, while everything scalar stays on the client's `query` option.
 *
 * **The bearer token is the client's job, not this file's.** Search requires
 * `analytics.read`, so the request is authenticated by default and the 401
 * recovery belongs to the shared client; nothing here disables auth.
 */

import { apiClient } from '@/lib/api-client'
import type { QueryParams } from '@/lib/api-client'
import type { UUIDString } from '@/types/api'
import type { SearchEntityKind, SearchListParams, SearchResponse } from '@/types/search'

/** Resolved against the `/api/v1` base URL configured on the client. */
export const SEARCH_ENDPOINTS = {
  search: '/search',
} as const

function queryFrom(params: object): QueryParams {
  const query: QueryParams = {}
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) continue
    query[key] = value as string | number | boolean
  }
  return query
}

/**
 * Appends a repeated query parameter to the path.
 *
 * Same contract and same reason as its siblings in `work.ts`/`planner.ts`: the
 * client's serialiser emits a key once, which is the wrong shape for a list.
 */
function withRepeatedParam(path: string, key: string, values: readonly string[]): string {
  if (values.length === 0) return path
  const suffix = values.map((value) => `${key}=${encodeURIComponent(value)}`).join('&')
  return `${path}${path.includes('?') ? '&' : '?'}${suffix}`
}

/**
 * One cross-entity search over the caller's own records.
 *
 * **The scope is the caller's and only the caller's.** There is no `user_id`
 * here and the predicate is on the query rather than on the filter, so a
 * filter can never widen the result set past its owner.
 *
 * **Nothing matched is a 200, not an error.** A term that finds nothing answers
 * with two empty lists and `total: 0`, so the caller renders an empty state
 * rather than a failure.
 *
 * `limit` above 200 and a `q` outside 1..200 characters are 422s, not clamps —
 * the caller is expected to have kept inside the bounds published on
 * `@/types/search`.
 */
export function searchEverything(
  params: SearchListParams,
  signal?: AbortSignal,
): Promise<SearchResponse> {
  const withTypes = withRepeatedParam(
    SEARCH_ENDPOINTS.search,
    'types',
    (params.types ?? []) as SearchEntityKind[],
  )
  const path = withRepeatedParam(withTypes, 'tag_ids', (params.tag_ids ?? []) as UUIDString[])

  return apiClient.get<SearchResponse>(path, {
    query: queryFrom(params),
    signal,
  })
}