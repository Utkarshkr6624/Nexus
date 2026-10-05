import { act, render, screen, waitFor, within } from '@testing-library/react'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { queryClient } from '@/app/query-client'
import { AppProviders } from '@/app/providers'
import DashboardPage from '@/pages/dashboard-page'
import { useAuthStore } from '@/stores/auth-store'
import type { ApiErrorEnvelope, HealthResponse, User } from '@/types/api'
import type {
  ComparisonTotal,
  DailyMetricRead,
  MetricRange,
  OverviewRead,
  ProjectAnalyticsRead,
  TimeDistributionRead,
  WorkloadRead,
} from '@/types/analytics'
import type { ActivityEvent, Task } from '@/types/work'

/**
 * The dashboard as an intelligence surface, asserted at the network boundary.
 *
 * The page is mounted for real — real providers, real router, real components
 * and the real `DashboardPage` — and only `fetch` is stubbed. Every figure on
 * screen therefore comes from a body this file wrote, so a value can be checked
 * by hand: 10 tasks with 8 completed is 80%, 6 on time out of 8 considered is
 * 75%, 135 recorded minutes is 2h 15m. Nothing here asserts on chart internals
 * (a recharts `<path>` is an implementation detail); the claims are about text,
 * roles and the structure that carries the reading order.
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

const HEALTH: HealthResponse = {
  status: 'healthy',
  app: 'NEXUS',
  version: '0.1.0',
  environment: 'development',
  database: { status: 'connected', latency_ms: 1.23 },
  uptime_seconds: 3725.5,
  timestamp: '2026-01-01T00:00:00Z',
}

/**
 * A fixed seven-day window, pinned in the URL rather than left to the default
 * preset. `useAnalyticsWindow` resolves `7d` against the real today, so an
 * unpinned window would make every range caption and every staleness count
 * depend on the day the suite runs. Pinned, the figures below are the figures.
 */
const WINDOW_START = '2026-01-05'
const WINDOW_END = '2026-01-11'
const WINDOW_QUERY = `/dashboard?range=custom&start=${WINDOW_START}&end=${WINDOW_END}`

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

function day(
  metric_date: string,
  tasks_completed: number,
  planned_minutes: number,
  actual_minutes: number,
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
    work_sessions: 0,
    calendar_events: 0,
    knowledge_events: 0,
    projects_touched: 0,
    updated_at: null,
  }
}

/**
 * Seven days, five of them with something on them. The two sums the headline
 * row reads are checked in the test body: 2+1+0+3+2 = 8 tasks completed, and
 * 60+30+0+45+0+0+0 = 135 minutes recorded.
 */
const DAILY: DailyMetricRead[] = [
  day(WINDOW_START, 2, 60, 60),
  day('2026-01-06', 1, 45, 30),
  day('2026-01-07', 0, 0, 0),
  day('2026-01-08', 3, 45, 45),
  day('2026-01-09', 2, 30, 0),
  day('2026-01-10', 0, 0, 0),
  day(WINDOW_END, 0, 0, 0),
]

const TOTALS: ComparisonTotal[] = [
  { label: 'tasks_created', current: 9, previous: 6, absolute_change: 3, percent_change: 50 },
  { label: 'tasks_completed', current: 8, previous: 5, absolute_change: 3, percent_change: 60 },
  { label: 'actual_minutes', current: 135, previous: 90, absolute_change: 45, percent_change: 50 },
]

/** 24 + 19 + 18 + 17 = 78, the total the card prints. */
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

const WORKLOAD: WorkloadRead = {
  open_tasks: 5,
  high_priority_open: 2,
  overdue_open: 1,
  scheduled_minutes: 600,
  available_minutes: 800,
  // 600 scheduled against 800 declared, which is 75% exactly.
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
  totals: TOTALS,
  productivity: {
    score: 78,
    available: true,
    reason_if_unavailable: null,
    components: PRODUCTIVITY_COMPONENTS,
    formula: 'A weighted sum of four factors, clamped to 0-100.',
    label: 'Productivity score',
    disclaimer: 'A NEXUS-derived metric, not a validated measure of productivity.',
    range: RANGE,
    weight_total: 100,
  },
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
  consistency: null,
  focus: null,
  estimation: null,
  workload: WORKLOAD,
  daily: DAILY,
  reason_if_empty: null,
}

