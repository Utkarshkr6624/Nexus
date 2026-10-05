import type { ReactElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/tooltip'
import {
  LearningActivityRow,
  LearningActivitySummary,
  LearningActivityTimeline,
  LearningActivityTimelineSkeleton,
  LearningEmptyState,
  LearningGoalCard,
  LearningGoalCardGrid,
  LearningGoalCardGridSkeleton,
  LearningRegionError,
  LearningStaleNotice,
  SkillCard,
  SkillCardGrid,
  SkillGapList,
  SkillGapListSkeleton,
  describeDaysSince,
  describeDuration,
  describeEvidenceCount,
  describeGap,
  describeLevelClaim,
  describeWindow,
  formatLevelOfScale,
  levelSourcePhrase,
} from '@/features/learning/components'
import {
  LEVEL_SOURCE_META,
  NOT_ENOUGH_DATA_TITLE,
} from '@/features/learning/components/learning-vocabulary'
import { NO_VALUE, formatNumber } from '@/features/analytics/format'
import { ApiError } from '@/lib/api-client'
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
  SkillGapListRead,
  SkillGapRead,
  SkillListRead,
  SkillRead,
} from '@/types/learning'

/**
 * The Phase 9 learning component library, mounted for real.
 *
 * `features/learning/components` is the layer both learning pages delegate every
 * presentational decision to, and it is where the phase's hardest rule lives:
 * **a level is never a bare number.** There is no prop that turns the attribution
 * off, no branch that renders `3/5` on its own, and every formatter that produces
 * a level phrase takes the `level_source` as a required argument. That is pinned
 * here by asserting on the *exact* string the card renders — `"3 of 5,
 * self-assessed by you"` — rather than on a substring, because a substring check
 * would still pass if the same paragraph also printed a bare `3` beside it.
 *
 * Five decisions shape this file, all inherited from the Phase 8 component suite
 * this one follows:
 *
 * **Every component is mounted for real, inside a router and a query client.**
 * Several render `Link` (`SkillCard`, `LearningGoalCard`, `SkillGapRow`) and
 * `MetricCard` renders a Radix tooltip, so the harness is a memory router plus a
 * **fresh** `QueryClient` per render. The shared singleton is deliberately not
 * used: `AppProviders` registers `onSessionChange(() => queryClient.clear())`
 * (`src/app/auth-bootstrap.tsx:13`) and in jsdom that clear lands mid-test and
 * strands every component at `pending` forever. The retry policy below is
 * `src/app/query-client.ts`'s own `queryRetryPolicy`, not a copy of it.
 *
 * **Only `fetch` is stubbed, and the routing table is there to prove it is not
 * needed.** These components are presentational by construction: a `SkillCard`
 * renders from a literal `SkillRead` and never asks the network anything. One
 * test asserts that directly — it renders panels a page would fill from six
 * different endpoints and then checks the request log is empty.
 *
 * **Recharts is given a size, and only a size.** jsdom has no layout engine, so
 * `ResponsiveContainer` measures 0×0 and renders an empty `<div>`: every chart
 * would come back as a title above an empty box, and half the assertions below
 * would pass without a single mark having been drawn. The mock replaces exactly
 * that measurement with a fixed 640×256 box and tags what it wraps in
 * `data-chart-surface`; the axes, areas and scales stay the library's own.
 * **Nothing asserts on a library internal** — no `d` attribute, no recharts
 * class name, no pixel coordinate.
 *
 * **Charts go through the real `LazyChart` boundary**, so they resolve the way
 * they do in the app. The boundary paints the *same* card title as the real
 * chart, so a `findByRole('heading')` would happily resolve against a skeleton;
 * what tells them apart is that the fallback's body is a 256px `Skeleton` while a
 * resolved chart replaces it with a `data-chart-surface` (or an empty state).
 * {@link resolveCharts} waits on exactly that, and everything after it is a
 * `findBy*`.
 *
 * **The dates are fixed and in a completed year.** 2019 is used throughout, so a
 * formatter that omits the year for "this year" cannot make a fixture written
 * for this year start failing in January — and nothing here asserts on a
 * relative age ("2 hours ago") or on a day count derived from `Date.now()`, which
 * would depend on the machine's clock.
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

/**
 * How long the first lazy resolution is allowed to take.
 *
 * Only the first test to touch a lazily loaded chart pays for the module import.
 * The bound only ever has to cover module loading, so raising it cannot turn a
 * genuine failure into a pass.
 */
const LAZY_RESOLVE_TIMEOUT_MS = 10_000

const SKILL_ID = '11111111-1111-4111-8111-111111111111'
const SKILL_TWO_ID = '22222222-2222-4222-8222-222222222222'
const GOAL_ID = '33333333-3333-4333-8333-333333333333'
const NO_SKILL_REASON =
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
}

interface Backend {
  summary?: Route
  metrics?: Route
  gaps?: Route
  activity?: Route
  goals?: Route
  skills?: Route
  activities?: Route
}

/**
 * The routing table, declared even though this file should never reach it.
 *
 * It exists so a component that *did* start fetching would fail loudly rather
 * than hang, and so the request log can be asserted empty. Nothing below
 * overrides an endpoint — that is the point.
 */
