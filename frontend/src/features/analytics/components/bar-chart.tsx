import type { ReactNode } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { ChartShell, ChartTooltip } from '@/features/analytics/components/chart-shell'
import { ChartDataTable } from '@/features/analytics/components/chart-data-table'
import {
  CHART_AXIS_PROPS,
  chartNumber,
  describeSeries,
  formatChartValue,
  type ChartValueUnit,
} from '@/features/analytics/chart-theme'
import { EmptyAnalytics, type AnalyticsMetricKey } from '@/features/analytics/components/empty-analytics'
import { chartColor } from '@/features/analytics/format'
import type { ChartRow } from '@/features/analytics/components/trend-chart'

export interface BarSeries {
  key: string
  label: string
  colorIndex?: number
  unit?: ChartValueUnit
}

/**
 * Bars for counts: tasks completed per day, and activity per project.
 *
 * **Horizontal when the labels are words.** A vertical bar chart of six project
 * names rotates them into unreadable slanted text; a horizontal one reads
 * top-to-bottom with the labels intact, so the orientation is a prop rather
 * than a second component.
 *
 * **The bars are the picture; the sentence and the table are the content.** The
 * frame names the drawing from the card's heading, hands it the summary
 * `describeSeries` writes, and puts the same rows behind a focusable disclosure,
 * so the category names and their values are readable without decoding a
 * rectangle.
 */
export interface AnalyticsBarChartProps {
  title: string
  subtitle?: ReactNode
  data: readonly ChartRow[]
  series: readonly BarSeries[]
  xKey?: string
  /** `vertical` = categories along the x axis (dates); `horizontal` = along y. */
  orientation?: 'vertical' | 'horizontal'
  actions?: ReactNode
  className?: string
  isLoading?: boolean
  isEmpty?: boolean
  emptyMetric?: AnalyticsMetricKey
  emptyReason?: string | null
  /** Colours every bar differently — for a single-series categorical chart. */
  colorByCategory?: boolean
  /** Names the first column of the data table: "Status", "Project", "Day". */
  rowHeading?: string
}

export function AnalyticsBarChart({
  title,
  subtitle,
  data,
  series,
  xKey = 'label',
  orientation = 'vertical',
  actions,
  className,
  isLoading = false,
  isEmpty = false,
  emptyMetric = 'trend',
  emptyReason,
  colorByCategory = false,
  rowHeading = 'Category',
}: AnalyticsBarChartProps) {
  const units = Object.fromEntries(series.map((entry) => [entry.key, entry.unit ?? 'count']))
  const empty = isEmpty || data.length === 0
  const horizontal = orientation === 'horizontal'

  const description = series
    .map((entry) => {
      const unit = entry.unit ?? 'count'
      return describeSeries(
        entry.label,
        data.map((row) => ({ label: String(row[xKey] ?? ''), value: chartNumber(row[entry.key]) })),
        { format: (value) => formatChartValue(value, unit), sums: unit !== 'percent' },
      )
    })
    .join(' ')

  const tableRows = data.map((row) => [
    String(row[xKey] ?? ''),
    ...series.map((entry) => formatChartValue(chartNumber(row[entry.key]), entry.unit ?? 'count')),
  ])

  const body = (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart
        data={data as ChartRow[]}
        layout={horizontal ? 'vertical' : 'horizontal'}
        margin={{ top: 4, right: 8, bottom: 0, left: horizontal ? 8 : -12 }}
      >
        <CartesianGrid stroke="hsl(var(--border))" vertical={horizontal} horizontal={!horizontal} />
        {/* An array, not a fragment: Recharts enumerates a chart's direct
            children to register its axes, and a Fragment is opaque to that walk
            — so wrapping the pair in `<>…</>` registered neither and the chart
            rendered with no axis and no category labels at all. Keys are
            required for the array form. */}
        {horizontal
          ? [
              <XAxis key="x" type="number" {...CHART_AXIS_PROPS} />,
              <YAxis
                key="y"
                type="category"
                dataKey={xKey}
                width={120}
                interval={0}
                {...CHART_AXIS_PROPS}
              />,
            ]
          : [
              <XAxis
                key="x"
                dataKey={xKey}
                interval="preserveStartEnd"
                minTickGap={16}
                {...CHART_AXIS_PROPS}
              />,
              <YAxis key="y" width={44} allowDecimals={false} {...CHART_AXIS_PROPS} />,
            ]}
        <Tooltip
          cursor={{ fill: 'hsl(var(--muted))' }}
          content={(props) => (
            <ChartTooltip
              active={props.active}
              label={props.label}
              payload={props.payload as never}
              units={units}
            />
          )}
        />
        {series.map((entry, index) =>
          colorByCategory ? (
            <Bar
              key={entry.key}
              dataKey={entry.key}
              name={entry.label}
              radius={[4, 4, 0, 0]}
              isAnimationActive={false}
            >
              {data.map((_row, rowIndex) => (
                <Cell key={`${entry.key}-${rowIndex}`} fill={chartColor(rowIndex)} />
              ))}
            </Bar>
          ) : (
            <Bar
              key={entry.key}
              dataKey={entry.key}
              name={entry.label}
              fill={chartColor(entry.colorIndex ?? index)}
              radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]}
              isAnimationActive={false}
            />
          ),
        )}
      </BarChart>
    </ResponsiveContainer>
  )

  return (
    <ChartShell
      title={title}
      subtitle={subtitle}
      actions={actions}
      isLoading={isLoading}
      className={className}
      accessibility={{
        description,
        dataTable: (
          <ChartDataTable
            caption={title}
            rowHeading={rowHeading}
            columns={series.map((entry) => entry.label)}
            rows={tableRows}
          />
        ),
      }}
      empty={
        empty ? <EmptyAnalytics metric={emptyMetric} reason={emptyReason} /> : undefined
      }
    >
      {body}
    </ChartShell>
  )
}