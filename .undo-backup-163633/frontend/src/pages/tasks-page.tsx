import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import {
  CheckSquare,
  Columns3,
  FolderPlus,
  LayoutList,
  Plus,
  Trash2,
} from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { PageHeader } from '@/components/feedback/page-header'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  ConfirmDialog,
  EmptyWork,
  KanbanBoard,
  ProjectFormDialog,
  TaskCard,
  TaskDialog,
  TaskFilters,
} from '@/features/work/components'
import {
  useActivityStats,
  useCompleteTaskWithStart,
  useCreateTask,
  useDeleteTask,
  useProjects,
  useSetTaskTags,
  useTags,
  useTaskTransition,
  useTasks,
  taskRestorePayload,
  workKeys,
} from '@/features/work/hooks'
import {
  canTransition,
  describeIllegalMove,
  describeRefusal,
  describeUnroutableMove,
  routeFor,
} from '@/features/work/task-transitions'
import type { TaskTransitionStatus } from '@/features/work/task-transitions'
import { useDebouncedValue } from '@/hooks/use-debounce'
import { cn } from '@/lib/utils'
import { toApiError } from '@/services/errors'
import { completeTask } from '@/services/work'
import { toast, useToastStore } from '@/stores/toast-store'
import {
  MAX_PAGE_SIZE,
  TASK_PRIORITIES,
  TASK_SORT_KEYS,
  TASK_STATUSES,
  TASK_STATUS_META,
} from '@/types/work'
import type {
  Project,
  Task,
  TaskFilterValue,
  TaskListParams,
  TaskPriority,
  TaskStatus,
  TaskSummary,
} from '@/types/work'

/** The view is a local preference, not a linkable location, so it is stored. */
const VIEW_KEY = 'nexus.tasks.view'
const PAGE_SIZE = 25

/**
 * How long a delete's undo stays on the toast. The store's own default is five
 * seconds, which is long enough to read a message and not long enough to notice
 * one; ten is the shortest window in which a person who has just hit the wrong
 * button can still catch it. It is a window and not a promise — the button only
 * exists while the toast does, and the toast disappears on its own after this.
 */
const UNDO_WINDOW_MS = 10_000

type ViewMode = 'list' | 'board'

/**
 * What each transition says when it lands and when it is refused. One entry per
 * verb `useTaskTransition` can perform, so a new route cannot arrive without
 * the page having an opinion about what it announces.
 */
const TRANSITION_COPY: Record<TaskTransitionStatus, { done: string; failed: string }> = {
  in_progress: { done: 'Task started', failed: 'Could not start the task' },
  completed: { done: 'Task completed', failed: 'Could not complete the task' },
  reopened: { done: 'Task reopened', failed: 'Could not reopen the task' },
  blocked: { done: 'Task blocked', failed: 'Could not block the task' },
  cancelled: { done: 'Task cancelled', failed: 'Could not cancel the task' },
}

function readView(): ViewMode {
  try {
    return window.localStorage.getItem(VIEW_KEY) === 'board' ? 'board' : 'list'
  } catch {
    return 'list'
  }
}

/**
 * The URL is the source of truth for the filters, so a filtered board survives
 * a refresh and a view can be shared. Every value is checked against the same
 * allowlists the backend uses — an unknown sort is a 422 there, and a stale
 * bookmark should narrow the list rather than break it.
 */
function parseFilters(params: URLSearchParams): TaskFilterValue {
  const search = params.get('q') ?? ''
  const status = params.get('status')
  const priority = params.get('priority')
  const sort = params.get('sort')
  const tagIds = (params.get('tags') ?? '')
    .split(',')
    .map((id) => id.trim())
    .filter(Boolean)

  return {
    search: search || undefined,
    status: TASK_STATUSES.includes(status as TaskStatus) ? (status as TaskStatus) : undefined,
    priority: TASK_PRIORITIES.includes(priority as TaskPriority)
      ? (priority as TaskPriority)
      : undefined,
    project_id: params.get('project') || undefined,
    due_after: params.get('after') || undefined,
    due_before: params.get('before') || undefined,
    tag_ids: tagIds.length ? tagIds : undefined,
    sort: TASK_SORT_KEYS.includes(sort as (typeof TASK_SORT_KEYS)[number]) ? sort! : 'created_at',
    order: params.get('order') === 'asc' ? 'asc' : 'desc',
  }
}