function installBackend(overrides: Backend = {}): Call[] {
  const unused = (): Route => () => envelope('not_found', 'No component may fetch.', 404, 'req-x')
  const summary = overrides.summary ?? unused()
  const metrics = overrides.metrics ?? unused()
  const gaps = overrides.gaps ?? unused()
  const activity = overrides.activity ?? unused()
  const goals = overrides.goals ?? unused()
  const skills = overrides.skills ?? unused()
  const activities = overrides.activities ?? unused()

  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      calls.push({ url, method })

      if (url.includes('/learning/summary')) return summary(url)
      if (url.includes('/learning/metrics')) return metrics(url)
      if (url.includes('/learning/gaps')) return gaps(url)
      if (url.includes('/learning/activity')) return activity(url)
      if (url.includes('/learning/goals')) return goals(url)
      if (url.includes('/learning/skills')) return skills(url)
      if (url.includes('/learning/activities')) return activities(url)
      return envelope('not_found', 'No stub matched this request.', 404, 'req-unmatched')
    }),
  )
  return calls
}

/**
 * Ships the retry policy from `src/app/query-client.ts`, so nothing in this file
 * is testing a policy the app does not ship.
 */
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

/** Mounts one component inside the same provider stack the app uses. */
function renderComponent(node: ReactElement, entry = '/learning') {
  const router = createMemoryRouter([{ path: '*', element: node }], { initialEntries: [entry] })
  return render(
    <QueryClientProvider client={createTestClient()}>
      <TooltipProvider delayDuration={200}>
        <RouterProvider router={router} />
      </TooltipProvider>
    </QueryClientProvider>,
  )
}

/** The `LazyChart` fallback's body — a marker no resolved chart can produce. */
function chartFallback(container: HTMLElement): Element | null {
  return container.querySelector('[data-slot="skeleton"].h-64')
}

function chartSurface(container: HTMLElement): Element | null {
  return container.querySelector('[data-chart-surface]')
}

/**
 * Waits for every `LazyChart` boundary in `container` to resolve.
 *
 * The boundary paints the real chart's own title, so waiting on the title would
 * pass against a skeleton. Waiting for the fallback's 256px `Skeleton` to leave
 * the document is what actually proves the module arrived.
 */
async function resolveCharts(container: HTMLElement): Promise<void> {
  await waitFor(() => expect(chartFallback(container)).toBeNull(), {
    timeout: LAZY_RESOLVE_TIMEOUT_MS,
  })
}

/**
 * No figure on this surface may print a number it does not have. `NaN`,
 * `Infinity` and the word `undefined` are the three ways a nullable leaks into a
 * rendered string, and the brief names all three.
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

/** The card a title belongs to, found the way a reader finds it. */
function cardFor(name: string): HTMLElement {
  return screen.getByRole('heading', { name }).closest('div.rounded-lg') as HTMLElement
}

/**
 * A `MetricCard` tile, found through its label.
 *
 * `MetricCard` prints its label as a small uppercase paragraph rather than a
 * heading — it is a figure with a caption, not a section — so the tile is located
 * the way a reader finds it rather than by heading role.
 */
function tileFor(label: string): HTMLElement {
  return screen.getByText(label).closest('div.rounded-lg') as HTMLElement
}

/** The labelled sub-section of a card — `Current level`, `Evidence`, and so on. */
function sectionFor(card: HTMLElement, heading: string): HTMLElement {
  const node = within(card).getByRole('heading', { level: 4, name: heading })
  return node.closest('section') as HTMLElement
}

/** The row a gap belongs to, found through the skill's name. */
function gapRowFor(name: string): HTMLElement {
  return screen.getByText(name).closest('li') as HTMLElement
}

/**
 * An absolute instant, formatted exactly the way the formatters under test do
 * it. Pinning the locale here means the expectation is the machine's own
 * rendering of that instant rather than one hard-coded spelling of it.
 */
