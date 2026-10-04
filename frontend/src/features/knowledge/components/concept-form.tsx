import { useState } from 'react'
import type { FormEvent } from 'react'
import { Lightbulb } from 'lucide-react'

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
import { useCreateConcept, useUpdateConcept } from '@/features/knowledge/hooks'
import { bannerError, fieldErrorMessages, toApiError } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import type { ApiError } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import type { Concept, UUIDString } from '@/types/knowledge'

import { TagPicker } from './tag-picker'

export interface ConceptFormProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Present to edit, absent to create. */
  concept?: Concept
  onSaved?: (concept: Concept) => void
}

const TEXTAREA_CLASSES =
  'w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background'

function ConceptFields({
  concept,
  onCancel,
  onSaved,
}: {
  concept?: Concept
  onCancel: () => void
  onSaved: (concept: Concept) => void
}) {
  const [name, setName] = useState(() => concept?.name ?? '')
  const [description, setDescription] = useState(() => concept?.description ?? '')
  const [tagIds, setTagIds] = useState<UUIDString[]>(() => concept?.tag_ids ?? [])
  const [error, setError] = useState<ApiError | null>(null)

  const create = useCreateConcept()
  const update = useUpdateConcept()
  const pending = create.isPending || update.isPending
  const fieldErrors = fieldErrorMessages(error)
  const banner = bannerError(error)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)

    if (!name.trim()) {
      setError(toApiError(new Error('A concept needs a name.')))
      return
    }

    try {
      // `tag_ids` is a full replacement set, not a delta, so the editor always
      // sends the whole set it is showing.
      const saved = concept
        ? await update.mutateAsync({
            id: concept.id,
            payload: { name: name.trim(), description: description.trim() || null, tag_ids: tagIds },
          })
        : await create.mutateAsync({
            name: name.trim(),
            description: description.trim() || null,
            tag_ids: tagIds,
          })
      toast.success(concept ? 'Concept updated' : 'Concept created', saved.name)
      onSaved(saved)
    } catch (cause) {
      const apiError = toApiError(cause)
      setError(apiError)
      toast.error(
        concept ? 'Could not update the concept' : 'Could not create the concept',
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
        <Label htmlFor="concept-name">Name</Label>
        <Input
          id="concept-name"
          value={name}
          required
          maxLength={200}
          placeholder="async"
          error={Boolean(fieldErrors.name)}
          onChange={(event) => {
            setName(event.target.value)
            setError(null)
          }}
        />
        {fieldErrors.name && <p className="app-form-error">{fieldErrors.name}</p>}
        <p className="app-form-hint">Names are unique per account — a repeat is a 409.</p>
      </div>

      <div className="app-form-field">
        <Label htmlFor="concept-description">Description</Label>
        <textarea
          id="concept-description"
          rows={4}
          value={description}
          placeholder="What this idea means, in your own words"
          onChange={(event) => setDescription(event.target.value)}
          className={cn(TEXTAREA_CLASSES)}
        />
        {fieldErrors.description && (
          <p className="app-form-error">{fieldErrors.description}</p>
        )}
      </div>

      <div className="app-form-field">
        <Label>Tags</Label>
        <TagPicker value={tagIds} onChange={setTagIds} creatable disabled={pending} />
      </div>

      <DialogFooter>
        <Button variant="ghost" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending || !name.trim()}>
          {pending ? 'Saving…' : concept ? 'Save changes' : 'Create concept'}
        </Button>
      </DialogFooter>
    </form>
  )
}

export function ConceptFormDialog({ open, onOpenChange, concept, onSaved }: ConceptFormProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Lightbulb aria-hidden="true" className="size-4" />
            {concept ? 'Edit concept' : 'New concept'}
          </DialogTitle>
          <DialogDescription>
            A concept is the named idea a note explains or a resource supports. Renaming one is an
            ordinary edit.
          </DialogDescription>
        </DialogHeader>

        {open && (
          <ConceptFields
            key={concept?.id ?? 'new'}
            concept={concept}
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
