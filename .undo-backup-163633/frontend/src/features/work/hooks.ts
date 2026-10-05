/**
 * TanStack Query bindings for the Phase 3 work surface.
 *
 * **`workKeys` is the single owner of the query-key shape.** `projects()`,
 * `tasks()` and `activity()` called with no argument yield the *prefix* for that
 * family — exactly what `invalidateQueries({ queryKey: workKeys.tasks() })`
 * needs to reach every task list, board, detail, subtask and dependency query
 * at once. Called with params they yield the key for that one query.
 *
 * **Every mutation invalidates the whole `['work']` tree.** A completed task
 * that moves its Kanban column but leaves the counts or the feed stale is the
 * classic failure here; picking keys per mutation is how that ships. The tree is
 * small and the invalidation is cheap, so the aggregate is the correct trade.
 *
 * **Retry policy is inherited.** `app/query-client.ts` refuses to retry a 4xx,
 * so a 404 — another account's id, or one that never existed — surfaces as
 * not-found on the first response rather than being asked for again.
 *
 * **List keys are stabilised.** Params are projected onto a fixed-length key
 * part with the unset ones normalised to `null`, so a filter object re-created on
 * every render hashes to the same key instead of thrashing the cache.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query'

import type { TaskTransitionStatus } from '@/features/work/task-transitions'
import { canTransition, describeIllegalMove } from '@/features/work/task-transitions'
import {
  archiveProject,
  blockTask,
  cancelTask,
  completeProject,
  completeTask,
  createProject,
  createTag,
  createTask,
  deleteProject,
  deleteTask,
  fetchActivity,
  fetchActivityStats,
  fetchProject,
  fetchProjectActivity,
  fetchProjectSummary,
  fetchProjectTasks,
  fetchProjects,
  fetchTags,
  fetchTasks,
  reopenTask,
  restoreProject,
  setTaskTags,
  startTask,
  updateProject,
  updateTask,
  type ProjectCreatePayload,
  type ProjectUpdatePayload,
  type TaskCreatePayload,
  type TaskUpdatePayload,
} from '@/services/work'
import type {
  ActivityEvent,
  ActivityListParams,
  ActivityStats,
  Paginated,
  Project,
  ProjectListParams,
  ProjectSummary,
  ProjectTaskListParams,
  Task,
  TaskListParams,
  UUIDString,
  WorkTag,
} from '@/types/work'

type Enabled = { enabled?: boolean }

function projectKeyPart(params: ProjectListParams = {}): unknown[] {
  return [
    params.limit ?? null,
    params.offset ?? null,
    params.status ?? null,
    params.priority ?? null,
    params.search ?? null,
    params.sort ?? null,
    params.order ?? null,
  ]
}

function taskKeyPart(params: TaskListParams = {}): unknown[] {
  return [
    params.limit ?? null,
    params.offset ?? null,
    params.project_id ?? null,
    params.parent_id ?? null,
    params.status ?? null,
    params.priority ?? null,
    params.due_before ?? null,
    params.due_after ?? null,
    params.search ?? null,
    // Sorted so `['a','b']` and `['b','a']` — the same filter — share a key.
    [...(params.tag_ids ?? [])].sort().join(','),
    params.sort ?? null,
    params.order ?? null,
  ]
}

function activityKeyPart(params: ActivityListParams = {}): unknown[] {
  return [
    params.limit ?? null,
    params.offset ?? null,
    params.project_id ?? null,
    params.task_id ?? null,
    params.event_type ?? null,
  ]
}

function projectTaskKeyPart(params: ProjectTaskListParams = {}): unknown[] {
  return [params.limit ?? null, params.offset ?? null, params.status ?? null]
}

/** Stable key factory. Every key lives under the `['work']` root. */
export const workKeys = {
  all: () => ['work'] as const,
  projects: (params?: ProjectListParams) =>
    (params
      ? ['work', 'projects', 'list', ...projectKeyPart(params)]
      : ['work', 'projects']) as readonly unknown[],
  project: (id: UUIDString | null | undefined) => ['work', 'project', id ?? null] as const,
  projectSummary: (id: UUIDString | null | undefined) =>
    ['work', 'project', id ?? null, 'summary'] as const,
  projectActivity: (id: UUIDString | null | undefined, params?: ActivityListParams) =>
    ['work', 'project', id ?? null, 'activity', ...activityKeyPart(params)] as const,
  projectTasks: (id: UUIDString | null | undefined, params?: ProjectTaskListParams) =>
    ['work', 'project', id ?? null, 'tasks', ...projectTaskKeyPart(params)] as const,
  tasks: (params?: TaskListParams) =>
    (params ? ['work', 'tasks', 'list', ...taskKeyPart(params)] : ['work', 'tasks']) as readonly unknown[],
  task: (id: UUIDString | null | undefined) => ['work', 'task', id ?? null] as const,
  subtasks: (id: UUIDString | null | undefined) => ['work', 'task', id ?? null, 'subtasks'] as const,
  dependencies: (id: UUIDString | null | undefined) =>
    ['work', 'task', id ?? null, 'dependencies'] as const,
  tags: (params?: { limit?: number; offset?: number }) =>
    ['work', 'tags', params?.limit ?? null, params?.offset ?? null] as readonly unknown[],
  activity: (params?: ActivityListParams) =>
    (params
      ? ['work', 'activity', 'list', ...activityKeyPart(params)]
      : ['work', 'activity']) as readonly unknown[],
  stats: () => ['work', 'stats'] as const,
}

