import { useState } from 'react'
import type { FormEvent } from 'react'
import { Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { instantToLocalInput, localInputToInstant } from '@/features/planner/datetime'
import {
  useCreateCalendarEvent,
  useDeleteCalendarEvent,
  useUpdateCalendarEvent,
} from '@/features/planner/hooks'
import type { ApiError } from '@/lib/api-client'
import { bannerError, fieldErrorMessages, toApiError } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import { CALENDAR_EVENT_TYPES, EVENT_TYPE_META } from '@/types/planner'
import type { CalendarEvent, CalendarEventType } from '@/types/planner'

export interface EventFormDefaults {
  /** An instant; rendered into the `datetime-local` input in `timeZone`. */
  starts_at?: string
  ends_at?: string
  project_id?: string
  task_id?: string
  event_type?: CalendarEventType
}

export interface CalendarEventDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Present to edit, absent to create. */
  event?: CalendarEvent
  /** Ids and labels only, so both `Task` and `TaskSummary` fit. */
  projects: Array<{ id: string; name: string }>
  tasks: Array<{ id: string; title: string }>
  /** The zone the `datetime-local` values are read in. */
  timeZone: string
  defaults?: EventFormDefaults
  onSaved?: (event: CalendarEvent) => void
  onDeleted?: (event: CalendarEvent) => void
}

const TEXTAREA_CLASSES =
  'w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background aria-[invalid=true]:border-destructive'

interface FormState {
  title: string
  event_type: CalendarEventType
  starts_at: string
  ends_at: string
  all_day: boolean
  project_id: string
  task_id: string
  location: string
  description: string
}

/** The next whole hour, in the zone the form is being read in. */
function defaultWindow(timeZone: string): { starts_at: string; ends_at: string } {
  const now = new Date()
  const local = instantToLocalInput(now.toISOString(), timeZone)
  const start = new Date(now.getTime() + 60 * 60_000)
  return {
    starts_at: local.slice(0, 13) + '00',
    ends_at: instantToLocalInput(start.toISOString(), timeZone).slice(0, 13) + '00',
  }
}

function emptyForm(timeZone: string, defaults?: EventFormDefaults): FormState {
  const fallback = defaultWindow(timeZone)
  return {
    title: '',
    event_type: defaults?.event_type ?? 'other',
    starts_at: defaults?.starts_at ? instantToLocalInput(defaults.starts_at, timeZone) : fallback.starts_at,
    ends_at: defaults?.ends_at ? instantToLocalInput(defaults.ends_at, timeZone) : fallback.ends_at,
    all_day: false,
    project_id: defaults?.project_id ?? '',
    task_id: defaults?.task_id ?? '',
    location: '',
    description: '',
  }
}

function formFrom(event: CalendarEvent, timeZone: string): FormState {
  return {
    title: event.title,
    event_type: event.event_type,
    starts_at: instantToLocalInput(event.starts_at, timeZone),
    ends_at: instantToLocalInput(event.ends_at, timeZone),
    all_day: event.all_day,
    project_id: event.project_id ?? '',
    task_id: event.task_id ?? '',
    location: event.location ?? '',
    description: event.description ?? '',
  }
}

/**
 * Create or edit a calendar event.
 *
 * **A `datetime-local` value carries no zone**, and the browser will not add one.
 * It is resolved here *in `timeZone`* — the zone the user is looking at — and the
 * interface says so, because "09:00" that lands at the wrong instant is
 * indistinguishable from a bug until someone misses a meeting.
 */
