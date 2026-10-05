import { describe, expect, it } from 'vitest'

import { ApiError } from '@/lib/api-client'
import {
  TASK_TRANSITIONS,
  TASK_TRANSITION_CONTROLS,
  allowedFromError,
  canTransition,
  controlsFor,
  describeIllegalMove,
  describeRefusal,
  describeUnroutableMove,
  routeFor,
} from '@/features/work/task-transitions'
import { TASK_STATUSES } from '@/types/work'
import type { TaskStatus } from '@/types/work'

/**
 * The lifecycle table, pinned against the backend that owns it.
 *
 * `TaskService._LEGAL_TRANSITIONS` (`backend/app/services/task_service.py`)
 * is the authority; this file is the client's copy of it, and a copy that
 * drifts is the failure the copy was made to prevent. The expected values are
 * therefore transcribed from the service and written out again here rather than
 * imported: an import would agree with itself forever.
 *
 * The rules being protected are the ones the task card used to break — it
 * offered Complete on every unfinished task, so a `todo` or `blocked` card
 * answered a click with `422 A task cannot move from 'todo' to 'completed'.`
 */
const BACKEND_TABLE: Record<TaskStatus, TaskStatus[]> = {
  todo: ['in_progress', 'blocked', 'cancelled'],
  in_progress: ['todo', 'blocked', 'completed', 'cancelled'],
  blocked: ['todo', 'in_progress', 'cancelled'],
  completed: ['todo', 'in_progress'],
  cancelled: [],
}

/**
 * The five calls the task API answers, and the edges each one walks.
 *
 * Every entry was answered against the running backend during the fix, which
 * is how `completed -> in_progress` came to be here: `/start` is not a
 * `todo`-only route, and `set_status` clears `completed_at` on the way out.
 */
const ROUTES: Array<{ from: TaskStatus[]; to: TaskStatus }> = [
  { from: ['in_progress'], to: 'completed' },
  { from: ['completed'], to: 'todo' },
  { from: ['todo', 'blocked', 'completed'], to: 'in_progress' },
  { from: ['todo', 'in_progress'], to: 'blocked' },
  { from: ['todo', 'in_progress', 'blocked'], to: 'cancelled' },
]

describe('task lifecycle table', () => {
  it('matches the transitions TaskService allows', () => {
    for (const status of TASK_STATUSES) {
      expect([...TASK_TRANSITIONS[status]].sort()).toEqual([...BACKEND_TABLE[status]].sort())
    }
  })

  it('names no status the vocabulary does not have', () => {
    // `Record<TaskStatus, …>` catches a missing key at compile time; it does
    // not catch one that reaches past the union.
    for (const targets of Object.values(TASK_TRANSITIONS)) {
      for (const target of targets) expect(TASK_STATUSES).toContain(target)
    }
  })

  it('refuses the two edges that made the bug visible', () => {
    // The service's own comment: work nobody started does not get to claim it
    // was finished, and the point of blocking is that the work did not happen.
    expect(canTransition('todo', 'completed')).toBe(false)
    expect(canTransition('blocked', 'completed')).toBe(false)
    expect(canTransition('in_progress', 'completed')).toBe(true)
  })

  it('treats a cancelled task as final', () => {
    expect(TASK_TRANSITIONS.cancelled).toEqual([])
    for (const status of TASK_STATUSES) {
      if (status === 'cancelled') continue
      expect(canTransition('cancelled', status)).toBe(false)
    }
  })

  it('treats a move to the status a task already holds as legal', () => {
    // `set_status` short-circuits it into a no-op so a retried click is a retry
    // rather than a mistake, and the client must not refuse what the server
    // accepts quietly.
    for (const status of TASK_STATUSES) {
      expect(canTransition(status, status)).toBe(true)
    }
  })

  it('answers "is there a route for this?" separately from "is it legal?"', () => {
    // The two edges the table allows and no endpoint walks. `/reopen` acts on a
    // completed task and is a no-op on an open one, so a client that sent these
    // would get 200 and a card that had not moved.
    expect(canTransition('in_progress', 'todo')).toBe(true)
    expect(routeFor('in_progress', 'todo')).toBeNull()
    expect(canTransition('blocked', 'todo')).toBe(true)
    expect(routeFor('blocked', 'todo')).toBeNull()
  })

  it('routes every edge the API actually walks', () => {
    // Each of these is a real `POST /tasks/{id}/…` call answered against the
    // running backend during the fix; the table is only useful if the client
    // can actually take the moves it advertises.
    expect(routeFor('todo', 'in_progress')).toBe('in_progress')
    expect(routeFor('todo', 'blocked')).toBe('blocked')
    expect(routeFor('todo', 'cancelled')).toBe('cancelled')
    expect(routeFor('in_progress', 'completed')).toBe('completed')
    expect(routeFor('blocked', 'in_progress')).toBe('in_progress')
    expect(routeFor('completed', 'todo')).toBe('reopened')
  })

  it('never routes an illegal edge', () => {
    for (const from of TASK_STATUSES) {
      for (const to of TASK_STATUSES) {
        if (canTransition(from, to)) continue
        expect(routeFor(from, to)).toBeNull()
      }
    }
  })

  it('reaches exactly the statuses the routes can reach', () => {
    // The closure above, computed from the table: every reachable pair has a
    // route, so the client cannot offer a move it has nowhere to send. A pair
    // to the status a task already holds is excluded — that is the no-op, not
    // a transition.
    for (const from of TASK_STATUSES) {
      for (const to of TASK_STATUSES) {
        if (from === to) {
          expect(routeFor(from, to)).toBeNull()
          continue
        }
        const reachable = ROUTES.some((route) => route.from.includes(from) && route.to === to)
        expect(routeFor(from, to) !== null).toBe(reachable)
      }
    }
  })
})

