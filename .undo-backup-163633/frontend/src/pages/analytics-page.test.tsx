import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { queryClient } from '@/app/query-client'
import { AppProviders } from '@/app/providers'
import AnalyticsPage from '@/pages/analytics-page'
import { useAuthStore } from '@/stores/auth-store'
import { formatRangeLabel } from '@/types/analytics'
import type { ApiErrorEnvelope, User } from '@/types/api'
import type {
  ConsistencyRead,
  DailyMetricRead,
  DeadlineAdherenceRead,
  EstimationAccuracyRead,
  FocusRead,
  KnowledgeAnalyticsRead,
  LearningAnalyticsRead,
  MetricRange,
  OverviewRead,
  ProductivityRead,
  ProjectAnalyticsRead,
  TaskAnalyticsRead,
  TimeDistributionRead,
  TrendPoint,
  WorkloadRead,
} from '@/types/analytics'

/**
 * The dedicated Analytics page, asserted at the network boundary.
 *
 * The page is mounted for real — real providers, real memory router, real
 * charts, real `AnalyticsPage` — and only `fetch` is stubbed. Every figure on
 * screen is therefore a body this file wrote, so a value can be checked by
 * hand: 8 tasks completed against 5 the period before is `up 3 (+60%)`, 135
 * recorded minutes is `2h 15m`, 6 of 8 tasks finished in time is 75%, and four
 * days with something recorded in a seven-day window is the heatmap's
 * "4 days … in the last 7".
 *
 * Four claims the spec makes by name are pinned here rather than left to the
 * component tests:
 *
 * - **Every tab is reachable** — the eight the brief names, in the order it
 *   names them, each rendering its own panels and no error state.
 * - **The window is shared state** — changing it re-reads the analytics
 *   endpoints over the new window, which is only observable in the outgoing
 *   request URLs.
 * - **A tab fetches only what it reads.** The page hands every single-metric
 *   hook an `enabled` flag keyed on the open tab, so the endpoint for an
 *   unopened tab must never appear in the request log at all. This is the
 *   brief's "do not reload unrelated parts of the application".
 * - **A metric that cannot be measured says so.** `null` never becomes `NaN`,
 *   `Infinity`, `undefined%` or "0% productivity" — checked by scanning the
 *   rendered text of every tab rather than by inspecting a formatter.
 */

const USER: User = {
  id: '11111111-1111-4111-8111-111111111111',
  email: 'ada@nexus.local',
  username: 'ada',
  display_name: 'Ada Lovelace',
  avatar_url: null,
  role: 'user',
  permissions: [],
  is_active: true,
  is_verified: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  last_login_at: null,
}

/**
 * A fixed seven-day window, pinned in the URL rather than left to the default
 * preset. `useAnalyticsWindow` resolves `7d` against the real today, so an
 * unpinned window would make every range caption depend on the day the suite
 * runs. Pinned, the figures below are the figures.
 *
 * 2026-01-05 is a Monday, which is also the heatmap's week convention.
 */
const WINDOW_START = '2026-01-05'
const WINDOW_END = '2026-01-11'
const WINDOW_QUERY = `/analytics?range=custom&start=${WINDOW_START}&end=${WINDOW_END}`

/** The window a second test applies, so a range change is visible by hand. */
const NEXT_START = '2026-02-02'
const NEXT_END = '2026-02-08'

const RANGE: MetricRange = {
  start_date: WINDOW_START,
  end_date: WINDOW_END,
  granularity: 'day',
}

const PREVIOUS_RANGE: MetricRange = {
  start_date: '2025-12-29',
  end_date: '2026-01-04',
  granularity: 'day',
}

/**
 * Range captions are rendered through `Intl`, so they follow the machine's
 * locale. The expectation is built with the same formatter the page uses
 * rather than hard-coded to one language — the numbers are what these tests
 * pin, not the month names.
 */
const WINDOW_LABEL = formatRangeLabel(WINDOW_START, WINDOW_END)
const NEXT_LABEL = formatRangeLabel(NEXT_START, NEXT_END)

/**
 * A stamp taken at module load, so the freshness banner reads "just now" on
 * every machine at every hour. A hard-coded date would put the banner into
 * "N days ago" territory as the fixture aged, which says nothing about the
 * page.
 */
const UPDATED_AT = new Date().toISOString()

function day(
  metric_date: string,
  tasks_completed: number,
  planned_minutes: number,
  actual_minutes: number,
  work_sessions: number,
): DailyMetricRead {
  return {
    metric_date,
    tasks_created: 0,
    tasks_completed,
    tasks_overdue: 0,
    tasks_cancelled: 0,
    tasks_blocked: 0,
    tasks_rescheduled: 0,
    planned_minutes,
    actual_minutes,
    work_sessions,
    calendar_events: 0,
    knowledge_events: 0,
    projects_touched: 0,
    updated_at: UPDATED_AT,
  }
}

/**
 * Seven days, four of them with something on them. The three sums the page
 * reads are checked in the test bodies:
 *
 * - tasks completed: 2+1+0+3+2+0+0 = 8
 * - actual minutes: 60+30+0+45+0+0+0 = 135, i.e. `2h 15m`
 * - work sessions: 2+1+0+3+0+0+0 = 6
 *
 * The heatmap adds completed tasks to sessions per day, so its active days are
 * the four days with either: 05 Jan (2+2), 06 Jan (1+1), 08 Jan (3+3) and
 * 09 Jan (2+0).
 */
const DAILY: DailyMetricRead[] = [
  day(WINDOW_START, 2, 60, 60, 2),
  day('2026-01-06', 1, 45, 30, 1),
  day('2026-01-07', 0, 0, 0, 0),
  day('2026-01-08', 3, 45, 45, 3),
  day('2026-01-09', 2, 30, 0, 0),
  day('2026-01-10', 0, 0, 0, 0),
  day(WINDOW_END, 0, 0, 0, 0),
]

/** 24 + 19 + 18 + 17 = 78, the total the score card prints. */
const PRODUCTIVITY_COMPONENTS = [
  {
    name: 'Completion',
    points: 24,
    max_points: 30,
    explanation: 'On-time share of completed work.',
  },
  { name: 'Consistency', points: 19, max_points: 25, explanation: 'Days with recorded activity.' },
  {
    name: 'Deadline rate',
    points: 18,
    max_points: 25,
    explanation: 'Finished before the due date.',
  },
  {
    name: 'Focus time',
    points: 17,
    max_points: 20,
    explanation: 'Completed planned work sessions.',
  },
]

