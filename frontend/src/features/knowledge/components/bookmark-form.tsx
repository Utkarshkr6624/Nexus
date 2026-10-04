import { useState } from 'react'
import type { FormEvent } from 'react'
import { Bookmark as BookmarkIcon } from 'lucide-react'

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
import { useCreateBookmark, useUpdateBookmark } from '@/features/knowledge/hooks'
import { bannerError, fieldErrorMessages, toApiError } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import type { ApiError } from '@/lib/api-client'
import type { Bookmark } from '@/types/knowledge'

export interface BookmarkFormProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Present to edit, absent to create. */
  bookmark?: Bookmark
  onSaved?: (bookmark: Bookmark) => void
}

interface FormState {
  url: string
  title: string
  description: string
}

const EMPTY: FormState = { url: '', title: '', description: '' }

const TEXTAREA_CLASSES =
  'w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background'

/** Mounted only while open and keyed on the id, so the form is seeded once. */
function BookmarkFields({
  bookmark,
  onCancel,
  onSaved,
}: {
  bookmark?: Bookmark
  onCancel: () => void
  onSaved: (bookmark: Bookmark) => void
}) {
  const [form, setForm] = useState<FormState>(() =>
    bookmark
      ? {
          url: bookmark.url,
          title: bookmark.title ?? '',
          description: bookmark.description ?? '',
        }
      : EMPTY,
  )
  const [error, setError] = useState<ApiError | null>(null)

  const create = useCreateBookmark()
  const update = useUpdateBookmark()
  const pending = create.isPending || update.isPending
  const fieldErrors = fieldErrorMessages(error)
  const banner = bannerError(error)

  function patch(changes: Partial<FormState>) {
    setForm((current) => ({ ...current, ...changes }))
    setError(null)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)

    const url = form.url.trim()
    // Caught client-side as well as server-side: the backend refuses anything
    // that is not an absolute http(s) URL with a 422, and the message is better
    // next to the field than in a banner over the whole form.
    if (!/^https?:\/\/\S+$/i.test(url)) {
      setError(toApiError(new Error('A bookmark needs an absolute http or https URL.')))
      return
    }

    const payload = {
      url,
      title: form.title.trim() || null,
      description: form.description.trim() || null,
    }

    try {
      const saved = bookmark
        ? await update.mutateAsync({ id: bookmark.id, payload })
        : await create.mutateAsync(payload)
      toast.success(bookmark ? 'Bookmark updated' : 'Bookmark saved', saved.title ?? saved.url)
      onSaved(saved)
    } catch (cause) {
      const apiError = toApiError(cause)
      setError(apiError)
      toast.error(
        bookmark ? 'Could not update the bookmark' : 'Could not save the bookmark',
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
        <Label htmlFor="bookmark-url">URL</Label>
        <Input
          id="bookmark-url"
          value={form.url}
          required
          placeholder="https://example.com/article"
          error={Boolean(fieldErrors.url)}
          onChange={(event) => patch({ url: event.target.value })}
        />
        {fieldErrors.url && <p className="app-form-error">{fieldErrors.url}</p>}
      </div>

      <div className="app-form-field">
        <Label htmlFor="bookmark-title">Title</Label>
        <Input
          id="bookmark-title"
          value={form.title}
          maxLength={300}
          placeholder="Optional — falls back to the URL"
          error={Boolean(fieldErrors.title)}
          onChange={(event) => patch({ title: event.target.value })}
        />
        {fieldErrors.title && <p className="app-form-error">{fieldErrors.title}</p>}
      </div>

      <div className="app-form-field">
        <Label htmlFor="bookmark-description">Description</Label>
        <textarea
          id="bookmark-description"
          rows={3}
          value={form.description}
          placeholder="Why this is worth keeping"
          onChange={(event) => patch({ description: event.target.value })}
          className={TEXTAREA_CLASSES}
        />
      </div>

      <DialogFooter>
        <Button variant="ghost" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending || !form.url.trim()}>
          {pending ? 'Saving…' : bookmark ? 'Save changes' : 'Save bookmark'}
        </Button>
      </DialogFooter>
    </form>
  )
}

export function BookmarkFormDialog({ open, onOpenChange, bookmark, onSaved }: BookmarkFormProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <BookmarkIcon aria-hidden="true" className="size-4" />
            {bookmark ? 'Edit bookmark' : 'New bookmark'}
          </DialogTitle>
          <DialogDescription>
            The domain is derived from the URL by the server, so a bookmark cannot be filed under
            a site it does not belong to.
          </DialogDescription>
        </DialogHeader>

        {open && (
          <BookmarkFields
            key={bookmark?.id ?? 'new'}
            bookmark={bookmark}
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
