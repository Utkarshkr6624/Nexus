/**
 * TanStack Query bindings for the Phase 6 analytics surface.
 *
 * **`analyticsKeys` is the single owner of the query-key shape.** Every key
 * lives under the `['analytics']` root, and every read's key carries the
 * resolved window — `start_date`, `end_date`, `granularity` and `project_id` —
 * because the same endpoint asked about two windows is two different answers
 * and one of them must never be rendered for the other.
 *
 * **`rebuildAnalytics` invalidates the whole `['analytics']` tree.** A rebuild
 * rewrites `daily_metrics` for a window, which moves the overview's `daily`
 * series, the heatmap, every trend and the staleness banner at once. Picking
 * the keys it "should" affect is how a dashboard ships showing last week's
 * numbers next to this week's totals. The tree is small, so the aggregate is
 * the correct trade.
 *
 * **`placeholderData` is kept on the window-shaped reads.** Paging the range
 * picker blanks a whole dashboard for a frame otherwise, and a stale frame
 * beats an empty one — the staleness banner says how old the figures are, so
 * the interim render is not silent.
 *
 * **Retry policy is inherited.** `app/query-client.ts` refuses to retry a 4xx,
 * so a 422 for an inverted window surfaces on the first response.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query'
import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

import {
  exportCsvUrl as buildExportCsvUrl,
  fetchConsistency,
  fetchDeadlines,
  fetchEstimation,
  fetchExportManifest,
  fetchFocus,
  fetchKnowledge,
  fetchLearning,
  fetchOverview,
  fetchProductivity,
  fetchProjects,
  fetchSeries,
  fetchTasks,
  fetchTimeDistribution,
  fetchTrends,
  rebuildAnalytics,
} from '@/services/analytics'
import {
  DEFAULT_WINDOW_PRESET,
  isWindowPreset,
  resolveWindow,
  type AnalyticsCsvDataset,
  type AnalyticsListParams,
  type AnalyticsRange,
  type ConsistencyRead,
  type CsvExportManifestRead,
  type DailyMetricRead,
  type DeadlineAdherenceRead,
  type EstimationAccuracyRead,
  type FocusRead,
  type Granularity,
  type KnowledgeAnalyticsRead,
  type LearningAnalyticsRead,
  type OverviewRead,
  type ProductivityRead,
  type ProjectAnalyticsRead,
  type RebuildRead,
  type TaskAnalyticsRead,
  type TimeDistributionRead,
  type TrendMetric,
  type TrendPoint,
  type WindowPresetId,
} from '@/types/analytics'

type Enabled = { enabled?: boolean }

/**
 * Normalised, fixed-length key part. An unset param becomes `null` rather than
 * being dropped, so a params object re-created on every render hashes to the
 * same key instead of thrashing the cache.
 */
function windowKeyPart(params: AnalyticsListParams = {}): unknown[] {
  return [
    params.start_date ?? null,
    params.end_date ?? null,
    params.granularity ?? null,
    params.project_id ?? null,
  ]
}

