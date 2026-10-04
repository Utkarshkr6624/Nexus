import { useMemo } from 'react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  ArrowUpRight,
  CalendarClock,
  CheckCircle2,
  ListTodo,
  RefreshCw,
  Sparkles,
  Target,
  Timer,
} from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { EmptyState } from '@/components/feedback/empty-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import { useHealth } from '@/features/health/use-health'
import { formatLatency, formatRelative, formatTimestamp, formatUptime, greetingFor, longDate } from '@/features/health/format'
import {
  DateRangePicker,
  EmptyAnalytics,
  MetricCard,
  StalenessBanner,
} from '@/features/analytics/components'
import {
  AnalyticsBarChart,
  LazyChart,
  TimeDistributionChart,
  TrendChart,
} from '@/features/analytics/components/lazy-charts'
import type { ChartRow } from '@/features/analytics/components'
import {
  useAnalyticsStaleness,
  useAnalyticsWindow,
  useOverview,
  useProjectAnalytics,
  useRebuildAnalytics,
  useTimeDistribution,
} from '@/features/analytics/hooks'
import {
  NO_VALUE,
  formatMetricDate,
  formatMinutes,
  formatNumber,
  formatPercent,
  formatScore,
  formatShortDate,
} from '@/features/analytics/format'
import { useActivity, useTasks } from '@/features/work/hooks'
import { SeverityBadge } from '@/features/risk/components/severity-badge'
import { useRiskSummary, useRisks } from '@/features/risk/hooks'
import { selectDisplayName, useAuthStore } from '@/stores/auth-store'
import { toApiError } from '@/services/errors'
import { cn } from '@/lib/utils'
import { formatRangeLabel } from '@/types/analytics'
import { WORK_EVENT_META } from '@/types/work'
import type { ComparisonTotal, DailyMetricRead } from '@/types/analytics'
import type { RiskSummaryRead } from '@/types/risk'

/**
 * The dashboard is an intelligence surface, not a launchpad.
 *
 * **Hierarchy over card count.** The brief is explicit that a dashboard of
 * twenty equal-weight tiles is not a design, so the page leads with one headline
 * row — the score, the work, the pressure on it — and then a small number of
 * substantial panels. Reading order is deliberate: what happened, where it went,
 * what is at risk, what is next.
 *
 * **Every figure is an API response.** There is no sample activity, no
 * illustrative project and no placeholder figure left over from Phase 1. A
 * metric the backend cannot compute renders its `reason_if_unavailable`
 * verbatim, and a `null` renders as a dash — never as `0`.
 *
 * **One window, one set of numbers.** Every analytics panel reads from the same
 * `/overview` response wherever that response carries the figure, so the
 * headline row cannot disagree with the chart below it. The exceptions are the
 * three endpoints `/overview` does not fold in — the time distribution, the
 * per-project rollups and the work feed — which are fetched for the panels that
 * read them.
 *
 * **The detection engine gets one strip, not a grid.** `RiskSignals` answers
 * "does anything need me?" in a sentence and three rows, and sits below the
 * panels rather than among them. It reads its own endpoint rather than
 * `/overview`, because a finding is not a metric over the selected window: it is
 * a conclusion about the current plan, and it would be wrong to rescale it every
 * time the date picker moves.
 */
