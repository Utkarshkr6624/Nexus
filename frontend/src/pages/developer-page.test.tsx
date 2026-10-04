import type { ReactElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/tooltip'
import DeveloperPage from '@/pages/developer-page'
import { NO_VALUE, formatNumber } from '@/features/analytics/format'
import { queryRetryPolicy } from '@/app/query-client'
import type { ApiErrorEnvelope } from '@/types/api'
import type { Paginated } from '@/types/pagination'
import {
  NOT_ENOUGH_DATA,
  type CommitListRead,
  type CommitRead,
  type DeveloperActivityRead,
  type DeveloperMetricRead,
  type DeveloperSummaryRead,
  type RepositoryListRead,
  type RepositoryRead,
} from '@/types/developer'
import type { Project } from '@/types/work'

/**
 * The Developer Intelligence dashboard, asserted at the network boundary.
 *
 * The page is mounted for real — real router, real components, real hooks — and
 * only `fetch` is stubbed. Every figure on screen is therefore a body this file
 * wrote, so the numbers can be checked by hand: 18 commits inside a 30-day
 * window, 1,284 across the whole history, 12 days carrying at least one commit
 * and 842 changed lines.
 *
 * **The query client is local, and that is the point.** `AppProviders` mounts the
 * shared singleton and registers `onSessionChange(() => queryClient.clear())`
 * (`src/app/auth-bootstrap.tsx:13`). In jsdom that clear lands mid-test and leaves
 * every component sitting at `pending` forever, which is why this suite builds a
 * fresh client per render. The defaults below are the ones in
 * `src/app/query-client.ts`, carried over rather than relaxed: the retry policy in
 * particular is what makes the error surfaces arrive after a few seconds rather
 * than on the first response, and the two 5xx cases below wait with an explicit
 * `{ timeout: 20_000 }` because of it.
 *
 * Recharts is given a size and only a size — jsdom has no layout engine, so
 * `ResponsiveContainer` measures 0×0 and every chart would come back as an empty
 * box. The mock replaces exactly that measurement and tags what it wraps in
 * `data-chart-surface`, and no assertion below reaches into a recharts
 * internal. The charts sit behind the real `LazyChart` boundary, which paints
 * the *same* card title as the chart it replaces, so {@link resolveCharts}
 * waits on the fallback's skeleton leaving the document before any `findBy*`.
 *
 * The dates are fixed in 2019 — a completed year — because the formatters omit
 * the year for dates in the current one, and nothing here asserts on a relative
 * age, which would depend on the machine's clock.
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

/** The retry policy genuinely backs off; these are not generous for comfort. */
const RETRY_SURFACE_TIMEOUT_MS = 20_000

const REPO_ID = '11111111-1111-4111-8111-111111111111'
const REPO_TWO_ID = '22222222-2222-4222-8222-222222222222'
const PROJECT_ID = '33333333-3333-4333-8333-333333333333'
const COMMIT_ID = '44444444-4444-4444-8444-444444444444'
const SCAN_FAILURE = 'The directory could not be read because it is not a git work tree.'

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
  activity?: Route
  commits?: Route
  repositories?: Route
  create?: Route
  projects?: Route
}

/**
 * Stubs `fetch` with the routing table below, so one test can replace a single
 * endpoint — the failing list, the failing series, the refused registration —
 * without restating the rest. Literal sub-paths are matched before
 * `/developer/repositories`, which would otherwise swallow them.
 */
