import type { ReactElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/tooltip'
import {
  CareerEmptyState,
  CareerProfileCard,
  CareerProfileRegion,
  CareerRecordList,
  CareerRecordListSkeleton,
  CareerRegionError,
  CareerStaleNotice,
  CareerSummaryTiles,
  CareerSummaryTilesSkeleton,
  DevelopmentAreasPanel,
  DevelopmentAreasSkeleton,
  PortfolioEvidenceRow,
  PortfolioEvidenceScopeNote,
  PortfolioEvidenceTimeline,
  PortfolioEvidenceTimelineSkeleton,
  SkillOverviewGrid,
  SkillOverviewGridSkeleton,
  SkillOverviewTile,
  countFromRecord,
  describeCareerDaysSince,
  describeCareerEvidence,
  describeCareerLevel,
  describeDevelopmentSentence,
  describeEvidenceSource,
  describeLinkLabel,
  describeRecordPeriod,
  developmentAreasFromGaps,
  formatCareerLevel,
  formatRecordMonth,
  isNavigableLink,
} from '@/features/career/components'
import {
  CAREER_EVIDENCE_TYPE_META,
  CAREER_RECORD_KIND_META,
  NOT_ENOUGH_DATA_TITLE,
} from '@/features/career/components/career-vocabulary'
import { NO_VALUE, formatNumber } from '@/features/analytics/format'
import { ApiError } from '@/lib/api-client'
import { queryRetryPolicy } from '@/app/query-client'
import type { ApiErrorEnvelope } from '@/types/api'
import type {
  CareerEvidenceListRead,
  CareerEvidenceRead,
  CareerExperienceListRead,
  CareerExperienceRead,
  CareerProfileRead,
  CareerSummaryRead,
  SkillGapRead,
  SkillListRead,
  SkillRead,
} from '@/types/learning'

/**
 * The Phase 9 career component library, mounted for real.
 *
 * `features/career/components` is the layer the Career page delegates every
 * presentational decision to, and it carries two rules the phase exists to
 * enforce.
 *
 * **A level is never a bare number.** `describeCareerLevel` takes the
 * `level_source` as a required argument and every tile puts a `LevelOriginBadge`
 * beside the result; there is no prop that turns either off. Pinned here on the
 * *exact* string the surface renders — `"2 of 5, self-assessed by you"` — so the
 * assertion fails if a bare `2` ever appears beside it.
 *
 * **A gap is not a criticism.** The development-areas panel exists to say
 * *"3 of 5, self-assessed by you. 1 related learning activity in the last 30
 * days."* and the whole file scans the rendered text for the vocabulary the
 * brief forbids — "you are weak at", "not good at", "lack of skill" — because a
 * neutral sentence written once and paraphrased twice is how a verdict gets in.
 *
 * **Nothing here was written by NEXUS.** Every field on a profile, a record and a
 * manually added piece of evidence is something the user typed, so the empty
 * states name the action *the person* takes and never promise that NEXUS will
 * find, infer or draft a qualification.
 *
 * The harness is the Phase 8 component suite's, unchanged in every decision
 * that matters: every component is mounted for real inside a memory router and
 * a **fresh** `QueryClient` per render (the shared singleton is not used because
 * `AppProviders` registers `onSessionChange(() => queryClient.clear())`, which
 * strands every component at `pending` in jsdom); only `fetch` is stubbed, by a
 * routing table whose every route returns 404 so a component that started
 * fetching would fail loudly; the retry policy is `src/app/query-client.ts`'s,
 * Ships `src/app/query-client.ts`'s own retry policy, not a copy of it; and
 * recharts is given a size and only a size,
 * with the wrapped box tagged `data-chart-surface` and no assertion anywhere
 * reaching into a library internal.
 *
 * The dates are fixed in 2019 — a completed year — so a formatter that omits the
 * year for "this year" cannot make a fixture fail in January, and nothing here
 * asserts on a relative age or on a day count taken from `Date.now()`.
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

const PROFILE_ID = '11111111-1111-4111-8111-111111111111'
const SKILL_ID = '22222222-2222-4222-8222-222222222222'
const EVIDENCE_ID = '33333333-3333-4333-8333-333333333333'
const RECORD_ID = '44444444-4444-4444-8444-444444444444'
const NO_LEVEL_REASON =
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
  profile?: Route
  experience?: Route
  evidence?: Route
  skills?: Route
  gaps?: Route
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
  const profile = overrides.profile ?? unused()
  const experience = overrides.experience ?? unused()
  const evidence = overrides.evidence ?? unused()
  const skills = overrides.skills ?? unused()
  const gaps = overrides.gaps ?? unused()

  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      calls.push({ url, method })

      if (url.includes('/career/summary')) return summary(url)
      if (url.includes('/career/profile')) return profile(url)
      if (url.includes('/career/experience')) return experience(url)
      if (url.includes('/career/evidence')) return evidence(url)
      if (url.includes('/learning/skills')) return skills(url)
      if (url.includes('/learning/gaps')) return gaps(url)
      return envelope('not_found', 'No stub matched this request.', 404, 'req-unmatched')
    }),
  )
  return calls
}

/**
 * Uses `src/app/query-client.ts`'s own `queryRetryPolicy`, so a change to the
 * shipped policy is a change to what this file tests.
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
function renderComponent(node: ReactElement, entry = '/career') {
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

/**
 * The month and year a `YYYY-MM-DD` value is rendered as, in the machine's own
 * locale. Pinned here so the expectation is a rendering rather than one
 * hard-coded spelling of it.
 */
function monthYear(dateOnly: string): string {
  const parts = dateOnly.split('-').map(Number)
  const year = parts[0] ?? 1970
  const month = parts[1] ?? 1
  return new Intl.DateTimeFormat(undefined, { month: 'short', year: 'numeric', timeZone: 'UTC' }).format(
    new Date(Date.UTC(year, month - 1, 1)),
  )
}

/** An absolute instant, formatted the way the career formatters render it. */
function instant(iso: string): string {
  return new Intl.DateTimeFormat(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(iso))
}

/* ---------------------------------------------------------------- fixtures */

function profile(overrides: Partial<CareerProfileRead> = {}): CareerProfileRead {
  return {
    id: PROFILE_ID,
    target_role: 'Machine Learning Engineer',
    target_domain: 'Machine Learning',
    headline: 'I work on model evaluation and ranking.',
    summary: 'Five years of applied machine learning, most of it on retrieval and ranking.',
    location: 'Lisbon',
    links: ['https://github.com/ada', 'not a url at all'],
    created_at: '2019-01-02T09:00:00Z',
    updated_at: '2019-01-29T14:05:00Z',
    ...overrides,
  }
}

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
    current_level: 3,
    level_source: 'user_defined',
    gap: 1,
    evidence_count: 6,
    evidence_last_30d: 1,
    days_since_last_activity: 1,
    available: true,
    reason_if_unavailable: null,
    explanation:
      'Target 4/5, current self-assessed 3/5. NEXUS recorded 1 related learning activity in the last 30 days.',
    ...overrides,
  }
}