export default function DashboardPage() {
  const user = useAuthStore((state) => state.user)
  const displayName = selectDisplayName(user)
  const now = useMemo(() => new Date(), [])

  const health = useHealth()
  const window = useAnalyticsWindow()

  const params = useMemo(
    () => ({
      start_date: window.range.start_date,
      end_date: window.range.end_date,
      granularity: window.granularity,
    }),
    [window.range.start_date, window.range.end_date, window.granularity],
  )

  const rangeLabel = formatRangeLabel(window.range.start_date, window.range.end_date)
  const overview = useOverview(params)
  const time = useTimeDistribution(params)
  const projects = useProjectAnalytics(params)
  const activity = useActivity({ limit: 6 })
  const deadlines = useTasks({
    limit: 8,
    due_after: window.range.start_date,
    due_before: window.range.end_date,
    sort: 'due_date',
    order: 'asc',
  })

  const rebuild = useRebuildAnalytics()
  const staleness = useAnalyticsStaleness(window.range, overview.data)
  const apiError = overview.error ? toApiError(overview.error) : null
  const healthError = health.error ? toApiError(health.error) : null

  const refreshing = health.isFetching || overview.isFetching

  return (
    <div className="app-container space-y-6 py-6 lg:py-8">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0 space-y-1.5">
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            {longDate(now)}
          </p>
          <h1 className="text-xl font-semibold tracking-tight text-foreground">
            {greetingFor(now)}, {displayName}
          </h1>
          <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">
            Everything below is computed from your own recorded activity, over{' '}
            {rangeLabel.toLowerCase()}. Where a figure cannot be measured, it says why rather than
            reporting zero.
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <Button type="button" variant="outline" asChild>
            <Link to="/analytics">
              Open analytics
              <ArrowUpRight aria-hidden="true" />
            </Link>
          </Button>
          <Button
            type="button"
            onClick={() => {
              void health.refetch()
              void overview.refetch()
            }}
            disabled={refreshing}
          >
            {refreshing ? <Spinner size="sm" /> : <RefreshCw aria-hidden="true" />}
            Refresh
          </Button>
        </div>
      </header>

      <DateRangePicker
        preset={window.preset}
        start={window.range.start_date}
        end={window.range.end_date}
        granularity={window.granularity}
        onPresetChange={window.setPreset}
        onCustomChange={window.setCustom}
        onGranularityChange={window.setGranularity}
      />

      {/*
        Analytics failing and the backend being down are reported once each, by
        the surface that owns them. This banner carries no retry of its own: the
        header's Refresh covers the whole page, and a second "Retry" button here
        would compete with the health card's own recovery.
      */}
      {apiError && !overview.data && (
        <ErrorState error={apiError} title="Analytics could not load" className="py-4" />
      )}

      {overview.isPending && !overview.data && <HeadlineSkeleton />}

      {overview.data && (
        <>
          <StalenessBanner
            {...staleness}
            updatedAt={latestUpdatedAt(overview.data.daily)}
            rowsWritten={rebuild.data?.rows_written ?? null}
            isRecalculating={rebuild.isPending}
            onRecalculate={() => rebuild.mutate(params)}
          />

          <HeadlineRow totals={overview.data.totals} overview={overview.data} />

          <div className="grid gap-4 lg:grid-cols-12">
            <LazyChart
              title="Activity"
              subtitle={`${rangeLabel} · recorded time against planned`}
              className="lg:col-span-8"
            >
              <TrendChart
                title="Activity"
                subtitle={`${rangeLabel} · recorded time against planned`}
                className="lg:col-span-8"
                data={activityRows(overview.data.daily)}
                series={[
                  { key: 'actual', label: 'Recorded', unit: 'minutes' },
                  { key: 'planned', label: 'Planned', unit: 'minutes', colorIndex: 1 },
                ]}
              />
            </LazyChart>

            <LazyChart title="Where the time went" subtitle={rangeLabel} className="lg:col-span-4">
              <TimeDistributionChart
                title="Where the time went"
                subtitle={rangeLabel}
                className="lg:col-span-4"
                buckets={time.data?.by_project ?? []}
                unassignedMinutes={time.data?.unassigned_minutes ?? 0}
                totalMinutes={time.data?.total_minutes ?? 0}
                reasonIfUnavailable={time.data?.reason_if_unavailable ?? null}
                isLoading={time.isPending}
                error={
                  time.error && time.data === undefined ? (
                    <ErrorState
                      error={toApiError(time.error)}
                      title="Where the time went could not load"
                      onRetry={() => void time.refetch()}
                      compact
                      className="w-full"
                    />
                  ) : undefined
                }
              />
            </LazyChart>
          </div>

          <div className="grid gap-4 lg:grid-cols-12">
            <LazyChart
              title="Tasks completed per day"
              subtitle={rangeLabel}
              className="lg:col-span-8"
            >
              <AnalyticsBarChart
                title="Tasks completed per day"
                subtitle={rangeLabel}
                className="lg:col-span-8"
                data={overview.data.daily.map((row) => ({
                  label: formatMetricDate(row.metric_date),
                  count: row.tasks_completed,
                }))}
                series={[{ key: 'count', label: 'Tasks completed' }]}
                isEmpty={overview.data.daily.length === 0}
                emptyMetric="overview"
              />
            </LazyChart>

            <UpcomingDeadlines query={deadlines} className="lg:col-span-4" />
          </div>

          <div className="grid gap-4 lg:grid-cols-12">
            <ProjectPerformance query={projects} rangeLabel={rangeLabel} className="lg:col-span-8" />
            <RecentActivity query={activity} className="lg:col-span-4" />
          </div>
        </>
      )}

      <RiskSignals />

      <HealthCard
        data={health.data}
        error={healthError}
        isPending={health.isPending}
        updatedAt={health.dataUpdatedAt}
        onRetry={() => void health.refetch()}
        isFetching={health.isFetching}
      />
    </div>
  )
}

