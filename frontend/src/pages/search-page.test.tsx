import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import SearchPage from '@/pages/search-page'
import { isAbortError, toApiError } from '@/services/errors'
import type { ApiErrorEnvelope } from '@/types/api'
import type { PageMeta } from '@/types/pagination'
import {
  SEARCH_ENTITY_KINDS,
  SEARCH_KIND_META,
  searchHitHref,
} from '@/types/search'
import type { SearchGroup, SearchHit, SearchResponse } from '@/types/search'

/**
 * The global search page, asserted at the network boundary.
 *
 * Real router, real components, real hooks; only `fetch` is stubbed.
 *
 * **The query client is local**, for the reason `recommendations-page.test.tsx`
 * gives: `AppProviders` mounts the shared singleton and clears it on a session
 * change, which in jsdom strands every component at `pending`. The shipped
 * defaults are reproduced rather than relaxed so the retry behaviour under test
 * is the behaviour that ships.
 *
 * **The route table is the real one.** The "every kind links somewhere" test
 * reads `src/routes/router.tsx` rather than a hand-copied list of paths, so
 * renaming or removing a route turns this suite red instead of leaving the page
 * shipping a link to a 404.
 */

const QUERY = 'atlas'

const PROJECT_ID = '11111111-1111-4111-8111-111111111111'
const TASK_ID = '22222222-2222-4222-8222-222222222222'
const NOTE_ID = '33333333-3333-4333-8333-333333333333'

const TOTAL_HITS = 25

/**
 * A 25-row corpus with one hit of every kind and a couple of repeats.
 *
 * Built by cycling the union rather than by listing kinds, so the corpus covers
 * all eleven without the fixture drifting out of step with the enum — and the
 * `index % 11` walk means several kinds hold two rows, which is what makes the
 * grouped view's per-group counts worth asserting.
 */
function buildCorpus(): SearchHit[] {
  return Array.from({ length: TOTAL_HITS }, (_, index) => {
    const kind = SEARCH_ENTITY_KINDS[index % SEARCH_ENTITY_KINDS.length] ?? 'project'
    const title = `Record ${index + 1}`
    return makeHit({
      kind,
      title,
      snippet: `${title} mentions Atlas in its body text.`,
      match_start: title.length + 1,
      match_end: title.length + 1 + 'Atlas'.length,
    })
  })
}

const CORPUS = buildCorpus()

/**
 * `kind` and `title` are the two fields every fixture must decide; `id` and the
 * optional context fields have defaults, so a test that is only about one
 * property does not have to spell out the other nine.
 */
type HitOverrides = Omit<Partial<SearchHit>, 'kind' | 'title'> & Pick<SearchHit, 'kind' | 'title'>

function makeHit(overrides: HitOverrides): SearchHit {
  return {
    id: TASK_ID,
    snippet: `${overrides.title} mentions Atlas in its body text.`,
    match_start: overrides.title.length + 1,
    match_end: overrides.title.length + 1 + 'Atlas'.length,
    matched_field: 'content',
    project_id: PROJECT_ID,
    project_name: 'Atlas',
    relative_date: '4 days ago',
    updated_at: '2026-01-09T08:05:00Z',
    ...overrides,
  }
}

/**
 * The backend's shape: `hits` is the ranked page and `groups` partitions
 * exactly that page, one bucket per kind in first-appearance order — not one
 * bucket per run of adjacent hits. Built the same way here so a test cannot pass
 * against a payload the server would never send.
 */
function pageOf(hits: SearchHit[], meta: Partial<PageMeta> = {}): SearchResponse {
  const buckets = new Map<SearchHit['kind'], SearchHit[]>()
  for (const hit of hits) {
    const bucket = buckets.get(hit.kind)
    if (bucket) bucket.push(hit)
    else buckets.set(hit.kind, [hit])
  }
  const groups: SearchGroup[] = [...buckets].map(([kind, group]) => ({ kind, hits: group }))
  return {
    query: QUERY,
    hits,
    groups,
    meta: { total: hits.length, limit: 50, offset: 0, ...meta },
  }
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

type Route = (url: URL) => Response | Promise<Response>

function installBackend(handler: Route = defaultHandler): string[] {
  const urls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const raw = String(input)
      urls.push(raw)
      if (raw.includes('/api/v1/search')) return handler(new URL(raw, 'http://nexus.test'))
      return envelope('not_found', 'No stub matched this request.', 404, 'req-unmatched')
    }),
  )
  return urls
}

