import type { ReactElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/tooltip'
import LearningPage from '@/pages/learning-page'
import { NO_VALUE, formatNumber } from '@/features/analytics/format'
import { formatLevelOfScale, levelSourcePhrase } from '@/features/learning/components'
import { NOT_ENOUGH_DATA_TITLE } from '@/features/learning/components/learning-vocabulary'
import { queryRetryPolicy } from '@/app/query-client'
import type { ApiErrorEnvelope } from '@/types/api'
import type {
  LearningActivityBucketRead,
  LearningActivityListRead,
  LearningActivityRead,
  LearningActivitySeriesRead,
  LearningGoalListRead,
  LearningGoalRead,
  LearningSummaryRead,
  SkillGapRead,
  SkillListRead,
  SkillRead,
  SkillLevelSource,
} from '@/types/learning'

/**
 * The Learning dashboard, asserted at the network boundary.
 *
 * The page is mounted for real — real router, real components, real hooks — and
 * only `fetch` is stubbed. Every figure on screen is therefore a body this file
 * wrote, so the numbers can be checked by hand: 3 activities inside a 30-day
 * window, 11 recorded all time, 2 tracked skills and 135 recorded minutes.
 *
 * **The query client is local, and that is the point.** `AppProviders` mounts the
 * shared singleton and registers `onSessionChange(() => queryClient.clear())`
 * (`src/app/auth-bootstrap.tsx:13`). In jsdom that clear lands mid-test and
 * strands every component at `pending` forever, which is why this suite builds a
 * fresh client per render. The defaults below are the ones in
 * `src/app/query-client.ts`, carried over rather than relaxed: the retry policy in
 * particular is what makes the error surfaces arrive after a few seconds rather
 * than on the first response, and the 5xx case below waits with an explicit
 * `{ timeout: 20_000 }` because of it.
 *
 * Recharts is given a size and only a size — jsdom has no layout engine, so
 * `ResponsiveContainer` measures 0×0 and every chart would come back as an empty
 * box. The mock replaces exactly that measurement and tags what it wraps in
 * `data-chart-surface`, and no assertion below reaches into a recharts internal.
 *
 * **Two traps this file is written around, both real.** The charts sit behind the
 * real `LazyChart` boundary, which paints the *same* card title as the chart it
 * replaces, so {@link resolveCharts} waits on the fallback's skeleton leaving the
 * document before any chart assertion. And `SkillGapList` — like every card here —
 * paints its own title before its rows exist, so a `findByRole('heading')` on a
 * card title is not a wait for its data: {@link waitForData} waits on a string
 * that can only be on screen once a read has been answered.
 *
 * The dates are fixed in 2019 — a completed year — and every goal fixture leaves
 * `target_date` null, because a target date is rendered relative to today and
 * nothing here asserts on a day count that would depend on the machine's clock.
 *
 * The three forms are asserted for what they must **not** send as much as for
 * what they do: a skill created here carries no `level_source`, and an activity
 * recorded here carries no `source_type` or `source_id`. Both are NEXUS's to
 * derive, and a client that could set them would be forging the provenance of
 * every row on this page.
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

/** The retry policy genuinely backs off; this is not generous for comfort. */
const RETRY_SURFACE_TIMEOUT_MS = 20_000

/**
 * How long the first read is given to land.
 *
 * The page mounts a lazily imported chart alongside every panel, and jsdom
 * evaluates those modules on the same event loop the assertions run on, so the
 * first commit of a fully loaded page is measured in hundreds of milliseconds
 * rather than the single-digit ones a unit test would assume. Generous here
 * cannot mask a defect: every assertion after it is the real check, and a
 * region that never renders fails on its own text rather than on this bound.
 */
const DATA_TIMEOUT_MS = 10_000

const SKILL_ID = '11111111-1111-4111-8111-111111111111'
const SKILL_TWO_ID = '22222222-2222-4222-8222-222222222222'
const GOAL_ID = '33333333-3333-4333-8333-333333333333'
const COMPLETED_GOAL_ID = '44444444-4444-4444-8444-444444444444'
const ACTIVITY_ID = '55555555-5555-4555-8555-555555555555'
const VIEWED_ID = '66666666-6666-4666-8666-666666666666'
const UNMEASURED_REASON =
  'No level has ever been recorded for this skill, so there is no distance to a target to measure.'

/* ------------------------------------------------------------------ harness */

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
  details: Record<string, unknown> | null = null,
): Response {
  const body: ApiErrorEnvelope = {
    error: { code, message, details, request_id: requestId },
  }
  return json(body, status)
}