function evidence(overrides: Partial<CareerEvidenceRead> = {}): CareerEvidenceRead {
  return {
    id: EVIDENCE_ID,
    evidence_type: 'achievement',
    title: 'Published a note on ranking evaluation',
    description: 'Written by me, stored as I typed it.',
    occurred_on: '2019-01-20',
    project_id: null,
    skill_id: null,
    repository_id: null,
    source: 'manual',
    created_at: '2019-01-20T12:00:00Z',
    updated_at: '2019-01-20T12:00:00Z',
    ...overrides,
  }
}

function record(overrides: Partial<CareerExperienceRead> = {}): CareerExperienceRead {
  return {
    id: RECORD_ID,
    kind: 'experience',
    title: 'Machine Learning Engineer',
    organisation: 'Analytical Engines',
    started_on: '2021-03-01',
    ended_on: '2024-06-01',
    description: 'Owned the retrieval stack.',
    url: null,
    created_at: '2019-01-02T09:00:00Z',
    updated_at: '2019-01-02T09:00:00Z',
    ...overrides,
  }
}

/**
 * `GET /career/summary`, field for field as the route sends it.
 *
 * Every key here was read off a live response body, which is the only way a
 * figure of 19 keys is not quietly a figure of 13: the summary route does not
 * send `experience_count`, `education_count`, `certification_count`,
 * `linked_evidence_count`, `by_type` or `target_domain`, and a fixture typed
 * against the older shape is how four tiles came to render "—".
 */
const SUMMARY: CareerSummaryRead = {
  has_profile: true,
  target_role: 'Machine Learning Engineer',
  link_count: 2,
  record_count: 4,
  evidence_count: 4,
  evidence_in_window: 1,
  manual_evidence_count: 3,
  linked_project_count: 2,
  project_count: 5,
  completed_project_count: 2,
  repository_count: 3,
  skills_with_evidence: 1,
  learning_activity_count: 12,
  window_days: 30,
  window_start: '2018-12-30T09:00:00Z',
  window_end: '2019-01-29T09:00:00Z',
  latest_evidence_on: '2019-01-20',
  has_data: true,
  summary: '4 pieces of evidence are on this profile, 2 of them linked to a project, skill or repository.',
}

/** Every `CareerEmptyState` variant, with the sentence the component ships. */
const EMPTY_COPY: Array<[string, string, string]> = [
  [
    'profile',
    'No career profile yet',
    'A profile is entirely your own: a target role, a domain, a one-line headline and any ' +
      'links you want on it. Nothing on it is written for you — NEXUS has no opinion about what ' +
      'you are aiming at and will not guess at one.',
  ],
  [
    'records',
    'No education, experience or certifications listed yet',
    'These are the dated records a profile is made of: where you studied, the roles you have ' +
      'held, the certifications you hold. You add them, with the dates you know, and NEXUS stores ' +
      'them exactly as given.',
  ],
  [
    'evidence',
    'No evidence added yet',
    'Evidence is what you want to point at: a project, a feature, a repository’s recorded ' +
      'activity, a certification or an achievement. Add one with a date, and it appears in the ' +
      'timeline grouped by kind.',
  ],
  [
    'skills',
    'No skills to show yet',
    'This panel reads the skills you have tracked. Add one on the learning page with the level ' +
      'you claim for it, and it appears here with that level and where it came from.',
  ],
  [
    'development',
    NOT_ENOUGH_DATA_TITLE,
    'This panel lists skills where you have set a target above the current level and there is ' +
      'little recorded evidence behind them. With no such skills it has nothing to say — which is ' +
      'not the same as saying every skill is well evidenced.',
  ],
  [
    'summary',
    NOT_ENOUGH_DATA_TITLE,
    'The counts above are read from the records on this profile. They stay at nothing until ' +
      'there is at least one, because a dashboard of zeroes reads as a measurement and is not one.',
  ],
  [
    'filtered',
    'Nothing matches this filter',
    'These records exist; none of them match what is selected. Clearing the filter shows them again.',
  ],
]

/* ------------------------------------------------------------ the profile */

describe('CareerProfileCard', () => {
  it('prints the user’s own words and says who wrote them', () => {
    const { container } = renderComponent(<CareerProfileCard profile={profile()} />)

    const card = cardFor('I work on model evaluation and ranking.')
    expect(card.textContent).toContain('Machine Learning Engineer')
    expect(
      card.textContent,
    ).toContain('Five years of applied machine learning, most of it on retrieval and ranking.')
    expect(card.textContent).toContain('Everything above was entered by you.')
    expect(card.textContent).toContain(`Last edited ${instant('2019-01-29T14:05:00Z')}`)

    // A link that parses is an anchor; one that does not is plain text, because
    // an `<a href>` built from a string that is not a URL is navigable somewhere
    // the user did not intend.
    expect(screen.getByRole('link', { name: 'github.com/ada' })).toHaveAttribute(
      'href',
      'https://github.com/ada',
    )
    expect(screen.queryByRole('link', { name: 'not a url at all' })).toBeNull()
    expect(screen.getByText('not a url at all')).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })

  it('gives every missing field its own sentence rather than a fill-in', () => {
    const { container } = renderComponent(
      <CareerProfileCard
        profile={profile({
          headline: null,
          target_role: null,
          target_domain: null,
          location: null,
          summary: null,
          links: [],
        })}
      />,
    )

    const card = cardFor('Your career profile')
    expect(card.textContent).toContain('No target domain set')
    expect(card.textContent).toContain('No location set')
    expect(
      card.textContent,
    ).toContain(
      'No summary written yet. This paragraph is yours — NEXUS does not draft one, because a ' +
        'summary it wrote would be a claim it has no standing to make on your behalf.',
    )
    expect(
      card.textContent,
    ).toContain(
      'No links yet. Anything you add — a repository, a portfolio, a profile page — is listed ' +
        'here exactly as you entered it.',
    )
    // Nothing on the card was generated to fill the gaps.
    expect(card.textContent).not.toMatch(/Analytical Engines|Machine Learning Engineer/)
    expectNoFabricatedNumbers(container)
  })

  it('falls back to the target role as the title when there is no headline', () => {
    renderComponent(<CareerProfileCard profile={profile({ headline: null })} />)

    expect(screen.getByRole('heading', { name: 'Machine Learning Engineer' })).toBeInTheDocument()
  })
})

