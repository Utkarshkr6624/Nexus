import { useCallback, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Play, ShieldAlert } from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { LiveStatus } from '@/components/feedback/live-status'
import { PageHeader } from '@/components/feedback/page-header'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { RiskCard } from '@/features/risk/components/risk-card'
import { RiskEmptyState } from '@/features/risk/components/risk-empty-state'
import { RiskListSkeleton } from '@/features/risk/components/risk-list-skeleton'
import { RiskSummaryTiles } from '@/features/risk/components/risk-summary-tiles'
import { RISK_STATUS_META, SEVERITY_META } from '@/features/risk/components/risk-vocabulary'
import {
  useAcknowledgeRisk,
  useDismissRisk,
  useResolveRisk,
  useRiskSummary,
  useRisks,
  useRunEvaluation,
} from '@/features/risk/hooks'
import { toApiError } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import { RISK_SEVERITIES, RISK_STATUSES, RISK_TYPES } from '@/types/risk'
import type {
  RiskRead,
  RiskSeverity,
  RiskStatus,
  RiskType,
  UUIDString,
} from '@/types/risk'

/**
 * The Risk Center: every condition the detection engine currently considers
 * live, with the evidence behind it and the three answers it accepts.
 *
 * **The page composes existing components rather than drawing its own rows.**
 * `RiskSummaryTiles` owns the header row and its band links, `RiskCard` owns a
 * finding and its transitions, `RiskListSkeleton` and `RiskEmptyState` own the
 * two states where a list is not yet rows. What is left for this file is the
 * part none of them can decide for themselves: which filters are in force,
 * where they live, and what happens when one of them changes.
 *
 * **Every filter is in the URL, including the severity band.** `?severity=`,
 * `?status=`, `?risk_type=` and `?page=` make the view shareable and survive a
 * reload, and they make the back button walk out of a filter rather than out of
 * the page. The band used to be narrowed in the browser, which meant the pager
 * had to be withdrawn under it: paging a client-side filter over one server page
 * would show fewer rows than the band actually holds. `GET /risks` now takes
 * `severity`, so the narrowing happens in one indexed query and the pager works
 * under every filter.
 *
 * **The status filter defaults to `active`, and that is a visible choice rather
 * than a hidden one.** The select reads "Active" on arrival and the tiles above
 * report the *live* set (`active` plus `acknowledged`), so the two are not the
 * same question: the header asks how loud things are, the list asks what is
 * still unanswered. Acknowledging a card therefore removes it from this view on
 * the next read — which is the point of acknowledging, and why the card is
 * redrawn from the row the server returned before the list refetches.
 *
 * **Empty is stated as a result, never as a wall of zeroes.** `RiskSummaryTiles`
 * replaces the whole header row with `RiskEmptyState` when nothing is recorded,
 * and when nothing at all is recorded this page drops its filter bar and list
 * rather than printing four zeroes above an empty table. Four zeroes read as a
 * measurement, and the engine running and finding nothing is the opposite of
 * one.
 *
 * **Language is neutral.** The copy here describes what the data says and what
 * the engine does; it never characterises the person. The engine proposes and
 * never performs, so "Accept" records a decision and "Not for me" records a
 * decline — neither is styled as a failure.
 */

/** The page size the list asks for. The backend's default, stated here so the
 *  pager's arithmetic does not depend on a server value changing silently. */
const PAGE_SIZE = 20

/** Stable empty array so the row memo below is not re-created each render. */
const NO_ROWS: RiskRead[] = []

/** The status the list shows before the user narrows it. */
const DEFAULT_STATUS: RiskStatus = 'active'

/** The URL word for "every status", distinct from the absent parameter. */
const ALL_STATUSES = 'all'

/**
 * Risk type names for the filter.
 *
 * `RISK_TYPES` is a closed set on the client, but the backend enum can grow, so
 * the lookup falls back to the server's own word rather than rendering an empty
 * option.
 */
const RISK_TYPE_LABELS: Record<RiskType, string> = {
  deadline: 'Deadline',
  workload: 'Workload',
  project: 'Project',
  task: 'Task',
  scheduling: 'Scheduling',
  estimation: 'Estimation',
  consistency: 'Consistency',
}

function typeLabel(type: RiskType): string {
  return (
    RISK_TYPE_LABELS[type] ?? type.charAt(0).toUpperCase() + type.slice(1).replace(/_/g, ' ')
  )
}