/* ------------------------------------------------------------------ plumbing */

type PanelQuery<T> = {
  data: T | undefined
  error: unknown
  isPending: boolean
  refetch: () => unknown
}

/**
 * The three states the panels below share: loading, failed, loaded.
 *
 * `isPending` alone cannot tell "nothing to show" from "the read failed". Once a
 * query errors its status is `error` rather than `pending` and its `data` is
 * `undefined` — the exact shape an empty response has — so without this branch a
 * 500 fell through to the panel's empty state. That copy claims the window was
 * read and found nothing, which is precisely the thing a failed read cannot
 * tell a reader, and it is a claim someone could act on: "nothing is due in
 * this window" invites skipping the work.
 *
 * A failed *refetch* that still has the last good rows keeps those rows, the way
 * the health card does. Stale rows are older truth; an error drawn over them
 * would hide data the reader already had.
 */
function PanelGate<T>({
  query,
  title,
  pending,
  children,
}: {
  query: PanelQuery<T>
  title: string
  pending: ReactNode
  children: () => ReactNode
}) {
  if (query.isPending) return <>{pending}</>
  if (query.error !== null && query.error !== undefined && query.data === undefined) {
    return (
      <ErrorState
        error={toApiError(query.error)}
        title={`${title} could not load`}
        onRetry={() => void query.refetch()}
        compact
      />
    )
  }
  return <>{children()}</>
}

/* ------------------------------------------------------------------ headline */

/**
 * One row, five figures, ordered by how much they change a decision: the score,
 * the work, the hours it took, the deadlines it met, and what is already
 * competing for the same hours.
 *
 * The score card is twice the width of the rest because it is the one figure
 * with an explanation attached rather than a single number — a reader who wants
 * to know how it was computed can see it here without opening a second screen.
 */
function HeadlineRow({
  totals,
  overview,
}: {
  totals: readonly ComparisonTotal[]
  overview: NonNullable<ReturnType<typeof useOverview>['data']>
}) {
  const completed = total(totals, 'tasks_completed')
  const recorded = total(totals, 'actual_minutes')
  const workload = overview.workload
  const productivity = overview.productivity

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
      <Card className="min-w-0 xl:col-span-2">
        <CardHeader className="flex-row items-start justify-between space-y-0 pb-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
            {productivity.label}
          </p>
          <Sparkles className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        </CardHeader>
        <CardContent className="space-y-1.5">
          {productivity.available && productivity.score !== null ? (
            <>
              <p className="text-4xl font-semibold tabular-nums tracking-tight text-foreground">
                {formatScore(productivity.score)}
                <span className="ml-1 text-lg font-normal text-muted-foreground">/ 100</span>
              </p>
              {/* The explanation is on the card rather than behind a tooltip: a
                  score whose arithmetic is hidden is a number nobody can act on. */}
              <p className="text-xs leading-relaxed text-muted-foreground">
                {productivity.components.length > 0
                  ? `${productivity.components
                      .map((part) => `${part.name} ${formatNumber(part.points, 0)}/${formatNumber(part.max_points, 0)}`)
                      .join(' · ')}`
                  : productivity.formula}
              </p>
              <p className="text-[11px] leading-relaxed text-muted-foreground/80">
                {productivity.disclaimer}
              </p>
            </>
          ) : (
            <>
              <p className="text-sm font-medium leading-snug text-foreground">
                {productivity.reason_if_unavailable ?? 'Not enough activity yet'}
              </p>
              <p className="text-xs text-muted-foreground">Not measurable for this window.</p>
            </>
          )}
        </CardContent>
      </Card>

      <MetricCard
        label="Tasks completed"
        size="lg"
        icon={CheckCircle2}
        value={formatNumber(completed?.current ?? null)}
        comparison={completed ?? null}
        comparisonUnit="tasks"
      />
      <MetricCard
        label="Work time"
        size="lg"
        icon={Timer}
        value={formatMinutes(recorded?.current ?? null)}
        comparison={recorded ?? null}
        formatChange={(value) => formatMinutes(value)}
      />
      <MetricCard
        label="Deadline adherence"
        size="lg"
        icon={Target}
        value={formatPercent(overview.deadlines.adherence_rate)}
        unavailableReason={
          overview.deadlines.available ? null : overview.deadlines.reason_if_unavailable
        }
        hint={
          overview.deadlines.available
            ? `${formatNumber(overview.deadlines.on_time)} on time · ${formatNumber(overview.deadlines.late)} late`
            : undefined
        }
      />
      <MetricCard
        label="Current workload"
        size="lg"
        icon={ListTodo}
        value={workload ? formatNumber(workload.open_tasks) : NO_VALUE}
        unavailableReason={workload && !workload.available ? workload.reason_if_unavailable : null}
        hint={
          workload
            ? `${formatNumber(workload.high_priority_open)} high priority · ${
                workload.workload_ratio === null
                  ? 'no availability declared'
                  : `${formatPercent(workload.workload_ratio)} of declared time`
              }`
            : undefined
        }
      />
    </div>
  )
}

