import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Pencil, Plus, Trash2 } from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { PageHeader } from '@/components/feedback/page-header'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { ConfirmDialog } from '@/features/work/components/confirm-dialog'
import { EmptyWork } from '@/features/work/components/empty-work'
import { PriorityBadge } from '@/features/work/components/priority-badge'
import { ProjectFormDialog } from '@/features/work/components/project-form-dialog'
import { StatusBadge } from '@/features/work/components/status-badge'
import { WorkProgress } from '@/features/work/components/work-progress'
import {
  projectRestorePayload,
  taskRestorePayload,
  useCreateProject,
  useCreateTask,
  useDeleteProject,
  useProjectSummary,
  useProjectTasks,
  useProjects,
  useSetTaskTags,
} from '@/features/work/hooks'
import { toApiError } from '@/services/errors'
import { toast, useToastStore } from '@/stores/toast-store'
import { MAX_PAGE_SIZE, PROJECT_STATUS_META } from '@/types/work'
import type { Paginated, Project, Task } from '@/types/work'

const PAGE_LIMIT = 25

/**
 * How long a delete's undo stays on the toast. The store's own default is five
 * seconds, which is long enough to read a message and not long enough to notice
 * one; ten is the shortest window in which a person who has just hit the wrong
 * button can still catch it. It is a window and not a promise — the button only
 * exists while the toast does, and the toast disappears on its own after this.
 */
const UNDO_WINDOW_MS = 10_000

/**
 * How many of a project's tasks undo is prepared to hold and re-create.
 *
 * `MAX_PAGE_SIZE` is the largest page `GET /projects/{id}/tasks` will answer,
 * and it is also the point past which "Undo" stops being a correction and
 * becomes a bulk import nobody asked for: twenty seconds of requests fired from
 * a toast button. Above it the toast restores the project alone and says the
 * tasks are gone, rather than quietly bringing back the first hundred and
 * leaving the reader to assume that was all of them.
 */
const PROJECT_UNDO_TASK_LIMIT = MAX_PAGE_SIZE

/** A restored project is always `planned`: `ProjectCreate` carries no status. */
const RESTORED_PROJECT_STATUS = PROJECT_STATUS_META.planned.label.toLowerCase()

/**
 * What undo knows about the tasks inside the project about to be deleted.
 *
 * Four cases rather than a boolean, because the honest sentence differs in every
 * one of them: "it had none", "here they all are", "there were too many to
 * bring back" and "I never found out" are four different claims, and collapsing
 * the last three into "restores the project" is how an undo ends up quietly
 * deleting work it said it would restore.
 */
type UndoableTasks =
  | { kind: 'unknown' }
  | { kind: 'empty' }
  | { kind: 'too_many'; total: number }
  | { kind: 'all'; tasks: Task[] }

function pluralTasks(count: number): string {
  return `${count} task${count === 1 ? '' : 's'}`
}

/**
 * Reads the project's tasks once, at the moment the confirm dialog opens, and
 * turns them into the promise undo will keep.
 *
 * `useProjectTasks` carries `placeholderData`, so for the frame after the dialog
 * changes project the observer can still be holding the *previous* project's
 * rows. Re-creating those under a newly created project would file one set of
 * tasks inside a different project, so a page that does not agree with itself
 * about which project it is describing counts as not read.
 */
function readUndoableTasks(
  page: Paginated<Task> | undefined,
  isError: boolean,
  projectId: string | undefined,
): UndoableTasks {
  if (projectId === undefined || isError || page === undefined) return { kind: 'unknown' }
  if (!page.items.every((task) => task.project_id === projectId)) return { kind: 'unknown' }
  if (page.meta.total > PROJECT_UNDO_TASK_LIMIT) {
    return { kind: 'too_many', total: page.meta.total }
  }
  if (page.items.length === 0) return { kind: 'empty' }
  return { kind: 'all', tasks: page.items }
}

/**
 * What the confirm dialog promises, read off the same union the undo keeps — so
 * the button and the offer cannot tell the user two different stories.
 *
 * This is still a confirm dialog. The copy says what Undo will do; it does not
 * ask the reader to choose between losing the project and losing its tasks.
 */
