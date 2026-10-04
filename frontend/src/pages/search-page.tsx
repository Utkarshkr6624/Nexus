import { useCallback } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { LayoutList, Search, SearchX, SlidersHorizontal, X } from 'lucide-react'

import { EmptyState } from '@/components/feedback/empty-state'
import { ErrorState } from '@/components/feedback/error-state'
import { LoadingState } from '@/components/feedback/loading-state'
import { PageHeader } from '@/components/feedback/page-header'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { useSearch } from '@/features/search/hooks'
import { useDebouncedValue } from '@/hooks/use-debounce'
import { toApiError } from '@/services/errors'
import {
  SEARCH_DEFAULT_LIMIT,
  SEARCH_ENTITY_KINDS,
  SEARCH_KIND_META,
  SEARCH_MAX_QUERY_CHARS,
  SEARCH_MIN_QUERY_CHARS,
  SEARCH_PAGE_SIZES,
  hitDateLabel,
  searchHitHref,
} from '@/types/search'
import type {
  SearchEntityKind,
  SearchGroup,
  SearchHit,
  SearchKindMeta,
} from '@/types/search'

/**
 * One query across everything the signed-in account owns.
 *
 * **The state lives in the URL.** `q`, `types`, `limit`, `offset` and `group`
 * are all read back out of the query string, which is what makes a search
 * shareable, survives a reload, and works with the back button. Every control
 * below is a view of that URL and nothing else.
 *
 * **The box updates the URL per keystroke and only the request is debounced.**
 * Typing stays responsive because the input is bound to `q` directly, while the
 * network waits out `SEARCH_DEBOUNCE_MS` — a request per character is the thing
 * a debounce exists to prevent, and re-rendering a controlled input is not.
 *
 * **A blank box fires nothing.** `useSearch` disables itself below
 * {@link SEARCH_MIN_QUERY_CHARS}, so an untouched page — or one the user has just
 * cleared — renders an invitation rather than the 422 the backend would
 * correctly answer for an empty `q`.
 *
 * **Grouping is the server's grouping.** `SearchResponse.groups` partitions the
 * very same page `hits` holds, so toggling between the two views changes the
 * layout and never the result set. Re-bucketing `hits` in JavaScript would have
 * been the same pixels and a second source of truth about the order.
 *
 * **A kind is an icon and a word, never a colour.** Chips, group headings and
 * row badges all carry the kind's name in text, so the filter row is readable
 * in dark mode, by a reader with a colour vision deficiency, and by a screen
 * reader.
 */

/** How long the box must settle before a request goes out. */
const SEARCH_DEBOUNCE_MS = 200

/** URL key for the grouping toggle; absent means grouped, which is the default. */
const GROUP_PARAM = 'group'

/** The word on the control that means "search every kind", i.e. no `types`. */
const ALL_KINDS_LABEL = 'All kinds'

/** Stable empty array, so the row list is not re-created on every render. */
const NO_HITS: SearchHit[] = []

/**
 * The vocabulary for a kind this build has no definition for.
 *
 * `SEARCH_KIND_META` is closed, so a kind outside it can only arrive from a
 * backend newer than this bundle. Falling back to the server's own word keeps
 * the row readable instead of rendering an empty chip, and the row still links
 * nowhere — which is the honest answer for a surface this build cannot route to.
 */
function kindMeta(kind: string): SearchKindMeta {
  return (
    SEARCH_KIND_META[kind as SearchEntityKind] ?? {
      label: kind.charAt(0).toUpperCase() + kind.slice(1),
      plural: `${kind}s`,
      icon: SearchX,
      route: '',
      detail: false,
    }
  )
}

/**
 * Reads `?types=` back into a known-kind list.
 *
 * An unrecognised word is dropped rather than sent: the backend answers
 * `?types=nonsense` with a 422, and a hand-edited link should not be able to
 * turn the page into an error. The result is de-duplicated and ordered by the
 * union's declaration order so the chips never reorder themselves between two
 * equivalent URLs.
 */
function readKinds(values: readonly string[]): SearchEntityKind[] {
  const known = new Set<SearchEntityKind>()
  for (const value of values) {
    if ((SEARCH_ENTITY_KINDS as readonly string[]).includes(value)) {
      known.add(value as SearchEntityKind)
    }
  }
  return SEARCH_ENTITY_KINDS.filter((kind) => known.has(kind))
}

