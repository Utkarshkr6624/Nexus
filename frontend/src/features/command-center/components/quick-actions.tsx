/**
 * Quick actions: create a record without leaving the Command Center.
 *
 * **Every action calls a real create endpoint.** There is no local-only draft, no
 * optimistic row that never reaches the database, and no action that claims to
 * have done something the backend refused. Each form holds one string, posts it,
 * and then reports what came back — a success line naming the record the server
 * created, or a failure line saying that nothing was created.
 *
 * **Only records with a single required field are offered.** `POST /tasks`
 * requires a `project_id`, so a task is not something this panel can create
 * without first asking which project; inventing one would be a silent
 * fabrication. Projects, notes, tags and learning goals each need exactly a name,
 * so each is honest here and a task is not.
 *
 * **Cache invalidation belongs to the feature hooks.** `useCreateProject`,
 * `useCreateTag`, `useCreateNote` and `useCreateLearningGoal` already invalidate
 * their own key families, so a project created here shows up on the Projects page
 * and in this page's panels without a reload — and this panel cannot fall out of
 * step with them if that policy ever changes.
 *
 * The palette's own quick actions are owned by another slice; this is a separate
 * surface wired to the same services, and it imports nothing from it.
 */
import { useState } from 'react'
import type { FormEvent } from 'react'
import { useMutation } from '@tanstack/react-query'
import { FolderKanban, GraduationCap, Plus, StickyNote, Tag } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useCreateNote } from '@/features/knowledge/hooks'
import { useCreateLearningGoal } from '@/features/learning/hooks'
import { useCreateProject, useCreateTag } from '@/features/work/hooks'
import { fieldErrorMessages, toApiError } from '@/services/errors'
import { cn } from '@/lib/utils'
import type { NoteCreatePayload } from '@/types/knowledge'
import type { LearningGoalCreatePayload } from '@/types/learning'
import type { ProjectCreatePayload } from '@/types/work'

/** What a form reports back after a call, in one sentence. */
type Outcome = { tone: 'success' | 'error'; message: string } | null

/**
 * A refused create, in one sentence.
 *
 * Every branch says what did *not* happen as well as what failed: "nothing was
 * created" is the fact a person needs, because the alternative reading is that
 * the record they just typed is now in the database.
 *
 * A 422 carries the backend's own field messages, and they name the limit — "a
 * tag name may be at most 48 characters". Reporting only "the backend rejected
 * that name" threw that away and made the user guess which part of what they
 * typed was wrong.
 */
function failureMessage(error: unknown): string {
  const api = toApiError(error)
  if (api.status === 422) {
    const [first] = Object.values(fieldErrorMessages(api))
    if (!first) return 'The backend rejected that name. Nothing was created.'
    // The backend's own sentence already ends in a full stop; adding a second
    // one would be `…48 characters.. Nothing was created.`
    return `${first.replace(/[.\s]+$/, '')}. Nothing was created.`
  }
  if (api.status === 409) return 'Something with that name already exists. Nothing was created.'
  if (api.status === 403) return 'That record belongs to another account. Nothing was created.'
  if (api.isTimeout) return 'The backend did not answer in time. Nothing was created.'
  return 'Nothing was created. Try again.'
}

interface QuickActionProps {
  id: string
  label: string
  hint: string
  placeholder: string
  /** The backend's own cap on the name, from its schema. Enforced here too. */
  maxLength: number
  icon: typeof Tag
  onCreate: (name: string) => Promise<{ label: string }>
}

