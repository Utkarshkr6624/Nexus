import type { ReactElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/tooltip'
import CareerPage from '@/pages/career-page'
import { formatNumber } from '@/features/analytics/format'
import { queryRetryPolicy } from '@/app/query-client'
import { useToastStore } from '@/stores/toast-store'
import type { ApiErrorEnvelope } from '@/types/api'
import type { Paginated } from '@/types/pagination'
import type {
  CareerEvidenceListRead,
  CareerEvidenceRead,
  CareerExperienceListRead,
  CareerExperienceRead,
  CareerProfileRead,
  CareerRecordKind,
  CareerSummaryRead,
  SkillGapRead,
  SkillListRead,
  SkillRead,
} from '@/types/learning'
import type { RepositoryListRead } from '@/types/developer'
import type { Project } from '@/types/work'

/**
 * The two career dialogs and the two pagers, asserted at the network boundary.
 *
 * The page is mounted for real — real router, real components, real hooks, real
 * portals — and only `fetch` is stubbed. The dialogs are therefore exercised
 * through the same code path a person uses: click the header button, type, press
 * submit.
 *
 * **The rule this file exists to pin is that a form invents nothing.** Every other
 * page-level test in `career-page.test.tsx` reads what NEXUS already has; these
 * tests are about the moment NEXUS is handed the chance to write something on the
 * user's behalf, and they say it must not. The load-bearing assertions are the
 * exact payload key sets: {@link payloadKeys} compares the *set of keys* on the
 * body, so a field that appeared with a prefilled value fails the test whether or
 * not the value it carried happened to look plausible.
 *
 * **Every date in these fixtures is in 2019**, a completed year, so nothing here
 * depends on the machine's clock — which matters most for the "no date is
 * defaulted to today" assertions: an empty box is checked directly rather than by
 * comparing against `new Date()`.
 *
 * **The query client is local, and the retry policy is the real one.** Mirrors of
 * `src/app/query-client.ts`: a 4xx is never retried, so a 422 or a 500 on a write
 * surfaces on the first response and these tests can assert it inside the default
 * `findBy` budget. Mutations carry `retry: false`, so "the server refused this" is
 * one POST here and never two.
 *
 * Recharts is mocked to supply a size only — this page draws no chart, and the
 * mock exists to keep the provider stack identical to the learning page's. Every
 * digit-free assertion below is scoped to the render container, because recharts
 * leaks a `<span id="recharts_measurement_span">0</span>` into `document.body` and
 * never removes it. The dialogs portal outside that container, so anything about
 * the forms is asserted through roles and values rather than through this guard.
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
 * How long a first read is given to land. Generous cannot mask a defect: every
 * assertion after it is the real check, and a region that never renders fails on
 * its own text rather than on this bound.
 */
const DATA_TIMEOUT_MS = 10_000

/** The page's own page sizes — the constants the pagers are built against. */
const RECORD_FETCH_LIMIT = 50
const EVIDENCE_FETCH_LIMIT = 50

const PROFILE_ID = '11111111-1111-4111-8111-111111111111'
const SKILL_ID = '22222222-2222-4222-8222-222222222222'
const PROJECT_ID = '33333333-3333-4333-8333-333333333333'
const REPO_ID = '44444444-4444-4444-8444-444444444444'
const EVIDENCE_ID = '55555555-5555-4555-8555-555555555555'
const RECORD_ID = '66666666-6666-4666-8666-666666666666'

/** Every date typed into a form below. 2019: a completed year, no clock. */
const TYPED_DATE = '2019-04-17'
const TYPED_START = '2019-01-07'
const TYPED_END = '2019-09-30'

/**
 * The toast stack is module state, not rendered by this page — it is asserted
 * through the store, so each test starts from an empty one rather than inheriting
 * the previous test's announcement.
 */
beforeEach(() => {
  useToastStore.getState().dismissAll()
})

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
  createEvidence?: Route
  createRecord?: Route
}

/** The query part of a request URL, which is relative in every call below. */
function searchOf(url: string): URLSearchParams {
  const index = url.indexOf('?')
  return new URLSearchParams(index < 0 ? '' : url.slice(index + 1))
}

/** A non-negative integer off the wire, or `0` — never `NaN` into an assertion. */
function readInt(url: string, key: string): number {
  const parsed = Number(searchOf(url).get(key))
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 0
}

/**
 * Stubs `fetch` with the routing table below, so one test can replace a single
 * endpoint — a paginated list, a refused write, a backend that is down — without
 * restating the rest. Writes are matched before the reads they would otherwise be
 * swallowed by, because `/career/evidence` is both.
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
  const createEvidence = overrides.createEvidence ?? (() => json(EVIDENCE.items[0] ?? null, 201))
  const createRecord = overrides.createRecord ?? (() => json(RECORDS.items[0] ?? null, 201))

  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      const rawBody = init?.body
      const body = typeof rawBody === 'string' ? (JSON.parse(rawBody) as unknown) : null
      calls.push({ url, method, body })

      if (method === 'POST' && url.includes('/career/evidence')) return createEvidence(url)
      if (method === 'POST' && url.includes('/career/experience')) return createRecord(url)
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
 * The keys a write actually put on the wire, sorted.
 *
 * This is the load-bearing helper of the file. A payload assertion written as
 * `toMatchObject` passes when an extra field rides along, and an extra field is
 * exactly the failure mode here: a description filled from the project list, a
 * `project_id` preselected, a `source` claiming a derivation NEXUS did not
 * perform. Comparing key *sets* cannot be satisfied by a superset.
 */
