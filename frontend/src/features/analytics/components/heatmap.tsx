import { useId, useMemo } from 'react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ChartDataTable } from '@/features/analytics/components/chart-data-table'
import { EmptyAnalytics } from '@/features/analytics/components/empty-analytics'
import { describeSeries, formatChartValue } from '@/features/analytics/chart-theme'
import { formatNumber, formatShortDate } from '@/features/analytics/format'
import { cn } from '@/lib/utils'
import type { DateOnlyString } from '@/types/analytics'

export interface HeatmapDay {
  date: DateOnlyString
  /** What "active" means for this chart — the magnitude of the day. */
  value: number
}

export interface HeatmapProps {
  title: string
  subtitle?: string
  /** One row per day in the window, in date order. Gaps are inactive. */
  days: readonly HeatmapDay[]
  start: DateOnlyString
  end: DateOnlyString
  /** What the value counts, in the accessible summary: "work sessions". */
  valueName: string
  isLoading?: boolean
  className?: string
}

/** Intensity buckets over the observed maximum, so a quiet week is still visible. */
const LEVELS = [
  'bg-muted',
  'bg-chart-1/25',
  'bg-chart-1/50',
  'bg-chart-1/75',
  'bg-chart-1',
] as const

function levelFor(value: number, max: number): number {
  if (value <= 0) return 0
  if (max <= 0) return 1
  // Square-root scaling: the differences a reader cares about are at the low end,
  // and linear buckets put every ordinary day in the same palest square.
  return Math.min(LEVELS.length - 1, 1 + Math.floor(Math.sqrt(value / max) * (LEVELS.length - 1)))
}

/**
 * A calendar heatmap of active days.
 *
 * **Hand-rolled, because it needs no library.** It is a CSS grid of squares; a
 * charting package would add a dependency and an SVG overlay to draw thirty
 * rounded rectangles.
 *
 * **The grid is one image, and the sentence beside it is what it says.**
 * "14 active days in the last 30" is the information a colour grid conveys
 * visually, and a screen reader cannot read a colour at all — so the summary is
 * visible text, not a visually-hidden afterthought. Each square also carries its
 * own `title` for a pointer user.
 *
 * The grid and its scale are then a single `role="img"` named from the card's
 * own heading, described by the busiest day, with the per-day figures behind a
 * focusable disclosure: a colour grid with nothing behind it leaves anyone not
 * reading colours with nothing at all.
 *
 * Columns are weeks and rows are weekdays, Monday first, matching the planner's
 * week convention, and the grid is wrapped in a horizontally scrollable region
 * so a 90-day window does not squash each day below 8px.
 */
export function Heatmap({
  title,
  subtitle,
  days,
  start,
  end,
  valueName,
  isLoading = false,
  className,
}: HeatmapProps) {
  const byDate = useMemo(() => {
    const map = new Map<DateOnlyString, number>()
    for (const day of days) map.set(day.date, day.value)
    return map
  }, [days])

  const cells = useMemo(() => {
    const startDate = parseDate(start)
    const endDate = parseDate(end)
    if (!startDate || !endDate || endDate < startDate) return []

    // Leading blanks so the first column is a whole week starting on Monday.
    const leading = (startDate.getDay() + 6) % 7
    const output: Array<{ date: DateOnlyString | null; value: number }> = []
    for (let index = 0; index < leading; index += 1) output.push({ date: null, value: 0 })

    const cursor = new Date(startDate)
    while (cursor <= endDate) {
      const date = toKey(cursor)
      output.push({ date, value: byDate.get(date) ?? 0 })
      cursor.setDate(cursor.getDate() + 1)
    }
    return output
  }, [byDate, end, start])

  const activeDays = cells.filter((cell) => cell.date !== null && cell.value > 0).length
  const totalDays = cells.filter((cell) => cell.date !== null).length
  const max = cells.reduce((highest, cell) => Math.max(highest, cell.value), 0)
  const empty = totalDays > 0 && activeDays === 0

  const baseId = useId().replace(/:/g, '')
  const titleId = `${baseId}-title`
  const descriptionId = `${baseId}-description`

  const description = `${describeSeries(
    `Recorded ${valueName}`,
    cells
      .filter((cell) => cell.date !== null)
      .map((cell) => ({ label: formatShortDate(cell.date as DateOnlyString), value: cell.value })),
    { format: (value) => formatChartValue(value, 'count') },
  )} ${formatNumber(activeDays)} of ${formatNumber(totalDays)} days recorded any.`

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="pb-3">
        <CardTitle id={titleId}>{title}</CardTitle>
        <CardDescription>{subtitle}</CardDescription>
      </CardHeader>

      <CardContent className="space-y-3">
        {isLoading ? (
          <div className="h-24 w-full animate-pulse rounded-md bg-muted" />
        ) : empty ? (
          <EmptyAnalytics metric="heatmap" />
        ) : (
          <>
            {/* The sentence is the accessible version of the grid below it. */}
            <p className="text-sm text-foreground">
              <span className="font-semibold tabular-nums">{formatNumber(activeDays)}</span>{' '}
              {activeDays === 1 ? 'day' : 'days'} with recorded {valueName} in the last{' '}
              <span className="tabular-nums">{formatNumber(totalDays)}</span>
            </p>

            {/* The grid and its scale are one image: a colour a reader cannot
                see, described by the card's heading and the sentence below it. */}
            <div
              role="img"
              aria-labelledby={titleId}
              aria-describedby={descriptionId}
              className="space-y-2"
            >
              <div className="overflow-x-auto pb-1">
                <div className="grid w-max grid-flow-col grid-rows-7 gap-[3px]">
                  {cells.map((cell, index) => (
                    <span
                      key={cell.date ?? `blank-${index}`}
                      title={
                        cell.date
                          ? `${formatShortDate(cell.date)} · ${formatNumber(cell.value)} ${valueName}`
                          : undefined
                      }
                      className={cn(
                        'size-3 rounded-[3px]',
                        cell.date === null
                          ? 'bg-transparent'
                          : LEVELS[levelFor(cell.value, max)],
                      )}
                    />
                  ))}
                </div>
              </div>

              <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                <span>Less</span>
                {LEVELS.map((level) => (
                  <span key={level} className={cn('size-3 rounded-[3px]', level)} />
                ))}
                <span>More</span>
              </div>
            </div>

            <p id={descriptionId} className="sr-only">
              {description}
            </p>

            <ChartDataTable
              caption={title}
              rowHeading="Day"
              columns={[`${valueName[0]?.toUpperCase() ?? ''}${valueName.slice(1)}`]}
              rows={cells
                .filter((cell) => cell.date !== null)
                .map((cell) => [
                  formatShortDate(cell.date as DateOnlyString),
                  formatNumber(cell.value),
                ])}
            />
          </>
        )}
      </CardContent>
    </Card>
  )
}

function parseDate(value: DateOnlyString): Date | null {
  const [year, month, day] = value.split('-').map(Number)
  if (year === undefined || month === undefined || day === undefined) return null
  const date = new Date(year, month - 1, day)
  return Number.isNaN(date.getTime()) ? null : date
}

function toKey(date: Date): DateOnlyString {
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${month}-${day}`
}