function QuickAction({ id, label, hint, placeholder, maxLength, icon: Icon, onCreate }: QuickActionProps) {
  const [name, setName] = useState('')
  const [outcome, setOutcome] = useState<Outcome>(null)

  const create = useMutation({
    mutationFn: onCreate,
    onSuccess: (created) => {
      setName('')
      setOutcome({ tone: 'success', message: `Created ${created.label}.` })
    },
    onError: (error) => setOutcome({ tone: 'error', message: failureMessage(error) }),
  })

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmed = name.trim()
    if (trimmed.length === 0) return
    setOutcome(null)
    create.mutate(trimmed)
  }

  /**
   * Why `Create` is unavailable, or `null` when it is.
   *
   * All four buttons are greyed out on an account with nothing in it, which is
   * true and useless: a reader cannot tell "you have not typed anything yet"
   * from "this is closed to you". The reason is the sentence under the field,
   * and the title rides on a wrapper because `disabled:pointer-events-none`
   * stops a title on the button itself from ever being read.
   */
  const blockedReason = create.isPending
    ? `Asking the backend. This waits rather than sending a second request.`
    : name.trim().length === 0
      ? 'Type a name above and this becomes Create. Nothing is sent until you press it.'
      : null
  const hintId = `${id}-hint`
  const blockedId = `${id}-blocked`

  return (
    <form onSubmit={submit} className="space-y-2 rounded-md border border-border p-3">
      <Label htmlFor={id} className="flex items-center gap-2 text-xs">
        <Icon aria-hidden="true" className="size-3.5 text-muted-foreground" />
        {label}
      </Label>
      <div className="flex gap-2">
        <Input
          id={id}
          value={name}
          placeholder={placeholder}
          maxLength={maxLength}
          aria-describedby={blockedReason ? `${hintId} ${blockedId}` : hintId}
          onChange={(event) => setName(event.target.value)}
          className="h-8 text-sm"
        />
        <span className="inline-flex shrink-0" title={blockedReason ?? `Create this ${label.toLowerCase()}`}>
          <Button
            type="submit"
            size="sm"
            aria-describedby={blockedReason ? blockedId : undefined}
            disabled={blockedReason !== null}
          >
            <Plus aria-hidden="true" />
            {create.isPending ? 'Creating…' : 'Create'}
          </Button>
        </span>
      </div>
      {blockedReason && (
        <p id={blockedId} className="text-xs leading-relaxed text-muted-foreground">
          {blockedReason}
        </p>
      )}
      <p id={hintId} className="text-xs leading-relaxed text-muted-foreground">
        {hint}
      </p>
      {outcome && (
        <p
          role="status"
          className={cn(
            'text-xs leading-relaxed',
            outcome.tone === 'success' ? 'text-success' : 'text-destructive',
          )}
        >
          {outcome.message}
        </p>
      )}
    </form>
  )
}

export function QuickActions() {
  const createProject = useCreateProject()
  const createNote = useCreateNote()
  const createTag = useCreateTag()
  const createGoal = useCreateLearningGoal()

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <QuickAction
        id="quick-action-project"
        label="New project"
        hint="POST /projects. A name is the only required field."
        placeholder="Atlas migration"
        maxLength={200}
        icon={FolderKanban}
        onCreate={async (name) => {
          const payload: ProjectCreatePayload = { name }
          const project = await createProject.mutateAsync(payload)
          return { label: `project “${project.name}”` }
        }}
      />

      <QuickAction
        id="quick-action-note"
        label="New note"
        hint="POST /notes. Stored as written; nothing is generated for it."
        placeholder="What the Atlas numbers mean"
        maxLength={300}
        icon={StickyNote}
        onCreate={async (name) => {
          const payload: NoteCreatePayload = { title: name }
          const note = await createNote.mutateAsync(payload)
          return { label: `note “${note.title}”` }
        }}
      />

      <QuickAction
        id="quick-action-tag"
        label="New tag"
        hint="POST /tags. Tags label work; they create nothing else. 48 characters, the backend's own cap."
        placeholder="deep-work"
        maxLength={48}
        icon={Tag}
        onCreate={async (name) => {
          const tag = await createTag.mutateAsync(name)
          return { label: `tag “${tag.name}”` }
        }}
      />

      <QuickAction
        id="quick-action-goal"
        label="New learning goal"
        hint="POST /learning/goals. Progress stays yours; NEXUS never fills it in."
        placeholder="Finish the linear algebra course"
        maxLength={200}
        icon={GraduationCap}
        onCreate={async (name) => {
          const payload: LearningGoalCreatePayload = { title: name }
          const goal = await createGoal.mutateAsync(payload)
          return { label: `goal “${goal.title}”` }
        }}
      />
    </div>
  )
}