const PRODUCTIVITY_FORMULA = 'A weighted sum of four factors, clamped to 0-100.'
const PRODUCTIVITY_DISCLAIMER = 'A NEXUS-derived metric, not a validated measure of productivity.'

const PRODUCTIVITY: ProductivityRead = {
  score: 78,
  available: true,
  reason_if_unavailable: null,
  components: PRODUCTIVITY_COMPONENTS,
  formula: PRODUCTIVITY_FORMULA,
  label: 'Productivity score',
  disclaimer: PRODUCTIVITY_DISCLAIMER,
  range: RANGE,
  weight_total: 100,
}

const CONSISTENCY: ConsistencyRead = {
  score: 74,
  available: true,
  reason_if_unavailable: null,
  active_days: 4,
  window_days: 7,
  work_sessions: 6,
  session_count: 6,
  active_day_ratio: 57.1,
  longest_streak: 2,
  current_streak: 1,
  formula: 'Days with recorded activity over days in the window.',
  label: 'Consistency score',
  disclaimer: 'A NEXUS-derived metric, not a validated measure of consistency.',
  range: RANGE,
  components: [
    { name: 'Active days', points: 19, max_points: 25, explanation: 'Four of seven days.' },
  ],
}

const FOCUS: FocusRead = {
  score: 61,
  available: true,
  reason_if_unavailable: null,
  // A whole number on purpose: `formatMinutes` rounds, and 20 is the only value
  // this fixture asserts, so the rounding is not doing any work here.
  avg_session_minutes: 20,
  completed_planned_sessions: 5,
  interruptions: 3,
  reschedules: 1,
  focused_minutes: 135,
  total_minutes: 135,
  formula: 'Completed planned sessions against interruptions.',
  label: 'Focus score',
  disclaimer: 'Derived from recorded work-session behaviour, not from attention.',
  range: RANGE,
  components: [
    { name: 'Focus time', points: 17, max_points: 20, explanation: 'Sessions run to completion.' },
  ],
}

const ESTIMATION: EstimationAccuracyRead = {
  available: true,
  reason_if_unavailable: null,
  sample_count: 5,
  pairs_compared: 5,
  absolute_error: 12,
  mean_absolute_error: 12,
  percentage_error: 20,
  mean_percentage_error: 18,
  bias: -8,
  median_error: 10,
  under_estimation_rate: 60,
  over_estimation_rate: 40,
  underestimation_rate: 60,
  overestimation_rate: 40,
  range: RANGE,
}

/** 600 scheduled against 800 declared, which is 75% exactly. */
const WORKLOAD: WorkloadRead = {
  open_tasks: 5,
  high_priority_open: 2,
  overdue_open: 1,
  scheduled_minutes: 600,
  available_minutes: 800,
  workload_ratio: 75,
  average_daily_scheduled_minutes: 100,
  high_priority_tasks: 2,
  overdue_tasks: 1,
  actual_minutes: 135,
  available: true,
  reason_if_unavailable: null,
  comparison: [],
  status_counts: { todo: 3, in_progress: 2 },
  priority_counts: { high: 2, medium: 2, low: 1 },
  range: RANGE,
}

const OVERVIEW: OverviewRead = {
  range: RANGE,
  previous_range: PREVIOUS_RANGE,
  stale: false,
  is_stale: false,
  aggregates_through: WINDOW_END,
  data_as_of: WINDOW_END,
  totals: [
    { label: 'tasks_completed', current: 8, previous: 5, absolute_change: 3, percent_change: 60 },
    {
      label: 'actual_minutes',
      current: 135,
      previous: 90,
      absolute_change: 45,
      percent_change: 50,
    },
  ],
  productivity: PRODUCTIVITY,
  deadlines: {
    available: true,
    reason_if_unavailable: null,
    on_time: 6,
    late: 2,
    still_overdue: 1,
    // 6 of the 8 tasks considered finished inside the window.
    adherence_rate: 75,
    rate: 75,
    overdue_open: 1,
    total_considered: 8,
    range: RANGE,
    components: [],
  },
  consistency: CONSISTENCY,
  focus: FOCUS,
  estimation: ESTIMATION,
  workload: WORKLOAD,
  daily: DAILY,
  reason_if_empty: null,
}

/**
 * Three recorded buckets. A bucket with nothing in it is omitted rather than
 * zero-filled, so the window's three quiet days are genuinely absent here.
 */
const TRENDS: TrendPoint[] = [
  {
    bucket: WINDOW_START,
    label: '5 Jan',
    value: 2,
    previous: 1,
    absolute_change: 1,
    percent_change: 100,
    period_start: null,
    period_end: null,
  },
  {
    bucket: '2026-01-06',
    label: '6 Jan',
    value: 1,
    previous: 2,
    absolute_change: -1,
    percent_change: -50,
    period_start: null,
    period_end: null,
  },
  {
    bucket: '2026-01-08',
    label: '8 Jan',
    value: 3,
    previous: 1,
    absolute_change: 2,
    percent_change: 200,
    period_start: null,
    period_end: null,
  },
]

/** 90 of 135 minutes is two thirds; 45 of 135 is one third. */
const TIME: TimeDistributionRead = {
  total_minutes: 135,
  available: true,
  reason_if_unavailable: null,
  unassigned_minutes: 0,
  project_id: null,
  by_project: [
    { key: 'atlas', label: 'Atlas', minutes: 90, share: 66.7 },
    { key: 'beacon', label: 'Beacon', minutes: 45, share: 33.3 },
  ],
  by_task: [
    { key: 't-1', label: 'Draft the migration plan', minutes: 90, share: 66.7 },
    { key: 't-2', label: 'Rotate the API keys', minutes: 45, share: 33.3 },
  ],
  slices: [],
  range: RANGE,
}

/**
 * Two projects, one of them with nothing to complete. The second is the case a
 * `completion_rate: null` exists for: no bar and a sentence, rather than a 0%
 * bar claiming the project failed.
 */
