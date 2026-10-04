/**
 * Wire types for the Phase 4 planner surface: calendar events, work sessions,
 * availability and the scheduling engine.
 *
 * Mirrors `backend/app/schemas/planner.py`.
 *
 * **Every instant on this surface is timezone-aware and offset-intact.** The
 * backend stores what the caller sent and returns it unchanged — which *day* an
 * instant falls on is a question about a zone, answered by the `tz` parameter
 * on the planner routes. So the one thing this module must not do is let a
 * caller re-derive a date from a UTC string: {@link formatPlannerInstant}
 * exists for exactly that, and the date-only fields below are already
 * `YYYY-MM-DD` in the zone the response was computed for. See
 * `features/planner/datetime.ts` for the interactive-input half of the same rule.
 *
 * **`null` capacity is not zero.** `PlannerDay.available_minutes` is `null` when
 * the user has declared no hours for that weekday — "no hours declared" and
 * "declared zero hours" are different answers and only the second one means the
 * day is overloaded. The same holds for `ratio`, which is `null` rather than
 * NaN or Infinity for a day that cannot be divided. Render the null; never
 * coerce it.
 */
import {
  AlertTriangle,
  Briefcase,
  CalendarDays,
  CheckCircle2,
  CircleDashed,
  CircleX,
  Coffee,
  Flag,
  GraduationCap,
  Info,
  Play,
  TriangleAlert,
  Users,
} from 'lucide-react'

import type { ISODateTimeString, UUIDString } from './api'
import type { PageMeta, Paginated, PaginationParams } from './pagination'
import type { DateOnlyString, StatusMeta } from './work'

// Re-exported so a consumer of the planner vocabulary needs one import, not two.
export type { DateOnlyString, ISODateTimeString, PageMeta, Paginated, StatusMeta, UUIDString }

/* -------------------------------------------------------------- vocabulary */

/** One of the `CalendarEventType` values in `backend/app/models/enums.py`. */
export type CalendarEventType =
  | 'work'
  | 'study'
  | 'meeting'
  | 'personal'
  | 'break'
  | 'deadline'
  | 'other'

/** One of the `WorkSessionStatus` values. `active` is the running timer. */
export type WorkSessionStatus = 'planned' | 'active' | 'completed' | 'cancelled'

export type ConflictKind =
  | 'overlapping_events'
  | 'overlapping_sessions'
  | 'outside_availability'
  | 'after_deadline'

export type ConflictSeverity = 'info' | 'warning' | 'error'

/** `0` = Monday … `6` = Sunday, matching `AvailabilityRuleCreate.weekday`. */
export type Weekday = 0 | 1 | 2 | 3 | 4 | 5 | 6

/** `HH:MM` or `HH:MM:SS` — a wall-clock time with no date attached. */
export type WallClockTimeString = string

/** Which of the three planner views a surface is showing. */
export type PlannerView = 'day' | 'week' | 'month'

/** Sort keys the calendar list endpoint allowlists. Anything else is a 422. */
export const CALENDAR_SORT_KEYS = [
  'starts_at',
  'ends_at',
  'title',
  'created_at',
  'updated_at',
] as const
export type CalendarSortKey = (typeof CALENDAR_SORT_KEYS)[number]

export const WORK_SESSION_SORT_KEYS = [
  'scheduled_start',
  'scheduled_end',
  'status',
  'created_at',
  'updated_at',
] as const
export type WorkSessionSortKey = (typeof WORK_SESSION_SORT_KEYS)[number]

export type SortOrder = 'asc' | 'desc'

/** `limit` above 100 is a 422 on the calendar and session lists, not a clamp. */
export const MAX_PAGE_SIZE = 100

/**
 * A `break` releases a slot rather than blocking one and a `deadline` bounds
 * the scheduler without occupying anything, so the three kinds have to be
 * distinguishable without reading the title.
 */
export const EVENT_TYPE_META: Record<CalendarEventType, StatusMeta> = {
  work: {
    label: 'Work',
    icon: Briefcase,
    tone: 'info',
    description: 'A block of working time.',
  },
  study: {
    label: 'Study',
    icon: GraduationCap,
    tone: 'neutral',
    description: 'Learning time.',
  },
  meeting: {
    label: 'Meeting',
    icon: Users,
    tone: 'info',
    description: 'Something to attend.',
  },
  personal: {
    label: 'Personal',
    icon: CircleDashed,
    tone: 'neutral',
    description: 'Off the clock.',
  },
  break: {
    label: 'Break',
    icon: Coffee,
    tone: 'success',
    description: 'Deliberately not spent working.',
  },
  deadline: {
    label: 'Deadline',
    icon: Flag,
    tone: 'warning',
    description: 'Bounds the scheduler; occupies nothing.',
  },
  other: {
    label: 'Other',
    icon: CalendarDays,
    tone: 'neutral',
    description: 'Imported or unclassified.',
  },
}

