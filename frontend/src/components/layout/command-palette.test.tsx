import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useEffect } from 'react'

import { CommandPalette } from '@/components/layout/command-palette'
import { useCommandPaletteStore } from '@/features/command-palette/command-palette-store'
import type { SearchHit } from '@/features/command-palette/search'
import { useThemeStore } from '@/stores/theme-store'

/**
 * The command palette, against a stubbed backend.
 *
 * Three things are being defended here, and each has a test a plausible
 * simplification would fail:
 *
 * - **It is modal.** A palette that leaves the page behind tabbable lets a
 *   keyboard user tab out of an element that visually covers everything.
 *   `inert` and the focus cycle are the two halves of that.
 * - **One list, three kinds.** The arrow keys and `aria-activedescendant` index
 *   a single flattened array, so a record returned by the search endpoint has to
 *   be reachable with the same keys as a destination.
 * - **Nothing happens by accident.** The search fires only for a query that
 *   could return something, only once the typing settles, and a quick action
 *   only posts from inside its own form.
 */

const LISTBOX = 'Commands, destinations and records'

function hit(overrides: Partial<SearchHit> = {}): SearchHit {
  return {
    kind: 'task',
    id: '44444444-4444-4444-8444-444444444444',
    title: 'Rotate the staging credentials',
    snippet: 'The shared credentials have to stop being shared.',
    match_start: 0,
    match_end: 4,
    matched_field: 'title',
    project_id: '11111111-1111-4111-8111-111111111111',
    project_name: 'Atlas',
    relative_date: 'today',
    updated_at: '2026-02-01T10:00:00Z',
    ...overrides,
  }
}

function searchBody(hits: SearchHit[]) {
  return {
    query: 'atlas',
    hits,
    groups: hits.map((entry) => ({ kind: entry.kind, hits: [entry] })),
    meta: { total: hits.length, limit: 10, offset: 0 },
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

interface Recorded {
  url: string
  method: string
}

/** Routes the palette's request shapes: search, projects, and anything else. */
function stubFetch(
  handler: (url: string, method: string) => Response = (url, method) =>
    url.includes('/search')
      ? json(searchBody([]))
      : method === 'POST'
        ? json({})
        : json({ items: [], meta: { total: 0 } }),
): Recorded[] {
  const calls: Recorded[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      calls.push({ url, method })
      return handler(url, method)
    }),
  )
  return calls
}

/** Fetches that went to `GET /api/v1/search`. */
function searchCalls(calls: Recorded[]): Recorded[] {
  return calls.filter((call) => call.url.includes('/search'))
}

/**
 * Every path the router has been at, in order.
 *
 * Written from an effect rather than during render, and onto an array rather
 * than a binding, so the hooks lint sees the mutation where it belongs.
 */
const locations: string[] = []

function LocationProbe() {
  const { pathname } = useLocation()
  useEffect(() => {
    locations.push(pathname)
  }, [pathname])
  return null
}

