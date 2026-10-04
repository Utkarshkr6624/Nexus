import type { ReactElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/tooltip'
import {
  BranchList,
  CommitTimeline,
  DeveloperActivitySection,
  DeveloperEmptyState,
  DeveloperMetricList,
  DeveloperMetricListSkeleton,
  DeveloperSummaryTiles,
  LanguageBreakdown,
  RepositoryCard,
  ScanRunList,
  ScanStatusPanel,
  TimelineScopeNote,
  commitFilesPhrase,
  describeCurrentBranch,
  formatDeveloperMetricValue,
} from '@/features/developer/components'
import { NOT_ENOUGH_DATA_TITLE } from '@/features/developer/components/developer-vocabulary'
import { NO_VALUE, formatNumber } from '@/features/analytics/format'
import { queryRetryPolicy } from '@/app/query-client'
import type { ApiErrorEnvelope } from '@/types/api'
import {
  NOT_ENOUGH_DATA,
  type BranchRead,
  type CommitRead,
  type DeveloperActivityRead,
  type DeveloperMetricRead,
  type DeveloperMetricUnit,
  type RepositoryRead,
  type ScanRunRead,
} from '@/types/developer'

/**
 * The Phase 8 component library, mounted for real.
 *
 * `features/developer/components` is the layer both pages delegate every
 * presentational decision to, and it is where the brief's hardest rule actually
 * lives: **git records evidence, and the surface may never upgrade it into a
 * verdict about a person.** Nothing here can claim hours, focus, productivity or
 * effort, and nothing here may turn an absence of measurement into a number.
 *
 * Five decisions shape this file.
 *
 * **Every component is mounted for real, inside a router and a query client.**
 * Several render `Link` (`RepositoryCard`, `CommitTimeline`, the summary tiles)
 * and `MetricCard` renders a Radix tooltip, so the harness is the one
 * `risk-center-page.test.tsx` uses — a memory router plus a **fresh**
 * `QueryClient` per render. The shared singleton is deliberately not used:
 * `AppProviders` registers `onSessionChange(() => queryClient.clear())`
 * (`src/app/auth-bootstrap.tsx:13`), and in jsdom that clear lands mid-test and
 * strands every component at `pending` forever. The retry policy below is
 * `src/app/query-client.ts`'s own `queryRetryPolicy`, not a copy of it.
 *
 * **Only `fetch` is stubbed, and the routing table is there to prove it is not
 * needed.** The components are presentational by construction — a
 * `RepositoryCard` renders from a literal `RepositoryRead` and never asks the
 * network anything. One test asserts that directly: it renders panels a page
 * would fill from five different endpoints and then checks the request log is
 * empty. That is what keeps this a *component* suite rather than a second copy
 * of the page suites.
 *
 * **Recharts is given a size, and only a size.** jsdom has no layout engine, so
 * `ResponsiveContainer` measures 0×0 and renders an empty `<div>` — every chart
 * would come back as a title above an empty box, and half the assertions below
 * would pass without a single mark having been drawn. The mock replaces exactly
 * that measurement with a fixed 640×256 box and tags what it wraps in
 * `data-chart-surface`; the axes, scales, areas and sectors are the library's
 * own. **Nothing asserts on a library internal** — no `d` attribute, no recharts
 * class name, no pixel coordinate.
 *
 * **The charts go through the real `LazyChart` boundary**, so they resolve the
 * way they do in the app. The boundary paints the *same* card title as the real
 * chart, so a `findByRole('heading')` would happily resolve against a skeleton;
 * what tells them apart is that the fallback's body is a 256px `Skeleton` while a
 * resolved chart replaces it with a `data-chart-surface` (or an empty state).
 * {@link resolveCharts} waits on exactly that, and everything after it is a
 * `findBy*`.
 *
 * **The dates are fixed and in a completed year.** 2019 is used throughout, so a
 * formatter that omits the year for "this year" cannot make a fixture written
 * for this year start failing in January — and nothing here asserts on a relative
 * age ("2 hours ago"), which would depend on the machine's clock.
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
 * Only the first test to touch a lazily loaded chart pays for it: the
 * `import()` has to fetch and transform the chart module, and the very first
 * also pulls in the whole of Recharts. Every later test finds its module in the
 * registry and resolves in milliseconds. The bound only ever has to cover module
 * loading, so raising it cannot turn a genuine failure into a pass.
 */
const LAZY_RESOLVE_TIMEOUT_MS = 10_000

const REPO_ID = '11111111-1111-4111-8111-111111111111'
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
  metrics?: Route
  activity?: Route
  commits?: Route
  repositories?: Route
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
  const activity = overrides.activity ?? unused()
  const commits = overrides.commits ?? unused()
  const repositories = overrides.repositories ?? unused()

  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      calls.push({ url, method })

      if (url.includes('/developer/summary')) return summary(url)
      if (url.includes('/developer/metrics')) return metrics(url)
      if (url.includes('/developer/activity')) return activity(url)
      if (url.includes('/developer/commits')) return commits(url)
      if (url.includes('/developer/repositories')) return repositories(url)
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
function renderComponent(node: ReactElement, entry = '/developer') {
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
 */
function expectNoFabricatedNumbers(): void {
  const text = document.body.textContent ?? ''
  expect(text).not.toMatch(/NaN/)
  expect(text).not.toMatch(/Infinity/)
  expect(text).not.toMatch(/undefined/)
}

/**
 * The big figure a metric card prints, or `null` when it printed none at all.
 *
 * The card renders its figure in its own oversized paragraph, so reading that
 * paragraph is how "this card shows a number" is told apart from "this card
 * shows the reason it could not be" — the distinction the whole surface turns
 * on.
 */
function figureOf(card: HTMLElement): string | null {
  return card.querySelector('.text-2xl')?.textContent ?? null
}

/** The card a metric label belongs to, found the way a reader finds it. */
function cardFor(label: string): HTMLElement {
  return screen.getByRole('heading', { name: label }).closest('div.rounded-lg') as HTMLElement
}

/**
 * The figure a card will print for a metric, built the way it is rendered: the
 * value formatted at its unit's precision, then the unit named in words.
 *
 * Built from `formatNumber` rather than written out, because grouping and decimal
 * separators follow the machine's locale — the same reason
 * `analytics-page.test.tsx` builds its range captions with `formatRangeLabel`.
 */
function expectedFigure(value: number, unit: DeveloperMetricUnit): string {
  return `${formatNumber(value, unit === 'ratio' ? 2 : 0)} ${unit.toLowerCase()}`
}

/**
 * An absolute instant, formatted exactly the way the formatters under test do it.
 * Pinning the locale here means the expectation is the machine's own rendering of
 * that instant rather than one hard-coded spelling of it.
 */
function instant(iso: string): string {
  return new Intl.DateTimeFormat(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(iso))
}

/** The daily x-axis label `formatActivityBucketLabel` builds for a bucket start. */
function bucketLabel(iso: string): string {
  return instant(iso)
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
 * Two of them are load-bearing rather than decorative. `maintenance_activity` is
 * a **genuine zero** (`available: true`, `value: 0`) and must render as `0` and
 * never as a dash; `recent_momentum` is **unmeasurable** — it divides the last
 * seven days by the seven before them, and the denominator is zero — and must
 * render its reason with no figure beside it at all. Collapsing those two into
 * one "empty" state would report that momentum was nil when in fact there was
 * nothing to divide by, which is the exact error the contract forbids.
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
    source: 'git_commits',
    explanation: 'Commits in the last 7 days over the 7 before them.',
    available: false,
    reason_if_unavailable: NOT_ENOUGH_DATA,
  }),
]

