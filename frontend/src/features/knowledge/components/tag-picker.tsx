import { useState } from 'react'
import { Plus, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'
import { useCreateTag, useTags } from '@/features/work/hooks'
import { toast } from '@/stores/toast-store'
import { cn } from '@/lib/utils'
import type { UUIDString } from '@/types/knowledge'

export interface TagPickerProps {
  /** The object's full tag set — assignment replaces, so this is not a delta. */
  value: UUIDString[]
  onChange: (tagIds: UUIDString[]) => void
  /** Offers "create tag" in the dropdown; off where tags are read-only. */
  creatable?: boolean
  disabled?: boolean
  className?: string
  'aria-label'?: string
}

/**
 * Multi-select over the shared `/tags` vocabulary.
 *
 * Tags live outside the knowledge base, so this resolves names from the one
 * cached tag query rather than carrying a second vocabulary — the same trick
 * `TaskCard` uses, for the same reason.
 */
export function TagPicker({
  value,
  onChange,
  creatable = false,
  disabled = false,
  className,
  'aria-label': ariaLabel = 'Tags',
}: TagPickerProps) {
  const { data: tagPage, isPending } = useTags()
  const createTag = useCreateTag()
  const [newTag, setNewTag] = useState('')

  const tags = tagPage?.items ?? []
  const available = tags.filter((tag) => !value.includes(tag.id))

  function toggle(id: UUIDString) {
    onChange(value.includes(id) ? value.filter((tagId) => tagId !== id) : [...value, id])
  }

  async function submitNewTag() {
    const name = newTag.trim()
    if (!name) return
    try {
      const created = await createTag.mutateAsync(name)
      onChange([...value, created.id])
      setNewTag('')
    } catch {
      // The mutation's own error state is the signal; the tag is simply not added.
      toast.error('Could not create that tag', 'The name may already be taken.')
    }
  }

  return (
    <div className={cn('space-y-2', className)}>
      {value.length > 0 && (
        <ul aria-label={`${ariaLabel} — selected`} className="flex flex-wrap gap-1.5">
          {value.map((id) => {
            const tag = tags.find((candidate) => candidate.id === id)
            return (
              <li
                key={id}
                className="flex items-center gap-1 rounded-md border border-primary/30 bg-primary/10 px-1.5 py-0.5 text-xs text-primary"
              >
                {tag?.name ?? 'Unknown tag'}
                <button
                  type="button"
                  onClick={() => toggle(id)}
                  disabled={disabled}
                  className="rounded-sm hover:text-destructive"
                >
                  <X aria-hidden="true" className="size-3" />
                  <span className="sr-only">Remove {tag?.name ?? 'tag'}</span>
                </button>
              </li>
            )
          })}
        </ul>
      )}

      {isPending ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Spinner size="sm" />
          Loading tags
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-1.5">
          {available.length === 0 && (
            <p className="text-xs text-muted-foreground">Every tag is already applied.</p>
          )}
          {available.map((tag) => (
            <button
              key={tag.id}
              type="button"
              disabled={disabled}
              onClick={() => toggle(tag.id)}
              className="rounded-md border border-border px-1.5 py-0.5 text-xs text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground disabled:opacity-50"
            >
              {tag.name}
            </button>
          ))}
        </div>
      )}

      {creatable && (
        /*
         * Not a `<form>`, deliberately. Every host that offers `creatable` renders
         * the picker inside its own `<form>` — the concept dialog is one — and a
         * nested form is invalid HTML whose submit event bubbles to the outer
         * handler: pressing "Add" created the *concept* and closed the dialog
         * instead of adding the tag. A `<div>` with a `type="button"` cannot
         * submit anything, so the only write this control can cause is the tag.
         */
        <div className="flex items-center gap-2">
          <Input
            value={newTag}
            disabled={disabled}
            maxLength={80}
            placeholder="New tag"
            aria-label="New tag name"
            onChange={(event) => setNewTag(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                void submitNewTag()
              }
            }}
            className="h-8 max-w-40 text-xs"
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={disabled || !newTag.trim()}
            onClick={() => void submitNewTag()}
          >
            <Plus aria-hidden="true" className="size-3.5" />
            Add
          </Button>
        </div>
      )}
    </div>
  )
}
