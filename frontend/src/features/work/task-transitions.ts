/**
 * The task lifecycle, mirrored once for the whole client.
 *
 * **The server owns this table; this is a copy of it, and the copy exists only
 * so the UI stops offering actions that will be refused.** `TaskService`
 * publishes `todo -> {in_progress, blocked, cancelled}`,
 * `in_progress -> {todo, blocked, completed, cancelled}`,
 * `blocked -> {todo, in_progress, cancelled}`,
 * `completed -> {todo, in_progress}` and a terminal `cancelled`, and the
 * client's own comment on that table — *"work nobody started does not get to
 * claim it was finished"* — is the rule the task card was breaking: it rendered
 * a Complete checkmark for every unfinished task, so a `todo` or `blocked` card
 * answered a click with `422 A task cannot move from 'todo' to 'completed'.`
 *
 * Both the card and the page read their affordances from here, so there is one
 * answer to "what may this task do next" rather than a button list in one
 * component and a `served` boolean in another. `task-transitions.test.ts` pins
 * the table against the backend's own values, because a second copy that drifts
 * is the failure this module exists to prevent.
 *
 * **A legal edge is not the same as a reachable one.** Two edges the service
 * allows — `in_progress -> todo` and `blocked -> todo` — have no route that
 * walks them: `/reopen` only acts on a `COMPLETED` task, so posting it to an
 * open card is a no-op that leaves the status untouched. {@link routeFor} is the
 * honest question ("is there an endpoint that does this?"), and the surfaces
 * that would otherwise fake it say so rather than quietly doing nothing.
 */
import type { ApiError } from '@/lib/api-client'
import { TASK_STATUS_META, TASK_STATUSES } from '@/types/work'
import type { TaskStatus } from '@/types/work'

/**
 * The legal transitions, written down once, in board order.
 *
 * Written out rather than derived: the value is the rule, and a rule that is
 * computed from something else is a rule with one more thing to be wrong.
 */
export const TASK_TRANSITIONS: Record<TaskStatus, readonly TaskStatus[]> = {
  todo: ['in_progress', 'blocked', 'cancelled'],
  in_progress: ['todo', 'blocked', 'completed', 'cancelled'],
  blocked: ['todo', 'in_progress', 'cancelled'],
  completed: ['todo', 'in_progress'],
  cancelled: [],
}

/**
 * What a card in `from` may move to. A move to the status it already holds is
 * legal — `TaskService.set_status` short-circuits it into a no-op so a retried
 * click is a retry rather than a mistake.
 */
export function canTransition(from: TaskStatus, to: TaskStatus): boolean {
  if (from === to) return true
  return TASK_TRANSITIONS[from].includes(to)
}

/** The one mutation, five routes. `reopened` is `/reopen`, which is todo. */
export type TaskTransitionStatus = 'in_progress' | 'completed' | 'reopened' | 'blocked' | 'cancelled'

/**
 * The route that walks an edge, or `null` when there is none.
 *
 * `null` covers two different situations and the caller has to say which:
 *
 * - the edge is **illegal** — `canTransition` already answered no, and
 *   {@link describeIllegalMove} explains it;
 * - the edge is **legal but unwalkable** — `in_progress -> todo` and
 *   `blocked -> todo` are in the service's table, and no endpoint takes them.
 *   Guessing here would be worse than the refusal: `/reopen` would answer 200
 *   and leave the card exactly where it was.
 */
export function routeFor(from: TaskStatus, to: TaskStatus): TaskTransitionStatus | null {
  if (from === to) return null
  if (!canTransition(from, to)) return null
  if (to === 'todo') {
    // The only route to `todo` is `/reopen`, and it acts on a completed task.
    return from === 'completed' ? 'reopened' : null
  }
  if (to === 'in_progress') return 'in_progress'
  if (to === 'completed') return 'completed'
  if (to === 'blocked') return 'blocked'
  return 'cancelled'
}

/* ------------------------------------------------------- the card's ladder */

/** The three verbs a card offers, named for their accessible label. */
export type TaskTransitionAction = 'start' | 'complete' | 'reopen'

export interface TaskTransitionControl {
  action: TaskTransitionAction
  /** The status a card holds when this control means anything. */
  from: TaskStatus
  /** The status the control lands it on. */
  to: TaskStatus
  /** Verb in the button's accessible name: "Start", "Complete", "Reopen". */
  label: string
}