type Route = (url: string) => Response | Promise<Response>

interface Call {
  url: string
  method: string
  body: unknown
}

interface Backend {
  summary?: Route
  activity?: Route
  gaps?: Route
  goals?: Route
  skills?: Route
  activities?: Route
  createGoal?: Route
  createSkill?: Route
  createActivity?: Route
}

/**
 * Stubs `fetch` with the routing table below, so one test can replace a single
 * endpoint — the failing gap list, the cold account, the refused goal — without
 * restating the rest. `/learning/activities` is matched before `/learning/activity`
 * because the former contains the latter.
 */
function installBackend(overrides: Backend = {}): Call[] {
  const summary = overrides.summary ?? (() => json(SUMMARY))
  const activity = overrides.activity ?? (() => json(SERIES))
  const gaps = overrides.gaps ?? (() => json(GAPS))
  const goals = overrides.goals ?? (() => json(GOALS))
  const skills = overrides.skills ?? (() => json(SKILLS))
  const activities = overrides.activities ?? (() => json(ACTIVITIES))
  const createGoal = overrides.createGoal ?? (() => json(goal()))
  const createSkill = overrides.createSkill ?? (() => json(skill({ name: 'Rust' })))
  const createActivity = overrides.createActivity ?? (() => json(learningActivity()))

  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      const rawBody = init?.body
      const body = typeof rawBody === 'string' ? (JSON.parse(rawBody) as unknown) : null
      calls.push({ url, method, body })

      if (method === 'POST' && url.includes('/learning/goals')) return createGoal(url)
      if (method === 'POST' && url.includes('/learning/skills')) return createSkill(url)
      if (method === 'POST' && url.includes('/learning/activities')) return createActivity(url)
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

/** The card a title belongs to. */
function cardFor(name: string): HTMLElement {
  return screen.getByRole('heading', { name }).closest('div.rounded-lg') as HTMLElement
}

/**
 * A `MetricCard` tile, found through its label inside the scope that owns it.
 *
 * `MetricCard` prints its label as a small uppercase paragraph rather than a
 * heading — it is a figure with a caption, not a section — and two of the five
 * labels ("Goals", "Skills") are also section headings further down the page, so
 * the tile is located the way a reader finds it rather than by role or globally.
 */
function tileFor(label: string, scope: HTMLElement): HTMLElement {
  return within(scope).getByText(label).closest('div.rounded-lg') as HTMLElement
}

/** The card the five summary tiles live in. */
function summaryCard(): HTMLElement {
  return cardFor('Learning summary')
}

/**
 * The region a heading owns, so a label cannot be found twice on the page.
 *
 * A `<section>` when the region is one, and the card itself when it is a card
 * placed directly in a grid — the gap list and the deadlines panel are the
 * latter.
 */
function regionFor(heading: string): HTMLElement {
  const node = screen.getByRole('heading', { name: heading })
  return (node.closest('section') ?? node.closest('div.rounded-lg')) as HTMLElement
}

/**
 * Waits until the first read has landed everywhere, not merely the page chrome.
 *
 * `findByRole('heading', { name: 'Skill gaps' })` is **not** that wait: a card
 * paints its own title before its rows exist, so a `getBy*` straight afterwards
 * races the fetch and fails. These three strings — the server's summary sentence,
 * a skill name and a recorded activity title — can only be on screen once a read
 * has been answered.
 */
async function waitForData(): Promise<void> {
  // Three strings from three different endpoints — the summary sentence, a gap
  // explanation and a recorded activity title — so the wait really is "every
  // read has landed", not "one has".
  await screen.findByText(SUMMARY.summary, undefined, { timeout: DATA_TIMEOUT_MS })
  await screen.findByText(GAP_EXPLANATION, undefined, { timeout: DATA_TIMEOUT_MS })
  await screen.findByText('Read the chapter on regularisation', undefined, {
    timeout: DATA_TIMEOUT_MS,
  })
}

/**
 * The only level rendering this surface is allowed: `3 of 5`, and the phrase
 * naming who set it, in the same sentence.
 *
 * Asserted as an exact string rather than a substring, because a substring check
 * would still pass if the same paragraph also printed a bare `3` beside it — and
 * a level nobody can attribute is not a level.
 */
function expectLevelClaim(scope: HTMLElement, level: number, source: SkillLevelSource): void {
  expect(scope.textContent).toContain(`${formatLevelOfScale(level)}, ${levelSourcePhrase(source)}`)
  const bare = [...scope.querySelectorAll('p, span, li')]
    .map((node) => (node.textContent ?? '').trim())
    .filter((text) => text === formatLevelOfScale(level))
  expect(bare).toEqual([])
}

function getCalls(calls: Call[], needle: string): Call[] {
  return calls.filter((call) => call.url.includes(needle))
}

function getPosts(calls: Call[], needle: string): Call[] {
  return getCalls(calls, needle).filter((call) => call.method === 'POST')
}

/** Lets every in-flight fetch and its re-render settle before asserting. */
async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50))
  })
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
  const start = `2019-01-${String(day).padStart(2, '0')}T00:00:00Z`
  return {
    bucket_start: start,
    bucket_end: `2019-01-${String(day + 1).padStart(2, '0')}T00:00:00Z`,
    activities: 1,
    sessions: 1,
    minutes: 45,
    ...overrides,
  }
}

