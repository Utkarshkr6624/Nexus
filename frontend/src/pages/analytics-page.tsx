import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Activity,
  BarChart3,
  BrainCircuit,
  CalendarClock,
  CheckCircle2,
  FolderKanban,
  Gauge,
  GraduationCap,
  ListTodo,
  Sparkles,
  Target,
  Timer,
} from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { PageHeader } from '@/components/feedback/page-header'
import { LoadingState } from '@/components/feedback/loading-state'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Separator } from '@/components/ui/separator'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  AnalyticsBarChart,
  DateRangePicker,
  EmptyAnalytics,
  Heatmap,
  MetricCard,
  ScoreCard,
  StalenessBanner,
  TimeDistributionChart,
  TrendChart,
} from '@/features/analytics/components'
import type { ChartRow } from '@/features/analytics/components'
import {
  useAnalyticsStaleness,
  useAnalyticsWindow,
  useConsistency,
  useDeadlines,
  useDailySeries,
  useEstimation,
  useFocus,
  useKnowledgeAnalytics,
  useLearningAnalytics,
  useOverview,
  useProductivity,
  useProjectAnalytics,
  useRebuildAnalytics,
  useTaskAnalytics,
  useTimeDistribution,
  useTrends,
} from '@/features/analytics/hooks'
import {
  NO_VALUE,
  formatDelta,
  formatMetricDate,
  formatMinutes,
  formatNumber,
  formatPercent,
  formatScore,
  formatShortDate,
  totalLabel,
} from '@/features/analytics/format'
import { toApiError } from '@/services/errors'
import { formatRangeLabel } from '@/types/analytics'
import type {
  AnalyticsListParams,
  ComparisonTotal,
  DailyMetricRead,
  Granularity,
  OverviewRead,
  TrendPoint,
} from '@/types/analytics'

/**
 * The analytics surface.
 *
 * **Every number on this page is a response.** There is no fallback data, no
 * sample series and no computed-on-the-client substitute: a figure the backend
 * declined to compute renders as the backend's own `reason_if_unavailable`,
 * word for word. The only numbers this file produces are differences the API
 * already sends (`absolute_change`, `percent_change`) and date arithmetic for
 * the axis.
 *
 * **The tab and the window both live in the URL.** `?range=30d&tab=projects` is
 * a shareable view rather than a transient state, and the back button walks
 * through tabs and windows instead of out of the page.
 *
 * **A tab fetches only what it reads.** The single-metric routes are gated on
 * their tab being open, so opening the page costs four requests rather than
 * eleven; `/overview`'s copies are preferred wherever it carries one, because
 * two requests over one window can disagree by a task created in between.
 */
const TABS = [
  { value: 'overview', label: 'Overview', icon: Gauge },
  { value: 'productivity', label: 'Productivity', icon: Sparkles },
  { value: 'time', label: 'Time', icon: Timer },
  { value: 'projects', label: 'Projects', icon: FolderKanban },
  { value: 'tasks', label: 'Tasks', icon: ListTodo },
  { value: 'deadlines', label: 'Deadlines', icon: Target },
  { value: 'learning', label: 'Learning', icon: GraduationCap },
  { value: 'knowledge', label: 'Knowledge', icon: BrainCircuit },
] as const

type TabValue = (typeof TABS)[number]['value']

const DEFAULT_TAB: TabValue = 'overview'

function isTab(value: string | null): value is TabValue {
  return value !== null && TABS.some((tab) => tab.value === value)
}

