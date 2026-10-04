import { useState } from 'react'
import type { FormEvent } from 'react'

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
import { useCreateTask, useSetTaskTags, useTags, useUpdateTask } from '@/features/work/hooks'
import type { ApiError } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { toApiError, bannerError, fieldErrorMessages } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import { PRIORITY_META, TASK_PRIORITIES, TASK_STATUSES, TASK_STATUS_META } from '@/types/work'
import type { Project, Task, TaskPriority, TaskStatus } from '@/types/work'

export interface TaskDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Present to edit, absent to create. */
  task?: Task
  projects: Project[]
  /** Pre-selected project when creating from the board or a project page. */
  defaultProjectId?: string
  onSaved?: (task: Task) => void
}

interface FormState {
  project_id: string
  title: string
  description: string
  priority: TaskPriority
  status: TaskStatus
  start_date: string
  due_date: string
  estimated_minutes: string
  tag_ids: string[]
}

function emptyForm(defaultProjectId?: string): FormState {
  return {
    project_id: defaultProjectId ?? '',
    title: '',
    description: '',
    priority: 'medium',
    status: 'todo',
    start_date: '',
    due_date: '',
    estimated_minutes: '',
    tag_ids: [],
  }
}

function formFrom(task: Task): FormState {
  return {
    project_id: task.project_id,
    title: task.title,
    description: task.description ?? '',
    priority: task.priority,
    status: task.status,
    start_date: task.start_date ?? '',
    due_date: task.due_date ?? '',
    estimated_minutes: task.estimated_minutes === null ? '' : String(task.estimated_minutes),
    tag_ids: [...task.tag_ids],
  }
}

const TEXTAREA_CLASSES = cn(
  'w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm',
  'placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
  'aria-[invalid=true]:border-destructive',
)

/**
 * The form, mounted only while the dialog is open and re-keyed on the task id.
 * That is what makes "read the task when it opens" true without an effect: a
 * fresh mount seeds the state from props, and closing unmounts it, so a
 * cancelled edit can never seed the next one.
 */