/** Stable key factory. Every key lives under the `['analytics']` root. */
export const analyticsKeys = {
  all: () => ['analytics'] as const,
  overview: (params?: AnalyticsListParams) =>
    (params ? ['analytics', 'overview', ...windowKeyPart(params)] : ['analytics', 'overview']) as readonly unknown[],
  productivity: (params?: AnalyticsListParams) =>
    (params
      ? ['analytics', 'productivity', ...windowKeyPart(params)]
      : ['analytics', 'productivity']) as readonly unknown[],
  deadlines: (params?: AnalyticsListParams) =>
    (params ? ['analytics', 'deadlines', ...windowKeyPart(params)] : ['analytics', 'deadlines']) as readonly unknown[],
  consistency: (params?: AnalyticsListParams) =>
    (params
      ? ['analytics', 'consistency', ...windowKeyPart(params)]
      : ['analytics', 'consistency']) as readonly unknown[],
  focus: (params?: AnalyticsListParams) =>
    (params ? ['analytics', 'focus', ...windowKeyPart(params)] : ['analytics', 'focus']) as readonly unknown[],
  estimation: (params?: AnalyticsListParams) =>
    (params
      ? ['analytics', 'estimation', ...windowKeyPart(params)]
      : ['analytics', 'estimation']) as readonly unknown[],
  workload: (params?: AnalyticsListParams) =>
    (params ? ['analytics', 'workload', ...windowKeyPart(params)] : ['analytics', 'workload']) as readonly unknown[],
  time: (params?: AnalyticsListParams) =>
    (params ? ['analytics', 'time', ...windowKeyPart(params)] : ['analytics', 'time']) as readonly unknown[],
  projects: (params?: AnalyticsListParams) =>
    (params ? ['analytics', 'projects', ...windowKeyPart(params)] : ['analytics', 'projects']) as readonly unknown[],
  tasks: (params?: AnalyticsListParams) =>
    (params ? ['analytics', 'tasks', ...windowKeyPart(params)] : ['analytics', 'tasks']) as readonly unknown[],
  learning: (params?: AnalyticsListParams) =>
    (params ? ['analytics', 'learning', ...windowKeyPart(params)] : ['analytics', 'learning']) as readonly unknown[],
  knowledge: (params?: AnalyticsListParams) =>
    (params
      ? ['analytics', 'knowledge', ...windowKeyPart(params)]
      : ['analytics', 'knowledge']) as readonly unknown[],
  trends: (params?: AnalyticsListParams & { metric?: TrendMetric }) =>
    (params
      ? ['analytics', 'trends', ...windowKeyPart(params), params.metric ?? 'tasks_completed']
      : ['analytics', 'trends']) as readonly unknown[],
  series: (params?: AnalyticsListParams) =>
    (params ? ['analytics', 'series', ...windowKeyPart(params)] : ['analytics', 'series']) as readonly unknown[],
  manifest: () => ['analytics', 'manifest'] as const,
}

/* ------------------------------------------------------- window (in the URL) */

export interface AnalyticsWindow {
  preset: WindowPresetId
  range: AnalyticsRange
  granularity: Granularity
  /** The custom window, retained so switching back to Custom restores it. */
  custom: { start_date: string; end_date: string } | null
  setPreset: (preset: WindowPresetId) => void
  setCustom: (startDate: string, endDate: string) => void
  setGranularity: (granularity: Granularity) => void
}

const GRANULARITIES: readonly string[] = ['day', 'week', 'month']

/**
 * The analytics window, held in the URL.
 *
 * **Shareable, not remembered.** `?range=30d` is the same view for the person
 * it was sent to, and the browser's back button moves through windows instead
 * of out of them. Only a custom window carries `start`/`end`; every preset is
 * resolved against today on the client so the link stays short and a link sent
 * yesterday still means "yesterday's last 7 days" rather than a frozen window.
 */
export function useAnalyticsWindow(defaultPreset: WindowPresetId = DEFAULT_WINDOW_PRESET): AnalyticsWindow {
  const [searchParams, setSearchParams] = useSearchParams()

  const presetParam = searchParams.get('range')
  const preset: WindowPresetId = isWindowPreset(presetParam) ? presetParam : defaultPreset
  const startParam = searchParams.get('start')
  const endParam = searchParams.get('end')

  const custom = useMemo(() => {
    const base = resolveWindow('custom', undefined, {
      start_date: startParam ?? undefined,
      end_date: endParam ?? undefined,
    })
    return { start_date: base.start_date, end_date: base.end_date }
  }, [startParam, endParam])

  const granularityParam = searchParams.get('granularity')
  const granularity: Granularity = GRANULARITIES.includes(granularityParam ?? '')
    ? (granularityParam as Granularity)
    : 'day'

  const range = useMemo(
    () => (preset === 'custom' ? custom : resolveWindow(preset)),
    [preset, custom],
  )

  const write = useCallback(
    (next: { preset: WindowPresetId; start?: string; end?: string; granularity?: Granularity }) => {
      const params = new URLSearchParams(searchParams)
      if (next.preset === defaultPreset) params.delete('range')
      else params.set('range', next.preset)
      if (next.start && next.end) {
        params.set('start', next.start)
        params.set('end', next.end)
      } else {
        params.delete('start')
        params.delete('end')
      }
      if (next.granularity) params.set('granularity', next.granularity)
      // `replace` so paging the window does not fill the history with states a
      // back button has to walk back through one range at a time.
      setSearchParams(params, { replace: true })
    },
    [searchParams, setSearchParams, defaultPreset],
  )

  return {
    preset,
    range,
    granularity,
    custom,
    setPreset: (next) => write({ preset: next }),
    setCustom: (startDate, endDate) => write({ preset: 'custom', start: startDate, end: endDate }),
    setGranularity: (next) => write({ preset, granularity: next }),
  }
}