export default function AnalyticsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const window = useAnalyticsWindow()

  const tabParam = searchParams.get('tab')
  const tab: TabValue = isTab(tabParam) ? tabParam : DEFAULT_TAB
  const is = (value: TabValue): boolean => tab === value

  const setTab = useCallback(
    (next: string) => {
      const params = new URLSearchParams(searchParams)
      if (next === DEFAULT_TAB) params.delete('tab')
      else params.set('tab', next)
      setSearchParams(params)
    },
    [searchParams, setSearchParams],
  )

  const rangeLabel = formatRangeLabel(window.range.start_date, window.range.end_date)
  const params: AnalyticsListParams = useMemo(
    () => ({
      start_date: window.range.start_date,
      end_date: window.range.end_date,
      granularity: window.granularity,
    }),
    [window.range.start_date, window.range.end_date, window.granularity],
  )

  const overview = useOverview(params, { enabled: is('overview') })
  const completedTrend = useTrends({ ...params, metric: 'tasks_completed' }, { enabled: is('overview') })
  const productivity = useProductivity(params, { enabled: is('productivity') })
  const consistency = useConsistency(params, { enabled: is('productivity') })
  const focus = useFocus(params, { enabled: is('productivity') })
  const estimation = useEstimation(params, { enabled: is('productivity') })
  const time = useTimeDistribution(params, { enabled: is('time') })
  const series = useDailySeries(params, { enabled: is('time') })
  const projects = useProjectAnalytics(params, { enabled: is('projects') })
  const taskAnalytics = useTaskAnalytics(params, { enabled: is('tasks') || is('deadlines') })
  const deadlines = useDeadlines(params, { enabled: is('deadlines') })
  const learning = useLearningAnalytics(params, { enabled: is('learning') })
  const knowledge = useKnowledgeAnalytics(params, { enabled: is('knowledge') })

  const rebuild = useRebuildAnalytics()
  const staleness = useAnalyticsStaleness(window.range, overview.data)
  const updatedAt = latestUpdatedAt(overview.data?.daily ?? [])

  return (
    <div className="app-container space-y-6 py-6 lg:py-8">
      <PageHeader
        title="Analytics"
        eyebrow={
          <>
            <CalendarClock className="size-3.5" aria-hidden="true" />
            {rangeLabel}
          </>
        }
        badges={
          overview.data ? (
            <Badge variant={staleness.status === 'fresh' ? 'success' : 'warning'}>
              {staleness.status === 'fresh'
                ? 'Up to date'
                : staleness.status === 'partial'
                  ? 'Partly calculated'
                  : 'Calculating'}
            </Badge>
          ) : null
        }
        description="Every figure is computed from your own recorded activity. Where a metric cannot be measured, it says so instead of reporting zero."
      />

      <div className="space-y-3">
        <DateRangePicker
          preset={window.preset}
          start={window.range.start_date}
          end={window.range.end_date}
          granularity={window.granularity}
          onPresetChange={window.setPreset}
          onCustomChange={window.setCustom}
          onGranularityChange={window.setGranularity}
        />

        {overview.data && (
          <StalenessBanner
            {...staleness}
            updatedAt={updatedAt}
            rowsWritten={rebuild.data?.rows_written ?? null}
            isRecalculating={rebuild.isPending}
            onRecalculate={() => rebuild.mutate(params)}
          />
        )}
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="max-w-full overflow-x-auto">
          {TABS.map((entry) => (
            <TabsTrigger
              key={entry.value}
              value={entry.value}
              // The trigger's own active style paints the word in `--primary`,
              // which measures 4.26:1 on the light canvas — under the 4.5:1 that
              // WCAG AA asks of normal text, though it clears the floor in dark.
              // The open tab is still marked by its 2px underline, by
              // `aria-selected` and by focus; the word itself takes the
              // foreground colour so it reads in both themes.
              className={entry.value === tab ? 'text-foreground' : undefined}
            >
              {entry.label}
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="overview">
          {is('overview') && (
            <OverviewTab
              query={overview}
              completedTrend={completedTrend}
              rangeLabel={rangeLabel}
              start={window.range.start_date}
              end={window.range.end_date}
              granularity={window.granularity}
            />
          )}
        </TabsContent>

        <TabsContent value="productivity">
          {is('productivity') && (
            <ProductivityTab
              productivity={productivity}
              consistency={consistency}
              focus={focus}
              estimation={estimation}
              rangeLabel={rangeLabel}
            />
          )}
        </TabsContent>

        <TabsContent value="time">
          {is('time') && <TimeTab time={time} series={series} rangeLabel={rangeLabel} />}
        </TabsContent>

        <TabsContent value="projects">
          {is('projects') && <ProjectsTab query={projects} rangeLabel={rangeLabel} />}
        </TabsContent>

        <TabsContent value="tasks">
          {is('tasks') && <TasksTab query={taskAnalytics} rangeLabel={rangeLabel} />}
        </TabsContent>

        <TabsContent value="deadlines">
          {is('deadlines') && (
            <DeadlinesTab
              deadlines={deadlines}
              tasks={taskAnalytics}
              rangeLabel={rangeLabel}
            />
          )}
        </TabsContent>

        <TabsContent value="learning">
          {is('learning') && <LearningTab query={learning} rangeLabel={rangeLabel} />}
        </TabsContent>

        <TabsContent value="knowledge">
          {is('knowledge') && <KnowledgeTab query={knowledge} rangeLabel={rangeLabel} />}
        </TabsContent>
      </Tabs>
    </div>
  )
}

/* ------------------------------------------------------------------ plumbing */

type QueryLike<T> = {
  data: T | undefined
  error: unknown
  isPending: boolean
  refetch: () => unknown
}

/**
 * The three states every tab shares: loading, failed, loaded.
 *
 * The failed case goes through `ErrorState` with a working retry rather than a
 * bare "Something went wrong", because a failed analytics read is otherwise
 * indistinguishable from a dashboard that is merely empty.
 */
function QueryGate<T>({
  query,
  label,
  onRetry,
  children,
}: {
  query: QueryLike<T>
  label: string
  onRetry: () => void
  children: (data: T) => React.ReactNode
}) {
  if (query.isPending) return <LoadingState label={label} />
  if (query.error) return <ErrorState error={toApiError(query.error)} onRetry={onRetry} />
  if (query.data === undefined) return <LoadingState label={label} compact />
  return <>{children(query.data)}</>
}

function total(totals: readonly ComparisonTotal[], label: string): ComparisonTotal | undefined {
  return totals.find((entry) => entry.label === label)
}

/** One row per day in the window, keyed for a chart. */
function dailyRows(daily: readonly DailyMetricRead[], key: keyof DailyMetricRead): ChartRow[] {
  return daily.map((row) => ({
    label: formatMetricDate(row.metric_date),
    value: Number(row[key] ?? 0),
  }))
}

function dailyPair(
  daily: readonly DailyMetricRead[],
  first: keyof DailyMetricRead,
  second: keyof DailyMetricRead,
  labels: [string, string],
): ChartRow[] {
  return daily.map((row) => ({
    label: formatMetricDate(row.metric_date),
    [labels[0]]: Number(row[first] ?? 0),
    [labels[1]]: Number(row[second] ?? 0),
  }))
}

function latestUpdatedAt(daily: readonly DailyMetricRead[]): string | null {
  let latest: string | null = null
  for (const row of daily) {
    if (row.updated_at && (latest === null || row.updated_at > latest)) latest = row.updated_at
  }
  return latest
}

/* ---------------------------------------------------------------- overview tab */

function OverviewTab({
  query,
  completedTrend,
  rangeLabel,
  start,
  end,
  granularity,
}: {
  query: QueryLike<OverviewRead>
  completedTrend: QueryLike<TrendPoint[]> & object
  rangeLabel: string
  start: string
  end: string
  granularity: Granularity
}) {
  return (
    <QueryGate
      query={query}
      label="Loading your window"
      onRetry={() => void query.refetch()}
    >
      {(overview) => (
        <div className="space-y-4">
          <HeadlineRow overview={overview} rangeLabel={rangeLabel} />

          <div className="grid gap-4 lg:grid-cols-2">
            <AnalyticsBarChart
              title="Tasks completed per day"
              subtitle={rangeLabel}
              data={dailyRows(overview.daily, 'tasks_completed')}
              series={[{ key: 'value', label: 'Tasks completed' }]}
              isEmpty={overview.daily.length === 0}
              emptyMetric="overview"
            />
            {/* Gated like every other panel here. A 5xx or a timeout turns this
                read's data into `[]`, and the chart would then say the window
                held no completions — inverting a failed read into a claim
                about the work. */}
            <QueryGate
              query={completedTrend}
              label="Loading the comparison"
              onRetry={() => void completedTrend.refetch()}
            >
              {(points) => (
                <TrendChart
                  title="Tasks completed against the previous period"
                  subtitle={rangeLabel}
                  kind="line"
                  data={points.map((point) => ({
                    label: point.bucket ? formatMetricDate(point.bucket) : point.label,
                    completed: point.value,
                    previous: point.previous,
                  }))}
                  series={[
                    { key: 'completed', label: 'This period' },
                    { key: 'previous', label: 'Previous period', colorIndex: 4 },
                  ]}
                  emptyMetric="trend"
                />
              )}
            </QueryGate>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <TrendChart
              title="Recorded time"
              subtitle={`${rangeLabel} · actual minutes against planned`}
              data={dailyPair(
                overview.daily,
                'actual_minutes',
                'planned_minutes',
                ['actual', 'planned'],
              )}
              series={[
                { key: 'actual', label: 'Recorded', unit: 'minutes' },
                { key: 'planned', label: 'Planned', unit: 'minutes', colorIndex: 1 },
              ]}
              isEmpty={overview.daily.length === 0}
              emptyMetric="overview"
            />

            <Heatmap
              title="Active days"
              subtitle={rangeLabel}
              start={start}
              end={end}
              valueName="events"
              days={overview.daily.map((row) => ({
                date: row.metric_date,
                value:
                  row.tasks_completed +
                  row.work_sessions +
                  row.calendar_events +
                  row.knowledge_events,
              }))}
            />

            <div className="grid gap-4 sm:grid-cols-2">
              {overview.consistency ? (
                <ScoreCard score={overview.consistency} compact facts={consistencyFacts(overview.consistency)} />
              ) : (
                <EmptyPanel metric="consistency" title="Consistency" />
              )}
              {overview.focus ? (
                <ScoreCard score={overview.focus} compact facts={focusFacts(overview.focus)} />
              ) : (
                <EmptyPanel metric="focus" title="Focus" />
              )}
            </div>
          </div>

          <TotalsTable totals={overview.totals} rangeLabel={rangeLabel} granularity={granularity} />
        </div>
      )}
    </QueryGate>
  )
}

/**
 * The five figures the page leads with, in reading order: the score, then the
 * work, then the pressure on it.
 *
 * Each card draws from the one `/overview` response, so the headline row cannot
 * disagree with the panels below it — the failure mode of a dashboard that
 * assembles itself from five endpoints over the same window.
 */
function HeadlineRow({ overview, rangeLabel }: { overview: OverviewRead; rangeLabel: string }) {
  const completed = total(overview.totals, 'tasks_completed')
  const recorded = total(overview.totals, 'actual_minutes')
  const workload = overview.workload

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
      <MetricCard
        label="Productivity score"
        size="lg"
        icon={Sparkles}
        value={formatScore(overview.productivity.score)}
        unavailableReason={
          overview.productivity.available ? null : overview.productivity.reason_if_unavailable
        }
        explanation={`${overview.productivity.formula} ${overview.productivity.disclaimer}`}
      />
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
        unavailableReason={overview.deadlines.available ? null : overview.deadlines.reason_if_unavailable}
        hint={
          overview.deadlines.available
            ? `${formatNumber(overview.deadlines.on_time)} on time, ${formatNumber(overview.deadlines.late)} late`
            : undefined
        }
      />
      <MetricCard
        label="Current workload"
        size="lg"
        icon={ListTodo}
        value={workload ? formatNumber(workload.open_tasks) : NO_VALUE}
        unavailableReason={workload && !workload.available ? workload.reason_if_unavailable : null}
        hint={workloadHint(workload)}
      />
      <p className="sr-only">Figures cover {rangeLabel}.</p>
    </div>
  )
}

function workloadHint(workload: OverviewRead['workload']): string | undefined {
  if (!workload) return undefined
  const parts = [`${formatNumber(workload.high_priority_open)} high priority`]
  if (workload.overdue_open > 0) parts.push(`${formatNumber(workload.overdue_open)} overdue`)
  parts.push(
    workload.workload_ratio === null
      ? 'no availability declared'
      : `${formatPercent(workload.workload_ratio)} of declared time`,
  )
  return parts.join(' · ')
}

function consistencyFacts(read: OverviewRead['consistency']): Array<{ label: string; value: string }> {
  if (!read) return []
  return [
    { label: 'Active days', value: `${read.active_days} of ${read.window_days}` },
    { label: 'Longest streak', value: `${read.longest_streak}d` },
  ]
}

function focusFacts(read: OverviewRead['focus']): Array<{ label: string; value: string }> {
  if (!read) return []
  return [
    { label: 'Focused', value: formatMinutes(read.focused_minutes) },
    { label: 'Interruptions', value: formatNumber(read.interruptions) },
  ]
}

/** Every compared total, so nothing on the page is a summary of a summary. */
function TotalsTable({
  totals,
  rangeLabel,
  granularity,
}: {
  totals: readonly ComparisonTotal[]
  rangeLabel: string
  granularity: Granularity
}) {
  if (totals.length === 0) {
    return <EmptyAnalytics metric="overview" reason="No daily aggregates exist for this window." />
  }

  return (
    <Card className="min-w-0">
      <CardHeader className="pb-3">
        <CardTitle>All compared totals</CardTitle>
        <CardDescription>
          {rangeLabel}, bucketed by {granularity}, against the same-length period before it. Every
          column is a `daily_metrics` aggregate; the backend decides the order.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="divide-y divide-border">
          {totals.map((entry) => {
            const meta = totalLabel(entry.label)
            const value = meta.unit === 'minutes' ? formatMinutes(entry.current) : formatNumber(entry.current)
            const delta = formatDelta(entry.absolute_change, entry.percent_change, {
              // `format` already renders the unit ("45m"), and `formatDelta`
              // appends `unit` after it — passing both produced "up 45m minutes".
              // Only one of them carries the unit, so minutes rows pass the
              // formatter alone.
              higherIsBetter: meta.higherIsBetter,
              ...(meta.unit === 'minutes' ? { format: (n: number) => formatMinutes(n) } : {}),
            })
            return (
              <li key={entry.label} className="flex flex-wrap items-center justify-between gap-x-6 gap-y-1 py-2.5">
                <span className="min-w-0 text-sm text-foreground">{meta.label}</span>
                <span className="text-sm font-medium tabular-nums text-foreground">{value}</span>
                <span className="text-xs tabular-nums text-muted-foreground">
                  {delta.direction === 'unknown' ? delta.label : `${delta.arrow} ${delta.label}`}
                </span>
              </li>
            )
          })}
        </ul>
      </CardContent>
    </Card>
  )
}

/* ------------------------------------------------------------ productivity tab */

function ProductivityTab({
  productivity,
  consistency,
  focus,
  estimation,
  rangeLabel,
}: {
  productivity: QueryLike<Awaited<ReturnType<typeof useProductivity>>['data']> & object
  consistency: QueryLike<Awaited<ReturnType<typeof useConsistency>>['data']> & object
  focus: QueryLike<Awaited<ReturnType<typeof useFocus>>['data']> & object
  estimation: QueryLike<Awaited<ReturnType<typeof useEstimation>>['data']> & object
  rangeLabel: string
}) {
  return (
    <QueryGate
      query={productivity}
      label="Loading your score"
      onRetry={() => void productivity.refetch()}
    >
      {(score) => (
        <div className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <ScoreCard score={score} />
            <EstimationPanel query={estimation} rangeLabel={rangeLabel} />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <QueryGate
              query={consistency}
              label="Loading consistency"
              onRetry={() => void consistency.refetch()}
            >
              {(read) => (
                <ScoreCard
                  score={read}
                  facts={[
                    { label: 'Active days', value: `${read.active_days} of ${read.window_days}` },
                    { label: 'Current streak', value: `${read.current_streak}d` },
                    { label: 'Longest streak', value: `${read.longest_streak}d` },
                    { label: 'Work sessions', value: formatNumber(read.work_sessions) },
                  ]}
                />
              )}
            </QueryGate>

            <QueryGate query={focus} label="Loading focus" onRetry={() => void focus.refetch()}>
              {(read) => (
                <ScoreCard
                  score={read}
                  facts={[
                    {
                      label: 'Mean session',
                      value: read.avg_session_minutes === null ? '—' : formatMinutes(read.avg_session_minutes),
                    },
                    { label: 'Focused', value: formatMinutes(read.focused_minutes) },
                    { label: 'Interruptions', value: formatNumber(read.interruptions) },
                    { label: 'Reschedules', value: formatNumber(read.reschedules) },
                  ]}
                />
              )}
            </QueryGate>
          </div>
        </div>
      )}
    </QueryGate>
  )
}

/** How far the estimates landed from the recorded time, in the backend's terms. */
function EstimationPanel({
  query,
  rangeLabel,
}: {
  query: QueryLike<Awaited<ReturnType<typeof useEstimation>>['data']> & object
  rangeLabel: string
}) {
  return (
    <QueryGate query={query} label="Loading estimates" onRetry={() => void query.refetch()}>
      {(read) => {
        if (!read.available) {
          return (
            <Card className="min-w-0">
              <CardHeader className="pb-2">
                <CardTitle>Estimation accuracy</CardTitle>
                <CardDescription>{rangeLabel}</CardDescription>
              </CardHeader>
              <CardContent>
                <EmptyAnalytics metric="estimation" reason={read.reason_if_unavailable} />
              </CardContent>
            </Card>
          )
        }

        return (
          <Card className="min-w-0">
            <CardHeader className="pb-3">
              <CardTitle>Estimation accuracy</CardTitle>
              <CardDescription>
                {rangeLabel} · {formatNumber(read.sample_count)} task
                {read.sample_count === 1 ? '' : 's'} carrying both an estimate and tracked time.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <MetricCard
                  label="Mean error"
                  value={formatMinutes(read.absolute_error)}
                  hint="Estimated against recorded, in minutes"
                />
                <MetricCard
                  label="Bias"
                  value={read.bias === null ? '—' : `${formatMinutes(read.bias)}`}
                  hint="Negative means the estimates ran below the time taken."
                />
                <MetricCard
                  label="Under-estimated"
                  value={formatPercent(read.under_estimation_rate)}
                  hint="Share of compared tasks that ran over their estimate."
                />
                <MetricCard
                  label="Over-estimated"
                  value={formatPercent(read.over_estimation_rate)}
                  hint="Share of compared tasks that came in under their estimate."
                />
              </div>
            </CardContent>
          </Card>
        )
      }}
    </QueryGate>
  )
}

/* ------------------------------------------------------------------- time tab */

function TimeTab({
  time,
  series,
  rangeLabel,
}: {
  time: QueryLike<Awaited<ReturnType<typeof useTimeDistribution>>['data']> & object
  series: QueryLike<Awaited<ReturnType<typeof useDailySeries>>['data']> & object
  rangeLabel: string
}) {
  return (
    <QueryGate query={time} label="Loading your time" onRetry={() => void time.refetch()}>
      {(read) => (
        <div className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <TimeDistributionChart
              title="Where the time went"
              subtitle={`${rangeLabel} · by project`}
              buckets={read.by_project}
              unassignedMinutes={read.unassigned_minutes}
              totalMinutes={read.total_minutes}
              reasonIfUnavailable={read.reason_if_unavailable}
            />
            <TimeDistributionChart
              title="By task"
              subtitle={`${rangeLabel} · by task`}
              buckets={read.by_task}
              totalMinutes={read.total_minutes}
              reasonIfUnavailable={read.reason_if_unavailable}
              maxSlices={8}
            />
          </div>

          <QueryGate query={series} label="Loading the daily series" onRetry={() => void series.refetch()}>
            {(daily) => (
              <TrendChart
                title="Planned against recorded"
                subtitle={rangeLabel}
                kind="line"
                data={dailyPair(
                  daily,
                  'planned_minutes',
                  'actual_minutes',
                  ['planned', 'actual'],
                )}
                // Both series carry an explicit index. `colorIndex` falls back to
                // the series' *position*, so an unindexed "Recorded" in second
                // place would take the same token as the indexed "Planned" above
                // it — two green lines and no way to tell which is which.
                series={[
                  { key: 'planned', label: 'Planned', unit: 'minutes', colorIndex: 1 },
                  { key: 'actual', label: 'Recorded', unit: 'minutes', colorIndex: 0 },
                ]}
                rowHeading="Day"
                isEmpty={daily.length === 0}
                emptyMetric="time"
              />
            )}
          </QueryGate>
        </div>
      )}
    </QueryGate>
  )
}

/* --------------------------------------------------------------- projects tab */

function ProjectsTab({
  query,
  rangeLabel,
}: {
  query: QueryLike<Awaited<ReturnType<typeof useProjectAnalytics>>['data']> & object
  rangeLabel: string
}) {
  return (
    <QueryGate query={query} label="Loading projects" onRetry={() => void query.refetch()}>
      {(projects) => {
        if (projects.length === 0) {
          return <EmptyAnalytics metric="projects" />
        }

        return (
          <div className="space-y-4">
            <AnalyticsBarChart
              title="Completion by project"
              subtitle={rangeLabel}
              orientation="horizontal"
              colorByCategory
              data={projects.map((project) => ({
                label: project.name,
                rate: project.completion_rate,
              }))}
              series={[{ key: 'rate', label: 'Completion rate', unit: 'percent' }]}
              emptyMetric="projects"
            />

            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {projects.map((project) => (
                <Card key={project.project_id} className="min-w-0">
                  <CardHeader className="pb-3">
                    <div className="flex items-start justify-between gap-2">
                      <CardTitle level="h3" className="truncate">
                        {project.name}
                      </CardTitle>
                      <Badge variant="secondary" className="shrink-0 capitalize">
                        {project.status.replace(/_/g, ' ')}
                      </Badge>
                    </div>
                    <CardDescription>
                      {formatNumber(project.completed_tasks)} of {formatNumber(project.total_tasks)}{' '}
                      tasks · {formatMinutes(project.total_work_minutes)} recorded
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="space-y-1.5">
                      <div className="flex items-baseline justify-between text-xs">
                        <span className="text-muted-foreground">Completion</span>
                        <span className="font-medium tabular-nums text-foreground">
                          {formatPercent(project.completion_rate)}
                        </span>
                      </div>
                      {/* A null rate draws no bar: a 0% bar beside "no tasks"
                          would claim the project failed rather than that it has
                          none to fail. */}
                      {project.completion_rate === null ? (
                        <p className="text-xs text-muted-foreground">
                          {project.reason_if_unavailable ??
                            'No tasks in this project, so there is no completion rate.'}
                        </p>
                      ) : (
                        <Progress
                          value={project.completion_rate}
                          aria-label={`${project.name} completion rate`}
                        />
                      )}
                    </div>

                    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                      <Stat label="Overdue" value={formatNumber(project.overdue_tasks)} />
                      <Stat
                        label="Velocity"
                        value={
                          project.velocity_tasks_per_week === null
                            ? '—'
                            : `${formatNumber(project.velocity_tasks_per_week, 1)}/week`
                        }
                      />
                      <Stat label="Remaining" value={formatNumber(project.remaining_tasks)} />
                      <Stat label="Activity events" value={formatNumber(project.activity_events)} />
                    </dl>

                    {project.velocity?.definition && (
                      <p className="text-[11px] leading-relaxed text-muted-foreground">
                        {project.velocity.definition}
                      </p>
                    )}
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        )
      }}
    </QueryGate>
  )
}

/* ------------------------------------------------------------------ tasks tab */

function TasksTab({
  query,
  rangeLabel,
}: {
  query: QueryLike<Awaited<ReturnType<typeof useTaskAnalytics>>['data']> & object
  rangeLabel: string
}) {
  return (
    <QueryGate query={query} label="Loading tasks" onRetry={() => void query.refetch()}>
      {(read) => (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <MetricCard
              label="Completion rate"
              size="lg"
              icon={CheckCircle2}
              value={formatPercent(read.completion_rate)}
              unavailableReason={read.available ? null : read.reason_if_unavailable}
              hint={`${formatNumber(read.completed_tasks)} of ${formatNumber(read.total_tasks)} tasks`}
            />
            <MetricCard
              label="Open"
              size="lg"
              icon={ListTodo}
              value={formatNumber(read.open_tasks)}
              hint={`${formatNumber(read.blocked_tasks)} blocked · ${formatNumber(read.cancelled_tasks)} cancelled`}
            />
            <MetricCard
              label="Overdue"
              size="lg"
              icon={Target}
              value={formatNumber(read.overdue_tasks)}
              hint={formatPercent(read.overdue_rate)}
            />
            <MetricCard
              label="Mean time to complete"
              size="lg"
              icon={Timer}
              value={read.avg_completion_days === null ? '—' : `${formatNumber(read.avg_completion_days, 1)} days`}
              hint={read.avg_completion_days === null ? 'Nothing has been completed in this window.' : undefined}
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <AnalyticsBarChart
              title="Tasks by status"
              subtitle={rangeLabel}
              colorByCategory
              data={Object.entries(read.by_status).map(([key, count]) => ({
                label: key.replace(/_/g, ' '),
                count,
              }))}
              series={[{ key: 'count', label: 'Tasks' }]}
              emptyMetric="tasks"
            />
            <AnalyticsBarChart
              title="Tasks by priority"
              subtitle={rangeLabel}
              colorByCategory
              data={Object.entries(read.by_priority).map(([key, count]) => ({
                label: key,
                count,
              }))}
              series={[{ key: 'count', label: 'Tasks' }]}
              emptyMetric="tasks"
            />
          </div>

          <OverdueList query={query} />
        </div>
      )}
    </QueryGate>
  )
}

/* ------------------------------------------------------------- deadlines tab */

function DeadlinesTab({
  deadlines,
  tasks,
  rangeLabel,
}: {
  deadlines: QueryLike<Awaited<ReturnType<typeof useDeadlines>>['data']> & object
  tasks: QueryLike<Awaited<ReturnType<typeof useTaskAnalytics>>['data']> & object
  rangeLabel: string
}) {
  return (
    <QueryGate
      query={deadlines}
      label="Loading deadlines"
      onRetry={() => void deadlines.refetch()}
    >
      {(read) => (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <MetricCard
              label="Adherence"
              size="lg"
              icon={Target}
              value={formatPercent(read.adherence_rate)}
              unavailableReason={read.available ? null : read.reason_if_unavailable}
              hint={`${formatNumber(read.total_considered)} finished task${read.total_considered === 1 ? '' : 's'} considered`}
            />
            <MetricCard
              label="On time"
              size="lg"
              icon={CheckCircle2}
              value={formatNumber(read.on_time)}
            />
            <MetricCard
              label="Late"
              size="lg"
              icon={CalendarClock}
              value={formatNumber(read.late)}
            />
            <MetricCard
              label="Still overdue"
              size="lg"
              icon={Target}
              value={formatNumber(read.still_overdue)}
              hint={read.overdue_open > 0 ? `${formatNumber(read.overdue_open)} open past due` : undefined}
            />
          </div>

          <AnalyticsBarChart
            title="On time against late"
            subtitle={rangeLabel}
            data={[
              { label: 'On time', count: read.on_time },
              { label: 'Late', count: read.late },
              { label: 'Still overdue', count: read.still_overdue },
            ]}
            series={[{ key: 'count', label: 'Tasks' }]}
            colorByCategory
            emptyMetric="deadlines"
            emptyReason={read.available ? null : read.reason_if_unavailable}
          />

          <OverdueList query={tasks} />
        </div>
      )}
    </QueryGate>
  )
}

function OverdueList({ query }: { query: QueryLike<Awaited<ReturnType<typeof useTaskAnalytics>>['data']> & object }) {
  const read = query.data
  if (!read) return null
  if (read.top_overdue.length === 0) {
    return <EmptyAnalytics metric="deadlines" reason="Nothing in this window is past its due date." />
  }

  return (
    <Card className="min-w-0">
      <CardHeader className="pb-3">
        <CardTitle>Currently overdue</CardTitle>
        <CardDescription>
          A task with no due date appears here unfinished rather than late — its overdue count is
          null, not zero.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="divide-y divide-border">
          {read.top_overdue.map((task) => (
            <li key={task.task_id} className="flex items-center justify-between gap-3 py-2 first:pt-0 last:pb-0">
              <span className="min-w-0 truncate text-sm text-foreground">{task.title}</span>
              <span className="flex shrink-0 items-center gap-3 text-xs tabular-nums text-muted-foreground">
                {task.due_date && <span>{formatShortDate(task.due_date)}</span>}
                <span>{task.days_overdue === null ? 'no due date' : `${task.days_overdue}d late`}</span>
              </span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}

/* ---------------------------------------------------------------- learning tab */

function LearningTab({
  query,
  rangeLabel,
}: {
  query: QueryLike<Awaited<ReturnType<typeof useLearningAnalytics>>['data']> & object
  rangeLabel: string
}) {
  return (
    <QueryGate query={query} label="Learning activity" onRetry={() => void query.refetch()}>
      {(read) => (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <MetricCard
              label="Study events"
              size="lg"
              icon={GraduationCap}
              value={formatNumber(read.study_events)}
              unavailableReason={read.available ? null : read.reason_if_unavailable}
              hint="Calendar events typed study"
            />
            <MetricCard
              label="Study time"
              size="lg"
              icon={Timer}
              value={formatMinutes(read.study_minutes)}
            />
            <MetricCard
              label="Knowledge interactions"
              size="lg"
              icon={BrainCircuit}
              value={formatNumber(read.knowledge_interactions)}
            />
            <MetricCard
              label="Tasks linked to knowledge"
              size="lg"
              icon={ListTodo}
              value={read.knowledge_linked_tasks === null ? '—' : formatNumber(read.knowledge_linked_tasks)}
              hint={
                read.knowledge_linked_tasks === null
                  ? 'Not computable from the rows that exist — null, not zero.'
                  : undefined
              }
            />
          </div>

          <Card className="min-w-0">
            <CardHeader className="pb-2">
              <CardTitle>What these numbers mean</CardTitle>
              <CardDescription>Verbatim from the API, because the scope is the part that matters.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm leading-relaxed text-muted-foreground">
              <p>{read.definition}</p>
              <p>{read.basis}</p>
              <Separator />
              <p className="text-xs">
                {rangeLabel}. {formatNumber(read.notes_created)} notes created,{' '}
                {formatNumber(read.notes_updated)} updated, {formatNumber(read.projects_touched)}{' '}
                projects touched.
              </p>
            </CardContent>
          </Card>
        </div>
      )}
    </QueryGate>
  )
}

/* --------------------------------------------------------------- knowledge tab */

function KnowledgeTab({
  query,
  rangeLabel,
}: {
  query: QueryLike<Awaited<ReturnType<typeof useKnowledgeAnalytics>>['data']> & object
  rangeLabel: string
}) {
  return (
    <QueryGate query={query} label="Knowledge activity" onRetry={() => void query.refetch()}>
      {(read) => (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <MetricCard
              label="Notes written"
              size="lg"
              icon={BrainCircuit}
              value={formatNumber(read.notes_created)}
              unavailableReason={read.available ? null : read.reason_if_unavailable}
              hint={`${formatNumber(read.notes_updated)} updated`}
            />
            <MetricCard
              label="Concepts"
              size="lg"
              icon={Activity}
              value={formatNumber(read.concepts_created)}
            />
            <MetricCard
              label="Links created"
              size="lg"
              icon={BarChart3}
              value={formatNumber(read.links_created)}
            />
            <MetricCard
              label="Interactions"
              size="lg"
              icon={CheckCircle2}
              value={formatNumber(read.interactions)}
              hint="Writes only — no views are recorded."
            />
          </div>

          <div>
            <RankList
              title="Most used tags"
              subtitle={rangeLabel}
              rows={read.most_used_tags.map((tag) => ({ label: tag.label, count: tag.count }))}
              emptyReason="No tag has been used in this window."
            />
            {/* `most_active_concepts` and `top_tags` are declared on the response
                but the service never fills either, so a panel over them can only
                ever print its own empty copy — "No concept has been touched in
                this window" directly under a card counting the concepts that
                were created. That is a claim about the user's account the backend
                never made, so the panel is left out rather than made to lie. */}
          </div>
        </div>
      )}
    </QueryGate>
  )
}

/* -------------------------------------------------------------- small pieces */

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular-nums text-foreground">{value}</dd>
    </div>
  )
}

/** A labelled list with a bar per row, or its own reason when there is none. */
function RankList({
  title,
  subtitle,
  rows,
  emptyReason,
}: {
  title: string
  subtitle: string
  rows: Array<{ label: string; count: number }>
  emptyReason: string
}) {
  const max = rows.reduce((highest, row) => Math.max(highest, row.count), 0)

  return (
    <Card className="min-w-0">
      <CardHeader className="pb-3">
        <CardTitle>{title}</CardTitle>
        <CardDescription>{subtitle}</CardDescription>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <EmptyAnalytics metric="knowledge" reason={emptyReason} />
        ) : (
          <ul className="space-y-2.5">
            {rows.map((row) => (
              <li key={row.label} className="space-y-1.5">
                <div className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="min-w-0 truncate text-foreground">{row.label}</span>
                  <span className="shrink-0 tabular-nums text-muted-foreground">
                    {formatNumber(row.count)}
                  </span>
                </div>
                <Progress value={row.count} max={max || 1} aria-label={`${row.label}: ${row.count}`} />
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

/** A card-sized empty state, for a score `/overview` did not return at all. */
function EmptyPanel({ metric, title }: { metric: 'consistency' | 'focus'; title: string }) {
  return (
    <Card className="min-w-0">
      <CardHeader className="pb-2">
        <CardTitle level="h3">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <EmptyAnalytics metric={metric} reason="This response carried no score for the window." />
      </CardContent>
    </Card>
  )
}
