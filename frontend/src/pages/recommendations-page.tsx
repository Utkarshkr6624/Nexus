import { useCallback, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CircleAlert, Lightbulb } from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { LiveStatus } from '@/components/feedback/live-status'
import { PageHeader } from '@/components/feedback/page-header'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { RecommendationCard } from '@/features/risk/components/recommendation-card'
import { RecommendationListSkeleton } from '@/features/risk/components/recommendation-list-skeleton'
import { RiskEmptyState } from '@/features/risk/components/risk-empty-state'
import {
  RECOMMENDATION_STATUS_META,
  SEVERITY_META,
} from '@/features/risk/components/risk-vocabulary'
import {
  useAcceptRecommendation,
  useCompleteRecommendation,
  useRecommendations,
  useRejectRecommendation,
} from '@/features/risk/hooks'
import { toApiError } from '@/services/errors'
import { RECOMMENDATION_PRIORITIES, RECOMMENDATION_STATUSES } from '@/types/risk'
import type { StatusMeta } from '@/types/work'
import type {
  RecommendationPriority,
  RecommendationRead,
  RecommendationStatus,
  UUIDString,
} from '@/types/risk'

/**
 * The suggestions a detected risk has raised, grouped by how soon they want an
 * answer.
 *
 * **Grouping is by priority, and the priority word is the group's heading.** The
 * backend derives priority from the risk's severity — one source of truth — so
 * a client that ranked the groups itself would be reimplementing
 * `risk_severity_for`. The grouping here is a `Map` filled in the order
 * `RECOMMENDATION_PRIORITIES` declares (most severe first), so an unranked sort
 * cannot quietly promote `medium` above `high`.
 *
 * **Each group heading carries an icon and a word, never colour alone** — the
 * same contract `SeverityBadge` discharges on every card. A group whose only
 * signal was a red or amber edge would be unreadable in dark mode, to a reader
 * with a colour vision deficiency, and to a screen reader, so the heading repeats
 * what the card already states in words.
 *
 * **A suggestion is always shown with its WHY.** `RecommendationCard` renders
 * `reason` in full and never elides it, because the backend makes an empty
 * reason unconstructible and the imperative on its own is precisely the failure
 * the schema exists to prevent. A reader who disagrees with the suggestion can
 * do so on the evidence, which is the whole reason the engine records it.
 *
 * **The three actions are three answers, not three intensities.** Accepting
 * records "I will do this" and changes nothing about the work; "Mark completed"
 * is reachable only after an acceptance, which is the lifecycle the backend
 * enforces; "Not for me" records a decline, which is a legitimate answer and is
 * not a judgement on the underlying risk. None of the three is styled as
 * destructive.
 *
 * **The status filter defaults to `new`.** `GET /recommendations` takes one
 * status at a time — `api-client`'s serialiser cannot emit a repeated key — so
 * the open set (`new` plus `viewed`) is not expressible as one query. `new` is
 * the backend's own recommendation for this screen, and it is the set that is
 * still waiting on an answer. The filter offers every other state, including
 * "All statuses" for the answered history.
 *
 * **No header sentence is composed here.** `RecommendationListRead` is frozen at
 * five fields and carries no `summary`, so the count line under the filter is
 * built from `by_priority` — real counts, printed in the backend's own register:
 * the numbers, and nothing about the person they describe.
 */

/** The page size the list asks for, matching the Risk Center's. */
const PAGE_SIZE = 20

/** Stable empty array so the row memo is not re-created on every render. */
const NO_ROWS: RecommendationRead[] = []

/** The status the list shows before the user narrows it. */
const DEFAULT_STATUS: RecommendationStatus = 'new'

/** The URL word for "every status", distinct from the absent parameter. */
const ALL_STATUSES = 'all'

interface PriorityGroup {
  priority: string
  items: RecommendationRead[]
}