/** The backend's own gap sentence, which the page must print verbatim. */
const GAP_EXPLANATION =
  'Target 4/5, current self-assessed 2/5. NEXUS recorded 6 related learning activities in the last 30 days.'

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

/** A cold account: real zeroes, and the flag that says they are an absence. */
const COLD_SUMMARY: LearningSummaryRead = {
  ...SUMMARY,
  goal_count: 0,
  active_goal_count: 0,
  completed_goal_count: 0,
  skill_count: 0,
  activity_count: 0,
  activities_in_window: 0,
  minutes_in_window: 0,
  latest_activity_at: null,
  has_data: false,
  summary: 'No learning activity has been recorded yet.',
}

const SERIES: LearningActivitySeriesRead = {
  granularity: 'day',
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
}

/** Two rows the page itself is able to count, so the breakdown can be published. */
const ACTIVITIES: LearningActivityListRead = {
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
  limit: 12,
  offset: 0,
}

const SKILLS: SkillListRead = {
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
  limit: 50,
  offset: 0,
}

/**
 * One measurable gap and one whose levels could not be compared.
 *
 * `GET /learning/gaps` answers a bare array, so "nothing has been recorded at
 * all" is the answer on a cold account and the page has to be able to say it
 * instead of drawing an empty chart.
 */
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

const COMPLETED_GOAL = goal({
  id: COMPLETED_GOAL_ID,
  title: 'Work through the SQL handbook',
  status: 'completed',
  progress: 100,
  target_skill_id: SKILL_TWO_ID,
  completed_at: '2019-01-29T14:05:00Z',
})

const GOALS: LearningGoalListRead = {
  items: [goal(), COMPLETED_GOAL],
  total: 2,
  limit: 50,
  offset: 0,
}

/* -------------------------------------------------------------------- tests */