function instant(iso: string): string {
  return new Intl.DateTimeFormat(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(iso))
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
    explanation:
      'Target 4/5, current self-assessed 2/5. NEXUS recorded 6 related learning activities in the last 30 days.',
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

function activity(overrides: Partial<LearningActivityRead> = {}): LearningActivityRead {
  return {
    id: '44444444-4444-4444-8444-444444444444',
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

/** Three daily buckets with a quiet day in the middle, which is the whole point. */
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

const SUMMARY: LearningSummaryRead = {
  goal_count: 4,
  active_goal_count: 2,
  completed_goal_count: 1,
  skill_count: 3,
  skills_with_evidence: 2,
  activity_count: 11,
  activities_in_window: 3,
  minutes_in_window: 135,
  window_days: 30,
  window_start: '2019-01-01T00:00:00Z',
  window_end: '2019-01-30T23:59:59Z',
  latest_activity_at: '2019-01-29T14:05:00Z',
  has_data: true,
  summary: '3 learning activities were recorded in the last 30 days across 3 tracked skills.',
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

/** Every `LearningEmptyState` variant, with the sentence the component ships. */
const EMPTY_COPY: Array<[string, string, string]> = [
  [
    'goals',
    'No learning goals yet',
    'A goal is something you write down yourself: a title, where you want the level to go, ' +
      'and optionally a target date. NEXUS never creates a goal on your behalf, and every ' +
      'progress figure on this page is the one you set.',
  ],
  [
    'skills',
    'No skills tracked yet',
    'A skill is a name plus a level you claim for it. Add one and it becomes the unit ' +
      'everything else here is counted in — gaps, evidence and activity all attach to a skill.',
  ],
  [
    'gaps',
    NOT_ENOUGH_DATA_TITLE,
    'A gap is the distance between a recorded level and the target you set for it, and it is ' +
      'computed on read from both. Track a skill with a target above its current level and the ' +
      'gap, its evidence count and the sentence explaining it appear here.',
  ],
  [
    'activities',
    'Nothing recorded yet',
    'Every entry here is an event that was recorded: a study session, a completed task, a ' +
      'note, a concept or a resource that was opened. Record one — or let NEXUS derive it from ' +
      'a task, note, project or repository — and the trail builds itself.',
  ],
  [
    'summary',
    NOT_ENOUGH_DATA_TITLE,
    'The counts above are read from recorded learning activities and goals. They stay at ' +
      'nothing until there is at least one of either, because a dashboard of zeroes is not a ' +
      'measurement.',
  ],
  [
    'filtered',
    'Nothing matches this filter',
    'These records exist; none of them match what is selected. Clearing the filter shows them ' +
      'again.',
  ],
]

/* -------------------------------------------------------------- the levels */

describe('a skill level is never a bare number', () => {
  it('renders a self-assessed level with the phrase naming who set it', () => {
    const { container } = renderComponent(<SkillCard skill={skill()} />)

    // The exact string, not a substring: the assertion has to fail if the card
    // ever also printed `2` on its own beside the claim.
    expect(within(cardFor('Machine Learning')).getByText('2 of 5, self-assessed by you')).toBeInTheDocument()

    const section = sectionFor(cardFor('Machine Learning'), 'Current level')
    expect(section.textContent).toContain(levelSourcePhrase('user_defined'))
    expect(within(section).getByText(LEVEL_SOURCE_META.user_defined.label)).toBeInTheDocument()
    expect(within(section).getByTitle(LEVEL_SOURCE_META.user_defined.description)).toBeInTheDocument()

    // The target is the person's own claim too, so it is named as such.
    const target = sectionFor(cardFor('Machine Learning'), 'Target level')
    expect(within(target).getByText('— the target you set for this skill.')).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })

  it('renders an estimate with the phrase naming what derived it, and shows its working', () => {
    const { container } = renderComponent(
      <SkillCard skill={skill({ level_source: 'system_estimate', current_level: 3, confidence: 80 })} />,
    )

    const card = cardFor('Machine Learning')
    const section = sectionFor(card, 'Current level')
    expect(
      within(section).getByText('3 of 5, estimated by NEXUS from recorded activities'),
    ).toBeInTheDocument()
    expect(within(section).getByText(LEVEL_SOURCE_META.system_estimate.label)).toBeInTheDocument()

    // An estimate shows what it rests on. A self-assessment must not, because a
    // confidence of zero attached to a claim would read as "worthless".
    const evidence = sectionFor(card, 'Evidence')
    expect(evidence.textContent).toContain('Estimate confidence 80%')
    expect(
      screen.queryByText(/NEXUS attaches no confidence figure to it/),
    ).not.toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })

  it('never attaches a confidence figure to a level the person claimed', () => {
    renderComponent(<SkillCard skill={skill({ level_source: 'user_defined', confidence: 0 })} />)

    const evidence = sectionFor(cardFor('Machine Learning'), 'Evidence')
    expect(
      evidence.textContent,
    ).toContain(
      'No estimate has been made for this skill. A level you set is recorded as your claim, ' +
        'and NEXUS attaches no confidence figure to it.',
    )
    // `confidence: 0` is a real measurement meaning "nothing is inferred here",
    // and printing "confidence 0%" would read as a grade of zero.
    expect(evidence.textContent).not.toMatch(/Estimate confidence/)
  })

  it('states a genuine zero evidence count in words rather than as a dash', () => {
    const { container } = renderComponent(
      <SkillCard skill={skill({ evidence_count: 0, last_activity_at: null })} />,
    )

    const evidence = sectionFor(cardFor('Machine Learning'), 'Evidence')
    expect(evidence.textContent).toContain('No learning activities recorded against this skill.')
    expect(evidence.textContent).toContain('Nothing has been recorded against this skill yet.')
    // The dash is `NO_VALUE`, and a dash here would say "the response did not
    // carry this" — which is a different claim from "it carried a zero".
    expect(within(evidence).queryByText(NO_VALUE)).toBeNull()
    expect(evidence.textContent).not.toMatch(/undefined/)
    expectNoFabricatedNumbers(container)
  })

  it('puts every level on the grid through the same attributing renderer', () => {
    const { container } = renderComponent(
      <SkillCardGrid
        skills={[
          skill(),
          skill({
            id: SKILL_TWO_ID,
            name: 'SQL',
            current_level: 5,
            target_level: 5,
            level_source: 'system_estimate',
            confidence: 95,
            evidence_count: 40,
          }),
        ]}
      />,
    )

    // Both sources, both visible, on the same screen. There is no third state.
    expect(screen.getByText('2 of 5, self-assessed by you')).toBeInTheDocument()
    expect(
      screen.getByText('5 of 5, estimated by NEXUS from recorded activities'),
    ).toBeInTheDocument()
    expect(screen.getAllByText('Self-assessed')).toHaveLength(1)
    expect(screen.getAllByText('NEXUS estimate')).toHaveLength(1)
    expect(screen.getByText('2 skills shown.')).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })
})

describe('the level and gap formatters', () => {
  it('clamps a level to the scale, dashes a null, and never invents one', () => {
    expect(formatLevelOfScale(3)).toBe('3 of 5')
    expect(formatLevelOfScale(1)).toBe('1 of 5')
    // A payload from a newer backend must not render as an out-of-range number
    // nobody has ever been asked to interpret.
    expect(formatLevelOfScale(9)).toBe('5 of 5')

    expect(formatLevelOfScale(null)).toBe(NO_VALUE)
    expect(formatLevelOfScale(undefined)).toBe(NO_VALUE)
    expect(formatLevelOfScale(Number.NaN)).toBe(NO_VALUE)
  })

  it('requires the source: the phrase is part of the level, not an option', () => {
    expect(describeLevelClaim(3, 'user_defined')).toBe('3 of 5, self-assessed by you')
    expect(describeLevelClaim(3, 'system_estimate')).toBe(
      '3 of 5, estimated by NEXUS from recorded activities',
    )
    expect(levelSourcePhrase('user_defined')).toBe('self-assessed by you')
    expect(levelSourcePhrase('system_estimate')).toBe('estimated by NEXUS from recorded activities')

    // A null level produces a dash and *no* attribution, because there is
    // nothing on screen to attribute.
    expect(describeLevelClaim(null, 'system_estimate')).toBe(NO_VALUE)
  })

  it('treats a zero gap as a measurement and a null gap as an absence', () => {
    expect(describeGap(2)).toBe('2 levels between the recorded level and the target')
    expect(describeGap(1)).toBe('1 level between the recorded level and the target')
    // The recorded level has reached the target. That is an answer.
    expect(describeGap(0)).toBe('No gap — the recorded level has reached the target')
    expect(describeGap(0)).not.toBe(NO_VALUE)

    expect(describeGap(null)).toBe(NO_VALUE)
    expect(describeGap(undefined)).toBe(NO_VALUE)
  })

  it('counts evidence, days since, duration and window without substituting a zero', () => {
    expect(describeEvidenceCount(6)).toBe('6 learning activities recorded against this skill.')
    expect(describeEvidenceCount(1)).toBe('1 learning activity recorded against this skill.')
    expect(describeEvidenceCount(0)).toBe('No learning activities recorded against this skill.')
    expect(describeEvidenceCount(null)).toBe(NO_VALUE)

    expect(describeDaysSince(0)).toBe('The most recent activity was recorded today.')
    expect(describeDaysSince(1)).toBe('The most recent activity was recorded yesterday.')
    expect(describeDaysSince(9)).toBe('The most recent activity was recorded 9 days ago.')
    // `days_since_last_activity: null` means nothing has ever been recorded,
    // which `0` — "today" — would flatten into a different fact.
    expect(describeDaysSince(null)).toBe('Nothing has been recorded against this skill yet.')

    expect(describeDuration(0)).toBe('Recorded with a duration of 0 minutes, as a measurement.')
    expect(describeDuration(45)).toBe('45m of recorded time.')
    // An event has no length. "0m" would claim a measured zero-length session.
    expect(describeDuration(null)).toBe('Recorded as an event — no duration was attached to it.')
    expect(describeDuration(null)).not.toMatch(/\d\s*m/)

    expect(describeWindow(30)).toBe('the last 30 days')
    expect(describeWindow(1)).toBe('the last 1 day')
    expect(describeWindow(null)).toBe('the whole recorded history')
  })
})

/* -------------------------------------------------------------- the gaps */

describe('SkillGapList', () => {
  it('renders the backend explanation verbatim, with its digits intact', () => {
    const { container } = renderComponent(
      <SkillGapList
        gaps={[gap()]}
        windowDays={30}
        buildHref={(row) => (row.skill_id ? `/learning/skills/${row.skill_id}` : null)}
      />,
    )

    const row = gapRowFor('Machine Learning')
    // Verbatim, because a client that rebuilt the sentence from the fields
    // would be a second answer to the same question.
    expect(
      within(row).getByText(
        'Target 4/5, current self-assessed 2/5. NEXUS recorded 6 related learning activities in the last 30 days.',
      ),
    ).toBeInTheDocument()

    expect(within(row).getByText('Current 2 of 5, self-assessed by you')).toBeInTheDocument()
    expect(within(row).getByText('target 4 / 5, the level you set')).toBeInTheDocument()
    expect(within(row).getByText('2 levels between the recorded level and the target')).toBeInTheDocument()
    expect(within(row).getByText('6 learning activities recorded against this skill.')).toBeInTheDocument()
    expect(within(row).getByText('6 in the last 30 days')).toBeInTheDocument()
    expect(within(row).getByText('The most recent activity was recorded yesterday.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Machine Learning' })).toHaveAttribute(
      'href',
      `/learning/skills/${SKILL_ID}`,
    )
    expectNoFabricatedNumbers(container)
  })

  it('shows an unmeasurable gap as its reason, with no level and no figure beside it', () => {
    const unmeasured = gap({
      available: false,
      reason_if_unavailable: NO_SKILL_REASON,
      explanation:
        'No gap could be computed for Machine Learning because no level has been recorded against it.',
    })
    const { container } = renderComponent(<SkillGapList gaps={[unmeasured]} windowDays={30} />)

    const row = gapRowFor('Machine Learning')
    expect(within(row).getByText(NOT_ENOUGH_DATA_TITLE)).toBeInTheDocument()
    expect(within(row).getByText(NO_SKILL_REASON)).toBeInTheDocument()
    expect(
      within(row).getByText(
        'No gap could be computed for Machine Learning because no level has been recorded against it.',
      ),
    ).toBeInTheDocument()

    // A number beside "not measured" is the exact failure `available` exists to
    // prevent: no gap figure, no level claim, no evidence count, no dash.
    expect(row.textContent).not.toMatch(/levels between/)
    expect(row.textContent).not.toMatch(/Current \d+ of 5/)
    expect(row.textContent).not.toMatch(/learning activities recorded/)
    expect(within(row).queryByText(NO_VALUE)).toBeNull()
    expectNoFabricatedNumbers(container)
  })

  it('renders a measured zero gap as reached, never as a dash and never as unavailable', () => {
    const { container } = renderComponent(
      <SkillGapList
        gaps={[
          gap({
            current_level: 4,
            target_level: 4,
            gap: 0,
            explanation: 'Target 4/5, current self-assessed 4/5. NEXUS recorded 6 related learning activities in the last 30 days.',
          }),
        ]}
        windowDays={30}
      />,
    )

    const row = gapRowFor('Machine Learning')
    expect(
      within(row).getByText('No gap — the recorded level has reached the target'),
    ).toBeInTheDocument()
    expect(within(row).getByText('Current 4 of 5, self-assessed by you')).toBeInTheDocument()
    expect(within(row).queryByText(NO_VALUE)).toBeNull()
    expect(row.textContent).not.toContain(NOT_ENOUGH_DATA_TITLE)
    expectNoFabricatedNumbers(container)
  })

  it('explains an empty gap list rather than drawing a zero', () => {
    const { container } = renderComponent(<SkillGapList gaps={[]} />)

    expect(screen.getByText(NOT_ENOUGH_DATA_TITLE)).toBeInTheDocument()
    expect(
      screen.getByText(
        'A gap is the distance between a recorded level and the target you set for it, and it is ' +
          'computed on read from both. Track a skill with a target above its current level and the ' +
          'gap, its evidence count and the sentence explaining it appear here.',
      ),
    ).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })

  it('prefers the backend reason over its own copy, and says how many rows it covers', () => {
    renderComponent(
      <SkillGapList
        gaps={[gap()]}
        windowDays={30}
        emptyReason={null}
      />,
    )

    expect(
      screen.getByText('1 skill with a recorded target, counted over the last 30 days.'),
    ).toBeInTheDocument()
  })

  it('draws silhouettes carrying no digits and no level meter while the read is in flight', () => {
    renderComponent(<SkillGapListSkeleton count={4} />)

    const status = screen.getByRole('status')
    expect(status).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByText('Loading skill gaps')).toBeInTheDocument()
    // A grey "4 / 5 · 2 / 5" would be a comparison nobody made.
    expect(status.textContent ?? '').not.toMatch(/\d/)
  })
})

/* ------------------------------------------------------------- the goals */

describe('LearningGoalCard', () => {
  it('renders a zero progress as a real measurement and says whose figure it is', () => {
    const { container } = renderComponent(<LearningGoalCard goal={goal({ progress: 0 })} />)

    const card = cardFor('Reach level 4 on machine learning')
    expect(within(card).getByText('0%')).toBeInTheDocument()
    expect(
      within(card).getByText('0% — the progress figure is the one you set, and it has not moved.'),
    ).toBeInTheDocument()
    // A zero here is "not started", not "missing".
    expect(within(card).queryByText(NO_VALUE)).toBeNull()
    expectNoFabricatedNumbers(container)
  })

  it('renders a moved progress without claiming NEXUS derived it', () => {
    const { container } = renderComponent(<LearningGoalCard goal={goal({ progress: 40 })} />)

    const card = cardFor('Reach level 4 on machine learning')
    expect(within(card).getByText('40%')).toBeInTheDocument()
    expect(
      within(card).getByText('40% — the progress figure is the one you set.'),
    ).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })

  it('states a missing deadline and a missing effort estimate rather than zeroing them', () => {
    const { container } = renderComponent(
      <LearningGoalCard goal={goal({ target_date: null, estimated_effort_minutes: null })} />,
    )

    const card = cardFor('Reach level 4 on machine learning')
    const target = sectionFor(card, 'Target')
    // "0 days remaining" would be a deadline NEXUS invented.
    expect(target.textContent).toContain('No target date set.')
    expect(target.textContent).not.toMatch(/\d+ days/)
    expect(target.textContent).toContain(
      'No effort estimate recorded — the estimate on a goal is yours, and none was given.',
    )
    expectNoFabricatedNumbers(container)
  })

  it('renders a real effort estimate as the person’s own', () => {
    renderComponent(<LearningGoalCard goal={goal({ estimated_effort_minutes: 120 })} />)

    expect(
      screen.getByText('2h of estimated effort, your own estimate.'),
    ).toBeInTheDocument()
  })

  it('separates a completed goal from an archived one, and never counts archived as open', () => {
    const { container } = renderComponent(
      <LearningGoalCardGrid goals={[goal({ status: 'completed', completed_at: '2019-01-29T14:05:00Z' })]} />,
    )

    expect(screen.getByText('Completed')).toBeInTheDocument()
    expect(screen.queryByText('Archived')).toBeNull()
    expect(screen.getByText(/^Completed on /)).toBeInTheDocument()
    expect(
      screen.getByText(
        '1 goal shown. Archived goals are listed here but are not counted among the goals still open.',
      ),
    ).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })

  it('draws silhouettes with no digits and no half-filled progress bar', () => {
    renderComponent(<LearningGoalCardGridSkeleton count={3} />)

    const status = screen.getByRole('status')
    expect(status).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByText('Loading learning goals')).toBeInTheDocument()
    // A pulse in the shape of "40%" is a number to anyone glancing at it.
    expect(status.textContent ?? '').not.toMatch(/\d/)
  })

  it('explains an account that has written down no goal at all', () => {
    const { container } = renderComponent(
      <LearningGoalCardGrid goals={[]} emptyAction={<button type="button">Write down a goal</button>} />,
    )

    expect(screen.getByText('No learning goals yet')).toBeInTheDocument()
    expect(
      screen.getByText(
        'A goal is something you write down yourself: a title, where you want the level to go, ' +
          'and optionally a target date. NEXUS never creates a goal on your behalf, and every ' +
          'progress figure on this page is the one you set.',
      ),
    ).toBeInTheDocument()
    // The only thing on the panel to do is named, and offered.
    expect(screen.getByRole('button', { name: 'Write down a goal' })).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })
})

/* --------------------------------------------------------- the activity trail */

describe('LearningActivityRow', () => {
  it('names the event type and where the entry came from, and keeps them apart', () => {
    const { container } = renderComponent(
      <ul>
        <LearningActivityRow activity={activity()} goalName="Reach level 4" skillName="Machine Learning" />
      </ul>,
    )

    expect(screen.getByText('Study session')).toBeInTheDocument()
    // A null pair means the person typed it in; nothing else does.
    expect(screen.getByText('Entered by you.')).toBeInTheDocument()
    expect(screen.getByText('Machine Learning')).toBeInTheDocument()
    expect(screen.getByText('Reach level 4')).toBeInTheDocument()
    expect(screen.getByTitle(instant('2019-01-29T14:05:00Z'))).toBeInTheDocument()
    expect(screen.getByText('45m of recorded time.')).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })

  it('never presents a derived activity as one the person typed', () => {
    renderComponent(
      <ul>
        <LearningActivityRow
          activity={activity({
            activity_type: 'coding_activity',
            duration_minutes: null,
            source_type: 'repository',
            source_id: '55555555-5555-4555-8555-555555555555',
            skill_id: null,
            goal_id: null,
          })}
        />
      </ul>,
    )

    expect(screen.getByText('Coding activity')).toBeInTheDocument()
    expect(screen.getByText('Derived from a repository scan.')).toBeInTheDocument()
    expect(screen.queryByText('Entered by you.')).toBeNull()
    // The skill was deleted; the trail outlives it and says so.
    expect(screen.getByText('No skill named')).toBeInTheDocument()
    // An event has no length, so no `0m` is printed anywhere on the row.
    expect(screen.getByText('Recorded as an event — no duration was attached to it.')).toBeInTheDocument()
    expect(document.body.textContent ?? '').not.toMatch(/\b0m\b/)
  })

  it('records a zero duration as a measurement rather than as an absence', () => {
    renderComponent(
      <ul>
        <LearningActivityRow activity={activity({ duration_minutes: 0 })} />
      </ul>,
    )

    expect(
      screen.getByText('Recorded with a duration of 0 minutes, as a measurement.'),
    ).toBeInTheDocument()
  })

  it('never renders a commit as a task completion', () => {
    renderComponent(
      <ul>
        <LearningActivityRow
          activity={activity({
            activity_type: 'coding_activity',
            title: 'Six commits touched Python files in this repository',
          })}
        />
      </ul>,
    )

    expect(screen.getByText('Coding activity')).toBeInTheDocument()
    // The row states what the record is; the claim is the event, not an outcome.
    expect(
      screen.getByText(
        'A recorded code event, usually derived from a repository. A commit is a code event; it is not a task completion and this surface never renders it as one.',
      ),
    ).toBeInTheDocument()
  })
})

describe('LearningActivityTimeline', () => {
  it('says when the visible rows are the whole trail and when they are a position in it', () => {
    const complete = renderComponent(
      <LearningActivityTimeline activities={[activity()]} total={1} />,
    )
    expect(complete.getByText('1 activity shown, newest first.')).toBeInTheDocument()
    expect(
      complete.getByText('Every activity that has been recorded is shown here.'),
    ).toBeInTheDocument()
    complete.unmount()

    renderComponent(<LearningActivityTimeline activities={[activity()]} total={128} />)
    expect(
      screen.getByText(
        'Showing 1 of 128 recorded activities. This is a position in the list, not the whole trail.',
      ),
    ).toBeInTheDocument()
  })

  it('explains an empty trail and what fills it', () => {
    const { container } = renderComponent(<LearningActivityTimeline activities={[]} />)

    expect(screen.getByText('Nothing recorded yet')).toBeInTheDocument()
    expect(
      screen.getByText(
        'Every entry here is an event that was recorded: a study session, a completed task, a ' +
          'note, a concept or a resource that was opened. Record one — or let NEXUS derive it from ' +
          'a task, note, project or repository — and the trail builds itself.',
      ),
    ).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })

  it('announces its silhouette without ever drawing a figure', () => {
    renderComponent(<LearningActivityTimelineSkeleton count={4} />)

    const status = screen.getByRole('status')
    expect(status).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByText('Loading the learning activity timeline')).toBeInTheDocument()
    expect(status.textContent ?? '').not.toMatch(/\d/)
  })
})