function payloadKeys(body: unknown): string[] {
  expect(typeof body).toBe('object')
  expect(body).not.toBeNull()
  return Object.keys(body as Record<string, unknown>).sort()
}

function getCalls(calls: Call[], needle: string): Call[] {
  return calls.filter((call) => call.url.includes(needle))
}

function getPosts(calls: Call[], needle: string): Call[] {
  return getCalls(calls, needle).filter((call) => call.method === 'POST')
}

/** Every URL a given endpoint was read at, in order, as `offset=N` or `offset=0`. */
function readOffsets(calls: Call[], needle: string): number[] {
  return getCalls(calls, needle)
    .filter((call) => call.method === 'GET')
    .map((call) => readInt(call.url, 'offset'))
}

/** The region a heading owns, so a label cannot be found twice on the page. */
function regionFor(heading: string): HTMLElement {
  const nodes = screen.getAllByRole('heading', { name: heading })
  for (const node of nodes) {
    const region = node.closest('section')
    if (region) return region as HTMLElement
  }
  const first = nodes[0]
  return (first?.closest('section') ?? first?.closest('div.rounded-lg')) as HTMLElement
}

/** The nav a pager labels for itself. */
function pagerFor(label: string): HTMLElement | null {
  return screen.queryByRole('navigation', { name: label })
}

/** What a toast was pushed with, as the store holds it. */
function toasts(): Array<{ title: string; description?: string; variant: string }> {
  return useToastStore.getState().toasts
}

/**
 * Waits until the reads the assertions depend on have landed.
 *
 * Every card on this page paints its heading before its rows exist, so a
 * `findByRole('heading')` is not a wait for data; these strings are. The link
 * pickers matter as much as the lists here — they are fed by the projects,
 * skills and repositories reads, and a dialog opened before those land would be
 * opened into an empty picker.
 */
async function waitForData(): Promise<void> {
  await screen.findAllByText(SUMMARY.summary, undefined, { timeout: DATA_TIMEOUT_MS })
  await screen.findByText(PROFILE.headline as string, undefined, { timeout: DATA_TIMEOUT_MS })
  // A skill name, which is the same on every backend in this file — the paginated
  // pools replace the two lists, and waiting on a row from either of those would
  // be waiting on a fixture rather than on the page.
  await screen.findAllByText('Machine Learning', undefined, { timeout: DATA_TIMEOUT_MS })
}

/** The three option texts a link picker currently offers, in order. */
function optionTexts(dialog: HTMLElement, label: RegExp): string[] {
  const picker = within(dialog).getByLabelText(label) as HTMLSelectElement
  return [...picker.options].map((option) => option.textContent ?? '')
}

/**
 * Waits until the project, skill and repository reads have filled the pickers.
 *
 * These three lists are fetched by the page for names only, and nothing on the
 * page renders them otherwise — so an evidence dialog opened too early would be
 * opened into an empty picker, which would quietly make "nothing is preselected"
 * true for the wrong reason.
 */
async function waitForLinkPickers(dialog: HTMLElement): Promise<void> {
  await waitFor(() => {
    expect(optionTexts(dialog, /^Linked project/)).toEqual([
      'Not linked to a project',
      PROJECT.name,
    ])
    expect(optionTexts(dialog, /^Linked skill/)).toEqual([
      'Not linked to a skill',
      'Machine Learning',
      'SQL',
    ])
    expect(optionTexts(dialog, /^Linked repository/)).toEqual([
      'Not linked to a repository',
      'nexo',
    ])
  })
}

/** Lets every in-flight fetch and its re-render settle before asserting. */
async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50))
  })
}

/**
 * Opens a dialog from a header button and waits for it.
 *
 * The dialog is portalled outside the render container, so every lookup is
 * global or scoped to the dialog — never to `container`.
 */
async function openDialog(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(screen.getByRole('button', { name }))
  const dialog = await screen.findByRole('dialog')
  await waitFor(() => expect(dialog).toBeInTheDocument())
  return dialog
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
    started_on: '2019-01-02',
    ended_on: null,
    description: 'Owned the retrieval stack.',
    url: null,
    created_at: '2019-01-02T09:00:00Z',
    updated_at: '2019-01-02T09:00:00Z',
    ...overrides,
  }
}

/** A row id that is stable, obviously synthetic, and never collides. */
function rowId(index: number): string {
  return `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`
}

/**
 * A pool big enough for three pages, mixed across the types the filters narrow
 * on, so `total` is a real filtered count rather than a number the fixture typed.
 */
const EVIDENCE_POOL: CareerEvidenceRead[] = Array.from({ length: 120 }, (_, index) =>
  evidence({
    id: rowId(index + 1),
    evidence_type: index % 4 === 0 ? 'certification' : 'achievement',
    title: `Evidence row ${index + 1}`,
    description: null,
    occurred_on: '2019-01-20',
  }),
)

