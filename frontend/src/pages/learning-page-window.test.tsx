import type { ReactElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/tooltip'
import { formatNumber } from '@/features/analytics/format'
import { describeWindow } from '@/features/learning/components'
import LearningPage from '@/pages/learning-page'
import { queryRetryPolicy } from '@/app/query-client'
import type { ApiErrorEnvelope } from '@/types/api'
import type { Granularity } from '@/types/analytics'
import type {
  LearningActivityBucketRead,
  LearningActivityRead,
  LearningActivitySeriesRead,
  LearningGoalRead,
  LearningSummaryRead,
  SkillGapRead,
  SkillRead,
} from '@/types/learning'

/**
 * The Learning dashboard's view controls: the grain, the three pagers, and the
 * one chart that is deliberately *not* windowed.
 *
 * The page is mounted for real — real router, real components, real hooks — and
 * only `fetch` is stubbed, through the routing table below with per-endpoint
 * overrides. Every figure a reader could see is therefore a body this file wrote,
 * and the counts are checkable by hand: 45 goals behind a two-row page, 41 skills
 * behind a one-row page, 30 recorded activities behind a twelve-row page.
 *
 * **The query client is local and its defaults are the shipped ones.**
 * `AppProviders` mounts the shared singleton and registers
 * `onSessionChange(() => queryClient.clear())`, which in jsdom strands every
 * component at `pending`; and the retry policy in `src/app/query-client.ts` is
 * what decides whether a 4xx arrives on the first response or after a backoff.
 *
 * **Recharts is given a size, and only a size.** jsdom has no layout engine, so
 * `ResponsiveContainer` measures 0×0 and every chart comes back as an empty box.
 * The mock replaces exactly that measurement and tags what it wraps in
 * `data-chart-surface`. Charts sit behind the real `LazyChart` boundary, whose
 * fallback paints the *same* card title as the chart it replaces — so
 * {@link resolveCharts} waits on the fallback leaving the document, and every
 * chart assertion after it is a `findBy*`.
 *
 * **Three traps this file is written around.**
 *
 * - recharts leaks a `<span id="recharts_measurement_span">0</span>` into
 *   `document.body` and never removes it, so the digit-free guard is scoped to
 *   the render container rather than the whole body.
 * - A card paints its own title before its rows exist, so `findByRole('heading')`
 *   on a card title is not a wait for its data; {@link waitForData} waits on
 *   content strings that can only be on screen once a read has been answered.
 * - Two of the three paginated lists are fetched **twice**, at different `limit`s:
 *   `limit=20` for the cards and `limit=200` for the pickers and the name
 *   lookups. A routing table that answers both with one body would quietly make
 *   "this page" and "the whole account" the same list, so {@link dualList} keeps
 *   them apart and every request assertion filters on the limit it means.
 *
 * The dates are fixed in 2019 — a completed year — and no goal fixture carries a
 * `target_date`, because a deadline is rendered relative to today and nothing
 * here asserts on a day count that would depend on the machine's clock.
 */

vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  const react = await import('react')
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children?: ReactElement<Record<string, unknown>> }) =>
      children ? (
        <div data-chart-surface="true">
          {react.cloneElement(children, { width: 640, height: 256 })}
        </div>
      ) : null,
  }
})

/** Only the first test to touch a lazy chart pays for the module import. */
const LAZY_RESOLVE_TIMEOUT_MS = 10_000

/** Generous for the first commit of a fully loaded page; every assertion after is the check. */
const DATA_TIMEOUT_MS = 10_000

const SKILL_ID = '11111111-1111-4111-8111-111111111111'
const SKILL_TWO_ID = '22222222-2222-4222-8222-222222222222'
const SKILL_THREE_ID = '33333333-3333-4333-8333-333333333333'
const GOAL_ID = '44444444-4444-4444-8444-444444444444'
const COMPLETED_GOAL_ID = '55555555-5555-4555-8555-555555555555'
const ACTIVITY_ID = '66666666-6666-4666-8666-666666666666'
const VIEWED_ID = '77777777-7777-4777-8777-777777777777'

/** The page sizes the dashboard chose: 20 for the cards, 12 for the trail. */
const GOAL_PAGE_SIZE = 20
const SKILL_PAGE_SIZE = 20
const ACTIVITY_PAGE_SIZE = 12

/** The limit behind the pickers, the name lookups and the all-time per-skill chart. */
const PICKER_LIMIT = 200

const UNMEASURED_REASON =
  'No level has ever been recorded for this skill, so there is no distance to a target to measure.'

/* ------------------------------------------------------------------ harness */

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

interface Call {
  url: string
  method: string
}

interface Backend {
  summary?: Route
  activity?: Route
  gaps?: Route
  goals?: Route
  skills?: Route
  activities?: Route
}

/** The query string of a request, so a limit or an offset can be asserted on. */
function readParams(url: string): URLSearchParams {
  return new URL(url, 'http://nexus.test').searchParams
}

function granularityOf(url: string): Granularity {
  const value = readParams(url).get('granularity')
  return value === 'week' || value === 'month' ? value : 'day'
}