/**
 * The band metadata for a priority word the client may not know.
 *
 * `SEVERITY_META` covers the four bands the backend ships today, but a payload
 * from a newer engine can carry a word this build has never heard of. Falling
 * back to the server's own word with a neutral icon keeps the row readable
 * rather than rendering a heading with nothing in it.
 */
function priorityMeta(priority: string): StatusMeta {
  return (
    SEVERITY_META[priority as RecommendationPriority] ?? {
      label: priority.charAt(0).toUpperCase() + priority.slice(1),
      icon: CircleAlert,
      tone: 'neutral',
      description: 'A priority band this build does not have a definition for.',
    }
  )
}

/**
 * Buckets one page by priority, most severe first.
 *
 * Every band present in the page gets a group, and a band with no rows gets
 * none — an empty section heading is the "wall of zeroes" this surface is
 * written to avoid. Rows the client does not recognise the priority of are kept
 * in a trailing bucket rather than dropped, because a suggestion the user has
 * not answered must not disappear because a newer engine shipped a band.
 */
function groupByPriority(items: readonly RecommendationRead[]): PriorityGroup[] {
  const buckets = new Map<string, RecommendationRead[]>()
  for (const item of items) {
    const existing = buckets.get(item.priority)
    if (existing) existing.push(item)
    else buckets.set(item.priority, [item])
  }

  const ordered: PriorityGroup[] = []
  const claimed = new Set<string>()
  for (const priority of RECOMMENDATION_PRIORITIES) {
    const rows = buckets.get(priority)
    if (rows) {
      ordered.push({ priority, items: rows })
      claimed.add(priority)
    }
  }
  for (const [priority, rows] of buckets) {
    if (!claimed.has(priority)) ordered.push({ priority, items: rows })
  }
  return ordered
}

/**
 * Reads a URL parameter back into a union member, or `undefined`.
 *
 * An unrecognised word is dropped rather than sent: the backend validates the
 * status against its enum and answers `?status=nonsense` with a 422, and a
 * hand-edited link should not be able to turn the page into an error.
 */
function fromParam<T extends string>(
  value: string | null,
  allowed: readonly string[],
): T | undefined {
  return value !== null && allowed.includes(value) ? (value as T) : undefined
}

function pageFromParam(value: string | null): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 1
}

/** A refused transition, in one sentence, per card. */
function transitionMessage(error: ReturnType<typeof toApiError>): string {
  if (error.status === 404) {
    return 'This suggestion is no longer available.'
  }
  if (error.status === 409) {
    return 'This suggestion has already been answered, so this action no longer applies to it.'
  }
  if (error.status === 403) {
    return 'This suggestion belongs to another account.'
  }
  if (error.isTimeout) {
    return 'The backend did not answer in time. Nothing was changed — try again.'
  }
  return 'The answer was not recorded. Nothing was changed — try again.'
}