/** Answers from the corpus, honouring `limit`/`offset` exactly as the API does. */
const defaultHandler: Route = (url) => {
  const limit = Number(url.searchParams.get('limit') ?? 50)
  const offset = Number(url.searchParams.get('offset') ?? 0)
  return json(pageOf(CORPUS.slice(offset, offset + limit), { total: TOTAL_HITS, limit, offset }))
}

function searchCalls(urls: readonly string[]): URL[] {
  return urls
    .filter((url) => url.includes('/api/v1/search'))
    .map((url) => new URL(url, 'http://nexus.test'))
}

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

function renderSearch(entry = '/search') {
  const router = createMemoryRouter([{ path: '*', element: <SearchPage /> }], {
    initialEntries: [entry],
  })
  const result = render(
    <QueryClientProvider client={createTestClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { ...result, router }
}

/**
 * Rebound by every `installBackend` call.
 *
 * The helpers below assert against this rather than threading `urls` through
 * every `await user.click(...)`, which would put the assertion in a different
 * place from the interaction it is about.
 */
let installedUrls: string[] = []

/**
 * `delay: null` everywhere a box is typed into.
 *
 * `user-event` waits a real timer between keystrokes by default, and in jsdom
 * each of those lands in the tens of milliseconds — long enough for the 200 ms
 * debounce to fire between two characters. Without this, "type atlas" produces
 * five requests and every assertion about the settled term is a race.
 */
async function typeIntoBox(value: string): Promise<ReturnType<typeof userEvent.setup>> {
  const user = userEvent.setup({ delay: null })
  const input = screen.getByLabelText('Search NEXUS')
  await user.clear(input)
  if (value.length > 0) await user.type(input, value)
  return user
}

async function waitForSearch(count = 1): Promise<void> {
  await waitFor(() => expect(searchCalls(installedUrls).length).toBeGreaterThanOrEqual(count), {
    timeout: 5_000,
  })
}

/** Lets the debounce elapse without asserting on anything that happened. */
async function settle(ms = 400): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms))
  })
}

let routePaths: Set<string>

beforeAll(async () => {
  const module = await import('@/routes/router')
  routePaths = new Set<string>()
  for (const route of module.router.routes) {
    if (route.path) routePaths.add(route.path)
    for (const child of route.children ?? []) {
      if (child.path) routePaths.add(child.path)
    }
  }
})

