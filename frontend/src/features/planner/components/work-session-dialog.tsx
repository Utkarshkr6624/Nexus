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
import { formatMinutes, instantToLocalInput, localInputToInstant } from '@/features/planner/datetime'
import {
  useCreateWorkSession,
  useDeleteWorkSession,
  useUpdateWorkSession,
} from '@/features/planner/hooks'
import type { ApiError } from '@/lib/api-client'
import { bannerError, fieldErrorMessages, toApiError } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import { WORK_SESSION_STATUSES, WORK_SESSION_STATUS_META } from '@/types/planner'
import type { WorkSession, WorkSessionStatus } from '@/types/planner'

export interface SessionFormDefaults {
  starts_at?: string
  ends_at?: string
  project_id?: string
  task_id?: string
  estimated_minutes?: number | null
}

export interface WorkSessionDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Present to edit, absent to create. */
  session?: WorkSession
  projects: Array<{ id: string; name: string }>
  tasks: Array<{ id: string; title: string; estimated_minutes?: number | null }>
  timeZone: string
  /**
   * Pre-selects a task. **This is the Task → Calendar integration**: the task
   * surface opens this dialog with the task already chosen, so booking time is
   * one click from the thing being booked.
   */
  defaultTaskId?: string
  defaults?: SessionFormDefaults
  onSaved?: (session: WorkSession) => void
  onDeleted?: (session: WorkSession) => void
}

interface FormState {
  task_id: string
  project_id: string
  starts_at: string
  ends_at: string
  estimated_minutes: string
  status: WorkSessionStatus
}

/** Next whole hour for one hour, in the zone the form is read in. */
function defaultWindow(timeZone: string): { starts_at: string; ends_at: string } {
  const now = new Date()
  const later = new Date(now.getTime() + 60 * 60_000)
  return {
    starts_at: instantToLocalInput(now.toISOString(), timeZone).slice(0, 13) + '00',
    ends_at: instantToLocalInput(later.toISOString(), timeZone).slice(0, 13) + '00',
  }
}

function emptyForm(timeZone: string, defaults?: SessionFormDefaults, defaultTaskId?: string): FormState {
  const fallback = defaultWindow(timeZone)
  return {
    task_id: defaultTaskId ?? defaults?.task_id ?? '',
    project_id: defaults?.project_id ?? '',
    starts_at: defaults?.starts_at ? instantToLocalInput(defaults.starts_at, timeZone) : fallback.starts_at,
    ends_at: defaults?.ends_at ? instantToLocalInput(defaults.ends_at, timeZone) : fallback.ends_at,
    estimated_minutes:
      defaults?.estimated_minutes === null || defaults?.estimated_minutes === undefined
        ? ''
        : String(defaults.estimated_minutes),
    status: 'planned',
  }
}

function formFrom(session: WorkSession, timeZone: string): FormState {
  return {
    task_id: session.task_id ?? '',
    project_id: session.project_id ?? '',
    starts_at: instantToLocalInput(session.scheduled_start, timeZone),
    ends_at: instantToLocalInput(session.scheduled_end, timeZone),
    estimated_minutes: session.estimated_minutes === null ? '' : String(session.estimated_minutes),
    status: session.status,
  }
}

/**
 * Schedule a task into a window, and time it.
 *
 * A session is **planned work, not spent work**: it arrives with
 * `actual_minutes = 0` and only the routed `start`/`stop` pair records time, so
 * the tracked total can never be a function of how carefully a form was filled
 * in. Status is editable here because a session has no lifecycle rules a blanket
 * write could bypass — cancelling a reserved slot is an ordinary edit.
 */
