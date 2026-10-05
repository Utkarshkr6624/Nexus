import type { ReactElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/tooltip'
import DeveloperPage from '@/pages/developer-page'
import DeveloperRepositoryPage from '@/pages/developer-repository-page'
import { NO_VALUE, formatNumber } from '@/features/analytics/format'
import { queryRetryPolicy } from '@/app/query-client'
import type { ApiErrorEnvelope } from '@/types/api'
import {
  type BranchListRead,
  type BranchRead,
  type CommitListRead,
  type CommitRead,
  type DeveloperActivityRead,
  type DeveloperSummaryRead,
  type RepositoryListRead,
  type RepositoryRead,
  type ScanRunRead,
  type UUIDString,
} from '@/types/developer'
import type { Project } from '@/types/work'

/**
 * The repository detail page, asserted at the network boundary.
 *
 * Mounted for real — real router, real components, real hooks — with only `fetch`
 * stubbed, and the same local `QueryClient` harness `developer-page.test.tsx`
 * uses. The shared singleton is not used because `AppProviders` registers
 * `onSessionChange(() => queryClient.clear())` (`src/app/auth-bootstrap.tsx:13`)
 * and in jsdom that clear lands mid-test and strands every component at
 * `pending`. The retry policy is `src/app/query-client.ts`'s own
 * `queryRetryPolicy`, not a copy of it — which is why the 5xx case below waits
 * with `{ timeout: 20_000 }`.
 *
 * Three claims the specification makes by name are pinned here.
 *
 * - **A broken repository is a row, not an exception.** The last scan failed, so
 *   the page renders a panel carrying the recorded sentence. `POST .../scan`
 *   answers 200 with `status: 'error'` whether or not git could read the
 *   directory, so there is no error path for a bad repository to travel down.
 * - **Another account's repository is a 404, and a 404 is an answer.** The page
 *   gets its own empty state with a way back rather than a retry button that
 *   could never succeed — and the request carries no user id, because ownership
 *   is the server's alone.
 * - **Absence of measurement is `—`, and a real zero is `0`.** The change
 *   figures fall back to dashes when the series could not be read, and the
 *   whole-history commit count keeps its digits either way.
 *
 * Recharts is given a size and only a size, so the charts draw rather than
 * collapsing to an empty box, and nothing below reaches into a recharts
 * internal. The dates are fixed in 2019 — a completed year — and nothing here
 * asserts on a relative age, which would depend on the machine's clock.
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
  detail?: Route
  activity?: Route
  commits?: Route
  branches?: Route
  scan?: Route
  project?: Route
  repositories?: Route
  summary?: Route
  metrics?: Route
  projects?: Route
  remove?: Route
}

/**
 * Stubs `fetch` with the routing table below, so one test can replace a single
 * endpoint without restating the rest. Literal sub-paths are matched before
 * `/developer/repositories/{id}`, which would otherwise swallow them.
 */
function installBackend(overrides: Backend = {}): Call[] {
  const detail = overrides.detail ?? (() => json(REPOSITORY))
  const activity = overrides.activity ?? (() => json(ACTIVITY))
  const commits = overrides.commits ?? (() => json(COMMITS))
  const branches = overrides.branches ?? (() => json(BRANCHES))
  const scan = overrides.scan ?? (() => json(SCAN_RUN))
  const project = overrides.project ?? (() => json(PROJECT))
  const repositories = overrides.repositories ?? (() => json(REPOSITORY_LIST))
  const summary = overrides.summary ?? (() => json(SUMMARY))
  const metrics = overrides.metrics ?? (() => json([]))
  const projects = overrides.projects ?? (() => json({ items: [], total: 0, limit: 100, offset: 0 }))
  const remove = overrides.remove ?? (() => new Response(null, { status: 204 }))

  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      calls.push({ url, method })
      const { pathname } = new URL(url, 'http://test')

      if (method === 'DELETE' && pathname.includes('/developer/repositories/')) {
        return remove(url)
      }
      if (url.includes('/developer/activity')) return activity(url)
      if (url.includes('/scan')) return scan(url)
      if (url.includes('/commits')) return commits(url)
      if (url.includes('/branches')) return branches(url)
      if (url.includes('/developer/repositories/')) return detail(url)
      if (pathname.endsWith('/developer/repositories')) return repositories(url)
      if (pathname.endsWith('/developer/summary')) return summary(url)
      if (pathname.endsWith('/developer/metrics')) return metrics(url)
      if (url.includes('/projects/')) return project(url)
      if (pathname.endsWith('/projects')) return projects(url)
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

function renderRepositoryPage(repositoryId: UUIDString = REPO_ID) {
  const router = createMemoryRouter(
    [
      { path: '/developer/:repositoryId', element: <DeveloperRepositoryPage /> },
      { path: '/developer', element: <DeveloperPage /> },
    ],
    { initialEntries: [`/developer/${repositoryId}`] },
  )
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
 * string, and the specification names all three.
 */
function expectNoFabricatedNumbers(): void {
  const text = document.body.textContent ?? ''
  expect(text).not.toMatch(/NaN/)
  expect(text).not.toMatch(/Infinity/)
  expect(text).not.toMatch(/undefined/)
}

/**
 * A paragraph whose **whole** text is `text`.
 *
 * Several sentences on this page are split across a bold lead-in `<span>` and the
 * words that follow it, so `getByText` with a string only ever sees the fragments
 * and never the sentence a reader actually gets.
 */
function paragraph(text: string): HTMLElement {
  return screen.getByText((_, element) => {
    if (element?.tagName !== 'P') return false
    return (element.textContent ?? '').trim() === text
  })
}

/** The `dt`/`dd` pair a figure is rendered as, found through its label. */
function factRow(label: string): HTMLElement {
  return screen.getByText(label).closest('div') as HTMLElement
}

/** An absolute instant, formatted the way the formatters under test do it. */
function instant(iso: string): string {
  return new Intl.DateTimeFormat(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(iso))
}

function callsTo(calls: Call[], method: string, needle: string): Call[] {
  return calls.filter((call) => call.method === method && call.url.includes(needle))
}

/**
 * The one request that names the repository and nothing else.
 *
 * Matched on the whole path rather than by substring: `/commits` and `/branches`
 * hang off the same path, so a substring match would count three reads as one.
 */
function detailCalls(calls: Call[]): Call[] {
  const path = `/api/v1/developer/repositories/${REPO_ID}`
  return calls.filter(
    (call) =>
      call.method === 'GET' && new URL(call.url, 'http://test').pathname === path,
  )
}

/* ---------------------------------------------------------------- fixtures */

function repository(overrides: Partial<RepositoryRead> = {}): RepositoryRead {
  return {
    id: REPO_ID,
    name: 'Nexo',
    local_path: '/home/ada/code/nexo',
    description: 'The NEXUS work tree.',
    primary_language: 'TypeScript',
    project_id: null,
    is_active: true,
    current_branch: 'main',
    default_branch: 'main',
    branch_count: 2,
    commit_count: 1284,
    first_commit_at: '2018-02-04T09:14:00Z',
    latest_commit_at: '2019-01-29T14:05:00Z',
    working_tree_dirty: true,
    last_scanned_at: '2019-01-29T14:06:00Z',
    last_scan_status: 'ok',
    last_scan_error: null,
    created_at: '2018-02-01T08:00:00Z',
    updated_at: '2019-01-29T14:06:00Z',
    ...overrides,
  }
}

const REPOSITORY = repository()

const UNSCANNED_REPOSITORY = repository({
  description: null,
  primary_language: null,
  current_branch: null,
  default_branch: null,
  branch_count: 0,
  commit_count: 0,
  first_commit_at: null,
  latest_commit_at: null,
  working_tree_dirty: false,
  last_scanned_at: null,
  last_scan_status: 'ok',
})

function repositoryList(
  items: RepositoryRead[],
  overrides: Partial<RepositoryListRead> = {},
): RepositoryListRead {
  return { items, total: items.length, limit: 12, offset: 0, ...overrides }
}

const REPOSITORY_LIST = repositoryList([REPOSITORY])

const SUMMARY: DeveloperSummaryRead = {
  repository_count: 1,
  active_repository_count: 1,
  commit_count: REPOSITORY.commit_count,
  commits_in_window: 0,
  active_days: 0,
  change_volume: 0,
  repositories_touched: 0,
  window_days: 30,
  window_start: '2019-01-01T00:00:00Z',
  window_end: '2019-01-30T23:59:59Z',
  latest_commit_at: REPOSITORY.latest_commit_at,
  last_scanned_at: REPOSITORY.last_scanned_at,
  has_data: false,
  summary: 'A repository is registered, but no scan has read one yet.',
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

const COMMITS: CommitListRead = { items: [commit()], total: 1, limit: 50, offset: 0 }

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

/**
 * Two branches, only one of them checked out — which is the detached-HEAD-shaped
 * pair the surface keeps apart rather than merging into a single chip.
 */
const BRANCHES: BranchListRead = {
  items: [
    branch(),
    branch({
      id: '77777777-7777-4777-8777-777777777777',
      name: 'feature/git-scan',
      is_current: false,
      is_default: false,
      head_commit_hash: null,
      last_committed_at: null,
    }),
  ],
  total: 2,
  limit: 100,
  offset: 0,
}

/** Three dense buckets with a quiet day in the middle, which is the point. */
const ACTIVITY: DeveloperActivityRead = {
  granularity: 'day',
  window_days: 30,
  window_start: '2019-01-01T00:00:00Z',
  window_end: '2019-01-30T23:59:59Z',
  repository_id: REPO_ID,
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
      repository_count: 1,
    },
  ],
  total_commits: 13,
}

const SCAN_RUN: ScanRunRead = {
  id: '66666666-6666-4666-8666-666666666666',
  repository_id: REPO_ID,
  status: 'ok',
  commits_discovered: 1284,
  commits_added: 12,
  branches_discovered: 2,
  duration_ms: 412,
  error: null,
  scanned_at: '2019-01-29T14:06:00Z',
  created_at: '2019-01-29T14:06:00Z',
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

/* -------------------------------------------------------------------- tests */

describe('developer repository page', () => {
  it('shows what the last scan recorded about the repository', async () => {
    const calls = installBackend()
    const { container } = renderRepositoryPage()

    expect(await screen.findByRole('heading', { level: 1, name: 'Nexo' })).toBeInTheDocument()
    await resolveCharts(container)

    expect(screen.getByText('The NEXUS work tree.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Developer' })).toHaveAttribute('href', '/developer')
    // The most common tracked extension, announced twice on purpose: once as the
    // chip in the masthead, and once as the single language the scan recognised.
    // The distribution is not retained per repository, so it is never drawn as a
    // chart of one bar.
    expect(screen.getAllByText('TypeScript')).toHaveLength(2)
    expect(screen.getByText('Most common tracked language')).toBeInTheDocument()
    // The scan state is announced twice as well: the chip in the masthead and the
    // chip in the scan panel, so a reader who scrolls never loses it.
    expect(screen.getAllByText('Scanned')).toHaveLength(2)

    // The facts card reports the directory, the branch, the two commit dates and
    // the working tree exactly as the scan read them.
    expect(screen.getByText('/home/ada/code/nexo')).toBeInTheDocument()
    // `main` is also the commit's branch and the branch row's name, so the fact
    // is read out of its own labelled block rather than off the page.
    const branchFact = screen.getByText('Branch checked out').closest('div') as HTMLElement
    expect(within(branchFact).getByText('main')).toBeInTheDocument()
    expect(
      within(branchFact).getByText('Git resolved main as the default branch.'),
    ).toBeInTheDocument()
    // Scoped to the facts card: the same instants are titled on the commit row
    // and on the branch row, and those are separate measurements.
    const facts = screen.getByText('First commit').closest('div.rounded-lg') as HTMLElement
    expect(within(facts).getByTitle(instant('2018-02-04T09:14:00Z'))).toBeInTheDocument()
    expect(within(facts).getByTitle(instant('2019-01-29T14:05:00Z'))).toBeInTheDocument()
    expect(screen.getByText('Uncommitted changes were present when the scan ran.')).toBeInTheDocument()

    // Nothing here re-reads the repository on its own, so an old scan is
    // disclosed rather than presented as current.
    expect(
      screen.getByText(
        `The last scan is more than ${formatNumber(24)} hours old. Nothing here re-reads a repository on its own, so these figures are as of that scan rather than as of now — run a scan to bring them forward.`,
      ),
    ).toBeInTheDocument()

    // The series is narrowed to this repository, and says so.
    expect(
      await screen.findByRole('heading', { name: 'Recorded activity in this repository' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/One point per day across the last 30 days for Nexo\./)).toBeInTheDocument()
    expect(container.querySelector('[data-chart-surface]')).not.toBeNull()

    // The commit history and the branch list are the evidence itself.
    expect(screen.getByText('Record the scan outcome as data rather than an exception')).toBeInTheDocument()
    expect(screen.getByText('+128 / −12')).toBeInTheDocument()
    expect(screen.getByText('2 branches recorded at the last scan.')).toBeInTheDocument()
    expect(screen.getAllByText('Default')).toHaveLength(1)
    expect(screen.getAllByText('Checked out')).toHaveLength(1)
    expect(screen.getByText('Head commit not recorded')).toBeInTheDocument()
    expect(screen.getByText('Head commit not dated by git')).toBeInTheDocument()

    // Ownership is the server's alone: the request names the repository and
    // nothing else.
    const detail = detailCalls(calls)
    expect(detail).toHaveLength(1)
    expect(detail[0]?.url).not.toContain('user_id')
    expect(detail[0]?.url).not.toContain('owner')
    expectNoFabricatedNumbers()
  })

  it('sums the change statistics from the dense buckets, keeping a real zero as 0', async () => {
    installBackend()
    const { container } = renderRepositoryPage()

    await screen.findByRole('heading', { level: 1, name: 'Nexo' })
    await resolveCharts(container)

    // 7 + 0 + 11 files, 220 + 0 + 480 additions, 30 + 0 + 60 deletions. The
    // quiet day is counted as zero rather than skipped, so the sums cover the
    // whole window rather than the days that happened to carry a commit.
    expect(within(factRow('Files changed')).getByText(formatNumber(18))).toBeInTheDocument()
    expect(within(factRow('Lines added')).getByText(formatNumber(700))).toBeInTheDocument()
    expect(within(factRow('Lines removed')).getByText(formatNumber(90))).toBeInTheDocument()
    // Whole history is a different measurement and carries its own figure.
    expect(
      within(factRow('Commits, whole history')).getByText(formatNumber(1284)),
    ).toBeInTheDocument()
    expect(
      screen.getByText('13 commits recorded across 3 day buckets for Nexo.'),
    ).toBeInTheDocument()
  })

  it('renders a nullable figure as a dash, never as a zero', async () => {
    // A 404 arrives on the first response — the shared policy refuses to retry a
    // 4xx — so the change statistics have no series to sum and must say so.
    installBackend({
      activity: () => envelope('not_found', 'No activity series for this window.', 404, 'req-activity-404'),
    })
    renderRepositoryPage()

    await screen.findByRole('heading', { level: 1, name: 'Nexo' })

    for (const label of ['Files changed', 'Lines added', 'Lines removed']) {
      expect(within(factRow(label)).getByText(NO_VALUE)).toBeInTheDocument()
      expect(within(factRow(label)).queryByText('0')).toBeNull()
    }
    // The whole-history figure is unaffected: a missing series is not a missing
    // repository, and the commit count was already recorded.
    expect(
      within(factRow('Commits, whole history')).getByText(formatNumber(1284)),
    ).toBeInTheDocument()
    expectNoFabricatedNumbers()
  })

  it('renders a failed scan as a panel carrying the recorded sentence, never a traceback', async () => {
    installBackend({
      detail: () =>
        json(
          repository({
            last_scan_status: 'error',
            last_scan_error: SCAN_FAILURE,
          }),
        ),
    })
    const { container } = renderRepositoryPage()

    expect(await screen.findByRole('heading', { level: 1, name: 'Nexo' })).toBeInTheDocument()
    await resolveCharts(container)

    // The whole page still renders. A broken repository must never break NEXUS,
    // so the failure is a row with a sentence rather than an exception.
    expect(screen.getAllByText('Scan failed')).toHaveLength(2)
    expect(screen.getByText(SCAN_FAILURE)).toBeInTheDocument()
    expect(screen.getByText('The last scan could not read this repository')).toBeInTheDocument()
    expect(screen.getByText(/Nothing else is affected\./)).toBeInTheDocument()
    // The history is still listed: a failed scan reads nothing new, it does not
    // remove what a previous scan already recorded.
    expect(screen.getAllByRole('heading', { name: 'Commit history' })).toHaveLength(2)
    expect(screen.getByText('1 commit shown, newest first.')).toBeInTheDocument()

    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/Traceback/)
    expect(text).not.toMatch(/File "|line \d+, in/)
    expectNoFabricatedNumbers()
  })

  it('records a failed scan attempt as data, so the button never throws', async () => {
    const user = userEvent.setup()
    const calls = installBackend({
      scan: () =>
        json({
          ...SCAN_RUN,
          status: 'error',
          commits_discovered: 0,
          commits_added: 0,
          branches_discovered: 0,
          error: SCAN_FAILURE,
        }),
    })
    renderRepositoryPage()

    await screen.findByRole('heading', { level: 1, name: 'Nexo' })
    await user.click(screen.getByRole('button', { name: 'Scan now' }))

    // `POST .../scan` answers 200 whether or not git could read the directory, so
    // a failure resolves rather than rejects — there is no error path here for a
    // bad repository to travel down.
    expect(await screen.findByRole('heading', { name: 'Scan you just ran' })).toBeInTheDocument()
    expect(screen.getByText('0 commits discovered, 0 added, 0 branches seen.')).toBeInTheDocument()
    expect(screen.getByText(SCAN_FAILURE)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Scan now' })).toBeInTheDocument()

    const posts = callsTo(calls, 'POST', '/scan')
    expect(posts).toHaveLength(1)
    expect(posts[0]?.url).toContain(`/developer/repositories/${REPO_ID}/scan`)

    // The scan run explains its own gap: 1,284 discovered against 12 added is the
    // deduplication doing its job, and saying so is what stops it reading as loss.
    expectNoFabricatedNumbers()
  })

  it('explains the gap between a scan run\'s discovered and added counts', async () => {
    const user = userEvent.setup()
    installBackend()
    renderRepositoryPage()

    await screen.findByRole('heading', { level: 1, name: 'Nexo' })
    await user.click(screen.getByRole('button', { name: 'Scan now' }))

    expect(await screen.findByRole('heading', { name: 'Scan you just ran' })).toBeInTheDocument()
    expect(
      screen.getByText(`${formatNumber(1284)} commits discovered, 12 added, 2 branches seen.`),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        'Fewer commits were added than discovered because the ones already recorded ' +
          'were recognised and left alone — re-scanning does not duplicate history.',
      ),
    ).toBeInTheDocument()
  })

  it('renders a 404 as its own empty state, with a way back and no retry', async () => {
    installBackend({
      detail: () => envelope('not_found', 'Repository not found.', 404, 'req-repo-404'),
    })
    renderRepositoryPage()

    // Another account's repository is a 404, never a 403, and ownership is the
    // server's alone. Both a removed row and someone else's answer the same way,
    // so this screen says so and offers a way out instead of a retry button that
    // could never succeed.
    expect(await screen.findByText('That repository is not here')).toBeInTheDocument()
    expect(
      screen.getByText(
        'It may have been removed, or it may belong to another account. Both answer the same way, so there is nothing further to check.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to developer intelligence' })).toHaveAttribute(
      'href',
      '/developer',
    )
    expect(screen.queryByRole('button', { name: /Retry/i })).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
    expectNoFabricatedNumbers()
  })

  it('shows a loading silhouette carrying no digits while the row is being read', () => {
    const never = (): Promise<Response> => new Promise(() => undefined)
    installBackend({ detail: never, activity: never, commits: never, branches: never })
    const { container } = renderRepositoryPage()

    // The skeleton is the whole page: a repository with no commits yet has
    // figures of zero, and a grey `0` there would be a number the page does not
    // have. Scoped to the render container because recharts leaves its own
    // `recharts_measurement_span` in `document.body` — a library internal, not
    // anything this page printed.
    expect(container.textContent ?? '').not.toMatch(/\d/)
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(5)
    expect(screen.queryByRole('heading', { level: 1, name: 'Nexo' })).toBeNull()
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull()
  })

  it('says a repository that has never been scanned is unscanned, with nothing to show', async () => {
    // The payload is `UNSCANNED_REPOSITORY` — `last_scan_status: 'ok'` beside a
    // null `last_scanned_at`, which is what the server actually sends for a
    // registered repository. Reading the status instead of the timestamp is what
    // let this page narrate a scan that had not happened.
    installBackend({
      detail: () => json(UNSCANNED_REPOSITORY),
      activity: () => json({ ...ACTIVITY, repository_id: REPO_ID, buckets: [], total_commits: 0 }),
      commits: () => json({ items: [], total: 0, limit: 50, offset: 0 }),
      branches: () => json({ items: [], total: 0, limit: 100, offset: 0 }),
    })
    const { container } = renderRepositoryPage()

    expect(await screen.findByRole('heading', { level: 1, name: 'Nexo' })).toBeInTheDocument()
    await resolveCharts(container)

    expect(screen.getAllByText('Never scanned')).toHaveLength(2)
    expect(
      screen.getByText(
        'This repository has not been scanned yet, so there are no commits, branches or activity ' +
          'figures to show. Its commits appear once a scan reads the directory.',
      ),
    ).toBeInTheDocument()

    // Nothing here may describe what a scan found, because no scan has run. The
    // sentences below would each be a claim about a repository nobody has read.
    expect(screen.getByText('Not read yet — no scan has looked inside this repository.')).toBeInTheDocument()
    expect(
      screen.getByText('No scan has run, so nothing has asked git which branch is checked out.'),
    ).toBeInTheDocument()
    expect(
      screen.getByText('Not read yet — a scan records the first commit when it reads the directory.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Not read yet — no scan has recorded a commit.')).toBeInTheDocument()
    expect(screen.getByText('Unknown — no scan has looked at it.')).toBeInTheDocument()

    const text = document.body.textContent ?? ''
    expect(text).not.toContain('No branch yet — this repository has no commits.')
    expect(text).not.toContain('when the scan ran')

    // A whole-history commit count is still a real figure, and a real zero is a
    // measurement: NEXUS has read the repository and it holds no commits yet.
    expect(
      within(factRow('Commits, whole history')).getByText(formatNumber(0)),
    ).toBeInTheDocument()

    // The two lists explain themselves rather than rendering empty boxes.
    expect(
      screen.getByText(
        'No commit has been recorded for this repository yet. Its commits appear once a scan reads the directory with the git CLI.',
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        'No scan has read this repository, so git has not been asked which branches it has. Run a scan and they appear here.',
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        'No commit was recorded in Nexo during the last 30 days. Every bucket in that range was ' +
          'still read, and each is a recorded zero — a quiet stretch is a fact, not missing data.',
      ),
    ).toBeInTheDocument()
    expect(container.querySelector('[data-chart-surface]')).toBeNull()
    expectNoFabricatedNumbers()
  })

  it('tells a repository a scan read and found empty apart from one never read', async () => {
    // The other half of the same pair: `last_scanned_at` is set, so every figure
    // here *was* measured — and the page is allowed to describe what the scan
    // found. Both rows are all-zero and both arrived from the same endpoint; only
    // the timestamp separates them, which is exactly why the copy may not be
    // shared.
    installBackend({
      detail: () =>
        json(
          repository({
            primary_language: null,
            current_branch: null,
            default_branch: null,
            branch_count: 0,
            commit_count: 0,
            first_commit_at: null,
            latest_commit_at: null,
            working_tree_dirty: false,
            last_scan_status: 'ok',
            last_scanned_at: '2019-01-29T14:06:00Z',
          }),
        ),
      activity: () => json({ ...ACTIVITY, repository_id: REPO_ID, buckets: [], total_commits: 0 }),
      commits: () => json({ items: [], total: 0, limit: 50, offset: 0 }),
      branches: () => json({ items: [], total: 0, limit: 100, offset: 0 }),
    })
    const { container } = renderRepositoryPage()

    expect(await screen.findByRole('heading', { level: 1, name: 'Nexo' })).toBeInTheDocument()
    await resolveCharts(container)

    // A scan ran, so the scan-shaped language is true and is used.
    expect(screen.getByText(/^Scanned /)).toBeInTheDocument()
    expect(screen.queryAllByText('Never scanned')).toHaveLength(0)
    expect(
      screen.getByText(
        'Read from the local work tree at its last scan. Nothing here is re-read on its own.',
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText('The branches the last scan observed. Attribution is reported as git reported it: a commit whose branch could not be resolved is not filed under one, and a detached HEAD leaves no branch marked as checked out.'),
    ).toBeInTheDocument()

    // An empty repository says so in words rather than rendering a zero date or a
    // zero count that would read as a measurement.
    expect(screen.getByText('No branch yet — this repository has no commits.')).toBeInTheDocument()
    expect(
      screen.getByText('No commits yet, so there is no first commit to date.'),
    ).toBeInTheDocument()
    expect(screen.getByText('No commits recorded yet.')).toBeInTheDocument()
    expect(
      screen.getByText('Git resolved no default branch — there is nothing for one to point at yet.'),
    ).toBeInTheDocument()
    expect(
      screen.getByText('No uncommitted changes were present when the scan ran.'),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        'No tracked language was recognised. Either the repository tracks no files, or every file it tracks uses an extension outside the recognised set — which is reported as nothing rather than as a category of its own.',
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        'No branch was recorded for this repository. A repository with no commits has no branches yet, and one on a detached HEAD still has them.',
      ),
    ).toBeInTheDocument()

    // A branch count *is* a measurement here, so it keeps its digits: zero
    // branches is what the scan found, not the absence of one.
    expect(within(factRow('Branches recorded')).getByText(formatNumber(0))).toBeInTheDocument()
    expect(
      within(factRow('Commits, whole history')).getByText(formatNumber(0)),
    ).toBeInTheDocument()
    expectNoFabricatedNumbers()
  })

  it('reports an unreachable repository with a retry that asks again and recovers', async () => {
    const user = userEvent.setup()
    let failing = true
    installBackend({
      detail: () =>
        failing
          ? envelope('internal_error', 'The repository service is unavailable.', 500, 'req-repo-1')
          : json(REPOSITORY),
    })
    renderRepositoryPage()

    // The shared retry policy asks twice more with a backoff, so the error surface
    // cannot arrive inside the default 5 s budget.
    const alert = await screen.findByRole('alert', {}, { timeout: RETRY_SURFACE_TIMEOUT_MS })
    expect(alert).toHaveTextContent('The backend hit an unexpected error')
    expect(alert).toHaveTextContent(
      'The failure was recorded on the server. Retry, and quote the request ID below.',
    )
    expect(alert).toHaveTextContent('The repository service is unavailable.')
    expect(alert).toHaveTextContent('req-repo-1')

    // A failed read is not an empty repository and not a missing one.
    expect(screen.queryByText('That repository is not here')).toBeNull()
    expect(screen.queryByRole('heading', { level: 1, name: 'Nexo' })).toBeNull()

    failing = false
    await user.click(screen.getByRole('button', { name: /Retry/i }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Nexo' })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  })

  it('links to the project it belongs to, and says plainly when it belongs to none', async () => {
    const linked = installBackend({
      detail: () => json(repository({ project_id: PROJECT_ID })),
    })
    const withProject = renderRepositoryPage()

    expect(await screen.findByRole('link', { name: 'NEXUS' })).toHaveAttribute(
      'href',
      `/projects/${PROJECT_ID}`,
    )
    expect(
      screen.getByText('Linking a repository to a project puts its recorded commits beside the work they served.'),
    ).toBeInTheDocument()
    expect(linked.some((call) => call.url.includes(`/projects/${PROJECT_ID}`))).toBe(true)
    withProject.unmount()

    installBackend({ detail: () => json(repository({ project_id: null })) })
    renderRepositoryPage()
    expect(await screen.findByText('Nexo')).toBeInTheDocument()
    // `project_id` is null routinely: the foreign key is `ON DELETE SET NULL`, so
    // the recorded history outlives the project. That is a statement about
    // linkage and not about the repository's value.
    expect(
      screen.getByText(
        'Not linked to a project. The recorded history outlives the project it was attached to, so this is optional and can be set later.',
      ),
    ).toBeInTheDocument()
  })

  it('never claims working time, hours, focus or effort', async () => {
    installBackend()
    const { container } = renderRepositoryPage()
    await screen.findByRole('heading', { level: 1, name: 'Nexo' })
    await resolveCharts(container)

    // Scanned against the rendered page rather than against the copy that shipped
    // today, so this is a regression guard and not a restatement of it. The words
    // "streak" and "session" are *refuted* by this page's own copy rather than
    // absent from it, so they are asserted positively below instead of banned
    // here — a ban would only be satisfiable by deleting the sentences that deny
    // the claim.
    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/\d+\s*hours?\s*(focused|spent|worked|of coding|in review)/i)
    expect(text).not.toMatch(/productivity|unproductive|lazy|burnout/i)
    expect(text).not.toMatch(/you were (busy|active|focused|productive)/i)
    expectNoFabricatedNumbers()

    expect(
      screen.getByText(
        'These are counts of lines in a diff. They say nothing about how the change was made or how long it took.',
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        'Every commit the scan recorded in this repository, newest first. Each row is the record ' +
          'itself — no grouping, no streaks, and no figure for how long anything took.',
      ),
    ).toBeInTheDocument()
  })
/**
 * The two mutations on this page, and the state a repository is in before
 * either of them has run.
 *
 * **The fixture is the shape the server sends, not the shape that would be
 * convenient.** A registered-but-unread repository answers `last_scan_status:
 * 'ok'` with `last_scanned_at: null`, because `DEFAULT_GIT_SCAN_STATUS` is `ok`
 * and the enum has no third member. Reading the status to decide "has this been
 * scanned?" therefore reports that a scan happened, and the page said so: a
 * "Scanned" chip, a "Scan finished" sentence and a working-tree note about "when
 * the scan ran", for a repository git has never been asked about.
 */
describe('developer repository page: acting on the repository', () => {
  it('scans on one click and brings the figures up to date with what git reports', async () => {
    const user = userEvent.setup()
    let scanned = false
    const calls = installBackend({
      detail: () => json(scanned ? repository({ last_scanned_at: '2019-01-29T14:06:00Z' }) : REPOSITORY),
      // The row the scan leaves behind: three commits on `main` instead of the
      // 1,284 the previous scan recorded.
      scan: () => {
        scanned = true
        return json({ ...SCAN_RUN, commits_discovered: 1300, commits_added: 16, branches_discovered: 2 })
      },
    })
    renderRepositoryPage()

    // The control is labelled as the scan it is — there is no scheduler, so this
    // button *is* the scan, and a label like "Refresh" would hide that.
    const scan = await screen.findByRole('button', { name: 'Scan now' })
    expect(screen.getByText(/A scan reads the repository with the git CLI/)).toBeInTheDocument()

    await user.click(scan)

    // The scan call, on the repository and nothing else.
    await waitFor(() =>
      expect(callsTo(calls, 'POST', `/developer/repositories/${REPO_ID}/scan`)).toHaveLength(1),
    )

    // …and the view is re-read, so the page stops showing the old snapshot. The
    // invalidation has to reach the row itself: a scan run that reports 1,300
    // commits while the card still claims 1,284 is the stale-data bug wearing a
    // successful toast.
    await waitFor(() => expect(detailCalls(calls).length).toBeGreaterThan(1))
    expect(
      await screen.findByText(
        'Fewer commits were added than discovered because the ones already recorded were recognised and left alone — re-scanning does not duplicate history.',
      ),
    ).toBeInTheDocument()
  })

  it('removes a repository only after a confirmation that names it and its recorded history', async () => {
    const user = userEvent.setup()
    let rows: RepositoryRead[] = [REPOSITORY]
    const calls = installBackend({
      repositories: () => json(repositoryList(rows)),
      remove: (url) => {
        rows = rows.filter((row) => !url.endsWith(`/developer/repositories/${row.id}`))
        return new Response(null, { status: 204 })
      },
    })
    const { router } = renderRepositoryPage()

    await user.click(await screen.findByRole('button', { name: 'Remove repository' }))

    // The dialog is up, and it is specific: it names the repository and says the
    // recorded evidence goes with it, because "removed" and "removed along with
    // every commit NEXUS read from it" are not the same offer.
    const dialog = await screen.findByRole('dialog')
    expect(
      within(dialog).getByText(
        '“Nexo” and every commit, branch and scan record NEXUS read from it will be removed. ' +
          'The activity event stays in your trail. This cannot be undone.',
      ),
    ).toBeInTheDocument()

    // Cancelling is a real exit, and it costs nothing.
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(callsTo(calls, 'DELETE', `/developer/repositories/${REPO_ID}`)).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: 'Remove repository' }))
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Remove repository' }),
    )

    await waitFor(() =>
      expect(callsTo(calls, 'DELETE', `/developer/repositories/${REPO_ID}`)).toHaveLength(1),
    )

    // …and the repository is off the dashboard's grid, because the list is
    // re-read rather than left holding the row that was just deleted.
    await waitFor(() => expect(router.state.location.pathname).toBe('/developer'))
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Nexo' })).not.toBeInTheDocument(),
    )
    expect(await screen.findByText('No repositories registered yet')).toBeInTheDocument()
  })

  it('tells an unscanned repository apart from one a scan found empty', async () => {
    installBackend({
      detail: () => json(UNSCANNED_REPOSITORY),
      activity: () => json({ ...ACTIVITY, repository_id: REPO_ID, buckets: [], total_commits: 0 }),
      commits: () => json({ items: [], total: 0, limit: 50, offset: 0 }),
      branches: () => json({ items: [], total: 0, limit: 100, offset: 0 }),
    })
    const { container } = renderRepositoryPage()

    expect(await screen.findByRole('heading', { level: 1, name: 'Nexo' })).toBeInTheDocument()
    await resolveCharts(container)
    // The chip in the header says so — twice, because `PageHeader` renders its
    // badge row in both layouts. The backend's own `last_scan_status` is `'ok'`
    // here, so nothing on this page may turn that into a completed scan.
    expect(screen.getAllByText('Never scanned')).toHaveLength(2)
    expect(
      paragraph(
        'Never scanned. Registering a repository records its path and proves it is a git work ' +
          'tree; it does not open it. Run the first scan to read its commits and branches.',
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        'This repository has not been scanned yet, so there are no commits, branches or activity ' +
          'figures to show. Its commits appear once a scan reads the directory.',
      ),
    ).toBeInTheDocument()

    // Nothing on the page narrates a scan that never ran. "Scan finished" and
    // the working-tree note both describe an event, and there was no event.
    const text = document.body.textContent ?? ''
    expect(text).not.toContain('Scan finished')
    expect(text).not.toContain('when the scan ran')
    expect(text).not.toContain('Read from the local work tree at its last scan')
    expect(text).not.toContain('The branches the last scan observed')

    // The branch and the language say they are unread rather than making claims
    // about a repository nobody has looked at.
    expect(
      screen.getByText('Not read yet — no scan has looked inside this repository.'),
    ).toBeInTheDocument()
    expect(
      screen.getByText('No scan has run, so nothing has asked git which branch is checked out.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Unknown — no scan has looked at it.')).toBeInTheDocument()
    expect(
      screen.getByText(
        'Not counted yet. No scan has read this repository, so its tracked extensions have not ' +
          'been looked at — this is an absence of a measurement rather than a repository with no ' +
          'recognised language.',
      ),
    ).toBeInTheDocument()

    // A branch count is a measurement of a scan that did not happen, so it is a
    // dash rather than a zero — a zero would say the scan found no branches.
    expect(within(factRow('Branches recorded')).getByText(NO_VALUE)).toBeInTheDocument()
    expectNoFabricatedNumbers()
  })
})
})
