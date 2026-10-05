import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Compass } from 'lucide-react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  CareerEmptyState,
  CareerRegionError,
  CareerStaleNotice,
} from '@/features/career/components/career-empty-state'
import {
  DEFAULT_DEVELOPMENT_EVIDENCE_THRESHOLD,
  describeCareerDaysSince,
  describeDevelopmentSentence,
  developmentAreasFromGaps,
} from '@/features/career/components/career-format'
import { LevelOriginBadge } from '@/features/career/components/career-badges'
import { CAREER_LEVEL_SCALE } from '@/features/career/components/career-vocabulary'
import { formatNumber } from '@/features/analytics/format'
import { cn } from '@/lib/utils'
import type { ApiError } from '@/lib/api-client'
import type { SkillGapRead } from '@/types/learning'

/**
 * The skills you have set a target above the current level on, where the recorded
 * evidence behind them is thin.
 *
 * ## The wording rule this panel exists to enforce
 *
 * Everything on it is **a level with its source, and a count of records beside
 * it** — never a judgement. The sentence for a row is assembled by
 * `describeDevelopmentSentence` and takes the form:
 *
 * > "3 of 5, self-assessed by you. 1 related learning activity in the last 30
 * > days."
 *
 * which is what the product asks for, and *not* "you are weak at X", which is the
 * same fact phrased as a verdict. Two reasons, and both are load-bearing:
 *
 * 1. **The levels are claims, not measurements.** One is the user's own number;
 *    the other is an estimate that is refused outright below three recorded
 *    activities. Neither is a score, and a panel that ranked them would be
 *    inventing the ranking.
 * 2. **A low count is a statement about records, not about a person.** "1
 *    related activity in the last 30 days" is true whatever it implies; "1
 *    activity" said with a warning colour is not.
 *
 * ## Membership is explicit and mechanical
 *
 * A skill is listed only when the user has **set a target above the current
 * level** — the gap is `max(0, target - current)`, so a positive gap means the
 * comparison was deliberate — **and** the window carries fewer related
 * activities than `threshold`. The default threshold is
 * `learning_min_evidence_for_estimate`, which is the count at which the backend
 * stops refusing to estimate at all; it is a threshold on records, not a standard
 * anybody has to meet. Pass `threshold: null` to list every skill with an open
 * gap regardless of how much is recorded behind it.
 *
 * Rows whose levels could not be compared (`available: false`) are **excluded**
 * rather than shown as zero-evidence: an unmeasured comparison is not a skill
 * with a gap, and listing it here would claim a shortfall that was never
 * measured.
 */
export interface DevelopmentAreaRowProps {
  gap: SkillGapRead
  windowDays?: number | null
  href?: string | null
  className?: string
}

export function DevelopmentAreaRow({
  gap,
  windowDays = null,
  href = null,
  className,
}: DevelopmentAreaRowProps) {
  return (
    <li className={cn('min-w-0 space-y-2 py-3 first:pt-0 last:pb-0', className)}>
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
        <LevelOriginBadge source={gap.level_source} size="sm" />
      </div>

      <p className="text-sm leading-relaxed text-foreground/90">
        {describeDevelopmentSentence(gap, windowDays)}
      </p>

      <p className="text-xs leading-relaxed text-muted-foreground">
        Target {formatNumber(gap.target_level)} of {formatNumber(CAREER_LEVEL_SCALE)}, the level
        you set for this skill. {describeCareerDaysSince(gap.days_since_last_activity)}
      </p>
    </li>
  )
}

/* ------------------------------------------------------------------ skeleton */

/**
 * The panel's loading silhouette.
 *
 * **No digits and no level meters.** The real row opens with a level and a count
 * of activities, so a skeleton that showed "1 / 5 · 0 activities" would be
 * publishing both an unattributed level and a measured zero nobody recorded.
 */
export function DevelopmentAreasSkeleton({
  count = 3,
  className,
}: {
  count?: number
  className?: string
}) {
  return (
    <div role="status" aria-busy="true" className={cn('space-y-4', className)}>
      <span className="sr-only">Loading development areas</span>
      {Array.from({ length: Math.max(1, count) }, (_, index) => (
        <div key={index} aria-hidden="true" className="min-w-0 space-y-2">
          <div className="h-3.5 w-2/5 animate-pulse rounded-md bg-muted" />
          <div className="h-3 w-4/5 animate-pulse rounded-md bg-muted" />
          <div className="h-3 w-3/5 animate-pulse rounded-md bg-muted" />
        </div>
      ))}
    </div>
  )
}

