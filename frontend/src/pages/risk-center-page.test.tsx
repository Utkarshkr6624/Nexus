import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/tooltip'
import RiskCenterPage from '@/pages/risk-center-page'
import { isAbortError, toApiError } from '@/services/errors'
import type { ApiErrorEnvelope } from '@/types/api'
import type { RiskListRead, RiskRead, RiskStatus, RiskSummaryRead } from '@/types/risk'

/**
 * The Risk Center, asserted at the network boundary.
 *
 * The page is mounted for real — real router, real components, real hooks — and
 * only `fetch` is stubbed. Every figure on screen is therefore a body this file
 * wrote, so the counts can be checked by hand: four live risks, one critical,
 * two high, one medium, and the four band tiles say exactly that.
 *
 * **The query client is local, and that is the point.** `AppProviders` mounts the
 * shared singleton and registers `onSessionChange(() => queryClient.clear())`
 * (`src/app/auth-bootstrap.tsx:13`). In jsdom that clear lands mid-test and
 * leaves every component sitting at `pending` forever, which is why these two
 * page suites build a fresh client per render instead. The defaults below are
 * the ones in `src/app/query-client.ts`, reproduced rather than relaxed: the
 * retry policy in particular is what makes the error surface arrive after a few
 * seconds rather than on the first response.
 */

const CRITICAL_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const HIGH_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
const HIGH_TWO_ID = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
const MEDIUM_ID = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd'
const TASK_ID = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'
const PROJECT_ID = 'ffffffff-ffff-4fff-8fff-ffffffffffff'

function risk(overrides: Partial<RiskRead> & Pick<RiskRead, 'id' | 'title'>): RiskRead {
  return {
    risk_type: 'deadline',
    severity: 'high',
    score: 60,
    description:
      'The recorded work sessions before the due date cover less time than the tasks need.',
    evidence: [
      { label: 'Time needed', detail: '300 minutes of estimated work.', contribution: 34 },
      {
        label: 'Time booked',
        detail: '120 minutes of recorded work sessions fall before the due date.',
        contribution: -12.5,
      },
    ],
    evidence_strength: 'medium',
    entity_type: 'task',
    entity_id: TASK_ID,
    status: 'active',
    detected_at: '2026-01-09T08:00:00Z',
    resolved_at: null,
    metadata: {},
    recommendations: [],
    ...overrides,
  }
}

const CRITICAL = risk({
  id: CRITICAL_ID,
  risk_type: 'project',
  severity: 'critical',
  // 88 is above the backend's 75 floor for `critical`.
  score: 88,
  entity_type: 'project',
  entity_id: PROJECT_ID,
  title: 'Four open tasks have no scheduled work before the project deadline',
})

const HIGH = risk({
  id: HIGH_ID,
  title: 'Two tasks are due in the next two days with less time booked than they need',
})

const HIGH_TWO = risk({
  id: HIGH_TWO_ID,
  risk_type: 'workload',
  // A finding about the account as a whole, which is how a workload risk can be
  // live without colliding with the task-keyed deduplication index.
  entity_type: null,
  entity_id: null,
  score: 54,
  title: 'Planned time for the next two weeks exceeds the declared hours by 480 minutes',
})

const MEDIUM = risk({
  id: MEDIUM_ID,
  risk_type: 'estimation',
  severity: 'medium',
  score: 31,
  title: 'Five completed tasks ran about 45% over their recorded estimates',
})

/** Most severe first, which is the order `GET /risks` answers in. */
const RISKS = [CRITICAL, HIGH, HIGH_TWO, MEDIUM]

const SUMMARY: RiskSummaryRead = {
  critical: 1,
  high: 2,
  medium: 1,
  low: 0,
  total: 4,
  needs_attention: true,
}

const EMPTY_SUMMARY: RiskSummaryRead = {
  critical: 0,
  high: 0,
  medium: 0,
  low: 0,
  total: 0,
  needs_attention: false,
}