describe('CareerProfileRegion', () => {
  it('treats a profile that does not exist as a cold start, not as a failure', () => {
    const { container } = renderComponent(
      <CareerProfileRegion
        profile={null}
        emptyAction={<button type="button">Write your profile</button>}
      />,
    )

    // "No profile yet" is the first thing a new account sees. An alert with a
    // retry button would be a worse answer than a form.
    expect(screen.getByText('No career profile yet')).toBeInTheDocument()
    expect(
      screen.getByText(
        'A profile is entirely your own: a target role, a domain, a one-line headline and any ' +
          'links you want on it. Nothing on it is written for you — NEXUS has no opinion about what ' +
          'you are aiming at and will not guess at one.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Write your profile' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
    expectNoFabricatedNumbers(container)
  })

  it('renders the backend’s own reason in preference to its built-in copy', () => {
    renderComponent(
      <CareerProfileRegion
        profile={null}
        emptyReason="A profile exists on this account, but the read above did not return it. Retrying will fetch it again."
      />,
    )

    expect(
      screen.getByText(
        'A profile exists on this account, but the read above did not return it. Retrying will fetch it again.',
      ),
    ).toBeInTheDocument()
  })

  it('separates a failed read from an absent one, and offers a retry', () => {
    renderComponent(
      <CareerProfileRegion
        profile={null}
        error={
          new ApiError({
            status: 500,
            code: 'internal_error',
            message: 'The career service is unavailable.',
            requestId: 'req-career-1',
          })
        }
        onRetry={() => undefined}
      />,
    )

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('the career profile could not be loaded')
    expect(alert).toHaveTextContent('req-career-1')
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument()
    // A failed read must not masquerade as a cold start.
    expect(screen.queryByText('No career profile yet')).toBeNull()
  })

  it('draws a silhouette with no digits and no placeholder words', () => {
    const { container } = renderComponent(<CareerProfileRegion profile={null} isLoading />)

    // A skeleton that rendered a fake headline would put invented words — the
    // one thing this surface may not do — on screen.
    expect(container.textContent ?? '').not.toMatch(/\d/)
    expect(container.textContent ?? '').toBe('')
  })
})

/* ------------------------------------------------------------- the summary */

describe('CareerSummaryTiles', () => {
  it('counts the rows the profile holds and quotes the server’s own sentence', () => {
    const { container } = renderComponent(<CareerSummaryTiles summary={SUMMARY} />)

    expect(screen.getByText(SUMMARY.summary)).toBeInTheDocument()
    expect(screen.getByText(`Most recent evidence dated ${formatCareerDateOf('2019-01-20')}.`)).toBeInTheDocument()

    // A reader who wants a judgement is not given one by NEXUS wearing a
    // number's clothes: no readiness score, no employer match, no fit.
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/readiness|employer match|good fit|suitab/i)
    expectNoFabricatedNumbers(container)
  })

  /**
   * The regression this row was written for.
   *
   * Four of the five tiles read `experience_count`, `education_count`,
   * `certification_count` and `linked_evidence_count`, none of which
   * `GET /career/summary` sends — so they rendered `NO_VALUE`, this project's
   * own "not measured" dash, over figures the backend had counted. Every tile is
   * now asserted against a value from the fixture **and** against the absence of
   * the dash, because a dash-free assertion alone would pass on a label that had
   * quietly stopped rendering a count.
   */
  it('renders five real numbers from the fields the summary route sends', () => {
    const { container } = renderComponent(<CareerSummaryTiles summary={SUMMARY} />)

    const expected: [string, number][] = [
      ['Dated records', SUMMARY.record_count],
      ['Evidence', SUMMARY.evidence_count],
      [`Evidence, last ${formatNumber(SUMMARY.window_days)} days`, SUMMARY.evidence_in_window],
      ['Entered by you', SUMMARY.manual_evidence_count],
      ['Projects completed', SUMMARY.completed_project_count],
    ]

    for (const [label, value] of expected) {
      const tile = tileFor(label)
      expect(within(tile).getByText(formatNumber(value))).toBeInTheDocument()
      expect(tile.textContent ?? '').not.toContain(NO_VALUE)
    }

    // Not one of the six keys the route dropped survives as a label.
    const labels = container.textContent ?? ''
    expect(labels).not.toMatch(/Linked evidence/)
    expect(screen.queryByText('Certifications')).toBeNull()
    expectNoFabricatedNumbers(container)
  })

  it('names the window its date-bounded tile covers, rather than saying "recent"', () => {
    renderComponent(<CareerSummaryTiles summary={{ ...SUMMARY, window_days: 90 }} />)

    const tile = tileFor(`Evidence, last ${formatNumber(90)} days`)
    // A tile copied out of context still says what range it counted over.
    expect(within(tile).getByText(formatNumber(SUMMARY.evidence_in_window))).toBeInTheDocument()
    expect(tile.textContent).toContain('Evidence dated inside that window, over the whole history before it')
    expect(screen.queryByText(`Evidence, last ${formatNumber(30)} days`)).toBeNull()
  })

  it('refuses to render a row of zeroes on a profile that holds nothing', () => {
    const { container } = renderComponent(
      <CareerSummaryTiles
        summary={{
          ...SUMMARY,
          has_profile: false,
          target_role: null,
          link_count: 0,
          record_count: 0,
          evidence_count: 0,
          evidence_in_window: 0,
          manual_evidence_count: 0,
          linked_project_count: 0,
          project_count: 0,
          completed_project_count: 0,
          repository_count: 0,
          skills_with_evidence: 0,
          learning_activity_count: 0,
          latest_evidence_on: null,
          has_data: false,
          summary: 'Nothing has been recorded on this profile yet.',
        }}
      />,
    )

    expect(screen.getByText(NOT_ENOUGH_DATA_TITLE)).toBeInTheDocument()
    expect(
      screen.getByText(
        'The counts above are read from the records on this profile. They stay at nothing until ' +
          'there is at least one, because a dashboard of zeroes reads as a measurement and is not one.',
      ),
    ).toBeInTheDocument()
    expect(screen.queryByText('Evidence')).toBeNull()
    expectNoFabricatedNumbers(container)
  })

  it('announces its silhouette without a placeholder digit', () => {
    renderComponent(<CareerSummaryTiles summary={null} isLoading />)

    const status = screen.getByRole('status')
    expect(status).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByText('Loading the career summary')).toBeInTheDocument()
    expect(status.textContent ?? '').not.toMatch(/\d/)
  })
})