/**
 * Reads a URL parameter back into a union member, or `undefined`.
 *
 * A hand-edited link or a band link written by an older build must not put an
 * unknown word on the wire: `?status=nonsense` is a 422 server-side, so an
 * unrecognised value is dropped here and the page renders its unfiltered view
 * instead of an error the user cannot act on.
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

/**
 * Where the affected entity lives.
 *
 * There is no per-task or per-project detail route on this surface, so the card
 * links to the list the entity is filed under rather than to a URL that would
 * answer "not found". Returning `null` is honest and the card renders the kind
 * as plain text instead.
 */
function entityHref(risk: RiskRead): string | null {
  if (risk.entity_type === 'task') return '/tasks'
  if (risk.entity_type === 'project') return '/projects'
  return null
}

/**
 * What a refused transition says.
 *
 * The three refusals this page can meet are distinguished on purpose, because
 * each one means something different and only one of them is worth retrying: a
 * 404 is a row that is no longer there, a 409 is a row that has already been
 * closed, and a 403 is somebody else's. The shared `ErrorState` maps them to
 * page-level prose; a card three rows down needs a sentence.
 */
function transitionMessage(error: ReturnType<typeof toApiError>): string {
  if (error.status === 404) {
    return 'This risk is no longer available. It has already been closed.'
  }
  if (error.status === 409) {
    return 'This risk has already been closed, so this action no longer applies to it.'
  }
  if (error.status === 403) {
    return 'This risk belongs to another account.'
  }
  if (error.isTimeout) {
    return 'The backend did not answer in time. Nothing was changed — try again.'
  }
  return 'The change was not recorded. Nothing was changed — try again.'
}

