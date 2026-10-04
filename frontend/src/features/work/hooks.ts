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

import {
  archiveProject,
  blockTask,
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
 * The three lifecycle transitions behind one call, for a control that offers a
 * "what next?" menu. The server decides whether the move is legal and a 422 is
 * surfaced rather than swallowed.
 */
export function useTaskTransition(): UseMutationResult<
  Task,
  Error,
  { id: UUIDString; status: 'completed' | 'reopened' | 'blocked'; note?: string }
> {
  return useMutation({
    mutationFn: ({ id, status, note }) => {
      if (status === 'completed') return completeTask(id)
      if (status === 'reopened') return reopenTask(id)
      return blockTask(id, note)
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

