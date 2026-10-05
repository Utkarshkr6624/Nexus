/**
 * Thin typed wrappers over the Phase 3 work endpoints.
 *
 * No React here — every function is a promise-returning call the hooks in
 * `features/work/hooks.ts` wrap in a `queryFn`/`mutationFn`.
 *
 * Two shapes in this file are dictated by the backend rather than chosen:
 *
 * - `addTaskDependency` sends `depends_on_id` as a **query parameter**, because
 *   the route has no request body, and returns the **blocker** rather than the
 *   task that was blocked.
 * - `blockTask` posts an optional body carrying only a note; the route pins the
 *   target status to `blocked`.
 */
import { apiClient, queryFrom } from '@/lib/api-client'
import type { Paginated } from '@/types/pagination'
import type {
  ActivityEvent,
  ActivityStats,
  DateOnlyString,
  Project,
  ProjectListParams,
  ProjectPriority,
  ProjectStatus,
  ProjectSummary,
  Task,
  TaskListParams,
  TaskPriority,
  TaskStatus,
  TaskSummary,
  UUIDString,
  WorkTag,
} from '@/types/work'

export const WORK_ENDPOINTS = {
  projects: '/projects',
  project: (id: UUIDString) => `/projects/${id}`,
  projectComplete: (id: UUIDString) => `/projects/${id}/complete`,
  projectArchive: (id: UUIDString) => `/projects/${id}/archive`,
  projectRestore: (id: UUIDString) => `/projects/${id}/restore`,
  projectSummary: (id: UUIDString) => `/projects/${id}/summary`,
  projectActivity: (id: UUIDString) => `/projects/${id}/activity`,
  projectTasks: (id: UUIDString) => `/projects/${id}/tasks`,
  tasks: '/tasks',
  task: (id: UUIDString) => `/tasks/${id}`,
  taskComplete: (id: UUIDString) => `/tasks/${id}/complete`,
  taskReopen: (id: UUIDString) => `/tasks/${id}/reopen`,
  taskStart: (id: UUIDString) => `/tasks/${id}/start`,
  taskBlock: (id: UUIDString) => `/tasks/${id}/block`,
  taskCancel: (id: UUIDString) => `/tasks/${id}/cancel`,
  taskPriority: (id: UUIDString) => `/tasks/${id}/priority`,
  taskSubtasks: (id: UUIDString) => `/tasks/${id}/subtasks`,
  taskDependencies: (id: UUIDString) => `/tasks/${id}/dependencies`,
  taskDependency: (id: UUIDString, dependsOnId: UUIDString) =>
    `/tasks/${id}/dependencies/${dependsOnId}`,
  taskTags: (id: UUIDString) => `/tasks/${id}/tags`,
  tags: '/tags',
  tag: (id: UUIDString) => `/tags/${id}`,
  activity: '/activity',
  activityStats: '/activity/stats',
} as const

/**
 * Appends a repeated query parameter to the path.
 *
 * `QueryParams` is a flat record, so the client can only emit each key once —
 * which is exactly the wrong shape for `tag_ids`, where the backend parses a
 * list and would read `?tag_ids=a,b` as one unparseable id. The client's
 * `query` option is left empty in that case and the repeats go on the path,
 * which `joinUrl` passes through untouched.
 */
function withRepeatedParam(path: string, key: string, values: readonly string[]): string {
  if (values.length === 0) return path
  const suffix = values.map((value) => `${key}=${encodeURIComponent(value)}`).join('&')
  return `${path}${path.includes('?') ? '&' : '?'}${suffix}`
}

/* ---------------------------------------------------------------- projects */

export interface ProjectCreatePayload {
  name: string
  description?: string | null
  priority?: ProjectPriority
  start_date?: DateOnlyString | null
  target_date?: DateOnlyString | null
}

export type ProjectUpdatePayload = Partial<ProjectCreatePayload>

export function fetchProjects(
  params: ProjectListParams = {},
  signal?: AbortSignal,
): Promise<Paginated<Project>> {
  return apiClient.get<Paginated<Project>>(WORK_ENDPOINTS.projects, {
    query: queryFrom(params as Record<string, unknown>),
    signal,
  })
}

export function createProject(payload: ProjectCreatePayload): Promise<Project> {
  return apiClient.post<Project>(WORK_ENDPOINTS.projects, payload)
}

export function fetchProject(id: UUIDString, signal?: AbortSignal): Promise<Project> {
  return apiClient.get<Project>(WORK_ENDPOINTS.project(id), { signal })
}

export function updateProject(
  id: UUIDString,
  payload: ProjectUpdatePayload,
): Promise<Project> {
  return apiClient.patch<Project>(WORK_ENDPOINTS.project(id), payload)
}

export function deleteProject(id: UUIDString): Promise<void> {
  return apiClient.delete<void>(WORK_ENDPOINTS.project(id), { parse: 'none' })
}

export function completeProject(id: UUIDString): Promise<Project> {
  return apiClient.post<Project>(WORK_ENDPOINTS.projectComplete(id), undefined, { parse: 'json' })
}

export function archiveProject(id: UUIDString): Promise<Project> {
  return apiClient.post<Project>(WORK_ENDPOINTS.projectArchive(id), undefined, { parse: 'json' })
}

export function restoreProject(id: UUIDString): Promise<Project> {
  return apiClient.post<Project>(WORK_ENDPOINTS.projectRestore(id), undefined, { parse: 'json' })
}

export function fetchProjectSummary(
  id: UUIDString,
  signal?: AbortSignal,
): Promise<ProjectSummary> {
  return apiClient.get<ProjectSummary>(WORK_ENDPOINTS.projectSummary(id), { signal })
}