/**
 * The empty case, and the one the spec is most opinionated about: every rate,
 * score and comparison is `null` rather than `0`, and each of those carries the
 * reason it could not be computed. The single total is a zero baseline, the
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
    score: null,
    available: false,
    reason_if_unavailable: 'No task has been completed in this window.',
    components: [],
    formula: 'A weighted sum of four factors, clamped to 0-100.',
    label: 'Productivity score',
    disclaimer: 'A NEXUS-derived metric, not a validated measure of productivity.',
    range: null,
    weight_total: 100,
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

const TIME: TimeDistributionRead = {
  total_minutes: 135,
  available: true,
  reason_if_unavailable: null,
  unassigned_minutes: 0,
  project_id: null,
  // 90 of 135 is two thirds; 45 of 135 is one third.
  by_project: [
    { key: 'atlas', label: 'Atlas', minutes: 90, share: 66.7 },
    { key: 'beacon', label: 'Beacon', minutes: 45, share: 33.3 },
  ],
  by_task: [],
  slices: [],
  range: RANGE,
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
  range: RANGE,
}

/** The spec's own worked example: 10 tasks, 8 completed, so 80%. */
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
    velocity: null,
    velocity_tasks_per_week: null,
    weekly_completed: [],
    work_minutes: 90,
    estimated_minutes: 120,
    actual_minutes: 90,
    avg_task_minutes: 9,
    activity_events: 12,
    available: true,
    reason_if_unavailable: null,
    range: RANGE,
  },
]

const EMPTY_PROJECTS: ProjectAnalyticsRead[] = []

function task(id: string, title: string, status: Task['status'], due_date: string | null): Task {
  return {
    id,
    project_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    owner_id: USER.id,
    parent_id: null,
    title,
    description: null,
    status,
    priority: 'medium',
    start_date: null,
    due_date,
    estimated_minutes: null,
    actual_minutes: 0,
    completed_at: status === 'completed' ? `${due_date ?? WINDOW_END}T10:00:00Z` : null,
    position: 0,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    tag_ids: [],
    is_overdue: false,
    has_blocked_dependencies: false,
  }
}

const TASKS = [
  task('t-1', 'Draft the migration plan', 'todo', '2026-01-09'),
  task('t-2', 'Rotate the API keys', 'in_progress', '2026-01-07'),
  // Completed work is not upcoming work; the panel must not list it.
  task('t-3', 'Archive the release branch', 'completed', '2026-01-08'),
]

const EMPTY_TASKS: Task[] = []

/**
 * Two events: one two minutes old, and one whose `created_at` is not a
 * timestamp at all. The second is the case the guard on the row exists for —
 * without it `formatRelative(NaN)` returns the literal string `NaNh ago`.
 */
const ACTIVITY: ActivityEvent[] = [
  {
    id: 'e-1',
    user_id: USER.id,
    project_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    task_id: 't-1',
    event_type: 'task_completed',
    metadata: {},
    created_at: new Date(Date.now() - 2 * 60_000).toISOString(),
  },
  {
    id: 'e-2',
    user_id: USER.id,
    project_id: null,
    task_id: null,
    event_type: 'task_created',
    metadata: {},
    created_at: 'not-a-timestamp',
  },
]

const EMPTY_ACTIVITY: ActivityEvent[] = []

/**
 * One event whose `event_type` is not in `WorkEventType`, beside one that is.
 *
 * The cast is the point, not a shortcut: `activity_events.event_type` is a plain
 * `String` column with no CHECK constraint, so this is a response the server is
 * free to send today — a value added by a later release, or written by one — and
 * the client cannot assume its union is exhaustive at runtime.
 */
const UNMAPPED_ACTIVITY: ActivityEvent[] = [
  {
    id: 'e-9',
    user_id: USER.id,
    project_id: null,
    task_id: null,
    event_type: 'constellation_aligned' as ActivityEvent['event_type'],
    metadata: {},
    created_at: new Date(Date.now() - 5 * 60_000).toISOString(),
  },
]

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function envelope(
  code: string,
  message: string,
  status: number,
  requestId: string,
): Response {
  const body: ApiErrorEnvelope = {
    error: { code, message, details: null, request_id: requestId },
  }
  return json(body, status)
}