/**
 * The one-click controls a card offers: start it, finish it, take it back.
 *
 * **Every rung is checked against {@link TASK_TRANSITIONS}, and the list is
 * deliberately short.** `blocked` and `cancelled` are absent, and that absence
 * is the point rather than an omission:
 *
 * - `cancelled` is terminal, so nothing can follow it.
 * - `blocked` is off the ladder. Its one route out that a button could offer,
 *   `POST /tasks/{id}/start`, is a move the board already offers as a column
 *   change, and its unblock edge — back to `todo` — has no route at all (see
 *   {@link routeFor}). A "Unblock" button here would have been a client-side
 *   status write dressed up as an action, which is the second half of the bug
 *   this table was written to kill.
 *
 * The board picks its moves up from the same table, so a card and the column it
 * sits in can never disagree about what the server will accept.
 */
export const TASK_TRANSITION_CONTROLS: readonly TaskTransitionControl[] = [
  { action: 'start', from: 'todo', to: 'in_progress', label: 'Start' },
  { action: 'complete', from: 'in_progress', to: 'completed', label: 'Complete' },
  { action: 'reopen', from: 'completed', to: 'todo', label: 'Reopen' },
]

/** The controls a card in `status` may render, in ladder order. */
export function controlsFor(status: TaskStatus): readonly TaskTransitionControl[] {
  return TASK_TRANSITION_CONTROLS.filter((control) => control.from === status)
}

/* ------------------------------------------------------ saying "no" usefully */

function labelFor(status: TaskStatus): string {
  return TASK_STATUS_META[status].label.toLowerCase()
}

function list(statuses: readonly TaskStatus[]): string {
  return statuses.map(labelFor).join(', ')
}

/**
 * The sentence a refusal should carry, in the server's own wording so the two
 * are indistinguishable to a reader: *"A task cannot move from 'todo' to
 * 'completed'. From to do it can move to: in progress, blocked, cancelled."*
 *
 * The destinations are in board order rather than the alphabetical order the
 * server sends, which is the one difference between this and
 * {@link describeRefusal} — and the only thing a reader would have to notice
 * to tell the two apart.
 *
 * The clause after the full stop is the one that earns its keep. "Cannot move"
 * tells the user they lost; what they can do next is the part they can act on.
 */
export function describeIllegalMove(from: TaskStatus, to: TaskStatus): string {
  const allowed = TASK_TRANSITIONS[from]
  const head = `A task cannot move from '${from}' to '${to}'.`
  return allowed.length === 0
    ? `${head} ${TASK_STATUS_META[from].label} is final.`
    : `${head} From ${labelFor(from)} it can move to: ${list(allowed)}.`
}

/**
 * The `allowed` list a 422 carries in its `details`, filtered to statuses the
 * client knows. Anything else in `details` — a field error map, a sort key —
 * is not an answer to this question and is ignored rather than rendered.
 */
export function allowedFromError(error: ApiError): readonly TaskStatus[] {
  const allowed = error.details?.allowed
  if (!Array.isArray(allowed)) return []
  return allowed.filter((value): value is TaskStatus =>
    TASK_STATUSES.includes(value as TaskStatus),
  )
}

/**
 * A server refusal, with its own `allowed` list turned into words.
 *
 * The message alone is the toast in every other surface, which left the user
 * holding a refusal with no way out in front of them. When the envelope carries
 * the list, the sentence names it; when it does not — a dependency that is
 * still open, say, which is refused for a reason that is not the lifecycle —
 * the message is returned untouched rather than padded.
 *
 * An empty `allowed` list is an answer, not an absence: `cancelled` is terminal,
 * and the backend says so by sending nothing. That is the one refusal worth a
 * sentence of its own, because "cannot move from 'cancelled' to 'completed'"
 * reads like a rule the user could find a way around.
 */
export function describeRefusal(error: ApiError): string {
  const raw = error.details?.allowed
  const allowed = allowedFromError(error)
  const from = error.details?.from
  const origin =
    typeof from === 'string' && TASK_STATUSES.includes(from as TaskStatus)
      ? (from as TaskStatus)
      : null
  if (!Array.isArray(raw) || origin === null) return error.message
  if (allowed.length === 0) {
    return raw.length === 0
      ? `${error.message} ${TASK_STATUS_META[origin].label} is final.`
      : error.message
  }
  return `${error.message} From ${labelFor(origin)} it can move to: ${list(allowed)}.`
}

/**
 * Why a legal edge cannot be taken, for the two that have no route behind them.
 * Said plainly, because the alternative — a control that answers 200 and
 * changes nothing — looks like a broken app rather than a missing endpoint.
 */
export function describeUnroutableMove(from: TaskStatus, to: TaskStatus): string {
  return (
    `The lifecycle allows ${labelFor(from)} to become ${labelFor(to)}, but no endpoint ` +
    `performs that move: "/reopen" only acts on a completed task.`
  )
}