export default function RecommendationsPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  /**
   * The rows the transitions returned, overlaid on the fetched page.
   *
   * The hooks write the returned row into the detail cache and invalidate the
   * list; this overlay additionally redraws the card from the server's own
   * answer, which is the only version of "accepted" guaranteed to agree with
   * what was stored. On the default `new` filter the row then leaves the view
   * on the next read, because an answered suggestion is no longer waiting.
   */
  const [overrides, setOverrides] = useState<Record<UUIDString, RecommendationRead>>({})

  const rawStatus = searchParams.get('status')
  // An absent `status` already means "the default, which is New", so "All
  // statuses" needs a word of its own: deleting the parameter would leave the
  // control reading "All statuses" beside a list filtered to New.
  const status: RecommendationStatus | undefined =
    rawStatus === ALL_STATUSES
      ? undefined
      : (fromParam<RecommendationStatus>(rawStatus, RECOMMENDATION_STATUSES) ??
        DEFAULT_STATUS)
  // The control mirrors what the query is doing, so a hand-typed word it does
  // not recognise cannot leave the select blank beside a differently filtered
  // list.
  const statusValue = status ?? ALL_STATUSES
  const page = pageFromParam(searchParams.get('page'))

  const apply = useCallback(
    (patch: Record<string, string | undefined>, resetPage = true) => {
      const next = new URLSearchParams(searchParams)
      for (const [key, value] of Object.entries(patch)) {
        if (value === undefined) next.delete(key)
        else next.set(key, value)
      }
      if (resetPage) next.delete('page')
      setSearchParams(next)
    },
    [searchParams, setSearchParams],
  )

  const list = useRecommendations({
    status,
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
  })
  const accept = useAcceptRecommendation()
  const reject = useRejectRecommendation()
  const complete = useCompleteRecommendation()

  const fetched: RecommendationRead[] = list.data?.items ?? NO_ROWS
  const items = useMemo(
    () => fetched.map((row) => overrides[row.id] ?? row),
    [fetched, overrides],
  )
  const groups = useMemo(() => groupByPriority(items), [items])

  const total = list.data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const filtering = (status ?? DEFAULT_STATUS) !== DEFAULT_STATUS

  const record = useCallback((updated: RecommendationRead) => {
    setOverrides((previous) => ({ ...previous, [updated.id]: updated }))
  }, [])

  const onAccept = useCallback(
    (row: RecommendationRead) => {
      accept.mutate(row.id, { onSuccess: record })
    },
    [accept, record],
  )
  const onReject = useCallback(
    (row: RecommendationRead) => {
      reject.mutate(row.id, { onSuccess: record })
    },
    [reject, record],
  )
  const onComplete = useCallback(
    (row: RecommendationRead) => {
      complete.mutate(row.id, { onSuccess: record })
    },
    [complete, record],
  )

  const actingId: UUIDString | undefined = accept.isPending
    ? accept.variables
    : reject.isPending
      ? reject.variables
      : complete.isPending
        ? complete.variables
        : undefined

  /**
 * The failure belonging to one card.
 *
 * Each mutation hook keeps its own error, and `variables` still names the row
 * it was called with after the call settles — which is what lets the message be
 * attributed to the card it happened on rather than shown once for the page.
 */