/**
 * One endpoint answering two genuinely different questions.
 *
 * `limit=200` is the complete read behind the pickers, the deadlines panel, the
 * name lookups and the all-time per-skill chart; anything else is the page of
 * cards. Answering both from one body would make "the twenty on screen" and
 * "the whole account" the same list, which is exactly the confusion the page's
 * own prose is written against.
 */
function dualList(
  page: { items: unknown[]; total: number },
  all: { items: unknown[]; total: number },
): Route {
  return (url) => {
    const params = readParams(url)
    const limit = Number(params.get('limit') ?? 0)
    if (limit === PICKER_LIMIT) {
      return json({ items: all.items, total: all.total, limit: PICKER_LIMIT, offset: 0 })
    }
    return json({
      items: page.items,
      total: page.total,
      limit,
      offset: Number(params.get('offset') ?? 0),
    })
  }
}

/**
 * Stubs `fetch` with the routing table below, so one test can replace a single
 * endpoint — the slow re-bucketing, the account that really does have 45 goals —
 * without restating the rest. `/learning/activities` is matched before
 * `/learning/activity` because the former contains the latter.
 */
function installBackend(overrides: Backend = {}): Call[] {
  const summary = overrides.summary ?? (() => json(SUMMARY))
  const activity = overrides.activity ?? (() => json(series('day')))
  const gaps = overrides.gaps ?? (() => json(GAPS))
  const goals = overrides.goals ?? dualList(GOAL_PAGE, GOAL_PAGE)
  const skills = overrides.skills ?? dualList(SKILL_PAGE, SKILL_PAGE)
  const activities = overrides.activities ?? dualList(ACTIVITY_PAGE, ACTIVITY_PAGE)

  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      calls.push({ url, method })

      if (method === 'POST') return envelope('not_found', 'No stub matched this write.', 404, 'req-no-post')
      if (url.includes('/learning/summary')) return summary(url)
      if (url.includes('/learning/activities')) return activities(url)
      if (url.includes('/learning/activity')) return activity(url)
      if (url.includes('/learning/gaps')) return gaps(url)
      if (url.includes('/learning/goals')) return goals(url)
      if (url.includes('/learning/skills')) return skills(url)
      return envelope('not_found', 'No stub matched this request.', 404, 'req-unmatched')
    }),
  )
  return calls
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

function renderLearningPage(entry = '/learning') {
  const router = createMemoryRouter([{ path: '*', element: <LearningPage /> }], {
    initialEntries: [entry],
  })
  const view = render(
    <QueryClientProvider client={createTestClient()}>
      <TooltipProvider delayDuration={200}>
        <RouterProvider router={router} />
      </TooltipProvider>
    </QueryClientProvider>,
  )
  return { ...view, router }
}

/** The `LazyChart` fallback's body — a marker no resolved chart can produce. */
function chartFallback(container: HTMLElement): Element | null {
  return container.querySelector('[data-slot="skeleton"].h-64')
}

async function resolveCharts(container: HTMLElement): Promise<void> {
  await waitFor(() => expect(chartFallback(container)).toBeNull(), {
    timeout: LAZY_RESOLVE_TIMEOUT_MS,
  })
}

/** Lets every in-flight fetch and its re-render settle before asserting. */
async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50))
  })
}

/**
 * No figure on this page may print a number it does not have. `NaN`, `Infinity`
 * and the word `undefined` are the three ways a nullable leaks into a rendered
 * string, and the spec names all three.
 *
 * Scoped to the render container rather than `document.body`: recharts leaks a
 * `<span id="recharts_measurement_span">0</span>` into the body and never removes
 * it, so a whole-body scan would be asserting on a library's own markup.
 */
function expectNoFabricatedNumbers(root: HTMLElement): void {
  const text = root.textContent ?? ''
  expect(text).not.toMatch(/NaN/)
  expect(text).not.toMatch(/Infinity/)
  expect(text).not.toMatch(/undefined/)
}

/**
 * Waits until the reads that feed the grain, the pagers and the all-time chart
 * have landed — not merely the page chrome.
 *
 * Three strings from three different endpoints, and each is one that appears
 * exactly once on the page: the server's summary sentence, the gap explanation
 * it composed, and a recorded activity title. A skill *name* would not do — it
 * is printed by the card, by the gap row, by two pickers and by the trail, and
 * a `findByText` that resolves to five elements has resolved to nothing.
 */
async function waitForData(): Promise<void> {
  await screen.findByText(SUMMARY.summary, undefined, { timeout: DATA_TIMEOUT_MS })
  await screen.findByText(GAP_EXPLANATION, undefined, { timeout: DATA_TIMEOUT_MS })
  await screen.findByText('Read the chapter on regularisation', undefined, { timeout: DATA_TIMEOUT_MS })
}

/** The `nav` a pager is, found by its own accessible name. */
function pager(label: string): HTMLElement {
  return screen.getByRole('navigation', { name: label })
}

function expectNoPager(label: string): void {
  expect(screen.queryByRole('navigation', { name: label })).toBeNull()
}

function getCalls(calls: Call[], needle: string): Call[] {
  return calls.filter((call) => call.url.includes(needle))
}

/** Only the paginated card read, never the `limit=200` complete read beside it. */
function pageCalls(calls: Call[], needle: string, size: number): Call[] {
  return getCalls(calls, needle).filter(
    (call) => Number(readParams(call.url).get('limit') ?? 0) === size,
  )
}

