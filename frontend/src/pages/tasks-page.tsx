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
  useDeleteTask,
  useProjects,
  useTags,
  useTaskTransition,
  useTasks,
  workKeys,
} from '@/features/work/hooks'
import { useDebouncedValue } from '@/hooks/use-debounce'
import { cn } from '@/lib/utils'
import { toApiError } from '@/services/errors'
import { completeTask } from '@/services/work'
import { toast } from '@/stores/toast-store'
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

type ViewMode = 'list' | 'board'

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
   * The backend owns transitions, and it exposes only three: complete, reopen
   * and block. A drop into a column none of them serves is reported rather than
   * swallowed, and a drop they do serve is answered by the server — which
   * refuses an illegal one (an open prerequisite, a cancelled task) with a
   * message that reaches the user instead of leaving a card in a column it
   * never entered.
   */
  const moveTask = useCallback(
    async (task: Task, next: TaskStatus) => {
      if (task.status === next) return

      const served =
        next === 'completed' || next === 'blocked' || (task.status === 'completed' && next === 'todo')
      if (!served) {
        toast.warning(
          'No transition to that column',
          `The API can only complete, reopen or block a task, so "${task.title}" stays ${TASK_STATUS_META[task.status].label.toLowerCase()}.`,
        )
        return
      }

      const status = next === 'completed' ? 'completed' : next === 'blocked' ? 'blocked' : 'reopened'
      try {
        const saved = await transition.mutateAsync({ id: task.id, status })
        toast.info('Moved', `${saved.title} is now ${TASK_STATUS_META[saved.status].label.toLowerCase()}.`)
      } catch (cause) {
        toast.error('The transition was refused', toApiError(cause).message)
      }
    },
    [transition],
  )

  async function runTransition(task: Task, status: 'completed' | 'reopened') {
    try {
      const saved = await transition.mutateAsync({ id: task.id, status })
      toast.success(status === 'completed' ? 'Task completed' : 'Task reopened', saved.title)
    } catch (cause) {
      toast.error(
        status === 'completed' ? 'Could not complete the task' : 'Could not reopen the task',
        toApiError(cause).message,
      )
    }
  }

  /**
   * There is no bulk endpoint, so this is one request per task and the summary
   * states the partial outcome. A blanket "done" would be a claim the server
   * never made.
   */
  async function completeSelected() {
    const ids = [...selected]
    if (ids.length === 0) return
    setBulkRunning(true)

    let completed = 0
    const failures: string[] = []
    for (const id of ids) {
      try {
        await completeTask(id)
        completed += 1
      } catch (cause) {
        failures.push(toApiError(cause).message)
      }
    }

    setBulkRunning(false)
    setSelected([])
    void queryClient.invalidateQueries({ queryKey: workKeys.tasks() })
    void queryClient.invalidateQueries({ queryKey: workKeys.projects() })
    void queryClient.invalidateQueries({ queryKey: workKeys.stats() })

    if (failures.length === 0) {
      toast.success(`${completed} task${completed === 1 ? '' : 's'} completed`)
      return
    }
    toast.warning(
      `${completed} of ${ids.length} completed`,
      `${failures.length} refused — ${failures[0]}`,
    )
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
          const title = pendingDelete.title
          deleteOne
            .mutateAsync(pendingDelete.id)
            .then(() => {
              setPendingDelete(null)
              toast.success('Task deleted', title)
            })
            .catch((cause: unknown) => {
              toast.error('Could not delete the task', toApiError(cause).message)
            })
        }}
      />
    </div>
  )
}