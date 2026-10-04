/**
 * Thin typed wrappers over the Phase 6 analytics endpoints.
 *
 * No React here — every function is a promise-returning call the hooks in
 * `features/analytics/hooks.ts` wrap in a `queryFn`/`mutationFn`.
 *
 * Two things are dictated by the backend rather than chosen here:
 *
 * - **A missing query parameter is meaningful.** `start_date`/`end_date` default
 *   to the last 7 days server-side, and an *inverted* window is a 422 rather
 *   than an empty result — an empty result would read exactly like "you did
 *   nothing". So the helpers below drop unset params (never sending a literal
 *   `null`) but send whatever the caller resolved, and the client never derives
 *   a window from a response.
 * - **`POST /rebuild` answers 202 with `{rows_written}`**, not 200 with a
 *   resource, so it is typed as a mutation rather than a query.
 *
 * The CSV route is a *download*, not a JSON read. `exportCsvUrl` builds the
 * shareable href; `downloadCsvExport` is the authenticated fetch a browser
 * actually needs, because NEXUS authenticates with a bearer header and a bare
 * `<a href>` would arrive unauthenticated.
 */
import { apiClient, type QueryParams, queryFrom } from '@/lib/api-client'
import type {
  AnalyticsCsvDataset,
  AnalyticsListParams,
  CsvExportManifestRead,
  ConsistencyRead,
  DeadlineAdherenceRead,
  EstimationAccuracyRead,
  FocusRead,
  Granularity,
  KnowledgeAnalyticsRead,
  LearningAnalyticsRead,
  OverviewRead,
  ProductivityRead,
  ProjectAnalyticsRead,
  RebuildRead,
  TaskAnalyticsRead,
  TimeDistributionRead,
  TrendMetric,
  TrendPoint,
  DailyMetricRead,
} from '@/types/analytics'

export const ANALYTICS_ENDPOINTS = {
  overview: '/analytics/overview',
  productivity: '/analytics/productivity',
  deadlines: '/analytics/deadlines',
  consistency: '/analytics/consistency',
  focus: '/analytics/focus',
  estimation: '/analytics/estimation',
  workload: '/analytics/workload',
  time: '/analytics/time',
  projects: '/analytics/projects',
  tasks: '/analytics/tasks',
  learning: '/analytics/learning',
  knowledge: '/analytics/knowledge',
  trends: '/analytics/trends',
  series: '/analytics/series',
  rebuild: '/analytics/rebuild',
  exportCsv: '/analytics/export.csv',
  exportManifest: '/analytics/export',
  featureSnapshot: '/analytics/feature-snapshot',
} as const

/** Dropped when unset: `null`/undefined would serialise as a literal. */
function windowQuery(params: AnalyticsListParams = {}): QueryParams {
  return queryFrom({
    start_date: params.start_date,
    end_date: params.end_date,
    granularity: params.granularity,
    project_id: params.project_id,
  })
}

export function fetchOverview(
  params: AnalyticsListParams = {},
  signal?: AbortSignal,
): Promise<OverviewRead> {
  return apiClient.get<OverviewRead>(ANALYTICS_ENDPOINTS.overview, {
    query: windowQuery(params),
    signal,
  })
}

export function fetchProductivity(
  params: AnalyticsListParams = {},
  signal?: AbortSignal,
): Promise<ProductivityRead> {
  return apiClient.get<ProductivityRead>(ANALYTICS_ENDPOINTS.productivity, {
    query: windowQuery(params),
    signal,
  })
}

export function fetchDeadlines(
  params: AnalyticsListParams = {},
  signal?: AbortSignal,
): Promise<DeadlineAdherenceRead> {
  return apiClient.get<DeadlineAdherenceRead>(ANALYTICS_ENDPOINTS.deadlines, {
    query: windowQuery(params),
    signal,
  })
}

export function fetchConsistency(
  params: AnalyticsListParams = {},
  signal?: AbortSignal,
): Promise<ConsistencyRead> {
  return apiClient.get<ConsistencyRead>(ANALYTICS_ENDPOINTS.consistency, {
    query: windowQuery(params),
    signal,
  })
}

export function fetchFocus(params: AnalyticsListParams = {}, signal?: AbortSignal): Promise<FocusRead> {
  return apiClient.get<FocusRead>(ANALYTICS_ENDPOINTS.focus, { query: windowQuery(params), signal })
}

export function fetchEstimation(
  params: AnalyticsListParams = {},
  signal?: AbortSignal,
): Promise<EstimationAccuracyRead> {
  return apiClient.get<EstimationAccuracyRead>(ANALYTICS_ENDPOINTS.estimation, {
    query: windowQuery(params),
    signal,
  })
}

export function fetchTimeDistribution(
  params: AnalyticsListParams = {},
  signal?: AbortSignal,
): Promise<TimeDistributionRead> {
  return apiClient.get<TimeDistributionRead>(ANALYTICS_ENDPOINTS.time, {
    query: windowQuery(params),
    signal,
  })
}