function installBackend(overrides: Backend = {}): Call[] {
  const summary = overrides.summary ?? (() => json(SUMMARY))
  const metrics = overrides.metrics ?? (() => json(METRICS))
  const activity = overrides.activity ?? (() => json(ACTIVITY))
  const commits = overrides.commits ?? (() => json(COMMITS))
  const projects = overrides.projects ?? (() => json(PROJECTS))
  const create =
    overrides.create ??
    ((url) =>
      json(
        repository({ local_path: new URL(url, 'http://test').searchParams.get('local_path') ?? '' }),
      ))

  const repositories =
    overrides.repositories ??
    ((url: string) => {
      const params = new URL(url, 'http://test').searchParams
      const isActive = params.get('is_active')
      const items = isActive === null ? REPOSITORIES : REPOSITORIES.filter((row) => row.is_active === (isActive === 'true'))
      return json({ items, total: items.length, limit: 12, offset: 0 } satisfies RepositoryListRead)
    })

  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      calls.push({ url, method })

      if (method === 'POST' && url.includes('/developer/repositories')) return create(url)
      if (url.includes('/developer/summary')) return summary(url)
      if (url.includes('/developer/metrics')) return metrics(url)
      if (url.includes('/developer/activity')) return activity(url)
      if (url.includes('/developer/commits')) return commits(url)
      if (url.includes('/developer/repositories')) return repositories(url)
      if (url.includes('/projects')) return projects(url)
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

function renderDeveloperPage(entry = '/developer') {
  const router = createMemoryRouter([{ path: '*', element: <DeveloperPage /> }], {
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
 */
function expectNoFabricatedNumbers(): void {
  const text = document.body.textContent ?? ''
  expect(text).not.toMatch(/NaN/)
  expect(text).not.toMatch(/Infinity/)
  expect(text).not.toMatch(/undefined/)
}

/** The card a metric label belongs to, found the way a reader finds it. */
function cardFor(label: string): HTMLElement {
  return screen.getByRole('heading', { name: label }).closest('div.rounded-lg') as HTMLElement
}

/** The oversized figure a metric card prints, or `null` when it printed none. */
function figureOf(card: HTMLElement): string | null {
  return card.querySelector('.text-2xl')?.textContent ?? null
}

function expectedFigure(value: number, unit: DeveloperMetricRead['unit']): string {
  return `${formatNumber(value, unit === 'ratio' ? 2 : 0)} ${unit.toLowerCase()}`
}

/**
 * A summary-tile card, located by its label the way a reader finds it.
 *
 * Scoped to the Overview section because two of the six labels — "Repositories"
 * and "Lines changed" — are also headings further down the page, and a bare
 * `getByText` would find both.
 */
function tileFor(label: string): HTMLElement {
  const section = screen.getByRole('heading', { name: 'Overview' }).closest('section') as HTMLElement
  return within(section).getByText(label).closest('div.rounded-lg') as HTMLElement
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

function metric(
  overrides: Partial<DeveloperMetricRead> & Pick<DeveloperMetricRead, 'key' | 'label'>,
): DeveloperMetricRead {
  return {
    value: 1,
    unit: 'count',
    definition: 'How the figure is computed, in one sentence.',
    window_days: 30,
    source: 'git_commits',
    explanation: '1 commit was recorded inside the window.',
    available: true,
    reason_if_unavailable: null,
    ...overrides,
  }
}

/**
 * The eight metrics `GET /developer/metrics` always returns.
 *
 * `maintenance_activity` is a **genuine zero** and must render as `0`;
 * `recent_momentum` is **unmeasurable** — the seven days before the window
 * contain no commits, so there is nothing to divide by — and must render its
 * reason with no figure beside it. The two are different answers.
 */
const METRICS: DeveloperMetricRead[] = [
  metric({
    key: 'commit_activity',
    label: 'Commit activity',
    value: 18,
    definition: 'Commits recorded inside the window, counted once each.',
    explanation: '18 commits were recorded between 1 Jan 2019 and 30 Jan 2019.',
  }),
  metric({
    key: 'repository_activity',
    label: 'Repository activity',
    value: 2,
    definition: 'Distinct repositories with at least one commit inside the window.',
    explanation: '2 of the 2 registered repositories recorded a commit inside the window.',
  }),
  metric({
    key: 'change_volume',
    label: 'Change volume',
    value: 842,
    unit: 'lines',
    definition: 'Lines added plus lines removed inside the window.',
    explanation: '842 lines were added or removed across 18 recorded commits.',
  }),
  metric({
    key: 'active_days',
    label: 'Active days',
    value: 12,
    unit: 'days',
    definition: 'Distinct UTC dates carrying at least one commit inside the window.',
    explanation: '12 distinct days carried at least one commit inside the window.',
  }),
  metric({
    key: 'consistency',
    label: 'Consistency',
    value: 0.4,
    unit: 'ratio',
    definition: 'Active days divided by the days the window covers, as a ratio between 0 and 1.',
    explanation: '12 active days out of the 30 days the window covers.',
  }),
  metric({
    key: 'repository_growth',
    label: 'Repository growth',
    value: 3,
    definition: 'Commits landing on branches the scan first saw inside the window.',
    explanation: '3 commits landed on branches first seen inside the window.',
  }),
  metric({
    key: 'maintenance_activity',
    label: 'Maintenance activity',
    value: 0,
    definition: 'Commits touching files not modified in the 90 days before the window.',
    explanation: '0 commits inside the window touched a file untouched in the 90 days before it.',
  }),
  metric({
    key: 'recent_momentum',
    label: 'Recent momentum',
    value: null,
    unit: 'ratio',
    definition: 'Commits in the last 7 days divided by the commits in the 7 before them.',
    window_days: 14,
    explanation: 'Commits in the last 7 days over the 7 before them.',
    available: false,
    reason_if_unavailable: NOT_ENOUGH_DATA,
  }),
]

const SUMMARY: DeveloperSummaryRead = {
  repository_count: 2,
  active_repository_count: 2,
  commit_count: 1284,
  commits_in_window: 18,
  active_days: 12,
  change_volume: 842,
  repositories_touched: 2,
  window_days: 30,
  window_start: '2019-01-01T00:00:00Z',
  window_end: '2019-01-30T23:59:59Z',
  latest_commit_at: '2019-01-29T14:05:00Z',
  last_scanned_at: '2019-01-29T14:06:00Z',
  has_data: true,
  summary: '18 commits were recorded in the last 30 days across 2 repositories.',
}

const EMPTY_SUMMARY: DeveloperSummaryRead = {
  ...SUMMARY,
  repository_count: 0,
  active_repository_count: 0,
  commit_count: 0,
  commits_in_window: 0,
  active_days: 0,
  change_volume: 0,
  repositories_touched: 0,
  latest_commit_at: null,
  last_scanned_at: null,
  has_data: false,
  summary: 'No repository has been registered yet.',
}

function repository(overrides: Partial<RepositoryRead> = {}): RepositoryRead {
  return {
    id: REPO_ID,
    name: 'Nexo',
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
    ...overrides,
  }
}

/**
 * Two registered work trees, the second of which git could not read.
 *
 * The failed scan is a **row with a sentence**, not an exception: the repository
 * is still on the page, still counted in the summary, and the reason it could not
 * be read is rendered verbatim underneath.
 */
const REPOSITORIES: RepositoryRead[] = [
  repository(),
  repository({
    id: REPO_TWO_ID,
    name: 'broken-mirror',
    local_path: '/home/ada/code/broken-mirror',
    primary_language: null,
    project_id: null,
    current_branch: null,
    default_branch: null,
    branch_count: 0,
    commit_count: 0,
    first_commit_at: null,
    latest_commit_at: null,
    last_scan_status: 'error',
    last_scan_error: SCAN_FAILURE,
  }),
]

function commit(overrides: Partial<CommitRead> = {}): CommitRead {
  return {
    id: COMMIT_ID,
    repository_id: REPO_ID,
    commit_hash: '9f2c4b7a1d3e5f60718293a4b5c6d7e8f9012345',
    short_hash: '9f2c4b7a',
    committed_at: '2019-01-29T14:05:00Z',
    message: 'Record the scan outcome as data rather than an exception',
    author_name: 'Ada',
    author_email: 'ada@nexus.local',
    additions: 128,
    deletions: 12,
    files_changed: 3,
    branch: 'main',
    created_at: '2019-01-29T14:05:04Z',
    ...overrides,
  }
}

const COMMITS: CommitListRead = {
  items: [commit()],
  total: 1,
  limit: 25,
  offset: 0,
}

/** Three dense buckets, with a quiet day in the middle, which is the point. */
const ACTIVITY: DeveloperActivityRead = {
  granularity: 'day',
  window_days: 30,
  window_start: '2019-01-01T00:00:00Z',
  window_end: '2019-01-30T23:59:59Z',
  repository_id: null,
  buckets: [
    {
      bucket_start: '2019-01-07T00:00:00Z',
      bucket_end: '2019-01-08T00:00:00Z',
      commits: 4,
      additions: 220,
      deletions: 30,
      files_changed: 7,
      repository_count: 1,
    },
    {
      bucket_start: '2019-01-08T00:00:00Z',
      bucket_end: '2019-01-09T00:00:00Z',
      commits: 0,
      additions: 0,
      deletions: 0,
      files_changed: 0,
      repository_count: 0,
    },
    {
      bucket_start: '2019-01-09T00:00:00Z',
      bucket_end: '2019-01-10T00:00:00Z',
      commits: 9,
      additions: 480,
      deletions: 60,
      files_changed: 11,
      repository_count: 2,
    },
  ],
  total_commits: 13,
}

const PROJECT: Project = {
  id: PROJECT_ID,
  owner_id: '99999999-9999-4999-8999-999999999999',
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

/* -------------------------------------------------------------------- tests */

describe('developer dashboard', () => {
  it('leads with the six summary tiles and the sentence the server composed for them', async () => {
    installBackend()
    const { container } = renderDeveloperPage()

    expect(await screen.findByText(SUMMARY.summary)).toBeInTheDocument()
    await resolveCharts(container)

    expect(screen.getByRole('heading', { level: 1, name: 'Developer Intelligence' })).toBeInTheDocument()
    // The badge quotes the window the *server* answered for, not the one the
    // client asked for: the default preset sends no `window_days` at all.
    expect(screen.getByText(`Figures cover the last ${formatNumber(30)} days`)).toBeInTheDocument()

    expect(within(tileFor('Repositories')).getByText(formatNumber(2))).toBeInTheDocument()
    expect(within(tileFor(`Commits, last ${formatNumber(30)} days`)).getByText(formatNumber(18)))
      .toBeInTheDocument()
    expect(within(tileFor('Commits, whole history')).getByText(formatNumber(1284))).toBeInTheDocument()
    expect(within(tileFor('Days with a commit')).getByText(formatNumber(12))).toBeInTheDocument()
    expect(within(tileFor('Lines changed')).getByText(formatNumber(842))).toBeInTheDocument()
    expect(within(tileFor('Repositories with commits')).getByText(formatNumber(2)))
      .toBeInTheDocument()

    // Every tile names what it counts in words, so a bare `12` beside "Days with
    // a commit" cannot be read as hours.
    expect(
      within(tileFor('Days with a commit')).getByText(
        'Distinct days carrying at least one commit — not hours spent',
      ),
    ).toBeInTheDocument()

    expectNoFabricatedNumbers()
  })

  it('renders all eight metrics with their value, their definition and their explanation', async () => {
    installBackend()
    renderDeveloperPage()

    expect(await screen.findByRole('heading', { name: 'Commit activity' })).toBeInTheDocument()

    // Exactly eight, inside the section that owns them. The endpoint always
    // returns all eight — a metric the data cannot support is marked, not
    // dropped — so a seventh or a ninth would mean the client invented one.
    const section = screen.getByRole('heading', { name: 'Metrics' }).closest('section') as HTMLElement
    expect(within(section).getAllByRole('heading', { level: 3 })).toHaveLength(8)
    for (const entry of METRICS) {
      expect(within(section).getByRole('heading', { name: entry.label })).toBeInTheDocument()
    }

    for (const entry of METRICS.filter((item) => item.available)) {
      const card = cardFor(entry.label)
      expect(figureOf(card)).toBe(expectedFigure(entry.value ?? 0, entry.unit))
      expect(within(card).getByText(entry.definition)).toBeInTheDocument()
      expect(within(card).getByText(entry.explanation)).toBeInTheDocument()
    }
    expectNoFabricatedNumbers()
  })

  it('shows an unmeasurable metric as its reason, with no figure beside it', async () => {
    installBackend()
    renderDeveloperPage()

    const card = await screen.findByRole('heading', { name: 'Recent momentum' }).then((node) =>
      node.closest('div.rounded-lg') as HTMLElement,
    )

    // `recent_momentum` divides the last seven days by the seven before them. A
    // zero denominator is an absence of measurement, not a measurement of zero.
    expect(figureOf(card)).toBeNull()
    expect(within(card).getByText('Not enough data yet.')).toBeInTheDocument()
    expect(within(card).getByText(NOT_ENOUGH_DATA)).toBeInTheDocument()
    expect(within(card).queryByText(NO_VALUE)).toBeNull()

    // The seven measured metrics beside it are untouched.
    expect(figureOf(cardFor('Commit activity'))).toBe(expectedFigure(18, 'count'))
  })

  it('renders a genuine zero as 0, never as a dash', async () => {
    installBackend()
    renderDeveloperPage()

    const card = await screen.findByRole('heading', { name: 'Maintenance activity' }).then((node) =>
      node.closest('div.rounded-lg') as HTMLElement,
    )

    expect(figureOf(card)).toBe(expectedFigure(0, 'count'))
    expect(within(card).queryByText(NO_VALUE)).toBeNull()
    expect(within(card).queryByText('Not enough data yet.')).toBeNull()
    expect(document.body.textContent).not.toMatch(/undefined/)
  })

  it('shows every busy region as a silhouette carrying no digits', () => {
    // Six reads that never settle, so the page is caught in its loading state
    // rather than in a state the test has to keep in sync with.
    const never = (): Promise<Response> => new Promise(() => undefined)
    installBackend({
      summary: never,
      metrics: never,
      activity: never,
      commits: never,
      repositories: never,
      projects: never,
    })
    renderDeveloperPage()

    // The masthead paints immediately; a blank page while the reads land is what
    // the skeletons exist to avoid.
    expect(screen.getByRole('heading', { level: 1, name: 'Developer Intelligence' })).toBeInTheDocument()
    expect(screen.getByText('Loading the developer summary')).toBeInTheDocument()
    expect(screen.getByText('Loading repositories')).toBeInTheDocument()
    expect(screen.getByText('Loading the commit timeline')).toBeInTheDocument()
    expect(screen.getByText('Loading recorded metrics')).toBeInTheDocument()
    expect(screen.getByText('Loading the change statistics')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Commit activity' })).toBeNull()
    expect(screen.queryByText(SUMMARY.summary)).toBeNull()

    // Nothing in a busy region reads as a figure. A grey `0` on a tile that may
    // well read "Not enough data yet." is a number, and this surface never shows
    // a number it does not have.
    const busy = screen.getAllByRole('status')
    expect(busy.length).toBeGreaterThanOrEqual(5)
    for (const region of busy) {
      expect(region.textContent ?? '').not.toMatch(/\d/)
    }
  })

  it('plots the activity series with its empty buckets, and names its window', async () => {
    const calls = installBackend()
    const { container } = renderDeveloperPage()

    expect(await screen.findByRole('heading', { name: 'Recorded commit activity' })).toBeInTheDocument()
    await resolveCharts(container)

    expect(container.querySelector('[data-chart-surface]')).not.toBeNull()
    expect(screen.getByText(/Empty buckets are plotted as zero commits rather than skipped\./)).toBeInTheDocument()
    // 220 + 480 additions and 30 + 60 deletions, summed from the dense buckets
    // rather than from the days that happened to carry a commit.
    expect(within(changeRow('Files changed')).getByText(formatNumber(18))).toBeInTheDocument()
    expect(within(changeRow('Lines added')).getByText(formatNumber(700))).toBeInTheDocument()
    expect(within(changeRow('Lines removed')).getByText(formatNumber(90))).toBeInTheDocument()
    expect(screen.getByText('13 commits recorded across 3 day buckets.')).toBeInTheDocument()

    // The window lives in the URL and the default preset sends no `window_days`,
    // so the server owns the length that is actually used.
    expect(getCalls(calls, '/developer/summary')[0]?.url).not.toContain('window_days')
    expect(getCalls(calls, '/developer/activity')[0]?.url).toContain('granularity=day')
    expectNoFabricatedNumbers()
  })

  it('lists both registered work trees, and a broken one is a card carrying a sentence', async () => {
    installBackend()
    renderDeveloperPage()

    expect(await screen.findByRole('heading', { name: 'Nexo' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'broken-mirror' })).toBeInTheDocument()

    // A broken repository must never break NEXUS. It is a row, with the recorded
    // reason rendered verbatim — not an exception, and not a page that stopped.
    expect(screen.getByText(SCAN_FAILURE)).toBeInTheDocument()
    expect(screen.getAllByText('Scan failed')).toHaveLength(1)
    expect(screen.queryByRole('alert')).toBeNull()

    // It is still a registered repository, so it is still linked to its own page
    // and still counted above.
    expect(screen.getByRole('link', { name: 'broken-mirror' })).toHaveAttribute(
      'href',
      `/developer/${REPO_TWO_ID}`,
    )
    expect(screen.getByText('No tracked language')).toBeInTheDocument()
    // A repository with no commits has no branch, and that is said in words
    // rather than rendered as an empty field.
    expect(
      screen.getByText('No branch yet — this repository has no commits.'),
    ).toBeInTheDocument()
    expectNoFabricatedNumbers()
  })

  it('states an account that has registered nothing, and fabricates no totals for it', async () => {
    installBackend({
      summary: () => json(EMPTY_SUMMARY),
      repositories: () => json({ items: [], total: 0, limit: 12, offset: 0 } satisfies RepositoryListRead),
      activity: () => json({ ...ACTIVITY, total_commits: 0, buckets: [] }),
      commits: () => json({ items: [], total: 0, limit: 25, offset: 0 } satisfies CommitListRead),
      metrics: () => json([]),
    })
    renderDeveloperPage()

    expect(await screen.findByText('No repositories registered yet')).toBeInTheDocument()
    expect(
      screen.getByText(
        'Registering a local git work tree is what starts this. NEXUS reads the path with ' +
          'the git CLI on the machine it runs on — there is no hosted service and no account ' +
          'to connect — and everything it shows is read from what that scan records.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('Nothing registered yet')).toBeInTheDocument()

    // Six zeroes above an empty account would read as a measurement, and the
    // engine finding nothing is the opposite of one. So no tile row at all, no
    // pager quoting "0 repositories", and no metric cards.
    expect(screen.queryByText('Days with a commit')).toBeNull()
    expect(screen.queryByText('Commits, whole history')).toBeNull()
    expect(screen.queryByLabelText('Repository pages')).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Metrics' })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Overview' })).toBeNull()
    // The call to action is offered twice — once in the masthead, once in the
    // empty state — because it is the only thing on the page to do.
    expect(screen.getAllByRole('button', { name: 'Register repository' })).toHaveLength(2)
    expectNoFabricatedNumbers()
  })

  it('reports a 5xx repository list with a retry that asks again and recovers', async () => {
    const user = userEvent.setup()
    let failing = true
    installBackend({
      repositories: () =>
        failing
          ? envelope('internal_error', 'The repository service is unavailable.', 500, 'req-dev-1')
          : json({ items: REPOSITORIES, total: REPOSITORIES.length, limit: 12, offset: 0 }),
    })
    renderDeveloperPage()

    // The shared retry policy asks twice more with a backoff, so the error surface
    // cannot arrive inside the default 5 s budget.
    const alert = await screen.findByRole('alert', {}, { timeout: RETRY_SURFACE_TIMEOUT_MS })
    expect(alert).toHaveTextContent('The repository list could not load')
    expect(alert).toHaveTextContent(
      'The failure was recorded on the server. Retry, and quote the request ID below.',
    )
    expect(alert).toHaveTextContent('The repository service is unavailable.')
    expect(alert).toHaveTextContent('req-dev-1')
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument()

    // A failed read is distinguishable from an empty one: no repository is
    // invented, and no empty state claims there was simply nothing registered.
    expect(screen.queryByRole('heading', { name: 'Nexo' })).toBeNull()
    expect(screen.queryByText('No repositories registered yet')).toBeNull()

    failing = false
    await user.click(screen.getByRole('button', { name: /Retry/i }))

    expect(await screen.findByRole('heading', { name: 'Nexo' })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  })

  it('reports a failed activity read with dashes for the change figures, never zeros', async () => {
    installBackend({
      activity: () => envelope('internal_error', 'The activity service is unavailable.', 500, 'req-dev-2'),
    })
    renderDeveloperPage()

    const alert = await screen.findByRole('alert', {}, { timeout: RETRY_SURFACE_TIMEOUT_MS })
    expect(alert).toHaveTextContent('The activity series could not load')
    expect(alert).toHaveTextContent('req-dev-2')

    // "No measurement yet" and "nothing changed" are different facts, and only
    // one of them would be a claim. The figures read as dashes.
    for (const label of ['Files changed', 'Lines added', 'Lines removed']) {
      expect(within(changeRow(label)).getByText(NO_VALUE)).toBeInTheDocument()
      expect(within(changeRow(label)).queryByText('0')).toBeNull()
    }

    // A panel that failed must not take the page with it: the repositories, the
    // metrics and the timeline are all still on screen.
    expect(screen.getByRole('heading', { name: 'Nexo' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Commit activity' })).toBeInTheDocument()
    expectNoFabricatedNumbers()
  })

  it('surfaces a 422 on the registration form under the field the server named', async () => {
    const user = userEvent.setup()
    const calls = installBackend({
      create: () =>
        envelope('validation_error', 'That path is not a git work tree.', 422, 'req-dev-3', {
          errors: [
            {
              field: 'local_path',
              message: 'That directory does not contain a .git entry.',
            },
            { field: 'name', message: 'The name must be 200 characters or fewer.' },
          ],
        }),
    })
    renderDeveloperPage()

    await screen.findByRole('heading', { name: 'Nexo' })
    await user.click(screen.getByRole('button', { name: 'Register repository' }))

    const dialog = await screen.findByRole('dialog')
    const pathField = within(dialog).getByLabelText('Local path')
    expect(pathField).toHaveAttribute('placeholder', '/home/you/code/my-project')

    await user.type(pathField, '/home/ada/code/not-a-repo')
    await user.click(within(dialog).getByRole('button', { name: 'Register repository' }))

    // The backend validates the path against the filesystem, so the client has
    // no sentence of its own to give. The server's field errors are placed under
    // the fields they name, and the input itself is marked invalid.
    const pathError = await within(dialog).findByText('That directory does not contain a .git entry.')
    expect(pathError).toBeInTheDocument()
    expect(
      within(pathError.parentElement as HTMLElement).getByLabelText('Local path'),
    ).toHaveAttribute('aria-invalid', 'true')

    expect(
      within(dialog).getByText('The name must be 200 characters or fewer.'),
    ).toBeInTheDocument()

    // Field-scoped errors are not repeated as a banner: the reader is told once,
    // next to the thing they must change.
    expect(within(dialog).queryByRole('alert')).toBeNull()
    expect(screen.getByRole('heading', { name: 'Nexo' })).toBeInTheDocument()

    const posts = calls.filter((call) => call.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(posts[0]?.url).toContain('/developer/repositories')
  })

  it('never prints NaN, Infinity or an undefined figure', async () => {
    installBackend()
    const { container } = renderDeveloperPage()
    await resolveCharts(container)
    await settle()

    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/NaN/)
    expect(text).not.toMatch(/Infinity/)
    expect(text).not.toMatch(/undefined/)
    // The brief's language rule, scanned against the rendered page rather than
    // against the copy that shipped today.
    expect(text).not.toMatch(/\d+\s*hours?\s*(focused|spent|worked)/i)
    expect(text).not.toMatch(/productivity|unproductive|lazy|burnout/i)
  })
})

/** The `dt`/`dd` pair a change figure is rendered as, found through its label. */
function changeRow(label: string): HTMLElement {
  return screen.getByText(label).closest('div') as HTMLElement
}