/**
 * Reads `?limit=` back into a size the control offers.
 *
 * Anything else — a hand-typed 37, a negative, a value past the backend's 200 —
 * resolves to the default rather than being put on the wire, where it would earn
 * a 422. The select below only ever renders the four offered sizes, so the
 * control cannot disagree with what is being sent.
 */
function readLimit(value: string | null): number {
  const parsed = Number(value)
  return SEARCH_PAGE_SIZES.includes(parsed) ? parsed : SEARCH_DEFAULT_LIMIT
}

function readOffset(value: string | null): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 0
}

interface HitRowProps {
  hit: SearchHit
  /** 1-based position in the flat ranked page; absent in the grouped view. */
  rank?: number
}

function HitRow({ hit, rank }: HitRowProps) {
  const meta = kindMeta(hit.kind)
  const Icon = meta.icon
  const href = searchHitHref(hit)
  const date = hitDateLabel(hit)

  return (
    <li className="space-y-1.5 px-3 py-3 sm:px-4">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Icon aria-hidden="true" className="size-3.5 shrink-0" />
        <span>{meta.label}</span>
        {rank !== undefined && (
          <span className="ml-auto font-mono text-xs">#{rank}</span>
        )}
      </div>

      <h3 className="text-sm font-medium leading-snug">
        {href ? (
          <Link
            to={href}
            className="rounded-sm text-foreground underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          >
            {hit.title}
          </Link>
        ) : (
          // A kind this build has no page for is rendered as plain text. A link
          // would be a promise the router cannot keep.
          <span>{hit.title}</span>
        )}
      </h3>

      <Snippet hit={hit} />

      {(hit.project_name || date) && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          {hit.project_name && <span>in {hit.project_name}</span>}
          {date && (
            <time dateTime={hit.updated_at}>{date}</time>
          )}
        </div>
      )}
    </li>
  )
}

/**
 * The snippet with its matched region marked.
 *
 * `match_start`/`match_end` are **character offsets into the snippet**, which is
 * the whole reason this renders as three text nodes rather than a
 * `dangerouslySetInnerHTML` with sentinels: the text being highlighted is
 * whatever the user wrote, so a note body containing those sentinels verbatim
 * would render as a broken highlight or, worse, a false one. The offsets are
 * clamped rather than trusted, because an out-of-range pair from a newer backend
 * must shorten the row, not blank it.
 */
function Snippet({ hit }: { hit: SearchHit }) {
  const snippet = typeof hit.snippet === 'string' ? hit.snippet : ''
  const from = Math.max(0, Math.min(hit.match_start ?? 0, snippet.length))
  const to = Math.max(from, Math.min(hit.match_end ?? 0, snippet.length))

  return (
    <p className="text-sm leading-relaxed text-muted-foreground">
      {from > 0 && snippet.slice(0, from)}
      {to > from && (
        <mark className="rounded-sm bg-warning/40 px-0.5 text-foreground">{snippet.slice(from, to)}</mark>
      )}
      {to < snippet.length && snippet.slice(to)}
    </p>
  )
}

/** One kind's block in the grouped view. */
function KindGroup({ group }: { group: SearchGroup }) {
  const meta = kindMeta(group.kind)
  const Icon = meta.icon
  const headingId = `search-group-${group.kind}`

  return (
    <section className="space-y-2" aria-labelledby={headingId}>
      <div className="flex items-center gap-2">
        <Icon aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
        <h2 id={headingId} className="text-sm font-semibold tracking-tight text-foreground">
          {meta.plural}
        </h2>
        <span className="text-xs text-muted-foreground">
          {group.hits.length} on this page
        </span>
      </div>
      <ul className="divide-y divide-border rounded-lg border border-border bg-card">
        {group.hits.map((hit) => (
          <HitRow key={`${hit.kind}-${hit.id}`} hit={hit} />
        ))}
      </ul>
    </section>
  )
}

