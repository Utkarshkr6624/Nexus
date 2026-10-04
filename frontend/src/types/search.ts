/**
 * Wire types and presentation vocabulary for the global search surface
 * (`GET /api/v1/search`).
 *
 * These mirror `backend/app/schemas/search.py` exactly. As with every other
 * types module in this directory, a mismatch here fails at runtime rather than
 * compile time, because the response body is only typed by convention — so the
 * two files move together.
 *
 * **The response is two views of one page.** `hits` is the flat ranked list a
 * palette walks top to bottom; `groups` partitions *exactly those hits* and adds
 * nothing. A client renders either one and can never disagree with itself, which
 * is why the page toggles between them rather than re-grouping in JavaScript.
 *
 * **`meta.total` counts hits *discovered*, not rows matching.** The backend caps
 * how deep each entity kind is scanned, so the union cannot grow without bound
 * and the total stops growing with it. That is a stated property of the endpoint
 * rather than a bug to work around, so the pager treats it as the size of the
 * result set it may page through.
 *
 * **No model is involved in producing any of this.** The search is `ILIKE` over
 * eleven tables, ranked by which column matched and then by recency. NEXUS runs
 * one intent classifier and it plays no part here, so nothing on this page may
 * imply semantic or fuzzy matching: a term that is not a substring is not a
 * near-miss, it is a miss.
 */

