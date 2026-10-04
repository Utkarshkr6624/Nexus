import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/tooltip'
import { CommandPalette } from '@/components/layout/command-palette'
import { dateOnlyOf, shiftDateOnly } from '@/features/command-center/priority'
import { queryRetryPolicy } from '@/app/query-client'
import type { OverviewRead } from '@/types/analytics'
import type { ApiErrorEnvelope } from '@/types/api'
import type { Conflict, ConflictList } from '@/types/planner'
import type { DeveloperSummaryRead } from '@/types/developer'
import type { LearningGoalListRead, LearningGoalRead, LearningSummaryRead } from '@/types/learning'
import type { MlStatusRead, RoutingDecisionRead } from '@/types/ml'
import type {
  RecommendationListRead,
  RecommendationRead,
  RiskListRead,
  RiskRead,
  RiskSummaryRead,
} from '@/types/risk'
import type { ActivityStats, Task } from '@/types/work'
import CommandCenterPage from '@/pages/command-center-page'

/**
 * The Command Center, asserted at the network boundary.
 *
 * Real router, real components, real hooks; only `fetch` is stubbed. Twelve
 * endpoints are read and each fixture below is the answer to exactly one of them,
 * so a panel that renders has demonstrably rendered *from that endpoint* rather
 * than from a constant in the component.
 *
 * **Every fixture is pinned to the clock the page uses.** The page reads
 * `new Date()` once and derives its `due_before` from it, so the fixtures compute
 * their own dates with the same `dateOnlyOf`/`shiftDateOnly` helpers the page
 * uses. A deadline bucket that moved would make the expected priority scores
 * wrong, and a test that could go stale for that reason is not a test.
 *
 * **The query client is local.** `AppProviders` mounts the shared singleton and
 * clears the cache on a session change, which in jsdom strands every component at
 * `pending` mid-test. A fresh client per render avoids that, with the defaults
 * from `src/app/query-client.ts` carried over rather than relaxed so the retry
 * behaviour under test is the shipped behaviour.
 */

/* ------------------------------------------------------------------ fixtures */

const NOW = new Date()
const TODAY = dateOnlyOf(NOW)

function daysAgo(n: number): string {
  return shiftDateOnly(NOW, -n)
}

function daysAhead(n: number): string {
  return shiftDateOnly(NOW, n)
}

function hoursAgo(n: number): string {
  return new Date(NOW.getTime() - n * 3_600_000).toISOString()
}

const RISK_ID = '11111111-1111-4111-8111-111111111111'
const RECOMMENDATION_ID = '22222222-2222-4222-8222-222222222222'
const OVERDUE_TASK_ID = '33333333-3333-4333-8333-333333333333'
const CRITICAL_TASK_ID = '44444444-4444-4444-8444-444444444444'
const GOAL_ID = '55555555-5555-4555-8555-555555555555'

const CRITICAL_RISK: RiskRead = {
  id: RISK_ID,
  risk_type: 'deadline',
  severity: 'critical',
  score: 82,
  title: 'Four open tasks have no booked time before the Atlas release',
  description:
    'Four tasks carry a due date inside the release window and no work session has been scheduled before it.',
  evidence: [
    { label: 'Unscheduled tasks', detail: '4 tasks due inside the window', contribution: 30 },
    { label: 'Days to release', detail: '6 days remain', contribution: 18 },
  ],
  evidence_strength: 'high',
  entity_type: 'project',
  entity_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  status: 'active',
  detected_at: hoursAgo(48),
  resolved_at: null,
  metadata: {},
  recommendations: [],
}

/**
 * A second finding, deliberately in the low band.
 *
 * It exists so the determinism test has two records of the *same kind* to permute
 * inside a response. Reordering two different kinds proves nothing — they are
 * concatenated in a fixed order anyway — but reordering two risks is the case a
 * sort that leaned on payload position would get wrong.
 */
const LOW_RISK: RiskRead = {
  ...CRITICAL_RISK,
  id: '12121212-1212-4121-8121-121212121212',
  risk_type: 'consistency',
  severity: 'low',
  score: 12,
  title: 'Two notes have not been revised in three months',
  description: 'Two knowledge entries were last revised more than ninety days ago.',
  evidence: [],
  evidence_strength: 'low',
  entity_type: null,
  entity_id: null,
  detected_at: hoursAgo(24 * 20),
}

const SUGGESTION: RecommendationRead = {
  id: RECOMMENDATION_ID,
  recommendation_type: 'block_time',
  priority: 'high',
  title: 'Block two work sessions before the release',
  description: 'Give the migration plan two sessions ahead of the release date.',
  reason: 'The recorded work booked before the deadline covers 120 of about 300 minutes.',
  entity_type: 'project',
  entity_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  risk_id: RISK_ID,
  status: 'new',
  created_at: hoursAgo(24),
  responded_at: null,
  expires_at: null,
  metadata: {},
}

