import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/tooltip'
import KnowledgePage from '@/pages/knowledge-page'
import { queryRetryPolicy } from '@/app/query-client'
import type { ApiErrorEnvelope } from '@/types/api'
import type { Bookmark, Concept, Note, Resource } from '@/types/knowledge'
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
  return { items, meta: { total: items.length, limit: 25, offset: 0 } }
}

/**
 * The four sort allowlists as the service enforces them
 * (`app/services/knowledge_service.py::_SORT_KEYS`). The stub 422s anything else
 * with the real envelope, so a page that sends a foreign key fails here exactly
 * as it does against the API rather than quietly getting a 200.
 */
const SORT_ALLOWLIST: Record<string, readonly string[]> = {
  note: ['created_at', 'status', 'title', 'updated_at'],
  concept: ['created_at', 'name', 'updated_at'],
  resource: ['created_at', 'resource_type', 'title', 'updated_at'],
  bookmark: ['archived_at', 'created_at', 'domain', 'updated_at'],
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

describe('knowledge page — sort the endpoint will accept', () => {
  it('sends a sort each list accepts on a stale shared link, and never 422s', async () => {
    failing.clear()
    const calls = installBackend()

    renderKnowledge('/knowledge?tab=bookmarks&sort=name')
    await settle()
    await screen.findByText('Atlas')

    const bookmarkLists = calls.filter(
      (call) => call.method === 'GET' && call.url.includes('/knowledge/bookmarks?'),
    )
    expect(bookmarkLists.length).toBeGreaterThan(0)
    for (const call of bookmarkLists) {
      expect(call.url).toContain('sort=created_at')
    }
    // No red panel, and no Retry that could only fail the same way again.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('drops the sort with the tab so the next list asks for its own ordering', async () => {
    failing.clear()
    const calls = installBackend()
    const user = userEvent.setup()

    renderKnowledge('/knowledge')
    await settle()
    await screen.findByText('Keyboard runbook')

    // `Title` is valid for notes; on the Concepts tab it is a 422.
    await user.selectOptions(screen.getByLabelText('Sort'), 'title')
    await settle()
    await user.click(screen.getByRole('tab', { name: 'Concepts' }))
    await settle()
    await screen.findByText('async')

    const conceptLists = calls.filter(
      (call) => call.method === 'GET' && call.url.includes('/knowledge/concepts?'),
    )
    expect(conceptLists.length).toBeGreaterThan(0)
    for (const call of conceptLists) expect(call.url).toContain('sort=name')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('knowledge page — the Resources Type filter', () => {
  it('narrows the rows it shows and says the filter runs over the fetched window', async () => {
    failing.clear()
    const calls = installBackend()
    const user = userEvent.setup()

    renderKnowledge('/knowledge?tab=resources')
    await settle()
    await screen.findByText('MDN keyboard events')

    expect(screen.getByText('No URL resource')).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Type'), 'documentation')
    await settle()

    // The one documentation resource, and not the `other` one.
    expect(screen.getByText('MDN keyboard events')).toBeInTheDocument()
    expect(screen.queryByText('No URL resource')).not.toBeInTheDocument()
    expect(screen.getByText(/1 of 2 shown/)).toBeInTheDocument()

    // `GET /knowledge/resources` declares no `resource_type`, so a page that
    // filtered server-side would be sending a parameter the service drops.
    for (const call of calls.filter((row) => row.url.includes('/knowledge/resources'))) {
      expect(call.url).not.toContain('resource_type')
    }
  })

  it('clears back to every type from the same control', async () => {
    failing.clear()
    installBackend()
    const user = userEvent.setup()

    renderKnowledge('/knowledge?tab=resources&type=other')
    await settle()
    await screen.findByText('No URL resource')
    expect(screen.queryByText('MDN keyboard events')).not.toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Type'), '')
    await settle()

    expect(screen.getByText('MDN keyboard events')).toBeInTheDocument()
    expect(screen.getByText('No URL resource')).toBeInTheDocument()
  })
})

describe('knowledge page — every delete names its record', () => {
  it('gives each row a delete button that says which record it deletes', async () => {
    failing.clear()
    installBackend()

    renderKnowledge('/knowledge?tab=resources')
    await settle()
    await screen.findByText('MDN keyboard events')

    const rows = screen.getAllByRole('listitem').filter((row) => within(row).queryByRole('button'))
    expect(rows.length).toBeGreaterThan(0)

    const labels = screen
      .getAllByRole('button')
      .map((button) => button.getAttribute('aria-label') ?? button.textContent ?? '')
      .filter((label) => label.startsWith('Delete'))

    expect(labels).toContain('Delete MDN keyboard events')
    expect(labels).not.toContain('Delete undefined')
    expect(new Set(labels).size).toBe(labels.length)
  })
})