function listPage(items: RiskRead[]): RiskListRead {
  const bySeverity: Record<string, number> = { critical: 0, high: 0, medium: 0, low: 0 }
  for (const item of items) {
    bySeverity[item.severity] = (bySeverity[item.severity] ?? 0) + 1
  }
  return {
    items,
    total: items.length,
    limit: 20,
    offset: 0,
    by_severity: bySeverity,
    summary: `${items.length} live risks recorded.`,
  }
}

const RISK_LIST = listPage(RISKS)

/** The row a transition answers with: the same finding, moved one state along. */
function transitioned(row: RiskRead, status: RiskStatus): RiskRead {
  const closed = status === 'resolved' || status === 'dismissed'
  return { ...row, status, resolved_at: closed ? '2026-01-10T09:00:00Z' : null }
}

/** Resolves the row a transition URL names, so the answer is that finding. */
function rowFor(url: string): RiskRead {
  const match = RISKS.find((row) => url.includes(`/risks/${row.id}/`))
  return match ?? HIGH
}

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
  list?: Route
  acknowledge?: Route
  dismiss?: Route
  resolve?: Route
}

/**
 * Stubs `fetch` with the routing table below, so a single test can replace one
 * endpoint — the failing list, the one that never settles — without restating
 * the rest. `/risks/summary` is matched before `/risks` because the former is
 * a sub-path of the latter.
 */
function installBackend(overrides: Backend = {}): Call[] {
  const summary = overrides.summary ?? (() => json(SUMMARY))
  // The default list honours `severity` the way the server does. It has to:
  // the band is a server-side filter now, so a stub that ignored it would hand
  // the page rows for every band and the test would be asserting that the page
  // filters in JavaScript — which is exactly what it no longer does.
  const list =
    overrides.list ??
    ((url: string) => {
      const severity = new URL(url, 'http://test').searchParams.get('severity')
      if (!severity) return json(RISK_LIST)
      const items = RISK_LIST.items.filter((item) => item.severity === severity)
      const bySeverity: Record<string, number> = {
        critical: 0,
        high: 0,
        medium: 0,
        low: 0,
      }
      for (const item of items) bySeverity[item.severity] = (bySeverity[item.severity] ?? 0) + 1
      return json({
        ...RISK_LIST,
        items,
        total: items.length,
        by_severity: bySeverity,
        summary: RISK_LIST.summary,
      })
    })
  const acknowledge =
    overrides.acknowledge ?? ((url) => json(transitioned(rowFor(url), 'acknowledged')))
  const dismiss = overrides.dismiss ?? ((url) => json(transitioned(rowFor(url), 'dismissed')))
  const resolve = overrides.resolve ?? ((url) => json(transitioned(rowFor(url), 'resolved')))

  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      calls.push({ url, method })

      if (method === 'POST') {
        if (url.includes('/acknowledge')) return acknowledge(url)
        if (url.includes('/dismiss')) return dismiss(url)
        if (url.includes('/resolve')) return resolve(url)
      }
      if (url.includes('/risks/summary')) return summary(url)
      if (url.includes('/risks')) return list(url)
      return envelope('not_found', 'No stub matched this request.', 404, 'req-unmatched')
    }),
  )
  return calls
}

/**
 * Mirrors `src/app/query-client.ts`. Reproduced rather than replaced so the
 * retry behaviour under test is the behaviour the app ships with: a 5xx is
 * asked twice more, which is why the error surfaces are awaited with a long
 * timeout, while a 4xx is refused on the first response.
 */
function createTestClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => {
          if (isAbortError(error)) return false
          const status = toApiError(error).status
          if (status >= 400 && status < 500) return false
          return failureCount < 2
        },
      },
      mutations: { retry: false },
    },
  })
}

function renderRiskCenter(entry = '/risks') {
  const router = createMemoryRouter([{ path: '*', element: <RiskCenterPage /> }], {
    initialEntries: [entry],
  })
  render(
    <QueryClientProvider client={createTestClient()}>
      <TooltipProvider delayDuration={200}>
        <RouterProvider router={router} />
      </TooltipProvider>
    </QueryClientProvider>,
  )
  return { router }
}

