import type { ReactElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { TaskCard } from '@/features/work/components/task-card'
import type { Task, TaskStatus } from '@/types/work'

/**
 * The card's transition controls, mounted for real.
 *
 * This is the surface the bug was reported on: on `/tasks`, the checkmark on a
 * task in status `todo` produced *"0 of 1 completed — 1 refused — A task cannot
 * move from 'todo' to 'completed'."* The button existed because the card
 * rendered `onComplete && !finished` — that is, for every unfinished task
 * whatever its status — while `TaskService._LEGAL_TRANSITIONS` refuses
 * `todo -> completed` and `blocked -> completed` outright.
 *
 * What is pinned here is therefore not "a button is present" but **which
 * button, for which status**: `todo` offers Start and no Complete,
 * `in_progress` offers Complete, `blocked` offers neither Complete nor an
 * invented Unblock, and `cancelled` offers nothing at all because the status is
 * terminal. Every handler is supplied, so a control that is missing is missing
 * because the lifecycle says so and not because the caller forgot.
 */

const PROJECT_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const OWNER_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'

function task(status: TaskStatus, overrides: Partial<Task> = {}): Task {
  return {
    id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
    project_id: PROJECT_ID,
    owner_id: OWNER_ID,
    parent_id: null,
    title: 'Write the migration plan',
    description: null,
    status,
    priority: 'medium',
    start_date: null,
    due_date: null,
    estimated_minutes: null,
    actual_minutes: 0,
    completed_at: status === 'completed' ? '2026-01-02T10:00:00Z' : null,
    position: 0,
    created_at: '2026-01-01T09:00:00Z',
    updated_at: '2026-01-01T09:00:00Z',
    tag_ids: [],
    is_overdue: false,
    has_blocked_dependencies: false,
    ...overrides,
  }
}

/** A query client, because the card resolves its tag names from `useTags`. */
function cardTree(cards: ReactElement[]): ReactElement {
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {cards}
    </QueryClientProvider>
  )
}

type CardProps = Partial<React.ComponentProps<typeof TaskCard>>

function renderCard(status: TaskStatus, handlers: CardProps = {}) {
  return render(
    cardTree([
      <TaskCard key={status} task={task(status)} onStart={vi.fn()} onComplete={vi.fn()} onReopen={vi.fn()} {...handlers} />,
    ]),
  )
}

/** Every transition control the card rendered, by verb. */
function controls(): string[] {
  return ['Start', 'Complete', 'Reopen', 'Unblock', 'Resume']
    .filter((label) => screen.queryByRole('button', { name: new RegExp(`^${label} `) }) !== null)
}

beforeEach(() => {
  // The card resolves its tag names from the shared tag query. One empty page
  // keeps that one request out of the way; nothing here is about tags.
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify({ items: [], meta: { total: 0, limit: 100, offset: 0 } }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })),
  )
})

describe('task card transition controls', () => {
  it('offers Start, and no Complete, on a task nobody has started', () => {
    renderCard('todo')
    expect(screen.getByRole('button', { name: /^Start Write the migration plan$/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Complete/ })).not.toBeInTheDocument()
    expect(controls()).toEqual(['Start'])
  })

  it('offers Complete on a task that is under way', () => {
    renderCard('in_progress')
    expect(screen.getByRole('button', { name: /^Complete Write the migration plan$/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Start/ })).not.toBeInTheDocument()
    expect(controls()).toEqual(['Complete'])
  })

  it('offers Reopen on a completed task and nothing else', () => {
    renderCard('completed')
    expect(controls()).toEqual(['Reopen'])
  })

  it('offers a blocked task neither Complete nor an invented control', () => {
    // `blocked -> completed` is refused for the same reason `todo -> completed`
    // is. An "Unblock" button is not an answer either: `blocked -> todo` has no
    // route, so such a control could only have been a client-side status write
    // dressed up as an action.
    renderCard('blocked')
    expect(screen.queryByRole('button', { name: /^Complete/ })).not.toBeInTheDocument()
    expect(controls()).toEqual([])
  })

  it('offers a cancelled task nothing, because cancelled is terminal', () => {
    renderCard('cancelled')
    expect(controls()).toEqual([])
  })

  it('never offers a control the caller did not write a handler for', () => {
    // The board hands cards over with no handlers at all: its columns are the
    // affordance there. A control with no handler would be a button that does
    // nothing.
    render(cardTree([<TaskCard key="solo" task={task('todo')} onComplete={vi.fn()} />]))
    expect(controls()).toEqual([])
  })

  it('sends the task to the handler that matches the control', async () => {
    const onStart = vi.fn()
    const onComplete = vi.fn()
    render(
      cardTree([
        <TaskCard key="todo" task={task('todo')} onStart={onStart} onComplete={onComplete} />,
        <TaskCard
          key="in_progress"
          task={task('in_progress', { id: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd' })}
          onStart={onStart}
          onComplete={onComplete}
        />,
      ]),
    )

    screen.getByRole('button', { name: /^Start Write the migration plan$/ }).click()
    expect(onStart).toHaveBeenCalledWith(expect.objectContaining({ status: 'todo' }))
    expect(onComplete).not.toHaveBeenCalled()

    screen.getByRole('button', { name: /^Complete Write the migration plan$/ }).click()
    expect(onComplete).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd' }),
    )
  })
})