function offsetsOf(calls: Call[], needle: string, size: number): string[] {
  return pageCalls(calls, needle, size).map((call) => readParams(call.url).get('offset') ?? 'absent')
}

function grainsOf(calls: Call[]): string[] {
  return getCalls(calls, '/learning/activity?').map((call) => granularityOf(call.url))
}

/**
 * The sentence the window bar prints about the chart, rebuilt from what the
 * **server** answered rather than from what the client asked for.
 *
 * Rebuilt here rather than pasted, so a change to the copy is a change to this
 * expectation — and so the point being pinned stays the `granularity` field,
 * which is the one thing only the response knows.
 */
function captionFor(granularity: Granularity, windowDays: number): string {
  return (
    `The activity chart below plots one point per ${granularity} across ` +
    `${describeWindow(windowDays)}. Every bucket in that range is plotted, ` +
    'including the ones with nothing recorded in them.'
  )
}

/** The caption printed while a re-bucketing read is in flight over a previous answer. */
const REDRAWING_CAPTION = 'The activity chart is being redrawn at the grain chosen here.'

interface Deferred<T> {
  promise: Promise<T>
  resolve: (value: T) => void
}

function deferred<T>(): Deferred<T> {
  let resolve: (value: T) => void = () => undefined
  const promise = new Promise<T>((inner) => {
    resolve = inner
  })
  return { promise, resolve }
}

/* ---------------------------------------------------------------- fixtures */

function skill(overrides: Partial<SkillRead> = {}): SkillRead {
  return {
    id: SKILL_ID,
    name: 'Machine Learning',
    category: 'domain',
    description: 'Training models and reading their output.',
    current_level: 2,
    target_level: 4,
    level_source: 'user_defined',
    confidence: 0,
    evidence_count: 6,
    last_activity_at: '2019-01-29T14:05:00Z',
    created_at: '2019-01-02T09:00:00Z',
    updated_at: '2019-01-29T14:05:00Z',
    ...overrides,
  }
}

function goal(overrides: Partial<LearningGoalRead> = {}): LearningGoalRead {
  return {
    id: GOAL_ID,
    title: 'Reach level 4 on machine learning',
    description: 'Work through the recorded sessions once a week.',
    target_skill_id: SKILL_ID,
    target_topic: null,
    target_date: null,
    priority: 'medium',
    status: 'in_progress',
    progress: 40,
    estimated_effort_minutes: null,
    project_id: null,
    note_id: null,
    completed_at: null,
    created_at: '2019-01-02T09:00:00Z',
    updated_at: '2019-01-29T14:05:00Z',
    ...overrides,
  }
}

function learningActivity(overrides: Partial<LearningActivityRead> = {}): LearningActivityRead {
  return {
    id: ACTIVITY_ID,
    skill_id: SKILL_ID,
    goal_id: GOAL_ID,
    activity_type: 'study_session',
    title: 'Read the chapter on regularisation',
    description: 'One chapter, with notes.',
    occurred_at: '2019-01-29T14:05:00Z',
    duration_minutes: 45,
    source_type: null,
    source_id: null,
    created_at: '2019-01-29T14:05:04Z',
    ...overrides,
  }
}

function bucket(
  day: number,
  overrides: Partial<LearningActivityBucketRead> = {},
): LearningActivityBucketRead {
  return {
    bucket_start: `2019-01-${String(day).padStart(2, '0')}T00:00:00Z`,
    bucket_end: `2019-01-${String(day + 1).padStart(2, '0')}T00:00:00Z`,
    activities: 1,
    sessions: 1,
    minutes: 45,
    ...overrides,
  }
}

const GAP_EXPLANATION =
  'Target 4/5, current self-assessed 2/5. NEXUS recorded 6 related learning activities in the last 30 days.'

function gap(overrides: Partial<SkillGapRead> = {}): SkillGapRead {
  return {
    skill_id: SKILL_ID,
    skill_name: 'Machine Learning',
    target_level: 4,
    current_level: 2,
    level_source: 'user_defined',
    gap: 2,
    evidence_count: 6,
    evidence_last_30d: 6,
    days_since_last_activity: 1,
    available: true,
    reason_if_unavailable: null,
    explanation: GAP_EXPLANATION,
    ...overrides,
  }
}

const GAPS: SkillGapRead[] = [
  gap(),
  gap({
    skill_id: null,
    skill_name: 'Rust',
    available: false,
    gap: 0,
    evidence_count: 0,
    evidence_last_30d: 0,
    days_since_last_activity: null,
    reason_if_unavailable: UNMEASURED_REASON,
    explanation:
      'No gap could be computed for Rust because no level has ever been recorded against it.',
  }),
]

const SUMMARY: LearningSummaryRead = {
  goal_count: 2,
  active_goal_count: 1,
  completed_goal_count: 1,
  skill_count: 2,
  activity_count: 11,
  activities_in_window: 3,
  minutes_in_window: 135,
  window_days: 30,
  window_start: '2019-01-01T00:00:00Z',
  window_end: '2019-01-30T23:59:59Z',
  latest_activity_at: '2019-01-29T14:05:00Z',
  has_data: true,
  summary: '3 learning activities were recorded in the last 30 days across 2 tracked skills.',
}