describe('CareerSummaryTilesSkeleton', () => {
  it('reserves the layout without drawing a figure', () => {
    renderComponent(<CareerSummaryTilesSkeleton />)

    const status = screen.getByRole('status')
    expect(status.textContent ?? '').toBe('Loading the career summary')
    // The tile row is hand-rolled `<div>`s rather than the `Skeleton` primitive,
    // so it is matched on the muted block it is made of.
    expect(status.querySelectorAll('.bg-muted').length).toBeGreaterThan(5)
  })
})

/* -------------------------------------------------------------- the levels */

describe('a skill level is never a bare number', () => {
  it('renders both levels with the source of each beside them', () => {
    const { container } = renderComponent(<SkillOverviewTile skill={skill()} />)

    const card = cardFor('Machine Learning')
    const current = sectionFor(card, 'Current level')
    expect(
      within(current).getByText('2 of 5, self-assessed by you'),
    ).toBeInTheDocument()
    expect(within(current).getByText('Self-assessed')).toBeInTheDocument()
    expect(
      current.textContent,
    ).toContain('You set this level yourself and NEXUS records it without adjusting it.')

    const target = sectionFor(card, 'Target level')
    expect(within(target).getByText('4 of 5')).toBeInTheDocument()
    expect(within(target).getByText('— the level you set for this skill.')).toBeInTheDocument()

    const evidence = sectionFor(card, 'Evidence')
    expect(evidence.textContent).toContain('6 learning activities recorded.')
    expect(evidence.textContent).toContain(`Last recorded activity ${instant('2019-01-29T14:05:00Z')}.`)
    expectNoFabricatedNumbers(container)
  })

  it('attributes an estimate to NEXUS and says what it rests on', () => {
    const { container } = renderComponent(
      <SkillOverviewTile
        skill={skill({ current_level: 4, level_source: 'system_estimate', confidence: 92, evidence_count: 41 })}
      />,
    )

    const current = sectionFor(cardFor('Machine Learning'), 'Current level')
    expect(
      within(current).getByText('4 of 5, estimated by NEXUS from recorded activities'),
    ).toBeInTheDocument()
    expect(within(current).getByText('NEXUS estimate')).toBeInTheDocument()
    expect(current.textContent).toContain('NEXUS derived this level from the learning activities')
    expect(
      screen.getByText('41 learning activities recorded.'),
    ).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })

  it('states a genuine zero evidence count in words rather than as a dash', () => {
    const { container } = renderComponent(
      <SkillOverviewTile skill={skill({ evidence_count: 0, last_activity_at: null })} />,
    )

    const evidence = sectionFor(cardFor('Machine Learning'), 'Evidence')
    expect(evidence.textContent).toContain('No learning activities recorded against this skill.')
    expect(evidence.textContent).toContain('No activity has been recorded against this skill.')
    expect(within(evidence).queryByText(NO_VALUE)).toBeNull()
    expectNoFabricatedNumbers(container)
  })

  it('never ranks skills against each other, in order or in weight', () => {
    const { container } = renderComponent(
      <SkillOverviewGrid
        skills={[
          skill({ current_level: 1, target_level: 5 }),
          skill({
            id: '55555555-5555-4555-8555-555555555555',
            name: 'SQL',
            current_level: 5,
            target_level: 5,
            level_source: 'system_estimate',
          }),
        ]}
      />,
    )

    // The grid is not sorted by level and offers no "strongest skill": the order
    // is the order the caller passed in.
    const headings = screen.getAllByRole('heading', { level: 3 }).map((node) => node.textContent)
    expect(headings).toEqual(['Machine Learning', 'SQL'])
    expect(
      screen.getByText(
        '2 skills shown, in the order they were listed. Levels are not ranked against each other.',
      ),
    ).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })

  it('points an empty grid at the page that fills it, with no zeroes', () => {
    const { container } = renderComponent(<SkillOverviewGrid skills={[]} />)

    expect(screen.getByText('No skills to show yet')).toBeInTheDocument()
    expect(
      screen.getByText(
        'This panel reads the skills you have tracked. Add one on the learning page with the level ' +
          'you claim for it, and it appears here with that level and where it came from.',
      ),
    ).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })

  it('announces its silhouette without a level meter or a digit', () => {
    renderComponent(<SkillOverviewGridSkeleton count={3} />)

    const status = screen.getByRole('status')
    expect(screen.getByText('Loading tracked skills')).toBeInTheDocument()
    expect(status.textContent ?? '').not.toMatch(/\d/)
  })
})

/* --------------------------------------------------- development areas */

