/**
 * Timezone helpers for the planner surface.
 *
 * **The rule this module exists to enforce: never re-derive a date from a UTC
 * string.** Slicing `"2026-03-04T23:30:00Z"` to get a day answers "which day was
 * it in UTC", and for anyone east of Greenwich it answers the wrong question —
 * the user's 23:30 session lands on tomorrow's calendar and nobody is told. A
 * day is a question about a *zone*, so every conversion here names one.
 *
 * Two kinds of value cross this boundary and they are not interchangeable:
 *
 * - **Instants** (`starts_at`, `scheduled_start`, …) carry an offset. They are
 *   formatted with `Intl.DateTimeFormat` in the requested zone, and converted
 *   *to* an instant from a wall-clock input by resolving that input **in** the
 *   zone — see {@link localInputToInstant}.
 * - **Calendar dates** (`PlannerDay.date`, a `YYYY-MM-DD` key) are not instants
 *   at all. They are formatted through a noon-UTC anchor read back in UTC, so
 *   no zone offset can ever shift the label off the day it names.
 */

const LOCAL_INPUT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/

/**
 * How far `timeZone` is from UTC at `instant`, in milliseconds.
 *
 * Derived by formatting the instant in the zone and reading the wall clock back
 * as if it were UTC — the only way to learn an offset the runtime has not
 * published. `hour % 24` absorbs the `24:xx` some engines emit for midnight.
 */
export function zoneOffsetMs(instant: Date, timeZone: string): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).formatToParts(instant)

  const read: Record<string, string> = {}
  for (const part of parts) {
    if (part.type !== 'literal') read[part.type] = part.value
  }

  const asUtc = Date.UTC(
    Number(read.year),
    Number(read.month) - 1,
    Number(read.day),
    Number(read.hour) % 24,
    Number(read.minute),
    Number(read.second),
  )
  return asUtc - instant.getTime()
}

/**
 * Turns a `<input type="datetime-local">` value read **in `timeZone`** into the
 * instant it names.
 *
 * A `datetime-local` input carries no zone, so the browser cannot help: the
 * value is a wall clock in whatever zone the user is looking at. Two passes are
 * taken because the offset depends on the instant being resolved, and the first
 * guess can land on the far side of a DST change.
 */
export function localInputToInstant(local: string, timeZone: string): string | null {
  if (!LOCAL_INPUT_RE.test(local)) return null
  // Normalise to whole seconds, then read as UTC purely so `Date` accepts it —
  // the zone the value is really in is `timeZone`, not this parse.
  const [datePart = '', timePart = ''] = local.split('T')
  const naive = Date.parse(`${datePart}T${timePart.length === 5 ? `${timePart}:00` : timePart}Z`)
  if (Number.isNaN(naive)) return null

  const firstOffset = zoneOffsetMs(new Date(naive), timeZone)
  const resolved = naive - firstOffset
  const secondOffset = zoneOffsetMs(new Date(resolved), timeZone)
  const ms = secondOffset === firstOffset ? resolved : naive - secondOffset

  return new Date(ms).toISOString()
}

/** The inverse: an instant rendered as the wall clock of `timeZone`. */
export function instantToLocalInput(instant: string, timeZone: string): string {
  const date = new Date(instant)
  if (Number.isNaN(date.getTime())) return ''
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).formatToParts(date)

  const read: Record<string, string> = {}
  for (const part of parts) {
    if (part.type !== 'literal') read[part.type] = part.value
  }
  const hour = Number(read.hour) % 24
  return `${read.year}-${read.month}-${read.day}T${String(hour).padStart(2, '0')}:${read.minute}`
}

/** `HH:MM` in `timeZone`. */
export function formatTime(instant: string, timeZone: string): string {
  const date = new Date(instant)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(undefined, {
    timeZone,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

export function formatTimeRange(start: string, end: string, timeZone: string): string {
  return `${formatTime(start, timeZone)} – ${formatTime(end, timeZone)}`
}

/** Minutes since local midnight in `timeZone`; the timeline's y coordinate. */
export function minutesSinceMidnight(instant: string, timeZone: string): number {
  const local = instantToLocalInput(instant, timeZone)
  const [datePart = '', timePart = ''] = local.split('T')
  const [year, month, day] = datePart.split('-').map(Number)
  const [hour, minute] = timePart.split(':').map(Number)
  if (!year || !month || !day || hour === undefined || minute === undefined) return 0
  // Only the clock time is read; the date parts prove the instant landed on a day.
  return hour * 60 + minute
}

/**
 * Formats a `YYYY-MM-DD` calendar date.
 *
 * Anchored at noon UTC and read back in UTC on purpose: a calendar date has no
 * instant, and running it through a real offset is how a label ends up on the
 * day before the one it names.
 */
export function formatCalendarDate(
  date: string,
  options: Intl.DateTimeFormatOptions = { weekday: 'short', day: 'numeric', month: 'short' },
): string {
  const anchor = new Date(`${date}T12:00:00Z`)
  if (Number.isNaN(anchor.getTime())) return date
  return new Intl.DateTimeFormat(undefined, { ...options, timeZone: 'UTC' }).format(anchor)
}

/** `YYYY-MM-DD` for "now" as seen in `timeZone` — not `new Date().toISOString()`. */
export function todayInZone(timeZone: string): string {
  return instantToLocalInput(new Date().toISOString(), timeZone).slice(0, 10)
}

export function monthOf(date: string): string {
  return date.slice(0, 7)
}

/** `date` plus `days`, as a calendar date. */
export function shiftDate(date: string, days: number): string {
  const anchor = new Date(`${date}T12:00:00Z`)
  anchor.setUTCDate(anchor.getUTCDate() + days)
  return anchor.toISOString().slice(0, 10)
}

/** Monday, matching the backend's `weekday: 0 = Monday`. */
export function startOfWeek(date: string): string {
  const anchor = new Date(`${date}T12:00:00Z`)
  const weekday = anchor.getUTCDay() // 0 = Sunday
  const offset = weekday === 0 ? -6 : 1 - weekday
  return shiftDate(date, offset)
}

export function addMonths(month: string, delta: number): string {
  const [year, monthNumber] = month.split('-').map(Number)
  const anchor = new Date(Date.UTC(year ?? 1970, (monthNumber ?? 1) - 1 + delta, 1, 12))
  return anchor.toISOString().slice(0, 7)
}

/** `97` → `1h 37m`; `45` → `45m`. Compact, and never `0m` for a real span. */
export function formatMinutes(minutes: number): string {
  if (!Number.isFinite(minutes) || minutes <= 0) return '0m'
  const whole = Math.round(minutes)
  const hours = Math.floor(whole / 60)
  const rest = whole % 60
  if (hours === 0) return `${rest}m`
  if (rest === 0) return `${hours}h`
  return `${hours}h ${rest}m`
}

/** `HH:MM` wall-clock times as the availability API emits them (`09:00:00`). */
export function normalizeWallClock(value: string): string {
  const [hour = '00', minute = '00'] = value.split(':')
  return `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`
}

/** Minutes between two `HH:MM` wall-clock times, wrapping past midnight. */
export function wallClockMinutes(from: string, to: string): number {
  const [fh = '0', fm = '0'] = normalizeWallClock(from).split(':').map(Number)
  const [th = '0', tm = '0'] = normalizeWallClock(to).split(':').map(Number)
  const start = Number(fh) * 60 + Number(fm)
  const end = Number(th) * 60 + Number(tm)
  return end > start ? end - start : 24 * 60 - start + end
}