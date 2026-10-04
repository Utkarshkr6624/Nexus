/**
 * The command palette's "do something" half.
 *
 * Every entry here is either a **real request** or a **local command**. There is
 * no third kind: an action that needed a record it could not honestly name would
 * be a fake, and a palette that invents a row is worse than one with no such
 * action. The five creating actions therefore post to the same endpoints the
 * forms in each module post to, and a refusal is reported as a refusal — the
 * response is the only thing that decides whether this says "Created".
 *
 * The registry is plain data plus a `submit` function per form. It holds no
 * React state and never runs anything on its own: `submit` is called by
 * `QuickActionForm` when the user presses the button, so a quick action cannot
 * write a row because somebody arrowed past it.
 *
 * Two of the entries need no confirmation at all — `open-assistant` and
 * `open-settings` navigate, and `toggle-theme` flips a local store — and those
 * carry `navigateTo` / `toggleTheme` instead of a `form`.
 */
import type { QueryClient } from '@tanstack/react-query'
import {
  CalendarPlus,
  CheckSquare,
  FileText,
  FolderPlus,
  GraduationCap,
  Moon,
  Settings,
  Sparkles,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { knowledgeKeys } from '@/features/knowledge/hooks'
import { learningKeys } from '@/features/learning/hooks'
import { plannerKeys } from '@/features/planner/hooks'
import { workKeys } from '@/features/work/hooks'
import { createNote } from '@/services/knowledge'
import { createLearningGoal } from '@/services/learning'
import { createCalendarEvent } from '@/services/planner'
import { createProject, createTask } from '@/services/work'
import type { ProjectPriority, TaskPriority } from '@/types/work'

/** Raw form state: every field is a string until `submit` types it. */
export type QuickActionValues = Record<string, string>

export interface QuickActionFieldOption {
  value: string
  label: string
}

interface QuickActionFieldBase {
  label: string
  /** An empty value is refused in the browser, before a pointless request. */
  required: boolean
}

export type QuickActionField =
  | (QuickActionFieldBase & {
      name: string
      type: 'text'
      placeholder?: string
      /** Focused when the form opens, because it is the only always-filled one. */
      autoFocus?: boolean
    })
  | (QuickActionFieldBase & { name: string; type: 'date' | 'time' })
  | (QuickActionFieldBase & {
      name: string
      type: 'select'
      /** A fixed list, or the caller's live projects. */
      options?: readonly QuickActionFieldOption[]
      optionsFrom?: 'projects'
    })

export interface QuickActionSubmitContext {
  /** Supplied so a write can invalidate the family it belongs to. */
  queryClient: QueryClient
}

export interface QuickActionFormSpec {
  fields: readonly QuickActionField[]
  /**
   * Performs the real request and resolves with the label of what was created.
   * Rejecting is how a failure is reported — there is no success path that the
   * caller can reach without a resolved promise.
   */
  submit: (values: QuickActionValues, context: QuickActionSubmitContext) => Promise<string>
}

export type QuickActionId =
  | 'create-task'
  | 'create-project'
  | 'create-note'
  | 'create-goal'
  | 'schedule-time'
  | 'open-assistant'
  | 'toggle-theme'
  | 'open-settings'

export interface QuickActionDefinition {
  id: QuickActionId
  label: string
  /** One line, shown under the label and read by a screen reader. */
  description: string
  icon: LucideIcon
  keywords: readonly string[]
  /** Selection navigates immediately. Mutually exclusive with `form`. */
  navigateTo?: string
  /** Selection flips the local theme store. Mutually exclusive with `form`. */
  toggleTheme?: boolean
  form?: QuickActionFormSpec
}

/* ------------------------------------------------------------ form helpers */

const PRIORITY_OPTIONS: readonly QuickActionFieldOption[] = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
  { value: 'critical', label: 'Critical' },
]

function trimmed(values: QuickActionValues, name: string): string {
  return (values[name] ?? '').trim()
}