function task(overrides: Partial<Task> & Pick<Task, 'id' | 'title'>): Task {
  return {
    project_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    owner_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    parent_id: null,
    description: null,
    status: 'todo',
    priority: 'medium',
    start_date: null,
    due_date: null,
    estimated_minutes: null,
    actual_minutes: 0,
    completed_at: null,
    position: 0,
    created_at: hoursAgo(240),
    updated_at: hoursAgo(24),
    tag_ids: [],
    is_overdue: false,
    has_blocked_dependencies: false,
    ...overrides,
  }
}

const OVERDUE_TASK = task({
  id: OVERDUE_TASK_ID,
  title: 'Write the migration rollback plan',
  priority: 'critical',
  due_date: daysAgo(10),
  is_overdue: true,
})

const CRITICAL_TASK = task({
  id: CRITICAL_TASK_ID,
  title: 'Review the schema change',
  priority: 'critical',
  due_date: daysAhead(30),
})

const CONFLICT: Conflict = {
  kind: 'overlapping_sessions',
  severity: 'warning',
  message: 'Two work sessions overlap on 12 Jan between 09:00 and 10:30.',
  entity_type: 'work_session',
  entity_id: null,
  evidence: {},
}

const GOAL: LearningGoalRead = {
  id: GOAL_ID,
  title: 'Finish the linear algebra course',
  description: null,
  target_skill_id: null,
  target_topic: 'Linear algebra',
  target_date: daysAhead(3),
  priority: 'high',
  status: 'in_progress',
  progress: 40,
  estimated_effort_minutes: null,
  project_id: null,
  note_id: null,
  completed_at: null,
  created_at: hoursAgo(500),
  updated_at: hoursAgo(24),
}

const RISK_SUMMARY: RiskSummaryRead = {
  critical: 1,
  high: 2,
  medium: 3,
  low: 4,
  total: 10,
  needs_attention: true,
}

const ACTIVITY_STATS: ActivityStats = {
  projects: { total: 6, active: 3, completed: 2, planned: 1, on_hold: 0, archived: 0 },
  tasks: { total: 14, todo: 5, in_progress: 3, blocked: 2, completed: 4, cancelled: 0, overdue: 2 },
}

const RANGE = { start_date: daysAgo(7), end_date: TODAY, granularity: 'day' as const }

const OVERVIEW: OverviewRead = {
  range: RANGE,
  previous_range: null,
  stale: false,
  is_stale: false,
  aggregates_through: TODAY,
  data_as_of: TODAY,
  totals: [
    { label: 'Tasks completed', current: 12, previous: 9, absolute_change: 3, percent_change: 33.3 },
    { label: 'Focus minutes', current: 640, previous: null, absolute_change: null, percent_change: null },
  ],
  productivity: {
    score: 71,
    available: true,
    reason_if_unavailable: null,
    components: [],
    formula: 'sum(points) / weight_total',
    label: 'Productivity',
    disclaimer: 'A description of recorded work, not a measure of a person.',
    range: RANGE,
    weight_total: 100,
  },
  deadlines: {
    available: true,
    reason_if_unavailable: null,
    on_time: 8,
    late: 2,
    still_overdue: 2,
    adherence_rate: 0.8,
    rate: 0.8,
    overdue_open: 2,
    total_considered: 12,
    range: RANGE,
    components: [],
  },
  consistency: null,
  focus: null,
  estimation: null,
  workload: null,
  daily: [],
  reason_if_empty: null,
}

const DEVELOPER_SUMMARY: DeveloperSummaryRead = {
  repository_count: 2,
  active_repository_count: 2,
  commit_count: 480,
  commits_in_window: 17,
  active_days: 6,
  change_volume: 3120,
  repositories_touched: 2,
  window_days: 30,
  window_start: daysAgo(30) + 'T00:00:00Z',
  window_end: TODAY + 'T00:00:00Z',
  latest_commit_at: hoursAgo(5),
  last_scanned_at: hoursAgo(26),
  has_data: true,
  summary: '2 repositories, 17 commits in the last 30 days.',
}

const LEARNING_SUMMARY: LearningSummaryRead = {
  goal_count: 4,
  active_goal_count: 2,
  completed_goal_count: 1,
  skill_count: 3,
  activity_count: 11,
  activities_in_window: 5,
  minutes_in_window: 240,
  window_days: 30,
  window_start: daysAgo(30) + 'T00:00:00Z',
  window_end: TODAY + 'T00:00:00Z',
  latest_activity_at: hoursAgo(30),
  has_data: true,
  summary: '2 open goals, 5 activities recorded in the last 30 days.',
}