/* ------------------------------------------------------------ the summary */

describe('LearningActivitySummary', () => {
  it('leads with counts of records and the sentence the server composed for them', async () => {
    const { container } = renderComponent(
      <LearningActivitySummary summary={SUMMARY} series={SERIES} byType={{ study_session: 2, resource_viewed: 1 }} />,
    )

    expect(await screen.findByText(SUMMARY.summary)).toBeInTheDocument()
    await resolveCharts(container)

    expect(within(cardFor('Learning summary')).getByText(formatNumber(4))).toBeInTheDocument()
    expect(within(tileFor('Goals still open')).getByText(formatNumber(2))).toBeInTheDocument()
    expect(
      within(tileFor(`Activities, last ${formatNumber(30)} days`)).getByText(formatNumber(3)),
    ).toBeInTheDocument()
    expect(within(tileFor('Recorded minutes')).getByText(formatNumber(135))).toBeInTheDocument()
    expect(
      screen.getByText(/Most recent recorded activity 29 Jan 2019\./),
    ).toBeInTheDocument()
    expect(chartSurface(container)).not.toBeNull()

    // Every tile names what it counts, so a bare `3` cannot be read as hours.
    expect(
      screen.getByText('Minutes attached to those activities. Zero means every one was an event, not a span'),
    ).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })

  it('refuses to render a row of zeroes on an account that has recorded nothing', () => {
    const { container } = renderComponent(<LearningActivitySummary summary={COLD_SUMMARY} />)

    expect(screen.getByText(NOT_ENOUGH_DATA_TITLE)).toBeInTheDocument()
    expect(
      screen.getByText(
        'The counts above are read from recorded learning activities and goals. They stay at ' +
          'nothing until there is at least one of either, because a dashboard of zeroes is not a ' +
          'measurement.',
      ),
    ).toBeInTheDocument()
    // Six zeroes across the top of an empty account looks like a measurement.
    expect(screen.queryByText('Recorded minutes')).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Learning summary' })).toBeNull()
    expectNoFabricatedNumbers(container)
  })

  it('renders zero recorded minutes as 0 and says why that is a measurement', () => {
    const { container } = renderComponent(
      <LearningActivitySummary
        summary={{
          ...SUMMARY,
          minutes_in_window: 0,
          latest_activity_at: null,
          summary: '3 learning activities were recorded in the last 30 days; none carried a duration.',
        }}
      />,
    )

    const card = cardFor('Learning summary')
    expect(within(tileFor('Recorded minutes')).getByText(formatNumber(0))).toBeInTheDocument()
    // The "no most recent activity" clause and the zero-minutes clause share one
    // paragraph, so both are matched as fragments of it.
    expect(
      screen.getByText(
        /No activity has been recorded yet, so there is no most recent one\.[\s\S]*The minutes figure is zero because every activity in the window was an event rather than a span — that is a measurement, not a missing value\./,
      ),
    ).toBeInTheDocument()
    // "Not measured" and "measured as zero" are different facts, and only one of
    // them would be a claim.
    expect(card.textContent).not.toContain(NOT_ENOUGH_DATA_TITLE)
    expect(within(tileFor('Recorded minutes')).queryByText(NO_VALUE)).toBeNull()
    expectNoFabricatedNumbers(container)
  })

  it('plots the window with its empty buckets and states the run in records, not hours', async () => {
    const { container } = renderComponent(
      <LearningActivitySummary summary={SUMMARY} series={SERIES} />,
    )
    await resolveCharts(container)

    expect(await screen.findByText('Days with recorded activity')).toBeInTheDocument()
    expect(chartSurface(container)).not.toBeNull()
    expect(
      screen.getByText('Every bucket in the range is plotted, including the empty ones.'),
    ).toBeInTheDocument()
    // The run counts *consecutive buckets carrying an activity*, and stops at the
    // first empty one — 7 Jan and 9 Jan carry something, 8 Jan does not, so the
    // run is a single day rather than a fortnight.
    expect(
      screen.getByText(/1 consecutive day with recorded activity, ending 9 Jan 2019\./),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/2 of the 3 days in the window carried at least one recorded activity\./),
    ).toBeInTheDocument()

    // No streak of hours, focus or productivity anywhere on the surface.
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/\d+\s*hours?\s*(focused|spent|studied)/i)
    expect(text).not.toMatch(/streak of hours|productive|burnout/i)
    expectNoFabricatedNumbers(container)
  })

  it('announces its silhouette without a placeholder digit', () => {
    renderComponent(<LearningActivitySummary summary={null} isLoading />)

    const status = screen.getByRole('status')
    expect(status).toHaveAttribute('aria-busy', 'true')
    // The announcement is assembled from the tile title, so it reads
    // "Loading Learning summary" rather than a hand-written sentence.
    expect(screen.getByText(/^Loading /)).toBeInTheDocument()
    expect(status.textContent ?? '').not.toMatch(/\d/)
  })
})

