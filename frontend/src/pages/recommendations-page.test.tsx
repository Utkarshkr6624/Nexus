import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/tooltip'
import RecommendationsPage from '@/pages/recommendations-page'
import { queryRetryPolicy } from '@/app/query-client'
import type { ApiErrorEnvelope } from '@/types/api'
import {
  RECOMMENDATION_STATUSES,
  type RecommendationListRead,
  type RecommendationRead,
} from '@/types/risk'

/**
 * The Recommendations page, asserted at the network boundary.
 *
 * Real router, real components, real hooks; only `fetch` is stubbed. Four
 * suggestions are raised by the fixtures below, two of them still waiting on an
 * answer, so the counts the page composes can be checked by hand: four
 * suggestions, one in each band.
 *
 * **The query client is local.** `AppProviders` mounts the shared singleton and
 * registers `onSessionChange(() => queryClient.clear())`
 * (`src/app/auth-bootstrap.tsx:13`), which in jsdom clears the cache mid-test and
 * strands every component at `pending`. A fresh client per render avoids that,
 * with the defaults from `src/app/query-client.ts` carried over rather than
 * relaxed so the retry behaviour under test is the shipped behaviour.
 */

const CRITICAL_ID = '11111111-1111-4111-8111-111111111111'
const HIGH_ID = '22222222-2222-4222-8222-222222222222'
const HIGH_TWO_ID = '33333333-3333-4333-8333-333333333333'
const LOW_ID = '44444444-4444-4444-8444-444444444444'
const RISK_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'

function suggestion(
  overrides: Partial<RecommendationRead> & Pick<RecommendationRead, 'id' | 'title'>,
): RecommendationRead {
  return {
    recommendation_type: 'block_time',
    priority: 'high',
    description: 'Add two work sessions for the task before its due date.',
    reason: 'The recorded work sessions before the due date cover 120 of about 300 minutes.',
    entity_type: 'task',
    entity_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    risk_id: RISK_ID,
    status: 'new',
    created_at: '2026-01-09T08:05:00Z',
    responded_at: null,
    expires_at: null,
    metadata: {},
    ...overrides,
  }
}

/**
 * Four suggestions, one in each band, deliberately not in priority order in the
 * payload: the grouping is the page's job, and an already-sorted fixture would
 * let a `sort()` that ranks the bands wrongly still look right.
 */
const CRITICAL = suggestion({
  id: CRITICAL_ID,
  priority: 'critical',
  recommendation_type: 'review_deadline',
  title: 'Check whether the Atlas release date is still achievable',
  description: 'Read the four open tasks and move the date or the scope, whichever is right.',
  reason: 'Four open tasks have no scheduled work before the project deadline on 20 Jan.',
})

const HIGH = suggestion({
  id: HIGH_ID,
  title: 'Schedule another 180 minutes before 9 Jan',
  reason: 'Draft the migration plan needs 300 minutes and 120 are booked before it is due.',
})

const HIGH_TWO = suggestion({
  id: HIGH_TWO_ID,
  priority: 'high',
  recommendation_type: 'reduce_workload',
  title: 'Move about 480 minutes of planned work to later dates',
  description: 'Reschedule the two lowest-priority sessions into the following week.',
  reason: 'Planned time for the next two weeks exceeds the declared hours by 480 minutes.',
})

const LOW = suggestion({
  id: LOW_ID,
  priority: 'low',
  status: 'accepted',
  responded_at: '2026-01-09T11:00:00Z',
  title: 'Break the reporting rollup into smaller pieces',
  description: 'Split the rollup task into one task per report.',
  reason: 'Five completed tasks ran about 45% over their recorded estimates.',
})

const SUGGESTIONS = [HIGH, CRITICAL, LOW, HIGH_TWO]

function listPage(items: RecommendationRead[]): RecommendationListRead {
  const byPriority: Record<string, number> = { critical: 0, high: 0, medium: 0, low: 0 }
  for (const item of items) {
    byPriority[item.priority] = (byPriority[item.priority] ?? 0) + 1
  }
  return { items, total: items.length, limit: 20, offset: 0, by_priority: byPriority }
}

const PAGE = listPage(SUGGESTIONS)