describe('DevelopmentAreasPanel', () => {
  it('states a level with its source and a count of records, never a verdict', () => {
    const { container } = renderComponent(
      <DevelopmentAreasPanel gaps={[gap()]} windowDays={30} />,
    )

    const row = screen.getByText('Machine Learning').closest('li') as HTMLElement
    // The product's own sentence: a distance and a count of records. The same
    // fact phrased as "you are weak at X" is the thing this panel exists to
    // refuse.
    expect(
      within(row).getByText(
        '3 of 5, self-assessed by you. 1 related learning activity in the last 30 days.',
      ),
    ).toBeInTheDocument()
    expect(within(row).getByText('Self-assessed')).toBeInTheDocument()
    expect(
      within(row).getByText(
        `Target 4 of 5, the level you set for this skill. The most recent activity was recorded yesterday.`,
      ),
    ).toBeInTheDocument()

    expect(
      screen.getByText(
        '1 of 1 tracked skill listed has a target above the level recorded and fewer than 3 related activities in the last 30 days.',
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        'Each line is a level you set or an estimate NEXUS derived from recorded activities, ' +
          'shown with the number of records behind it. Nothing on this panel is a score, and no ' +
          'two skills are compared with each other.',
      ),
    ).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })

  it('never uses the brief’s forbidden vocabulary anywhere on the panel', () => {
    const { container } = renderComponent(
      <DevelopmentAreasPanel
        gaps={[
          gap(),
          gap({
            skill_id: '55555555-5555-4555-8555-555555555555',
            skill_name: 'SQL',
            current_level: 1,
            target_level: 5,
            gap: 4,
            level_source: 'system_estimate',
            evidence_last_30d: 0,
            evidence_count: 0,
            days_since_last_activity: null,
            explanation:
              'Target 5/5, current NEXUS estimate 1/5. NEXUS recorded 0 related learning activities in the last 30 days.',
          }),
        ]}
        windowDays={30}
      />,
    )

    const text = container.textContent ?? ''
    expect(text).not.toMatch(/not good at|you are weak|you are bad at|weak at|poor at/i)
    expect(text).not.toMatch(/lack(?:s|ing) (?:skill|ability|talent)|incompetent|unskilled/i)
    expect(text).not.toMatch(/weakness|strength|proficiency|readiness score/i)
    expect(text).not.toMatch(/improve your/i)

    // A zero count of recorded activities is still stated as a measurement.
    expect(
      screen.getByText(
        '1 of 5, estimated by NEXUS from recorded activities. No related learning activities recorded in the last 30 days.',
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        `Target 5 of 5, the level you set for this skill. Nothing has been recorded against this skill yet.`,
      ),
    ).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })

  it('excludes a skill whose levels could not be compared, and one already at its target', () => {
    renderComponent(
      <DevelopmentAreasPanel
        gaps={[
          gap({ current_level: 4, target_level: 4, gap: 0 }),
          gap({
            skill_id: '55555555-5555-4555-8555-555555555555',
            skill_name: 'Rust',
            available: false,
            reason_if_unavailable: NO_LEVEL_REASON,
            gap: 0,
          }),
        ]}
        windowDays={30}
      />,
    )

    // "Not enough data yet" — with the reason, and no row claiming a shortfall
    // nobody measured. An unmeasured comparison is excluded rather than listed
    // as a skill with a gap, and a skill already at its target has none.
    expect(screen.getByText(NOT_ENOUGH_DATA_TITLE)).toBeInTheDocument()
    expect(screen.queryByText('Machine Learning')).toBeNull()
    expect(screen.queryByText('Rust')).toBeNull()
    expect(developmentAreasFromGaps([
      gap({ current_level: 4, target_level: 4, gap: 0 }),
      gap({ skill_name: 'Rust', available: false, gap: 0 }),
    ])).toEqual([])
    expect(
      screen.getByText(
        'This panel lists skills where you have set a target above the current level and there is ' +
          'little recorded evidence behind them. With no such skills it has nothing to say — which is ' +
          'not the same as saying every skill is well evidenced.',
      ),
    ).toBeInTheDocument()
  })

  it('applies one inspectable membership rule, shared by the panel and its callers', () => {
    const gaps = [
      gap(),
      gap({ skill_name: 'No evidence', evidence_last_30d: 0 }),
      gap({ skill_name: 'Enough evidence', evidence_last_30d: 9 }),
      gap({ skill_name: 'At target', gap: 0 }),
      gap({ skill_name: 'Unmeasured', available: false }),
    ]

    // Open gap, measurable, and fewer than the threshold related activities.
    expect(developmentAreasFromGaps(gaps, 3).map((row) => row.skill_name)).toEqual([
      'Machine Learning',
      'No evidence',
    ])
    // `threshold: null` disables the evidence cut but not the other two rules.
    expect(developmentAreasFromGaps(gaps, null).map((row) => row.skill_name)).toEqual([
      'Machine Learning',
      'No evidence',
      'Enough evidence',
    ])
  })

  it('describes the window it counted over, and lets a caller state no window', () => {
    const { unmount } = renderComponent(
      <DevelopmentAreasPanel gaps={[gap()]} windowDays={30} />,
    )
    expect(
      screen.getByText(
        '1 of 1 tracked skill listed has a target above the level recorded and fewer than 3 related activities in the last 30 days.',
      ),
    ).toBeInTheDocument()
    unmount()

    renderComponent(<DevelopmentAreasPanel gaps={[gap()]} windowDays={null} />)
    expect(
      screen.getByText(
        '1 of 1 tracked skill listed has a target above the level recorded and fewer than 3 related activities in the window.',
      ),
    ).toBeInTheDocument()
  })

  it('announces its silhouette with no level and no zero beside it', () => {
    renderComponent(<DevelopmentAreasSkeleton count={3} />)

    const status = screen.getByRole('status')
    expect(screen.getByText('Loading development areas')).toBeInTheDocument()
    // "1 / 5 · 0 activities" would be both an unattributed level and a measured
    // zero nobody recorded.
    expect(status.textContent ?? '').not.toMatch(/\d/)
  })
})