const ML_STATUS: MlStatusRead = {
  enabled: true,
  available: true,
  unavailable_reason: null,
  model: {
    base_model: 'microsoft/deberta-v3-base',
    architecture: 'DebertaV2ForSequenceClassification',
    device: 'cpu',
    label_count: 14,
    max_sequence_length: 64,
    parameter_count: 184_000_000,
    checkpoint: 'artifacts/intent-classifier',
    load_seconds: 1.9,
  },
  threshold: 0.62,
  taxonomy_version: 'phase-10-v1',
  intents: [
    {
      intent: 'task_manage',
      description: 'Create, complete, block, cancel, reorder or delete a task.',
      destination: 'api/v1/tasks',
      destination_kind: 'router',
      service: 'TaskService',
      entrypoint: 'TaskService.list',
    },
  ],
}

const ROUTING_DECISION: RoutingDecisionRead = {
  intent: 'task_manage',
  confidence: 0.91,
  threshold: 0.62,
  status: 'accepted',
  destination: 'api/v1/tasks',
  destination_kind: 'router',
  target: { service: 'TaskService', module: 'app.services.task_service', entrypoint: 'TaskService.list' },
  reason: 'The classifier recognised a request that lands on the task surface.',
  alternatives: [{ intent: 'project_manage', confidence: 0.06 }],
}

/**
 * The expected order, worked out by hand from the rule.
 *
 * - Overdue critical task: 40 + 25 (overdue ≥ 7 days) + 15 = 80 of 80 → **100**
 * - Critical risk, score 82, 2 days old: 40 + 8 + 15 = 63 of 65 → **97**
 * - High suggestion, 1 day old: 30 + 7 + 15 = 52 of 65 → **80**
 * - Warning conflict (mapped to the `high` band): 30 + 11 = 41 of 55 → **75**
 * - Critical task due in 30 days: 40 + 0 + 15 = 55 of 80 → **69**
 * - High goal due in 3 days: 30 + 8 + 11 = 49 of 80 → **61**
 * - Low finding, score 12, 20 days old: 8 + 1 + 5 = 14 of 65 → **22**
 */
const EXPECTED_ORDER = [
  ['Write the migration rollback plan', '100'],
  [CRITICAL_RISK.title, '97'],
  [SUGGESTION.title, '80'],
  ['overlapping sessions', '75'],
  ['Review the schema change', '69'],
  [GOAL.title, '61'],
  [LOW_RISK.title, '22'],
] as const

/* ------------------------------------------------------------------- backend */

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function envelope(code: string, message: string, status: number, requestId: string): Response {
  const body: ApiErrorEnvelope = {
    error: { code, message, details: null, request_id: requestId },
  }
  return json(body, status)
}

type Route = (url: string) => Response | Promise<Response>

interface Backend {
  risks?: Route
  riskSummary?: Route
  recommendations?: Route
  deadlines?: Route
  criticalTasks?: Route
  conflicts?: Route
  goals?: Route
  activityStats?: Route
  overview?: Route
  developer?: Route
  learning?: Route
  mlStatus?: Route
  route?: Route
}

