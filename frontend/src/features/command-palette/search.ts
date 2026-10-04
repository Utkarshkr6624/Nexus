/**
 * The palette's view of global search.
 *
 * This exists as a thin adapter rather than a client of its own. `GET /search`
 * already has one canonical client — `src/services/search.ts` — with the wire
 * types in `src/types/search.ts`, written against `backend/app/schemas/search.py`.
 * A second copy of those types and a second `fetch` for the same endpoint would
 * be two places for the schema to drift, which is exactly the failure the search
 * page's own types were written to avoid.
 *
 * So this module owns only what is *palette-shaped*: a one-argument call that
 * hides the params object, the ten-hit ceiling a dropdown can show, and a
 * shortened label for a kind that has no long form here.
 */

import { searchEverything } from '@/services/search'
import { SEARCH_KIND_META } from '@/types/search'
import type { SearchEntityKind, SearchResponse } from '@/types/search'

export type { SearchEntityKind, SearchHit } from '@/types/search'

/** Shortest term worth sending. Mirrors the server's own floor. */
export const MIN_SEARCH_CHARS = 1

/** Longest term worth sending. Mirrors the server's own ceiling. */
export const MAX_SEARCH_CHARS = 200

/**
 * How many records the palette shows.
 *
 * Ten is a dropdown's worth. A palette that lists sixty records is a page, and a
 * page behind a keyboard shortcut is a trap — the user cannot see the rest and
 * has no way to know they exist. The full result set lives on `/search`, which
 * the palette offers as a result kind of its own.
 */
export const PALETTE_HIT_LIMIT = 10

/** What the palette renders, narrowed from the endpoint's full response. */
export type SearchResult = SearchResponse

export { searchEverything }

/**
 * Kind labels, read from the canonical table rather than restated.
 *
 * A palette that said "Tasks" where the search page said "Task" would be a small
 * inconsistency nobody could act on and everybody would notice.
 */
export const SEARCH_KIND_LABELS: Record<SearchEntityKind, string> = Object.fromEntries(
  (Object.keys(SEARCH_KIND_META) as SearchEntityKind[]).map((kind) => [
    kind,
    SEARCH_KIND_META[kind].label,
  ]),
) as Record<SearchEntityKind, string>

/**
 * Where selecting a record should take the user.
 *
 * Re-exported from the search page's vocabulary rather than reimplemented, so a
 * kind cannot resolve to one route in the palette and a different one on the page.
 */
export { searchHitHref as hitTargetPath } from '@/types/search'