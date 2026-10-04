/**
 * Thin typed wrappers over the Phase 4 planner endpoints.
 *
 * No React here — every function is a promise-returning call the hooks in
 * `features/planner/hooks.ts` wrap in a `queryFn`/`mutationFn`.
 *
 * Four shapes in this file are dictated by the backend rather than chosen, and
 * each has burned a client before:
 *
 * - `GET /calendar` and `GET /work-sessions` alias their window to `from`/`to`,
 *   so `PlannerListParams.from`/`to` are sent under those names.
 * - `GET /planner/conflicts` names its span `start`/`end`, **not** `from`/`to`.
 *   They are local `YYYY-MM-DD` dates in `tz`, not instants.
 * - `POST /planner/suggestions` takes `tz` and `task_ids` as **query params**
 *   on a POST with no body; `task_ids` is repeatable, so it goes on the path.
 * - `POST /work-sessions/{id}/start|stop` take no body at all — the timestamp is
 *   the database's, so a client cannot and must not supply one.
 *
 * Availability is a **PUT of the whole week**: `rules: []` clears it. There is
 * no "append" verb, by design, so `replaceAvailability` is the only writer.
 */
import { apiClient, queryFrom } from '@/lib/api-client'
import type { QueryParams } from '@/lib/api-client'
import type { Paginated } from '@/types/pagination'
import type {
  AvailabilityReplacementPayload,
  AvailabilityRuleInput,
  AvailabilityWeek,
  CalendarEvent,
  CalendarEventCreatePayload,
  CalendarEventListParams,
  CalendarEventUpdatePayload,
  ConflictList,
  ConflictListParams,
  DateOnlyString,
  ISODateTimeString,
  PlannerDay,
  PlannerMonth,
  PlannerWeek,
  SuggestionRequest,
  SuggestionResponse,
  UUIDString,
  WorkSession,
  WorkSessionCreatePayload,
  WorkSessionListParams,
  WorkSessionUpdatePayload,
} from '@/types/planner'

export const PLANNER_ENDPOINTS = {
  calendar: '/calendar',
  calendarEvent: (id: UUIDString) => `/calendar/${id}`,
  sessions: '/work-sessions',
  workSession: (id: UUIDString) => `/work-sessions/${id}`,
  sessionStart: (id: UUIDString) => `/work-sessions/${id}/start`,
  sessionStop: (id: UUIDString) => `/work-sessions/${id}/stop`,
  day: '/planner/day',
  week: '/planner/week',
  month: '/planner/month',
  conflicts: '/planner/conflicts',
  suggestions: '/planner/suggestions',
  availability: '/availability',
} as const

/** Dropped when unset: `null`/undefined would serialise as a literal. */
/**
 * Appends a repeated query parameter to the path.
 *
 * `QueryParams` is flat, so the client can only emit each key once — wrong for
 * `task_ids`, which the backend parses as a list and would read
 * `?task_ids=a,b` as one unparseable id.
 */
function withRepeatedParam(path: string, key: string, values: readonly string[]): string {
  if (values.length === 0) return path
  const suffix = values.map((value) => `${key}=${encodeURIComponent(value)}`).join('&')
  return `${path}${path.includes('?') ? '&' : '?'}${suffix}`
}

/** `from`/`to` are reserved-ish words; this keeps that mapping in one place. */
function windowQuery(params: {
  from?: ISODateTimeString
  to?: ISODateTimeString
}): QueryParams {
  return queryFrom({ from: params.from, to: params.to })
}

/* ---------------------------------------------------------------- calendar */

export function fetchCalendarEvents(
  params: CalendarEventListParams = {},
  signal?: AbortSignal,
): Promise<Paginated<CalendarEvent>> {
  const { from, to, ...rest } = params
  return apiClient.get<Paginated<CalendarEvent>>(PLANNER_ENDPOINTS.calendar, {
    query: { ...windowQuery({ from, to }), ...queryFrom(rest as Record<string, unknown>) },
    signal,
  })
}

export function createCalendarEvent(payload: CalendarEventCreatePayload): Promise<CalendarEvent> {
  return apiClient.post<CalendarEvent>(PLANNER_ENDPOINTS.calendar, payload)
}

export function updateCalendarEvent(
  id: UUIDString,
  payload: CalendarEventUpdatePayload,
): Promise<CalendarEvent> {
  return apiClient.patch<CalendarEvent>(PLANNER_ENDPOINTS.calendarEvent(id), payload)
}

export function deleteCalendarEvent(id: UUIDString): Promise<void> {
  return apiClient.delete<void>(PLANNER_ENDPOINTS.calendarEvent(id), { parse: 'none' })
}

/* ---------------------------------------------------------- work sessions */

export function fetchWorkSessions(
  params: WorkSessionListParams = {},
  signal?: AbortSignal,
): Promise<Paginated<WorkSession>> {
  const { from, to, ...rest } = params
  return apiClient.get<Paginated<WorkSession>>(PLANNER_ENDPOINTS.sessions, {
    query: { ...windowQuery({ from, to }), ...queryFrom(rest as Record<string, unknown>) },
    signal,
  })
}