/* -------------------------------------------------------- empty and error */

describe('LearningEmptyState', () => {
  it('says why each region is empty and what would fill it, in its own words', () => {
    // A bare "No goals" reads as a broken page; "goals appear when you write one
    // down" reads as an answer.
    for (const [variant, title, description] of EMPTY_COPY) {
      const { unmount } = renderComponent(
        <LearningEmptyState variant={variant as 'goals'} />,
      )
      expect(screen.getByText(title)).toBeInTheDocument()
      expect(screen.getByText(description)).toBeInTheDocument()
      unmount()
    }
  })

  it('does not call a cold start "not enough data", because nothing is missing', () => {
    const cold = renderComponent(<LearningEmptyState variant="goals" />)
    expect(screen.getByText('No learning goals yet')).toBeInTheDocument()
    expect(screen.queryByText(NOT_ENOUGH_DATA_TITLE)).toBeNull()
    cold.unmount()

    const filtered = renderComponent(<LearningEmptyState variant="filtered" />)
    expect(screen.getByText('Nothing matches this filter')).toBeInTheDocument()
    expect(
      screen.getByText(
        'These records exist; none of them match what is selected. Clearing the filter shows them again.',
      ),
    ).toBeInTheDocument()
    filtered.unmount()
  })

  it('renders the backend’s own reason verbatim, in preference to its built-in copy', () => {
    renderComponent(
      <LearningEmptyState
        variant="activities"
        reason="No activity has been recorded in the last 30 days. Every day in that range was still read, and each one is a recorded zero — a quiet stretch is a fact, not missing data."
      />,
    )

    expect(
      screen.getByText(
        'No activity has been recorded in the last 30 days. Every day in that range was still read, and each one is a recorded zero — a quiet stretch is a fact, not missing data.',
      ),
    ).toBeInTheDocument()
    expect(
      screen.queryByText(/Every entry here is an event that was recorded/),
    ).not.toBeInTheDocument()
  })
})