/**
 * The series, at whatever grain the server chose to build it.
 *
 * `granularity` is an **echo**, not the request: only the server knows which
 * buckets it produced, and every caption on the page is built from this field.
 */
function series(granularity: Granularity, overrides: Partial<LearningActivitySeriesRead> = {}): LearningActivitySeriesRead {
  return {
    granularity,
    window_days: 30,
    window_start: '2019-01-01T00:00:00Z',
    window_end: '2019-01-30T23:59:59Z',
    skill_id: null,
    buckets: [
      bucket(7, { activities: 2, sessions: 1, minutes: 45 }),
      bucket(8, { activities: 0, sessions: 0, minutes: null }),
      bucket(9, { activities: 1, sessions: 1, minutes: 90 }),
    ],
    total_activities: 3,
    total_minutes: 135,
    ...overrides,
  }
}

/** A series read that echoes back whichever grain was asked for. */
function echoingSeries(): Route {
  return (url) => json(series(granularityOf(url)))
}

/** Two rows, and the totals that make every list on the page fit on one page. */
const GOAL_PAGE: { items: LearningGoalRead[]; total: number } = {
  items: [
    goal(),
    goal({
      id: COMPLETED_GOAL_ID,
      title: 'Work through the SQL handbook',
      status: 'completed',
      progress: 100,
      target_skill_id: SKILL_TWO_ID,
      completed_at: '2019-01-29T14:05:00Z',
    }),
  ],
  total: 2,
}

const SKILL_PAGE: { items: SkillRead[]; total: number } = {
  items: [
    skill(),
    skill({
      id: SKILL_TWO_ID,
      name: 'SQL',
      current_level: 4,
      target_level: 5,
      level_source: 'system_estimate',
      confidence: 92,
      evidence_count: 41,
      category: null,
    }),
  ],
  total: 2,
}

/**
 * The complete skill read, as the page actually asks for it.
 *
 * `Rust` is here and **not** on the page above: the all-time per-skill chart is
 * built from this list, so a skill nobody is looking at today is still counted,
 * and the only way to see that is for the chart to be drawn from the whole set.
 */
const SKILL_ALL: { items: SkillRead[]; total: number } = {
  items: [
    ...SKILL_PAGE.items,
    skill({
      id: SKILL_THREE_ID,
      name: 'Rust',
      category: 'language',
      current_level: 1,
      target_level: 3,
      level_source: 'user_defined',
      evidence_count: 0,
      confidence: 0,
      last_activity_at: null,
    }),
  ],
  total: 3,
}

const ACTIVITY_PAGE: { items: LearningActivityRead[]; total: number } = {
  items: [
    learningActivity(),
    learningActivity({
      id: VIEWED_ID,
      activity_type: 'resource_viewed',
      duration_minutes: null,
      title: 'Opened the regularisation notebook',
    }),
  ],
  total: 2,
}

/* -------------------------------------------------------------------- tests */