export function fetchProjects(
  params: AnalyticsListParams = {},
  signal?: AbortSignal,
): Promise<ProjectAnalyticsRead[]> {
  return apiClient.get<ProjectAnalyticsRead[]>(ANALYTICS_ENDPOINTS.projects, {
    query: windowQuery(params),
    signal,
  })
}

export function fetchTasks(
  params: AnalyticsListParams = {},
  signal?: AbortSignal,
): Promise<TaskAnalyticsRead> {
  return apiClient.get<TaskAnalyticsRead>(ANALYTICS_ENDPOINTS.tasks, {
    query: windowQuery(params),
    signal,
  })
}

export function fetchLearning(
  params: AnalyticsListParams = {},
  signal?: AbortSignal,
): Promise<LearningAnalyticsRead> {
  return apiClient.get<LearningAnalyticsRead>(ANALYTICS_ENDPOINTS.learning, {
    query: windowQuery(params),
    signal,
  })
}

export function fetchKnowledge(
  params: AnalyticsListParams = {},
  signal?: AbortSignal,
): Promise<KnowledgeAnalyticsRead> {
  return apiClient.get<KnowledgeAnalyticsRead>(ANALYTICS_ENDPOINTS.knowledge, {
    query: windowQuery(params),
    signal,
  })
}

/**
 * One metric over time, read from `daily_metrics`.
 *
 * Buckets with no recorded activity are **omitted** by the backend rather than
 * zero-filled, so a chart of this data has genuine gaps. That is deliberate: a
 * line drawn through a day nothing happened is a claim about that day.
 */
export function fetchTrends(
  params: AnalyticsListParams & { metric?: TrendMetric; granularity?: Granularity },
  signal?: AbortSignal,
): Promise<TrendPoint[]> {
  return apiClient.get<TrendPoint[]>(ANALYTICS_ENDPOINTS.trends, {
    query: {
      ...windowQuery(params),
      ...queryFrom({ metric: params.metric ?? 'tasks_completed' }),
    },
    signal,
  })
}

export function fetchSeries(
  params: AnalyticsListParams = {},
  signal?: AbortSignal,
): Promise<DailyMetricRead[]> {
  return apiClient.get<DailyMetricRead[]>(ANALYTICS_ENDPOINTS.series, {
    query: windowQuery(params),
    signal,
  })
}

/** 202 + `{rows_written}`. Idempotent server-side: the same window upserts. */
export function rebuildAnalytics(
  params: AnalyticsListParams = {},
  signal?: AbortSignal,
): Promise<RebuildRead> {
  return apiClient.post<RebuildRead>(ANALYTICS_ENDPOINTS.rebuild, undefined, {
    query: windowQuery(params),
    signal,
    parse: 'json',
  })
}

export function fetchExportManifest(signal?: AbortSignal): Promise<CsvExportManifestRead> {
  return apiClient.get<CsvExportManifestRead>(ANALYTICS_ENDPOINTS.exportManifest, { signal })
}

/**
 * The shareable href for a CSV dataset.
 *
 * Absolute against the API base so it can be copied out of the address bar and
 * re-opened. It is *not* sufficient on its own to download: NEXUS authenticates
 * with a bearer header, so use {@link downloadCsvExport} for the click.
 */
export function exportCsvUrl(
  dataset: AnalyticsCsvDataset,
  params: AnalyticsListParams = {},
): string {
  return apiClient.buildUrl(ANALYTICS_ENDPOINTS.exportCsv, {
    ...windowQuery(params),
    dataset,
  })
}

/**
 * Downloads a dataset as a file.
 *
 * The route is a download rather than a JSON read, so it cannot be fetched with
 * a bare `fetch`: NEXUS authenticates with a bearer header that only
 * `apiClient` attaches, and an `<a href>` would arrive unauthenticated. The body
 * is therefore read as text through the client and handed to the browser as an
 * object URL, which is the one way to get an authenticated download.
 *
 * An empty window is not an error server-side — a header row with no data rows
 * is a valid file — so this only throws when the request itself fails.
 */
export async function downloadCsvExport(
  dataset: AnalyticsCsvDataset,
  params: AnalyticsListParams = {},
): Promise<{ filename: string; rowCount: number }> {
  const csv = await apiClient.get<string>(ANALYTICS_ENDPOINTS.exportCsv, {
    query: { ...windowQuery(params), dataset },
    parse: 'text',
  })

  const filename = `${dataset}-${params.start_date ?? 'all'}-${params.end_date ?? 'all'}.csv`
  const href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))

  try {
    const anchor = document.createElement('a')
    anchor.href = href
    anchor.download = filename
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
  } finally {
    // Revoking on the next tick keeps Safari from cancelling the download.
    setTimeout(() => URL.revokeObjectURL(href), 0)
  }

  return { filename, rowCount: countCsvRows(csv) }
}

/** Data rows, excluding the header. A quoted newline would overcount by one. */
function countCsvRows(csv: string): number {
  const lines = csv.split(/\r?\n/).filter((line) => line.length > 0)
  return Math.max(0, lines.length - 1)
}