export const WORK_SESSION_STATUS_META: Record<WorkSessionStatus, StatusMeta> = {
  planned: {
    label: 'Planned',
    icon: CircleDashed,
    tone: 'neutral',
    description: 'Reserved, not started.',
  },
  active: {
    label: 'Active',
    icon: Play,
    tone: 'info',
    description: 'The timer is running.',
  },
  completed: {
    label: 'Completed',
    icon: CheckCircle2,
    tone: 'success',
    description: 'Stopped; actual minutes recorded.',
  },
  cancelled: {
    label: 'Cancelled',
    icon: CircleX,
    tone: 'danger',
    description: 'The slot was released.',
  },
}

/** Every conflict kind gets a label and a tone so a list needs no switch. */
export const CONFLICT_KIND_META: Record<ConflictKind, StatusMeta> = {
  overlapping_events: {
    label: 'Overlapping events',
    icon: AlertTriangle,
    tone: 'warning',
    description: 'Two calendar events claim the same time.',
  },
  overlapping_sessions: {
    label: 'Overlapping sessions',
    icon: AlertTriangle,
    tone: 'warning',
    description: 'Two work sessions claim the same time.',
  },
  outside_availability: {
    label: 'Outside availability',
    icon: TriangleAlert,
    tone: 'warning',
    description: 'Work is booked when the user said they are free.',
  },
  after_deadline: {
    label: 'After deadline',
    icon: Flag,
    tone: 'danger',
    description: 'The only time booked for a task is after it was due.',
  },
}

export const CONFLICT_SEVERITY_META: Record<ConflictSeverity, StatusMeta> = {
  info: { label: 'Info', icon: Info, tone: 'neutral', description: 'Worth knowing.' },
  warning: { label: 'Warning', icon: AlertTriangle, tone: 'warning', description: 'Should be fixed.' },
  error: { label: 'Error', icon: TriangleAlert, tone: 'danger', description: 'Must be fixed.' },
}

export const CALENDAR_EVENT_TYPES = Object.keys(EVENT_TYPE_META) as CalendarEventType[]
export const WORK_SESSION_STATUSES = Object.keys(WORK_SESSION_STATUS_META) as WorkSessionStatus[]

/** Indexed by the backend's `weekday`, which starts at Monday. */
export const WEEKDAY_LABELS = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
] as const

export const WEEKDAY_SHORT_LABELS = [
  'Mon',
  'Tue',
  'Wed',
  'Thu',
  'Fri',
  'Sat',
  'Sun',
] as const

/* ------------------------------------------------------------------- reads */

export interface CalendarEvent {
  id: UUIDString
  owner_id: UUIDString
  title: string
  description: string | null
  event_type: CalendarEventType
  project_id: UUIDString | null
  task_id: UUIDString | null
  /** Stored instant, offset intact. Convert for display; never re-derive a day. */
  starts_at: ISODateTimeString
  ends_at: ISODateTimeString
  all_day: boolean
  location: string | null
  completed_at: ISODateTimeString | null
  created_at: ISODateTimeString
  updated_at: ISODateTimeString
}

export interface WorkSession {
  id: UUIDString
  owner_id: UUIDString
  task_id: UUIDString | null
  project_id: UUIDString | null
  scheduled_start: ISODateTimeString
  scheduled_end: ISODateTimeString
  /** Set only while `status` is `active`; the timestamp is the database's. */
  actual_start: ISODateTimeString | null
  actual_end: ISODateTimeString | null
  estimated_minutes: number | null
  actual_minutes: number
  status: WorkSessionStatus
  created_at: ISODateTimeString
  updated_at: ISODateTimeString
}

/** A recurring "I am working then" window, in naive wall-clock times. */
export interface AvailabilityRule {
  id: UUIDString
  /** `0` = Monday … `6` = Sunday. `number` because it comes off the wire. */
  weekday: number
  /** `HH:MM`, not an instant — a recurring window has no date of its own. */
  starts_at: WallClockTimeString
  ends_at: WallClockTimeString
  label: string | null
  created_at: ISODateTimeString
  updated_at: ISODateTimeString
}

/** The stored pattern plus the zone it is to be read in. Not paged. */
export interface AvailabilityWeek {
  rules: AvailabilityRule[]
  timezone: string
  meta: PageMeta
}

/** The span and zone a planner answer was computed for. */
export interface PlannerWindow {
  start_date: DateOnlyString
  end_date: DateOnlyString
  timezone: string
}