const PROJECTS: ProjectAnalyticsRead[] = [
  {
    project_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    name: 'Atlas',
    status: 'active',
    total_tasks: 10,
    completed_tasks: 8,
    remaining_tasks: 2,
    overdue_tasks: 0,
    completion_rate: 80,
    total_work_minutes: 90,
    avg_task_actual_minutes: 11.25,
    estimation: null,
    velocity: {
      tasks_per_week: 2.5,
      estimated_minutes_per_week: 180,
      weeks_measured: 4,
      definition: 'Tasks completed per week across the weeks measured in this window.',
    },
    velocity_tasks_per_week: 2.5,
    weekly_completed: [2, 3, 1, 2],
    work_minutes: 90,
    estimated_minutes: 120,
    actual_minutes: 90,
    avg_task_minutes: 9,
    activity_events: 12,
    available: true,
    reason_if_unavailable: null,
    range: RANGE,
  },
  {
    project_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    name: 'Beacon',
    status: 'planning',
    total_tasks: 0,
    completed_tasks: 0,
    remaining_tasks: 0,
    overdue_tasks: 0,
    completion_rate: null,
    total_work_minutes: 0,
    avg_task_actual_minutes: null,
    estimation: null,
    velocity: null,
    velocity_tasks_per_week: null,
    weekly_completed: [],
    work_minutes: 0,
    estimated_minutes: 0,
    actual_minutes: 0,
    avg_task_minutes: null,
    activity_events: 0,
    available: false,
    reason_if_unavailable: null,
    range: RANGE,
  },
]

/** 8 of 10 tasks is an 80% completion rate, and 1 of 10 overdue is 10%. */
const TASK_ANALYTICS: TaskAnalyticsRead = {
  total_tasks: 10,
  completed_tasks: 8,
  open_tasks: 2,
  overdue_tasks: 1,
  cancelled_tasks: 0,
  blocked_tasks: 1,
  completion_rate: 80,
  overdue_rate: 10,
  avg_completion_days: 2.5,
  avg_cycle_minutes: null,
  avg_estimate_error_minutes: null,
  tasks_created: 9,
  tasks_completed: 8,
  tasks_cancelled: 0,
  tasks_blocked: 1,
  tasks_overdue: 1,
  tasks_rescheduled: 0,
  available: true,
  reason_if_unavailable: null,
  estimation: null,
  top_overdue: [
    {
      task_id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
      title: 'Rotate the API keys',
      due_date: '2026-01-07',
      days_overdue: 4,
      priority: 'high',
    },
  ],
  by_status: { todo: 1, in_progress: 1, completed: 8 },
  by_priority: { high: 2, medium: 5, low: 3 },
  range: RANGE,
}

/** The same six of eight finished in time the overview headline reports. */
const DEADLINES: DeadlineAdherenceRead = {
  available: true,
  reason_if_unavailable: null,
  on_time: 6,
  late: 2,
  still_overdue: 1,
  adherence_rate: 75,
  rate: 75,
  overdue_open: 1,
  total_considered: 8,
  range: RANGE,
  components: [],
}

const LEARNING: LearningAnalyticsRead = {
  available: true,
  reason_if_unavailable: null,
  study_events: 4,
  study_minutes: 240,
  // Always null today: the knowledge rows carry no task id, so the correlation
  // is not computable rather than zero.
  knowledge_linked_tasks: null,
  knowledge_interactions: 9,
  notes_created: 3,
  notes_updated: 2,
  projects_touched: 2,
  basis: 'Calendar events typed study, plus every recorded knowledge event.',
  definition: 'Learning activity is what you have recorded, not an assessment of what you learned.',
  range: RANGE,
}

const KNOWLEDGE: KnowledgeAnalyticsRead = {
  available: true,
  reason_if_unavailable: null,
  notes_created: 5,
  notes_updated: 4,
  concepts_created: 2,
  resources_added: 1,
  bookmarks_added: 0,
  links_created: 3,
  notes_published: 0,
  documents_added: 1,
  interactions: 12,
  most_used_tags: [
    { key: 't-architecture', label: 'architecture', count: 7 },
    { key: 't-retrieval', label: 'retrieval', count: 3 },
  ],
  most_active_concepts: [
    { key: 'c-embeddings', label: 'Embeddings', count: 5 },
    { key: 'c-chunking', label: 'Chunking', count: 2 },
  ],
  top_tags: [],
  notes_by_status: {},
  range: RANGE,
}

/* ------------------------------------------------------------ empty payloads */

/**
 * The empty case, and the one the spec is most opinionated about: every rate,
 * score and comparison is `null` rather than `0`, and each of those carries the
 * reason it could not be computed. `tasks_completed: 0` in the one total is the
 * case that historically produced `Infinity%` or `+NaN%`.
 */
const EMPTY_OVERVIEW: OverviewRead = {
  range: RANGE,
  previous_range: PREVIOUS_RANGE,
  stale: false,
  is_stale: false,
  aggregates_through: null,
  data_as_of: null,
  totals: [
    {
      label: 'tasks_completed',
      current: 0,
      previous: 0,
      absolute_change: null,
      percent_change: null,
    },
  ],
  productivity: {
    ...PRODUCTIVITY,
    score: null,
    available: false,
    reason_if_unavailable: 'No task has been completed in this window.',
    components: [],
    range: null,
  },
  deadlines: {
    available: false,
    reason_if_unavailable: 'No task in this window carries a due date.',
    on_time: 0,
    late: 0,
    still_overdue: 0,
    adherence_rate: null,
    rate: null,
    overdue_open: 0,
    total_considered: 0,
    range: null,
    components: [],
  },
  consistency: null,
  focus: null,
  estimation: null,
  workload: null,
  daily: [],
  reason_if_empty: 'Nothing has been recorded in this window yet.',
}

const EMPTY_PRODUCTIVITY: ProductivityRead = {
  ...PRODUCTIVITY,
  score: null,
  available: false,
  reason_if_unavailable: 'No task has been completed in this window.',
  components: [],
  range: null,
}

const EMPTY_CONSISTENCY: ConsistencyRead = {
  ...CONSISTENCY,
  score: null,
  available: false,
  reason_if_unavailable: 'No day in this window has recorded activity.',
  active_days: 0,
  window_days: 7,
  work_sessions: 0,
  session_count: 0,
  active_day_ratio: null,
  longest_streak: 0,
  current_streak: 0,
  components: [],
  range: null,
}

