/**
 * TanStack Query bindings for the Phase 4 planner surface.
 *
 * **`plannerKeys` is the single owner of the query-key shape.** Called with no
 * argument, `events()`, `sessions()` and `conflicts()` yield the *prefix* for
 * that family; called with params they yield the key for that one query.
 *
 * **`tz` is part of the day/week/month/suggestions key, not a detail of the
 * fetch.** The same `2026-03-04` in Europe/Berlin and in America/New_York is a
 * different window of the calendar, so the two answers must never share a cache
 * entry — one of them is then rendered for the wrong day, with no error
 * anywhere.
 *
 * **Every mutation invalidates the whole `['planner']` tree.** Stopping a timer
 * that leaves the day, the week, the month, the session list, the conflict list
 * and the availability-derived capacity all stale is the classic failure here;
 * picking keys per mutation is how that ships. The tree is small and the
 * invalidation is cheap, so the aggregate is the correct trade.
 *
 * **Retry policy is inherited.** `app/query-client.ts` refuses to retry a 4xx,
 * so a 404 for another account's id surfaces as not-found on the first
 * response, and a 422 for an unknown `tz` or an over-100 `limit` is not asked
 * for again.
 *
 * **The engine is opt-in.** `useSuggestions` runs a full evaluation of the
 * backlog, so it stays disabled unless the caller passes `enabled: true`;
 * mounting the planner must not fire it on every page load.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query'

import {
  createCalendarEvent,
  createWorkSession,
  deleteCalendarEvent,
  deleteWorkSession,
  fetchAvailability,
  fetchCalendarEvents,
  fetchPlannerConflicts,
  fetchPlannerDay,
  fetchPlannerMonth,
  fetchPlannerWeek,
  fetchSuggestions,
  fetchWorkSessions,
  replaceAvailability,
  startWorkSession,
  stopWorkSession,
  updateCalendarEvent,
  updateWorkSession,
} from '@/services/planner'
import type {
  AvailabilityReplacementPayload,
  AvailabilityWeek,
  CalendarEvent,
  CalendarEventCreatePayload,
  CalendarEventListParams,
  CalendarEventUpdatePayload,
  ConflictList,
  ConflictListParams,
  DateOnlyString,
  Paginated,
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

type Enabled = { enabled?: boolean }

function calendarKeyPart(params: CalendarEventListParams = {}): unknown[] {
  return [
    params.limit ?? null,
    params.offset ?? null,
    params.from ?? null,
    params.to ?? null,
    params.project_id ?? null,
    params.task_id ?? null,
    params.event_type ?? null,
    params.sort ?? null,
    params.order ?? null,
  ]
}

function sessionKeyPart(params: WorkSessionListParams = {}): unknown[] {
  return [
    params.limit ?? null,
    params.offset ?? null,
    params.from ?? null,
    params.to ?? null,
    params.task_id ?? null,
    params.project_id ?? null,
    params.status ?? null,
    params.sort ?? null,
    params.order ?? null,
  ]
}

function conflictKeyPart(params: Partial<ConflictListParams> = {}): unknown[] {
  return [params.start ?? null, params.end ?? null, params.tz ?? null]
}

/** Stable key factory. Every key lives under the `['planner']` root. */
export const plannerKeys = {
  all: () => ['planner'] as const,
  events: (params?: CalendarEventListParams) =>
    (params
      ? ['planner', 'events', 'list', ...calendarKeyPart(params)]
      : ['planner', 'events']) as readonly unknown[],
  event: (id: UUIDString | null | undefined) => ['planner', 'event', id ?? null] as const,
  sessions: (params?: WorkSessionListParams) =>
    (params
      ? ['planner', 'sessions', 'list', ...sessionKeyPart(params)]
      : ['planner', 'sessions']) as readonly unknown[],
  session: (id: UUIDString | null | undefined) => ['planner', 'session', id ?? null] as const,
  /** `tz` is in the key: one day in two zones is two queries, not one. */
  day: (date: DateOnlyString | null | undefined, tz?: string | null) =>
    ['planner', 'day', date ?? null, tz ?? null] as const,
  week: (weekStart: DateOnlyString | null | undefined, tz?: string | null) =>
    ['planner', 'week', weekStart ?? null, tz ?? null] as const,
  month: (month: string | null | undefined, tz?: string | null) =>
    ['planner', 'month', month ?? null, tz ?? null] as const,
  conflicts: (params?: ConflictListParams) =>
    (params
      ? ['planner', 'conflicts', 'list', ...conflictKeyPart(params)]
      : ['planner', 'conflicts']) as readonly unknown[],
  /** `tz` is in the key: the same slots read in two zones are two answers. */
  suggestions: (params: SuggestionRequest = {}) =>
    ['planner', 'suggestions', params.tz ?? null] as const,
  availability: () => ['planner', 'availability'] as const,
}

