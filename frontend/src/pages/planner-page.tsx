import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CalendarPlus, ChevronLeft, ChevronRight, Globe, Sparkles } from 'lucide-react'

import { EmptyState } from '@/components/feedback/empty-state'
import { ErrorState } from '@/components/feedback/error-state'
import { PageHeader } from '@/components/feedback/page-header'
import { Alert, AlertDescription, AlertIcon, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  AvailabilityEditor,
  ConflictList,
  EventChip,
  MonthGrid,
  OverloadMeter,
  PlannerTimeline,
  QuickAdd,
  SuggestionCard,
  WorkSessionCard,
} from '@/features/planner/components'
import {
  useCalendarEvents,
  usePlannerConflicts,
  usePlannerDay,
  usePlannerMonth,
  usePlannerWeek,
  useSuggestions,
  useWorkSessions,
} from '@/features/planner/hooks'
import { addMonths, formatCalendarDate, localInputToInstant, shiftDate, startOfWeek } from '@/features/planner/datetime'
import { useIsMobile } from '@/hooks/use-media-query'
import { useProjects, useTasks } from '@/features/work/hooks'
import { toApiError } from '@/services/errors'
import {
  MAX_PAGE_SIZE,
  plannerLocalTimezone,
  plannerMonth,
  plannerToday,
  plannerWeekStart,
} from '@/types/planner'
import { toast } from '@/stores/toast-store'
import type {
  CalendarEvent,
  DateOnlyString,
  PlannerDay,
  PlannerSuggestion,
  WorkSession,
} from '@/types/planner'

const VIEW_KEY = 'nexus.planner.view'
const ZONE_KEY = 'nexus.planner.tz'
/** How far the agenda looks, in days from the selected one. */
const AGENDA_DAYS = 14

type ViewMode = 'day' | 'week' | 'month' | 'agenda'

const VIEWS: ViewMode[] = ['day', 'week', 'month', 'agenda']

/**
 * Zones offered in the toolbar: a short, common list, because a select nobody
 * can scan is worse than a short one. A zone the browser reports but the list
 * omits still works — it is appended as its own option rather than rewritten to
 * the default, which would silently move every day boundary.
 */
const ZONES = [
  'UTC',
  'America/Los_Angeles',
  'America/Denver',
  'America/Chicago',
  'America/New_York',
  'America/Sao_Paulo',
  'Europe/London',
  'Europe/Paris',
  'Europe/Berlin',
  'Europe/Madrid',
  'Europe/Warsaw',
  'Africa/Lagos',
  'Africa/Nairobi',
  'Asia/Dubai',
  'Asia/Kolkata',
  'Asia/Shanghai',
  'Asia/Singapore',
  'Asia/Tokyo',
  'Australia/Sydney',
  'Pacific/Auckland',
]

function readStored(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function isView(value: string | null): value is ViewMode {
  return value !== null && (VIEWS as string[]).includes(value)
}

function isDateKey(value: string | null): value is DateOnlyString {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  return Number.isFinite(Date.parse(`${value}T12:00:00Z`))
}

function isZone(value: string | null): value is string {
  if (!value) return false
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: value })
    return true
  } catch {
    return false
  }
}

/**
 * A 09:00–10:00 window on `date`, as instants resolved in `timeZone`.
 *
 * The seed is passed to the dialog, which renders it into a `datetime-local`
 * input in the same zone — so it has to be the instant 09:00 *there* means, not
 * 09:00 UTC wearing a local label.
 */
function defaultBlock(date: DateOnlyString, timeZone: string): { starts_at: string; ends_at: string } {
  return {
    starts_at: localInputToInstant(`${date}T09:00`, timeZone) ?? `${date}T09:00:00Z`,
    ends_at: localInputToInstant(`${date}T10:00`, timeZone) ?? `${date}T10:00:00Z`,
  }
}