const EMPTY_FOCUS: FocusRead = {
  ...FOCUS,
  score: null,
  available: false,
  reason_if_unavailable: 'No work session has been completed in this window.',
  avg_session_minutes: null,
  completed_planned_sessions: 0,
  interruptions: 0,
  reschedules: 0,
  focused_minutes: 0,
  total_minutes: 0,
  components: [],
  range: null,
}

const EMPTY_ESTIMATION: EstimationAccuracyRead = {
  ...ESTIMATION,
  available: false,
  reason_if_unavailable: 'No task carries both an estimate and tracked time.',
  sample_count: 0,
  pairs_compared: 0,
  absolute_error: null,
  mean_absolute_error: null,
  percentage_error: null,
  mean_percentage_error: null,
  bias: null,
  median_error: null,
  under_estimation_rate: null,
  over_estimation_rate: null,
  underestimation_rate: null,
  overestimation_rate: null,
  range: null,
}

const EMPTY_TIME: TimeDistributionRead = {
  total_minutes: 0,
  available: false,
  reason_if_unavailable: 'No work session has been completed in this window.',
  unassigned_minutes: 0,
  project_id: null,
  by_project: [],
  by_task: [],
  slices: [],
  range: null,
}

const EMPTY_TASK_ANALYTICS: TaskAnalyticsRead = {
  ...TASK_ANALYTICS,
  total_tasks: 0,
  completed_tasks: 0,
  open_tasks: 0,
  overdue_tasks: 0,
  cancelled_tasks: 0,
  blocked_tasks: 0,
  completion_rate: null,
  overdue_rate: null,
  avg_completion_days: null,
  tasks_created: 0,
  tasks_completed: 0,
  tasks_cancelled: 0,
  tasks_blocked: 0,
  tasks_overdue: 0,
  tasks_rescheduled: 0,
  available: false,
  reason_if_unavailable: 'No task has been recorded in this window.',
  top_overdue: [],
  by_status: {},
  by_priority: {},
  range: null,
}

const EMPTY_DEADLINES: DeadlineAdherenceRead = {
  available: false,
  reason_if_unavailable: 'No task in this window carries a due date.',
  on_time: 0,
  late: 0,
  still_overdue: 0,
  adherence_rate: null,
  rate: null,
  overdue_open: 0,
  total_considered: 0,
  range: null,
  components: [],
}

const EMPTY_LEARNING: LearningAnalyticsRead = {
  available: false,
  reason_if_unavailable: 'No calendar event in this window is typed study.',
  study_events: 0,
  study_minutes: 0,
  knowledge_linked_tasks: null,
  knowledge_interactions: 0,
  notes_created: 0,
  notes_updated: 0,
  projects_touched: 0,
  basis: 'Calendar events typed study, plus every recorded knowledge event.',
  definition: 'Learning activity is what you have recorded, not an assessment of what you learned.',
  range: null,
}

const EMPTY_KNOWLEDGE: KnowledgeAnalyticsRead = {
  available: false,
  reason_if_unavailable: 'No note, concept or link has been written in this window.',
  notes_created: 0,
  notes_updated: 0,
  concepts_created: 0,
  resources_added: 0,
  bookmarks_added: 0,
  links_created: 0,
  notes_published: 0,
  documents_added: 0,
  interactions: 0,
  most_used_tags: [],
  most_active_concepts: [],
  top_tags: [],
  notes_by_status: {},
  range: null,
}

/* ---------------------------------------------------------------- the stub */

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/**
 * `Page[T]`, the envelope `GET /analytics/projects` speaks.
 *
 * The route returns the rows under `items` with the counters under `meta`
 * rather than a bare array; `PROJECTS_PAGE_LIMIT` is the `meta.limit` a live
 * call comes back with when the client sends none.
 */
const PROJECTS_PAGE_LIMIT = 20

function projectsPage(items: ProjectAnalyticsRead[]): Response {
  return json({ items, meta: { total: items.length, limit: PROJECTS_PAGE_LIMIT, offset: 0 } })
}

function envelope(code: string, message: string, status: number, requestId: string): Response {
  const body: ApiErrorEnvelope = {
    error: { code, message, details: null, request_id: requestId },
  }
  return json(body, status)
}

type Route = (url: string) => Response | Promise<Response>

/** Every URL the stub answered, in order, so call counts can be asserted. */
type Calls = string[]

/**
 * The full set of analytics responses the page can ask for. Every endpoint is
 * distinct enough that first match is the only match, and a test can replace
 * one of them without restating the other eleven.
 */
function fullBackend(): Record<string, Route> {
  return {
    '/auth/me': () => json(USER),
    '/analytics/overview': () => json(OVERVIEW),
    '/analytics/productivity': () => json(PRODUCTIVITY),
    '/analytics/consistency': () => json(CONSISTENCY),
    '/analytics/focus': () => json(FOCUS),
    '/analytics/estimation': () => json(ESTIMATION),
    '/analytics/time': () => json(TIME),
    '/analytics/series': () => json(DAILY),
    '/analytics/projects': () => projectsPage(PROJECTS),
    '/analytics/tasks': () => json(TASK_ANALYTICS),
    '/analytics/deadlines': () => json(DEADLINES),
    '/analytics/learning': () => json(LEARNING),
    '/analytics/knowledge': () => json(KNOWLEDGE),
    '/analytics/trends': () => json(TRENDS),
  }
}

/** The same endpoints with nothing recorded in the window. */
function emptyBackend(): Record<string, Route> {
  return {
    '/auth/me': () => json(USER),
    '/analytics/overview': () => json(EMPTY_OVERVIEW),
    '/analytics/productivity': () => json(EMPTY_PRODUCTIVITY),
    '/analytics/consistency': () => json(EMPTY_CONSISTENCY),
    '/analytics/focus': () => json(EMPTY_FOCUS),
    '/analytics/estimation': () => json(EMPTY_ESTIMATION),
    '/analytics/time': () => json(EMPTY_TIME),
    '/analytics/series': () => json([]),
    '/analytics/projects': () => projectsPage([]),
    '/analytics/tasks': () => json(EMPTY_TASK_ANALYTICS),
    '/analytics/deadlines': () => json(EMPTY_DEADLINES),
    '/analytics/learning': () => json(EMPTY_LEARNING),
    '/analytics/knowledge': () => json(EMPTY_KNOWLEDGE),
    '/analytics/trends': () => json([]),
  }
}

