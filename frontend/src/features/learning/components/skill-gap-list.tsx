import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { CircleHelp, Layers, Timer } from 'lucide-react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  LearningEmptyState,
  LearningRegionError,
  LearningStaleNotice,
} from '@/features/learning/components/learning-empty-state'
import {
  describeDaysSince,
  describeEvidenceCount,
  describeGap,
  describeWindow,
} from '@/features/learning/components/learning-format'
import {
  SkillLevelComparison,
  SkillLevelUnavailableNote,
} from '@/features/learning/components/skill-card'
import { formatNumber } from '@/features/analytics/format'
import { cn } from '@/lib/utils'
import type { ApiError } from '@/lib/api-client'
import type { SkillGapRead } from '@/types/learning'

/**
 * The gaps between where a skill is and where its target sits.
 *
 * ## `available` is the whole contract of this list
 *
 * `SkillGapRead` carries `available` and `reason_if_unavailable` alongside the
 * numbers, and the two states are **different answers**:
 *
 * - `available: true, gap: 0` is a real measurement — the recorded level has
 *   reached the target — and is rendered as a success in words, with both levels
 *   still on screen.
 * - `available: false` means the levels could not be compared at all. It never
 *   carries a zero, and this row renders **no gap figure and no level figure**
 *   beside the reason, because a number next to "not measured" is the exact
 *   failure the field exists to prevent.
 *
 * The backend's own `reason_if_unavailable` is rendered verbatim: it names the
 * specific ingredient that was missing, and a generic sentence cannot.
 *
 * ## The explanation is the point of the row
 *
 * `SkillGapRead.explanation` is built server-side, always contains a digit, and
 * names both levels *and* the evidence count — "Target 4/5, current
 * self-assessed 2/5. NEXUS recorded 6 related learning activities in the last 30
 * days." It is rendered verbatim rather than re-composed here, because a client
 * that rebuilt the sentence from the fields would be a second answer to the same
 * question, and the contract's rule about stored copies applies to a rendered
 * copy just as much as to a database row.
 *
 * ## A gap is not a criticism
 *
 * Nothing on this list is coloured by size, sorted by urgency or worded as a
 * shortfall. A gap of 4 and a gap of 1 are printed in the same weight, because
 * both are the arithmetic of two numbers the user supplied — one recorded level
 * and one target they set.
 */
export interface SkillGapRowProps {
  gap: SkillGapRead
  /** Where this skill's page lives, when the caller has resolved it. */
  href?: string | null
  /** The window `evidence_last_30d` was counted over. */
  windowDays?: number | null
  className?: string
}

export function SkillGapRow({
  gap,
  href = null,
  windowDays = null,
  className,
}: SkillGapRowProps) {
  return (
    <li className={cn('min-w-0 space-y-2 py-4 first:pt-0 last:pb-0', className)}>
      <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        {href ? (
          <Link
            to={href}
            className="min-w-0 flex-1 truncate text-sm font-medium text-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {gap.skill_name}
          </Link>
        ) : (
          <p className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
            {gap.skill_name}
          </p>
        )}
        {gap.available && (
          <span className="shrink-0 text-xs text-muted-foreground">
            {describeGap(gap.gap)}
          </span>
        )}
      </div>

      {gap.available ? (
        <>
          <SkillLevelComparison
            currentLevel={gap.current_level}
            targetLevel={gap.target_level}
            levelSource={gap.level_source}
          />

          <p className="text-xs leading-relaxed text-foreground/80">{gap.explanation}</p>

          <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span className="flex min-w-0 items-center gap-1.5">
              <Layers aria-hidden="true" className="size-3 shrink-0" />
              <span className="truncate">{describeEvidenceCount(gap.evidence_count)}</span>
            </span>
            <span className="flex min-w-0 items-center gap-1.5">
              <Timer aria-hidden="true" className="size-3 shrink-0" />
              <span className="truncate">
                {formatNumber(gap.evidence_last_30d)} in {describeWindow(windowDays)}
              </span>
            </span>
          </div>

          <p className="flex items-start gap-1.5 text-xs leading-relaxed text-muted-foreground">
            <CircleHelp aria-hidden="true" className="mt-0.5 size-3 shrink-0" />
            <span>{describeDaysSince(gap.days_since_last_activity)}</span>
          </p>
        </>
      ) : (
        <div className="space-y-1.5">
          <SkillLevelUnavailableNote reason={gap.reason_if_unavailable} />
          <p className="text-xs leading-relaxed text-muted-foreground">{gap.explanation}</p>
        </div>
      )}
    </li>
  )
}

