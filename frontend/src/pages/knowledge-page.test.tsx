import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/tooltip'
import KnowledgePage from '@/pages/knowledge-page'
import { queryRetryPolicy } from '@/app/query-client'
import type { ApiErrorEnvelope } from '@/types/api'
import type {
  Bookmark,
  Concept,
  Note,
  Resource,
} from '@/types/knowledge'
import type { WorkTag } from '@/types/work'

/**
 * The Knowledge page, asserted at the network boundary.
 *
 * Real router, real components, real hooks; only `fetch` is stubbed. The three
 * things pinned here are all requests the page used to get wrong rather than
 * copy it used to render wrong:
 *
 * - `sort` is one URL parameter shared by four lists with four allowlists, and a
 *   value from one of them is a 422 for the other three.
 * - `GET /knowledge/resources` declares no `resource_type`, so the Type control
 *   has to narrow the window it fetched rather than send a parameter.
 * - Every delete row names the record it deletes.
 *
 * **The query client is local.** `AppProviders` mounts the shared singleton,
 * whose `onSessionChange(() => queryClient.clear())` clears the cache mid-test in
 * jsdom; a fresh client per render avoids that, with the shipped retry policy
 * carried over rather than relaxed.
 */

const NOTE_ID = '11111111-1111-4111-8111-111111111111'
const OTHER_NOTE_ID = '22222222-2222-4222-8222-222222222222'
const RESOURCE_ID = '33333333-3333-4333-8333-333333333333'
const BOOKMARK_ID = '44444444-4444-4444-8444-444444444444'
const CONCEPT_ID = '55555555-5555-4555-8555-555555555555'

function note(overrides: Partial<Note> & Pick<Note, 'id' | 'title'>): Note {
  return {
    owner_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    content: 'body',
    summary: null,
    status: 'draft',
    document_id: null,
    created_at: '2026-01-08T08:00:00Z',
    updated_at: '2026-01-09T08:00:00Z',
    tag_ids: [],
    revision_count: 0,
    is_archived: false,
    ...overrides,
  }
}

const NOTES = [
  note({ id: NOTE_ID, title: 'Keyboard runbook' }),
  note({ id: OTHER_NOTE_ID, title: 'Onboarding checklist' }),
]

const RESOURCES: Resource[] = [
  {
    id: RESOURCE_ID,
    owner_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    title: 'MDN keyboard events',
    description: 'Reference material.',
    url: 'https://developer.mozilla.org/keyboard',
    resource_type: 'documentation',
    created_at: '2026-01-08T08:00:00Z',
    updated_at: '2026-01-09T08:00:00Z',
  },
  {
    id: '66666666-6666-4666-8666-666666666666',
    owner_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    title: 'No URL resource',
    description: null,
    url: null,
    resource_type: 'other',
    created_at: '2026-01-08T08:00:00Z',
    updated_at: '2026-01-09T08:00:00Z',
  },
]

const BOOKMARKS: Bookmark[] = [
  {
    id: BOOKMARK_ID,
    owner_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    url: 'https://example.test/atlas',
    title: 'Atlas',
    description: null,
    domain: 'example.test',
    archived_at: null,
    created_at: '2026-01-08T08:00:00Z',
    updated_at: '2026-01-09T08:00:00Z',
  },
]

const CONCEPTS: Concept[] = [
  {
    id: CONCEPT_ID,
    owner_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    name: 'async',
    description: 'Concurrency without threads.',
    created_at: '2026-01-08T08:00:00Z',
    updated_at: '2026-01-09T08:00:00Z',
    tag_ids: [],
  },
]

/**
 * The shared `/tags` vocabulary.
 *
 * Typed `WorkTag` from `@/types/work`, which is what the page actually reads:
 * tags live outside the knowledge base and reach this page through `useTags()`
 * in `@/features/work/hooks`. There is no `WorkTagRead` in the tree — the type
 * the knowledge types once imported belonged to a tag model that no longer
 * exists here — and the fields are `task_count`/`project_count` rather than an
 * `updated_at`, because a tag has no `updated_at` to report.
 */
const TAGS: WorkTag[] = [
  {
    id: '77777777-7777-4777-8777-777777777777',
    name: 'runbook',
    created_at: '2026-01-08T08:00:00Z',
    task_count: 0,
    project_count: 0,
  },
]

interface Page<T> {
  items: T[]
  meta: { total: number; limit: number; offset: number }
}