function renderPalette() {
  // A fresh client per render: a cached hit list from a previous test would
  // answer this one before the stub was ever consulted.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/dashboard']}>
        <LocationProbe />
        <CommandPalette />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function input(): HTMLInputElement {
  return screen.getByRole('combobox')
}

function dialog(): HTMLElement {
  return screen.getByRole('dialog')
}

/**
 * Every element on `document.body` that is not the palette — the page the
 * palette is covering. Once the palette closes, everything on `body` counts.
 */
function backgroundRoots(): HTMLElement[] {
  const palette = screen.queryByRole('dialog', { hidden: true })
  return Array.from(document.body.children).filter(
    (child) => !palette || !(child as HTMLElement).contains(palette),
  ) as HTMLElement[]
}

beforeEach(() => {
  locations.length = 0
  window.localStorage.clear()
  useThemeStore.setState({ preference: 'light', resolvedTheme: 'light' })
  useCommandPaletteStore.setState({ open: true })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('command palette results', () => {
  it('renders destinations, quick actions and records in one grouped list', async () => {
    stubFetch((url) =>
      url.includes('/search') ? json(searchBody([hit()])) : json({ items: [] }),
    )
    renderPalette()

    const listbox = screen.getByRole('listbox', { name: LISTBOX })
    expect(within(listbox).getByRole('group', { name: 'Quick actions' })).toBeInTheDocument()
    expect(within(listbox).getByRole('group', { name: 'Destinations' })).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Your records' })).not.toBeInTheDocument()

    // "atlas" matches no destination and no command, so for a moment the only
    // honest thing to show is that the records are still being asked.
    fireEvent.change(input(), { target: { value: 'atlas' } })

    await waitFor(() =>
      expect(screen.getByRole('group', { name: 'Your records' })).toBeInTheDocument(),
    )
    const records = screen.getByRole('group', { name: 'Your records' })
    expect(within(records).getByText('Rotate the staging credentials')).toBeInTheDocument()
    // The record is labelled by the kind the API named, not by our guess.
    expect(within(records).getByText('Task')).toBeInTheDocument()
  })

  it('shows every kind that needs no query and spends no request on it', async () => {
    const calls = stubFetch()
    renderPalette()

    expect(screen.getByRole('option', { name: /^Dashboard/ })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /^New task/ })).toBeInTheDocument()

    // A blank box cannot match a row, so asking the backend about it is pure cost.
    await new Promise((resolve) => setTimeout(resolve, 400))
    expect(searchCalls(calls)).toHaveLength(0)
  })

  it('searches only after the typing settles, and only once', async () => {
    const calls = stubFetch((url) =>
      url.includes('/search') ? json(searchBody([hit()])) : json({ items: [] }),
    )
    renderPalette()

    // Typed faster than the 250 ms debounce, so only the settled term is sent.
    fireEvent.change(input(), { target: { value: 'a' } })
    fireEvent.change(input(), { target: { value: 'at' } })
    fireEvent.change(input(), { target: { value: 'atl' } })
    fireEvent.change(input(), { target: { value: 'atlas' } })

    expect(searchCalls(calls)).toHaveLength(0)

    // Half the debounce has gone and nothing has been sent: the palette waits
    // for the typing to stop rather than asking per keystroke.
    await new Promise((resolve) => setTimeout(resolve, 100))
    expect(searchCalls(calls)).toHaveLength(0)

    await waitFor(() => expect(searchCalls(calls)).toHaveLength(1))
    expect(searchCalls(calls)[0]?.url).toContain('q=atlas')

    // Still one: nothing since the term settled asked again.
    await new Promise((resolve) => setTimeout(resolve, 400))
    expect(searchCalls(calls)).toHaveLength(1)
  })

  it('drops the listbox entirely and says so when nothing matches', async () => {
    stubFetch((url) => (url.includes('/search') ? json(searchBody([])) : json({ items: [] })))
    renderPalette()

    fireEvent.change(input(), { target: { value: 'zzzzqqq' } })

    expect(await screen.findByText('Nothing matches')).toBeInTheDocument()
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    // An id naming an element that is not rendered is worse than no id at all.
    expect(input()).not.toHaveAttribute('aria-controls')
    expect(input()).not.toHaveAttribute('aria-activedescendant')
    expect(input()).toHaveAttribute('aria-expanded', 'false')
  })
})

describe('command palette keyboard', () => {
  it('tracks the active option with aria-activedescendant', () => {
    stubFetch()
    renderPalette()

    const listbox = screen.getByRole('listbox', { name: LISTBOX })
    const options = within(listbox).getAllByRole('option')
    const first = options[0]
    const second = options[1]

    expect(first?.getAttribute('data-kind')).toBe('action')
    expect(input()).toHaveAttribute('aria-activedescendant', first?.id)
    expect(first).toHaveAttribute('aria-selected', 'true')

    fireEvent.keyDown(dialog(), { key: 'ArrowDown' })

    expect(input()).toHaveAttribute('aria-activedescendant', second?.id)
    expect(second).toHaveAttribute('data-active', 'true')
    expect(first).toHaveAttribute('aria-selected', 'false')
  })

  it('reaches a record from the search endpoint with the arrow keys', async () => {
    stubFetch((url) =>
      url.includes('/search') ? json(searchBody([hit()])) : json({ items: [] }),
    )
    renderPalette()

    // "dashboard" matches a destination locally; the search endpoint answers the
    // same word with a record, so both kinds share one index space.
    fireEvent.change(input(), { target: { value: 'dashboard' } })
    const record = await screen.findByRole('option', { name: /Rotate the staging credentials/ })

    const all = screen.getAllByRole('option')
    const index = all.indexOf(record)
    expect(index).toBeGreaterThan(0)
    expect(all[index - 1]?.getAttribute('data-kind')).toBe('navigate')

    for (let step = 0; step < index; step += 1) {
      fireEvent.keyDown(dialog(), { key: 'ArrowDown' })
    }
    expect(input()).toHaveAttribute('aria-activedescendant', record.id)

    // One more ArrowDown wraps past the bottom of the whole list.
    fireEvent.keyDown(dialog(), { key: 'ArrowDown' })
    expect(input()).toHaveAttribute('aria-activedescendant', all[0]?.id)

    // And one ArrowUp from there wraps back down to it.
    fireEvent.keyDown(dialog(), { key: 'ArrowUp' })
    expect(input()).toHaveAttribute('aria-activedescendant', all[all.length - 1]?.id)
  })

  it('cycles Tab and Shift+Tab inside the palette instead of neutralising them', async () => {
    const user = userEvent.setup()
    stubFetch((url) =>
      url.includes('/projects')
        ? json({
            items: [
              {
                id: '11111111-1111-4111-8111-111111111111',
                name: 'Atlas',
              },
            ],
          })
        : json({ items: [], meta: { total: 0 } }),
    )
    renderPalette()

    // Opened through a quick action, because that is the view with more than
    // one control; a single-control cycle would pass a broken trap too.
    await user.click(screen.getByRole('option', { name: /^New task/ }))
    const first = await screen.findByLabelText('Task title')
    const last = screen.getByRole('button', { name: 'Create' })
    await waitFor(() => expect(last).toBeEnabled())

    last.focus()
    await user.tab()
    expect(document.activeElement).toBe(first)

    await user.tab({ shift: true })
    expect(document.activeElement).toBe(last)

    // Focus never left the dialog on the way through.
    expect(dialog().contains(document.activeElement)).toBe(true)
  })

  it('closes on Escape and hands focus back to the opener', async () => {
    const user = userEvent.setup()
    stubFetch()

    const opener = document.createElement('button')
    opener.textContent = 'open the palette'
    document.body.appendChild(opener)
    opener.focus()

    renderPalette()
    await waitFor(() => expect(input()).toBe(document.activeElement))

    await user.keyboard('{Escape}')

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(document.activeElement).toBe(opener)
    opener.remove()
  })
})

describe('command palette modality', () => {
  it('marks the background inert while open and not after close', async () => {
    stubFetch()
    renderPalette()

    const roots = backgroundRoots()
    expect(roots.length).toBeGreaterThan(0)
    for (const root of roots) {
      expect(root).toHaveAttribute('inert')
    }

    useCommandPaletteStore.getState().setOpen(false)
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    for (const root of backgroundRoots()) {
      expect(root).not.toHaveAttribute('inert')
    }
  })

  it('does not claim the palette itself as inert background', () => {
    stubFetch()
    renderPalette()

    expect(dialog().closest('[inert]')).toBeNull()
    // The backdrop is a pointer affordance, not a stop in the tab ring.
    const backdrop = screen.getByRole('button', { name: 'Close command palette' })
    expect(backdrop).toHaveAttribute('tabindex', '-1')
  })
})

describe('command palette selection', () => {
  it('navigates to the chosen destination', async () => {
    const user = userEvent.setup()
    stubFetch()
    renderPalette()

    await user.click(screen.getByRole('option', { name: /^Knowledge/ }))

    expect(locations.at(-1)).toBe('/knowledge')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('runs an instant quick action and navigates', async () => {
    const user = userEvent.setup()
    stubFetch()
    renderPalette()

    await user.click(screen.getByRole('option', { name: /^Open settings/ }))

    expect(locations.at(-1)).toBe('/settings')
  })

  it('flips the theme locally and writes nothing to the backend', async () => {
    const user = userEvent.setup()
    const calls = stubFetch()
    renderPalette()

    await user.click(screen.getByRole('option', { name: /^Toggle light/ }))

    expect(useThemeStore.getState().resolvedTheme).toBe('dark')
    expect(calls.filter((call) => call.method !== 'GET')).toHaveLength(0)
  })

  it('opens a creating action as a form rather than posting on selection', async () => {
    const user = userEvent.setup()
    const calls = stubFetch()
    renderPalette()

    await user.click(screen.getByRole('option', { name: /^New task/ }))

    expect(await screen.findByLabelText('Task title')).toBeInTheDocument()
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0)
    expect(locations.at(-1)).toBe('/dashboard')
  })

  it('navigates to the page that owns a selected record', async () => {
    const user = userEvent.setup()
    stubFetch((url) =>
      url.includes('/search') ? json(searchBody([hit()])) : json({ items: [] }),
    )
    renderPalette()

    fireEvent.change(input(), { target: { value: 'atlas' } })
    await user.click(await screen.findByRole('option', { name: /Rotate the staging credentials/ }))

    // A task has no detail route, so it lands on the list that owns it.
    expect(locations.at(-1)).toBe('/tasks')
  })

  it('opens a project record on its own detail page', async () => {
    const user = userEvent.setup()
    const projectHit = hit({ kind: 'project', title: 'Atlas migration', project_name: null })
    stubFetch((url) =>
      url.includes('/search') ? json(searchBody([projectHit])) : json({ items: [] }),
    )
    renderPalette()

    fireEvent.change(input(), { target: { value: 'atlas' } })
    await user.click(await screen.findByRole('option', { name: /Atlas migration/ }))

    expect(locations.at(-1)).toBe('/projects/44444444-4444-4444-8444-444444444444')
  })
})
