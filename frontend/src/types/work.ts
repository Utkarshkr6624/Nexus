/**
 * Wire types for the Phase 3 work surface: projects, tasks, tags and activity.
 *
 * Mirrors `backend/app/schemas/{project,task,tag,activity}.py`. Date-only
 * columns (`start_date`, `due_date`, `target_date`) arrive as `YYYY-MM-DD`
 * strings, never timestamps, and `is_overdue` is a backend-computed flag that is
 * read here and never re-derived — see the note on {@link Task}.
 */
import {
  AlertOctagon,
  Archive,
  Ban,
  CalendarClock,
  CheckCircle2,
  CircleDashed,
  CircleDot,
  CircleX,
  Flag,
  PauseCircle,
  Pencil,
  Play,
  Plus,
  RotateCcw,
  Trash2,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import type { ISODateTimeString, UUIDString } from './api'
import type { Paginated, PaginationParams } from './pagination'

// Re-exported so a consumer of the work vocabulary needs one import, not two.
export type { ISODateTimeString, Paginated, UUIDString }

/** `YYYY-MM-DD`. Date-only on the wire — no time component, no timezone. */
export type DateOnlyString = string

export type ProjectStatus = 'planned' | 'active' | 'on_hold' | 'completed' | 'archived'

export type ProjectPriority = 'low' | 'medium' | 'high' | 'critical'

export type TaskStatus = 'todo' | 'in_progress' | 'blocked' | 'completed' | 'cancelled'

export type TaskPriority = 'low' | 'medium' | 'high' | 'critical'

/** One of the `ActivityEvent` values in `backend/app/models/enums.py`. */
export type WorkEventType =
  | 'project_created'
  | 'project_updated'
  | 'project_completed'
  | 'project_archived'
  | 'project_deleted'
  | 'project_restored'
  | 'task_created'
  | 'task_updated'
  | 'task_started'
  | 'task_completed'
  | 'task_reopened'
  | 'task_blocked'
  | 'task_priority_changed'
  | 'task_due_date_changed'
  | 'task_deleted'
  | 'task_scheduled'

/**
 * Semantic tone. Components map these onto the existing design tokens
 * (`muted`, `primary`, `success`, `warning`, `destructive`) — never raw hex.
 */
export type StatusTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'

/**
 * How a status is presented. `tone` names an existing token family rather than
 * a colour, so a badge never carries a raw hex value.
 */
export interface StatusMeta {
  label: string
  icon: LucideIcon
  tone: StatusTone
  description: string
}

export type SortOrder = 'asc' | 'desc'

export const TASK_STATUS_META: Record<TaskStatus, StatusMeta> = {
  todo: {
    label: 'To do',
    icon: CircleDashed,
    tone: 'neutral',
    description: 'Not started.',
  },
  in_progress: {
    label: 'In progress',
    icon: CircleDot,
    tone: 'info',
    description: 'Started and under way.',
  },
  blocked: {
    label: 'Blocked',
    icon: Ban,
    tone: 'warning',
    description: 'Waiting on something else.',
  },
  completed: {
    label: 'Completed',
    icon: CheckCircle2,
    tone: 'success',
    description: 'Finished.',
  },
  cancelled: {
    label: 'Cancelled',
    icon: CircleX,
    tone: 'danger',
    description: 'Deliberately dropped. Not the same as finished.',
  },
}

export const PROJECT_STATUS_META: Record<ProjectStatus, StatusMeta> = {
  planned: {
    label: 'Planned',
    icon: CircleDashed,
    tone: 'neutral',
    description: 'Scoped but not started.',
  },
  active: {
    label: 'Active',
    icon: CircleDot,
    tone: 'info',
    description: 'Under way.',
  },
  on_hold: {
    label: 'On hold',
    icon: PauseCircle,
    tone: 'warning',
    description: 'Paused; resumes to active.',
  },
  completed: {
    label: 'Completed',
    icon: CheckCircle2,
    tone: 'success',
    description: 'Finished.',
  },
  archived: {
    label: 'Archived',
    icon: Archive,
    tone: 'neutral',
    description: 'Terminal. Restoring returns it to active.',
  },
}

export const PRIORITY_META: Record<TaskPriority, StatusMeta> = {
  low: {
    label: 'Low',
    icon: Flag,
    tone: 'neutral',
    description: 'No rush.',
  },
  medium: {
    label: 'Medium',
    icon: Flag,
    tone: 'info',
    description: 'The default grade.',
  },
  high: {
    label: 'High',
    icon: Flag,
    tone: 'warning',
    description: 'Competes for attention.',
  },
  critical: {
    label: 'Critical',
    icon: AlertOctagon,
    tone: 'danger',
    description: 'Ahead of everything else.',
  },
}

/**
 * How each activity event is presented. The tone is the only thing the feed
 * colours by, so a new event is readable the moment the backend can emit it.
 */
export const WORK_EVENT_META: Record<WorkEventType, StatusMeta> = {
  project_created: { label: 'Project created', icon: Plus, tone: 'info', description: 'New project.' },
  project_updated: { label: 'Project updated', icon: Pencil, tone: 'neutral', description: 'Details edited.' },
  project_completed: { label: 'Project completed', icon: CheckCircle2, tone: 'success', description: 'Finished.' },
  project_archived: { label: 'Project archived', icon: Archive, tone: 'neutral', description: 'Archived.' },
  project_restored: { label: 'Project restored', icon: RotateCcw, tone: 'info', description: 'Back in the working set.' },
  project_deleted: { label: 'Project deleted', icon: Trash2, tone: 'danger', description: 'Removed.' },
  task_created: { label: 'Task created', icon: Plus, tone: 'info', description: 'New task.' },
  task_updated: { label: 'Task updated', icon: Pencil, tone: 'neutral', description: 'Details edited.' },
  task_started: { label: 'Task started', icon: Play, tone: 'info', description: 'Moved into progress.' },
  task_completed: { label: 'Task completed', icon: CheckCircle2, tone: 'success', description: 'Finished.' },
  task_reopened: { label: 'Task reopened', icon: RotateCcw, tone: 'info', description: 'Taken back.' },
  task_blocked: { label: 'Task blocked', icon: Ban, tone: 'warning', description: 'Waiting on something.' },
  task_priority_changed: { label: 'Priority changed', icon: Flag, tone: 'warning', description: 'Re-triaged.' },
  task_due_date_changed: { label: 'Due date changed', icon: CalendarClock, tone: 'neutral', description: 'Rescheduled.' },
  task_deleted: { label: 'Task deleted', icon: Trash2, tone: 'danger', description: 'Removed.' },
  task_scheduled: { label: 'Task scheduled', icon: CalendarClock, tone: 'info', description: 'Given a date.' },
}

export const TASK_STATUS_ORDER: readonly TaskStatus[] = [
  'todo',
  'in_progress',
  'blocked',
  'completed',
  'cancelled',
]

export const TASK_STATUSES = Object.keys(TASK_STATUS_META) as TaskStatus[]
export const PROJECT_STATUSES = Object.keys(PROJECT_STATUS_META) as ProjectStatus[]
export const TASK_PRIORITIES = Object.keys(PRIORITY_META) as TaskPriority[]

export interface Project {
  id: UUIDString
  owner_id: UUIDString
  name: string
  description: string | null
  status: ProjectStatus
  priority: ProjectPriority
  start_date: DateOnlyString | null
  target_date: DateOnlyString | null
  completed_at: ISODateTimeString | null
  archived_at: ISODateTimeString | null
  created_at: ISODateTimeString
  updated_at: ISODateTimeString
}

export interface ProjectSummary {
  id: UUIDString
  name: string
  status: ProjectStatus
  priority: ProjectPriority
  target_date: DateOnlyString | null
  task_count: number
  completed_task_count: number
  /** Derived backend-side from the two counts above. Never recomputed here. */
  progress_percent: number
}

export interface ProjectStats {
  total: number
  active: number
  completed: number
  planned: number
  on_hold: number
  archived: number
}

export interface Task {
  id: UUIDString
  project_id: UUIDString
  owner_id: UUIDString
  parent_id: UUIDString | null
  title: string
  description: string | null
  status: TaskStatus
  priority: TaskPriority
  start_date: DateOnlyString | null
  due_date: DateOnlyString | null
  estimated_minutes: number | null
  actual_minutes: number
  completed_at: ISODateTimeString | null
  /** Board ordering key; lower sorts first. */
  position: number
  created_at: ISODateTimeString
  updated_at: ISODateTimeString
  tag_ids: UUIDString[]
  /**
   * Backend-computed: past due and still open. Date-based, so a task due today
   * is not overdue. Clients mirror this flag and never re-derive it — a second
   * definition would eventually disagree with the one the list endpoint used.
   */
  is_overdue: boolean
  /** True when at least one prerequisite task is unfinished. */
  has_blocked_dependencies: boolean
}

/** The lean shape a board column or dense list row renders. */
export interface TaskSummary {
  id: UUIDString
  project_id: UUIDString
  parent_id: UUIDString | null
  title: string
  status: TaskStatus
  priority: TaskPriority
  start_date: DateOnlyString | null
  due_date: DateOnlyString | null
  position: number
  completed_at: ISODateTimeString | null
  tag_ids: UUIDString[]
  is_overdue: boolean
}

export interface TaskStats {
  total: number
  todo: number
  in_progress: number
  blocked: number
  completed: number
  cancelled: number
  /** Counted across the open buckets, not folded into one of them. */
  overdue: number
}

export interface WorkTag {
  id: UUIDString
  name: string
  created_at: ISODateTimeString
  task_count: number
  project_count: number
}

export interface ActivityEvent {
  id: UUIDString
  user_id: UUIDString | null
  project_id: UUIDString | null
  task_id: UUIDString | null
  event_type: WorkEventType
  /** Non-sensitive event context; never a credential. */
  metadata: Record<string, unknown>
  created_at: ISODateTimeString
}

export interface ActivityStats {
  projects: ProjectStats
  tasks: TaskStats
}

export interface ProjectListParams extends PaginationParams {
  status?: ProjectStatus
  /**
   * Accepted for symmetry with {@link TaskListParams} but **not implemented by
   * `GET /projects`** — the route declares no priority filter, and FastAPI
   * ignores an undeclared query param rather than 422-ing it. Do not offer a
   * priority filter on the projects list until the backend adds one.
   */
  priority?: ProjectPriority
  search?: string
  sort?: string
  order?: SortOrder
}

export interface TaskListParams extends PaginationParams {
  project_id?: UUIDString
  /**
   * Declared here for the board and the subtask panel, but `GET /tasks` does
   * **not** implement it (`TaskService.list` has no such parameter and the
   * route therefore does not declare it); the value is ignored. Use
   * `GET /tasks/{id}/subtasks` to list a task's children.
   */
  parent_id?: UUIDString
  status?: TaskStatus
  priority?: TaskPriority
  due_before?: DateOnlyString
  due_after?: DateOnlyString
  search?: string
  /** Repeatable. A task must carry *every* tag listed, not any of them. */
  tag_ids?: UUIDString[]
  sort?: string
  order?: SortOrder
}

/** The only filters `GET /projects/{id}/tasks` accepts besides paging. */
export interface ProjectTaskListParams extends PaginationParams {
  status?: TaskStatus
}

export interface TagListParams extends PaginationParams {
  search?: string
}

export interface ActivityListParams extends PaginationParams {
  project_id?: UUIDString
  task_id?: UUIDString
  event_type?: WorkEventType
}

/**
 * Filters without pagination — what a filter bar owns and what the URL carries.
 * Separated from {@link TaskListParams} so changing a filter cannot silently
 * reset the page to an offset that no longer exists.
 */
export type TaskFilterValue = Omit<TaskListParams, 'limit' | 'offset'>

/**
 * Sort keys the task list endpoint allowlists. Anything else is a 422, so the
 * client resolves the name against this set rather than sending user input
 * straight through.
 */
export const TASK_SORT_KEYS = [
  'created_at',
  'updated_at',
  'title',
  'status',
  'priority',
  'start_date',
  'due_date',
  'position',
  'completed_at',
] as const

export type TaskSortKey = (typeof TASK_SORT_KEYS)[number]

export const TASK_SORT_LABELS: Record<TaskSortKey, string> = {
  created_at: 'Created',
  updated_at: 'Last updated',
  title: 'Title',
  status: 'Status',
  priority: 'Priority',
  start_date: 'Start date',
  due_date: 'Due date',
  position: 'Board order',
  completed_at: 'Completed',
}

/** Projects accept a different, shorter set. */
export const PROJECT_SORT_KEYS = [
  'created_at',
  'updated_at',
  'name',
  'status',
  'priority',
  'start_date',
  'target_date',
] as const

export type ProjectSortKey = (typeof PROJECT_SORT_KEYS)[number]

/** `limit` is a rejection above 100, not a silent truncation. */
export const MAX_PAGE_SIZE = 100

// -- List envelopes ---------------------------------------------------------

/** Every Phase 3 list endpoint answers `Page[T]`, never a bare array. */
export type ProjectPage = Paginated<Project>
export type TaskPage = Paginated<Task>
export type TagPage = Paginated<WorkTag>
export type ActivityPage = Paginated<ActivityEvent>

// -- Request payloads -------------------------------------------------------

export interface ProjectCreatePayload {
  name: string
  description?: string | null
  priority?: ProjectPriority
  start_date?: DateOnlyString | null
  target_date?: DateOnlyString | null
}

/**
 * `ProjectUpdate` is `extra="forbid"` and has no `status`: sending one is a 422
 * naming the field rather than a silent drop. Clearing an optional value is
 * `null`; omitting the key leaves it untouched.
 */
export type ProjectUpdatePayload = Partial<ProjectCreatePayload>

export interface TaskCreatePayload {
  project_id: UUIDString
  title: string
  description?: string | null
  priority?: TaskPriority
  /** The only place a task's initial status is settable. */
  status?: TaskStatus
  start_date?: DateOnlyString | null
  due_date?: DateOnlyString | null
  estimated_minutes?: number | null
  parent_id?: UUIDString | null
}

/**
 * `TaskUpdate` is `extra="forbid"` and carries neither `status` nor
 * `position`: every transition has its own endpoint.
 */
export type TaskUpdatePayload = Partial<Omit<TaskCreatePayload, 'status'>>

/** Body of `POST /tasks/{id}/block`; the route pins `status` to `blocked`. */
export interface TaskStatusChangePayload {
  note?: string | null
}

/** A replacement, not an addition: `[]` clears the object's tags. */
export interface TagAssignmentPayload {
  tag_ids: UUIDString[]
}