describe('the learning dashboard grain control', () => {
  it('is a named radiogroup with exactly one option in the tab order', async () => {
    installBackend()
    renderLearningPage()
    await waitForData()

    const group = screen.getByRole('radiogroup', { name: 'Grain' })
    const day = within(group).getByRole('radio', { name: 'Day' })
    const week = within(group).getByRole('radio', { name: 'Week' })
    const month = within(group).getByRole('radio', { name: 'Month' })

    // Three options and no more: the control is an exclusive choice, and the
    // name is the label a screen reader reads before the first arrow press.
    expect(within(group).getAllByRole('radio')).toHaveLength(3)

    // Roving tabindex: a keyboard user tabs *once* to change one thing. Without
    // it, three stops and no announcement of which one is chosen.
    expect(day).toHaveAttribute('aria-checked', 'true')
    expect(week).toHaveAttribute('aria-checked', 'false')
    expect(day).toHaveAttribute('tabindex', '0')
    expect(week).toHaveAttribute('tabindex', '-1')
    expect(month).toHaveAttribute('tabindex', '-1')
  })

  it('moves and selects together with the arrow keys, and puts the grain in the request', async () => {
    const user = userEvent.setup()
    const calls = installBackend({ activity: echoingSeries() })
    const { router, container } = renderLearningPage()
    await waitForData()

    const group = screen.getByRole('radiogroup', { name: 'Grain' })
    const dayRadio = within(group).getByRole('radio', { name: 'Day' })

    // Moving the arrow *selects* — that is what makes this a radiogroup rather
    // than three buttons, and it is why one key press can never leave the
    // pressed option and the chart on screen disagreeing.
    dayRadio.focus()
    await user.keyboard('{ArrowRight}')

    await waitFor(() => expect(grainsOf(calls)).toContain('week'))
    expect(within(group).getByRole('radio', { name: 'Week' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
    expect(within(group).getByRole('radio', { name: 'Week' })).toHaveFocus()
    // Roving tabindex follows the selection, so the group is still one tab stop.
    expect(within(group).getByRole('radio', { name: 'Week' })).toHaveAttribute('tabindex', '0')
    expect(within(group).getByRole('radio', { name: 'Day' })).toHaveAttribute('tabindex', '-1')

    await user.keyboard('{ArrowRight}')
    await waitFor(() => expect(grainsOf(calls)).toContain('month'))
    expect(within(group).getByRole('radio', { name: 'Month' })).toHaveAttribute(
      'aria-checked',
      'true',
    )

    // From here the URL is the record, not the request log: stepping back to a
    // grain whose answer is already cached and fresh issues no second call, and
    // that is correct — a selection that had to re-ask to look selected would be
    // a worse control than one that does not.
    await user.keyboard('{ArrowLeft}')
    await waitFor(() => expect(readParams(router.state.location.search).get('granularity')).toBe('week'))
    expect(within(group).getByRole('radio', { name: 'Week' })).toHaveAttribute(
      'aria-checked',
      'true',
    )

    // Home and End jump to the ends, which a one-step arrow cannot reach.
    await user.keyboard('{End}')
    await waitFor(() => expect(readParams(router.state.location.search).get('granularity')).toBe('month'))
    expect(within(group).getByRole('radio', { name: 'Month' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
    await user.keyboard('{Home}')
    await waitFor(() => expect(readParams(router.state.location.search).get('granularity')).toBeNull())
    expect(within(group).getByRole('radio', { name: 'Day' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
    expect(within(group).getByRole('radio', { name: 'Day' })).toHaveFocus()

    // Choosing a grain narrows nothing out: the window is carried through, so
    // widening the range never quietly undoes a grain already picked.
    expect(grainsOf(calls)).toContain('week')
    expect(grainsOf(calls)).toContain('month')
    for (const url of getCalls(calls, '/learning/activity?').map((call) => call.url)) {
      expect(url).not.toContain('window_days')
    }
    expectNoFabricatedNumbers(container)
  })

  it('carries the chosen grain into the URL, so the view is shareable', async () => {
    const user = userEvent.setup()
    const calls = installBackend({ activity: echoingSeries() })
    const { router } = renderLearningPage()
    await waitForData()

    await user.click(screen.getByRole('radio', { name: 'Week' }))
    await waitFor(() =>
      expect(readParams(router.state.location.search).get('granularity')).toBe('week'),
    )

    await user.click(screen.getByRole('radio', { name: 'Month' }))
    await waitFor(() =>
      expect(readParams(router.state.location.search).get('granularity')).toBe('month'),
    )

    await waitFor(() => expect(grainsOf(calls)).toEqual(expect.arrayContaining(['week', 'month'])))
  })

  it('restores the grain from a shared link, checked on arrival', async () => {
    installBackend({ activity: echoingSeries() })
    renderLearningPage('/learning?granularity=month')
    await waitForData()

    expect(screen.getByRole('radio', { name: 'Month' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('radio', { name: 'Day' })).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByRole('radio', { name: 'Month' })).toHaveAttribute('tabindex', '0')
    expect(screen.getByRole('radio', { name: 'Day' })).toHaveAttribute('tabindex', '-1')
    // And the caption agrees with the link before a single key is pressed.
    expect(screen.getByText(captionFor('month', 30))).toBeInTheDocument()
  })

  it('names the grain the server echoed, never the one the client asked for', async () => {
    const user = userEvent.setup()
    // The backend answered `month` to a request for `week`. Only the response
    // knows which buckets it actually built.
    installBackend({ activity: () => json(series('month')) })
    const { container } = renderLearningPage()
    await waitForData()

    expect(screen.getByText(captionFor('month', 30))).toBeInTheDocument()

    await user.click(screen.getByRole('radio', { name: 'Week' }))

    // The control says Week is chosen, the request says `granularity=week`, and
    // the caption says *month* — because month is what is on the chart.
    await waitFor(() => expect(screen.getByText(captionFor('month', 30))).toBeInTheDocument())
    expect(screen.queryByText(captionFor('week', 30))).toBeNull()
    expectNoFabricatedNumbers(container)
  })

  it('updates the caption when the grain changes, rather than leaving the old one up', async () => {
    const user = userEvent.setup()
    installBackend({ activity: echoingSeries() })
    const { container } = renderLearningPage()
    await waitForData()

    expect(screen.getByText(captionFor('day', 30))).toBeInTheDocument()

    await user.click(screen.getByRole('radio', { name: 'Week' }))
    await screen.findByText(captionFor('week', 30))
    expect(screen.queryByText(captionFor('day', 30))).toBeNull()

    await user.click(screen.getByRole('radio', { name: 'Month' }))
    await screen.findByText(captionFor('month', 30))
    expect(screen.queryByText(captionFor('week', 30))).toBeNull()

    expectNoFabricatedNumbers(container)
  })

  it('will not leave a stale caption standing while a slow re-bucketing is in flight', async () => {
    const user = userEvent.setup()
    const slow = deferred<Response>()
    installBackend({
      activity: (url) => {
        // Only the re-bucketing hangs; the first read answers so there is a
        // previous answer for the placeholder to hold on to.
        if (granularityOf(url) === 'week') return slow.promise
        return json(series(granularityOf(url)))
      },
    })
    const { container } = renderLearningPage()
    await waitForData()

    expect(screen.getByText(captionFor('day', 30))).toBeInTheDocument()

    await user.click(screen.getByRole('radio', { name: 'Week' }))

    // The picture on screen is still the daily one, so a caption claiming a
    // weekly chart would outlive its filter. The page says so instead.
    expect(await screen.findByText(REDRAWING_CAPTION)).toBeInTheDocument()
    expect(screen.queryByText(captionFor('day', 30))).toBeNull()
    expect(screen.queryByText(captionFor('week', 30))).toBeNull()

    await act(async () => {
      slow.resolve(json(series('week')))
      await slow.promise
      await new Promise((resolve) => setTimeout(resolve, 50))
    })

    await screen.findByText(captionFor('week', 30))
    expect(screen.queryByText(REDRAWING_CAPTION)).toBeNull()
    expectNoFabricatedNumbers(container)
  })
})

describe('the learning dashboard pagers', () => {
  it('renders no pager at all when each list already fits on one page', async () => {
    installBackend()
    const { container } = renderLearningPage()
    await waitForData()

    // Two disabled buttons under a list that already fits is noise. Nothing
    // here is a boundary, so nothing here claims to be one.
    expectNoPager('Goal pages')
    expectNoPager('Skill pages')
    expectNoPager('Activity pages')
    expect(container.querySelector('nav')).toBeNull()

    // The rows and the counts are still there; only the control is absent. The
    // goal title is printed by its card, by the activity picker and by the
    // trail's name lookup, so this counts occurrences rather than demanding one.
    expect(screen.getAllByText('Reach level 4 on machine learning').length).toBeGreaterThan(0)
    expectNoFabricatedNumbers(container)
  })

  it('quotes the backend total beside the pager, not the length of the page', async () => {
    installBackend({
      goals: dualList({ items: GOAL_PAGE.items, total: 45 }, GOAL_PAGE),
      skills: dualList({ items: [SKILL_PAGE.items[0] as SkillRead], total: 41 }, SKILL_ALL),
      activities: dualList({ items: ACTIVITY_PAGE.items, total: 30 }, ACTIVITY_PAGE),
    })
    const { container } = renderLearningPage()
    await waitForData()

    // 45 goals behind two rows, 41 skills behind one, 30 activities behind two.
    // A pager quoting its own page as the total is the mistake this page exists
    // to avoid, so the number beside it is the filtered set's size.
    expect(within(pager('Goal pages')).getByText(`Page 1 of 3 · ${formatNumber(45)} goals`)).toBeInTheDocument()
    expect(within(pager('Skill pages')).getByText(`Page 1 of 3 · ${formatNumber(41)} skills`)).toBeInTheDocument()
    expect(
      within(pager('Activity pages')).getByText(
        `Page 1 of 3 · ${formatNumber(30)} recorded activities`,
      ),
    ).toBeInTheDocument()

    // The count is the filtered set's size, not the number of rows on screen.
    // Compared against the page's own length, so a `total` that happens to be a
    // substring of another figure (41 skills contains "1 skill") cannot confuse it.
    expect(pager('Goal pages').textContent).not.toContain(
      `${formatNumber(GOAL_PAGE.items.length)} goals`,
    )
    expect(pager('Skill pages').textContent).not.toContain(
      `${formatNumber(1)} skill ·`,
    )
    expect(pager('Activity pages').textContent).not.toContain(
      `${formatNumber(ACTIVITY_PAGE.items.length)} recorded activities`,
    )
    expectNoFabricatedNumbers(container)
  })

  it('disables Previous on the first page and Next on the last, and steps the offset', async () => {
    const user = userEvent.setup()
    const calls = installBackend({
      goals: dualList({ items: GOAL_PAGE.items, total: 45 }, GOAL_PAGE),
    })
    const { container } = renderLearningPage()
    await waitForData()

    const goals = pager('Goal pages')
    const previous = within(goals).getByRole('button', { name: 'Previous' })
    const next = within(goals).getByRole('button', { name: 'Next' })

    // Page one of three: there is nowhere back to go, and somewhere forward.
    expect(previous).toBeDisabled()
    expect(next).toBeEnabled()
    expect(offsetsOf(calls, '/learning/goals?', GOAL_PAGE_SIZE)).toEqual(['0'])

    await user.click(next)
    await waitFor(() =>
      expect(within(pager('Goal pages')).getByText(`Page 2 of 3 · ${formatNumber(45)} goals`)).toBeInTheDocument(),
    )
    // The middle page has both boundaries open.
    expect(within(pager('Goal pages')).getByRole('button', { name: 'Previous' })).toBeEnabled()
    expect(within(pager('Goal pages')).getByRole('button', { name: 'Next' })).toBeEnabled()
    await waitFor(() => expect(offsetsOf(calls, '/learning/goals?', GOAL_PAGE_SIZE)).toContain('20'))

    await user.click(within(pager('Goal pages')).getByRole('button', { name: 'Next' }))
    await waitFor(() =>
      expect(within(pager('Goal pages')).getByText(`Page 3 of 3 · ${formatNumber(45)} goals`)).toBeInTheDocument(),
    )

    // The last page disables Next and re-enables Previous — and the offsets the
    // backend was actually asked for are 0, 20 and 40, never a page count.
    expect(within(pager('Goal pages')).getByRole('button', { name: 'Previous' })).toBeEnabled()
    expect(within(pager('Goal pages')).getByRole('button', { name: 'Next' })).toBeDisabled()
    await waitFor(() =>
      expect(offsetsOf(calls, '/learning/goals?', GOAL_PAGE_SIZE)).toEqual(['0', '20', '40']),
    )

    // Only the card read paged. The `limit=200` complete read behind the pickers
    // is a different question and was never asked to page.
    expect(offsetsOf(calls, '/learning/goals?', PICKER_LIMIT)).toEqual(['0'])
    expectNoFabricatedNumbers(container)
  })

  it('pages the skill cards and the trail independently, each with its own page size', async () => {
    const user = userEvent.setup()
    const calls = installBackend({
      skills: dualList({ items: [SKILL_PAGE.items[0] as SkillRead], total: 41 }, SKILL_ALL),
      activities: dualList({ items: ACTIVITY_PAGE.items, total: 30 }, ACTIVITY_PAGE),
    })
    const { container } = renderLearningPage()
    await waitForData()

    await user.click(within(pager('Skill pages')).getByRole('button', { name: 'Next' }))
    await waitFor(() =>
      expect(within(pager('Skill pages')).getByText(`Page 2 of 3 · ${formatNumber(41)} skills`)).toBeInTheDocument(),
    )
    await waitFor(() => expect(offsetsOf(calls, '/learning/skills?', SKILL_PAGE_SIZE)).toContain('20'))

    await user.click(within(pager('Activity pages')).getByRole('button', { name: 'Next' }))
    await waitFor(() =>
      expect(
        within(pager('Activity pages')).getByText(
          `Page 2 of 3 · ${formatNumber(30)} recorded activities`,
        ),
      ).toBeInTheDocument(),
    )
    await waitFor(() => expect(offsetsOf(calls, '/learning/activities?', ACTIVITY_PAGE_SIZE)).toContain('12'))

    // Twenty rows to a page of skills, twelve to a page of the trail: the two
    // pagers are not one control wearing two labels.
    expect(offsetsOf(calls, '/learning/skills?', SKILL_PAGE_SIZE)).toEqual(['0', '20'])
    expect(offsetsOf(calls, '/learning/activities?', ACTIVITY_PAGE_SIZE)).toEqual(['0', '12'])
    expectNoFabricatedNumbers(container)
  })

  it('writes the page position into the URL and omits it for page one', async () => {
    const user = userEvent.setup()
    installBackend({ goals: dualList({ items: GOAL_PAGE.items, total: 45 }, GOAL_PAGE) })
    const { router } = renderLearningPage()
    await waitForData()

    expect(readParams(router.state.location.search).get('goal_page')).toBeNull()

    await user.click(within(pager('Goal pages')).getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(readParams(router.state.location.search).get('goal_page')).toBe('2'))

    // A link to the default view and a link to page one mean the same thing,
    // which is what keeps a shared URL honest.
    await user.click(within(pager('Goal pages')).getByRole('button', { name: 'Previous' }))
    await waitFor(() => expect(readParams(router.state.location.search).get('goal_page')).toBeNull())
  })

  it('drops an out-of-range page instead of sitting disabled on both sides', async () => {
    const calls = installBackend({
      goals: dualList({ items: GOAL_PAGE.items, total: 45 }, GOAL_PAGE),
    })
    const { router, container } = renderLearningPage('/learning?goal_page=9')
    await waitForData()

    // A link can arrive at a page the data no longer has. Page 9 of 3 would
    // leave both buttons disabled under a list that does not exist, so the
    // param is dropped and the goals return to the first page.
    //
    // Waited for, not asserted immediately: `?goal_page=9` is honoured until the
    // backend's `total` has answered, so there is a real moment where the pager
    // reads "Page 9 of 3". The clamp cannot happen before it knows how many
    // pages exist, and the reader must never be stranded there.
    expect(
      await screen.findByText(`Page 1 of 3 · ${formatNumber(45)} goals`, undefined, {
        timeout: DATA_TIMEOUT_MS,
      }),
    ).toBeInTheDocument()
    expect(within(pager('Goal pages')).getByRole('button', { name: 'Previous' })).toBeDisabled()
    expect(within(pager('Goal pages')).getByRole('button', { name: 'Next' })).toBeEnabled()
    expect(readParams(router.state.location.search).get('goal_page')).toBeNull()
    expect(screen.queryByText(/Page 9 of/)).toBeNull()

    // The first page's offset is the one the backend ends up being asked for.
    // The out-of-range offset=160 does go out first — the URL is not known to
    // be stale until the total has answered — and is then replaced.
    await waitFor(() => expect(offsetsOf(calls, '/learning/goals?', GOAL_PAGE_SIZE)).toContain('0'))
    expectNoFabricatedNumbers(container)
  })

  it('has dropped the "Showing the first N goals of M" prose the pager replaces', async () => {
    const user = userEvent.setup()
    installBackend({
      goals: dualList({ items: GOAL_PAGE.items, total: 45 }, GOAL_PAGE),
    })
    const { container } = renderLearningPage()
    await waitForData()
    await user.click(within(pager('Goal pages')).getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(screen.getByText(/Page 2 of 3/)).toBeInTheDocument())

    // The pager replaced that sentence: it named a page length where there is
    // now a position, and "the first N" was wrong the moment the reader moved.
    expect(container.textContent ?? '').not.toMatch(/Showing the first/i)
    expect(container.textContent ?? '').not.toMatch(/first \d+ of \d+ goals/i)
    expectNoFabricatedNumbers(container)
  })

  it('withholds the by-type breakdown once the trail no longer fits on one page', async () => {
    installBackend({
      activities: dualList({ items: ACTIVITY_PAGE.items, total: 30 }, ACTIVITY_PAGE),
    })
    const { container } = renderLearningPage()
    await waitForData()

    // Counting activity_type across one page of thirty would report fewer
    // events than the window holds while reading as the whole of it.
    expect(screen.getByText('2 activities shown, newest first.')).toBeInTheDocument()
    // Stated twice — once by the trail card and once beneath it — because the
    // reader must be told the trail is a position and not the whole record.
    expect(
      screen.getAllByText(
        'Showing 2 of 30 recorded activities. This is a position in the list, not the whole trail.',
      ),
    ).toHaveLength(2)
    expect(screen.queryByText('What the trail is made of')).toBeNull()
    expectNoFabricatedNumbers(container)
  })

  it('never prints NaN, Infinity or an undefined figure with a pager in place', async () => {
    installBackend({
      goals: dualList({ items: GOAL_PAGE.items, total: 45 }, GOAL_PAGE),
      skills: dualList({ items: [SKILL_PAGE.items[0] as SkillRead], total: 41 }, SKILL_ALL),
      activities: dualList({ items: ACTIVITY_PAGE.items, total: 30 }, ACTIVITY_PAGE),
    })
    const { container } = renderLearningPage()
    await waitForData()
    await resolveCharts(container)
    await settle()

    expect(pager('Goal pages')).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
    // A count beside a pager is a count of records, not a claim about ability.
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/not good at|you are weak|you are bad at|weak at|poor at/i)
    expect(text).not.toMatch(/average level|strongest skill|readiness score/i)
  })
})

describe('the learning dashboard all-time per-skill chart', () => {
  it('says it is all time and that no control above it moves it', async () => {
    installBackend({ skills: dualList({ items: [SKILL_PAGE.items[0] as SkillRead], total: 41 }, SKILL_ALL) })
    const { container } = renderLearningPage()
    await waitForData()
    await resolveCharts(container)

    const section = document.getElementById('learning-by-skill-all-time')?.closest('section') as HTMLElement
    expect(within(section).getByRole('heading', { name: 'Recorded activity by skill — all time' })).toBeInTheDocument()
    expect(
      within(section).getByText(
        'This is the one figure on the page that ignores the window and the grain above. It counts ' +
          'every learning activity ever recorded against each tracked skill, so neither control ' +
          'changes it, and it covers every skill on the account rather than the ones on this page.',
      ),
    ).toBeInTheDocument()
    // Once the lazy chart has landed the frame carries the chart's own subtitle,
    // which repeats "all time" where the reader is actually looking. (The
    // longer frame description — including "a skill with nothing recorded
    // against it shows zero, which is a measurement rather than a missing
    // value" — belongs to the `LazyChart` fallback, which the resolved chart
    // replaces by design.)
    expect(
      within(section).getByText('Learning activities recorded against each tracked skill, all time.'),
    ).toBeInTheDocument()

    // The claim is load-bearing, so it must not also read as a windowed figure.
    expect(section.textContent ?? '').not.toContain(describeWindow(30))
    expectNoFabricatedNumbers(container)
  })

  it('is drawn from every tracked skill, not from the page of cards', async () => {
    installBackend({ skills: dualList({ items: [SKILL_PAGE.items[0] as SkillRead], total: 41 }, SKILL_ALL) })
    const { container } = renderLearningPage()
    await waitForData()
    await resolveCharts(container)

    // `Rust` is in the `limit=200` read and not on the `limit=20` page, so it is
    // absent from the cards — the all-time section is the only place on screen
    // that can name it, and it can only do that from the complete set.
    expect(screen.queryByRole('heading', { name: 'Rust' })).toBeNull()
    const section = document.getElementById('learning-by-skill-all-time')?.closest('section') as HTMLElement
    expect(section.textContent).toContain('Rust')
    expect(container.querySelector('[data-chart-surface]')).not.toBeNull()
  })

  it('does not move when the grain or the window does', async () => {
    const user = userEvent.setup()
    const calls = installBackend({
      activity: echoingSeries(),
      skills: dualList({ items: [SKILL_PAGE.items[0] as SkillRead], total: 41 }, SKILL_ALL),
    })
    const { container, router } = renderLearningPage()
    await waitForData()
    await resolveCharts(container)

    const section = document.getElementById('learning-by-skill-all-time')?.closest('section') as HTMLElement
    const before = section.textContent

    await user.click(screen.getByRole('radio', { name: 'Week' }))
    await screen.findByText(captionFor('week', 30))
    expect(section.textContent).toBe(before)

    await user.click(screen.getByRole('button', { name: '7 days' }))
    await waitFor(() => expect(readParams(router.state.location.search).get('range')).toBe('7d'))
    expect(section.textContent).toBe(before)

    // The windowed panels above it did move, so this is a control being ignored
    // rather than a page that has stopped reading anything.
    await waitFor(() =>
      expect(getCalls(calls, '/learning/summary?').some((call) => call.url.includes('window_days=7'))).toBe(true),
    )
    expectNoFabricatedNumbers(container)
  })
})