/* ----------------------------------------------------------------- queries */

/** The dashboard's single request: totals, comparisons and every headline score. */
export function useOverview(
  params: AnalyticsListParams,
  options: Enabled = {},
): UseQueryResult<OverviewRead> {
  return useQuery({
    queryKey: analyticsKeys.overview(params),
    queryFn: ({ signal }) => fetchOverview(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

export function useProductivity(
  params: AnalyticsListParams,
  options: Enabled = {},
): UseQueryResult<ProductivityRead> {
  return useQuery({
    queryKey: analyticsKeys.productivity(params),
    queryFn: ({ signal }) => fetchProductivity(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

export function useDeadlines(
  params: AnalyticsListParams,
  options: Enabled = {},
): UseQueryResult<DeadlineAdherenceRead> {
  return useQuery({
    queryKey: analyticsKeys.deadlines(params),
    queryFn: ({ signal }) => fetchDeadlines(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

export function useConsistency(
  params: AnalyticsListParams,
  options: Enabled = {},
): UseQueryResult<ConsistencyRead> {
  return useQuery({
    queryKey: analyticsKeys.consistency(params),
    queryFn: ({ signal }) => fetchConsistency(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

export function useFocus(
  params: AnalyticsListParams,
  options: Enabled = {},
): UseQueryResult<FocusRead> {
  return useQuery({
    queryKey: analyticsKeys.focus(params),
    queryFn: ({ signal }) => fetchFocus(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

export function useEstimation(
  params: AnalyticsListParams,
  options: Enabled = {},
): UseQueryResult<EstimationAccuracyRead> {
  return useQuery({
    queryKey: analyticsKeys.estimation(params),
    queryFn: ({ signal }) => fetchEstimation(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

export function useTimeDistribution(
  params: AnalyticsListParams,
  options: Enabled = {},
): UseQueryResult<TimeDistributionRead> {
  return useQuery({
    queryKey: analyticsKeys.time(params),
    queryFn: ({ signal }) => fetchTimeDistribution(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

export function useProjectAnalytics(
  params: AnalyticsListParams,
  options: Enabled = {},
): UseQueryResult<ProjectAnalyticsRead[]> {
  return useQuery({
    queryKey: analyticsKeys.projects(params),
    queryFn: ({ signal }) => fetchProjects(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

export function useTaskAnalytics(
  params: AnalyticsListParams,
  options: Enabled = {},
): UseQueryResult<TaskAnalyticsRead> {
  return useQuery({
    queryKey: analyticsKeys.tasks(params),
    queryFn: ({ signal }) => fetchTasks(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

export function useLearningAnalytics(
  params: AnalyticsListParams,
  options: Enabled = {},
): UseQueryResult<LearningAnalyticsRead> {
  return useQuery({
    queryKey: analyticsKeys.learning(params),
    queryFn: ({ signal }) => fetchLearning(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

export function useKnowledgeAnalytics(
  params: AnalyticsListParams,
  options: Enabled = {},
): UseQueryResult<KnowledgeAnalyticsRead> {
  return useQuery({
    queryKey: analyticsKeys.knowledge(params),
    queryFn: ({ signal }) => fetchKnowledge(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

/**
 * One metric over time.
 *
 * The metric is part of the key, not of the fetch only: switching from
 * "tasks completed" to "tasks created" is a different series, and sharing a
 * cache entry would draw one as the other.
 */
export function useTrends(
  params: AnalyticsListParams & { metric?: TrendMetric },
  options: Enabled = {},
): UseQueryResult<TrendPoint[]> {
  return useQuery({
    queryKey: analyticsKeys.trends(params),
    queryFn: ({ signal }) => fetchTrends(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

/** The stored `daily_metrics` rows. Empty means "never rebuilt", not "nothing". */
export function useDailySeries(
  params: AnalyticsListParams,
  options: Enabled = {},
): UseQueryResult<DailyMetricRead[]> {
  return useQuery({
    queryKey: analyticsKeys.series(params),
    queryFn: ({ signal }) => fetchSeries(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

export function useExportManifest(options: Enabled = {}): UseQueryResult<CsvExportManifestRead> {
  return useQuery({
    queryKey: analyticsKeys.manifest(),
    queryFn: ({ signal }) => fetchExportManifest(signal),
    enabled: options.enabled,
    // The column list is a published contract; it does not move per window.
    staleTime: 60 * 60_000,
  })
}

/** The shareable href for a dataset. Use `downloadCsvExport` for the click. */
export function exportCsvUrl(
  dataset: AnalyticsCsvDataset,
  params: AnalyticsListParams = {},
): string {
  return buildExportCsvUrl(dataset, params)
}

/* --------------------------------------------------------------- mutations */

/**
 * Recomputes `daily_metrics` for the window.
 *
 * One invalidation target for the whole tree, so a rebuild cannot ship having
 * moved the overview but left the trend or the heatmap on last week's rows.
 */
export function useRebuildAnalytics(): UseMutationResult<
  RebuildRead,
  Error,
  AnalyticsListParams
> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (params: AnalyticsListParams) => rebuildAnalytics(params),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: analyticsKeys.all() })
    },
  })
}

/* -------------------------------------------------------------- staleness */

export type StalenessStatus = 'fresh' | 'partial' | 'calculating' | 'unknown'

export interface AnalyticsStaleness {
  status: StalenessStatus
  /** The newest `daily_metrics` row behind the figures on screen. */
  aggregatesThrough: string | null
  /** The last day of the requested window. */
  windowEnd: string
  /** Days in the window with no aggregate at all. */
  missingDays: number
  windowDays: number
  /** The backend's own staleness flag, when it sent one. */
  reportedStale: boolean
  /** One sentence, ready to render. Never says "up to date" on stale data. */
  message: string
}

/**
 * How old the numbers on screen are.
 *
 * **The spec forbids silently showing stale numbers**, so this compares the
 * newest aggregate row against the *end of the requested window*: a window
 * that reaches into days the aggregator has not reached yet is reported as
 * partial rather than rendered as if it were complete. An empty series is
 * "calculating", never "0 activity" — the two are the same array on the wire
 * and completely different claims.
 *
 * The overview is accepted rather than fetched so the dashboard, which already
 * has it, does not pay for a second request to describe the data it is already
 * rendering.
 */
export function useAnalyticsStaleness(
  range: AnalyticsRange,
  overview?: OverviewRead,
): AnalyticsStaleness {
  return useMemo(() => {
    const daily = overview?.daily ?? []
    const latest = latestMetricDate(daily) ?? overview?.aggregates_through ?? null

    const windowEnd = range.end_date
    const windowStart = range.start_date
    const windowDays =
      overview?.range?.start_date && overview?.range?.end_date
        ? inclusiveDays(overview.range.start_date, overview.range.end_date)
        : inclusiveDays(windowStart, windowEnd)

    const covered = latest ? inclusiveDays(windowStart, latest < windowStart ? windowStart : latest) : 0
    const missingDays = Math.max(0, windowDays - covered)
    const reportedStale = Boolean(overview?.stale || overview?.is_stale)

    if (daily.length === 0) {
      return {
        status: 'calculating',
        aggregatesThrough: latest,
        windowEnd,
        missingDays: windowDays,
        windowDays,
        reportedStale,
        message: 'No aggregates have been written for this window yet.',
      }
    }

    if (missingDays > 0 || reportedStale) {
      return {
        status: 'partial',
        aggregatesThrough: latest,
        windowEnd,
        missingDays,
        windowDays,
        reportedStale,
        message: `Aggregates run through ${latest ?? 'an unknown date'}; ${missingDays} of ${windowDays} days in this window have not been calculated.`,
      }
    }

    return {
      status: 'fresh',
      aggregatesThrough: latest,
      windowEnd,
      missingDays: 0,
      windowDays,
      reportedStale: false,
      message: `Every day in this window is calculated, through ${latest ?? windowEnd}.`,
    }
  }, [range.start_date, range.end_date, overview])
}

function latestMetricDate(daily: readonly { metric_date: string }[]): string | null {
  let latest: string | null = null
  for (const row of daily) {
    if (latest === null || row.metric_date > latest) latest = row.metric_date
  }
  return latest
}

function inclusiveDays(start: string, end: string): number {
  const from = Date.parse(`${start}T00:00:00Z`)
  const to = Date.parse(`${end}T00:00:00Z`)
  if (Number.isNaN(from) || Number.isNaN(to) || to < from) return 0
  return Math.round((to - from) / 86_400_000) + 1
}
