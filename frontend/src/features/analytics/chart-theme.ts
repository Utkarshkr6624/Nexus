import { formatMinutes, formatNumber } from '@/features/analytics/format'

/**
 * Axis, grid and tick styling, so every chart on this surface inherits one
 * scale of typography instead of each declaring its own.
 *
 * Recharts paints SVG *attributes*, not classes, so these are resolved token
 * values (`hsl(var(--border))`) rather than a `border-border` utility — which
 * is also why the colours are spelled the same way as `CHART_COLORS`.
 */
export const CHART_AXIS_PROPS = {
  stroke: 'hsl(var(--border))',
  tick: { fill: 'hsl(var(--muted-foreground))', fontSize: 11 },
  tickLine: false,
  axisLine: false,
} as const

/** How a series' values read inside a tooltip and an axis. */
export type ChartValueUnit = 'count' | 'minutes' | 'percent' | 'raw'

/**
 * `null` renders as "no value" here exactly as it does everywhere else on this
 * surface. A chart that plotted an absent bucket as `0` would be claiming the
 * day was measured and found empty.
 */
export function formatChartValue(value: number | null | undefined, unit: ChartValueUnit): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  if (unit === 'minutes') return formatMinutes(value)
  if (unit === 'percent') return `${formatNumber(value, 0)}%`
  return formatNumber(value, Number.isInteger(value) ? 0 : 1)
}

/**
 * A row's value as a number, or `null` when the bucket carries no value.
 *
 * Series read `string | number | null | undefined`, and `Number('')` and
 * `Number(null)` are both `0` — which would turn "this day was not measured"
 * into a counted zero in a summary sentence and in the table below a chart.
 *
 * **`unknown`, not the wire type, because the wire type is a promise the
 * response does not keep.** Every caller here indexes a row object with a key
 * the caller chose, so `ChartRow`'s four-value union is a claim about a
 * response nobody has parsed. A degraded, partially cached or shape-shifted
 * answer puts a nested object where a figure was expected, and `Number(obj)`
 * calls `ToPrimitive` on it — which throws `TypeError: Cannot convert object to
 * primitive value` for any object with no usable `valueOf`/`toString`. That
 * took the whole chart, and the Developer page with it, over one unreadable
 * bucket.
 *
 * So the value is coerced once, here, by kind: a number is kept or rejected,
 * a string is parsed, and **everything else is `null` without being coerced at
 * all**. Nothing here can throw, and nothing here invents a figure — an
 * unreadable bucket returns `null`, which `formatChartValue` prints as `—` and
 * `describeSeries` leaves out of the total, rather than `0`, which would claim
 * the day was measured and found empty.
 */
export function chartNumber(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'string') {
    if (value === '') return null
    const numeric = Number(value)
    return Number.isFinite(numeric) ? numeric : null
  }
  return null
}

export interface SeriesSummaryOptions {
  format: (value: number | null | undefined) => string
  /**
   * Whether adding the points up says anything. Counts and minutes accumulate;
   * a rate does not, and "Completion rate: 140% in total across 3 projects" is
   * a sentence nobody can use.
   */
  sums?: boolean
}

/**
 * One sentence a screen reader hears instead of a drawing, carrying real values.
 *
 * A chart exposed without one of these announces its own axis — eight legend
 * words and seven tick numbers, not a single data value — which is the failure
 * this sentence exists to prevent. It names the series, the total (or the range,
 * where summing is meaningless), and the highest and lowest point with the row
 * they sit on, so a reader learns something the geometry never said.
 */
export function describeSeries(
  label: string,
  rows: ReadonlyArray<{ label: string; value: number | null }>,
  { format, sums = true }: SeriesSummaryOptions,
): string {
  const points = rows.filter((row): row is { label: string; value: number } => row.value !== null)
  if (points.length === 0) return `${label}: no value is recorded for any point in this window.`

  const highest = points.reduce((a, b) => (b.value > a.value ? b : a))
  const lowest = points.reduce((a, b) => (b.value < a.value ? b : a))
  const total = points.reduce((sum, row) => sum + row.value, 0)
  const count = `${points.length} ${points.length === 1 ? 'point' : 'points'}`
  const headline = sums
    ? `${format(total)} in total across ${count}`
    : `between ${format(lowest.value)} and ${format(highest.value)} across ${count}`

  return `${label}: ${headline}. Highest ${format(highest.value)} at ${highest.label}, lowest ${format(lowest.value)} at ${lowest.label}.`
}