function HeadlineSkeleton() {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6" aria-busy="true">
      {Array.from({ length: 5 }, (_, index) => (
        <Card key={index} className={cn('min-w-0', index === 0 && 'xl:col-span-2')}>
          <CardContent className="space-y-2 p-6">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-8 w-20" />
            <Skeleton className="h-3 w-32" />
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

function activityRows(daily: readonly DailyMetricRead[]): ChartRow[] {
  return daily.map((row) => ({
    label: formatMetricDate(row.metric_date),
    actual: row.actual_minutes,
    planned: row.planned_minutes,
  }))
}

function total(totals: readonly ComparisonTotal[], label: string): ComparisonTotal | undefined {
  return totals.find((entry) => entry.label === label)
}

function latestUpdatedAt(daily: readonly DailyMetricRead[]): string | null {
  let latest: string | null = null
  for (const row of daily) {
    if (row.updated_at && (latest === null || row.updated_at > latest)) latest = row.updated_at
  }
  return latest
}

/* ------------------------------------------------------------------- panels */

function UpcomingDeadlines({
  query,
  className,
}: {
  query: ReturnType<typeof useTasks>
  className?: string
}) {
  const open = (query.data?.items ?? []).filter(
    (task) => task.status !== 'completed' && task.status !== 'cancelled',
  )

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="pb-3">
        <CardTitle>Upcoming deadlines</CardTitle>
        <CardDescription>
          Open work with a due date inside the window, soonest first. Past-due tasks are marked.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <PanelGate
          query={query}
          title="Upcoming deadlines"
          pending={
            <div className="space-y-2">
              {Array.from({ length: 4 }, (_, index) => (
                <Skeleton key={index} className="h-5 w-full" />
              ))}
            </div>
          }
        >
          {() =>
            open.length === 0 ? (
              <EmptyState
                icon={CalendarClock}
                title="Nothing due in this window"
                description="No open task has a due date inside the selected range. Widen the window, or give a task a due date to see it here."
                compact
              />
            ) : (
              <ul className="divide-y divide-border">
                {open.map((task) => (
                  <li key={task.id} className="flex items-start justify-between gap-3 py-2 first:pt-0 last:pb-0">
                    <span className="min-w-0">
                      <span className="block truncate text-sm text-foreground">{task.title}</span>
                      <span className="text-xs text-muted-foreground">
                        {task.due_date ? formatShortDate(task.due_date) : 'no due date'}
                      </span>
                    </span>
                    {task.is_overdue && (
                      <Badge variant="destructive" className="shrink-0">
                        Overdue
                      </Badge>
                    )}
                  </li>
                ))}
              </ul>
            )
          }
        </PanelGate>
      </CardContent>
    </Card>
  )
}

function ProjectPerformance({
  query,
  rangeLabel,
  className,
}: {
  query: ReturnType<typeof useProjectAnalytics>
  rangeLabel: string
  className?: string
}) {
  const rows = query.data ?? []

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="pb-3">
        <CardTitle>Project performance</CardTitle>
        <CardDescription>
          Completion over the window, per project. A project with no tasks has no rate, and draws
          no bar.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <PanelGate
          query={query}
          title="Project performance"
          pending={
            <div className="space-y-3">
              {Array.from({ length: 3 }, (_, index) => (
                <div key={index} className="space-y-1.5">
                  <Skeleton className="h-3 w-40" />
                  <Skeleton className="h-2 w-full" />
                </div>
              ))}
            </div>
          }
        >
          {() =>
            rows.length === 0 ? (
              <EmptyAnalytics metric="projects" />
            ) : (
              <>
                <ul className="space-y-3">
                  {rows.slice(0, 6).map((project) => (
                    <li key={project.project_id} className="space-y-1.5">
                      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
                        <span className="min-w-0 truncate text-sm font-medium text-foreground">
                          {project.name}
                        </span>
                        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                          {formatNumber(project.completed_tasks)}/{formatNumber(project.total_tasks)} ·{' '}
                          {formatMinutes(project.total_work_minutes)} · {formatPercent(project.completion_rate)}
                        </span>
                      </div>
                      {project.completion_rate === null ? (
                        <p className="text-xs text-muted-foreground">
                          {project.reason_if_unavailable ?? 'No tasks in this window, so no rate.'}
                        </p>
                      ) : (
                        <Progress
                          value={project.completion_rate}
                          aria-label={`${project.name} completion rate`}
                        />
                      )}
                    </li>
                  ))}
                </ul>
                <p className="text-[11px] text-muted-foreground">
                  {rangeLabel} · showing the first {Math.min(rows.length, 6)} of {rows.length} projects.
                </p>
              </>
            )
          }
        </PanelGate>
      </CardContent>
    </Card>
  )
}

/**
 * Recent activity, from the work feed rather than from the analytics engine.
 *
 * The Phase 1 page filled this card with illustrative entries, which is exactly
 * the invented data the brief forbids: an entry that reads "Task completed —
 * Close out the release checklist" is a claim about work that may not exist. An
 * empty feed now renders an empty state instead.
 */
function RecentActivity({ query, className }: { query: ReturnType<typeof useActivity>; className?: string }) {
  const events = query.data?.items ?? []

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="pb-3">
        <CardTitle>Recent activity</CardTitle>
        <CardDescription>Recorded events from your own workspace, newest first.</CardDescription>
      </CardHeader>
      <CardContent>
        <PanelGate
          query={query}
          title="Recent activity"
          pending={
            <div className="space-y-2">
              {Array.from({ length: 4 }, (_, index) => (
                <Skeleton key={index} className="h-5 w-full" />
              ))}
            </div>
          }
        >
          {() =>
            events.length === 0 ? (
              <EmptyState
                icon={Activity}
                title="No activity recorded yet"
                description="Events appear here as you create projects, complete tasks and edit work. There is nothing recorded for this account."
                compact
              />
            ) : (
              <ul className="divide-y divide-border">
                {events.map((event) => {
                  const meta = WORK_EVENT_META[event.event_type]
                  return (
                    <li key={event.id} className="flex items-start gap-3 py-2 first:pt-0 last:pb-0">
                      <span
                        aria-hidden="true"
                        className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md border border-border bg-muted text-muted-foreground"
                      >
                        <meta.icon className="size-3" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm text-foreground">{meta.label}</span>
                        <span className="block truncate text-xs text-muted-foreground">
                          {meta.description}
                        </span>
                      </span>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {Number.isNaN(Date.parse(event.created_at))
                          ? null
                          : formatRelative(Date.parse(event.created_at))}
                      </span>
                    </li>
                  )
                })}
              </ul>
            )
          }
        </PanelGate>
      </CardContent>
    </Card>
  )
}

/* ------------------------------------------------------------------- risks */

/** How many findings the strip shows. Three is the most that can be read as a
 *  list; the rest of the answer belongs on the Risk Center, which is one click
 *  away and can show all of them. */
const RISK_STRIP_LIMIT = 3

/**
 * The detection engine, in one line.
 *
 * **The brief asks the dashboard not to be overwhelmed, and that is the whole
 * design constraint.** There is no second grid of tiles and no chart: a single
 * sentence saying how many items need attention, at most three findings beneath
 * it, and a link to the Risk Center for the rest. A dashboard that repeats the
 * whole Risk Center above the fold would be louder than the page it sits on, and
 * the panels below would be the thing being pushed down.
 *
 * **The count comes from the server's own `needs_attention` bit** rather than
 * from a threshold recomputed here, because the Risk Center's header reads the
 * same field and two implementations of "what counts as urgent" is how a widget
 * and a page start disagree. Medium and low are counted but do not raise it:
 * a widget that alarms over an amber band teaches people to ignore it.
 *
 * **Nothing is rendered when nothing was found.** The strip states that no
 * significant risk was detected, which is a result — the engine ran, read six
 * detectors and found no condition worth reporting — and it does not do it with
 * four zeroes, which read as a measurement.
 *
 * **The heading sits at `h3`.** The panels above own the `h2`s of this page, and
 * this strip is subordinate to them: it is the answer to one of the questions
 * they raise, not a section of its own. Promoting it would present three rows of
 * text as a peer of the activity chart.
 *
 * **A failed read says so and stops.** There is no retry control here, because
 * this page already carries the header's Refresh and a second one would compete
 * with the health card's own recovery; and no error card either, which on a page
 * this size would be the loudest thing on screen for a secondary widget. One
 * muted sentence, and the Risk Center link beside it, is the honest report.
 */
function RiskSignals() {
  const summary = useRiskSummary()
  // Ordered severity-descending by the server, so the first three rows are the
  // three most severe without this component ranking anything.
  const top = useRisks({ limit: RISK_STRIP_LIMIT, status: 'active' })

  const rows = top.data?.items ?? []
  const counts = summary.data

  return (
    <Card className="min-w-0">
      <CardHeader className="flex-row items-start justify-between space-y-0 pb-3">
        <div className="min-w-0 space-y-1">
          <CardTitle level="h3">Risk signals</CardTitle>
          <CardDescription>
            Conditions the detection engine has found in your recorded work.
          </CardDescription>
        </div>
        <Button type="button" variant="ghost" size="sm" asChild className="shrink-0">
          <Link to="/risks">
            Open Risk Center
            <ArrowUpRight aria-hidden="true" />
          </Link>
        </Button>
      </CardHeader>

      <CardContent className="space-y-3">
        {summary.isPending && !counts ? (
          <div role="status" className="space-y-2">
            <span className="sr-only">Loading the risk summary</span>
            <Skeleton className="h-4 w-56" />
            <Skeleton className="h-3 w-72" />
          </div>
        ) : counts === undefined ? (
          <p className="text-sm leading-relaxed text-muted-foreground">
            The risk summary could not be loaded. The Risk Center will say why.
          </p>
        ) : (
          <>
            <p className="text-sm leading-relaxed text-foreground">
              {attentionSentence(counts)}
            </p>
            {counts.total > 0 && <BandTally counts={counts} />}

            {/* Nothing is drawn for a band the page found no rows in. An empty
                list under a count sentence reads as a broken read rather than as
                an answer, and the sentence above already said what there is. */}
            {top.isPending && !top.data ? (
              <div role="status" className="space-y-2">
                <span className="sr-only">Loading the most severe findings</span>
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-4/5" />
              </div>
            ) : rows.length > 0 ? (
              <ul className="space-y-1.5">
                {rows.map((risk) => (
                  <li key={risk.id} className="flex items-center gap-2">
                    <SeverityBadge severity={risk.severity} size="sm" />
                    {/* Each row opens the Risk Center already narrowed to its own
                        band, so the click answers the question the row raised. */}
                    <Link
                      to={`/risks?severity=${risk.severity}`}
                      className="min-w-0 truncate text-sm text-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      {risk.title}
                    </Link>
                  </li>
                ))}
              </ul>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  )
}

/**
 * The one sentence the strip leads with.
 *
 * Three cases, and they are three different claims rather than one sentence with
 * a number swapped in: something needs an answer, something is recorded but
 * nothing is severe, or the engine ran and found nothing. The middle case says
 * so in plain words rather than printing "0 critical · 0 high", which would read
 * as a measurement taken rather than a pass completed.
 */
function attentionSentence(counts: RiskSummaryRead): string {
  if (counts.needs_attention) {
    const urgent = counts.critical + counts.high
    return `${urgent} item${urgent === 1 ? '' : 's'} need${urgent === 1 ? 's' : ''} attention.`
  }
  if (counts.total === 0) return 'No significant risk detected yet.'
  return 'Nothing is high or critical at the moment.'
}

/**
 * The live band counts, with the empty bands left out.
 *
 * "0 medium · 0 low" is noise on a summary line: an absent count is not a
 * finding, and printing four numbers to convey two is the wall-of-zeroes habit
 * this surface is written against. Each count is written as a word beside its
 * band, never as a tinted chip, for the reason the badges exist.
 */
function BandTally({ counts }: { counts: RiskSummaryRead }) {
  const bands = ([
    ['Critical', counts.critical],
    ['High', counts.high],
    ['Medium', counts.medium],
    ['Low', counts.low],
  ] as const).filter(([, count]) => count > 0)

  if (bands.length === 0) return null

  return (
    <p className="text-xs text-muted-foreground">
      Live findings by band:{' '}
      {bands.map(([label, count]) => `${count} ${label.toLowerCase()}`).join(' · ')}.
    </p>
  )
}

/* -------------------------------------------------------------------- health */

function HealthRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1.5">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="truncate font-mono text-xs text-foreground">{value}</dd>
    </div>
  )
}

/**
 * Backend health, kept at the foot of the page.
 *
 * It is real data from `GET /api/v1/health` rather than a placeholder card, but
 * it is not an intelligence figure, so it sits below the analytics panels
 * instead of competing with the productivity score for the first screen.
 */
function HealthCard({
  data,
  error,
  isPending,
  updatedAt,
  onRetry,
  isFetching,
}: {
  data: ReturnType<typeof useHealth>['data']
  error: ReturnType<typeof toApiError> | null
  isPending: boolean
  updatedAt: number
  onRetry: () => void
  isFetching: boolean
}) {
  const dbConnected = data?.database.status === 'connected'
  const degraded = data?.status === 'degraded'

  return (
    <Card className="min-w-0">
      <CardHeader className="flex-row items-start justify-between space-y-0 pb-3">
        <div className="min-w-0 space-y-1">
          <CardTitle>Backend health</CardTitle>
          <CardDescription>
            Live from <span className="font-mono">GET /api/v1/health</span>
          </CardDescription>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {data && (
            <Badge variant={degraded ? 'warning' : 'success'} className="uppercase tracking-[0.08em]">
              <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
              {data.status}
            </Badge>
          )}
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Refresh health"
            onClick={onRetry}
            disabled={isFetching}
          >
            {isFetching ? <Spinner size="sm" /> : <RefreshCw aria-hidden="true" />}
          </Button>
        </div>
      </CardHeader>

      <CardContent>
        {isPending && (
          <div className="grid gap-x-8 sm:grid-cols-2 lg:grid-cols-4">
            {Array.from({ length: 4 }, (_, index) => (
              <Skeleton key={index} className="h-4 w-full" />
            ))}
          </div>
        )}

        {!isPending && error && !data && <ErrorState error={error} onRetry={onRetry} compact />}

        {data && (
          <>
            <dl className="grid gap-x-8 sm:grid-cols-2 lg:grid-cols-4">
              <HealthRow label="Application" value={data.app} />
              <HealthRow label="Version" value={data.version} />
              <HealthRow label="Environment" value={data.environment} />
              <HealthRow label="Database" value={dbConnected ? 'connected' : 'unavailable'} />
              <HealthRow label="Round trip" value={formatLatency(data.database.latency_ms)} />
              <HealthRow label="Uptime" value={formatUptime(data.uptime_seconds)} />
              <HealthRow label="Reported at" value={formatTimestamp(data.timestamp)} />
              <HealthRow label="Checked" value={formatRelative(updatedAt)} />
            </dl>
            {error && (
              <p className="mt-3 text-xs text-muted-foreground">
                The most recent refresh failed; showing the last successful reading.
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}