const RECORD_POOL: CareerExperienceRead[] = Array.from({ length: 120 }, (_, index) =>
  record({
    id: rowId(index + 1),
    kind: index % 3 === 0 ? 'certification' : 'experience',
    title: `Dated record ${index + 1}`,
    organisation: null,
    description: null,
    url: null,
  }),
)

const SUMMARY: CareerSummaryRead = {
  has_profile: true,
  target_role: 'Machine Learning Engineer',
  target_domain: 'Machine Learning',
  experience_count: 2,
  education_count: 1,
  certification_count: 1,
  evidence_count: 4,
  by_type: { achievement: 1 },
  linked_evidence_count: 2,
  latest_evidence_on: '2019-01-20',
  has_data: true,
  summary: '4 pieces of evidence are on this profile, 2 of them linked to a project, skill or repository.',
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
const GAPS: SkillGapRead[] = [gap()]

/** Three rows against a `total` of three: one page, so no pager renders. */
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
    }),
  ],
  total: 3,
  limit: EVIDENCE_FETCH_LIMIT,
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
      ended_on: '2019-03-01',
      url: null,
    }),
  ],
  total: 3,
  limit: RECORD_FETCH_LIMIT,
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

/**
 * The evidence list, served from the pool: filtered the way the endpoint filters,
 * paged the way it pages, with `total` describing the **filtered** set rather
 * than the page in hand.
 */
function pagedEvidence(url: string, extra: readonly CareerEvidenceRead[] = []): Response {
  const type = searchOf(url).get('evidence_type')
  const all = type === null ? [...EVIDENCE_POOL, ...extra] : [...EVIDENCE_POOL, ...extra].filter((row) => row.evidence_type === type)
  const limit = readInt(url, 'limit') || EVIDENCE_FETCH_LIMIT
  const offset = readInt(url, 'offset')
  const items = all.slice(offset, offset + limit)
  const byType: Record<string, number> = {}
  for (const row of all) byType[row.evidence_type] = (byType[row.evidence_type] ?? 0) + 1
  return json({ items, total: all.length, limit, offset, by_type: byType } satisfies CareerEvidenceListRead)
}

/** The record list, served from the pool with the same filtering and paging. */
function pagedRecords(url: string, extra: readonly CareerExperienceRead[] = []): Response {
  const kind = searchOf(url).get('kind') as CareerRecordKind | null
  const all =
    kind === null
      ? [...RECORD_POOL, ...extra]
      : [...RECORD_POOL, ...extra].filter((row) => row.kind === kind)
  const limit = readInt(url, 'limit') || RECORD_FETCH_LIMIT
  const offset = readInt(url, 'offset')
  return json({
    items: all.slice(offset, offset + limit),
    total: all.length,
    limit,
    offset,
  } satisfies CareerExperienceListRead)
}

/** The small three-row evidence list, plus whatever a create returned. */
function evidenceList(extra: readonly CareerEvidenceRead[] = []): Response {
  const items = [...EVIDENCE.items, ...extra]
  const byType: Record<string, number> = {}
  for (const row of items) byType[row.evidence_type] = (byType[row.evidence_type] ?? 0) + 1
  return json({
    items,
    total: items.length,
    limit: EVIDENCE_FETCH_LIMIT,
    offset: 0,
    by_type: byType,
  } satisfies CareerEvidenceListRead)
}

/** The small three-row record list, plus whatever a create returned. */
function recordList(extra: readonly CareerExperienceRead[] = []): Response {
  const items = [...RECORDS.items, ...extra]
  return json({
    items,
    total: items.length,
    limit: RECORD_FETCH_LIMIT,
    offset: 0,
  } satisfies CareerExperienceListRead)
}

/** Installs a backend whose two lists are the paginated pools. */
function installPagedBackend(overrides: Backend = {}): Call[] {
  return installBackend({
    evidence: (url) => pagedEvidence(url),
    experience: (url) => pagedRecords(url),
    ...overrides,
  })
}

/* -------------------------------------------------------------------- tests */

