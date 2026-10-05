import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { Info } from 'lucide-react'

import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { formatDelta, type FormatDeltaOptions } from '@/features/analytics/format'
import { cn } from '@/lib/utils'
import type { ComparisonTotal } from '@/types/analytics'

/**
 * One headline figure.
 *
 * **Three rules this component exists to enforce.**
 *
 * 1. A period comparison carries **an arrow and the words**. The arrow is
 *    decorative; the sentence beside it is what a screen reader and a reader who
 *    cannot see the tint both get. Colour alone would fail both.
 * 2. `unavailableReason` replaces the number entirely. When the backend says a
 *    figure could not be computed, this card prints its reason — it never falls
 *    back to `0` and never shows a comparison for a number that does not exist.
 * 3. The tone of a delta is about whether the movement *helps*
 *    (`higherIsBetter`), which is a different question from which way it moved.
 *    "Tasks overdue, up 2" is an up arrow in a warning tone, not a red down arrow.
 */
export interface MetricCardProps {
  label: string
  /** Pre-formatted value. `null` renders as "not measurable", not as `0`. */
  value: ReactNode
  /** One line under the value: how the figure is defined or what feeds it. */
  hint?: ReactNode
  icon?: LucideIcon
  /** Period-over-period comparison, straight from `ComparisonTotal`. */
  comparison?: Pick<ComparisonTotal, 'absolute_change' | 'percent_change'> | null
  /** Names the unit in the comparison sentence: "up 3 tasks". */
  comparisonUnit?: string
  higherIsBetter?: boolean
  formatChange?: FormatDeltaOptions['format']
  /** Replaces the value with the backend's verbatim reason. */
  unavailableReason?: string | null
  /** Long explanation, behind a tooltip. Rendered for everyone on hover/focus. */
  explanation?: string
  /** `lg` for the headline row, `sm` for a supporting panel. */
  size?: 'sm' | 'lg'
  className?: string
  headerAction?: ReactNode
}

const TONE_CLASS = {
  neutral: 'text-muted-foreground',
  info: 'text-primary',
  success: 'text-success',
  warning: 'text-warning',
  danger: 'text-destructive',
} as const

export function MetricCard({
  label,
  value,
  hint,
  icon: Icon,
  comparison,
  comparisonUnit,
  higherIsBetter = true,
  formatChange,
  unavailableReason,
  explanation,
  size = 'sm',
  className,
  headerAction,
}: MetricCardProps) {
  const unavailable = unavailableReason !== null && unavailableReason !== undefined
  const delta =
    comparison && !unavailable
      ? formatDelta(comparison.absolute_change, comparison.percent_change, {
          unit: comparisonUnit,
          higherIsBetter,
          ...(formatChange ? { format: formatChange } : {}),
        })
      : null

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="flex-row items-start justify-between space-y-0 pb-2">
        <div className="min-w-0 space-y-1">
          <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
            {label}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {explanation && (
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  aria-label={`More about ${label}`}
                  className="flex size-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <Info className="size-3.5" aria-hidden="true" />
                </button>
              </TooltipTrigger>
              <TooltipContent side="top" className="max-w-xs text-pretty leading-relaxed">
                {explanation}
              </TooltipContent>
            </Tooltip>
          )}
          {Icon && (
            <span
              aria-hidden="true"
              className="flex size-7 items-center justify-center rounded-md border border-border bg-muted text-muted-foreground"
            >
              <Icon className="size-3.5" />
            </span>
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-1">
        {unavailable ? (
          <>
            <p
              className={cn(
                'font-semibold tracking-tight text-muted-foreground',
                size === 'lg' ? 'text-lg leading-snug' : 'text-sm leading-snug',
              )}
            >
              {unavailableReason}
            </p>
            {/* Full-strength muted, not `/80`: at 80% over a white card this line
                measures 3.46:1, under the 4.5:1 WCAG AA asks of 12px text. The
                card already says what the figure is not; it has no to be faint
                to be read. */}
            <p className="text-xs text-muted-foreground">Not measurable for this window.</p>
          </>
        ) : (
          <>
            <p
              className={cn(
                'font-semibold tabular-nums tracking-tight text-foreground',
                size === 'lg' ? 'text-3xl' : 'text-2xl',
              )}
            >
              {value}
            </p>

            {delta && (
              <p className={cn('flex items-center gap-1.5 text-xs font-medium', TONE_CLASS[delta.tone])}>
                {delta.direction !== 'unknown' && (
                  <span aria-hidden="true" className="text-sm leading-none">
                    {delta.arrow}
                  </span>
                )}
                {/* The sentence, not the colour, carries the comparison. */}
                <span className="text-muted-foreground">{delta.label}</span>
              </p>
            )}

            {hint && <p className="text-xs leading-relaxed text-muted-foreground">{hint}</p>}
          </>
        )}

        {headerAction}
      </CardContent>
    </Card>
  )
}