function undoPromise(project: Project, kind: UndoableTasks): string {
  const name = `“${project.name}”`
  switch (kind.kind) {
    case 'all':
      return `${name} and its ${pluralTasks(kind.tasks.length)} will be removed. Undo re-creates the project and the ${kind.tasks.length} tasks it has just read — as new rows, with new ids.`
    case 'empty':
      return `${name} will be removed. Undo re-creates it as a new project, back at ${RESTORED_PROJECT_STATUS}. It has no tasks, so nothing else comes back.`
    case 'too_many':
      return `${name} and its ${pluralTasks(kind.total)} will be removed. Undo re-creates the project alone — there are too many tasks to bring back — and the toast will say so.`
    case 'unknown':
      return `${name} and the tasks inside it will be removed. Undo re-creates the project as a new row, back at ${RESTORED_PROJECT_STATUS}. Its tasks are gone; they were not read before the delete.`
  }
}

/** The same union again, in the past tense, for the toast that offers the undo. */
function undoOffer(project: Project, kind: UndoableTasks): string {
  const name = `“${project.name}” is gone.`
  switch (kind.kind) {
    case 'all':
      return `${name} Undo re-creates it and its ${pluralTasks(kind.tasks.length)} — as new rows, with new ids.`
    case 'empty':
      return `${name} Undo re-creates it as a new project, back at ${RESTORED_PROJECT_STATUS}. It had no tasks, so nothing else comes back.`
    case 'too_many':
      return `${name} Undo re-creates it as a new project, back at ${RESTORED_PROJECT_STATUS}. Its ${pluralTasks(kind.total)} are gone and will not come back — too many to restore.`
    case 'unknown':
      return `${name} Undo re-creates it as a new project, back at ${RESTORED_PROJECT_STATUS}. Its tasks are gone; they were not read before the delete.`
  }
}

/** Date-only strings are parsed as UTC by `Date`, which shifts the day west of Greenwich. */
function formatDay(value: string | null): string {
  if (!value) return 'No target date'
  const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  const date = parts
    ? new Date(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]))
    : new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(date)
}

/**
 * Counts and progress for one row.
 *
 * The list endpoint returns bare projects; the per-project summary is where the
 * counts live, so a row asks for its own. A failure here costs that row its
 * numbers rather than the whole list, so it degrades to a dash.
 */
function RowProgress({ projectId, name }: { projectId: string; name: string }) {
  const summary = useProjectSummary(projectId)

  if (summary.isPending) return <Skeleton className="h-8 w-36" />
  const data = summary.data
  if (!data) return <span className="text-xs text-muted-foreground">Counts unavailable</span>

  return (
    <div className="w-full sm:w-36">
      <WorkProgress value={data.progress_percent} label={`${name} completion`} />
      <p className="mt-1.5 text-xs text-muted-foreground">
        {data.completed_task_count} of {data.task_count} tasks
      </p>
    </div>
  )
}

function ProjectRowSkeleton() {
  return (
    <li className="flex flex-col gap-4 px-4 py-4 sm:flex-row sm:items-center sm:gap-6">
      <div className="min-w-0 flex-1 space-y-2">
        <Skeleton className="h-4 w-56 max-w-full" />
        <Skeleton className="h-3 w-full max-w-md" />
      </div>
      <div className="flex gap-2">
        <Skeleton className="h-6 w-24" />
        <Skeleton className="h-6 w-16" />
      </div>
      <Skeleton className="h-8 w-36" />
      <Skeleton className="h-8 w-16" />
    </li>
  )
}