function installBackend(overrides: Record<string, Route> = {}): Calls {
  const routes = Object.entries({ ...fullBackend(), ...overrides })

  const calls: Calls = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      calls.push(url)
      for (const [fragment, handler] of routes) {
        if (url.includes(fragment)) return handler(url)
      }
      return envelope('not_found', 'No stub matched this request.', 404, 'req-unmatched')
    }),
  )
  return calls
}

function renderAnalytics(entry: string = WINDOW_QUERY) {
  const router = createMemoryRouter([{ path: '/analytics', element: <AnalyticsPage /> }], {
    initialEntries: [entry],
  })
  const tree = (
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>
  )
  const result = render(tree)
  return { ...result, router }
}

/** Lets every in-flight fetch and its re-render settle before asserting. */
async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50))
  })
}

function analyticsCalls(calls: Calls, fragment: string): string[] {
  return calls.filter((url) => url.includes(`/analytics/${fragment}`))
}

/**
 * The four strings the spec names by hand. Every tab is scanned for them,
 * because a formatter that guards one card is not evidence about the other
 * seven — the claim is about the page's rendered text, not about one helper.
 */
function expectNoBrokenNumbers(): void {
  const text = document.body.textContent ?? ''
  expect(text).not.toMatch(/NaN/)
  expect(text).not.toMatch(/Infinity/)
  expect(text).not.toMatch(/undefined%/)
  expect(text).not.toMatch(/0% productivity/i)
}

/**
 * The eight tabs the brief names, in the order it names them, with the sections
 * each one is expected to leave on the page. An inactive panel is unmounted and
 * hidden, so these heading lists are what the tab on screen actually owns.
 */
const TABS = [
  {
    label: 'Overview',
    value: 'overview',
    h2: [
      'Tasks completed per day',
      'Tasks completed against the previous period',
      'Recorded time',
      'Active days',
      'Consistency score',
      'Focus score',
      'All compared totals',
    ],
    h3: [],
  },
  {
    label: 'Productivity',
    value: 'productivity',
    h2: ['Productivity score', 'Estimation accuracy', 'Consistency score', 'Focus score'],
    h3: [],
  },
  {
    label: 'Time',
    value: 'time',
    h2: ['Where the time went', 'By task', 'Planned against recorded'],
    h3: [],
  },
  { label: 'Projects', value: 'projects', h2: ['Completion by project'], h3: ['Atlas', 'Beacon'] },
  {
    label: 'Tasks',
    value: 'tasks',
    h2: ['Tasks by status', 'Tasks by priority', 'Currently overdue'],
    h3: [],
  },
  {
    label: 'Deadlines',
    value: 'deadlines',
    h2: ['On time against late', 'Currently overdue'],
    h3: [],
  },
  { label: 'Learning', value: 'learning', h2: ['What these numbers mean'], h3: [] },
  {
    label: 'Knowledge',
    value: 'knowledge',
    // One rank list, not two. `most_active_concepts` and `top_tags` are declared
    // on the response and never filled by the service, so a panel over them can
    // only print "No concept has been touched in this window" — a claim the
    // backend never made, directly under a card counting the concepts created.
    h2: ['Most used tags'],
    h3: [],
  },
] as const

beforeEach(() => {
  // `AppProviders` mounts a shared singleton client, so a cached page from the
  // previous test would answer this one before the stub ever saw the request.
  queryClient.clear()
  window.localStorage.clear()
  useAuthStore.setState({
    accessToken: 'access-token',
    refreshToken: 'refresh-token',
    user: USER,
    status: 'authenticated',
    pending: false,
    error: null,
  })
})