/* ------------------------------------------------------------------ queries */

export function useProjects(
  params: ProjectListParams = {},
  options: Enabled = {},
): UseQueryResult<Paginated<Project>> {
  return useQuery({
    queryKey: workKeys.projects(params),
    queryFn: ({ signal }) => fetchProjects(params, signal),
    enabled: options.enabled,
  })
}

export function useProject(id: UUIDString | null | undefined): UseQueryResult<Project> {
  return useQuery({
    queryKey: workKeys.project(id),
    queryFn: ({ signal }) => fetchProject(id as UUIDString, signal),
    enabled: Boolean(id),
  })
}

export function useProjectSummary(
  id: UUIDString | null | undefined,
): UseQueryResult<ProjectSummary> {
  return useQuery({
    queryKey: workKeys.projectSummary(id),
    queryFn: ({ signal }) => fetchProjectSummary(id as UUIDString, signal),
    enabled: Boolean(id),
  })
}

export function useProjectActivity(
  id: UUIDString | null | undefined,
  params: ActivityListParams = {},
): UseQueryResult<Paginated<ActivityEvent>> {
  return useQuery({
    queryKey: workKeys.projectActivity(id, params),
    queryFn: ({ signal }) => fetchProjectActivity(id as UUIDString, params, signal),
    enabled: Boolean(id),
  })
}

export function useProjectTasks(
  id: UUIDString | null | undefined,
  params: ProjectTaskListParams = {},
): UseQueryResult<Paginated<Task>> {
  return useQuery({
    queryKey: workKeys.projectTasks(id, params),
    queryFn: ({ signal }) => fetchProjectTasks(id as UUIDString, params, signal),
    enabled: Boolean(id),
    // Keeps a column from collapsing back to a blank board mid-transition; the
    // invalidated refetch still replaces the result.
    placeholderData: (previous) => previous,
  })
}