describe('the career formatters', () => {
  it('requires the source for every level sentence', () => {
    expect(describeCareerLevel(3, 'user_defined')).toBe('3 of 5, self-assessed by you')
    expect(describeCareerLevel(3, 'system_estimate')).toBe(
      '3 of 5, estimated by NEXUS from recorded activities',
    )
    expect(formatCareerLevel(5)).toBe('5 of 5')
    // A level out of range renders as the top of the scale, not as a number
    // nobody has ever been asked to interpret.
    expect(formatCareerLevel(9)).toBe('5 of 5')
    expect(formatCareerLevel(null)).toBe(NO_VALUE)
  })

  it('distinguishes an absence of measurement from a measured zero', () => {
    expect(describeCareerEvidence(6)).toBe('6 learning activities recorded.')
    expect(describeCareerEvidence(1)).toBe('1 learning activity recorded.')
    expect(describeCareerEvidence(0)).toBe('No learning activities recorded against this skill.')
    expect(describeCareerEvidence(null)).toBe(NO_VALUE)

    expect(describeCareerDaysSince(0)).toBe('The most recent activity was recorded today.')
    expect(describeCareerDaysSince(1)).toBe('The most recent activity was recorded yesterday.')
    expect(describeCareerDaysSince(40)).toBe('The most recent activity was recorded 40 days ago.')
    // `null` means nothing has ever been recorded; `0` would claim today.
    expect(describeCareerDaysSince(null)).toBe('Nothing has been recorded against this skill yet.')
  })

  it('reads a sparse by_type record as absent rather than as zero', () => {
    expect(countFromRecord({ achievement: 0 }, 'achievement')).toBe(0)
    expect(countFromRecord({ achievement: 3 }, 'achievement')).toBe(3)
    // A key the response does not carry is the server declining to state a count.
    expect(countFromRecord({ achievement: 3 }, 'repository_activity')).toBeNull()
    expect(countFromRecord({}, 'achievement')).toBeNull()
    expect(countFromRecord(null, 'achievement')).toBeNull()
    expect(countFromRecord(undefined, 'achievement')).toBeNull()
  })

  it('never merges a typed row with one NEXUS derived', () => {
    expect(describeEvidenceSource('manual')).toBe(
      'Added by you. Everything on this row is what you typed.',
    )
    expect(describeEvidenceSource(null)).toBe(
      'Added by you. Everything on this row is what you typed.',
    )
    expect(describeEvidenceSource('project')).toBe(
      'Recorded by NEXUS from project records. You did not write this row; it was derived from a record you created.',
    )
    expect(describeEvidenceSource('repository')).toBe(
      'Recorded by NEXUS from repository scans. You did not write this row; it was derived from a record you created.',
    )
  })

  it('tells a current role from an undated one, and never invents a date', () => {
    const start = '2021-03-01'
    const end = '2024-06-01'

    expect(describeRecordPeriod(start, end)).toBe(`${monthYear(start)} — ${monthYear(end)}`)
    // `ended_on: null` means *current*, which is a fact about the record.
    expect(describeRecordPeriod(start, null)).toBe(`${monthYear(start)} — current`)
    // `started_on: null` is an absence, and no start is invented from created_at.
    expect(describeRecordPeriod(null, end)).toBe(`Until ${monthYear(end)}`)
    expect(describeRecordPeriod(null, null)).toBe('No dates given')

    expect(formatRecordMonth(start)).toBe(monthYear(start))
    expect(formatRecordMonth('')).toBe(NO_VALUE)
  })

  it('anchors a profile link only when it is a URL', () => {
    expect(describeLinkLabel('https://github.com')).toBe('github.com')
    expect(describeLinkLabel('https://github.com/ada')).toBe('github.com/ada')
    expect(describeLinkLabel('https://www.github.com/ada/nexo/')).toBe('github.com/ada/nexo')
    // Whatever the user pasted is shown as given, not mangled into a label.
    expect(describeLinkLabel('ada@nexus.local')).toBe('ada@nexus.local')

    expect(isNavigableLink('https://github.com/ada')).toBe(true)
    expect(isNavigableLink('javascript:alert(1)')).toBe(false)
    expect(isNavigableLink('not a url at all')).toBe(false)
  })

  it('assembles the development sentence from a level and a record count only', () => {
    expect(describeDevelopmentSentence(gap(), 30)).toBe(
      '3 of 5, self-assessed by you. 1 related learning activity in the last 30 days.',
    )
    expect(describeDevelopmentSentence(gap({ evidence_last_30d: 0 }), 30)).toBe(
      '3 of 5, self-assessed by you. No related learning activities recorded in the last 30 days.',
    )
    expect(describeDevelopmentSentence(gap({ evidence_last_30d: 4 }), 30)).toBe(
      '3 of 5, self-assessed by you. 4 related learning activities in the last 30 days.',
    )
    expect(describeDevelopmentSentence(gap(), null)).toBe(
      '3 of 5, self-assessed by you. 1 related learning activity in the window.',
    )
  })
})

/* ------------------------------------------------------------- the records */

describe('CareerRecordList', () => {
  it('shows a dated record the way the person wrote it', () => {
    const { container } = renderComponent(
      <CareerRecordList records={[record()]} title="Dated records" total={1} />,
    )

    const card = cardFor('Dated records')
    expect(card.textContent).toContain('1 record shown. Everything here was entered by you, with the dates you gave.')
    const row = screen.getByText('Machine Learning Engineer').closest('li') as HTMLElement
    expect(row.textContent).toContain('Analytical Engines')
    expect(row.textContent).toContain(`${monthYear('2021-03-01')} — ${monthYear('2024-06-01')}`)
    expect(row.textContent).toContain('Owned the retrieval stack.')
    expect(within(row).getByText('Experience')).toBeInTheDocument()
    expect(
      row.textContent,
    ).toContain(
      'A role you listed. An end date you left empty means the role is current, which is a fact ' +
        'about the record rather than a missing value.',
    )
    expectNoFabricatedNumbers(container)
  })

  it('says a role is current when no end date was given', () => {
    renderComponent(
      <CareerRecordList
        records={[record({ ended_on: null, title: 'Staff Engineer' })]}
        title="Dated records"
      />,
    )

    const row = screen.getByText('Staff Engineer').closest('li') as HTMLElement
    expect(row.textContent).toContain(`${monthYear('2021-03-01')} — current`)
  })

  it('anchors a record link only when it parses, and keeps a certification a record', () => {
    renderComponent(
      <ul>
        <CareerRecordList
          records={[
            record({ kind: 'certification', title: 'AWS Solutions Architect', url: 'https://example.com/cert' }),
            record({ id: '66666666-6666-4666-8666-666666666666', title: 'BSc Computer Science', url: 'see my transcript' }),
          ]}
        />
      </ul>,
    )

    expect(screen.getByRole('link', { name: 'example.com/cert' })).toHaveAttribute(
      'href',
      'https://example.com/cert',
    )
    expect(screen.queryByRole('link', { name: 'see my transcript' })).toBeNull()
    expect(screen.getByText('Certification')).toBeInTheDocument()
    // A certification exists because the *user* holds one; NEXUS issues nothing.
    expect(
      screen.getByText(
        'A certification you hold, listed with the issuer and dates you supplied. NEXUS issues nothing and verifies nothing.',
      ),
    ).toBeInTheDocument()
  })

  it('states an empty record list without promising NEXUS will fill it', () => {
    const { container } = renderComponent(<CareerRecordList records={[]} />)

    expect(screen.getByText('No education, experience or certifications listed yet')).toBeInTheDocument()
    expect(
      screen.getByText(
        'These are the dated records a profile is made of: where you studied, the roles you have ' +
          'held, the certifications you hold. You add them, with the dates you know, and NEXUS stores ' +
          'them exactly as given.',
      ),
    ).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })

  it('announces its silhouette with no date in it', () => {
    renderComponent(<CareerRecordListSkeleton count={4} />)

    const status = screen.getByRole('status')
    expect(screen.getByText('Loading career records')).toBeInTheDocument()
    // A grey "2021 — 2024" would be a career history nobody entered.
    expect(status.textContent ?? '').not.toMatch(/\d/)
  })

  it('names the kind it was narrowed to, and says a filtered list is not an empty profile', () => {
    const { unmount } = renderComponent(<CareerRecordList records={[record()]} kind="education" />)
    expect(screen.getByRole('heading', { name: 'Education' })).toBeInTheDocument()
    unmount()

    const empty = renderComponent(<CareerRecordList records={[]} kind="education" />)
    expect(screen.getByText('No education, experience or certifications listed yet')).toBeInTheDocument()
    expect(screen.queryByText('Nothing matches this filter')).toBeNull()
    empty.unmount()

    renderComponent(<CareerEmptyState variant="filtered" />)
    expect(screen.getByText('Nothing matches this filter')).toBeInTheDocument()
  })
})

