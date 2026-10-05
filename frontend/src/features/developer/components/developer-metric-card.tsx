import { CircleAlert, type LucideIcon } from 'lucide-react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { DeveloperEmptyState, DeveloperStaleNotice } from '@/features/developer/components/developer-empty-state'
import { formatDeveloperMetricValueWithUnit } from '@/features/developer/components/developer-format'
import {
  METRIC_ICONS,
  METRIC_UNIT_META,
  NOT_ENOUGH_DATA_TITLE,
} from '@/features/developer/components/developer-vocabulary'
import { formatNumber } from '@/features/analytics/format'
import { cn } from '@/lib/utils'
import type { DeveloperMetricRead } from '@/types/developer'

/**
 * The eight metrics, each showing its value, how it is defined and what it says.
 *
 * ## Why this is not the analytics `MetricCard`
 *
 * `MetricCard` is the right component for a headline figure, and it is reused
 * for the dashboard's summary tiles next door. It is the wrong one here for two
 * reasons the eight-metric brief cares about:
 *
 * 1. **Its label is a `<p>`, and these are cards in a grid.** The page owns one
 *    `<h1>` and every card below it must sit under a section heading as an
 *    `<h3>`, or the document outline is a flat list of unlabelled tiles. A
 *    heading per metric is what lets a reader navigate eight figures by name.
 * 2. **Its explanation lives behind a hover tooltip.** The brief requires each
 *    metric to show its value *and* its definition *and* its explanation. A
 *    tooltip is shown on hover and on focus, which means it is shown to nobody
 *    reading on a touch device and to nobody reading the printed page. The
 *    definition goes in as visible text and so does the explanation.
 *
 * What is reused is everything about *how* a figure reads: `Card` for the frame,
 * `Skeleton` for the loading silhouette, and the `NO_VALUE` convention the
 * analytics formatters already established for a figure that does not exist.
 *
 * ## `available` and `value: null` are one fact, not two
 *
 * `DeveloperMetricRead` carries both, and they are the same information in a
 * positive and a negative form: `available: false` with a
 * `reason_if_unavailable`, and a `value` that is null rather than 0. This card
 * collapses them — the reason replaces the figure, exactly as `MetricCard`
 * documents — because printing `0` beside "not enough data" is the one thing
 * that must never happen here. The zero-denominator case is the live example:
 * `recent_momentum` divides the last seven days by the seven before it, so a
 * quiet fortnight makes the metric *unmeasurable*, not worth zero, and
 * collapsing those two states would report that momentum was nil when in fact
 * there was nothing to divide by.
 *
 * The backend's `reason_if_unavailable` is rendered **verbatim** where it is
 * present, because it names the specific ingredient that was missing and a
 * generic sentence cannot.
 */

export interface DeveloperMetricCardProps {
  metric: DeveloperMetricRead
  /** `h3` inside a page section; pass `h4` when nested one level deeper. */
  titleLevel?: 'h3' | 'h4'
  className?: string
}

export function DeveloperMetricCard({
  metric,
  titleLevel = 'h3',
  className,
}: DeveloperMetricCardProps) {
  const unavailable = !metric.available || metric.value === null
  const Icon: LucideIcon = METRIC_ICONS[metric.key] ?? CircleAlert
  const unit = METRIC_UNIT_META[metric.unit]
  const reason = metric.reason_if_unavailable?.trim() ?? ''

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="flex-row items-start justify-between space-y-0 pb-2">
        <div className="min-w-0 space-y-1">
          <CardTitle level={titleLevel} className="text-sm leading-snug">
            {metric.label}
          </CardTitle>
          <p className="text-xs leading-relaxed text-muted-foreground">{metric.definition}</p>
        </div>
        <span
          aria-hidden="true"
          className="flex size-7 shrink-0 items-center justify-center rounded-md border border-border bg-muted text-muted-foreground"
        >
          <Icon className="size-3.5" />
        </span>
      </CardHeader>

      <CardContent className="space-y-3">
        {unavailable ? (
          <>
            <p className="text-sm font-semibold leading-snug text-muted-foreground">
              {NOT_ENOUGH_DATA_TITLE}
            </p>
            <p className="text-xs leading-relaxed text-muted-foreground">
              {reason.length > 0
                ? reason
                : 'The recorded data does not support a figure for this window.'}
            </p>
          </>
        ) : (
          <>
            <p className="text-2xl font-semibold tabular-nums tracking-tight text-foreground">
              {formatDeveloperMetricValueWithUnit(metric)}
            </p>
            <p className="text-xs leading-relaxed text-foreground/80">{metric.explanation}</p>
          </>
        )}

        <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 border-t border-border pt-2 text-[11px] text-muted-foreground">
          <span className="uppercase tracking-[0.1em]">Unit</span>
          <span>{unit ? unit.label : metric.unit}</span>
          <span aria-hidden="true">·</span>
          <span className="uppercase tracking-[0.1em]">Window</span>
          {/* `null` is a *semantic* whole-history answer and keeps its own words.
              Anything else is a figure, so it is printed by `formatNumber` rather
              than interpolated raw: a template literal calls `ToString` on its
              operand, and a `window_days` that arrived as an object rather than a
              number throws there — taking the dashboard down over a caption. */}
          <span>{metric.window_days === null ? 'Whole history' : `${formatNumber(metric.window_days)} days`}</span>
          <span aria-hidden="true">·</span>
          <span className="truncate font-mono" title={`Read from ${metric.source}`}>
            {metric.source}
          </span>
        </p>
      </CardContent>
    </Card>
  )
}

