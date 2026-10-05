import {
  AlertTriangle,
  CalendarDays,
  Check,
  Clock,
  CornerDownRight,
  Link2,
  Play,
  RotateCcw,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { useTags } from '@/features/work/hooks'
import { controlsFor } from '@/features/work/task-transitions'
import type { TaskTransitionAction } from '@/features/work/task-transitions'
import { cn } from '@/lib/utils'
import { TASK_STATUS_META } from '@/types/work'
import type { DateOnlyString, Task, TaskStatus, TaskSummary } from '@/types/work'

import { PriorityBadge } from './priority-badge'
import { StatusBadge } from './status-badge'

/** A board column renders `TaskSummary`; a detail surface renders `Task`. */
export type TaskCardTask = Task | TaskSummary

/**
 * Handlers are matched bivariantly, the way React matches its own event types.
 * A card can be handed the lean `TaskSummary` off a board while the caller that
 * opened it holds the full `Task`, and a strict contravariant parameter would
 * refuse that perfectly sound `(task: Task) => void`.
 */
export type TaskCardHandler = { bivarianceHack: (task: TaskCardTask) => void }['bivarianceHack']

export interface TaskCardProps {
  task: TaskCardTask
  projectName?: string
  onOpen?: TaskCardHandler
  onStart?: TaskCardHandler
  onComplete?: TaskCardHandler
  onReopen?: TaskCardHandler
  /** Single-line density, for list rows rather than board columns. */
  compact?: boolean
  draggable?: boolean
  onDragStart?: TaskCardHandler
  onDragEnd?: TaskCardHandler
  isDragging?: boolean
  selected?: boolean
  onSelect?: { bivarianceHack: (task: TaskCardTask, next: boolean) => void }['bivarianceHack']
  className?: string
}

const MAX_TAG_CHIPS = 3

/**
 * How each control looks, keyed by the verb rather than the status.
 *
 * Start hovers to `primary`, which is the token the in-progress badge is drawn
 * in (`info` maps to the default badge variant, and the default is primary), so
 * the control reads as belonging to the status it lands on. Complete keeps
 * success and Reopen keeps no accent at all, as it always has.
 */
const CONTROL_LOOK: Record<TaskTransitionAction, { icon: typeof Play; hover: string }> = {
  start: { icon: Play, hover: 'hover:text-primary' },
  complete: { icon: Check, hover: 'hover:text-success' },
  reopen: { icon: RotateCcw, hover: '' },
}

/** `YYYY-MM-DD` has no timezone; parse it as local midnight so it never shifts. */
function formatDateOnly(value: DateOnlyString): string {
  const date = new Date(`${value}T00:00:00`)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date)
}

function formatEstimate(minutes: number): string {
  if (minutes < 60) return `${minutes}m`
  const hours = minutes / 60
  return Number.isInteger(hours) ? `${hours}h` : `${hours.toFixed(1)}h`
}

function isDueToday(value: DateOnlyString): boolean {
  const due = new Date(`${value}T00:00:00`)
  if (Number.isNaN(due.getTime())) return false
  const now = new Date()
  return (
    due.getFullYear() === now.getFullYear() &&
    due.getMonth() === now.getMonth() &&
    due.getDate() === now.getDate()
  )
}

/**
 * `is_overdue` is read, never derived: the backend compares the due date
 * against its own today, so a card here and the same card in the list endpoint
 * agree by construction.
 */