/** The window the page fetches a list in, and so the one a list request carries. */
const LIST_LIMIT = 25

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function envelope(code: string, message: string, status: number): Response {
  const body: ApiErrorEnvelope = {
    error: { code, message, details: null, request_id: 'req-knowledge' },
  }
  return json(body, status)
}

function page<T>(items: T[]): Page<T> {
  return { items, meta: { total: items.length, limit: LIST_LIMIT, offset: 0 } }
}

/**
 * The four sort allowlists as the service enforces them
 * (`app/services/knowledge_service.py::_SORT_KEYS`). The stub 422s anything else
 * with the real envelope, so a page that sends a foreign key fails here exactly
 * as it does against the API rather than quietly getting a 200.
 *
 * **Keyed by the collection the stub resolves a URL to, not by the service's own
 * entity name.** `_SORT_KEYS` is keyed `note`/`concept`/`resource`/`bookmark`;
 * {@link COLLECTION} is keyed by the URL segment, which is the plural. Reading
 * one with the other's key silently produced `undefined` here, and since every
 * list request carries a `sort`, `SORT_ALLOWLIST[collection]!.includes(sort)`
 * threw `TypeError` inside the stub — which the page could not tell from a
 * transport failure, so the shipped `queryRetryPolicy` backed off and retried
 * while the panel sat on its skeleton. The `limit=1` count queries send no
 * `sort` and short-circuit past this line, which is why the stat tiles rendered
 * and made a dead list look like a live page.
 */
const SORT_ALLOWLIST: Record<string, readonly string[]> = {
  notes: ['created_at', 'status', 'title', 'updated_at'],
  concepts: ['created_at', 'name', 'updated_at'],
  resources: ['created_at', 'resource_type', 'title', 'updated_at'],
  bookmarks: ['archived_at', 'created_at', 'domain', 'updated_at'],
}

const COLLECTION: Record<string, Page<unknown>> = {
  notes: page(NOTES),
  concepts: page(CONCEPTS),
  resources: page(RESOURCES),
  bookmarks: page(BOOKMARKS),
}

interface Call {
  url: string
  method: string
}

/** Which collections answered, so one test can reject exactly one of them. */
const failing = new Set<string>()

function installBackend(): Call[] {
  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      calls.push({ url, method })

      if (method !== 'GET') return envelope('not_found', 'No write is stubbed here.', 404)
      if (url.includes('/tags')) return json(page(TAGS))

      const collection = Object.keys(COLLECTION).find((name) =>
        url.includes(`/knowledge/${name}`),
      )
      if (collection === undefined) return json({ nodes: [], edges: [], limit: 200, truncated: false })

      if (failing.has(collection)) {
        return envelope('validation_error', 'This collection is down in this test.', 500)
      }

      const sort = new URL(url, 'http://localhost').searchParams.get('sort')
      if (sort !== null && !SORT_ALLOWLIST[collection]!.includes(sort)) {
        return envelope(
          'validation_error',
          `Cannot sort ${collection} by '${sort}'.`,
          422,
        )
      }
      return json(COLLECTION[collection])
    }),
  )
  return calls
}

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

function renderKnowledge(entry = '/knowledge') {
  const router = createMemoryRouter([{ path: '*', element: <KnowledgePage /> }], {
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

async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 80))
  })
}

/**
 * The open tab's list, and nothing else.
 *
 * Radix keeps every `TabsContent` mounted and hides the inactive ones, so
 * `getByRole('tabpanel')` resolves to exactly the panel on screen — and the
 * dashboard above it has panels of its own, which a page-wide `getByText` cannot
 * tell apart from the list. A note is on this page twice by design: once as its
 * row in the Notes list and once in "Recently updated", which reads the same
 * window. `findByText('Keyboard runbook')` therefore matched two nodes and
 * `waitFor` kept retrying the ambiguity instead of giving up, which is how a test
 * with nothing wrong on it timed out at 20s.
 */
function openTab(): HTMLElement {
  return screen.getByRole('tabpanel')
}

/**
 * Asserts no failure panel, once the page has finished reacting to the request.
 *
 * A bare `queryByRole('alert')` samples one instant, so on a list that answers in
 * two ticks it passes against a page that is about to paint its error — which is
 * the failure these tests exist to catch. Waiting for the same assertion lets
 * React Query retry once and settle first, so "never 422s" is checked against a
 * finished render rather than the first one.
 */
async function waitForNoAlert(): Promise<void> {
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
}