describe('global search page', () => {
  it('offers a chip for every searchable kind, each with its icon and its word', async () => {
    installBackend()
    renderSearch()
    await screen.findByRole('heading', { level: 1, name: 'Search' })

    const filters = screen.getByRole('region', { name: 'Filter by kind' })
    const chips = within(filters).getAllByRole('button')

    // Eleven kinds plus the "All kinds" reset, which is how a reader gets out
    // of a filter that matched nothing.
    expect(chips).toHaveLength(SEARCH_ENTITY_KINDS.length + 1)
    expect(chips.map((chip) => chip.textContent?.trim())).toEqual([
      'All kinds',
      ...SEARCH_ENTITY_KINDS.map((kind) => SEARCH_KIND_META[kind].label),
    ])

    // A chip whose only signal was a background tint would be unreadable in dark
    // mode and to a screen reader, so each one carries an icon and a word.
    for (const kind of SEARCH_ENTITY_KINDS) {
      const chip = within(filters).getByRole('button', { name: SEARCH_KIND_META[kind].label })
      const icon = chip.querySelector('svg')
      expect(icon).not.toBeNull()
      expect(icon).toHaveAttribute('aria-hidden', 'true')
      // Real buttons, so they are reachable by keyboard and announce their state.
      expect(chip.tagName.toLowerCase()).toBe('button')
      expect(chip).toHaveAttribute('aria-pressed', 'false')
    }
  })

  it('maps every kind to a route that exists in the real route table', () => {
    // Read from `src/routes/router.tsx` rather than a hand-copied list: a route
    // that is renamed or dropped has to turn this test red, not the page.
    expect(routePaths.has('/search')).toBe(true)

    for (const kind of SEARCH_ENTITY_KINDS) {
      const meta = SEARCH_KIND_META[kind]
      expect(routePaths.has(meta.route)).toBe(true)

      const hit = makeHit({ kind, title: 'A record', id: TASK_ID })
      const href = searchHitHref(hit)
      expect(href).not.toBeNull()

      // Either the list route, or the detail pattern with the id filled in.
      const resolved = href as string
      const pattern = meta.detail ? meta.route.replace(/\/:[^/]+$/, '') : meta.route
      expect(resolved).toBe(meta.detail ? `${pattern}/${TASK_ID}` : meta.route)
      expect(routePaths.has(meta.detail ? meta.route : resolved)).toBe(true)
    }
  })

  it('asks for nothing until the box holds a non-blank term', async () => {
    installedUrls = installBackend()
    renderSearch()

    await screen.findByRole('heading', { level: 1, name: 'Search' })
    await settle()

    // An empty `q` is a 422, and an empty box is not a failed search — it is a
    // search nobody has asked yet.
    expect(searchCalls(installedUrls)).toHaveLength(0)
    expect(screen.getByText('Search everything you own')).toBeInTheDocument()

    // Whitespace is not a term either.
    await typeIntoBox('   ')
    await settle()
    expect(searchCalls(installedUrls)).toHaveLength(0)

    // The clear affordance puts it back to "not asked yet".
    await typeIntoBox(QUERY)
    await waitForSearch()
    await userEvent.setup({ delay: null }).click(
      screen.getByRole('button', { name: 'Clear search' }),
    )
    await settle()
    expect(searchCalls(installedUrls)).toHaveLength(1)
    expect(screen.getByText('Search everything you own')).toBeInTheDocument()
  }, 15_000)

  it('debounces the box and fires one request carrying the settled term', async () => {
    installedUrls = installBackend()
    renderSearch()
    await screen.findByRole('heading', { level: 1, name: 'Search' })

    await typeIntoBox('atl')
    // Three keystrokes, no request yet: the box has not settled.
    expect(searchCalls(installedUrls)).toHaveLength(0)

    await typeIntoBox(QUERY)
    await waitForSearch()

    const calls = searchCalls(installedUrls)
    expect(calls).toHaveLength(1)
    expect(calls[0]?.searchParams.get('q')).toBe(QUERY)

    // Editing again re-arms the debounce rather than firing per character.
    await typeIntoBox(`${QUERY} migration`)
    await waitForSearch(2)
    expect(searchCalls(installedUrls)).toHaveLength(2)
    expect(searchCalls(installedUrls)[1]?.searchParams.get('q')).toBe('atlas migration')
  }, 15_000)

  it('sends the selected kinds as repeated `types` and the page as limit/offset', async () => {
    const user = userEvent.setup({ delay: null })
    installedUrls = installBackend()
    renderSearch()
    await screen.findByRole('heading', { level: 1, name: 'Search' })

    const filters = screen.getByRole('region', { name: 'Filter by kind' })
    await user.click(within(filters).getByRole('button', { name: 'Task' }))
    await user.click(within(filters).getByRole('button', { name: 'Risk' }))

    await typeIntoBox(QUERY)
    await waitForSearch()

    const call = searchCalls(installedUrls).at(-1)
    expect(call?.searchParams.getAll('types')).toEqual(['task', 'risk'])
    expect(call?.searchParams.get('q')).toBe(QUERY)
    expect(call?.searchParams.get('limit')).toBe('50')
    expect(call?.searchParams.get('offset')).toBe('0')

    // `?types=` is a repeated key, never a comma-joined one: the backend parses
    // it as a list and would read `types=task,risk` as one unknown kind.
    expect(call?.search.includes('types=task&types=risk')).toBe(true)

    // The chips now report themselves as pressed, and the reset clears them.
    expect(within(filters).getByRole('button', { name: 'Task' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    await user.click(within(filters).getByRole('button', { name: 'All kinds' }))
    await waitFor(() =>
      expect(searchCalls(installedUrls).at(-1)?.searchParams.getAll('types')).toEqual([]),
    )
  }, 15_000)

  it('renders the hits grouped by kind, one block per kind present on the page', async () => {
    installedUrls = installBackend()
    renderSearch(`/search?q=${QUERY}`)
    await screen.findByRole('link', { name: 'Record 1' })

    // The groups are the server's, partitioning this same page: eleven kinds
    // cycle through 25 rows, so every kind appears exactly twice. The filter
    // region's heading is named in its own right and is not a result group.
    expect(screen.getByRole('heading', { name: 'Filter by kind' })).toBeInTheDocument()
    const headings = screen
      .getAllByRole('heading', { level: 2 })
      .map((heading) => heading.textContent)
      .filter((text) => text !== 'Filter by kind')
    expect(headings).toEqual(SEARCH_ENTITY_KINDS.map((kind) => SEARCH_KIND_META[kind].plural))

    // The corpus walks the eleven kinds twice and a third time, so the first
    // three groups hold three rows and the rest hold two. A `groups` list built
    // per run of adjacent hits would instead produce twenty-five one-row groups.
    expect(screen.getAllByText('3 on this page')).toHaveLength(3)
    expect(screen.getAllByText('2 on this page')).toHaveLength(SEARCH_ENTITY_KINDS.length - 3)
    // Grouped by default: the rank column belongs to the flat view only.
    expect(screen.queryByText('#1')).toBeNull()
    expect(screen.getByText('25 results for “atlas”')).toBeInTheDocument()
  })

  it('renders the same page as a flat ranked list when grouping is off', async () => {
    const user = userEvent.setup({ delay: null })
    installedUrls = installBackend()
    renderSearch(`/search?q=${QUERY}`)
    await screen.findByRole('link', { name: 'Record 1' })

    await user.click(screen.getByRole('switch', { name: 'Group by kind' }))

    expect(await screen.findByRole('heading', { name: 'Ranked results' })).toBeInTheDocument()
    // The kind is still named on every row — the switch changes the layout, not
    // the information.
    expect(screen.queryByRole('heading', { name: 'projects' })).toBeNull()

    const list = screen.getByRole('list')
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(25)
    // Three of the twenty-five rows are projects, each still naming its kind.
    expect(within(list).getAllByText('Project')).toHaveLength(3)
    expect(rows[0]?.textContent).toContain('#1')
    expect(rows[24]?.textContent).toContain('#25')
  })

  it('shows each hit with its kind, title, snippet, project and date', async () => {
    // The offsets are the backend's: they index into the snippet itself, so a
    // fixture that hard-codes them goes stale the moment the sentence changes.
    const snippet = 'The migration plan covers every service that reads Atlas.'
    const at = snippet.indexOf('Atlas')
    const task = makeHit({
      kind: 'task',
      id: TASK_ID,
      title: 'Draft the migration plan',
      snippet,
      match_start: at,
      match_end: at + 'Atlas'.length,
      project_name: 'Atlas rollout',
      relative_date: '4 days ago',
    })

    installedUrls = installBackend(() => json(pageOf([task], { total: 1 })))
    renderSearch(`/search?q=${QUERY}`)
    await screen.findByRole('link', { name: 'Draft the migration plan' })

    const link = screen.getByRole('link', { name: 'Draft the migration plan' })
    const row = link.closest('li') as HTMLElement
    expect(row).not.toBeNull()

    // Icon plus the word, never colour alone.
    expect(within(row).getByText('Task')).toBeInTheDocument()

    // The matched region is marked by offset, and the snippet stays plain text:
    // it is whatever the user wrote, so it is never interpreted as markup.
    const mark = within(row).getByText('Atlas')
    expect(mark.tagName.toLowerCase()).toBe('mark')
    expect(row.textContent).toContain('The migration plan covers every service that reads Atlas.')

    expect(within(row).getByText('in Atlas rollout')).toBeInTheDocument()
    expect(within(row).getByText('4 days ago')).toBeInTheDocument()

    // A task has no detail route in this product, so it opens the task list.
    expect(link).toHaveAttribute('href', '/tasks')
  })

  it('links a note, a concept and a repository to their own detail pages', async () => {
    installedUrls = installBackend(() =>
      json(
        pageOf([
          makeHit({ kind: 'note', id: NOTE_ID, title: 'A note' }),
          makeHit({ kind: 'concept', id: NOTE_ID, title: 'A concept' }),
          makeHit({ kind: 'repository', id: NOTE_ID, title: 'A repository' }),
        ]),
      ),
    )
    renderSearch(`/search?q=${QUERY}`)

    expect(await screen.findByRole('link', { name: 'A note' })).toHaveAttribute(
      'href',
      `/knowledge/notes/${NOTE_ID}`,
    )
    expect(screen.getByRole('link', { name: 'A concept' })).toHaveAttribute(
      'href',
      `/knowledge/concepts/${NOTE_ID}`,
    )
    expect(screen.getByRole('link', { name: 'A repository' })).toHaveAttribute(
      'href',
      `/developer/${NOTE_ID}`,
    )
  })

  it('pages with limit and offset, and submitting returns to the first page', async () => {
    const user = userEvent.setup({ delay: null })
    installedUrls = installBackend()
    const { router } = renderSearch(`/search?q=${QUERY}`)
    await screen.findByRole('link', { name: 'Record 1' })

    // First page of twenty-five at fifty a page: nothing after it.
    expect(screen.getByText('Page 1 of 1')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()

    await user.selectOptions(screen.getByLabelText('Per page'), '10')
    await waitFor(() => expect(screen.getByText('Page 1 of 3')).toBeInTheDocument())
    expect(searchCalls(installedUrls).at(-1)?.searchParams.get('limit')).toBe('10')
    expect(searchCalls(installedUrls).at(-1)?.searchParams.get('offset')).toBe('0')

    await user.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(screen.getByText('Page 2 of 3')).toBeInTheDocument())
    expect(router.state.location.search).toContain('offset=10')
    expect(searchCalls(installedUrls).at(-1)?.searchParams.get('offset')).toBe('10')
    // The second page is a different page of the union, not a per-kind page.
    expect(await screen.findByRole('link', { name: 'Record 11' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Previous' }))
    await waitFor(() => expect(screen.getByText('Page 1 of 3')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(screen.getByText('Page 2 of 3')).toBeInTheDocument())

    // The submit affordance works on Enter, and it re-reads the search from the
    // top rather than leaving the reader on a page they have not seen.
    await user.click(screen.getByLabelText('Search NEXUS'))
    await user.keyboard('{Enter}')
    await waitFor(() => expect(screen.getByText('Page 1 of 3')).toBeInTheDocument())
    expect(router.state.location.search).not.toContain('offset')
  }, 15_000)

  it('invites a search before one has been asked', async () => {
    installedUrls = installBackend()
    renderSearch()

    expect(await screen.findByText('Search everything you own')).toBeInTheDocument()
    expect(
      screen.getByText(/Type at least one character\. A single word is enough/),
    ).toBeInTheDocument()
  })

  it('states that a term matched nothing across every kind', async () => {
    installedUrls = installBackend(() => json(pageOf([], { total: 0 })))
    renderSearch(`/search?q=${QUERY}`)

    expect(await screen.findByText('Nothing matched')).toBeInTheDocument()
    expect(screen.getByText(/No record you own contains “atlas”/)).toBeInTheDocument()
  })

  it('separates a search narrowed to kinds that hold nothing from one that found nothing', async () => {
    const user = userEvent.setup({ delay: null })
    installedUrls = installBackend(() => json(pageOf([], { total: 0 })))
    const { router } = renderSearch(`/search?q=${QUERY}`)
    await screen.findByText('Nothing matched')

    // Conflating the two is how a filtered search reads as an empty account.
    const filters = screen.getByRole('region', { name: 'Filter by kind' })
    await user.click(within(filters).getByRole('button', { name: 'Skill' }))

    expect(await screen.findByText('Nothing matched within the selected kinds')).toBeInTheDocument()
    expect(screen.getByText(/^No skills match “atlas”\./)).toBeInTheDocument()
    expect(screen.queryByText(/^Nothing matched$/)).toBeNull()

    // The empty state offers the way back out, and taking it widens the search.
    // Two "All kinds" buttons exist here — the filter chip and the empty state's
    // own action — so the click is scoped to the filter row.
    await user.click(within(filters).getByRole('button', { name: 'All kinds' }))
    // Asserted on the URL, not on the wire: clearing the filter returns to the
    // *same* query that already ran, and a 30 s `staleTime` means React Query
    // answers it from cache rather than refetching — so there is no new request
    // to find, and its absence is correct behaviour rather than a missing one.
    await waitFor(() => expect(router.state.location.search).not.toContain('types'))
    expect(within(filters).getByRole('button', { name: 'Skill' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
    expect(await screen.findByText('Nothing matched')).toBeInTheDocument()
  }, 15_000)

  it('reports a failed search with a retry that asks again and recovers', async () => {
    const user = userEvent.setup({ delay: null })
    let failing = true
    installedUrls = installBackend(() =>
      failing
        ? envelope(
            'forbidden',
            'Reading across every module needs analytics.read.',
            403,
            'req-search-1',
          )
        : json(pageOf(CORPUS.slice(0, 3), { total: 3 })),
    )
    renderSearch(`/search?q=${QUERY}`)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The search could not run')
    expect(alert).toHaveTextContent('Reading across every module needs analytics.read.')
    expect(alert).toHaveTextContent('req-search-1')

    failing = false
    await user.click(screen.getByRole('button', { name: /Retry/i }))
    expect(await screen.findByRole('link', { name: 'Record 1' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  }, 15_000)

  it('announces the settled count in a live region that exists before it', async () => {
    installedUrls = installBackend()
    renderSearch()
    await screen.findByRole('heading', { level: 1, name: 'Search' })

    // Mounted empty: a live region has to be in the tree before its first update.
    const live = screen.getByRole('status')
    expect(live).toHaveAttribute('aria-live', 'polite')
    expect(live).toHaveTextContent('')

    await typeIntoBox(QUERY)
    await screen.findByRole('link', { name: 'Record 1' })

    await waitFor(() => expect(live).toHaveTextContent('25 results for atlas.'))
  })

  it('never prints undefined, NaN or a raw response body', async () => {
    // A hit that arrives without the optional fields must not blank the row.
    installedUrls = installBackend(() =>
      json(
        pageOf(
          [
            {
              kind: 'goal',
              id: TASK_ID,
              title: 'A goal with nothing optional set',
              snippet: 'Ship the atlas rewrite.',
              match_start: 0,
              match_end: 5,
              matched_field: 'name',
              project_id: null,
              project_name: null,
              relative_date: null,
              updated_at: '2026-01-09T08:05:00Z',
            },
          ],
          { total: 1 },
        ),
      ),
    )
    renderSearch(`/search?q=${QUERY}`)
    await screen.findByRole('heading', { name: 'A goal with nothing optional set' })
    await settle(0)

    // No project name and no relative date: the date falls back to the ISO day,
    // and the absent project is simply not printed.
    expect(screen.getByText('2026-01-09')).toBeInTheDocument()
    expect(screen.queryByText(/^in /)).toBeNull()

    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/undefined/)
    expect(text).not.toMatch(/NaN|Infinity/)
  })
})