function TaskForm({
  task,
  projects,
  defaultProjectId,
  onCancel,
  onSaved,
}: {
  task?: Task
  projects: Project[]
  defaultProjectId?: string
  onCancel: () => void
  onSaved: (task: Task) => void
}) {
  const [form, setForm] = useState<FormState>(() =>
    task ? formFrom(task) : emptyForm(defaultProjectId),
  )
  const [error, setError] = useState<ApiError | null>(null)

  const createTask = useCreateTask()
  const updateTask = useUpdateTask()
  const setTags = useSetTaskTags()
  const { data: tagPage } = useTags()
  const tags = tagPage?.items ?? []
  const pending = createTask.isPending || updateTask.isPending || setTags.isPending

  const fieldErrors = fieldErrorMessages(error)
  const banner = bannerError(error)

  function update(patch: Partial<FormState>) {
    setForm((current) => ({ ...current, ...patch }))
    setError(null)
  }

  function toggleTag(id: string) {
    update({
      tag_ids: form.tag_ids.includes(id)
        ? form.tag_ids.filter((existing) => existing !== id)
        : [...form.tag_ids, id],
    })
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)

    if (!form.project_id) {
      setError(toApiError(new Error('A task has to belong to a project.')))
      return
    }
    if (!form.title.trim()) {
      setError(toApiError(new Error('A task needs a title.')))
      return
    }

    const estimated =
      form.estimated_minutes === '' ? null : Number.parseInt(form.estimated_minutes, 10)
    if (estimated !== null && (!Number.isFinite(estimated) || estimated < 0)) {
      setError(toApiError(new Error('The estimate has to be a whole number of minutes.')))
      return
    }

    const details = {
      title: form.title.trim(),
      description: form.description.trim() || null,
      priority: form.priority,
      start_date: form.start_date || null,
      due_date: form.due_date || null,
      estimated_minutes: estimated,
    }

    try {
      const saved = task
        ? await updateTask.mutateAsync({ id: task.id, payload: details })
        : await createTask.mutateAsync({
            ...details,
            project_id: form.project_id,
            status: form.status,
          })

      const previous = task?.tag_ids ?? []
      const tagsChanged =
        form.tag_ids.length !== previous.length ||
        form.tag_ids.some((id) => !previous.includes(id))
      if (tagsChanged) {
        await setTags.mutateAsync({ id: saved.id, tagIds: form.tag_ids })
      }

      toast.success(task ? 'Task updated' : 'Task created', saved.title)
      onSaved(saved)
    } catch (cause) {
      const apiError = toApiError(cause)
      setError(apiError)
      toast.error(task ? 'Could not update the task' : 'Could not create the task', apiError.message)
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
        <Label htmlFor="task-project">Project</Label>
        <Select
          id="task-project"
          value={form.project_id}
          onChange={(event) => update({ project_id: event.target.value })}
        >
          <option value="">Choose a project…</option>
          {projects.map((project) => (
            <option key={project.id} value={project.id}>
              {project.name}
            </option>
          ))}
        </Select>
        {fieldErrors.project_id && <p className="app-form-error">{fieldErrors.project_id}</p>}
      </div>

      <div className="app-form-field">
        <Label htmlFor="task-title">Title</Label>
        <Input
          id="task-title"
          value={form.title}
          maxLength={300}
          required
          error={Boolean(fieldErrors.title)}
          placeholder="Rotate the staging database credentials"
          onChange={(event) => update({ title: event.target.value })}
        />
        {fieldErrors.title && <p className="app-form-error">{fieldErrors.title}</p>}
      </div>

      <div className="app-form-field">
        <Label htmlFor="task-description">Description</Label>
        <textarea
          id="task-description"
          rows={4}
          maxLength={8000}
          value={form.description}
          aria-invalid={Boolean(fieldErrors.description) || undefined}
          placeholder="Optional"
          onChange={(event) => update({ description: event.target.value })}
          className={TEXTAREA_CLASSES}
        />
        {fieldErrors.description && <p className="app-form-error">{fieldErrors.description}</p>}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="app-form-field">
          <Label htmlFor="task-priority">Priority</Label>
          <Select
            id="task-priority"
            value={form.priority}
            onChange={(event) => update({ priority: event.target.value as TaskPriority })}
          >
            {TASK_PRIORITIES.map((priority) => (
              <option key={priority} value={priority}>
                {PRIORITY_META[priority].label}
              </option>
            ))}
          </Select>
          {fieldErrors.priority && <p className="app-form-error">{fieldErrors.priority}</p>}
        </div>

        {task ? (
          <div className="app-form-field">
            <Label htmlFor="task-status-readonly">Status</Label>
            <Input
              id="task-status-readonly"
              value={TASK_STATUS_META[form.status].label}
              readOnly
              disabled
            />
            <p className="app-form-hint">
              Status changes go through the board transitions, which enforce their own rules.
            </p>
          </div>
        ) : (
          <div className="app-form-field">
            <Label htmlFor="task-status">Initial status</Label>
            <Select
              id="task-status"
              value={form.status}
              onChange={(event) => update({ status: event.target.value as TaskStatus })}
            >
              {TASK_STATUSES.map((status) => (
                <option key={status} value={status}>
                  {TASK_STATUS_META[status].label}
                </option>
              ))}
            </Select>
          </div>
        )}

        <div className="app-form-field">
          <Label htmlFor="task-start">Start date</Label>
          <Input
            id="task-start"
            type="date"
            value={form.start_date}
            error={Boolean(fieldErrors.start_date)}
            onChange={(event) => update({ start_date: event.target.value })}
          />
          {fieldErrors.start_date && <p className="app-form-error">{fieldErrors.start_date}</p>}
        </div>

        <div className="app-form-field">
          <Label htmlFor="task-due">Due date</Label>
          <Input
            id="task-due"
            type="date"
            value={form.due_date}
            error={Boolean(fieldErrors.due_date)}
            onChange={(event) => update({ due_date: event.target.value })}
          />
          {fieldErrors.due_date && <p className="app-form-error">{fieldErrors.due_date}</p>}
        </div>

        <div className="app-form-field">
          <Label htmlFor="task-estimate">Estimate (minutes)</Label>
          <Input
            id="task-estimate"
            type="number"
            min={0}
            step={5}
            value={form.estimated_minutes}
            error={Boolean(fieldErrors.estimated_minutes)}
            placeholder="90"
            onChange={(event) => update({ estimated_minutes: event.target.value })}
          />
          {fieldErrors.estimated_minutes && (
            <p className="app-form-error">{fieldErrors.estimated_minutes}</p>
          )}
        </div>
      </div>

      {tags.length > 0 && (
        <fieldset className="app-form-field">
          <legend className="text-sm font-medium leading-none text-foreground">Tags</legend>
          <div className="flex flex-wrap gap-1.5">
            {tags.map((tag) => {
              const active = form.tag_ids.includes(tag.id)
              return (
                <Button
                  key={tag.id}
                  type="button"
                  size="sm"
                  variant={active ? 'default' : 'outline'}
                  aria-pressed={active}
                  onClick={() => toggleTag(tag.id)}
                >
                  {tag.name}
                </Button>
              )
            })}
          </div>
        </fieldset>
      )}

      <DialogFooter>
        <Button variant="ghost" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending || !form.title.trim() || !form.project_id}>
          {pending ? 'Saving…' : task ? 'Save changes' : 'Create task'}
        </Button>
      </DialogFooter>
    </form>
  )
}

export function TaskDialog({
  open,
  onOpenChange,
  task,
  projects,
  defaultProjectId,
  onSaved,
}: TaskDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{task ? 'Edit task' : 'New task'}</DialogTitle>
          <DialogDescription>
            {task
              ? 'Changes are saved immediately. Status moves through the board transitions, not from here.'
              : 'A task needs a project and a title. Everything else can wait.'}
          </DialogDescription>
        </DialogHeader>

        {open && (
          <TaskForm
            key={task?.id ?? 'new'}
            task={task}
            projects={projects}
            defaultProjectId={defaultProjectId}
            onCancel={() => onOpenChange(false)}
            onSaved={(saved) => {
              onSaved?.(saved)
              onOpenChange(false)
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}