/** The row a transition answers with: the same suggestion, moved one state on. */
function transitioned(row: RecommendationRead, status: RecommendationRead['status']) {
  return { ...row, status, responded_at: '2026-01-09T12:00:00Z' }
}

function rowFor(url: string): RecommendationRead {
  return SUGGESTIONS.find((row) => url.includes(`/recommendations/${row.id}/`)) ?? HIGH
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
  list?: Route
  accept?: Route
  reject?: Route
  complete?: Route
}

function installBackend(overrides: Backend = {}): Call[] {
  const list = overrides.list ?? (() => json(PAGE))
  const accept = overrides.accept ?? ((url) => json(transitioned(rowFor(url), 'accepted')))
  const reject = overrides.reject ?? ((url) => json(transitioned(rowFor(url), 'rejected')))
  const complete = overrides.complete ?? ((url) => json(transitioned(rowFor(url), 'completed')))

  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      calls.push({ url, method })

      if (method === 'POST') {
        if (url.includes('/accept')) return accept(url)
        if (url.includes('/reject')) return reject(url)
        if (url.includes('/complete')) return complete(url)
      }
      if (url.includes('/recommendations')) return list(url)
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

function renderRecommendations(entry = '/recommendations') {
  const router = createMemoryRouter([{ path: '*', element: <RecommendationsPage /> }], {
    initialEntries: [entry],
  })
  const result = render(
    <QueryClientProvider client={createTestClient()}>
      <TooltipProvider delayDuration={200}>
        <RouterProvider router={router} />
      </TooltipProvider>
    </QueryClientProvider>,
  )
  return { ...result, router }
}

function cardFor(title: string): HTMLElement {
  return screen.getByRole('heading', { name: title }).closest('div.rounded-lg') as HTMLElement
}

function listCalls(calls: Call[]): Call[] {
  return calls.filter((call) => call.method === 'GET' && call.url.includes('/recommendations?'))
}

function postCalls(calls: Call[]): Call[] {
  return calls.filter((call) => call.method === 'POST')
}

async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50))
  })
}

