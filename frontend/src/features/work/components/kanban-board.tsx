import { useState } from 'react'

import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { TASK_STATUS_META } from '@/types/work'
import type { Task, TaskStatus } from '@/types/work'

import { TaskCard } from './task-card'

export interface KanbanBoardProps {
  tasks: Task[]
  /**
   * Called with the target column. The page behind it consults the mirrored
   * `TaskService._LEGAL_TRANSITIONS` before asking the server, and answers a
   * column the lifecycle does not allow with a refusal naming what the card
   * *can* do.
   */
  onMove: (task: Task, status: TaskStatus) => void
  onOpen?: (task: Task) => void
  loading?: boolean
  className?: string
}

/**
 * The four states a task is *worked in*. `cancelled` is the fifth status in the
 * vocabulary and is deliberately not a column: work that was dropped is not work
 * in progress, and a column for it invites treating it as such. Cancelled tasks
 * stay findable in the list view.
 *
 * **The columns are the lifecycle, not a promise that any card may enter any
 * of them.** A `blocked` card cannot be completed from here — the service
 * refuses that edge — and the page says so rather than letting the card land
 * where it was not put.
 */
const BOARD_COLUMNS: TaskStatus[] = ['todo', 'in_progress', 'blocked', 'completed']

/**
 * Drag-and-drop with a keyboard route to the same place.
 *
 * The arrow keys move a focused card one column left or right and call the same
 * `onMove` a drop does, so a board is operable without a pointer. Both routes
 * land on `onMove`, which answers for the legality of the move rather than
 * assuming it: nothing is moved optimistically, because an illegal move has to
 * be visible rather than silently reverted.
 */
export function KanbanBoard({ tasks, onMove, onOpen, loading = false, className }: KanbanBoardProps) {
  const [draggingId, setDraggingId] = useState<string | null>(null)
  const [overStatus, setOverStatus] = useState<TaskStatus | null>(null)

  function step(task: Task, direction: -1 | 1): void {
    const index = BOARD_COLUMNS.indexOf(task.status)
    const next = BOARD_COLUMNS[index + direction]
    if (next) onMove(task, next)
  }

  return (
    <div className={cn('space-y-3', className)}>
      <p className="sr-only" id="kanban-instructions">
        Press the left and right arrow keys to move a focused card between columns, or drag it with a
        pointer.
      </p>

      <div className="flex gap-3 overflow-x-auto pb-2">
        {BOARD_COLUMNS.map((status) => {
          const meta = TASK_STATUS_META[status]
          const column = tasks
            .filter((task) => task.status === status)
            .sort((a, b) => a.position - b.position)

          return (
            <section
              key={status}
              aria-label={meta.label}
              data-over={overStatus === status || undefined}
              onDragOver={(event) => {
                event.preventDefault()
                event.dataTransfer.dropEffect = 'move'
                setOverStatus(status)
              }}
              onDragLeave={() => setOverStatus((current) => (current === status ? null : current))}
              onDrop={(event) => {
                event.preventDefault()
                setOverStatus(null)
                const id = event.dataTransfer.getData('text/plain')
                const task = tasks.find((candidate) => candidate.id === id)
                if (task) onMove(task, status)
              }}
              className={cn(
                'flex w-72 shrink-0 flex-col gap-2 rounded-lg border border-border bg-muted/40 p-2 transition-colors',
                overStatus === status && 'border-primary bg-primary/[0.06] ring-1 ring-primary',
              )}
            >
              <header className="flex items-center justify-between gap-2 px-1">
                <h3 className="text-sm font-medium text-foreground">{meta.label}</h3>
                <span className="text-xs tabular-nums text-muted-foreground">{column.length}</span>
              </header>

              {loading ? (
                <div className="space-y-2">
                  <Skeleton className="h-16 w-full" />
                  <Skeleton className="h-16 w-full" />
                </div>
              ) : column.length === 0 ? (
                // Calm rather than loud: an empty column is a normal state on a
                // board, not an error to announce.
                <p className="flex flex-1 items-center justify-center rounded-lg border border-dashed border-border px-3 py-8 text-center text-xs text-muted-foreground">
                  {draggingId
                    ? `Drop here to move it to ${meta.label.toLowerCase()}`
                    : `Nothing in ${meta.label.toLowerCase()}`}
                </p>
              ) : (
                column.map((task) => (
                  <div
                    key={task.id}
                    tabIndex={0}
                    role="group"
                    aria-label={`${task.title}. ${meta.label}. Column ${
                      BOARD_COLUMNS.indexOf(status) + 1
                    } of ${BOARD_COLUMNS.length}.`}
                    aria-describedby="kanban-instructions"
                    onKeyDown={(event) => {
                      if (event.key === 'ArrowLeft') {
                        event.preventDefault()
                        step(task, -1)
                      } else if (event.key === 'ArrowRight') {
                        event.preventDefault()
                        step(task, 1)
                      }
                    }}
                    className="rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
                  >
                    <TaskCard
                      task={task}
                      draggable
                      isDragging={draggingId === task.id}
                      onOpen={onOpen ? (value) => onOpen(value as Task) : undefined}
                      onDragStart={() => setDraggingId(task.id)}
                      onDragEnd={() => {
                        setDraggingId(null)
                        setOverStatus(null)
                      }}
                    />
                  </div>
                ))
              )}
            </section>
          )
        })}
      </div>
    </div>
  )
}