describe('the learning dashboard', () => {
  it('leads with counts of records and the sentence the server composed for them', async () => {
    const calls = installBackend()
    const { container } = renderLearningPage()

    await waitForData()
    await resolveCharts(container)

    expect(screen.getByRole('heading', { level: 1, name: 'Learning' })).toBeInTheDocument()
    // The badge quotes the window the *server* answered for. The default preset
    // sends no `window_days` at all, so the client cannot know its length.
    expect(screen.getByText('Figures cover the last 30 days')).toBeInTheDocument()
    expect(getCalls(calls, '/learning/summary')[0]?.url).not.toContain('window_days')

    const tiles = summaryCard()
    expect(within(tiles).getByText(SUMMARY.summary)).toBeInTheDocument()
    expect(within(tileFor('Goals', tiles)).getByText(formatNumber(2))).toBeInTheDocument()
    expect(within(tileFor('Goals still open', tiles)).getByText(formatNumber(1))).toBeInTheDocument()
    expect(within(tileFor('Skills', tiles)).getByText(formatNumber(2))).toBeInTheDocument()
    expect(
      within(tileFor(`Activities, last ${formatNumber(30)} days`, tiles)).getByText(formatNumber(3)),
    ).toBeInTheDocument()
    expect(within(tileFor('Recorded minutes', tiles)).getByText(formatNumber(135))).toBeInTheDocument()

    // Every tile names what it counts, so a bare `3` cannot be read as hours.
    expect(
      within(tiles).getByText(
        'Minutes attached to those activities. Zero means every one was an event, not a span',
      ),
    ).toBeInTheDocument()

    // No tile claims a rate, a streak or an outcome.
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/\d+\s*hours?\s*(focused|spent|studied|worked)/i)
    expect(text).not.toMatch(/productivity|unproductive|lazy|burnout|retention/i)
    expectNoFabricatedNumbers(container)
  })

  it('never renders a skill level as a bare number, on either card or gap row', async () => {
    installBackend()
    renderLearningPage()
    await waitForData()

    const skills = regionFor('Skills')
    expect(within(skills).getByText('2 of 5, self-assessed by you')).toBeInTheDocument()
    expect(
      within(skills).getByText('4 of 5, estimated by NEXUS from recorded activities'),
    ).toBeInTheDocument()

    // Both sources are chip-labelled, and there is no third, unattributed state.
    expect(within(skills).getByText('Self-assessed')).toBeInTheDocument()
    expect(within(skills).getByText('NEXUS estimate')).toBeInTheDocument()

    // The gap row runs through the same attributing renderer.
    const gapRow = within(regionFor('Skill gaps'))
      .getByText('Machine Learning')
      .closest('li') as HTMLElement
    expect(gapRow.textContent).toContain('Current 2 of 5, self-assessed by you')
    expect(gapRow.textContent).toContain('target 4 / 5, the level you set')

    for (const [name, level, source] of [
      ['Machine Learning', 2, 'user_defined'],
      ['SQL', 4, 'system_estimate'],
    ] as Array<[string, number, SkillLevelSource]>) {
      expectLevelClaim(cardFor(name), level, source)
    }
  })

  it('renders the skill-gap explanation verbatim, with its digits intact', async () => {
    installBackend()
    const { container } = renderLearningPage()
    await waitForData()

    const gaps = regionFor('Skill gaps')
    expect(
      within(gaps).getByText(
        'Target 4/5, current self-assessed 2/5. NEXUS recorded 6 related learning activities in the last 30 days.',
      ),
    ).toBeInTheDocument()
    expect(
      within(gaps).getByText('2 skills with a recorded target, counted over the last 30 days.'),
    ).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })

  it('shows an unmeasurable gap as its reason, with no level and no figure beside it', async () => {
    installBackend()
    const { container } = renderLearningPage()
    await waitForData()

    const gaps = regionFor('Skill gaps')
    const row = within(gaps).getByText('Rust').closest('li') as HTMLElement
    expect(within(row).getByText(NOT_ENOUGH_DATA_TITLE)).toBeInTheDocument()
    expect(within(row).getByText(UNMEASURED_REASON)).toBeInTheDocument()
    expect(
      within(row).getByText(
        'No gap could be computed for Rust because no level has ever been recorded against it.',
      ),
    ).toBeInTheDocument()
    // A number beside "not measured" is the failure `available` exists to stop.
    expect(row.textContent).not.toMatch(/Current \d+ of 5/)
    expect(row.textContent).not.toMatch(/levels between/)
    expect(within(row).queryByText(NO_VALUE)).toBeNull()

    // The measurable gap beside it is untouched.
    const measured = within(gaps).getByText('Machine Learning').closest('li') as HTMLElement
    expect(measured.textContent).toContain('2 levels between the recorded level and the target')
    expectNoFabricatedNumbers(container)
  })

  it('renders a genuine zero as 0 and says whose figure it is', async () => {
    installBackend({
      summary: () =>
        json({
          ...SUMMARY,
          minutes_in_window: 0,
          latest_activity_at: null,
          summary: '3 learning activities were recorded in the last 30 days; none carried a duration.',
        }),
      goals: () =>
        json({
          ...GOALS,
          items: [goal({ progress: 0 }), COMPLETED_GOAL],
        } satisfies LearningGoalListRead),
    })
    const { container } = renderLearningPage()

    await screen.findByText('3 learning activities were recorded in the last 30 days; none carried a duration.')
    await screen.findByText(/0% — the progress figure is the one you set, and it has not moved\./)

    // Zero recorded minutes is a measurement: every activity in the window was
    // an event rather than a span.
    expect(
      screen.getByText(
        /The minutes figure is zero because every activity in the window was an event rather than a span — that is a measurement, not a missing value\./,
      ),
    ).toBeInTheDocument()
    const minutes = tileFor('Recorded minutes', summaryCard())
    expect(within(minutes).getByText(formatNumber(0))).toBeInTheDocument()
    expect(within(minutes).queryByText(NO_VALUE)).toBeNull()

    // Zero progress is a measurement too: not started, and the figure is yours.
    expect(screen.getByText('0%')).toBeInTheDocument()
    const card = cardFor('Reach level 4 on machine learning')
    expect(card.textContent).not.toContain(NOT_ENOUGH_DATA_TITLE)
    expect(within(card).queryByText(NO_VALUE)).toBeNull()
    expectNoFabricatedNumbers(container)
  })

  it('separates open goals from completed ones, and never invents a deadline', async () => {
    installBackend()
    const { container } = renderLearningPage()
    await waitForData()

    expect(screen.getByRole('heading', { name: 'Still open' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Completed' })).toBeInTheDocument()
    // The completion instant is stamped by the server and recorded with the row.
    expect(screen.getByText(/^Completed on /)).toBeInTheDocument()
    expect(screen.getByText('Work through the SQL handbook')).toBeInTheDocument()

    // A goal with no target date has no deadline to be approaching, so it is not
    // on the deadlines list and "0 days" is never printed for it.
    expect(
      screen.getByText(
        'No open goal carries a target date. A deadline is yours to set: add one to a goal and it ' +
          'appears here with the days remaining.',
      ),
    ).toBeInTheDocument()
    expect(regionFor('Upcoming deadlines').textContent).not.toMatch(/\b0 days\b/)

    const card = cardFor('Reach level 4 on machine learning')
    expect(card.textContent).toContain('No target date set.')
    expect(card.textContent).toContain(
      'No effort estimate recorded — the estimate on a goal is yours, and none was given.',
    )
    expect(within(card).queryByText(NO_VALUE)).toBeNull()
    expectNoFabricatedNumbers(container)
  })

  it('keeps the recorded trail as events, and names where each came from', async () => {
    installBackend()
    const { container } = renderLearningPage()
    await waitForData()

    const trail = regionFor('Recorded activity')
    expect(within(trail).getByText('2 activities shown, newest first.')).toBeInTheDocument()
    // Stated twice — once by the trail card and once beneath it — because the
    // trail has no pagination control and a reader must be told it is whole.
    expect(
      screen.getAllByText('Every activity that has been recorded is shown here.'),
    ).toHaveLength(2)
    expect(within(trail).getByText('Study session')).toBeInTheDocument()
    expect(within(trail).getByText('Resource viewed')).toBeInTheDocument()
    // A null pair means the person typed it in; nothing else does.
    expect(within(trail).getAllByText('Entered by you.').length).toBeGreaterThanOrEqual(2)

    // A page that was opened has no length, and no `0m` is printed for it.
    expect(
      within(trail).getByText('Recorded as an event — no duration was attached to it.'),
    ).toBeInTheDocument()
    expect(
      within(trail).getByText(
        'A resource was opened. This is the weakest kind of evidence in the set — it records that ' +
          'a page was viewed and nothing more, so it is weighted accordingly and grouped apart.',
      ),
    ).toBeInTheDocument()

    // A commit is a code event, not a task completion.
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/\b\d+\s*(tasks|projects)\s+(completed|delivered)\b/i)
    expectNoFabricatedNumbers(container)
  })

  it('plots the window and the per-skill breakdown with its own evidence', async () => {
    const calls = installBackend()
    const { container } = renderLearningPage()

    await waitForData()
    await resolveCharts(container)

    expect(screen.getByText('Days with recorded activity')).toBeInTheDocument()
    expect(container.querySelector('[data-chart-surface]')).not.toBeNull()
    // The run counts consecutive buckets carrying an activity and stops at the
    // first empty one, and states how many of the window's buckets carried one.
    expect(
      screen.getByText(/1 consecutive day with recorded activity, ending 9 Jan 2019\./),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/2 of the 3 days in the window carried at least one recorded activity\./),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        'Learning activities recorded against each tracked skill, all time.',
      ),
    ).toBeInTheDocument()

    // The trail on screen covers the whole window, so the by-type breakdown is
    // published; both kinds stay separate rows rather than being merged.
    expect(screen.getByText('What the trail is made of')).toBeInTheDocument()
    expect(
      screen.getByText(
        'Counts of recorded events, each kind listed separately. None of them is a measure of ' +
          'understanding, and a page that was opened is not the same evidence as a concept recorded.',
      ),
    ).toBeInTheDocument()

    // The window lives in the URL and the default preset sends no `window_days`.
    expect(getCalls(calls, '/learning/activity?')[0]?.url).toContain('granularity=day')
    expect(getCalls(calls, '/learning/activity?')[0]?.url).not.toContain('window_days')
    expect(getCalls(calls, '/learning/gaps')[0]?.url).not.toContain('window_days')
    expectNoFabricatedNumbers(container)
  })

  it('never sends a user id, because ownership is the server’s alone', async () => {
    const calls = installBackend()
    renderLearningPage()
    await waitForData()

    expect(calls.length).toBeGreaterThan(0)
    for (const call of calls) {
      expect(call.url).not.toMatch(/[?&]user_id=/)
      expect(call.url).not.toMatch(/[?&]owner_id=/)
    }
  })

  it('draws silhouettes with no digits while the first read is in flight', () => {
    // Six reads that never settle, so the page is caught in its loading state
    // rather than in a state the test has to keep in sync with.
    const never = (): Promise<Response> => new Promise(() => undefined)
    installBackend({
      summary: never,
      activity: never,
      gaps: never,
      goals: never,
      skills: never,
      activities: never,
    })
    renderLearningPage()

    // The masthead paints immediately; a blank page while the reads land is what
    // the skeletons exist to avoid.
    expect(screen.getByRole('heading', { level: 1, name: 'Learning' })).toBeInTheDocument()
    expect(screen.getAllByText(/^Loading /).length).toBeGreaterThan(0)

    // Nothing in a busy region reads as a figure. A grey `0` on a tile that may
    // well read "Not enough data yet." is a number, and this surface never shows
    // a number it does not have.
    const busy = screen.getAllByRole('status')
    expect(busy.length).toBeGreaterThanOrEqual(5)
    for (const region of busy) {
      expect(region.textContent ?? '').not.toMatch(/\d/)
    }
    // Card titles are painted from the start, so they prove nothing; the absence
    // of the server's sentence does.
    expect(screen.queryByText(SUMMARY.summary)).toBeNull()
    expect(screen.queryByText('Machine Learning')).toBeNull()
  })

  it('states an account that has recorded nothing, and fabricates no totals for it', async () => {
    installBackend({
      summary: () => json(COLD_SUMMARY),
      activity: () => json({ ...SERIES, buckets: [], total_activities: 0, total_minutes: null }),
      gaps: () => json([] satisfies SkillGapRead[]),
      goals: () => json({ items: [], total: 0, limit: 50, offset: 0 } satisfies LearningGoalListRead),
      skills: () => json({ items: [], total: 0, limit: 50, offset: 0 } satisfies SkillListRead),
      activities: () =>
        json({ items: [], total: 0, limit: 12, offset: 0 } satisfies LearningActivityListRead),
    })
    const { container } = renderLearningPage()

    expect(await screen.findByText('No skills tracked yet')).toBeInTheDocument()
    expect(screen.getAllByText('No learning goals yet').length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText('Nothing recorded yet').length).toBeGreaterThan(0)

    // Six zeroes across the top of an empty account would read as a measurement,
    // and the engine finding nothing is the opposite of one — so the tile row is
    // replaced by the empty state that explains what has to be recorded first.
    const tiles = regionFor('Overview')
    expect(tiles.textContent).toContain(NOT_ENOUGH_DATA_TITLE)
    expect(
      tiles.textContent,
    ).toContain(
      'The counts above are read from recorded learning activities and goals. They stay at ' +
        'nothing until there is at least one of either, because a dashboard of zeroes is not a ' +
        'measurement.',
    )
    expect(within(tiles).queryByText('Recorded minutes')).toBeNull()
    expect(within(tiles).queryByText('Goals still open')).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Machine Learning' })).toBeNull()
    expectNoFabricatedNumbers(container)
  })

  it('reports a 5xx gap read with a retry that asks again and recovers', async () => {
    const user = userEvent.setup()
    let failing = true
    installBackend({
      gaps: () =>
        failing
          ? envelope('internal_error', 'The gap service is unavailable.', 500, 'req-learn-1')
          : json(GAPS),
    })
    renderLearningPage()
    await screen.findByText(SUMMARY.summary)

    // The shared retry policy asks twice more with a backoff, so the error
    // surface cannot arrive inside the default 5 s budget.
    const alert = await screen.findByRole('alert', {}, { timeout: RETRY_SURFACE_TIMEOUT_MS })
    expect(alert).toHaveTextContent('the gap list could not be loaded')
    expect(alert).toHaveTextContent(
      'The failure was recorded on the server. Retry, and quote the request ID below.',
    )
    expect(alert).toHaveTextContent('The gap service is unavailable.')
    expect(alert).toHaveTextContent('req-learn-1')

    // A failed read is distinguishable from an empty one: no gap is invented and
    // no empty state claims there was simply nothing to compare.
    const gaps = regionFor('Skill gaps')
    expect(within(gaps).queryByText(NOT_ENOUGH_DATA_TITLE)).toBeNull()
    expect(within(gaps).queryByText('Machine Learning')).toBeNull()

    // The rest of the page is untouched by one failed region.
    expect(within(regionFor('Skills')).getByText('2 of 5, self-assessed by you')).toBeInTheDocument()

    failing = false
    await user.click(screen.getByRole('button', { name: /Retry/i }))

    expect(await within(gaps).findByText(UNMEASURED_REASON)).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  })

  it('surfaces a 422 on the goal form under the field the server named', async () => {
    const user = userEvent.setup()
    const calls = installBackend({
      createGoal: () =>
        envelope('validation_error', 'That goal could not be stored.', 422, 'req-learn-2', {
          errors: [
            { field: 'title', message: 'The title must be 200 characters or fewer.' },
            { field: 'target_date', message: 'That date is not a real calendar date.' },
          ],
        }),
    })
    renderLearningPage()
    await waitForData()

    await user.click(screen.getByRole('button', { name: 'New goal' }))

    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Title'), 'Reach level 4 on machine learning')
    await user.click(within(dialog).getByRole('button', { name: 'Save goal' }))

    // The server's field errors are placed under the fields they name, and the
    // input itself is marked invalid.
    const titleError = await within(dialog).findByText(
      'The title must be 200 characters or fewer.',
    )
    expect(
      within(titleError.parentElement as HTMLElement).getByLabelText('Title'),
    ).toHaveAttribute('aria-invalid', 'true')
    expect(within(dialog).getByText('That date is not a real calendar date.')).toBeInTheDocument()

    // Field-scoped errors are not repeated as a banner: the reader is told once,
    // next to the thing they must change.
    expect(within(dialog).queryByText('That goal could not be stored.')).toBeNull()
    expect(getPosts(calls, '/learning/goals')).toHaveLength(1)
  })

  it('writes down a goal without a progress figure NEXUS would have to invent', async () => {
    const user = userEvent.setup()
    const calls = installBackend()
    renderLearningPage()
    await waitForData()

    await user.click(screen.getByRole('button', { name: 'New goal' }))

    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Title'), 'Learn regularisation properly')
    await user.type(within(dialog).getByLabelText(/^Topic/), 'Regularisation')
    await user.selectOptions(within(dialog).getByLabelText('Priority'), 'high')
    await user.type(within(dialog).getByLabelText(/^Estimated effort/), '120')
    await user.click(within(dialog).getByRole('button', { name: 'Save goal' }))

    await waitFor(() => expect(getPosts(calls, '/learning/goals')).toHaveLength(1))
    const post = getPosts(calls, '/learning/goals')[0]
    expect(post?.body).toMatchObject({
      title: 'Learn regularisation properly',
      target_topic: 'Regularisation',
      priority: 'high',
      estimated_effort_minutes: 120,
    })
    // `progress` is not on the form at all: a percentage NEXUS set would be a
    // claim about commitment rather than about progress.
    expect(post?.body).not.toHaveProperty('progress')
    expect(post?.body).not.toHaveProperty('status')
    expect(post?.body).not.toHaveProperty('completed_at')
  })

  it('creates a skill without claiming who derived its level', async () => {
    const user = userEvent.setup()
    const calls = installBackend()
    renderLearningPage()
    await waitForData()

    await user.click(screen.getByRole('button', { name: 'Add a skill' }))

    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Name'), 'Rust')
    await user.selectOptions(within(dialog).getByLabelText('Current level (1–5)'), '1')
    await user.selectOptions(within(dialog).getByLabelText('Target level (1–5)'), '4')
    await user.click(within(dialog).getByRole('button', { name: 'Add skill' }))

    await waitFor(() => expect(getPosts(calls, '/learning/skills')).toHaveLength(1))
    const post = getPosts(calls, '/learning/skills')[0]
    expect(post?.body).toMatchObject({ name: 'Rust', current_level: 1, target_level: 4 })

    // **`level_source` is never sent.** It defaults to `user_defined`
    // server-side, so a skill created from this form is attributed to the
    // person who filled it in — the only honest attribution when no activity
    // has been recorded against it yet. A client able to set it could relabel a
    // claim as an inference.
    expect(post?.body).not.toHaveProperty('level_source')
    // `confidence` and `evidence_count` are measured, not typed: a client that
    // could set them could forge the number that lends an estimate credibility.
    expect(post?.body).not.toHaveProperty('confidence')
    expect(post?.body).not.toHaveProperty('evidence_count')
  })

  it('records an activity without claiming a provenance it did not perform', async () => {
    const user = userEvent.setup()
    const calls = installBackend()
    renderLearningPage()
    await waitForData()

    await user.click(screen.getByRole('button', { name: 'Record activity' }))

    const dialog = await screen.findByRole('dialog')
    await user.type(
      within(dialog).getByLabelText('What you did'),
      'Read the regularisation chapter',
    )
    await user.selectOptions(within(dialog).getByLabelText('Kind of event'), 'concept_learned')
    await user.click(within(dialog).getByRole('button', { name: 'Record activity' }))

    await waitFor(() => expect(getPosts(calls, '/learning/activities')).toHaveLength(1))
    const post = getPosts(calls, '/learning/activities')[0]
    expect(post?.body).toMatchObject({
      title: 'Read the regularisation chapter',
      activity_type: 'concept_learned',
    })

    // The entry came from the person typing it. A client that set a subsystem
    // name would be claiming a derivation it did not perform — the same forgery
    // as inventing the evidence itself.
    expect(post?.body).not.toHaveProperty('source_type')
    expect(post?.body).not.toHaveProperty('source_id')
    // A blank duration stays omitted: `0` would claim a measured zero-length
    // session for a page that was merely opened.
    expect(post?.body).not.toHaveProperty('duration_minutes')
  })

  it('never prints NaN, Infinity or an undefined figure', async () => {
    installBackend()
    const { container } = renderLearningPage()
    await waitForData()
    await resolveCharts(container)
    await settle()

    expectNoFabricatedNumbers(container)
    // The brief's language rule, scanned against the rendered page rather than
    // against the copy that shipped today.
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/not good at|you are weak|you are bad at|weak at|poor at/i)
    expect(text).not.toMatch(/average level|strongest skill|readiness score/i)
  })
})