export function useTasks(
  params: TaskListParams = {},
  options: Enabled = {},
): UseQueryResult<Paginated<Task>> {
  return useQuery({
    queryKey: workKeys.tasks(params),
    queryFn: ({ signal }) => fetchTasks(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

export function useTags(
  params: { limit?: number; offset?: number } = {},
): UseQueryResult<Paginated<WorkTag>> {
  return useQuery({
    queryKey: workKeys.tags(params),
    queryFn: ({ signal }) => fetchTags(params, signal),
    staleTime: 5 * 60_000,
  })
}

export function useActivity(
  params: ActivityListParams = {},
  options: Enabled = {},
): UseQueryResult<Paginated<ActivityEvent>> {
  return useQuery({
    queryKey: workKeys.activity(params),
    queryFn: ({ signal }) => fetchActivity(params, signal),
    enabled: options.enabled,
  })
}

export function useActivityStats(options: Enabled = {}): UseQueryResult<ActivityStats> {
  return useQuery({
    queryKey: workKeys.stats(),
    queryFn: ({ signal }) => fetchActivityStats(signal),
    enabled: options.enabled,
  })
}

/* ---------------------------------------------------------------- mutations */

/**
 * Every mutation invalidates the same aggregate prefix, so no write can ship
 * having forgotten to move the board, the counts or the feed.
 */
function useInvalidateWork() {
  const queryClient = useQueryClient()
  return () => {
    void queryClient.invalidateQueries({ queryKey: workKeys.all() })
  }
}

export function useCreateProject(): UseMutationResult<Project, Error, ProjectCreatePayload> {
  return useMutation({ mutationFn: createProject, onSuccess: useInvalidateWork() })
}

export function useUpdateProject(): UseMutationResult<
  Project,
  Error,
  { id: UUIDString; payload: ProjectUpdatePayload }
> {
  return useMutation({
    mutationFn: ({ id, payload }) => updateProject(id, payload),
    onSuccess: useInvalidateWork(),
  })
}

export function useDeleteProject(): UseMutationResult<void, Error, UUIDString> {
  return useMutation({ mutationFn: deleteProject, onSuccess: useInvalidateWork() })
}

/** `complete`, `archive` and `restore` are three routes with one shape. */
export function useProjectTransition(): UseMutationResult<
  Project,
  Error,
  { id: UUIDString; transition: 'complete' | 'archive' | 'restore' }
> {
  return useMutation({
    mutationFn: ({ id, transition }) => {
      if (transition === 'complete') return completeProject(id)
      if (transition === 'archive') return archiveProject(id)
      return restoreProject(id)
    },
    onSuccess: useInvalidateWork(),
  })
}

export function useCreateTask(): UseMutationResult<Task, Error, TaskCreatePayload> {
  return useMutation({ mutationFn: createTask, onSuccess: useInvalidateWork() })
}

export function useUpdateTask(): UseMutationResult<
  Task,
  Error,
  { id: UUIDString; payload: TaskUpdatePayload }
> {
  return useMutation({
    mutationFn: ({ id, payload }) => updateTask(id, payload),
    onSuccess: useInvalidateWork(),
  })
}

export function useDeleteTask(): UseMutationResult<void, Error, UUIDString> {
  return useMutation({ mutationFn: deleteTask, onSuccess: useInvalidateWork() })
}

/**
 * The lifecycle transitions behind one call, for a control that offers a
 * "what next?" menu. The server decides whether the move is legal and a 422 is
 * surfaced rather than swallowed.
 *
 * **Every route in `TaskService._LEGAL_TRANSITIONS` that an endpoint actually
 * walks appears here**, `/start` included. It used to be missing, which left
 * the client offering Complete on a `todo` card — a move the server refuses —
 * while the route that would have made it legal sat unused.
 */
export function useTaskTransition(): UseMutationResult<
  Task,
  Error,
  { id: UUIDString; status: TaskTransitionStatus; note?: string }
> {
  return useMutation({
    mutationFn: ({ id, status, note }) => {
      if (status === 'completed') return completeTask(id)
      if (status === 'reopened') return reopenTask(id)
      if (status === 'in_progress') return startTask(id)
      if (status === 'cancelled') return cancelTask(id)
      return blockTask(id, note)
    },
    onSuccess: useInvalidateWork(),
  })
}

/**
 * What one attempt to complete a task actually did.
 *
 * A union's worth of outcomes in a record rather than three flags, because the
 * caller has to say all three apart: a bulk toast that reports "3 completed"
 * without naming the one that had to be started first is claiming the same thing
 * happened three times when it did not.
 */
export interface CompleteTaskOutcome {
  /** The row as the server returned it, or the row passed in on a refusal. */
  task: Task
  /** Whether the task is `completed` now. */
  completed: boolean
  /** Whether `todo -> completed` had to be walked as two real transitions. */
  startedFirst: boolean
  /** Why it did not complete, already phrased for a reader. Never both true and set. */
  refusal: string | null
}

/**
 * Completing a task, including the one edge of the lifecycle that needs two
 * calls to take.
 *
 * **`todo -> completed` is not an edge the server has.** `TaskService` allows
 * `completed` from `in_progress` only — *"work nobody started does not get to
 * claim it was finished"* — so pressing "Complete selected" on a `todo` row
 * used to answer with a refusal that was correct and useless. **The rule is
 * kept and the server is untouched; what changed is that the client walks the
 * edge that does exist**, `POST /tasks/{id}/start` and then
 * `POST /tasks/{id}/complete`.
 *
 * Two round trips rather than one optimistic status write, deliberately. The
 * feed records a `task_started` event and then a `task_completed` one,
 * `started_at` is the moment the work began rather than a value the browser
 * invented, and a watcher on the board saw the same two steps a person dragging
 * the card across two columns would have produced.
 *
 * `blocked` and `cancelled` still refuse before the round trip — the meaning of
 * `blocked` is that the work did not happen, and `cancelled` is terminal — and
 * the sentence comes out of the same {@link describeIllegalMove} mirror the card
 * reads, so the bulk path and a single card cannot disagree about the rules.
 * A refusal is a *result* here, not a throw: it is an outcome the caller
 * reports by name, and the lifecycle answered without a request being wasted.
 *
 * This is the one place the rule lives. The task card keeps its deliberate
 * Start → Complete → Reopen ladder — a ladder is a sequence of deliberate
 * decisions, and quietly turning its first rung into a compound action would be
 * a lie about what the button did — so anything that means "complete this, now"
 * rather than "offer the next step" comes here instead.
 */
export function useCompleteTaskWithStart(): UseMutationResult<
  CompleteTaskOutcome,
  Error,
  Task
> {
  return useMutation({
    mutationFn: async (task: Task): Promise<CompleteTaskOutcome> => {
      const startedFirst = task.status === 'todo'
      if (!startedFirst && !canTransition(task.status, 'completed')) {
        return {
          task,
          completed: false,
          startedFirst: false,
          refusal: `“${task.title}” — ${describeIllegalMove(task.status, 'completed')}`,
        }
      }

      if (startedFirst) await startTask(task.id)
      const saved = await completeTask(task.id)
      return { task: saved, completed: true, startedFirst, refusal: null }
    },
    onSuccess: useInvalidateWork(),
  })
}

export function useSetTaskTags(): UseMutationResult<
  UUIDString[],
  Error,
  { id: UUIDString; tagIds: UUIDString[] }
> {
  return useMutation({
    mutationFn: ({ id, tagIds }) => setTaskTags(id, tagIds),
    onSuccess: useInvalidateWork(),
  })
}

export function useCreateTag(): UseMutationResult<WorkTag, Error, string> {
  return useMutation({ mutationFn: createTag, onSuccess: useInvalidateWork() })
}

/* ------------------------------------------------------ re-creating a delete */

/**
 * There is no undelete: `DELETE /tasks/{id}` removes the row, so undo can only
 * be a create. These two builders are the one place that decision is written
 * down, because both delete surfaces need it and a payload that quietly diverges
 * between them is a payload that quietly loses something.
 *
 * **Every payload here describes a new row, not the old one.** The id is minted
 * by the database, `created_at` is now, and the activity feed records a
 * `*_created` event rather than pretending the delete was rolled back. Callers
 * say so in the toast rather than claiming a resurrection.
 */

/**
 * The create payload that reproduces a deleted task as closely as the API
 * allows, filed under `projectId` — which is the *new* project's id when undo is
 * restoring a whole tree, and the same one when only the task went.
 *
 * `status` is carried because `TaskCreate` is the one payload that accepts one,
 * and the backend stamps `completed_at` when it is `completed`, so a finished
 * task comes back in the column it left rather than sliding to the left of the
 * board. `parent_id` is deliberately dropped: a subtask's parent is named by id,
 * and the parent comes back under a different one, so carrying the old id would
 * attach the subtask to somebody else's task or 404.
 *
 * Tag links are not here either — they have their own route, `setTaskTags` — so
 * the caller applies them once the new row has an id to attach them to.
 */
export function taskRestorePayload(task: Task, projectId: UUIDString): TaskCreatePayload {
  return {
    project_id: projectId,
    title: task.title,
    description: task.description,
    priority: task.priority,
    status: task.status,
    start_date: task.start_date,
    due_date: task.due_date,
    estimated_minutes: task.estimated_minutes,
  }
}

/**
 * The same, one level up. **`status` is absent because `ProjectCreate` has no
 * such field**: a project is always born `planned` and reaches every other state
 * through a transition, so a restored project returns to the board as planned
 * work whatever it was before. The toast has to say so.
 */
export function projectRestorePayload(project: Project): ProjectCreatePayload {
  return {
    name: project.name,
    description: project.description,
    priority: project.priority,
    start_date: project.start_date,
    target_date: project.target_date,
  }
}

