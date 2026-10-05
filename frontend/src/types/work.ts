/**
 * Wire types for the work surface: projects, tasks, tags and activity.
 *
 * Mirrors `backend/app/schemas/{project,task,tag,activity}.py`. Date-only
 * columns (`start_date`, `due_date`, `target_date`) arrive as `YYYY-MM-DD`
 * strings, never timestamps, and `is_overdue` is a backend-computed flag that is
 * read here and never re-derived — see the note on {@link Task}.
 *
 * The one place this module reaches past Phase 3 is {@link WorkEventType}: the
 * activity feed is written to by every phase from the planner onwards, so its
 * vocabulary is the whole `ActivityEvent` enum rather than the sixteen members
 * this surface originally shipped with.
 */
import {
  AlertOctagon,
  AlertTriangle,
  Archive,
  Award,
  Ban,
  BookMarked,
  Bookmark,
  Briefcase,
  CalendarClock,
  CalendarX,
  CheckCircle2,
  CircleDashed,
  CircleDot,
  CircleX,
  Eye,
  FileDiff,
  FileText,
  Flag,
  FolderGit2,
  GitBranch,
  GitCommitVertical,
  Globe,
  GraduationCap,
  History,
  Lightbulb,
  Link2,
  PauseCircle,
  Pencil,
  Play,
  Plus,
  RotateCcw,
  ScanLine,
  Sparkles,
  Target,
  Timer,
  Trash2,
  Unlink,
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

/**
 * Every `ActivityEvent` value in `backend/app/models/enums.py`, in the enum's
 * own declaration order.
 *
 * **All 63 of them, not the Phase 3 sixteen.** This union used to list only the
 * project and task members, while its own docstring claimed to mirror the enum,
 * and the other 47 values — planner, knowledge, risk, developer, learning — were
 * therefore values a stored `event_type` could hold but this type could not name.
 * The enum is the contract; a subset of it is a type that quietly disagrees with
 * the server, and the disagreement surfaces as a lookup miss on the one screen
 * that renders all of them.
 *
 * The column itself is unconstrained free text (`activity_events.event_type` is
 * a plain `String` with no CHECK), so this list cannot be exhaustive at runtime
 * either. {@link workEventMeta} is what callers resolve through; it answers for
 * a value this union does not name.
 */
export type WorkEventType =
  | 'project_created'
  | 'project_updated'
  | 'project_completed'
  | 'project_archived'
  | 'project_restored'
  | 'project_deleted'
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
  | 'work_session_started'
  | 'work_session_completed'
  | 'calendar_event_created'
  | 'calendar_event_updated'
  | 'calendar_event_deleted'
  | 'task_rescheduled'
  | 'planner_suggestion_accepted'
  | 'planner_suggestion_rejected'
  | 'note_created'
  | 'note_updated'
  | 'note_archived'
  | 'note_published'
  | 'note_restored'
  | 'note_revision_restored'
  | 'concept_created'
  | 'resource_created'
  | 'bookmark_created'
  | 'knowledge_link_created'
  | 'knowledge_link_removed'
  | 'risk_detected'
  | 'risk_updated'
  | 'risk_resolved'
  | 'risk_acknowledged'
  | 'risk_dismissed'
  | 'recommendation_created'
  | 'recommendation_viewed'
  | 'recommendation_accepted'
  | 'recommendation_rejected'
  | 'recommendation_completed'
  | 'repository_registered'
  | 'repository_updated'
  | 'repository_scanned'
  | 'repository_removed'
  | 'commit_detected'
  | 'branch_created'
  | 'branch_changed'
  | 'file_activity_detected'
  | 'learning_goal_created'
  | 'learning_goal_updated'
  | 'learning_goal_completed'
  | 'learning_session_recorded'
  | 'skill_created'
  | 'skill_updated'
  | 'skill_activity_recorded'
  | 'career_profile_updated'
  | 'career_evidence_added'
  | 'career_evidence_updated'

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
 *
 * **`Record<WorkEventType, StatusMeta>` is total and has to stay that way.**
 * `WorkEventType` and this map were both built from `ActivityEvent`, so the key
 * set is checked against the union at compile time and a member added to one
 * without the other fails `tsc` rather than failing at runtime in front of a
 * user. When a new `ActivityEvent` member lands, this map is where it gets its
 * label — the alternative is a row that cannot name itself, which is a far
 * worse outcome than a missing entry caught by the compiler.
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
  work_session_started: { label: 'Session started', icon: Timer, tone: 'info', description: 'Clock running.' },
  // The old copy read "Time recorded.", which is a claim about a duration on an
  // event that fires for a timer stopped inside the same second — 0 minutes. The
  // backend does record `actual_minutes` on the event, so the feed reads it and
  // says what it measured; this description must not assert a figure the map
  // itself cannot see. See `eventDetail` in `pages/dashboard-page.tsx`.
  work_session_completed: { label: 'Session completed', icon: CheckCircle2, tone: 'success', description: 'Timer stopped.' },
  calendar_event_created: { label: 'Event booked', icon: CalendarClock, tone: 'info', description: 'Time reserved.' },
  calendar_event_updated: { label: 'Event changed', icon: Pencil, tone: 'neutral', description: 'Details edited.' },
  calendar_event_deleted: { label: 'Event removed', icon: CalendarX, tone: 'neutral', description: 'The block was released.' },
  task_rescheduled: { label: 'Task rescheduled', icon: CalendarClock, tone: 'warning', description: 'A date that was already set moved.' },
  planner_suggestion_accepted: { label: 'Suggestion accepted', icon: CheckCircle2, tone: 'success', description: 'Planner proposal taken up.' },
  planner_suggestion_rejected: { label: 'Suggestion rejected', icon: CircleX, tone: 'neutral', description: 'Planner proposal declined.' },
  note_created: { label: 'Note created', icon: FileText, tone: 'info', description: 'New note.' },
  note_updated: { label: 'Note updated', icon: Pencil, tone: 'neutral', description: 'Details edited.' },
  note_archived: { label: 'Note archived', icon: Archive, tone: 'neutral', description: 'Set aside.' },
  note_published: { label: 'Note published', icon: Globe, tone: 'success', description: 'Asserted as shared.' },
  note_restored: { label: 'Note restored', icon: RotateCcw, tone: 'info', description: 'Out of the archive.' },
  note_revision_restored: { label: 'Revision restored', icon: History, tone: 'neutral', description: 'An earlier version is back.' },
  concept_created: { label: 'Concept created', icon: Lightbulb, tone: 'info', description: 'New concept.' },
  resource_created: { label: 'Resource created', icon: BookMarked, tone: 'info', description: 'Something filed.' },
  bookmark_created: { label: 'Bookmark created', icon: Bookmark, tone: 'info', description: 'Saved for later.' },
  knowledge_link_created: { label: 'Link created', icon: Link2, tone: 'info', description: 'Two notes joined.' },
  knowledge_link_removed: { label: 'Link removed', icon: Unlink, tone: 'neutral', description: 'The join was undone.' },
  risk_detected: { label: 'Risk detected', icon: AlertTriangle, tone: 'warning', description: 'Flagged by the engine.' },
  risk_updated: { label: 'Risk updated', icon: Pencil, tone: 'neutral', description: 'Details edited.' },
  risk_resolved: { label: 'Risk resolved', icon: CheckCircle2, tone: 'success', description: 'The condition cleared.' },
  risk_acknowledged: { label: 'Risk acknowledged', icon: Eye, tone: 'info', description: 'Seen and noted.' },
  risk_dismissed: { label: 'Risk dismissed', icon: Ban, tone: 'neutral', description: 'Set aside.' },
  recommendation_created: { label: 'Recommendation', icon: Sparkles, tone: 'info', description: 'Suggested.' },
  recommendation_viewed: { label: 'Recommendation opened', icon: Eye, tone: 'neutral', description: 'Read.' },
  recommendation_accepted: { label: 'Recommendation accepted', icon: CheckCircle2, tone: 'success', description: 'Taken on.' },
  recommendation_rejected: { label: 'Recommendation rejected', icon: CircleX, tone: 'danger', description: 'Turned down.' },
  recommendation_completed: { label: 'Recommendation completed', icon: CheckCircle2, tone: 'success', description: 'Carried out.' },
  repository_registered: { label: 'Repository registered', icon: FolderGit2, tone: 'info', description: 'Added to the workspace.' },
  repository_updated: { label: 'Repository updated', icon: Pencil, tone: 'neutral', description: 'Details edited.' },
  repository_scanned: { label: 'Repository scanned', icon: ScanLine, tone: 'info', description: 'History read.' },
  repository_removed: { label: 'Repository removed', icon: Trash2, tone: 'danger', description: 'Unregistered.' },
  commit_detected: { label: 'Commit detected', icon: GitCommitVertical, tone: 'info', description: 'A commit the last scan had not seen.' },
  branch_created: { label: 'Branch created', icon: GitBranch, tone: 'success', description: 'New branch.' },
  branch_changed: { label: 'Branch changed', icon: GitBranch, tone: 'neutral', description: 'Its head moved.' },
  file_activity_detected: { label: 'Uncommitted changes', icon: FileDiff, tone: 'neutral', description: 'Changes in the working tree.' },
  learning_goal_created: { label: 'Learning goal created', icon: Target, tone: 'info', description: 'New goal.' },
  learning_goal_updated: { label: 'Learning goal updated', icon: Pencil, tone: 'neutral', description: 'Details edited.' },
  learning_goal_completed: { label: 'Learning goal completed', icon: CheckCircle2, tone: 'success', description: 'Reached.' },
  learning_session_recorded: { label: 'Study session recorded', icon: GraduationCap, tone: 'success', description: 'Learning time logged.' },
  skill_created: { label: 'Skill added', icon: Award, tone: 'info', description: 'Now tracked.' },
  skill_updated: { label: 'Skill updated', icon: Pencil, tone: 'neutral', description: 'Details edited.' },
  skill_activity_recorded: { label: 'Skill activity recorded', icon: Sparkles, tone: 'neutral', description: 'Practice logged.' },
  career_profile_updated: { label: 'Career profile updated', icon: Briefcase, tone: 'neutral', description: 'Profile edited.' },
  career_evidence_added: { label: 'Evidence added', icon: Plus, tone: 'success', description: 'Added by you.' },
  career_evidence_updated: { label: 'Evidence updated', icon: Pencil, tone: 'neutral', description: 'Amended by you.' },
}