export function CalendarEventDialog({
  open,
  onOpenChange,
  event,
  projects,
  tasks,
  timeZone,
  defaults,
  onSaved,
  onDeleted,
}: CalendarEventDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{event ? 'Edit event' : 'New event'}</DialogTitle>
          <DialogDescription>
            {event
              ? 'Changes are saved immediately and move the event on the planner.'
              : 'An event is time you have committed to — a meeting, a deadline, a block you are not working.'}
          </DialogDescription>
        </DialogHeader>

        {open && (
          <EventForm
            key={event?.id ?? 'new'}
            event={event}
            projects={projects}
            tasks={tasks}
            timeZone={timeZone}
            defaults={defaults}
            onCancel={() => onOpenChange(false)}
            onSaved={(saved) => {
              onSaved?.(saved)
              onOpenChange(false)
            }}
            onDeleted={() => {
              if (event) onDeleted?.(event)
              onOpenChange(false)
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function EventForm({
  event,
  projects,
  tasks,
  timeZone,
  defaults,
  onCancel,
  onSaved,
  onDeleted,
}: {
  event?: CalendarEvent
  projects: Array<{ id: string; name: string }>
  tasks: Array<{ id: string; title: string }>
  timeZone: string
  defaults?: EventFormDefaults
  onCancel: () => void
  onSaved: (event: CalendarEvent) => void
  onDeleted: () => void
}) {
  const [form, setForm] = useState<FormState>(() =>
    event ? formFrom(event, timeZone) : emptyForm(timeZone, defaults),
  )
  const [error, setError] = useState<ApiError | null>(null)

  const create = useCreateCalendarEvent()
  const update = useUpdateCalendarEvent()
  const remove = useDeleteCalendarEvent()
  const pending = create.isPending || update.isPending

  const fieldErrors = fieldErrorMessages(error)
  const banner = bannerError(error)

  function apply(patch: Partial<FormState>) {
    setForm((current) => ({ ...current, ...patch }))
    setError(null)
  }

  async function submit(submitEvent: FormEvent<HTMLFormElement>) {
    submitEvent.preventDefault()
    setError(null)

    if (!form.title.trim()) {
      setError(toApiError(new Error('An event needs a title.')))
      return
    }

    const startsAt = localInputToInstant(form.starts_at, timeZone)
    const endsAt = localInputToInstant(form.ends_at, timeZone)
    if (!startsAt || !endsAt) {
      setError(toApiError(new Error('Both the start and the end need a date and a time.')))
      return
    }

    const details = {
      title: form.title.trim(),
      starts_at: startsAt,
      ends_at: endsAt,
      event_type: form.event_type,
      description: form.description.trim() || null,
      project_id: form.project_id || null,
      task_id: form.task_id || null,
      all_day: form.all_day,
      location: form.location.trim() || null,
    }

    try {
      const saved = event
        ? await update.mutateAsync({ id: event.id, payload: details })
        : await create.mutateAsync(details)
      toast.success(event ? 'Event updated' : 'Event created', saved.title)
      onSaved(saved)
    } catch (cause) {
      const apiError = toApiError(cause)
      setError(apiError)
      toast.error(event ? 'Could not update the event' : 'Could not create the event', apiError.message)
    }
  }

  return (
    <form className="app-form-stack" onSubmit={submit} noValidate>
      {banner && (
        <p role="alert" className="app-form-error">
          {banner.message}
        </p>
      )}

      <div className="app-form-field">
        <Label htmlFor="event-title">Title</Label>
        <Input
          id="event-title"
          value={form.title}
          maxLength={200}
          required
          error={Boolean(fieldErrors.title)}
          placeholder="Design review"
          onChange={(change) => apply({ title: change.target.value })}
        />
        {fieldErrors.title && <p className="app-form-error">{fieldErrors.title}</p>}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="app-form-field">
          <Label htmlFor="event-type">Type</Label>
          <Select
            id="event-type"
            value={form.event_type}
            onChange={(change) => apply({ event_type: change.target.value as CalendarEventType })}
          >
            {CALENDAR_EVENT_TYPES.map((type) => (
              <option key={type} value={type}>
                {EVENT_TYPE_META[type].label}
              </option>
            ))}
          </Select>
          <p className="app-form-hint">{EVENT_TYPE_META[form.event_type].description}</p>
          {fieldErrors.event_type && <p className="app-form-error">{fieldErrors.event_type}</p>}
        </div>

        <div className="app-form-field justify-end">
          <div className="flex items-center gap-2">
            <Switch
              id="event-all-day"
              checked={form.all_day}
              onCheckedChange={(checked) => apply({ all_day: checked })}
              aria-label="All day"
            />
            <Label htmlFor="event-all-day">All day</Label>
          </div>
          <p className="app-form-hint">Marks the event as spanning a whole day rather than a window.</p>
        </div>
      </div>

      {/*
        The note is not decoration: a `datetime-local` value is a wall clock, and
        silently reading it as UTC is how an event lands hours from where it was
        typed.
      */}
      <fieldset className="app-form-field">
        <legend className="text-sm font-medium leading-none text-foreground">When</legend>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="app-form-field">
            <Label htmlFor="event-start">Starts</Label>
            <Input
              id="event-start"
              type="datetime-local"
              value={form.starts_at}
              error={Boolean(fieldErrors.starts_at)}
              onChange={(change) => apply({ starts_at: change.target.value })}
            />
            {fieldErrors.starts_at && <p className="app-form-error">{fieldErrors.starts_at}</p>}
          </div>
          <div className="app-form-field">
            <Label htmlFor="event-end">Ends</Label>
            <Input
              id="event-end"
              type="datetime-local"
              value={form.ends_at}
              error={Boolean(fieldErrors.ends_at)}
              onChange={(change) => apply({ ends_at: change.target.value })}
            />
            {fieldErrors.ends_at && <p className="app-form-error">{fieldErrors.ends_at}</p>}
          </div>
        </div>
        <p className="app-form-hint">
          Read as <span className="font-medium text-foreground">{timeZone}</span>, not as UTC. Stored
          as an instant with that offset.
        </p>
      </fieldset>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="app-form-field">
          <Label htmlFor="event-project" optional>
            Project
          </Label>
          <Select
            id="event-project"
            value={form.project_id}
            onChange={(change) => apply({ project_id: change.target.value })}
          >
            <option value="">No project</option>
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </Select>
          {fieldErrors.project_id && <p className="app-form-error">{fieldErrors.project_id}</p>}
        </div>

        <div className="app-form-field">
          <Label htmlFor="event-task" optional>
            Task
          </Label>
          <Select
            id="event-task"
            value={form.task_id}
            onChange={(change) => apply({ task_id: change.target.value })}
          >
            <option value="">No task</option>
            {tasks.map((task) => (
              <option key={task.id} value={task.id}>
                {task.title}
              </option>
            ))}
          </Select>
          {fieldErrors.task_id && <p className="app-form-error">{fieldErrors.task_id}</p>}
        </div>
      </div>

      <div className="app-form-field">
        <Label htmlFor="event-location" optional>
          Location
        </Label>
        <Input
          id="event-location"
          value={form.location}
          maxLength={200}
          placeholder="Room 3 / call link"
          onChange={(change) => apply({ location: change.target.value })}
        />
        {fieldErrors.location && <p className="app-form-error">{fieldErrors.location}</p>}
      </div>

      <div className="app-form-field">
        <Label htmlFor="event-description" optional>
          Description
        </Label>
        <textarea
          id="event-description"
          rows={3}
          maxLength={8000}
          value={form.description}
          aria-invalid={Boolean(fieldErrors.description) || undefined}
          onChange={(change) => apply({ description: change.target.value })}
          className={TEXTAREA_CLASSES}
        />
        {fieldErrors.description && <p className="app-form-error">{fieldErrors.description}</p>}
      </div>

      <DialogFooter>
        {event && (
          <Button
            type="button"
            variant="ghost"
            className="mr-auto text-destructive hover:bg-destructive/10"
            disabled={remove.isPending}
            onClick={async () => {
              try {
                await remove.mutateAsync(event.id)
                toast.success('Event deleted', event.title)
                onDeleted()
              } catch (cause) {
                toast.error('Could not delete the event', toApiError(cause).message)
              }
            }}
          >
            <Trash2 aria-hidden="true" />
            Delete
          </Button>
        )}
        <Button variant="ghost" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending || !form.title.trim()}>
          {pending ? 'Saving…' : event ? 'Save changes' : 'Create event'}
        </Button>
      </DialogFooter>
    </form>
  )
}