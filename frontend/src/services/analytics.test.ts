/**
 * Envelope handling on the analytics list reads.
 *
 * The three list routes here do not agree on a shape: `/analytics/projects`
 * answers `Page[ProjectAnalyticsRead]` while `/analytics/trends` and
 * `/analytics/series` answer bare arrays. Typing the first as an array is what
 * took the dashboard down with `rows.slice is not a function`, so what is
 * pinned here is that each read hands its caller the rows and nothing else.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchProjects, fetchSeries, fetchTrends } from './analytics'
import type { ProjectAnalyticsRead, TrendPoint } from '@/types/analytics'

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** Answers every request with `body`, recording the URLs it was asked for. */
function stubBackend(body: unknown): { calls: string[] } {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input))
      return json(body)
    }),
  )
  return { calls }
}

const PROJECT: ProjectAnalyticsRead = {
  project_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  name: 'Atlas',
  status: 'active',
  total_tasks: 10,
  completed_tasks: 8,
  remaining_tasks: 2,
  overdue_tasks: 0,
  completion_rate: 80,
  total_work_minutes: 90,
  avg_task_actual_minutes: 11.25,
  estimation: null,
  velocity: null,
  velocity_tasks_per_week: null,
  weekly_completed: [],
  work_minutes: 90,
  estimated_minutes: 120,
  actual_minutes: 90,
  avg_task_minutes: 9,
  activity_events: 12,
  available: true,
  reason_if_unavailable: null,
  range: null,
}

const POINT: TrendPoint = {
  bucket: '2026-01-01',
  label: 'Jan 1',
  value: 4,
  previous: null,
  absolute_change: null,
  percent_change: null,
  period_start: '2026-01-01',
  period_end: '2026-01-01',
}

/** The `meta.limit` a live `GET /analytics/projects` answers with, no `limit` sent. */
const PAGE_LIMIT = 20

describe('analytics list reads', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('unwraps the items of a Page envelope into rows', async () => {
    stubBackend({ items: [PROJECT], meta: { total: 1, limit: PAGE_LIMIT, offset: 0 } })

    await expect(fetchProjects()).resolves.toEqual([PROJECT])
  })

  it('asks for no page of its own, so it never manufactures a pager', async () => {
    const { calls } = stubBackend({ items: [], meta: { total: 0, limit: PAGE_LIMIT, offset: 0 } })

    await fetchProjects({ start_date: '2026-01-01', end_date: '2026-01-07' })

    expect(calls[0]).toContain('/analytics/projects')
    expect(calls[0]).toContain('start_date=2026-01-01')
    expect(calls[0]).not.toContain('limit=')
    expect(calls[0]).not.toContain('offset=')
  })

  it('returns a bare array unchanged from the two routes that serve one', async () => {
    stubBackend([POINT])
    await expect(fetchTrends({})).resolves.toEqual([POINT])

    stubBackend([])
    await expect(fetchSeries()).resolves.toEqual([])
  })

  /**
   * A malformed payload is an empty panel, not a `TypeError`.
   *
   * The page behind these reads slices, maps and filters whatever arrives; a
   * body in neither recognised shape used to take the whole surface down rather
   * than render the empty state it already has.
   */
  it.each([
    ['a body with no rows', { meta: { total: 0, limit: 20, offset: 0 } }],
    ['a null items', { items: null, meta: { total: 0, limit: 20, offset: 0 } }],
    ['a null body', null],
    ['a scalar', 42],
  ])('renders an empty list for %s', async (_label, body) => {
    stubBackend(body)

    await expect(fetchProjects()).resolves.toEqual([])
  })
})