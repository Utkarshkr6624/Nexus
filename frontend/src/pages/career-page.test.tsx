import type { ReactElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/tooltip'
import CareerPage from '@/pages/career-page'
import { NO_VALUE, formatNumber } from '@/features/analytics/format'
import { formatLevelOfScale, levelSourcePhrase } from '@/features/learning/components'
import { NOT_ENOUGH_DATA_TITLE } from '@/features/career/components/career-vocabulary'
import { queryRetryPolicy } from '@/app/query-client'
import type { ApiErrorEnvelope } from '@/types/api'
import type { Paginated } from '@/types/pagination'
import type {
  CareerEvidenceListRead,
  CareerEvidenceRead,
  CareerExperienceListRead,
  CareerExperienceRead,
  CareerProfileRead,
  CareerSummaryRead,
  SkillGapRead,
  SkillLevelSource,
  SkillListRead,
  SkillRead,
} from '@/types/learning'
import type { RepositoryListRead } from '@/types/developer'
import type { Project } from '@/types/work'

/**
 * The Career page, asserted at the network boundary.
 *
 * The page is mounted for real — real router, real components, real hooks — and
 * only `fetch` is stubbed. Every figure on screen is therefore a body this file
 * wrote: 2 roles, 1 course, 1 certification, 4 pieces of evidence, 2 of them
 * linked to something.
 *
 * **The query client is local, and that is the point.** `AppProviders` mounts the
 * shared singleton and registers `onSessionChange(() => queryClient.clear())`
 * (`src/app/auth-bootstrap.tsx:13`). In jsdom that clear lands mid-test and
 * strands every component at `pending` forever, which is why this suite builds a
 * fresh client per render. The defaults below are the ones in
 * `src/app/query-client.ts`, carried over rather than relaxed: the retry policy in
 * particular is what makes the error surface arrive after a few seconds rather
 * than on the first response, and the 5xx case below waits with an explicit
 * `{ timeout: 20_000 }` because of it.
 *
 * Recharts is mocked to supply a size only, and nothing below asserts on a
 * library internal — this page draws no chart of its own, so the mock is here to
 * keep the shared provider stack identical to the learning page's rather than
 * because anything here needs a drawing surface.
 *
 * **A card title is not a wait for its data.** Every card on this page paints
 * its heading before its rows exist, so {@link waitForData} waits on strings
 * that can only be on screen once a read has been answered. That is the same
 * trap `LazyChart` sets on a chart's title, one level up.
 *
 * The dates are fixed in 2019 — a completed year — and the one date range this
 * page renders, `Last edited …`, is an absolute instant rather than a relative
 * age, so nothing here depends on the machine's clock.
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

/** The retry policy genuinely backs off; this is not generous for comfort. */
const RETRY_SURFACE_TIMEOUT_MS = 20_000

/**
 * How long the first read is given to land. Generous here cannot mask a defect:
 * every assertion after it is the real check, and a region that never renders
 * fails on its own text rather than on this bound.
 */
const DATA_TIMEOUT_MS = 10_000

const PROFILE_ID = '11111111-1111-4111-8111-111111111111'
const SKILL_ID = '22222222-2222-4222-8222-222222222222'
const PROJECT_ID = '33333333-3333-4333-8333-333333333333'
const REPO_ID = '44444444-4444-4444-8444-444444444444'
const EVIDENCE_ID = '55555555-5555-4555-8555-555555555555'
const RECORD_ID = '66666666-6666-4666-8666-666666666666'
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
  profile?: Route
  experience?: Route
  evidence?: Route
  skills?: Route
  gaps?: Route
  projects?: Route
  repositories?: Route
  saveProfile?: Route
}

/**
 * Stubs `fetch` with the routing table below, so one test can replace a single
 * endpoint — the absent profile, the failing evidence list, the refused save —
 * without restating the rest.
 */