export interface PlannerDay {
  /** `YYYY-MM-DD` **in the requested zone** — never sliced out of a UTC string. */
  date: DateOnlyString
  events: CalendarEvent[]
  sessions: WorkSession[]
  /** `null` = no hours declared for this weekday. Not zero, and not an overload. */
  available_minutes: number | null
  scheduled_minutes: number
  /** `null` whenever `available_minutes` is `null`. */
  overload_minutes: number | null
  overloaded: boolean
  /** scheduled ÷ available; `null` when availability is unknown. Never NaN. */
  ratio: number | null
}

export interface WeekTotals {
  available_minutes: number | null
  scheduled_minutes: number
  overload_minutes: number | null
  overloaded_days: number
  event_count: number
  session_count: number
}

export interface PlannerWeek {
  week_start: DateOnlyString
  week_end: DateOnlyString
  days: PlannerDay[]
  totals: WeekTotals
  window: PlannerWindow | null
}

export interface PlannerMonth {
  days: PlannerDay[]
  window: PlannerWindow
}

/** One item on a day, ordered for a timeline. `kind` is the discriminator. */
export interface PlannerSlot {
  kind: 'event' | 'session' | 'availability'
  starts_at: ISODateTimeString
  ends_at: ISODateTimeString
  id: UUIDString | null
}

export interface Conflict {
  kind: ConflictKind
  severity: ConflictSeverity
  message: string
  entity_type: 'calendar_event' | 'work_session'
  /** `null` for a conflict between two things that were both found. */
  entity_id: UUIDString | null
  /** The two intervals, or the window the work fell outside. */
  evidence: Record<string, unknown>
}

export interface ConflictList {
  window: PlannerWindow
  conflicts: Conflict[]
  meta: PageMeta
}

/**
 * One proposed slot. `reason` and `evidence` are the point: a schedule the user
 * did not write has to be arguable, and an engine that cannot say why it put
 * work at 14:00 on Tuesday is indistinguishable from one that guessed.
 */
export interface PlannerSuggestion {
  task_id: UUIDString
  task_title: string
  suggested_start: ISODateTimeString
  suggested_end: ISODateTimeString
  reason: string
  evidence: Record<string, unknown>
}

/** An empty `suggestions` with a populated `reason_if_empty` is a real answer. */
export interface SuggestionResponse {
  suggestions: PlannerSuggestion[]
  generated_for: PlannerWindow
  reason_if_empty: string | null
}

/* ------------------------------------------------------------- list params */

/**
 * Filters `GET /calendar` and `GET /work-sessions` share. The window is an
 * **overlap**, not a containment, and is half-open: `[from, to)`.
 */
export interface PlannerListParams extends PaginationParams {
  /** Sent under the `from` query param, as an offset-intact instant. */
  from?: ISODateTimeString
  /** Sent under the `to` query param. */
  to?: ISODateTimeString
  project_id?: UUIDString
  task_id?: UUIDString
  sort?: string
  order?: SortOrder
}

export interface CalendarEventListParams extends PlannerListParams {
  event_type?: CalendarEventType
}

export interface WorkSessionListParams extends PlannerListParams {
  status?: WorkSessionStatus
}

/** `GET /planner/conflicts` names its span `start`/`end`, as local dates. */
export interface ConflictListParams {
  start: DateOnlyString
  end: DateOnlyString
  tz?: string
}

/** `POST /planner/suggestions` takes these as query params, not a body. */
export interface SuggestionRequest {
  task_ids?: UUIDString[]
  tz?: string
}

/* ---------------------------------------------------------------- payloads */

/**
 * Every field that names a moment must be sent with an explicit offset; the
 * backend rejects a naive datetime outright rather than reading it as local
 * time.
 */
export interface CalendarEventCreatePayload {
  title: string
  starts_at: ISODateTimeString
  ends_at: ISODateTimeString
  event_type?: CalendarEventType
  description?: string | null
  project_id?: UUIDString | null
  task_id?: UUIDString | null
  all_day?: boolean
  location?: string | null
}

/** `extra="forbid"`: an unknown key is a 422 naming the field, not a silent drop. */
export type CalendarEventUpdatePayload = Partial<CalendarEventCreatePayload> & {
  completed_at?: ISODateTimeString | null
}

export interface WorkSessionCreatePayload {
  scheduled_start: ISODateTimeString
  scheduled_end: ISODateTimeString
  task_id?: UUIDString | null
  project_id?: UUIDString | null
  estimated_minutes?: number | null
  status?: WorkSessionStatus
}

/**
 * Unlike a task's, a session's `status` is writable here: a session has no
 * lifecycle rules a blanket write could bypass, and cancelling a slot is an
 * ordinary edit.
 */
export type WorkSessionUpdatePayload = Partial<WorkSessionCreatePayload> & {
  actual_start?: ISODateTimeString | null
  actual_end?: ISODateTimeString | null
  actual_minutes?: number | null
}

