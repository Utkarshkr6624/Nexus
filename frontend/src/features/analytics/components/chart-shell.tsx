import type { ReactNode } from 'react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { formatChartValue, type ChartValueUnit } from '@/features/analytics/chart-theme'
import { cn } from '@/lib/utils'

/**
 * The frame every chart on this surface sits in.
 *
 * One shell rather than a repeated header block, because the frame carries three
 * decisions that must not be made per chart: the body keeps a fixed height so a
 * loading skeleton and the loaded chart occupy the same space, an **empty**
 * result renders the caller's own `empty` copy rather than an axis with no
 * points, and the whole card is `min-w-0` so a wide series cannot push its grid
 * column — or the page — into a horizontal scroll on a phone.
 */
export interface ChartShellProps {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
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
  empty,
  error,
  isLoading = false,
  skeletonHeight = 256,
  className,
  children,
}: ChartShellProps) {
  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="flex-row items-start justify-between space-y-0 pb-4">
        <div className="min-w-0 space-y-1">
          <CardTitle>{title}</CardTitle>
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
            children
          )}
        </div>
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