const actionErrorFor = (id: UUIDString): string | null => {
    if (accept.isError && accept.variables === id) {
      return transitionMessage(toApiError(accept.error))
    }
    if (reject.isError && reject.variables === id) {
      return transitionMessage(toApiError(reject.error))
    }
    if (complete.isError && complete.variables === id) {
      return transitionMessage(toApiError(complete.error))
    }
    return null
  }

  return (
    <div className="app-container space-y-6 py-6 lg:py-8">
      <PageHeader
        title="Recommendations"
        eyebrow={
          <>
            <Lightbulb className="size-3.5" aria-hidden="true" />
            Suggested actions
          </>
        }
        description={
          'Each suggestion comes from a detected risk, carries the reason it was raised, ' +
          'and proposes something you do. NEXUS records your answer and changes nothing ' +
          'about the work itself.'
        }
      />

      <section className="space-y-3" aria-labelledby="recommendation-filters">
        {/* The filter bar carries no visible title, so the region is named for
            assistive technology only — a landmark a screen-reader user can
            navigate to, rather than an anonymous `<section>`. */}
        <h2 id="recommendation-filters" className="sr-only">
          Filters
        </h2>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <div className="app-form-field">
              <Label htmlFor="recommendation-status">Status</Label>
              <Select
                id="recommendation-status"
                value={statusValue}
                onChange={(event) => apply({ status: event.target.value || ALL_STATUSES })}
              >
                <option value={ALL_STATUSES}>All statuses</option>
                {RECOMMENDATION_STATUSES.map((entry) => (
                  <option key={entry} value={entry}>
                    {RECOMMENDATION_STATUS_META[entry].label}
                  </option>
                ))}
              </Select>
            </div>
          </div>

          {list.data && <p className="mt-3 text-xs text-muted-foreground">{countSentence(
            list.data.total,
            list.data.by_priority,
          )}</p>}
        </div>

        <LiveStatus active={list.isPlaceholderData} className="text-xs text-muted-foreground">
          Updating for the selected filter…
        </LiveStatus>
      </section>

      {list.isError && !list.isPlaceholderData ? (
        <ErrorState
          error={toApiError(list.error)}
          title="The suggestions could not load"
          onRetry={() => void list.refetch()}
        />
      ) : list.isPending && !list.data ? (
        <RecommendationListSkeleton count={3} />
      ) : groups.length === 0 ? (
        <RiskEmptyState variant={filtering ? 'filtered' : 'recommendations'} />
      ) : (
        <>
          <div className="space-y-8">
            {groups.map((group) => {
              const meta = priorityMeta(group.priority)
              const Icon = meta.icon
              const headingId = `priority-${group.priority}`
              return (
                <section key={group.priority} className="space-y-3" aria-labelledby={headingId}>
                  <div className="flex items-center gap-2">
                    <Icon
                      aria-hidden="true"
                      className="size-4 shrink-0 text-muted-foreground"
                    />
                    <h2
                      id={headingId}
                      className="text-sm font-semibold tracking-tight text-foreground"
                    >
                      {meta.label} priority
                    </h2>
                    <span className="text-xs text-muted-foreground">
                      {group.items.length} suggestion{group.items.length === 1 ? '' : 's'}
                    </span>
                  </div>

                  <div className="space-y-4">
                    {group.items.map((row) => (
                      <RecommendationCard
                        key={row.id}
                        recommendation={row}
                        onAccept={onAccept}
                        onReject={onReject}
                        onComplete={onComplete}
                        isActing={actingId === row.id}
                        actionError={actionErrorFor(row.id)}
                        // The Risk Center is where a finding is read in full, so
                        // the card's "View the finding" link goes there. It is
                        // offered only when the suggestion names a risk at all,
                        // which is the card's own condition for drawing it.
                        riskHref={(candidate) =>
                          candidate.risk_id === null ? null : '/risks'
                        }
                      />
                    ))}
                  </div>
                </section>
              )
            })}
          </div>

          {total > PAGE_SIZE && (
            <nav className="flex items-center justify-between gap-3" aria-label="Suggestion pages">
              <p className="text-xs text-muted-foreground">
                Page {page} of {totalPages} · {total} matching suggestion
                {total === 1 ? '' : 's'}
              </p>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page <= 1}
                  onClick={() =>
                    apply({ page: page - 1 > 1 ? String(page - 1) : undefined }, false)
                  }
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= totalPages}
                  onClick={() =>
                    apply({ page: page + 1 > 1 ? String(page + 1) : undefined }, false)
                  }
                >
                  Next
                </Button>
              </div>
            </nav>
          )}
        </>
      )}
    </div>
  )
}

/**
 * One factual sentence from the counts the server sent.
 *
 * `RecommendationListRead` carries no composed sentence of its own, so this is
 * built from `by_priority` rather than invented — the numbers are real, and
 * bands with nothing in them are left out rather than printed as a zero. The
 * register matches the risk list's: what was counted, and nothing about the
 * person it was counted from.
 */
function countSentence(
  total: number,
  byPriority: Record<string, number>,
): string {
  const bands = RECOMMENDATION_PRIORITIES.filter(
    (priority) => (byPriority[priority] ?? 0) > 0,
  ).map((priority) => `${byPriority[priority]} ${SEVERITY_META[priority].label.toLowerCase()}`)

  if (total === 0) return 'No suggestions match this filter.'
  if (bands.length === 0) return `${total} suggestion${total === 1 ? '' : 's'}.`
  return `${total} suggestion${total === 1 ? '' : 's'}: ${bands.join(', ')}.`
}