function installBackend(overrides: Backend = {}): Call[] {
  const summary = overrides.summary ?? (() => json(SUMMARY))
  const profile = overrides.profile ?? (() => json(PROFILE))
  const experience = overrides.experience ?? (() => json(RECORDS))
  const evidence = overrides.evidence ?? (() => json(EVIDENCE))
  const skills = overrides.skills ?? (() => json(SKILLS))
  const gaps = overrides.gaps ?? (() => json(GAPS))
  const projects = overrides.projects ?? (() => json(PROJECTS))
  const repositories = overrides.repositories ?? (() => json(REPOSITORIES))
  const saveProfile = overrides.saveProfile ?? (() => json(PROFILE))

  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      const rawBody = init?.body
      const body = typeof rawBody === 'string' ? (JSON.parse(rawBody) as unknown) : null
      calls.push({ url, method, body })

      if (method === 'PUT' && url.includes('/career/profile')) return saveProfile(url)
      if (url.includes('/career/summary')) return summary(url)
      if (url.includes('/career/profile')) return profile(url)
      if (url.includes('/career/experience')) return experience(url)
      if (url.includes('/career/evidence')) return evidence(url)
      if (url.includes('/learning/skills')) return skills(url)
      if (url.includes('/learning/gaps')) return gaps(url)
      if (url.includes('/projects')) return projects(url)
      if (url.includes('/developer/repositories')) return repositories(url)
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

function renderCareerPage(entry = '/career') {
  const router = createMemoryRouter([{ path: '*', element: <CareerPage /> }], {
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
 * The region a heading owns, so a label cannot be found twice on the page.
 *
 * Two headings on this page share a name — the "Portfolio evidence" section and
 * the timeline card inside it — so the one that actually owns a `<section>` wins,
 * and a heading that owns only a card falls back to that card.
 */
function regionFor(heading: string): HTMLElement {
  const nodes = screen.getAllByRole('heading', { name: heading })
  for (const node of nodes) {
    const region = node.closest('section')
    if (region) return region as HTMLElement
  }
  const first = nodes[0]
  return (first?.closest('section') ?? first?.closest('div.rounded-lg')) as HTMLElement
}

/** The card a title belongs to. */
function cardFor(name: string): HTMLElement {
  return screen.getByRole('heading', { name }).closest('div.rounded-lg') as HTMLElement
}

/**
 * A `MetricCard` tile, found through its label inside the scope that owns it —
 * two of the five labels are also section headings elsewhere on the page.
 */
function tileFor(label: string, scope: HTMLElement): HTMLElement {
  return within(scope).getByText(label).closest('div.rounded-lg') as HTMLElement
}

/** The count an `EvidenceCounts` row prints, exactly as it was rendered. */
function evidenceCountFor(label: string): string {
  const row = screen.getByText(label).closest('div') as HTMLElement
  return (row.querySelector('dd')?.textContent ?? '').trim()
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

/**
 * Waits until the reads this page's assertions depend on have landed.
 *
 * Every card here paints its heading before its rows exist, so a `findBy` on a
 * heading is not a wait for data; these strings are.
 */
async function waitForData(): Promise<void> {
  // The summary sentence is rendered twice — once in the masthead badge and once
  // in the summary card — so this waits on the count, not on uniqueness.
  await screen.findAllByText(SUMMARY.summary, undefined, { timeout: DATA_TIMEOUT_MS })
  await screen.findByText(PROFILE.headline as string, undefined, { timeout: DATA_TIMEOUT_MS })
  // "Machine Learning" is a skill tile, a gap row and a development-area row, so
  // this waits on the count; the skill reads alone are scoped below.
  await screen.findAllByText('Machine Learning', undefined, { timeout: DATA_TIMEOUT_MS })
  await screen.findByText('Published a note on ranking evaluation', undefined, { timeout: DATA_TIMEOUT_MS })
  // The target role appears in the masthead badge, on the profile card and as a
  // dated record, so this waits on the count too.
  await screen.findAllByText('Machine Learning Engineer', undefined, { timeout: DATA_TIMEOUT_MS })
}

function getCalls(calls: Call[], needle: string): Call[] {
  return calls.filter((call) => call.url.includes(needle))
}

/** Lets every in-flight fetch and its re-render settle before asserting. */
async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50))
  })
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
    links: ['https://github.com/ada/nexo'],
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
    current_level: 3,
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

const SUMMARY: CareerSummaryRead = {
  has_profile: true,
  target_role: 'Machine Learning Engineer',
  target_domain: 'Machine Learning',
  experience_count: 2,
  education_count: 1,
  certification_count: 1,
  evidence_count: 4,
  // `skill_activity` is a **genuine zero** — the backend counted it and found
  // none. `repository_activity` is **absent** — the response does not carry the
  // key at all, which is the server declining to state a count. The two must
  // never render the same way.
  by_type: { achievement: 1, skill_activity: 0 },
  linked_evidence_count: 2,
  latest_evidence_on: '2019-01-20',
  has_data: true,
  summary: '4 pieces of evidence are on this profile, 2 of them linked to a project, skill or repository.',
}

/** An account that has never written a profile. */
const NO_PROFILE_SUMMARY: CareerSummaryRead = {
  ...SUMMARY,
  has_profile: false,
  target_role: null,
  target_domain: null,
  experience_count: 0,
  education_count: 0,
  certification_count: 0,
  evidence_count: 0,
  by_type: {},
  linked_evidence_count: 0,
  latest_evidence_on: null,
  has_data: false,
  summary: 'Nothing has been recorded on this profile yet.',
}

const PROFILE = profile()

const SKILLS: SkillListRead = {
  items: [
    skill(),
    skill({
      id: '77777777-7777-4777-8777-777777777777',
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

/** `GET /learning/gaps` answers a bare array, never a wrapped envelope. */
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

const EVIDENCE: CareerEvidenceListRead = {
  items: [
    evidence(),
    evidence({
      id: '88888888-8888-4888-8888-888888888888',
      evidence_type: 'repository_activity',
      title: 'Repository scan recorded 12 commits',
      source: 'repository',
      repository_id: REPO_ID,
    }),
    evidence({
      id: '99999999-9999-4999-8999-999999999999',
      evidence_type: 'certification',
      title: 'AWS Certified Solutions Architect',
      source: 'manual',
    }),
  ],
  total: 3,
  limit: 50,
  offset: 0,
  by_type: { achievement: 1, repository_activity: 1, certification: 1 },
}

const RECORDS: CareerExperienceListRead = {
  items: [
    record(),
    record({
      id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      kind: 'education',
      title: 'BSc Computer Science',
      organisation: 'University of Lisbon',
      started_on: '2015-09-01',
      ended_on: '2019-07-01',
      description: null,
    }),
    record({
      id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
      kind: 'certification',
      title: 'AWS Certified Solutions Architect',
      organisation: 'Amazon Web Services',
      started_on: null,
      ended_on: '2024-03-01',
      url: null,
    }),
  ],
  total: 3,
  limit: 50,
  offset: 0,
}

const PROJECT: Project = {
  id: PROJECT_ID,
  owner_id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  name: 'NEXUS',
  description: null,
  status: 'active',
  priority: 'medium',
  start_date: null,
  target_date: null,
  completed_at: null,
  archived_at: null,
  created_at: '2018-01-01T00:00:00Z',
  updated_at: '2019-01-01T00:00:00Z',
}

const PROJECTS: Paginated<Project> = {
  items: [PROJECT],
  meta: { total: 1, limit: 100, offset: 0 },
}

const REPOSITORIES: RepositoryListRead = {
  items: [
    {
      id: REPO_ID,
      name: 'nexo',
      local_path: '/home/ada/code/nexo',
      description: null,
      primary_language: 'TypeScript',
      project_id: PROJECT_ID,
      is_active: true,
      current_branch: 'main',
      default_branch: 'main',
      branch_count: 3,
      commit_count: 1284,
      first_commit_at: '2018-02-04T09:14:00Z',
      latest_commit_at: '2019-01-29T14:05:00Z',
      working_tree_dirty: false,
      last_scanned_at: '2019-01-29T14:06:00Z',
      last_scan_status: 'ok',
      last_scan_error: null,
      created_at: '2018-02-01T08:00:00Z',
      updated_at: '2019-01-29T14:06:00Z',
    },
  ],
  total: 1,
  limit: 100,
  offset: 0,
}

/* -------------------------------------------------------------------- tests */

describe('the career page', () => {
  it('prints only what the person wrote, and says so on the profile itself', async () => {
    installBackend()
    const { container } = renderCareerPage()
    await waitForData()

    expect(screen.getByRole('heading', { level: 1, name: 'Career' })).toBeInTheDocument()
    const profileRegion = regionFor('Profile')
    expect(
      within(profileRegion).getByText(PROFILE.summary as string),
    ).toBeInTheDocument()
    expect(
      within(profileRegion).getByText(PROFILE.headline as string),
    ).toBeInTheDocument()
    expect(within(profileRegion).getByText('Everything above was entered by you.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit profile' })).toBeInTheDocument()

    // A link the user pasted is an anchor because it parses; nothing here is
    // generated, expanded or reworded.
    expect(screen.getByRole('link', { name: 'github.com/ada/nexo' })).toHaveAttribute(
      'href',
      'https://github.com/ada/nexo',
    )
    expectNoFabricatedNumbers(container)
  })

  it('counts the rows the profile holds, and invents no score to go with them', async () => {
    installBackend()
    const { container } = renderCareerPage()
    await waitForData()

    const tiles = cardFor('Career summary')
    expect(within(tiles).getByText(SUMMARY.summary)).toBeInTheDocument()
    expect(within(tileFor('Experience', tiles)).getByText(formatNumber(2))).toBeInTheDocument()
    expect(within(tileFor('Education', tiles)).getByText(formatNumber(1))).toBeInTheDocument()
    expect(within(tileFor('Certifications', tiles)).getByText(formatNumber(1))).toBeInTheDocument()
    expect(within(tileFor('Evidence', tiles)).getByText(formatNumber(4))).toBeInTheDocument()
    expect(
      within(tileFor('Linked evidence', tiles)).getByText(formatNumber(2)),
    ).toBeInTheDocument()

    // A reader who wants a judgement is not given one by NEXUS wearing a
    // number's clothes. The scan is for *claims*, not for the words "readiness"
    // or "employer match" — the page's own closing note uses those two to
    // disclaim exactly this, and a word-level scan would flag the disclaimer.
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/\b(?:strongest|weakest)\b/i)
    expect(text).not.toMatch(/\b(?:rated|scored|ranked)\s+(?:at|as)\b/i)
    expect(text).not.toMatch(/qualified to|certified by NEXUS|awarded by NEXUS/i)
    expectNoFabricatedNumbers(container)
  })

  it('renders a genuine zero beside a missing key as 0 and as a dash respectively', async () => {
    installBackend()
    const { container } = renderCareerPage()
    await waitForData()

    const breakdown = cardFor('How this evidence is made up')
    // A type the response *does* carry, counted at zero: a measurement.
    expect(evidenceCountFor('Linked to a skill')).toBe(formatNumber(0))
    // A type it does not carry: the server declining to state a count, which is
    // not the same fact and must not be rendered as one.
    expect(evidenceCountFor('Linked to a repository')).toBe(NO_VALUE)
    expect(evidenceCountFor('Manually added')).toBe(formatNumber(1))
    expect(
      breakdown.textContent,
    ).toContain(
      'A kind the response does not carry shows a dash rather than a zero, because a missing key ' +
        'is the server declining to state a count.',
    )
    expectNoFabricatedNumbers(container)
  })

  it('never renders a skill level as a bare number, on either tile or area row', async () => {
    installBackend()
    renderCareerPage()
    await waitForData()

    const skills = regionFor('Skill overview')
    expect(
      within(skills).getByText('3 of 5, self-assessed by you'),
    ).toBeInTheDocument()
    expect(
      within(skills).getByText('4 of 5, estimated by NEXUS from recorded activities'),
    ).toBeInTheDocument()
    expect(within(skills).getByText('Self-assessed')).toBeInTheDocument()
    expect(within(skills).getByText('NEXUS estimate')).toBeInTheDocument()

    for (const [name, level, source] of [
      ['Machine Learning', 3, 'user_defined'],
      ['SQL', 4, 'system_estimate'],
    ] as Array<[string, number, SkillLevelSource]>) {
      expectLevelClaim(cardFor(name), level, source)
    }
  })

  it('states development areas as a level with its source and a count of records', async () => {
    installBackend()
    const { container } = renderCareerPage()
    await waitForData()

    const panel = cardFor('Development areas')
    const row = within(panel).getByText('Machine Learning').closest('li') as HTMLElement
    // The product's own sentence: a distance and a count of records. The same
    // fact phrased as "you are weak at X" is the thing this panel refuses.
    expect(
      within(row).getByText(
        '3 of 5, self-assessed by you. 1 related learning activity in the last 30 days.',
      ),
    ).toBeInTheDocument()
    expect(within(row).getByText('Self-assessed')).toBeInTheDocument()
    expect(
      within(row).getByText(
        'Target 4 of 5, the level you set for this skill. The most recent activity was recorded yesterday.',
      ),
    ).toBeInTheDocument()

    // The panel names its own window, because its rule is only meaningful
    // against a stated range.
    expect(
      within(panel).getByText(
        '1 of 2 tracked skills listed have a target above the level recorded and fewer than 3 related activities in the last 30 days.',
      ),
    ).toBeInTheDocument()
    expect(panel.textContent).toContain(
      'A distance between a level and a target you chose, with the recorded activity behind it. ' +
        'It is not a ranking, a verdict or a gap in what you can do — it is arithmetic on two ' +
        'numbers you set.',
    )
    expectNoFabricatedNumbers(container)
  })

  it('never uses the brief’s forbidden vocabulary anywhere on the page', async () => {
    installBackend()
    const { container } = renderCareerPage()
    await waitForData()

    const text = container.textContent ?? ''
    expect(text).not.toMatch(/not good at|you are weak|you are bad at|weak at|poor at/i)
    expect(text).not.toMatch(/lack(?:s|ing) (?:skill|ability|talent)|incompetent|unskilled/i)
    expect(text).not.toMatch(/weakness|\bstrength\b|proficiency|behind schedule|falling behind/i)
    expect(text).not.toMatch(/\b(?:strongest|weakest)\b/i)
    expect(text).not.toMatch(/good fit for|suitable for|fit for the role/i)
    expectNoFabricatedNumbers(container)
  })

  it('tells a typed evidence row from a derived one, and names a certification as the user’s', async () => {
    installBackend()
    const { container } = renderCareerPage()
    await waitForData()

    const timeline = regionFor('Portfolio evidence')
    expect(
      within(timeline).getByText('3 rows shown across 3 kinds, newest first within each.'),
    ).toBeInTheDocument()

    // `Added by you` and `Recorded by NEXUS` are two different provenances and
    // are never collapsed into one "source" chip.
    expect(within(timeline).getAllByText('Added by you').length).toBeGreaterThanOrEqual(2)
    expect(within(timeline).getByText('Recorded by NEXUS')).toBeInTheDocument()
    expect(
      within(timeline).getByTitle(
        'Recorded by NEXUS from repository scans. You did not write this row; it was derived from a record you created.',
      ),
    ).toBeInTheDocument()

    // The vocabulary says a certification exists because the user supplied it,
    // before "things NEXUS recorded" misleads anyone.
    expect(
      within(timeline).getByText(
        'A certification **you supplied**, with the title and date you gave it. NEXUS issues no ' +
          'credential, verifies none and infers none — it stores what you entered.',
      ),
    ).toBeInTheDocument()

    // Repository evidence names code events and refuses the task count.
    expect(
      within(timeline).getByText(
        'Activity a repository scan recorded: commits, branches and changed lines. This names ' +
          'code events — it is not a count of tasks delivered, and it is not a statement about ' +
          'anyone’s time.',
      ),
    ).toBeInTheDocument()
    // The section says so in its own words rather than leaving the reader to
    // infer it: repository evidence is code events, not delivered projects.
    expect(
      screen.getByText(/it is not a count of projects delivered\./),
    ).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })

  it('shows dated records as records, and a current role as current', async () => {
    installBackend()
    const { container } = renderCareerPage()
    await waitForData()

    const records = regionFor('Education, experience and certifications')
    expect(
      within(records).getByText(
        '3 records shown. Everything here was entered by you, with the dates you gave.',
      ),
    ).toBeInTheDocument()

    const role = within(records).getByText('Machine Learning Engineer').closest('li') as HTMLElement
    expect(role.textContent).toContain('Analytical Engines')
    expect(role.textContent).toContain(
      'A role you listed. An end date you left empty means the role is current, which is a fact ' +
        'about the record rather than a missing value.',
    )

    // A certification listed with an end date and no start date says "Until …"
    // rather than inventing a start.
    const certification = within(records)
      .getAllByText('AWS Certified Solutions Architect')
      .at(-1)
      ?.closest('li') as HTMLElement
    expect(certification.textContent).toContain('Until ')
    expect(certification.textContent).not.toMatch(/undefined/)
    expectNoFabricatedNumbers(container)
  })

  it('draws silhouettes with no digits while the first read is in flight', () => {
    const never = (): Promise<Response> => new Promise(() => undefined)
    installBackend({
      summary: never,
      profile: never,
      experience: never,
      evidence: never,
      skills: never,
      gaps: never,
      projects: never,
      repositories: never,
    })
    renderCareerPage()

    expect(screen.getByRole('heading', { level: 1, name: 'Career' })).toBeInTheDocument()

    // Nothing in a busy region reads as a figure. A grey `0` on a tile that may
    // well read "Not enough data yet." is a number, and this surface never shows
    // a number it does not have.
    const busy = screen.getAllByRole('status')
    expect(busy.length).toBeGreaterThanOrEqual(5)
    for (const region of busy) {
      expect(region.textContent ?? '').not.toMatch(/\d/)
    }
    // Card headings are painted from the start, so they prove nothing; the
    // absence of a row does.
    expect(screen.queryByText('Machine Learning')).toBeNull()
    expect(screen.queryAllByText(SUMMARY.summary)).toHaveLength(0)
  })

  it('renders a cold-start profile as its own empty state, not as a failure', async () => {
    installBackend({
      // `GET /career/profile` answers `200` with a `null` body until the account
      // has been written — a state to render, and the target of the `PUT`.
      profile: () => json(null),
      summary: () => json(NO_PROFILE_SUMMARY),
      skills: () => json({ items: [], total: 0, limit: 50, offset: 0 } satisfies SkillListRead),
      gaps: () => json([] satisfies SkillGapRead[]),
      evidence: () =>
        json({ items: [], total: 0, limit: 50, offset: 0, by_type: {} } satisfies CareerEvidenceListRead),
      experience: () =>
        json({ items: [], total: 0, limit: 50, offset: 0 } satisfies CareerExperienceListRead),
    })
    const { container } = renderCareerPage()

    expect(await screen.findByText('No career profile yet')).toBeInTheDocument()
    expect(
      screen.getByText(
        'A profile is entirely your own: a target role, a domain, a one-line headline and any ' +
          'links you want on it. Nothing on it is written for you — NEXUS has no opinion about what ' +
          'you are aiming at and will not guess at one.',
      ),
    ).toBeInTheDocument()

    // A cold start is not an error: there is no alert and no retry button, and
    // the offer is a form rather than a failure notice.
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByRole('button', { name: 'Create profile' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Write your profile' })).toHaveLength(1)

    // Every other region explains its own emptiness instead of showing zeroes.
    expect(screen.getByText('No skills to show yet')).toBeInTheDocument()
    expect(screen.getByText('No evidence added yet')).toBeInTheDocument()
    expect(
      screen.getByText('No education, experience or certifications listed yet'),
    ).toBeInTheDocument()
    // Both the summary row and the development-areas panel explain their own
    // emptiness rather than rendering zeroes.
    expect(screen.getAllByText(NOT_ENOUGH_DATA_TITLE).length).toBeGreaterThanOrEqual(2)
    expectNoFabricatedNumbers(container)
  })

  it('routes a 404 on the profile to the same empty state, because ownership is the server’s', async () => {
    installBackend({
      // Every read is scoped to the caller's account, so "no profile" and "not
      // your profile" are the same 404 and the page cannot — and must not try to
      // — tell them apart.
      profile: () => envelope('not_found', 'No profile exists for this account.', 404, 'req-career-9'),
    })
    const { container } = renderCareerPage()

    expect(await screen.findByText('No career profile yet')).toBeInTheDocument()
    // A 404 is a cold start, not a failure, so no retry is offered and no
    // inventory of the account is claimed.
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByText(/That record does not exist/)).toBeNull()
    expect(screen.queryByRole('button', { name: /Retry/i })).toBeNull()

    // The rest of the page still renders: one missing profile is not the page.
    expect((await screen.findAllByText(SUMMARY.summary)).length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { name: 'Machine Learning', level: 3 })).toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })

  it('reports a 5xx evidence read with a retry that asks again and recovers', async () => {
    const user = userEvent.setup()
    let failing = true
    installBackend({
      evidence: () =>
        failing
          ? envelope('internal_error', 'The career service is unavailable.', 500, 'req-career-1')
          : json(EVIDENCE),
    })
    renderCareerPage()
    await screen.findAllByText(SUMMARY.summary)

    // The shared retry policy asks twice more with a backoff, so the error
    // surface cannot arrive inside the default 5 s budget.
    const alert = await screen.findByRole('alert', {}, { timeout: RETRY_SURFACE_TIMEOUT_MS })
    expect(alert).toHaveTextContent('the evidence timeline could not be loaded')
    expect(alert).toHaveTextContent(
      'The failure was recorded on the server. Retry, and quote the request ID below.',
    )
    expect(alert).toHaveTextContent('The career service is unavailable.')
    expect(alert).toHaveTextContent('req-career-1')

    // A failed read is distinguishable from an empty one: no row is invented and
    // no empty state claims the profile has no evidence.
    const timeline = regionFor('Portfolio evidence')
    expect(within(timeline).queryByText('No evidence added yet')).toBeNull()
    expect(within(timeline).queryByText('Published a note on ranking evaluation')).toBeNull()

    // The rest of the page is untouched by one failed region.
    expect(screen.getByRole('heading', { name: 'Machine Learning', level: 3 })).toBeInTheDocument()

    failing = false
    await user.click(screen.getByRole('button', { name: /Retry/i }))

    expect(
      await within(timeline).findByText('Published a note on ranking evaluation'),
    ).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  })

  it('surfaces a 422 on the profile form under the field the server named', async () => {
    const user = userEvent.setup()
    const calls = installBackend({
      saveProfile: () =>
        envelope('validation_error', 'That profile could not be stored.', 422, 'req-career-2', {
          errors: [
            { field: 'target_role', message: 'The target role must be 200 characters or fewer.' },
            { field: 'links', message: 'One of those entries is not a web address.' },
          ],
        }),
    })
    renderCareerPage()
    await waitForData()

    await user.click(screen.getByRole('button', { name: 'Edit profile' }))

    const dialog = await screen.findByRole('dialog')
    // The form is seeded from the profile it just fetched, and what it sends is
    // still only what the person typed.
    expect(within(dialog).getByLabelText(/^Target role/)).toHaveValue(
      'Machine Learning Engineer',
    )
    await user.clear(within(dialog).getByLabelText(/^Target role/))
    await user.type(within(dialog).getByLabelText(/^Target role/), 'Staff Engineer')
    await user.click(within(dialog).getByRole('button', { name: 'Save profile' }))

    const roleError = await within(dialog).findByText(
      'The target role must be 200 characters or fewer.',
    )
    expect(
      within(roleError.parentElement as HTMLElement).getByLabelText(/^Target role/),
    ).toHaveAttribute('aria-invalid', 'true')
    expect(within(dialog).getByText('One of those entries is not a web address.')).toBeInTheDocument()

    // Field-scoped errors are not repeated as a banner: the reader is told once,
    // next to the thing they must change.
    expect(within(dialog).queryByText('That profile could not be stored.')).toBeNull()

    const puts = getCalls(calls, '/career/profile').filter((call) => call.method === 'PUT')
    expect(puts).toHaveLength(1)
    expect(puts[0]?.body).toMatchObject({
      target_role: 'Staff Engineer',
      headline: PROFILE.headline,
      summary: PROFILE.summary,
      location: PROFILE.location,
      links: PROFILE.links,
    })
  })

  it('never sends a user id, and never sends a generated field on save', async () => {
    const user = userEvent.setup()
    const calls = installBackend()
    renderCareerPage()
    await waitForData()

    for (const call of calls) {
      expect(call.url).not.toMatch(/[?&]user_id=/)
      expect(call.url).not.toMatch(/[?&]owner_id=/)
    }

    await user.click(screen.getByRole('button', { name: 'Edit profile' }))
    const dialog = await screen.findByRole('dialog')
    await user.clear(within(dialog).getByLabelText(/^Summary/))
    await user.click(within(dialog).getByRole('button', { name: 'Save profile' }))

    await waitFor(() =>
      expect(getCalls(calls, '/career/profile').filter((call) => call.method === 'PUT')).toHaveLength(1),
    )
    const put = getCalls(calls, '/career/profile').find((call) => call.method === 'PUT')
    // Every field on the profile is the user's; a cleared one is an explicit
    // `null`, which is what a `PUT` means.
    expect(put?.body).toMatchObject({ summary: null })
    // Nothing here is generated, so nothing here can be a field NEXUS filled in.
    expect(put?.body).not.toHaveProperty('user_id')
    expect(put?.body).not.toHaveProperty('id')
    expect(put?.body).not.toHaveProperty('created_at')
    expect(put?.body).not.toHaveProperty('updated_at')
  })

  it('never prints NaN, Infinity or an undefined figure', async () => {
    installBackend()
    const { container } = renderCareerPage()
    await waitForData()
    await settle()

    expectNoFabricatedNumbers(container)
    // The page's own closing note states the rule the suite is pinning.
    expect(
      screen.getByText(
        /Reading this page: an evidence row is a fact about a record/,
      ),
    ).toBeInTheDocument()
  })
})