function writeFilters(filters: TaskFilterValue): URLSearchParams {
  const params = new URLSearchParams()
  if (filters.search) params.set('q', filters.search)
  if (filters.status) params.set('status', filters.status)
  if (filters.priority) params.set('priority', filters.priority)
  if (filters.project_id) params.set('project', filters.project_id)
  if (filters.due_after) params.set('after', filters.due_after)
  if (filters.due_before) params.set('before', filters.due_before)
  if (filters.tag_ids?.length) params.set('tags', filters.tag_ids.join(','))
  if (filters.sort && filters.sort !== 'created_at') params.set('sort', filters.sort)
  if (filters.order === 'asc') params.set('order', 'asc')
  return params
}

/**
 * A figure that has not been measured yet is a skeleton, never a zero: "you
 * have no tasks" is a claim about the work, and a cold load has not earned it.
 */
function StatTile({
  label,
  value,
  tone,
  loading,
}: {
  label: string
  value: number
  tone?: 'danger'
  loading?: boolean
}) {
  if (loading) {
    return (
      <div className="rounded-lg border border-border bg-card px-3 py-2">
        <Skeleton className="h-3 w-16" />
        <Skeleton className="mt-2 h-6 w-10" />
      </div>
    )
  }
  return (
    <div className="rounded-lg border border-border bg-card px-3 py-2">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p
        className={cn(
          'text-lg font-semibold tabular-nums',
          tone === 'danger' && value > 0 ? 'text-destructive' : 'text-foreground',
        )}
      >
        {value}
      </p>
    </div>
  )
}

function RowSkeleton() {
  return (
    <li className="flex items-center gap-3 rounded-lg border border-border bg-card px-3 py-2">
      <Skeleton className="size-4 shrink-0" />
      <Skeleton className="h-3.5 flex-1" />
      <Skeleton className="h-5 w-20" />
      <Skeleton className="h-5 w-16" />
      <Skeleton className="h-3 w-24" />
    </li>
  )
}