/**
 * What an event type the map does not carry is presented as.
 *
 * `activity_events.event_type` is a plain `String` column with no CHECK
 * constraint, so totality here is a compile-time property and not a runtime one:
 * a value added by a later backend release, or one written by an older one,
 * reaches the feed unchanged. That is why this record exists rather than a
 * lookup that assumes it can never miss.
 */
const UNKNOWN_EVENT_META: StatusMeta = {
  label: 'Unrecognised event',
  icon: CircleDashed,
  tone: 'neutral',
  description: 'Recorded, but not by a version this build knows.',
}

/**
 * `future_thing_happened` reads as `Future thing happened`: separators become
 * spaces and the sentence is capitalised to match the labels above. The value is
 * a row that exists, so it is never dropped — only its meaning is unavailable,
 * and a machine spelling is still better than no row at all. A value carrying no
 * letters at all falls back to the generic label rather than an empty line.
 */
function humaniseEventType(eventType: string): string {
  if (typeof eventType !== 'string') return UNKNOWN_EVENT_META.label
  const words = eventType.split(/[\s_-]+/).filter(Boolean)
  const [first, ...rest] = words
  if (first === undefined) return UNKNOWN_EVENT_META.label
  return [first.charAt(0).toUpperCase() + first.slice(1), ...rest].join(' ')
}

/**
 * The presentation for an `event_type`, for a value this build may not know.
 *
 * Indexing {@link WORK_EVENT_META} directly and then reading `.icon` off the
 * result is what took `/dashboard` down: the map held 16 of the enum's 63
 * members, one unknown event produced `undefined`, and the first property access
 * on it threw inside the render that owns every panel on the page. Every
 * consumer resolves through here instead, so a value outside the map costs a
 * generic row rather than the route.
 */
export function workEventMeta(eventType: string): StatusMeta {
  const known: StatusMeta | undefined = WORK_EVENT_META[eventType as WorkEventType]
  if (known) return known
  return { ...UNKNOWN_EVENT_META, label: humaniseEventType(eventType) }
}

export const TASK_STATUS_ORDER: readonly TaskStatus[] = [
  'todo',
  'in_progress',
  'blocked',
  'completed',
  'cancelled',
]

export const TASK_STATUSES = Object.keys(TASK_STATUS_META) as TaskStatus[]
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
  /**
   * The union is what the backend *should* send and the only thing this client
   * can name. The column carries no CHECK, so anything can come back — read
   * this through {@link workEventMeta}, which is total, rather than indexing
   * {@link WORK_EVENT_META} and assuming the lookup lands.
   */
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