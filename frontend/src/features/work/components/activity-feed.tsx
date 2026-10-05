import { History } from 'lucide-react'

import { EmptyState } from '@/components/feedback/empty-state'
import { cn } from '@/lib/utils'
import { workEventMeta } from '@/types/work'
import type { ActivityEvent, StatusMeta, WorkEventType } from '@/types/work'

const TONE_DOT: Record<StatusMeta['tone'], string> = {
  neutral: 'bg-muted-foreground/40',
  info: 'bg-primary',
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-destructive',
}

/**
 * `event_type` is a machine spelling and is never rendered. Each event maps to a
 * past-tense verb and a fallback noun here, so the feed reads as sentences; the
 * map is `Partial` because an event type added by a later release must degrade
 * to generic copy rather than leak its own identifier.
 */
const EVENT_VERB: Partial<Record<WorkEventType, string>> = {
  project_created: 'Created',
  project_updated: 'Updated',
  project_completed: 'Completed',
  project_archived: 'Archived',
  project_restored: 'Restored',
  task_created: 'Created',
  task_updated: 'Updated',
  task_started: 'Started',
  task_completed: 'Completed',
  task_reopened: 'Reopened',
  task_blocked: 'Blocked',
  task_priority_changed: 'Reprioritised',
  task_due_date_changed: 'Rescheduled',
  task_deleted: 'Deleted',
  task_scheduled: 'Scheduled',
}

const FALLBACK_VERB = 'Updated'

function nounFor(event: ActivityEvent): string {
  return event.project_id && !event.task_id ? 'a project' : 'a task'
}

/** The name of what happened, when the event recorded one. */
function subjectOf(event: ActivityEvent): string | null {
  const metadata = event.metadata ?? {}
  for (const key of ['title', 'name', 'task_title', 'project_name']) {
    const value = metadata[key]
    if (typeof value === 'string' && value.trim() !== '') return value.trim()
  }
  return null
}

function noteOf(event: ActivityEvent): string | null {
  const note = event.metadata?.note
  return typeof note === 'string' && note.trim() !== '' ? note.trim() : null
}

/** "Completed ‘Build API’" — never `task_completed`. */
function describe(event: ActivityEvent): string {
  const verb = EVENT_VERB[event.event_type] ?? FALLBACK_VERB
  const subject = subjectOf(event)
  const sentence = subject ? `${verb} “${subject}”` : `${verb} ${nounFor(event)}`
  const note = noteOf(event)
  return note ? `${sentence} — ${note}` : sentence
}

/** "just now" / "12 minutes ago" / "3 days ago". */
function relativeTime(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''

  const minutes = Math.round((Date.now() - then) / 60_000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`

  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`

  const days = Math.round(hours / 24)
  if (days <= 30) return `${days} day${days === 1 ? '' : 's'} ago`

  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(then))
}

export interface ActivityFeedProps {
  items: ActivityEvent[]
  emptyMessage?: string
  className?: string
}

/** Newest first, as the endpoint returns it. */
export function ActivityFeed({ items, emptyMessage, className }: ActivityFeedProps) {
  if (items.length === 0) {
    return (
      <EmptyState
        icon={History}
        title="Nothing has happened yet"
        description={emptyMessage ?? 'Transitions and edits show up here as they occur.'}
        compact
        className={className}
      />
    )
  }

  return (
    <ol className={cn('space-y-3', className)}>
      {items.map((event) => {
        // `workEventMeta` answers for an event type this build does not know:
        // `event_type` is an unconstrained column, and a miss must cost a dot
        // with a fallback icon rather than the feed.
        const meta = workEventMeta(event.event_type)
        const Icon = meta.icon
        return (
          <li key={event.id} className="flex items-start gap-3">
            <span
              aria-hidden="true"
              className={cn(
                'mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground',
                TONE_DOT[meta.tone],
              )}
            >
              <Icon className="size-3.5" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm text-foreground">{describe(event)}</p>
              <p className="text-xs text-muted-foreground">
                <time dateTime={event.created_at}>{relativeTime(event.created_at)}</time>
              </p>
            </div>
          </li>
        )
      })}
    </ol>
  )
}