export default function SearchPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  /**
   * Writes a patch onto the query string.
   *
   * A `string[]` repeats the key (that is how `types` is carried); `undefined`
   * deletes it. Deleting is what "back to the first page" and "every kind" both
   * mean here, so they are one operation rather than two that can disagree.
   */
  const apply = useCallback(
    (patch: Record<string, string | string[] | undefined>) => {
      const next = new URLSearchParams(searchParams)
      for (const [key, value] of Object.entries(patch)) {
        next.delete(key)
        if (Array.isArray(value)) {
          for (const entry of value) next.append(key, entry)
        } else if (value !== undefined) {
          next.set(key, value)
        }
      }
      setSearchParams(next)
    },
    [searchParams, setSearchParams],
  )

  const query = searchParams.get('q') ?? ''
  const kinds = readKinds(searchParams.getAll('types'))
  const limit = readLimit(searchParams.get('limit'))
  const offset = readOffset(searchParams.get('offset'))
  const grouped = searchParams.get(GROUP_PARAM) !== '0'

  // Only the request waits. The input is bound to `query` above, so the box
  // keeps every character as it is typed.
  const term = useDebouncedValue(query, SEARCH_DEBOUNCE_MS)

  const results = useSearch({
    q: term,
    types: kinds.length > 0 ? kinds : undefined,
    limit,
    offset,
  })

  const onQueryChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      // Any edit is a new question, so it starts again at the first page.
      apply({ q: event.target.value, offset: undefined })
    },
    [apply],
  )

  /**
   * Submitting re-reads the same term.
   *
   * The box is already the URL, so there is nothing to commit — what this is
   * for is the reader who has paged forward and presses Enter to run the search
   * again. It returns them to the top of the result set rather than leaving them
   * on a page of an answer they have not read yet.
   */
  const onSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault()
      apply({ offset: undefined })
    },
    [apply],
  )

  const onToggleKind = useCallback(
    (kind: SearchEntityKind) => {
      const next = kinds.includes(kind)
        ? kinds.filter((candidate) => candidate !== kind)
        : [...kinds, kind]
      apply({ types: next.length > 0 ? next : undefined, offset: undefined })
    },
    [apply, kinds],
  )

  const clearKinds = useCallback(() => apply({ types: undefined, offset: undefined }), [apply])
  const clearQuery = useCallback(() => apply({ q: '', offset: undefined }), [apply])

  const hits = results.data?.hits ?? NO_HITS
  const total = results.data?.meta.total ?? 0
  const answered = query.trim().length >= SEARCH_MIN_QUERY_CHARS
  const settling = query !== term

  const page = Math.floor(offset / limit) + 1
  const pages = Math.max(1, Math.ceil(total / limit))
  const hasPrevious = offset > 0
  const hasNext = offset + hits.length < total

  /**
   * What a filtered search that found nothing says.
   *
   * One selected kind gets named, because "no tasks match" is a real answer
   * about a real surface. Several get the collective wording, because no single
   * one of them is what the reader asked about.
   */
  const [onlyKind] = kinds
  const filteredNothing = onlyKind
    ? `No ${kindMeta(onlyKind).plural} match`
    : 'No records of those kinds match'

  /**
   * What is announced when a search settles.
   *
   * Mounted empty and filled only once a query has actually answered, so a
   * screen reader is not read a count that is about to change.
   */
  const announcement = results.data
    ? `${results.data.meta.total} result${results.data.meta.total === 1 ? '' : 's'} for ${results.data.query}.`
    : ''

  return (
    <div className="app-container space-y-6 py-6 lg:py-8">
      <PageHeader
        title="Search"
        eyebrow={
          <>
            <Search className="size-3.5" aria-hidden="true" />
            Everything
          </>
        }
        description="One query across projects, tasks, notes, resources, concepts, repositories, learning goals, skills, calendar events, risks and recommendations. Only your own records are searched."
      />

      {/* Mounted empty: a live region has to exist before its first update to be
          announced at all. */}
      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>

      <form className="space-y-2" role="search" aria-label="Global search" onSubmit={onSubmit}>
        <Label htmlFor="search-query">Search NEXUS</Label>
        <div className="flex items-start gap-2">
          <Input
            id="search-query"
            value={query}
            onChange={onQueryChange}
            placeholder="Projects, tasks, notes, concepts…"
            maxLength={SEARCH_MAX_QUERY_CHARS}
            autoComplete="off"
            spellCheck={false}
            endAdornment={
              // The adornment slot is mounted unconditionally, not only once
              // there is something to clear. `Input` returns a bare `<input>`
              // when `endAdornment` is absent and a wrapped one when it is
              // present, so a conditional slot makes the *input itself* unmount
              // and remount on the first keystroke — which drops the character
              // and leaves the box stuck on one letter.
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-8"
                aria-label="Clear search"
                disabled={query.length === 0}
                onClick={clearQuery}
              >
                <X aria-hidden="true" />
              </Button>
            }
          />
          <Button type="submit">
            <Search aria-hidden="true" />
            Search
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          Up to {SEARCH_MAX_QUERY_CHARS} characters. Results are ranked by which field matched,
          then by how recently the record changed.
        </p>
      </form>

      <section className="space-y-2" aria-labelledby="search-kind-filters">
        {/* The filter row carries no visible heading, so the region is named for
            assistive technology only. */}
        <h2 id="search-kind-filters" className="sr-only">
          Filter by kind
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            size="sm"
            variant={kinds.length === 0 ? 'secondary' : 'ghost'}
            aria-pressed={kinds.length === 0}
            onClick={clearKinds}
          >
            <LayoutList aria-hidden="true" />
            {ALL_KINDS_LABEL}
          </Button>

          {SEARCH_ENTITY_KINDS.map((kind) => {
            const meta = kindMeta(kind)
            const Icon = meta.icon
            const selected = kinds.includes(kind)
            return (
              <Button
                key={kind}
                type="button"
                size="sm"
                variant={selected ? 'secondary' : 'outline'}
                aria-pressed={selected}
                onClick={() => onToggleKind(kind)}
              >
                <Icon aria-hidden="true" />
                {meta.label}
              </Button>
            )
          })}
        </div>
      </section>

      {results.isError && !results.data ? (
        <ErrorState
          error={toApiError(results.error)}
          title="The search could not run"
          onRetry={() => void results.refetch()}
        />
      ) : !results.data ? (
        answered ? (
          <LoadingState label="Searching…" />
        ) : (
          <EmptyState
            icon={Search}
            title="Search everything you own"
            description="Type at least one character. A single word is enough — results come back ranked, and you can narrow them to one kind."
          />
        )
      ) : hits.length === 0 ? (
        kinds.length > 0 ? (
          // "Something exists, just not here" is a different claim from "nothing
          // matched", and conflating the two is how a filtered search reads as an
          // empty account.
          <EmptyState
            icon={SlidersHorizontal}
            title="Nothing matched within the selected kinds"
            description={`${filteredNothing} “${results.data.query}”. Widen the filter to search every kind.`}
            action={
              <Button type="button" variant="outline" size="sm" onClick={clearKinds}>
                {ALL_KINDS_LABEL}
              </Button>
            }
          />
        ) : (
          <EmptyState
            icon={SearchX}
            title="Nothing matched"
            description={`No record you own contains “${results.data.query}”. Search matches text inside a record, so a shorter or more general word may find it.`}
          />
        )
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">
              {total} result{total === 1 ? '' : 's'} for “{results.data.query}”
              {kinds.length > 0 && ' in the selected kinds'}
              {settling && ' · updating…'}
            </p>
            <div className="flex items-center gap-2">
              <Label htmlFor="search-grouping" className="text-xs text-muted-foreground">
                Group by kind
              </Label>
              <Switch
                id="search-grouping"
                aria-label="Group by kind"
                checked={grouped}
                onCheckedChange={(next) => apply({ [GROUP_PARAM]: next ? undefined : '0' })}
              />
            </div>
          </div>

          {grouped ? (
            <div className="space-y-6">
              {results.data.groups.map((group) => (
                <KindGroup key={group.kind} group={group} />
              ))}
            </div>
          ) : (
            <section aria-labelledby="search-ranked-heading" className="space-y-2">
              <h2 id="search-ranked-heading" className="sr-only">
                Ranked results
              </h2>
              <ol className="divide-y divide-border rounded-lg border border-border bg-card">
                {results.data.hits.map((hit, index) => (
                  <HitRow key={`${hit.kind}-${hit.id}`} hit={hit} rank={offset + index + 1} />
                ))}
              </ol>
            </section>
          )}

          <nav
            className="flex flex-wrap items-center justify-between gap-3"
            aria-label="Search result pages"
          >
            <div className="flex items-center gap-2">
              <Label htmlFor="search-page-size" className="text-xs text-muted-foreground">
                Per page
              </Label>
              <Select
                id="search-page-size"
                value={String(limit)}
                onChange={(event) =>
                  apply({ limit: event.target.value, offset: undefined })
                }
                className="h-8 w-20 text-xs"
              >
                {SEARCH_PAGE_SIZES.map((size) => (
                  <option key={size} value={size}>
                    {size}
                  </option>
                ))}
              </Select>
            </div>

            <p className="text-xs text-muted-foreground">
              Page {page} of {pages}
            </p>

            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!hasPrevious}
                onClick={() => apply({ offset: String(Math.max(0, offset - limit)) })}
              >
                Previous
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!hasNext}
                onClick={() => apply({ offset: String(offset + limit) })}
              >
                Next
              </Button>
            </div>
          </nav>
        </div>
      )}
    </div>
  )
}