function ProjectRow({
  project,
  onEdit,
  onDelete,
}: {
  project: Project
  onEdit: (project: Project) => void
  onDelete: (project: Project) => void
}) {
  return (
    <li className="flex flex-col gap-4 px-4 py-4 sm:flex-row sm:items-center sm:gap-6">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Link
            to={`/projects/${project.id}`}
            className="truncate rounded-sm text-sm font-medium text-foreground underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          >
            {project.name}
          </Link>
          <StatusBadge status={project.status} kind="project" />
          <PriorityBadge priority={project.priority} />
        </div>
        {project.description ? (
          <p className="mt-1 line-clamp-1 text-sm text-muted-foreground">{project.description}</p>
        ) : (
          <p className="mt-1 text-sm italic text-muted-foreground">No description</p>
        )}
        <p className="mt-1 text-xs text-muted-foreground">Target: {formatDay(project.target_date)}</p>
      </div>

      <RowProgress projectId={project.id} name={project.name} />

      <div className="flex shrink-0 items-center gap-1">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={() => onEdit(project)}
          aria-label={`Edit ${project.name}`}
        >
          <Pencil aria-hidden="true" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="text-muted-foreground hover:text-destructive"
          onClick={() => onDelete(project)}
          aria-label={`Delete ${project.name}`}
        >
          <Trash2 aria-hidden="true" />
        </Button>
      </div>
    </li>
  )
}