export interface AvailabilityRuleInput {
  /** `0` = Monday … `6` = Sunday; anything else is a 422. */
  weekday: number
  /** `HH:MM` or `HH:MM:SS` — a wall-clock time, never an instant. */
  starts_at: WallClockTimeString
  ends_at: WallClockTimeString
  label?: string | null
}

/** The complete pattern: `rules: []` clears the week. This is a PUT. */
export interface AvailabilityReplacementPayload {
  rules: AvailabilityRuleInput[]
}

/* ------------------------------------------------------------ list aliases */

export type CalendarEventPage = Paginated<CalendarEvent>
export type WorkSessionPage = Paginated<WorkSession>

/* ---------------------------------------------------------------- helpers */

/** What every "no value" render shows, so the em dash is decided once. */
export const PLANNER_EMPTY = '—'

/** Cache of `Intl.DateTimeFormat` per zone: building one is not free. */
const instantFormatters = new Map<string, Intl.DateTimeFormat>()

function instantFormatter(tz: string): Intl.DateTimeFormat | null {
  const cached = instantFormatters.get(tz)
  if (cached) return cached
  try {
    const formatter = new Intl.DateTimeFormat(undefined, {
      timeZone: tz,
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    })
    instantFormatters.set(tz, formatter)
    return formatter
  } catch {
    // An unknown IANA name is a 422 at the API; here it must not throw through
    // a render, so the caller falls back to the browser's own zone.
    return null
  }
}

/**
 * Renders an offset-intact instant in `tz`.
 *
 * This is the **only** sanctioned way to put a planner instant on screen. It
 * hands the whole string to `Intl` with the zone attached and lets it do the
 * arithmetic; slicing `iso.slice(0, 10)` or reading `getUTCDate()` moves a
 * 23:00 event onto the wrong day for anyone east of Greenwich, which is the one
 * bug this surface must not ship.
 *
 * An unparseable instant or an unknown zone renders as {@link PLANNER_EMPTY}
 * rather than "Invalid Date".
 */
export function formatPlannerInstant(iso: string, tz: string): string {
  if (!iso) return PLANNER_EMPTY
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return PLANNER_EMPTY
  const formatter = instantFormatter(tz)
  if (!formatter) return date.toLocaleString()
  try {
    return formatter.format(date)
  } catch {
    return PLANNER_EMPTY
  }
}

/**
 * Renders a duration for a capacity column.
 *
 * `null` — no availability declared — and `0` both render as
 * {@link PLANNER_EMPTY}, because neither is a number of minutes a reader should
 * act on. Non-finite or negative input is treated the same way rather than
 * becoming `NaN` on screen.
 */
export function formatPlannerMinutes(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return PLANNER_EMPTY
  if (!Number.isFinite(minutes) || minutes <= 0) return PLANNER_EMPTY
  const total = Math.round(minutes)
  const hours = Math.floor(total / 60)
  const rest = total % 60
  if (hours === 0) return `${rest}m`
  if (rest === 0) return `${hours}h`
  return `${hours}h ${rest}m`
}

/** Percentage of a day's capacity used; `null` when the denominator is unknown. */
export function formatPlannerRatio(ratio: number | null | undefined): string {
  if (ratio === null || ratio === undefined) return PLANNER_EMPTY
  if (!Number.isFinite(ratio)) return PLANNER_EMPTY
  return `${Math.round(ratio * 100)}%`
}

/** `YYYY-MM-DD` for "now" as seen in `tz` — not `new Date().toISOString()`. */
export function plannerToday(tz: string, now: Date = new Date()): DateOnlyString {
  const options: Intl.DateTimeFormatOptions = {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }
  try {
    return new Intl.DateTimeFormat('en-CA', { ...options, timeZone: tz }).format(now)
  } catch {
    return new Intl.DateTimeFormat('en-CA', options).format(now)
  }
}

/** The `YYYY-MM` of a `YYYY-MM-DD`, for the month query. */
export function plannerMonth(tz: string, date: DateOnlyString = plannerToday(tz)): string {
  return date.slice(0, 7)
}

/**
 * The Monday of the week containing `date`.
 *
 * Anchored at noon UTC on purpose: only the *weekday index* is wanted, and
 * running that arithmetic in `tz` would move a Sunday-evening date to the wrong
 * week twice a year.
 */
export function plannerWeekStart(tz: string, date: DateOnlyString = plannerToday(tz)): DateOnlyString {
  void tz
  const parsed = new Date(`${date}T12:00:00Z`)
  if (Number.isNaN(parsed.getTime())) return date
  const mondayOffset = (parsed.getUTCDay() + 6) % 7
  parsed.setUTCDate(parsed.getUTCDate() - mondayOffset)
  return parsed.toISOString().slice(0, 10)
}

/** The caller's own zone, which is the default every planner route assumes. */
export function plannerLocalTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}