export function WorkSessionDialog({
  open,
  onOpenChange,
  session,
  projects,
  tasks,
  timeZone,
  defaultTaskId,
  defaults,
  onSaved,
  onDeleted,
}: WorkSessionDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{session ? 'Edit work session' : 'Schedule work'}</DialogTitle>
          <DialogDescription>
            {session
              ? 'Changes are saved immediately. Time already recorded is not affected by moving the window.'
              : 'Reserve a stretch of time for a task. The timer starts when you start it, not now.'}
          </DialogDescription>
        </DialogHeader>

        {open && (
          <SessionForm
            key={session?.id ?? `new:${defaultTaskId ?? defaults?.task_id ?? ''}`}
            session={session}
            projects={projects}
            tasks={tasks}
            timeZone={timeZone}
            defaultTaskId={defaultTaskId}
            defaults={defaults}
            onCancel={() => onOpenChange(false)}
            onSaved={(saved) => {
              onSaved?.(saved)
              onOpenChange(false)
            }}
            onDeleted={() => {
              if (session) onDeleted?.(session)
              onOpenChange(false)
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function SessionForm({
  session,
  projects,
  tasks,
  timeZone,
  defaultTaskId,
  defaults,
  onCancel,
  onSaved,
  onDeleted,
}: {
  session?: WorkSession
  projects: Array<{ id: string; name: string }>
  tasks: Array<{ id: string; title: string; estimated_minutes?: number | null }>
  timeZone: string
  defaultTaskId?: string
  defaults?: SessionFormDefaults
  onCancel: () => void
  onSaved: (session: WorkSession) => void
  onDeleted: () => void
}) {
  const [form, setForm] = useState<FormState>(() =>
    session
      ? formFrom(session, timeZone)
      : emptyForm(timeZone, defaults, defaultTaskId),
  )
  const [error, setError] = useState<ApiError | null>(null)

  const create = useCreateWorkSession()
  const update = useUpdateWorkSession()
  const remove = useDeleteWorkSession()
  const pending = create.isPending || update.isPending

  const fieldErrors = fieldErrorMessages(error)
  const banner = bannerError(error)

  function apply(patch: Partial<FormState>) {
    setForm((current) => ({ ...current, ...patch }))
    setError(null)
  }

  const selectedTask = tasks.find((task) => task.id === form.task_id)

  async function submit(submitEvent: FormEvent<HTMLFormElement>) {
    submitEvent.preventDefault()
    setError(null)

    const scheduledStart = localInputToInstant(form.starts_at, timeZone)
    const scheduledEnd = localInputToInstant(form.ends_at, timeZone)
    if (!scheduledStart || !scheduledEnd) {
      setError(toApiError(new Error('Both the start and the end need a date and a time.')))
      return
    }

    const estimated =
      form.estimated_minutes === '' ? null : Number.parseInt(form.estimated_minutes, 10)
    if (estimated !== null && (!Number.isFinite(estimated) || estimated < 0)) {
      setError(toApiError(new Error('The estimate has to be a whole number of minutes.')))
      return
    }

    const details = {
      task_id: form.task_id || null,
      project_id: form.project_id || null,
      scheduled_start: scheduledStart,
      scheduled_end: scheduledEnd,
      estimated_minutes: estimated,
    }

    try {
      const saved = session
        ? await update.mutateAsync({ id: session.id, payload: { ...details, status: form.status } })
        : await create.mutateAsync(details)
      toast.success(
        session ? 'Session updated' : 'Time reserved',
        tasks.find((task) => task.id === saved.task_id)?.title ?? 'No task attached',
      )
      onSaved(saved)
    } catch (cause) {
      const apiError = toApiError(cause)
      setError(apiError)
      toast.error(
        session ? 'Could not update the session' : 'Could not reserve that time',
        apiError.message,
      )
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
        <Label htmlFor="session-task" optional>
          Task
        </Label>
        <Select
          id="session-task"
          value={form.task_id}
          onChange={(change) => apply({ task_id: change.target.value })}
        >
          <option value="">No task — just the time</option>
          {tasks.map((task) => (
            <option key={task.id} value={task.id}>
              {task.title}
            </option>
          ))}
        </Select>
        {fieldErrors.task_id && <p className="app-form-error">{fieldErrors.task_id}</p>}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="app-form-field">
          <Label htmlFor="session-start">Scheduled start</Label>
          <Input
            id="session-start"
            type="datetime-local"
            value={form.starts_at}
            error={Boolean(fieldErrors.scheduled_start)}
            onChange={(change) => apply({ starts_at: change.target.value })}
          />
          {fieldErrors.scheduled_start && (
            <p className="app-form-error">{fieldErrors.scheduled_start}</p>
          )}
        </div>
        <div className="app-form-field">
          <Label htmlFor="session-end">Scheduled end</Label>
          <Input
            id="session-end"
            type="datetime-local"
            value={form.ends_at}
            error={Boolean(fieldErrors.scheduled_end)}
            onChange={(change) => apply({ ends_at: change.target.value })}
          />
          {fieldErrors.scheduled_end && <p className="app-form-error">{fieldErrors.scheduled_end}</p>}
        </div>
      </div>

      <p className="app-form-hint">
        Read as <span className="font-medium text-foreground">{timeZone}</span>. Reserving time is
        not spending it — recorded minutes only move when the timer is started and stopped.
      </p>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="app-form-field">
          <Label htmlFor="session-estimate" optional>
            Estimated minutes
          </Label>
          <Input
            id="session-estimate"
            type="number"
            min={0}
            step={5}
            value={form.estimated_minutes}
            error={Boolean(fieldErrors.estimated_minutes)}
            placeholder={selectedTask?.estimated_minutes ? String(selectedTask.estimated_minutes) : '90'}
            onChange={(change) => apply({ estimated_minutes: change.target.value })}
          />
          {selectedTask?.estimated_minutes ? (
            <p className="app-form-hint">
              That task is estimated at {formatMinutes(selectedTask.estimated_minutes)}.
            </p>
          ) : null}
          {fieldErrors.estimated_minutes && (
            <p className="app-form-error">{fieldErrors.estimated_minutes}</p>
          )}
        </div>

        <div className="app-form-field">
          <Label htmlFor="session-project" optional>
            Project
          </Label>
          <Select
            id="session-project"
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
      </div>

      {session && (
        <div className="app-form-field">
          <Label htmlFor="session-status">Status</Label>
          <Select
            id="session-status"
            value={form.status}
            onChange={(change) => apply({ status: change.target.value as WorkSessionStatus })}
          >
            {WORK_SESSION_STATUSES.map((status) => (
              <option key={status} value={status}>
                {WORK_SESSION_STATUS_META[status].label}
              </option>
            ))}
          </Select>
          <p className="app-form-hint">
            {session.actual_minutes > 0
              ? `${formatMinutes(session.actual_minutes)} already recorded on this session.`
              : 'Cancelling releases the slot and keeps whatever time was recorded.'}
          </p>
          {fieldErrors.status && <p className="app-form-error">{fieldErrors.status}</p>}
        </div>
      )}

      <DialogFooter>
        {session && (
          <Button
            type="button"
            variant="ghost"
            className="mr-auto text-destructive hover:bg-destructive/10"
            disabled={remove.isPending}
            onClick={async () => {
              try {
                await remove.mutateAsync(session.id)
                toast.success('Session deleted')
                onDeleted()
              } catch (cause) {
                toast.error('Could not delete the session', toApiError(cause).message)
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
        <Button type="submit" disabled={pending}>
          {pending ? 'Saving…' : session ? 'Save changes' : 'Reserve time'}
        </Button>
      </DialogFooter>
    </form>
  )
}