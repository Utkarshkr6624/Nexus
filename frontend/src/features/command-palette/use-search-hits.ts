import { useQuery } from '@tanstack/react-query'

import { useDebouncedValue } from '@/hooks/use-debounce'

import {
  MAX_SEARCH_CHARS,
  MIN_SEARCH_CHARS,
  PALETTE_HIT_LIMIT,
  searchEverything,
} from './search'
import type { SearchResult } from './search'

/**
 * Longer than the palette's 120 ms destination filter, because this one leaves
 * the browser. The local filter can afford to be twitchy; a network round trip
 * per keystroke is the thing that makes a palette feel slow.
 */
export const SEARCH_DEBOUNCE_MS = 250

export interface SearchHitsState {
  hits: SearchResult['hits']
  /** False while the trimmed query is too short to be worth a request. */
  enabled: boolean
  /**
   * True from the keystroke that made the query searchable until its hits land:
   * either the term is still settling behind the debounce, or it is in flight.
   * The palette shows its "searching" line over this window, because it is
   * exactly the window in which an empty list would be a lie.
   */
  pending: boolean
  isFetching: boolean
  isError: boolean
  error: Error | null
  term: string
}

/**
 * The cross-entity search half of the palette.
 *
 * Disabled — not merely empty — for a query the endpoint would refuse, so an
 * empty box and a one-character term both cost nothing. That is the whole
 * reason this is a hook rather than a `useEffect` that fetches on render: a
 * keystroke that cannot produce results must not produce a request either.
 */
export function useSearchHits(query: string): SearchHitsState {
  const debounced = useDebouncedValue(query, SEARCH_DEBOUNCE_MS)
  const term = debounced.trim()
  const requested = query.trim()
  const enabled = term.length >= MIN_SEARCH_CHARS && term.length <= MAX_SEARCH_CHARS

  const result = useQuery({
    queryKey: ['command-palette', 'search', term],
    queryFn: ({ signal }) =>
      searchEverything({ q: term, limit: PALETTE_HIT_LIMIT }, signal),
    enabled,
    staleTime: 30_000,
  })

  return {
    hits: result.data?.hits ?? [],
    enabled,
    pending: enabled && (result.isFetching || requested !== term),
    isFetching: result.isFetching,
    isError: result.isError,
    error: result.error,
    term,
  }
}