/** The heading for whatever is on screen, labelled in the requested zone. */
function viewHeading(view: ViewMode, date: DateOnlyString): string {
  if (view === 'month') return formatCalendarDate(`${plannerMonth('', date)}-01`, { month: 'long', year: 'numeric' })
  if (view === 'week') {
    const from = startOfWeek(date)
    return `${formatCalendarDate(from)} – ${formatCalendarDate(shiftDate(from, 6))}`
  }
  if (view === 'agenda') return `Next ${AGENDA_DAYS} days from ${formatCalendarDate(date)}`
  return formatCalendarDate(date, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
}

/**
 * Day, Week, Month and Agenda behind one switcher, over one URL.
 *
 * **The URL owns the view and the selected day**, so a view is shareable and
 * survives a refresh. `localStorage` only seeds the *first* visit: once the
 * address bar names a view that has to win, or a shared link would land on
 * whatever the recipient happened to use last.
 *
 * **Mobile never renders the week grid.** Seven columns on a phone are seven
 * unreadable slivers, so `week` resolves to the agenda below `sm` rather than
 * being squeezed into something that technically fits.
 */
export default function PlannerPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const isMobile = useIsMobile()

  const requestedView = searchParams.get('view')
  const storedView = readStored(VIEW_KEY)
  const view: ViewMode = isView(requestedView)
    ? requestedView
    : isView(storedView)
      ? storedView
      : 'day'

  const zoneParam = searchParams.get('tz')
  const storedZone = readStored(ZONE_KEY)
  const tz = isZone(zoneParam)
    ? zoneParam
    : isZone(storedZone)
      ? storedZone
      : plannerLocalTimezone()

  const dateParam = searchParams.get('date')
  const date = isDateKey(dateParam) ? dateParam : plannerToday(tz)
  const today = plannerToday(tz)

  const [suggestOn, setSuggestOn] = useState(false)

  const effectiveView: ViewMode = isMobile && view === 'week' ? 'agenda' : view
  const weekStart = plannerWeekStart(tz, date)
  const month = plannerMonth(tz, date)

  /** The single writer for the address bar; a `null` drops a key to its default. */
  const navigate = useCallback(
    (next: { view?: ViewMode; date?: DateOnlyString; tz?: string | null }) => {
      const params = new URLSearchParams()
      const nextView = next.view ?? view
      const nextDate = next.date ?? date
      const nextZone = next.tz === undefined ? zoneParam : next.tz
      if (nextView !== 'day') params.set('view', nextView)
      if (nextDate !== plannerToday(tz)) params.set('date', nextDate)
      if (nextZone && nextZone !== plannerLocalTimezone()) params.set('tz', nextZone)
      setSearchParams(params)
    },
    [date, setSearchParams, tz, view, zoneParam],
  )

  useEffect(() => {
    try {
      window.localStorage.setItem(VIEW_KEY, view)
      window.localStorage.setItem(ZONE_KEY, tz)
    } catch {
      // Storage unavailable: the choice still applies for this page load.
    }
  }, [tz, view])

  const shift = useCallback(
    (amount: number) => {
      if (view === 'month') {
        navigate({ date: `${addMonths(month, amount)}-01` })
        return
      }
      navigate({ date: shiftDate(date, amount * (view === 'week' ? 7 : 1)) })
    },
    [date, month, navigate, view],
  )

  /* ---------------------------------------------------------------- queries */

  const day = usePlannerDay(date, tz, { enabled: effectiveView === 'day' })
  const week = usePlannerWeek(weekStart, tz, { enabled: effectiveView === 'week' })
  const monthQuery = usePlannerMonth(month, tz, { enabled: effectiveView === 'month' })

  // The agenda reads the two flat lists over a window rather than fourteen
  // planner days: one round trip each instead of one per day. The bounds are
  // local midnight in `tz` resolved to instants — a `T00:00:00Z` literal would
  // silently shift the window by the zone's offset.
  const agendaFrom = localInputToInstant(`${date}T00:00`, tz) ?? `${date}T00:00:00Z`
  const agendaTo =
    localInputToInstant(`${shiftDate(date, AGENDA_DAYS)}T00:00`, tz) ??
    `${shiftDate(date, AGENDA_DAYS)}T00:00:00Z`
  const agendaEvents = useCalendarEvents(
    { from: agendaFrom, to: agendaTo, limit: MAX_PAGE_SIZE, sort: 'starts_at', order: 'asc' },
    { enabled: effectiveView === 'agenda' },
  )
  const agendaSessions = useWorkSessions(
    { from: agendaFrom, to: agendaTo, limit: MAX_PAGE_SIZE, sort: 'scheduled_start', order: 'asc' },
    { enabled: effectiveView === 'agenda' },
  )

  const projects = useProjects({ limit: MAX_PAGE_SIZE, order: 'asc' })

  // Deadlines for the month grid, and the task titles the day, week and agenda
  // show for blocks booked against one.
  const deadlinesQuery = useTasks(
    { due_after: plannerWeekStart(tz, date), due_before: shiftDate(date, 42), limit: MAX_PAGE_SIZE },
    { enabled: effectiveView === 'month' },
  )
  const tasksQuery = useTasks({ limit: MAX_PAGE_SIZE })
  const tasks = useMemo(() => tasksQuery.data?.items ?? [], [tasksQuery.data])
  const taskTitles = useMemo(() => {
    const map = new Map<string, string>()
    for (const task of tasks) map.set(task.id, task.title)
    return map
  }, [tasks])

  const deadlines = useMemo(() => {
    const map = new Map<string, { id: string; title: string }[]>()
    for (const task of deadlinesQuery.data?.items ?? []) {
      if (!task.due_date || task.status === 'completed' || task.status === 'cancelled') continue
      const existing = map.get(task.due_date)
      if (existing) existing.push({ id: task.id, title: task.title })
      else map.set(task.due_date, [{ id: task.id, title: task.title }])
    }
    return map
  }, [deadlinesQuery.data])

  // Opt-in: a full evaluation of the backlog is the most expensive read on this
  // surface, so opening the planner must not fire it.
  const suggestions = useSuggestions({ tz }, { enabled: suggestOn })

  const conflictSpan = useMemo(() => {
    if (effectiveView === 'week') return { start: weekStart, end: shiftDate(weekStart, 6) }
    if (effectiveView === 'agenda') return { start: date, end: shiftDate(date, AGENDA_DAYS - 1) }
    return { start: date, end: date }
  }, [date, effectiveView, weekStart])
  const conflicts = usePlannerConflicts({ ...conflictSpan, tz })

  /* -------------------------------------------------------------- the agenda */

  const agendaGroups = useMemo(() => {
    type Row =
      | { kind: 'event'; start: string; event: CalendarEvent }
      | { kind: 'session'; start: string; session: WorkSession }

    const rows: Array<Row & { dayKey: DateOnlyString }> = [
      ...(agendaEvents.data?.items ?? []).map((event) => ({
        kind: 'event' as const,
        start: event.starts_at,
        event,
        // Which day an instant falls on is a question about the zone; slicing
        // the wire string would answer it in UTC.
        dayKey: localDayKey(event.starts_at, tz),
      })),
      ...(agendaSessions.data?.items ?? []).map((session) => ({
        kind: 'session' as const,
        start: session.scheduled_start,
        session,
        dayKey: localDayKey(session.scheduled_start, tz),
      })),
    ]

    const groups = new Map<DateOnlyString, Row[]>()
    for (const row of rows.sort((a, b) => Date.parse(a.start) - Date.parse(b.start))) {
      const bucket = groups.get(row.dayKey)
      if (bucket) bucket.push(row)
      else groups.set(row.dayKey, [row])
    }
    return [...groups.entries()]
  }, [agendaEvents.data, agendaSessions.data, tz])

  return (
    <div className="app-container space-y-5 py-6">
      <PageHeader
        title="Planner"
        description="What the week actually contains: blocks laid against hours you declare, conflicts surfaced, and nothing invented when there is nothing to suggest. The view, the day and the zone live in the address bar, so any of them can be shared."
        actions={
          <>
            <div className="w-44">
              <Select value={tz} aria-label="Time zone" onChange={(event) => navigate({ tz: event.target.value })}>
                {!ZONES.includes(tz) && <option value={tz}>{tz}</option>}
                {ZONES.map((zone) => (
                  <option key={zone} value={zone}>
                    {zone}
                  </option>
                ))}
              </Select>
            </div>
            <QuickAdd
              projects={projects.data?.items ?? []}
              tasks={tasks}
              timeZone={tz}
              defaults={defaultBlock(date, tz)}
            />
          </>
        }
      />

      {/* One `Tabs` for the whole planner, not one for the header and another
          for the body. `Tabs` derives both the trigger ids and the panel id from
          a per-instance `useId()`, so a `TabsList` and a `TabsContent` in
          separate instances produce a panel whose `aria-labelledby` names a tab
          that exists nowhere — the entire planner body exposed as an unnamed
          tabpanel. Rendering the list as a sibling of the panel would throw
          instead, which is why both live in this one. */}
      <Tabs value={view} onValueChange={(next) => navigate({ view: next as ViewMode })}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-1">
          <Button size="icon" variant="outline" className="size-9" aria-label="Previous" onClick={() => shift(-1)}>
            <ChevronLeft aria-hidden="true" />
          </Button>
          <Button size="sm" variant="outline" onClick={() => navigate({ date: today })}>
            Today
          </Button>
          <Button size="icon" variant="outline" className="size-9" aria-label="Next" onClick={() => shift(1)}>
            <ChevronRight aria-hidden="true" />
          </Button>
          <p className="ml-2 text-sm font-medium text-foreground">{viewHeading(view, date)}</p>
          <p className="flex items-center gap-1 text-xs text-muted-foreground">
            <Globe aria-hidden="true" className="size-3.5" />
            {tz}
          </p>
        </div>

        <TabsList aria-label="Planner view">
          <TabsTrigger value="day">Day</TabsTrigger>
          <TabsTrigger value="week">Week</TabsTrigger>
          <TabsTrigger value="month">Month</TabsTrigger>
          <TabsTrigger value="agenda">Agenda</TabsTrigger>
        </TabsList>
      </div>

      {isMobile && view === 'week' && (
        <p className="text-xs text-muted-foreground">
          Showing the agenda on a narrow screen — a seven-column week is not readable here.
        </p>
      )}

      {/* Only the selected panel mounts, so switching views never fires the
          queries for the three views nobody is looking at. */}
      <TabsContent value={view} className="mt-0 space-y-5">
        {effectiveView === 'day' && (
          <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,1fr)]">
            <div className="space-y-4">
              <OverloadMeter
                availableMinutes={day.data?.available_minutes ?? null}
                scheduledMinutes={day.data?.scheduled_minutes ?? 0}
                overloadMinutes={day.data?.overload_minutes ?? null}
                ratio={day.data?.ratio ?? null}
                overloaded={day.data?.overloaded ?? false}
                label={formatCalendarDate(date)}
              />

              <PlannerTimeline
                day={day.data}
                timeZone={tz}
                isToday={date === today}
                isLoading={day.isPending && !day.data}
                error={day.isError ? toApiError(day.error) : null}
                onRetry={() => void day.refetch()}
              />

              <SuggestionsBlock
                open={suggestOn}
                onToggle={() => setSuggestOn((current) => !current)}
                onRetry={() => void suggestions.refetch()}
                zone={tz}
                suggestions={suggestions.data?.suggestions ?? []}
                reasonIfEmpty={suggestions.data?.reason_if_empty ?? null}
                isLoading={suggestOn && suggestions.isPending}
                isError={suggestOn && suggestions.isError}
                error={suggestOn && suggestions.isError ? toApiError(suggestions.error) : null}
              />
            </div>

            <aside>
              <AvailabilityEditor timeZone={tz} />
            </aside>
          </div>
        )}

        {effectiveView === 'week' && (
          <>
            {week.data && (
              <div className="flex flex-wrap items-center gap-4 rounded-lg border border-border bg-card px-4 py-3 text-xs">
                <span className="text-muted-foreground">
                  Scheduled{' '}
                  <strong className="text-foreground">{week.data.totals.scheduled_minutes}m</strong>
                </span>
                <span className="text-muted-foreground">
                  Available{' '}
                  <strong className="text-foreground">
                    {week.data.totals.available_minutes === null
                      ? '—'
                      : `${week.data.totals.available_minutes}m`}
                  </strong>
                </span>
                {week.data.totals.overloaded_days > 0 && (
                  <Badge variant="destructive">
                    {week.data.totals.overloaded_days} overloaded day
                    {week.data.totals.overloaded_days === 1 ? '' : 's'}
                  </Badge>
                )}
                <span className="text-muted-foreground">
                  {week.data.totals.event_count} event{week.data.totals.event_count === 1 ? '' : 's'} ·{' '}
                  {week.data.totals.session_count} block
                  {week.data.totals.session_count === 1 ? '' : 's'}
                </span>
              </div>
            )}

            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-7">
              {(week.data?.days ?? []).map((entry) => (
                <WeekDayCard
                  key={entry.date}
                  day={entry}
                  timeZone={tz}
                  isToday={entry.date === today}
                  taskTitles={taskTitles}
                  onSelect={() => navigate({ view: 'day', date: entry.date })}
                />
              ))}
            </div>

            <AvailabilityEditor timeZone={tz} />
          </>
        )}

        {effectiveView === 'month' && (
          <MonthGrid
            month={month}
            timeZone={tz}
            days={monthQuery.data?.days}
            isLoading={monthQuery.isPending && !monthQuery.data}
            error={monthQuery.isError ? toApiError(monthQuery.error) : null}
            onRetry={() => void monthQuery.refetch()}
            deadlines={deadlines}
            selectedDate={date}
            onSelectDate={(next) => navigate({ view: 'day', date: next })}
            onShiftMonth={(amount) => navigate({ view: 'month', date: `${addMonths(month, amount)}-01` })}
          />
        )}

        {effectiveView === 'agenda' && (
          <section aria-label={`Agenda from ${date}`} className="space-y-3">
            <p className="text-xs text-muted-foreground">
              {AGENDA_DAYS} days as one list, each row bucketed into its{' '}
              <strong className="text-foreground">{tz}</strong> day — never the UTC one.
            </p>

            {agendaGroups.length === 0 && !agendaEvents.isPending && !agendaSessions.isPending ? (
              <EmptyState
                icon={CalendarPlus}
                compact
                title="Nothing scheduled"
                description={`No events or focus blocks between ${formatCalendarDate(date)} and ${formatCalendarDate(shiftDate(date, AGENDA_DAYS - 1))}.`}
                action={
                  <QuickAdd
                    projects={projects.data?.items ?? []}
                    tasks={tasks}
                    timeZone={tz}
                    defaults={defaultBlock(date, tz)}
                  />
                }
              />
            ) : (
              agendaGroups.map(([dayKey, rows]) => (
                <div key={dayKey} className="rounded-lg border border-border bg-card">
                  <h3
                    className={
                      dayKey === today
                        ? 'border-b border-border px-3 py-2 text-sm font-semibold text-primary'
                        : 'border-b border-border px-3 py-2 text-sm font-semibold text-foreground'
                    }
                  >
                    {formatCalendarDate(dayKey)}
                    {dayKey === today && <span className="ml-2 text-xs font-normal">today</span>}
                  </h3>
                  <ul className="space-y-1.5 p-2">
                    {rows.map((row) => (
                      <li key={row.kind === 'event' ? `e-${row.event.id}` : `s-${row.session.id}`}>
                        {row.kind === 'event' ? (
                          <EventChip event={row.event} timeZone={tz} showDate />
                        ) : (
                          <WorkSessionCard
                            session={row.session}
                            timeZone={tz}
                            taskTitle={row.session.task_id ? taskTitles.get(row.session.task_id) : undefined}
                          />
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              ))
            )}
          </section>
        )}
      </TabsContent>

      {effectiveView !== 'month' && (
        <ConflictList
          conflicts={conflicts.data?.conflicts ?? []}
          window={conflicts.data?.window}
          isLoading={conflicts.isPending}
          error={conflicts.isError ? toApiError(conflicts.error) : null}
          onRetry={() => void conflicts.refetch()}
        />
      )}
      </Tabs>
    </div>
  )
}

/* ------------------------------------------------------------- small parts */

/**
 * The `YYYY-MM-DD` an instant falls on in `timeZone` — the one sanctioned
 * instant → day conversion on this page. `slice(0, 10)` on the wire string
 * would answer in UTC and file a 23:30 block on tomorrow for anyone east of
 * Greenwich.
 */
function localDayKey(instant: string, timeZone: string): DateOnlyString {
  try {
    return new Intl.DateTimeFormat('en-CA', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).format(new Date(instant))
  } catch {
    return instant.slice(0, 10)
  }
}

/**
 * The suggestions strip, behind a deliberate action.
 *
 * **An empty list is answered out loud.** `reason_if_empty` names which
 * precondition was missing; a blank panel would leave "nothing to schedule"
 * indistinguishable from "the scheduler is broken", and those need different
 * things from the reader.
 */
function SuggestionsBlock({
  open,
  onToggle,
  onRetry,
  zone,
  suggestions,
  reasonIfEmpty,
  isLoading,
  isError,
  error,
}: {
  open: boolean
  onToggle: () => void
  onRetry: () => void
  zone: string
  suggestions: PlannerSuggestion[]
  reasonIfEmpty: string | null
  isLoading: boolean
  isError: boolean
  error: ReturnType<typeof toApiError> | null
}) {
  if (!open) {
    return (
      <Button size="sm" variant="outline" onClick={onToggle}>
        <Sparkles aria-hidden="true" />
        Suggest slots for this week
      </Button>
    )
  }

  if (isLoading) return <p className="text-sm text-muted-foreground">Walking the backlog…</p>
  if (isError && error) return <ErrorState error={error} onRetry={onRetry} compact />

  if (suggestions.length === 0) {
    return (
      <Alert>
        <AlertIcon />
        <AlertTitle>No suggestions</AlertTitle>
        <AlertDescription>
          {reasonIfEmpty ??
            'The engine returned nothing and did not say why. Declare working hours, or give an open task an estimate and a due date, then ask again.'}
        </AlertDescription>
      </Alert>
    )
  }

  return (
    <section aria-label="Suggested slots" className="space-y-2">
      <h2 className="text-sm font-semibold text-foreground">
        {suggestions.length} suggested slot{suggestions.length === 1 ? '' : 's'}
      </h2>
      <ul className="space-y-2">
        {suggestions.map((suggestion) => (
          <li key={suggestion.task_id}>
            <SuggestionCard
              suggestion={suggestion}
              timeZone={zone}
              onAccepted={() => toast.info('Booked', `${suggestion.task_title} is on the calendar.`)}
            />
          </li>
        ))}
      </ul>
    </section>
  )
}

/** One column of the week: the day's load, then its events and its blocks. */
function WeekDayCard({
  day,
  timeZone,
  isToday,
  taskTitles,
  onSelect,
}: {
  day: PlannerDay
  timeZone: string
  isToday: boolean
  taskTitles: Map<string, string>
  onSelect: () => void
}) {
  const overloaded = day.available_minutes !== null && day.overloaded

  return (
    <section
      aria-label={formatCalendarDate(day.date)}
      className={
        isToday
          ? 'flex flex-col gap-2 rounded-lg border border-primary/40 bg-card p-2'
          : 'flex flex-col gap-2 rounded-lg border border-border bg-card p-2'
      }
    >
      <header className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={onSelect}
          className={isToday ? 'text-xs font-semibold text-primary' : 'text-xs font-semibold text-foreground'}
        >
          {formatCalendarDate(day.date)}
        </button>
        <span className="text-[11px] tabular-nums text-muted-foreground">
          {day.available_minutes === null
            ? 'no hours'
            : overloaded
              ? `+${day.overload_minutes ?? 0}m over`
              : `${day.scheduled_minutes}m`}
        </span>
      </header>

      {day.events.length === 0 && day.sessions.length === 0 ? (
        <p className="py-2 text-center text-[11px] text-muted-foreground">Nothing booked</p>
      ) : (
        <ul className="space-y-1">
          {day.events.map((event) => (
            <li key={`e-${event.id}`}>
              <EventChip event={event} timeZone={timeZone} />
            </li>
          ))}
          {day.sessions.map((session) => (
            <li key={`s-${session.id}`}>
              <WorkSessionCard
                session={session}
                timeZone={timeZone}
                taskTitle={session.task_id ? taskTitles.get(session.task_id) : undefined}
                className="py-1"
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}