describe('the add-evidence dialog', () => {
  it('opens with nothing filled in, and sends nothing at all until the person types', async () => {
    const user = userEvent.setup()
    const calls = installBackend()
    const { container } = renderCareerPage()
    await waitForData()

    await user.click(screen.getByRole('button', { name: 'Add evidence' }))
    const dialog = await screen.findByRole('dialog')
    await waitForLinkPickers(dialog)
    expect(within(dialog).getByText('Add a piece of evidence')).toBeInTheDocument()

    // Every control is empty. An empty date box is the assertion that no date
    // was defaulted to today — a fixture date in 2019 could not distinguish the
    // two, and the box itself is unambiguous.
    expect(within(dialog).getByLabelText(/^What it was/)).toHaveValue('')
    expect(within(dialog).getByLabelText(/^Date it happened/)).toHaveValue('')
    expect(within(dialog).getByLabelText(/^Description/)).toHaveValue('')

    // The three link pickers offer records that already exist and preselect
    // none of them: a linked project is a claim about where something happened,
    // and choosing one for the reader would be making it.
    expect(within(dialog).getByLabelText(/^Linked project/)).toHaveValue('')
    expect(within(dialog).getByLabelText(/^Linked skill/)).toHaveValue('')
    expect(within(dialog).getByLabelText(/^Linked repository/)).toHaveValue('')

    // The pickers name records and nothing else — no status, no date, no
    // suggestion attached to an option.
    expect(optionTexts(dialog, /^Linked project/)).toEqual([
      'Not linked to a project',
      PROJECT.name,
    ])

    // Nothing elsewhere on this page is carried in: the profile's target role,
    // its headline and the project's own name are all things a form could have
    // assembled a title out of, and none of them reached the text box.
    const written = within(dialog).getByLabelText(/^What it was/) as HTMLInputElement
    const note = within(dialog).getByLabelText(/^Description/) as HTMLTextAreaElement
    expect([written.value, note.value]).toEqual(['', ''])
    for (const borrowed of [PROJECT.name, PROFILE.target_role, PROFILE.headline, SUMMARY.target_role]) {
      expect(written.value).not.toContain(borrowed ?? '')
    }

    // And opening the dialog is not a write: no POST, on any endpoint.
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0)
    expectNoFabricatedNumbers(container)
  })

  it('sends only the fields that were typed, and omits every blank rather than emptying it', async () => {
    const user = userEvent.setup()
    const calls = installBackend({
      createEvidence: () => json(evidence({ title: 'Ran the evaluation harness' }), 201),
    })
    renderCareerPage()
    await waitForData()

    const dialog = await openDialog(user, 'Add evidence')
    await user.type(within(dialog).getByLabelText(/^What it was/), 'Ran the evaluation harness')
    fireEvent.change(within(dialog).getByLabelText(/^Date it happened/), {
      target: { value: TYPED_DATE },
    })
    await user.click(within(dialog).getByRole('button', { name: /Save evidence/ }))

    await waitFor(() => expect(getPosts(calls, '/career/evidence')).toHaveLength(1))
    const post = getPosts(calls, '/career/evidence')[0]

    // The whole payload, as a key set. A `description`, a `project_id`, a
    // `skill_id`, a `repository_id` or a `source` appearing here — none of which
    // was typed — fails this line.
    expect(payloadKeys(post?.body)).toEqual(['evidence_type', 'occurred_on', 'title'])
    expect(post?.body).toEqual({
      evidence_type: 'achievement',
      title: 'Ran the evaluation harness',
      occurred_on: TYPED_DATE,
    })
    // Provenance is the server's to assign: `manual` is the only honest source
    // for anything typed into this form, and it is never sent by hand.
    expect(post?.body).not.toHaveProperty('source')
    expect(post?.body).not.toHaveProperty('user_id')
    expect(post?.body).not.toHaveProperty('id')
  })

  it('links only what was chosen, and leaves the other two out of the payload', async () => {
    const user = userEvent.setup()
    const calls = installBackend({
      createEvidence: () => json(evidence({ title: 'Shipped the ranking note' }), 201),
    })
    renderCareerPage()
    await waitForData()

    const dialog = await openDialog(user, 'Add evidence')
    await waitForLinkPickers(dialog)
    await user.type(within(dialog).getByLabelText(/^What it was/), 'Shipped the ranking note')
    fireEvent.change(within(dialog).getByLabelText(/^Date it happened/), {
      target: { value: TYPED_DATE },
    })
    await user.selectOptions(within(dialog).getByLabelText(/^Linked repository/), REPO_ID)

    await user.click(within(dialog).getByRole('button', { name: /Save evidence/ }))

    await waitFor(() => expect(getPosts(calls, '/career/evidence')).toHaveLength(1))
    const post = getPosts(calls, '/career/evidence')[0]
    expect(payloadKeys(post?.body)).toEqual([
      'evidence_type',
      'occurred_on',
      'repository_id',
      'title',
    ])
    expect(post?.body).toMatchObject({ repository_id: REPO_ID })
    // The two pickers left alone stay off the wire entirely: no `null` for a
    // link the person never made, and no `''` either.
    expect(post?.body).not.toHaveProperty('project_id')
    expect(post?.body).not.toHaveProperty('skill_id')
  })

  it('answers a blank date under the date box, with the submit button reachable', async () => {
    const user = userEvent.setup()
    const calls = installBackend()
    const { container } = renderCareerPage()
    await waitForData()

    const dialog = await openDialog(user, 'Add evidence')
    await user.type(within(dialog).getByLabelText(/^What it was/), 'Ran the evaluation harness')

    // The button is live with a blank date. A disabled control cannot be pressed
    // to be told, so the reason would sit behind a control nobody can reach.
    const save = within(dialog).getByRole('button', { name: /Save evidence/ })
    expect(save).toBeEnabled()
    await user.click(save)

    const dateInput = within(dialog).getByLabelText(/^Date it happened/)
    const refusal = await within(dialog).findByText(
      'Evidence must carry the date it happened on. A row with no date cannot be placed in order, ' +
        'so the server will not store one.',
    )
    expect(dateInput).toHaveAttribute('aria-invalid', 'true')
    // The message sits under the control that caused it, inside its own field.
    expect(refusal.closest('div.app-form-field')).toContainElement(dateInput)

    // One alert, and it is the field's — not a banner at the top of the dialog.
    const alerts = within(dialog).getAllByRole('alert')
    expect(alerts).toHaveLength(1)
    expect(alerts[0]).toHaveTextContent('A row with no date cannot be placed in order')

    // Nothing was written: an undated row is refused before it reaches the wire,
    // so there is no payload to have invented a date in.
    expect(getPosts(calls, '/career/evidence')).toHaveLength(0)
    expectNoFabricatedNumbers(container)
  })

  it('puts a 422 from the server under the field it named, and says it once', async () => {
    const user = userEvent.setup()
    const calls = installBackend({
      createEvidence: () =>
        envelope('validation_error', 'That evidence could not be stored.', 422, 'req-career-11', {
          errors: [
            { field: 'title', message: 'A row with that title already exists on this profile.' },
            { field: 'repository_id', message: 'That repository is not registered on this account.' },
          ],
        }),
    })
    renderCareerPage()
    await waitForData()

    const dialog = await openDialog(user, 'Add evidence')
    await user.type(within(dialog).getByLabelText(/^What it was/), 'Ran the evaluation harness')
    fireEvent.change(within(dialog).getByLabelText(/^Date it happened/), {
      target: { value: TYPED_DATE },
    })
    await user.click(within(dialog).getByRole('button', { name: /Save evidence/ }))

    const titleRefusal = await within(dialog).findByText(
      'A row with that title already exists on this profile.',
    )
    const titleInput = within(dialog).getByLabelText(/^What it was/)
    expect(titleInput).toHaveAttribute('aria-invalid', 'true')
    expect(titleRefusal.closest('div.app-form-field')).toContainElement(titleInput)

    // Both field-scoped refusals land on the field the server named.
    const repositoryRefusal = within(dialog).getByText(
      'That repository is not registered on this account.',
    )
    expect(
      repositoryRefusal.closest('div.app-form-field'),
    ).toContainElement(within(dialog).getByLabelText(/^Linked repository/))

    // The server's own message, once. A banner restating "that evidence could
    // not be stored" would leave the reader guessing which of seven inputs was
    // refused, so it is not rendered alongside the two messages that say so.
    expect(within(dialog).queryByText('That evidence could not be stored.')).toBeNull()
    expect(within(dialog).getAllByRole('alert')).toHaveLength(2)

    // A 4xx is never retried, and a mutation never is: one refusal, one attempt.
    expect(getPosts(calls, '/career/evidence')).toHaveLength(1)
  })

  it('refetches on a successful create and shows the row the server returned', async () => {
    const user = userEvent.setup()
    const created = evidence({
      id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
      title: 'Wrote the incident review',
      description: null,
      occurred_on: TYPED_DATE,
    })
    const state = { created: null as CareerEvidenceRead | null }
    const calls = installBackend({
      evidence: () => evidenceList(state.created === null ? [] : [state.created]),
      createEvidence: () => {
        state.created = created
        return json(created, 201)
      },
    })
    const { container } = renderCareerPage()
    await waitForData()

    const beforeReads = getCalls(calls, '/career/evidence').length

    const dialog = await openDialog(user, 'Add evidence')
    await user.type(within(dialog).getByLabelText(/^What it was/), 'Wrote the incident review')
    fireEvent.change(within(dialog).getByLabelText(/^Date it happened/), {
      target: { value: TYPED_DATE },
    })
    await user.click(within(dialog).getByRole('button', { name: /Save evidence/ }))

    // The dialog closes on success and the list is read again — the new row is
    // the server's row, fetched back, not a row the client spliced into state.
    await waitFor(() =>
      expect(getCalls(calls, '/career/evidence').length).toBeGreaterThan(beforeReads),
    )
    const timeline = regionFor('Portfolio evidence')
    expect(await within(timeline).findByText('Wrote the incident review')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()

    // It was added by the person, because that is what this form is.
    const row = screen.getByText('Wrote the incident review').closest('li') as HTMLElement
    expect(within(row).getByText('Added by you')).toBeInTheDocument()

    expect(toasts().map((entry) => entry.title)).toContain('Evidence added')
    expectNoFabricatedNumbers(container)
  })

  it('reports a refused create with a toast, and fabricates no row', async () => {
    const user = userEvent.setup()
    const calls = installBackend({
      createEvidence: () =>
        envelope('internal_error', 'The career service is unavailable.', 500, 'req-career-12'),
    })
    const { container } = renderCareerPage()
    await waitForData()

    const dialog = await openDialog(user, 'Add evidence')
    await user.type(within(dialog).getByLabelText(/^What it was/), 'Wrote the incident review')
    fireEvent.change(within(dialog).getByLabelText(/^Date it happened/), {
      target: { value: TYPED_DATE },
    })
    await user.click(within(dialog).getByRole('button', { name: /Save evidence/ }))

    // A 5xx on a write is not retried — a mutation carries `retry: false` — so
    // the refusal lands on the first response.
    await waitFor(() => expect(getPosts(calls, '/career/evidence')).toHaveLength(1))

    const announced = toasts().find((entry) => entry.title === 'Could not add that evidence')
    expect(announced).toBeDefined()
    expect(announced?.description).toBe('The career service is unavailable.')
    expect(announced?.variant).toBe('destructive')

    // The row was not written, so it is not on screen. A list that showed the
    // typed title here would be claiming a record the server refused.
    const timeline = regionFor('Portfolio evidence')
    expect(within(timeline).queryByText('Wrote the incident review')).toBeNull()
    expect(within(timeline).getByText('Published a note on ranking evaluation')).toBeInTheDocument()

    // The dialog stays open with what was typed, so nothing has to be retyped.
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/^What it was/)).toHaveValue('Wrote the incident review')
    await settle()
    expectNoFabricatedNumbers(container)
  })
})