/**
 * A priority field left blank is omitted rather than sent as a guess.
 *
 * The backend defaults it server-side, and pinning one here would make "the
 * medium one I typed" indistinguishable from "the medium one I never chose".
 */
function priorityOf(values: QuickActionValues): TaskPriority | undefined {
  const value = trimmed(values, 'priority')
  return value ? (value as TaskPriority) : undefined
}

/** Same rule for the optional dates: an absent value sends no key at all. */
function dateOf(values: QuickActionValues, name: string): string | undefined {
  return trimmed(values, name) || undefined
}

/**
 * Combines a local wall-clock date and time into the instant the API takes.
 *
 * `YYYY-MM-DDTHH:mm` with no offset is read as **local** time by the platform,
 * which is what a person typing "09:00" means; `toISOString()` then states that
 * instant in UTC, which is what the wire carries.
 */
function localInstant(date: string, time: string): string {
  return new Date(`${date}T${time}`).toISOString()
}

/* ---------------------------------------------------------------- registry */

export const QUICK_ACTIONS: readonly QuickActionDefinition[] = [
  {
    id: 'create-task',
    label: 'New task',
    description: 'Create a task in a project, with an optional priority and due date.',
    icon: CheckSquare,
    keywords: ['task', 'todo', 'add', 'create', 'new', 'checklist'],
    form: {
      fields: [
        {
          name: 'title',
          type: 'text',
          label: 'Task title',
          placeholder: 'Rotate the staging credentials',
          required: true,
          autoFocus: true,
        },
        {
          // `project_id` is required by the API, so the picker is not optional
          // here: there is no honest task to create without one.
          name: 'project',
          type: 'select',
          label: 'Project',
          required: true,
          optionsFrom: 'projects',
        },
        {
          name: 'priority',
          type: 'select',
          label: 'Priority',
          required: false,
          options: PRIORITY_OPTIONS,
        },
        { name: 'due_date', type: 'date', label: 'Due date', required: false },
      ],
      submit: async (values, { queryClient }) => {
        const priority = priorityOf(values)
        const dueDate = dateOf(values, 'due_date')
        const task = await createTask({
          project_id: trimmed(values, 'project'),
          title: trimmed(values, 'title'),
          ...(priority ? { priority } : {}),
          ...(dueDate ? { due_date: dueDate } : {}),
        })
        void queryClient.invalidateQueries({ queryKey: workKeys.all() })
        return task.title
      },
    },
  },
  {
    id: 'create-project',
    label: 'New project',
    description: 'Create a project to hold long-running work.',
    icon: FolderPlus,
    keywords: ['project', 'initiative', 'add', 'create', 'new', 'space'],
    form: {
      fields: [
        {
          name: 'title',
          type: 'text',
          label: 'Project name',
          placeholder: 'Nexus migration',
          required: true,
          autoFocus: true,
        },
        {
          name: 'priority',
          type: 'select',
          label: 'Priority',
          required: false,
          options: PRIORITY_OPTIONS,
        },
        { name: 'target_date', type: 'date', label: 'Target date', required: false },
      ],
      submit: async (values, { queryClient }) => {
        const priority = priorityOf(values)
        const targetDate = dateOf(values, 'target_date')
        const project = await createProject({
          // The API calls it `name`; the palette calls it a title everywhere.
          name: trimmed(values, 'title'),
          ...(priority ? { priority: priority as ProjectPriority } : {}),
          ...(targetDate ? { target_date: targetDate } : {}),
        })
        void queryClient.invalidateQueries({ queryKey: workKeys.all() })
        return project.name
      },
    },
  },
  {
    id: 'create-note',
    label: 'New note',
    description: 'Add a note to the knowledge base.',
    icon: FileText,
    keywords: ['note', 'knowledge', 'write', 'add', 'create', 'new', 'memo'],
    form: {
      fields: [
        {
          name: 'title',
          type: 'text',
          label: 'Note title',
          placeholder: 'What the migration taught us',
          required: true,
          autoFocus: true,
        },
        {
          name: 'content',
          type: 'text',
          label: 'First lines (optional)',
          required: false,
        },
      ],
      submit: async (values, { queryClient }) => {
        const content = trimmed(values, 'content')
        const note = await createNote({
          title: trimmed(values, 'title'),
          ...(content ? { content } : {}),
        })
        void queryClient.invalidateQueries({ queryKey: knowledgeKeys.all() })
        return note.title
      },
    },
  },
  {
    id: 'create-goal',
    label: 'New learning goal',
    description: 'Record what you are trying to learn and by when.',
    icon: GraduationCap,
    keywords: ['goal', 'learning', 'study', 'course', 'add', 'create', 'new'],
    form: {
      fields: [
        {
          name: 'title',
          type: 'text',
          label: 'Goal',
          placeholder: 'Ship a Postgres migration without downtime',
          required: true,
          autoFocus: true,
        },
        {
          name: 'target_topic',
          type: 'text',
          label: 'Topic (optional)',
          required: false,
        },
        { name: 'target_date', type: 'date', label: 'Target date', required: false },
      ],
      submit: async (values, { queryClient }) => {
        const targetTopic = trimmed(values, 'target_topic')
        const targetDate = dateOf(values, 'target_date')
        const goal = await createLearningGoal({
          title: trimmed(values, 'title'),
          ...(targetTopic ? { target_topic: targetTopic } : {}),
          ...(targetDate ? { target_date: targetDate } : {}),
        })
        void queryClient.invalidateQueries({ queryKey: learningKeys.all() })
        return goal.title
      },
    },
  },
  {
    id: 'schedule-time',
    label: 'Schedule time',
    description: 'Put an event on the calendar.',
    icon: CalendarPlus,
    keywords: ['schedule', 'calendar', 'event', 'block', 'meeting', 'book', 'plan'],
    form: {
      fields: [
        {
          name: 'title',
          type: 'text',
          label: 'What is it for',
          placeholder: 'Design review',
          required: true,
          autoFocus: true,
        },
        { name: 'date', type: 'date', label: 'Date', required: true },
        { name: 'starts_at', type: 'time', label: 'From', required: true },
        { name: 'ends_at', type: 'time', label: 'To', required: true },
      ],
      submit: async (values, { queryClient }) => {
        const date = trimmed(values, 'date')
        const event = await createCalendarEvent({
          title: trimmed(values, 'title'),
          starts_at: localInstant(date, trimmed(values, 'starts_at')),
          ends_at: localInstant(date, trimmed(values, 'ends_at')),
        })
        void queryClient.invalidateQueries({ queryKey: plannerKeys.all() })
        return event.title
      },
    },
  },
  {
    id: 'open-assistant',
    label: 'Open the assistant',
    description: 'Ask the intent classifier what it can do.',
    icon: Sparkles,
    keywords: ['assistant', 'ai', 'intent', 'ask', 'classifier'],
    navigateTo: '/assistant',
  },
  {
    id: 'toggle-theme',
    label: 'Toggle light / dark theme',
    description: 'Flip the effective theme; no request is made.',
    icon: Moon,
    keywords: ['theme', 'dark', 'light', 'appearance', 'contrast'],
    toggleTheme: true,
  },
  {
    id: 'open-settings',
    label: 'Open settings',
    description: 'Profile, sessions, password and preferences.',
    icon: Settings,
    keywords: ['settings', 'preferences', 'profile', 'password', 'account'],
    navigateTo: '/settings',
  },
]

export function findQuickAction(id: QuickActionId): QuickActionDefinition | undefined {
  return QUICK_ACTIONS.find((action) => action.id === id)
}

function terms(query: string): string[] {
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
}

/**
 * The same AND-over-substrings rule the destinations use, so one query filters
 * both lists by the same meaning of "matches".
 */
export function matchesQuickAction(action: QuickActionDefinition, query: string): boolean {
  if (!query.trim()) return true
  const haystack = [action.label, action.description, ...action.keywords].join(' ').toLowerCase()
  return terms(query).every((term) => haystack.includes(term))
}