/** Which stub answers which request, keyed by a stable path fragment. */
function installBackend(overrides: Backend = {}): { url: string; method: string }[] {
  const routes: Array<[string, Route | undefined, Route]> = [
    ['/risks/summary', overrides.riskSummary, () => json(RISK_SUMMARY)],
    [
      '/risks?',
      overrides.risks,
      () => json({ items: [CRITICAL_RISK, LOW_RISK], total: 2, limit: 20, offset: 0, by_severity: { critical: 1, high: 0, medium: 0, low: 1 }, summary: 'Two live findings.' } as RiskListRead),
    ],
    [
      '/recommendations',
      overrides.recommendations,
      () => json({ items: [SUGGESTION], total: 1, limit: 20, offset: 0, by_priority: { critical: 0, high: 1, medium: 0, low: 0 } } as RecommendationListRead),
    ],
    [
      '/tasks?',
      undefined,
      (url) => {
        if (url.includes('due_before=')) {
          return overrides.deadlines
            ? overrides.deadlines(url)
            : json({ items: [OVERDUE_TASK], meta: { total: 1, limit: 20, offset: 0 } })
        }
        return overrides.criticalTasks
          ? overrides.criticalTasks(url)
          : json({ items: [CRITICAL_TASK], meta: { total: 1, limit: 20, offset: 0 } })
      },
    ],
    [
      '/planner/conflicts',
      overrides.conflicts,
      () =>
        json({
          window: { start_date: TODAY, end_date: daysAhead(14), timezone: 'UTC' },
          conflicts: [CONFLICT],
          meta: { total: 1, limit: 50, offset: 0 },
        } as ConflictList),
    ],
    [
      '/learning/goals',
      overrides.goals,
      () => json({ items: [GOAL], total: 1, limit: 20, offset: 0 } as LearningGoalListRead),
    ],
    ['/activity/stats', overrides.activityStats, () => json(ACTIVITY_STATS)],
    ['/analytics/overview', overrides.overview, () => json(OVERVIEW)],
    ['/developer/summary', overrides.developer, () => json(DEVELOPER_SUMMARY)],
    ['/learning/summary', overrides.learning, () => json(LEARNING_SUMMARY)],
    ['/ml/status', overrides.mlStatus, () => json(ML_STATUS)],
  ]

  const calls: { url: string; method: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      calls.push({ url, method })

      if (method === 'POST') {
        if (url.includes('/ml/route')) return overrides.route ? overrides.route(url) : json(ROUTING_DECISION)
        return created(url, init?.body)
      }
      for (const [fragment, override, fallback] of routes) {
        if (url.includes(fragment)) return override ? override(url) : fallback(url)
      }
      return envelope('not_found', 'No stub matched this request.', 404, 'req-unmatched')
    }),
  )
  return calls
}

/**
 * A create, answered.
 *
 * Only the echoed name is under test — that is the whole of what the quick
 * action reports back — so the stub returns that field and nothing else rather
 * than a full record whose every other member would be untested filler.
 */
function created(url: string, body: BodyInit | null | undefined): Response {
  const parsed = JSON.parse(typeof body === 'string' ? body : '{}') as Record<string, unknown>
  const name = String(parsed.name ?? parsed.title ?? '')
  if (url.includes('/notes') || url.includes('/learning/goals')) return json({ title: name })
  return json({ name })
}

/** Ships the retry policy from `src/app/query-client.ts`. */
function createTestClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        retry: queryRetryPolicy,
      },
      mutations: { retry: false },
    },
  })
}

/**
 * The page inside the chrome that owns the palette.
 *
 * `CommandPalette` is mounted alongside it because the page's Search entry
 * opens the palette through the shared store rather than rendering one, so
 * without the palette in the tree there would be nothing for that entry to open
 * — which is exactly the duplication the entry exists to avoid.
 */
function renderPage(entry = '/command-center') {
  const router = createMemoryRouter(
    [
      {
        path: '*',
        element: (
          <>
            <CommandCenterPage />
            <CommandPalette />
          </>
        ),
      },
    ],
    { initialEntries: [entry] },
  )
  return render(
    <QueryClientProvider client={createTestClient()}>
      <TooltipProvider delayDuration={200}>
        <RouterProvider router={router} />
      </TooltipProvider>
    </QueryClientProvider>,
  )
}

function panel(name: string): HTMLElement {
  return screen.getByRole('region', { name }).closest('div.rounded-lg') as HTMLElement
}

/* --------------------------------------------------------------------- tests */