export default function TasksPage() {
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const [view, setView] = useState<ViewMode>(readView)
  const [taskDialogOpen, setTaskDialogOpen] = useState(false)
  const [projectDialogOpen, setProjectDialogOpen] = useState(false)
  const [editingTask, setEditingTask] = useState<Task | undefined>()
  const [pendingDelete, setPendingDelete] = useState<Task | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [bulkRunning, setBulkRunning] = useState(false)

  const filters = useMemo(() => parseFilters(searchParams), [searchParams])
  const page = Math.max(1, Number.parseInt(searchParams.get('page') ?? '1', 10) || 1)
  const filtering = writeFilters(filters).toString() !== writeFilters({}).toString()

  const setFilters = useCallback(
    (next: TaskFilterValue) => {
      // Always back to page 1: a page number that survived a filter change is
      // an offset into a different list.
      setSearchParams(writeFilters(next), { replace: true })
    },
    [setSearchParams],
  )

  const goToPage = useCallback(
    (next: number) => {
      const params = writeFilters(filters)
      if (next > 1) params.set('page', String(next))
      setSearchParams(params, { replace: true })
    },
    [filters, setSearchParams],
  )

  useEffect(() => {
    try {
      window.localStorage.setItem(VIEW_KEY, view)
    } catch {
      // Storage unavailable: the choice still applies for this page load.
    }
  }, [view])

  /**
   * A checkbox is a claim about the rows on screen. Paging, filtering or
   * switching view replaces those rows, so a selection kept across the change
   * would let "Complete selected" act on tasks the reader is no longer looking
   * at — and report a count against a page showing different rows. The ids are
   * kept, not the rows, so the selection is dropped with the page.
   */
  const [selectionScope, setSelectionScope] = useState({ filters, page, view })
  if (
    selectionScope.filters !== filters ||
    selectionScope.page !== page ||
    selectionScope.view !== view
  ) {
    setSelectionScope({ filters, page, view })
    setSelected((current) => (current.length === 0 ? current : []))
  }

  // The URL updates per keystroke; only the request is debounced, so the input
  // never lags behind the person typing in it.
  const search = useDebouncedValue(filters.search ?? '', 300)
  const queryFilters: TaskFilterValue = { ...filters, search: search || undefined }

  const listParams: TaskListParams =
    view === 'board'
      ? { ...queryFilters, limit: MAX_PAGE_SIZE, offset: 0 }
      : { ...queryFilters, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }

  const tasks = useTasks(listParams)
  const projects = useProjects({ limit: MAX_PAGE_SIZE, order: 'asc' })
  const tags = useTags({ limit: MAX_PAGE_SIZE })
  const stats = useActivityStats()

  const projectOptions: Project[] = useMemo(
    () => projects.data?.items ?? [],
    [projects.data],
  )

  const projectNames = useMemo(() => {
    const map = new Map<string, string>()
    for (const project of projectOptions) map.set(project.id, project.name)
    return map
  }, [projectOptions])

  const transition = useTaskTransition()
  const deleteOne = useDeleteTask()
  const completeOne = useCompleteTaskWithStart()
  const createRow = useCreateTask()
  const setTags = useSetTaskTags()

  const items = tasks.data?.items ?? []
  const total = tasks.data?.meta.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  function openNewTask() {
    setEditingTask(undefined)
    setTaskDialogOpen(true)
  }

  function openTask(task: Task | TaskSummary) {
    // Both the list and the board hand over full task rows; the card prop is
    // widened to TaskSummary so a peer surface can render the lean shape too.
    setEditingTask(task as Task)
    setTaskDialogOpen(true)
  }

  /**
   * The backend owns transitions, and the lifecycle table in
   * `features/work/task-transitions.ts` is a mirror of it, so a column the
   * server will refuse is refused here too — with the sentence that says what
   * the card *can* do, which the error envelope's `allowed` list makes
   * possible. An edge the table allows but no route walks is reported as the
   * gap it is rather than sent to `/reopen`, where it would answer 200 and
   * leave the card exactly where it was.
   */
  const moveTask = useCallback(
    async (task: Task, next: TaskStatus) => {
      if (task.status === next) return

      if (!canTransition(task.status, next)) {
        toast.warning('No transition to that column', describeIllegalMove(task.status, next))
        return
      }
      const status = routeFor(task.status, next)
      if (status === null) {
        toast.warning('No endpoint moves it there', describeUnroutableMove(task.status, next))
        return
      }

      try {
        const saved = await transition.mutateAsync({ id: task.id, status })
        toast.info('Moved', `${saved.title} is now ${TASK_STATUS_META[saved.status].label.toLowerCase()}.`)
      } catch (cause) {
        toast.error('The transition was refused', describeRefusal(toApiError(cause)))
      }
    },
    [transition],
  )

  async function runTransition(task: Task, status: TaskTransitionStatus) {
    const copy = TRANSITION_COPY[status]
    try {
      const saved = await transition.mutateAsync({ id: task.id, status })
      toast.success(copy.done, saved.title)
    } catch (cause) {
      toast.error(copy.failed, describeRefusal(toApiError(cause)))
    }
  }

  /**
   * There is no bulk endpoint, so this is one request per task and the summary
   * states the partial outcome. A blanket "done" would be a claim the server
   * never made.
   *
   * **A `todo` row is started and then completed, rather than refused.**
   * `TaskService` has no `todo -> completed` edge, so this used to answer "0 of 1
   * completed" on a row the person had ticked because they had, in fact, done
   * the work. The rule is untouched — the start is a real request, and the feed
   * records both steps — and the decision lives in one place,
   * {@link useCompleteTaskWithStart}, so a second caller gets the same answer
   * instead of re-deriving the edge.
   *
   * **A task the lifecycle genuinely will not complete is named, not counted.**
   * A `blocked` or `cancelled` row is refused before the round trip, reported by
   * title with the server's own sentence, and left selected: clearing it would
   * remove the checkbox that named it.
   */
  async function completeSelected() {
    const ids = [...selected]
    if (ids.length === 0) return
    setBulkRunning(true)

    const rowOf = (id: string): Task | undefined => items.find((task) => task.id === id)
    let completed = 0
    let startedFirst = 0
    const kept: string[] = []
    const refusals: string[] = []

    for (const id of ids) {
      const task = rowOf(id)
      if (!task) {
        // The row left the page between the click and this loop. With no status
        // in hand there is nothing to decide, so the call goes straight out and
        // the server's answer is the only one there is.
        try {
          await completeTask(id)
          completed += 1
        } catch (cause) {
          refusals.push(`“A task”: ${describeRefusal(toApiError(cause))}`)
          kept.push(id)
        }
        continue
      }
      try {
        const outcome = await completeOne.mutateAsync(task)
        if (!outcome.completed) {
          refusals.push(outcome.refusal ?? `“${task.title}” could not be completed.`)
          kept.push(id)
          continue
        }
        completed += 1
        if (outcome.startedFirst) startedFirst += 1
      } catch (cause) {
        refusals.push(`“${task.title}”: ${describeRefusal(toApiError(cause))}`)
        kept.push(id)
      }
    }

    setBulkRunning(false)
    setSelected(kept)
    void queryClient.invalidateQueries({ queryKey: workKeys.tasks() })
    void queryClient.invalidateQueries({ queryKey: workKeys.projects() })
    void queryClient.invalidateQueries({ queryKey: workKeys.stats() })

    // "Started first" is not a detail. Without it the toast reads as if all N
    // rows had been sitting in progress, and the feed has two events per row to
    // contradict it.
    const startedClause =
      startedFirst === 0 ? '' : `${startedFirst} ${startedFirst === 1 ? 'was' : 'were'} started first.`

    if (refusals.length === 0) {
      toast.success(`${completed} task${completed === 1 ? '' : 's'} completed`, startedClause)
      return
    }
    // Three is as much as a toast can carry before the reasons stop being read;
    // the count in the summary is the honest answer to "and the rest?".
    const shown = refusals.slice(0, 3)
    const rest = refusals.length - shown.length
    toast.warning(
      `${completed} of ${ids.length} completed`,
      [
        ...shown,
        rest > 0 ? `…and ${rest} more.` : '',
        startedClause,
        kept.length > 0 ? 'Those tasks are still selected.' : '',
      ]
        .filter(Boolean)
        .join(' '),
    )
  }

  /**
   * Announce a deleted task with an undo attached, in the words the situation
   * actually warrants.
   *
   * **`DELETE /tasks/{id}` removes the row, so undo cannot be a rollback — it is
   * a create.** What comes back is a *new* task under a *new* id, and the toast
   * says exactly that rather than letting the button imply the original row was
   * reinstated. The status is carried because `TaskCreate` accepts one and the
   * backend stamps `completed_at` for it, so a finished task comes back in the
   * column it left instead of sliding to the left of the board.
   */
  function announceDeletedTask(task: Task) {
    // A second click on the button re-creating a row is a person who did not
    // read the toast, not a request for two copies. The flag closes the window;
    // it does not raise an error about the first one having worked.
    let spent = false

    const id = toast.custom({
      title: 'Task deleted',
      variant: 'success',
      description: `“${task.title}” is gone. Undo re-creates it as a new task — a new id, still ${TASK_STATUS_META[task.status].label}.`,
      durationMs: UNDO_WINDOW_MS,
      action: {
        label: 'Undo',
        onClick: () => {
          if (spent) return
          spent = true
          useToastStore.getState().dismiss(id)

          void restoreTask(task).then(
            (restored) => {
              toast.success(
                'Task restored',
                `“${task.title}” is back, as a new task with a new id${restored.tagLinks === 0 ? '' : ` and its ${restored.tagLinks} tag link${restored.tagLinks === 1 ? '' : 's'}`}.`,
              )
            },
            (cause: unknown) => {
              toast.error('Could not restore the task', toApiError(cause).message)
            },
          )
        },
      },
    })
  }

  /**
   * Put one deleted task back, as a new row under the same project. Tag links
   * are a second route applied to the new id — `TaskCreate` has no field for
   * them — so a task that carried tags would come back bare without this step.
   */
  async function restoreTask(task: Task): Promise<{ tagLinks: number }> {
    const restored = await createRow.mutateAsync(taskRestorePayload(task, task.project_id))
    if (task.tag_ids.length === 0) return { tagLinks: 0 }
    await setTags.mutateAsync({ id: restored.id, tagIds: task.tag_ids })
    return { tagLinks: task.tag_ids.length }
  }

  return (
    <div className="app-container space-y-6 py-6">
      <PageHeader
        title="Tasks"
        description="Every task you own, as a dense list or as the board. Filters live in the address bar, so a filtered view survives a refresh."
        actions={
          <>
            <Button variant="outline" onClick={() => setProjectDialogOpen(true)}>
              <FolderPlus aria-hidden="true" />
              New project
            </Button>
            <Button onClick={openNewTask}>
              <Plus aria-hidden="true" />
              New task
            </Button>
          </>
        }
      />

      {stats.isError ? null : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile
            label="Tasks"
            value={stats.data?.tasks.total ?? 0}
            loading={stats.isPending && !stats.data}
          />
          <StatTile
            label="In progress"
            value={stats.data?.tasks.in_progress ?? 0}
            loading={stats.isPending && !stats.data}
          />
          <StatTile
            label="Blocked"
            value={stats.data?.tasks.blocked ?? 0}
            loading={stats.isPending && !stats.data}
          />
          <StatTile
            label="Overdue"
            value={stats.data?.tasks.overdue ?? 0}
            tone="danger"
            loading={stats.isPending && !stats.data}
          />
        </div>
      )}

      <TaskFilters
        value={filters}
        onChange={setFilters}
        projects={projectOptions}
        tags={tags.data?.items ?? []}
        resultCount={tasks.isPending ? undefined : total}
      />

      <Tabs value={view} onValueChange={(next) => setView(next === 'board' ? 'board' : 'list')}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <TabsList>
            <TabsTrigger value="list">
              <LayoutList aria-hidden="true" />
              List
            </TabsTrigger>
            <TabsTrigger value="board">
              <Columns3 aria-hidden="true" />
              Board
            </TabsTrigger>
          </TabsList>

          {view === 'list' && selected.length > 0 && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground" aria-live="polite">
                {selected.length} selected
              </span>
              <Button size="sm" disabled={bulkRunning} onClick={() => void completeSelected()}>
                <CheckSquare aria-hidden="true" />
                {bulkRunning ? 'Completing…' : 'Complete selected'}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setSelected([])}>
                Clear
              </Button>
            </div>
          )}
        </div>

        <TabsContent value="list">
          {tasks.isPending ? (
            <ul className="space-y-2">
              {Array.from({ length: 8 }, (_, index) => (
                <RowSkeleton key={index} />
              ))}
            </ul>
          ) : tasks.isError ? (
            <ErrorState error={toApiError(tasks.error)} onRetry={() => void tasks.refetch()} />
          ) : items.length === 0 ? (
            <EmptyWork
              kind={filtering ? 'matches' : 'tasks'}
              action={
                filtering ? undefined : (
                  <Button onClick={openNewTask}>
                    <Plus aria-hidden="true" />
                    New task
                  </Button>
                )
              }
            />
          ) : (
            <ul className="space-y-2">
              {items.map((task) => (
                <li key={task.id} className="flex items-center gap-1">
                  <TaskCard
                    task={task}
                    compact
                    className="flex-1"
                    projectName={projectNames.get(task.project_id)}
                    onOpen={openTask}
                    onStart={(value) => void runTransition(value as Task, 'in_progress')}
                    onComplete={(value) => void runTransition(value as Task, 'completed')}
                    onReopen={(value) => void runTransition(value as Task, 'reopened')}
                    selected={selected.includes(task.id)}
                    onSelect={(value, next) =>
                      setSelected((current) =>
                        next
                          ? [...current, (value as Task).id]
                          : current.filter((id) => id !== (value as Task).id),
                      )
                    }
                  />
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-8 shrink-0 text-muted-foreground hover:text-destructive"
                    onClick={() => setPendingDelete(task)}
                  >
                    <Trash2 aria-hidden="true" />
                    <span className="sr-only">Delete {task.title}</span>
                  </Button>
                </li>
              ))}
            </ul>
          )}

          {total > PAGE_SIZE && (
            <nav className="mt-4 flex items-center justify-between gap-3" aria-label="Task pages">
              <p className="text-xs text-muted-foreground">
                Page {page} of {totalPages} · {total} matching task{total === 1 ? '' : 's'}
              </p>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page <= 1}
                  onClick={() => goToPage(page - 1)}
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= totalPages}
                  onClick={() => goToPage(page + 1)}
                >
                  Next
                </Button>
              </div>
            </nav>
          )}
        </TabsContent>

        <TabsContent value="board">
          {tasks.isError ? (
            <ErrorState error={toApiError(tasks.error)} onRetry={() => void tasks.refetch()} />
          ) : items.length === 0 && !tasks.isPending ? (
            <EmptyWork
              kind={filtering ? 'board' : 'tasks'}
              action={
                <Button onClick={openNewTask}>
                  <Plus aria-hidden="true" />
                  New task
                </Button>
              }
            />
          ) : (
            <KanbanBoard
              tasks={items}
              loading={tasks.isPending}
              onMove={(task, status) => void moveTask(task, status)}
              onOpen={openTask}
            />
          )}
        </TabsContent>
      </Tabs>

      <TaskDialog
        open={taskDialogOpen}
        onOpenChange={(open) => {
          setTaskDialogOpen(open)
          if (!open) setEditingTask(undefined)
        }}
        task={editingTask}
        projects={projectOptions}
      />

      <ProjectFormDialog open={projectDialogOpen} onOpenChange={setProjectDialogOpen} />

      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null)
        }}
        title="Delete this task?"
        description={
          pendingDelete
            ? `"${pendingDelete.title}" and its subtasks, dependency edges and tag links are removed. The activity history is kept.`
            : ''
        }
        confirmLabel="Delete task"
        destructive
        pending={deleteOne.isPending}
        onConfirm={() => {
          if (!pendingDelete) return
          const task = pendingDelete
          deleteOne
            .mutateAsync(task.id)
            .then(() => {
              setPendingDelete(null)
              // Undo is offered only once the delete has actually landed: an
              // undo on a failed delete would be a create on top of a row that
              // is still there.
              announceDeletedTask(task)
            })
            .catch((cause: unknown) => {
              toast.error('Could not delete the task', toApiError(cause).message)
            })
        }}
      />
    </div>
  )
}