/* ------------------------------------------------------------------ skeleton */

/**
 * One metric tile's silhouette.
 *
 * **No digits, no placeholder zero.** The tile above it will either print a
 * figure or print "Not enough data yet.", and a grey `0` would be read as the
 * first of those while the second is what the page actually means.
 */
export function DeveloperMetricCardSkeleton({ className }: { className?: string }) {
  return (
    <Card className={cn('min-w-0', className)} aria-hidden="true">
      <CardHeader className="flex-row items-start justify-between space-y-0 pb-2">
        <div className="min-w-0 flex-1 space-y-2">
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-3 w-full" />
        </div>
        <Skeleton className="size-7 shrink-0 rounded-md" />
      </CardHeader>
      <CardContent className="space-y-3">
        <Skeleton className="h-7 w-28" />
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-4/5" />
        <Skeleton className="h-3 w-1/2" />
      </CardContent>
    </Card>
  )
}

export interface DeveloperMetricListSkeletonProps {
  /** Matches the eight tiles the endpoint always returns. */
  count?: number
  className?: string
}

/** The whole list while its first read is in flight. */
export function DeveloperMetricListSkeleton({
  count = 8,
  className,
}: DeveloperMetricListSkeletonProps) {
  return (
    <div
      role="status"
      aria-busy="true"
      className={cn('grid gap-4 sm:grid-cols-2 xl:grid-cols-4', className)}
    >
      <span className="sr-only">Loading recorded metrics</span>
      {Array.from({ length: Math.max(1, count) }, (_, index) => (
        <DeveloperMetricCardSkeleton key={index} />
      ))}
    </div>
  )
}

/* ---------------------------------------------------------------------- list */

export interface DeveloperMetricListProps {
  metrics: readonly DeveloperMetricRead[]
  isLoading?: boolean
  /** A refetch is in flight behind figures already on screen. */
  isStale?: boolean
  emptyReason?: string | null
  titleLevel?: 'h3' | 'h4'
  className?: string
}

/**
 * The eight tiles, in the order the endpoint sends them.
 *
 * **The server's ordering survives** — the list is rendered in the order it
 * arrived rather than sorted by value, because a grid that reorders itself
 * between a window change and its refetch is a grid nobody can learn a position
 * in. `DEVELOPER_METRIC_KEYS` documents the canonical order the backend uses.
 *
 * Loading draws eight silhouettes rather than a spinner so the tile grid keeps
 * its shape; empty gets the shared "Not enough data yet." state, which is what a
 * response with no metrics at all means.
 */
export function DeveloperMetricList({
  metrics,
  isLoading = false,
  isStale = false,
  emptyReason = null,
  titleLevel = 'h3',
  className,
}: DeveloperMetricListProps) {
  if (isLoading) {
    return <DeveloperMetricListSkeleton count={8} className={className} />
  }

  if (metrics.length === 0) {
    return (
      <DeveloperEmptyState
        variant="metrics"
        reason={emptyReason}
        className={cn('rounded-lg border border-border bg-card', 'min-h-[12rem]', className)}
      />
    )
  }

  return (
    <div className={cn('space-y-3', className)}>
      <DeveloperStaleNotice isStale={isStale} subject="the metrics" />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {metrics.map((metric) => (
          <DeveloperMetricCard
            key={metric.key}
            metric={metric}
            titleLevel={titleLevel}
            className="h-full"
          />
        ))}
      </div>
    </div>
  )
}