describe('the add-record dialog', () => {
  it('opens with no employer, no dates and no title, having sent nothing', async () => {
    const user = userEvent.setup()
    const calls = installBackend()
    renderCareerPage()
    await waitForData()

    const dialog = await openDialog(user, 'Add a record')
    expect(within(dialog).getByText('Add a dated record')).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/^Title/)).toHaveValue('')
    expect(within(dialog).getByLabelText(/^Organisation/)).toHaveValue('')
    expect(within(dialog).getByLabelText(/^Start date/)).toHaveValue('')
    expect(within(dialog).getByLabelText(/^End date/)).toHaveValue('')
    expect(within(dialog).getByLabelText(/^Description/)).toHaveValue('')
    expect(within(dialog).getByLabelText(/^Link/)).toHaveValue('')
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0)
  })

  it('omits a blank end date rather than sending an empty one or an explicit null', async () => {
    const user = userEvent.setup()
    const calls = installBackend({
      createRecord: () => json(record({ title: 'Data Analyst', ended_on: null }), 201),
    })
    renderCareerPage()
    await waitForData()

    const dialog = await openDialog(user, 'Add a record')
    await user.type(within(dialog).getByLabelText(/^Title/), 'Data Analyst')
    fireEvent.change(within(dialog).getByLabelText(/^Start date/), {
      target: { value: TYPED_START },
    })
    await user.click(within(dialog).getByRole('button', { name: /Save record/ }))

    await waitFor(() => expect(getPosts(calls, '/career/experience')).toHaveLength(1))
    const post = getPosts(calls, '/career/experience')[0]

    // `ended_on` is absent, not `''` and not `null`: the backend reads an absent
    // end date as "this record is current", which is a fact about the record
    // rather than a field left unfilled. Sending `null` would ask it to store a
    // date of nothing; sending `''` would ask it to store an empty string.
    expect(payloadKeys(post?.body)).toEqual(['kind', 'started_on', 'title'])
    expect(post?.body).not.toHaveProperty('ended_on')
    expect(post?.body).not.toHaveProperty('organisation')
    expect(post?.body).not.toHaveProperty('description')
    expect(post?.body).not.toHaveProperty('url')
    expect(post?.body).toMatchObject({
      kind: 'experience',
      title: 'Data Analyst',
      started_on: TYPED_START,
    })
  })

  it('sends a title, an issuer and dates the person typed, and nothing else', async () => {
    const user = userEvent.setup()
    const calls = installBackend({
      createRecord: () => json(record({ title: 'AWS Certified Solutions Architect' }), 201),
    })
    renderCareerPage()
    await waitForData()

    const dialog = await openDialog(user, 'Add a record')
    await user.selectOptions(within(dialog).getByLabelText(/^Kind of record/), 'certification')
    await user.type(
      within(dialog).getByLabelText(/^Title/),
      'AWS Certified Solutions Architect',
    )
    await user.type(within(dialog).getByLabelText(/^Organisation/), 'Amazon Web Services')
    fireEvent.change(within(dialog).getByLabelText(/^Start date/), {
      target: { value: TYPED_START },
    })
    fireEvent.change(within(dialog).getByLabelText(/^End date/), {
      target: { value: TYPED_END },
    })
    await user.type(
      within(dialog).getByLabelText(/^Description/),
      'Renewed by sitting the exam again.',
    )
    await user.type(within(dialog).getByLabelText(/^Link/), 'https://example.test/cert')
    await user.click(within(dialog).getByRole('button', { name: /Save record/ }))

    await waitFor(() => expect(getPosts(calls, '/career/experience')).toHaveLength(1))
    const post = getPosts(calls, '/career/experience')[0]

    expect(payloadKeys(post?.body)).toEqual([
      'description',
      'ended_on',
      'kind',
      'organisation',
      'started_on',
      'title',
      'url',
    ])
    expect(post?.body).toEqual({
      kind: 'certification',
      title: 'AWS Certified Solutions Architect',
      organisation: 'Amazon Web Services',
      started_on: TYPED_START,
      ended_on: TYPED_END,
      description: 'Renewed by sitting the exam again.',
      url: 'https://example.test/cert',
    })
    // No user id, no id, no timestamps: ownership is the server's alone and the
    // instants are stamped there.
    for (const field of ['user_id', 'owner_id', 'id', 'created_at', 'updated_at', 'source']) {
      expect(post?.body).not.toHaveProperty(field)
    }
  })

  it('refuses an end date before the start date under the end-date box, writing nothing', async () => {
    const user = userEvent.setup()
    const calls = installBackend()
    renderCareerPage()
    await waitForData()

    const dialog = await openDialog(user, 'Add a record')
    await user.type(within(dialog).getByLabelText(/^Title/), 'Data Analyst')
    fireEvent.change(within(dialog).getByLabelText(/^Start date/), {
      target: { value: TYPED_START },
    })
    fireEvent.change(within(dialog).getByLabelText(/^End date/), {
      target: { value: '2019-01-03' },
    })
    await user.click(within(dialog).getByRole('button', { name: /Save record/ }))

    const endInput = within(dialog).getByLabelText(/^End date/)
    const refusal = await within(dialog).findByText(
      'An end date before the start date is refused. Leave the end date empty if the record is ' +
        'still current.',
    )
    expect(endInput).toHaveAttribute('aria-invalid', 'true')
    expect(refusal.closest('div.app-form-field')).toContainElement(endInput)
    expect(getPosts(calls, '/career/experience')).toHaveLength(0)
  })

  it('adds the record it created and reports it in a toast', async () => {
    const user = userEvent.setup()
    const created = record({
      id: 'ffffffff-ffff-4fff-8fff-ffffffffffff',
      title: 'Data Analyst',
      organisation: null,
      started_on: TYPED_START,
      ended_on: null,
      description: null,
    })
    const state = { created: null as CareerExperienceRead | null }
    const calls = installBackend({
      experience: () => recordList(state.created === null ? [] : [state.created]),
      createRecord: () => {
        state.created = created
        return json(created, 201)
      },
    })
    const { container } = renderCareerPage()
    await waitForData()

    const dialog = await openDialog(user, 'Add a record')
    await user.type(within(dialog).getByLabelText(/^Title/), 'Data Analyst')
    fireEvent.change(within(dialog).getByLabelText(/^Start date/), {
      target: { value: TYPED_START },
    })
    await user.click(within(dialog).getByRole('button', { name: /Save record/ }))

    const records = regionFor('Education, experience and certifications')
    expect(await within(records).findByText('Data Analyst')).toBeInTheDocument()
    expect(getPosts(calls, '/career/experience')).toHaveLength(1)

    // A record with no end date reads as current, because that is what an absent
    // end date means — not as a row with a missing value.
    const row = within(records).getByText('Data Analyst').closest('li') as HTMLElement
    expect(row.textContent).toContain('current')

    expect(toasts().map((entry) => entry.title)).toContain('Record added')
    expectNoFabricatedNumbers(container)
  })
})

