import { useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, CheckCircle2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Spinner } from '@/components/ui/spinner'
import { workKeys } from '@/features/work/hooks'
import { toApiError } from '@/services/errors'
import { fetchProjects } from '@/services/work'
import type { Project, ProjectListParams } from '@/types/work'

import type {
  QuickActionDefinition,
  QuickActionField,
  QuickActionFieldOption,
  QuickActionValues,
} from './quick-actions'

/**
 * The projects a task can be filed under.
 *
 * `GET /projects` rather than a hard-coded list, because `project_id` is
 * mandatory on `POST /tasks`: a picker of made-up projects would produce a task
 * the user cannot then find or edit. Reuses the work feature's query key so the
 * list a normal create-task form shows is the same list.
 */
const PICKER_PARAMS: ProjectListParams = { limit: 100 }

export interface QuickActionFormProps {
  action: QuickActionDefinition
  /** Closes the palette. Called after a result has been shown and dismissed. */
  onDone: () => void
  onCancel: () => void
}

type Phase = 'editing' | 'failed'

export function QuickActionForm({ action, onDone, onCancel }: QuickActionFormProps) {
  const spec = action.form
  const queryClient = useQueryClient()

  const [values, setValues] = useState<QuickActionValues>({})
  const [phase, setPhase] = useState<Phase>('editing')
  const [created, setCreated] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  const needsProjects = Boolean(spec?.fields.some((field) => field.type === 'select' && field.optionsFrom === 'projects'))

  // Only requested when a field actually needs it: opening the palette must not
  // spend a round trip on a form that will never show a project.
  const projectsQuery = useQuery({
    queryKey: workKeys.projects(PICKER_PARAMS),
    queryFn: ({ signal }) => fetchProjects(PICKER_PARAMS, signal),
    enabled: needsProjects,
  })

  const projectOptions = useMemo<QuickActionFieldOption[]>(
    () =>
      (projectsQuery.data?.items ?? []).map((project: Project) => ({
        value: project.id,
        label: project.name,
      })),
    [projectsQuery.data],
  )

  const mutation = useMutation({
    mutationFn: async (submitted: QuickActionValues) => {
      if (!spec) throw new Error(`${action.id} has no form`)
      return spec.submit(submitted, { queryClient })
    },
    onSuccess: (label) => {
      setCreated(label)
      setFailure(null)
    },
    onError: (cause) => {
      // The server's own words, not a substitute. A palette that reports
      // "Created" over a 422 is worse than no palette.
      const error = toApiError(cause)
      setFailure(error.isTransportError ? error.message : `${error.message} (${error.status})`)
      setCreated(null)
      setPhase('failed')
    },
  })

  if (!spec) return null

  function setValue(name: string, value: string) {
    setValues((previous) => ({ ...previous, [name]: value }))
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (mutation.isPending) return
    mutation.mutate(values)
  }

  const projectsPending = needsProjects && projectsQuery.isPending
  const noProjects = needsProjects && projectsQuery.isSuccess && projectOptions.length === 0

  if (created !== null) {
    return (
      <div className="px-4 py-8 text-center" role="status">
        <CheckCircle2 className="mx-auto size-6 text-success" aria-hidden="true" />
        <p className="mt-3 text-sm font-medium text-foreground">Created “{created}”.</p>
        <p className="mt-1 text-sm text-muted-foreground">
          It is saved. Open the module to see it in place.
        </p>
        <Button type="button" className="mt-4" onClick={onDone}>
          Done
        </Button>
      </div>
    )
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-3 px-4 py-4">
      <p className="text-xs text-muted-foreground">{action.description}</p>

      {spec.fields.map((field) => (
        <QuickActionFieldControl
          key={field.name}
          field={field}
          value={values[field.name] ?? ''}
          options={
            field.type !== 'select'
              ? undefined
              : field.optionsFrom === 'projects'
                ? projectOptions
                : field.options
          }
          disabled={mutation.isPending}
          onChange={(value) => setValue(field.name, value)}
        />
      ))}

      {projectsPending && (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Spinner size="sm" label="Loading projects" />
          Loading your projects…
        </p>
      )}

      {noProjects && (
        <p className="text-xs text-destructive" role="alert">
          You have no projects yet, and a task must belong to one. Create a project first.
        </p>
      )}

      {failure !== null && (
        <p
          role="alert"
          className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-2.5 py-2 text-xs text-destructive"
        >
          <AlertCircle className="mt-px size-3.5 shrink-0" aria-hidden="true" />
          <span>
            <span className="font-medium">Nothing was created. </span>
            {failure}
          </span>
        </p>
      )}

      <div className="mt-1 flex items-center justify-end gap-2">
        {phase === 'failed' && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              setFailure(null)
              setPhase('editing')
            }}
          >
            Edit and retry
          </Button>
        )}
        <Button type="button" variant="outline" size="sm" onClick={onCancel} disabled={mutation.isPending}>
          Cancel
        </Button>
        <Button
          type="submit"
          size="sm"
          disabled={mutation.isPending || projectsPending || noProjects}
        >
          {mutation.isPending ? 'Creating…' : 'Create'}
        </Button>
      </div>
    </form>
  )
}

interface QuickActionFieldControlProps {
  field: QuickActionField
  value: string
  options?: readonly QuickActionFieldOption[]
  disabled: boolean
  onChange: (value: string) => void
}

function QuickActionFieldControl({
  field,
  value,
  options,
  disabled,
  onChange,
}: QuickActionFieldControlProps) {
  // The id is derived from the action, so two forms can never claim one label.
  const id = `quick-action-${field.name}`

  if (field.type === 'select') {
    return (
      <div className="app-form-field">
        <Label htmlFor={id} optional={!field.required}>
          {field.label}
        </Label>
        <Select
          id={id}
          name={field.name}
          required={field.required}
          disabled={disabled}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="">{field.required ? 'Choose…' : 'Default'}</option>
          {(options ?? []).map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
      </div>
    )
  }

  return (
    <div className="app-form-field">
      <Label htmlFor={id} optional={!field.required}>
        {field.label}
      </Label>
      <Input
        id={id}
        name={field.name}
        // `date`, `time` and `text` share this branch of the union, so the
        // text-only props are read through the narrowing rather than asserted.
        type={field.type}
        placeholder={field.type === 'text' ? field.placeholder : undefined}
        autoFocus={field.type === 'text' && field.autoFocus ? true : undefined}
        required={field.required}
        disabled={disabled}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  )
}