const UNAVAILABLE = METRICS[7] as DeveloperMetricRead
const ZERO_METRIC = METRICS[6] as DeveloperMetricRead

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

function branch(overrides: Partial<BranchRead> = {}): BranchRead {
  return {
    id: '55555555-5555-4555-8555-555555555555',
    repository_id: REPO_ID,
    name: 'main',
    is_current: true,
    is_default: true,
    head_commit_hash: '9f2c4b7a1d3e5f60718293a4b5c6d7e8f9012345',
    last_committed_at: '2019-01-29T14:05:00Z',
    created_at: '2018-02-01T08:00:00Z',
    updated_at: '2019-01-29T14:05:00Z',
    ...overrides,
  }
}

function scanRun(overrides: Partial<ScanRunRead> = {}): ScanRunRead {
  return {
    id: '66666666-6666-4666-8666-666666666666',
    repository_id: REPO_ID,
    status: 'ok',
    commits_discovered: 1284,
    commits_added: 1284,
    branches_discovered: 3,
    duration_ms: 412,
    error: null,
    scanned_at: '2019-01-29T14:06:00Z',
    created_at: '2019-01-29T14:06:00Z',
    ...overrides,
  }
}

/** Three dense buckets with a quiet day in the middle, which is the whole point. */
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

const EMPTY_ACTIVITY: DeveloperActivityRead = {
  ...ACTIVITY,
  buckets: ACTIVITY.buckets.map((bucket) => ({
    ...bucket,
    commits: 0,
    additions: 0,
    deletions: 0,
    files_changed: 0,
    repository_count: 0,
  })),
  total_commits: 0,
}