/* ----------------------------------------------------------------- queries */

/**
 * Keeps the calendar grid from blanking while paging or refiltering; the
 * invalidated refetch still replaces the result.
 */
export function useCalendarEvents(
  params: CalendarEventListParams = {},
  options: Enabled = {},
): UseQueryResult<Paginated<CalendarEvent>> {
  return useQuery({
    queryKey: plannerKeys.events(params),
    queryFn: ({ signal }) => fetchCalendarEvents(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

export function useWorkSessions(
  params: WorkSessionListParams = {},
  options: Enabled = {},
): UseQueryResult<Paginated<WorkSession>> {
  return useQuery({
    queryKey: plannerKeys.sessions(params),
    queryFn: ({ signal }) => fetchWorkSessions(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

/**
 * `available_minutes` and `ratio` arrive as `null` on a day with no declared
 * hours; both are forwarded untouched so a view can render "—" rather than 0.
 */
export function usePlannerDay(
  date: DateOnlyString | null | undefined,
  tz?: string,
  options: Enabled = {},
): UseQueryResult<PlannerDay> {
  return useQuery({
    queryKey: plannerKeys.day(date, tz),
    queryFn: ({ signal }) => fetchPlannerDay(date as DateOnlyString, tz, signal),
    enabled: Boolean(date) && options.enabled !== false,
    placeholderData: (previous) => previous,
  })
}

export function usePlannerWeek(
  weekStart: DateOnlyString | null | undefined,
  tz?: string,
  options: Enabled = {},
): UseQueryResult<PlannerWeek> {
  return useQuery({
    queryKey: plannerKeys.week(weekStart, tz),
    queryFn: ({ signal }) => fetchPlannerWeek(weekStart as DateOnlyString, tz, signal),
    enabled: Boolean(weekStart) && options.enabled !== false,
    placeholderData: (previous) => previous,
  })
}

export function usePlannerMonth(
  month: string | null | undefined,
  tz?: string,
  options: Enabled = {},
): UseQueryResult<PlannerMonth> {
  return useQuery({
    queryKey: plannerKeys.month(month, tz),
    queryFn: ({ signal }) => fetchPlannerMonth(month as string, tz, signal),
    enabled: Boolean(month) && options.enabled !== false,
    placeholderData: (previous) => previous,
  })
}

export function usePlannerConflicts(
  params: ConflictListParams,
  options: Enabled = {},
): UseQueryResult<ConflictList> {
  return useQuery({
    queryKey: plannerKeys.conflicts(params),
    queryFn: ({ signal }) => fetchPlannerConflicts(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

/**
 * The scheduling engine, **off unless asked for**. A full evaluation of the
 * backlog is the most expensive read on this surface, so opening the planner
 * must not fire it; pass `enabled: true` from a deliberate "Suggest slots"
 * action. The engine writes nothing, so a short cache is safe. The zone rides in
 * the key for the same reason it does on the day/week/month queries: the panel
 * renders these slot times, so the answer for one zone must never be drawn with
 * another's label after the user switches.
 */
export function useSuggestions(
  params: SuggestionRequest = {},
  options: Enabled = {},
): UseQueryResult<SuggestionResponse> {
  return useQuery({
    queryKey: plannerKeys.suggestions(params),
    queryFn: ({ signal }) => fetchSuggestions(params, signal),
    enabled: options.enabled === true,
    staleTime: 60_000,
  })
}

/**
 * The stored weekly pattern, reported in `tz`.
 *
 * The zone rides in the key for the same reason it does on the day/week/month
 * queries: the stored rules are naive wall-clock times, so the zone only changes
 * how the answer reads — and one reader's answer must never be rendered for
 * another's.
 */
export function useAvailability(tz?: string, options: Enabled = {}): UseQueryResult<AvailabilityWeek> {
  return useQuery({
    queryKey: [...plannerKeys.availability(), tz ?? null],
    queryFn: ({ signal }) => fetchAvailability(tz, signal),
    enabled: options.enabled,
    staleTime: 5 * 60_000,
  })
}

/* --------------------------------------------------------------- mutations */

/**
 * One invalidation target for every write below, so no mutation can ship
 * having forgotten to move the day, the week, the month, the session list, the
 * conflicts or the availability.
 */
function useInvalidatePlanner() {
  const queryClient = useQueryClient()
  return () => {
    void queryClient.invalidateQueries({ queryKey: plannerKeys.all() })
  }
}

export function useCreateCalendarEvent(): UseMutationResult<
  CalendarEvent,
  Error,
  CalendarEventCreatePayload
> {
  return useMutation({ mutationFn: createCalendarEvent, onSuccess: useInvalidatePlanner() })
}

export function useUpdateCalendarEvent(): UseMutationResult<
  CalendarEvent,
  Error,
  { id: UUIDString; payload: CalendarEventUpdatePayload }
> {
  return useMutation({
    mutationFn: ({ id, payload }) => updateCalendarEvent(id, payload),
    onSuccess: useInvalidatePlanner(),
  })
}

export function useDeleteCalendarEvent(): UseMutationResult<void, Error, UUIDString> {
  return useMutation({ mutationFn: deleteCalendarEvent, onSuccess: useInvalidatePlanner() })
}

export function useCreateWorkSession(): UseMutationResult<
  WorkSession,
  Error,
  WorkSessionCreatePayload
> {
  return useMutation({ mutationFn: createWorkSession, onSuccess: useInvalidatePlanner() })
}

export function useUpdateWorkSession(): UseMutationResult<
  WorkSession,
  Error,
  { id: UUIDString; payload: WorkSessionUpdatePayload }
> {
  return useMutation({
    mutationFn: ({ id, payload }) => updateWorkSession(id, payload),
    onSuccess: useInvalidatePlanner(),
  })
}

export function useDeleteWorkSession(): UseMutationResult<void, Error, UUIDString> {
  return useMutation({ mutationFn: deleteWorkSession, onSuccess: useInvalidatePlanner() })
}

/**
 * Start and stop are two routes with one shape, for a single timer button.
 * `transition` is accepted as a synonym of `action` so a caller that already
 * reads as "transition" keeps compiling.
 */
export function useWorkSessionTimer(): UseMutationResult<
  WorkSession,
  Error,
  { id: UUIDString; action?: 'start' | 'stop'; transition?: 'start' | 'stop' }
> {
  return useMutation({
    mutationFn: ({ id, action, transition }) =>
      (action ?? transition) === 'start' ? startWorkSession(id) : stopWorkSession(id),
    onSuccess: useInvalidatePlanner(),
  })
}

/**
 * `PUT /availability` takes the **whole** week, so an empty `rules` is a real
 * answer and not an omitted field. Both `{ payload }` and a bare
 * `{ rules }` are accepted; the second just saves a wrapper at the call site.
 */
export function useReplaceAvailability(): UseMutationResult<
  AvailabilityWeek,
  Error,
  | { payload: AvailabilityReplacementPayload; tz?: string }
  | (AvailabilityReplacementPayload & { tz?: string })
> {
  return useMutation({
    mutationFn: (variables) => {
      const withWrapper = variables as {
        payload?: AvailabilityReplacementPayload
        rules?: AvailabilityReplacementPayload['rules']
        tz?: string
      }
      const payload = withWrapper.payload ?? { rules: withWrapper.rules ?? [] }
      return replaceAvailability(payload, withWrapper.tz)
    },
    onSuccess: useInvalidatePlanner(),
  })
}