import type { ReactNode } from 'react'
import {
  Briefcase,
  CalendarClock,
  FolderCheck,
  Images,
  PenLine,
  type LucideIcon,
} from 'lucide-react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { MetricCard } from '@/features/analytics/components/metric-card'
import { formatCareerDate } from '@/features/career/components/career-format'
import {
  CareerEmptyState,
  CareerRegionError,
  CareerStaleNotice,
} from '@/features/career/components/career-empty-state'
import { formatNumber } from '@/features/analytics/format'
import { cn } from '@/lib/utils'
import type { ApiError } from '@/lib/api-client'
import type { CareerSummaryRead } from '@/types/learning'

/**
 * The headline counts for a career profile.
 *
 * ## Counts, and the hints say what they count
 *
 * Every tile is a count of records the profile holds: rows of each kind, evidence
 * rows, rows that link to something. **There is no readiness score, no employer
 * match and no "fit for the role" anywhere on this surface**, and the absence is
 * the design: a score derived from records that do not support one is a verdict
 * about a person, which is the failure this phase exists to prevent. A reader who
 * wants a judgement is not given one by NEXUS wearing a number's clothes.
 *
 * ## Five tiles, five counters the route actually sends
 *
 * The labels here are not decoration over a count: each one names the field it
 * reads, because this row once carried `Experience`, `Education`, `Certifications`
 * and `Linked evidence` over four fields `GET /career/summary` does not send. A
 * tile rendered `—` for a figure the backend *had* counted, and the dash is this
 * project's sentence for *not measured* — so a misnamed field borrowed a meaning
 * it did not have. The set below is drawn from `record_count`,
 * `evidence_count`, `evidence_in_window`, `manual_evidence_count` and
 * `completed_project_count`: what is on the CV, what is in the portfolio, how
 * recent it is, how much of it the person wrote, and what finished.
 *
 * `record_count` is a **merged** figure — education, experience and certifications
 * share one table on the server — so its label says "dated records" rather than
 * promising the three-way split the route stopped sending. `completed_project_count`
 * is read from the project's own status column, never inferred from the evidence
 * table, and its hint says so.
 *
 * The cold-start row refuses to render at all: `has_data: false` means there is
 * nothing to summarise, and four zeroes across the top of an empty profile reads
 * as a measurement rather than as the absence of one. On that flag the tiles are
 * replaced by the shared empty state, which explains what has to be added first.
 * `has_profile` is checked separately, so "no profile yet" and "a profile with
 * nothing on it yet" stay two states.
 *
 * The summary sentence is the backend's own. `CareerSummaryRead.summary` is
 * rendered **verbatim**, so the wording has one owner rather than one per screen —
 * eight headers paraphrasing the same counts would eventually paraphrase them
 * differently, and one of them would add an adjective the backend never wrote.
 */
export interface CareerSummaryTilesProps {
  summary: CareerSummaryRead | null
  isLoading?: boolean
  isStale?: boolean
  error?: ApiError | null
  onRetry?: () => void
  title?: string
  subtitle?: ReactNode
  titleLevel?: 'h3' | 'h4'
  className?: string
}

export function CareerSummaryTiles({
  summary,
  isLoading = false,
  isStale = false,
  error = null,
  onRetry,
  title = 'Career summary',
  subtitle,
  titleLevel = 'h3',
  className,
}: CareerSummaryTilesProps) {
  if (isLoading) return <CareerSummaryTilesSkeleton className={className} />

  if (error) {
    return (
      <div className={cn('rounded-lg border border-border bg-card p-4', className)}>
        <CareerRegionError error={error} onRetry={onRetry} subject="the career summary" />
      </div>
    )
  }

  if (!summary?.has_data) {
    return (
      <CareerEmptyState
        variant="summary"
        className={cn('rounded-lg border border-border bg-card', 'min-h-[12rem]', className)}
      />
    )
  }

  const tiles: { key: string; label: string; value: string; hint: string; icon: LucideIcon }[] = [
    {
      key: 'record_count',
      label: 'Dated records',
      value: formatNumber(summary.record_count),
      hint: 'Education, roles and certifications you listed. The summary counts them as one figure rather than three, so the per-kind split is the dated-records list’s job',
      icon: Briefcase,
    },
    {
      key: 'evidence_count',
      label: 'Evidence',
      value: formatNumber(summary.evidence_count),
      hint: 'Rows you added or that a subsystem derived from a record you created',
      icon: Images,
    },
    {
      // The window is named in the label rather than left to the hint: a figure
      // that is date-bounded cannot be quoted without its range, and this label
      // is the only thing that travels when a tile is copied out of context.
      key: 'evidence_in_window',
      label: `Evidence, last ${formatNumber(summary.window_days)} days`,
      value: formatNumber(summary.evidence_in_window),
      hint: 'Evidence dated inside that window, over the whole history before it',
      icon: CalendarClock,
    },
    {
      key: 'manual_evidence_count',
      label: 'Entered by you',
      value: formatNumber(summary.manual_evidence_count),
      hint: 'Of the evidence above, how much you typed in rather than a subsystem recording',
      icon: PenLine,
    },
    {
      key: 'completed_project_count',
      label: 'Projects completed',
      value: formatNumber(summary.completed_project_count),
      hint: 'Projects that reached completed, read from the project’s own status. It says what finished, not what it achieved',
      icon: FolderCheck,
    },
  ]

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="pb-4">
        <CardTitle level={titleLevel}>{title}</CardTitle>
        {subtitle && <p className="text-sm text-muted-foreground">{subtitle}</p>}
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-5">
          {tiles.map((tile) => (
            <MetricCard
              key={tile.key}
              label={tile.label}
              value={tile.value}
              hint={tile.hint}
              icon={tile.icon}
              className="h-full"
            />
          ))}
        </div>

        <CareerStaleNotice isStale={isStale} subject="the summary" />

        <p className="text-xs leading-relaxed text-muted-foreground">{summary.summary}</p>
        <p className="text-xs leading-relaxed text-muted-foreground">
          {summary.latest_evidence_on
            ? `Most recent evidence dated ${formatCareerDate(summary.latest_evidence_on)}.`
            : 'No evidence has been dated yet, so there is no most recent one.'}
        </p>
      </CardContent>
    </Card>
  )
}

/**
 * The summary row's loading state.
 *
 * Five tile silhouettes at the layout the loaded row uses, so nothing below moves
 * when the numbers arrive. **No placeholder digits**: a grey `0` on a tile that
 * may well read "Not enough data yet." is a number, and this surface never shows a
 * number it does not have.
 */
export function CareerSummaryTilesSkeleton({ className }: { className?: string }) {
  return (
    <div role="status" aria-busy="true" className={cn('space-y-3', className)}>
      <span className="sr-only">Loading the career summary</span>
      <div
        aria-hidden="true"
        className="min-w-0 space-y-3 rounded-lg border border-border bg-card p-6"
      >
        <SkeletonTileRow />
      </div>
    </div>
  )
}

function SkeletonTileRow() {
  return (
    <>
      <div className="h-4 w-48 animate-pulse rounded-md bg-muted" />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-5">
        {Array.from({ length: 5 }, (_, index) => (
          <div key={index} className="min-w-0 space-y-2 rounded-lg border border-border bg-card p-6">
            <div className="h-2.5 w-3/4 animate-pulse rounded-md bg-muted" />
            <div className="h-6 w-16 animate-pulse rounded-md bg-muted" />
            <div className="h-3 w-full animate-pulse rounded-md bg-muted" />
          </div>
        ))}
      </div>
      <div className="h-3 w-4/5 animate-pulse rounded-md bg-muted" />
    </>
  )
}