/** Every `DeveloperEmptyState` variant, with the sentence the component ships. */
const EMPTY_COPY: Array<[string, string, string]> = [
  [
    'repositories',
    'No repositories registered yet',
    'Registering a local git work tree is what starts this. NEXUS reads the path with ' +
      'the git CLI on the machine it runs on — there is no hosted service and no account ' +
      'to connect — and everything it shows is read from what that scan records.',
  ],
  [
    'commits',
    NOT_ENOUGH_DATA_TITLE,
    'The timeline is built from commits a scan read out of a registered repository. ' +
      "Register a work tree and run its first scan, and every commit it finds appears here.",
  ],
  [
    'metrics',
    NOT_ENOUGH_DATA_TITLE,
    'Each metric is computed from recorded commits and changed lines. Once a scan has ' +
      'read at least one commit, every metric below shows a value or the specific reason ' +
      'it could not be computed.',
  ],
  [
    'activity',
    NOT_ENOUGH_DATA_TITLE,
    'The series buckets commits day by day, week by week or month by month across the ' +
      'window. Every bucket in the range is plotted, including the empty ones, so a quiet ' +
      'stretch shows as zero commits rather than as a gap.',
  ],
  [
    'languages',
    NOT_ENOUGH_DATA_TITLE,
    'Languages are counted from the file extensions git tracks. A repository whose ' +
      'files use extensions outside the recognised set contributes nothing here — there is ' +
      'no "other" bucket, because that would be a category, not a language.',
  ],
  [
    'branches',
    NOT_ENOUGH_DATA_TITLE,
    'Branches are read from the repository at scan time. A repository with no commits ' +
      'has no branches to list yet, and one on a detached HEAD still has them.',
  ],
  [
    'scanRuns',
    'No scan has been run yet',
    'Every attempt to read a repository is recorded whatever its outcome. Run a scan and ' +
      'the run appears here with what it discovered, or with the sentence explaining why it ' +
      'could not read the directory.',
  ],
  [
    'filtered',
    'Nothing matches this filter',
    'The repositories exist and have been scanned; none of them match what is selected. ' +
      'Clearing the filter shows them again.',
  ],
]

/* --------------------------------------------------------- the eight metrics */

describe('DeveloperMetricList', () => {
  it('shows all eight, each with its value, its definition and its explanation together', () => {
    renderComponent(<DeveloperMetricList metrics={METRICS} />)

    expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(8)
    expect(METRICS.map((entry) => entry.label).sort()).toEqual(
      screen.getAllByRole('heading', { level: 3 }).map((node) => node.textContent).sort(),
    )

    for (const entry of METRICS.filter((item) => item.available)) {
      const card = cardFor(entry.label)
      const value = entry.value ?? 0
      expect(figureOf(card)).toBe(expectedFigure(value, entry.unit))
      // The explanation is visible text, not a hover tooltip. The brief requires
      // the value *and* the definition *and* the explanation, and a tooltip is
      // shown to nobody on a touch device or a printed page.
      expect(within(card).getByText(entry.definition)).toBeInTheDocument()
      expect(within(card).getByText(entry.explanation)).toBeInTheDocument()
      // The source is named too, so a reader can see which recorded facts the
      // figure was built from rather than having to take the label's word.
      expect(within(card).getByText(entry.source)).toBeInTheDocument()
    }
    expectNoFabricatedNumbers()
  })

  it('replaces an unmeasurable figure with the reason and prints no number for it', () => {
    renderComponent(<DeveloperMetricList metrics={METRICS} />)

    const card = cardFor(UNAVAILABLE.label)

    expect(figureOf(card)).toBeNull()
    expect(within(card).getByText(NOT_ENOUGH_DATA_TITLE)).toBeInTheDocument()
    // The backend's own sentence, verbatim, because it names the ingredient that
    // was missing and a generic sentence cannot.
    expect(within(card).getByText(UNAVAILABLE.reason_if_unavailable as string)).toBeInTheDocument()
    expect(within(card).queryByText(NO_VALUE)).toBeNull()

    // The seven metrics beside it are untouched: marking one unavailable must not
    // blank the ones that were measured.
    expect(figureOf(cardFor('Commit activity'))).toBe(expectedFigure(18, 'count'))
  })

  it('renders a genuine zero as 0, never as a dash and never as a reason', () => {
    renderComponent(<DeveloperMetricList metrics={METRICS} />)

    const card = cardFor(ZERO_METRIC.label)

    // Zero commits touching an old file is a measurement. Collapsing it into the
    // insufficient-data state would throw away a real answer.
    expect(figureOf(card)).toBe(expectedFigure(0, 'count'))
    expect(within(card).queryByText(NO_VALUE)).toBeNull()
    expect(within(card).queryByText(NOT_ENOUGH_DATA_TITLE)).toBeNull()
    expect(within(card).getByText(ZERO_METRIC.explanation)).toBeInTheDocument()
    expect(card.textContent).not.toMatch(/undefined/)
  })

  it('draws eight silhouettes carrying no digits while the first read is in flight', () => {
    const { container } = renderComponent(<DeveloperMetricList metrics={[]} isLoading />)

    expect(screen.getByRole('status')).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByText('Loading recorded metrics')).toBeInTheDocument()
    // Eight tiles, each reserving the blocks the real card occupies, so the grid
    // does not reflow when the data lands.
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(8)
    // A grey `0` on a tile that may well read "Not enough data yet." is a
    // number, and this surface never shows a number it does not have.
    expect(screen.getByRole('status').textContent ?? '').not.toMatch(/\d/)
  })

  it('announces the list skeleton without ever drawing a figure', () => {
    renderComponent(<DeveloperMetricListSkeleton />)

    const status = screen.getByRole('status')
    expect(status).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByText('Loading recorded metrics')).toBeInTheDocument()
    expect(status.textContent ?? '').not.toMatch(/\d/)
  })

  it('states the insufficient-data condition when the endpoint returned no metrics at all', () => {
    renderComponent(<DeveloperMetricList metrics={[]} />)

    expect(screen.getByText(NOT_ENOUGH_DATA_TITLE)).toBeInTheDocument()
    expect(
      screen.getByText(
        'Each metric is computed from recorded commits and changed lines. Once a scan has ' +
          'read at least one commit, every metric below shows a value or the specific reason ' +
          'it could not be computed.',
      ),
    ).toBeInTheDocument()
    expectNoFabricatedNumbers()
  })
})