export default function ProjectsPage() {
  const projects = useProjects({ limit: PAGE_LIMIT, offset: 0 })

  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Project | undefined>(undefined)
  const [pendingDelete, setPendingDelete] = useState<Project | null>(null)

  const remove = useDeleteProject()
  const recreateProject = useCreateProject()
  const recreateTask = useCreateTask()
  const setTags = useSetTaskTags()

  /**
   * Read the doomed project's tasks while the dialog is up, not after the delete
   * has landed — afterwards the endpoint is a 404 and the tasks are gone for
   * good. Owner-scoped and bounded by the same page the project detail uses.
   */
  const pendingTasks = useProjectTasks(pendingDelete?.id ?? null, {
    limit: PROJECT_UNDO_TASK_LIMIT,
  })
  const undoableTasks = useMemo(
    () => readUndoableTasks(pendingTasks.data, pendingTasks.isError, pendingDelete?.id),
    [pendingTasks.data, pendingTasks.isError, pendingDelete?.id],
  )

  const items = projects.data?.items ?? []
  const total = projects.data?.meta.total ?? items.length

  function openCreate() {
    setEditing(undefined)
    setFormOpen(true)
  }

  function openEdit(project: Project) {
    setEditing(project)
    setFormOpen(true)
  }

  /**
   * Announce the delete with an undo attached.
   *
   * `DELETE /projects/{id}` cascades to the tasks inside it, so undo cannot be a
   * rollback — it is a create, of the project *and* of the tasks read a moment
   * earlier, every one of them a new row with a new id. The toast states which
   * of the two it is about to do, because a button that silently brings back
   * half a project is worse than no button at all.
   *
   * Undo is offered only after the delete has actually landed: on a failed
   * delete there is nothing to undo, and offering it would be a create on top of
   * a project that is still sitting there.
   */
  function announceDeletedProject(project: Project, kind: UndoableTasks) {
    // A second click re-creating rows is a person who did not read the toast,
    // not a request for two projects. The flag closes the window; it does not
    // raise an error about the first one having worked.
    let spent = false

    const id = toast.custom({
      title: 'Project deleted',
      variant: 'success',
      description: undoOffer(project, kind),
      durationMs: UNDO_WINDOW_MS,
      action: {
        label: 'Undo',
        onClick: () => {
          if (spent) return
          spent = true
          useToastStore.getState().dismiss(id)

          void restoreDeletedProject(project, kind).then(
            (restored) => {
              if (kind.kind !== 'all') {
                toast.success(
                  'Project restored',
                  `“${project.name}” is back as a new project with a new id, at ${RESTORED_PROJECT_STATUS}.${restored.tasksGone}`,
                )
                return
              }
              const missing = kind.tasks.length - restored.tasks
              toast.success(
                'Project restored',
                [
                  `“${project.name}” is back as a new project with a new id, at ${RESTORED_PROJECT_STATUS}, with ${restored.tasks} of ${kind.tasks.length} ${kind.tasks.length === 1 ? 'task' : 'tasks'} re-created.`,
                  missing > 0 ? `${missing} could not be.` : '',
                ]
                  .filter(Boolean)
                  .join(' '),
              )
            },
            (cause: unknown) => {
              toast.error('Could not restore the project', toApiError(cause).message)
            },
          )
        },
      },
    })
  }

  /**
   * Put the project back, and with it every task undo read in time.
   *
   * Sequentially, because this runs from a toast button with no progress UI and
   * a burst of concurrent writes would turn one accidental delete into a burst
   * of retries the server has no reason to enjoy.
   */
  async function restoreDeletedProject(
    project: Project,
    kind: UndoableTasks,
  ): Promise<{ tasks: number; tasksGone: string }> {
    const created = await recreateProject.mutateAsync(projectRestorePayload(project))
    if (kind.kind !== 'all') {
      return {
        tasks: 0,
        tasksGone:
          kind.kind === 'empty'
            ? ' It had no tasks, so there was nothing else to bring back.'
            : ` Its ${kind.kind === 'too_many' ? pluralTasks(kind.total) : 'tasks'} did not come back.`,
      }
    }

    let tasks = 0
    for (const task of kind.tasks) {
      try {
        const row = await recreateTask.mutateAsync(taskRestorePayload(task, created.id))
        // `TaskCreate` has no field for tag links; they are a second route, and
        // applied against the new id because the old one is gone.
        if (task.tag_ids.length > 0) {
          await setTags.mutateAsync({ id: row.id, tagIds: task.tag_ids })
        }
        tasks += 1
      } catch {
        // One task that will not come back — a title the server now rejects, a
        // set of tags that has since changed — must not abandon the rest. The
        // project and the other tasks are still worth having, and the toast
        // counts what actually arrived rather than what was asked for.
      }
    }
    return { tasks, tasksGone: '' }
  }

  return (
    <div className="app-container py-6 lg:py-8">
      <PageHeader
        title="Projects"
        eyebrow="Work"
        description="Every effort you are running, and how far each one has got."
        actions={
          <Button type="button" onClick={openCreate}>
            <Plus aria-hidden="true" />
            New project
          </Button>
        }
      />

      <div className="mt-6" aria-busy={projects.isPending}>
        {projects.isPending ? (
          <ul className="divide-y divide-border rounded-lg border border-border">
            {Array.from({ length: 5 }, (_, index) => (
              <ProjectRowSkeleton key={index} />
            ))}
          </ul>
        ) : projects.isError ? (
          <ErrorState error={toApiError(projects.error)} onRetry={() => void projects.refetch()} />
        ) : items.length === 0 ? (
          <div className="rounded-lg border border-border">
            <EmptyWork
              kind="projects"
              action={
                <Button type="button" onClick={openCreate}>
                  <Plus aria-hidden="true" />
                  New project
                </Button>
              }
            />
          </div>
        ) : (
          <>
            <ul className="divide-y divide-border rounded-lg border border-border">
              {items.map((project) => (
                <ProjectRow
                  key={project.id}
                  project={project}
                  onEdit={openEdit}
                  onDelete={setPendingDelete}
                />
              ))}
            </ul>
            <p className="mt-3 text-xs text-muted-foreground">
              Showing {items.length} of {total}
              {total > items.length ? ' projects — the rest are further down the sequence.' : ''}
            </p>
          </>
        )}
      </div>

      <ProjectFormDialog
        open={formOpen}
        project={editing}
        onOpenChange={(open) => {
          setFormOpen(open)
          if (!open) setEditing(undefined)
        }}
        onSaved={(saved) => {
          setFormOpen(false)
          setEditing(undefined)
          toast.success(editing ? 'Project updated' : 'Project created', saved.name)
        }}
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open && !remove.isPending) setPendingDelete(null)
        }}
        title="Delete this project?"
        description={pendingDelete ? undoPromise(pendingDelete, undoableTasks) : ''}
        confirmLabel="Delete project"
        destructive
        pending={remove.isPending}
        onConfirm={() => {
          if (!pendingDelete) return
          const project = pendingDelete
          const kind = undoableTasks
          remove.mutate(project.id, {
            onSuccess: () => {
              setPendingDelete(null)
              announceDeletedProject(project, kind)
            },
            onError: (cause) => {
              toast.error('Could not delete that project', toApiError(cause).message)
            },
          })
        }}
      />
    </div>
  )
}