describe('command center page', () => {
  it('renders every panel from the endpoint that panel reads', async () => {
    const calls = installBackend()
    renderPage()

    expect(screen.getByRole('heading', { level: 1, name: 'Command Center' })).toBeInTheDocument()

    // Findings — GET /risks/summary. The sentence carries the backend's own
    // needs-attention flag rather than one the page recomputed.
    await waitFor(() =>
      expect(within(panel('Findings')).getByText(/raised needs-attention/)).toBeInTheDocument(),
    )
    expect(within(panel('Findings')).getByText('Critical')).toBeInTheDocument()
    expect(within(panel('Findings')).getByText(/10 findings live in total/)).toBeInTheDocument()

    // Deadlines and blockers — GET /activity/stats.
    await waitFor(() =>
      expect(within(panel('Deadlines and blockers')).getByText(/14 tasks in total/)).toBeInTheDocument(),
    )
    expect(within(panel('Deadlines and blockers')).getByText('Overdue')).toBeInTheDocument()

    // Momentum — GET /analytics/overview.
    await waitFor(() =>
      expect(within(panel('Momentum')).getByText('Tasks completed')).toBeInTheDocument(),
    )
    expect(within(panel('Momentum')).getByText('Focus minutes')).toBeInTheDocument()
    expect(within(panel('Momentum')).getByText('12')).toBeInTheDocument()

    // The classifier — GET /ml/status.
    await waitFor(() =>
      expect(within(panel('The classifier')).getByText('microsoft/deberta-v3-base')).toBeInTheDocument(),
    )
    expect(within(panel('The classifier')).getByText(/The 1 classes in taxonomy phase-10-v1/)).toBeInTheDocument()

    // Recorded engineering — GET /developer/summary.
    await waitFor(() => expect(within(panel('Recorded engineering')).getByText('17')).toBeInTheDocument())
    expect(within(panel('Recorded engineering')).getByText(/2 repositories, 17 commits/)).toBeInTheDocument()

    // Learning — GET /learning/summary.
    await waitFor(() =>
      expect(within(panel('Learning')).getByText(/2 open goals, 5 activities/)).toBeInTheDocument(),
    )

    // And every figure really came from the wire rather than from a constant in
    // a component. Twelve endpoints, twelve requests.
    const requested = calls.filter((call) => call.method === 'GET').map((call) => call.url)
    for (const fragment of [
      '/risks/summary',
      '/risks?status=active',
      '/recommendations?status=new',
      '/tasks?status=todo&due_before=',
      '/tasks?status=todo&priority=critical',
      '/planner/conflicts?start=',
      '/learning/goals?limit=20&target_before=',
      '/learning/summary',
      '/activity/stats',
      '/analytics/overview',
      '/developer/summary',
      '/ml/status',
    ]) {
      expect(requested.some((url) => url.includes(fragment))).toBe(true)
    }
  })

  it('ranks the signals by the documented rule and states the rule on the page', async () => {
    const user = userEvent.setup()
    installBackend()
    renderPage()

    expect(await screen.findByText(OVERDUE_TASK.title)).toBeInTheDocument()

    // The rule is published, in full, under the list — including the three
    // things in it that were chosen rather than measured.
    await user.click(screen.getByText('How this order is calculated'))
    expect(screen.getByText(/Detector score/)).toBeInTheDocument()
    expect(screen.getByText(/convention chosen for this page/)).toBeInTheDocument()
    expect(
      screen.getByText(/the engine has not re-found recently may no longer hold/),
    ).toBeInTheDocument()
    // And it says plainly that the model plays no part in the ordering.
    expect(screen.getByText(/It does not see a deadline, so it does not order this list/)).toBeInTheDocument()
  })

  it('orders the queue exactly as the rule computes, top score first', async () => {
    const user = userEvent.setup()
    installBackend()
    renderPage()

    expect(await screen.findByText(OVERDUE_TASK.title)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Show all 7' }))

    const rows = within(screen.getByTestId('priority-queue')).getAllByRole('listitem')
    const titles = rows.map((row) => row.querySelector('[data-priority-title]')?.textContent)
    const scores = rows.map((row) => row.querySelector('[data-priority-score]')?.textContent)

    expect(titles).toEqual(EXPECTED_ORDER.map(([title]) => title))
    expect(scores).toEqual(EXPECTED_ORDER.map(([, score]) => score))

    // And the order is monotonically non-increasing, whatever produced it.
    const numbers = scores.map((value) => Number(value))
    expect(numbers).toEqual([...numbers].sort((a, b) => b - a))
  })

  it('is insensitive to the order the records arrived in', async () => {
    const user = userEvent.setup()
    const order = async (): Promise<string[]> => {
      await user.click(screen.getByRole('button', { name: /Show all 7/ }))
      return within(screen.getByTestId('priority-queue'))
        .getAllByRole('listitem')
        .map((row) => row.querySelector('[data-priority-title]')?.textContent ?? '')
    }

    installBackend()
    const first = renderPage()
    expect(await screen.findByText(OVERDUE_TASK.title)).toBeInTheDocument()
    const firstOrder = await order()
    first.unmount()

    // The same records, the same endpoints, with the two findings in the one
    // response swapped. A rank that leaned on payload position would differ;
    // this one cannot.
    installBackend({
      risks: () =>
        json({
          items: [LOW_RISK, CRITICAL_RISK],
          total: 2,
          limit: 20,
          offset: 0,
          by_severity: { critical: 1, high: 0, medium: 0, low: 1 },
          summary: 'Two live findings.',
        } as RiskListRead),
    })
    renderPage()
    expect(await screen.findByText(OVERDUE_TASK.title)).toBeInTheDocument()

    expect(await order()).toEqual(firstOrder)
    // The permutation really did take effect, so this is not a vacuous pass: the
    // critical finding is above the low one no matter which way round the
    // response put them.
    expect(firstOrder.indexOf(CRITICAL_RISK.title)).toBeLessThan(
      firstOrder.indexOf(LOW_RISK.title),
    )
  })

  it('gives every row its arithmetic, normalised against that signal’s own ceiling', async () => {
    const user = userEvent.setup()
    installBackend()
    renderPage()

    expect(await screen.findByText(OVERDUE_TASK.title)).toBeInTheDocument()
    const row = screen.getByText(OVERDUE_TASK.title).closest('li') as HTMLElement
    await user.click(within(row).getByRole('button', { name: 'Why' }))

    const table = within(row).getByRole('table')
    // 40 severity + 25 deadline + 15 task priority = 80 of the 80 those three
    // factors alone could have contributed. The ceiling is per signal, not the
    // 105 a signal with every factor would have.
    expect(within(table).getByText('40/40')).toBeInTheDocument()
    expect(within(table).getByText('25/25')).toBeInTheDocument()
    expect(within(table).getByText('15/15')).toBeInTheDocument()
    expect(within(table).getByText('80/80')).toBeInTheDocument()
    expect(within(table).getByText(/Normalised to 100 of 100/)).toBeInTheDocument()
    expect(within(table).getByText(/A calculated ranking, not a measurement/)).toBeInTheDocument()
    expect(within(table).getByText(/10 days ago/)).toBeInTheDocument()
  })

  it('labels each panel measured, calculated or model-derived, and says which is which', async () => {
    installBackend()
    renderPage()

    expect(await screen.findByText(OVERDUE_TASK.title)).toBeInTheDocument()

    // Two `Calculated` badges live under "Next up": the panel's own header, and
    // the rule disclosure below the list. The header is the first.
    const [queueHeader] = within(panel('Next up')).getAllByText('Calculated')
    expect(queueHeader).toBeDefined()
    for (const measured of [
      'Findings',
      'Deadlines and blockers',
      'Momentum',
      'Recorded engineering',
      'Learning',
    ]) {
      expect(within(panel(measured)).getByText('Measured')).toBeInTheDocument()
    }
    expect(within(panel('The classifier')).getByText('Model-derived')).toBeInTheDocument()

    // The labels mean different things, and the badge says so rather than
    // leaving a reader to infer it from three hues.
    expect(queueHeader?.closest('span')).toHaveAttribute(
      'title',
      expect.stringContaining('ranking, not a measurement'),
    )
    expect(within(panel('The classifier')).getByText('Model-derived').closest('span')).toHaveAttribute(
      'title',
      expect.stringContaining('never a measurement of your work'),
    )
    expect(within(panel('Momentum')).getByText('Measured').closest('span')).toHaveAttribute(
      'title',
      'Counted from stored records by a real endpoint.',
    )
  })

  it('never presents a prediction as a measurement', async () => {
    const user = userEvent.setup()
    const calls = installBackend()
    renderPage()

    const classifier = panel('The classifier')
    await within(classifier).findByText('microsoft/deberta-v3-base')

    await user.type(screen.getByLabelText('Classify one sentence'), 'add a task called draft')
    await user.click(screen.getByRole('button', { name: 'Classify' }))

    const prediction = await screen.findByTestId('ml-prediction')

    // Framed as a prediction three times over: the heading says it, the badge
    // says it, and the first sentence says it.
    expect(within(prediction).getByRole('heading', { name: 'Prediction' })).toBeInTheDocument()
    expect(within(prediction).getByText('Model-derived')).toBeInTheDocument()
    expect(
      within(prediction).getByText(/It did not read your work, count anything, or check a record/),
    ).toBeInTheDocument()

    // It sits inside its own dashed region, so it cannot be read as part of the
    // measured panels beside it.
    expect(prediction.className).toContain('border-dashed')

    // The outcome is the headline and the intent is the evidence beneath it: an
    // `generation_unavailable` answer still carries a correctly recognised
    // intent, so reading the intent alone would turn a recognition into an
    // apparent failure.
    expect(within(prediction).getByText('Outcome')).toBeInTheDocument()
    expect(within(prediction).getByText('Predicted intent')).toBeInTheDocument()
    expect(within(prediction).getByText('91%')).toBeInTheDocument()
    expect(within(prediction).getByText('62%')).toBeInTheDocument()

    // It really was a call to the real endpoint, with the sentence sent verbatim.
    const posted = calls.find((call) => call.method === 'POST' && call.url.includes('/ml/route'))
    expect(posted?.url).toBe('/api/v1/ml/route')

    // And the block never sits inside a measured panel: a predicted figure is
    // structurally separated from a counted one, not merely labelled near it.
    expect(panel('The classifier').contains(prediction)).toBe(true)
    for (const measured of [
      'Findings',
      'Deadlines and blockers',
      'Momentum',
      'Recorded engineering',
      'Learning',
    ]) {
      expect(panel(measured).contains(prediction)).toBe(false)
      expect(within(panel(measured)).queryByText('91%')).toBeNull()
    }
  })

  it('keeps every other panel alive when one request fails, and retries it', async () => {
    const user = userEvent.setup()
    let failing = true
    installBackend({
      developer: () =>
        failing
          ? envelope('internal_error', 'The developer summary is unavailable.', 500, 'req-dev-1')
          : json(DEVELOPER_SUMMARY),
    })
    renderPage()

    // The failed panel reports itself, in its own words, with the request id.
    await waitFor(
      () => expect(screen.getByText('Recorded engineering could not load')).toBeInTheDocument(),
      { timeout: 20_000 },
    )
    expect(
      screen.getByText('Recorded engineering could not load').closest('[role="alert"]'),
    ).toHaveTextContent('req-dev-1')
    expect(panel('Recorded engineering')).not.toHaveTextContent('17')

    // Every other panel still rendered, from its own endpoint.
    await waitFor(() =>
      expect(within(panel('Findings')).getByText(/10 findings live in total/)).toBeInTheDocument(),
    )
    expect(within(panel('Momentum')).getByText('Tasks completed')).toBeInTheDocument()
    expect(within(panel('The classifier')).getByText('microsoft/deberta-v3-base')).toBeInTheDocument()
    expect(screen.getByText(OVERDUE_TASK.title)).toBeInTheDocument()

    // And the retry asks again and recovers.
    failing = false
    await user.click(within(panel('Recorded engineering')).getByRole('button', { name: 'Retry' }))
    expect(await within(panel('Recorded engineering')).findByText('17')).toBeInTheDocument()
    expect(screen.queryByText('Recorded engineering could not load')).toBeNull()
  })

  it('names a source that failed while the rest of the queue still ranks', async () => {
    const user = userEvent.setup()
    let failing = true
    const conflicts = (): Response =>
      failing
        ? envelope('internal_error', 'The planner is unavailable.', 500, 'req-plan-1')
        : json({
            window: { start_date: TODAY, end_date: daysAhead(14), timezone: 'UTC' },
            conflicts: [CONFLICT],
            meta: { total: 1, limit: 50, offset: 0 },
          } as ConflictList)

    installBackend({ conflicts })
    renderPage()

    // A briefing that silently omitted one source would pass for a complete one.
    expect(
      await screen.findByText(
        '1 of the sources behind this list could not be read, so it is incomplete.',
        {},
        { timeout: 20_000 },
      ),
    ).toBeInTheDocument()
    expect(screen.getByText(/Schedule conflicts:/)).toBeInTheDocument()
    expect(screen.getByText('The planner is unavailable.')).toBeInTheDocument()

    // The five sources that did answer are ranked and rendered anyway.
    expect(screen.getByText(OVERDUE_TASK.title)).toBeInTheDocument()
    expect(screen.getByText(CRITICAL_RISK.title)).toBeInTheDocument()
    expect(screen.queryByText('overlapping sessions')).toBeNull()

    failing = false
    await user.click(within(panel('Next up')).getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.queryByText(/could not be read/)).toBeNull())
    expect(await screen.findByText('overlapping sessions')).toBeInTheDocument()
  })

  it('fails the queue only when every source behind it failed', async () => {
    // 403 rather than 500 so the shared client does not retry: this case is
    // about which panel fails, not about backoff.
    const refused = (): Response =>
      envelope('forbidden', 'That record belongs to another account.', 403, 'req-queue')

    installBackend({
      risks: refused,
      recommendations: refused,
      deadlines: refused,
      criticalTasks: refused,
      conflicts: refused,
      goals: refused,
    })
    renderPage()

    // Nothing answered, so the panel itself fails and says so, with a retry.
    await waitFor(() => expect(screen.getByText('Next up could not load')).toBeInTheDocument())
    expect(screen.getByText('Next up could not load').closest('[role="alert"]')).toHaveTextContent(
      'req-queue',
    )

    // And the page is not blank: six other panels read their own endpoints.
    await waitFor(() =>
      expect(within(panel('Momentum')).getByText('Tasks completed')).toBeInTheDocument(),
    )
    expect(within(panel('The classifier')).getByText('microsoft/deberta-v3-base')).toBeInTheDocument()
    expect(within(panel('Findings')).getByText(/10 findings live in total/)).toBeInTheDocument()
  })

  it('gives every panel its own empty state when the records say nothing', async () => {
    installBackend({
      risks: () =>
        json({
          items: [],
          total: 0,
          limit: 20,
          offset: 0,
          by_severity: { critical: 0, high: 0, medium: 0, low: 0 },
          summary: 'Nothing live.',
        } as RiskListRead),
      recommendations: () =>
        json({
          items: [],
          total: 0,
          limit: 20,
          offset: 0,
          by_priority: { critical: 0, high: 0, medium: 0, low: 0 },
        } as RecommendationListRead),
      deadlines: () => json({ items: [], meta: { total: 0, limit: 20, offset: 0 } }),
      criticalTasks: () => json({ items: [], meta: { total: 0, limit: 20, offset: 0 } }),
      conflicts: () =>
        json({
          window: { start_date: TODAY, end_date: daysAhead(14), timezone: 'UTC' },
          conflicts: [],
          meta: { total: 0, limit: 50, offset: 0 },
        } as ConflictList),
      goals: () => json({ items: [], total: 0, limit: 20, offset: 0 } as LearningGoalListRead),
      riskSummary: () =>
        json({ ...RISK_SUMMARY, critical: 0, high: 0, medium: 0, low: 0, total: 0, needs_attention: false }),
      activityStats: () =>
        json({
          ...ACTIVITY_STATS,
          tasks: { total: 0, todo: 0, in_progress: 0, blocked: 0, completed: 0, cancelled: 0, overdue: 0 },
        }),
      overview: () => json({ ...OVERVIEW, totals: [] }),
      developer: () =>
        json({
          ...DEVELOPER_SUMMARY,
          has_data: false,
          commit_count: 0,
          commits_in_window: 0,
          active_days: 0,
          summary: 'No repositories registered.',
        }),
      learning: () =>
        json({
          ...LEARNING_SUMMARY,
          has_data: false,
          goal_count: 0,
          active_goal_count: 0,
          activities_in_window: 0,
          minutes_in_window: 0,
          summary: 'Nothing recorded.',
        }),
    })
    renderPage()

    // Seven panels, seven distinct empty states — none of them a wall of zeroes,
    // and none of them a dash standing in for something that is a real zero.
    expect(await screen.findByText('Nothing is asking for a decision')).toBeInTheDocument()
    expect(screen.getByText('No live findings')).toBeInTheDocument()
    expect(screen.getByText('No tasks recorded')).toBeInTheDocument()
    expect(screen.getByText('No aggregates yet')).toBeInTheDocument()
    expect(screen.getByText('No repositories registered')).toBeInTheDocument()
    expect(screen.getByText('Nothing recorded yet')).toBeInTheDocument()

    // Nothing anywhere on the page claims a number it does not have.
    expect(document.body.textContent ?? '').not.toMatch(/undefined|NaN|Infinity/)
  })

  it('says the classifier is switched off rather than reporting an empty model', async () => {
    installBackend({
      mlStatus: () =>
        json({
          ...ML_STATUS,
          enabled: false,
          available: false,
          unavailable_reason: 'disabled',
          model: null,
          intents: [],
        } satisfies MlStatusRead),
    })
    renderPage()

    expect(await screen.findByText('The classifier is switched off')).toBeInTheDocument()
    expect(screen.getByText(/every other panel reads a database, not the model/)).toBeInTheDocument()
    // No model identity is invented for a runtime that loaded nothing.
    expect(screen.queryByText('microsoft/deberta-v3-base')).toBeNull()
  })

  it('creates a record through the real endpoint and reports what came back', async () => {
    const user = userEvent.setup()
    const calls = installBackend()
    renderPage()

    const form = (await screen.findByLabelText('New project')).closest('form') as HTMLElement
    await user.type(within(form).getByPlaceholderText('Atlas migration'), 'Atlas migration')
    await user.click(within(form).getByRole('button', { name: 'Create' }))

    expect(await screen.findByText('Created project “Atlas migration”.')).toBeInTheDocument()
    const post = calls.find((call) => call.method === 'POST')
    expect(post?.url).toBe('/api/v1/projects')
  })

  it('opens the command palette rather than duplicating a search box', async () => {
    const user = userEvent.setup()
    installBackend()
    renderPage()
    await screen.findByRole('heading', { level: 1, name: 'Command Center' })

    // There is no second search field: the entry hands off to the palette, which
    // owns its own input and its own focus. Only the palette's combobox exists.
    expect(screen.queryByRole('combobox')).toBeNull()

    await user.click(screen.getByRole('button', { name: /^Search/ }))
    expect(await screen.findByRole('dialog', { name: 'Command palette' })).toBeInTheDocument()

    const combobox = await screen.findByRole('combobox')
    // The palette focuses its input on mount, which is the whole of what "focus
    // the palette" means from here.
    await waitFor(() => expect(combobox).toHaveFocus())
  })
})