describe('formatDeveloperMetricValue', () => {
  it('formats a real measurement, zero included, and never substitutes a value for an absence', () => {
    // Every formatter on this surface takes `number | null | undefined` and
    // returns the analytics dash rather than `?? 0`. A zero here is a
    // measurement and keeps its digits; the nullish and the non-finite cases are
    // absences and collapse to `—`.
    expect(formatDeveloperMetricValue(0, 'count')).toBe(formatNumber(0))
    expect(formatDeveloperMetricValue(842, 'lines')).toBe(formatNumber(842))
    expect(formatDeveloperMetricValue(0.4, 'ratio')).toBe(formatNumber(0.4, 2))

    expect(formatDeveloperMetricValue(null, 'count')).toBe(NO_VALUE)
    expect(formatDeveloperMetricValue(undefined, 'count')).toBe(NO_VALUE)
    expect(formatDeveloperMetricValue(Number.NaN, 'count')).toBe(NO_VALUE)
    expect(formatDeveloperMetricValue(Number.POSITIVE_INFINITY, 'ratio')).toBe(NO_VALUE)
  })
})

/* ------------------------------------------------------------- empty states */

describe('DeveloperEmptyState', () => {
  it('says why each region is empty and what would fill it, in its own words', () => {
    // A bare "No commits" reads as a broken page; "no commits, and commits appear
    // when a scan reads the repository" reads as an answer. Each region gets its
    // own sentence because a missing commit history and a missing branch list are
    // filled by completely different things.
    for (const [variant, title, description] of EMPTY_COPY) {
      const { unmount } = renderComponent(
        <DeveloperEmptyState variant={variant as 'commits'} />,
      )
      expect(screen.getByText(title)).toBeInTheDocument()
      expect(screen.getByText(description)).toBeInTheDocument()
      unmount()
    }
  })

  it('does not call a cold start "not enough data", because nothing is missing', () => {
    // The account has not started, rather than having failed to supply
    // something. A title reading "Not enough data yet" would imply the former,
    // and a filtered list is the mirror image: the records exist.
    const cold = renderComponent(<DeveloperEmptyState variant="repositories" />)
    expect(screen.getByText('No repositories registered yet')).toBeInTheDocument()
    expect(screen.queryByText(NOT_ENOUGH_DATA_TITLE)).toBeNull()
    cold.unmount()

    const filtered = renderComponent(<DeveloperEmptyState variant="filtered" />)
    expect(screen.getByText('Nothing matches this filter')).toBeInTheDocument()
    expect(
      screen.getByText(
        'The repositories exist and have been scanned; none of them match what is selected. ' +
          'Clearing the filter shows them again.',
      ),
    ).toBeInTheDocument()
    filtered.unmount()
  })

  it('renders the backend\'s own reason verbatim, in preference to its built-in copy', () => {
    renderComponent(
      <DeveloperEmptyState
        variant="commits"
        reason="No commit was recorded anywhere during the last 30 days. Every bucket in that range was still read, and each is a recorded zero — a quiet stretch is a fact, not missing data."
      />,
    )

    expect(
      screen.getByText(
        'No commit was recorded anywhere during the last 30 days. Every bucket in that range was still read, and each is a recorded zero — a quiet stretch is a fact, not missing data.',
      ),
    ).toBeInTheDocument()
    expect(
      screen.queryByText(
        'The timeline is built from commits a scan read out of a registered repository. Register a work tree and run its first scan, and every commit it finds appears here.',
      ),
    ).not.toBeInTheDocument()
  })
})

/* ----------------------------------------------------------- a broken repo */