export interface DevelopmentAreasPanelProps {
  gaps: readonly SkillGapRead[]
  /**
   * Tracked skills matching the query, from the envelope's `total`.
   *
   * Used as the denominator of the "N of M" line. Preferred over
   * `gaps.length`, which is only the page in hand: the endpoint pages, and
   * dividing by a page slice would report a rate the server did not send.
   * `null` falls back to the rows in hand.
   */
  total?: number | null
  /** The window `evidence_last_30d` was counted over. */
  windowDays?: number | null
  /** Related activities below which a skill is listed. `null` disables the cut. */
  threshold?: number | null
  isLoading?: boolean
  isStale?: boolean
  error?: ApiError | null
  onRetry?: () => void
  emptyReason?: string | null
  emptyAction?: ReactNode
  buildHref?: (gap: SkillGapRead) => string | null
  title?: string
  subtitle?: ReactNode
  skeletonCount?: number
  titleLevel?: 'h3' | 'h4'
  className?: string
}

/**
 * The panel card, with its loading, empty, error and stale states.
 *
 * **An empty panel is not a compliment.** With no skill in this state the copy
 * says exactly that — no tracked skill has both an open target and thin
 * recorded evidence — rather than implying every skill is well evidenced. The
 * two are different claims and only the first is one NEXUS can make.
 */
export function DevelopmentAreasPanel({
  gaps,
  total = null,
  windowDays = null,
  threshold = DEFAULT_DEVELOPMENT_EVIDENCE_THRESHOLD,
  isLoading = false,
  isStale = false,
  error = null,
  onRetry,
  emptyReason = null,
  emptyAction,
  buildHref,
  title = 'Development areas',
  subtitle,
  skeletonCount = 3,
  titleLevel = 'h3',
  className,
}: DevelopmentAreasPanelProps) {
  const areas = developmentAreasFromGaps(gaps, threshold)
  const trackedCount = total ?? gaps.length

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="pb-4">
        <CardTitle level={titleLevel}>{title}</CardTitle>
        {subtitle && <CardDescription>{subtitle}</CardDescription>}
      </CardHeader>

      <CardContent className="space-y-3">
        <CareerStaleNotice isStale={isStale} subject="the development areas" />

        {isLoading ? (
          <DevelopmentAreasSkeleton count={skeletonCount} />
        ) : error ? (
          <CareerRegionError error={error} onRetry={onRetry} subject="the development areas" compact />
        ) : areas.length === 0 ? (
          <CareerEmptyState
            variant="development"
            reason={emptyReason}
            action={emptyAction}
            className="py-4"
          />
        ) : (
          <>
            <p className="text-xs text-muted-foreground">
              {formatNumber(areas.length)} of {formatNumber(trackedCount)}{' '}
              tracked {trackedCount === 1 ? 'skill' : 'skills'} listed
              {threshold === null
                ? trackedCount === 1
                  ? ' has a target above the level recorded.'
                  : ' have a target above the level recorded.'
                : ` ${trackedCount === 1 ? 'has' : 'have'} a target above the level recorded and fewer than ${formatNumber(threshold)} related ${
                    threshold === 1 ? 'activity' : 'activities'
                  } in ${windowDays === null ? 'the window' : `the last ${formatNumber(windowDays)} days`}.`}
            </p>
            <ul className="divide-y divide-border">
              {areas.map((area) => (
                <DevelopmentAreaRow
                  key={area.skill_id ?? area.skill_name}
                  gap={area}
                  windowDays={windowDays}
                  href={buildHref ? buildHref(area) : null}
                />
              ))}
            </ul>
          </>
        )}

        <p className="flex items-start gap-1.5 border-t border-border pt-3 text-[11px] leading-relaxed text-muted-foreground">
          <Compass aria-hidden="true" className="mt-0.5 size-3 shrink-0" />
          <span>
            Each line is a level you set or an estimate NEXUS derived from recorded activities,
            shown with the number of records behind it. Nothing on this panel is a score, and no
            two skills are compared with each other.
          </span>
        </p>
      </CardContent>
    </Card>
  )
}