export function TaskCard({
  task,
  projectName,
  onOpen,
  onStart,
  onComplete,
  onReopen,
  compact = false,
  draggable = false,
  onDragStart,
  onDragEnd,
  isDragging = false,
  selected = false,
  onSelect,
  className,
}: TaskCardProps) {
  const status = task.status as TaskStatus
  const estimate = 'estimated_minutes' in task ? task.estimated_minutes : null
  const waitingOnDependency =
    'has_blocked_dependencies' in task ? task.has_blocked_dependencies : false

  // Tag ids arrive without names, so the card resolves them from the shared tag
  // query — one cached request for the whole board, not one per card.
  const { data: tagPage } = useTags()
  const tagNames = (tagPage?.items ?? [])
    .filter((tag) => task.tag_ids.includes(tag.id))
    .map((tag) => tag.name)

  const dueToday = !task.is_overdue && task.due_date !== null && isDueToday(task.due_date)
  const DueIcon = task.is_overdue ? AlertTriangle : dueToday ? Clock : CalendarDays

  // Which controls a card may offer is the server's answer rather than a
  // condition written here. It used to be `onComplete && !finished`, which put
  // a Complete checkmark on every unfinished card — including `todo` and
  // `blocked`, the two statuses the lifecycle refuses to complete — and the
  // click answered with a 422 the user could do nothing with. `controlsFor`
  // reads the mirrored table, so `todo` offers Start, `in_progress` offers
  // Complete, `completed` offers Reopen, and `blocked`/`cancelled` offer
  // nothing at all.
  const controls = controlsFor(status)
  const handlers: Record<TaskTransitionAction, TaskCardHandler | undefined> = {
    start: onStart,
    complete: onComplete,
    reopen: onReopen,
  }

  return (
    <article
      draggable={draggable}
      onDragStart={(event) => {
        if (!draggable) return
        event.dataTransfer.effectAllowed = 'move'
        // Firefox refuses to start a drag unless some payload is set.
        event.dataTransfer.setData('text/plain', task.id)
        onDragStart?.(task)
      }}
      onDragEnd={() => onDragEnd?.(task)}
      data-dragging={isDragging || undefined}
      className={cn(
        'group rounded-lg border bg-card transition-colors',
        selected ? 'border-primary' : 'border-border',
        isDragging && 'opacity-50',
        compact ? 'flex items-center gap-3 px-3 py-2' : 'space-y-2 p-3',
        draggable && 'cursor-grab active:cursor-grabbing',
        className,
      )}
    >
      {onSelect && (
        <input
          type="checkbox"
          checked={selected}
          onChange={(event) => onSelect(task, event.target.checked)}
          aria-label={`Select ${task.title}`}
          className="size-4 shrink-0 rounded border-input accent-primary"
        />
      )}

      <div className={cn('min-w-0 flex-1', compact && 'flex items-center gap-3')}>
        <div className="min-w-0 flex-1">
          {onOpen ? (
            <button
              type="button"
              onClick={() => onOpen(task)}
              className={cn(
                'block w-full truncate text-left font-medium text-foreground hover:underline',
                'focus-visible:rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                compact ? 'text-sm' : 'text-sm',
              )}
            >
              {task.title}
            </button>
          ) : (
            <p className="truncate text-sm font-medium text-foreground">{task.title}</p>
          )}

          {projectName && (
            <p className="truncate text-xs text-muted-foreground">{projectName}</p>
          )}

          {!compact && task.due_date && (
            <p
              className={cn(
                'flex items-center gap-1.5 text-xs',
                task.is_overdue
                  ? 'font-medium text-destructive'
                  : dueToday
                    ? 'font-medium text-warning'
                    : 'text-muted-foreground',
              )}
            >
              {/* Icon *and* words: the colour is the third signal, never the only
                  one, so the state survives a screen reader and a greyscale
                  print. */}
              <DueIcon aria-hidden="true" className="size-3.5 shrink-0" />
              {task.is_overdue
                ? `Overdue · ${formatDateOnly(task.due_date)}`
                : dueToday
                  ? 'Due today'
                  : `Due ${formatDateOnly(task.due_date)}`}
            </p>
          )}

          {!compact && (task.parent_id !== null || waitingOnDependency) && (
            <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              {task.parent_id !== null && (
                <span className="flex items-center gap-1">
                  <CornerDownRight aria-hidden="true" className="size-3.5" />
                  Subtask
                </span>
              )}
              {waitingOnDependency && (
                <span className="flex items-center gap-1 text-warning">
                  <Link2 aria-hidden="true" className="size-3.5" />
                  Waiting on a dependency
                </span>
              )}
            </p>
          )}

          {!compact && tagNames.length > 0 && (
            <ul className="mt-1 flex flex-wrap gap-1">
              {tagNames.slice(0, MAX_TAG_CHIPS).map((name) => (
                <li
                  key={name}
                  className="rounded border border-border bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground"
                >
                  {name}
                </li>
              ))}
              {tagNames.length > MAX_TAG_CHIPS && (
                <li className="px-1 py-0.5 text-[11px] text-muted-foreground">
                  +{tagNames.length - MAX_TAG_CHIPS}
                </li>
              )}
            </ul>
          )}
        </div>

        <div className={cn('flex items-center gap-2', compact && 'shrink-0')}>
          {!compact && <StatusBadge status={status} size="sm" />}
          <PriorityBadge priority={task.priority} size="sm" />

          {estimate !== null && (
            <span className="text-xs tabular-nums text-muted-foreground">
              {formatEstimate(estimate)}
            </span>
          )}

          {compact && task.due_date && (
            <span
              className={cn(
                'w-28 shrink-0 text-xs',
                task.is_overdue ? 'font-medium text-destructive' : 'text-muted-foreground',
              )}
            >
              {task.is_overdue ? 'Overdue · ' : ''}
              {formatDateOnly(task.due_date)}
            </span>
          )}

          {compact && (
            <span className="w-28 shrink-0">
              <StatusBadge status={status} size="sm" />
            </span>
          )}

          {controls.map((control) => {
            const handler = handlers[control.action]
            // A caller that supplies no handler for a control simply does not
            // get the control: the board hands cards over with no handlers at
            // all, and its columns are the affordance there.
            if (!handler) return null
            const look = CONTROL_LOOK[control.action]
            const Icon = look.icon
            return (
              <Button
                key={control.action}
                variant="ghost"
                size="icon"
                className={cn('size-7 text-muted-foreground', look.hover)}
                onClick={() => handler(task)}
                title={TASK_STATUS_META[control.to].description}
              >
                <Icon aria-hidden="true" />
                <span className="sr-only">
                  {control.label} {task.title}
                </span>
              </Button>
            )
          })}
        </div>
      </div>
    </article>
  )
}