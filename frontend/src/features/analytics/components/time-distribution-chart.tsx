import type { ReactNode } from 'react'
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'

import { ChartShell, ChartTooltip } from '@/features/analytics/components/chart-shell'
import { ChartDataTable } from '@/features/analytics/components/chart-data-table'
import { EmptyAnalytics } from '@/features/analytics/components/empty-analytics'
import { describeSeries, formatChartValue } from '@/features/analytics/chart-theme'
import { chartColor, formatMinutes, formatPercent } from '@/features/analytics/format'
import type { TimeBucketRead } from '@/types/analytics'

interface DonutSlice {
  key: string
  label: string
  value: number
  color: string
}

/**
 * Where the recorded time went.
 *
 * **A donut, not a pie.** With six slices a pie is unreadable — the outer wedges
 * crowd each other and the reader is decoding angles. Punching the centre out
 * buys the middle for the total, so the shape carries one number and the legend
 * carries the rest.
 *
 * **The legend carries the values, not just the colours.** Each row states the
 * label, the duration and the share, so the chart is readable without hovering
 * and the sums can be checked. `share` is `null` when the window's total is
 * zero, and renders as "no share" rather than as `0%` — a claim about nothing.
 *
 * The legend is drawn in words beside the donut, and the same rows sit behind a
 * focusable disclosure as a table, so the breakdown is reachable by keyboard
 * and readable without the drawing at all.
 */
export interface TimeDistributionChartProps {
  title: string
  subtitle?: ReactNode
  buckets: readonly TimeBucketRead[]
  /**
   * Minutes recorded against no project. Reported separately by the backend
   * rather than folded into an arbitrary "other", and shown here as its own
   * slice so the donut still sums to `totalMinutes`.
   */
  unassignedMinutes?: number
  totalMinutes: number
  reasonIfUnavailable?: string | null
  isLoading?: boolean
  /**
   * The caller's failure surface, shown in place of the donut.
   *
   * Without it a failed request renders `EmptyAnalytics` over `buckets = []`,
   * which states that no time was recorded in the window — the opposite of what
   * a 500 knows, and a reader could act on it.
   */
  error?: ReactNode
  className?: string
  /** Only the largest few are drawn; the rest are folded into "Other". */
  maxSlices?: number
}

export function TimeDistributionChart({
  title,
  subtitle,
  buckets,
  unassignedMinutes = 0,
  totalMinutes,
  reasonIfUnavailable,
  isLoading = false,
  error,
  className,
  maxSlices = 6,
}: TimeDistributionChartProps) {
  const meaningful = buckets.filter((bucket) => bucket.minutes > 0)

  // Beyond a handful of slices the donut stops being readable, so the tail is
  // summed into one named slice and shown as such rather than silently dropped.
  const head = meaningful.slice(0, maxSlices)
  const tail = meaningful.slice(maxSlices)
  const tailMinutes = tail.reduce((total, bucket) => total + bucket.minutes, 0)

  const slices: DonutSlice[] = head.map((bucket, index) => ({
    key: bucket.key || bucket.label,
    label: bucket.label,
    value: bucket.minutes,
    color: chartColor(index),
  }))
  if (tailMinutes > 0) {
    slices.push({
      key: '__other',
      label: `${tail.length} smaller ${tail.length === 1 ? 'source' : 'sources'}`,
      value: tailMinutes,
      color: chartColor(head.length),
    })
  }
  if (unassignedMinutes > 0) {
    slices.push({
      key: '__unassigned',
      label: 'Unassigned',
      value: unassignedMinutes,
      color: chartColor(slices.length),
    })
  }

  const empty = slices.length === 0
  const total = totalMinutes || slices.reduce((sum, slice) => sum + slice.value, 0)

  const description = `${describeSeries(
    'Recorded time',
    slices.map((slice) => ({ label: slice.label, value: slice.value })),
    { format: (value) => formatChartValue(value, 'minutes') },
  )} Split across ${slices.length} ${slices.length === 1 ? 'source' : 'sources'}.`

  const body = (
    <div className="flex h-full flex-col items-center gap-4 sm:flex-row sm:items-center">
      <div className="relative h-40 w-40 shrink-0 sm:h-44 sm:w-44">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Tooltip
              content={(props) => (
                <ChartTooltip
                  active={props.active}
                  label={props.label}
                  payload={props.payload as never}
                  units={{}}
                />
              )}
            />
            <Pie
              data={slices}
              dataKey="value"
              nameKey="label"
              innerRadius="62%"
              outerRadius="92%"
              paddingAngle={2}
              stroke="hsl(var(--card))"
              isAnimationActive={false}
            >
              {slices.map((slice) => (
                <Cell key={slice.key} fill={slice.color} />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-xl font-semibold tabular-nums text-foreground">
            {formatMinutes(total)}
          </span>
          <span className="text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
            tracked
          </span>
        </div>
      </div>

      <ul className="min-w-0 flex-1 space-y-1.5">
        {slices.map((slice) => (
          <li key={slice.key} className="flex items-center gap-2 text-sm">
            <span
              aria-hidden="true"
              className="size-2.5 shrink-0 rounded-[3px]"
              style={{ backgroundColor: slice.color }}
            />
            <span className="min-w-0 flex-1 truncate text-foreground">{slice.label}</span>
            <span className="shrink-0 tabular-nums text-muted-foreground">
              {formatMinutes(slice.value)}
            </span>
            <span className="w-12 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
              {total > 0 ? formatPercent((slice.value / total) * 100, 0) : '—'}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )

  return (
    <ChartShell
      title={title}
      subtitle={subtitle}
      isLoading={isLoading}
      error={error}
      className={className}
      accessibility={{
        description,
        dataTable: (
          <ChartDataTable
            caption={title}
            rowHeading="Source"
            columns={['Recorded', 'Share']}
            rows={slices.map((slice) => [
              slice.label,
              formatMinutes(slice.value),
              total > 0 ? formatPercent((slice.value / total) * 100, 0) : '—',
            ])}
          />
        ),
      }}
      empty={empty ? <EmptyAnalytics metric="time" reason={reasonIfUnavailable} /> : undefined}
    >
      {body}
    </ChartShell>
  )
}