type Route = (url: string) => Response | Promise<Response>

interface Backend {
  overview?: Route
  time?: Route
  projects?: Route
  activity?: Route
  tasks?: Route
}

/** Every URL the stub answered, in order, so call counts can be asserted. */
type Calls = string[]

/** A list endpoint's envelope, with the counters the list reads require. */
function page<T>(items: T[], limit: number) {
  return json({ items, meta: { total: items.length, limit, offset: 0 } })
}

/**
 * The page size `GET /analytics/projects` applies when the client sends none —
 * the `meta.limit` a live call comes back with. The stub carries it so a test
 * that inspects the envelope reads the number the server would send.
 */
const PROJECTS_PAGE_LIMIT = 20

/**
 * Stubs `fetch` with the routing table below, letting a single test replace one
 * endpoint (the failing overview, the one that never settles) without restating
 * the other six. Every fragment is distinct, so first match is the only match.
 */
function installBackend(overrides: Backend = {}): Calls {
  const routes: [string, Route][] = [
    ['/auth/me', () => json(USER)],
    ['/health', () => json(HEALTH)],
    ['/analytics/overview', overrides.overview ?? (() => json(OVERVIEW))],
    ['/analytics/time', overrides.time ?? (() => json(TIME))],
    ['/analytics/projects', overrides.projects ?? (() => page(PROJECTS, PROJECTS_PAGE_LIMIT))],
    ['/activity', overrides.activity ?? (() => page(ACTIVITY, 6))],
    ['/tasks', overrides.tasks ?? (() => page(TASKS, 8))],
  ]

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

function renderDashboard(entry: string = WINDOW_QUERY) {
  const router = createMemoryRouter([{ path: '/dashboard', element: <DashboardPage /> }], {
    initialEntries: [entry],
  })
  const tree = (
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>
  )
  const result = render(tree)
  return { ...result, rerenderDashboard: () => result.rerender(tree) }
}

/**
 * Waits for every lazy chart boundary to have replaced its `aria-busy`
 * placeholder. The dashboard mounts Recharts behind `React.lazy`, so a
 * heading-based wait can succeed against the fallback frame and leave the real
 * body unasserted; "no busy regions left" is the honest signal that the charts
 * have landed.
 */
async function waitForPanels(): Promise<void> {
  await waitFor(() =>
    expect(document.querySelectorAll('[aria-busy="true"]')).toHaveLength(0),
  )
}

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

/** The component breakdown the score card prints on one line, verbatim. */
const PRODUCTIVITY_COMPONENTS_LINE =
  'Completion 24/30 · Consistency 19/25 · Deadline rate 18/25 · Focus time 17/20'

describe('dashboard intelligence surface', () => {
  it('renders the five headline metrics with their values and comparisons', async () => {
    installBackend()
    renderDashboard()

    // Every value is read inside the headline row rather than page-wide: the
    // panels below repeat some of these figures (135 minutes is both the row's
    // work time and the donut's centre), and the claim under test is about the
    // row.
    const score = await screen.findByText('78')
    const row = score.closest('div.rounded-lg')?.parentElement as HTMLElement

    // The score leads, and says how it was arrived at rather than only what it is.
    expect(score.parentElement).toHaveTextContent('78/ 100')
    expect(screen.getByText('Productivity score')).toBeInTheDocument()
    expect(screen.getByText(PRODUCTIVITY_COMPONENTS_LINE)).toBeInTheDocument()

    // 8 completed against 5 the period before: +3, which is 60% of 5.
    expect(screen.getByText('Tasks completed')).toBeInTheDocument()
    expect(within(row).getByText('8')).toBeInTheDocument()
    // Both compared figures rose, so the arrow appears on each of them; the
    // sentence beside it, not the arrow or the tint, is what carries the claim.
    expect(within(row).getAllByText('↑')).toHaveLength(2)
    expect(screen.getByText('up 3 tasks (+60%) from the previous period')).toBeInTheDocument()

    // 135 recorded minutes, up 45 from 90 — one 45-minute session.
    expect(screen.getByText('Work time')).toBeInTheDocument()
    expect(within(row).getByText('2h 15m')).toBeInTheDocument()
    expect(screen.getByText('up 45m (+50%) from the previous period')).toBeInTheDocument()

    // 6 of 8 finished inside the window.
    expect(screen.getByText('Deadline adherence')).toBeInTheDocument()
    expect(within(row).getByText('75%')).toBeInTheDocument()
    expect(screen.getByText('6 on time · 2 late')).toBeInTheDocument()

    // 5 open tasks, 600 scheduled minutes against 800 declared = 75%.
    expect(screen.getByText('Current workload')).toBeInTheDocument()
    expect(within(row).getByText('5')).toBeInTheDocument()
    expect(screen.getByText('2 high priority · 75% of declared time')).toBeInTheDocument()

    await waitForPanels()
  })

  it('keeps one distinct headline row above the panels, not a grid of equal cards', async () => {
    installBackend()
    renderDashboard()

    const score = await screen.findByText('78')

    // Its card takes two of the row's six columns and the other four take one
    // each, which is the "20 tiny cards" hierarchy stated as layout.
    const scoreCard = score.closest('div.rounded-lg')
    expect(scoreCard).toHaveClass('xl:col-span-2')
    const row = scoreCard?.parentElement as HTMLElement
    expect(row).toHaveClass('xl:grid-cols-6')
    expect(row.children).toHaveLength(5)

    // The score is the one figure set larger than everything else on the page,
    // and the other four share the size just below it.
    expect(within(row).getByText('78')).toHaveClass('text-4xl')
    for (const value of ['8', '2h 15m', '75%', '5']) {
      expect(within(row).getByText(value)).toHaveClass('text-3xl')
    }

    // One masthead, and the outline below it is the seven panels in reading
    // order. The five headline figures are deliberately not headings: promoting
    // them would put five more top-level names above the page's own title.
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    await waitForPanels()
    expect(
      screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent),
    ).toEqual([
      'Activity',
      'Where the time went',
      'Tasks completed per day',
      'Upcoming deadlines',
      'Project performance',
      'Recent activity',
      'Backend health',
    ])
    expect(screen.queryByRole('heading', { name: 'Tasks completed' })).not.toBeInTheDocument()
  })

  it('renders each lower panel with its title and the recorded data behind it', async () => {
    installBackend()
    renderDashboard()

    await waitForPanels()

    // The completion trend plots the same daily rows the headline count is
    // summed from, so with data present it draws the series rather than falling
    // back to the empty state. The bars themselves are a recharts detail; the
    // claim is that the panel chose to plot.
    const completion = screen.getByRole('heading', { name: 'Tasks completed per day' })
    const completionCard = completion.closest('div.rounded-lg') as HTMLElement
    expect(
      within(completionCard).queryByText('Not enough activity yet'),
    ).not.toBeInTheDocument()

    // Time distribution: 90 of 135 minutes is two thirds of the tracked time.
    const timePanel = screen.getByRole('heading', { name: 'Where the time went' })
    const timeCard = timePanel.closest('div.rounded-lg') as HTMLElement
    expect(within(timeCard).getByText('Atlas')).toBeInTheDocument()
    expect(within(timeCard).getByText('1h 30m')).toBeInTheDocument()
    expect(within(timeCard).getByText('67%')).toBeInTheDocument()
    expect(within(timeCard).getByText('Beacon')).toBeInTheDocument()
    expect(within(timeCard).getByText('45m')).toBeInTheDocument()
    expect(within(timeCard).getByText('33%')).toBeInTheDocument()

    // Project performance: 8 of 10 tasks is 80%, and the bar carries the same
    // figure as its accessible name rather than colour alone.
    const projectCard = screen
      .getByRole('heading', { name: 'Project performance' })
      .closest('div.rounded-lg') as HTMLElement
    expect(within(projectCard).getByText('Atlas')).toBeInTheDocument()
    expect(within(projectCard).getByText('8/10 · 1h 30m · 80%')).toBeInTheDocument()
    expect(within(projectCard).getByLabelText('Atlas completion rate')).toBeInTheDocument()

    // Upcoming deadlines lists the two open tasks and drops the completed one.
    const deadlineCard = screen
      .getByRole('heading', { name: 'Upcoming deadlines' })
      .closest('div.rounded-lg') as HTMLElement
    expect(within(deadlineCard).getByText('Draft the migration plan')).toBeInTheDocument()
    expect(within(deadlineCard).getByText('Rotate the API keys')).toBeInTheDocument()
    expect(within(deadlineCard).queryByText('Archive the release branch')).not.toBeInTheDocument()

    // Recent activity is fed by the work feed, and each row states its event.
    const activityCard = screen
      .getByRole('heading', { name: 'Recent activity' })
      .closest('div.rounded-lg') as HTMLElement
    expect(within(activityCard).getByText('Task completed')).toBeInTheDocument()
    expect(within(activityCard).getByText('Task created')).toBeInTheDocument()
  })

  it('gives every lower panel an empty state when the window holds no activity', async () => {
    installBackend({
      overview: () => json(EMPTY_OVERVIEW),
      time: () => json(EMPTY_TIME),
      projects: () => page(EMPTY_PROJECTS, PROJECTS_PAGE_LIMIT),
      activity: () => page(EMPTY_ACTIVITY, 6),
      tasks: () => page(EMPTY_TASKS, 8),
    })
    renderDashboard()

    await waitForPanels()

    // Four of the panels share one honest title and differ only in what fills
    // them, so each is pinned by its own sentence rather than by the title.
    expect(screen.getAllByText('Not enough activity yet')).toHaveLength(4)
    expect(
      screen.getByText('A trend needs recorded days to plot. Each point is a day something happened, so an unrecorded stretch is a gap rather than a zero.'),
    ).toBeInTheDocument()
    expect(
      screen.getByText('Every figure on this tab is computed from recorded tasks, sessions and knowledge events. There is nothing recorded in this window yet.'),
    ).toBeInTheDocument()
    expect(
      screen.getByText('A project appears here once it has tasks or work sessions in the window. There is no figure to show for a project with neither.'),
    ).toBeInTheDocument()

    // The time panel is the one the backend explains: its own
    // `reason_if_unavailable` is rendered word for word in place of the copy
    // above, because it names the ingredient that was missing.
    expect(
      screen.getByText('No work session has been completed in this window.'),
    ).toBeInTheDocument()

    expect(screen.getByText('Nothing due in this window')).toBeInTheDocument()
    expect(screen.getByText('No activity recorded yet')).toBeInTheDocument()

    // The backend's own reasons, verbatim, replace the numbers they explain.
    expect(screen.getByText('No task has been completed in this window.')).toBeInTheDocument()
    expect(screen.getByText('No task in this window carries a due date.')).toBeInTheDocument()
  })

  /**
 * Waits for the `ErrorState` that quotes a given request ID.
 *
 * The shared client retries a 5xx twice before giving up, so a failure surface
 * cannot arrive inside the default budget and a `findBy*` against whatever
 * alerts happen to be on screen matches an unrelated one. Keying on the request
 * ID is what makes this wait about the panel under test.
 */
async function alertForRequest(requestId: string): Promise<HTMLElement> {
  return await waitFor(
    () => {
      const match = screen
        .getAllByRole('alert')
        .find((node) => node.textContent?.includes(requestId))
      if (!match) throw new Error(`No error surface quotes ${requestId} yet.`)
      return match
    },
    { timeout: 20_000 },
  )
}

/**
 * A panel whose request failed must say so.
 *
 * An empty state is a claim about the records that exist: "nothing is due in
 * this window" asserts that the backend read the window and found no open
 * task. A 500 says nothing of the sort — the window was never read — so
 * rendering the empty state in its place tells the reader their deadlines are
 * clear when in fact nobody has looked. Each panel here is failed on its own,
 * and the rest of the page must survive each failure.
 */
  it('reports a failed panel request rather than an empty state', async () => {
    installBackend({
      tasks: () => envelope('internal_error', 'The work module is unavailable.', 500, 'req-tasks-1'),
    })
    renderDashboard()

    const deadlines = await alertForRequest('req-tasks-1')
    expect(deadlines).toHaveTextContent('Upcoming deadlines could not load')
    expect(deadlines).toHaveTextContent('The work module is unavailable.')
    // The empty state made the opposite claim, and it must not survive.
    expect(screen.queryByText('Nothing due in this window')).not.toBeInTheDocument()
    // The retry is scoped to the panel that failed, not to the whole page.
    expect(within(deadlines).getByRole('button', { name: /retry/i })).toBeEnabled()

    // Every other panel still rendered: one bad request must not blank the page.
    expect(screen.getByRole('heading', { name: 'Upcoming deadlines' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Project performance' })).toBeInTheDocument()
    // "Atlas" names a project in the performance panel and a slice in the
    // donut's legend, so it is matched by presence rather than by count.
    expect(screen.getAllByText('Atlas').length).toBeGreaterThan(0)
    expect(screen.getByText('Task completed')).toBeInTheDocument()
  })

  it('reports a failed project and activity panel on its own, not as zeroes', async () => {
    installBackend({
      projects: () =>
        envelope('internal_error', 'The rollup did not build.', 500, 'req-projects-1'),
      activity: () =>
        envelope('internal_error', 'The work feed is unavailable.', 500, 'req-activity-1'),
    })
    renderDashboard()

    const projects = await alertForRequest('req-projects-1')
    const activity = await alertForRequest('req-activity-1')

    expect(projects).toHaveTextContent('Project performance could not load')
    expect(activity).toHaveTextContent('Recent activity could not load')
    // Neither panel's empty state may stand in for the failure.
    expect(screen.queryByText('No activity recorded yet')).not.toBeInTheDocument()
    // The panel that did answer is untouched.
    expect(screen.getByText('Draft the migration plan')).toBeInTheDocument()
  })

  it('reports a failed time-distribution read rather than an empty donut', async () => {
    installBackend({
      time: () =>
        envelope('internal_error', 'The distribution query timed out.', 500, 'req-time-1'),
    })
    renderDashboard()

    const alert = await alertForRequest('req-time-1')
    expect(alert).toHaveTextContent('Where the time went could not load')
    expect(alert).toHaveTextContent('The distribution query timed out.')
    expect(
      screen.queryByText('No work session has been completed in this window.'),
    ).not.toBeInTheDocument()
  })

  it('renders an event type the vocabulary does not know instead of taking the page down', async () => {
    installBackend({ activity: () => page(UNMAPPED_ACTIVITY, 6) })
    renderDashboard()

    await waitForPanels()

    // The row is still reported — an event this build cannot interpret is a
    // record that exists, and hiding it would be a quieter lie than showing it.
    // Its spelling is turned into a sentence, never printed as `event_type`.
    const activityCard = screen
      .getByRole('heading', { name: 'Recent activity' })
      .closest('div.rounded-lg') as HTMLElement
    const row = within(activityCard).getByText('Constellation aligned').closest('li') as HTMLElement
    expect(row).toBeInTheDocument()
    expect(activityCard.textContent).not.toContain('constellation_aligned')

    // The label is the only claim made about it, and it is marked as such.
    expect(within(row).getByText(/not by a version this build knows/)).toBeInTheDocument()

    // The rest of the page is untouched: the panel beside it still rendered, so
    // one unreadable event cost a row's wording and nothing else.
    expect(screen.getByRole('heading', { name: 'Backend health' })).toBeInTheDocument()
    expect(screen.getByText('Project performance')).toBeInTheDocument()
  })

  it('renders a relative timestamp for a recorded event', async () => {
    installBackend()
    renderDashboard()

    await waitForPanels()

    const activityCard = screen
      .getByRole('heading', { name: 'Recent activity' })
      .closest('div.rounded-lg') as HTMLElement

    // The fixture's first event was written exactly two minutes ago.
    expect(within(activityCard).getByText('2m ago')).toBeInTheDocument()
  })

  it('renders nothing rather than NaN for an event with a malformed created_at', async () => {
    installBackend()
    renderDashboard()

    await waitForPanels()

    const activityCard = screen
      .getByRole('heading', { name: 'Recent activity' })
      .closest('div.rounded-lg') as HTMLElement
    const malformed = within(activityCard)
      .getByText('Task created')
      .closest('li') as HTMLElement

    // The event itself is still reported; only the timestamp it cannot render
    // is withheld. `formatRelative(NaN)` would print "NaNh ago".
    expect(malformed).toHaveTextContent('Task created')
    expect(malformed).toHaveTextContent('New task.')
    expect(malformed.textContent).not.toMatch(/NaN|ago|just now/)
  })

  it('never prints NaN, Infinity or an undefined percentage for a null metric', async () => {
    installBackend({
      overview: () => json(EMPTY_OVERVIEW),
      time: () => json(EMPTY_TIME),
      projects: () => page(EMPTY_PROJECTS, PROJECTS_PAGE_LIMIT),
    })
    renderDashboard()

    await waitForPanels()

    // A real 0 against a real previous 0: the count is a measurement, so it
    // prints, while the comparison has no baseline and is declined in words.
    expect(screen.getByText('0')).toBeInTheDocument()
    expect(screen.getByText('no comparison with the previous period')).toBeInTheDocument()

    // No recorded time and no workload are both "—", never `0` and never `NaN%`.
    const workTimeCard = screen.getByText('Work time').closest('div.rounded-lg') as HTMLElement
    const workloadCard = screen.getByText('Current workload').closest('div.rounded-lg') as HTMLElement
    expect(within(workTimeCard).getByText('—')).toBeInTheDocument()
    expect(within(workloadCard).getByText('—')).toBeInTheDocument()

    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/NaN/)
    expect(text).not.toMatch(/Infinity/)
    expect(text).not.toMatch(/undefined%/)
  })

  it('shows the headline skeleton while the overview is still in flight', async () => {
    let release: (() => void) | null = null
    const pending = new Promise<Response>((resolve) => {
      release = () => resolve(json(OVERVIEW))
    })
    installBackend({ overview: () => pending })
    const { container } = renderDashboard()

    // The masthead and the window control paint immediately — a blank page
    // while the analytics load is what the skeleton exists to avoid.
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      /Good (morning|afternoon|evening)|Still up, Ada/,
    )
    expect(screen.getByRole('group', { name: 'Date range' })).toBeInTheDocument()

    const skeleton = container.querySelector('[aria-busy="true"]')
    expect(skeleton).not.toBeNull()
    expect(skeleton?.children).toHaveLength(5)
    expect(screen.queryByText('Productivity score')).not.toBeInTheDocument()

    // Releasing the request hands the row over to the real figures.
    await act(async () => {
      release?.()
    })
    expect(await screen.findByText('78')).toBeInTheDocument()
    await waitForPanels()
  })

  it('reports a failed analytics request with the analytics error state', async () => {
    installBackend({
      overview: () =>
        envelope('internal_error', 'The analytics engine is unavailable.', 500, 'req-analytics-1'),
    })
    renderDashboard()

    // The shared client retries a 5xx twice with a backoff, so the error
    // surface cannot arrive inside the default 5s budget.
    const alert = await screen.findByRole('alert', {}, { timeout: 20_000 })
    expect(alert).toHaveTextContent('Analytics could not load')
    // The dashboard's own title replaces the generic one, but the status-derived
    // message and the backend's text both survive underneath it.
    expect(alert).toHaveTextContent(
      'The failure was recorded on the server. Retry, and quote the request ID below.',
    )
    expect(alert).toHaveTextContent('The analytics engine is unavailable.')
    expect(alert).toHaveTextContent('req-analytics-1')

    // No figure is invented for the failed request...
    expect(screen.queryByText('Productivity score')).not.toBeInTheDocument()
    // ... and the rest of the page is still usable: the health card answers.
    expect(screen.getByRole('heading', { name: 'Backend health' })).toBeInTheDocument()
    expect(screen.getByText('1h 2m')).toBeInTheDocument()
  })

  it('asks for the overview once across a re-render, for the window on screen', async () => {
    const calls = installBackend()
    const { rerenderDashboard } = renderDashboard()

    await screen.findByText('78')
    const overviewCalls = () => calls.filter((url) => url.includes('/analytics/overview'))
    expect(overviewCalls()).toHaveLength(1)
    // One window, one set of numbers: the figures below the headline are for
    // the same seven days the headline was read over.
    expect(overviewCalls()[0]).toContain(`start_date=${WINDOW_START}`)
    expect(overviewCalls()[0]).toContain(`end_date=${WINDOW_END}`)
    expect(overviewCalls()[0]).toContain('granularity=day')

    rerenderDashboard()
    await screen.findByText('78')
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50))
    })

    expect(overviewCalls()).toHaveLength(1)
  })
})
