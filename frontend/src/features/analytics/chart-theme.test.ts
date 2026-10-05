import { describe, expect, it } from 'vitest'

import { chartNumber, formatChartValue } from '@/features/analytics/chart-theme'
import { NO_VALUE } from '@/features/analytics/format'

/**
 * The one coercion every chart on this surface reads a bucket through.
 *
 * These cases are all things a response can be and a `ChartRow` is not obliged to
 * contain: the row type is a claim about a payload nobody has parsed, and a
 * degraded or shape-shifted answer breaks it. `chartNumber` used to reach
 * `ToNumber` on whatever arrived, which throws `TypeError: Cannot convert object
 * to primitive value` — and that is not a rendering problem, it took the whole
 * chart down over a single bucket.
 */

/** Objects whose `ToPrimitive` cannot produce a value, and so throw on coercion. */
const UNCOERCIBLE: ReadonlyArray<readonly [string, unknown]> = [
  ['an object with no prototype', Object.create(null)],
  ['an object with no callable toString', { toString: null, valueOf: null }],
  ['an object whose toString throws', { toString: () => { throw new Error('nope') } }],
  ['a symbol', Symbol('count')],
]

describe('chartNumber', () => {
  it('keeps a number this client can do arithmetic with', () => {
    expect(chartNumber(0)).toBe(0)
    expect(chartNumber(42)).toBe(42)
    expect(chartNumber(-7)).toBe(-7)
    expect(chartNumber(2.5)).toBe(2.5)
  })

  it('parses a number that arrived as a string', () => {
    expect(chartNumber('42')).toBe(42)
    expect(chartNumber('2.5')).toBe(2.5)
    expect(chartNumber('-7')).toBe(-7)
  })

  it('calls an absent value absent rather than zero', () => {
    // `Number('')` and `Number(null)` are both `0`, which would put "this day
    // was not measured" into a summary sentence and a table as a counted zero.
    expect(chartNumber(null)).toBeNull()
    expect(chartNumber(undefined)).toBeNull()
    expect(chartNumber('')).toBeNull()
  })

  it('calls a value it cannot read absent rather than zero', () => {
    expect(chartNumber(NaN)).toBeNull()
    expect(chartNumber(Infinity)).toBeNull()
    expect(chartNumber(-Infinity)).toBeNull()
    expect(chartNumber('not a number')).toBeNull()
    // Values a response could plausibly carry that are not counts at all.
    expect(chartNumber(true)).toBeNull()
    expect(chartNumber(false)).toBeNull()
    expect(chartNumber({})).toBeNull()
    expect(chartNumber([])).toBeNull()
    expect(chartNumber([1, 2, 3])).toBeNull()
  })

  it.each(UNCOERCIBLE)('returns null for %s instead of throwing', (_label, value) => {
    // The regression. Each of these throws `Cannot convert object to primitive
    // value` under `Number(value)` or any implicit coercion, and the throw
    // happened inside render, so it reached the route error boundary rather than
    // blanking one cell.
    expect(() => chartNumber(value)).not.toThrow()
    expect(chartNumber(value)).toBeNull()
  })

  it('leaves an unreadable bucket rendering as "no value", never as a zero', () => {
    // The half of the fix that is not about the throw: a bucket this client
    // cannot read must not be plotted, totalled or tabulated as a measured 0.
    const read = chartNumber(Object.create(null))
    expect(read).not.toBe(0)
    expect(formatChartValue(read, 'count')).toBe(NO_VALUE)
    expect(formatChartValue(read, 'minutes')).toBe(NO_VALUE)
    expect(formatChartValue(read, 'percent')).toBe(NO_VALUE)
  })
})