/** The card a title belongs to, found the way a reader finds it on the page. */
function cardFor(title: string): HTMLElement {
  return screen.getByRole('heading', { name: title }).closest('div.rounded-lg') as HTMLElement
}

function listCalls(calls: Call[]): Call[] {
  return calls.filter((call) => call.method === 'GET' && call.url.includes('/risks?'))
}

function postCalls(calls: Call[]): Call[] {
  return calls.filter((call) => call.method === 'POST')
}

/** Lets every in-flight fetch and its re-render settle before asserting. */
async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50))
  })
}

describe('risk center page', () => {
  it('leads with the four summary bands, their counts and their band links', async () => {
    installBackend()
    renderRiskCenter()

    await screen.findByRole('heading', { name: HIGH.title })

    expect(screen.getByRole('heading', { level: 1, name: 'Risk Center' })).toBeInTheDocument()

    // Four tiles, most severe first, each one a link into that band. The API has
    // no severity parameter, so the band is a view of the URL rather than of the
    // query — which is why the tile is a link and why it says so. The page's own
    // links are filtered out by their href because the findings below also link
    // to the task or project each one is about.
    const tiles = screen
      .getAllByRole('link')
      .filter((link) => link.getAttribute('href')?.startsWith('/risks?severity=') === true)
    expect(tiles.map((tile) => tile.getAttribute('href'))).toEqual([
      '/risks?severity=critical',
      '/risks?severity=high',
      '/risks?severity=medium',
      '/risks?severity=low',
    ])
    expect(within(tiles[0] as HTMLElement).getByText('1')).toBeInTheDocument()
    expect(within(tiles[1] as HTMLElement).getByText('2')).toBeInTheDocument()
    expect(within(tiles[2] as HTMLElement).getByText('1')).toBeInTheDocument()
    // Zero is a real count *within* a populated row: the low band has nothing in
    // it, and that is stated rather than hidden.
    expect(within(tiles[3] as HTMLElement).getByText('0')).toBeInTheDocument()

    expect(screen.getByText(/At least one recorded risk is high or critical\./)).toBeInTheDocument()
    expect(screen.getByText(/4 risks recorded in total\./)).toBeInTheDocument()

    // Every finding is on the page with its evidence, not just the counts.
    for (const row of RISKS) {
      expect(screen.getByRole('heading', { name: row.title })).toBeInTheDocument()
    }
    expect(screen.getAllByRole('heading', { name: 'Why' })).toHaveLength(4)
  })

  it('narrows the list to the band in the URL and sends it to the server', async () => {
    const calls = installBackend()
    renderRiskCenter('/risks?severity=critical')

    await screen.findByRole('heading', { name: CRITICAL.title })

    // Only the critical finding is listed...
    expect(screen.queryByRole('heading', { name: HIGH.title })).toBeNull()
    expect(screen.queryByRole('heading', { name: MEDIUM.title })).toBeNull()
    // ... and the tile for the band in force is marked as the current one.
    expect(screen.getByRole('link', { name: /Critical/ })).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('link', { name: /High/ })).not.toHaveAttribute('aria-current')
    expect(screen.getByLabelText('Severity band')).toHaveValue('critical')

    // The band goes on the wire. It used to be narrowed in the browser over one
    // server page, which is why `total` could not count past that page and the
    // pager had to be withdrawn under a band; `GET /risks` now takes `severity`,
    // so the narrowing is one indexed query and both problems are gone.
    expect(listCalls(calls)).toHaveLength(1)
    expect(listCalls(calls)[0]?.url).toContain('severity=critical')
    expect(listCalls(calls)[0]?.url).toContain('status=active')

    // And the page no longer carries the notice explaining what narrowing cost.
    expect(screen.queryByText(/The API filters by status and type only/)).not.toBeInTheDocument()
  })

  it('acknowledges, dismisses and resolves through the three transition endpoints', async () => {
    const user = userEvent.setup()
    const calls = installBackend()
    // Every status, so the card stays on screen after the transition: on the
    // default `active` filter an acknowledged risk leaves the view on the next
    // read, which is the point of acknowledging but not what this asserts.
    renderRiskCenter('/risks?status=all')

    await screen.findByRole('heading', { name: HIGH.title })

    // Acknowledge.
    await user.click(within(cardFor(HIGH.title)).getByRole('button', { name: 'Acknowledge' }))
    await waitFor(() =>
      expect(within(cardFor(HIGH.title)).getByText('Acknowledged')).toBeInTheDocument(),
    )

    expect(postCalls(calls)).toHaveLength(1)
    expect(postCalls(calls)[0]?.url).toContain(`/risks/${HIGH_ID}/acknowledge`)
    expect(postCalls(calls)[0]?.method).toBe('POST')
    // Acknowledging is "still true, stop asking": the two remaining answers stay
    // on offer and the acknowledge button is withdrawn, because the backend would
    // answer a second one with a 409.
    const acknowledged = cardFor(HIGH.title)
    expect(within(acknowledged).queryByRole('button', { name: 'Acknowledge' })).toBeNull()
    expect(within(acknowledged).getByRole('button', { name: 'Mark resolved' })).toBeInTheDocument()
    expect(within(acknowledged).getByRole('button', { name: 'Dismiss' })).toBeInTheDocument()

    // Dismiss. A terminal risk replaces the buttons with its closing sentence.
    await user.click(within(cardFor(HIGH_TWO.title)).getByRole('button', { name: 'Dismiss' }))
    await waitFor(() =>
      expect(within(cardFor(HIGH_TWO.title)).getByText(/^Closed\. Dismissed/)).toBeInTheDocument(),
    )
    expect(postCalls(calls)[1]?.url).toContain(`/risks/${HIGH_TWO_ID}/dismiss`)
    expect(within(cardFor(HIGH_TWO.title)).queryByRole('button', { name: 'Dismiss' })).toBeNull()

    // Resolve.
    await user.click(within(cardFor(MEDIUM.title)).getByRole('button', { name: 'Mark resolved' }))
    await waitFor(() =>
      expect(within(cardFor(MEDIUM.title)).getByText(/^Closed\. Resolved/)).toBeInTheDocument(),
    )
    expect(postCalls(calls)[2]?.url).toContain(`/risks/${MEDIUM_ID}/resolve`)

    // Three transitions, three endpoints: none of them fell back to a generic
    // "transition" call with a status argument the backend does not accept.
    expect(postCalls(calls).map((call) => call.url)).toEqual([
      `/api/v1/risks/${HIGH_ID}/acknowledge`,
      `/api/v1/risks/${HIGH_TWO_ID}/dismiss`,
      `/api/v1/risks/${MEDIUM_ID}/resolve`,
    ])
  })

  it('redraws each card from the row its transition returned', async () => {
    const user = userEvent.setup()
    // The list answers with the page it was asked for, unchanged. The only
    // source of the new status on screen is therefore the row the transition
    // returned — which is what the page overlays onto the fetched page, and what
    // stops a card flashing its old status while the list refetches.
    installBackend()
    renderRiskCenter('/risks?status=all')
    await screen.findByRole('heading', { name: HIGH.title })

    expect(cardFor(HIGH.title)).toHaveTextContent('Active')
    await user.click(within(cardFor(HIGH.title)).getByRole('button', { name: 'Acknowledge' }))
    await waitFor(() => expect(cardFor(HIGH.title)).toHaveTextContent('Acknowledged'))

    // The band chip is re-derived from the returned row too, and the row's own
    // resolved timestamp is absent because an acknowledged risk is still live.
    expect(within(cardFor(HIGH.title)).getByText('High')).toBeInTheDocument()
    expect(cardFor(HIGH.title)).not.toHaveTextContent('Closed.')
  })

  it('shows the busy state for both reads, then the findings', async () => {
    let release: (() => void) | null = null
    const pending = new Promise<Response>((resolve) => {
      release = () => resolve(json(RISK_LIST))
    })
    installBackend({ list: () => pending })
    renderRiskCenter()

    // The masthead paints immediately; a blank page while the risks load is what
    // the two skeletons exist to avoid.
    expect(screen.getByRole('heading', { level: 1, name: 'Risk Center' })).toBeInTheDocument()
    expect(screen.getByText('Loading the risk counts')).toBeInTheDocument()
    expect(screen.getByText('Loading detected risks')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: HIGH.title })).toBeNull()
    // Nothing in either placeholder reads as a figure.
    expect(document.body.textContent).not.toMatch(/\b0\b/)

    await act(async () => {
      release?.()
    })
    expect(await screen.findByRole('heading', { name: HIGH.title })).toBeInTheDocument()
    expect(screen.queryByText('Loading detected risks')).toBeNull()
  })

  it('reports a failed list with a retry that asks again and recovers', async () => {
    const user = userEvent.setup()
    let failing = true
    installBackend({
      list: () =>
        failing
          ? envelope('internal_error', 'The risk service is unavailable.', 500, 'req-risk-1')
          : json(RISK_LIST),
    })
    renderRiskCenter()

    // The shared retry policy asks twice more with a backoff, so the error
    // surface cannot arrive inside the default 5s budget.
    const alert = await screen.findByRole('alert', {}, { timeout: 20_000 })
    expect(alert).toHaveTextContent('The risk list could not load')
    expect(alert).toHaveTextContent(
      'The failure was recorded on the server. Retry, and quote the request ID below.',
    )
    expect(alert).toHaveTextContent('The risk service is unavailable.')
    expect(alert).toHaveTextContent('req-risk-1')

    // A failed read is distinguishable from an empty one: no finding is invented
    // and no empty state claims there was simply nothing to find.
    expect(screen.queryByRole('heading', { name: HIGH.title })).toBeNull()
    expect(screen.queryByText('No significant risk detected yet')).toBeNull()

    failing = false
    await user.click(screen.getByRole('button', { name: /Retry/i }))

    expect(await screen.findByRole('heading', { name: HIGH.title })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('reports a failed summary separately from a failed list', async () => {
    installBackend({
      summary: () =>
        envelope('internal_error', 'The count service is unavailable.', 500, 'req-summary-1'),
    })
    renderRiskCenter()

    const alert = await screen.findByRole('alert', {}, { timeout: 20_000 })
    expect(alert).toHaveTextContent('The risk counts could not load')
    expect(alert).toHaveTextContent('req-summary-1')
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument()

    // The list is unaffected: a header that failed must not take the page with it.
    expect(await screen.findByRole('heading', { name: HIGH.title })).toBeInTheDocument()
  })

  it('states an empty account as a result rather than a wall of zeroes', async () => {
    installBackend({
      summary: () => json(EMPTY_SUMMARY),
      list: () => json(listPage([])),
    })
    renderRiskCenter()

    expect(
      await screen.findByText('No significant risk detected yet'),
    ).toBeInTheDocument()

    // Four zeroes above an empty table reads as a measurement, and the engine
    // running and finding nothing is the opposite of one: no tiles, no filter
    // bar, no list — and the sentence appears once, not once per region.
    expect(screen.queryAllByRole('link')).toHaveLength(0)
    expect(screen.queryByLabelText('Severity band')).toBeNull()
    expect(screen.queryByLabelText('Status')).toBeNull()
    expect(screen.getAllByText('No significant risk detected yet')).toHaveLength(1)
    expect(document.body.textContent).not.toMatch(/0\s*%/)
    expect(document.body.textContent).not.toMatch(/0%/)
    expect(document.body.textContent).not.toMatch(/NaN|Infinity|undefined%/)
  })

  it('tells a filter that matched nothing apart from an account with nothing', async () => {
    installBackend({ list: () => json(listPage([])) })
    renderRiskCenter('/risks?severity=low')

    // The counts still report four recorded risks, so the list saying "nothing
    // matches" is a statement about the band and not about the account.
    expect(await screen.findByText(/4 risks recorded in total\./)).toBeInTheDocument()
    expect(screen.getByText('Nothing matches this filter')).toBeInTheDocument()
    expect(screen.queryByText('No significant risk detected yet')).toBeNull()
    // The filter bar stays: there is something to clear.
    expect(screen.getByLabelText('Severity band')).toHaveValue('low')
  })

  /**
   * The "Updating for the selected filters…" notice has to be *in the document*
   * before it has anything to say.
   *
   * `role="status"` announces a change to a region that already exists. Written
   * as `{busy && <p role="status">…</p>}` the region and its text arrive in the
   * accessibility tree together, and most screen readers stay silent — so the
   * notice that exists precisely to say "these rows are the previous answer"
   * would be the one nobody hears. The region is therefore mounted empty and
   * only its content toggles.
   */
  it('mounts the updating notice before it has anything to announce', async () => {
    // The second read is held open on purpose. The stub answers every request
    // immediately, and `isPlaceholderData` is true only for the window between
    // the band changing and the answer landing — a window that closes inside one
    // microtask, so a notice that *did* render would never be observable.
    let release: (() => void) | null = null
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    const calls = installBackend({
      list: (url: string) => {
        const severity = new URL(url, 'http://test').searchParams.get('severity')
        if (severity === 'low') return gate.then(() => json(listPage([])))
        return json(RISK_LIST)
      },
    })
    renderRiskCenter('/risks?severity=critical')

    await screen.findByRole('heading', { name: CRITICAL.title })

    // Settled, and nothing to announce — but the region is still on the page.
    const region = screen.getByRole('status')
    expect(region).toBeInTheDocument()
    expect(region).toBeEmptyDOMElement()
    expect(region).not.toHaveTextContent('Updating for the selected filters')

    // Change the band. The notice fills the region that was already there
    // rather than replacing it, which is the whole property under test.
    await userEvent.selectOptions(screen.getByLabelText('Severity band'), 'low')
    await screen.findByText('Updating for the selected filters…')

    const filled = screen.getByRole('status')
    expect(filled).toBe(region)
    expect(filled).toHaveClass('text-xs', 'text-muted-foreground')
    // The previous answer is still on screen behind the notice, which is the
    // behaviour the notice exists to explain.
    expect(listCalls(calls).length).toBeGreaterThan(1)

    await act(async () => {
      release?.()
    })

    // And it empties again once the answer lands, so it is not a stale notice
    // left sitting under the rows.
    await waitFor(() => expect(screen.getByRole('status')).toBeEmptyDOMElement())
    expect(await screen.findByText('Nothing matches this filter')).toBeInTheDocument()
  })

  it('never prints NaN, Infinity or an undefined percentage', async () => {
    installBackend({ list: () => json(listPage([])) })
    renderRiskCenter('/risks?severity=low')
    await screen.findByText('Nothing matches this filter')
    await settle()

    // A band that matches nothing, on an account with four recorded risks: the
    // tiles are still real counts and the withdrawn pager must not leave a
    // division by zero behind it.
    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/NaN/)
    expect(text).not.toMatch(/Infinity/)
    expect(text).not.toMatch(/undefined%/)
    expect(text).not.toMatch(/0% productivity/i)
  })

  /**
   * The brief's language rule, checked against the whole rendered page.
   *
   * Everything here describes what the recorded data shows: which conditions are
   * live, what they were scored from and what could be done about them. It never
   * characterises the reader, and it never manufactures urgency. Scanning the
   * page's text rather than the copy that shipped today is what makes this a
   * regression guard instead of a restatement.
   */
  it('never describes the reader with the four words the brief rules out', async () => {
    installBackend()
    renderRiskCenter()
    await screen.findByRole('heading', { name: HIGH.title })
    await settle()

    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/failing/i)
    expect(text).not.toMatch(/unproductive/i)
    expect(text).not.toMatch(/lazy/i)
    expect(text).not.toMatch(/burnout/i)
  })
})