describe('LearningRegionError and LearningStaleNotice', () => {
  it('names what failed, quotes the request id, and offers a retry', () => {
    renderComponent(
      <LearningRegionError
        error={
          new ApiError({
            status: 500,
            code: 'internal_error',
            message: 'The learning service is unavailable.',
            requestId: 'req-learn-1',
          })
        }
        subject="the skill list"
        onRetry={() => undefined}
      />,
    )

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('the skill list could not be loaded')
    expect(alert).toHaveTextContent(
      'The failure was recorded on the server. Retry, and quote the request ID below.',
    )
    expect(alert).toHaveTextContent('The learning service is unavailable.')
    expect(alert).toHaveTextContent('req-learn-1')
    // A failed read is not an empty one, and it is never a stack trace.
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument()
    expect(alert.textContent ?? '').not.toMatch(/Traceback|line \d+, in/)
  })

  it('says the figures are from the previous read while a refetch is in flight', () => {
    const settled = renderComponent(<LearningStaleNotice isStale={false} subject="the goal list" />)
    // It renders nothing at all when settled, so a page carries no residue from
    // a state it has already left.
    expect(settled.container.textContent ?? '').toBe('')
    settled.unmount()

    renderComponent(<LearningStaleNotice isStale subject="the goal list" />)
    expect(
      screen.getByText('Refreshing the goal list. The values shown are from the last completed read.'),
    ).toBeInTheDocument()
  })
})