/* ------------------------------------------------------------ the evidence */

describe('PortfolioEvidenceTimeline', () => {
  it('groups rows by kind without ranking one kind above another', () => {
    const { container } = renderComponent(
      <PortfolioEvidenceTimeline
        evidence={[
          evidence(),
          evidence({ id: '77777777-7777-4777-8777-777777777777', evidence_type: 'repository_activity', title: 'Six commits touched Python files' }),
          evidence({ id: '88888888-8888-4888-8888-888888888888', evidence_type: 'certification', title: 'AWS Solutions Architect' }),
        ]}
        byType={{ achievement: 1, repository_activity: 1, certification: 1 }}
        total={3}
      />,
    )

    expect(
      screen.getByText('3 rows shown across 3 kinds, newest first within each.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Achievement/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Repository activity/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Certification/ })).toBeInTheDocument()
    expect(screen.getAllByText('1 in total')).toHaveLength(3)

    // A certification exists because the user supplied it. The vocabulary says so
    // where a reader hovers, before "things NEXUS recorded" misleads anyone.
    expect(
      screen.getByText(
        'A certification **you supplied**, with the title and date you gave it. NEXUS issues no ' +
          'credential, verifies none and infers none — it stores what you entered.',
      ),
    ).toBeInTheDocument()
    // Repository evidence names code events and says it is not a task count.
    expect(
      screen.getByText(
        'Activity a repository scan recorded: commits, branches and changed lines. This names ' +
          'code events — it is not a count of tasks delivered, and it is not a statement about ' +
          'anyone’s time.',
      ),
    ).toBeInTheDocument()
    expect(container.textContent ?? '').not.toMatch(/projects delivered/i)
    expectNoFabricatedNumbers(container)
  })

  it('says how many rows are shown when the response carries no count for that kind', () => {
    const { container } = renderComponent(
      <PortfolioEvidenceTimeline evidence={[evidence()]} byType={{ repository_activity: 4 }} total={1} />,
    )

    // A missing key is not a measured zero, so the header says what is on screen
    // rather than substituting a figure the server never sent.
    expect(screen.getByText(/1 shown/)).toBeInTheDocument()
    expect(screen.queryByText(/0 in total/)).toBeNull()
    expectNoFabricatedNumbers(container)
  })

  it('tells a typed row from a derived one on every single row', () => {
    const { container } = renderComponent(
      <ul>
        <PortfolioEvidenceRow evidence={evidence()} />
        <PortfolioEvidenceRow
          evidence={evidence({
            id: '99999999-9999-4999-8999-999999999999',
            title: 'Repository scan recorded 12 commits',
            source: 'repository',
          })}
        />
      </ul>,
    )

    expect(screen.getByText('Added by you')).toBeInTheDocument()
    expect(screen.getByText('Recorded by NEXUS')).toBeInTheDocument()
    expect(
      screen.getByTitle('Added by you. Everything on this row is what you typed.'),
    ).toBeInTheDocument()
    expect(
      screen.getByTitle(
        'Recorded by NEXUS from repository scans. You did not write this row; it was derived from a record you created.',
      ),
    ).toBeInTheDocument()
    expect(container.textContent ?? '').not.toMatch(/undefined/)
    expectNoFabricatedNumbers(container)
  })

  it('names a link whose target has been deleted rather than dropping it', () => {
    renderComponent(
      <ul>
        <PortfolioEvidenceRow
          evidence={evidence({
            project_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            skill_id: SKILL_ID,
            repository_id: null,
          })}
          skillName="Machine Learning"
        />
      </ul>,
    )

    expect(screen.getByText('Linked project no longer available')).toBeInTheDocument()
    expect(screen.getByText('Machine Learning')).toBeInTheDocument()
  })

  it('states an empty timeline in terms of what the user does', () => {
    const { container } = renderComponent(<PortfolioEvidenceTimeline evidence={[]} />)

    expect(screen.getByText('No evidence added yet')).toBeInTheDocument()
    expect(
      screen.getByText(
        'Evidence is what you want to point at: a project, a feature, a repository’s recorded ' +
          'activity, a certification or an achievement. Add one with a date, and it appears in the ' +
          'timeline grouped by kind.',
      ),
    ).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })

  it('says when the visible rows are the whole set and when they are a position in it', () => {
    const complete = renderComponent(
      <PortfolioEvidenceScopeNote total={2} shown={2} />,
    )
    expect(complete.getByText('Everything on file is shown here.')).toBeInTheDocument()
    complete.unmount()

    renderComponent(<PortfolioEvidenceScopeNote total={40} shown={12} />)
    expect(
      screen.getByText(
        'Showing 12 of 40 recorded rows. This is a position in the list, not the whole set.',
      ),
    ).toBeInTheDocument()
  })

  it('announces its silhouette with no date in it', () => {
    renderComponent(<PortfolioEvidenceTimelineSkeleton groups={2} rowsPerGroup={2} />)

    const status = screen.getByRole('status')
    expect(screen.getByText('Loading portfolio evidence')).toBeInTheDocument()
    // A grey "01 Jan 2025" would be a date the user never entered.
    expect(status.textContent ?? '').not.toMatch(/\d/)
  })
})

/* -------------------------------------------------------- empty and error */

describe('CareerEmptyState', () => {
  it('says why each region is empty and what the user does to fill it', () => {
    for (const [variant, title, description] of EMPTY_COPY) {
      const { unmount } = renderComponent(<CareerEmptyState variant={variant as 'profile'} />)
      expect(screen.getByText(title)).toBeInTheDocument()
      expect(screen.getByText(description)).toBeInTheDocument()
      unmount()
    }
  })

  it('does not call a cold start "not enough data", because nothing is missing', () => {
    const cold = renderComponent(<CareerEmptyState variant="profile" />)
    expect(screen.getByText('No career profile yet')).toBeInTheDocument()
    expect(screen.queryByText(NOT_ENOUGH_DATA_TITLE)).toBeNull()
    cold.unmount()

    // A profile that exists but has nothing on it is a different state from one
    // that was never written.
    const empty = renderComponent(<CareerEmptyState variant="records" />)
    expect(
      screen.getByText('No education, experience or certifications listed yet'),
    ).toBeInTheDocument()
    expect(screen.queryByText(NOT_ENOUGH_DATA_TITLE)).toBeNull()
    empty.unmount()
  })

  it('never promises that NEXUS will find or draft a qualification', () => {
    const { container } = renderComponent(
      <div>
        {EMPTY_COPY.map(([variant]) => (
          <CareerEmptyState key={variant} variant={variant as 'profile'} />
        ))}
      </div>,
    )

    const text = container.textContent ?? ''
    // Every empty state must name the action the *person* takes.
    expect(text).not.toMatch(/NEXUS will find|NEXUS will draft|we will generate|automatically generated/i)
    expect(text).not.toMatch(/import from|GitHub sync|LinkedIn import/i)
    expectNoFabricatedNumbers(container)
  })
})

describe('CareerRegionError and CareerStaleNotice', () => {
  it('names the region that failed and quotes the request id', () => {
    renderComponent(
      <CareerRegionError
        error={
          new ApiError({
            status: 500,
            code: 'internal_error',
            message: 'The career service is unavailable.',
            requestId: 'req-career-2',
          })
        }
        subject="the evidence timeline"
        onRetry={() => undefined}
      />,
    )

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('the evidence timeline could not be loaded')
    expect(alert).toHaveTextContent('req-career-2')
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument()
    expect(alert.textContent ?? '').not.toMatch(/Traceback|line \d+, in/)
  })

  it('says the figures are from the previous read while a refetch is in flight', () => {
    const settled = renderComponent(<CareerStaleNotice isStale={false} subject="the career profile" />)
    // It renders nothing at all when settled, so a page carries no residue from a
    // state it has already left.
    expect(settled.container.textContent ?? '').toBe('')
    settled.unmount()

    renderComponent(<CareerStaleNotice isStale subject="the career profile" />)
    expect(
      screen.getByText('Refreshing the career profile. The values shown are from the last completed read.'),
    ).toBeInTheDocument()
  })
})

/* ---------------------------------------------------------------- the guard */

describe('the career component library', () => {
  it('fetches nothing of its own, so a card renders from a literal and a provider', () => {
    const calls = installBackend()

    renderComponent(
      <div>
        <CareerSummaryTiles summary={SUMMARY} />
        <CareerProfileRegion profile={profile()} />
        <SkillOverviewGrid skills={[skill()]} />
        <DevelopmentAreasPanel gaps={[gap()]} windowDays={30} />
        <PortfolioEvidenceTimeline evidence={[evidence()]} byType={{ achievement: 1 }} total={1} />
        <CareerRecordList records={[record()]} title="Dated records" total={1} />
      </div>,
    )

    // Presentational by construction: query state and the derived joins live in
    // `hooks.ts`, which another layer owns. A component that started fetching
    // would make every page suite a lie about what it is mounting.
    expect(calls).toHaveLength(0)
    expect(screen.getByText('2 of 5, self-assessed by you')).toBeInTheDocument()
    expect(
      screen.getByText('3 of 5, self-assessed by you. 1 related learning activity in the last 30 days.'),
    ).toBeInTheDocument()
    expect(screen.getByText(SUMMARY.summary)).toBeInTheDocument()
    expectNoFabricatedNumbers(document.body)
  })

  it('never writes a qualification the user did not supply', () => {
    const { container } = renderComponent(
      <div>
        <CareerProfileCard profile={profile()} />
        <CareerRecordList records={[record()]} title="Dated records" />
        <PortfolioEvidenceTimeline evidence={[evidence()]} total={1} />
      </div>,
    )

    const text = container.textContent ?? ''
    // Nothing may be awarded, verified, inferred or issued by the surface.
    expect(text).not.toMatch(/awarded|verified by|issued by NEXUS|recognised by|inferred employer/i)
    expect(text).not.toMatch(/qualified to|ready for|employer match|fit for/i)
    expectNoFabricatedNumbers(container)
  })

  it('carries both honesty rules through the shapes the pages hand it', () => {
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
    const EVIDENCE: CareerEvidenceListRead = {
      items: [evidence()],
      total: 1,
      limit: 50,
      offset: 0,
      by_type: { achievement: 1 },
      by_source: { manual: 1 },
      manual_count: 1,
      summary: '1 career evidence record.',
    }
    const RECORDS: CareerExperienceListRead = {
      items: [record()],
      total: 1,
      limit: 50,
      offset: 0,
      by_kind: { experience: 1 },
      current_count: 1,
      summary: '1 career record.',
    }

    const { container } = renderComponent(
      <div>
        <SkillOverviewGrid skills={SKILLS.items} />
        <DevelopmentAreasPanel gaps={[gap()]} windowDays={30} />
        <PortfolioEvidenceTimeline evidence={EVIDENCE.items} byType={EVIDENCE.by_type} total={EVIDENCE.total} />
        <CareerRecordList records={RECORDS.items} total={RECORDS.total} />
      </div>,
    )

    // Both levels attributed, both provenances named, no fabricated figure.
    expect(screen.getByText('2 of 5, self-assessed by you')).toBeInTheDocument()
    expect(screen.getByText('Added by you')).toBeInTheDocument()
    // Once as the group heading, once as the badge on the row.
    expect(screen.getAllByText('Achievement').length).toBeGreaterThanOrEqual(2)
    expect(CAREER_EVIDENCE_TYPE_META.certification.label).toBe('Certification')
    expect(CAREER_RECORD_KIND_META.experience.label).toBe('Experience')
    expectNoFabricatedNumbers(container)
  })

  it('resolves its lazy chart boundaries without leaving a fallback behind', async () => {
    const { container } = renderComponent(
      <PortfolioEvidenceTimeline evidence={[evidence()]} total={1} />,
    )
    await resolveCharts(container)
    expect(chartFallback(container)).toBeNull()
  })
})

/** The `YYYY-MM-DD` rendering `formatCareerDate` produces, in this locale. */
function formatCareerDateOf(dateOnly: string): string {
  const parts = dateOnly.split('-').map(Number)
  const year = parts[0] ?? 1970
  const month = parts[1] ?? 1
  const day = parts[2] ?? 1
  const now = new Date()
  return new Intl.DateTimeFormat(undefined, {
    day: 'numeric',
    month: 'short',
    ...(year === now.getFullYear() ? {} : { year: 'numeric' }),
  }).format(new Date(year, month - 1, day))
}