export function createWorkSession(payload: WorkSessionCreatePayload): Promise<WorkSession> {
  return apiClient.post<WorkSession>(PLANNER_ENDPOINTS.sessions, payload)
}

export function updateWorkSession(
  id: UUIDString,
  payload: WorkSessionUpdatePayload,
): Promise<WorkSession> {
  return apiClient.patch<WorkSession>(PLANNER_ENDPOINTS.workSession(id), payload)
}

export function deleteWorkSession(id: UUIDString): Promise<void> {
  return apiClient.delete<void>(PLANNER_ENDPOINTS.workSession(id), { parse: 'none' })
}

/**
 * `actual_start` is the **database's** clock, so there is no body to send. A
 * 422 means the session is completed or cancelled — neither can be resumed.
 */
export function startWorkSession(id: UUIDString): Promise<WorkSession> {
  return apiClient.post<WorkSession>(PLANNER_ENDPOINTS.sessionStart(id), undefined, {
    parse: 'json',
  })
}

/** Likewise bodyless: the elapsed figure is a difference of two DB timestamps. */
export function stopWorkSession(id: UUIDString): Promise<WorkSession> {
  return apiClient.post<WorkSession>(PLANNER_ENDPOINTS.sessionStop(id), undefined, {
    parse: 'json',
  })
}

/* ----------------------------------------------------------------- planner */

/**
 * `date` is the local day **in `tz`**. Omitting `tz` uses the server default;
 * passing a wrong one is a 422, never a silent shift to UTC.
 */
export function fetchPlannerDay(
  date: DateOnlyString,
  tz?: string,
  signal?: AbortSignal,
): Promise<PlannerDay> {
  return apiClient.get<PlannerDay>(PLANNER_ENDPOINTS.day, {
    query: queryFrom({ date, tz }),
    signal,
  })
}

/** `week_start` is taken as given, not snapped to Monday. */
export function fetchPlannerWeek(
  weekStart: DateOnlyString,
  tz?: string,
  signal?: AbortSignal,
): Promise<PlannerWeek> {
  return apiClient.get<PlannerWeek>(PLANNER_ENDPOINTS.week, {
    query: queryFrom({ week_start: weekStart, tz }),
    signal,
  })
}

/** `month` is `YYYY-MM` and is validated strictly, never parsed leniently. */
export function fetchPlannerMonth(
  month: string,
  tz?: string,
  signal?: AbortSignal,
): Promise<PlannerMonth> {
  return apiClient.get<PlannerMonth>(PLANNER_ENDPOINTS.month, {
    query: queryFrom({ month, tz }),
    signal,
  })
}

/** The span is named `start`/`end`, as local dates — unlike the list filters. */
export function fetchPlannerConflicts(
  params: ConflictListParams,
  signal?: AbortSignal,
): Promise<ConflictList> {
  const { start, end, tz } = params
  return apiClient.get<ConflictList>(PLANNER_ENDPOINTS.conflicts, {
    query: queryFrom({ start, end, tz }),
    signal,
  })
}

/**
 * The scheduling engine. **Nothing is written** — a suggestion is a proposal
 * and the row appears when the user acts through `POST /work-sessions`.
 *
 * A POST with no body: `tz` and the repeatable `task_ids` ride on the query
 * string. Deterministic, so the answer is stable enough to cache briefly, but
 * expensive enough that callers must gate it behind an explicit `enabled`.
 */
export function fetchSuggestions(
  params: SuggestionRequest = {},
  signal?: AbortSignal,
): Promise<SuggestionResponse> {
  const { task_ids: taskIds, tz } = params
  return apiClient.post<SuggestionResponse>(
    withRepeatedParam(PLANNER_ENDPOINTS.suggestions, 'task_ids', taskIds ?? []),
    undefined,
    { parse: 'json', query: queryFrom({ tz }), signal },
  )
}

/* ----------------------------------------------------------- availability */

/** `?tz` changes only how the wall-clock rules are *described*, never stored. */
export function fetchAvailability(
  tz?: string,
  signal?: AbortSignal,
): Promise<AvailabilityWeek> {
  return apiClient.get<AvailabilityWeek>(PLANNER_ENDPOINTS.availability, {
    query: queryFrom({ tz }),
    signal,
  })
}

/** The complete week, replacement not merge. `rules: []` clears it. */
export function replaceAvailability(
  payload: AvailabilityReplacementPayload,
  tz?: string,
): Promise<AvailabilityWeek> {
  return apiClient.put<AvailabilityWeek>(PLANNER_ENDPOINTS.availability, payload, {
    query: queryFrom({ tz }),
  })
}

/* ------------------------------------------------------------------ types */

export type {
  AvailabilityRuleInput,
  AvailabilityReplacementPayload,
  CalendarEventCreatePayload,
  CalendarEventListParams,
  CalendarEventUpdatePayload,
  ConflictListParams,
  SuggestionRequest,
  WorkSessionCreatePayload,
  WorkSessionListParams,
  WorkSessionUpdatePayload,
}