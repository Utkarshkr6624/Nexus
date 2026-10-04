import { describe, expect, it } from 'vitest'

import { addMonths } from '@/features/planner/datetime'

/**
 * `addMonths` is the app's one month arithmetic rule. It was written twice
 * more inside the month and planner pages before this test existed, and a copy
 * of month arithmetic is a copy of a bug: the month view and the planner's
 * month view have to land on the same month or a reader clicking "next" twice
 * arrives somewhere the URL does not describe.
 *
 * The noon-UTC anchor is deliberate. Anchoring at midnight makes the arithmetic
 * depend on the reader's own time zone, so a month boundary could render as the
 * last day of the previous month for someone west of UTC and the first of this
 * one for someone east of it.
 */

describe('addMonths', () => {
  it('moves forwards and backwards across a year boundary', () => {
    expect(addMonths('2026-01', 1)).toBe('2026-02')
    expect(addMonths('2026-12', 1)).toBe('2027-01')
    expect(addMonths('2026-01', -1)).toBe('2025-12')
  })

  it('handles a delta that skips whole years', () => {
    expect(addMonths('2026-03', 12)).toBe('2027-03')
    expect(addMonths('2026-03', -24)).toBe('2024-03')
  })

  it('zero-pads the month rather than emitting 2026-2', () => {
    expect(addMonths('2026-01', 0)).toBe('2026-01')
  })

  it('fails loudly on a month it cannot read rather than emitting a malformed one', () => {
    // The `?? 1970` default on the line above is written for exactly this case
    // and does not fire, because `'nonsense'.split('-').map(Number)` yields
    // `NaN` rather than `undefined`. Worth pinning: the two copies of this
    // helper that lived inside the month and planner pages *did* produce a
    // value here — the string `"NaN-NaN"`, which would have gone straight into
    // a `?date=` parameter. Throwing is the better of the two behaviours, and
    // this test is what stops a future "fix" quietly reintroducing the other.
    expect(() => addMonths('nonsense', 0)).toThrow()
  })
})
