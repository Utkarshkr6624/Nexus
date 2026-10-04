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
import { useCreateProject, useUpdateProject } from '@/features/work/hooks'
import type { ApiError } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { toApiError, bannerError, fieldErrorMessages } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import { PRIORITY_META, TASK_PRIORITIES } from '@/types/work'
import type { Project, ProjectPriority } from '@/types/work'

export interface ProjectFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Present to edit, absent to create. */
  project?: Project
  onSaved?: (project: Project) => void
}

interface FormState {
  name: string
  description: string
  priority: ProjectPriority
  start_date: string
  target_date: string
}

const EMPTY: FormState = {
  name: '',
  description: '',
  priority: 'medium',
  start_date: '',
  target_date: '',
}

function formFrom(project: Project): FormState {
  return {
    name: project.name,
    description: project.description ?? '',
    priority: project.priority,
    start_date: project.start_date ?? '',
    target_date: project.target_date ?? '',
  }
}

const TEXTAREA_CLASSES = cn(
  'w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm',
  'placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
  'aria-[invalid=true]:border-destructive',
)

/**
 * Mounted only while the dialog is open and keyed on the project id, so the
 * form is seeded from the record exactly once per opening rather than being
 * re-derived from props on every render.
 */
function ProjectForm({
  project,
  onCancel,
  onSaved,
}: {
  project?: Project
  onCancel: () => void
  onSaved: (project: Project) => void
}) {
  const [form, setForm] = useState<FormState>(() => (project ? formFrom(project) : EMPTY))
  const [error, setError] = useState<ApiError | null>(null)

  const createProject = useCreateProject()
  const updateProject = useUpdateProject()
  const pending = createProject.isPending || updateProject.isPending

  const fieldErrors = fieldErrorMessages(error)
  const banner = bannerError(error)

  function update(patch: Partial<FormState>) {
    setForm((current) => ({ ...current, ...patch }))
    setError(null)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)

    if (!form.name.trim()) {
      setError(toApiError(new Error('A project needs a name.')))
      return
    }
    // Caught here as well as server-side so the user is told which pair of dates
    // is wrong instead of getting a 422 for the whole form.
    if (form.start_date && form.target_date && form.target_date < form.start_date) {
      setError(toApiError(new Error('The target date cannot be earlier than the start date.')))
      return
    }

    const payload = {
      name: form.name.trim(),
      description: form.description.trim() || null,
      priority: form.priority,
      start_date: form.start_date || null,
      target_date: form.target_date || null,
    }

    try {
      const saved = project
        ? await updateProject.mutateAsync({ id: project.id, payload })
        : await createProject.mutateAsync(payload)
      toast.success(project ? 'Project updated' : 'Project created', saved.name)
      onSaved(saved)
    } catch (cause) {
      const apiError = toApiError(cause)
      setError(apiError)
      toast.error(
        project ? 'Could not update the project' : 'Could not create the project',
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
        <Label htmlFor="project-name">Name</Label>
        <Input
          id="project-name"
          value={form.name}
          maxLength={200}
          required
          error={Boolean(fieldErrors.name)}
          placeholder="Q4 Platform Migration"
          onChange={(event) => update({ name: event.target.value })}
        />
        {fieldErrors.name && <p className="app-form-error">{fieldErrors.name}</p>}
      </div>

      <div className="app-form-field">
        <Label htmlFor="project-description">Description</Label>
        <textarea
          id="project-description"
          rows={3}
          maxLength={2000}
          value={form.description}
          aria-invalid={Boolean(fieldErrors.description) || undefined}
          placeholder="Optional"
          onChange={(event) => update({ description: event.target.value })}
          className={TEXTAREA_CLASSES}
        />
        {fieldErrors.description && <p className="app-form-error">{fieldErrors.description}</p>}
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <div className="app-form-field">
          <Label htmlFor="project-priority">Priority</Label>
          <Select
            id="project-priority"
            value={form.priority}
            onChange={(event) => update({ priority: event.target.value as ProjectPriority })}
          >
            {TASK_PRIORITIES.map((priority) => (
              <option key={priority} value={priority}>
                {PRIORITY_META[priority].label}
              </option>
            ))}
          </Select>
          {fieldErrors.priority && <p className="app-form-error">{fieldErrors.priority}</p>}
        </div>

        <div className="app-form-field">
          <Label htmlFor="project-start">Start date</Label>
          <Input
            id="project-start"
            type="date"
            value={form.start_date}
            error={Boolean(fieldErrors.start_date)}
            onChange={(event) => update({ start_date: event.target.value })}
          />
          {fieldErrors.start_date && <p className="app-form-error">{fieldErrors.start_date}</p>}
        </div>

        <div className="app-form-field">
          <Label htmlFor="project-target">Target date</Label>
          <Input
            id="project-target"
            type="date"
            value={form.target_date}
            error={Boolean(fieldErrors.target_date)}
            onChange={(event) => update({ target_date: event.target.value })}
          />
          {fieldErrors.target_date && <p className="app-form-error">{fieldErrors.target_date}</p>}
        </div>
      </div>

      <DialogFooter>
        <Button variant="ghost" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending || !form.name.trim()}>
          {pending ? 'Saving…' : project ? 'Save changes' : 'Create project'}
        </Button>
      </DialogFooter>
    </form>
  )
}

export function ProjectFormDialog({
  open,
  onOpenChange,
  project,
  onSaved,
}: ProjectFormDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{project ? 'Edit project' : 'New project'}</DialogTitle>
          <DialogDescription>
            Projects are created as <em>planned</em>. Their lifecycle moves through the archive,
            restore and complete transitions, which carry their own rules.
          </DialogDescription>
        </DialogHeader>

        {open && (
          <ProjectForm
            key={project?.id ?? 'new'}
            project={project}
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