/* ------------------------------------------------------------------ skeleton */

/**
 * The gap list's loading silhouette.
 *
 * **No digits, and no level meter.** A grey "4 / 5 · 2 / 5" would be a
 * comparison nobody made. The rows are three text lines each, which is what the
 * real row occupies when it has both levels and an explanation.
 */
export function SkillGapListSkeleton({
  count = 4,
  className,
}: {
  count?: number
  className?: string
}) {
  return (
    <div role="status" aria-busy="true" className={cn('space-y-4', className)}>
      <span className="sr-only">Loading skill gaps</span>
      {Array.from({ length: Math.max(1, count) }, (_, index) => (
        <div key={index} aria-hidden="true" className="min-w-0 space-y-2">
          <div className="flex items-baseline justify-between gap-3">
            <div className="h-3.5 w-2/5 animate-pulse rounded-md bg-muted" />
            <div className="h-3 w-32 shrink-0 animate-pulse rounded-md bg-muted" />
          </div>
          <div className="h-3.5 w-3/5 animate-pulse rounded-md bg-muted" />
          <div className="h-3 w-full animate-pulse rounded-md bg-muted" />
        </div>
      ))}
    </div>
  )
}

export interface SkillGapListProps {
  gaps: readonly SkillGapRead[]
  /**
   * Gaps matching the query, from the envelope's `total`.
   *
   * Preferred over the length of `gaps`, which is only the page in hand: the
   * endpoint pages, and a header that counted its own rows would say "50 skills"
   * on an account holding eighty. `null` falls back to the rows in hand.
   */
  total?: number | null
  /** The window `evidence_last_30d` was counted over; `null` says "as supplied". */
  windowDays?: number | null
  isLoading?: boolean
  isStale?: boolean
  error?: ApiError | null
  onRetry?: () => void
  emptyReason?: string | null
  emptyAction?: ReactNode
  /** Builds each row's link. Omit for a read-only list. */
  buildHref?: (gap: SkillGapRead) => string | null
  title?: string
  /** The sentence under the title; states what the list covers. */
  subtitle?: ReactNode
  skeletonCount?: number
  titleLevel?: 'h3' | 'h4'
  className?: string
}

/**
 * The gap list card, with its loading, empty, error and stale states.
 *
 * **The server's ordering survives.** A list that re-sorts itself between a
 * window change and its refetch is a list nobody can learn a position in, so the
 * rows are rendered in the order they arrived and the only ordering claim made
 * anywhere is the one the backend made.
 *
 * The title's own `<h3>`/`<h4>` level is a prop because the same card is used as
 * a page section and inside a narrower panel.
 */
export function SkillGapList({
  gaps,
  total = null,
  windowDays = null,
  isLoading = false,
  isStale = false,
  error = null,
  onRetry,
  emptyReason = null,
  emptyAction,
  buildHref,
  title = 'Skill gaps',
  subtitle,
  skeletonCount = 4,
  titleLevel = 'h3',
  className,
}: SkillGapListProps) {
  const gapCount = total ?? gaps.length

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="pb-4">
        <CardTitle level={titleLevel}>{title}</CardTitle>
        {subtitle && <CardDescription>{subtitle}</CardDescription>}
      </CardHeader>

      <CardContent className="space-y-3">
        <LearningStaleNotice isStale={isStale} subject="the gap list" />

        {isLoading ? (
          <SkillGapListSkeleton count={skeletonCount} />
        ) : error ? (
          <LearningRegionError error={error} onRetry={onRetry} subject="the gap list" compact />
        ) : gaps.length === 0 ? (
          <LearningEmptyState
            variant="gaps"
            reason={emptyReason}
            action={emptyAction}
            className="py-4"
          />
        ) : (
          <>
            <p className="text-xs text-muted-foreground">
              {formatNumber(gapCount)} {gapCount === 1 ? 'skill' : 'skills'} with a recorded
              target, counted over {describeWindow(windowDays)}.
            </p>
            <ul className="divide-y divide-border">
              {gaps.map((gap, index) => (
                <SkillGapRow
                  // `skill_id` alone is not a key: the list is not always narrowed
                  // by it, and two rows for the same skill would collide and React
                  // would drop one. The index is the only thing guaranteed distinct
                  // within this render, so the key pairs it with whatever identity
                  // the row does carry.
                  key={`${gap.skill_id ?? gap.skill_name}-${index}`}
                  gap={gap}
                  href={buildHref ? buildHref(gap) : null}
                  windowDays={windowDays}
                />
              ))}
            </ul>
          </>
        )}
      </CardContent>
    </Card>
  )
}