export function fetchProjectActivity(
  id: UUIDString,
  params: { limit?: number; offset?: number; event_type?: string } = {},
  signal?: AbortSignal,
): Promise<Paginated<ActivityEvent>> {
  return apiClient.get<Paginated<ActivityEvent>>(WORK_ENDPOINTS.projectActivity(id), {
    query: queryFrom(params as Record<string, unknown>),
    signal,
  })
}

export function fetchProjectTasks(
  id: UUIDString,
  params: { limit?: number; offset?: number; status?: TaskStatus } = {},
  signal?: AbortSignal,
): Promise<Paginated<Task>> {
  return apiClient.get<Paginated<Task>>(WORK_ENDPOINTS.projectTasks(id), {
    query: queryFrom(params as Record<string, unknown>),
    signal,
  })
}

/* ------------------------------------------------------------------- tasks */

export interface TaskCreatePayload {
  project_id: UUIDString
  title: string
  description?: string | null
  priority?: TaskPriority
  /** The only route that sets a status at all; later changes are transitions. */
  status?: TaskStatus
  start_date?: DateOnlyString | null
  due_date?: DateOnlyString | null
  estimated_minutes?: number | null
  parent_id?: UUIDString | null
}

export type TaskUpdatePayload = Partial<Omit<TaskCreatePayload, 'project_id'>> & {
  project_id?: UUIDString
}

export function fetchTasks(
  params: TaskListParams = {},
  signal?: AbortSignal,
): Promise<Paginated<Task>> {
  const { tag_ids: tagIds, ...rest } = params
  return apiClient.get<Paginated<Task>>(
    withRepeatedParam(WORK_ENDPOINTS.tasks, 'tag_ids', tagIds ?? []),
    { query: queryFrom(rest as Record<string, unknown>), signal },
  )
}

export function createTask(payload: TaskCreatePayload): Promise<Task> {
  return apiClient.post<Task>(WORK_ENDPOINTS.tasks, payload)
}

export function updateTask(id: UUIDString, payload: TaskUpdatePayload): Promise<Task> {
  return apiClient.patch<Task>(WORK_ENDPOINTS.task(id), payload)
}

export function deleteTask(id: UUIDString): Promise<void> {
  return apiClient.delete<void>(WORK_ENDPOINTS.task(id), { parse: 'none' })
}

export function completeTask(id: UUIDString): Promise<Task> {
  return apiClient.post<Task>(WORK_ENDPOINTS.taskComplete(id), undefined, { parse: 'json' })
}

/**
 * The only route into `in_progress`, and therefore the only route that makes
 * completing a task reachable at all: `TaskService._LEGAL_TRANSITIONS` allows
 * `completed` from `in_progress` but not from `todo`.
 */
export function startTask(id: UUIDString): Promise<Task> {
  return apiClient.post<Task>(WORK_ENDPOINTS.taskStart(id), undefined, { parse: 'json' })
}

export function reopenTask(id: UUIDString): Promise<Task> {
  return apiClient.post<Task>(WORK_ENDPOINTS.taskReopen(id), undefined, { parse: 'json' })
}

/** `cancelled` is terminal, and cancelling stamps no `completed_at`. */
export function cancelTask(id: UUIDString): Promise<Task> {
  return apiClient.post<Task>(WORK_ENDPOINTS.taskCancel(id), undefined, { parse: 'json' })
}

/** The body is optional; the route pins the resulting status to `blocked`. */
export function blockTask(id: UUIDString, note?: string): Promise<Task> {
  return apiClient.post<Task>(
    WORK_ENDPOINTS.taskBlock(id),
    note ? { note } : undefined,
    { parse: 'json' },
  )
}

/** Returns the **blocker**, not the task that was blocked. */
export function addTaskDependency(
  id: UUIDString,
  dependsOnId: UUIDString,
): Promise<TaskSummary> {
  return apiClient.post<TaskSummary>(WORK_ENDPOINTS.taskDependencies(id), undefined, {
    query: { depends_on_id: dependsOnId },
  })
}

/** A replacement, not an addition: an empty list clears the task's tags. */
export function setTaskTags(id: UUIDString, tagIds: UUIDString[]): Promise<UUIDString[]> {
  return apiClient.put<UUIDString[]>(WORK_ENDPOINTS.taskTags(id), { tag_ids: tagIds })
}

/* -------------------------------------------------------------------- tags */

export function fetchTags(
  params: { limit?: number; offset?: number } = {},
  signal?: AbortSignal,
): Promise<Paginated<WorkTag>> {
  return apiClient.get<Paginated<WorkTag>>(WORK_ENDPOINTS.tags, {
    query: queryFrom(params as Record<string, unknown>),
    signal,
  })
}

export function createTag(name: string): Promise<WorkTag> {
  return apiClient.post<WorkTag>(WORK_ENDPOINTS.tags, { name })
}

/* ---------------------------------------------------------------- activity */

export function fetchActivity(
  params: {
    limit?: number
    offset?: number
    project_id?: UUIDString
    task_id?: UUIDString
    event_type?: string
  } = {},
  signal?: AbortSignal,
): Promise<Paginated<ActivityEvent>> {
  return apiClient.get<Paginated<ActivityEvent>>(WORK_ENDPOINTS.activity, {
    query: queryFrom(params as Record<string, unknown>),
    signal,
  })
}

export function fetchActivityStats(signal?: AbortSignal): Promise<ActivityStats> {
  return apiClient.get<ActivityStats>(WORK_ENDPOINTS.activityStats, { signal })
}

/* ------------------------------------------------------------------- types */

export type { ProjectStatus, TaskPriority, TaskStatus }