describe('the career pagers', () => {
  it('renders nothing at all over a single page', async () => {
    installBackend()
    const { container } = renderCareerPage()
    await waitForData()

    // A pager over one page is two disabled buttons and a count the list already
    // states; it says nothing the reader did not already have.
    expect(pagerFor('Portfolio evidence pages')).toBeNull()
    expect(pagerFor('Dated record pages')).toBeNull()
    expect(screen.queryByRole('navigation', { name: /pages/i })).toBeNull()
    expectNoFabricatedNumbers(container)
  })

  it('quotes the backend’s own total rather than the page in hand', async () => {
    installPagedBackend()
    renderCareerPage()
    await waitForData()

    const evidencePager = await waitFor(() => {
      const pager = pagerFor('Portfolio evidence pages')
      expect(pager).not.toBeNull()
      return pager as HTMLElement
    })
    const recordPager = pagerFor('Dated record pages') as HTMLElement

    // `total` is the backend's figure across every matching row — 120 rows, of
    // which the page in hand holds 50. Quoting 50 here would be the one number
    // on the page that is a claim rather than a measurement.
    expect(evidencePager.textContent).toContain(
      `Page 1 of 3 · ${formatNumber(120)} matching evidence rows`,
    )
    expect(recordPager.textContent).toContain(`Page 1 of 3 · ${formatNumber(120)} matching records`)
  })

  it('disables at both boundaries and asks the endpoint for the offset it needs', async () => {
    const user = userEvent.setup()
    const calls = installPagedBackend()
    const { router } = renderCareerPage()
    await waitForData()

    const pager = (await waitFor(() => {
      const found = pagerFor('Dated record pages')
      expect(found).not.toBeNull()
      return found as HTMLElement
    })) as HTMLElement

    const previous = within(pager).getByRole('button', { name: 'Previous' })
    const next = within(pager).getByRole('button', { name: 'Next' })

    // First page: nothing before it, and something after it.
    expect(previous).toBeDisabled()
    expect(next).toBeEnabled()

    await user.click(next)
    await waitFor(() => expect(router.state.location.search).toContain('experience_offset=50'))
    expect(readOffsets(calls, '/career/experience')).toContain(RECORD_FETCH_LIMIT)
    // The offset is a URL, so "the second page of records" is a link somebody can
    // be sent and the back button steps back through pages rather than out of
    // the page.
    expect(await screen.findByText('Dated record 51')).toBeInTheDocument()
    expect(within(pager).getByText(`Page 2 of 3 · ${formatNumber(120)} matching records`))
      .toBeInTheDocument()
    expect(previous).toBeEnabled()
    expect(next).toBeEnabled()

    await user.click(next)
    await waitFor(() => expect(router.state.location.search).toContain('experience_offset=100'))
    expect(readOffsets(calls, '/career/experience')).toContain(RECORD_FETCH_LIMIT * 2)
    expect(within(pager).getByText(`Page 3 of 3 · ${formatNumber(120)} matching records`))
      .toBeInTheDocument()
    // Last page: nothing after it.
    expect(next).toBeDisabled()
    expect(previous).toBeEnabled()

    await user.click(previous)
    await waitFor(() => expect(router.state.location.search).toContain('experience_offset=50'))
    expect(within(pager).getByText(`Page 2 of 3 · ${formatNumber(120)} matching records`))
      .toBeInTheDocument()
  })

  it('writes the first page as the absent parameter, not as ?offset=0', async () => {
    const user = userEvent.setup()
    const calls = installPagedBackend()
    const { router } = renderCareerPage('/career?experience_offset=50')
    await waitForData()

    const pager = (await waitFor(() => {
      const found = pagerFor('Dated record pages')
      expect(found).not.toBeNull()
      return found as HTMLElement
    })) as HTMLElement
    await user.click(within(pager).getByRole('button', { name: 'Previous' }))

    // `?experience_offset=0` would say the same as no parameter at all and make
    // two links to one view differ, so the first page is the absent one.
    await waitFor(() => expect(router.state.location.search).not.toContain('experience_offset'))
    expect(router.state.location.search).toBe('')
    expect(readOffsets(calls, '/career/experience')).toContain(0)
    expect(within(pager).getByText(`Page 1 of 3 · ${formatNumber(120)} matching records`))
      .toBeInTheDocument()
  })

  it('clears its own offset when its filter changes, so a narrowed set cannot strand the reader', async () => {
    const user = userEvent.setup()
    const calls = installPagedBackend()
    const { router } = renderCareerPage('/career?evidence_offset=50&experience_offset=100')
    await waitForData()

    // Both lists start on a page past the first.
    expect(await screen.findByText('Evidence row 51')).toBeInTheDocument()
    expect(await screen.findByText('Dated record 101')).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Kind of evidence'), 'certification')
    await user.selectOptions(screen.getByLabelText('Record kind'), 'certification')

    // The evidence offset goes first: the kind of evidence narrows 120 rows to
    // 30, which is a single page, so page two no longer exists.
    await waitFor(() => expect(router.state.location.search).not.toContain('evidence_offset'))
    expect(router.state.location.search).toContain('evidence=certification')
    expect(await screen.findByText('Evidence row 1')).toBeInTheDocument()
    expect(screen.queryByText('Evidence row 51')).toBeNull()

    await waitFor(() => expect(router.state.location.search).not.toContain('experience_offset'))
    expect(router.state.location.search).toContain('kind=certification')
    expect(await screen.findByText('Dated record 1')).toBeInTheDocument()
    expect(screen.queryByText('Dated record 101')).toBeNull()

    // A narrowed set that fits on one page renders no pager at all.
    expect(pagerFor('Portfolio evidence pages')).toBeNull()
    expect(pagerFor('Dated record pages')).toBeNull()

    // And the reads that followed the filter carried no offset, which is the
    // only request a single-page set can answer.
    const evidenceReads = getCalls(calls, '/career/evidence').filter((call) => call.method === 'GET')
    const recordReads = getCalls(calls, '/career/experience').filter((call) => call.method === 'GET')
    expect(evidenceReads.at(-1)?.url).not.toContain('offset=50')
    expect(recordReads.at(-1)?.url).not.toContain('offset=100')
  })

  it('never claims the whole set on a page that is not the first', async () => {
    const user = userEvent.setup()
    installPagedBackend()
    const { container } = renderCareerPage()
    await waitForData()

    const pager = (await waitFor(() => {
      const found = pagerFor('Portfolio evidence pages')
      expect(found).not.toBeNull()
      return found as HTMLElement
    })) as HTMLElement
    await user.click(within(pager).getByRole('button', { name: 'Next' }))

    const timeline = regionFor('Portfolio evidence')
    await within(timeline).findByText('Evidence row 51')

    // "Everything on file is shown here" would be false on page two: the row in
    // hand is a position in a list of 120, and the pager two lines below says so.
    expect(timeline.textContent).not.toContain('Everything on file is shown here')
    expect(within(pager).getByText(`Page 2 of 3 · ${formatNumber(120)} matching evidence rows`))
      .toBeInTheDocument()
    expectNoFabricatedNumbers(container)
  })

  it('prints no NaN, Infinity or undefined figure on either paged list', async () => {
    installPagedBackend()
    const { container } = renderCareerPage('/career?evidence_offset=100&experience_offset=100')
    await waitForData()
    await screen.findByText('Evidence row 101')
    await screen.findByText('Dated record 101')
    await settle()

    expectNoFabricatedNumbers(container)
  })
})