describe('the controls a card offers', () => {
  it('offers Start, Complete and Reopen on the three rungs of the ladder', () => {
    expect(controlsFor('todo').map((control) => control.action)).toEqual(['start'])
    expect(controlsFor('in_progress').map((control) => control.action)).toEqual(['complete'])
    expect(controlsFor('completed').map((control) => control.action)).toEqual(['reopen'])
  })

  it('offers a blocked task nothing', () => {
    // Not Complete — the server refuses that edge — and no "Unblock" either:
    // `blocked -> todo` has no route, so such a button could only have been a
    // client-side status write pretending to be an action.
    expect(controlsFor('blocked')).toEqual([])
  })

  it('offers a cancelled task nothing, because cancelled is terminal', () => {
    expect(controlsFor('cancelled')).toEqual([])
  })

  it('has no control the lifecycle forbids', () => {
    for (const control of TASK_TRANSITION_CONTROLS) {
      expect(canTransition(control.from, control.to)).toBe(true)
      expect(routeFor(control.from, control.to)).not.toBeNull()
    }
  })

  it('never offers Complete on a task that has not been started', () => {
    const completes = TASK_TRANSITION_CONTROLS.filter((control) => control.action === 'complete')
    expect(completes.map((control) => control.from)).toEqual(['in_progress'])
  })
})

describe('refusals', () => {
  it('names what the task can do instead of only what it cannot', () => {
    // The toast a user reads after clicking the old checkmark. Without the
    // second sentence it is a dead end: "cannot move" and nothing else.
    expect(describeIllegalMove('todo', 'completed')).toBe(
      "A task cannot move from 'todo' to 'completed'. From to do it can move to: in progress, blocked, cancelled.",
    )
    expect(describeIllegalMove('blocked', 'completed')).toBe(
      "A task cannot move from 'blocked' to 'completed'. From blocked it can move to: to do, in progress, cancelled.",
    )
  })

  it('says a cancelled task is final rather than listing nothing', () => {
    expect(describeIllegalMove('cancelled', 'completed')).toContain('Cancelled is final.')
  })

  it("quotes the server's own `allowed` list", () => {
    // The 422 body, verbatim from the running API:
    //   details: {"from": "blocked", "to": "completed",
    //             "allowed": ["cancelled", "in_progress", "todo"]}
    const error = new ApiError({
      status: 422,
      code: 'validation_error',
      message: "A task cannot move from 'blocked' to 'completed'.",
      details: { from: 'blocked', to: 'completed', allowed: ['cancelled', 'in_progress', 'todo'] },
    })
    expect(allowedFromError(error)).toEqual(['cancelled', 'in_progress', 'todo'])
    expect(describeRefusal(error)).toBe(
      "A task cannot move from 'blocked' to 'completed'. From blocked it can move to: cancelled, in progress, to do.",
    )
  })

  it('leaves a refusal with no allowed list untouched', () => {
    // An open dependency is refused for a reason that is not the lifecycle, so
    // there is nothing to list and padding the message would be a lie.
    const error = new ApiError({
      status: 422,
      code: 'validation_error',
      message: 'This task is waiting on 2 unfinished prerequisites.',
      details: { prerequisites: ['a', 'b'] },
    })
    expect(allowedFromError(error)).toEqual([])
    expect(describeRefusal(error)).toBe('This task is waiting on 2 unfinished prerequisites.')
  })

  it('says a terminal status is final when the server sends an empty allowed list', () => {
    // The 422 the API answers for `cancelled -> completed`, verbatim:
    //   details: {"from": "cancelled", "to": "completed", "allowed": []}
    // An empty list is an answer rather than an absence, and without the extra
    // sentence it reads like a rule the user could find a way around.
    const error = new ApiError({
      status: 422,
      code: 'validation_error',
      message: "A task cannot move from 'cancelled' to 'completed'.",
      details: { from: 'cancelled', to: 'completed', allowed: [] },
    })
    expect(describeRefusal(error)).toBe(
      "A task cannot move from 'cancelled' to 'completed'. Cancelled is final.",
    )
  })

  it('ignores an `allowed` list that is not a list of statuses', () => {
    // `/tasks?sort=` refuses with `details.allowed` holding sort keys. Reading
    // that as a lifecycle answer would offer "created_at" as a destination, so
    // nothing in that list survives the filter.
    const error = new ApiError({
      status: 422,
      code: 'validation_error',
      message: "Cannot sort tasks by 'nonsense'.",
      details: { allowed: ['created_at', 'nonsense'] },
    })
    expect(allowedFromError(error)).toEqual([])
    expect(describeRefusal(error)).toBe("Cannot sort tasks by 'nonsense'.")
  })

  it('explains a legal move that has no endpoint behind it', () => {
    expect(describeUnroutableMove('blocked', 'todo')).toContain('/reopen')
    expect(describeUnroutableMove('blocked', 'todo')).toContain('blocked')
  })
})