import {
  BookOpen,
  CalendarDays,
  Code2,
  FolderKanban,
  GraduationCap,
  Lightbulb,
  ListTodo,
  NotebookPen,
  ShieldAlert,
  Sparkles,
  Target,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import type { PageMeta } from './pagination'
import type { ISODateTimeString, UUIDString } from './api'

/**
 * The tables one search reads. A closed union, not `string`, so a typo in a
 * comparison is a compile error rather than a request the backend answers 422.
 *
 * The declaration order is the backend's `SearchEntityKind` order and is used
 * for the kind filter chips, so the chips read in the same order the ranked
 * results do.
 */
export type SearchEntityKind =
  | 'project'
  | 'task'
  | 'note'
  | 'resource'
  | 'concept'
  | 'repository'
  | 'goal'
  | 'skill'
  | 'event'
  | 'risk'
  | 'recommendation'

export const SEARCH_ENTITY_KINDS: readonly SearchEntityKind[] = [
  'project',
  'task',
  'note',
  'resource',
  'concept',
  'repository',
  'goal',
  'skill',
  'event',
  'risk',
  'recommendation',
] as const

/**
 * The shortest term the backend accepts. One character: a zero-length query
 * matches every row of every table, which is a table dump wearing a search
 * endpoint's clothes, so the page treats an empty box as "not asked yet" and
 * fires no request at all.
 */
export const SEARCH_MIN_QUERY_CHARS = 1

/** The longest term the backend accepts, in characters. */
export const SEARCH_MAX_QUERY_CHARS = 200

/** How many entity kinds `?types=` may carry — the size of the union above. */
export const SEARCH_MAX_TYPES = SEARCH_ENTITY_KINDS.length

/** The page size used when the caller does not ask for one. */
export const SEARCH_DEFAULT_LIMIT = 50

/**
 * The largest page the backend will serve.
 *
 * Above this the answer is a 422, **not** a silently truncated page: a client
 * that asked for 5000 and received 200 could not tell a capped page from a short
 * one. The page-size control therefore offers nothing above this number, so the
 * only way to hit the ceiling is to type the value by hand.
 */
export const SEARCH_MAX_LIMIT = 200

/**
 * The page sizes the pager offers. All at or below {@link SEARCH_MAX_LIMIT};
 * the largest is chosen so a reader can page a broad term without scrolling
 * past a screenful.
 */
export const SEARCH_PAGE_SIZES: readonly number[] = [10, 25, 50, 100] as const

/** One matching row. */
export interface SearchHit {
  /** Which table this row came from. */
  kind: SearchEntityKind
  id: UUIDString
  /** The row's own label — name, or title. */
  title: string
  /** Text from the matched column, trimmed around the hit. Plain text, never HTML. */
  snippet: string
  /** Start of the matched region, as a character offset into `snippet`. */
  match_start: number
  /** End of the matched region, as a character offset into `snippet`. */
  match_end: number
  /** The column that produced the snippet and the ranking, by model attribute name. */
  matched_field: string
  /** The project this row is filed under, or `null` for a row that has none. */
  project_id: UUIDString | null
  /** That project's name, resolved in the caller's scope. */
  project_name: string | null
  /** `today`, `yesterday`, `4 days ago`, … or null when the row carries no date. */
  relative_date: string | null
  updated_at: ISODateTimeString
}

/** One entity kind's share of the page, for the grouped rendering. */
export interface SearchGroup {
  kind: SearchEntityKind
  hits: SearchHit[]
}

/** The whole answer: one ranked list, and the same hits grouped by kind. */
export interface SearchResponse {
  /** The term as it was actually searched for, after trimming. */
  query: string
  hits: SearchHit[]
  groups: SearchGroup[]
  meta: PageMeta
}

/** Query parameters for `GET /api/v1/search`. */
export interface SearchListParams {
  /** Required, 1..200 characters. The page never sends a blank one. */
  q: string
  /** Restrict to these kinds. Absent means all eleven. */
  types?: SearchEntityKind[]
  /** Only rows filed under this project. */
  project_id?: UUIDString
  /** One status, applied to every searched kind that has one. */
  status?: string
  /** One priority. Risks carry a severity, not a priority. */
  priority?: string
  /** Inclusive lower bound on each kind's own date column. */
  from?: string
  /** Inclusive upper bound; covers the whole named day. */
  to?: string
  /** Tasks carrying **every** listed tag. */
  tag_ids?: UUIDString[]
  /** 1..200. Over the ceiling is a 422, not a clamp. */
  limit?: number
  /** >= 0. */
  offset?: number
}

export interface SearchKindMeta {
  /** Singular noun for the kind, as a chip label and a row badge. */
  label: string
  /** Plural noun for counts, e.g. "3 tasks". */
  plural: string
  icon: LucideIcon
  /**
   * The route in `src/routes/router.tsx` that shows this kind.
   *
   * A `:param` segment marks a detail route and is replaced with the hit's own
   * id by {@link searchHitHref}; a plain path is the list that contains it.
   * Every entry here is asserted against the real route table by
   * `search-page.test.tsx`, so a route that is renamed or dropped turns the test
   * red rather than leaving a link to a 404.
   */
  route: string
  /** True when `route` ends in a `:param` to be filled in with the hit's id. */
  detail: boolean
}

/**
 * Per-kind presentation vocabulary, keyed by the wire value.
 *
 * An **icon plus the word** is the whole identity of a kind here — never colour
 * alone — so the filter chips, the group headings and the row badges all name
 * the kind in text as well as drawing it.
 */
export const SEARCH_KIND_META: Readonly<Record<SearchEntityKind, SearchKindMeta>> = {
  project: {
    label: 'Project',
    plural: 'projects',
    icon: FolderKanban,
    // A project has its own detail page, so a hit opens the project rather than
    // the list the user would then have to search again.
    route: '/projects/:projectId',
    detail: true,
  },
  task: {
    label: 'Task',
    plural: 'tasks',
    icon: ListTodo,
    route: '/tasks',
    detail: false,
  },
  note: {
    label: 'Note',
    plural: 'notes',
    icon: NotebookPen,
    route: '/knowledge/notes/:noteId',
    detail: true,
  },
  resource: {
    label: 'Resource',
    plural: 'resources',
    icon: BookOpen,
    // No resource detail route exists, so a resource opens the knowledge base
    // that lists it rather than a page that would 404.
    route: '/knowledge',
    detail: false,
  },
  concept: {
    label: 'Concept',
    plural: 'concepts',
    icon: Lightbulb,
    route: '/knowledge/concepts/:conceptId',
    detail: true,
  },
  repository: {
    label: 'Repository',
    plural: 'repositories',
    icon: Code2,
    route: '/developer/:repositoryId',
    detail: true,
  },
  goal: {
    label: 'Learning goal',
    plural: 'goals',
    icon: Target,
    route: '/learning',
    detail: false,
  },
  skill: {
    label: 'Skill',
    plural: 'skills',
    icon: GraduationCap,
    route: '/learning',
    detail: false,
  },
  event: {
    label: 'Calendar event',
    plural: 'events',
    icon: CalendarDays,
    route: '/planner',
    detail: false,
  },
  risk: {
    label: 'Risk',
    plural: 'risks',
    icon: ShieldAlert,
    route: '/risks',
    detail: false,
  },
  recommendation: {
    label: 'Recommendation',
    plural: 'recommendations',
    icon: Sparkles,
    route: '/recommendations',
    detail: false,
  },
}

/**
 * The href a hit should link to, or `null` when its kind has no route.
 *
 * A hit is only ever as navigable as its kind's page: a kind without a route
 * renders as plain text and says so, rather than linking to a 404 that reads
 * like the record itself is broken.
 */
export function searchHitHref(hit: SearchHit): string | null {
  const meta = SEARCH_KIND_META[hit.kind]
  if (!meta || !meta.route) return null
  if (!meta.detail) return meta.route
  const pattern = meta.route.replace(/\/:[^/]+$/, '')
  return pattern ? `${pattern}/${hit.id}` : meta.route
}

/**
 * The date a hit is stamped with, or `null` when there is none.
 *
 * `relative_date` is the backend's own wording and is preferred because it is
 * the one written for a person ("4 days ago"); the ISO date is only a fallback
 * for a row that arrived without one.
 */
export function hitDateLabel(hit: SearchHit): string | null {
  if (hit.relative_date) return hit.relative_date
  const day = typeof hit.updated_at === 'string' ? hit.updated_at.slice(0, 10) : ''
  return day.length === 10 ? day : null
}