describe('knowledge page — sort the endpoint will accept', () => {
  it('sends a sort each list accepts on a stale shared link, and never 422s', async () => {
    failing.clear()
    const calls = installBackend()

    renderKnowledge('/knowledge?tab=bookmarks&sort=name')
    await settle()
    await within(openTab()).findByText('Atlas')

    // `limit=1` is the dashboard's count tile, not the list: it asks for a
    // number, never for an ordering, so a `sort` assertion over every bookmarks
    // URL would be asserting something about a request that never had one.
    const bookmarkLists = calls.filter(
      (call) =>
        call.method === 'GET' &&
        call.url.includes('/knowledge/bookmarks?') &&
        call.url.includes(`limit=${LIST_LIMIT}`),
    )
    expect(bookmarkLists.length).toBeGreaterThan(0)
    for (const call of bookmarkLists) {
      expect(call.url).toContain('sort=created_at')
    }
    // No red panel, and no Retry that could only fail the same way again.
    await waitForNoAlert()
  })

  it('drops the sort with the tab so the next list asks for its own ordering', async () => {
    failing.clear()
    const calls = installBackend()
    const user = userEvent.setup()

    renderKnowledge('/knowledge')
    await settle()
    await within(openTab()).findByText('Keyboard runbook')

    // `Title` is valid for notes; on the Concepts tab it is a 422.
    await user.selectOptions(screen.getByLabelText('Sort'), 'title')
    await settle()
    await user.click(screen.getByRole('tab', { name: 'Concepts' }))
    await settle()
    await within(openTab()).findByText('async')

    const conceptLists = calls.filter(
      (call) =>
        call.method === 'GET' &&
        call.url.includes('/knowledge/concepts?') &&
        call.url.includes(`limit=${LIST_LIMIT}`),
    )
    expect(conceptLists.length).toBeGreaterThan(0)
    for (const call of conceptLists) expect(call.url).toContain('sort=name')
    await waitForNoAlert()
  })
})

describe('knowledge page — the Resources Type filter', () => {
  it('narrows the rows it shows and says the filter runs over the fetched window', async () => {
    failing.clear()
    const calls = installBackend()
    const user = userEvent.setup()

    renderKnowledge('/knowledge?tab=resources')
    await settle()
    await within(openTab()).findByText('MDN keyboard events')

    expect(within(openTab()).getByText('No URL resource')).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Type'), 'documentation')
    await settle()

    // The one documentation resource, and not the `other` one.
    expect(within(openTab()).getByText('MDN keyboard events')).toBeInTheDocument()
    expect(within(openTab()).queryByText('No URL resource')).not.toBeInTheDocument()
    expect(screen.getByText(/1 of 2 shown/)).toBeInTheDocument()

    // `GET /knowledge/resources` declares no `resource_type`, so a page that
    // filtered server-side would be sending a parameter the service drops.
    for (const call of calls.filter(
      (row) => row.url.includes('/knowledge/resources') && row.url.includes(`limit=${LIST_LIMIT}`),
    )) {
      expect(call.url).not.toContain('resource_type')
    }
  })

  it('clears back to every type from the same control', async () => {
    failing.clear()
    installBackend()
    const user = userEvent.setup()

    renderKnowledge('/knowledge?tab=resources&type=other')
    await settle()
    await within(openTab()).findByText('No URL resource')
    expect(within(openTab()).queryByText('MDN keyboard events')).not.toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Type'), '')
    await settle()

    expect(within(openTab()).getByText('MDN keyboard events')).toBeInTheDocument()
    expect(within(openTab()).getByText('No URL resource')).toBeInTheDocument()
  })
})

describe('knowledge page — every delete names its record', () => {
  it('gives each row a delete button that says which record it deletes', async () => {
    failing.clear()
    installBackend()

    renderKnowledge('/knowledge?tab=resources')
    await settle()
    await within(openTab()).findByText('MDN keyboard events')

    const panel = within(openTab())
    const rows = panel.getAllByRole('listitem').filter((row) => within(row).queryByRole('button'))
    expect(rows.length).toBeGreaterThan(0)

    const labels = panel
      .getAllByRole('button')
      .map((button) => button.getAttribute('aria-label') ?? button.textContent ?? '')
      .filter((label) => label.startsWith('Delete'))

    expect(labels).toContain('Delete MDN keyboard events')
    expect(labels).not.toContain('Delete undefined')
    expect(new Set(labels).size).toBe(labels.length)
  })
})