describe('recommendations page', () => {
  it('groups by priority, most severe first, with an icon and a word on each heading', async () => {
    installBackend()
    renderRecommendations()

    await screen.findByRole('heading', { name: CRITICAL.title })

    expect(screen.getByRole('heading', { level: 1, name: 'Recommendations' })).toBeInTheDocument()

    // The backend derives priority from the risk's severity, so the grouping
    // follows `RECOMMENDATION_PRIORITIES` rather than anything the payload
    // happened to arrive in. The payload above is deliberately not sorted.
    expect(
      screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent),
    ).toEqual(['Filters', 'Critical priority', 'High priority', 'Low priority'])

    // A band whose only signal was a red or amber edge would be unreadable in
    // dark mode, to a colour-blind reader and to a screen reader, so the heading
    // repeats what the card already states in words — with an icon beside it.
    for (const label of ['Critical', 'High', 'Low']) {
      const heading = screen.getByRole('heading', { name: `${label} priority` })
      const icon = heading.previousElementSibling
      expect(icon?.tagName.toLowerCase()).toBe('svg')
      expect(icon).toHaveAttribute('aria-hidden', 'true')
    }

    // The count beside each heading is the band's own, and the count line under
    // the filter is composed from `by_priority` rather than from a sent sentence.
    // Critical and Low hold one each, High holds two.
    expect(screen.getAllByText('1 suggestion')).toHaveLength(2)
    expect(screen.getByText('2 suggestions')).toBeInTheDocument()
    expect(screen.getByText('4 suggestions: 1 critical, 2 high, 1 low.')).toBeInTheDocument()
  })

  it('shows every suggestion with its WHAT and its WHY', async () => {
    installBackend()
    renderRecommendations()

    await screen.findByRole('heading', { name: CRITICAL.title })

    for (const row of SUGGESTIONS) {
      expect(screen.getByRole('heading', { name: row.title })).toBeInTheDocument()
      // The reason is rendered in full and never elided: a reader who disagrees
      // with the suggestion can only do so on the evidence, and the evidence is
      // this sentence.
      expect(screen.getByText(row.reason)).toBeInTheDocument()
      expect(screen.getByText(row.description)).toBeInTheDocument()
      // Nothing here is a percentage: the suggestion reports what is booked, not
      // a score of the person.
      expect(document.body.textContent ?? '').not.toMatch(/undefined|NaN|Infinity/)
    }
  })

  it('answers with accept, completion and decline, each through its own endpoint', async () => {
    const user = userEvent.setup()
    const calls = installBackend()
    // Every status, so each card stays on screen after its transition.
    renderRecommendations('/recommendations?status=all')

    await screen.findByRole('heading', { name: CRITICAL.title })

    // Accept. Only the transitions legal from `new` are on offer: completion is
    // reachable after an acceptance, never before.
    await user.click(within(cardFor(CRITICAL.title)).getByRole('button', { name: 'Accept' }))
    await waitFor(() =>
      expect(within(cardFor(CRITICAL.title)).getByText('Accepted')).toBeInTheDocument(),
    )
    expect(within(cardFor(CRITICAL.title)).queryByRole('button', { name: 'Accept' })).toBeNull()
    expect(
      within(cardFor(CRITICAL.title)).getByRole('button', { name: 'Mark completed' }),
    ).toBeInTheDocument()

    // Mark completed, on a suggestion that was already accepted.
    await user.click(within(cardFor(LOW.title)).getByRole('button', { name: 'Mark completed' }))
    await waitFor(() =>
      expect(within(cardFor(LOW.title)).getByText('Completed')).toBeInTheDocument(),
    )
    expect(within(cardFor(LOW.title)).getByText(/^No longer open\./)).toBeInTheDocument()

    // Not for me. Declining is a legitimate answer, and the copy says the
    // underlying condition may still be true rather than calling it a mistake.
    await user.click(within(cardFor(HIGH.title)).getByRole('button', { name: 'Not for me' }))
    await waitFor(() =>
      expect(within(cardFor(HIGH.title)).getByText('Rejected')).toBeInTheDocument(),
    )
    expect(within(cardFor(HIGH.title)).getByText(/^Declined\./)).toBeInTheDocument()
    expect(within(cardFor(HIGH.title)).queryByRole('button')).toBeNull()

    // Three answers, three endpoints, and no generic "transition" call with a
    // status argument the backend does not accept.
    expect(postCalls(calls).map((call) => call.url)).toEqual([
      `/api/v1/recommendations/${CRITICAL_ID}/accept`,
      `/api/v1/recommendations/${LOW_ID}/complete`,
      `/api/v1/recommendations/${HIGH_ID}/reject`,
    ])
    expect(postCalls(calls).every((call) => call.method === 'POST')).toBe(true)
  })

  it('redraws a card from the row its transition returned', async () => {
    const user = userEvent.setup()
    // The list answers with the page it was asked for, unchanged, so the new
    // status on screen can only have come from the row the transition returned.
    installBackend()
    renderRecommendations('/recommendations?status=all')
    await screen.findByRole('heading', { name: CRITICAL.title })

    expect(cardFor(CRITICAL.title)).toHaveTextContent('New')
    await user.click(within(cardFor(CRITICAL.title)).getByRole('button', { name: 'Accept' }))
    await waitFor(() => expect(cardFor(CRITICAL.title)).toHaveTextContent('Accepted'))

    // `responded_at` came back with the row, so the card can say the suggestion
    // has been answered without a second query.
    expect(within(cardFor(CRITICAL.title)).getByText(/^Answered /)).toBeInTheDocument()
  })

  it('reaches every status, including an explicit "all", through the filter', async () => {
    const user = userEvent.setup()
    const calls = installBackend()
    const { router } = renderRecommendations()
    await screen.findByRole('heading', { name: CRITICAL.title })

    const select = screen.getByLabelText('Status')

    // The screen opens on the suggestions that are still waiting for an answer,
    // which is the backend's own recommendation for this list.
    expect(select).toHaveValue('new')
    expect(listCalls(calls)[0]?.url).toContain('status=new')

    // Every status the backend can hold, plus the explicit "all". The assertion is
    // that each of them reaches the wire, which is the claim: the control, the
    // URL and the request are three places the choice could fail to propagate,
    // and the page owns the first two.
    for (const status of RECOMMENDATION_STATUSES) {
      await user.selectOptions(select, status)

      await waitFor(() => expect(router.state.location.search).toBe(`?status=${status}`))
      await waitFor(() =>
        expect(listCalls(calls).some((call) => call.url.includes(`status=${status}`))).toBe(true),
      )
      expect(select).toHaveValue(status)
    }

    await user.selectOptions(select, 'all')

    // "Every status" is a word of its own rather than the absent parameter: an
    // absent `status` already means "the default, which is New", so a control
    // reading "All statuses" beside a list filtered to New would be lying. On
    // the wire it is simply no filter at all.
    await waitFor(() => expect(router.state.location.search).toBe('?status=all'))
    await waitFor(() =>
      expect(listCalls(calls).some((call) => !call.url.includes('status='))).toBe(true),
    )
    expect(select).toHaveValue('all')
    expect(screen.getByText('4 suggestions: 1 critical, 2 high, 1 low.')).toBeInTheDocument()
  })

  it('shows the busy state while the suggestions load, then the cards', async () => {
    let release: (() => void) | null = null
    const pending = new Promise<Response>((resolve) => {
      release = () => resolve(json(PAGE))
    })
    installBackend({ list: () => pending })
    renderRecommendations()

    expect(screen.getByRole('heading', { level: 1, name: 'Recommendations' })).toBeInTheDocument()
    expect(screen.getByText('Loading suggestions')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: CRITICAL.title })).toBeNull()

    await act(async () => {
      release?.()
    })
    expect(await screen.findByRole('heading', { name: CRITICAL.title })).toBeInTheDocument()
    expect(screen.queryByText('Loading suggestions')).toBeNull()
  })

  it('reports a failed read with a retry that asks again and recovers', async () => {
    const user = userEvent.setup()
    let failing = true
    installBackend({
      list: () =>
        failing
          ? envelope('internal_error', 'The suggestion service is unavailable.', 500, 'req-rec-1')
          : json(PAGE),
    })
    renderRecommendations()

    const alert = await screen.findByRole('alert', {}, { timeout: 20_000 })
    expect(alert).toHaveTextContent('The suggestions could not load')
    expect(alert).toHaveTextContent('The suggestion service is unavailable.')
    expect(alert).toHaveTextContent('req-rec-1')

    failing = false
    await user.click(screen.getByRole('button', { name: /Retry/i }))
    expect(await screen.findByRole('heading', { name: CRITICAL.title })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('states an account with nothing open, and a filter that matched nothing', async () => {
    const calls = installBackend({ list: () => json(listPage([])) })

    const first = renderRecommendations()
    expect(await screen.findByText('No suggestions open')).toBeInTheDocument()
    expect(screen.getByText('No suggestions match this filter.')).toBeInTheDocument()
    // Nothing here is a wall of zeroes: the empty state replaces the groups
    // rather than printing a heading over each of the four bands.
    expect(screen.queryByRole('heading', { name: /priority$/ })).toBeNull()
    first.unmount()

    renderRecommendations('/recommendations?status=completed')
    // "Something exists, just not here" is a different claim from "nothing has
    // been raised", and conflating the two is how a filtered list reads as an
    // empty account.
    expect(await screen.findByText('Nothing matches this filter')).toBeInTheDocument()
    expect(screen.queryByText('No suggestions open')).toBeNull()
    expect(listCalls(calls).at(-1)?.url).toContain('status=completed')
  })

  it('never prints NaN, Infinity or an undefined percentage', async () => {
    installBackend()
    renderRecommendations()
    await screen.findByRole('heading', { name: CRITICAL.title })
    await settle()

    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/NaN/)
    expect(text).not.toMatch(/Infinity/)
    expect(text).not.toMatch(/undefined%/)
    expect(text).not.toMatch(/0% productivity/i)
  })

  /**
   * The brief's language rule, checked against the whole rendered page.
   *
   * A suggestion here proposes something and states the evidence behind it. It
   * never characterises the reader and never manufactures urgency — declining is
   * named "Not for me", not a rejection, and a declined suggestion can be raised
   * again. Scanning the page's text rather than the copy that shipped today is
   * what makes this a regression guard.
   */
  it('never describes the reader with the four words the brief rules out', async () => {
    installBackend()
    renderRecommendations()
    await screen.findByRole('heading', { name: CRITICAL.title })
    await settle()

    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/failing/i)
    expect(text).not.toMatch(/unproductive/i)
    expect(text).not.toMatch(/lazy/i)
    expect(text).not.toMatch(/burnout/i)
  })
})