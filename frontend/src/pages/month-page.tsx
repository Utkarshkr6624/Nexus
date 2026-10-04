import { useCallback, useEffect, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { LoadingState } from '@/components/feedback/loading-state'
import { PageHeader } from '@/components/feedback/page-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { ConflictList, MonthGrid, QuickAdd } from '@/features/planner/components'
import { addMonths, localInputToInstant } from '@/features/planner/datetime'
import { usePlannerConflicts, usePlannerMonth } from '@/features/planner/hooks'
import { useIsMobile } from '@/hooks/use-media-query'
import { useProjects, useTasks } from '@/features/work/hooks'
import { toApiError } from '@/services/errors'
import { MAX_PAGE_SIZE, plannerLocalTimezone, plannerToday } from '@/types/planner'
import type { DateOnlyString } from '@/types/planner'

const ZONE_KEY = 'nexus.planner.tz'

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

function readZone(): string | null {
  try {
    return window.localStorage.getItem(ZONE_KEY)
  } catch {
    return null
  }
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

function isMonth(value: string | null): value is string {
  return Boolean(value && /^\d{4}-(0[1-9]|1[0-2])$/.test(value))
}

function isDateKey(value: string | null): value is DateOnlyString {
  return Boolean(value && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(`${value}T12:00:00Z`)))
}

/**
 * A 09:00-10:00 window on `date`, as instants resolved in `timeZone`. The
 * dialog renders the seed into a `datetime-local` input in the same zone, so it
 * has to be the instant 09:00 *there* means.
 */
function defaultBlock(date: DateOnlyString, timeZone: string): { starts_at: string; ends_at: string } {
  return {
    starts_at: localInputToInstant(`${date}T09:00`, timeZone) ?? `${date}T09:00:00Z`,
    ends_at: localInputToInstant(`${date}T10:00`, timeZone) ?? `${date}T10:00:00Z`,
  }
}

/** The local days the month route actually covers, for the deadlines query. */
function monthBounds(month: string): { start: DateOnlyString; end: DateOnlyString } {
  const [year = 1970, number = 1] = month.split('-').map(Number)
  const last = new Date(Date.UTC(year, number, 0, 12))
  return { start: `${month}-01`, end: last.toISOString().slice(0, 10) }
}

/**
 * The month as its own destination.
 *
 * Split from the planner on purpose: a month answers a different question —
 * "what does the whole month look like" rather than "what am I doing on
 * Tuesday" — and, unlike the week grid, it still works on a phone. The month and
 * the selected day still live in the address bar (`?month=`, `?date=`), so a
 * month is shareable and a refresh lands where it left off; `tz` is inherited
 * from the planner's stored preference so the two pages cannot disagree about
 * which zone "Monday" means.
 */
export default function MonthPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const isMobile = useIsMobile()

  const zoneParam = searchParams.get('tz')
  const storedZone = readZone()
  const tz = isZone(zoneParam) ? zoneParam : isZone(storedZone) ? storedZone : plannerLocalTimezone()
  const today = plannerToday(tz)

  const requestedMonth = searchParams.get('month')
  const month = isMonth(requestedMonth) ? requestedMonth : today.slice(0, 7)
  const selectedParam = searchParams.get('date')
  const selected = isDateKey(selectedParam) ? selectedParam : today

  const navigate = useCallback(
    (next: { month?: string; date?: DateOnlyString; tz?: string | null }) => {
      const params = new URLSearchParams()
      const nextMonth = next.month ?? month
      const nextDate = next.date ?? selected
      const nextZone = next.tz === undefined ? zoneParam : next.tz
      if (nextMonth !== today.slice(0, 7)) params.set('month', nextMonth)
      if (nextDate !== today) params.set('date', nextDate)
      if (nextZone && nextZone !== plannerLocalTimezone()) params.set('tz', nextZone)
      setSearchParams(params)
    },
    [month, selected, setSearchParams, today, zoneParam],
  )

  useEffect(() => {
    try {
      window.localStorage.setItem(ZONE_KEY, tz)
    } catch {
      // Storage unavailable: the zone still applies for this page load.
    }
  }, [tz])

  const monthQuery = usePlannerMonth(month, tz)
  const bounds = useMemo(() => monthBounds(month), [month])
  const conflicts = usePlannerConflicts({ start: bounds.start, end: bounds.end, tz })
  const projects = useProjects({ limit: MAX_PAGE_SIZE, order: 'asc' })
  const tasksQuery = useTasks({
    due_after: bounds.start,
    due_before: bounds.end,
    limit: MAX_PAGE_SIZE,
  })

  // Deadlines are the only thing on a cell that changes what today requires, so
  // they are fetched for the whole month and ranked above everything else.
  const deadlines = useMemo(() => {
    const map = new Map<string, { id: string; title: string }[]>()
    for (const task of tasksQuery.data?.items ?? []) {
      if (!task.due_date || task.status === 'completed' || task.status === 'cancelled') continue
      const existing = map.get(task.due_date)
      if (existing) existing.push({ id: task.id, title: task.title })
      else map.set(task.due_date, [{ id: task.id, title: task.title }])
    }
    return map
  }, [tasksQuery.data])

  const days = monthQuery.data?.days ?? []
  const overloadedDays = days.filter((day) => day.available_minutes !== null && day.overloaded).length
  const busyDays = days.filter((day) => day.scheduled_minutes > 0).length

  function stepMonth(amount: number) {
    const next = addMonths(month, amount)
    navigate({ month: next, date: `${next}-01` })
  }

  return (
    <div className="app-container space-y-5 py-6">
      <PageHeader
        title="Month"
        eyebrow="Planner"
        description="Deadlines, events, scheduled work and the days that are over capacity. Each cell draws at most three rows and says how many it left out — a cell that shows everything shows nothing."
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
              tasks={tasksQuery.data?.items ?? []}
              timeZone={tz}
              defaults={defaultBlock(selected, tz)}
            />
          </>
        }
      />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-1">
          <Button
            size="icon"
            variant="outline"
            className="size-9"
            aria-label="Previous month"
            onClick={() => stepMonth(-1)}
          >
            <ChevronLeft aria-hidden="true" />
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => navigate({ month: today.slice(0, 7), date: today })}
          >
            Today
          </Button>
          <Button size="icon" variant="outline" className="size-9" aria-label="Next month" onClick={() => stepMonth(1)}>
            <ChevronRight aria-hidden="true" />
          </Button>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span>
            {busyDays} day{busyDays === 1 ? '' : 's'} with something on
          </span>
          {overloadedDays > 0 && (
            <Badge variant="destructive">
              {overloadedDays} overloaded day{overloadedDays === 1 ? '' : 's'}
            </Badge>
          )}
          <span>
            {deadlines.size} day{deadlines.size === 1 ? '' : 's'} with a deadline
          </span>
        </div>
      </div>

      {isMobile && (
        <p className="text-xs text-muted-foreground">
          Each cell shows a count rather than a squeezed list — open a day on the planner to see what is
          on it.
        </p>
      )}

      {monthQuery.isPending && !monthQuery.data ? (
        <LoadingState label="Loading the month" />
      ) : monthQuery.isError ? (
        <ErrorState error={toApiError(monthQuery.error)} onRetry={() => void monthQuery.refetch()} />
      ) : (
        <>
          <MonthGrid
            month={month}
            timeZone={tz}
            days={days}
            deadlines={deadlines}
            selectedDate={selected}
            onSelectDate={(date) => navigate({ date })}
            onShiftMonth={stepMonth}
          />

          {days.length === 0 && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <CalendarDays aria-hidden="true" className="size-4" />
              The backend returned no days for {month}.
            </p>
          )}
        </>
      )}

      {tasksQuery.isError && (
        <p className="text-xs text-muted-foreground">
          Deadlines could not be loaded for this month ({toApiError(tasksQuery.error).message}); the grid
          shows events and scheduled work only.
        </p>
      )}

      <ConflictList
        conflicts={conflicts.data?.conflicts ?? []}
        window={conflicts.data?.window}
        isLoading={conflicts.isPending}
        error={conflicts.isError ? toApiError(conflicts.error) : null}
        onRetry={() => void conflicts.refetch()}
      />
    </div>
  )
}