describe('analytics page', () => {
  it('offers the eight sections the brief names, with Overview open and no other', async () => {
    installBackend()
    renderAnalytics()

    const tablist = screen.getByRole('tablist')
    expect(
      within(tablist)
        .getAllByRole('tab')
        .map((tab) => tab.textContent),
    ).toEqual([
      'Overview',
      'Productivity',
      'Time',
      'Projects',
      'Tasks',
      'Deadlines',
      'Learning',
      'Knowledge',
    ])

    // The default tab needs no `?tab=` in the URL, so the bare page and the
    // Overview section are the same link.
    expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getAllByRole('tabpanel')).toHaveLength(1)

    // The window is part of the URL rather than component state, so this view is
    // shareable, and the bare entry above already resolves to the pinned window.
    expect(screen.getAllByText(WINDOW_LABEL).length).toBeGreaterThan(0)
    await screen.findByText('78')
  })

  it('reaches every tab and renders the sections that belong to it', async () => {
    const user = userEvent.setup()
    installBackend()
    const { router } = renderAnalytics()
    await screen.findByText('78')

    for (const tab of TABS.slice(1)) {
      await user.click(screen.getByRole('tab', { name: tab.label }))

      await waitFor(() =>
        expect(screen.getByRole('tab', { name: tab.label })).toHaveAttribute(
          'aria-selected',
          'true',
        ),
      )
      // A non-default tab is a URL, and it is written *alongside* the window
      // rather than over it — changing section must not change which days are
      // on screen.
      await waitFor(() =>
        expect(router.state.location.search).toBe(
          `?range=custom&start=${WINDOW_START}&end=${WINDOW_END}&tab=${tab.value}`,
        ),
      )

      // Exactly one panel is exposed, and it is the tab that was asked for.
      expect(screen.getAllByRole('tabpanel')).toHaveLength(1)
      await screen.findByRole('heading', { level: 2, name: tab.h2[0] })

      expect(
        screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent),
      ).toEqual(tab.h2)
      // `queryAll`, because a tab with no nested cards has no `h3` at all and
      // that absence is part of the claim.
      expect(
        screen.queryAllByRole('heading', { level: 3 }).map((heading) => heading.textContent),
      ).toEqual([...tab.h3])
      // No tab fell back to its error surface on the way.
      expect(screen.queryByRole('button', { name: /Retry/i })).not.toBeInTheDocument()
    }

    // Overview is the tab the URL leaves implicit, so choosing it again clears
    // the parameter rather than writing `?tab=overview`.
    await user.click(screen.getByRole('tab', { name: 'Overview' }))
    await waitFor(() =>
      expect(router.state.location.search).toBe(
        `?range=custom&start=${WINDOW_START}&end=${WINDOW_END}`,
      ),
    )
  })

  it('leads the Overview tab with the five headline figures over the panels behind them', async () => {
    installBackend()
    renderAnalytics()
    await screen.findByText('78')

    // One masthead, and the window it covers is stated above it rather than
    // inferred from the charts.
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(screen.getByRole('heading', { level: 1, name: 'Analytics' })).toBeInTheDocument()
    expect(screen.getAllByText(WINDOW_LABEL).length).toBeGreaterThan(0)

    // Every day in the window has an aggregate row and they run to its last
    // day, so the freshness badge is a real claim rather than a default — and
    // the banner says what it actually knows (a row per day) rather than
    // claiming the days were calculated, which it cannot see. It also warns
    // that recalculating can still move the figures, because it can.
    expect(screen.getByText('Up to date')).toBeInTheDocument()
    expect(screen.getByText('Updated just now')).toBeInTheDocument()
    expect(
      screen.getByText(
        `A daily aggregate exists for each of the 7 days in this window, through ${WINDOW_END}. ` +
          'Recalculating rebuilds them from your recorded activity, so these figures can still change.',
      ),
    ).toBeInTheDocument()

    const score = screen.getByText('78')
    const scoreCard = score.closest('div.rounded-lg') as HTMLElement
    const row = scoreCard.parentElement as HTMLElement

    // Five figures share one row, and the score leads it. The page says how the
    // score was arrived at rather than only printing it — behind the info button
    // on the score card, the backend's formula and its disclaimer.
    expect(row).toHaveClass('xl:grid-cols-5')
    expect(
      screen.getByRole('button', { name: 'More about Productivity score' }),
    ).toBeInTheDocument()

    // 8 completed against 5 the period before: +3, which is 60% of 5.
    expect(within(row).getByText('Tasks completed')).toBeInTheDocument()
    expect(within(row).getByText('8')).toBeInTheDocument()
    // `MetricCard` prints the comparison as two siblings — a decorative arrow and
    // the sentence beside it — so the sentence is what is matched here, and the
    // arrow is asserted as the span that immediately precedes it.
    const tasksDelta = within(row).getByText('up 3 tasks (+60%) from the previous period')
    expect(tasksDelta.previousElementSibling).toHaveTextContent('↑')

    // 135 recorded minutes, up 45 from 90 — one 45-minute session. The change is
    // rendered as a duration rather than a bare count, because the column it is
    // compared across is minutes.
    expect(within(row).getByText('Work time')).toBeInTheDocument()
    expect(within(row).getByText('2h 15m')).toBeInTheDocument()
    const workTimeDelta = within(row).getByText('up 45m (+50%) from the previous period')
    expect(workTimeDelta.previousElementSibling).toHaveTextContent('↑')

    // 6 of 8 finished inside the window.
    expect(within(row).getByText('Deadline adherence')).toBeInTheDocument()
    expect(within(row).getByText('75%')).toBeInTheDocument()
    expect(within(row).getByText('6 on time, 2 late')).toBeInTheDocument()

    // 5 open tasks, 600 scheduled minutes against 800 declared = 75%.
    expect(within(row).getByText('Current workload')).toBeInTheDocument()
    expect(within(row).getByText('5')).toBeInTheDocument()
    expect(
      within(row).getByText('2 high priority · 1 overdue · 75% of declared time'),
    ).toBeInTheDocument()

    // Two of the five compared figures rose, so each carries the arrow its
    // sentence already explains — the words are what a colour-blind reader gets.
    expect(within(row).getAllByText('↑')).toHaveLength(2)

    // The trend, distribution and heatmap panels below the row. Each decides to
    // plot from the data it was handed rather than falling back to an empty
    // axis, and the bars themselves remain a recharts detail.
    const trend = screen.getByRole('heading', {
      name: 'Tasks completed against the previous period',
    })
    expect(trend.closest('div.rounded-lg')).toHaveTextContent(WINDOW_LABEL)
    expect(
      within(trend.closest('div.rounded-lg') as HTMLElement).queryByText('Not enough activity yet'),
    ).not.toBeInTheDocument()

    const perDay = screen.getByRole('heading', { name: 'Tasks completed per day' })
    expect(
      within(perDay.closest('div.rounded-lg') as HTMLElement).queryByText(
        'Not enough activity yet',
      ),
    ).not.toBeInTheDocument()

    const recorded = screen.getByRole('heading', { name: 'Recorded time' })
    expect(recorded.closest('div.rounded-lg')).toHaveTextContent(
      `${WINDOW_LABEL} · actual minutes against planned`,
    )

    // The heatmap is a colour grid, so the sentence beside it is the claim: four
    // of the seven days had a completed task or a work session on them. Matched
    // on its shape rather than word for word, because the page hands `Heatmap` a
    // `valueName` of "recorded events" while the component's own sentence already
    // reads "with recorded {valueName}" — so the noun is repeated in the markup.
    // That is a call-site wording bug (src/pages/analytics-page.tsx:419, against
    // the usage documented at heatmap.tsx:23), left for its author; pinning it
    // here would make this test assert the duplication.
    const heatmap = screen.getByRole('heading', { name: 'Active days' })
    const heatmapSummary = within(heatmap.closest('div.rounded-lg') as HTMLElement).getByText(
      (_text, element) =>
        element?.tagName === 'P' &&
        /^4 days with .+ in the last 7$/.test(element.textContent ?? ''),
    )
    expect(heatmapSummary).toBeInTheDocument()

    // Every compared total, so the two headline figures can be traced to the
    // aggregate columns they are summed from. The response carries exactly two
    // rows, and this table prints both verbatim rather than summarising them.
    const totals = screen.getByRole('heading', { name: 'All compared totals' })
    const totalsCard = totals.closest('div.rounded-lg') as HTMLElement
    expect(within(totalsCard).getByText('Tasks completed')).toBeInTheDocument()
    expect(within(totalsCard).getByText('Time recorded')).toBeInTheDocument()
    expect(within(totalsCard).getByText('2h 15m')).toBeInTheDocument()
    // Here the arrow and the sentence are one string, unlike the headline cards.
    expect(
      within(totalsCard).getByText('↑ up 3 (+60%) from the previous period'),
    ).toBeInTheDocument()
    // The minutes row renders the unit exactly once. It used to read "up 45m
    // minutes", because this table handed `formatDelta` both `unit: 'minutes'`
    // and a `format` that already renders minutes — and `formatDelta` appends
    // the unit *after* the formatted number. Fixed at
    // src/pages/analytics-page.tsx:576 by dropping `unit`, so the sentence is
    // now asserted word for word rather than around the duplication.
    expect(
      within(totalsCard).getByText('↑ up 45m (+50%) from the previous period'),
    ).toBeInTheDocument()
    // Guard the specific regression: the unit must not be spelled twice.
    expect(totalsCard.textContent).not.toContain('45m minutes')
  })

  it('splits the recorded time into a donut whose legend carries the shares', async () => {
    const user = userEvent.setup()
    installBackend()
    renderAnalytics()
    await screen.findByText('78')

    await user.click(screen.getByRole('tab', { name: 'Time' }))
    await screen.findByRole('heading', { name: 'Where the time went' })

    const byProject = screen
      .getByRole('heading', { name: 'Where the time went' })
      .closest('div.rounded-lg') as HTMLElement

    // The centre carries the one number the shape can hold: 135 minutes.
    expect(within(byProject).getByText('2h 15m')).toBeInTheDocument()

    // 90 of 135 is 66.67%, 45 of 135 is 33.33%; the legend states both, so the
    // chart reads without hovering and the shares can be added up.
    expect(within(byProject).getByText('Atlas')).toBeInTheDocument()
    expect(within(byProject).getByText('1h 30m')).toBeInTheDocument()
    expect(within(byProject).getByText('67%')).toBeInTheDocument()
    expect(within(byProject).getByText('Beacon')).toBeInTheDocument()
    expect(within(byProject).getByText('45m')).toBeInTheDocument()
    expect(within(byProject).getByText('33%')).toBeInTheDocument()

    // The same window read by task, which is the other question the brief asks
    // of time distribution.
    const byTask = screen
      .getByRole('heading', { name: 'By task' })
      .closest('div.rounded-lg') as HTMLElement
    expect(within(byTask).getByText('Draft the migration plan')).toBeInTheDocument()
    expect(within(byTask).getByText('Rotate the API keys')).toBeInTheDocument()

    // And the planned-against-recorded series, plotted from the daily rows.
    const series = screen
      .getByRole('heading', { name: 'Planned against recorded' })
      .closest('div.rounded-lg') as HTMLElement
    expect(series).toHaveTextContent(WINDOW_LABEL)
    expect(within(series).queryByText('Not enough activity yet')).not.toBeInTheDocument()
  })

  it('re-reads the analytics endpoints over the window a newly applied range asks for', async () => {
    const calls = installBackend()
    renderAnalytics()
    await screen.findByText('78')

    const initial = analyticsCalls(calls, 'overview')
    expect(initial).toHaveLength(1)
    expect(initial[0]).toContain(`start_date=${WINDOW_START}`)
    expect(initial[0]).toContain(`end_date=${WINDOW_END}`)
    expect(initial[0]).toContain('granularity=day')

    // Move the window through the same control a reader uses. The picker holds
    // its draft until Apply, so a request is issued once for the new window
    // rather than once per keystroke.
    fireEvent.change(screen.getByLabelText('From'), { target: { value: NEXT_START } })
    fireEvent.change(screen.getByLabelText('To'), { target: { value: NEXT_END } })
    await userEvent.setup().click(screen.getByRole('button', { name: 'Apply range' }))

    await waitFor(() => expect(analyticsCalls(calls, 'overview')).toHaveLength(2))
    await settle()

    // Both ends moved, and the overview is not the only read that followed the
    // window: the trend the Overview tab draws is a separate endpoint over the
    // same seven days, so a stale pair would put last month's line beside this
    // month's totals.
    const overviewCalls = analyticsCalls(calls, 'overview')
    expect(overviewCalls[1]).toContain(`start_date=${NEXT_START}`)
    expect(overviewCalls[1]).toContain(`end_date=${NEXT_END}`)
    expect(overviewCalls[1]).toContain('granularity=day')

    const trendCalls = analyticsCalls(calls, 'trends')
    expect(trendCalls).toHaveLength(2)
    expect(trendCalls[1]).toContain(`start_date=${NEXT_START}`)
    expect(trendCalls[1]).toContain(`end_date=${NEXT_END}`)
    expect(trendCalls[1]).toContain('metric=tasks_completed')

    // And the page says which window it is now showing, rather than redrawing
    // the old figures silently.
    expect(screen.getAllByText(NEXT_LABEL).length).toBeGreaterThan(0)
    expect(screen.queryByText(WINDOW_LABEL)).not.toBeInTheDocument()
  })

  it('never asks for a tab it has not opened, and reloads nothing else', async () => {
    const user = userEvent.setup()
    const calls = installBackend()
    renderAnalytics()
    await screen.findByText('78')
    await settle()

    // Opening the page costs the Overview tab's two reads and nothing else.
    // Every other tab is passed an `enabled: false` until it is opened, so its
    // endpoint must be absent from the log rather than merely unused.
    const unopened = [
      'productivity',
      'consistency',
      'focus',
      'estimation',
      'time',
      'series',
      'projects',
      'tasks',
      'deadlines',
      'learning',
      'knowledge',
    ]
    expect(analyticsCalls(calls, 'overview')).toHaveLength(1)
    expect(analyticsCalls(calls, 'trends')).toHaveLength(1)
    for (const fragment of unopened) {
      expect(analyticsCalls(calls, fragment)).toEqual([])
    }

    // Opening Projects fetches Projects — and only Projects.
    await user.click(screen.getByRole('tab', { name: 'Projects' }))
    await screen.findByRole('heading', { name: 'Completion by project' })
    await settle()

    expect(analyticsCalls(calls, 'projects')).toHaveLength(1)
    expect(analyticsCalls(calls, 'projects')[0]).toContain(`start_date=${WINDOW_START}`)
    for (const fragment of unopened.filter((entry) => entry !== 'projects')) {
      expect(analyticsCalls(calls, fragment)).toEqual([])
    }

    // The brief's "do not reload unrelated parts of the application": switching
    // a tab touches no task, activity, knowledge or session endpoint, and does
    // not re-verify the session the shell already verified on mount.
    const nonAnalytics = calls.filter((url) => !url.includes('/analytics/'))
    expect(nonAnalytics.every((url) => url.includes('/auth/me'))).toBe(true)
    expect(nonAnalytics.filter((url) => url.includes('/auth/me'))).toHaveLength(1)
  })

  it('shows the busy state for the window in flight, then the figures', async () => {
    let release: (() => void) | null = null
    const pending = new Promise<Response>((resolve) => {
      release = () => resolve(json(OVERVIEW))
    })
    installBackend({ '/analytics/overview': () => pending })
    renderAnalytics()

    // The masthead, the window control and the tab list paint immediately: a
    // blank page while the analytics load is what the busy state exists to
    // avoid.
    expect(screen.getByRole('heading', { level: 1, name: 'Analytics' })).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Date range' })).toBeInTheDocument()
    expect(screen.getAllByRole('tab')).toHaveLength(8)

    // The label is rendered twice on purpose — once for the spinner, once for
    // the reader — so the busy region is pinned by the text it announces.
    expect(screen.getAllByText('Loading your window').length).toBeGreaterThan(0)
    expect(screen.queryByText('Productivity score')).not.toBeInTheDocument()
    expect(screen.queryByText('Up to date')).not.toBeInTheDocument()

    // Releasing the request hands the tab over to the real figures.
    await act(async () => {
      release?.()
    })
    expect(await screen.findByText('78')).toBeInTheDocument()
    expect(screen.getByText('Up to date')).toBeInTheDocument()
  })

  it('reports a failed analytics read with a retryable error state', async () => {
    installBackend({
      '/analytics/overview': () =>
        envelope('internal_error', 'The analytics engine is unavailable.', 500, 'req-analytics-1'),
    })
    renderAnalytics()

    // The shared client retries a 5xx twice with a backoff, so the error
    // surface cannot arrive inside the default 5s budget.
    const alert = await screen.findByRole('alert', {}, { timeout: 20_000 })
    expect(alert).toHaveTextContent('The backend hit an unexpected error')
    expect(alert).toHaveTextContent(
      'The failure was recorded on the server. Retry, and quote the request ID below.',
    )
    expect(alert).toHaveTextContent('The analytics engine is unavailable.')
    expect(alert).toHaveTextContent('req-analytics-1')

    // A failed read is distinguishable from an empty one: no figure is invented,
    // and no empty state claims there was simply no activity.
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument()
    expect(screen.queryByText('78')).not.toBeInTheDocument()
    expect(screen.queryByText('Not enough activity yet')).not.toBeInTheDocument()
  })

  it('says "Not enough activity yet" rather than "0% productivity" for an empty window', async () => {
    installBackend(emptyBackend())
    renderAnalytics()
    await screen.findByText('No task has been completed in this window.')
    await settle()

    // Six panels on this tab have nothing to plot, and each says the same
    // honest thing rather than drawing an axis with no points.
    expect(screen.getAllByText('Not enough activity yet')).toHaveLength(6)

    // Two panels share the overview copy, and each copy is the backend's own
    // words for what it looked for.
    expect(
      screen.getAllByText(
        'Every figure on this tab is computed from recorded tasks, sessions and knowledge events. There is nothing recorded in this window yet.',
      ),
    ).toHaveLength(2)
    expect(
      screen.getByText(
        'A trend needs recorded days to plot. Each point is a day something happened, so an unrecorded stretch is a gap rather than a zero.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('No day in this window has recorded activity yet.')).toBeInTheDocument()
    // Consistency and focus were absent from the response entirely rather than
    // measured at zero, so each panel says what it did and did not receive.
    expect(screen.getByRole('heading', { name: 'Consistency' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Focus' })).toBeInTheDocument()
    expect(screen.getAllByText('This response carried no score for the window.')).toHaveLength(2)

    // The two headline metrics the backend declined carry their reasons in
    // place of the numbers.
    expect(screen.getByText('No task in this window carries a due date.')).toBeInTheDocument()

    // A real 0 against a real previous 0 is a measurement and prints; the
    // comparison has no baseline and is declined in words. It is declined twice,
    // once on the headline card and once in the totals table below it.
    expect(screen.getAllByText('no comparison with the previous period')).toHaveLength(2)
    // No recorded time and no workload are both a dash — never a 0 and never a
    // rate the data does not support.
    const workTimeCard = screen.getByText('Work time').closest('div.rounded-lg') as HTMLElement
    const workloadCard = screen
      .getByText('Current workload')
      .closest('div.rounded-lg') as HTMLElement
    expect(within(workTimeCard).getByText('—')).toBeInTheDocument()
    expect(within(workloadCard).getByText('—')).toBeInTheDocument()

    expectNoBrokenNumbers()
  })

  it('never prints NaN, Infinity or an undefined percentage on any tab', async () => {
    const user = userEvent.setup()
    installBackend(emptyBackend())
    renderAnalytics()
    await screen.findByText('No task has been completed in this window.')

    // Every tab, one at a time, with a payload in which every rate, score,
    // share and bias is `null` and every count is zero. A guard in one card is
    // not evidence about the other seven, so the scan is a page-level one.
    for (const tab of TABS) {
      await user.click(screen.getByRole('tab', { name: tab.label }))
      await waitFor(() =>
        expect(screen.getByRole('tab', { name: tab.label })).toHaveAttribute(
          'aria-selected',
          'true',
        ),
      )
      await settle()
      expectNoBrokenNumbers()
    }

    // The scan is only meaningful if the tabs really rendered: the last one
    // carries a rank list whose bars divide by the largest count, which is zero
    // here.
    expect(screen.getByRole('heading', { name: 'Most used tags' })).toBeInTheDocument()
    expect(screen.getAllByText('Not enough activity yet').length).toBeGreaterThan(0)
  })
})
