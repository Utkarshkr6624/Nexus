import { useId, type ReactNode } from 'react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { formatChartValue, type ChartValueUnit } from '@/features/analytics/chart-theme'
import { cn } from '@/lib/utils'

/**
 * The frame every chart on this surface sits in.
 *
 * One shell rather than a repeated header block, because the frame carries four
 * decisions that must not be made per chart: the body keeps a fixed height so a
 * loading skeleton and the loaded chart occupy the same space, an **empty**
 * result renders the caller's own `empty` copy rather than an axis with no
 * points, the drawing is a single `role="img"` named from the card's own
 * heading, and the whole card is `min-w-0` so a wide series cannot push its
 * grid column — or the page — into a horizontal scroll on a phone.
 */
export interface ChartShellProps {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
  /**
   * The chart's accessible name and description, in one object.
   *
   * Without this a chart announces only what is drawn inside it — its category
   * labels and its axis ticks. The Tasks chart, for instance, was heard as
   * "img: todo in progress blocked completed cancelled total 0 2 4 6 8": eight
   * legend words and seven numbers, not one task. `role="img"` below names the
   * drawing from the card's own heading and hands the sentence here as its
   * description, so the shapes stop being noise and the data becomes words.
   *
   * It is optional because a chart with no rows renders the empty state instead
   * of a drawing, and an empty state has nothing to describe.
   */
  accessibility?: { description: string; dataTable?: ReactNode }
  /**
   * Nothing to plot: the caller's reason, not a zeroed axis.
   *
   * `error` is a separate slot rather than a flavour of `empty` because a
   * failed read and an empty one are different claims: `empty` says the window
   * was measured and held nothing, `error` says it was never measured. A
   * caller whose query failed passes the failure here so the chart cannot draw
   * its empty copy over a request that never came back.
   */
  empty?: ReactNode
  error?: ReactNode
  isLoading?: boolean
  skeletonHeight?: number
  className?: string
  /** Rendered inside the frame, already inside the fixed-height body. */
  children: ReactNode
}

export function ChartShell({
  title,
  subtitle,
  actions,
  accessibility,
  empty,
  error,
  isLoading = false,
  skeletonHeight = 256,
  className,
  children,
}: ChartShellProps) {
  // Stable, colon-free ids: `useId` guarantees uniqueness, and stripping the
  // colons keeps the values usable in a CSS selector by anything reading the
  // DOM later.
  const baseId = useId().replace(/:/g, '')
  const titleId = `${baseId}-title`
  const descriptionId = `${baseId}-description`

  const drawn = !isLoading && !error && !empty

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="flex-row items-start justify-between space-y-0 pb-4">
        <div className="min-w-0 space-y-1">
          {/* The visible heading is the chart's accessible name — no second copy
              of the title exists in the accessibility tree. */}
          <CardTitle id={titleId}>{title}</CardTitle>
          {subtitle && <CardDescription>{subtitle}</CardDescription>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </CardHeader>

      <CardContent>
        <div className="h-64 w-full min-w-0" style={{ minHeight: skeletonHeight }}>
          {isLoading ? (
            <Skeleton className="h-full w-full" />
          ) : error ? (
            <div className="flex h-full items-start">{error}</div>
          ) : empty ? (
            empty
          ) : (
            // `role="img"` makes the drawing's contents presentational: the
            // grid, the ticks and the marks themselves are described by the
            // sentence below instead of being read out one fragment at a time.
            <div
              role="img"
              aria-labelledby={titleId}
              aria-describedby={accessibility ? descriptionId : undefined}
              className="h-full w-full min-w-0"
            >
              {children}
            </div>
          )}
        </div>

        {drawn && accessibility && (
          <p id={descriptionId} className="sr-only">
            {accessibility.description}
          </p>
        )}
        {drawn && accessibility?.dataTable}
      </CardContent>
    </Card>
  )
}

/* ----------------------------------------------------------------- tooltip */

/** One row of a recharts tooltip, narrowed to the fields this surface reads. */
export interface TooltipEntry {
  name?: string | number
  value?: string | number
  color?: string
  dataKey?: string | number
}

/**
 * One tooltip for every chart on the surface.
 *
 * Recharts' own tooltip renders the series *keys* rather than their labels and
 * lays them out in a floating white box that does not read as part of this
 * design system. This one names each series, formats its unit and reuses the
 * card tokens, and it renders a muted `—` for a missing value rather than
 * nothing at all.
 */
export function ChartTooltip({
  active,
  label,
  payload,
  units,
}: {
  active?: boolean
  label?: string | number
  payload?: readonly TooltipEntry[]
  /** `dataKey` → unit, so each row formats itself. */
  units?: Record<string, ChartValueUnit>
}) {
  if (!active || !payload || payload.length === 0) return null

  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-xs shadow-md">
      {label !== undefined && label !== '' && (
        <p className="mb-1 font-medium text-popover-foreground">{label}</p>
      )}
      <ul className="space-y-0.5">
        {payload.map((entry, index) => {
          const key = String(entry.dataKey ?? entry.name ?? index)
          const unit = units?.[key] ?? 'count'
          const numeric =
            typeof entry.value === 'number' ? entry.value : Number(entry.value ?? Number.NaN)
          return (
            <li key={key} className="flex items-center gap-2 text-popover-foreground/90">
              <span
                aria-hidden="true"
                className="size-2 shrink-0 rounded-[2px]"
                style={{ backgroundColor: entry.color }}
              />
              <span className="flex-1">{key}</span>
              <span className="font-medium tabular-nums text-popover-foreground">
                {formatChartValue(numeric, unit)}
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