/* ---------------------------------------------------------------- the guard */

describe('the learning component library', () => {
  it('fetches nothing of its own, so a card renders from a literal and a provider', () => {
    const calls = installBackend()

    renderComponent(
      <div>
        <LearningActivitySummary summary={SUMMARY} series={SERIES} />
        <SkillCardGrid skills={[skill()]} />
        <SkillGapList gaps={[gap()]} windowDays={30} />
        <LearningGoalCardGrid goals={[goal()]} skillNames={{ [SKILL_ID]: 'Machine Learning' }} />
        <LearningActivityTimeline activities={[activity()]} total={1} />
      </div>,
    )

    // Presentational by construction: query state, windowing and the derived
    // joins live in `hooks.ts`, which another layer owns. A component that
    // started fetching would make every page suite a lie about what it is
    // mounting.
    expect(calls).toHaveLength(0)
    expect(screen.getByText('2 of 5, self-assessed by you')).toBeInTheDocument()
    // Once on the skill card and once on the gap row — both are readings of the
    // same `evidence_count`, and both say whose claim the level beside it is.
    expect(screen.getAllByText('6 learning activities recorded against this skill.')).toHaveLength(2)
    expectNoFabricatedNumbers(document.body)
  })

  it('never says a person is bad at something, in any register', () => {
    const { container } = renderComponent(
      <div>
        <SkillCardGrid skills={[skill()]} />
        <SkillGapList
          gaps={[
            gap(),
            gap({
              // A distinct id, because the list keys its rows on
              // `skill_id ?? skill_name` and two rows under one key would render
              // as one.
              skill_id: SKILL_TWO_ID,
              skill_name: 'Rust',
              available: false,
              gap: 0,
              reason_if_unavailable: NO_SKILL_REASON,
            }),
          ]}
          windowDays={30}
        />
        <LearningActivitySummary summary={SUMMARY} />
      </div>,
    )

    const text = container.textContent ?? ''
    // The brief's language rule, scanned against what actually rendered rather
    // than against the copy that shipped today.
    expect(text).not.toMatch(/not good at|you are weak|you are bad at|poor at|weak at/i)
    expect(text).not.toMatch(/lack(?:s|ing) (?:skill|ability)|incompetent|unskilled/i)
    expect(text).not.toMatch(/proficiency|score of|readiness/i)
    expectNoFabricatedNumbers(container)
  })

  it('carries the honesty rule through every fixture the pages hand it', () => {
    // The shapes below are what the pages actually pass: a list, a windowed
    // series, a sparse by_type record. Rendering them must still produce a level
    // with its source and no fabricated figure.
    const GAPS: SkillGapListRead = {
      items: [gap()],
      total: 1,
      limit: 50,
      offset: 0,
      available_count: 1,
      unavailable_count: 0,
      by_level_source: { user_defined: 1 },
    }
    const SKILLS: SkillListRead = {
      items: [skill()],
      total: 1,
      limit: 50,
      offset: 0,
      by_level_source: { user_defined: 1 },
      by_category: {},
      skills_with_evidence: 1,
      skills_without_evidence: 0,
    }
    const GOALS: LearningGoalListRead = {
      items: [goal()],
      total: 1,
      limit: 50,
      offset: 0,
      by_status: { in_progress: 1 },
      summary: '1 learning goal is recorded on this account.',
    }
    const TRAIL: LearningActivityListRead = {
      items: [activity()],
      total: 1,
      limit: 12,
      offset: 0,
      by_type: { study_session: 1 },
      summary: '1 learning activity was recorded.',
    }

    const { container } = renderComponent(
      <div>
        <SkillCardGrid skills={SKILLS.items} />
        <SkillGapList gaps={GAPS.items} windowDays={30} />
        <LearningGoalCardGrid goals={GOALS.items} />
        <LearningActivityTimeline activities={TRAIL.items} total={TRAIL.total} />
      </div>,
    )

    expect(screen.getByText('2 of 5, self-assessed by you')).toBeInTheDocument()
    expect(
      screen.getByText(
        'Target 4/5, current self-assessed 2/5. NEXUS recorded 6 related learning activities in the last 30 days.',
      ),
    ).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })
})