export default function RiskCenterPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  /**
   * The rows the transitions returned, overlaid on the fetched page.
   *
   * The hooks already write the row into the detail cache and invalidate the
   * lists, so this is belt-and-braces with a purpose: on the default `active`
   * filter an acknowledged risk leaves the view on the next read, and without
   * the overlay the card would show its old status for the length of that
   * refetch. Reflecting the server's own row is also the only version of
   * "acknowledged" that is guaranteed to agree with what was stored.
   */
  const [overrides, setOverrides] = useState<Record<UUIDString, RiskRead>>({})

  const severity = fromParam<RiskSeverity>(searchParams.get('severity'), RISK_SEVERITIES)
  const riskType = fromParam<RiskType>(searchParams.get('risk_type'), RISK_TYPES)
  const page = pageFromParam(searchParams.get('page'))

  /**
   * "Every status" needs a word of its own.
   *
   * An *absent* `status` already means "the default, which is Active", so a
   * control whose "All statuses" option deleted the parameter would show
   * "All statuses" selected beside a list filtered to Active. The sentinel
   * makes the two distinguishable in the URL, which is also what lets the
   * selection survive a reload and be linked to.
   */
  const rawStatus = searchParams.get('status')
  const status: RiskStatus | undefined =
    rawStatus === ALL_STATUSES
      ? undefined
      : (fromParam<RiskStatus>(rawStatus, RISK_STATUSES) ?? DEFAULT_STATUS)
  // The control mirrors what the query is doing, so a hand-typed word it does
  // not recognise cannot leave the select blank beside a list that is filtered
  // by something else.
  const statusValue = status ?? ALL_STATUSES

  const apply = useCallback(
    (patch: Record<string, string | undefined>, resetPage = true) => {
      const next = new URLSearchParams(searchParams)
      for (const [key, value] of Object.entries(patch)) {
        if (value === undefined) next.delete(key)
        else next.set(key, value)
      }
      // A filter change invalidates the page number. Leaving `page=3` behind
      // after narrowing to a set of two rows would land the reader on an empty
      // third page and call it an absence.
      if (resetPage) next.delete('page')
      setSearchParams(next)
    },
    [searchParams, setSearchParams],
  )

  const summary = useRiskSummary()
  const list = useRisks({
    status,
    risk_type: riskType,
    severity,
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
  })
  const evaluate = useRunEvaluation()

  const acknowledge = useAcknowledgeRisk()
  const dismiss = useDismissRisk()
  const resolve = useResolveRisk()

  const fetched: RiskRead[] = list.data?.items ?? NO_ROWS
  const items = useMemo(
    () => fetched.map((risk) => overrides[risk.id] ?? risk),
    [fetched, overrides],
  )

  // `total` is the server's count for the *filtered* set, band included, so the
  // pager is driven from it under every filter rather than withdrawn.
  const total = list.data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const bandFiltered = severity !== undefined

  // Nothing is recorded anywhere: the header row has already said so, so the
  // filter bar and the list below it are not drawn at all.
  const nothingRecorded = summary.data !== undefined && summary.data.total === 0
  const filtering =
    riskType !== undefined || bandFiltered || (status ?? DEFAULT_STATUS) !== DEFAULT_STATUS
  // "Nothing matched" and "nothing exists" are different claims, and the tiles
  // are the only thing on the page that knows which one is true.
  const emptyVariant = (summary.data ? summary.data.total > 0 : filtering)
    ? 'filtered'
    : 'risks'

  const buildSeverityHref = useCallback(
    (band: RiskSeverity) => {
      const next = new URLSearchParams(searchParams)
      next.set('severity', band)
      next.delete('page')
      return `/risks?${next.toString()}`
    },
    [searchParams],
  )

  const record = useCallback((updated: RiskRead) => {
    setOverrides((previous) => ({ ...previous, [updated.id]: updated }))
  }, [])

  const onAcknowledge = useCallback(
    (risk: RiskRead) => {
      acknowledge.mutate(risk.id, { onSuccess: record })
    },
    [acknowledge, record],
  )
  const onDismiss = useCallback(
    (risk: RiskRead) => {
      dismiss.mutate(risk.id, { onSuccess: record })
    },
    [dismiss, record],
  )
  const onResolve = useCallback(
    (risk: RiskRead) => {
      resolve.mutate(risk.id, { onSuccess: record })
    },
    [resolve, record],
  )

  /** Which card is waiting on the network, if any. */
  const actingId: UUIDString | undefined = acknowledge.isPending
    ? acknowledge.variables
    : dismiss.isPending
      ? dismiss.variables
      : resolve.isPending
        ? resolve.variables
        : undefined

  /**
   * The failure belonging to one card.
   *
   * Each mutation hook keeps its own error, and `variables` still names the row
   * it was called with after the call settles — which is what lets the message
   * be attributed to a card rather than shown once for the whole page.
   */
  const actionErrorFor = (id: UUIDString): string | null => {
    if (acknowledge.isError && acknowledge.variables === id) {
      return transitionMessage(toApiError(acknowledge.error))
    }
    if (dismiss.isError && dismiss.variables === id) {
      return transitionMessage(toApiError(dismiss.error))
    }
    if (resolve.isError && resolve.variables === id) {
      return transitionMessage(toApiError(resolve.error))
    }
    return null
  }

  const runDetection = useCallback(() => {
    evaluate.mutate(undefined, {
      onSuccess: (run) => {
        // A pass that judged nothing is a normal answer, not a failure, so it
        // is reported in the backend's own words rather than as zeroes.
        if (!run.evaluated) {
          toast.info('Nothing to evaluate', run.reason_if_not_evaluated ?? undefined)
          return
        }
        toast.success(
          'Detection run complete',
          [
            `${run.risks_found} live`,
            `${run.risks_created} new`,
            `${run.risks_resolved} resolved`,
          ].join(', ') + `, in ${run.duration_ms} ms.`,
        )
      },
      onError: (cause) => {
        toast.error('The detection run did not complete', toApiError(cause).message)
      },
    })
  }, [evaluate])

  return (
    <div className="app-container space-y-6 py-6 lg:py-8">
      <PageHeader
        title="Risk Center"
        eyebrow={
          <>
            <ShieldAlert className="size-3.5" aria-hidden="true" />
            Detected conditions
          </>
        }
        actions={
          <Button type="button" onClick={runDetection} disabled={evaluate.isPending}>
            {evaluate.isPending ? 'Running…' : 'Run detection now'}
            <Play aria-hidden="true" />
          </Button>
        }
        description={
          'Every finding below was raised by the detection engine from your own recorded ' +
          'work, and every one carries the evidence it was scored from. Nothing here ' +
          'describes you: it describes what the data currently shows.'
        }
      />

      {summary.isPending && !summary.data ? (
        <SummaryTilesSkeleton />
      ) : summary.data ? (
        <RiskSummaryTiles
          summary={summary.data}
          activeSeverity={severity ?? null}
          buildHref={buildSeverityHref}
        />
      ) : summary.error ? (
        <ErrorState
          error={toApiError(summary.error)}
          title="The risk counts could not load"
          onRetry={() => void summary.refetch()}
          compact
        />
      ) : null}

      {nothingRecorded ? null : (
        <>
          <section className="space-y-3" aria-labelledby="risk-filters">
            {/* The filter bar is a card with no visible title, so the section
                gets one for assistive technology only: a landmark named
                "Filters" is what a screen-reader user navigates by, and a bare
                `<section>` would expose an unnamed region instead. */}
            <h2 id="risk-filters" className="sr-only">
              Filters
            </h2>
            <div className="rounded-lg border border-border bg-card p-3">
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                <div className="app-form-field">
                  <Label htmlFor="risk-severity">Severity band</Label>
                  <Select
                    id="risk-severity"
                    value={severity ?? ''}
                    onChange={(event) =>
                      apply({ severity: event.target.value || undefined })
                    }
                  >
                    <option value="">All bands</option>
                    {RISK_SEVERITIES.map((band) => (
                      <option key={band} value={band}>
                        {SEVERITY_META[band].label}
                      </option>
                    ))}
                  </Select>
                </div>

                <div className="app-form-field">
                  <Label htmlFor="risk-status">Status</Label>
                  <Select
                    id="risk-status"
                    value={statusValue}
                    onChange={(event) =>
                      apply({ status: event.target.value || ALL_STATUSES })
                    }
                  >
                    <option value={ALL_STATUSES}>All statuses</option>
                    {RISK_STATUSES.map((entry) => (
                      <option key={entry} value={entry}>
                        {RISK_STATUS_META[entry].label}
                      </option>
                    ))}
                  </Select>
                </div>

                <div className="app-form-field">
                  <Label htmlFor="risk-type">Risk type</Label>
                  <Select
                    id="risk-type"
                    value={riskType ?? ''}
                    onChange={(event) =>
                      apply({ risk_type: event.target.value || undefined })
                    }
                  >
                    <option value="">All types</option>
                    {RISK_TYPES.map((entry) => (
                      <option key={entry} value={entry}>
                        {typeLabel(entry)}
                      </option>
                    ))}
                  </Select>
                </div>
              </div>

              {list.data?.summary && (
                <p className="mt-3 text-xs text-muted-foreground">{list.data.summary}</p>
              )}
            </div>

            {/* A refetch under a changed filter keeps the previous page on screen
                rather than blanking, so the reader is told the rows are the
                previous answer instead of being shown them as the current one. */}
            <LiveStatus active={list.isPlaceholderData} className="text-xs text-muted-foreground">
              Updating for the selected filters…
            </LiveStatus>
          </section>

          {list.isError && !list.isPlaceholderData ? (
            <ErrorState
              error={toApiError(list.error)}
              title="The risk list could not load"
              onRetry={() => void list.refetch()}
            />
          ) : list.isPending && !list.data ? (
            <RiskListSkeleton count={3} />
          ) : items.length === 0 ? (
            <RiskEmptyState variant={emptyVariant} />
          ) : (
            <>
              <div className="space-y-4">
                {items.map((risk) => (
                  <RiskCard
                    key={risk.id}
                    risk={risk}
                    entityHref={entityHref}
                    onAcknowledge={onAcknowledge}
                    onDismiss={onDismiss}
                    onResolve={onResolve}
                    isActing={actingId === risk.id}
                    actionError={actionErrorFor(risk.id)}
                    titleLevel="h2"
                  />
                ))}
              </div>

              {/* `total` is the server's count for the filtered set, band
                  included, so this is correct under every combination. */}
              {total > PAGE_SIZE && (
                <nav className="flex items-center justify-between gap-3" aria-label="Risk pages">
                  <p className="text-xs text-muted-foreground">
                    Page {page} of {totalPages} · {total} matching risk
                    {total === 1 ? '' : 's'}
                  </p>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={page <= 1}
                      // `page=1` is the absence of a page, so it is written as an
                      // absent parameter rather than a literal one.
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
        </>
      )}
    </div>
  )
}

/**
 * The header row's own placeholder.
 *
 * `RiskSummaryTiles` has no loading state of its own — it is a component that
 * reads counts, and a component that reads counts cannot invent them — so the
 * four cards are reserved here instead. The skeletons are labelled and marked
 * busy, and nothing in them resembles a number: a pulse in the shape of a count
 * is a count to anyone glancing at it, and an absent finding is not a zero.
 */
function SummaryTilesSkeleton() {
  return (
    <div role="status" aria-busy="true" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <span className="sr-only">Loading the risk counts</span>
      {Array.from({ length: 4 }, (_, index) => (
        <Card key={index} className="min-w-0" aria-hidden="true">
          <CardContent className="space-y-2 p-6">
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-7 w-12" />
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