describe('ScanStatusPanel', () => {
  it('renders a failed scan as a panel carrying the recorded sentence, never a traceback', () => {
    renderComponent(
      <ScanStatusPanel
        status="error"
        error={SCAN_FAILURE}
        lastScannedAt="2019-01-29T14:06:00Z"
        commitCount={0}
        onScan={() => undefined}
      />,
    )

    // "A broken repository must never break NEXUS": the failure arrives as data
    // on a 200, so what a reader gets is a panel — not an exception, and not a
    // 500 that empties the page.
    expect(screen.getByText('The last scan could not read this repository')).toBeInTheDocument()
    expect(screen.getByText(SCAN_FAILURE)).toBeInTheDocument()
    expect(screen.getByRole('alert')).toBeInTheDocument()

    // Nothing else is affected, and the record of the failure is kept.
    expect(screen.getByText(/The repository is still registered/)).toBeInTheDocument()
    // The chip carries the word, not only a colour.
    expect(screen.getByText('Scan failed')).toBeInTheDocument()

    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/Traceback/)
    expect(text).not.toMatch(/File "|line \d+, in/)
    expectNoFabricatedNumbers()
  })

  it('says a repository that has never been scanned is unscanned, not broken', () => {
    renderComponent(
      <ScanStatusPanel status={null} error={null} lastScannedAt={null} commitCount={0} />,
    )

    expect(screen.getByText('Never scanned')).toBeInTheDocument()
    expect(
      screen.getByText(
        'Registering a repository records its path and proves it is a git work tree; it does not open it. Run the first scan to read its commits and branches.',
      ),
    ).toBeInTheDocument()
    // A null status is a normal state after registering, so no failure copy.
    expect(screen.queryByText('The last scan could not read this repository')).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('falls back to a sentence of its own when the backend recorded no reason', () => {
    renderComponent(
      <ScanStatusPanel status="error" error={null} lastScannedAt="2019-01-29T14:06:00Z" />,
    )

    expect(screen.getByText('The backend recorded no reason for the failure.')).toBeInTheDocument()
    expectNoFabricatedNumbers()
  })
})

describe('ScanRunList', () => {
  it('keeps the record of a failed attempt, with the sentence it recorded', () => {
    renderComponent(
      <ScanRunList
        runs={[
          scanRun({
            status: 'error',
            commits_discovered: 0,
            commits_added: 0,
            branches_discovered: 0,
            error: SCAN_FAILURE,
          }),
        ]}
      />,
    )

    expect(screen.getByText('0 commits discovered, 0 added, 0 branches seen.')).toBeInTheDocument()
    expect(screen.getByText(SCAN_FAILURE)).toBeInTheDocument()
    // A failed attempt is part of the repository's history and is not hidden.
    expect(screen.getByText('Scan failed')).toBeInTheDocument()
    expectNoFabricatedNumbers()
  })

  it('explains the gap between discovered and added as deduplication, not data loss', () => {
    renderComponent(<ScanRunList runs={[scanRun({ commits_added: 0 })]} />)

    // Re-scanning an unchanged repository discovers every commit and adds none.
    // A reader who did not know that would read the gap as loss.
    expect(
      screen.getByText(
        `${formatNumber(1284)} commits discovered, 0 added, 3 branches seen.`,
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        'Fewer commits were added than discovered because the ones already recorded ' +
          'were recognised and left alone — re-scanning does not duplicate history.',
      ),
    ).toBeInTheDocument()
  })

  it('explains an empty scan record rather than showing a blank list', () => {
    renderComponent(<ScanRunList runs={[]} />)

    expect(screen.getByText('No scan has been run yet')).toBeInTheDocument()
    expect(
      screen.getByText(
        'Every attempt to read a repository is recorded whatever its outcome. Run a scan and ' +
          'the run appears here with what it discovered, or with the sentence explaining why it ' +
          'could not read the directory.',
      ),
    ).toBeInTheDocument()
  })
})

/* ------------------------------------------------------------- commit facts */

describe('CommitTimeline', () => {
  it('shows the commit as evidence: who, when, which branch, and how many lines moved', () => {
    renderComponent(
      <CommitTimeline
        commits={[commit()]}
        title="Commit history"
        subtitle="Every commit the scan recorded in this repository, newest first."
        repositoryName={() => 'Nexo'}
        repositoryHref={() => `/developer/${REPO_ID}`}
      />,
    )

    expect(screen.getByRole('heading', { name: 'Commit history' })).toBeInTheDocument()
    expect(screen.getByText('1 commit shown, newest first.')).toBeInTheDocument()
    expect(
      screen.getByText('Record the scan outcome as data rather than an exception'),
    ).toBeInTheDocument()
    expect(screen.getByText('9f2c4b7a')).toBeInTheDocument()
    expect(screen.getByText('Ada')).toBeInTheDocument()
    expect(screen.getByText('main')).toBeInTheDocument()
    expect(screen.getByText('+128 / −12')).toBeInTheDocument()
    expect(screen.getByText('3 files changed')).toBeInTheDocument()
    expect(screen.getByTitle(instant('2019-01-29T14:05:00Z'))).toBeInTheDocument()

    // One row per commit, literally: no grouping into sessions and no streaks,
    // because a commit timestamp records that work happened at an instant and
    // nothing about its length.
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(document.body.textContent).not.toMatch(/session|streak|focused|productive/i)
    expectNoFabricatedNumbers()
  })

  it('prints a genuine zero diff as 0 and names an unattributable branch rather than guessing one', () => {
    renderComponent(
      <CommitTimeline
        commits={[
          commit({
            short_hash: 'aabbccdd',
            message: 'Move a file without changing a line',
            author_name: null,
            branch: null,
            additions: 0,
            deletions: 0,
            files_changed: 0,
          }),
        ]}
      />,
    )

    // `additions`, `deletions` and `files_changed` are plain numbers and may be
    // genuinely zero. "Recorded as zero" and "never counted" are different facts,
    // and the row does not merge them.
    expect(screen.getByText('+0 / −0')).toBeInTheDocument()
    expect(screen.getByText('no files recorded')).toBeInTheDocument()
    // A null author is not a fabricated person, and a null branch is not one
    // either — attribution is explicitly best-effort and the backend refuses to
    // guess.
    expect(screen.getByText('Author not recorded')).toBeInTheDocument()
    expect(screen.getByText('Branch not attributable')).toBeInTheDocument()
    expectNoFabricatedNumbers()
  })

  it('shows a skeleton with no digits while the history is in flight', () => {
    renderComponent(<CommitTimeline commits={[]} isLoading />)

    expect(screen.getByText('Loading the commit timeline')).toBeInTheDocument()
    // A grey `+0 / −0` would be a diff, and this surface never shows a diff it
    // does not have.
    expect(screen.getByRole('status').textContent ?? '').not.toMatch(/\d/)
  })

  it('says why an empty timeline is empty, and what fills it', () => {
    renderComponent(
      <CommitTimeline
        commits={[]}
        emptyReason="No commit has been recorded for this repository yet. Its commits appear once a scan reads the directory with the git CLI."
      />,
    )

    expect(screen.getByText(NOT_ENOUGH_DATA_TITLE)).toBeInTheDocument()
    expect(
      screen.getByText(
        'No commit has been recorded for this repository yet. Its commits appear once a scan reads the directory with the git CLI.',
      ),
    ).toBeInTheDocument()
  })
})

describe('TimelineScopeNote', () => {
  it('says when the visible rows are the whole history and when they are a position in it', () => {
    const complete = renderComponent(<TimelineScopeNote total={12} shown={12} />)
    expect(screen.getByText('Every commit a scan has recorded is shown here.')).toBeInTheDocument()
    complete.unmount()

    renderComponent(<TimelineScopeNote total={1284} shown={25} />)
    expect(
      screen.getByText(
        `Showing ${formatNumber(25)} of ${formatNumber(1284)} recorded commits. This is a ` +
          'position in the list, not the whole history.',
      ),
    ).toBeInTheDocument()
  })
})

/* ---------------------------------------------------------------- branches */

describe('BranchList', () => {
  it('keeps "checked out" and "default" apart, because a detached HEAD has one and not the other', () => {
    renderComponent(
      <BranchList
        branches={[
          branch({ id: 'b1', name: 'main', is_current: false, is_default: true }),
          branch({ id: 'b2', name: 'feature/git-scan', is_current: true, is_default: false }),
        ]}
      />,
    )

    expect(screen.getByText('2 branches recorded at the last scan.')).toBeInTheDocument()
    // On a detached HEAD exactly one of the two is true. A combined chip would
    // have to hide one and lose the difference, which is the state a reader most
    // needs to be told about.
    expect(screen.getAllByText('Default')).toHaveLength(1)
    expect(screen.getAllByText('Checked out')).toHaveLength(1)
    expect(screen.getByText('main')).toBeInTheDocument()
    expect(screen.getByText('feature/git-scan')).toBeInTheDocument()
  })

  it('states a missing head rather than rendering an empty cell or a zero', () => {
    renderComponent(
      <BranchList
        branches={[
          branch({ head_commit_hash: null, last_committed_at: null, is_current: false, is_default: false }),
        ]}
      />,
    )

    expect(screen.getByText('Head commit not recorded')).toBeInTheDocument()
    expect(screen.getByText('Head commit not dated by git')).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/undefined/)
  })

  it('explains an empty branch list rather than drawing a zero', () => {
    renderComponent(<BranchList branches={[]} />)

    expect(screen.getByText(NOT_ENOUGH_DATA_TITLE)).toBeInTheDocument()
    expect(
      screen.getByText(
        'Branches are read from the repository at scan time. A repository with no commits ' +
          'has no branches to list yet, and one on a detached HEAD still has them.',
      ),
    ).toBeInTheDocument()
  })
})

/* ----------------------------------------------------------------- charts */

describe('DeveloperActivitySection', () => {
  it('plots every bucket in the window, including the ones that recorded nothing', async () => {
    const { container } = renderComponent(
      <DeveloperActivitySection activity={ACTIVITY} granularity="day" />,
    )
    await resolveCharts(container)

    expect(await screen.findByRole('heading', { name: 'Recorded commit activity' })).toBeInTheDocument()
    expect(chartSurface(container)).not.toBeNull()

    // The buckets are dense by contract: a quiet Tuesday arrives with
    // `commits: 0` rather than being skipped. Filtering it out here would
    // compress the timeline and make a sparse fortnight look as dense as a busy
    // one — a misreading of the data, not a presentation choice.
    for (const bucket of ACTIVITY.buckets) {
      expect(await screen.findByText(bucketLabel(bucket.bucket_start))).toBeInTheDocument()
    }

    expect(screen.getByText('13 commits recorded across 3 day buckets.')).toBeInTheDocument()
    expectNoFabricatedNumbers()
  })

  it('treats a window with no commits as a measured zero, stated in words', async () => {
    const { container } = renderComponent(
      <DeveloperActivitySection activity={EMPTY_ACTIVITY} granularity="day" />,
    )
    await resolveCharts(container)

    // A quiet stretch is a fact, not missing data, so the chart body is replaced
    // by an explanation — and by no axis at all, because a zero-height chart
    // says nothing the sentence does not say better.
    expect(chartSurface(container)).toBeNull()
    expect(
      await screen.findByText(
        'No commit was recorded anywhere during the last 30 days. Every bucket in that range ' +
          'was still read, and each is a recorded zero — a quiet stretch is a fact, not missing data.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('0 commits recorded across 3 day buckets.')).toBeInTheDocument()
    expectNoFabricatedNumbers()
  })

  it('names the repository it was narrowed to, because "everything" and "this one" differ', async () => {
    const { container } = renderComponent(
      <DeveloperActivitySection
        activity={{ ...ACTIVITY, repository_id: REPO_ID }}
        granularity="week"
        scopeLabel="Nexo"
      />,
    )
    await resolveCharts(container)

    expect(
      await screen.findByText(/One point per week across the last 30 days for Nexo\./),
    ).toBeInTheDocument()
    expect(
      screen.getByText('13 commits recorded across 3 week buckets for Nexo.'),
    ).toBeInTheDocument()
  })
})

describe('LanguageBreakdown', () => {
  it('names every recognised language and offers no other bucket at all', async () => {
    const { container } = renderComponent(
      <LanguageBreakdown
        languages={[
          { language: 'TypeScript', count: 2, unit: 'repositories' },
          { language: 'Python', count: 1, unit: 'repositories' },
        ]}
      />,
    )
    await resolveCharts(container)

    expect(await screen.findByText('TypeScript')).toBeInTheDocument()
    expect(await screen.findByText('Python')).toBeInTheDocument()
    expect(chartSurface(container)).not.toBeNull()

    // A repository whose tracked extensions the scan did not recognise
    // contributes nothing, because a bucket of unrecognised extensions is a
    // category rather than a language.
    expect(screen.queryByText('Other')).toBeNull()
    expectNoFabricatedNumbers()
  })

  it('explains an empty breakdown rather than drawing a bar of nothing', async () => {
    const { container } = renderComponent(<LanguageBreakdown languages={[]} />)
    await resolveCharts(container)

    expect(chartSurface(container)).toBeNull()
    expect(
      await screen.findByText(
        'Languages are counted from the file extensions git tracks. A repository whose files ' +
          'use extensions outside the recognised set contributes nothing here — there is no ' +
          '"other" bucket, because that would be a category rather than a language.',
      ),
    ).toBeInTheDocument()
  })
})

/* ------------------------------------------------------------ cards and rows */

describe('RepositoryCard', () => {
  it('shows what the repository recorded, and links to its own page', () => {
    renderComponent(
      <RepositoryCard
        repository={repository()}
        href={`/developer/${REPO_ID}`}
        projectName="NEXUS"
        projectHref={`/projects/${PROJECT_ID}`}
        recentCommitCount={18}
        recentWindowDays={30}
      />,
    )

    expect(screen.getByRole('heading', { name: 'Nexo' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Nexo' })).toHaveAttribute('href', `/developer/${REPO_ID}`)
    expect(screen.getByText('TypeScript')).toBeInTheDocument()
    expect(screen.getByText('Scanned')).toBeInTheDocument()
    expect(screen.getByText(formatNumber(1284))).toBeInTheDocument()
    expect(
      screen.getByText('3 branches recorded at the last scan, default branch main.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Some commits recorded')).toBeInTheDocument()
    expect(screen.getByText('18 commits recorded in the last 30 days.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'NEXUS' })).toHaveAttribute('href', `/projects/${PROJECT_ID}`)

    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/productiv|focus|effort|hours spent/i)
    expectNoFabricatedNumbers()
  })

  it('renders a failed scan as a row carrying a sentence, not as a broken card', () => {
    renderComponent(
      <RepositoryCard
        repository={repository({
          last_scan_status: 'error',
          last_scan_error: SCAN_FAILURE,
          latest_commit_at: null,
          commit_count: 0,
        })}
        recentCommitCount={0}
        recentWindowDays={30}
      />,
    )

    // A broken repository is a row on the page. It is not a 500, and it does not
    // remove the repository from the grid.
    expect(screen.getByText('Scan failed')).toBeInTheDocument()
    expect(screen.getByText(SCAN_FAILURE)).toBeInTheDocument()
    expect(screen.getByText('No commits yet')).toBeInTheDocument()
    expect(screen.getByText('No commits recorded')).toBeInTheDocument()
    // A zero window count is a measurement, not an absence, so it keeps its `0`.
    expect(screen.getByText('0 commits recorded in the last 30 days.')).toBeInTheDocument()
    expectNoFabricatedNumbers()
  })

  it('distinguishes an empty repository from a detached HEAD', () => {
    // Both carry `current_branch: null`, and they are different states: one has
    // nothing to check out, the other is parked at a commit. Collapsing them
    // would tell a reader their repository is broken when it is checked out at a
    // commit.
    const empty = renderComponent(
      <RepositoryCard repository={repository({ current_branch: null, commit_count: 0 })} />,
    )
    expect(screen.getByText('No branch yet — this repository has no commits.')).toBeInTheDocument()
    empty.unmount()

    const detached = renderComponent(
      <RepositoryCard repository={repository({ current_branch: null, commit_count: 1284 })} />,
    )
    expect(screen.getByText('Detached HEAD — no branch is checked out.')).toBeInTheDocument()
    detached.unmount()

    expect(describeCurrentBranch(repository())).toBe('main')
  })

  it('reads a zero-file commit as a recorded zero, in words', () => {
    expect(commitFilesPhrase(0)).toBe('no files recorded')
    expect(commitFilesPhrase(1)).toBe('1 file changed')
    expect(commitFilesPhrase(3)).toBe('3 files changed')
  })
})

/* ---------------------------------------------------------------- the guard */

describe('the developer component library', () => {
  it('fetches nothing of its own, so a card renders from a literal and a provider', () => {
    const calls = installBackend()

    renderComponent(
      <div>
        <DeveloperSummaryTiles
          summary={{
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
          }}
          buildHref={(tile) => (tile.key === 'active_days' ? '/developer?focus=active_days' : null)}
        />
        <RepositoryCard repository={repository()} href={`/developer/${REPO_ID}`} />
        <CommitTimeline commits={[commit(), commit({ id: 'c2', short_hash: 'bbccddee' })]} />
        <BranchList branches={[branch()]} />
        <ScanRunList runs={[scanRun()]} />
        <DeveloperMetricList metrics={METRICS} />
      </div>,
    )

    // Presentational by construction: query state, windowing and granularity live
    // in `hooks.ts`, which another layer owns. A component that started fetching
    // would make every page suite a lie about what it is mounting.
    expect(calls).toHaveLength(0)

    expect(
      screen.getByText('18 commits were recorded in the last 30 days across 2 repositories.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Days with a commit/ })).toHaveAttribute(
      'href',
      '/developer?focus=active_days',
    )
    expect(screen.getByText('2 commits shown, newest first.')).toBeInTheDocument()
    expect(screen.getByText('1 branch recorded at the last scan.')).toBeInTheDocument()
    expect(
      screen.getByText(`${formatNumber(1284)} commits discovered, ${formatNumber(1284)} added, 3 branches seen.`),
    ).toBeInTheDocument()
    expectNoFabricatedNumbers()
  })

  it('refuses to print a row of zeroes on an account that has recorded nothing', () => {
    installBackend()
    renderComponent(
      <DeveloperSummaryTiles
        summary={{
          repository_count: 0,
          active_repository_count: 0,
          commit_count: 0,
          commits_in_window: 0,
          active_days: 0,
          change_volume: 0,
          repositories_touched: 0,
          window_days: 30,
          window_start: '2019-01-01T00:00:00Z',
          window_end: '2019-01-30T23:59:59Z',
          latest_commit_at: null,
          last_scanned_at: null,
          has_data: false,
          summary: 'No commits have been recorded yet.',
        }}
      />,
    )

    // Six zeroes across the top of an empty account looks like a measurement,
    // and it is the opposite of one.
    expect(screen.getByText('No repositories registered yet')).toBeInTheDocument()
    expect(screen.queryByText('Days with a commit')).toBeNull()
    expect(document.body.textContent).not.toMatch(/NaN|Infinity|undefined/)
  })
})